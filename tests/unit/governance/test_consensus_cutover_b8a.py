"""B8a specs: phase-to-disposition table, frozen legacy contract, activation classification."""
import pytest

from peerhub.core.errors import SchemaError
from peerhub.governance.cutover import (
    FrozenLegacyEvaluator,
    migration_disposition,
)


@pytest.mark.parametrize("phase", ["proposed", "voting"])
def test_early_phases_upcast_to_voting_on_core(phase):
    d = migration_disposition(phase)
    assert (d.target_phase, d.evaluator) == ("voting", "core")
    assert d.retain_unbound_acks is False


def test_quorum_reached_without_floor_stays_quorum_reached():
    assert migration_disposition("quorum_reached").target_phase == "quorum_reached"


def test_quorum_reached_with_floor_moves_to_final_call():
    d = migration_disposition("quorum_reached", floor_requires_final_call=True)
    assert d.target_phase == "final_call" and d.evaluator == "core"


def test_partial_final_call_retains_unbound_acks_under_frozen_legacy():
    d = migration_disposition("final_call")
    assert d.target_phase == "final_call"
    assert d.retain_unbound_acks is True
    assert d.evaluator == "frozen_legacy"


def test_final_call_ignores_new_floor_flag():
    a = migration_disposition("final_call")
    b = migration_disposition("final_call", floor_requires_final_call=True)
    assert a == b


@pytest.mark.parametrize("outcome", ["approved", "rejected", "escalated"])
def test_legacy_resolved_maps_from_generic_outcome(outcome):
    assert migration_disposition("resolved", generic_outcome=outcome).target_phase == outcome


@pytest.mark.parametrize("outcome", [None, "", "weird"])
def test_legacy_resolved_unknown_outcome_is_ambiguity_hold_never_guessed(outcome):
    assert migration_disposition("resolved", generic_outcome=outcome).target_phase == "ambiguity_hold"


def test_timeout_with_evidence_and_no_outcome_escalates():
    d = migration_disposition("timeout", timeout_evidence=True)
    assert d.target_phase == "escalated"


def test_timeout_without_evidence_or_outcome_is_ambiguity_hold():
    assert migration_disposition("timeout").target_phase == "ambiguity_hold"


def test_timeout_explicit_outcome_wins_over_default_escalation():
    d = migration_disposition("timeout", generic_outcome="rejected", timeout_evidence=True)
    assert d.target_phase == "rejected"


def test_pending_unmaterialized_preserves_snapshot_and_reroutes_to_exclusive_materializer():
    d = migration_disposition("pending_unmaterialized")
    assert d.preserve_approval_snapshot is True
    assert d.recovery_action == "reroute_exclusive_materializer"


@pytest.mark.parametrize("phase", ["claimed_no_target", "target_materialized_no_result"])
def test_claimed_or_unresulted_target_evaluates_from_evidence_never_force_executes(phase):
    for state in (None, "revoked", "materialized"):
        d = migration_disposition(phase, target_state=state)
        assert d.recovery_action == "evaluate_from_evidence"
        assert d.recovery_action != "force_execute"
        assert d.preserve_approval_snapshot is True


def test_unknown_phase_fails_closed_with_schema_error():
    with pytest.raises(SchemaError):
        migration_disposition("not_a_phase")


def test_frozen_evaluator_requires_ack_from_every_frozen_member():
    ev = FrozenLegacyEvaluator({"a", "b"})
    assert ev.is_complete({"a", "b"}) is True
    assert ev.is_complete({"a"}) is False


def test_frozen_evaluator_extra_ackers_outside_frozen_set_do_not_substitute():
    ev = FrozenLegacyEvaluator({"a", "b"})
    assert ev.is_complete({"a", "c"}) is False
    assert ev.is_complete({"a", "b", "c"}) is True


def test_frozen_evaluator_ignores_new_floor_flag():
    ev = FrozenLegacyEvaluator({"a"})
    assert ev.is_complete({"a"}, floor_flag=True) is True
    assert ev.is_complete(set(), floor_flag=True) is False


def test_frozen_evaluator_empty_frozen_set_fails_closed():
    assert FrozenLegacyEvaluator(set()).is_complete({"a"}) is False
