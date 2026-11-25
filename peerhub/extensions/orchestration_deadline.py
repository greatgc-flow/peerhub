"""Hard-deadline execution over a cancellable runtime port."""
from __future__ import annotations

import time
from typing import Any

from peerhub.extensions.orchestration_model import PlanStep, StepExecutionResult


class HardDeadlineStepRunner:
    """Step runner over a runtime port that can really cancel: at the plan deadline the work is KILLED, not merely noticed.

    The port is duck-typed (`submit/get/cancel` plus `hard_cancel = True`, as `runtime_port.ProcessRuntimeAdapter` declares), and
    the composition root wires the port to the runner. `PlanExecutor(require_hard_deadline=True)` accepts only
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
