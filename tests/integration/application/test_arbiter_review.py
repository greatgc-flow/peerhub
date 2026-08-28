from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

from peerhub.application.arbiter_review import (
    ARBITER_BUDGET_TARGET_ID,
    ArbiterBudgetExceeded,
    ArbiterBudgetManager,
    ArbiterReviewCoordinator,
    FinalArbiterPolicy,
    build_condensed_arbiter_prompt,
    classify_consensus_dissent,
    load_final_arbiter_policy,
)
from peerhub.application.direct_ask import DirectAskRequest, DirectAskResult
from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import RecordNotFoundError
from peerhub.core.identity import AuthenticatedSubject
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.contract import RequestState
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus import ConsensusService
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import SequentialIdSource


class FixedClock(Clock):
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def now(self) -> int:
        return self.value


class FakeExecutor:
    def __init__(self, responses: Iterable[str]) -> None:
        self._responses = iter(responses)
        self.requests: list[DirectAskRequest] = []

    def __call__(
        self,
        request: DirectAskRequest,
        *,
        clock: Clock,
        ids: IdSource,
        authenticated_subject: AuthenticatedSubject,
    ) -> DirectAskResult:
        del clock, ids, authenticated_subject
        self.requests.append(request)
        return DirectAskResult(
            command_id=f"ask-command-{len(self.requests)}",
            attempt_id=f"ask-attempt-{len(self.requests)}",
            peer_kind="cc",
            profile_id="cc.deepthink",
            response_text=next(self._responses),
            request_state=RequestState.SUCCEEDED_VERIFIED,
            error_code=None,
            execution_certainty=None,
        )


def _services(
    tmp_path: Path,
) -> tuple[
    ConsensusService,
    GovernanceBroker,
    FixedClock,
    SequentialIdSource,
]:
    clock = FixedClock()
    ids = SequentialIdSource()
    store = SqliteStateStore(
        tmp_path / "arbiter.sqlite3",
        workspace_home_id="arbiter-test",
    )
    store.initialize()
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    consensus = ConsensusService(broker, clock=clock, ids=ids)
    return consensus, broker, clock, ids


def _resolved_round(
    consensus: ConsensusService,
    round_id: str,
    *,
    choices: tuple[str | None, str | None],
) -> None:
    consensus.propose(
        round_id=round_id,
        title="Deployment choice",
        question="Should we deploy?",
        body="Review the rollout evidence and decide.",
        proposer_id="peer-a",
        required_participants=("peer-a", "peer-b"),
        eligible_participants=("peer-a", "peer-b"),
        risk="normal",
        source_hash="sha256:test",
    )
    for actor_id, choice in zip(("peer-a", "peer-b"), choices):
        if choice is not None:
            consensus.cast_vote(
                round_id,
                actor_id=actor_id,
                choice=choice,
            )
    target = consensus.get_target(round_id)
    assert target is not None
    if target.state["phase"] != "quorum_reached":
        consensus.request_escalation(
            round_id,
            "missing required vote",
            "peer-a",
            0,
            "human-tier-0",
        )
    consensus.resolve(
        round_id,
        "approved",
        "human:reviewer",
        "manual resolution",
    )


def _enabled_policy() -> FinalArbiterPolicy:
    return FinalArbiterPolicy(enabled=True)


def test_config_loader_uses_working_configured_candidate(tmp_path: Path) -> None:
    config_dir = tmp_path / ".peerhub"
    config_dir.mkdir()
    (config_dir / "arbiter.json").write_text(
        """
        {
          "enabled": true,
          "candidate": {
            "peer_name": "cc",
            "profile_id": "cc.effort"
          },
          "triggers": ["dissent"],
          "max_invocations": 3,
          "window_seconds": 900
        }
        """,
        encoding="utf-8",
    )

    assert load_final_arbiter_policy(tmp_path) == FinalArbiterPolicy(
        enabled=True,
        peer_name="cc",
        profile_id="cc.effort",
        triggers=("dissent",),
        max_invocations=3,
        window_seconds=900,
    )


def _coordinator(
    *,
    tmp_path: Path,
    consensus: ConsensusService,
    broker: GovernanceBroker,
    clock: Clock,
    ids: IdSource,
    executor: FakeExecutor,
    policy: FinalArbiterPolicy | None,
) -> ArbiterReviewCoordinator:
    return ArbiterReviewCoordinator(
        broker,
        consensus,
        workspace_root=tmp_path,
        clock=clock,
        ids=ids,
        authenticated_subject=AuthenticatedSubject(
            "local-cli:test-user",
            "test",
        ),
        executor=executor,
        configured_policy=policy,
    )


def test_missing_round_is_a_real_error(tmp_path: Path) -> None:
    consensus, broker, clock, ids = _services(tmp_path)
    coordinator = _coordinator(
        tmp_path=tmp_path,
        consensus=consensus,
        broker=broker,
        clock=clock,
        ids=ids,
        executor=FakeExecutor(("VERDICT: APPROVE",)),
        policy=_enabled_policy(),
    )

    with pytest.raises(RecordNotFoundError, match="missing"):
        coordinator.review("missing")


def test_absent_config_is_disabled_without_state_or_executor_effects(
    tmp_path: Path,
) -> None:
    consensus, broker, clock, ids = _services(tmp_path)
    _resolved_round(consensus, "round-disabled", choices=("agree", "disagree"))
    before = consensus.get_target("round-disabled")
    assert before is not None
    executor = FakeExecutor(("VERDICT: APPROVE",))
    coordinator = _coordinator(
        tmp_path=tmp_path,
        consensus=consensus,
        broker=broker,
        clock=clock,
        ids=ids,
        executor=executor,
        policy=None,
    )

    assert coordinator.review("round-disabled") == {
        "fired": False,
        "reason": "arbiter_disabled",
    }
    after = consensus.get_target("round-disabled")
    assert after is not None and after.revision == before.revision
    assert executor.requests == []
    assert broker.get_target(ARBITER_BUDGET_TARGET_ID) is None
    assert broker.list_targets("arbiter-review", "round-disabled") == ()
    assert broker.list_targets("arbiter-opinion", "round-disabled") == ()


def test_unanimous_round_does_not_trigger(tmp_path: Path) -> None:
    consensus, broker, clock, ids = _services(tmp_path)
    _resolved_round(consensus, "round-unanimous", choices=("agree", "agree"))
    executor = FakeExecutor(("VERDICT: APPROVE",))
    coordinator = _coordinator(
        tmp_path=tmp_path,
        consensus=consensus,
        broker=broker,
        clock=clock,
        ids=ids,
        executor=executor,
        policy=_enabled_policy(),
    )

    assert coordinator.review("round-unanimous") == {
        "fired": False,
        "reason": "no_dissent",
    }
    assert executor.requests == []
    assert broker.get_target(ARBITER_BUDGET_TARGET_ID) is None


def test_dissent_covers_disagree_and_missing_required_vote(
    tmp_path: Path,
) -> None:
    consensus, _, _, _ = _services(tmp_path)
    _resolved_round(consensus, "round-disagree", choices=("agree", "disagree"))
    disagree = consensus.get_target("round-disagree")
    assert disagree is not None
    assert [
        (item.voter_id, item.reason, item.choice)
        for item in classify_consensus_dissent(disagree.state)
    ] == [("peer-b", "non_agree", "disagree")]

    _resolved_round(consensus, "round-missing", choices=("agree", None))
    missing = consensus.get_target("round-missing")
    assert missing is not None
    findings = classify_consensus_dissent(missing.state)
    assert [(item.voter_id, item.reason, item.choice) for item in findings] == [
        ("peer-b", "no_vote", "no_vote")
    ]
    prompt = build_condensed_arbiter_prompt(missing.state, findings)
    assert prompt.splitlines()[0].startswith("Return exactly one first line")
    assert "peer-a, peer-b" in prompt
    assert "peer-b: no_vote" in prompt
    assert len(prompt) <= 1200


def test_sixth_budget_reservation_is_side_effect_free(tmp_path: Path) -> None:
    _, broker, clock, ids = _services(tmp_path)
    manager = ArbiterBudgetManager(
        broker,
        clock=clock,
        ids=ids,
        actor_id="local-cli:test-user",
    )
    policy = _enabled_policy()
    for index in range(5):
        manager.reserve(
            round_id=f"round-{index}",
            review_id=f"review-{index}",
            policy=policy,
        )
    before = broker.get_target(ARBITER_BUDGET_TARGET_ID)
    assert before is not None
    assert before.state["count"] == 5

    with pytest.raises(ArbiterBudgetExceeded, match="budget_exceeded"):
        manager.reserve(
            round_id="round-6",
            review_id="review-6",
            policy=policy,
        )

    after = broker.get_target(ARBITER_BUDGET_TARGET_ID)
    assert after is not None
    assert after.revision == before.revision
    assert after.state == before.state


def test_budget_window_resets_only_at_anchored_deadline(tmp_path: Path) -> None:
    _, broker, clock, ids = _services(tmp_path)
    manager = ArbiterBudgetManager(
        broker,
        clock=clock,
        ids=ids,
        actor_id="local-cli:test-user",
    )
    policy = _enabled_policy()
    manager.reserve(round_id="round-a", review_id="review-a", policy=policy)
    clock.value = 18_999
    manager.reserve(round_id="round-b", review_id="review-b", policy=policy)
    before_rollover = broker.get_target(ARBITER_BUDGET_TARGET_ID)
    assert before_rollover is not None
    assert before_rollover.state["window_start"] == 1_000
    assert before_rollover.state["count"] == 2

    clock.value = 19_000
    manager.reserve(round_id="round-c", review_id="review-c", policy=policy)
    after_rollover = broker.get_target(ARBITER_BUDGET_TARGET_ID)
    assert after_rollover is not None
    assert after_rollover.state["window_start"] == 19_000
    assert after_rollover.state["count"] == 1
    assert len(after_rollover.state["slots"]) == 1


def test_verified_success_records_all_evidence_and_keeps_resolution(
    tmp_path: Path,
) -> None:
    consensus, broker, clock, ids = _services(tmp_path)
    _resolved_round(consensus, "round-success", choices=("agree", "disagree"))
    before = consensus.get_target("round-success")
    assert before is not None
    resolution = before.state["resolution"]
    executor = FakeExecutor(("\nVERDICT: approve\nReasoning follows.",))
    coordinator = _coordinator(
        tmp_path=tmp_path,
        consensus=consensus,
        broker=broker,
        clock=clock,
        ids=ids,
        executor=executor,
        policy=_enabled_policy(),
    )

    result = coordinator.review("round-success")

    assert result["fired"] is True
    assert result["parsed_verdict"] == "APPROVE"
    assert result["canonical"] is True
    request = broker.get_target(str(result["request_target_id"]))
    opinion = broker.get_target(str(result["opinion_target_id"]))
    budget = broker.get_target(ARBITER_BUDGET_TARGET_ID)
    round_target = consensus.get_target("round-success")
    assert request is not None and request.state["candidate"] == {
        "peer_name": "cc",
        "profile_id": "cc.deepthink",
    }
    assert request.state["dissent"][0]["choice"] == "disagree"
    assert opinion is not None
    assert opinion.state["dispatch"]["state"] == "SUCCEEDED_VERIFIED"
    assert opinion.state["parsed_verdict"] == "APPROVE"
    assert budget is not None
    assert budget.state["count"] == 1
    assert budget.state["slots"][0]["state"] == "CONSUMED"
    assert round_target is not None
    assert round_target.state["resolution"] == resolution
    assert round_target.state["arbiter_opinion"]["opinion_target_id"] == result[
        "opinion_target_id"
    ]
    assert executor.requests[0].required_capability_tier is CapabilityTier.READ_ONLY


def test_garbled_first_line_records_noncanonical_opinion(
    tmp_path: Path,
) -> None:
    consensus, broker, clock, ids = _services(tmp_path)
    _resolved_round(consensus, "round-garbled", choices=("agree", "disagree"))
    before = consensus.get_target("round-garbled")
    assert before is not None
    executor = FakeExecutor(("Here is my analysis.\nVERDICT: APPROVE",))
    coordinator = _coordinator(
        tmp_path=tmp_path,
        consensus=consensus,
        broker=broker,
        clock=clock,
        ids=ids,
        executor=executor,
        policy=_enabled_policy(),
    )

    result = coordinator.review("round-garbled")

    assert result["fired"] is True
    assert result["parsed_verdict"] is None
    assert result["canonical"] is False
    opinion = broker.get_target(str(result["opinion_target_id"]))
    assert opinion is not None and opinion.state["parsed_verdict"] is None
    after = consensus.get_target("round-garbled")
    assert after is not None
    assert after.revision == before.revision
    assert "arbiter_opinion" not in after.state
    budget = broker.get_target(ARBITER_BUDGET_TARGET_ID)
    assert budget is not None
    assert budget.state["slots"][0]["state"] == "CONSUMED"
