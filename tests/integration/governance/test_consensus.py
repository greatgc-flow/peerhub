from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import InvalidMutationError, StaleRevisionError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus import ConsensusService
from peerhub.persistence.sqlite import SqliteStateStore


def _service(tmp_path: Path) -> tuple[ConsensusService, GovernanceBroker]:
    store = SqliteStateStore(
        tmp_path / "consensus.sqlite3",
        workspace_home_id="consensus-test",
    )
    store.initialize()
    broker = GovernanceBroker(
        store,
        clock=FakeClock(range(1, 50)),
        ids=FakeIdSource([f"id-{i}" for i in range(1, 100)]),
    )
    return ConsensusService(broker, clock=FakeClock(range(1, 50)), ids=FakeIdSource([f"domain-{i}" for i in range(1, 100)])), broker


@pytest.mark.parametrize(("participants", "required"), [(2, 2), (3, 3), (4, 4)])
def test_propose_builds_canonical_envelope(tmp_path: Path, participants: int, required: int) -> None:
    service, broker = _service(tmp_path)
    actor_ids = tuple(f"peer-{i}" for i in range(participants))
    service.propose(
        round_id=f"round-{participants}",
        title="Choose",
        question="Which?",
        body="The body",
        proposer_id="peer-0",
        required_participants=actor_ids,
        eligible_participants=actor_ids,
        risk="normal",
        source_hash="sha256:test",
    )

    target = broker.get_target(f"round-{participants}")
    assert target is not None
    assert target.revision == 1
    assert target.state["schema"] == "peerhub.consensus-round.v1"
    assert target.state["phase"] == "proposed"
    assert target.state["participants"]["quorum"]["required"] == required
    assert target.state["votes"] == {}


def test_cast_vote_counts_only_agreements_and_dissent_blocks_quorum(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.propose(
        round_id="round-vote",
        title="Choose",
        question="Which?",
        body="The body",
        proposer_id="peer-a",
        required_participants=("peer-a", "peer-b"),
        eligible_participants=("peer-a", "peer-b"),
        risk="normal",
        source_hash="sha256:test",
    )
    service.cast_vote("round-vote", actor_id="peer-a", choice="agree")
    service.cast_vote("round-vote", actor_id="peer-b", choice="disagree")

    target = broker.get_target("round-vote")
    assert target is not None
    assert target.state["phase"] == "voting"
    assert target.state["quorum"]["reached"] is False
    assert target.state["quorum"]["counted_votes"] == 1
    assert target.state["quorum"]["recorded_votes"] == 2
    assert target.state["quorum"]["decisive_votes"] == 2
    assert target.state["votes"]["peer-a"]["choice"] == "agree"


def test_ineligible_vote_is_rejected(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    service.propose(
        round_id="round-auth",
        title="Choose",
        question="Which?",
        body="The body",
        proposer_id="peer-a",
        required_participants=("peer-a",),
        eligible_participants=("peer-a",),
        risk="normal",
        source_hash="sha256:test",
    )
    with pytest.raises(InvalidMutationError, match="eligible"):
        service.cast_vote("round-auth", actor_id="peer-x", choice="agree")


def test_vote_correction_overwrites_without_double_counting(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.propose(
        round_id="round-correct",
        title="Choose",
        question="Which?",
        body="The body",
        proposer_id="peer-a",
        required_participants=("peer-a", "peer-b"),
        eligible_participants=("peer-a", "peer-b"),
        risk="normal",
        source_hash="sha256:test",
    )
    service.cast_vote("round-correct", actor_id="peer-a", choice="agree")
    target = broker.get_target("round-correct")
    assert target is not None

    service.cast_vote(
        "round-correct",
        actor_id="peer-a",
        choice="disagree",
        expected_revision=target.revision,
    )

    corrected = broker.get_target("round-correct")
    assert corrected is not None
    assert corrected.state["votes"]["peer-a"]["choice"] == "disagree"
    assert corrected.state["quorum"]["counted_votes"] == 0
    assert corrected.state["quorum"]["recorded_votes"] == 1
    assert corrected.state["quorum"]["decisive_votes"] == 1
    assert corrected.state["quorum"]["reached"] is False
    assert corrected.state["phase"] == "voting"


def test_vote_rejected_once_quorum_reached(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.propose(
        round_id="round-closed",
        title="Choose",
        question="Which?",
        body="The body",
        proposer_id="peer-a",
        required_participants=("peer-a", "peer-b"),
        eligible_participants=("peer-a", "peer-b", "peer-c"),
        risk="normal",
        source_hash="sha256:test",
    )
    service.cast_vote("round-closed", actor_id="peer-a", choice="agree")
    service.cast_vote("round-closed", actor_id="peer-b", choice="agree")

    with pytest.raises(InvalidMutationError, match="closed"):
        service.cast_vote("round-closed", actor_id="peer-c", choice="agree")


def test_stale_vote_raises_stale_revision(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    service.propose(
        round_id="round-stale",
        title="Choose",
        question="Which?",
        body="The body",
        proposer_id="peer-a",
        required_participants=("peer-a", "peer-b"),
        eligible_participants=("peer-a", "peer-b"),
        risk="normal",
        source_hash="sha256:test",
    )
    service.cast_vote("round-stale", actor_id="peer-a", choice="agree")
    with pytest.raises(StaleRevisionError):
        service.cast_vote(
            "round-stale",
            actor_id="peer-b",
            choice="agree",
            expected_revision=1,
        )


def _quorum_round(service: ConsensusService, round_id: str = "round-ops") -> None:
    service.propose(round_id=round_id, title="Choose", question="Which?", body="body",
                   proposer_id="peer-a", required_participants=("peer-a", "peer-b"),
                   eligible_participants=("peer-a", "peer-b"), risk="normal", source_hash="sha256:test")
    service.cast_vote(round_id, actor_id="peer-a", choice="agree")
    service.cast_vote(round_id, actor_id="peer-b", choice="agree")


def test_final_call_ack_completes_and_resolves(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    _quorum_round(service)
    service.final_call_ack("round-ops", actor_id="peer-a", ack=True)
    service.final_call_ack("round-ops", actor_id="peer-b", ack=True)
    state = broker.get_target("round-ops").state  # type: ignore[union-attr]
    assert state["phase"] == "resolved" and state["resolution"]["decision_hash"]


def test_false_ack_leaves_final_call_and_timeout_escalates(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    _quorum_round(service)
    service.final_call_ack("round-ops", actor_id="peer-a", ack=False)
    service.mark_timeout("round-ops", "deadline exceeded")
    state = broker.get_target("round-ops").state  # type: ignore[union-attr]
    assert state["phase"] == "final_call" and state["escalation"]["tier"] == 0


def test_escalation_resolve_and_abandon_are_terminal(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.propose(round_id="round-escalate", title="x", question="q", body="b", proposer_id="peer-a",
                    required_participants=("peer-a",), eligible_participants=("peer-a",), risk="normal", source_hash="s")
    service.request_escalation("round-escalate", "needs human", "peer-a", 0, "human-tier-0")
    service.resolve("round-escalate", "approved", "human:one", "human decision")
    assert broker.get_target("round-escalate").state["status"] == "resolved"  # type: ignore[union-attr]
    with pytest.raises(InvalidMutationError):
        service.abandon("round-escalate", "late", "too late", "human:one")

    service.propose(round_id="round-abandon", title="x", question="q", body="b", proposer_id="peer-a",
                    required_participants=("peer-a",), eligible_participants=("peer-a",), risk="normal", source_hash="s")
    service.abandon("round-abandon", "cancelled", "cancel", "peer-a")
    assert broker.get_target("round-abandon").state["phase"] == "abandoned"  # type: ignore[union-attr]


def test_final_call_ack_and_resolve_emit_observable_effect_intents(tmp_path: Path) -> None:
    from peerhub.governance.contract import EffectOutcome

    service, broker = _service(tmp_path)
    _quorum_round(service, "round-effects")
    service.final_call_ack("round-effects", actor_id="peer-a", ack=True)
    service.final_call_ack("round-effects", actor_id="peer-b", ack=True)

    from collections.abc import Mapping
    pending = broker.recover_pending_effects()
    matching = [
        p for p in pending
        if isinstance(p.event.payload, Mapping)
        and p.event.payload.get("target_id") == "round-effects"
        and p.event.payload.get("effect_kind") == "consensus.resolved"
    ]
    assert len(matching) == 1
    effect = matching[0].event
    payload = effect.payload
    assert payload["effect_kind"] == "consensus.resolved"
    effect_data = payload["effect_payload"]
    assert effect_data["outcome"] == "approved"
    assert effect_data["final_call_complete"] is True
    assert effect_data["basis"] == "all final-call acknowledgements agree"

    receipts = service.process_consensus_effects("round-effects")
    assert len(receipts) == 1
    assert receipts[0].outcome == EffectOutcome.EFFECT_SUCCEEDED

    remaining = broker.recover_pending_effects()
    assert not any(
        isinstance(p.event.payload, Mapping)
        and p.event.payload.get("target_id") == "round-effects"
        and p.event.payload.get("effect_kind") == "consensus.resolved"
        for p in remaining
    )


def test_reject_on_dissent_and_abandon_emit_observable_effect_intents(tmp_path: Path) -> None:
    from collections.abc import Mapping
    service, broker = _service(tmp_path)
    service.propose(
        round_id="round-dissent",
        title="Dissent Round",
        question="Agree?",
        body="body",
        proposer_id="peer-a",
        required_participants=("peer-a", "peer-b"),
        eligible_participants=("peer-a", "peer-b"),
        risk="normal",
        source_hash="sha256:dissent",
    )
    service.cast_vote("round-dissent", actor_id="peer-a", choice="agree")
    service.cast_vote("round-dissent", actor_id="peer-b", choice="disagree")
    service.reject_on_dissent("round-dissent", rejected_by="peer-b", basis="disagree recorded")

    pending = broker.recover_pending_effects()
    dissent_effects = [
        p for p in pending
        if isinstance(p.event.payload, Mapping)
        and p.event.payload.get("target_id") == "round-dissent"
        and p.event.payload.get("effect_kind") == "consensus.resolved"
    ]
    assert len(dissent_effects) == 1
    dissent_payload = dissent_effects[0].event.payload
    assert dissent_payload["effect_kind"] == "consensus.resolved"
    assert dissent_payload["effect_payload"]["outcome"] == "rejected"
    assert dissent_payload["effect_payload"]["dissent"] is True

    service.propose(
        round_id="round-abandon-effect",
        title="Abandon Round",
        question="Abandon?",
        body="body",
        proposer_id="peer-a",
        required_participants=("peer-a",),
        eligible_participants=("peer-a",),
        risk="normal",
        source_hash="sha256:abandon",
    )
    service.abandon("round-abandon-effect", reason_code="CANCELLED", reason="user cancelled", abandoned_by="peer-a")
    pending = broker.recover_pending_effects()
    abandon_effects = [
        p for p in pending
        if isinstance(p.event.payload, Mapping)
        and p.event.payload.get("target_id") == "round-abandon-effect"
        and p.event.payload.get("effect_kind") == "consensus.abandoned"
    ]
    assert len(abandon_effects) == 1
    abandon_payload = abandon_effects[0].event.payload
    assert abandon_payload["effect_kind"] == "consensus.abandoned"
    assert abandon_payload["effect_payload"]["reason_code"] == "CANCELLED"

