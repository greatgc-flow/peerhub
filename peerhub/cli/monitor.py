"""PeerHub CLI monitor command.

Composition root for the combined quota refresh and read-only diagnostic dashboard.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from time import monotonic, sleep
from pathlib import Path
from typing import Any, cast

from peerhub.cli.support import agy_token_use, print_warnings
from peerhub.cli.monitor_preferences import GROUP_IDS

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
                   help="Emit machine-readable JSON (NDJSON for watch; one object for --snapshot)")
    p.add_argument("--schema-version", choices=("1.1", "2.0"), default=None,
                   help="JSON contract version (requires --json; default: 2.0)")
    if _legacy_alias_available():
        p.add_argument("--legacy", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--allow-agy-token-use", action="store_true",
                   help="Keep collecting after agy /usage consumed model tokens")
    p.add_argument("--view", choices=["auto", "rich", "plain"], default="auto",
                   help="Dashboard style: rich (bars, colour, pace), plain (ASCII); auto = rich on a TTY. TTY with VT redraws in place at most 1 Hz; non-TTY or no VT appends once every N s without escapes")
    p.add_argument("--collect-every", type=int, default=1,
                   help="Collect only every Kth cycle (K>=1)")
    p.add_argument("--system-dir", dest="sys_dir", type=Path,
                   help="Explicit portable provider installation directory")
    p.add_argument("--group", action="append", choices=(*GROUP_IDS, "all"), default=None,
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
            action.add_argument("--group", choices=GROUP_IDS, nargs="+", required=True)
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
    legacy = bool(getattr(args, "legacy", False))
    schema = getattr(args, "schema_version", None)
    if schema is not None and not args.json:
        parser.error("--schema-version requires --json")
    effective_schema = schema or ("1.1" if legacy and args.json else "2.0")
    if legacy and args.json and schema == "2.0":
        parser.error("--legacy conflicts with --schema-version 2.0; remove --legacy or select schema 1.1")
    if args.json and effective_schema == "1.1" and args.group:
        parser.error("schema 1.1 cannot represent --group; use schema 2.0")
    if args.json and effective_schema == "1.1" and args.snapshot:
        parser.error("schema 1.1 cannot represent --snapshot; use schema 2.0")


def _legacy_alias_available(version: str | None = None) -> bool:
    from peerhub._version import __version__

    value = (version or __version__).split("+", 1)[0]
    try:
        major, minor, *_ = (int(part.split("-", 1)[0]) for part in value.split("."))
    except ValueError:
        return True
    return (major, minor) < (0, 19)


def _render_human_frame(payload: dict[str, Any], *, view: str, width: int, color: bool) -> str:
    from peerhub.cli.monitor_view import render_grouped_frame

    unicode_ok = view != "plain" and (sys.stdout.encoding or "").lower().startswith("utf")
    return render_grouped_frame(payload, view="plain" if view == "plain" else "rich",
                                color=color, unicode=unicode_ok, width=width)


def run_monitor(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    validate_monitor_args(parser, args)
    legacy = bool(getattr(args, "legacy", False))
    schema = getattr(args, "schema_version", None) or ("1.1" if legacy and args.json else "2.0")
    if args.json and schema == "1.1":
        if legacy:
            print("warning: --legacy is deprecated; use --schema-version 1.1 (removed in 0.19.0)",
                  file=sys.stderr, flush=True)
        from peerhub.cli.monitor_schema11 import run_schema_11_watch
        return run_schema_11_watch(args, sleep_fn=sleep, monotonic_fn=monotonic)
    if legacy:
        print("warning: --legacy is deprecated; the grouped visual dashboard is now the default "
              "(removed in 0.19.0)", file=sys.stderr, flush=True)
    if args.snapshot:
        provided_options = cast(set[str], getattr(args, "_provided_options", set[str]()))
        conflicts = provided_options & {
            "--interval-seconds", "--cycles", "--peers", "--timeout-seconds", "--collect-every",
            "--allow-agy-token-use", "--system-dir",
        }
        if conflicts:
            parser.error("--snapshot cannot be combined with watch or collection options: " + ", ".join(sorted(conflicts)))
        return _run_monitor_snapshot(args, parser)
    from peerhub.cli.monitor_preferences import effective_preferences
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
                  f"PeerHub monitor defaults are invalid ({preference.get('reason') or 'unknown reason'}). Run 'peerhub monitor defaults show' to inspect them.")
            return 1
        selected = cast(list[str], preference["selected_groups"])
    return _run_monitor_grouped_watch(args, cast(list[str], selected), cast(dict[str, Any], preference))


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
    effective_view = ("rich" if output.live else "plain") if args.view == "auto" else args.view
    color = effective_view == "rich" and use_color(output.live, os.environ)
    last_payload: dict[str, Any] | None = None
    last_frame_monotonic = 0.0

    def emit(cycle: int, phase: str, refresh: dict[str, Any]) -> int:
        nonlocal last_payload, last_frame_monotonic
        try:
            payload: dict[str, Any] = render_monitor_frame(args.db, selected)
        except SchemaVersionError:
            payload = {"schema_version": "2.0", "status": "FAILED", "groups": {},
                       "errors": {"schema": "SchemaVersionError"}}
        payload.update({"selected_groups": selected, "available_groups": list(GROUP_IDS),
                       "preference": {key: preference.get(key) for key in ("status", "source", "reason")},
                       "store_selection": {"path": str(args.db), "source": getattr(args, "db_source", "unknown")}})
        last_payload = payload
        last_frame_monotonic = monotonic()
        if args.json:
            print(json.dumps({"schema_version": "2.0", "cycle": cycle, "phase": phase, "refresh": refresh,
                              "snapshot": payload}, ensure_ascii=True, separators=(",", ":")), flush=True)
        else:
            output.write(_render_human_frame(payload, view=effective_view, width=output.width(), color=color))
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
            if remaining > 0 and output.live:
                while remaining > 0:
                    sleep(min(1.0, remaining))
                    remaining = next_deadline - monotonic()
                    if remaining > 0 and last_payload is not None:
                        display = dict(last_payload)
                        display["next_refresh_in"] = remaining
                        display["display_elapsed_seconds"] = max(0.0, monotonic() - last_frame_monotonic)
                        output.write(_render_human_frame(display, view=effective_view,
                                                         width=output.width(), color=color))
            elif remaining > 0:
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
        print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) if args.json else
              f"PeerHub monitor defaults are invalid ({preference.get('reason') or 'unknown reason'}). Run 'peerhub monitor defaults show' to inspect them.")
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
    """Render a human dashboard from one schema 2.0 snapshot."""
    from peerhub.cli.view import enable_vt, use_color
    tty = sys.stdout.isatty()
    live = tty and enable_vt()
    effective_view = ("rich" if live else "plain") if view == "auto" else view
    color = effective_view == "rich" and use_color(live, os.environ)
    print(_render_human_frame(payload, view=effective_view, width=shutil.get_terminal_size((100, 24)).columns,
                              color=color), end="")


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
