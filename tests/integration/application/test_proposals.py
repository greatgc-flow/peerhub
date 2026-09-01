from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from collections.abc import Iterator
from pathlib import Path
import time

import pytest

from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import (
    LegacyActionCall,
    LegacyTranslator,
    ProposalAddCommand,
    ProposalVoteCommand,
    TranslatedCommand,
)
from peerhub.application.peer_registry import PeerRegistryService
from peerhub.application.proposals import (
    ESCALATION_MID_ROUND_GATE,
    ESCALATION_SELF_FINALIZATION,
    ESCALATION_TOO_FEW_VOTERS,
    ProposalCoordinator,
)
from peerhub.cli import main
from peerhub.client import Client
from peerhub.core.context import Clock, PathLayout, RuntimeContext
from peerhub.core.errors import InvalidMutationError
from peerhub.core.ports import RequestContext
from peerhub.core.protocol import CommandSuccess
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus import ConsensusService
from peerhub.health.contract import (
    AdmissionState,
    AvailabilityState,
    HealthPolicy,
    HealthProjectionSnapshot,
    HealthScopeMembershipSnapshot,
)
from peerhub.health.service import HealthService
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.runtime import create_runtime
from peerhub.telemetry.contract import (
    EvidenceRef,
    EvidenceState,
    EvidenceValue,
    ReadinessMeasurement,
    ReadinessObserved,
)
from peerhub.telemetry.projections import TelemetryProjector
from tests.fakes import SequentialIdSource


class FixedClock(Clock):
    def __init__(self, value: int) -> None:
        self.value = value

    def now(self) -> int:
        return self.value


@dataclass
class ProposalServices:
    store: SqliteStateStore
    broker: GovernanceBroker
    consensus: ConsensusService
    registry: PeerRegistryService
    health: HealthService
    coordinator: ProposalCoordinator
    clock: FixedClock
    ids: SequentialIdSource


def _readiness(
    *,
    ids: SequentialIdSource,
    instance_id: str,
    profile_id: str,
    observed_at: int,
    freshness: int,
) -> ReadinessObserved:
    return ReadinessObserved(
        observation_id=ids.new_id("readiness-observation"),
        instance_id=instance_id,
        profile_id=profile_id,
        evidence=EvidenceValue(
            state=EvidenceState.MEASURED,
            source_tag="proposal-test",
            provider_id="proposal-test",
            provider_version="1",
            observed_at=observed_at,
            captured_at=observed_at,
            freshness_ttl=freshness,
            evidence_ref=EvidenceRef(
                f"sha256:{instance_id}-proposal-health"
            ),
            value=ReadinessMeasurement(
                runtime_revision="proposal-runtime-v1",
                issued_at=observed_at,
                valid_until=observed_at + freshness,
                integrity_verified=True,
            ),
        ),
    )


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
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    consensus = ConsensusService(broker, clock=clock, ids=ids)
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


def _add(
    services: ProposalServices,
    *,
    subject: str,
    proposer: str = "cc",
):
    return services.coordinator.add_proposal(
        subject=subject,
        from_peer=proposer,
        impact="high",
        rationale="Prevent silent failures",
        text="Require all errors to exit non-zero",
    )


def test_add_snapshots_health_filtered_electorate_and_legacy_id(
    services: ProposalServices,
) -> None:
    result = _add(services, subject="Strict Error Handling")
    second = _add(services, subject="Strict Error Handling")

    assert result.round_id == "20260902-strict-error-handling-001"
    assert second.round_id == "20260902-strict-error-handling-002"
    assert result.eligible_participants == ("cc", "cx")
    target = services.broker.get_target(result.round_id)
    assert target is not None
    assert target.state["participants"]["required"] == ("cc", "cx")
    assert target.state["participants"]["eligible"] == ("cc", "cx")
    assert target.state["proposal"]["question"] == (
        "Should PeerHub ratify the proposal: Strict Error Handling?"
    )
    assert target.state["proposal"]["body"] == (
        "Impact: high\n\nRationale:\nPrevent silent failures"
        "\n\nChanges:\nRequire all errors to exit non-zero"
    )


def test_zero_eligible_voters_rejects_before_round_creation(
    services: ProposalServices,
) -> None:
    coordinator = ProposalCoordinator(
        services.broker,
        services.consensus,
        peer_registry=services.registry,
        health=services.health,
        voter_node_ids=("ag",),
        clock=services.clock,
        ids=services.ids,
    )
    before = len(services.broker.list_targets("consensus-round", None))

    with pytest.raises(
        InvalidMutationError,
        match="zero eligible voters",
    ):
        coordinator.add_proposal(subject="No electorate")

    assert len(services.broker.list_targets("consensus-round", None)) == before


@pytest.mark.parametrize(
    ("choice", "counted", "recorded", "decisive", "outcome"),
    [
        ("agree", 1, 1, 1, None),
        ("disagree", 0, 1, 1, "NACK"),
        ("abstain", 0, 1, 0, None),
        ("need_more_info", 0, 1, 0, None),
    ],
)
def test_all_four_vote_choices_have_exact_quorum_accounting(
    services: ProposalServices,
    choice: str,
    counted: int,
    recorded: int,
    decisive: int,
    outcome: str | None,
) -> None:
    added = _add(services, subject=f"Choice {choice}", proposer="ag")
    result = services.coordinator.vote_proposal(
        added.round_id,
        voter="cc",
        vote=choice,
        reason=f"reason for {choice}",
    )

    target = services.broker.get_target(added.round_id)
    assert target is not None
    assert target.state["quorum"]["counted_votes"] == counted
    assert target.state["quorum"]["recorded_votes"] == recorded
    assert target.state["quorum"]["decisive_votes"] == decisive
    assert target.state["votes"]["cc"]["reason"] == f"reason for {choice}"
    assert result.outcome == outcome


def test_agree_plus_abstain_never_satisfies_two_voter_quorum(
    services: ProposalServices,
) -> None:
    added = _add(services, subject="No raw count quorum", proposer="ag")
    services.coordinator.vote_proposal(
        added.round_id, voter="cc", vote="agree"
    )
    result = services.coordinator.vote_proposal(
        added.round_id, voter="cx", vote="abstain"
    )

    target = services.broker.get_target(added.round_id)
    assert target is not None
    assert result.outcome is None
    assert target.state["status"] == "open"
    assert target.state["phase"] == "voting"
    assert target.state["quorum"]["counted_votes"] == 1
    assert target.state["quorum"]["recorded_votes"] == 2
    assert target.state["quorum"]["reached"] is False


def test_disagree_immediately_produces_nack(
    services: ProposalServices,
) -> None:
    added = _add(services, subject="Dissent veto")
    result = services.coordinator.vote_proposal(
        added.round_id, voter="cx", vote="disagree"
    )

    target = services.broker.get_target(added.round_id)
    assert target is not None
    assert result.outcome == "NACK"
    assert result.disagreed == ("cx",)
    assert target.state["status"] == "resolved"
    assert target.state["resolution"]["outcome"] == "rejected"


def test_fewer_than_two_voters_escalates(
    services: ProposalServices,
) -> None:
    coordinator = ProposalCoordinator(
        services.broker,
        services.consensus,
        peer_registry=services.registry,
        health=services.health,
        voter_node_ids=("cc",),
        clock=services.clock,
        ids=services.ids,
    )
    added = coordinator.add_proposal(subject="Singleton")
    result = coordinator.vote_proposal(
        added.round_id, voter="cc", vote="agree"
    )

    assert result.outcome == "ESCALATED"
    assert result.escalation_reason == ESCALATION_TOO_FEW_VOTERS


def test_mid_round_gate_closure_escalates(
    services: ProposalServices,
) -> None:
    added = _add(services, subject="Gate closure")
    services.clock.value += 3601

    result = services.coordinator.vote_proposal(
        added.round_id, voter="cc", vote="agree"
    )

    assert result.outcome == "ESCALATED"
    assert result.escalation_reason == ESCALATION_MID_ROUND_GATE


def test_completed_proposer_alone_agreement_escalates(
    services: ProposalServices,
) -> None:
    added = _add(services, subject="Self finalization", proposer="cc")
    services.coordinator.vote_proposal(
        added.round_id, voter="cc", vote="agree"
    )
    result = services.coordinator.vote_proposal(
        added.round_id, voter="cx", vote="abstain"
    )

    assert result.outcome == "ESCALATED"
    assert result.escalation_reason == ESCALATION_SELF_FINALIZATION


def test_unanimous_agree_commits_frozen_invariant_write_request(
    services: ProposalServices,
) -> None:
    added = _add(services, subject="Ratify invariant", proposer="cc")
    services.coordinator.vote_proposal(
        added.round_id, voter="cc", vote="agree"
    )
    result = services.coordinator.vote_proposal(
        added.round_id, voter="cx", vote="agree"
    )

    assert result.outcome == "CONSENSUS_OK"
    assert result.agreed == ("cc", "cx")
    assert result.invariant_request_target_id is not None
    round_target = services.broker.get_target(added.round_id)
    request = services.broker.get_target(
        result.invariant_request_target_id
    )
    assert round_target is not None
    assert request is not None
    state = request.state
    decision_hash = round_target.state["resolution"]["decision_hash"]
    assert request.target_id == (
        f"ratified-invariant-write-request:{added.round_id}:{decision_hash}"
    )
    assert state["kind"] == "ratified-invariant-write-request"
    assert state["scope"] == added.round_id
    assert state["schema_version"] == 1
    assert state["status"] == "REQUESTED"
    assert state["round_id"] == added.round_id
    assert state["approved_revision"] == round_target.revision
    assert state["decision_hash"] == decision_hash
    assert state["proposer_id"] == "cc"
    assert state["title"] == "Ratify invariant"
    assert state["question"] == (
        "Should PeerHub ratify the proposal: Ratify invariant?"
    )
    assert state["source_hash"].startswith("sha256:")
    assert state["participants"]["required"] == ("cc", "cx")
    assert state["votes"]["cc"]["choice"] == "agree"
    assert state["votes"]["cx"]["choice"] == "agree"
    assert state["proposed_invariant_text"] == (
        "Require all errors to exit non-zero"
    )
    assert state["target_doc_hint"] == "10-invariants.md"
    assert isinstance(state["requested_at"], int)
    assert not (Path("10-invariants.md")).exists()

    repeated = services.coordinator.reconcile_outcome(added.round_id)
    assert repeated.outcome == "CONSENSUS_OK"
    assert repeated.invariant_request_target_id == request.target_id
    assert services.broker.get_target(request.target_id).revision == 1  # type: ignore[union-attr]


def _submission(key: str) -> SubmissionMetadata:
    return SubmissionMetadata(
        f"request-{key}",
        f"correlation-{key}",
        "proposal-client",
        "cc",
        {},
        f"idempotency-{key}",
        None,
        None,
        1,
    )


def _seed_runtime_health(
    store: SqliteStateStore,
    policy: HealthPolicy,
    *,
    timestamp: int,
) -> None:
    ids = SequentialIdSource()
    with store.unit_of_work() as unit:
        for index, instance_id in enumerate(("cc", "cx"), start=1):
            observation = _readiness(
                ids=ids,
                instance_id=instance_id,
                profile_id=f"{instance_id}.standard",
                observed_at=timestamp,
                freshness=policy.readiness_freshness_seconds,
            )
            unit.add_readiness_observation(observation)
            unit.add_health_projection(
                HealthProjectionSnapshot(
                    projection_id=f"runtime-health-{index}",
                    instance_id=instance_id,
                    profile_id=f"{instance_id}.standard",
                    availability_state=AvailabilityState.HEALTHY,
                    admission_state=AdmissionState.OPEN,
                    readiness_observation_id=observation.observation_id,
                    operational_projection_id=None,
                    operational_projection_revision=None,
                    policy_id=policy.policy_id,
                    policy_revision=policy.revision,
                    cooldown_until=None,
                    evidence_refs=(observation.evidence.evidence_ref,),
                    revision=1,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        unit.commit()


def test_legacy_translation_for_both_actions_executes(tmp_path: Path) -> None:
    timestamp = int(time.time())
    context = RuntimeContext(
        "legacy-proposal-test",
        PathLayout.for_workspace(tmp_path),
        FixedClock(timestamp),
        SequentialIdSource(),
    )
    with create_runtime(
        context,
        proposal_voters=("cc", "cx"),
    ) as runtime:
        _seed_runtime_health(
            runtime.state_store,
            runtime.health_service.policy,
            timestamp=timestamp,
        )
        client = Client(
            runtime.application_api,
            caller=RequestContext(
                principal="proposal-user",
                client_id="proposal-client",
            ),
        )
        translated_add = LegacyTranslator().translate(
            LegacyActionCall(
                "proposal-add",
                {
                    "subject": "Legacy execution",
                    "from": "cc",
                    "impact": "med",
                    "detail": "because",
                    "text": "change",
                },
            ),
            _submission("add"),
        )
        assert isinstance(translated_add, TranslatedCommand)
        assert isinstance(translated_add.command, ProposalAddCommand)
        add_outcome = client.submit(translated_add.command)
        assert isinstance(add_outcome, CommandSuccess)
        round_id = add_outcome.result["round_id"]
        assert isinstance(round_id, str)

        translated_vote = LegacyTranslator().translate(
            LegacyActionCall(
                "proposal-vote",
                {
                    "proposal_id": round_id,
                    "voter": "cc",
                    "vote": "agree",
                    "reason": "yes",
                },
            ),
            _submission("vote"),
        )
        assert isinstance(translated_vote, TranslatedCommand)
        assert isinstance(translated_vote.command, ProposalVoteCommand)
        vote_outcome = client.submit(translated_vote.command)
        assert isinstance(vote_outcome, CommandSuccess)
        assert vote_outcome.result["choice"] == "agree"


def test_cli_add_and_vote_exact_compatibility_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / ".peerhub"
    config_dir.mkdir(parents=True)
    (config_dir / "proposals.json").write_text(
        json.dumps({"voters": ["cc", "cx"]}),
        encoding="utf-8",
    )
    timestamp = int(time.time())
    context = RuntimeContext(
        "cli-proposal-test",
        PathLayout.for_workspace(tmp_path),
        FixedClock(timestamp),
        SequentialIdSource(),
    )
    with create_runtime(
        context,
        proposal_voters=("cc", "cx"),
    ) as runtime:
        _seed_runtime_health(
            runtime.state_store,
            runtime.health_service.policy,
            timestamp=timestamp,
        )

    assert main([
        "consensus",
        "proposal-add",
        "--workspace",
        str(tmp_path),
        "--subject",
        "CLI proposal",
        "--from",
        "cc",
        "--impact",
        "high",
        "--rationale",
        "because",
        "--text",
        "change",
    ]) == 0
    add_lines = capsys.readouterr().out.splitlines()
    round_id = add_lines[0].split()[2]
    assert add_lines == [
        f"[HUB] PROPOSAL-ADD {round_id} | from=cc | impact=HIGH",
        "      Vote with: hub.py proposal-vote "
        f"--proposal-id {round_id} --vote agree --voter <peer>",
    ]

    assert main([
        "consensus",
        "proposal-vote",
        "--workspace",
        str(tmp_path),
        "--proposal-id",
        round_id,
        "--voter",
        "cc",
        "--vote",
        "agree",
    ]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"[HUB] PROPOSAL-VOTE {round_id} | cc:AGREE"
    ]

    assert main([
        "consensus",
        "proposal-vote",
        "--workspace",
        str(tmp_path),
        "--proposal-id",
        round_id,
        "--voter",
        "cx",
        "--vote",
        "agree",
    ]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"[HUB] PROPOSAL-VOTE {round_id} | cx:AGREE",
        f"[HUB] PROPOSAL CONSENSUS_OK {round_id} | "
        "unanimous agree: cc,cx",
    ]
