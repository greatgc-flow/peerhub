"""Tests for B7a TDD RED."""

import pytest
from peerhub.governance.provenance import (
    resolve_provenance,
    action_name,
    upcast_v1_round,
    validate_required_votes,
    count_fixed_agrees,
    UpcastResult
)
from peerhub.governance.policy_snapshot import ConfigurationError
from peerhub.core.errors import SchemaError

# 1. resolve_provenance tests
def test_resolve_provenance_direct_path():
    origin, action = resolve_provenance(None, None)
    assert origin == "direct"
    assert action == "consensus.round.propose"

def test_resolve_provenance_explicit_path_preserved():
    origin, action = resolve_provenance("proposals", "governance.proposal.create")
    assert origin == "proposals"
    assert action == "governance.proposal.create"

def test_resolve_provenance_inconsistent_origin_only():
    with pytest.raises(ValueError):
        resolve_provenance("proposals", None)

def test_resolve_provenance_inconsistent_action_only():
    with pytest.raises(ValueError):
        resolve_provenance(None, "governance.proposal.create")

# 1b. action_name tests
def test_action_name_determinism():
    res = action_name("direct", "consensus.round.propose")
    assert isinstance(res, str)
    assert len(res) > 0

# 2. upcast_v1_round tests
def test_upcast_v1_round_unknown_schema():
    with pytest.raises(SchemaError):
        upcast_v1_round({"schema_version": 999}, {})

def test_upcast_v1_round_ambiguity_hold():
    # V1 record (no origin field, shared operation key) NEVER guesses origin.
    # evidence_map has NO independent per-round mapping.
    record = {"round_id": "r1", "schema_version": 1, "operation": "shared"}
    res = upcast_v1_round(record, {})
    assert isinstance(res, UpcastResult)
    assert res.status == "ambiguity_hold"

def test_upcast_v1_round_resolved_from_evidence():
    record = {"round_id": "r1", "schema_version": 1, "operation": "shared", "revision": 4, "receipt": "x", "approvers": ["a"]}
    evidence_map = {"r1": {"origin": "proposals", "action": "create"}}
    res = upcast_v1_round(record, evidence_map)
    assert isinstance(res, UpcastResult)
    assert res.status != "ambiguity_hold"
    assert res.record["origin"] == "proposals"
    assert res.record["action"] == "create"
    assert res.record["revision"] == 4 # Preserved
    assert res.record["receipt"] == "x"
    assert res.record["approvers"] == ["a"]
    # pure, doesn't mutate input
    assert "origin" not in record

def test_upcast_v1_round_already_v2():
    record = {"round_id": "r1", "schema_version": 2, "origin": "a", "action": "b"}
    res = upcast_v1_round(record, {})
    assert res.record["schema_version"] == 2

# 3. validate_required_votes tests
def test_validate_required_votes_rejects_non_int():
    for bad_val in [2.5, "3", [2]]:
        with pytest.raises(ConfigurationError):
            validate_required_votes(bad_val, 5)

def test_validate_required_votes_rejects_bool():
    with pytest.raises(ConfigurationError):
        validate_required_votes(True, 5)

def test_validate_required_votes_rejects_zero_or_negative():
    for bad_val in [0, -1, -5]:
        with pytest.raises(ConfigurationError):
            validate_required_votes(bad_val, 5)

def test_validate_required_votes_enforces_floor_2():
    with pytest.raises(ConfigurationError):
        validate_required_votes(1, 5)

def test_validate_required_votes_rejects_unattainable():
    with pytest.raises(ConfigurationError):
        validate_required_votes(6, 5) # 6 required > 5 pool

def test_validate_required_votes_accepts_valid():
    validate_required_votes(3, 5)
    validate_required_votes(2, 2)
    validate_required_votes(5, 5)

# 4. count_fixed_agrees tests
def test_count_fixed_agrees_v2():
    agreeing = {"u1", "u2", "u3", "u_out"}
    eligible = {"u1", "u2", "u3", "u4"}
    req_set = {"u1"}
    count = count_fixed_agrees("v2", agreeing, eligible, req_set)
    # v2: count agreeing voters that are in eligible -> u1, u2, u3 (3)
    assert count == 3

def test_count_fixed_agrees_v1_upcast():
    agreeing = {"u1", "u2", "u3", "u_out"}
    eligible = {"u1", "u2", "u3", "u4"}
    req_set = {"u1", "u2"}
    count = count_fixed_agrees("v1_upcast", agreeing, eligible, req_set)
    # v1_upcast: count ONLY voters in required_set -> u1, u2 (2)
    assert count == 2

def test_count_fixed_agrees_v1_upcast_eligible_not_substitute():
    agreeing = {"u1", "u3"}
    eligible = {"u1", "u2", "u3"}
    req_set = {"u1", "u2"}
    count = count_fixed_agrees("v1_upcast", agreeing, eligible, req_set)
    # u3 is eligible but not required, so it shouldn't count for v1
    assert count == 1

def test_gap_upcast_does_not_mutate_input_record():
    original = {"round_id": "r1", "schema_version": 1, "operation": "shared"}
    evidence = {"r1": {"origin": "a", "action": "b"}}
    res = upcast_v1_round(original, evidence)
    assert original == {"round_id": "r1", "schema_version": 1, "operation": "shared"}
    assert res.record != original

def test_gap_original_fields_carried_unchanged():
    original = {
        "round_id": "r1", "schema_version": 1, "operation": "shared",
        "revision": "rev1", "hashes": {"h1": "h2"}, "receipts": ["rcpt1"], "approval": "ident1"
    }
    evidence = {"r1": {"origin": "a", "action": "b"}}
    res = upcast_v1_round(original, evidence)
    assert res.record["revision"] == "rev1"
    assert res.record["hashes"] == {"h1": "h2"}
    assert res.record["receipts"] == ["rcpt1"]
    assert res.record["approval"] == "ident1"

def test_gap_v2_counting_ignores_not_eligible():
    count = count_fixed_agrees("v2", {"a", "b", "c"}, {"a", "b"}, {"a"})
    assert count == 2

def test_gap_validate_required_votes_rejects_none_and_others():
    for bad in [None, 3.14, "4"]:
        with pytest.raises(ConfigurationError):
            validate_required_votes(bad, 5)

def test_gap_evidence_different_round_id_does_not_resolve():
    record = {"round_id": "r1", "schema_version": 1, "operation": "shared"}
    evidence_map = {"r2": {"origin": "a", "action": "b"}}
    res = upcast_v1_round(record, evidence_map)
    assert res.status == "ambiguity_hold"
    
def test_gap_unknown_schema_version_values_raise():
    for bad_ver in [0, 3, "x", None]:
        with pytest.raises(SchemaError):
            upcast_v1_round({"schema_version": bad_ver}, {})


def test_gap_ambiguity_hold_stays_v1_and_keeps_operation():
    from peerhub.governance.provenance import upcast_v1_round
    rec = {"schema_version": 1, "round_id": "r9", "operation": "consensus.propose"}
    res = upcast_v1_round(rec, {})
    assert res.status == "ambiguity_hold"
    assert res.record["schema_version"] == 1
    assert res.record["operation"] == "consensus.propose"
    assert "origin" not in res.record


def test_gap_v2_record_without_origin_fails_closed():
    from peerhub.core.errors import SchemaError
    from peerhub.governance.provenance import upcast_v1_round
    with pytest.raises(SchemaError):
        upcast_v1_round({"schema_version": 2, "round_id": "r1"}, {})


def test_gap_upcast_does_not_mutate_nested_input():
    from peerhub.governance.provenance import upcast_v1_round
    rec = {"schema_version": 1, "round_id": "r1", "participants": {"a": 1}}
    res = upcast_v1_round(rec, {"r1": {"origin": "proposals", "action": "governance.proposal.create"}})
    res.record["participants"]["a"] = 2
    assert rec["participants"]["a"] == 1 and rec["schema_version"] == 1
