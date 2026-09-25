import tomllib
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

def freeze_policy_snapshot(
    config: dict[str, Any],
    risk: str,
    action: str,
    origin: str,
    *,
    tier0: bool = False,
    unresolved_dissent: bool = False,
) -> PolicySnapshot:
    """Evaluates configuration once and freezes it into an authoritative snapshot.

    The mandatory Final Call obligation (tier-0 OR high-risk OR unresolved
    dissent) is computed here and stored, so later concern obligations update
    the snapshot without re-resolving external configuration.
    """
    return PolicySnapshot(
        version=1,
        formula=config["formula"],
        required_votes=config["required_votes"],
        final_call_rule=config["final_call_rule"],
        deadlines=config["deadlines"],
        escalation_paths=config["escalation_paths"],
        risk=risk,
        action=action,
        origin=origin,
        mandatory_final_call=mandatory_final_call(tier0, risk == "high", unresolved_dissent),
    )

def decode_policy_snapshot(raw: dict[str, Any]) -> PolicySnapshot:
    """Decodes a serialized snapshot, failing closed on missing versions or inconsistencies."""
    try:
        version = raw["version"]
        if version != 1:
            raise ConfigurationError(f"Unsupported snapshot version: {version}")
        
        required_votes = raw["required_votes"]
        if (
            not isinstance(required_votes, int)
            or isinstance(required_votes, bool)
            or required_votes < 1
        ):
            raise ConfigurationError(f"Inconsistent snapshot: required_votes={required_votes!r}")
        if not isinstance(raw["mandatory_final_call"], bool):
            raise ConfigurationError("Inconsistent snapshot: mandatory_final_call must be bool")
        if not raw["action"] or not raw["origin"]:
            raise ConfigurationError("Inconsistent snapshot: empty action/origin")
        return PolicySnapshot(
            version=version,
            formula=raw["formula"],
            required_votes=required_votes,
            final_call_rule=raw["final_call_rule"],
            deadlines=raw["deadlines"],
            escalation_paths=raw["escalation_paths"],
            risk=raw["risk"],
            action=raw["action"],
            origin=raw["origin"],
            mandatory_final_call=raw["mandatory_final_call"]
        )
    except KeyError as e:
        raise ConfigurationError(f"Missing required field in snapshot: {e}")

def effective_depth(configured_depth: str, risk: str, risk_floors: dict[str, str]) -> str:
    """Calculates effective depth as max(configured, risk_floor)."""
    if not risk or risk not in risk_floors:
        floor = "QUORUM"
    else:
        floor = risk_floors[risk]
        
    order = ["NONE", "ADVISORY", "NOTIFY", "REVIEW", "QUORUM", "SUPERMAJORITY", "UNANIMOUS"]
    
    def get_rank(depth: str) -> int:
        d = depth.upper()
        return order.index(d) if d in order else -1
        
    conf_rank = get_rank(configured_depth)
    floor_rank = get_rank(floor)
    
    if conf_rank >= floor_rank and conf_rank != -1:
        return configured_depth
    return floor

def mandatory_final_call(tier0: bool, high_risk: bool, unresolved_dissent: bool) -> bool:
    """Evaluates whether a mandatory final call is required."""
    return tier0 or high_risk or unresolved_dissent

def select_policy(action: str, overrides: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Selects policy considering proposal overrides, action keys, and defaults."""
    if "proposals.create" in overrides and action == "proposals.create":
        return overrides["proposals.create"]
    if action in overrides:
        return overrides[action]
    return defaults

def load_config(path: str) -> dict[str, Any]:
    """Loads configuration from TOML, failing closed on OSError."""
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except OSError as e:
        raise ConfigurationError(f"Failed to read config: {e}")
    except tomllib.TOMLDecodeError as e:
        raise ConfigurationError(f"Failed to parse TOML: {e}")
