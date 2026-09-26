"""E2: shell operations the proposal/escalation callers need on V2 rounds."""
from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import InvalidMutationError
from peerhub.governance.authorization import AuthorizationError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus_shell import BrokerAuthorityVersionStore, ConsensusShell
from peerhub.persistence.sqlite import SqliteStateStore


class _Health:
    def check_health_gate(self, actor_id: str, evaluated_at: int) -> bool:
        return True


@pytest.fixture
def env(tmp_path: Path):
    store = SqliteStateStore(tmp_path / "e2.sqlite3", workspace_home_id="e2")
    store.initialize()
    ids = FakeIdSource([f"i-{n}" for n in range(1, 3000)])
    broker = GovernanceBroker(store, clock=FakeClock(range(1, 10_000)), ids=ids)
    shell = ConsensusShell(broker, clock=FakeClock(range(1, 10_000)), ids=ids, verifier=None,
                           health_port=_Health(), authority_store=BrokerAuthorityVersionStore(broker))
    cfg = {"formula": "unanimous", "required_votes": 2, "final_call_rule": "never",
           "deadlines": {}, "escalation_paths": ["human-tier-0"]}
    shell.propose_v2("r1", "T", "Q", "B", "p1", ["p1", "p2"], ["p1", "p2"], "normal", "sha256:x", cfg)
    return shell, broker


def _effects(broker, rid):
    return [p.event.payload for p in broker.recover_pending_effects()
            if p.event.payload.get("target_id") == rid
            and p.event.payload.get("effect_kind") != "consensus.noop"]


def test_reject_on_dissent_rejects_and_emits_rejected_effect(env):
    shell, broker = env
    shell.cast_vote("r1", "p1", "agree")
    shell.cast_vote("r1", "p2", "disagree")
    shell.reject_on_dissent("r1", "p1", "eligible voter dissent")
    assert broker.get_target("r1").state["phase"] == "rejected"
    effects = _effects(broker, "r1")
    assert [e["effect_kind"] for e in effects] == ["consensus.resolved"]
    assert effects[0]["effect_payload"]["outcome"] == "rejected"


def test_reject_on_dissent_without_a_stored_dissent_is_refused_and_writes_nothing(env):
    shell, broker = env
    shell.cast_vote("r1", "p1", "agree")
    rev = broker.get_target("r1").revision
    with pytest.raises(InvalidMutationError):
        shell.reject_on_dissent("r1", "p1", "no dissent here")
    assert broker.get_target("r1").revision == rev


def test_reject_on_dissent_by_non_frozen_actor_is_refused(env):
    shell, broker = env
    shell.cast_vote("r1", "p2", "disagree")
    with pytest.raises(AuthorizationError):
        shell.reject_on_dissent("r1", "outsider", "x")


def test_request_escalation_records_reason_tier_and_authority_and_keeps_round_open(env):
    shell, broker = env
    shell.request_escalation("r1", "too_few_voters", "p1", 0, "human-tier-0")
    state = broker.get_target("r1").state
    assert state["escalation"]["reason"] == "too_few_voters"
    assert state["escalation"]["tier"] == 0
    assert state["escalation"]["required_authority"] == "human-tier-0"
    assert state["escalation"]["requested_by"] == "p1"
    assert state["phase"] not in ("approved", "rejected", "abandoned")


def test_escalated_round_can_be_resolved_and_then_emits_the_effect(env):
    shell, broker = env
    shell.request_escalation("r1", "self_finalization", "p1", 0, "human-tier-0")
    shell.resolve("r1", "approved", "p2", "administrator decision")
    assert broker.get_target("r1").state["phase"] == "approved"
    assert [e["effect_kind"] for e in _effects(broker, "r1")] == ["consensus.resolved"]


def test_resolve_from_plain_voting_is_refused(env):
    shell, broker = env
    with pytest.raises(InvalidMutationError):
        shell.resolve("r1", "approved", "p1", "no quorum, no escalation")


def test_final_call_nack_with_blocking_concern_holds_the_barrier(tmp_path: Path):
    store = SqliteStateStore(tmp_path / "n.sqlite3", workspace_home_id="e2n")
    store.initialize()
    ids = FakeIdSource([f"n-{n}" for n in range(1, 3000)])
    broker = GovernanceBroker(store, clock=FakeClock(range(1, 10_000)), ids=ids)
    shell = ConsensusShell(broker, clock=FakeClock(range(1, 10_000)), ids=ids, verifier=None,
                           health_port=_Health(), authority_store=BrokerAuthorityVersionStore(broker))
    cfg = {"formula": "max(2, N)", "required_votes": 2, "final_call_rule": "always",
           "deadlines": {}, "escalation_paths": []}
    shell.propose_v2("rn", "T", "Q", "B", "p1", ["p1", "p2"], ["p1", "p2"], "normal", "sha256:x", cfg)
    shell.cast_vote("rn", "p1", "agree")
    shell.cast_vote("rn", "p2", "agree")
    shell.final_call_ack("rn", "p1")
    shell.final_call_nack("rn", "p2", nack_type="block")
    state = broker.get_target("rn").state
    assert state["phase"] == "final_call"
    assert state["barrier_held"] is True
    shell.final_call_nack("rn", "p2", nack_type="terminal_rejection")
    assert broker.get_target("rn").state["phase"] == "rejected"
