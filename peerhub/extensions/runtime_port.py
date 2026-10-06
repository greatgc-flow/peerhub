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
import copy
import hashlib
import json
import math
from typing import Any, Callable, cast


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
        # The loopback accepts a deliberately small typed parameter-map schema,
        # not arbitrary JSON Schema or a security sandbox. Handlers are trusted.
        if not capability.capability_id or not isinstance(cast(object, capability.schema), dict) or not callable(handler):
            raise ValueError("Capability registration requires an ID, schema map, and trusted handler.")
        for name, spec in capability.schema.items():
            self._validate_spec(name, spec)
        self._capabilities[capability.capability_id] = copy.deepcopy(capability)
        self._handlers[capability.capability_id] = handler

    @staticmethod
    def _validate_spec(name: str, spec: Any) -> None:
        types = ("number", "integer", "string", "boolean", "object", "array", "null")
        if not isinstance(cast(object, name), str) or not name:
            raise ValueError("Capability parameters require nonempty names.")
        if isinstance(spec, str):
            if spec not in types:
                raise ValueError(f"Unsupported parameter type: {spec}")
            return
        if not isinstance(spec, dict):
            raise ValueError(f"Invalid parameter schema for '{name}'.")
        spec = cast(dict[str, Any], spec)
        if spec.get("type") not in types:
            raise ValueError(f"Invalid parameter schema for '{name}'.")
        allowed = {"type", "enum"}
        declared_type = spec["type"]
        if declared_type in ("number", "integer"):
            allowed.update(("minimum", "maximum"))
        if declared_type == "string":
            allowed.update(("minLength", "maxLength"))
        if declared_type == "array":
            allowed.update(("minItems", "maxItems"))
        if set(spec) - allowed:
            raise ValueError(f"Unsupported constraints for '{name}'.")
        for key in allowed - {"type", "enum"}:
            if key in spec:
                value = spec[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"Invalid bound '{key}' for '{name}'.")
                if key not in ("minimum", "maximum") and (not isinstance(value, int) or value < 0):
                    raise ValueError(f"Size bounds must be nonnegative integers for '{name}'.")
        for lower, upper in (("minimum", "maximum"), ("minLength", "maxLength"), ("minItems", "maxItems")):
            if lower in spec and upper in spec and spec[lower] > spec[upper]:
                raise ValueError(f"Inverted bounds for '{name}'.")
        if "enum" in spec and (not isinstance(spec["enum"], list) or not spec["enum"]):
            raise ValueError(f"Enum must be a nonempty list for '{name}'.")

    @staticmethod
    def _validate_value(name: str, value: Any, spec: Any) -> None:
        declared_type = spec if isinstance(spec, str) else spec["type"]
        valid = {
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "string": isinstance(value, str),
            "boolean": isinstance(value, bool),
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "null": value is None,
        }[declared_type]
        if not valid:
            raise ValueError(f"Parameter '{name}' must be {declared_type}.")
        if declared_type == "number" and isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"Parameter '{name}' must be finite.")
        if isinstance(spec, dict):
            spec = cast(dict[str, Any], spec)
            if "enum" in spec and not any(type(value) is type(item) and value == item for item in spec["enum"]):
                raise ValueError(f"Parameter '{name}' is outside its enum.")
            for bound in ("minimum", "maximum"):
                if bound in spec and ((bound == "minimum" and value < spec[bound]) or (bound == "maximum" and value > spec[bound])):
                    raise ValueError(f"Parameter '{name}' violates {bound}.")
            for lower, upper in (("minLength", "maxLength"), ("minItems", "maxItems")):
                if lower in spec and len(cast(Any, value)) < spec[lower] or upper in spec and len(cast(Any, value)) > spec[upper]:
                    raise ValueError(f"Parameter '{name}' violates its size bounds.")

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

        # Validate before handler invocation or job publication.
        if not isinstance(cast(object, params), dict) or set(params) - set(cap.schema):
            raise ValueError("Invocation parameters must match the declared capability schema.")
        for field, spec in cap.schema.items():
            if field not in params:
                raise ValueError(
                    f"Missing required parameter '{field}' for capability '{capability_id}'."
                )
            self._validate_value(field, params[field], spec)
        try:
            json.dumps(params, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invocation parameters must be finite JSON values.") from exc
        params = copy.deepcopy(params)

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
            return copy.deepcopy(job)

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
            return copy.deepcopy(job)

        try:
            output = handler(copy.deepcopy(params))
            if not isinstance(cast(object, output), dict):
                raise ValueError("Capability output must be a JSON object.")
            json.dumps(output, allow_nan=False)
            job = InvocationJob(
                job_id=job_id,
                capability_id=capability_id,
                params=params,
                status="COMPLETED",
                output=copy.deepcopy(output),
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
        return copy.deepcopy(job)

    def get(self, job_id: str) -> InvocationJob:
        if job_id not in self._jobs:
            raise RuntimeJobNotFoundError(f"Job '{job_id}' not found.")
        return copy.deepcopy(self._jobs[job_id])

    def cancel(self, job_id: str) -> InvocationJob:
        job = self.get(job_id)
        if job.status in ("COMPLETED", "FAILED", "CANCELLED"):
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
        return copy.deepcopy(updated)

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
