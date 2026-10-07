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
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from typing import Any, Callable, Sequence, cast


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


class RuntimeCancellationError(RuntimePortError):
    """The process tree could not be confirmed dead: the job is NOT reported cancelled (its effects may continue)."""


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

    def _checked_params(self, capability_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Validate invocation parameters against the capability schema before any handler/process is touched."""
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
        return copy.deepcopy(params)

    def submit(self, capability_id: str, params: dict[str, Any]) -> InvocationJob:
        params = self._checked_params(capability_id, params)

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


# -----------------------------------------------------------------------------
# Hard-cancellable binding (M3.6): capabilities run as separate OS processes
# -----------------------------------------------------------------------------
_TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")
_PLACEHOLDER_WHOLE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_PLACEHOLDER_ANYWHERE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


class ProcessRuntimeAdapter(LoopbackRuntimeAdapter):
    """Runs each capability as its own process so `cancel` can really stop the work (process-TREE kill, confirmed).

    A capability is a fixed argv template registered by the operator: `shell=False`, whole-element `{param}` placeholders only,
    parameters validated against the declared schema first. There is no way to submit an arbitrary command line (Invariant 12).
    A job's stdout must be one JSON object (bounded); anything else fails the job. The registered argv is trusted, like the
    loopback handlers; this is failure isolation and cancellation, not a security sandbox.
    """

    hard_cancel = True  # declares the capability the orchestration runner requires: cancel() kills and CONFIRMS the whole tree

    def __init__(self, *, max_output_bytes: int = 1_000_000, cancel_wait_seconds: float = 10.0) -> None:
        super().__init__()
        if type(max_output_bytes) is not int or max_output_bytes < 1:
            raise ValueError("max_output_bytes must be a positive integer.")
        self._max_output = max_output_bytes
        self._cancel_wait = cancel_wait_seconds
        self._argv: dict[str, tuple[str, ...]] = {}
        self._procs: dict[str, tuple[subprocess.Popen[bytes], str]] = {}

    def register_command(self, capability: RuntimeCapability, argv: Sequence[str]) -> None:
        template = tuple(argv)
        if not template or any(not isinstance(cast(object, a), str) or not a for a in template):
            raise ValueError("A command capability needs a non-empty argv of non-empty strings.")
        exe = template[0]
        if _PLACEHOLDER_ANYWHERE.search(exe) or (not os.path.isabs(exe) and shutil.which(exe) is None):
            raise ValueError("argv[0] must be an absolute path or an executable on PATH, never a placeholder.")
        for part in template[1:]:
            whole = _PLACEHOLDER_WHOLE.match(part)
            if whole is not None and whole.group(1) not in capability.schema:
                raise ValueError(f"Placeholder {part} does not name a declared parameter.")
            if whole is None and _PLACEHOLDER_ANYWHERE.search(part):
                raise ValueError("A placeholder must be a whole argv element; embedded {name} is ambiguous.")
        self.register_capability(capability, lambda _params: {})  # reuse schema validation; execution never uses the handler
        self._argv[capability.capability_id] = template

    def submit(self, capability_id: str, params: dict[str, Any]) -> InvocationJob:
        params = self._checked_params(capability_id, params)
        template = self._argv.get(capability_id)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        job_id = f"job-{uuid.uuid4().hex[:16]}"
        if capability_id in self._injected_failures or template is None:
            job = InvocationJob(job_id, capability_id, params, "FAILED",
                                error=self._injected_failures.get(capability_id, "No command registered"),
                                created_at=now, completed_at=now)
            self._jobs[job_id] = job
            return copy.deepcopy(job)
        argv = [template[0]] + [str(params[m.group(1)]) if (m := _PLACEHOLDER_WHOLE.match(p)) else p for p in template[1:]]
        handle, out_path = tempfile.mkstemp(prefix="peerhub-job-", suffix=".out")
        os.close(handle)
        kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "shell": False}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
        try:
            with open(out_path, "wb") as sink:
                proc = cast("subprocess.Popen[bytes]", subprocess.Popen(argv, stdout=sink, **kwargs))
        except OSError as exc:
            os.unlink(out_path)
            job = InvocationJob(job_id, capability_id, params, "FAILED", error=f"start failed: {type(exc).__name__}",
                                created_at=now, completed_at=now)
            self._jobs[job_id] = job
            return copy.deepcopy(job)
        self._procs[job_id] = (proc, out_path)
        job = InvocationJob(job_id, capability_id, params, "RUNNING", created_at=now)
        self._jobs[job_id] = job
        return copy.deepcopy(job)

    def _settle(self, job_id: str) -> InvocationJob:
        job = self._jobs[job_id]
        entry = self._procs.get(job_id)
        if job.status in _TERMINAL or entry is None:
            return job
        proc, out_path = entry
        code = proc.poll()
        if code is None:
            return job
        done_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        output: dict[str, Any] | None = None
        error: str | None = None
        try:
            with open(out_path, "rb") as f:
                raw = f.read(self._max_output + 1)
        except OSError:
            raw = b""
        if code != 0:
            error = f"exit code {code}"
        elif len(raw) > self._max_output:
            error = "output exceeds max_output_bytes"
        else:
            try:
                parsed = json.loads(raw.decode("utf-8"))
                if isinstance(parsed, dict):
                    json.dumps(parsed, allow_nan=False)
                    output = cast(dict[str, Any], parsed)
                else:
                    error = "output is not a JSON object"
            except (ValueError, UnicodeDecodeError):
                error = "output is not valid JSON"
        self._procs.pop(job_id, None)
        try:
            os.unlink(out_path)
        except OSError:
            pass
        settled = InvocationJob(job.job_id, job.capability_id, job.params, "COMPLETED" if error is None else "FAILED",
                                output=output, error=error, created_at=job.created_at, completed_at=done_at)
        self._jobs[job_id] = settled
        return settled

    def get(self, job_id: str) -> InvocationJob:
        if job_id not in self._jobs:
            raise RuntimeJobNotFoundError(f"Job '{job_id}' not found.")
        return copy.deepcopy(self._settle(job_id))

    @staticmethod
    def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
        if os.name == "nt":
            done = subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15)
            if done.returncode != 0 and not (done.returncode == 128 and proc.poll() is not None):  # 128: parent already gone; orphans of a dead parent cannot be enumerated (known limit)
                raise OSError(f"taskkill exited {done.returncode}")
        else:
            import signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def cancel(self, job_id: str) -> InvocationJob:
        job = self.get(job_id)
        if job.status in _TERMINAL:
            return job
        proc, out_path = self._procs[job_id]
        try:
            self._kill_tree(proc)
            proc.wait(timeout=self._cancel_wait)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise RuntimeCancellationError(f"Job '{job_id}' could not be confirmed dead: {type(exc).__name__}") from exc
        self._procs.pop(job_id, None)
        try:
            os.unlink(out_path)
        except OSError:
            pass
        cancelled = InvocationJob(job.job_id, job.capability_id, job.params, "CANCELLED", error="Job cancelled; process tree killed",
                                  created_at=job.created_at, completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        self._jobs[job_id] = cancelled
        return copy.deepcopy(cancelled)

    def collect(self, job_id: str) -> dict[str, Any]:
        self.get(job_id)  # settle first
        return super().collect(job_id)
