"""Unit tests for the pure CancellationLadder reducer state transitions."""

import pytest

from peerhub.core.execution import CancellationGrace
from peerhub.dispatch.contract import ProcessBirthIdentity
from peerhub.dispatch.process import (
    CancellationAction,
    CancellationDecision,
    CancellationLadder,
    CancellationStage,
    CancellationState,
    ObservationState,
    TreeProcessObservation,
)


def test_cancellation_ladder_custom_grace():
    grace = CancellationGrace(
        soft_cancel_grace_ms=3000,
        terminate_tree_grace_ms=1500,
    )
    ladder = CancellationLadder(grace=grace)
    assert ladder.grace.soft_cancel_grace_ms == 3000
    assert ladder.grace.terminate_tree_grace_ms == 1500

    state, decision = ladder.start(now_ms=100)
    assert state.stage is CancellationStage.SOFT_CANCEL
    assert state.deadline_ms == 3100
    assert decision.action is CancellationAction.SOFT_CANCEL
    assert decision.next_deadline_ms == 3100


def test_cancellation_ladder_5_step_escalation():
    """Test pure reducer escalation through all 5 states:
    SOFT_CANCEL -> TERMINATE_TREE -> KILL_TREE -> RECONCILE_TREE -> COMPLETED
    """
    ladder = CancellationLadder(
        grace=CancellationGrace(
            soft_cancel_grace_ms=5000,
            terminate_tree_grace_ms=2000,
        )
    )

    # Step 0: Start at now=0
    state, decision = ladder.start(now_ms=0)
    assert state.stage is CancellationStage.SOFT_CANCEL
    assert decision.action is CancellationAction.SOFT_CANCEL
    assert state.deadline_ms == 5000

    running_obs = [
        TreeProcessObservation(
            identity=ProcessBirthIdentity(pid=10, process_creation_time=1),
            state=ObservationState.RUNNING,
        )
    ]

    # Step 1: Tick before deadline (now=3000) -> stay in SOFT_CANCEL
    state, decision = ladder.step(state, observations=running_obs, now_ms=3000)
    assert state.stage is CancellationStage.SOFT_CANCEL
    assert decision.action is CancellationAction.SOFT_CANCEL

    # Step 2: Tick at/after deadline (now=5000) -> escalate to TERMINATE_TREE
    state, decision = ladder.step(state, observations=running_obs, now_ms=5000)
    assert state.stage is CancellationStage.TERMINATE_TREE
    assert decision.action is CancellationAction.TERMINATE_TREE
    assert state.deadline_ms == 7000  # 5000 + 2000

    # Step 3: Tick at/after terminate deadline (now=7000) -> escalate to KILL_TREE
    state, decision = ladder.step(state, observations=running_obs, now_ms=7000)
    assert state.stage is CancellationStage.KILL_TREE
    assert decision.action is CancellationAction.KILL_TREE
    assert state.deadline_ms == 8000  # 7000 + 1000

    # Step 4: Tick at/after kill deadline (now=8000) -> escalate to RECONCILE_TREE
    state, decision = ladder.step(state, observations=running_obs, now_ms=8000)
    assert state.stage is CancellationStage.RECONCILE_TREE
    assert decision.action is CancellationAction.RECONCILE_TREE
    assert decision.unresolved_identities == (10,)
    assert decision.all_terminated is False

    # Step 5: Tick after RECONCILE_TREE -> COMPLETED
    state, decision = ladder.step(state, observations=running_obs, now_ms=9000)
    assert state.stage is CancellationStage.COMPLETED
    assert decision.action is CancellationAction.NONE
    assert decision.unresolved_identities == (10,)
    assert decision.all_terminated is False


def test_cancellation_ladder_early_completion_on_all_terminated():
    ladder = CancellationLadder()
    state, decision = ladder.start(now_ms=0)
    assert state.stage is CancellationStage.SOFT_CANCEL

    term_obs = [
        TreeProcessObservation(
            identity=ProcessBirthIdentity(pid=10, process_creation_time=1),
            state=ObservationState.TERMINATED,
        )
    ]

    state, decision = ladder.step(state, observations=term_obs, now_ms=100)
    assert state.stage is CancellationStage.COMPLETED
    assert decision.action is CancellationAction.NONE
    assert decision.all_terminated is True
    assert decision.unresolved_identities == ()
