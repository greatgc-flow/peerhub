"""E1: approval and its effect commit atomically for every approval path; recovery after restart."""
from pathlib import Path

from fakes import FakeClock, FakeIdSource
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus_shell import BrokerAuthorityVersionStore, ConsensusShell
from peerhub.governance.contract import EffectIntent
from peerhub.persistence.sqlite import SqliteStateStore


class _Health:
    def check_health_gate(self, actor_id: str, evaluated_at: int) -> bool:
        return True


def _cfg(rule: str) -> dict:
    return {"formula": "max(2, N)", "required_votes": 2, "final_call_rule": rule,
            "deadlines": {}, "escalation_paths": []}


def _env(db: Path, tag: str, **shell_kw):
    store = SqliteStateStore(db, workspace_home_id="e1")
    if not db.exists():
        store.initialize()
    ids = FakeIdSource([f"{tag}-{i}" for i in range(1, 3000)])
    broker = GovernanceBroker(store, clock=FakeClock(range(1, 10_000)), ids=ids)
    shell = ConsensusShell(broker, clock=FakeClock(range(1, 10_000)), ids=ids, verifier=None,
                           health_port=_Health(), authority_store=BrokerAuthorityVersionStore(broker),
                           **shell_kw)
    return shell, broker


def _propose(shell, rule, rid="r1", origin=None, action=None):
    shell.propose_v2(rid, "T", "Q", "B", "p1", ["p1", "p2"], ["p1", "p2"], "normal",
                     "sha256:x", _cfg(rule), origin=origin, action=action)


def _effects(broker, rid):
    return [p.event.payload for p in broker.recover_pending_effects()
            if p.event.payload.get("target_id") == rid
            and p.event.payload.get("effect_kind") != "consensus.noop"]


def test_immediate_quorum_approval_commits_state_and_resolved_effect_together(tmp_path):
    shell, broker = _env(tmp_path / "a.sqlite3", "a")
    _propose(shell, "never")
    shell.cast_vote("r1", "p1", "agree")
    assert _effects(broker, "r1") == []
    sub = shell.cast_vote("r1", "p2", "agree")
    state = broker.get_target("r1").state
    assert state["phase"] == "approved"
    effects = _effects(broker, "r1")
    assert [e["effect_kind"] for e in effects] == ["consensus.resolved"]
    assert effects[0]["effect_payload"]["outcome"] == "approved"
    assert effects[0]["effect_payload"]["round_id"] == "r1"
    assert broker.get_outbox_event(sub.receipt.outbox_event_id) is not None


def test_mandatory_final_call_defers_the_effect_until_the_last_ack(tmp_path):
    shell, broker = _env(tmp_path / "b.sqlite3", "b")
    _propose(shell, "always")
    shell.cast_vote("r1", "p1", "agree")
    shell.cast_vote("r1", "p2", "agree")
    assert broker.get_target("r1").state["phase"] == "final_call"
    assert _effects(broker, "r1") == []
    shell.final_call_ack("r1", "p1")
    assert _effects(broker, "r1") == []
    shell.final_call_ack("r1", "p2")
    assert broker.get_target("r1").state["phase"] == "approved"
    assert [e["effect_kind"] for e in _effects(broker, "r1")] == ["consensus.resolved"]


def test_dissenting_vote_prevents_approval_and_emits_no_effect(tmp_path):
    shell, broker = _env(tmp_path / "c.sqlite3", "c")
    _propose(shell, "never")
    shell.cast_vote("r1", "p1", "agree")
    shell.cast_vote("r1", "p2", "block")
    assert broker.get_target("r1").state["phase"] == "voting"
    assert _effects(broker, "r1") == []


def test_action_specific_effect_factory_is_used_for_the_matching_action(tmp_path):
    def proposal_effect(state, round_id, actor_id):
        return EffectIntent(kind="proposal.custom", payload={"round_id": round_id, "by": actor_id})

    shell, broker = _env(tmp_path / "d.sqlite3", "d",
                         effect_factories={"governance.proposal.create": proposal_effect})
    _propose(shell, "never", origin="proposals", action="governance.proposal.create")
    shell.cast_vote("r1", "p1", "agree")
    shell.cast_vote("r1", "p2", "agree")
    effects = _effects(broker, "r1")
    assert [e["effect_kind"] for e in effects] == ["proposal.custom"]
    assert effects[0]["effect_payload"]["by"] == "p2"


def test_pending_effect_is_recovered_by_the_v2_worker_after_restart(tmp_path):
    db = tmp_path / "e.sqlite3"
    shell, broker = _env(db, "first")
    _propose(shell, "never")
    shell.cast_vote("r1", "p1", "agree")
    shell.cast_vote("r1", "p2", "agree")
    shell2, broker2 = _env(db, "second")
    receipts = shell2.process_consensus_effects("r1")
    assert len(receipts) == 1 and receipts[0].owner_id.startswith("consensus-v2:")
    assert shell2.process_consensus_effects("r1") == ()
