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


def _load_layer(path: Path | Traversable) -> dict[str, Any]:
    with path.open("rb") as handle:
        layer = tomllib.load(handle)
    version = layer.get("schema_version", 1)
    if type(version) is not int or version != 1:
        raise ValueError("model profile config schema_version must be 1")
    if not isinstance(layer.get("profiles", {}), dict):
        raise ValueError("model profile config [profiles] must be a table")
    return layer


def _load_layers(workspace: str | Path | None) -> list[tuple[str, dict[str, Any]]]:
    packaged = resources.files("peerhub.config_data").joinpath("model-defaults.toml")
    global_home = Path(os.environ.get("PEERHUB_CONFIG_HOME") or Path.home() / ".peerhub" / "config")
    paths = [("global", global_home / "models.toml")]
    if workspace is not None:
        paths.append(("workspace", Path(workspace) / ".peerhub" / "config" / "models.toml"))
    layers = [("packaged", _load_layer(packaged))]
    for name, path in paths:
        if path.exists():
            layers.append((name, _load_layer(path)))
    return layers


def _select_profile(profile: str, layers: list[tuple[str, dict[str, Any]]]) -> tuple[dict[str, Any], dict[str, str]]:
    selected: dict[str, Any] = {}
    sources: dict[str, str] = {}
    found = False
    for name, layer in layers:
        profiles = cast("dict[str, Any]", layer.get("profiles", {}))
        if profile not in profiles:
            continue
        entry = profiles[profile]
        if not isinstance(entry, dict):
            raise ValueError("model profile entry must be a table")
        for key, value in cast("dict[str, Any]", entry).items():
            selected[key] = value
            sources[key] = name
        found = True
    if not found:
        raise ValueError(f"unknown model profile {profile!r}")
    return selected, sources


def resolve_profile(kind: str, profile: str, workspace: str | Path) -> tuple[str | None, str | None]:
    profile = qualified_profile(kind, profile)
    if not profile.startswith(kind + ".") or not profile[len(kind) + 1:]:
        raise ValueError("profile must be a fully qualified name for the target provider")
    selected, _ = _select_profile(profile, _load_layers(workspace))
    return _profile_values(selected)


def _profile_values(selected: dict[str, Any]) -> tuple[str | None, str | None]:
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


def inspect_profile(profile: str, workspace: str | Path, *, include_pool_source: bool = False) -> dict[str, Any]:
    """Return effective declarative values with the winning TOML layer per field."""
    return _inspect_profile(profile, _load_layers(workspace), include_pool_source=include_pool_source)


def _inspect_profile(profile: str, layers: list[tuple[str, dict[str, Any]]], *, include_pool_source: bool) -> dict[str, Any]:
    qualified = profile
    if "." not in qualified or not all(qualified.split(".", 1)):
        raise ValueError("profile must be a fully qualified provider.profile name")
    effective, sources = _select_profile(qualified, layers)
    model, effort = _profile_values(effective)
    mode = effective["selection_mode"]
    result: dict[str, Any] = {
        "profile": qualified,
        "fields": {
            "selection_mode": {"value": mode, "source": sources["selection_mode"]},
            "model": {"value": model, "source": sources.get("model", sources["selection_mode"])},
            "effort": {"value": effort, "source": sources.get("reasoning_effort", sources["selection_mode"])},
            "pools": {"value": effective.get("pools"), "source": sources.get("pools", sources["selection_mode"])},
        },
    }
    if include_pool_source:
        result["fields"]["pools_source"] = {"value": effective.get("pools_source"), "source": sources.get("pools_source")}
    return result


def list_profiles(workspace: str | Path, *, include_pool_source: bool = False) -> list[dict[str, Any]]:
    """List the union of explicitly named profiles across the same resolver layers."""
    layers = _load_layers(workspace)
    names: set[str] = set()
    for _, layer in layers:
        names.update(cast("dict[str, Any]", layer.get("profiles", {})))
    return [_inspect_profile(name, layers, include_pool_source=include_pool_source) for name in sorted(names)]


def packaged_defaults_meta() -> dict[str, Any]:
    """Read release-maintained revision and observation metadata from packaged defaults."""
    packaged = resources.files("peerhub.config_data").joinpath("model-defaults.toml")
    layer = _load_layer(packaged)
    return {
        "defaults_revision": str(layer.get("defaults_revision") or ""),
        "observed_at": str(layer.get("observed_at") or ""),
        "observed_versions": dict(layer.get("observed_versions") or {}),
    }


def known_model_pools(workspace: str | Path | None = None) -> dict[str, Any]:
    """List exact model registry entries with the same layer precedence as pool resolution."""
    models: dict[str, Any] = {}
    for _, layer in _load_layers(workspace):
        models.update(cast("dict[str, Any]", layer.get("exact_model_pools", {})))
    return dict(sorted(models.items()))


def resolve_model_pools(model: str, workspace: str | Path | None = None) -> list[str] | None:
    """Resolve pool IDs for an exact model name from workspace > global > packaged defaults."""
    entry = inspect_model_pools(model, workspace)
    return entry["pools"] if entry is not None else None


def inspect_model_pools(model: str, workspace: str | Path | None = None) -> dict[str, Any] | None:
    """Keep the exact mapping's verification evidence alongside its pool IDs."""
    for _, layer in reversed(_load_layers(workspace)):
        exact_pools: object = layer.get("exact_model_pools")
        if isinstance(exact_pools, dict) and model in exact_pools:
            entry: object = cast(dict[str, Any], exact_pools)[model]
            if isinstance(entry, dict) and "pools" in entry:
                pools: object = cast(dict[str, Any], entry)["pools"]
                if isinstance(pools, list):
                    entry = cast(dict[str, Any], entry)
                    return {"pools": [str(p) for p in cast(list[object], pools)],
                            "verified": bool(entry.get("pools_source")) and entry.get("pools_source") != "unverified"}
    return None

