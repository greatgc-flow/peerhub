"""CLI management commands for monitor preferences."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from peerhub.core.monitor_groups import GROUP_IDS, GROUPS

SHOW_QUOTA_NOTES_DEFAULT = False


def _atomic_write(path: Path, groups: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "schema_version = 1\n\n[monitor]\ndefault_groups = [" + ", ".join(json.dumps(group) for group in groups) + "]\n"
    handle, temporary = tempfile.mkstemp(prefix="monitor-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _emit(payload: dict[str, Any], json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    else:
        print(json.dumps(payload, ensure_ascii=True, indent=2))


def run_preferences(args: Any) -> int:
    """Run management subcommands without opening the database or starting peers."""
    from peerhub.extensions.monitor_preferences import effective_preferences, preference_paths

    cwd = Path.cwd()
    env = dict(os.environ)
    preference = effective_preferences(cwd, env)
    action = args.monitor_action
    if action == "groups":
        error = preference["status"] != "OK"
        groups = [{**item, "default_selected": None if error else item["id"] in preference["selected_groups"]}
                  for item in GROUPS]
        payload = {"schema_version": "2.0", "status": "ERROR" if error else "OK", "groups": groups,
                   "selection_scope": "grouped_views",
                   "preference": {key: preference[key] for key in ("status", "source", "reason")}}
        if args.json:
            _emit(payload, True)
        else:
            print("ID         DEFAULT  CONTENT")
            for item in groups:
                selected = "yes" if item["default_selected"] else "no" if item["default_selected"] is False else "unknown"
                print(f"{item['id']:<10} {selected:<8} {item['description']}")
            print(f"Defaults: {preference['source']} ({preference['status']})")
            if error and preference.get("reason"):
                print(f"Reason: {preference['reason']}")
        return int(error)
    if action != "defaults":
        return 2
    subaction = args.defaults_action
    if subaction == "show":
        payload = {"schema_version": "1.0", "status": preference["status"],
                   "selected_groups": preference.get("selected_groups"),
                   "selection_scope": "grouped_views",
                   "preference": {key: preference[key] for key in ("status", "source", "reason")}}
        if args.json:
            _emit(payload, True)
        else:
            print("ID         DEFAULT")
            for group in GROUP_IDS:
                selected = "unknown" if preference["status"] != "OK" else "yes" if group in preference["selected_groups"] else "no"
                print(f"{group:<10} {selected}")
            source = preference["source"]
            if sys.stdout.isatty() and source in ("workspace", "global"):
                from peerhub.cli.monitor_links import file_link_url, monitor_links_enabled, osc_link
                from peerhub.cli.view import FrameOutput

                if monitor_links_enabled(env):
                    workspace, global_path = preference_paths(cwd, env)
                    path = workspace if source == "workspace" else global_path
                    output = FrameOutput(overwrite=False)
                    try:
                        if output.vt and (url := file_link_url(path)):
                            source = osc_link(url, source)
                    finally:
                        output.close()
            print(f"Defaults: {source} ({preference['status']})")
            if preference.get("reason"):
                print(f"Reason: {preference['reason']}")
        return int(preference["status"] != "OK")
    workspace, global_path = preference_paths(cwd, env)
    target = global_path if args.global_preferences else workspace
    source = "global" if args.global_preferences else "workspace"
    if subaction == "set":
        selected = sorted(set(args.group), key=GROUP_IDS.index)
        _atomic_write(target, selected)
        _emit({"schema_version": "1.0", "status": "OK", "changed": True,
               "selected_groups": selected, "preference": {"status": "OK", "source": source, "reason": None}}, args.json)
        return 0
    existed = target.exists()
    if existed:
        target.unlink()
    _emit({"schema_version": "1.0", "status": "OK", "changed": existed,
           "selected_groups": None, "preference": {"status": "OK", "source": source, "reason": None}}, args.json)
    return 0
