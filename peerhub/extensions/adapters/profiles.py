"""Read-only declarative model/profile selection, not provider availability evidence.

Explicit profiles use workspace > global > packaged per-key configuration.
No profile is selected implicitly, and no wildcard invents a named profile.
"""
from __future__ import annotations

import os
import re
import sys
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


BINDING_KEYS = ("selection_mode", "model", "reasoning_effort")
CARD_KEYS = ("use_for", "avoid_for", "traits", "evidence", "card_checked")
# 0.25.0 plan D11/A9: optional per-profile fields. They are never inherited from an alias target.
META_KEYS = ("alias_of", "effort_override", "suggest", "measurement")
META_DEFAULTS: dict[str, Any] = {"alias_of": None, "effort_override": None, "suggest": True, "measurement": "measured"}
MEASUREMENTS = ("measured", "unmeasured")
_PROFILE_ID = re.compile(r"(?:ag|cc|cx|ca|oa|ga)\.[A-Za-z0-9_.-]+")
_NOTICED: set[tuple[str, str, str | None]] = set()


def _card(effective: dict[str, Any], sources: dict[str, str]) -> dict[str, Any] | None:
    """Validate format only (repo paths are not in the wheel); existence is a packaging test."""
    card = {key: effective[key] for key in CARD_KEYS if key in effective}
    if not card:
        return None
    for key in ("use_for", "avoid_for"):
        value = card.get(key)
        if value is not None and (not isinstance(value, str) or not value or len(value) > 80):
            raise ValueError(f"profile card {key} must be a nonempty string of at most 80 characters")
    traits: object = card.get("traits")
    if traits is not None and (not isinstance(traits, list) or not 1 <= len(cast("list[object]", traits)) <= 3
                               or not all(isinstance(t, str) and t and len(t) <= 40 for t in cast("list[object]", traits))):
        raise ValueError("profile card traits must be 1-3 nonempty strings of at most 40 characters")
    evidence: object = card.get("evidence")
    if (not isinstance(evidence, list) or not evidence
            or not all(isinstance(e, str) and e and not any(c.isspace() for c in e) for e in cast("list[object]", evidence))):
        raise ValueError("profile card needs evidence: a list of lesson ids, repo paths or #issue numbers")
    checked = card.get("card_checked")
    if not isinstance(checked, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked):
        raise ValueError("profile card needs card_checked as YYYY-MM-DD")
    return {key: {"value": value, "source": sources[key]} for key, value in card.items()}


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
        entry = cast("dict[str, Any]", entry)
        if found and any(key in entry and entry[key] != selected.get(key) for key in BINDING_KEYS):
            for key in CARD_KEYS:  # card claims are bound to the binding they were checked against
                selected.pop(key, None)
                sources.pop(key, None)
        for key, value in entry.items():
            selected[key] = value
            sources[key] = name
        found = True
    if not found:
        raise ValueError(f"unknown model profile {profile!r}; run 'peerhub profile list' to see valid profiles")
    return selected, sources


def _check_meta(profile: str, entry: dict[str, Any]) -> None:
    """Validate the optional profile fields of one effective entry (format only)."""
    kind = profile.split(".", 1)[0]
    suggest: object = entry.get("suggest", True)
    if not isinstance(suggest, bool):
        raise ValueError(f"profile {profile}: suggest must be true or false")
    measurement: object = entry.get("measurement", "measured")
    if measurement not in MEASUREMENTS:
        raise ValueError(f"profile {profile}: measurement must be measured or unmeasured")
    alias: object = entry.get("alias_of")
    override: object = entry.get("effort_override")
    if override is not None and alias is None:
        raise ValueError(f"profile {profile}: effort_override needs alias_of")
    if override is not None and (not isinstance(override, str) or not override):
        raise ValueError(f"profile {profile}: effort_override must be a nonempty string")
    if alias is None:
        return
    if not isinstance(alias, str) or not _PROFILE_ID.fullmatch(alias):
        raise ValueError(f"profile {profile}: alias_of must be a qualified profile id such as {kind}.effort")
    if alias.split(".", 1)[0] != kind:
        raise ValueError(f"profile {profile}: alias_of must name a {kind} profile")
    if alias == profile:
        raise ValueError(f"profile {profile}: alias_of cannot name itself")


def _effective(profile: str, layers: list[tuple[str, dict[str, Any]]]) -> tuple[dict[str, Any], dict[str, str]]:
    """The merged entry of one profile; an alias takes the binding of its target (one level), its own keys on top."""
    selected, sources = _select_profile(profile, layers)
    _check_meta(profile, selected)
    target = selected.get("alias_of")
    if target is None:
        return selected, sources
    target_entry, target_sources = _select_profile(target, layers)
    if target_entry.get("alias_of") is not None:
        raise ValueError(f"profile {profile}: alias chains are not supported ({target} is itself an alias)")
    effective = {key: value for key, value in target_entry.items() if key not in CARD_KEYS and key not in META_KEYS}
    effective_sources = {key: target_sources[key] for key in effective}
    effective.update(selected)
    effective_sources.update(sources)
    override = selected.get("effort_override")
    if override is not None:
        effective["reasoning_effort"] = override
        effective_sources["reasoning_effort"] = sources["effort_override"]
    return effective, effective_sources


def reset_deprecation_notices() -> None:
    """Forget which alias notices were printed (tests; a long-lived process starts over)."""
    _NOTICED.clear()


def _notice_alias(profile: str, effective: dict[str, Any]) -> None:
    """One stderr line per process and alias when a deprecated alias is resolved for an ask."""
    target, override = str(effective["alias_of"]), effective.get("effort_override")
    key = (profile, target, override)
    if key in _NOTICED:
        return
    _NOTICED.add(key)
    effort = f" with effort {override}" if override else ""
    use = f"--profile {target} --effort {override}" if override else f"--profile {target}"
    print(f"peerhub: profile {profile} is deprecated: an alias of {target}{effort}; use {use}", file=sys.stderr)


def resolve_profile(kind: str, profile: str, workspace: str | Path) -> tuple[str | None, str | None]:
    profile = qualified_profile(kind, profile)
    if not profile.startswith(kind + ".") or not profile[len(kind) + 1:]:
        raise ValueError("profile must be a fully qualified name for the target provider")
    selected, _ = _effective(profile, _load_layers(workspace))
    values = _profile_values(selected)
    if selected.get("alias_of") is not None:
        _notice_alias(profile, selected)
    return values


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


def inspect_profile(profile: str, workspace: str | Path, *, include_pool_source: bool = False,
                    include_meta: bool = False) -> dict[str, Any]:
    """Return effective declarative values with the winning TOML layer per field.

    include_meta adds alias_of, effort_override, suggest and measurement (source "default" when no layer sets one).
    They stay out by default: the monitor read model embeds this result and its JSON must not grow.
    """
    return _inspect_profile(profile, _load_layers(workspace), include_pool_source=include_pool_source, include_meta=include_meta)


def _inspect_profile(profile: str, layers: list[tuple[str, dict[str, Any]]], *, include_pool_source: bool,
                     include_meta: bool = False) -> dict[str, Any]:
    qualified = profile
    if "." not in qualified or not all(qualified.split(".", 1)):
        raise ValueError("profile must be a fully qualified provider.profile name")
    effective, sources = _effective(qualified, layers)
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
    if include_meta:
        for key in META_KEYS:
            result["fields"][key] = {"value": effective.get(key, META_DEFAULTS[key]), "source": sources.get(key, "default")}
    result["card"] = _card(effective, sources)
    return result


def list_profiles(workspace: str | Path, *, include_pool_source: bool = False, include_meta: bool = False) -> list[dict[str, Any]]:
    """List the union of explicitly named profiles across the same resolver layers."""
    layers = _load_layers(workspace)
    names: set[str] = set()
    for _, layer in layers:
        names.update(cast("dict[str, Any]", layer.get("profiles", {})))
    return [_inspect_profile(name, layers, include_pool_source=include_pool_source, include_meta=include_meta)
            for name in sorted(names)]


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

