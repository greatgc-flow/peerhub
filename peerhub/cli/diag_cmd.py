"""Read-only diagnostics commands."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from copy import copy
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from peerhub.cli.output import emit
from peerhub.cli.store_select import select_store
from peerhub.cli.support import error_line
from peerhub.core.schema_version import SchemaVersionError

if TYPE_CHECKING:
    from peerhub.cli.arguments import Subparsers


class _DiagRunningHelpFormatter(argparse.HelpFormatter):
    def add_argument(self, action: argparse.Action) -> None:
        if "--limit" in action.option_strings:
            # Resolve the fallback when rendering help so core commands need no extension import.
            try:
                from peerhub.extensions.diag_running import MAX_ROWS
            except ImportError:
                pass
            else:
                action = copy(action)
                action.help = f"{action.help} Omit to read up to {MAX_ROWS} rows."
        super().add_argument(action)


def register_diag_parser(subparsers: Subparsers) -> None:
    diag_parser = subparsers.add_parser("diag", help="Read-only, low-token diagnostics for AI agents (humans use `peerhub monitor`)")
    diag_parser.description = "Read-only, low-token diagnostics for AI agents; `diag --plan` is the one command to size parallel work. Humans use `peerhub monitor`."
    diag_parser.add_argument("--json", action="store_true")
    diag_parser.add_argument("--live", action="store_true", help="Watch read-only snapshots; never refresh provider evidence (a human fallback: `peerhub monitor` is the human screen)")
    diag_parser.add_argument("--interval-seconds", type=float, default=2,
                             help="Live snapshot interval in positive seconds (default %(default)s)")
    diag_parser.add_argument("--view", choices=["plain", "rich"], default="plain",
                             help="Dashboard style for ordinary and live snapshots. The plan uses its own text layout. With --live, snapshots every N s "
                                  "(a one-second interval targets 1 Hz). A TTY with VT overwrites in place. A non-TTY or a terminal "
                                  "without VT appends snapshots without escapes.")
    diag_parser.add_argument("--cycles", dest="count", type=int, default=0,
                             help="Number of live snapshots (default %(default)s, watch until interrupted)")
    diag_parser.add_argument("--plan", action="store_true",
                             help="Per-pool spend/hold/rest/blocked with target %%/h until reset, from stored observations only (no refresh)")
    diag_parser.add_argument("--peer", dest="plan_peers", action="append", metavar="PEER",
                             help="With --plan, show only these subjects (exact match, repeatable)")
    diag_parser.add_argument("--observation-db", dest="plan_obs_db", metavar="PATH",
                             help="With --plan, read observations from this other workspace database (read-only, never created). "
                                  "Load still comes from the selected store.")
    diag_parser.epilog = "Examples: peerhub diag --json | peerhub diag --live | peerhub monitor (refreshes quota when due on a TTY by default)"
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
    d_quota.add_argument("--max-age-seconds", type=_nonnegative_seconds, metavar="N",
                         help="Show quota and rate-limit observations at most N seconds old (default: any age)")
    d_quota.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    d_quota.epilog = "examples: peerhub --db ws.db diag quota --json | peerhub --db ws.db diag quota --pool P --observation-db other_ws.db"
    d_readiness = diag_sub.add_parser("readiness", help="Cached profile readiness (read-only; never probes)")
    d_readiness.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    d_readiness.epilog = "examples: peerhub diag readiness | peerhub --db ws.db diag readiness --json"

    d_running = diag_sub.add_parser("running", help="Lease-active asks (read-only; a lease is not proof a process is alive)",
                                    formatter_class=_DiagRunningHelpFormatter,
                                    description="Read-only. Deliveries whose claim lease has not expired and that are not acknowledged "
                                                "(certainty NOT_STARTED or STARTED), read from the store only: no provider is contacted "
                                                "and nothing is written or refreshed. A lease is not proof a process is alive; "
                                                "elapsed time comes from recorded start evidence and is null without it. "
                                                "Claims are matched to the workspace.generation file beside the database; without that file all generations match. "
                                                "Query cost grows with the delivery history of the live streams themselves (observed: about 45k SQLite VM steps at 9,000 rows in one stream). Rows are oldest first. Exit: 0 ok, 4 storage fault, 5 database unavailable, 6 schema version.")
    d_running.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    d_running.add_argument("--peer", help="Only this peer (exact match, e.g. cx)")
    d_running.add_argument("--limit", type=int, default=None, help="Maximum rows to read, 1 or more (default %(default)s).")
    d_running.epilog = "examples: peerhub diag running | peerhub --db ws.db diag running --json"

    d_integrity = diag_sub.add_parser("integrity", help="Run the explicit full SQLite integrity check (read-only; may take time)")
    d_integrity.epilog = "example: peerhub diag integrity"


def _nonnegative_seconds(value: str) -> int:
    message = "seconds must be a non-negative integer"
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(message) from exc
    if seconds < 0:
        raise argparse.ArgumentTypeError(message)
    return seconds


def _reject_dashboard_flags_on_subcommand(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Dashboard-only flags must not be accepted and silently ignored by a diag subcommand."""
    if args.plan and args.view != "plain":
        parser.error("diag --view rich applies to the dashboard")
    if args.action is None:
        return
    if args.view != "plain":
        parser.error("diag --view applies to the dashboard, not to diag subcommands")


def run_diag(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    _reject_dashboard_flags_on_subcommand(parser, args)
    if args.action is not None and (args.live or args.count or args.interval_seconds != 2):
        parser.error("--live/--cycles/--interval-seconds apply to the dashboard, not diag subcommands")
    if not args.live and (args.count or args.interval_seconds != 2):
        parser.error("--cycles/--interval-seconds require --live")
    if args.plan and (args.live or args.action is not None):
        parser.error("--plan is a one-shot snapshot; it cannot be combined with --live or diag subcommands")
    if not args.plan and (args.plan_peers is not None or args.plan_obs_db is not None):
        parser.error("diag --peer and --observation-db require --plan")
    from peerhub.cli.store_select import env_override_note, report_missing_store
    db_source = getattr(args, "db_source", None)
    is_explicit = db_source in ("explicit", "env")
    obs_db = select_store(args.plan_obs_db, use_env=False, discover=False).path if args.plan_obs_db else None
    if args.action is None and report_missing_store(
        obs_db or args.db, json_output=args.json, selected=is_explicit or obs_db is not None,
        source="explicit" if obs_db is not None else db_source,
    ):
        return 5
    try:
        from peerhub.extensions.diag import ReadonlyDiag
        from peerhub.extensions import diag_quota
    except ImportError as e:
        error_line("diag_unavailable", f"The diagnostics extension is unavailable ({e})", "peerhub guide")
        return 5
    store = {"path": str(Path(args.db).resolve()), "source": getattr(args, "db_source", "unknown")}  # #338: resolved
    store_note = env_override_note(store["path"], store["source"])
    if args.plan:  # #339: read-only, stored observations only (no refresh, no provider call)
        from peerhub._console import FrameOutput
        from peerhub.cli.view import render_plan
        from peerhub.extensions.diag_read_model import plan_data
        from peerhub.extensions.adapters.profiles import list_profiles
        from peerhub.extensions.diag_running import sample_load

        rep = diag_quota.read_quota_snapshot(obs_db or args.db, include_quota_samples=True)
        peers = frozenset(args.plan_peers or ())
        pools_of_profile: dict[str, list[str]] = {}
        for profile in list_profiles(Path.cwd()):
            pools_of_profile[str(profile["profile"])] = [str(pool) for pool in cast("list[object]", profile["fields"]["pools"]["value"] or [])]
        load = sample_load(args.db, pools_of_profile=pools_of_profile)
        profiles_by_pool: dict[str, list[str]] = {}
        for name, pools in pools_of_profile.items():
            for pool in pools:
                profiles_by_pool.setdefault(pool, []).append(name)
        profiles_by_pool = {pool: sorted(names) for pool, names in profiles_by_pool.items()}
        if args.json:
            plan_output = json.dumps(plan_data(rep, store, load, profiles_by_pool, peers=peers), separators=(",", ":"), ensure_ascii=True)
        else:
            plan_output = render_plan(rep, width=FrameOutput.width(), store_path=store["path"], store_source=store["source"],
                                      store_note=store_note, load=load, profiles_by_pool=profiles_by_pool,
                                      peers=peers, separate_observation_store=obs_db is not None and obs_db != args.db,
                                      unicode=(sys.stdout.encoding or "").lower().startswith("utf"))
        print(plan_output, flush=True)
        return 6 if rep.error and rep.error.startswith("SchemaVersionError") else 0 if rep.status == "OK" else 4
    if args.action is None:
        from peerhub.extensions.diag_watch import dashboard_snapshots
        from peerhub._console import FrameOutput, use_color
        from peerhub.cli.view import format_frame
        from peerhub.extensions.diag_read_model import dashboard_data
        code = 0
        output = FrameOutput(interactive=not args.json, overwrite=args.live)
        try:
            for rep in dashboard_snapshots(args.db, interval_s=args.interval_seconds,
                                            count=args.count if args.live else 1):
                if args.json:
                    data = dashboard_data(rep)
                    data["store_selection"] = store  # #338, additive
                    print(json.dumps(data, indent=None if args.live else 2, ensure_ascii=True), flush=True)
                else:
                    output.write(format_frame(rep, args.view, color=use_color(output.vt, os.environ),
                                              width=output.width(), store_path=store["path"],
                                              store_source=store["source"], store_note=store_note, show_quota_age=args.live))
                code = (5 if not Path(args.db).is_file() else 6 if rep.error and rep.error.startswith("SchemaVersionError")
                        else 0 if rep.status == "OK" else 4)
                if code:
                    break
        except KeyboardInterrupt:
            return code
        finally:
            output.close()
        return code
    if args.action == "readiness":
        from peerhub.extensions.diag_readiness import format_readiness, readiness_report
        from peerhub.cli.recovery import get_guidance
        rep = readiness_report(args.db)
        for profile in rep["profiles"]:
            action = profile.pop("action")
            stream = profile.pop("stream_id")
            if action:
                entry = get_guidance("ask_refusal" if action == "wait_reset" else
                                     "ask_delivery_failed" if action == "inspect_facts" else action,
                                     db_path=args.db, stream_id=stream)
                if entry is not None:
                    profile["next"] = {"code": action, "argv": entry.recover[0]["argv"]}
        print(json.dumps(rep, separators=(",", ":"), ensure_ascii=True) if args.json else format_readiness(rep))
        return diag_quota.EXIT_BY_STATUS[rep["status"]]
    if args.action == "running":  # pure reader: no CoreStore, no provider, no refresh
        from peerhub._console import FrameOutput
        from peerhub.extensions.diag_running import format_running, running_report
        if not Path(args.db).is_file():
            report_missing_store(args.db, json_output=args.json, selected=is_explicit, source=db_source)
            return 5
        rep = running_report(args.db, peer=args.peer, limit=args.limit)
        print(json.dumps(rep, indent=2, ensure_ascii=True) if args.json else format_running(rep, width=FrameOutput.width()))
        code = diag_quota.EXIT_BY_STATUS[rep["status"]]
        if code and not args.json:
            error_line("diag_unavailable", str(rep["error"] or rep["status"]), "peerhub diag health")
        return code
    if args.action == "quota":  # pure reader: no CoreStore (it would create/migrate the database)
        report_missing_store(args.obs_db or args.db, selected=is_explicit or bool(args.obs_db), source="explicit" if args.obs_db else db_source)
        rep = diag_quota.quota_report(args.obs_db or args.db, peer=args.peer, pool=args.pool,
                                     max_age_seconds=args.max_age_seconds)
        code = {**diag_quota.EXIT_BY_STATUS, "PARTIAL": 4}[rep["status"]]
        print(json.dumps(rep, indent=2, ensure_ascii=True) if args.json else
              diag_quota.format_quota_table(rep, max_age_seconds=args.max_age_seconds))
        if code and Path(args.obs_db or args.db).is_file():
            error_line("diag_unavailable", str(rep.get("error") or rep["status"]), "peerhub observation refresh")
        return code
    if args.action == "integrity":
        if not Path(args.db).is_file():
            report_missing_store(args.db, selected=is_explicit, source=db_source)
            result = {"status": "UNAVAILABLE", "error": f"database not found: {args.db}"}
            if args.json:
                emit(result, True)
            return 5
        try:
            from peerhub.extensions.diag import integrity_check
            result = integrity_check(args.db)
        except SchemaVersionError as exc:
            result = {"status": "SCHEMA_VERSION_ERROR", "error": str(exc)}
            emit(result, args.json)
            error_line("schema_version_error", str(exc), "peerhub guide")
            return 6
        except sqlite3.Error as exc:
            result = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
            emit(result, args.json)
            error_line("storage_corrupt", str(result["error"]), "peerhub diag health")
            return 4
        emit(result, args.json)
        return 0 if result["status"] == "OK" else 4
    if args.action == "health":  # pure reader as well: never CoreStore (no create, no migration of a missing/old --db)
        if not Path(args.db).is_file():
            report_missing_store(args.db, selected=is_explicit, source=db_source)
            result: dict[str, object] = {"status": "UNAVAILABLE", "stream_id": args.stream_id, "error": f"database not found: {args.db}"}
            if args.json:
                emit(result, True)
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
        emit(result, args.json)
        if str(result.get("error", "")).startswith("SchemaVersionError"):
            error_line("schema_version_error", f"SCHEMA VERSION ERROR: {result['error']}", "peerhub guide")
            return 6
        if result.get("status") == "NOT_FOUND":
            error_line("stream_not_found", "The selected stream does not exist", "peerhub stream list")
            return 1
        if result.get("status") in ("ERROR", "PARTIAL"):
            error_line("diag_unavailable", str(result.get("error") or result["status"]), "peerhub diag quota")
            return 4
        return 0
    return 0
