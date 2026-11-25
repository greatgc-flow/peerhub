"""Monitor group catalog and saved display preferences shared by CLI and diagnostics.

The CLI registers the group choices even when extensions are disabled.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, cast

GROUPS: tuple[dict[str, Any], ...] = (
    {"id": "overview", "order": 1, "description": "Cross-group peer summary and highest-priority state.", "collection_effect": "read_only"},
    {"id": "activity", "order": 2, "description": "Running and recent asks (state, request and reply lines) and the leads that launched them.", "collection_effect": "read_only"},
    {"id": "peers", "order": 3, "description": "Per-peer dependency chain: quota, profile, session reuse, and context.", "collection_effect": "read_only"},
    {"id": "execution", "order": 4, "description": "Bridge delivery certainty, session reuse, and measured delivery latency; not task success.", "collection_effect": "read_only"},
    {"id": "sessions", "order": 5, "description": "Retained session lifecycle, fresh/reuse state, and configured profile/model/effort.", "collection_effect": "read_only"},
    {"id": "usage", "order": 6, "description": "Provider-reported token usage and its reporting coverage.", "collection_effect": "read_only"},
    {"id": "context", "order": 7, "description": "Runtime context occupancy evidence; unknown when providers do not report it.", "collection_effect": "read_only"},
    {"id": "capacity", "order": 8, "description": "Quota pools, remaining capacity, freshness, and supported seven-day pace.", "collection_effect": "read_only"},
    {"id": "runtime", "order": 9, "description": "Current profile provenance and adapter/runtime details.", "collection_effect": "read_only"},
    {"id": "coverage", "order": 10, "description": "Data completeness, stale/unknown evidence, legacy gaps, and sampling limits.", "collection_effect": "read_only"},
    {"id": "store", "order": 11, "description": "Database schema, size, projection, and storage integrity summary.", "collection_effect": "read_only"},
)
GROUP_IDS = tuple(str(item["id"]) for item in GROUPS)
DEFAULT_GROUPS = ("overview", "activity", "peers", "execution", "sessions", "usage", "context", "capacity", "coverage")


def preference_paths(cwd: Path, env: dict[str, str]) -> tuple[Path, Path]:
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
    workspace, global_path = preference_paths(current, dict(environment))
    for path, source in ((workspace, "workspace"), (global_path, "global")):
        if path.exists():
            return _decode(path, source)
    return {"status": "OK", "source": "packaged", "reason": None, "selected_groups": list(DEFAULT_GROUPS)}
