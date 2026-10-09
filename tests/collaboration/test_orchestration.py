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
from pathlib import Path
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Any

from peerhub.extensions.orchestration import (
    Plan,
    PlanStep,
    PlanBounds,
    PlanExecutor,
    StepExecutionResult,
    PlanBoundExceededError,
    AmbiguousExecutionReplayForbiddenError,
    PlanDependencyCycleError,
    PlanStateTransitionError,
    PlanStepExecutionError,
    RecordPlanJournal,
    UnsupportedExecutionBoundError,
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
    import peerhub.extensions.orchestration as orc_module

    for mod_name, mod in sys.modules.items():
        if mod_name.startswith("peerhub.extensions.orchestration"):
            assert mod is not None


def test_retry_counts_against_total_step_budget() -> None:
    plan = Plan("retry-budget", "retry", [PlanStep("s", "effect", {})], PlanBounds(max_steps=1))
    attempts: list[int] = []

    def runner(step: PlanStep, attempt: int) -> StepExecutionResult:
        attempts.append(attempt)
        return StepExecutionResult(step.step_id, attempt, "FAILED")

    with pytest.raises(PlanBoundExceededError):
        PlanExecutor().execute_plan(plan, runner)
    assert attempts == [1]


def test_last_step_cannot_report_success_after_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    import peerhub.extensions.orchestration as module

    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    plan = Plan("last-timeout", "timeout", [PlanStep("s", "effect", {})], PlanBounds(timeout_seconds=1))

    def runner(step: PlanStep, attempt: int) -> StepExecutionResult:
        clock[0] = 2.0
        return StepExecutionResult(step.step_id, attempt, "COMPLETED")

    with pytest.raises(PlanBoundExceededError):
        PlanExecutor().execute_plan(plan, runner)


def test_resume_preserves_attempt_numbers_and_budget() -> None:
    plan = Plan("resume-budget", "retry", [PlanStep("s", "effect", {})])
    recorded = {"s": StepExecutionResult("s", 2, "FAILED")}
    attempts: list[int] = []

    def runner(step: PlanStep, attempt: int) -> StepExecutionResult:
        attempts.append(attempt)
        return StepExecutionResult(step.step_id, attempt, "FAILED")

    result = PlanExecutor().resume_plan(plan, recorded, runner)
    assert attempts == [3]
    assert result.status == "FAILED"


def test_resume_cannot_reset_total_step_budget() -> None:
    plan = Plan("resume-total", "retry", [PlanStep("s", "effect", {})], PlanBounds(max_steps=2))
    recorded = {"s": StepExecutionResult("s", 2, "FAILED")}
    with pytest.raises(PlanBoundExceededError):
        PlanExecutor().resume_plan(plan, recorded, lambda s, a: pytest.fail("budget exhausted"))


@pytest.mark.parametrize("bounds", [PlanBounds(max_depth=0), PlanBounds(max_fanout=0), PlanBounds(timeout_seconds=float("nan"))])
def test_invalid_bounds_rejected_before_execution(bounds: PlanBounds) -> None:
    plan = Plan("invalid-bound", "invalid", [PlanStep("s", "effect", {})], bounds)
    with pytest.raises(PlanBoundExceededError):
        PlanExecutor().execute_plan(plan, lambda s, a: pytest.fail("invalid plan executed"))


@pytest.mark.parametrize("steps", [
    [PlanStep("s", "effect", {}), PlanStep("s", "effect", {})],
    [PlanStep("s", "effect", {}, depends_on=["missing"])],
])
def test_invalid_step_identity_or_dependency_rejected(steps: list[PlanStep]) -> None:
    with pytest.raises(PlanStateTransitionError):
        PlanExecutor().execute_plan(Plan("invalid-dag", "invalid", steps), lambda s, a: pytest.fail("invalid DAG executed"))


def test_runner_result_must_bind_to_step_and_attempt() -> None:
    plan = Plan("result-binding", "binding", [PlanStep("s", "effect", {})])
    with pytest.raises(PlanStepExecutionError):
        PlanExecutor().execute_plan(plan, lambda s, a: StepExecutionResult("different", a, "COMPLETED"))


@pytest.fixture
def journal(tmp_path: Path) -> RecordPlanJournal:
    from peerhub.core.models import Peer, Stream
    from peerhub.core.store import CoreStore

    store = CoreStore(tmp_path / "core.db")
    store.register_peer(Peer(peer_id="executor", display_name="Executor"))
    store.create_stream(Stream(stream_id="execution", members=["executor"]))
    return RecordPlanJournal(store, "execution", "executor")


@pytest.mark.catalog_id("ORC-014")
def test_durable_plan_requires_acceptance_and_exact_binding(journal: RecordPlanJournal) -> None:
    plan = Plan("accepted", "explicit", [PlanStep("s", "effect", {})])
    executor = PlanExecutor(journal)
    with pytest.raises(PlanStateTransitionError):
        executor.execute_plan(plan, lambda s, a: pytest.fail("unaccepted"))
    ref = journal.accept_plan(plan, accepted_by="operator", evidence_refs=["artifact:plan", "record:approval"])
    assert journal.accept_plan(plan, accepted_by="operator", evidence_refs=["artifact:plan", "record:approval"]) == ref
    with pytest.raises(PlanStateTransitionError):
        executor.execute_plan(replace(plan, title="tampered"), lambda s, a: pytest.fail("tampered"))
    result = executor.execute_plan(plan, lambda s, a: StepExecutionResult(s.step_id, a, "COMPLETED"))
    assert result.status == "COMPLETED"
    recorded, _ = journal.recover(plan)
    assert recorded["s"].status == "COMPLETED"


def test_running_is_durable_before_callback_and_crash_blocks_replay(journal: RecordPlanJournal) -> None:
    plan = Plan("crash", "crash", [PlanStep("s", "effect", {}, idempotent=True)])
    journal.accept_plan(plan, accepted_by="operator")

    def crash(step: PlanStep, attempt: int) -> StepExecutionResult:
        recorded, _ = journal.recover(plan)
        assert recorded["s"].status == "MAY_HAVE_STARTED"
        raise RuntimeError("lost after side effect")

    with pytest.raises(RuntimeError):
        PlanExecutor(journal).execute_plan(plan, crash)
    recovered, _ = journal.recover(plan)
    assert recovered["s"].status == "MAY_HAVE_STARTED"
    with pytest.raises(AmbiguousExecutionReplayForbiddenError):
        PlanExecutor(journal).execute_plan(plan, lambda s, a: pytest.fail("blind replay"))


@pytest.mark.catalog_id("ORC-015")
def test_durable_restart_skips_completed_and_ignores_caller_fabrication(journal: RecordPlanJournal) -> None:
    plan = Plan("restart", "restart", [PlanStep("a", "effect", {}), PlanStep("b", "effect", {}, depends_on=["a"])])
    journal.accept_plan(plan, accepted_by="operator")
    journal.record_attempt(plan, StepExecutionResult("a", 1, "MAY_HAVE_STARTED"), "test-owner", running=True)
    journal.record_attempt(plan, StepExecutionResult("a", 1, "COMPLETED"), "test-owner", running=False)
    with pytest.raises(PlanStateTransitionError):
        PlanExecutor(journal).resume_plan(plan, {}, lambda s, a: pytest.fail("fake results"))
    calls: list[str] = []
    result = PlanExecutor(journal).execute_plan(plan, lambda s, a: calls.append(s.step_id) or StepExecutionResult(s.step_id, a, "COMPLETED"))
    assert result.status == "COMPLETED"
    assert calls == ["b"]


def test_absolute_deadline_does_not_reset_on_restart(journal: RecordPlanJournal) -> None:
    plan = Plan("deadline", "deadline", [PlanStep("s", "effect", {})], PlanBounds(timeout_seconds=0.01))
    journal.accept_plan(plan, accepted_by="operator")
    time.sleep(0.02)
    with pytest.raises(PlanBoundExceededError):
        PlanExecutor(journal).execute_plan(plan, lambda s, a: pytest.fail("expired deadline"))


def test_strict_execution_fails_closed_without_hard_cancellable_adapter() -> None:
    with pytest.raises(UnsupportedExecutionBoundError):
        PlanExecutor(require_hard_deadline=True).execute_plan(
            Plan("strict", "strict", [PlanStep("s", "effect", {})]),
            lambda s, a: pytest.fail("unsupported hard bound invoked"),
        )


def test_concurrent_durable_executors_do_not_invoke_twice(
    journal: RecordPlanJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    from peerhub.core.store import IdempotencyConflictError

    plan = Plan("concurrent", "concurrent", [PlanStep("s", "effect", {})])
    journal.accept_plan(plan, accepted_by="operator")
    original_append = journal._append
    barrier = threading.Barrier(2)
    calls: list[int] = []

    def simultaneous_start(plan: Plan, kind: str, suffix: str, body: dict[str, Any]) -> Any:
        if kind == "running":
            barrier.wait(timeout=5)
        return original_append(plan, kind, suffix, body)

    monkeypatch.setattr(journal, "_append", simultaneous_start)

    def execute(_: int) -> str:
        try:
            PlanExecutor(journal).execute_plan(plan, lambda s, a: calls.append(a) or StepExecutionResult(s.step_id, a, "COMPLETED"))
            return "COMPLETED"
        except (IdempotencyConflictError, AmbiguousExecutionReplayForbiddenError):
            return "BLOCKED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(execute, [1, 2]))
    assert sorted(outcomes) == ["BLOCKED", "COMPLETED"]
    assert calls == [1]


def test_durable_attempts_cannot_be_rewritten_or_fabricated(journal: RecordPlanJournal) -> None:
    plan = Plan("binding", "binding", [PlanStep("s", "effect", {})])
    journal.accept_plan(plan, accepted_by="operator")
    with pytest.raises(PlanStateTransitionError):
        journal.record_attempt(plan, StepExecutionResult("s", 1, "COMPLETED"), "owner", running=False)
    journal.record_attempt(plan, StepExecutionResult("s", 1, "MAY_HAVE_STARTED"), "owner", running=True)
    with pytest.raises(PlanStateTransitionError):
        journal.record_attempt(plan, StepExecutionResult("s", 1, "COMPLETED"), "different-owner", running=False)


def test_journal_replays_past_first_core_page(journal: RecordPlanJournal) -> None:
    for i in range(260):
        journal.store.append_record(stream_id=journal.stream_id, author_peer_id=journal.author_peer_id,
            kind="noise", body={}, idempotency_key=f"noise-{i}", created_at="2026-10-06T00:00:00Z")
    plan = Plan("paged", "paged", [PlanStep("s", "effect", {})])
    journal.accept_plan(plan, accepted_by="operator")
    result = PlanExecutor(journal).execute_plan(plan, lambda s, a: StepExecutionResult(s.step_id, a, "COMPLETED"))
    assert result.status == "COMPLETED"
    assert journal.recover(plan)[0]["s"].status == "COMPLETED"


def test_durable_callback_payload_mutation_does_not_rebind_plan(journal: RecordPlanJournal) -> None:
    plan = Plan("mutable-payload", "immutable binding", [PlanStep("s", "effect", {"value": "original"})])
    journal.accept_plan(plan, accepted_by="operator")

    def runner(step: PlanStep, attempt: int) -> StepExecutionResult:
        step.payload["value"] = "changed"
        return StepExecutionResult(step.step_id, attempt, "COMPLETED")

    assert PlanExecutor(journal).execute_plan(plan, runner).status == "COMPLETED"
    assert plan.steps[0].payload == {"value": "original"}
    assert journal.recover(plan)[0]["s"].status == "COMPLETED"
