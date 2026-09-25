"""Provenance and V2 schema adapter for governance records."""

from typing import Any, Optional, Tuple, Set
from dataclasses import dataclass

from peerhub.governance.policy_snapshot import ConfigurationError
from peerhub.core.errors import SchemaError

@dataclass(frozen=True)
class UpcastResult:
    status: str
    record: dict[str, Any]

def resolve_provenance(explicit_origin: Optional[str], explicit_action: Optional[str]) -> Tuple[str, str]:
    """Resolve the true origin and action, filling direct path defaults."""
    raise NotImplementedError("RED phase")

def action_name(origin: str, action: str) -> str:
    """Builds the policy resolver key deterministically."""
    raise NotImplementedError("RED phase")

def upcast_v1_round(record: dict[str, Any], evidence_map: dict[str, Any]) -> UpcastResult:
    """Upcast a V1 record to V2, using evidence map for disambiguation."""
    raise NotImplementedError("RED phase")

def validate_required_votes(value: Any, pool_size: int) -> None:
    """Validate fixed-count rules against a pool size."""
    raise NotImplementedError("RED phase")

def count_fixed_agrees(round_kind: str, agreeing_voters: Set[str], eligible: Set[str], required_set: Set[str]) -> int:
    """Count agreeing votes based on schema version rules."""
    raise NotImplementedError("RED phase")
