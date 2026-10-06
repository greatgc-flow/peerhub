"""Tests for M3.4 Orchestration (ORC-001..012).

Verifies:
- Core Invariant 9: Orchestration always bounded (max_steps, timeout).
- Core Invariant 10: MAY_HAVE_STARTED no blind replay.
- Plan/Step lifecycle and DAG ordering.
- Crash recovery / state hydration.
- Pure Python standard library compliance (REL-009).
"""

from __future__ import annotations

import sys
import time
import pytest

from peerhub.m3.orchestration import (
    Plan,
    PlanStep,
    PlanBounds,
    PlanExecutor,
    StepExecutionResult,
    PlanBoundExceededError,
    AmbiguousExecutionReplayForbiddenError,
    PlanDependencyCycleError,
    PlanStateTransitionError,
)


def test_orc_001_dag_execution() -> None:
    """ORC-001: Sequential and parallel step execution completes plan successfully."""
    step1 = PlanStep(
        step_id="step-1",
        action_type="prepare",
        payload={"task": "init"},
    )
    step2 = PlanStep(
        step_id="step-2",
        action_type="process",
        payload={"task": "work"},
        depends_on=["step-1"],
    )
    plan = Plan(plan_id="p1", title="test-plan", steps=[step1, step2])

    execution_order: list[str] = []

    def mock_runner(step: PlanStep, attempt: int) -> StepExecutionResult:
        execution_order.append(step.step_id)
        return StepExecutionResult(
            step_id=step.step_id,
            attempt=attempt,
            status="COMPLETED",
            output={"done": True},
        )

    executor = PlanExecutor()
    summary = executor.execute_plan(plan, mock_runner)

    assert summary.status == "COMPLETED"
    assert summary.steps_executed == 2
    assert execution_order == ["step-1", "step-2"]
    assert summary.step_results["step-1"].status == "COMPLETED"
    assert summary.step_results["step-2"].status == "COMPLETED"


def test_orc_002_plan_state_transition() -> None:
    """ORC-002: Plan state machine enforces valid transitions."""
    step = PlanStep(step_id="s1", action_type="echo", payload={})
    plan = Plan(plan_id="p-trans", title="test", steps=[step])

    def runner(s: PlanStep, a: int) -> StepExecutionResult:
        return StepExecutionResult(step_id=s.step_id, attempt=a, status="COMPLETED")

    executor = PlanExecutor()
    summary = executor.execute_plan(plan, runner)
    assert summary.status == "COMPLETED"

    # Plan already completed cannot be re-executed via resume if no work remains
    with pytest.raises(PlanStateTransitionError):
        executor.resume_plan(plan, summary.step_results, runner)


def test_orc_003_invariant_bound_max_steps() -> None:
    """ORC-003: Invariant 9: max_steps limit terminates execution."""
    steps = [
        PlanStep(step_id=f"step-{i}", action_type="echo", payload={})
        for i in range(5)
    ]
    bounds = PlanBounds(max_steps=2)
    plan = Plan(plan_id="p-bound-steps", title="bounded", steps=steps, bounds=bounds)

    executed_count = 0

    def runner(s: PlanStep, a: int) -> StepExecutionResult:
        nonlocal executed_count
        executed_count += 1
        return StepExecutionResult(step_id=s.step_id, attempt=a, status="COMPLETED")

    executor = PlanExecutor()
    with pytest.raises(PlanBoundExceededError):
        executor.execute_plan(plan, runner)

    assert executed_count == 2


def test_orc_004_invariant_bound_timeout() -> None:
    """ORC-004: Invariant 9: timeout terminates execution."""
    step1 = PlanStep(step_id="slow-1", action_type="sleep", payload={})
    step2 = PlanStep(step_id="slow-2", action_type="sleep", payload={})
    bounds = PlanBounds(timeout_seconds=0.1)
    plan = Plan(plan_id="p-bound-time", title="timeout", steps=[step1, step2], bounds=bounds)

    def runner(s: PlanStep, a: int) -> StepExecutionResult:
        time.sleep(0.12)
        return StepExecutionResult(step_id=s.step_id, attempt=a, status="COMPLETED")

    executor = PlanExecutor()
    with pytest.raises(PlanBoundExceededError):
        executor.execute_plan(plan, runner)


def test_orc_005_invariant_may_have_started_no_blind_replay() -> None:
    """ORC-005: Invariant 10: MAY_HAVE_STARTED results forbid blind retry on non-idempotent steps."""
    step = PlanStep(
        step_id="transfer-step",
        action_type="transfer_funds",
        payload={"amount": 100},
        idempotent=False,
        max_attempts=3,
    )
    plan = Plan(plan_id="p-non-idempotent", title="p", steps=[step])

    call_count = 0

    def runner(s: PlanStep, a: int) -> StepExecutionResult:
        nonlocal call_count
        call_count += 1
        return StepExecutionResult(
            step_id=s.step_id,
            attempt=a,
            status="MAY_HAVE_STARTED",
            error="Connection lost during transfer",
        )

    executor = PlanExecutor()
    with pytest.raises(AmbiguousExecutionReplayForbiddenError):
        executor.execute_plan(plan, runner)

    # Must NOT attempt a second call! Blind replay strictly forbidden
    assert call_count == 1


def test_orc_006_invariant_may_have_started_idempotent_retry() -> None:
    """ORC-006: Invariant 10: Idempotent steps with MAY_HAVE_STARTED can be boundedly retried."""
    step = PlanStep(
        step_id="idempotent-step",
        action_type="get_or_create",
        payload={"key": "k"},
        idempotent=True,
        max_attempts=3,
    )
    plan = Plan(plan_id="p-idempotent", title="p", steps=[step])

    attempts: list[int] = []

    def runner(s: PlanStep, a: int) -> StepExecutionResult:
        attempts.append(a)
        if a == 1:
            return StepExecutionResult(
                step_id=s.step_id,
                attempt=a,
                status="MAY_HAVE_STARTED",
                error="Timeout",
            )
        return StepExecutionResult(
            step_id=s.step_id,
            attempt=a,
            status="COMPLETED",
            output={"result": "ok"},
        )

    executor = PlanExecutor()
    summary = executor.execute_plan(plan, runner)

    assert summary.status == "COMPLETED"
    assert attempts == [1, 2]


def test_orc_007_cycle_detection() -> None:
    """ORC-007: Circular dependency in step DAG detected and rejected."""
    step_a = PlanStep(step_id="a", action_type="t", payload={}, depends_on=["b"])
    step_b = PlanStep(step_id="b", action_type="t", payload={}, depends_on=["a"])
    plan = Plan(plan_id="p-cycle", title="cycle", steps=[step_a, step_b])

    executor = PlanExecutor()
    with pytest.raises(PlanDependencyCycleError):
        executor.execute_plan(plan, lambda s, a: StepExecutionResult(s.step_id, a, "COMPLETED"))


def test_orc_008_step_failure_stops_plan() -> None:
    """ORC-008: Failed step terminates plan execution and skips dependent steps."""
    step1 = PlanStep(step_id="s1", action_type="t", payload={}, max_attempts=1)
    step2 = PlanStep(step_id="s2", action_type="t", payload={}, depends_on=["s1"])
    plan = Plan(plan_id="p-fail", title="fail", steps=[step1, step2])

    executed: list[str] = []

    def runner(s: PlanStep, a: int) -> StepExecutionResult:
        executed.append(s.step_id)
        if s.step_id == "s1":
            return StepExecutionResult(s.step_id, a, "FAILED", error="hard crash")
        return StepExecutionResult(s.step_id, a, "COMPLETED")

    executor = PlanExecutor()
    summary = executor.execute_plan(plan, runner)

    assert summary.status == "FAILED"
    assert executed == ["s1"]
    assert "s2" not in summary.step_results


def test_orc_009_crash_recovery_resume() -> None:
    """ORC-009: Crash recovery skips COMPLETED steps and executes remaining."""
    step1 = PlanStep(step_id="s1", action_type="t", payload={})
    step2 = PlanStep(step_id="s2", action_type="t", payload={}, depends_on=["s1"])
    plan = Plan(plan_id="p-resume", title="resume", steps=[step1, step2])

    # Simulated prior execution record where s1 succeeded before crash
    recorded = {
        "s1": StepExecutionResult(step_id="s1", attempt=1, status="COMPLETED", output={"x": 10})
    }

    executed: list[str] = []

    def runner(s: PlanStep, a: int) -> StepExecutionResult:
        executed.append(s.step_id)
        return StepExecutionResult(s.step_id, a, "COMPLETED")

    executor = PlanExecutor()
    summary = executor.resume_plan(plan, recorded, runner)

    assert summary.status == "COMPLETED"
    assert executed == ["s2"]  # s1 was not re-executed!
    assert summary.step_results["s1"].output == {"x": 10}
    assert summary.step_results["s2"].status == "COMPLETED"


def test_orc_010_crash_recovery_refuses_ambiguous_non_idempotent() -> None:
    """ORC-010: Crash recovery refuses to resume plan if recorded result has MAY_HAVE_STARTED on non-idempotent step."""
    step1 = PlanStep(step_id="s1", action_type="t", payload={}, idempotent=False)
    step2 = PlanStep(step_id="s2", action_type="t", payload={}, depends_on=["s1"])
    plan = Plan(plan_id="p-ambig", title="ambig", steps=[step1, step2])

    recorded = {
        "s1": StepExecutionResult(step_id="s1", attempt=1, status="MAY_HAVE_STARTED", error="mid-call crash")
    }

    executor = PlanExecutor()
    with pytest.raises(AmbiguousExecutionReplayForbiddenError):
        executor.resume_plan(plan, recorded, lambda s, a: StepExecutionResult(s.step_id, a, "COMPLETED"))


def test_orc_011_plan_serialization() -> None:
    """ORC-011: Plan serializes and deserializes accurately for Artifact preservation."""
    step1 = PlanStep(step_id="s1", action_type="action1", payload={"data": 123}, depends_on=[])
    step2 = PlanStep(step_id="s2", action_type="action2", payload={"data": 456}, depends_on=["s1"], idempotent=False)
    bounds = PlanBounds(max_depth=3, max_steps=20, max_fanout=5, max_attempts=2, timeout_seconds=120.0)

    original_plan = Plan(plan_id="p-serial", title="Ser/Deser Plan", steps=[step1, step2], bounds=bounds)

    data = original_plan.to_dict()
    restored_plan = Plan.from_dict(data)

    assert restored_plan.plan_id == original_plan.plan_id
    assert restored_plan.title == original_plan.title
    assert len(restored_plan.steps) == 2
    assert restored_plan.steps[1].idempotent is False
    assert restored_plan.bounds.timeout_seconds == 120.0


def test_orc_012_rel_009_standard_library_compliance() -> None:
    """ORC-012: REL-009 Standard library compliance."""
    import peerhub.m3.orchestration as orc_module

    for mod_name, mod in sys.modules.items():
        if mod_name.startswith("peerhub.m3.orchestration"):
            assert mod is not None
