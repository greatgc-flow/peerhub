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
import dataclasses
import datetime
import math
import time
import hashlib
import json
import uuid
import copy
import sqlite3
import threading
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


class CostBoundExceededError(PlanBoundExceededError):
    """Reported cost reached or exceeded the accepted plan's cost budget (Invariant 9)."""


@dataclasses.dataclass(frozen=True)
class PlanBounds:
    max_depth: int = 5
    max_steps: int = 50
    max_fanout: int = 10
    max_attempts: int = 3
    timeout_seconds: float = 300.0
    cost_budget: float | None = None  # None = no budget. With a budget every attempt result must report a finite cost >= 0.

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
            cost_budget=None if data.get("cost_budget") is None else float(data["cost_budget"]),
        )


@dataclasses.dataclass(frozen=True)
class PlanStep:
    step_id: str
    action_type: str
    payload: dict[str, Any]
    idempotent: bool = True
    depends_on: list[str] = dataclasses.field(default_factory=lambda: list[str]())
    max_attempts: int = 3
    # Deterministic conditions over COMMITTED outputs (no expression language):
    #   when    = {"step_id": <a dependency>, "key": k, "equals": v}  -> the step is SKIPPED unless that output matches exactly
    #   stop_if = {"key": k, "equals": v}                             -> after this step, a match STOPS the plan cleanly
    when: dict[str, Any] | None = None
    stop_if: dict[str, Any] | None = None

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
            when=dict(data["when"]) if data.get("when") is not None else None,
            stop_if=dict(data["stop_if"]) if data.get("stop_if") is not None else None,
        )


@dataclasses.dataclass(frozen=True)
class StepExecutionResult:
    step_id: str
    attempt: int
    status: str  # COMPLETED | FAILED | MAY_HAVE_STARTED | SKIPPED (engine decision, never a runner result)
    output: dict[str, Any] | None = None
    error: str | None = None
    cost: float | None = None  # None = not reported (UNKNOWN, never treated as zero)

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
            cost=float(data["cost"]) if data.get("cost") is not None else None,
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
    status: str  # COMPLETED | STOPPED | FAILED | BOUNDS_EXCEEDED | CANCELLED
    steps_executed: int
    step_results: dict[str, StepExecutionResult]
    elapsed_seconds: float
    total_cost: float = 0.0  # sum of REPORTED costs over every attempt, including earlier runs of the same durable plan


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
            "cost_budget": plan.bounds.cost_budget,
            "cost_accounting": "RESULT_REPORTED" if plan.bounds.cost_budget is not None else "NOT_CONFIGURED",
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

    def recover_cost(self, plan: Plan) -> float:
        """Total REPORTED cost over every committed attempt of this plan (all runs and resumes)."""
        seen: dict[tuple[str, int], float] = {}
        for record in self._records(plan.plan_id):
            if record.kind == "m3.orchestration.result":
                result = StepExecutionResult.from_dict(record.body["result"])
                if result.cost is not None:
                    seen[(result.step_id, result.attempt)] = result.cost
        return float(sum(seen.values()))

    def record_attempt(self, plan: Plan, result: StepExecutionResult, owner: str, *, running: bool) -> None:
        if not owner or result.step_id not in {s.step_id for s in plan.steps} or result.attempt < 1:
            raise PlanStateTransitionError("Attempt evidence must bind a valid plan step, attempt and owner.")
        if running and result.status != "MAY_HAVE_STARTED":
            raise PlanStateTransitionError("Pre-invocation evidence cannot assert a terminal outcome.")
        if not running and result.status not in ("COMPLETED", "FAILED", "MAY_HAVE_STARTED", "SKIPPED"):
            raise PlanStateTransitionError("Unsupported execution certainty.")
        if not running and result.status != "SKIPPED":
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
    """Bounded executor of an explicitly accepted plan.

    Serial by default; `parallel=True` runs up to `bounds.max_fanout` independent ready steps at once (the runner must then be
    thread-safe; durable journal appends are serialized). Without a hard-cancellable runner a callback that overruns the deadline
    is only *detected* after it returns: `require_hard_deadline=True` therefore needs a runner that declares
    `hard_cancellable = True` (see `runtime_port.HardDeadlineStepRunner`), which kills its work at the deadline.

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
                "Strict execution needs a step runner that declares hard_cancellable (e.g. runtime_port.HardDeadlineStepRunner).")
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


class HardDeadlineStepRunner:
    """Step runner over a runtime port that can really cancel: at the plan deadline the work is KILLED, not merely noticed.

    The port is duck-typed (`submit/get/cancel` plus `hard_cancel = True`, as `runtime_port.ProcessRuntimeAdapter` declares), so
    this module imports no other extension; the composition root wires them. `PlanExecutor(require_hard_deadline=True)` accepts only
    runners declaring `hard_cancellable`. A confirmed kill yields a FAILED result (the effect stopped); an unconfirmed kill
    (the port's `cancel` raised) yields MAY_HAVE_STARTED, which is never blindly replayed.
    """

    hard_cancellable = True

    def __init__(self, port: Any, *, poll_seconds: float = 0.05, cost_key: str = "cost") -> None:
        if getattr(port, "hard_cancel", False) is not True:
            raise TypeError("A hard-deadline runner needs a runtime port that declares hard_cancel (cancellation that is confirmed).")
        self._port = port
        self._poll = poll_seconds
        self._cost_key = cost_key
        self._deadline: float | None = None

    def set_deadline(self, monotonic_deadline: float) -> None:
        self._deadline = monotonic_deadline

    def __call__(self, step: PlanStep, attempt: int) -> StepExecutionResult:
        try:
            job = self._port.submit(step.action_type, step.payload)
        except Exception as exc:  # rejected before any process started: a safe, certain failure
            return StepExecutionResult(step.step_id, attempt, "FAILED", error=f"not started: {exc}")
        terminal = ("COMPLETED", "FAILED", "CANCELLED")
        while job.status not in terminal:
            if self._deadline is not None and time.monotonic() >= self._deadline:
                try:
                    job = self._port.cancel(job.job_id)
                except Exception as exc:
                    return StepExecutionResult(step.step_id, attempt, "MAY_HAVE_STARTED", error=f"deadline reached; {exc}")
                if job.status == "CANCELLED":
                    return StepExecutionResult(step.step_id, attempt, "FAILED", error="deadline reached; work cancelled (process tree killed)")
                break  # the job had already finished on its own: its real outcome (and cost) is used, never discarded or replayed
            time.sleep(self._poll)
            job = self._port.get(job.job_id)
        if job.status == "COMPLETED" and job.output is not None:
            raw_cost = job.output.get(self._cost_key)
            cost = float(raw_cost) if isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool) else None
            return StepExecutionResult(step.step_id, attempt, "COMPLETED", output=job.output, cost=cost)
        return StepExecutionResult(step.step_id, attempt, "FAILED", error=job.error or job.status)
