"""PeerHub CLI Entrypoint.

Commands (canonical inventory: docs/implementation/command_inventory.json, derived from this parser by tools/command_inventory.py):
  peer register / get
  stream create / show
  record append / read
  offset get / advance
  diag health / quota   (read-only; quota never refreshes evidence)
  legacy-import dry-run / apply   (explicit side-by-side importer of a legacy v0.x store; MIGRATION_CUTOVER step 7)
Exit codes: 0 ok, 1 error, 2 idempotency conflict, 3 CAS lost, 4 storage fault, 5 diag unavailable, 6 schema version, 7 legacy import refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from pydantic import BaseModel

from peerhub._console import tolerant_streams
from peerhub.cli.support import print_warnings as _print_warnings
from peerhub.core.models import Peer, Stream, utc_now_iso
from peerhub.core.schema_version import SchemaVersionError
from peerhub.core.store import CoreStore, IdempotencyConflictError, CasMismatchError, StorageCorruptError, StorageFullError, StorageReadOnlyError


DEFAULT_DB_PATH = ".peerhub/core.db"
_PREVIOUS_DEFAULT_DB_PATH = ".peerhub/m1.db"  # existing durable data: read-only discovery, never renamed here


def _resolve_db_path(configured_path: str, *, use_env: bool = True, discover: bool = True,
                     workspace_root: Path | None = None) -> str:
    import os
    if use_env and "PEERHUB_DB" in os.environ:
        return os.environ["PEERHUB_DB"]
    if configured_path != DEFAULT_DB_PATH or not discover:
        return configured_path
    if workspace_root is not None:
        roots = [workspace_root.resolve()]
    else:
        current = Path.cwd().resolve()
        roots = [current, current / "peerhub", *current.parents]
    for root in roots:
        for relative in (DEFAULT_DB_PATH, _PREVIOUS_DEFAULT_DB_PATH):
            candidate = root / relative
            if candidate.is_file():
                return str(candidate)
    return str(workspace_root / DEFAULT_DB_PATH) if workspace_root is not None else configured_path


def build_parser(prog: str = "peerhub") -> argparse.ArgumentParser:
    description = "PeerHub CLI"
    parser = argparse.ArgumentParser(prog=prog, description=description)
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

    # stream
    stream_parser = subparsers.add_parser("stream", help="Stream commands")
    st_sub = stream_parser.add_subparsers(dest="action", required=True)
    
    s_create = st_sub.add_parser("create", help="Create a stream")
    s_create.add_argument("--stream", required=True, dest="stream_id")
    s_create.add_argument("--title")
    s_create.add_argument("--members", nargs="*", default=[])

    s_show = st_sub.add_parser("show", help="Show stream details")
    s_show.add_argument("--stream", required=True, dest="stream_id")

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

    ask_parser = subparsers.add_parser("ask", help="Deliver one prompt through the durable Session Bridge")
    ask_parser.add_argument("peer", help="Peer ID (built-ins: cx/codex, cc/claude, ag/agy)")
    ask_parser.add_argument("prompt", nargs="?")
    ask_parser.add_argument("--query-file", help="UTF-8 prompt file; exclusive with prompt")
    ask_parser.add_argument("--stream", dest="stream_id", help="Conversation Stream (default: peer:<id>:chat)")
    ask_parser.add_argument("--request-id", help="Reuse on retries; uncertain execution is never replayed automatically")
    ask_parser.add_argument("--author-peer", dest="author", default="user", help="Peer ID recorded as the prompt author (auto-registered)")
    ask_parser.add_argument("-w", "--workspace", help="Provider working directory; default: current directory")
    ask_parser.add_argument("--model")
    ask_parser.add_argument("--effort")
    ask_parser.add_argument("-p", "--profile", help="Explicit Runtime Target profile; model/effort override its defaults")
    ask_parser.add_argument("--timeout-seconds", type=float, default=60)
    ask_parser.add_argument("--silence-timeout-seconds", type=float, default=None,
                            help="Abort after this many seconds without stdout/stderr; execution remains uncertain")
    ask_parser.add_argument("--max-output-bytes", type=int, default=1_000_000)
    ask_parser.add_argument("--json", action="store_true")

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
    diag_parser.add_argument("--cycles", dest="count", type=int, default=0, help="Live snapshot count; 0 watches until interrupted")
    diag_parser.epilog = "examples: peerhub diag | peerhub diag --json | peerhub diag --live | peerhub observation refresh | peerhub monitor (collects quota evidence every cycle and draws the dashboard)"
    diag_sub = diag_parser.add_subparsers(dest="action", required=False)
    
    d_health = diag_sub.add_parser("health", help="Inspect stream or store health (read-only)")
    d_health.add_argument("--stream", default=None, dest="stream_id",
                          help="Optional stream ID to inspect (default: inspects entire store and active streams)")
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
    d_quota.add_argument("--json", action="store_true", help="Machine-readable output (schema_version 1.0) instead of the table")
    d_quota.epilog = "examples: peerhub --db ws.db diag quota --json | peerhub --db ws.db diag quota --pool P --observation-db other_ws.db"

    # legacy import (the --db option is the PeerHub TARGET store; --source is the legacy v0.x database, opened read-only)
    legacy_parser = subparsers.add_parser("legacy-import", help="Import a legacy v0.x store (dry-run first)")
    legacy_sub = legacy_parser.add_subparsers(dest="action", required=True)
    for name in ("dry-run", "apply"):
        lp = legacy_sub.add_parser(name, help=f"{name} the legacy import")
        lp.add_argument("--source", required=True, help="Path to the legacy SQLite database (never modified)")
        if name == "apply":
            lp.add_argument("--plan-digest", default=None, help="plan_digest from a prior dry-run; apply is refused if the plan changed")

    return parser


def _dump(model: BaseModel) -> str:
    """JSON with ASCII escapes only: lossless for every Unicode text whatever the console encoding (no encoding is forced on the user)."""
    return json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=True)



def _run_ask_cli(args: argparse.Namespace, parser: argparse.ArgumentParser, argv: list[str] | None) -> int:
    from peerhub.extensions.ask import ask
    if (args.prompt is None) == (args.query_file is None):
        parser.error("ask requires exactly one of prompt or --query-file")
    prompt = Path(args.query_file).read_text(encoding="utf-8-sig") if args.query_file else args.prompt
    # An explicit provider workspace owns its default store, even when cwd has another store.
    import os
    actual = sys.argv[1:] if argv is None else argv
    explicit_db = any(a == "--db" or a.startswith("--db=") for a in actual) or "PEERHUB_DB" in os.environ
    if args.workspace and not explicit_db:
        args.db = _resolve_db_path(DEFAULT_DB_PATH, workspace_root=Path(args.workspace))
    streamed: list[str] = []
    def show_output(text: str) -> None:
        streamed.append(text)
        print(text, end="", flush=True)
    result = ask(args.db, args.peer, prompt, stream_id=args.stream_id, request_id=args.request_id,
                 author=args.author, workspace=args.workspace, model=args.model, effort=args.effort,
                 profile=args.profile, silence_timeout_s=args.silence_timeout_seconds,
                 timeout_s=args.timeout_seconds, max_bytes=args.max_output_bytes,
                 on_output=None if args.json else show_output)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=True))
    elif result["status"] in ("delivered", "recovered_terminal") and result.get("response") is not None:
        if streamed:
            print()
        else:
            print(result["response"])
    else:
        print(json.dumps(result, ensure_ascii=True), file=sys.stderr)
    return 0 if result["status"] in ("delivered", "recovered_terminal") else 1


def _run_observation_cli(args: argparse.Namespace) -> int:
    from peerhub.extensions.quota_capture import refresh_quota
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    CoreStore(args.db)  # infrastructure bootstrap; the collector itself owns only Observation tables
    result = refresh_quota(args.db, args.peers, sys_dir=args.sys_dir, deadline_sec=args.timeout_seconds)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    _print_warnings(result)
    return 0 if result["status"] == "OK" else 1


def main(argv: list[str] | None = None, prog: str = "peerhub") -> int:
    tolerant_streams()
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    actual = sys.argv[1:] if argv is None else argv
    explicit_db = any(a == "--db" or a.startswith("--db=") for a in actual)
    if hasattr(args, "db") and args.db:
        args.db = _resolve_db_path(args.db, discover=not explicit_db)
    if getattr(args, "obs_db", None):
        args.obs_db = _resolve_db_path(args.obs_db, use_env=False, discover=False)

    try:
        if args.subcommand == "ask":
            return _run_ask_cli(args, parser, argv)
        if args.subcommand == "monitor":
            from peerhub.cli.monitor import run_monitor
            return run_monitor(args, parser)
        if args.subcommand == "observation":
            return _run_observation_cli(args)
        if args.subcommand == "legacy-import":  # before CoreStore(): a dry-run must not create or migrate the target
            from peerhub.core.legacy_import import LegacyImporter, LegacyPlanChangedError, LegacySourceError

            try:
                imp = LegacyImporter(args.source, args.db)
                report = imp.dry_run() if args.action == "dry-run" else imp.apply(expected_plan_digest=args.plan_digest)
            except (LegacySourceError, LegacyPlanChangedError) as e:
                print(f"LEGACY IMPORT REFUSED ({type(e).__name__}): {e}", file=sys.stderr)
                return 7
            print(json.dumps(report, indent=2, ensure_ascii=True))
            return 0
        if args.subcommand == "diag":  # optional first-party extension: Core must work without it (E2E-009)
            if args.action is not None and (args.live or args.count or args.interval_seconds != 2):
                parser.error("--live/--cycles/--interval-seconds apply to the dashboard, not diag subcommands")
            if not args.live and (args.count or args.interval_seconds != 2):
                parser.error("--cycles/--interval-seconds require --live")
            try:
                from peerhub.extensions.diag import ReadonlyDiag
                from peerhub.extensions import diag_quota
            except ImportError as e:
                print(f"ERROR: the diagnostics extension is unavailable ({e})", file=sys.stderr)
                return 5
            if args.action is None:
                from dataclasses import asdict
                from peerhub.extensions.diag_watch import dashboard_snapshots
                code = 0
                try:
                    for rep in dashboard_snapshots(args.db, interval_s=args.interval_seconds,
                                                    count=args.count if args.live else 1):
                        print(json.dumps(asdict(rep), indent=None if args.live else 2, ensure_ascii=True)
                              if args.json else diag_quota.format_dashboard(rep), flush=True)
                        code = (5 if not Path(args.db).is_file() else 6 if rep.error and rep.error.startswith("SchemaVersionError")
                                else 0 if rep.status == "OK" else 4)
                        if code:
                            break
                except KeyboardInterrupt:
                    return code
                return code
            if args.action == "quota":  # pure reader: no CoreStore (it would create/migrate the database)
                rep = diag_quota.quota_report(args.obs_db or args.db, peer=args.peer, pool=args.pool)
                code = {**diag_quota.EXIT_BY_STATUS, "PARTIAL": 4}[rep["status"]]
                print(json.dumps(rep, indent=2, ensure_ascii=True) if args.json else diag_quota.format_quota_table(rep))
                if code and not args.json:
                    print(f"DIAG QUOTA {rep['status']}: {rep['error']}", file=sys.stderr)
                return code
            if args.action == "health":  # pure reader as well: never CoreStore (no create, no migration of a missing/old --db)
                if not Path(args.db).is_file():
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
                            "status": rep.status,
                            "store": args.db,
                            "snapshot": rep.snapshot,
                            "total_peers": len(peers_data),
                            "total_streams": len(streams_data),
                            "active_streams": [s["stream_id"] for s in streams_data[:5]],
                        }
                result["observations"] = diag_quota.observations_summary(args.obs_db or args.db)
                print(json.dumps(result, indent=2, ensure_ascii=True))
                if str(result.get("error", "")).startswith("SchemaVersionError"):
                    print(f"SCHEMA VERSION ERROR: {result['error']}", file=sys.stderr)
                    return 6
                return 0
        store = CoreStore(args.db)
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

        elif args.subcommand == "record":
            if args.action == "append":
                try:
                    body_data = json.loads(args.body)
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

    except (StorageReadOnlyError, StorageFullError, StorageCorruptError) as e:
        print(f"STORAGE ERROR ({type(e).__name__}): {e}", file=sys.stderr)
        return 4
    except SchemaVersionError as e:
        print(f"SCHEMA VERSION ERROR: {e}", file=sys.stderr)
        return 6
    except IdempotencyConflictError as e:
        print(f"CONFLICT ERROR: {e}", file=sys.stderr)
        return 2
    except CasMismatchError as e:
        print(f"CAS ERROR: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
