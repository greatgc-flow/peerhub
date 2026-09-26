"""Provenance and V2 schema adapter for governance records."""

from collections.abc import Mapping
import re
from typing import Any, cast, Optional, Tuple, Set
from dataclasses import dataclass

from peerhub.governance.policy_snapshot import ConfigurationError
from peerhub.core.errors import SchemaError

@dataclass(frozen=True)
class UpcastResult:
    status: str
    record: dict[str, Any]

def resolve_provenance(explicit_origin: Optional[str], explicit_action: Optional[str]) -> Tuple[str, str]:
    """Resolve the true origin and action, filling direct path defaults."""
    if explicit_origin is None and explicit_action is None:
        return "direct", "consensus.round.propose"
    if explicit_origin is None or explicit_action is None:
        raise ValueError("Must provide both origin and action or neither")
    return explicit_origin, explicit_action

def action_name(origin: str, action: str) -> str:
    """Builds the policy resolver key deterministically."""
    return f"{origin}:{action}"

_V1_SCHEMA_SUFFIX = re.compile(r"\.v1$")
_SCHEMA_VERSION = re.compile(r"\.v(\d+)$")


def _schema_version(record: dict[str, Any]) -> Any:
    """Numeric ``schema_version`` if present, else the ``.vN`` suffix of ``schema``.

    Real persisted consensus rounds carry a schema string such as
    ``peerhub.consensus-round.v1``; synthetic records may carry a number.
    """
    if "schema_version" in record:
        return record["schema_version"]
    schema = record.get("schema")
    if isinstance(schema, str):
        match = _SCHEMA_VERSION.search(schema)
        if match:
            return int(match.group(1))
    return None


def _thaw(value: Any) -> Any:
    """Deep, mutable copy of persisted state (broker state holds frozen mappings)."""
    if isinstance(value, Mapping):
        mapping = cast(Mapping[Any, Any], value)
        return {key: _thaw(item) for key, item in mapping.items()}
    if isinstance(value, tuple):
        return tuple(_thaw(item) for item in cast(tuple[Any, ...], value))
    if isinstance(value, list):
        return [_thaw(item) for item in cast(list[Any], value)]
    return value


def upcast_v1_round(record: dict[str, Any], evidence_map: dict[str, Any]) -> UpcastResult:
    """Upcast a V1 record to V2 in memory (pure; input is never mutated).

    Origin is never guessed: only an independent per-round mapping in
    ``evidence_map`` resolves it. Without one the record is returned
    unchanged (still V1) with status ``ambiguity_hold``.
    """
    schema_version = _schema_version(record)
    if schema_version not in (1, 2):
        raise SchemaError(f"Unknown schema_version: {schema_version}")

    new_record = _thaw(record)
    if schema_version == 2:
        if not new_record.get("origin") or not new_record.get("action"):
            raise SchemaError("Inconsistent V2 record: missing origin/action")
        return UpcastResult(status="ok", record=new_record)

    evidence = evidence_map.get(new_record.get("round_id"))
    if not evidence or not evidence.get("origin") or not evidence.get("action"):
        return UpcastResult(status="ambiguity_hold", record=new_record)

    if "schema_version" in new_record:
        new_record["schema_version"] = 2
    if isinstance(new_record.get("schema"), str):
        new_record["schema"] = _V1_SCHEMA_SUFFIX.sub(".v2", new_record["schema"])
    new_record["origin"] = evidence["origin"]
    new_record["action"] = evidence["action"]
    return UpcastResult(status="upcasted", record=new_record)


def validate_required_votes(value: Any, pool_size: int) -> None:
    """Validate fixed-count rules against a pool size."""
    if type(value) is bool:
        raise ConfigurationError("value cannot be bool")
    if not isinstance(value, int):
        raise ConfigurationError("value must be an int")
    if value < 2:
        raise ConfigurationError("value must be at least 2")
    if value > pool_size:
        raise ConfigurationError("value cannot exceed pool size")

def count_fixed_agrees(round_kind: str, agreeing_voters: Set[str], eligible: Set[str], required_set: Set[str]) -> int:
    """Count agreeing votes based on schema version rules."""
    if round_kind == "v2":
        return len(agreeing_voters.intersection(eligible))
    elif round_kind == "v1_upcast":
        return len(agreeing_voters.intersection(required_set))
    else:
        raise ValueError(f"Unknown round_kind: {round_kind}")
