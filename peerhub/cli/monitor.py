"""PeerHub CLI monitor command.

Composition root for the combined quota refresh and read-only diagnostic dashboard.
"""

from __future__ import annotations

import argparse
from functools import lru_cache
from importlib import resources
import json
import math
import os
import sqlite3
import sys
from time import monotonic, sleep
from pathlib import Path
from typing import Any, cast

from peerhub.cli.support import agy_token_command, agy_token_stop_message, error_line, print_warnings, refresh_with_profile_pools
from peerhub.cli.monitor_preferences import GROUP_IDS

def _lead_reader(**facts: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The lead sessions of the registered Claude Code folders (#376 A-1); looked up at call time, so tests isolate it."""
    from peerhub.extensions import lead_sessions
    return lead_sessions.read_leads(lead_sessions.lead_dirs(os.environ), **facts)


def register_monitor_parser(subparsers: Any) -> None:
    from peerhub.cli.monitor_view import MONITOR_LEGEND

    p = subparsers.add_parser("monitor", help="Watch quota evidence and diagnostic dashboard",
                             epilog="\n".join(MONITOR_LEGEND), formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--interval-seconds", dest="interval", type=float, default=60.0,
                   help="Collection cycles every N s (positive float); supported TTY countdown redraw at most 1 Hz")
    p.add_argument("--cycles", type=int, default=None,
                   help="Number of cycles (default: 1 when stdout is not a TTY, until interrupted on a TTY; 0 = until interrupted everywhere)")
    p.add_argument("--peers", nargs="+", default=["cx", "cc", "ag"],
                   help="Peers to refresh quota for")
    p.add_argument("--timeout-seconds", dest="timeout", type=float, default=15.0,
                   help="Probe timeout")
    p.add_argument("--json", action="store_true")
    p.add_argument("--schema-version", choices=("1.1", "2.0"), default=None,
                   help="JSON contract version (requires --json; default: 2.0)")
    if _legacy_alias_available():
        p.add_argument("--legacy", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--allow-agy-token-use", action="store_true",
                   help="Keep collecting after agy /usage or /credits consumed model tokens")
    p.add_argument("--view", choices=["auto", "rich", "plain"],
                   default=os.environ.get("PEERHUB_MONITOR_VIEW") or "auto",
                   help="Dashboard style: auto (rich on a TTY), rich (colour), plain (ASCII override for ambiguous-width box glyphs, ▲▼ and █ in Korean/East-Asian locales). Set PEERHUB_MONITOR_VIEW=plain for a lasting ASCII fix (auto|rich|plain; explicit --view wins). Both show PACE; VT TTY redraws at most 1 Hz; non-TTY appends completed frames; TTY without VT appends without escapes")
    p.add_argument("--collect-every", type=int, default=1,
                   help="Collect only every Kth cycle (K>=1)")
    p.add_argument("--system-dir", dest="sys_dir", type=Path,
                   help="Explicit portable provider installation directory")
    p.add_argument("--group", action="append", choices=(*GROUP_IDS, "all"), default=None,
                   help="Select monitor groups for a read-only snapshot or grouped watch")
    p.add_argument("--snapshot", action="store_true", help="Render one read-only grouped snapshot without refreshing providers")
    layout = p.add_mutually_exclusive_group()
    layout.add_argument("--compact", action="store_true", help="Render the compact dashboard (default)")
    layout.add_argument("--expand", action="store_true", help="Render detailed grouped sections with bars, pace and forecasts")
    p.add_argument("--show-quota-notes", action="store_true",
                   help="Show why quota windows are unavailable (plan omissions or provider-disabled windows)")

    actions = p.add_subparsers(dest="monitor_action")
    groups = actions.add_parser("groups", help="List monitor groups and default selection")
    groups.add_argument("--json", action="store_true")
    defaults = actions.add_parser("defaults", help="Inspect or change monitor display defaults")
    default_actions = defaults.add_subparsers(dest="defaults_action", required=True)
    show = default_actions.add_parser("show", help="Show effective monitor defaults")
    show.add_argument("--json", action="store_true")
    for action_name in ("set", "reset"):
        action = default_actions.add_parser(action_name, help=f"{action_name.capitalize()} monitor defaults")
        action.add_argument("--global", dest="global_preferences", action="store_true",
                            help="Select the global configuration layer")
        if action_name == "set":
            action.add_argument("--group", choices=GROUP_IDS, nargs="+", required=True, help="Monitor groups to show by default")
        action.add_argument("--json", action="store_true")
    repair = actions.add_parser("repair", help="Rebuild monitor projections from durable PeerHub evidence")
    repair.add_argument("--chunk-size", type=int, default=500, help="Bounded delivery read chunk (1-5000)")
    repair.add_argument("--json", action="store_true")


def validate_monitor_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.view not in {"auto", "rich", "plain"}:
        parser.error("PEERHUB_MONITOR_VIEW must be auto, rich or plain")
    if not math.isfinite(args.interval) or not args.interval > 0:
        parser.error("--interval-seconds must be a finite positive number")
    if args.cycles is not None and args.cycles < 0:
        parser.error("--cycles must be >= 0")
    if not math.isfinite(args.timeout) or not args.timeout > 0:
        parser.error("--timeout-seconds must be a finite positive number")
    if args.collect_every < 1:
        parser.error("--collect-every must be >= 1")
    if (getattr(args, "expand", False) or getattr(args, "compact", False)) and args.json:
        option = "--expand" if getattr(args, "expand", False) else "--compact"
        parser.error(f"{option} applies to human dashboard output and cannot be combined with --json")
    if getattr(args, "show_quota_notes", False) and args.json:
        parser.error("--show-quota-notes applies to human dashboard output and cannot be combined with --json")
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
    """Check whether the deprecated --legacy alias is active in this release.

    Sunset is explicitly extended to 0.20.0 (removal deferred; no removal evidence needed).
    """
    from peerhub._version import __version__

    value = (version or __version__).split("+", 1)[0]
    try:
        major, minor, *_ = (int(part.split("-", 1)[0]) for part in value.split("."))
    except ValueError:
        return True
    return (major, minor) < (0, 20)


@lru_cache(maxsize=32)
def _cached_profiles(workspace: str, fingerprints: tuple[tuple[str, int, int], ...]) -> list[dict[str, Any]]:
    from peerhub.extensions.adapters.profiles import list_profiles
    return list_profiles(Path(workspace))


def _profile_fingerprints(workspace: Path) -> tuple[tuple[str, int, int], ...]:
    paths = [Path(str(resources.files("peerhub.config_data").joinpath("model-defaults.toml"))),
             Path(os.environ.get("PEERHUB_CONFIG_HOME") or Path.home() / ".peerhub/config") / "models.toml",
             workspace / ".peerhub/config/models.toml"]
    result: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            stat = path.stat()
            result.append((str(path), stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            result.append((str(path), 0, 0))
    return tuple(result)


def _render_human_frame(payload: dict[str, Any], *, view: str, width: int,
                        height: int | None, color: bool, expand: bool = False, show_quota_notes: bool = False) -> str:
    from peerhub.cli.monitor_view import render_grouped_frame

    unicode_ok = view != "plain" and (sys.stdout.encoding or "").lower().startswith("utf")
    # A5: separately from the decoration, text cells keep every character this stream can encode (not only on UTF).
    text_encoding = sys.stdout.encoding or None
    from peerhub.cli.store_select import DEFAULT_DB_PATH, env_override_note, select_store
    selection = payload.get("store_selection", {})
    store_path = Path(str(selection.get("path") or ".peerhub/core.db")).resolve()
    workspace = store_path.parent.parent if store_path.parent.name == ".peerhub" else store_path.parent
    try:
        profiles = _cached_profiles(str(workspace), _profile_fingerprints(workspace))
    except (OSError, ValueError):
        profiles = []
    nearest = select_store(DEFAULT_DB_PATH, use_env=False)
    payload = {**payload, "configured_profiles": profiles,
               "_nearest_store_path": nearest.path if nearest.source == "discovered" else None,
               "_store_note": env_override_note(str(store_path), selection.get("source"))}
    return render_grouped_frame(payload, view="plain" if view == "plain" else "rich",
                                color=color, unicode=unicode_ok, width=width, height=height,
                                expand=expand, text_encoding=text_encoding, show_quota_notes=show_quota_notes)


def _read_failed(payload: dict[str, Any]) -> bool:
    """True when a selected group could not be read (exit code 4). A store that never had a quota refresh is not such
    a case: the read model gives its capacity group as read with no observations, for the human frame and for --json."""
    groups_obj: object = payload.get("groups")
    groups = cast(dict[str, Any], groups_obj) if isinstance(groups_obj, dict) else {}
    return any(isinstance(group, dict) and cast(dict[str, Any], group).get("status") == "ERROR"
               for group in groups.values())


def run_monitor(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    validate_monitor_args(parser, args)
    if args.cycles is None:
        args.cycles = 0 if sys.stdout.isatty() else 1
    legacy = bool(getattr(args, "legacy", False))
    schema = getattr(args, "schema_version", None) or ("1.1" if legacy and args.json else "2.0")
    if args.json and schema == "1.1":
        if legacy:
            print("warning: --legacy is deprecated; use --schema-version 1.1 "
                  "(removed in 0.20.0; sunset extended, removal deferred as no removal evidence needed)",
                  file=sys.stderr, flush=True)
        from peerhub.cli.monitor_schema11 import run_schema_11_watch
        return run_schema_11_watch(args, sleep_fn=sleep, monotonic_fn=monotonic)
    if legacy:
        print("warning: --legacy is deprecated; the grouped visual dashboard is now the default "
              "(removed in 0.20.0; sunset extended, removal deferred as no removal evidence needed)",
              file=sys.stderr, flush=True)
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
            if args.json:
                print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
            error_line("invalid_arguments", "Monitor defaults are invalid", "peerhub monitor defaults show")
            return 1
        selected = cast(list[str], preference["selected_groups"])
    return _run_monitor_grouped_watch(args, cast(list[str], selected), cast(dict[str, Any], preference))


def _run_monitor_grouped_watch(args: argparse.Namespace, selected: list[str], preference: dict[str, Any]) -> int:
    """Emit selected grouped snapshots while retaining quota refresh cadence and safety stops."""
    from peerhub.cli.store_select import report_missing_store
    db_source = getattr(args, "db_source", None)
    is_explicit = db_source in ("explicit", "env")
    if report_missing_store(args.db, json_output=args.json, selected=is_explicit, source=db_source):
        return 5
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.core.schema_version import SchemaVersionError
    from peerhub.cli.view import FrameOutput, use_color
    output = FrameOutput(interactive=not args.json, anchor_rows=True)
    effective_view = ("rich" if output.live else "plain") if args.view == "auto" else args.view
    color = effective_view == "rich" and use_color(output.live, os.environ)
    expand = bool(getattr(args, "expand", False))
    show_quota_notes = bool(getattr(args, "show_quota_notes", False))
    last_payload: dict[str, Any] | None = None
    last_frame_monotonic = 0.0

    def emit(cycle: int, phase: str, refresh: dict[str, Any]) -> int:
        nonlocal last_payload, last_frame_monotonic
        try:
            payload: dict[str, Any] = render_monitor_frame(args.db, selected, human=not args.json,
                                                           preference_source=preference.get("source"),
                                                           lead_reader=_lead_reader)
        except SchemaVersionError:
            payload = {"schema_version": "2.0", "status": "FAILED", "groups": {},
                       "errors": {"schema": "SchemaVersionError"}}
        payload.update({"selected_groups": selected, "available_groups": list(GROUP_IDS),
                       "preference": {key: preference.get(key) for key in ("status", "source", "reason")},
                       "store_selection": {"path": str(args.db), "source": getattr(args, "db_source", "unknown")}})
        if output.live and refresh.get("status") not in (None, "NOT_STARTED"):
            warnings = refresh.get("warnings", [])
            warning_count = len(cast(list[Any], warnings)) if isinstance(warnings, list) else 0
            payload["refresh_state"] = (f"REFRESH {refresh['status']}"
                                         + (f" · {warning_count} warnings" if warning_count else ""))
            if warning_count:
                payload["refresh_warnings"] = warnings
        last_payload = payload
        last_frame_monotonic = monotonic()
        if args.json:
            print(json.dumps({"schema_version": "2.0", "cycle": cycle, "phase": phase, "refresh": refresh,
                              "snapshot": payload}, ensure_ascii=True, separators=(",", ":")), flush=True)
        else:
            width, height = output.geometry() if output.live else (output.width(), 24)
            output.write(_render_human_frame(payload, view=effective_view, width=width,
                                             height=_live_rows(height) if output.live else None, color=color,
                                             expand=expand, show_quota_notes=show_quota_notes))
        if _read_failed(payload):
            error_line("diag_unavailable", "Monitor evidence could not be read", "peerhub diag health")
            return 4
        errors_obj: object = payload.get("errors")
        errors = cast(dict[str, Any], errors_obj) if isinstance(errors_obj, dict) else {}
        return 6 if "schema" in errors else 0

    def redraw(state: str, next_in: float | None = None) -> None:
        """Draw the last payload again between two reads. Every such frame is that one payload aged by the time since
        its read, so line 1 and POOLS always show the same moment (P9)."""
        if last_payload is None:
            return
        display = dict(last_payload)
        display["refresh_state"] = state
        display["display_elapsed_seconds"] = max(0.0, monotonic() - last_frame_monotonic)
        if next_in is not None:
            display["next_refresh_in"] = next_in
        width, height = output.geometry()
        output.write(_render_human_frame(display, view=effective_view, width=width, height=_live_rows(height), color=color,
                                         expand=expand, show_quota_notes=show_quota_notes))

    try:
        if args.json or sys.stdout.isatty():
            code = emit(0, "initial", {"status": "NOT_STARTED"})
            if code == 6:
                return code
        done = 0
        last_refresh_code = 0
        next_deadline = monotonic() + args.interval
        while True:
            if report_missing_store(args.db, json_output=args.json, selected=is_explicit, source=db_source):
                return 5
            refresh: dict[str, Any]
            if done % args.collect_every == 0:
                if output.live:
                    redraw("REFRESHING", 0)
                result = refresh_with_profile_pools(args.db, args.peers, sys_dir=getattr(args, "sys_dir", None),
                                                    deadline_sec=args.timeout)
                last_refresh_code = 1 if result["status"] != "OK" else 0
                refresh = {"status": result["status"], "warnings": result.get("warnings", []),
                           "observations": len(result.get("observations", []))}
                if not args.json and not output.live:
                    print(f"refresh: {result['status']}", file=sys.stderr, flush=True)
                    print_warnings(result)
                token_command = agy_token_command(result)
                if token_command and not args.allow_agy_token_use:
                    message = agy_token_stop_message(token_command)
                    if output.live and last_payload is not None:
                        redraw("STOPPED · AGY TOKEN USE")
                    else:
                        print(message, file=sys.stderr, flush=True)
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
                    if remaining > 0:
                        redraw("REFRESH OK" if last_refresh_code == 0 else "REFRESH WARN", remaining)
            elif remaining > 0:
                sleep(remaining)
            now = monotonic()
            while next_deadline <= now:
                next_deadline += args.interval
    except KeyboardInterrupt:
        return 0
    except (FileNotFoundError, sqlite3.OperationalError):
        if report_missing_store(args.db, json_output=args.json, selected=is_explicit, source=db_source):
            return 5
        raise
    finally:
        output.close()


def _run_monitor_snapshot(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from peerhub.cli.monitor_preferences import GROUP_IDS, effective_preferences
    from peerhub.cli.store_select import report_missing_store

    requested = args.group
    unique_requested = list(dict.fromkeys(requested or []))
    if "all" in unique_requested and len(unique_requested) > 1:
        parser.error("--group all cannot be combined with other group IDs")
    preference = ({"status": "OK", "source": "command", "reason": None}
                  if unique_requested else effective_preferences())
    if preference["status"] != "OK":
        payload = {"schema_version": "2.0", "status": "ERROR", "selected_groups": None,
                   "preference": {key: preference[key] for key in ("status", "source", "reason")}}
        if args.json:
            print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
        error_line("invalid_arguments", "Monitor defaults are invalid", "peerhub monitor defaults show")
        return 1
    selected: list[str] = (list(GROUP_IDS) if unique_requested == ["all"] else
                           sorted(set(cast(list[str], unique_requested)), key=GROUP_IDS.index) if unique_requested else
                           cast(list[str], preference["selected_groups"]))
    db_source = getattr(args, "db_source", None)
    is_explicit = db_source in ("explicit", "env")
    if report_missing_store(args.db, json_output=args.json, selected=is_explicit, source=db_source):
        return 5
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.core.schema_version import SchemaVersionError
    expand = bool(getattr(args, "expand", False))
    show_quota_notes = bool(getattr(args, "show_quota_notes", False))
    try:
        payload = render_monitor_frame(args.db, selected, human=not args.json,
                                       preference_source=preference["source"], lead_reader=_lead_reader)
    except SchemaVersionError:
        payload = {"schema_version": "2.0", "status": "FAILED", "selected_groups": selected,
                   "errors": {"schema": "SchemaVersionError"}}
        if args.json:
            print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
        else:
            _print_grouped_snapshot(payload, args.view, expand=expand, show_quota_notes=show_quota_notes)
        error_line("schema_version_error", "Unsupported database schema", "peerhub guide")
        return 6
    payload["selected_groups"] = selected
    payload["available_groups"] = list(GROUP_IDS)
    payload["preference"] = {key: preference[key] for key in ("status", "source", "reason")}
    payload["store_selection"] = {"path": args.db, "source": getattr(args, "db_source", "unknown")}
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    else:
        _print_grouped_snapshot(payload, args.view, expand=expand, show_quota_notes=show_quota_notes)
    if _read_failed(payload) or payload["status"] == "FAILED":
        error_line("diag_unavailable", "Monitor evidence could not be read", "peerhub diag health")
        return 4
    return 0


LIVE_ROW_MARGIN = 2  # the command line stays on screen above the frame; terminals report one row more than they show


def _live_rows(height: int) -> int:
    """Rows the frame may use: the terminal height minus a margin, so the first line (ATTN) never scrolls off."""
    return max(8, height - LIVE_ROW_MARGIN)


def _print_grouped_snapshot(payload: dict[str, Any], view: str, expand: bool = False,
                            show_quota_notes: bool = False) -> None:
    """Render a human dashboard from one schema 2.0 snapshot."""
    from peerhub.cli.view import FrameOutput, use_color
    tty = sys.stdout.isatty()
    output = FrameOutput(interactive=tty, overwrite=False)
    try:
        effective_view = ("rich" if output.vt else "plain") if view == "auto" else view
        color = effective_view == "rich" and use_color(output.vt, os.environ)
        bounded = tty and (expand or output.vt)
        width, height = output.geometry() if bounded else (output.width(), 24)
        effective_height = _live_rows(height) if bounded else None
        frame = _render_human_frame(payload, view=effective_view, width=width,
                                    height=effective_height, color=color, expand=expand,
                                    show_quota_notes=show_quota_notes)
        sys.stdout.write(frame)
        sys.stdout.flush()
    finally:
        output.close()


def run_monitor_repair(args: argparse.Namespace) -> int:
    from peerhub.cli.store_select import report_missing_store
    db_source = getattr(args, "db_source", None)
    is_explicit = db_source in ("explicit", "env")
    if report_missing_store(args.db, json_output=args.json, selected=is_explicit, source=db_source):
        return 5
    from peerhub.extensions.session_receipts import rebuild_delivery_projections
    try:
        result = rebuild_delivery_projections(args.db, chunk_size=args.chunk_size)
    except (OSError, ValueError, sqlite3.Error) as exc:
        result = {"status": "ERROR", "reason": type(exc).__name__}
        if args.json:
            print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        error_line("storage_corrupt", str(result["reason"]), "peerhub diag health")
        return 4
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")) if args.json else "\n".join(f"{key}: {value}" for key, value in result.items()))
    return 0
