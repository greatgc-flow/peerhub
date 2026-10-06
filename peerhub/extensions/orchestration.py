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
import math
import time
import hashlib
import json
import uuid
import copy
import sqlite3
from typing import Any, Callable, cast


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


class UnsupportedExecutionBoundError(OrchestrationError):
    """No hard-cancellable runtime binding is available for strict execution."""


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


def _plan_json(plan: Plan) -> str:
    return json.dumps(plan.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)


class RecordPlanJournal:
    """Authoritative evidence through public Core ports, no private database.

    Requires a dedicated single-author Stream so append idempotency enforces
    one owner per invocation attempt across executor instances. Evidence refs
    can bind an Artifact or approval Record; their verification belongs to the
    accepting authority, not to invented journal approval logic.
    """

    def __init__(self, store: Any, stream_id: str, author_peer_id: str) -> None:
        self.store = store
        self.stream_id = stream_id
        self.author_peer_id = author_peer_id

    def _check_owner(self) -> None:
        if self.store.get_stream(self.stream_id).members != [self.author_peer_id]:
            raise PlanStateTransitionError("Durable execution requires a dedicated single-author stream.")

    def _records(self, plan_id: str) -> list[Any]:
        self._check_owner()
        records: list[Any] = []
        after = 0
        while True:
            page = self.store.read_records(self.stream_id, after_position=after, limit=256)
            if not page:
                break
            for record in page:
                body = record.body
                if record.kind.startswith("m3.orchestration.") and isinstance(body, dict) and cast(dict[str, Any], body).get("plan_id") == plan_id:
                    if record.author_peer_id != self.author_peer_id:
                        raise PlanStateTransitionError("Plan evidence has a foreign authority.")
                    records.append(record)
            after = page[-1].position
        return records

    def _append(self, plan: Plan, kind: str, suffix: str, body: dict[str, Any]) -> Any:
        self._check_owner()
        def owner_guard(conn: sqlite3.Connection, *, stream_id: str, peer_id: str) -> None:
            members = [row[0] for row in conn.execute(
                "SELECT peer_id FROM stream_members WHERE stream_id=?", (stream_id,)
            )]
            if members != [peer_id] or peer_id != self.author_peer_id:
                raise PlanStateTransitionError("Execution stream ownership changed before durable append.")
        return self.store.append_record(
            guard=owner_guard,
            stream_id=self.stream_id, author_peer_id=self.author_peer_id,
            kind=f"m3.orchestration.{kind}", body={"plan_id": plan.plan_id, **body},
            idempotency_key="orchestration:" + hashlib.sha256(json.dumps([plan.plan_id, suffix]).encode()).hexdigest(),
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    def accept_plan(self, plan: Plan, *, accepted_by: str,
                    evidence_refs: list[str] | None = None) -> str:
        if not accepted_by:
            raise PlanStateTransitionError("Accepted plan requires an explicit accepting authority.")
        if not math.isfinite(plan.bounds.timeout_seconds) or plan.bounds.timeout_seconds <= 0:
            raise PlanBoundExceededError("Accepted plan requires a finite positive deadline.")
        if evidence_refs is not None and any(not isinstance(cast(object, ref), str) or not ref for ref in evidence_refs):
            raise PlanStateTransitionError("Acceptance evidence references must be nonempty strings.")
        serialized = _plan_json(plan)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        accepted = [r for r in self._records(plan.plan_id) if r.kind == "m3.orchestration.accepted"]
        if accepted:
            body = accepted[0].body
            if body["plan_digest"] != digest or body["accepted_by"] != accepted_by or body["evidence_refs"] != (evidence_refs or []):
                raise PlanStateTransitionError("Plan ID already binds different acceptance evidence.")
            return accepted[0].record_id
        now = datetime.datetime.now(datetime.timezone.utc)
        deadline = (now + datetime.timedelta(seconds=plan.bounds.timeout_seconds)).isoformat()
        record = self._append(plan, "accepted", "accepted", {
            "plan_json": serialized, "plan_digest": digest,
            "accepted_by": accepted_by, "evidence_refs": evidence_refs or [],
            "accepted_at": now.isoformat(), "deadline": deadline,
            "cost_budget": None, "cost_accounting": "NOT_CONFIGURED",
        })
        return record.record_id

    def recover(self, plan: Plan) -> tuple[dict[str, StepExecutionResult], str]:
        records = self._records(plan.plan_id)
        accepted = [r for r in records if r.kind == "m3.orchestration.accepted"]
        digest = hashlib.sha256(_plan_json(plan).encode("utf-8")).hexdigest()
        if len(accepted) != 1 or accepted[0].body.get("plan_digest") != digest:
            raise PlanStateTransitionError("Execution requires this exact explicitly accepted plan.")
        results: dict[str, StepExecutionResult] = {}
        for record in records:
            if record.kind in ("m3.orchestration.running", "m3.orchestration.result"):
                body = record.body
                if body.get("plan_digest") != digest:
                    raise PlanStateTransitionError("Step evidence binds another plan digest.")
                result = StepExecutionResult.from_dict(body["result"])
                results[result.step_id] = result
        return results, str(accepted[0].body["deadline"])

    def record_attempt(self, plan: Plan, result: StepExecutionResult, owner: str, *, running: bool) -> None:
        if not owner or result.step_id not in {s.step_id for s in plan.steps} or result.attempt < 1:
            raise PlanStateTransitionError("Attempt evidence must bind a valid plan step, attempt and owner.")
        if running and result.status != "MAY_HAVE_STARTED":
            raise PlanStateTransitionError("Pre-invocation evidence cannot assert a terminal outcome.")
        if not running and result.status not in ("COMPLETED", "FAILED", "MAY_HAVE_STARTED"):
            raise PlanStateTransitionError("Unsupported execution certainty.")
        if not running:
            prior = [r for r in self._records(plan.plan_id) if r.kind == "m3.orchestration.running"
                     and r.body["result"]["step_id"] == result.step_id
                     and r.body["result"]["attempt"] == result.attempt]
            if len(prior) != 1 or prior[0].body["owner"] != owner:
                raise PlanStateTransitionError("Terminal evidence requires its committed invocation owner.")
        self._append(plan, "running" if running else "result",
                     f"{result.step_id}:{result.attempt}:{'running' if running else 'result'}", {
            "plan_digest": hashlib.sha256(_plan_json(plan).encode("utf-8")).hexdigest(),
            "owner": owner, "certainty": "MAY_HAVE_STARTED" if running else result.status,
            "result": result.to_dict(),
        })


class PlanExecutor:
    """Best-effort callable executor with optional durable accepted evidence.

    Strict hard-deadline mode fails closed until a cancellable runtime is bound.
    Checking an arbitrary callback's duration is not hard cancellation.
    """

    def __init__(self, journal: RecordPlanJournal | None = None,
                 *, require_hard_deadline: bool = False) -> None:
        self.journal = journal
        self.require_hard_deadline = require_hard_deadline

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
        if self.require_hard_deadline:
            raise UnsupportedExecutionBoundError("No hard-cancellable runtime is bound; strict production execution is unsupported.")
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
        step_ids = {s.step_id for s in plan.steps}
        for key, result in initial_results.items():
            if key not in step_ids or result.step_id != key or result.attempt < 1:
                raise PlanStateTransitionError("Recorded result does not bind to this plan's step.")
            if result.status not in ("COMPLETED", "FAILED", "MAY_HAVE_STARTED"):
                raise PlanStateTransitionError("Unsupported recorded step status; safe recovery requires certainty.")
        prior_attempts = sum(result.attempt for result in initial_results.values())

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
            previous = results.get(step.step_id)
            attempt = previous.attempt + 1 if previous is not None else 1
            max_step_attempts = min(step.max_attempts, plan.bounds.max_attempts)
            step_completed = False

            while attempt <= max_step_attempts:
                if prior_attempts + steps_executed_in_this_run >= plan.bounds.max_steps:
                    raise PlanBoundExceededError(
                        f"Execution of plan '{plan.plan_id}' exhausted max_steps across attempts/resumes."
                    )
                # Check timeout before each attempt
                if (time.monotonic() - start_time) > plan.bounds.timeout_seconds:
                    raise PlanBoundExceededError(
                        f"Execution of plan '{plan.plan_id}' exceeded timeout limit "
                        f"of {plan.bounds.timeout_seconds}s."
                    )

                if deadline is not None and datetime.datetime.now(datetime.timezone.utc) >= deadline:
                    raise PlanBoundExceededError("Accepted plan's absolute deadline has expired.")
                if self.journal is not None:
                    self.journal.record_attempt(plan, StepExecutionResult(
                        step.step_id, attempt, "MAY_HAVE_STARTED", error="RUNNING; outcome not committed"
                    ), owner, running=True)
                res = step_runner(PlanStep.from_dict(step.to_dict()), attempt)
                steps_executed_in_this_run += 1
                if res.step_id != step.step_id or res.attempt != attempt or res.status not in (
                    "COMPLETED", "FAILED", "MAY_HAVE_STARTED"
                ):
                    raise PlanStepExecutionError("Runner result does not bind to the invoked step and attempt.")
                results[step.step_id] = res
                if self.journal is not None:
                    self.journal.record_attempt(plan, res, owner, running=False)
                # A final step must not bypass the deadline. This detects an
                # overrun, but an arbitrary synchronous callback cannot be safely
                # interrupted; hard cancellation requires a bounded runtime port.
                if (time.monotonic() - start_time) > plan.bounds.timeout_seconds or (
                    deadline is not None and datetime.datetime.now(datetime.timezone.utc) >= deadline
                ):
                    raise PlanBoundExceededError(
                        f"Execution of plan '{plan.plan_id}' exceeded timeout during step '{step.step_id}'."
                    )

                if res.status == "COMPLETED":
                    step_completed = True
                    break

                if res.status == "MAY_HAVE_STARTED":
                    if self.journal is not None or not step.idempotent:
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
