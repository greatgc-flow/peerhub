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


def test_cast_vote_updates_votes_and_reaches_quorum(tmp_path: Path) -> None:
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
    assert target.state["phase"] == "quorum_reached"
    assert target.state["quorum"]["reached"] is True
    assert target.state["quorum"]["counted_votes"] == 2
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
    assert corrected.state["quorum"]["counted_votes"] == 1
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
