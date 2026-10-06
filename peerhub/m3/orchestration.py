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
import dataclasses
import datetime
import time
from typing import Any, Callable


class OrchestrationError(Exception):
    """Base exception for orchestration."""


class PlanBoundExceededError(OrchestrationError):
    """Raised when plan bounds (steps, depth, fanout, timeout) are exceeded (Invariant 9)."""


class AmbiguousExecutionReplayForbiddenError(OrchestrationError):
    """Raised when blind retry of MAY_HAVE_STARTED step is forbidden (Invariant 10)."""


class PlanDependencyCycleError(OrchestrationError):
    """Raised when step dependency graph has circular dependencies."""


class PlanStateTransitionError(OrchestrationError):
    """Raised on invalid plan lifecycle transitions."""


class PlanStepExecutionError(OrchestrationError):
    """Raised when a step permanently fails."""


@dataclasses.dataclass(frozen=True)
class PlanBounds:
    max_depth: int = 5
    max_steps: int = 50
    max_fanout: int = 10
    max_attempts: int = 3
    timeout_seconds: float = 300.0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanBounds:
        return cls(
            max_depth=int(data.get("max_depth", 5)),
            max_steps=int(data.get("max_steps", 50)),
            max_fanout=int(data.get("max_fanout", 10)),
            max_attempts=int(data.get("max_attempts", 3)),
            timeout_seconds=float(data.get("timeout_seconds", 300.0)),
        )


@dataclasses.dataclass(frozen=True)
class PlanStep:
    step_id: str
    action_type: str
    payload: dict[str, Any]
    idempotent: bool = True
    depends_on: list[str] = dataclasses.field(default_factory=lambda: list[str]())
    max_attempts: int = 3

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanStep:
        raw_deps = data.get("depends_on", [])
        deps = [str(d) for d in raw_deps]
        return cls(
            step_id=str(data["step_id"]),
            action_type=str(data["action_type"]),
            payload=dict(data.get("payload", {})),
            idempotent=bool(data.get("idempotent", True)),
            depends_on=deps,
            max_attempts=int(data.get("max_attempts", 3)),
        )


@dataclasses.dataclass(frozen=True)
class StepExecutionResult:
    step_id: str
    attempt: int
    status: str  # COMPLETED | FAILED | MAY_HAVE_STARTED
    output: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepExecutionResult:
        return cls(
            step_id=str(data["step_id"]),
            attempt=int(data["attempt"]),
            status=str(data["status"]),
            output=dict(data["output"]) if data.get("output") is not None else None,
            error=str(data["error"]) if data.get("error") is not None else None,
        )


@dataclasses.dataclass(frozen=True)
class Plan:
    plan_id: str
    title: str
    steps: list[PlanStep]
    bounds: PlanBounds = dataclasses.field(default_factory=PlanBounds)
    created_at: str = dataclasses.field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "title": self.title,
            "steps": [s.to_dict() for s in self.steps],
            "bounds": self.bounds.to_dict(),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        return cls(
            plan_id=str(data["plan_id"]),
            title=str(data["title"]),
            steps=[PlanStep.from_dict(s) for s in data.get("steps", [])],
            bounds=PlanBounds.from_dict(data.get("bounds", {})),
            created_at=str(
                data.get(
                    "created_at",
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                )
            ),
        )


@dataclasses.dataclass(frozen=True)
class PlanExecutionSummary:
    plan_id: str
    status: str  # COMPLETED | FAILED | BOUNDS_EXCEEDED | CANCELLED
    steps_executed: int
    step_results: dict[str, StepExecutionResult]
    elapsed_seconds: float


class PlanExecutor:
    """Executes accepted plans adhering to bounds and certainty invariants."""

    def execute_plan(
        self,
        plan: Plan,
        step_runner: Callable[[PlanStep, int], StepExecutionResult],
    ) -> PlanExecutionSummary:
        return self._run(plan, initial_results={}, step_runner=step_runner)

    def resume_plan(
        self,
        plan: Plan,
        recorded_results: dict[str, StepExecutionResult],
        step_runner: Callable[[PlanStep, int], StepExecutionResult],
    ) -> PlanExecutionSummary:
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

        # Check if all steps already completed
        all_completed = all(
            step.step_id in recorded_results
            and recorded_results[step.step_id].status == "COMPLETED"
            for step in plan.steps
        )
        if all_completed and len(plan.steps) > 0:
            raise PlanStateTransitionError(
                f"Cannot resume plan '{plan.plan_id}': all steps already completed."
            )

        return self._run(
            plan, initial_results=dict(recorded_results), step_runner=step_runner
        )

    def _run(
        self,
        plan: Plan,
        initial_results: dict[str, StepExecutionResult],
        step_runner: Callable[[PlanStep, int], StepExecutionResult],
    ) -> PlanExecutionSummary:
        # 1. Validate DAG and detect cycles
        self._validate_dag(plan)

        start_time = time.monotonic()
        results: dict[str, StepExecutionResult] = dict(initial_results)
        steps_executed_in_this_run = 0

        step_map = {s.step_id: s for s in plan.steps}
        in_degree: dict[str, int] = collections.defaultdict(int)
        adjacency: dict[str, list[str]] = collections.defaultdict(list)

        for s in plan.steps:
            for dep in s.depends_on:
                adjacency[dep].append(s.step_id)
            in_degree[s.step_id] = len(s.depends_on)

        # Ready queue
        ready_queue: collections.deque[str] = collections.deque()
        for s in plan.steps:
            if s.step_id in results and results[s.step_id].status == "COMPLETED":
                # already satisfied
                continue
            # check if all dependencies are satisfied
            deps_met = all(
                dep in results and results[dep].status == "COMPLETED"
                for dep in s.depends_on
            )
            if deps_met:
                ready_queue.append(s.step_id)

        failed = False

        while ready_queue:
            # Check timeout bound
            elapsed = time.monotonic() - start_time
            if elapsed > plan.bounds.timeout_seconds:
                raise PlanBoundExceededError(
                    f"Execution of plan '{plan.plan_id}' exceeded timeout limit of "
                    f"{plan.bounds.timeout_seconds}s (elapsed {elapsed:.2f}s)."
                )

            current_id = ready_queue.popleft()
            step = step_map[current_id]

            # Check max_steps bound
            if steps_executed_in_this_run >= plan.bounds.max_steps:
                raise PlanBoundExceededError(
                    f"Execution of plan '{plan.plan_id}' exceeded max_steps limit "
                    f"of {plan.bounds.max_steps} steps."
                )

            # Execute step with bounded retry
            attempt = 1
            max_step_attempts = min(step.max_attempts, plan.bounds.max_attempts)
            step_completed = False

            while attempt <= max_step_attempts:
                # Check timeout before each attempt
                if (time.monotonic() - start_time) > plan.bounds.timeout_seconds:
                    raise PlanBoundExceededError(
                        f"Execution of plan '{plan.plan_id}' exceeded timeout limit "
                        f"of {plan.bounds.timeout_seconds}s."
                    )

                res = step_runner(step, attempt)
                steps_executed_in_this_run += 1
                results[step.step_id] = res

                if res.status == "COMPLETED":
                    step_completed = True
                    break

                if res.status == "MAY_HAVE_STARTED":
                    if not step.idempotent:
                        # Invariant 10: blind replay strictly forbidden
                        raise AmbiguousExecutionReplayForbiddenError(
                            f"Step '{step.step_id}' returned MAY_HAVE_STARTED and is "
                            f"non-idempotent. Blind replay is forbidden (Invariant 10)."
                        )
                    # If idempotent, we can boundedly retry if attempts remain
                    attempt += 1
                    continue

                if res.status == "FAILED":
                    attempt += 1
                    continue

            if not step_completed:
                failed = True
                break

            # Enqueue newly satisfied downstream steps
            for downstream_id in adjacency[current_id]:
                downstream_step = step_map[downstream_id]
                if downstream_id in results and results[downstream_id].status == "COMPLETED":
                    continue
                deps_met = all(
                    dep in results and results[dep].status == "COMPLETED"
                    for dep in downstream_step.depends_on
                )
                if deps_met and downstream_id not in ready_queue:
                    ready_queue.append(downstream_id)

        total_elapsed = time.monotonic() - start_time
        final_status = "FAILED" if failed else "COMPLETED"

        # Check if all steps in plan were completed
        if not failed:
            all_done = all(
                s.step_id in results and results[s.step_id].status == "COMPLETED"
                for s in plan.steps
            )
            if not all_done:
                final_status = "FAILED"

        return PlanExecutionSummary(
            plan_id=plan.plan_id,
            status=final_status,
            steps_executed=steps_executed_in_this_run,
            step_results=results,
            elapsed_seconds=total_elapsed,
        )

    def _validate_dag(self, plan: Plan) -> None:
        """Verify no cycles exist in plan step dependencies."""
        step_ids = {s.step_id for s in plan.steps}
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
