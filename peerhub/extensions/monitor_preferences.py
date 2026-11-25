"""Read saved monitor preferences using workspace, global and packaged defaults."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, cast

from peerhub.core.monitor_groups import DEFAULT_GROUPS, GROUP_IDS


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
