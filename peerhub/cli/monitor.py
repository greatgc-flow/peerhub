"""PeerHub CLI monitor command.

Composition root for the combined quota refresh and read-only diagnostic dashboard.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from time import monotonic, sleep
from pathlib import Path
from typing import Any, cast

from peerhub.cli.support import agy_token_use, print_warnings

MONITOR_FRAME_SCHEMA = "1.1"


def register_monitor_parser(subparsers: Any) -> None:
    p = subparsers.add_parser("monitor", help="Watch quota evidence and diagnostic dashboard")
    p.add_argument("--interval-seconds", dest="interval", type=float, default=60.0,
                   help="Collection cycles every N s (positive float); supported TTY countdown redraw at most 1 Hz")
    p.add_argument("--cycles", type=int, default=0,
                   help="Number of cycles to run (0 = until interrupted)")
    p.add_argument("--peers", nargs="+", default=["cx", "cc", "ag"],
                   help="Peers to refresh quota for")
    p.add_argument("--timeout-seconds", dest="timeout", type=float, default=15.0,
                   help="Probe timeout")
    p.add_argument("--json", action="store_true",
                   help="Emit NDJSON frames")
    p.add_argument("--allow-agy-token-use", action="store_true",
                   help="Keep collecting after agy /usage consumed model tokens")
    p.add_argument("--view", choices=["auto", "rich", "plain"], default="auto",
                   help="Dashboard style: rich (bars, colour, pace), plain (ASCII); auto = rich on a TTY. TTY with VT redraws in place at most 1 Hz; non-TTY or no VT appends once every N s without escapes")
    p.add_argument("--collect-every", type=int, default=1,
                   help="Collect only every Kth cycle (K>=1)")
    p.add_argument("--system-dir", dest="sys_dir", type=Path,
                   help="Explicit portable provider installation directory")
    p.add_argument("--group", action="append", choices=("overview", "execution", "sessions", "usage", "context",
                   "capacity", "runtime", "coverage", "store", "all"), default=None,
                   help="Select monitor groups for a read-only snapshot or grouped watch")
    p.add_argument("--snapshot", action="store_true", help="Render one read-only grouped snapshot without refreshing providers")

    actions = p.add_subparsers(dest="monitor_action")
    groups = actions.add_parser("groups", help="List monitor groups and default selection")
    groups.add_argument("--json", action="store_true", help="Emit JSON")
    defaults = actions.add_parser("defaults", help="Inspect or change monitor display defaults")
    default_actions = defaults.add_subparsers(dest="defaults_action", required=True)
    show = default_actions.add_parser("show", help="Show effective monitor defaults")
    show.add_argument("--json", action="store_true", help="Emit JSON")
    for action_name in ("set", "reset"):
        action = default_actions.add_parser(action_name, help=f"{action_name.capitalize()} monitor defaults")
        action.add_argument("--global", dest="global_preferences", action="store_true",
                            help="Select the global configuration layer")
        if action_name == "set":
            action.add_argument("--group", choices=("overview", "execution", "sessions", "usage", "context",
                               "capacity", "runtime", "coverage", "store"), nargs="+", required=True)
        action.add_argument("--json", action="store_true", help="Emit JSON")
    repair = actions.add_parser("repair", help="Rebuild monitor projections from durable PeerHub evidence")
    repair.add_argument("--chunk-size", type=int, default=500, help="Bounded delivery read chunk (1-5000)")
    repair.add_argument("--json", action="store_true", help="Emit JSON")


def validate_monitor_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not args.interval > 0:
        parser.error("--interval-seconds must be a positive number")
    if args.cycles < 0:
        parser.error("--cycles must be >= 0")
    if not args.timeout > 0:
        parser.error("--timeout-seconds must be a positive number")
    if args.collect_every < 1:
        parser.error("--collect-every must be >= 1")


def _frame_drawer(format_frame: Any, view: str, *, color: bool, db_name: str, cycle: int, refresh: dict[str, Any], interval: float,
                  collect_every: int, output: Any, store_path: str = "", store_source: str = "") -> Any:
    unicode_ok = (sys.stdout.encoding or "").lower().startswith("utf")

    def draw(rep: Any, next_in: float | None) -> None:
        output.write(format_frame(rep, view, color=color, unicode=unicode_ok, width=output.width(), db_name=db_name, cycle=cycle,
                           refresh=refresh, next_refresh_in=next_in, interval=interval, refresh_every=collect_every,
                           store_path=store_path, store_source=store_source))

    return draw


def _tick(rep: Any, draw: Any, interval: float, collect_in: float, advance: Any) -> None:
    """Between two cycles redraw once per second from the same snapshot (clock, ages and countdowns move; nothing is re-read)."""
    start = monotonic()
    while True:
        left = interval - (monotonic() - start)
        if left <= 0:
            return
        sleep(min(1.0, left))
        elapsed = monotonic() - start
        if elapsed >= interval:
            return
        if elapsed >= 1.0:
            draw(advance(rep, rep.read_at + elapsed), max(0.0, collect_in - elapsed))


def _cycle_exit_code(report: Any, refresh_code: int) -> int:
    """Return the most actionable failure from the completed diagnostic/refresh cycle."""
    if report.status == "FAILED":
        return 6 if (report.error or "").startswith("SchemaVersionError:") else 4
    if report.status == "PARTIAL":
        return 4
    return refresh_code


def run_monitor(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    validate_monitor_args(parser, args)
    if args.snapshot:
        provided_options = cast(set[str], getattr(args, "_provided_options", set[str]()))
        conflicts = provided_options & {
            "--interval-seconds", "--cycles", "--peers", "--timeout-seconds", "--collect-every",
            "--allow-agy-token-use", "--system-dir",
        }
        if conflicts:
            parser.error("--snapshot cannot be combined with watch or collection options: " + ", ".join(sorted(conflicts)))
        return _run_monitor_snapshot(args, parser)
    from peerhub.cli.monitor_preferences import GROUP_IDS, effective_preferences
    if args.group:
        requested = list(dict.fromkeys(args.group))
        if "all" in requested and len(requested) > 1:
            parser.error("--group all cannot be combined with other group IDs")
        selected = list(GROUP_IDS) if requested == ["all"] else sorted(requested, key=GROUP_IDS.index)
        preference = {"status": "OK", "source": "command", "reason": None, "selected_groups": selected}
    else:
        preference = effective_preferences()
        if preference["status"] != "OK":
            payload = {"schema_version": "2.0", "status": "ERROR", "groups": {},
                       "preference": {key: preference.get(key) for key in ("status", "source", "reason")}}
            print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) if args.json else
                  json.dumps(payload, ensure_ascii=True, indent=2))
            return 1
        selected = cast(list[str], preference["selected_groups"])
    # Bare monitor keeps the established 1.1 dashboard contract. Explicit groups
    # and persisted user preferences opt into the structurally grouped frames.
    if args.group or preference["source"] in ("workspace", "global"):
        return _run_monitor_grouped_watch(args, cast(list[str], selected), cast(dict[str, Any], preference))
    from peerhub.cli.store_select import report_missing_store
    if report_missing_store(args.db, json_output=args.json):
        return 5
    # Lazy imports: registering the parser must work when first-party extensions are blocked (Core-only path); the
    # extension-backed pieces are only needed once the command actually runs.
    from peerhub.cli.view import FrameOutput, advance_report, dashboard_data, format_frame, use_color
    from peerhub.extensions.diag import ReadonlyDiag
    from peerhub.extensions.quota_capture import refresh_quota

    tty = sys.stdout.isatty()
    view = ("rich" if tty else "plain") if args.view == "auto" else args.view
    output = FrameOutput(interactive=not args.json)
    color = view == "rich" and use_color(output.live, os.environ)
    db_name = os.path.basename(str(args.db))
    store_path = str(args.db)
    store_source = getattr(args, "db_source", "unknown")
    live, draw = False, None
    total: int = args.cycles
    done: int = 0
    last_refresh_status_code: int = 0
    next_deadline = monotonic() + args.interval

    try:
        # Show the last committed evidence immediately; provider latency must not hide the current state.
        initial = ReadonlyDiag(args.db).render(["peers", "streams", "resource_pools", "observations", "activity"])
        initial_refresh = {"status": "NOT_STARTED"}
        if args.json:
            print(json.dumps({
                "schema_version": MONITOR_FRAME_SCHEMA, "cycle": 0, "phase": "initial", "refresh": initial_refresh,
                "store_selection": {"path": store_path, "source": store_source}, "snapshot": dashboard_data(initial),
            }, ensure_ascii=True, separators=(",", ":")), flush=True)
        else:
            live = output.live
            draw = _frame_drawer(format_frame, view, color=color, db_name=db_name, cycle=0,
                                 refresh=initial_refresh, interval=args.interval,
                                 collect_every=args.collect_every, output=output,
                                 store_path=store_path, store_source=store_source)
            draw(initial, args.interval)
        while True:
            if report_missing_store(args.db, json_output=args.json):
                return 5
            is_refresh_cycle = (done % args.collect_every) == 0
            refresh_info: dict[str, Any] = {}
            if is_refresh_cycle:
                result = refresh_quota(
                    args.db,
                    args.peers,
                    sys_dir=getattr(args, "sys_dir", None),
                    deadline_sec=args.timeout
                )
                last_refresh_status_code = 1 if result["status"] != "OK" else 0
                refresh_info = {
                    "status": result["status"],
                    "warnings": result.get("warnings", []),
                    "observations": len(result.get("observations", []))
                }
                if not args.json:
                    print(f"refresh: {result['status']}", file=sys.stderr, flush=True)
                    print_warnings(result)

                if agy_token_use(result) and not args.allow_agy_token_use:
                    print("error: agy /usage consumed model tokens; stopping watch mode so quota is not burned every interval "
                          "(pass --allow-agy-token-use to continue)", file=sys.stderr, flush=True)
                    return 1
            else:
                refresh_info = {"skipped": True}

            diag = ReadonlyDiag(args.db)
            rep = diag.render(["peers", "streams", "resource_pools", "observations", "activity"])

            if args.json:
                snapshot_dict = dashboard_data(rep)
                frame = {
                    "schema_version": MONITOR_FRAME_SCHEMA,
                    "cycle": done,
                    "phase": "complete",
                    "refresh": refresh_info,
                    "store_selection": {"path": store_path, "source": store_source},
                    "snapshot": snapshot_dict,
                }
                print(json.dumps(frame, ensure_ascii=True, separators=(",", ":")), flush=True)
            else:
                live = output.live
                draw = _frame_drawer(format_frame, view, color=color, db_name=db_name, cycle=done, refresh=refresh_info,
                                     interval=args.interval, collect_every=args.collect_every, output=output,
                                     store_path=store_path, store_source=store_source)
                until_collect = args.collect_every - done % args.collect_every
                draw(rep, until_collect * args.interval)

            done += 1
            if total > 0 and done >= total:
                return _cycle_exit_code(rep, last_refresh_status_code)

            if live and draw is not None:
                until_collect = ((done - 1) // args.collect_every + 1) * args.collect_every - (done - 1)
                remaining = max(0.0, next_deadline - monotonic())
                if remaining:
                    _tick(rep, draw, remaining, until_collect * args.interval, advance_report)
            else:
                remaining = next_deadline - monotonic()
                if remaining > 0:
                    sleep(remaining)
            now = monotonic()
            while next_deadline <= now:
                next_deadline += args.interval

    except KeyboardInterrupt:
        return 0
    except (FileNotFoundError, sqlite3.OperationalError):
        if report_missing_store(args.db, json_output=args.json):
            return 5
        raise
    finally:
        output.close()


def _run_monitor_grouped_watch(args: argparse.Namespace, selected: list[str], preference: dict[str, Any]) -> int:
    """Emit selected grouped snapshots while retaining quota refresh cadence and safety stops."""
    from peerhub.cli.store_select import report_missing_store
    if report_missing_store(args.db, json_output=args.json):
        return 5
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.extensions.quota_capture import refresh_quota
    from peerhub.core.schema_version import SchemaVersionError
    from peerhub.cli.view import FrameOutput, use_color

    output = FrameOutput(interactive=not args.json)
    color = (args.view == "rich" or (args.view == "auto" and output.live)) and use_color(output.live, os.environ)

    def human_frame(payload: dict[str, Any]) -> str:
        heading = "\x1b[1;36m" if color else ""
        reset = "\x1b[0m" if color else ""
        lines = [f"PeerHub monitor - {payload.get('status', 'UNKNOWN')}"]
        pref = payload.get("preference")
        if isinstance(pref, dict):
            lines.append(f"Defaults: {cast(dict[str, Any], pref).get('source', 'unknown')}")
        groups_obj: object = payload.get("groups")
        groups = cast(dict[str, Any], groups_obj) if isinstance(groups_obj, dict) else {}
        for group_id, value in groups.items():
            group = cast(dict[str, Any], value) if isinstance(value, dict) else {}
            label = f"{group_id.upper()} - {group.get('status', 'UNKNOWN')}"
            lines.extend(("", f"{heading}{label}{reset}" if heading else label))
            data = group.get("data")
            lines.append("  unavailable" if data is None else json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True))
        return "\n".join(lines)

    def emit(cycle: int, phase: str, refresh: dict[str, Any]) -> int:
        try:
            payload: dict[str, Any] = render_monitor_frame(args.db, selected)
        except SchemaVersionError:
            payload = {"schema_version": "2.0", "status": "FAILED", "groups": {},
                       "errors": {"schema": "SchemaVersionError"}}
        payload.update({"selected_groups": selected, "available_groups": ["overview", "execution", "sessions", "usage",
                       "context", "capacity", "runtime", "coverage", "store"],
                       "preference": {key: preference.get(key) for key in ("status", "source", "reason")},
                       "store_selection": {"path": str(args.db), "source": getattr(args, "db_source", "unknown")}})
        if args.json:
            print(json.dumps({"schema_version": "2.0", "cycle": cycle, "phase": phase, "refresh": refresh,
                              "snapshot": payload}, ensure_ascii=True, separators=(",", ":")), flush=True)
        else:
            output.write(human_frame(payload))
        groups_obj: object = payload.get("groups")
        groups = cast(dict[str, Any], groups_obj) if isinstance(groups_obj, dict) else {}
        if any(isinstance(v, dict) and cast(dict[str, Any], v).get("status") == "ERROR" for v in groups.values()):
            return 4
        errors_obj: object = payload.get("errors")
        errors = cast(dict[str, Any], errors_obj) if isinstance(errors_obj, dict) else {}
        return 6 if "schema" in errors else 0

    try:
        code = emit(0, "initial", {"status": "NOT_STARTED"})
        if code == 6:
            return code
        done = 0
        last_refresh_code = 0
        next_deadline = monotonic() + args.interval
        while True:
            if report_missing_store(args.db, json_output=args.json):
                return 5
            refresh: dict[str, Any]
            if done % args.collect_every == 0:
                result = refresh_quota(args.db, args.peers, sys_dir=getattr(args, "sys_dir", None),
                                       deadline_sec=args.timeout)
                last_refresh_code = 1 if result["status"] != "OK" else 0
                refresh = {"status": result["status"], "warnings": result.get("warnings", []),
                           "observations": len(result.get("observations", []))}
                if not args.json:
                    print(f"refresh: {result['status']}", file=sys.stderr, flush=True)
                    print_warnings(result)
                if agy_token_use(result) and not args.allow_agy_token_use:
                    print("error: agy /usage consumed model tokens; stopping watch mode so quota is not burned every interval "
                          "(pass --allow-agy-token-use to continue)", file=sys.stderr, flush=True)
                    return 1
            else:
                refresh = {"skipped": True}
            code = emit(done, "complete", refresh)
            done += 1
            if code == 6:
                return code
            if args.cycles > 0 and done >= args.cycles:
                return code or last_refresh_code
            remaining = next_deadline - monotonic()
            if remaining > 0:
                sleep(remaining)
            now = monotonic()
            while next_deadline <= now:
                next_deadline += args.interval
    except KeyboardInterrupt:
        return 0
    except (FileNotFoundError, sqlite3.OperationalError):
        if report_missing_store(args.db, json_output=args.json):
            return 5
        raise
    finally:
        output.close()


def _run_monitor_snapshot(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from peerhub.cli.monitor_preferences import GROUP_IDS, effective_preferences
    from peerhub.cli.store_select import report_missing_store

    preference = effective_preferences()
    if preference["status"] != "OK":
        payload = {"schema_version": "2.0", "status": "ERROR", "selected_groups": None,
                   "preference": {key: preference[key] for key in ("status", "source", "reason")}}
        print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) if args.json else json.dumps(payload, indent=2))
        return 1
    requested = args.group
    unique_requested = list(dict.fromkeys(requested or []))
    if "all" in unique_requested and len(unique_requested) > 1:
        parser.error("--group all cannot be combined with other group IDs")
    selected: list[str] = (list(GROUP_IDS) if unique_requested == ["all"] else
                           sorted(set(cast(list[str], unique_requested)), key=GROUP_IDS.index) if unique_requested else
                           cast(list[str], preference["selected_groups"]))
    if report_missing_store(args.db, json_output=args.json):
        return 5
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.core.schema_version import SchemaVersionError
    try:
        payload = render_monitor_frame(args.db, selected)
    except SchemaVersionError:
        payload = {"schema_version": "2.0", "status": "FAILED", "selected_groups": selected,
                   "errors": {"schema": "SchemaVersionError"}}
        if args.json:
            print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
        else:
            _print_grouped_snapshot(payload, args.view)
        return 6
    payload["selected_groups"] = selected
    payload["available_groups"] = list(GROUP_IDS)
    payload["preference"] = {key: preference[key] for key in ("status", "source", "reason")}
    payload["store_selection"] = {"path": args.db, "source": getattr(args, "db_source", "unknown")}
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    else:
        _print_grouped_snapshot(payload, args.view)
    groups_obj: object = payload.get("groups")
    groups = cast(dict[str, Any], groups_obj) if isinstance(groups_obj, dict) else {}
    group_errors = False
    for value in groups.values():
        if isinstance(value, dict) and cast(dict[str, Any], value).get("status") == "ERROR":
            group_errors = True
            break
    return 4 if group_errors or payload["status"] == "FAILED" else 0


def _print_grouped_snapshot(payload: dict[str, Any], view: str) -> None:
    """Render a compact human-readable group outline; JSON remains the machine contract."""
    from peerhub.cli.view import enable_vt, use_color
    tty = sys.stdout.isatty()
    requested_rich = view == "rich" or (view == "auto" and tty)
    rich = requested_rich and use_color(tty and enable_vt(), os.environ)
    heading = "\x1b[1;36m" if rich else ""
    reset = "\x1b[0m" if rich else ""
    print(f"PeerHub monitor - {payload.get('status', 'UNKNOWN')}")
    preference = payload.get("preference")
    if isinstance(preference, dict):
        print(f"Defaults: {cast(dict[str, Any], preference).get('source', 'unknown')}")
    groups_obj: object = payload.get("groups")
    groups = cast(dict[str, Any], groups_obj) if isinstance(groups_obj, dict) else {}
    for group_id, value in groups.items():
        group = cast(dict[str, Any], value) if isinstance(value, dict) else {}
        print(f"\n{heading}{group_id.upper()} - {group.get('status', 'UNKNOWN')}{reset}")
        data: object = group.get("data")
        if data is None:
            print("  unavailable")
        else:
            print(json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True))


def run_monitor_repair(args: argparse.Namespace) -> int:
    from peerhub.cli.store_select import report_missing_store
    if report_missing_store(args.db, json_output=args.json):
        return 5
    from peerhub.extensions.session_receipts import rebuild_delivery_projections
    try:
        result = rebuild_delivery_projections(args.db, chunk_size=args.chunk_size)
    except (OSError, ValueError, sqlite3.Error) as exc:
        result = {"status": "ERROR", "reason": type(exc).__name__}
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")) if args.json else f"ERROR: {result['reason']}")
        return 4
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")) if args.json else json.dumps(result, indent=2))
    return 0
