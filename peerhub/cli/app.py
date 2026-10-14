"""PeerHub CLI Entrypoint.

Commands (canonical inventory: docs/implementation/command_inventory.json, derived from this parser by tools/command_inventory.py):
  peer register / get / list
  stream create / show / list
  record append / read
  offset get / advance
  profile list / show   (read-only effective TOML)
  session list / show   (read-only, bounded and redacted)
  ask                 (provider execution and durable delivery)
  observation refresh (provider probes and persisted evidence)
  monitor             (collection plus dashboard)
  diag                (read-only dashboard, optionally live)
  diag health / quota / integrity (read-only; integrity is explicit)
Exit codes: 0 ok, 1 error, 2 idempotency conflict, 3 CAS lost, 4 storage fault, 5 diag unavailable, 6 schema version.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, cast
from pydantic import BaseModel

from peerhub._console import tolerant_streams
from peerhub.cli.store_select import DEFAULT_DB_PATH, StoreSelection, select_store
from peerhub.cli.support import print_warnings as _print_warnings
from peerhub.core.models import Peer, Stream, utc_now_iso
from peerhub.core.schema_version import SchemaVersionError
from peerhub.core.store import CoreStore, IdempotencyConflictError, CasMismatchError, StorageCorruptError, StorageFullError, StorageReadOnlyError




def _select_db(configured_path: str, *, use_env: bool = True, discover: bool = True,
               workspace_root: Path | None = None) -> StoreSelection:
    return select_store(configured_path, use_env=use_env, discover=discover, workspace_root=workspace_root)


def _resolve_db_path(configured_path: str, *, use_env: bool = True, discover: bool = True,
                     workspace_root: Path | None = None) -> str:
    return _select_db(configured_path, use_env=use_env, discover=discover, workspace_root=workspace_root).path


def _page_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer from 1 to 200") from exc
    if not 1 <= limit <= 200:
        raise argparse.ArgumentTypeError("limit must be from 1 to 200")
    return limit


def _session_page_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer from 1 to 100") from exc
    if not 1 <= limit <= 100:
        raise argparse.ArgumentTypeError("limit must be from 1 to 100")
    return limit


def _page_cursor(kind: str, cursor: str | None) -> str | None:
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid page cursor") from exc
    if not isinstance(data, dict):
        raise ValueError("invalid page cursor")
    cursor_data = cast(dict[str, Any], data)
    if cursor_data.get("v") != 1 or cursor_data.get("kind") != kind or not isinstance(cursor_data.get("after"), str):
        raise ValueError("page cursor does not match this listing")
    return cast(str, cursor_data["after"])


def _make_page_cursor(kind: str, after: str) -> str:
    raw = json.dumps({"v": 1, "kind": kind, "after": after}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _encode_session_cursor(cursor: dict[str, Any]) -> str:
    raw = json.dumps(cursor, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_session_cursor(raw_cursor: str, *, store_id: str, lineage: str, filters: dict[str, str | None]) -> dict[str, Any]:
    try:
        decoded: object = json.loads(base64.urlsafe_b64decode(raw_cursor + "=" * (-len(raw_cursor) % 4)))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid session cursor") from exc
    if not isinstance(decoded, dict):
        raise ValueError("invalid session cursor")
    data = cast(dict[str, Any], decoded)
    if (data.get("v") != 1 or data.get("store") != store_id or data.get("lineage") != lineage
            or data.get("filters") != filters or data.get("order") != "scope-generation-handle-v1"
            or not isinstance(data.get("high_water"), int) or not isinstance(data.get("after"), list)
            or len(data["after"]) != 4):
        raise ValueError("session cursor does not match store, lineage, filters, or ordering")
    return data


def build_parser(prog: str = "peerhub") -> argparse.ArgumentParser:
    description = "PeerHub CLI"
    parser = argparse.ArgumentParser(prog=prog, description=description)
    from peerhub._version import __version__
    parser.add_argument("--version", "-v", "-V", action="version", version=f"peerhub {__version__}")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to SQLite database")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # peer
    peer_parser = subparsers.add_parser("peer", help="Peer commands")
    peer_sub = peer_parser.add_subparsers(dest="action", required=True)
    
    p_reg = peer_sub.add_parser("register", help="Register a peer")
    p_reg.add_argument("--peer", required=True, dest="peer_id")
    p_reg.add_argument("--name", dest="display_name")
    p_reg.add_argument("--adapter", dest="adapter_ref")

    p_get = peer_sub.add_parser("get", help="Get a peer")
    p_get.add_argument("--peer", required=True, dest="peer_id")

    p_list = peer_sub.add_parser("list", help="List peers (read-only, bounded)")
    p_list.add_argument("--limit", type=_page_limit, default=50)
    p_list.add_argument("--cursor")
    p_list.add_argument("--format", choices=["auto", "table", "json"], default="auto")

    # stream
    stream_parser = subparsers.add_parser("stream", help="Stream commands")
    st_sub = stream_parser.add_subparsers(dest="action", required=True)
    
    s_create = st_sub.add_parser("create", help="Create a stream")
    s_create.add_argument("--stream", required=True, dest="stream_id")
    s_create.add_argument("--title")
    s_create.add_argument("--members", nargs="*", default=[])

    s_show = st_sub.add_parser("show", help="Show stream details")
    s_show.add_argument("--stream", required=True, dest="stream_id")

    s_list = st_sub.add_parser("list", help="List streams (read-only, bounded)")
    s_list.add_argument("--limit", type=_page_limit, default=50)
    s_list.add_argument("--cursor")
    s_list.add_argument("--format", choices=["auto", "table", "json"], default="auto")

    # record
    record_parser = subparsers.add_parser("record", help="Record commands")
    rec_sub = record_parser.add_subparsers(dest="action", required=True)

    r_append = rec_sub.add_parser("append", help="Append a record")
    r_append.add_argument("--stream", required=True, dest="stream_id")
    r_append.add_argument("--author-peer", required=True, dest="author_peer_id")
    r_append.add_argument("--kind", required=True)
    r_append.add_argument("--body", required=True, help="JSON body string")
    r_append.add_argument("--idempotency-key", required=True, dest="idemp_key")
    r_append.add_argument("--created-at", default=None, help="RFC3339; pass the same value on retries (part of the idempotency digest)")
    r_append.add_argument("--targets", nargs="*", default=[])

    r_read = rec_sub.add_parser("read", help="Read records from stream")
    r_read.add_argument("--stream", required=True, dest="stream_id")
    r_read.add_argument("--after", type=int, default=0)
    r_read.add_argument("--limit", type=int, default=50)

    # offset
    offset_parser = subparsers.add_parser("offset", help="Offset commands")
    off_sub = offset_parser.add_subparsers(dest="action", required=True)

    o_get = off_sub.add_parser("get", help="Get peer offset")
    o_get.add_argument("--peer", required=True, dest="peer_id")
    o_get.add_argument("--stream", required=True, dest="stream_id")

    o_adv = off_sub.add_parser("advance", help="Advance peer offset with CAS")
    o_adv.add_argument("--peer", required=True, dest="peer_id")
    o_adv.add_argument("--stream", required=True, dest="stream_id")
    o_adv.add_argument("--position", required=True, type=int, dest="new_position")
    o_adv.add_argument("--revision", required=True, type=int, dest="expected_revision")

    ask_parser = subparsers.add_parser("ask", help="Deliver one prompt through the durable Session Bridge",
                                     description="Reuse the latest compatible retained session of this peer/stream; otherwise start fresh with bounded catch-up. Select --fresh or an exact --session HANDLE. Results include a session handle and mode.")
    ask_parser.add_argument("peer", help="Peer ID (built-ins: cx/codex, cc/claude, ag/agy)")
    ask_parser.add_argument("prompt", nargs="?")
    ask_parser.add_argument("--query-file", help="UTF-8 prompt file; exclusive with prompt")
    ask_parser.add_argument("--stream", dest="stream_id", help="Conversation Stream (default: peer:<id>:chat)")
    ask_parser.add_argument("--request-id", help="Reuse on retries; uncertain execution is never replayed automatically")
    ask_parser.add_argument("--fresh", action="store_true", help="Allocate a fresh session with bounded Stream catch-up")
    ask_parser.add_argument("--session", metavar="HANDLE", help="Reuse this exact retained handle; incompatible sessions are rejected")
    ask_parser.add_argument("--author-peer", dest="author", default="user", help="Peer ID recorded as the prompt author (auto-registered)")
    ask_parser.add_argument("-w", "--workspace", help="Provider working directory; default: current directory")
    ask_parser.add_argument("--model")
    ask_parser.add_argument("--effort")
    ask_parser.add_argument("-p", "--profile", help="Explicit Runtime Target profile; model/effort override its defaults")
    ask_parser.add_argument("--writable", action="store_true", help="Let the peer write files in the workspace (default: read-only)")
    ask_parser.add_argument("--timeout-seconds", type=float, default=60)
    ask_parser.add_argument("--silence-timeout-seconds", type=float, default=None,
                            help="Abort after this many seconds without stdout/stderr; execution remains uncertain")
    ask_parser.add_argument("--max-output-bytes", type=int, default=1_000_000)
    ask_parser.add_argument("--json", action="store_true")

    profile_parser = subparsers.add_parser("profile", help="Inspect effective local model profiles (read-only)")
    profile_sub = profile_parser.add_subparsers(dest="action", required=True)
    profile_list = profile_sub.add_parser("list", help="List configured profiles")
    profile_list.add_argument("--workspace", type=Path, default=None)
    profile_list.add_argument("--format", choices=["auto", "table", "json"], default="auto")
    profile_show = profile_sub.add_parser("show", help="Show effective profile values and source layers")
    profile_show.add_argument("profile")
    profile_show.add_argument("--workspace", type=Path, default=None)
    profile_show.add_argument("--format", choices=["auto", "table", "json"], default="auto")

    session_parser = subparsers.add_parser("session", help="Inspect retained sessions (read-only)")
    session_sub = session_parser.add_subparsers(dest="action", required=True)
    session_list = session_sub.add_parser("list", help="List retained sessions")
    session_list.add_argument("--stream")
    session_list.add_argument("--peer")
    session_list.add_argument("--limit", type=_session_page_limit, default=20)
    session_list.add_argument("--cursor")
    session_list.add_argument("--format", choices=["auto", "table", "json"], default="auto")
    session_show = session_sub.add_parser("show", help="Show a retained session by PeerHub handle")
    session_show.add_argument("handle")
    session_show.add_argument("--format", choices=["auto", "table", "json"], default="auto")

    observation_parser = subparsers.add_parser("observation", help="Explicit observation collection (mutating)")
    observation_sub = observation_parser.add_subparsers(dest="action", required=True)
    refresh = observation_sub.add_parser("refresh", help="Collect provider quota into append-only Observation evidence")
    refresh.add_argument("--peers", nargs="+", default=["cx", "cc", "ag"])
    refresh.add_argument("--system-dir", dest="sys_dir", type=Path, help="Explicit portable provider installation directory")
    refresh.add_argument("--timeout-seconds", type=float, default=15)

    from peerhub.cli.monitor import register_monitor_parser
    register_monitor_parser(subparsers)

    # diag
    diag_parser = subparsers.add_parser("diag", help="Readonly diagnostics")
    diag_parser.add_argument("--json", action="store_true", help="Emit the read-only dashboard as JSON")
    diag_parser.add_argument("--live", action="store_true", help="Watch read-only snapshots; never refresh provider evidence")
    diag_parser.add_argument("--interval-seconds", type=float, default=2, help="Live snapshot interval (positive seconds)")
    diag_parser.add_argument("--view", choices=["plain", "rich"], default="plain", help="Dashboard style: plain (ASCII), rich (bars, pace and alerts). With --live, snapshots every N s: TTY with VT overwrites in place; non-TTY or no VT appends without escapes. Monitor countdown redraw is at most 1 Hz")
    diag_parser.add_argument("--cycles", dest="count", type=int, default=0, help="Live snapshot count; 0 watches until interrupted")
    diag_parser.epilog = "examples: peerhub diag | peerhub diag --json | peerhub diag --live | peerhub observation refresh | peerhub monitor (collects quota evidence every cycle and draws the dashboard)"
    diag_sub = diag_parser.add_subparsers(dest="action", required=False)
    
    d_health = diag_sub.add_parser("health", help="Inspect stream or store health (read-only)")
    d_health.add_argument("--stream", default=None, dest="stream_id",
                          help="Optional stream ID to inspect (default: inspects the store and a bounded stream sample)")
    d_health.add_argument("--observation-db", dest="obs_db", default=None, metavar="PATH",
                          help="Read the observations summary from this OTHER PeerHub workspace database (opened read-only, never created); "
                               "default: the --db database. The stream part always comes from --db.")
    d_health.epilog = "examples: peerhub diag health | peerhub diag health --stream s1"

    d_quota = diag_sub.add_parser("quota", help="Show current quota/rate-limit evidence (read-only, never refreshes)",
                                  description="Latest quota/rate_limit Observation per subject/pool exactly as Diag evaluates it "
                                              "(MEASURED/STALE/UNKNOWN/UNAVAILABLE). Missing evidence is UNKNOWN, never unlimited. "
                                              "Exit: 0 ok, 4 storage fault/corrupt evidence, 5 database or observation tables unavailable, 6 schema version.")
    d_quota.add_argument("--observation-db", dest="obs_db", default=None, metavar="PATH",
                         help="Read observations from this OTHER PeerHub workspace database (opened read-only, never created); default: the --db database")
    d_quota.add_argument("--peer", default=None, help="Only this subject_ref (exact match)")
    d_quota.add_argument("--pool", default=None, help="Only this resource_pool_ref (exact match)")
    d_quota.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Machine-readable output (schema_version 1.0) instead of the table")
    d_quota.epilog = "examples: peerhub --db ws.db diag quota --json | peerhub --db ws.db diag quota --pool P --observation-db other_ws.db"

    d_integrity = diag_sub.add_parser("integrity", help="Run the explicit full SQLite integrity check (read-only; may take time)")
    d_integrity.epilog = "example: peerhub diag integrity"

    return parser


def _dump(model: BaseModel) -> str:
    """JSON with ASCII escapes only: lossless for every Unicode text whatever the console encoding (no encoding is forced on the user)."""
    return json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=True)



def _session_trailer(session: dict[str, Any]) -> str:
    suffix = ""
    if session["mode"] == "fresh":
        fallback = " fallback" if session.get("prior_handle") and session["reason"] not in ("no_prior", "explicit_fresh") else ""
        suffix = f"{fallback} reason={session['reason']}"
    return f"session: {session['handle']} {session['mode']} gen{session['generation']}{suffix}"


def _run_ask_cli(args: argparse.Namespace, parser: argparse.ArgumentParser, argv: list[str] | None) -> int:
    from peerhub.extensions.ask import ask
    from peerhub.extensions.bridge import normalize_session_selector
    normalize_session_selector(fresh=args.fresh, session=args.session)
    if (args.prompt is None) == (args.query_file is None):
        parser.error("ask requires exactly one of prompt or --query-file")
    prompt = Path(args.query_file).read_text(encoding="utf-8-sig") if args.query_file else args.prompt
    # An explicit provider workspace owns its default store, even when cwd has another store.
    import os
    actual = sys.argv[1:] if argv is None else argv
    explicit_db = any(a == "--db" or a.startswith("--db=") for a in actual) or "PEERHUB_DB" in os.environ
    if args.workspace and not explicit_db:
        args.db = _resolve_db_path(DEFAULT_DB_PATH, workspace_root=Path(args.workspace))
    has_streamed = False
    output_has_newline = False
    def show_output(text: str) -> None:
        nonlocal has_streamed, output_has_newline
        has_streamed = True
        output_has_newline = text.endswith("\n")
        print(text, end="", flush=True)
    try:
        result = ask(args.db, args.peer, prompt, stream_id=args.stream_id, request_id=args.request_id,
                     author=args.author, workspace=args.workspace, model=args.model, effort=args.effort,
                     profile=args.profile, silence_timeout_s=args.silence_timeout_seconds, writable=args.writable,
                     timeout_s=args.timeout_seconds, max_bytes=args.max_output_bytes,
                     fresh=args.fresh, session=args.session,
                     on_output=None if args.json else show_output)
    except Exception:
        if has_streamed and not args.json and not output_has_newline:
            print()
        raise
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=True))
    elif result["status"] in ("delivered", "recovered_terminal") and result.get("response") is not None:
        if has_streamed and not result.get("display_truncated"):
            if not output_has_newline:
                print()
        else:
            if has_streamed:
                print()
            print(result["response"])
    elif result["status"] == "not_executed":
        reason = result.get("reason", "refusal")
        if reason in ("quota_exhausted", "usage_limit"):
            reason_str = "usage limit / quota exhausted"
        elif reason == "invalid_model":
            reason_str = "invalid model selection"
        elif reason == "auth_failure":
            reason_str = "authentication failure"
        else:
            reason_str = reason.replace("_", " ")
        reset_time = result.get("reset_time") or result.get("resets_at")
        reset_suffix = f"; reset: {reset_time}" if reset_time else ""
        print(f"Task outcome: NOT_EXECUTED (provider refusal: {reason_str}{reset_suffix}; no tool execution observed)", file=sys.stderr)
    else:
        print(json.dumps(result, ensure_ascii=True), file=sys.stderr)
    session = result.get("session")
    if not args.json and isinstance(session, dict):
        session = cast("dict[str, Any]", session)
        if has_streamed and not output_has_newline and result["status"] not in ("delivered", "recovered_terminal"):
            print()
        print(_session_trailer(session))
    error: dict[str, Any] | None = result.get("error")
    if error is not None and error.get("code") != "session_lost" and (error.get("code") == "legacy_selector_unsupported" or str(error.get("code", "")).startswith("session_")):
        return 2
    return 0 if result["status"] in ("delivered", "recovered_terminal") else 1


def _run_observation_cli(args: argparse.Namespace) -> int:
    from peerhub.cli.store_select import report_missing_store
    if getattr(args, "db_source", "default") not in ("explicit", "env") and report_missing_store(args.db, json_output=True):
        return 5
    from peerhub.extensions.quota_capture import refresh_quota
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    CoreStore(args.db)  # explicit --db/PEERHUB_DB retains collection bootstrap semantics
    result = refresh_quota(args.db, args.peers, sys_dir=args.sys_dir, deadline_sec=args.timeout_seconds)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    _print_warnings(result)
    return 0 if result["status"] == "OK" else 1


def _reject_dashboard_flags_on_subcommand(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Dashboard-only flags must not be accepted and silently ignored by a diag subcommand."""
    if args.action is None:
        return
    if args.view != "plain":
        parser.error("diag --view applies to the dashboard, not to diag subcommands")
    if args.json and args.action == "health":
        parser.error("diag health always prints JSON; --json applies to the dashboard and `diag quota`")


def _run_light_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Commands that never open CoreStore through main(): the monitor loop and explicit collection."""
    if args.subcommand == "monitor":
        from peerhub.cli.monitor import run_monitor
        return run_monitor(args, parser)
    return _run_observation_cli(args)


def _run_diag_cli(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    _reject_dashboard_flags_on_subcommand(parser, args)
    if args.action is not None and (args.live or args.count or args.interval_seconds != 2):
        parser.error("--live/--cycles/--interval-seconds apply to the dashboard, not diag subcommands")
    if not args.live and (args.count or args.interval_seconds != 2):
        parser.error("--cycles/--interval-seconds require --live")
    from peerhub.cli.store_select import report_missing_store
    if args.action is None and report_missing_store(args.db, json_output=args.json):
        return 5
    try:
        from peerhub.extensions.diag import ReadonlyDiag
        from peerhub.extensions import diag_quota
    except ImportError as e:
        print(f"ERROR: the diagnostics extension is unavailable ({e})", file=sys.stderr)
        return 5
    if args.action is None:
        from peerhub.extensions.diag_watch import dashboard_snapshots
        from peerhub.cli.view import FrameOutput, dashboard_data, format_frame, use_color
        code = 0
        output = FrameOutput(interactive=not args.json, overwrite=args.live)
        try:
            for rep in dashboard_snapshots(args.db, interval_s=args.interval_seconds,
                                            count=args.count if args.live else 1):
                if args.json:
                    print(json.dumps(dashboard_data(rep), indent=None if args.live else 2, ensure_ascii=True), flush=True)
                else:
                    output.write(format_frame(rep, args.view, color=use_color(output.vt, os.environ),
                                              width=output.width(), store_path=str(args.db),
                                              store_source=getattr(args, "db_source", "unknown")))
                code = (5 if not Path(args.db).is_file() else 6 if rep.error and rep.error.startswith("SchemaVersionError")
                        else 0 if rep.status == "OK" else 4)
                if code:
                    break
        except KeyboardInterrupt:
            return code
        finally:
            output.close()
        return code
    if args.action == "quota":  # pure reader: no CoreStore (it would create/migrate the database)
        report_missing_store(args.obs_db or args.db)
        rep = diag_quota.quota_report(args.obs_db or args.db, peer=args.peer, pool=args.pool)
        code = {**diag_quota.EXIT_BY_STATUS, "PARTIAL": 4}[rep["status"]]
        print(json.dumps(rep, indent=2, ensure_ascii=True) if args.json else diag_quota.format_quota_table(rep))
        if code and not args.json:
            print(f"DIAG QUOTA {rep['status']}: {rep['error']}", file=sys.stderr)
        return code
    if args.action == "integrity":
        if not Path(args.db).is_file():
            report_missing_store(args.db)
            result = {"status": "UNAVAILABLE", "error": f"database not found: {args.db}"}
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 5
        try:
            from peerhub.extensions.diag import integrity_check
            result = integrity_check(args.db)
        except SchemaVersionError as exc:
            result = {"status": "SCHEMA_VERSION_ERROR", "error": str(exc)}
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 6
        except sqlite3.Error as exc:
            result = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 4
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0 if result["status"] == "OK" else 4
    if args.action == "health":  # pure reader as well: never CoreStore (no create, no migration of a missing/old --db)
        if not Path(args.db).is_file():
            report_missing_store(args.db)
            result: dict[str, object] = {"status": "UNAVAILABLE", "stream_id": args.stream_id, "error": f"database not found: {args.db}"}
            print(json.dumps(result, indent=2, ensure_ascii=True))
            print(f"DIAG HEALTH UNAVAILABLE: {result['error']}", file=sys.stderr)
            return 5
        diag = ReadonlyDiag(args.db)
        if args.stream_id:
            result = diag.inspect_stream_health(args.stream_id)
        else:
            rep = diag.render(["peers", "streams", "resource_pools", "observations"])
            if rep.status == "FAILED":
                result = {"status": "ERROR", "error": rep.error}
            else:
                streams_sec = rep.sections.get("streams")
                streams_data: list[dict[str, Any]] = streams_sec.data.get("streams", []) if streams_sec and streams_sec.status == "OK" else []
                peers_sec = rep.sections.get("peers")
                peers_data: list[dict[str, Any]] = peers_sec.data.get("peers", []) if peers_sec and peers_sec.status == "OK" else []
                result = {
                    "schema_version": "2.0",
                    "status": rep.status,
                    "store": args.db,
                    "store_selection": {"path": args.db, "source": getattr(args, "db_source", "unknown")},
                    "snapshot": rep.snapshot,
                    "total_peers": len(peers_data),
                    "total_streams": len(streams_data),
                    "stream_sample": [s["stream_id"] for s in streams_data[:5]],
                }
        result["observations"] = diag_quota.observations_summary(args.obs_db or args.db)
        if args.obs_db is not None and result["observations"]["status"] != "OK" and result.get("status") == "OK":
            result["status"] = "PARTIAL"
        result["schema_version"] = "2.0"
        print(json.dumps(result, indent=2, ensure_ascii=True))
        if str(result.get("error", "")).startswith("SchemaVersionError"):
            print(f"SCHEMA VERSION ERROR: {result['error']}", file=sys.stderr)
            return 6
        if result.get("status") == "NOT_FOUND":
            return 1
        if result.get("status") in ("ERROR", "PARTIAL"):
            return 4
        return 0
    return 0


def _run_core_cli(args: argparse.Namespace, store: CoreStore) -> int:
    if args.subcommand == "peer":
        if args.action == "register":
            p = store.register_peer(
                Peer(
                    peer_id=args.peer_id,
                    display_name=args.display_name,
                    adapter_ref=args.adapter_ref,
                )
            )
            print(_dump(p))
        elif args.action == "list":
            after = _page_cursor("peer", args.cursor)
            fetched = store.list_peers_page(after_peer_id=after, limit=args.limit + 1)
            items = fetched[:args.limit]
            next_cursor = _make_page_cursor("peer", items[-1].peer_id) if len(fetched) > args.limit else None
            _print_page([item.model_dump(mode="json") for item in items], next_cursor, args.format,
                        "peer_id", "display_name", store_selection={"path": args.db, "source": getattr(args, "db_source", "unknown")})
        elif args.action == "get":
            p = store.get_peer(args.peer_id)
            if not p:
                print(f"Peer not found: {args.peer_id}", file=sys.stderr)
                return 1
            print(_dump(p))

    elif args.subcommand == "stream":
        if args.action == "create":
            s = store.create_stream(
                Stream(
                    stream_id=args.stream_id,
                    title=args.title,
                    members=args.members,
                )
            )
            print(_dump(s))
        elif args.action == "show":
            s = store.get_stream(args.stream_id)
            if not s:
                print(f"Stream not found: {args.stream_id}", file=sys.stderr)
                return 1
            print(_dump(s))
        elif args.action == "list":
            after = _page_cursor("stream", args.cursor)
            fetched = store.list_streams_page(after_stream_id=after, limit=args.limit + 1)
            items = fetched[:args.limit]
            next_cursor = _make_page_cursor("stream", items[-1].stream_id) if len(fetched) > args.limit else None
            _print_page([item.model_dump(mode="json") for item in items], next_cursor, args.format,
                        "stream_id", "state", "title", "members",
                        store_selection={"path": args.db, "source": getattr(args, "db_source", "unknown")})

    elif args.subcommand == "record":
        if args.action == "append":
            try:
                body_data: Any = json.loads(args.body)
            except Exception:
                body_data = args.body

            rec = store.append_record(
                stream_id=args.stream_id,
                author_peer_id=args.author_peer_id,
                kind=args.kind,
                body=body_data,
                idempotency_key=args.idemp_key,
                targets=args.targets,
                created_at=args.created_at or utc_now_iso(),
            )
            print(_dump(rec))

        elif args.action == "read":
            records = store.read_records(
                stream_id=args.stream_id,
                after_position=args.after,
                limit=args.limit,
            )
            out = [r.model_dump() for r in records]
            print(json.dumps(out, indent=2, ensure_ascii=True))

    elif args.subcommand == "offset":
        if args.action == "get":
            off = store.get_offset(args.peer_id, args.stream_id)
            print(_dump(off))
        elif args.action == "advance":
            off = store.advance_offset_cas(
                peer_id=args.peer_id,
                stream_id=args.stream_id,
                new_position=args.new_position,
                expected_revision=args.expected_revision,
            )
            print(_dump(off))

    return 0


def _print_page(items: list[dict[str, Any]], next_cursor: str | None, output_format: str,
                *columns: str, store_selection: dict[str, str]) -> None:
    use_json = output_format == "json" or (output_format == "auto" and not sys.stdout.isatty())
    if use_json:
        print(json.dumps({"schema_version": "1.0", "store_selection": store_selection,
                          "items": items, "next_cursor": next_cursor}, ensure_ascii=True, indent=2))
        return
    if not items:
        print("(no results)")
        return
    values = [[", ".join(row[key]) if isinstance(row[key], list) else str(row[key] or "") for key in columns]
              for row in items]
    widths = [max(len(column), *(len(row[i]) for row in values)) for i, column in enumerate(columns)]
    print("  ".join(column.ljust(widths[i]) for i, column in enumerate(columns)))
    print("  ".join("-" * width for width in widths))
    for row in values:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))
    if next_cursor:
        print(f"next_cursor: {next_cursor}")


def _run_profile_cli(args: argparse.Namespace) -> int:
    from peerhub.extensions.adapters.profiles import inspect_profile, list_profiles

    workspace = args.workspace or Path.cwd()
    try:
        if args.action == "list":
            profiles = list_profiles(workspace)
            payload: dict[str, Any] = {"schema_version": "1.0", "workspace": str(workspace), "profiles": profiles}
        else:
            profile = inspect_profile(args.profile, workspace)
            payload = {"schema_version": "1.0", "workspace": str(workspace), **profile}
    except (OSError, ValueError) as exc:
        use_json = args.format == "json" or (args.format == "auto" and not sys.stdout.isatty())
        error = {"schema_version": "1.0", "status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        if use_json:
            print(json.dumps(error, ensure_ascii=True, indent=2))
        else:
            print(f"ERROR: {error['error']}", file=sys.stderr)
        return 1
    use_json = args.format == "json" or (args.format == "auto" and not sys.stdout.isatty())
    if use_json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0
    if args.action == "list":
        rows = [[profile["profile"], str(profile["fields"]["model"]["value"] or "CLI default"),
                 str(profile["fields"]["effort"]["value"] or "provider default")] for profile in payload["profiles"]]
        columns = ["PROFILE", "MODEL", "EFFORT"]
    else:
        rows = [[key, str(field["value"] or "(unset)"), str(field["source"])]
                for key, field in payload["fields"].items()]
        columns = ["FIELD", "VALUE", "SOURCE"]
    if not rows:
        print("(no profiles)")
        return 0
    widths = [max(len(col), *(len(row[index]) for row in rows)) for index, col in enumerate(columns)]
    print("  ".join(col.ljust(widths[i]) for i, col in enumerate(columns)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))
    return 0


def _run_session_cli(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.is_file():
        from peerhub.cli.store_select import report_missing_store
        report_missing_store(args.db, json_output=True)
        return 5
    from contextlib import closing
    from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
    from peerhub.extensions.observation_model import read_only_authorizer

    stat = db_path.stat()
    identity = f"{db_path.resolve()}|{stat.st_dev}|{stat.st_ino}".casefold()
    store_id = hashlib.sha256(identity.encode()).hexdigest()
    generation_path = db_path.parent / "workspace.generation"
    lineage = generation_path.read_text(encoding="utf-8").strip() if generation_path.is_file() else ""
    filters = {"stream": getattr(args, "stream", None), "peer": getattr(args, "peer", None)}
    use_json = args.format == "json" or (args.format == "auto" and not sys.stdout.isatty())

    def emit(payload: dict[str, Any]) -> int:
        if use_json:
            print(json.dumps(payload, ensure_ascii=True, indent=2))
        elif payload.get("status") == "NOT_FOUND":
            print(f"Session not found: {args.handle}", file=sys.stderr)
        elif args.action == "show":
            for key, value in payload.get("session", {}).items():
                print(f"{key}: {value}")
        else:
            rows = payload.get("items", [])
            if not rows:
                print("(no sessions)")
            else:
                keys = ("handle", "stream_id", "peer_id", "generation", "state", "resumable", "last_seen")
                widths = [max(len(key), *(len(str(row.get(key, ""))) for row in rows)) for key in keys]
                print("  ".join(key.upper().ljust(widths[i]) for i, key in enumerate(keys)))
                print("  ".join("-" * width for width in widths))
                for row in rows:
                    print("  ".join(str(row.get(key, "")).ljust(widths[i]) for i, key in enumerate(keys)))
                if payload.get("next_cursor"):
                    print(f"next_cursor: {payload['next_cursor']}")
        status = payload.get("status")
        return 1 if status == "NOT_FOUND" else 4 if status == "ERROR" else 0

    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
                raise sqlite3.OperationalError("session inspection connection is not read-only")
            conn.set_authorizer(read_only_authorizer(allow_transactions=True))
            conn.execute("BEGIN")
            conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if schema_version > SUPPORTED_SCHEMA_VERSION:
                raise SchemaVersionError(future_schema_message(schema_version, SUPPORTED_SCHEMA_VERSION))
            has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bridge_session_history'").fetchone()
            if args.action == "show":
                row = None if not has_table else conn.execute(
                    "SELECT session_handle,stream_id,peer_id,session_generation,state,resumable,last_seen,"
                    "context_watermark,bootstrap_truncated,context_watermark_version,created_at,tombstoned_at "
                    "FROM bridge_session_history WHERE session_handle=?", (args.handle,)).fetchone()
                if row is None:
                    return emit({"schema_version": "1.0", "status": "NOT_FOUND", "handle": args.handle})
                return emit({"schema_version": "1.0", "status": "OK", "session": _session_public_row(row)})
            if not has_table:
                return emit({"schema_version": "1.0", "store_selection": {"path": str(db_path), "source": getattr(args, "db_source", "unknown")},
                             "items": [], "next_cursor": None})
            cursor = None if not args.cursor else _decode_session_cursor(args.cursor, store_id=store_id, lineage=lineage, filters=filters)
            high_water = int(cursor["high_water"]) if cursor else int(
                conn.execute("SELECT COALESCE(MAX(rowid),0) FROM bridge_session_history").fetchone()[0])
            where = ["rowid <= ?"]
            params: list[Any] = [high_water]
            if filters["stream"] is not None:
                where.append("stream_id = ?")
                params.append(filters["stream"])
            if filters["peer"] is not None:
                where.append("peer_id = ?")
                params.append(filters["peer"])
            if cursor:
                stream, peer, generation, handle = cursor["after"]
                where.append("(stream_id > ? OR (stream_id = ? AND peer_id > ?) OR "
                             "(stream_id = ? AND peer_id = ? AND session_generation < ?) OR "
                             "(stream_id = ? AND peer_id = ? AND session_generation = ? AND session_handle < ?))")
                params.extend([stream, stream, peer, stream, peer, generation, stream, peer, generation, handle])
            params.append(args.limit + 1)
            rows = conn.execute(
                "SELECT rowid,session_handle,stream_id,peer_id,session_generation,state,resumable,last_seen,"
                "context_watermark,bootstrap_truncated,context_watermark_version,created_at,tombstoned_at "
                "FROM bridge_session_history WHERE " + " AND ".join(where) +
                " ORDER BY stream_id,peer_id,session_generation DESC,session_handle DESC LIMIT ?", params).fetchall()
            page_rows = rows[:args.limit]
            next_cursor = None
            if len(rows) > args.limit and page_rows:
                last = page_rows[-1]
                next_cursor = _encode_session_cursor({"v": 1, "store": store_id, "lineage": lineage, "filters": filters,
                    "order": "scope-generation-handle-v1", "high_water": high_water,
                    "after": [last["stream_id"], last["peer_id"], last["session_generation"], last["session_handle"]]})
            return emit({"schema_version": "1.0", "store_selection": {"path": str(db_path), "source": getattr(args, "db_source", "unknown")},
                         "items": [_session_public_row(row) for row in page_rows], "next_cursor": next_cursor})
    except SchemaVersionError as exc:
        error = {"schema_version": "1.0", "status": "SCHEMA_VERSION_ERROR", "error": str(exc)}
        if use_json:
            print(json.dumps(error, ensure_ascii=True, indent=2))
        else:
            print(f"SCHEMA VERSION ERROR: {exc}", file=sys.stderr)
        return 6
    except ValueError as exc:
        error = {"schema_version": "1.0", "status": "ERROR", "error": str(exc)}
        if use_json:
            print(json.dumps(error, ensure_ascii=True, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        error = {"schema_version": "1.0", "status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        if use_json:
            print(json.dumps(error, ensure_ascii=True, indent=2))
        else:
            print(f"ERROR: {error['error']}", file=sys.stderr)
        return 4


def _session_public_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "handle": str(row["session_handle"]), "stream_id": str(row["stream_id"]),
        "peer_id": str(row["peer_id"]), "generation": int(row["session_generation"]),
        "state": str(row["state"]), "resumable": bool(row["resumable"]),
        "last_seen": float(row["last_seen"]), "context_watermark": row["context_watermark"],
        "bootstrap_truncated": None if row["bootstrap_truncated"] is None else bool(row["bootstrap_truncated"]),
        "context_watermark_version": int(row["context_watermark_version"]),
        "created_at": float(row["created_at"]), "tombstoned_at": row["tombstoned_at"],
    }


def main(argv: list[str] | None = None, prog: str = "peerhub") -> int:
    tolerant_streams()
    parser = build_parser(prog=prog)
    actual = sys.argv[1:] if argv is None else argv
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 2 and "ask" in actual and "--json" in actual:
            print(json.dumps(dict(status="error", response=None, session=None,
                                  error=dict(code="invalid_arguments", message="Invalid ask arguments."))))
            return 2
        raise
    def error_output(code: str, message: str, session: dict[str, Any] | None = None) -> None:
        if args.subcommand == "ask" and args.json:
            print(json.dumps(dict(status="error", response=None, session=session, error=dict(code=code, message=message)), ensure_ascii=True))
        else:
            print(message, file=sys.stderr)
            if args.subcommand == "ask" and session is not None:
                print(_session_trailer(session))
    explicit_db = any(a == "--db" or a.startswith("--db=") for a in actual)
    try:
        if hasattr(args, "db") and args.db:
            selection = _select_db(args.db, discover=not explicit_db)
            args.db, args.db_source = selection.path, selection.source
        if getattr(args, "obs_db", None):
            args.obs_db = _resolve_db_path(args.obs_db, use_env=False, discover=False)
        if args.subcommand == "ask":
            return _run_ask_cli(args, parser, argv)
        if args.subcommand == "profile":
            return _run_profile_cli(args)
        if args.subcommand == "session":
            return _run_session_cli(args)
        if args.subcommand in ("monitor", "observation"):
            return _run_light_command(args, parser)
        if args.subcommand == "diag":  # optional first-party extension: Core must work without it (E2E-009)
            return _run_diag_cli(args, parser)
        core_read = ((args.subcommand, args.action) in {
            ("peer", "get"), ("peer", "list"), ("stream", "show"), ("stream", "list"),
            ("record", "read"), ("offset", "get")
        })
        if core_read:
            from peerhub.cli.store_select import report_missing_store
            if report_missing_store(args.db):
                return 5
        store = CoreStore(args.db, readonly=core_read)
        return _run_core_cli(args, store)

    except (StorageReadOnlyError, StorageFullError, StorageCorruptError) as e:
        error_output("storage_corrupt", f"STORAGE ERROR ({type(e).__name__}): {e}", getattr(e, "session", None))
        return 4
    except SchemaVersionError as e:
        error_output("schema_version_error", f"SCHEMA VERSION ERROR: {e}")
        return 6
    except IdempotencyConflictError as e:
        error_output("idempotency_conflict", f"CONFLICT ERROR: {e}")
        return 2
    except CasMismatchError as e:
        error_output("cas_conflict", f"CAS ERROR: {e}")
        return 3
    except ValueError as e:
        error_output(getattr(e, "code", "invalid_arguments"), f"ERROR: {e}")
        return 2 if args.subcommand == "ask" and getattr(e, "code", None) != "session_lost" else 1
    except SystemExit as e:
        if args.subcommand == "ask" and args.json and e.code == 2:
            error_output("invalid_arguments", "Invalid ask arguments.")
            return 2
        raise
    except Exception as e:
        error_output("delivery_failed", f"ERROR: {e}", getattr(e, "session", None))
        return 1


if __name__ == "__main__":
    sys.exit(main())
