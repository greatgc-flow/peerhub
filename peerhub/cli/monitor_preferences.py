"""Stable monitor group catalog and display-only preference management."""
from __future__ import annotations

import json
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any, cast

GROUPS: tuple[dict[str, Any], ...] = (
    {"id": "overview", "order": 1, "description": "Cross-group peer summary and highest-priority state.", "collection_effect": "read_only"},
    {"id": "peers", "order": 2, "description": "Per-peer dependency chain: quota, profile, session reuse, and context.", "collection_effect": "read_only"},
    {"id": "execution", "order": 3, "description": "Bridge delivery certainty, session reuse, and measured delivery latency; not task success.", "collection_effect": "read_only"},
    {"id": "sessions", "order": 4, "description": "Retained session lifecycle, fresh/reuse state, and configured profile/model/effort.", "collection_effect": "read_only"},
    {"id": "usage", "order": 5, "description": "Provider-reported token usage and its reporting coverage.", "collection_effect": "read_only"},
    {"id": "context", "order": 6, "description": "Runtime context occupancy evidence; unknown when providers do not report it.", "collection_effect": "read_only"},
    {"id": "capacity", "order": 7, "description": "Quota pools, remaining capacity, freshness, and supported seven-day pace.", "collection_effect": "read_only"},
    {"id": "runtime", "order": 8, "description": "Current profile provenance and adapter/runtime details.", "collection_effect": "read_only"},
    {"id": "coverage", "order": 9, "description": "Data completeness, stale/unknown evidence, legacy gaps, and sampling limits.", "collection_effect": "read_only"},
    {"id": "store", "order": 10, "description": "Database schema, size, projection, and storage integrity summary.", "collection_effect": "read_only"},
)
GROUP_IDS = tuple(str(item["id"]) for item in GROUPS)
DEFAULT_GROUPS = ("overview", "peers", "execution", "sessions", "usage", "context", "capacity", "coverage")


def _preference_paths(cwd: Path, env: dict[str, str]) -> tuple[Path, Path]:
    workspace = cwd / ".peerhub" / "config" / "monitor.toml"
    config_home = env.get("PEERHUB_CONFIG_HOME", "").strip()
    global_root = Path(config_home).expanduser() if config_home else Path.home() / ".peerhub" / "config"
    return workspace, global_root / "monitor.toml"


def _decode(path: Path, source: str) -> dict[str, Any]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return {"status": "ERROR", "source": source, "reason": "invalid_toml"}
    if data.get("schema_version") != 1:
        return {"status": "ERROR", "source": source, "reason": "unsupported_schema"}
    monitor_raw: object = data.get("monitor")
    monitor: dict[str, Any] = cast(dict[str, Any], monitor_raw) if isinstance(monitor_raw, dict) else {}
    groups_raw: object = monitor.get("default_groups")
    if not isinstance(groups_raw, list):
        return {"status": "ERROR", "source": source, "reason": "invalid_group"}
    raw_items: list[object] = cast(list[object], groups_raw)
    if any(not isinstance(item, str) or item not in GROUP_IDS for item in raw_items):
        return {"status": "ERROR", "source": source, "reason": "invalid_group"}
    groups = cast(list[str], raw_items)
    if len(groups) != len(set(groups)):
        return {"status": "ERROR", "source": source, "reason": "invalid_group"}
    return {"status": "OK", "source": source, "reason": None, "selected_groups": groups}


def effective_preferences(cwd: Path | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    current = cwd or Path.cwd()
    environment = os.environ if env is None else env
    workspace, global_path = _preference_paths(current, dict(environment))
    for path, source in ((workspace, "workspace"), (global_path, "global")):
        if path.exists():
            return _decode(path, source)
    return {"status": "OK", "source": "packaged", "reason": None, "selected_groups": list(DEFAULT_GROUPS)}


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
        _emit(payload, args.json)
        return int(preference["status"] != "OK")
    workspace, global_path = _preference_paths(cwd, env)
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
