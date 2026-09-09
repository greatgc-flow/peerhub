"""Generic layered-JSON-config loading and merging for PeerHub governance
files (item 9, dotdir consolidation, ratified 2026-09-09).

``arbiter.json`` and ``proposals.json`` each gain a global fallback layer
on top of the existing workspace tier: **workspace > global > built-in
safe default**. This module supplies the two mechanics both files share --
loading and validating one JSON layer, and merging several of them -- so
each domain module (``arbiter_review.py``, ``proposals.py``) only has to
declare its own allowed keys and per-field type checks.

Merge semantics: a list-valued key is replaced whole by the highest layer
that defines it (never merged element-wise); a key named in
``object_keys`` (a nested object, e.g. arbiter.json's ``candidate``) is
merged field-by-field instead, so a workspace layer can override just
``candidate.profile_id`` while inheriting ``candidate.peer_name`` from the
global layer. Every other top-level key is replaced whole by the highest
layer that defines it -- the built-in default is never represented as an
explicit layer here; callers apply their own per-field defaults (matching
the ratified "enabled=false, empty electorate" safe default) when reading
the merged mapping, exactly as they did before this layer existed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

_SCHEMA_VERSION_KEY = "schema_version"
_SUPPORTED_SCHEMA_VERSION = 1


class LayeredConfigError(ValueError):
    """Raised for a malformed governance-config layer."""


def load_json_layer(
    path: Path | None,
    *,
    label: str,
    layer: str,
    allowed_keys: frozenset[str],
) -> Mapping[str, Any] | None:
    """Load and validate one governance-config layer.

    Returns ``None`` when ``path`` is ``None`` or does not exist -- "this
    layer has no value" (§3.1's absent-layer rule), never a fallback to a
    different layer. Raises ``LayeredConfigError`` for a present-but-
    malformed layer (not a JSON object, missing/wrong ``schema_version``,
    or an unknown top-level key) -- a *selected* malformed layer is a hard
    error, never a reason to quietly use a lower layer (§3.1's
    malformed-layer rule).
    """

    if path is None or not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as stream:
        raw_object: object = json.load(stream)
    if not isinstance(raw_object, Mapping):
        raise LayeredConfigError(f"{label} ({layer} layer) must contain a JSON object")
    raw_mapping = cast("Mapping[str, Any]", raw_object)
    version = raw_mapping.get(_SCHEMA_VERSION_KEY)
    if version != _SUPPORTED_SCHEMA_VERSION:
        raise LayeredConfigError(
            f"{label} ({layer} layer) must declare "
            f"{_SCHEMA_VERSION_KEY}={_SUPPORTED_SCHEMA_VERSION}, got {version!r}"
        )
    unknown = set(raw_mapping.keys()) - allowed_keys - {_SCHEMA_VERSION_KEY}
    if unknown:
        raise LayeredConfigError(
            f"{label} ({layer} layer) has unknown key(s): {sorted(unknown)}"
        )
    return raw_mapping


def merge_layers(
    layers: Sequence[tuple[str, Mapping[str, Any] | None]],
    *,
    object_keys: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], dict[str, str]]:
    """Merge layers ordered lowest-to-highest precedence.

    Returns ``(merged, winning_layer)``: ``merged`` is the combined
    mapping (``schema_version`` excluded -- it is per-layer bookkeeping,
    not a governed field); ``winning_layer`` maps each top-level key (and,
    for an ``object_keys`` entry, ``"key.subkey"``) to the name of the
    layer that supplied its final value -- the diagnostic the ratified
    spec requires.
    """

    merged: dict[str, Any] = {}
    winning_layer: dict[str, str] = {}
    for layer_name, data in layers:
        if data is None:
            continue
        for key, value in data.items():
            if key == _SCHEMA_VERSION_KEY:
                continue
            if key in object_keys and isinstance(value, Mapping):
                sub_value = cast("Mapping[str, Any]", value)
                sub = dict(merged.get(key, {}))
                for subkey, subvalue in sub_value.items():
                    sub[subkey] = subvalue
                    winning_layer[f"{key}.{subkey}"] = layer_name
                merged[key] = sub
                winning_layer[key] = layer_name
            else:
                merged[key] = value
                winning_layer[key] = layer_name
    return merged, winning_layer
