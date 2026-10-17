"""Read-only declarative model/profile selection, not provider availability evidence.

Explicit profiles use workspace > global > packaged per-key configuration.
No profile is selected implicitly, and no wildcard invents a named profile.
"""
from __future__ import annotations

import os
import tomllib
from importlib import resources
from importlib.resources.abc import Traversable
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


def inspect_profile(profile: str, workspace: str | Path) -> dict[str, Any]:
    """Return effective declarative values with the winning TOML layer per field."""
    qualified = profile
    if "." not in qualified or not all(qualified.split(".", 1)):
        raise ValueError("profile must be a fully qualified provider.profile name")
    kind = qualified.split(".", 1)[0]
    model, effort = resolve_profile(kind, qualified, workspace)  # shared resolver remains the validation oracle
    packaged = resources.files("peerhub.config_data").joinpath("model-defaults.toml")
    global_home = Path(os.environ.get("PEERHUB_CONFIG_HOME") or Path.home() / ".peerhub" / "config")
    layer_paths: list[tuple[str, Any]] = [("packaged", packaged)]
    global_path = global_home / "models.toml"
    workspace_path = Path(workspace) / ".peerhub" / "config" / "models.toml"
    if global_path.exists():
        layer_paths.append(("global", global_path))
    if workspace_path.exists():
        layer_paths.append(("workspace", workspace_path))
    effective: dict[str, Any] = {}
    sources: dict[str, str] = {}
    found = False
    for layer_name, path in layer_paths:
        with path.open("rb") as handle:
            layer = tomllib.load(handle)
        profiles = layer.get("profiles", {})
        if not isinstance(profiles, dict):
            raise ValueError("model profile config [profiles] must be a table")
        profile_map = cast("dict[str, Any]", profiles)
        entry = profile_map.get(qualified)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise ValueError("model profile entry must be a table")
        found = True
        for key, value in cast("dict[str, Any]", entry).items():
            effective[key] = value
            sources[key] = layer_name
    if not found:
        raise ValueError(f"unknown model profile {qualified!r}")
    mode = effective["selection_mode"]
    return {
        "profile": qualified,
        "fields": {
            "selection_mode": {"value": mode, "source": sources["selection_mode"]},
            "model": {"value": model, "source": sources.get("model", sources["selection_mode"])},
            "effort": {"value": effort, "source": sources.get("reasoning_effort", sources["selection_mode"])},
            "pools": {"value": effective.get("pools"), "source": sources.get("pools", sources["selection_mode"])},
        },
    }


def list_profiles(workspace: str | Path) -> list[dict[str, Any]]:
    """List the union of explicitly named profiles across the same resolver layers."""
    packaged = resources.files("peerhub.config_data").joinpath("model-defaults.toml")
    global_home = Path(os.environ.get("PEERHUB_CONFIG_HOME") or Path.home() / ".peerhub" / "config")
    paths: list[Path | Traversable] = [packaged, global_home / "models.toml", Path(workspace) / ".peerhub" / "config" / "models.toml"]
    names: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        with path.open("rb") as handle:
            layer = tomllib.load(handle)
        profiles = layer.get("profiles", {})
        if not isinstance(profiles, dict):
            raise ValueError("model profile config [profiles] must be a table")
        profile_map = cast("dict[str, Any]", profiles)
        names.update(str(name) for name in profile_map)
    return [inspect_profile(name, workspace) for name in sorted(names)]


def packaged_defaults_meta() -> dict[str, Any]:
    """Read release-maintained revision and observation metadata from packaged defaults."""
    packaged = resources.files("peerhub.config_data").joinpath("model-defaults.toml")
    with packaged.open("rb") as handle:
        layer = tomllib.load(handle)
    return {
        "defaults_revision": str(layer.get("defaults_revision") or ""),
        "observed_at": str(layer.get("observed_at") or ""),
        "observed_versions": dict(layer.get("observed_versions") or {}),
    }
