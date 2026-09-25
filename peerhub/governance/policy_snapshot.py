from dataclasses import dataclass
from typing import Any, Mapping

class ConfigurationError(Exception):
    """Raised when policy configuration is invalid, unreadable, or inconsistent."""
    pass

@dataclass(frozen=True)
class PolicySnapshot:
    version: int
    formula: str
    required_votes: int
    final_call_rule: str
    deadlines: dict[str, int]
    escalation_paths: list[str]
    risk: str
    action: str
    origin: str
    mandatory_final_call: bool

def freeze_policy_snapshot(config: dict[str, Any], risk: str, action: str, origin: str) -> PolicySnapshot:
    """Evaluates configuration and freezes it into an authoritative snapshot."""
    raise NotImplementedError("RED phase")

def decode_policy_snapshot(raw: dict[str, Any]) -> PolicySnapshot:
    """Decodes a serialized snapshot, failing closed on missing versions or inconsistencies."""
    raise NotImplementedError("RED phase")

def effective_depth(configured_depth: str, risk: str, risk_floors: dict[str, str]) -> str:
    """Calculates effective depth as max(configured, risk_floor)."""
    raise NotImplementedError("RED phase")

def mandatory_final_call(tier0: bool, high_risk: bool, unresolved_dissent: bool) -> bool:
    """Evaluates whether a mandatory final call is required."""
    raise NotImplementedError("RED phase")

def select_policy(action: str, overrides: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Selects policy considering proposal overrides, action keys, and defaults."""
    raise NotImplementedError("RED phase")

def load_config(path: str) -> dict[str, Any]:
    """Loads configuration from TOML, failing closed on OSError."""
    raise NotImplementedError("RED phase")
