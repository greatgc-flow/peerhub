"""Layered model/profile configuration resolution.

Precedence (highest to lowest): workspace binding (SQLite, via
``PeerRegistryService.get_profile_binding``) > global config
(``~/.peerhub/config/models.toml``, overridable via ``PEERHUB_CONFIG_HOME``)
> packaged default (``peerhub/config_data/model-defaults.toml``) >
configuration error. Adapters never read config themselves -- this service
resolves once per dispatch into an immutable ``ResolvedModelBinding``,
carried unchanged on ``AdapterRequest`` (ARCHITECTURE decision, 2026-09-08
3-round ag/cx/cc ratification).
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any, cast

from peerhub.adapters.contract import ModelSelectionMode, ResolvedModelBinding
from peerhub.application import config_paths
from peerhub.application.peer_registry import PeerRegistryService
from peerhub.core.protocol import JsonValue


class ModelConfigError(RuntimeError):
    """Raised when a config layer is malformed, or nothing resolves at all."""


def global_config_path() -> Path:
    """Return the global config file to consult.

    ``PEERHUB_CONFIG_HOME``, when set, REPLACES the default
    ``~/.peerhub/config`` directory outright for CI/portable-install
    isolation -- it must never silently fall back to ``~`` if its file is
    absent, since that would defeat the exact isolation it exists for.
    The returned path may or may not exist; "does not exist" simply means
    this layer is absent, not an error.
    """

    return config_paths.resolve_global_config_home().path / "models.toml"


def _read_profile_entry(
    data: dict[str, Any], profile_id: str
) -> dict[str, Any] | None:
    profiles_raw = data.get("profiles")
    if not isinstance(profiles_raw, dict):
        return None
    profiles = cast("dict[str, Any]", profiles_raw)
    entry = profiles.get(profile_id)
    return cast("dict[str, Any]", entry) if isinstance(entry, dict) else None


def _binding_from_toml_entry(
    entry: dict[str, Any], *, source_layer: str
) -> ResolvedModelBinding:
    mode_raw = entry.get("selection_mode", "pinned")
    if mode_raw not in ("pinned", "cli_default"):
        raise ModelConfigError(
            f"{source_layer} config: selection_mode must be 'pinned' or "
            f"'cli_default', got {mode_raw!r}"
        )
    selection_mode = ModelSelectionMode(mode_raw)
    model = entry.get("model")
    if selection_mode is ModelSelectionMode.PINNED:
        if not isinstance(model, str) or not model:
            raise ModelConfigError(
                f"{source_layer} config: 'model' is required when "
                "selection_mode is 'pinned'"
            )
    else:
        if model is not None:
            raise ModelConfigError(
                f"{source_layer} config: 'model' must not be set when "
                "selection_mode is 'cli_default'"
            )
        model = None
    effort = entry.get("reasoning_effort")
    if effort is not None and not isinstance(effort, str):
        raise ModelConfigError(
            f"{source_layer} config: reasoning_effort must be a string"
        )
    return ResolvedModelBinding(
        selection_mode=selection_mode,
        model_id=model,
        reasoning_effort=effort,
        source_layer=source_layer,
    )


def _binding_from_workspace_state(
    state: Mapping[str, JsonValue], *, source_layer: str
) -> ResolvedModelBinding:
    # Absent selection_mode means a schema_version-1 record, which always
    # carried a real model_id -- treat as pinned (peer_registry.py's own
    # bind_profile()/collect_model_status() apply the same default).
    mode_raw = state.get("selection_mode", "pinned")
    if not isinstance(mode_raw, str) or mode_raw not in (
        ModelSelectionMode.PINNED.value,
        ModelSelectionMode.CLI_DEFAULT.value,
    ):
        raise ModelConfigError(
            "workspace binding: selection_mode must be 'pinned' or "
            f"'cli_default', got {mode_raw!r}"
        )
    selection_mode = ModelSelectionMode(mode_raw)
    model = state.get("model_id")
    effort = state.get("reasoning_effort")
    if selection_mode is ModelSelectionMode.PINNED:
        if not isinstance(model, str) or not model:
            raise ModelConfigError(
                "workspace binding: model_id is required when "
                "selection_mode is 'pinned'"
            )
    elif model is not None:
        raise ModelConfigError(
            "workspace binding: model_id must not be set when "
            "selection_mode is 'cli_default'"
        )
    if effort is not None and not isinstance(effort, str):
        raise ModelConfigError(
            "workspace binding: reasoning_effort must be a string"
        )
    return ResolvedModelBinding(
        selection_mode=selection_mode,
        model_id=model if selection_mode is ModelSelectionMode.PINNED else None,
        reasoning_effort=effort,
        source_layer=source_layer,
    )


class ModelConfigService:
    """Resolves one ``ResolvedModelBinding`` per (node_id, profile_id)."""

    def __init__(self, registry: PeerRegistryService | None) -> None:
        self._registry = registry

    def resolve(self, *, node_id: str, profile_id: str) -> ResolvedModelBinding:
        if self._registry is not None:
            binding = self._registry.get_profile_binding(node_id, profile_id)
            if binding is not None:
                return _binding_from_workspace_state(
                    binding.state, source_layer="workspace"
                )

        global_path = global_config_path()
        if global_path.is_file():
            with global_path.open("rb") as handle:
                data = tomllib.load(handle)
            entry = _read_profile_entry(data, profile_id)
            if entry is not None:
                return _binding_from_toml_entry(entry, source_layer="global")

        packaged = resources.files("peerhub.config_data").joinpath(
            "model-defaults.toml"
        )
        with packaged.open("rb") as handle:
            data = tomllib.load(handle)
        entry = _read_profile_entry(data, profile_id)
        if entry is None:
            # "*" is a wildcard for any profile the packaged defaults have
            # no specific opinion about (e.g. a custom/future adapter) --
            # cli_default only, never a model string, so there is nothing
            # to go stale. A known profile always matches its own entry
            # above first.
            entry = _read_profile_entry(data, "*")
        if entry is not None:
            return _binding_from_toml_entry(
                entry, source_layer="packaged_default"
            )

        raise ModelConfigError(
            "No model configuration resolved for profile "
            f"{profile_id!r} (node {node_id!r}) -- checked the workspace "
            f"binding, the global config ({global_path}), and the packaged "
            "default. Configure one, e.g.:\n"
            f"  peerhub node bind-profile --node-id {node_id} "
            f"--profile-id {profile_id} --model-id <model> --actor <you>\n"
            "or, to explicitly use the underlying CLI's own default model:\n"
            f"  peerhub node bind-profile --node-id {node_id} "
            f"--profile-id {profile_id} --selection-mode cli_default "
            "--actor <you>"
        )
