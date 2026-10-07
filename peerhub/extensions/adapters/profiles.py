"""Read-only declarative model/profile selection, not provider availability evidence.

Explicit profiles use workspace > global > packaged per-key configuration.
No profile is selected implicitly, and no wildcard invents a named profile.
"""
from __future__ import annotations

import os
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any, cast


def resolve_profile(kind: str, profile: str, workspace: str | Path) -> tuple[str | None, str | None]:
    profile = qualified_profile(kind, profile)
    if not profile.startswith(kind + ".") or not profile[len(kind) + 1:]:
        raise ValueError("profile must be a fully qualified name for the target provider")
    packaged = resources.files("peerhub.config_data").joinpath("model-defaults.toml")
    with packaged.open("rb") as handle:
        layers = [tomllib.load(handle)]
    global_home = Path(os.environ.get("PEERHUB_CONFIG_HOME") or Path.home() / ".peerhub" / "config")
    for path in (global_home / "models.toml", Path(workspace) / ".peerhub" / "config" / "models.toml"):
        if path.exists():
            with path.open("rb") as handle:
                layers.append(tomllib.load(handle))
    selected: dict[str, Any] = {}
    found = False
    for layer in layers:
        if layer.get("schema_version", 1) != 1:
            raise ValueError("model profile config schema_version must be 1")
        profiles = layer.get("profiles", {})
        if not isinstance(profiles, dict):
            raise ValueError("model profile config [profiles] must be a table")
        profiles = cast("dict[str, Any]", profiles)
        if profile not in profiles:
            continue
        entry = profiles[profile]
        if not isinstance(entry, dict):
            raise ValueError("model profile entry must be a table")
        selected.update(cast("dict[str, Any]", entry))
        found = True
    if not found:
        raise ValueError(f"unknown model profile {profile!r}")
    mode = selected.get("selection_mode")
    model = selected.get("model") if mode == "pinned" else None
    effort = selected.get("reasoning_effort")
    if mode not in ("pinned", "cli_default"):
        raise ValueError("model profile selection_mode must be pinned or cli_default")
    if mode == "pinned" and (not isinstance(model, str) or not model):
        raise ValueError("pinned model profile requires a nonempty model")
    if effort is not None and (not isinstance(effort, str) or not effort):
        raise ValueError("model profile reasoning_effort must be a nonempty string")
    return model, effort


def qualified_profile(kind: str, profile: str) -> str:
    """Legacy short profile IDs are qualified by the selected provider, never guessed across providers."""
    return f"{kind}.{profile}" if "." not in profile else profile
