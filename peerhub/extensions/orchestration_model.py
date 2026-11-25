"""Plans, execution results and errors shared by orchestration and its journal."""
from __future__ import annotations

import dataclasses
import datetime
from typing import Any


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
