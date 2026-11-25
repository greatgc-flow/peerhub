"""Local profile inspection commands."""
from __future__ import annotations

import argparse
import json
import sqlite3
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from peerhub.cli.arguments import WORKSPACE_OPTIONS
from peerhub.cli.output import emit
from peerhub.cli.support import error_line
from peerhub.core.schema_version import SchemaVersionError

if TYPE_CHECKING:
    from peerhub.cli.arguments import Subparsers


def register_profile_parser(subparsers: Subparsers) -> None:
    profile_parser = subparsers.add_parser("profile", help="Inspect effective local model profiles (read-only)")
    profile_sub = profile_parser.add_subparsers(dest="action", required=True)
    profile_list = profile_sub.add_parser("list", help="List configured profiles")
    profile_list.add_argument("--format", choices=["auto", "table", "json"], default="auto", help=argparse.SUPPRESS)
    profile_show = profile_sub.add_parser("show", help="Show effective profile values and source layers")
    profile_show.add_argument("profile", help="Profile ID to inspect (for example, cx.standard)")
    profile_show.add_argument("--format", choices=["auto", "table", "json"], default="auto", help=argparse.SUPPRESS)
    profile_stats = profile_sub.add_parser("stats", help="Aggregate stored ask measurements by profile")
    profile_check = profile_sub.add_parser("check", help="Check installed model IDs and measurement age")
    for profile_action in (profile_list, profile_show, profile_stats, profile_check):
        profile_action.add_argument(*WORKSPACE_OPTIONS, type=Path, default=None,
                                    help="Workspace whose profile files apply (default: current directory)")


def _use_for_cell(card: dict[str, Any] | None) -> str:
    return str(card["use_for"]["value"]) if card and "use_for" in card else "-"


def _card_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "; ".join(str(item) for item in cast("list[object]", value)) or "(unset)"
    return str(value or "(unset)")


def _profile_notes(fields: dict[str, Any]) -> str:
    """`profile list` NOTES: a deprecated alias, a profile that is not suggested, an unmeasured model id; `-` when none."""
    notes: list[str] = []
    alias = fields["alias_of"]["value"]
    if alias:
        override = fields["effort_override"]["value"]
        notes.append(f"alias of {alias} ({override})" if override else f"alias of {alias}")
    if fields["suggest"]["value"] is False:
        notes.append("not suggested")
    if fields["measurement"]["value"] == "unmeasured":
        notes.append("unmeasured")
    return "; ".join(notes) or "-"


def run_profile(args: argparse.Namespace) -> int:
    from peerhub.extensions.adapters.profiles import inspect_profile, known_model_pools, list_profiles

    workspace = getattr(args, "workspace", None) or Path.cwd()
    use_json = args.json or getattr(args, "format", None) == "json"
    try:
        if args.action == "show":
            profile = inspect_profile(args.profile, workspace, include_meta=True)
            payload: dict[str, Any] = {"schema_version": "1.0", "workspace": str(workspace), **profile}
        else:
            from peerhub.cli.profile_stats import render_profile_stats
            from peerhub.extensions.profile_stats import check_profiles, read_profile_stats
            profiles = list_profiles(workspace, include_meta=True)
            try:
                stats = read_profile_stats(args.db, workspace, profiles)
            except (sqlite3.Error, SchemaVersionError, OSError, ValueError):
                if args.action != "list":
                    raise
                stats = [{"n": None, "stale": None} for _ in profiles]
            if args.action == "stats":
                payload = {"schema_version": "1.0", "workspace": str(workspace), "profiles": stats}
                emit(payload, True) if use_json else print(render_profile_stats(stats))
                return 0
            if args.action == "check":
                findings = check_profiles(profiles, stats, workspace)
                if use_json:
                    emit({**findings, "workspace": str(workspace)}, True)
                else:
                    missing = ", ".join(f"{name} ({model})" for name, model in findings["missing_models"].items())
                    print("Missing from installed model lists | " + (missing or "-"))
                    print("No current measurement in 30 days | " + (", ".join(findings["measurement_overdue"]) or "-"))
                return 0
            for profile, stat in zip(profiles, stats):
                profile["stats"] = {key: stat[key] for key in ("n", "stale")}
            payload = {"schema_version": "1.0", "workspace": str(workspace), "profiles": profiles,
                       "known_models": known_model_pools(workspace)}
    except (OSError, ValueError) as exc:
        error = {"schema_version": "1.0", "status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        if use_json:
            print(json.dumps(error, ensure_ascii=True, indent=2))
        error_line("invalid_arguments", str(exc))  # the message only: no Python class name (#401)
        return 1
    if use_json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0
    if args.action == "list":
        rows = [[profile["profile"], str(profile["fields"]["model"]["value"] or "CLI default"),
                 str(profile["fields"]["effort"]["value"] or "provider default"),
                 _profile_notes(profile["fields"]),
                 "unavailable" if profile["stats"]["n"] is None else
                 str(profile["stats"]["n"]) + (" stale" if profile["stats"]["stale"] else ""),
                 _use_for_cell(profile.get("card"))] for profile in payload["profiles"]]
        columns = ["PROFILE", "MODEL", "EFFORT", "NOTES", "STATS", "USE FOR (profile show ID)"]
    else:
        card: dict[str, Any] = payload.get("card") or {}
        items = list(payload["fields"].items()) + list(card.items())
        rows = [[key, _card_value(field["value"]), str(field["source"])] for key, field in items]
        columns = ["FIELD", "VALUE", "SOURCE"]
    if not rows:
        print("(no profiles)")
        return 0
    widths = [max(len(col), *(len(row[index]) for row in rows)) for index, col in enumerate(columns)]
    if args.action == "list":
        from peerhub._console import FrameOutput

        terminal_width = FrameOutput.width()
        wrapper = textwrap.TextWrapper(width=terminal_width, subsequent_indent="  " if terminal_width > 2 else "",
                                       break_on_hyphens=False)
    else:
        wrapper = None
    if wrapper is not None and sum(widths) + 2 * (len(columns) - 1) > wrapper.width:
        for profile, *values in rows:
            print(wrapper.fill(profile))
            for label, value in zip(columns[1:], values):
                print(wrapper.fill(f"  {label}: {value}"))
            print()
    else:
        print("  ".join(col.ljust(widths[i]) for i, col in enumerate(columns)).rstrip())
        print("  ".join("-" * width for width in widths))
        for row in rows:
            print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)).rstrip())
    if args.action == "list":
        assert wrapper is not None
        print("\n" + wrapper.fill("Known models (pool):"))
        for model, entry in payload["known_models"].items():
            print(wrapper.fill(f"  {model}: {', '.join(entry['pools'])} ({entry.get('pools_source') or 'unverified'})"))
    return 0


_SESSION_CONTEXT_FIELDS = ("ctx_est_pct", "ctx_est_tokens", "ctx_age_seconds")
