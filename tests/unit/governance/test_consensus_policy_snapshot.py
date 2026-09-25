"""TDD RED-state spec for consensus policy snapshot (B4)."""

import pytest
from peerhub.governance.policy_snapshot import (
    PolicySnapshot,
    ConfigurationError,
    freeze_policy_snapshot,
    decode_policy_snapshot,
    effective_depth,
    mandatory_final_call,
    select_policy,
    load_config,
)

# --- 4.1 Policy Freezing and Saved Snapshot Identity ---

def test_snapshot_fields_and_versioning():
    snapshot = decode_policy_snapshot({
        "version": 1,
        "formula": "majority",
        "required_votes": 3,
        "final_call_rule": "never",
        "deadlines": {"vote": 86400},
        "escalation_paths": ["escalate"],
        "risk": "low",
        "action": "update",
        "origin": "system",
        "mandatory_final_call": False,
    })
    assert snapshot.action == "update"
    assert snapshot.origin == "system"
    assert snapshot.version == 1

def test_decode_fail_closed_missing_version():
    with pytest.raises(ConfigurationError):
        decode_policy_snapshot({"formula": "majority"})

def test_decode_fail_closed_inconsistent_snapshot():
    with pytest.raises(ConfigurationError):
        decode_policy_snapshot({"version": 1, "formula": "majority"})

def test_load_config_unreadable_toml_fails_closed(monkeypatch):
    def mock_open(*args, **kwargs):
        raise OSError("Permission denied")
    monkeypatch.setattr("builtins.open", mock_open)
    with pytest.raises(ConfigurationError):
        load_config("dummy/path.toml")

def test_freeze_policy_snapshot_valid():
    config = {
        "formula": "majority",
        "required_votes": 3,
        "final_call_rule": "never",
        "deadlines": {"vote": 86400},
        "escalation_paths": [],
    }
    snapshot = freeze_policy_snapshot(config, "low", "update", "system")
    assert snapshot.version == 1
    assert snapshot.action == "update"

# --- 4.2 Exact Risk-to-Depth Mapping and Mandatory Final Call ---

def test_effective_depth_max_configured_and_floor():
    risk_floors = {"high": "QUORUM", "critical": "SUPERMAJORITY"}
    assert effective_depth("NONE", "high", risk_floors) == "QUORUM"
    assert effective_depth("SUPERMAJORITY", "high", risk_floors) == "SUPERMAJORITY"

def test_effective_depth_unknown_risk_defaults_to_quorum():
    risk_floors = {"high": "QUORUM"}
    assert effective_depth("NONE", "unknown_risk", risk_floors) == "QUORUM"

def test_effective_depth_missing_risk_defaults_to_quorum():
    risk_floors = {"high": "QUORUM"}
    assert effective_depth("NONE", "", risk_floors) == "QUORUM"
    
def test_mandatory_final_call_triggers():
    assert mandatory_final_call(tier0=True, high_risk=False, unresolved_dissent=False) is True
    assert mandatory_final_call(tier0=False, high_risk=True, unresolved_dissent=False) is True
    assert mandatory_final_call(tier0=False, high_risk=False, unresolved_dissent=True) is True
    assert mandatory_final_call(tier0=False, high_risk=False, unresolved_dissent=False) is False

def test_mandatory_final_call_retained_durably():
    snapshot = decode_policy_snapshot({
        "version": 1,
        "formula": "majority",
        "required_votes": 3,
        "final_call_rule": "never",
        "deadlines": {},
        "escalation_paths": [],
        "risk": "low",
        "action": "update",
        "origin": "system",
        "mandatory_final_call": True,
    })
    assert snapshot.mandatory_final_call is True

# --- 4.3 Per-Action Selection ---

def test_select_policy_precedence_proposal():
    overrides = {
        "proposals.create": {"formula": "unanimous"},
        "update_repo": {"formula": "majority"},
    }
    defaults = {"formula": "simple"}
    policy = select_policy("proposals.create", overrides, defaults)
    assert policy["formula"] == "unanimous"

def test_select_policy_precedence_action_key():
    overrides = {
        "proposals.create": {"formula": "unanimous"},
        "update_repo": {"formula": "supermajority"},
    }
    defaults = {"formula": "simple"}
    policy = select_policy("update_repo", overrides, defaults)
    assert policy["formula"] == "supermajority"

def test_select_policy_precedence_global_default():
    overrides = {
        "proposals.create": {"formula": "unanimous"},
        "update_repo": {"formula": "supermajority"},
    }
    defaults = {"formula": "simple"}
    policy = select_policy("unknown_action", overrides, defaults)
    assert policy["formula"] == "simple"

def test_select_policy_proposal_strict_behavior():
    overrides = {
        "proposals.create": {"formula": "unanimous", "reject_on_dissent": True},
    }
    defaults = {"formula": "majority", "reject_on_dissent": False}
    
    policy = select_policy("proposals.create", overrides, defaults)
    assert policy["formula"] == "unanimous"
    assert policy["reject_on_dissent"] is True
    
    policy_default = select_policy("other_action", overrides, defaults)
    assert policy_default["formula"] == "majority"
    assert policy_default["reject_on_dissent"] is False
