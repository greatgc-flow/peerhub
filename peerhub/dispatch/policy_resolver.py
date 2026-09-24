import os
import time
import tomllib
from pathlib import Path
from typing import Dict, Any, Optional

from peerhub.core.errors import ConfigurationError
from peerhub.dispatch.policy import (
    ConsultationDepth,
    ConsensusPolicy,
    SessionPolicy,
    RoutingPolicy,
    TransportPolicy,
    DispatchPolicy,
)
from peerhub.health.contract import HealthConsequencePolicy, DefaultConsequence, RecoveryAuthority
from peerhub.dispatch.policy import TelemetryPolicy


def _load_toml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return {}

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ConfigurationError(f"Config file {path} must be UTF-8 encoded.") from e

    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigurationError(f"Invalid TOML in {path}: {e}") from e

def _merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _merge_dicts(merged[k], v)
        else:
            merged[k] = v
    return merged

class PolicyResolver:
    def __init__(self, workspace_config: Path, global_config: Path):
        self.workspace_config = workspace_config
        self.global_config = global_config

        # Package default config path
        # Assume it's located at peerhub/config_data/dispatch-defaults.toml
        # We can construct the path relative to this file
        current_dir = Path(__file__).parent
        self.default_config_path = current_dir.parent / "config_data" / "dispatch-defaults.toml"
        
        self.default_config = _load_toml(self.default_config_path)

    def resolve(
        self,
        action_name: str,
        *,
        cli_overrides: Optional[Dict[str, Any]] = None,
        per_command_minimums: Optional[Dict[str, ConsultationDepth]] = None
    ) -> DispatchPolicy:
        # Load configs
        global_cfg = _load_toml(self.global_config)
        workspace_cfg = _load_toml(self.workspace_config)

        # Merge in order: default -> global -> workspace -> CLI
        merged = _merge_dicts(self.default_config, global_cfg)
        merged = _merge_dicts(merged, workspace_cfg)
        if cli_overrides:
            merged = _merge_dicts(merged, cli_overrides)

        # Build individual policies with validation
        consultation = merged.get("consultation", {})
        consensus = merged.get("consensus", {})
        session = merged.get("session", {})
        routing = merged.get("routing", {})
        transport = merged.get("transport", {})
        health = merged.get("health_consequence", {})
        telemetry = merged.get("telemetry", {})

        # Validation helper
        def _check_keys(section_dict: Dict[str, Any], expected_keys: set, section_name: str):
            for k in section_dict.keys():
                if k not in expected_keys:
                    raise ConfigurationError(f"Unknown key {k!r} in [{section_name}]")

        def _get(d: dict, k: str, exp_type: type, section_name: str, allow_none: bool = False):
            if k not in d:
                if allow_none:
                    return None
                raise ConfigurationError(f"Missing key {k!r} in [{section_name}]")
            val = d[k]
            if not isinstance(val, exp_type):
                raise ConfigurationError(f"Wrong type for {k!r} in [{section_name}], expected {exp_type.__name__}")
            return val

        # Validate consultation
        _check_keys(consultation, {"default_depth", "overrides"}, "consultation")
        depth_str = consultation.get("default_depth", "quorum")
        overrides = consultation.get("overrides", {})
        if not isinstance(overrides, dict):
            raise ConfigurationError("consultation.overrides must be a dict")
        
        if action_name in overrides:
            depth_str = overrides[action_name]
        
        try:
            depth = ConsultationDepth(depth_str)
        except ValueError:
            raise ConfigurationError(f"Invalid consultation depth: {depth_str}")
        
        if per_command_minimums and action_name in per_command_minimums:
            min_depth = per_command_minimums[action_name]
            # Hierarchy: NONE < NOTIFY < REVIEW < QUORUM < UNANIMOUS
            hierarchy = [ConsultationDepth.NONE, ConsultationDepth.NOTIFY, ConsultationDepth.REVIEW, ConsultationDepth.QUORUM, ConsultationDepth.UNANIMOUS]
            if hierarchy.index(depth) < hierarchy.index(min_depth):
                depth = min_depth

        # Validate consensus
        _check_keys(consensus, {"quorum_formula", "final_call_rule", "timeout_seconds", "escalation_target"}, "consensus")
        qf = _get(consensus, "quorum_formula", str, "consensus")
        if qf not in {"majority", "supermajority", "unanimous", "all_required"}:
            raise ConfigurationError(f"invalid formula: {qf}")
        fcr = _get(consensus, "final_call_rule", str, "consensus")
        if fcr not in {"always", "tier_0_or_high_risk_or_dissent", "never"}:
            raise ConfigurationError(f"invalid rule: {fcr}")

        cons_policy = ConsensusPolicy(
            quorum_formula=qf,
            final_call_rule=fcr,
            timeout_seconds=_get(consensus, "timeout_seconds", int, "consensus"),
            escalation_target=_get(consensus, "escalation_target", str, "consensus")
        )

        # Validate session
        _check_keys(session, {"default_mode", "soft_pressure_threshold", "hard_pressure_threshold", "max_observation_age_ms"}, "session")
        soft = _get(session, "soft_pressure_threshold", int, "session")
        hard = _get(session, "hard_pressure_threshold", int, "session")
        if soft > hard:
            raise ConfigurationError("soft must be < hard")
        if soft == hard:
            raise ConfigurationError("soft must be strictly < hard")

        sess_policy = SessionPolicy(
            default_mode=_get(session, "default_mode", str, "session"),
            soft_pressure_threshold=soft,
            hard_pressure_threshold=hard,
            max_observation_age_ms=_get(session, "max_observation_age_ms", int, "session")
        )

        # Validate routing
        _check_keys(routing, {"effort_routing", "preference_map"}, "routing")
        rout_policy = RoutingPolicy(
            effort_routing=_get(routing, "effort_routing", str, "routing"),
            preference_map=_get(routing, "preference_map", dict, "routing")
        )

        # Validate transport
        _check_keys(transport, {"max_inline_bytes", "staging_dir", "cleanup_rule", "retention_mode", "retained_input_ttl_days"}, "transport")
        max_bytes = _get(transport, "max_inline_bytes", int, "transport")
        if max_bytes <= 0:
            raise ConfigurationError("max_inline_bytes must be > 0")
        
        staging_dir = _get(transport, "staging_dir", str, "transport")
        if ".." in staging_dir:
            raise ConfigurationError("Path traversal in staging_dir")
        if Path(staging_dir).is_absolute() or staging_dir.startswith("/"):
            raise ConfigurationError("Absolute path in staging_dir")

        trans_policy = TransportPolicy(
            max_inline_bytes=max_bytes,
            staging_dir=staging_dir,
            cleanup_rule=_get(transport, "cleanup_rule", str, "transport"),
            retention_mode=_get(transport, "retention_mode", str, "transport"),
            retained_input_ttl_days=_get(transport, "retained_input_ttl_days", int, "transport")
        )

        # Validate health consequence
        _check_keys(health, {"default_consequence", "recovery_authority"}, "health_consequence")
        try:
            dc = DefaultConsequence(_get(health, "default_consequence", str, "health_consequence"))
        except ValueError:
            raise ConfigurationError("Invalid default_consequence")
        
        try:
            ra = RecoveryAuthority(_get(health, "recovery_authority", str, "health_consequence"))
        except ValueError:
            raise ConfigurationError("Invalid recovery_authority")

        health_policy = HealthConsequencePolicy(
            default_consequence=dc,
            recovery_authority=ra
        )

        # Validate telemetry
        _check_keys(telemetry, {"refresh_before_dispatch", "headroom_surface"}, "telemetry")
        telem_policy = TelemetryPolicy(
            refresh_before_dispatch=_get(telemetry, "refresh_before_dispatch", bool, "telemetry"),
            headroom_surface=_get(telemetry, "headroom_surface", str, "telemetry")
        )

        return DispatchPolicy(
            consultation_depth=depth,
            consensus=cons_policy,
            session=sess_policy,
            routing=rout_policy,
            transport=trans_policy,
            health_consequence=health_policy,
            telemetry=telem_policy,
            resolved_from="resolver",
            resolved_at=int(time.time() * 1000),
            source_hash="000"
        )
