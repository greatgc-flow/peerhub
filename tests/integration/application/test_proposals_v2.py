"""Run the whole existing proposal suite against ACTIVATED V2 storage.

Every test of test_proposals.py is re-run with a ProposalCoordinator wired through
ConsensusFacade after the atomic consensus V2 activation: same expectations, new engine.
"""
from __future__ import annotations

import sqlite3

from tests.integration.application.test_proposals import *  # noqa: F401,F403
from tests.integration.application.test_proposals import (  # noqa: F401
    ProposalServices, FixedClock, _readiness, _add,
)
from peerhub.application.consensus_facade import ConsensusFacade
from peerhub.application.health_gate_port import PeerHealthGatePort
from peerhub.application.proposals import PROPOSAL_CREATE_ACTION, PROPOSAL_SYSTEM_PRINCIPALS, make_v2_ratified_effect_factory
from peerhub.governance.consensus_shell import BrokerAuthorityVersionStore, ConsensusShell
from peerhub.persistence.consensus_activation import activate_consensus_v2


def _activate_v2(db_path) -> None:
    connection = sqlite3.connect(db_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        activate_consensus_v2(connection, now=1)
    finally:
        connection.close()


@pytest.fixture
def services(tmp_path: Path) -> Iterator[ProposalServices]:
    timestamp = int(
        datetime(2026, 9, 2, tzinfo=timezone.utc).timestamp()
    )
    clock = FixedClock(timestamp)
    ids = SequentialIdSource()
    store = SqliteStateStore(
        tmp_path / "proposals.sqlite3",
        workspace_home_id="proposal-test",
    )
    store.initialize()
    _activate_v2(tmp_path / "proposals.sqlite3")
    store = SqliteStateStore(
        tmp_path / "proposals.sqlite3",
        workspace_home_id="proposal-test",
    )
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    legacy = ConsensusService(broker, clock=clock, ids=ids)
    registry = PeerRegistryService(broker, clock=clock, ids=ids)
    policy = HealthPolicy(
        policy_id="proposal-health-v1",
        revision=1,
        readiness_freshness_seconds=3600,
        recovery_backoff_seconds=(30, 60),
        recovery_jitter_fraction=0.0,
        readiness_observation_threshold=1,
        administrative_recovery_probe_limit=1,
    )
    with store.unit_of_work() as unit:
        unit.add_health_policy_revision(policy)
        unit.commit()
    health = HealthService(
        store,
        telemetry=TelemetryProjector(store, ids=ids, freshness_ttl=3600),
        policy=policy,
        membership=HealthScopeMembershipSnapshot(
            configuration_revision=1,
            configuration_digest="a" * 64,
            configured_members=(
                ("cc", "cc.standard"),
                ("cx", "cx.standard"),
                ("ag", "ag.standard"),
            ),
            bindings=(),
        ),
        clock=clock,
        ids=ids,
    )
    for instance_id in ("cc", "cx"):
        health.evaluate_and_persist_readiness(
            _readiness(
                ids=ids,
                instance_id=instance_id,
                profile_id=f"{instance_id}.standard",
                observed_at=timestamp,
                freshness=3600,
            ),
            sealed_runtime_revision="proposal-runtime-v1",
            adapter_declares_probe_safe=True,
        )
    shell = ConsensusShell(
        broker, clock=clock, ids=ids, verifier=None,
        health_port=PeerHealthGatePort(registry, health),
        authority_store=BrokerAuthorityVersionStore(broker),
        effect_factories={PROPOSAL_CREATE_ACTION: make_v2_ratified_effect_factory(clock)},
        system_principals=PROPOSAL_SYSTEM_PRINCIPALS,
    )
    consensus = ConsensusFacade(legacy, shell, is_v2_active=store.is_consensus_v2_active)
    coordinator = ProposalCoordinator(
        broker,
        consensus,
        peer_registry=registry,
        health=health,
        voter_node_ids=("cc", "ag", "cx"),
        clock=clock,
        ids=ids,
    )
    result = ProposalServices(
        store,
        broker,
        consensus,
        registry,
        health,
        coordinator,
        clock,
        ids,
    )
    yield result
    store.close()




# ---------------------------------------------------------------------------
# Deliberate V2 divergence (design 4.2 / 6.2): a HIGH-risk proposal is never
# auto-approved on the last agree; it opens a mandatory Final Call and is
# approved atomically by the last ACK. Everything else must match V1 exactly.
# ---------------------------------------------------------------------------
def test_unanimous_agree_commits_frozen_invariant_write_request(services):  # noqa: F811
    added = _add(services, subject="Ratify invariant", proposer="cc")
    services.coordinator.vote_proposal(added.round_id, voter="cc", vote="agree")
    pending = services.coordinator.vote_proposal(added.round_id, voter="cx", vote="agree")

    assert pending.outcome is None  # Final Call open, nothing approved yet
    assert services.broker.get_target(added.round_id).state["phase"] == "final_call"
    assert not [  # no invariant effect before the ACKs
        e for e in services.broker.recover_pending_effects()
        if e.event.payload.get("effect_kind") != "consensus.noop"
    ]

    services.consensus.final_call_ack(added.round_id, actor_id="cc", ack=True)
    services.consensus.final_call_ack(added.round_id, actor_id="cx", ack=True)
    result = services.coordinator.reconcile_outcome(added.round_id)

    assert result.outcome == "CONSENSUS_OK"
    assert result.agreed == ("cc", "cx")
    round_target = services.broker.get_target(added.round_id)
    request = services.broker.get_target(result.invariant_request_target_id)
    assert request is not None
    state = request.state
    decision_hash = round_target.state["resolution"]["decision_hash"]
    assert request.target_id == f"ratified-invariant-write-request:{added.round_id}:{decision_hash}"
    assert state["kind"] == "ratified-invariant-write-request"
    assert state["status"] == "REQUESTED"
    assert state["round_id"] == added.round_id
    assert state["approved_revision"] == round_target.revision
    assert state["decision_hash"] == decision_hash
    assert state["proposer_id"] == "cc"
    assert state["participants"]["required"] == ("cc", "cx")
    assert state["votes"]["cc"]["choice"] == "agree"
    assert state["proposed_invariant_text"] == "Require all errors to exit non-zero"
    repeated = services.coordinator.reconcile_outcome(added.round_id)
    assert repeated.invariant_request_target_id == request.target_id
    assert services.broker.get_target(request.target_id).revision == 1
