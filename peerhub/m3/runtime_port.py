"""Remote Runtime Port & Adapter Contract (M3.6).

Implements Core Invariant 12:
- Remote runtime capability-bound, no general remote shell.
- Public port operations: probe, submit, get, cancel, collect.
- Deterministic loopback/fake adapter failure isolation.
- Pure Python standard library implementation (REL-009).
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
from typing import Any, Callable


class RuntimePortError(Exception):
    """Base exception for runtime port operations."""


class GeneralRemoteShellForbiddenError(RuntimePortError):
    """Raised when arbitrary shell execution is attempted (Invariant 12)."""


class RuntimeCapabilityNotFoundError(RuntimePortError):
    """Raised when requested capability is not found."""


class RuntimeJobNotFoundError(RuntimePortError):
    """Raised when job ID is not found."""


class RuntimeJobNotReadyError(RuntimePortError):
    """Raised when collecting output from an incomplete job."""


class RuntimeJobFailedError(RuntimePortError):
    """Raised when collecting output from a failed or cancelled job."""


@dataclasses.dataclass(frozen=True)
class RuntimeCapability:
    capability_id: str
    name: str
    description: str
    schema: dict[str, Any] = dataclasses.field(default_factory=lambda: dict[str, Any]())

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeCapability:
        return cls(
            capability_id=str(data["capability_id"]),
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            schema=dict(data.get("schema", {})),
        )


@dataclasses.dataclass(frozen=True)
class InvocationJob:
    job_id: str
    capability_id: str
    params: dict[str, Any]
    status: str  # SUBMITTED | RUNNING | COMPLETED | FAILED | CANCELLED
    output: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = dataclasses.field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InvocationJob:
        return cls(
            job_id=str(data["job_id"]),
            capability_id=str(data["capability_id"]),
            params=dict(data.get("params", {})),
            status=str(data["status"]),
            output=dict(data["output"]) if data.get("output") is not None else None,
            error=str(data["error"]) if data.get("error") is not None else None,
            created_at=str(
                data.get(
                    "created_at",
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                )
            ),
            completed_at=str(data["completed_at"])
            if data.get("completed_at") is not None
            else None,
        )


class LoopbackRuntimeAdapter:
    """Deterministic fake/loopback adapter implementing the Runtime Port contract."""

    def __init__(self) -> None:
        self._capabilities: dict[str, RuntimeCapability] = {}
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._injected_failures: dict[str, str] = {}  # capability_id -> error_message
        self._jobs: dict[str, InvocationJob] = {}

    def register_capability(
        self,
        capability: RuntimeCapability,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        self._capabilities[capability.capability_id] = capability
        self._handlers[capability.capability_id] = handler

    def inject_failure(self, capability_id: str, error_message: str) -> None:
        """Inject controlled failure for testing error isolation."""
        self._injected_failures[capability_id] = error_message

    def clear_failure(self, capability_id: str) -> None:
        self._injected_failures.pop(capability_id, None)

    def invoke_raw_shell(self, command: str) -> None:
        """Invariant 12: General remote shell is strictly forbidden."""
        raise GeneralRemoteShellForbiddenError(
            f"Arbitrary shell command '{command}' execution is forbidden. "
            f"Remote execution must be capability-bound (Invariant 12)."
        )

    def probe(self, capability_id: str) -> dict[str, Any]:
        if capability_id not in self._capabilities:
            return {"capability_id": capability_id, "available": False, "error": "Not registered"}

        if capability_id in self._injected_failures:
            return {
                "capability_id": capability_id,
                "available": False,
                "error": self._injected_failures[capability_id],
            }

        return {
            "capability_id": capability_id,
            "available": True,
            "name": self._capabilities[capability_id].name,
        }

    def submit(self, capability_id: str, params: dict[str, Any]) -> InvocationJob:
        if capability_id not in self._capabilities:
            raise RuntimeCapabilityNotFoundError(
                f"Capability '{capability_id}' not registered on runtime port."
            )

        cap = self._capabilities[capability_id]

        # Parameter schema validation
        for field in cap.schema:
            if field not in params:
                raise ValueError(
                    f"Missing required parameter '{field}' for capability '{capability_id}'."
                )

        now = datetime.datetime.now(datetime.timezone.utc)
        job_id = f"job-{hashlib.sha256(f'{capability_id}:{now.isoformat()}'.encode('utf-8')).hexdigest()[:16]}"

        # Check injected failure
        if capability_id in self._injected_failures:
            job = InvocationJob(
                job_id=job_id,
                capability_id=capability_id,
                params=params,
                status="FAILED",
                error=self._injected_failures[capability_id],
                created_at=now.isoformat(),
                completed_at=now.isoformat(),
            )
            self._jobs[job_id] = job
            return job

        # Synchronous loopback execution
        handler = self._handlers.get(capability_id)
        if handler is None:
            job = InvocationJob(
                job_id=job_id,
                capability_id=capability_id,
                params=params,
                status="FAILED",
                error="Handler not implemented",
                created_at=now.isoformat(),
                completed_at=now.isoformat(),
            )
            self._jobs[job_id] = job
            return job

        try:
            output = handler(params)
            job = InvocationJob(
                job_id=job_id,
                capability_id=capability_id,
                params=params,
                status="COMPLETED",
                output=output,
                created_at=now.isoformat(),
                completed_at=now.isoformat(),
            )
        except Exception as exc:
            job = InvocationJob(
                job_id=job_id,
                capability_id=capability_id,
                params=params,
                status="FAILED",
                error=str(exc),
                created_at=now.isoformat(),
                completed_at=now.isoformat(),
            )

        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> InvocationJob:
        if job_id not in self._jobs:
            raise RuntimeJobNotFoundError(f"Job '{job_id}' not found.")
        return self._jobs[job_id]

    def cancel(self, job_id: str) -> InvocationJob:
        job = self.get(job_id)
        if job.status == "COMPLETED":
            return job

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        updated = InvocationJob(
            job_id=job.job_id,
            capability_id=job.capability_id,
            params=job.params,
            status="CANCELLED",
            output=job.output,
            error="Job cancelled by user request",
            created_at=job.created_at,
            completed_at=now_iso,
        )
        self._jobs[job_id] = updated
        return updated

    def collect(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job.status in ("SUBMITTED", "RUNNING"):
            raise RuntimeJobNotReadyError(
                f"Job '{job_id}' is in state '{job.status}' and not ready to collect."
            )
        if job.status in ("FAILED", "CANCELLED"):
            raise RuntimeJobFailedError(
                f"Cannot collect output: job '{job_id}' ended in state '{job.status}' (error: {job.error})."
            )
        assert job.output is not None
        return job.output
