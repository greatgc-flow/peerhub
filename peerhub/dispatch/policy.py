from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
from peerhub.health.contract import HealthConsequencePolicy, DefaultConsequence, RecoveryAuthority

class ConsultationDepth(str, Enum):
    NONE = "none"
    NOTIFY = "notify"
    REVIEW = "review"
    QUORUM = "quorum"
    UNANIMOUS = "unanimous"

@dataclass(frozen=True)
class ConsensusPolicy:
    quorum_formula: str
    final_call_rule: str
    timeout_seconds: int
    escalation_target: str

@dataclass(frozen=True)
class SessionPolicy:
    default_mode: str
    soft_pressure_threshold: int
    hard_pressure_threshold: int
    max_observation_age_ms: int

@dataclass(frozen=True)
class RoutingPolicy:
    effort_routing: str
    preference_map: Dict[str, List[str]]

@dataclass(frozen=True)
class TransportPolicy:
    max_inline_bytes: int
    staging_dir: str
    cleanup_rule: str
    retention_mode: str
    retained_input_ttl_days: int

@dataclass(frozen=True)
class TelemetryPolicy:
    refresh_before_dispatch: bool
    headroom_surface: str

@dataclass(frozen=True)
class DispatchPolicy:
    """Resolved once, immutable for the action's lifetime. All nested values are immutable."""
    
    # --- Consultation axis ---
    consultation_depth: ConsultationDepth
    
    # --- Consensus sub-policy ---
    consensus: ConsensusPolicy
    
    # --- Session management ---
    session: SessionPolicy
    
    # --- Profile/routing ---
    routing: RoutingPolicy
    
    # --- Transport/IPC ---
    transport: TransportPolicy
    
    # --- Health consequences ---
    health_consequence: HealthConsequencePolicy
    
    # --- Telemetry ---
    telemetry: TelemetryPolicy
    
    # --- Provenance ---
    resolved_from: str
    resolved_at: int
    source_hash: str
