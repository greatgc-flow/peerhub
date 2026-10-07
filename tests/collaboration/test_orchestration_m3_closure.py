"""M3.4 closure: cost budget, conditional skip/stop, bounded parallel fan-out, max_depth, hard-deadline binding."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from peerhub.extensions.orchestration import (
    CostBoundExceededError,
    Plan,
    PlanBoundExceededError,
    PlanBounds,
    PlanExecutor,
    PlanStateTransitionError,
    PlanStep,
    PlanStepExecutionError,
    RecordPlanJournal,
    StepExecutionResult,
    UnsupportedExecutionBoundError,
)


@pytest.fixture
def journal(tmp_path: Path) -> RecordPlanJournal:
    from peerhub.core.models import Peer, Stream
    from peerhub.core.store import CoreStore

    store = CoreStore(tmp_path / "core.db")
    store.register_peer(Peer(peer_id="executor"))
    store.create_stream(Stream(stream_id="execution", members=["executor"]))
    return RecordPlanJournal(store, "execution", "executor")


def done(step: PlanStep, attempt: int, cost: float | None = None, **output) -> StepExecutionResult:
    return StepExecutionResult(step.step_id, attempt, "COMPLETED", output=output or None, cost=cost)


def chain(n: int, **bounds) -> Plan:
    steps = [PlanStep(f"s{i}", "act", {}, depends_on=[f"s{i - 1}"] if i else []) for i in range(n)]
    return Plan("p", "chain", steps, bounds=PlanBounds(**bounds))


# ------------------------------------------------------------------------------------------------ cost
@pytest.mark.catalog_id("ORC-013")
def test_cost_budget_stops_the_plan_when_exceeded():
    plan = chain(3, cost_budget=10.0)
    ran: list[str] = []

    def runner(step, attempt):
        ran.append(step.step_id)
        return done(step, attempt, cost=6.0)

    with pytest.raises(CostBoundExceededError):
        PlanExecutor().execute_plan(plan, runner)
    assert ran == ["s0", "s1"]  # 6 -> 12 exceeds 10: s2 is never started


def test_no_attempt_starts_once_the_budget_is_spent():
    plan = chain(2, cost_budget=5.0)
    with pytest.raises(CostBoundExceededError, match="no further attempt"):
        PlanExecutor().execute_plan(plan, lambda s, a: done(s, a, cost=5.0))  # spent == budget after s0: exhausted


@pytest.mark.catalog_id("ORC-013")
def test_unreported_cost_is_unknown_not_zero_when_a_budget_exists():
    with pytest.raises(PlanStepExecutionError, match="report its cost"):
        PlanExecutor().execute_plan(chain(1, cost_budget=1.0), lambda s, a: done(s, a))  # no cost reported
    summary = PlanExecutor().execute_plan(chain(1), lambda s, a: done(s, a))  # without a budget, cost may be omitted
    assert summary.status == "COMPLETED" and summary.total_cost == 0.0


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_reported_cost_must_be_finite_and_nonnegative(bad):
    with pytest.raises(PlanStepExecutionError):
        PlanExecutor().execute_plan(chain(1, cost_budget=100.0), lambda s, a: done(s, a, cost=bad))


def test_cost_is_summed_across_attempts_and_survives_a_restart(journal):
    plan = Plan("budgeted", "t", [PlanStep("a", "x", {}), PlanStep("b", "x", {}, depends_on=["a"])],
                bounds=PlanBounds(cost_budget=10.0))
    journal.accept_plan(plan, accepted_by="operator")

    def flaky(step, attempt):
        if step.step_id == "a" and attempt == 1:
            return StepExecutionResult("a", 1, "FAILED", error="boom", cost=4.0)  # a failed attempt still costs
        return done(step, attempt, cost=4.0)  # 4 + 4 + 4 = 12 > 10

    with pytest.raises(CostBoundExceededError):
        PlanExecutor(journal).execute_plan(plan, flaky)
    assert journal.recover_cost(plan) == 12.0  # every attempt is durable
    fresh = RecordPlanJournal(journal.store, "execution", "executor")
    assert fresh.recover_cost(plan) == 12.0  # a new process sees the same spend: no evasion by crash-restart


@pytest.mark.catalog_id("ORC-013")
def test_a_restarted_executor_starts_nothing_once_the_durable_budget_is_spent(journal):
    plan = Plan("restart", "t", [PlanStep("a", "x", {}), PlanStep("b", "x", {}, depends_on=["a"]), PlanStep("c", "x", {}, depends_on=["b"])],
                bounds=PlanBounds(cost_budget=5.0))
    journal.accept_plan(plan, accepted_by="operator")
    with pytest.raises(CostBoundExceededError):
        PlanExecutor(journal).execute_plan(plan, lambda s, a: done(s, a, cost=3.0) if s.step_id != "c" else done(s, a, cost=3.0))
    ran: list[str] = []
    with pytest.raises(CostBoundExceededError):
        PlanExecutor(RecordPlanJournal(journal.store, "execution", "executor")).execute_plan(
            plan, lambda s, a: ran.append(s.step_id) or done(s, a, cost=1.0))
    assert ran == []


def test_the_accepted_record_binds_the_budget_and_the_accounting_mode(journal):
    with_budget = Plan("b1", "t", [PlanStep("a", "x", {})], bounds=PlanBounds(cost_budget=3.0))
    without = Plan("b2", "t", [PlanStep("a", "x", {})])
    for plan in (with_budget, without):
        journal.accept_plan(plan, accepted_by="operator")
    recs = {r.body["plan_id"]: r.body for r in journal.store.read_records("execution") if r.kind == "m3.orchestration.accepted"}
    assert (recs["b1"]["cost_budget"], recs["b1"]["cost_accounting"]) == (3.0, "RESULT_REPORTED")
    assert (recs["b2"]["cost_budget"], recs["b2"]["cost_accounting"]) == (None, "NOT_CONFIGURED")


# ------------------------------------------------------------------------------------------- conditional
def gate_plan() -> Plan:
    return Plan("cond", "t", [
        PlanStep("gate", "x", {}),
        PlanStep("left", "x", {}, depends_on=["gate"], when={"step_id": "gate", "key": "route", "equals": "left"}),
        PlanStep("right", "x", {}, depends_on=["gate"], when={"step_id": "gate", "key": "route", "equals": "right"}),
        PlanStep("after_left", "x", {}, depends_on=["left"]),
    ])


@pytest.mark.parametrize("route,ran", [("left", ["gate", "left", "after_left"]), ("right", ["gate", "right"])])
def test_when_skips_the_untaken_branch_and_skips_its_dependents(route, ran):
    executed: list[str] = []

    def runner(step, attempt):
        executed.append(step.step_id)
        return done(step, attempt, route=route)

    summary = PlanExecutor().execute_plan(gate_plan(), runner)
    assert executed == ran and summary.status == "COMPLETED"
    skipped = sorted(k for k, v in summary.step_results.items() if v.status == "SKIPPED")
    assert skipped == sorted({"left", "right", "after_left"} - set(ran))
    assert summary.steps_executed == len(ran)  # skipping is a decision, not an invocation


@pytest.mark.catalog_id("ORC-013")
def test_when_requires_exact_type_and_value_and_unknown_never_matches():
    plan = Plan("t", "t", [PlanStep("g", "x", {}), PlanStep("n", "x", {}, depends_on=["g"], when={"step_id": "g", "key": "ok", "equals": True})])
    for output in ({"ok": 1}, {"ok": "true"}, {}, None):  # 1 is not True; missing/unknown is not a match
        summary = PlanExecutor().execute_plan(plan, lambda s, a, o=output: StepExecutionResult(s.step_id, a, "COMPLETED", output=o))
        assert summary.step_results["n"].status == "SKIPPED"
    summary = PlanExecutor().execute_plan(plan, lambda s, a: done(s, a, ok=True))
    assert summary.step_results["n"].status == "COMPLETED"


def test_stop_if_ends_the_plan_cleanly_without_running_the_rest():
    plan = Plan("t", "t", [PlanStep("a", "x", {}), PlanStep("b", "x", {}, depends_on=["a"], stop_if={"key": "done", "equals": True}),
                           PlanStep("c", "x", {}, depends_on=["b"])])
    ran: list[str] = []

    def runner(step, attempt):
        ran.append(step.step_id)
        return done(step, attempt, done=True)

    summary = PlanExecutor().execute_plan(plan, runner)
    assert ran == ["a", "b"] and summary.status == "STOPPED" and "c" not in summary.step_results


@pytest.mark.catalog_id("ORC-013")
def test_a_stop_is_honoured_after_a_restart(journal):
    plan = Plan("stopper", "t", [PlanStep("a", "x", {}, stop_if={"key": "go", "equals": "no"}), PlanStep("b", "x", {}, depends_on=["a"])])
    journal.accept_plan(plan, accepted_by="operator")
    first = PlanExecutor(journal).execute_plan(plan, lambda s, a: done(s, a, go="no"))
    assert first.status == "STOPPED"
    ran: list[str] = []
    again = PlanExecutor(RecordPlanJournal(journal.store, "execution", "executor")).execute_plan(plan, lambda s, a: ran.append(s.step_id))
    assert again.status == "STOPPED" and ran == []  # nothing runs after the recorded stop


@pytest.mark.parametrize("bad_step", [
    PlanStep("n", "x", {}, depends_on=["g"], when={"step_id": "other", "key": "k", "equals": 1}),  # not a dependency
    PlanStep("n", "x", {}, depends_on=["g"], when={"key": "k", "equals": 1}),
    PlanStep("n", "x", {}, depends_on=["g"], stop_if={"step_id": "g", "key": "k", "equals": 1}),
])
def test_malformed_conditions_are_rejected_before_anything_runs(bad_step):
    plan = Plan("t", "t", [PlanStep("g", "x", {}), bad_step, PlanStep("other", "x", {})])
    ran: list[str] = []
    with pytest.raises(PlanStateTransitionError):
        PlanExecutor().execute_plan(plan, lambda s, a: ran.append(s.step_id))
    assert ran == []


# ---------------------------------------------------------------------------------------- depth, parallel
@pytest.mark.catalog_id("ORC-013")
def test_max_depth_is_enforced_from_the_dependency_chain():
    with pytest.raises(PlanBoundExceededError, match="max_depth"):
        PlanExecutor().execute_plan(chain(4, max_depth=3), lambda s, a: done(s, a))
    assert PlanExecutor().execute_plan(chain(3, max_depth=3), lambda s, a: done(s, a)).status == "COMPLETED"


@pytest.mark.catalog_id("ORC-013")
def test_parallel_runs_independent_steps_concurrently_but_never_above_max_fanout():
    steps = [PlanStep(f"s{i}", "x", {}) for i in range(6)]
    plan = Plan("fan", "t", steps, bounds=PlanBounds(max_fanout=2))
    live, peak, lock = 0, 0, threading.Lock()

    def runner(step, attempt):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return done(step, attempt)

    summary = PlanExecutor(parallel=True).execute_plan(plan, runner)
    assert summary.status == "COMPLETED" and summary.steps_executed == 6
    assert peak == 2  # concurrent, and bounded by max_fanout


def test_parallel_respects_dependencies_and_serializes_durable_journal_appends(journal):
    plan = Plan("pj", "t", [PlanStep("root", "x", {}), PlanStep("a", "x", {}, depends_on=["root"]),
                            PlanStep("b", "x", {}, depends_on=["root"]), PlanStep("join", "x", {}, depends_on=["a", "b"])],
                bounds=PlanBounds(max_fanout=2))
    journal.accept_plan(plan, accepted_by="operator")
    order: list[str] = []

    def runner(step, attempt):
        order.append(step.step_id)
        time.sleep(0.02)
        return done(step, attempt)

    summary = PlanExecutor(journal, parallel=True).execute_plan(plan, runner)
    assert summary.status == "COMPLETED" and order[0] == "root" and order[-1] == "join"
    results, _ = journal.recover(plan)
    assert sorted(results) == ["a", "b", "join", "root"] and all(r.status == "COMPLETED" for r in results.values())


def test_parallel_failure_stops_new_steps_and_a_bound_abort_propagates():
    steps = [PlanStep(f"s{i}", "x", {}, max_attempts=1) for i in range(4)]
    plan = Plan("pf", "t", steps, bounds=PlanBounds(max_fanout=1))
    summary = PlanExecutor(parallel=True).execute_plan(plan, lambda s, a: StepExecutionResult(s.step_id, a, "FAILED", error="x"))
    assert summary.status == "FAILED" and summary.steps_executed == 1  # fan-out 1: the first failure stops the rest
    with pytest.raises(PlanBoundExceededError):
        PlanExecutor(parallel=True).execute_plan(Plan("pm", "t", [PlanStep(f"m{i}", "x", {}) for i in range(5)],
                                                       bounds=PlanBounds(max_steps=2, max_fanout=2)), lambda s, a: done(s, a))


# --------------------------------------------------------------------------------------------- hard deadline
def test_strict_mode_needs_a_runner_that_declares_hard_cancellation():
    def plain(s, a):
        return done(s, a)

    with pytest.raises(UnsupportedExecutionBoundError):
        PlanExecutor(require_hard_deadline=True).execute_plan(chain(1), plain)

    class Hard:
        hard_cancellable = True
        deadline = None

        def set_deadline(self, monotonic_deadline):
            self.deadline = monotonic_deadline

        def __call__(self, step, attempt):
            return done(step, attempt)

    runner = Hard()
    assert PlanExecutor(require_hard_deadline=True).execute_plan(chain(1, timeout_seconds=30.0), runner).status == "COMPLETED"
    # the executor binds the plan deadline (a small tolerance: the Windows monotonic clock ticks every ~15.6 ms)
    assert runner.deadline is not None and 0 < runner.deadline - time.monotonic() <= 30.5


@pytest.mark.catalog_id("ORC-013")
def test_parallel_journal_appends_are_never_entered_concurrently(journal):
    """The executor itself must serialize durable appends: a spy that detects re-entrancy fails if the lock is removed."""
    inside, overlaps = 0, []
    real = journal.record_attempt
    guard = threading.Lock()

    def spy(*args, **kwargs):
        nonlocal inside
        with guard:
            inside += 1
            if inside > 1:
                overlaps.append(inside)
        time.sleep(0.01)  # widen the window so an unserialized pair would overlap
        try:
            return real(*args, **kwargs)
        finally:
            with guard:
                inside -= 1

    journal.record_attempt = spy  # type: ignore[method-assign]
    plan = Plan("spy", "t", [PlanStep(f"s{i}", "x", {}) for i in range(6)], bounds=PlanBounds(max_fanout=4))
    journal.accept_plan(plan, accepted_by="operator")
    summary = PlanExecutor(journal, parallel=True).execute_plan(plan, lambda s, a: (time.sleep(0.005), done(s, a))[1])
    assert summary.status == "COMPLETED" and overlaps == []


@pytest.mark.catalog_id("RUN-013")
def test_a_job_that_finishes_exactly_at_the_deadline_is_used_not_discarded():
    from peerhub.extensions.orchestration import HardDeadlineStepRunner

    class Job:
        def __init__(self, status, output=None, error=None):
            self.job_id, self.status, self.output, self.error = "j1", status, output, error

    class Port:
        hard_cancel = True

        def submit(self, capability, params):
            return Job("RUNNING")

        def get(self, job_id):
            return Job("RUNNING")

        def cancel(self, job_id):  # the process had just exited on its own: cancel reports the real, completed job
            return Job("COMPLETED", output={"cost": 2.5, "answer": 1})

    runner = HardDeadlineStepRunner(Port(), poll_seconds=0.001)
    runner.set_deadline(time.monotonic() - 1)  # the deadline has already passed
    result = runner(PlanStep("s", "cap", {}), 1)
    assert result.status == "COMPLETED" and result.cost == 2.5 and result.output == {"cost": 2.5, "answer": 1}  # no lost cost, no replay

    class Killing(Port):
        def cancel(self, job_id):
            return Job("CANCELLED", error="killed")

    killing = HardDeadlineStepRunner(Killing(), poll_seconds=0.001)
    killing.set_deadline(time.monotonic() - 1)
    assert killing(PlanStep("s", "cap", {}), 1).status == "FAILED"  # a real kill is still a certain failure
