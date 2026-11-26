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
import textwrap
from time import monotonic, sleep
from pathlib import Path
from typing import Any, cast

from peerhub.cli.monitor_legend import MONITOR_LEGEND, POOL_LEGEND
from peerhub.cli.monitor_preferences import SHOW_QUOTA_NOTES_DEFAULT
from peerhub.cli.monitor_refresh import (
    MONITOR_TICK_SECONDS, agy_token_command, agy_token_stop_message, monitor_auto_probe, refresh_monitor_quota,
)
from peerhub.cli.support import error_line, print_warnings
from peerhub.core.peer_kinds import QUOTA_CAPABLE_PEERS
from peerhub.core.monitor_groups import GROUP_IDS

RUNNING_REFRESH_SECONDS = 5
_GROUP_METAVAR = "GROUP"
_GROUP_CHOICES_HELP = " Choices: %(choices)s."


class _MonitorHelpFormatter(argparse.HelpFormatter):
    def _fill_text(self, text: str, width: int, indent: str) -> str:
        return "\n".join(textwrap.fill(line, width, initial_indent=indent, subsequent_indent=indent)
                         for line in text.splitlines())


def _lead_reader(**facts: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The lead sessions of the registered Claude Code folders (#376 A-1); looked up at call time, so tests isolate it."""
    from peerhub.extensions import lead_sessions
    return lead_sessions.read_leads(lead_sessions.lead_dirs(os.environ), **facts)


def register_monitor_parser(subparsers: Any) -> None:
    p = subparsers.add_parser("monitor", help="Watch committed quota evidence and diagnostics",
                             epilog="\n".join((POOL_LEGEND, *MONITOR_LEGEND)), formatter_class=_MonitorHelpFormatter)
    p.add_argument("--interval-seconds", dest="interval", type=float, default=60.0,
                   help="Run watch cycles every N s (positive seconds, default %(default)s). "
                        "On supported TTYs, the countdown redraws at most 1 Hz.")
    p.add_argument("--cycles", type=int, default=None,
                   help="Number of cycles (default: 1 when stdout is not a TTY, until interrupted on a TTY; 0 = until interrupted everywhere)")
    p.add_argument("--peers", nargs="+", default=list(QUOTA_CAPABLE_PEERS),
                   help="Peers to refresh quota for during watch")
    probe = p.add_mutually_exclusive_group()
    probe.add_argument("--probe", action="store_true", help="Refresh on the watch cadence, including redirected stdout")
    probe.add_argument("--no-probe", action="store_true",
                       help="Disable automatic quota refresh on a TTY (also PEERHUB_MONITOR_PROBE=off)")
    p.add_argument("--timeout-seconds", dest="timeout", type=float, default=15.0,
                   help="Probe timeout in seconds (default %(default)s)")
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
                   help="Check for quota collection every Kth cycle (K>=1, default %(default)s)")
    p.add_argument("--system-dir", dest="sys_dir", type=Path,
                   help="Explicit portable provider installation directory")
    p.add_argument("--group", action="append", choices=(*GROUP_IDS, "all"), default=None, metavar=_GROUP_METAVAR,
                   help="Select groups for a snapshot or watch. See peerhub monitor groups and use --expand for detailed sections."
                        + _GROUP_CHOICES_HELP)
    p.add_argument("--snapshot", action="store_true", help="Render one read-only grouped snapshot without refreshing providers")
    layout = p.add_mutually_exclusive_group()
    layout.add_argument("--compact", action="store_true", help="Render the compact dashboard (default)")
    layout.add_argument("--expand", action="store_true", help="Render detailed grouped sections with bars, pace and forecasts")
    p.add_argument("--show-quota-notes", action="store_true", default=SHOW_QUOTA_NOTES_DEFAULT,
                   help="Include unavailable quota windows and their reasons in compact and expanded output (hidden by default)")

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
            action.add_argument("--group", choices=GROUP_IDS, nargs="+", required=True, metavar=_GROUP_METAVAR,
                                help="Groups to show by default. See peerhub monitor groups." + _GROUP_CHOICES_HELP)
        action.add_argument("--json", action="store_true")
    repair = actions.add_parser("repair", help="Release dead asks and rebuild monitor projections from durable evidence")
    repair.add_argument("--chunk-size", type=int, default=500, help="Bounded delivery read chunk (1-5000)")
    repair.add_argument("--json", action="store_true")



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
    if getattr(args, "show_quota_notes", SHOW_QUOTA_NOTES_DEFAULT) and args.json:
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
    if args.snapshot and args.probe:
        parser.error("--snapshot cannot be combined with --probe")


@lru_cache(maxsize=32)
def _cached_profiles(workspace: str, fingerprints: tuple[tuple[str, str, int, int], ...]) -> list[dict[str, Any]]:
    from peerhub.extensions.adapters.profiles import META_KEYS, list_profiles

    profiles = list_profiles(Path(workspace), include_meta=True)
    for profile in profiles:
        fields: dict[str, dict[str, Any]] = profile["fields"]
        card: dict[str, dict[str, Any]] | None = profile["card"]
        sources = {field["source"] for field in (*fields.values(), *(card or {}).values())}
        profile["_definition_layer"] = next((layer for layer, *_ in reversed(fingerprints) if layer in sources), None)
        profile["suggest"] = fields["suggest"]["value"]
        for key in META_KEYS:
            fields.pop(key)
    return profiles


def _profile_fingerprints(workspace: Path) -> tuple[tuple[str, str, int, int], ...]:
    global_root = Path(os.environ.get("PEERHUB_CONFIG_HOME") or Path.home() / ".peerhub/config")
    paths = [("packaged", Path(str(resources.files("peerhub.config_data").joinpath("model-defaults.toml")))),
             ("global", global_root / "models.toml"), ("workspace", workspace / ".peerhub/config/models.toml")]
    result: list[tuple[str, str, int, int]] = []
    for layer, path in paths:
        try:
            stat = path.stat()
            result.append((layer, str(path), stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            result.append((layer, str(path), 0, 0))
    return tuple(result)


def _render_human_frame(payload: dict[str, Any], *, view: str, width: int,
                        height: int | None, color: bool, expand: bool = False,
                        show_quota_notes: bool = SHOW_QUOTA_NOTES_DEFAULT,
                        links: bool = False,
                        nearest_store: str | None = None) -> str:
    from peerhub.cli.monitor_links import file_link_url, monitor_links_enabled
    from peerhub.cli.monitor_layout import FRAME_TRAILING_ROWS
    from peerhub.cli.monitor_view import render_grouped_frame

    unicode_ok = view != "plain" and (sys.stdout.encoding or "").lower().startswith("utf")
    # A5: separately from the decoration, text cells keep every character this stream can encode (not only on UTF).
    text_encoding = sys.stdout.encoding or None
    from peerhub.cli.store_select import DEFAULT_DB_PATH, env_override_note, select_store
    selection = payload.get("store_selection", {})
    store_path = Path(str(selection.get("path") or ".peerhub/core.db")).resolve()
    workspace = store_path.parent.parent if store_path.parent.name == ".peerhub" else store_path.parent
    profile_urls: dict[str, str] = {}
    try:
        fingerprints = _profile_fingerprints(workspace)
        profiles = _cached_profiles(str(workspace), fingerprints)
        if view == "rich" and links and monitor_links_enabled(os.environ):
            urls = {layer: file_link_url(path) for layer, path, _, _ in fingerprints}
            profile_urls = {profile["profile"]: url for profile in profiles
                            if (url := urls.get(profile.get("_definition_layer") or ""))}
    except (OSError, ValueError):
        profiles = []
    nearest = select_store(DEFAULT_DB_PATH, use_env=False) if nearest_store is None else None
    payload = {**payload, "configured_profiles": profiles, "_profile_urls": profile_urls,
               "_nearest_store_path": (nearest.path if nearest is not None and nearest.source == "discovered"
                                        else nearest_store),
               "_store_note": env_override_note(str(store_path), selection.get("source"))}
    return render_grouped_frame(payload, view="plain" if view == "plain" else "rich",
                                color=color, unicode=unicode_ok, width=width,
                                height=None if height is None else height + FRAME_TRAILING_ROWS,
                                expand=expand, text_encoding=text_encoding, show_quota_notes=show_quota_notes,
                                links=links, tty=sys.stdout.isatty())


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
            print("warning: --legacy is deprecated; use --schema-version 1.1",
                  file=sys.stderr, flush=True)
        from peerhub.cli.monitor_schema11 import run_schema_11_watch
        return run_schema_11_watch(args, sleep_fn=sleep, monotonic_fn=monotonic)
    if legacy:
        print("warning: --legacy is deprecated; the grouped visual dashboard is now the default",
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
    if args.group:
        requested = list(dict.fromkeys(args.group))
        if "all" in requested and len(requested) > 1:
            parser.error("--group all cannot be combined with other group IDs")
        selected = list(GROUP_IDS) if requested == ["all"] else sorted(requested, key=GROUP_IDS.index)
        preference = {"status": "OK", "source": "command", "reason": None, "selected_groups": selected}
    else:
        from peerhub.extensions.monitor_preferences import effective_preferences

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
    nearest_store: str | None = None
    nearest_store_resolved = False

    def get_nearest_store() -> str | None:
        nonlocal nearest_store, nearest_store_resolved
        if not nearest_store_resolved:
            from peerhub.cli.store_select import DEFAULT_DB_PATH, select_store
            nearest = select_store(DEFAULT_DB_PATH, use_env=False, allow_process=False)
            nearest_store = nearest.path if nearest.source == "discovered" else None
            nearest_store_resolved = True
        return nearest_store
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.extensions.monitor_alerts import merge_activity_frame
    from peerhub.core.schema_version import SchemaVersionError
    from peerhub._console import FrameOutput, use_color
    output = FrameOutput(interactive=not args.json, anchor_rows=True, alternate=args.cycles == 0)
    auto_probe = monitor_auto_probe(args)
    effective_view = ("rich" if output.live else "plain") if args.view == "auto" else args.view
    color = effective_view == "rich" and use_color(output.live, os.environ)
    links = color and output.vt
    expand = bool(getattr(args, "expand", False))
    show_quota_notes = bool(getattr(args, "show_quota_notes", SHOW_QUOTA_NOTES_DEFAULT))
    last_payload: dict[str, Any] | None = None
    last_frame_monotonic = 0.0

    def read_groups(groups: list[str]) -> dict[str, Any]:
        try:
            return render_monitor_frame(args.db, groups, human=not args.json,
                                        preference_source=preference.get("source"), lead_reader=_lead_reader)
        except SchemaVersionError:
            return {"schema_version": "2.0", "status": "FAILED", "groups": {},
                    "errors": {"schema": "SchemaVersionError"}}

    def read_frame() -> dict[str, Any]:
        nonlocal last_payload, last_frame_monotonic
        payload = read_groups(selected)
        payload.update({"selected_groups": selected, "available_groups": list(GROUP_IDS),
                       "preference": {key: preference.get(key) for key in ("status", "source", "reason")},
                       "store_selection": {"path": str(args.db), "source": getattr(args, "db_source", "unknown")}})
        last_payload = payload
        last_frame_monotonic = monotonic()
        return payload

    def read_code(payload: dict[str, Any]) -> int:
        if _read_failed(payload):
            error_line("diag_unavailable", "Monitor evidence could not be read", "peerhub diag health")
            return 4
        errors_obj: object = payload.get("errors")
        errors = cast(dict[str, Any], errors_obj) if isinstance(errors_obj, dict) else {}
        return 6 if "schema" in errors else 0

    def emit(cycle: int, phase: str, refresh: dict[str, Any]) -> int:
        payload = read_frame()
        if output.live and refresh.get("status") not in (None, "NOT_STARTED"):
            warnings = refresh.get("warnings", [])
            warning_count = len(cast(list[Any], warnings)) if isinstance(warnings, list) else 0
            payload["refresh_state"] = (f"REFRESH {refresh['status']}"
                                         + (f" · {warning_count} warnings" if warning_count else ""))
            if warning_count:
                payload["refresh_warnings"] = warnings
        if args.json:
            print(json.dumps({"schema_version": "2.0", "cycle": cycle, "phase": phase, "refresh": refresh,
                              "snapshot": payload}, ensure_ascii=True, separators=(",", ":")), flush=True)
        else:
            width, height = output.geometry() if output.live else (output.width(), 24)
            output.write(_render_human_frame(payload, view=effective_view, width=width,
                                             height=_live_rows(height, alternate=output.alt_screen) if output.live else None,
                                             color=color,
                                             expand=expand, show_quota_notes=show_quota_notes, links=links,
                                             nearest_store=get_nearest_store()))
        return read_code(payload)

    def redraw(state: str | None = None, next_in: float | None = None, *, live_refresh: dict[str, Any] | None = None) -> None:
        """Redraw the latest activity and age the quota facts since their full read, consistently in line 1 and POOLS."""
        if last_payload is None:
            return
        display = dict(last_payload)
        if state is not None:
            display["refresh_state"] = state
        display["display_elapsed_seconds"] = max(0.0, monotonic() - last_frame_monotonic)
        if next_in is not None:
            display["next_refresh_in"] = next_in
        if live_refresh is not None:
            display["_live_refresh"] = live_refresh
        width, height = output.geometry()
        output.write(_render_human_frame(display, view=effective_view, width=width,
                                         height=_live_rows(height, alternate=output.alt_screen) if output.live else None,
                                         color=color,
                                         expand=expand, show_quota_notes=show_quota_notes, links=links,
                                         nearest_store=get_nearest_store()))

    def watch_live() -> int:
        nonlocal last_payload
        from peerhub.cli.monitor_refresh import RefreshJob, RefreshSchedule, read_quota_readings, required_quota_pools

        collect = args.probe or auto_probe
        peers = tuple(cast(list[str], getattr(args, "peers", list(QUOTA_CAPABLE_PEERS))))
        required = required_quota_pools(args.db, peers) if collect else dict.fromkeys(peers, set[str]())
        schedule = RefreshSchedule(required, forced_interval=args.interval * args.collect_every if args.probe else None)
        job: RefreshJob | None = None
        job_started = 0.0
        next_read = next_tick = monotonic()
        activity_interval = min(RUNNING_REFRESH_SECONDS, args.interval)
        next_activity = next_tick if "activity" in selected else math.inf
        heartbeat = 0
        warnings: list[str] = []
        try:
            while True:
                tick = monotonic()
                redraw_due = tick >= next_tick
                completed = job if redraw_due and job is not None and job.done() else None
                payload = None
                if redraw_due and (completed is not None or tick >= next_read):
                    if report_missing_store(args.db, json_output=False, selected=is_explicit, source=db_source):
                        return 5
                    payload = read_frame()
                    if collect:
                        readings = read_quota_readings(args.db)
                        schedule.update(readings, monotonic())
                    if next_activity != math.inf:
                        next_activity = tick + activity_interval
                    if tick >= next_read:
                        next_read = tick + args.interval
                elif tick >= next_activity:
                    activity = read_groups(["activity"])
                    if "schema" in activity.get("errors", {}):
                        return 6
                    if last_payload is not None:
                        last_payload = merge_activity_frame(last_payload, activity)
                    next_activity = tick + activity_interval
                if not redraw_due:
                    sleep(max(0.0, min(next_tick, next_activity) - monotonic()))
                    continue
                now = monotonic()
                stop = False
                if completed is not None:
                    job = None
                    if completed.result is not None:
                        schedule.completed(completed.peers, completed.result, now)
                        warnings = completed.result.get("warnings", [])
                        stop = bool(agy_token_command(completed.result) and not args.allow_agy_token_use)
                    elif not completed.cancelled():
                        schedule.deferred(completed.peers, now)
                schema_error = payload is not None and "schema" in (payload.get("errors") or {})
                if collect and job is None and not stop and not schema_error and (due := schedule.due(now)):
                    job = RefreshJob.start(args, due, force=args.probe, required=required,
                                           retry=tuple(peer for peer in due if schedule.failed(peer)))
                    job_started = now
                if last_payload is not None:
                    last_payload["refresh_warnings"] = warnings
                redraw("STOPPED · AGY TOKEN USE" if stop else None, live_refresh={
                    "heartbeat": heartbeat, "selected_pools": required,
                    "next_in": schedule.next_in(now) if collect else None,
                    "peers": job.peers if job is not None else (), "elapsed": now - job_started,
                    "failed_ago": schedule.failed_ago(now), "retry_in": schedule.retry_in(now),
                })
                if stop:
                    return 1
                if payload is not None and read_code(payload) == 6:
                    return 6
                heartbeat += 1
                next_tick = tick + MONITOR_TICK_SECONDS
                sleep(max(0.0, min(next_tick, next_activity) - monotonic()))
        finally:
            if job is not None:
                job.stop()

    if not Path(args.db).exists() and report_missing_store(args.db, json_output=args.json, selected=is_explicit, source=db_source):
        return 5  # a missing store is reported before any frame, with or without --probe
    try:
        if not args.json and args.cycles == 0 and sys.stdout.isatty():
            return watch_live()
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
            result = None
            if done % args.collect_every == 0:
                if args.probe:
                    if output.live:
                        redraw("REFRESHING", 0)
                    from peerhub.cli.monitor_refresh import refresh_with_profile_pools

                    result = refresh_with_profile_pools(args.db, args.peers, sys_dir=args.sys_dir,
                                                        deadline_sec=args.timeout)
                elif auto_probe:
                    result = refresh_monitor_quota(args, (lambda: redraw("REFRESHING")) if output.live else None)
            if result is not None:
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
                        if args.probe:
                            redraw("REFRESH OK" if last_refresh_code == 0 else "REFRESH WARN", remaining)
                        else:
                            redraw()
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
    from peerhub.cli.store_select import report_missing_store

    requested = args.group
    unique_requested = list(dict.fromkeys(requested or []))
    if "all" in unique_requested and len(unique_requested) > 1:
        parser.error("--group all cannot be combined with other group IDs")
    if unique_requested:
        preference = {"status": "OK", "source": "command", "reason": None}
    else:
        from peerhub.extensions.monitor_preferences import effective_preferences

        preference = effective_preferences()
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
    show_quota_notes = bool(getattr(args, "show_quota_notes", SHOW_QUOTA_NOTES_DEFAULT))
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


LIVE_ROW_MARGIN = 2  # the command line stays on screen above a normal-screen frame


def _live_rows(height: int, *, alternate: bool = False) -> int:
    """Rows the frame may use on the selected screen."""
    return max(0, height if alternate else height - LIVE_ROW_MARGIN)


def _print_grouped_snapshot(payload: dict[str, Any], view: str, expand: bool = False,
                            show_quota_notes: bool = SHOW_QUOTA_NOTES_DEFAULT) -> None:
    """Render a human dashboard from one schema 2.0 snapshot."""
    from peerhub._console import FrameOutput, use_color
    tty = sys.stdout.isatty()
    output = FrameOutput(interactive=tty, overwrite=False)
    try:
        effective_view = ("rich" if output.vt else "plain") if view == "auto" else view
        color = effective_view == "rich" and use_color(output.vt, os.environ)
        links = color and output.vt
        bounded = tty and (expand or output.vt)
        width, height = output.geometry() if bounded else (output.width(), 24)
        effective_height = _live_rows(height) if bounded else None
        frame = _render_human_frame(payload, view=effective_view, width=width,
                                    height=effective_height, color=color, expand=expand,
                                    show_quota_notes=show_quota_notes, links=links)
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
    from peerhub.extensions.bridge_ledger import reap_dead_asks
    from peerhub.extensions.delivery_projection_rebuild import rebuild_delivery_projections
    try:
        released = reap_dead_asks(args.db) if 1 <= args.chunk_size <= 5000 else 0
        result = rebuild_delivery_projections(args.db, chunk_size=args.chunk_size)
        result["released_dead_asks"] = released
    except (OSError, ValueError, sqlite3.Error) as exc:
        result = {"status": "ERROR", "reason": type(exc).__name__}
        if args.json:
            print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        error_line("storage_corrupt", str(result["reason"]), "peerhub diag health")
        return 4
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")) if args.json else "\n".join(f"{key}: {value}" for key, value in result.items()))
    return 0


def run_monitor_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if getattr(args, "monitor_action", None) is not None:
        if args.monitor_action == "repair":
            return run_monitor_repair(args)
        from peerhub.cli.monitor_preferences import run_preferences
        return run_preferences(args)
    return run_monitor(args, parser)
