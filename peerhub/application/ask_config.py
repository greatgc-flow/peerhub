"""Layered configuration for the ``peerhub ask`` dispatch path.

Precedence (highest to lowest): global config
(``~/.peerhub/config/ask.toml``, overridable via ``PEERHUB_CONFIG_HOME``) >
packaged default (``peerhub/config_data/ask-defaults.toml``). This mirrors
``ModelConfigService``'s existing layering deliberately -- it is the same
config mechanism, applied to dispatch-shaping bounds instead of per-peer
model identity. There is no workspace-binding layer because none of these
values are per-peer facts.

Every bound the ask path applies is resolved through here rather than
written as a Python literal, so an operator can retune it without a code
change.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, cast

from peerhub.application import config_paths


class AskConfigError(RuntimeError):
    """Raised when an ask-config layer is malformed."""


def global_ask_config_path() -> Path:
    """Return the global ask-config file to consult.

    ``PEERHUB_CONFIG_HOME``, when set, REPLACES the default
    ``~/.peerhub/config`` directory outright, exactly as it does for
    ``models.toml`` -- never a silent fallback to ``~``, since that would
    defeat the CI/portable-install isolation it exists for. The returned
    path may or may not exist; "does not exist" simply means this layer is
    absent, not an error.
    """

    return config_paths.resolve_global_config_home().path / "ask.toml"


@dataclass(frozen=True)
class ContinuityConfig:
    """Bounds on cross-dispatch room/task checkpoint injection."""

    enabled: bool
    max_room_checkpoints: int
    max_task_checkpoints: int
    max_units_per_task: int
    max_chars: int
    include_unattributed_tasks: bool


@dataclass(frozen=True)
class PromptStagingConfig:
    """Where and whether an oversized prompt is staged to a file.

    ``location`` (item 3, dotdir consolidation, ratified 2026-09-09):
    ``"workspace"`` preserves the original behavior (staged under
    ``relative_dir`` inside the workspace root -- a durable dot-directory).
    ``"temp"`` stages under the OS temp root instead (via
    ``config_paths.resolve_workspace_temp()``), so staged prompt bytes
    never sit under a durable dot-directory at all.

    ``janitor_max_age_seconds``: a staged file older than this is presumed
    abandoned (its owning dispatch's outcome never resolved) and is
    reclaimed by the startup janitor sweep -- not deleted the moment a
    dispatch merely runs long.
    """

    enabled: bool
    relative_dir: str
    location: str = "workspace"
    janitor_max_age_seconds: float = 3600.0



@dataclass(frozen=True)
class TranscriptStorageConfig:
    """Durable dispatch transcript storage bounds."""

    enabled: bool

@dataclass(frozen=True)
class AskConfig:
    """Resolved configuration for one ask dispatch."""

    continuity: ContinuityConfig
    prompt_staging: PromptStagingConfig
    transcript_storage: TranscriptStorageConfig


def _table(data: Mapping[str, Any], name: str) -> dict[str, Any]:
    raw = data.get(name)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AskConfigError(f"ask config: [{name}] must be a table")
    return cast("dict[str, Any]", raw)


def _bool(table: Mapping[str, Any], key: str, section: str) -> bool:
    value = table.get(key)
    if type(value) is not bool:
        raise AskConfigError(
            f"ask config: [{section}] {key} must be a boolean, got {value!r}"
        )
    return value


def _text(table: Mapping[str, Any], key: str, section: str) -> str:
    value = table.get(key)
    if type(value) is not str or not value:
        raise AskConfigError(
            f"ask config: [{section}] {key} must be a nonempty string, "
            f"got {value!r}"
        )
    return value


_PROMPT_STAGING_LOCATIONS = frozenset({"workspace", "temp"})


def _prompt_staging_location(table: Mapping[str, Any], section: str) -> str:
    value = table.get("location", "workspace")
    if value not in _PROMPT_STAGING_LOCATIONS:
        raise AskConfigError(
            f"ask config: [{section}] location must be one of "
            f"{sorted(_PROMPT_STAGING_LOCATIONS)}, got {value!r}"
        )
    return cast(str, value)


def _janitor_max_age_seconds(table: Mapping[str, Any], section: str) -> float:
    value = table.get("janitor_max_age_seconds", 3600.0)
    if type(value) not in (int, float) or isinstance(value, bool) or value <= 0:
        raise AskConfigError(
            f"ask config: [{section}] janitor_max_age_seconds must be a "
            f"positive number, got {value!r}"
        )
    return float(value)


def _nonnegative_int(table: Mapping[str, Any], key: str, section: str) -> int:
    value = table.get(key)
    if type(value) is not int or value < 0:
        raise AskConfigError(
            f"ask config: [{section}] {key} must be a nonnegative integer, "
            f"got {value!r}"
        )
    return value


def _merge_layer(
    base: dict[str, dict[str, Any]],
    overlay: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Overlay per-key within each known section; unknown sections ignored."""

    merged = {section: dict(values) for section, values in base.items()}
    for section in merged:
        for key, value in _table(overlay, section).items():
            merged[section][key] = value
    return merged


_SECTIONS = ("continuity", "prompt_staging", "transcript_storage")


def load_ask_config() -> AskConfig:
    """Resolve the packaged defaults, overlaid by the global config layer."""

    packaged = resources.files("peerhub.config_data").joinpath(
        "ask-defaults.toml"
    )
    with packaged.open("rb") as handle:
        packaged_data = tomllib.load(handle)

    layers: dict[str, dict[str, Any]] = {
        section: _table(packaged_data, section) for section in _SECTIONS
    }

    global_path = global_ask_config_path()
    if global_path.is_file():
        with global_path.open("rb") as handle:
            layers = _merge_layer(layers, tomllib.load(handle))

    continuity = layers["continuity"]
    prompt_staging = layers["prompt_staging"]
    transcript_storage = layers["transcript_storage"]
    return AskConfig(
        continuity=ContinuityConfig(
            enabled=_bool(continuity, "enabled", "continuity"),
            max_room_checkpoints=_nonnegative_int(
                continuity, "max_room_checkpoints", "continuity"
            ),
            max_task_checkpoints=_nonnegative_int(
                continuity, "max_task_checkpoints", "continuity"
            ),
            max_units_per_task=_nonnegative_int(
                continuity, "max_units_per_task", "continuity"
            ),
            max_chars=_nonnegative_int(continuity, "max_chars", "continuity"),
            include_unattributed_tasks=_bool(
                continuity, "include_unattributed_tasks", "continuity"
            ),
        ),
        prompt_staging=PromptStagingConfig(
            enabled=_bool(prompt_staging, "enabled", "prompt_staging"),
            relative_dir=_text(
                prompt_staging, "relative_dir", "prompt_staging"
            ),
            location=_prompt_staging_location(prompt_staging, "prompt_staging"),
            janitor_max_age_seconds=_janitor_max_age_seconds(prompt_staging, "prompt_staging"),
        ),
        transcript_storage=TranscriptStorageConfig(
            enabled=_bool(transcript_storage, "enabled", "transcript_storage"),
        ),
    )
