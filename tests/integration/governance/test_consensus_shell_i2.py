from peerhub.core.errors import InvalidMutationError
import pytest
from pathlib import Path
from typing import Optional

from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus_shell import ConsensusShell, BrokerAuthorityVersionStore
from fakes import FakeClock, FakeIdSource
from peerhub.governance.authorization import AuthorizationError, HealthIdentityPort
from peerhub.governance.authority_fence import StaleAuthorityError
from peerhub.governance.contract import MutationRequest, EffectIntent, resolve_local_os_write_provenance
from peerhub.core.protocol import CommandID

class FakeHealthPort(HealthIdentityPort):
    def __init__(self, healthy_actors: set[str]):
        self.healthy_actors = healthy_actors
    def check_health_gate(self, actor_id: str, evaluated_at: int) -> bool:
        return actor_id in self.healthy_actors

def fake_verifier(credential_id: str, claimed_actor_id: str) -> bool:
    return credential_id.startswith("valid")

@pytest.fixture
def test_env(tmp_path: Path):
    store = SqliteStateStore(tmp_path / "shell2.sqlite3", workspace_home_id="test-shell2")
    store.initialize()
    clock = FakeClock(range(1, 100))
    ids = FakeIdSource([f"id-{i}" for i in range(1, 100)])
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    
    authority_store = BrokerAuthorityVersionStore(broker)
    health_port = FakeHealthPort(healthy_actors={"peer-1", "peer-2", "peer-3"})
    shell = ConsensusShell(
        broker,
        clock=clock,
        ids=ids,
        verifier=fake_verifier,
        health_port=health_port,
        authority_store=authority_store
    )
    return shell, broker, authority_store, health_port

def inject_state(broker: GovernanceBroker, round_id: str, phase: str, ack_ledger: dict = None, extra_state: dict = None):
    state = {
        "schema": "peerhub.consensus-round.v2",
        "phase": phase,
        "expected_authority_version": 1,
        "frozen_authority_set": ["peer-1", "peer-2", "peer-3"],
        "policy_snapshot": {
            "origin": "direct", 
            "action": "consensus.round.propose",
            "version": 1,
            "formula": "max(2, N)",
            "required_votes": 2,
            "final_call_rule": "always",
            "deadlines": {},
            "escalation_paths": [],
            "risk": "normal",
            "mandatory_final_call": False,
        },
        "participants": ["peer-1", "peer-2", "peer-3"],
        "ack_ledger": ack_ledger or {},
        "candidate_revision": 0,
        "proposal": {
            "title": "Test Proposal",
            "question": "Do we proceed?",
            "body": "Body",
            "proposer_id": "peer-1",
            "source_hash": "sha256:abc",
            "required_participants": ["peer-1", "peer-2"],
        }
    }
    if extra_state:
        state.update(extra_state)
    broker.submit(MutationRequest(
        request_id=f"inject-{round_id}",
        command_id=CommandID("cmd"),
        correlation_id="corr",
        client_id="test",
        command_type="test",
        idempotency_key=f"idem-{round_id}",
        actor_id="tester",
        policy_revision="1",
        target_id=round_id,
        expected_revision=0,
        operation="inject",
        desired_state=state,
        effect_intent=EffectIntent(kind="consensus.noop", payload={}),
        write_provenance=resolve_local_os_write_provenance()
    ))


# ==========================================
# cast_vote tests
# ==========================================

def test_cast_vote_records_vote_and_dissent_obligation(test_env):
    shell, broker, _, _ = test_env
    inject_state(broker, "round-vote-1", "voting")
    
    shell.cast_vote("round-vote-1", "peer-1", choice="block")
    
    target = broker.get_target("round-vote-1")
    assert target.state["phase"] == "voting"
    votes = target.state.get("votes", {})
    assert "peer-1" in votes
    assert votes["peer-1"]["choice"] == "block"
    assert "dissent_obligation" in votes["peer-1"]

def test_cast_vote_quorum_reached_opens_final_call_when_mandatory(test_env):
    shell, broker, _, _ = test_env
    inject_state(broker, "round-vote-2", "voting")
    
    shell.cast_vote("round-vote-2", "peer-1", choice="agree")
    target = broker.get_target("round-vote-2")
    assert target.state["phase"] == "voting"
    
    shell.cast_vote("round-vote-2", "peer-2", choice="agree")
    target = broker.get_target("round-vote-2")
    assert target.state["phase"] == "final_call"

def test_cast_vote_fails_closed_for_non_frozen_actor_with_no_write(test_env):
    shell, broker, _, _ = test_env
    inject_state(broker, "round-vote-3", "voting")
    before = broker.get_target("round-vote-3")
    
    with pytest.raises(AuthorizationError):
        shell.cast_vote("round-vote-3", "peer-unknown", choice="agree")
        
    after = broker.get_target("round-vote-3")
    assert before.revision == after.revision

def test_cast_vote_fails_for_stale_authority_with_no_write(test_env):
    shell, broker, authority_store, _ = test_env
    inject_state(broker, "round-vote-4", "voting")
    authority_store.increment()
    before = broker.get_target("round-vote-4")
    
    with pytest.raises(StaleAuthorityError):
        shell.cast_vote("round-vote-4", "peer-1", choice="agree")
        
    after = broker.get_target("round-vote-4")
    assert before.revision == after.revision

# ==========================================
# correction tests
# ==========================================

def test_correction_clears_ledger_and_returns_to_voting(test_env):
    shell, broker, _, _ = test_env
    inject_state(broker, "round-corr-1", "final_call", ack_ledger={"hash": ["peer-1"]})
    
    shell.correction("round-corr-1", "peer-2")
    
    target = broker.get_target("round-corr-1")
    assert target.state["phase"] == "voting"
    assert target.state["ack_ledger"] == {}

def test_correction_fails_closed_for_non_frozen_actor(test_env):
    shell, broker, _, _ = test_env
    inject_state(broker, "round-corr-2", "final_call")
    before = broker.get_target("round-corr-2")
    
    with pytest.raises(AuthorizationError):
        shell.correction("round-corr-2", "peer-unknown")
        
    after = broker.get_target("round-corr-2")
    assert before.revision == after.revision

def test_correction_fails_for_stale_authority(test_env):
    shell, broker, authority_store, _ = test_env
    inject_state(broker, "round-corr-3", "final_call")
    authority_store.increment()
    before = broker.get_target("round-corr-3")
    
    with pytest.raises(StaleAuthorityError):
        shell.correction("round-corr-3", "peer-1")
        
    after = broker.get_target("round-corr-3")
    assert before.revision == after.revision


# ==========================================
# retraction tests
# ==========================================

def test_retraction_pre_authorization_drops_ack(test_env):
    shell, broker, _, _ = test_env
    inject_state(broker, "round-ret-1", "final_call", ack_ledger={"hash": ["peer-1", "peer-2"]})
    
    shell.retraction("round-ret-1", "peer-1")
    
    target = broker.get_target("round-ret-1")
    assert target.state["phase"] == "voting"
    # Depending on implementation, it may clear just the ack or the whole ledger. 
    # Usually returning to voting clears the ledger or removes the retracting actor.
    assert "peer-1" not in str(target.state.get("ack_ledger", {}))

def test_retraction_post_authorization_bumps_authority_version(test_env):
    shell, broker, authority_store, _ = test_env
    inject_state(broker, "round-ret-2", "approved", ack_ledger={"hash": ["peer-1", "peer-2"]})
    version_before = authority_store.read_version()
    
    shell.retraction("round-ret-2", "peer-1")
    
    target = broker.get_target("round-ret-2")
    assert target.state["phase"] == "approved"
    assert "RevocationRecorded" in str(target.state.get("revocations", []))
    assert authority_store.read_version() == version_before + 1

def test_retraction_fails_closed_for_non_frozen_actor(test_env):
    shell, broker, _, _ = test_env
    inject_state(broker, "round-ret-3", "final_call")
    before = broker.get_target("round-ret-3")
    
    with pytest.raises(AuthorizationError):
        shell.retraction("round-ret-3", "peer-unknown")
        
    after = broker.get_target("round-ret-3")
    assert before.revision == after.revision

def test_retraction_fails_for_stale_authority(test_env):
    shell, broker, authority_store, _ = test_env
    inject_state(broker, "round-ret-4", "final_call")
    authority_store.increment()
    before = broker.get_target("round-ret-4")
    
    with pytest.raises(StaleAuthorityError):
        shell.retraction("round-ret-4", "peer-1")
        
    after = broker.get_target("round-ret-4")
    assert before.revision == after.revision


# ==========================================
# mark_timeout tests
# ==========================================

def test_mark_timeout_records_evidence(test_env):
    shell, broker, _, _ = test_env
    inject_state(broker, "round-timeout-1", "voting")
    
    shell.mark_timeout("round-timeout-1", "peer-1")
    
    target = broker.get_target("round-timeout-1")
    assert target.state.get("timeout_evidence") is not None
    assert target.state["timeout_evidence"]["actor_id"] == "peer-1"

def test_mark_timeout_fails_closed_for_non_frozen_actor(test_env):
    shell, broker, _, _ = test_env
    inject_state(broker, "round-timeout-2", "voting")
    before = broker.get_target("round-timeout-2")
    
    with pytest.raises(AuthorizationError):
        shell.mark_timeout("round-timeout-2", "peer-unknown")
        
    after = broker.get_target("round-timeout-2")
    assert before.revision == after.revision

def test_mark_timeout_fails_for_stale_authority(test_env):
    shell, broker, authority_store, _ = test_env
    inject_state(broker, "round-timeout-3", "voting")
    authority_store.increment()
    before = broker.get_target("round-timeout-3")
    
    with pytest.raises(StaleAuthorityError):
        shell.mark_timeout("round-timeout-3", "peer-1")
        
    after = broker.get_target("round-timeout-3")
    assert before.revision == after.revision


# ==========================================
# abandon tests
# ==========================================

def test_abandon_is_terminal_and_blocks_further_acks(test_env):
    shell, broker, _, _ = test_env
    inject_state(broker, "round-abandon-1", "voting")
    
    shell.abandon("round-abandon-1", "peer-1")
    
    target = broker.get_target("round-abandon-1")
    assert target.state["phase"] == "abandoned"
    
    # Terminal phase is an invalid-phase mutation, not an authorization failure.
    with pytest.raises(InvalidMutationError):
        shell.final_call_ack("round-abandon-1", "peer-2")

def test_abandon_fails_closed_for_non_frozen_actor(test_env):
    shell, broker, _, _ = test_env
    inject_state(broker, "round-abandon-2", "voting")
    before = broker.get_target("round-abandon-2")
    
    with pytest.raises(AuthorizationError):
        shell.abandon("round-abandon-2", "peer-unknown")
        
    after = broker.get_target("round-abandon-2")
    assert before.revision == after.revision

def test_abandon_fails_for_stale_authority(test_env):
    shell, broker, authority_store, _ = test_env
    inject_state(broker, "round-abandon-3", "voting")
    authority_store.increment()
    before = broker.get_target("round-abandon-3")
    
    with pytest.raises(StaleAuthorityError):
        shell.abandon("round-abandon-3", "peer-1")
        
    after = broker.get_target("round-abandon-3")
    assert before.revision == after.revision
