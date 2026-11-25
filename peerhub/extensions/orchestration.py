"""Orchestration Engine (M3.4).

Implements Core Invariant 9 & 10:
- Bounded planner/executor (max_depth, max_steps, max_fanout, max_attempts, timeout).
- MAY_HAVE_STARTED no blind replay: non-idempotent ambiguous results forbid retry.
- DAG dependency ordering and cycle detection.
- Crash recovery / state hydration.
- Pure Python standard library implementation (REL-009).
"""

from __future__ import annotations

import collections
import concurrent.futures
import copy
import datetime
import math
import threading
import time
import uuid
from typing import Any, Callable

from peerhub.extensions.orchestration_deadline import HardDeadlineStepRunner
from peerhub.extensions.orchestration_journal import RecordPlanJournal
from peerhub.extensions.orchestration_model import (
    AmbiguousExecutionReplayForbiddenError,
    CostBoundExceededError,
    OrchestrationError,
    Plan,
    PlanBoundExceededError,
    PlanBounds,
    PlanDependencyCycleError,
    PlanExecutionSummary,
    PlanStateTransitionError,
    PlanStep,
    PlanStepExecutionError,
    StepExecutionResult,
    UnsupportedExecutionBoundError,
)

__all__ = [
    "AmbiguousExecutionReplayForbiddenError", "CostBoundExceededError", "HardDeadlineStepRunner",
    "OrchestrationError", "Plan", "PlanBoundExceededError", "PlanBounds", "PlanDependencyCycleError",
    "PlanExecutionSummary", "PlanExecutor", "PlanStateTransitionError", "PlanStep", "PlanStepExecutionError",
    "RecordPlanJournal", "StepExecutionResult", "UnsupportedExecutionBoundError",
]


class PlanExecutor:
    """Bounded executor of an explicitly accepted plan.

    Serial by default; `parallel=True` runs up to `bounds.max_fanout` independent ready steps at once (the runner must then be
    thread-safe; durable journal appends are serialized). Without a hard-cancellable runner a callback that overruns the deadline
    is only *detected* after it returns: `require_hard_deadline=True` therefore needs a runner that declares
    `hard_cancellable = True` (see `orchestration_deadline.HardDeadlineStepRunner`), which kills its work at the deadline.

    Cost: with `bounds.cost_budget` every attempt must report a finite cost (unreported = unknown, never zero). Spending is summed
    from the durable journal across attempts and resumes and checked BEFORE each attempt and after each result, so a restart
    cannot evade the budget. A single attempt (and, in parallel mode, up to `max_fanout` in-flight attempts) can overshoot it.
    """

    def __init__(self, journal: RecordPlanJournal | None = None,
                 *, require_hard_deadline: bool = False, parallel: bool = False) -> None:
        self.journal = journal
        self.require_hard_deadline = require_hard_deadline
        self.parallel = parallel

    def execute_plan(
        self,
        plan: Plan,
        step_runner: Callable[[PlanStep, int], StepExecutionResult],
    ) -> PlanExecutionSummary:
        plan = copy.deepcopy(plan)
        if self.journal is not None:
            recovered, _ = self.journal.recover(plan)
            if recovered:
                return self.resume_plan(plan, recovered, step_runner)
        return self._run(plan, initial_results={}, step_runner=step_runner)

    def resume_plan(
        self,
        plan: Plan,
        recorded_results: dict[str, StepExecutionResult],
        step_runner: Callable[[PlanStep, int], StepExecutionResult],
    ) -> PlanExecutionSummary:
        plan = copy.deepcopy(plan)
        if self.journal is not None:
            authoritative, _ = self.journal.recover(plan)
            if recorded_results != authoritative:
                raise PlanStateTransitionError("Caller results do not match authoritative execution evidence.")
            if any(result.status == "MAY_HAVE_STARTED" for result in authoritative.values()):
                raise AmbiguousExecutionReplayForbiddenError("Unfinished durable invocation requires external reconciliation, not a blind retry.")
        step_map = {s.step_id: s for s in plan.steps}

        # Check for ambiguous non-idempotent recorded results
        for step_id, res in recorded_results.items():
            if res.status == "MAY_HAVE_STARTED":
                step = step_map.get(step_id)
                if step is not None and not step.idempotent:
                    raise AmbiguousExecutionReplayForbiddenError(
                        f"Cannot resume plan '{plan.plan_id}': step '{step_id}' has recorded "
                        f"MAY_HAVE_STARTED status and is non-idempotent (Invariant 10)."
                    )

        # Check if every step is already resolved (completed or skipped)
        all_resolved = all(
            step.step_id in recorded_results
            and recorded_results[step.step_id].status in ("COMPLETED", "SKIPPED")
            for step in plan.steps
        )
        if all_resolved and len(plan.steps) > 0:
            raise PlanStateTransitionError(
                f"Cannot resume plan '{plan.plan_id}': all steps already completed."
            )

        return self._run(
            plan, initial_results=dict(recorded_results), step_runner=step_runner
        )

    @staticmethod
    def _matches(predicate: dict[str, Any], result: StepExecutionResult | None) -> bool:
        """Exact (type and value) match of one committed output key. Anything missing or unknown is NOT a match."""
        if result is None or result.status != "COMPLETED" or result.output is None:
            return False
        key, expected = predicate["key"], predicate["equals"]
        return key in result.output and type(result.output[key]) is type(expected) and result.output[key] == expected

    def _run(
        self,
        plan: Plan,
        initial_results: dict[str, StepExecutionResult],
        step_runner: Callable[[PlanStep, int], StepExecutionResult],
    ) -> PlanExecutionSummary:
        if self.require_hard_deadline and getattr(step_runner, "hard_cancellable", False) is not True:
            raise UnsupportedExecutionBoundError(
                "Strict execution needs a step runner that declares hard_cancellable (e.g. orchestration_deadline.HardDeadlineStepRunner).")
        owner = uuid.uuid4().hex
        deadline: datetime.datetime | None = None
        if self.journal is not None:
            _, deadline_iso = self.journal.recover(plan)
            deadline = datetime.datetime.fromisoformat(deadline_iso)
        # 1. Validate DAG and detect cycles
        self._validate_dag(plan)
        for name in ("max_depth", "max_steps", "max_fanout", "max_attempts"):
            value = getattr(plan.bounds, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise PlanBoundExceededError(f"{name} must be a positive integer.")
        if not math.isfinite(plan.bounds.timeout_seconds) or plan.bounds.timeout_seconds <= 0:
            raise PlanBoundExceededError("timeout_seconds must be finite and positive.")
        budget = plan.bounds.cost_budget
        if budget is not None and (isinstance(budget, bool) or not math.isfinite(budget) or budget < 0):
            raise PlanBoundExceededError("cost_budget must be a finite number >= 0 (or None for no budget).")
        step_ids = {s.step_id for s in plan.steps}
        for key, result in initial_results.items():
            if key not in step_ids or result.step_id != key or result.attempt < 1:
                raise PlanStateTransitionError("Recorded result does not bind to this plan's step.")
            if result.status not in ("COMPLETED", "FAILED", "MAY_HAVE_STARTED", "SKIPPED"):
                raise PlanStateTransitionError("Unsupported recorded step status; safe recovery requires certainty.")
        prior_attempts = sum(result.attempt for result in initial_results.values() if result.status != "SKIPPED")

        start_time = time.monotonic()
        step_map = {s.step_id: s for s in plan.steps}
        results: dict[str, StepExecutionResult] = dict(initial_results)
        lock = threading.Lock()
        reserved = 0  # attempts started in this run (reserved before invocation so parallel steps cannot exceed max_steps)
        spent = self.journal.recover_cost(plan) if self.journal is not None else float(
            sum(r.cost for r in initial_results.values() if r.cost is not None))
        flags = {"failed": False, "abort": False, "stop": False}

        if self.require_hard_deadline:
            remaining = plan.bounds.timeout_seconds
            if deadline is not None:
                remaining = min(remaining, (deadline - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
            bind = getattr(step_runner, "set_deadline", None)
            if callable(bind):
                bind(time.monotonic() + max(0.0, remaining))

        def resolved(step_id: str) -> bool:
            r = results.get(step_id)
            return r is not None and r.status in ("COMPLETED", "SKIPPED")

        def overrun() -> bool:
            return (time.monotonic() - start_time) > plan.bounds.timeout_seconds or (
                deadline is not None and datetime.datetime.now(datetime.timezone.utc) >= deadline)

        # A recorded stop condition is honoured on resume: the plan already stopped there.
        for sid, res in list(results.items()):
            cond = step_map[sid].stop_if
            if cond is not None and self._matches(cond, res):
                flags["stop"] = True

        def run_step(step: PlanStep) -> str:
            """Returns 'completed' | 'skipped' | 'failed' | 'stop'. Raises a bound/ambiguity error to abort the plan."""
            nonlocal reserved, spent
            with lock:
                skip = any(results[d].status == "SKIPPED" for d in step.depends_on) or (
                    step.when is not None and not self._matches(step.when, results.get(step.when["step_id"])))
                if skip:
                    note = StepExecutionResult(step.step_id, 1, "SKIPPED", error="condition not met or dependency skipped")
                    results[step.step_id] = note
                    if self.journal is not None:
                        self.journal.record_attempt(plan, note, owner, running=False)
                    return "skipped"
            previous = results.get(step.step_id)
            attempt = previous.attempt + 1 if previous is not None else 1
            max_step_attempts = min(step.max_attempts, plan.bounds.max_attempts)
            while attempt <= max_step_attempts:
                with lock:
                    if flags["abort"]:
                        return "failed"
                    if prior_attempts + reserved >= plan.bounds.max_steps:
                        raise PlanBoundExceededError(
                            f"Execution of plan '{plan.plan_id}' exhausted max_steps across attempts/resumes.")
                    if budget is not None and spent >= budget:
                        raise CostBoundExceededError(
                            f"Plan '{plan.plan_id}' reached its cost budget ({spent} of {budget}); no further attempt is started.")
                    if overrun():
                        raise PlanBoundExceededError(
                            f"Execution of plan '{plan.plan_id}' exceeded its timeout or absolute deadline.")
                    if self.journal is not None:
                        self.journal.record_attempt(plan, StepExecutionResult(
                            step.step_id, attempt, "MAY_HAVE_STARTED", error="RUNNING; outcome not committed"
                        ), owner, running=True)
                    reserved += 1
                res = step_runner(PlanStep.from_dict(step.to_dict()), attempt)
                with lock:
                    if res.step_id != step.step_id or res.attempt != attempt or res.status not in (
                        "COMPLETED", "FAILED", "MAY_HAVE_STARTED"
                    ):
                        raise PlanStepExecutionError("Runner result does not bind to the invoked step and attempt.")
                    if res.cost is not None and (isinstance(res.cost, bool) or not math.isfinite(res.cost) or res.cost < 0):
                        raise PlanStepExecutionError("A reported cost must be a finite number >= 0.")
                    if budget is not None and res.status != "MAY_HAVE_STARTED" and res.cost is None:
                        raise PlanStepExecutionError(
                            "This plan has a cost budget: every attempt result must report its cost (unreported is unknown, not zero).")
                    results[step.step_id] = res
                    if res.cost is not None:
                        spent += res.cost
                    if self.journal is not None:
                        self.journal.record_attempt(plan, res, owner, running=False)
                    # A final step must not bypass the deadline. This detects an overrun; only a hard-cancellable runner
                    # bounds the work itself.
                    if overrun():
                        raise PlanBoundExceededError(
                            f"Execution of plan '{plan.plan_id}' exceeded timeout during step '{step.step_id}'.")
                    if budget is not None and spent > budget:
                        raise CostBoundExceededError(
                            f"Plan '{plan.plan_id}' exceeded its cost budget ({spent} of {budget}) during step '{step.step_id}'.")

                    if res.status == "COMPLETED":
                        if step.stop_if is not None and self._matches(step.stop_if, res):
                            return "stop"
                        return "completed"

                    if res.status == "MAY_HAVE_STARTED":
                        if self.journal is not None or not step.idempotent:
                            # Invariant 10: blind replay strictly forbidden
                            raise AmbiguousExecutionReplayForbiddenError(
                                f"Step '{step.step_id}' returned MAY_HAVE_STARTED and is "
                                f"non-idempotent. Blind replay is forbidden (Invariant 10)."
                            )
                    attempt += 1  # FAILED, or MAY_HAVE_STARTED of an explicitly idempotent step: bounded retry
            return "failed"

        def downstream(step_id: str) -> list[str]:
            return [s.step_id for s in plan.steps if step_id in s.depends_on]

        def ready_now() -> list[str]:
            return [s.step_id for s in plan.steps
                    if not resolved(s.step_id) and all(resolved(d) for d in s.depends_on)]

        if not flags["stop"]:
            queued: set[str] = set()
            ready: collections.deque[str] = collections.deque()

            def enqueue(candidates: list[str]) -> None:
                for sid in candidates:
                    if sid not in queued and not resolved(sid) and all(resolved(d) for d in step_map[sid].depends_on):
                        queued.add(sid)
                        ready.append(sid)

            enqueue(ready_now())

            def settle(step_id: str, outcome: str) -> None:
                if outcome == "failed":
                    flags["failed"] = True
                elif outcome == "stop":
                    flags["stop"] = True
                else:
                    enqueue(downstream(step_id))

            if not self.parallel:
                while ready and not flags["failed"] and not flags["stop"]:
                    sid = ready.popleft()
                    settle(sid, run_step(step_map[sid]))
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=plan.bounds.max_fanout) as pool:
                    inflight: dict[concurrent.futures.Future[str], str] = {}
                    error: BaseException | None = None
                    while True:
                        while (ready and len(inflight) < plan.bounds.max_fanout and not flags["failed"]
                               and not flags["stop"] and error is None):
                            sid = ready.popleft()
                            inflight[pool.submit(run_step, step_map[sid])] = sid
                        if not inflight:
                            break
                        done, _ = concurrent.futures.wait(inflight, return_when=concurrent.futures.FIRST_COMPLETED)
                        for fut in done:
                            sid = inflight.pop(fut)
                            try:
                                settle(sid, fut.result())
                            except BaseException as exc:  # a bound/ambiguity abort: stop starting, let in-flight steps finish
                                flags["abort"] = True
                                error = error or exc
                    if error is not None:
                        raise error

        total_elapsed = time.monotonic() - start_time
        if flags["failed"]:
            final_status = "FAILED"
        elif flags["stop"]:
            final_status = "STOPPED"
        else:
            final_status = "COMPLETED" if all(resolved(s.step_id) for s in plan.steps) else "FAILED"

        return PlanExecutionSummary(
            plan_id=plan.plan_id,
            status=final_status,
            steps_executed=reserved,
            step_results=results,
            elapsed_seconds=total_elapsed,
            total_cost=spent,
        )

    def _validate_dag(self, plan: Plan) -> None:
        """Verify no cycles exist in plan step dependencies."""
        step_ids = {s.step_id for s in plan.steps}
        if len(step_ids) != len(plan.steps):
            raise PlanStateTransitionError("Plan step IDs must be unique.")
        for step in plan.steps:
            if not step.step_id or isinstance(step.max_attempts, bool) or step.max_attempts < 1:
                raise PlanStateTransitionError("Step IDs and positive attempt limits are required.")
            if len(set(step.depends_on)) != len(step.depends_on) or any(
                dep not in step_ids for dep in step.depends_on
            ):
                raise PlanStateTransitionError("Dependencies must be unique existing step IDs.")
        adjacency: dict[str, list[str]] = collections.defaultdict(list)
        in_degree: dict[str, int] = {s.step_id: 0 for s in plan.steps}

        for s in plan.steps:
            for dep in s.depends_on:
                if dep in step_ids:
                    adjacency[dep].append(s.step_id)
                    in_degree[s.step_id] += 1

        queue: collections.deque[str] = collections.deque(
            [sid for sid, deg in in_degree.items() if deg == 0]
        )
        visited_count = 0

        while queue:
            node = queue.popleft()
            visited_count += 1
            for nxt in adjacency[node]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if visited_count < len(plan.steps):
            raise PlanDependencyCycleError(
                f"Plan '{plan.plan_id}' contains circular step dependencies."
            )

        for step in plan.steps:  # conditions are deterministic predicates over committed outputs, validated up front
            if step.when is not None:
                w = step.when
                if (set(w) != {"step_id", "key", "equals"} or not isinstance(w["key"], str)
                        or w["step_id"] not in step.depends_on):
                    raise PlanStateTransitionError(
                        f"Step '{step.step_id}': when must be {{step_id (one of depends_on), key, equals}}.")
            if step.stop_if is not None:
                c = step.stop_if
                if set(c) != {"key", "equals"} or not isinstance(c["key"], str):
                    raise PlanStateTransitionError(f"Step '{step.step_id}': stop_if must be {{key, equals}}.")

        # Longest dependency chain (in steps) must fit max_depth (checked on an acyclic graph).
        depth: dict[str, int] = {}
        for sid in self._topological(plan):
            depth[sid] = 1 + max((depth[d] for d in next(s for s in plan.steps if s.step_id == sid).depends_on), default=0)
        max_depth = plan.bounds.max_depth
        if type(max_depth) is int and depth and max(depth.values()) > max_depth:
            raise PlanBoundExceededError(
                f"Plan '{plan.plan_id}' has a dependency chain of {max(depth.values())} steps, above max_depth {max_depth}.")

    @staticmethod
    def _topological(plan: Plan) -> list[str]:
        indeg = {s.step_id: len(s.depends_on) for s in plan.steps}
        order: list[str] = []
        queue = collections.deque(sid for sid, d in indeg.items() if d == 0)
        while queue:
            node = queue.popleft()
            order.append(node)
            for s in plan.steps:
                if node in s.depends_on:
                    indeg[s.step_id] -= 1
                    if indeg[s.step_id] == 0:
                        queue.append(s.step_id)
        return order
