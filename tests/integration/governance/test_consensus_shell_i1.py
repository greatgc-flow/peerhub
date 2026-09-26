import pytest
from pathlib import Path

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
    store = SqliteStateStore(tmp_path / "shell.sqlite3", workspace_home_id="test-shell")
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

def test_authority_store_reads_initial_version(test_env):
    shell, broker, authority_store, _ = test_env
    assert authority_store.read_version() == 1

def test_authority_store_increments_version(test_env):
    shell, broker, authority_store, _ = test_env
    new_version = authority_store.increment()
    assert new_version == 2
    assert authority_store.read_version() == 2

def test_propose_v2_persists_schema_and_frozen_policy(test_env):
    shell, broker, authority_store, _ = test_env
    config = {
        "formula": "max(2, N)",
        "required_votes": 2,
        "final_call_rule": "always",
        "deadlines": {"voting": 86400},
        "escalation_paths": ["human-tier-0"]
    }
    shell.propose_v2(
        round_id="round-prop",
        title="Test Proposal",
        question="Do we proceed?",
        body="Body",
        proposer_id="peer-1",
        required_participants=["peer-1", "peer-2"],
        eligible_participants=["peer-1", "peer-2", "peer-3"],
        risk="high",
        source_hash="sha256:abc",
        config=config,
        origin="direct",
        action="consensus.round.propose"
    )
    
    target = broker.get_target("round-prop")
    assert target is not None
    assert target.state["schema"] == "peerhub.consensus-round.v2"
    assert target.state["phase"] == "voting"
    assert "policy_snapshot" in target.state
    assert target.state["policy_snapshot"]["origin"] == "direct"
    assert target.state["policy_snapshot"]["action"] == "consensus.round.propose"
    assert tuple(target.state["participants"]) == ("peer-1", "peer-2", "peer-3")
    assert set(target.state["frozen_authority_set"]) == {"peer-1", "peer-2", "peer-3"}
    assert target.state["expected_authority_version"] == 1
    assert target.state["ack_ledger"] == {}

def inject_final_call_state(broker: GovernanceBroker, round_id: str):
    state = {
        "schema": "peerhub.consensus-round.v2",
        "phase": "final_call",
        "expected_authority_version": 1,
        "frozen_authority_set": ["peer-1", "peer-2"],
        "policy_snapshot": {"origin": "direct", "action": "consensus.round.propose"},
        "participants": ["peer-1", "peer-2"],
        "ack_ledger": {}
    }
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

def test_final_call_ack_completes_when_all_required_ack(test_env):
    shell, broker, authority_store, _ = test_env
    inject_final_call_state(broker, "round-ack")
    
    shell.final_call_ack("round-ack", "peer-1")
    target = broker.get_target("round-ack")
    assert target.state["phase"] == "final_call"
    
    shell.final_call_ack("round-ack", "peer-2")
    target = broker.get_target("round-ack")
    assert target.state["phase"] == "approved"
    
    pending = broker.recover_pending_effects()
    assert any(p.event.payload.get("effect_kind") == "consensus.resolved" for p in pending)

def test_final_call_ack_raises_stale_authority_error_on_increment(test_env):
    shell, broker, authority_store, _ = test_env
    inject_final_call_state(broker, "round-stale")
    
    authority_store.increment()
    
    with pytest.raises(StaleAuthorityError):
        shell.final_call_ack("round-stale", "peer-1")

def test_final_call_ack_raises_authorization_error_if_not_frozen(test_env):
    shell, broker, authority_store, _ = test_env
    inject_final_call_state(broker, "round-auth")
    
    with pytest.raises(AuthorizationError):
        shell.final_call_ack("round-auth", "peer-3")

def test_final_call_ack_fails_closed_if_unhealthy(test_env):
    shell, broker, authority_store, health_port = test_env
    inject_final_call_state(broker, "round-health")
    
    health_port.healthy_actors.remove("peer-1")
    
    with pytest.raises(AuthorizationError):
        shell.final_call_ack("round-health", "peer-1")


def test_failed_ack_writes_nothing(test_env):
    shell, broker, authority_store, _ = test_env
    inject_final_call_state(broker, "round-nowrite")
    before = broker.get_target("round-nowrite")
    authority_store.increment()
    with pytest.raises(StaleAuthorityError):
        shell.final_call_ack("round-nowrite", "peer-1")
    with pytest.raises(AuthorizationError):
        shell.final_call_ack("round-nowrite", "peer-3")
    after = broker.get_target("round-nowrite")
    assert after.revision == before.revision
    assert after.state["ack_ledger"] == {}


def test_incomplete_ack_persists_binding_and_stays_final_call(test_env):
    shell, broker, authority_store, _ = test_env
    inject_final_call_state(broker, "round-bind")
    before = broker.get_target("round-bind")
    shell.final_call_ack("round-bind", "peer-1")
    after = broker.get_target("round-bind")
    assert after.revision == before.revision + 1
    assert after.state["phase"] == "final_call"
    assert "peer-1" in str(after.state["ack_ledger"])
    assert "peer-2" not in str(after.state["ack_ledger"])


def test_two_authority_store_handles_do_not_lose_increments(test_env):
    shell, broker, authority_store, _ = test_env
    other = BrokerAuthorityVersionStore(broker)
    assert authority_store.increment() == 2
    assert other.increment() == 3
    assert authority_store.read_version() == 3


def test_ack_with_invalid_credential_fails_closed_and_valid_one_passes(test_env):
    shell, broker, authority_store, _ = test_env
    inject_final_call_state(broker, "round-cred")
    with pytest.raises(AuthorizationError):
        shell.final_call_ack("round-cred", "peer-1", credential_id="bogus")
    shell.final_call_ack("round-cred", "peer-1", credential_id="valid-1")
    assert broker.get_target("round-cred").state["phase"] == "final_call"


def test_acks_bound_to_a_different_candidate_hash_do_not_count(test_env):
    shell, broker, authority_store, _ = test_env
    inject_final_call_state(broker, "round-stalecand")
    target = broker.get_target("round-stalecand")
    state = dict(target.state)
    state["ack_ledger"] = {"sha256:some-older-candidate": ["peer-1"]}
    broker.submit(MutationRequest(
        request_id="inject-stalecand-2", command_id=CommandID("cmd-2"), correlation_id="corr",
        client_id="test", command_type="test", idempotency_key="idem-stalecand-2",
        actor_id="tester", policy_revision="1", target_id="round-stalecand",
        expected_revision=target.revision, operation="inject", desired_state=state,
        effect_intent=EffectIntent(kind="consensus.noop", payload={}),
        write_provenance=resolve_local_os_write_provenance(),
    ))
    shell.final_call_ack("round-stalecand", "peer-2")
    assert broker.get_target("round-stalecand").state["phase"] == "final_call"


def test_propose_v2_state_is_listable_as_consensus_round_with_provenance(test_env):
    shell, broker, _, _ = test_env
    shell.propose_v2(
        round_id="round-list", title="T", question="Q", body="B", proposer_id="peer-1",
        required_participants=["peer-1", "peer-2"], eligible_participants=["peer-1", "peer-2"],
        risk="normal", source_hash="sha256:x",
        config={"formula": "max(2, N)", "required_votes": 2, "final_call_rule": "never",
                "deadlines": {}, "escalation_paths": []},
    )
    state = broker.get_target("round-list").state
    assert state["kind"] == "consensus-round" and state["round_id"] == "round-list"
    assert (state["origin"], state["action"]) == ("direct", "consensus.round.propose")
    assert "round-list" in [t.target_id for t in broker.list_targets("consensus-round", None)]
