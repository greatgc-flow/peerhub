"""``peerhub config init --scope global|workspace`` (item 13, dotdir
consolidation, ratified 2026-09-09).

Scaffolds one scope's config directory and seeds it with a commented
starter file copied from the packaged defaults, but only where one
genuinely exists and only when nothing is already there -- this command
never overwrites a file a user has already started editing. ``ask.toml``
is seeded at both scopes; ``models.toml`` only at global scope, since
there is no workspace ``models.toml`` (ratified release gate: workspace
model bindings remain the sole workspace model authority).
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from peerhub.application.config_paths import (
    resolve_global_config_home,
    resolve_workspace_config_home,
)

_GLOBAL_STARTERS: tuple[tuple[str, str], ...] = (
    ("ask-defaults.toml", "ask.toml"),
    ("model-defaults.toml", "models.toml"),
)
_WORKSPACE_STARTERS: tuple[tuple[str, str], ...] = (
    ("ask-defaults.toml", "ask.toml"),
)


def init_config_scope(
    *, scope: str, workspace_root: Path | None
) -> tuple[Path, tuple[str, ...]]:
    """Create ``scope``'s config directory and seed any missing starters.

    Returns ``(config_home, created)`` -- ``created`` names only the
    starter files actually written this call; a file already present is
    left untouched and not counted.
    """

    if scope == "global":
        config_home = resolve_global_config_home().path
        starters = _GLOBAL_STARTERS
    elif scope == "workspace":
        if workspace_root is None:
            raise ValueError("scope='workspace' requires a workspace_root")
        config_home = resolve_workspace_config_home(workspace_root).path
        starters = _WORKSPACE_STARTERS
    else:
        raise ValueError(f"unknown scope {scope!r}; must be 'global' or 'workspace'")

    config_home.mkdir(parents=True, exist_ok=True)
    packaged = resources.files("peerhub.config_data")
    created: list[str] = []
    for source_name, dest_name in starters:
        dest = config_home / dest_name
        if dest.exists():
            continue
        with resources.as_file(packaged.joinpath(source_name)) as source_path:
            shutil.copy2(source_path, dest)
        created.append(dest_name)

    return config_home, tuple(created)
