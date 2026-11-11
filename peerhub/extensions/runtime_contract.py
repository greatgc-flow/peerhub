"""Provider-neutral runtime port for the logical Execution Kernel.

This module intentionally imports neither the concrete Bridge nor a vendor adapter.
Importers of the historical bridge symbols continue to get these *same* class objects.
The port describes capabilities; it never owns execution certainty or persistence.
"""
from __future__ import annotations

from typing import Any, Iterable, Protocol


class RuntimeTargetError(RuntimeError):
    """Ambiguous runtime failure (process/session may have started)."""

    def __init__(self, message: str = "", *, termination_confirmed: bool | None = None) -> None:
        super().__init__(message)
        self.termination_confirmed = termination_confirmed


class PrespawnError(RuntimeTargetError):
    """Failure proven to be before any process/session start (resolves the invocation marker; certainty stays NOT_STARTED)."""


class ContextLostError(PrespawnError):
    """Adapter reports context/session loss (compaction, context limit) before the run began: safe NOT_STARTED retry on a fresh generation."""


RuntimeEvent = tuple[str, Any] | tuple[str, Any, Any]  # ("started", exec_id) | ("timeout", seconds) | ("terminal", {"response": ...}) | ...


class RuntimeTarget(Protocol):
    runtime_kind: str

    @property
    def resumable(self) -> bool: ...  # read capability; custom attributes also satisfy this boundary

    def fingerprint(self) -> str: ...
    def binding(self) -> str: ...  # model/profile binding; a mapping is only resumable under the same binding
    def create_session(self) -> str: ...
    def resume_session(self, external_session_id: str, *, vendor_session_id: str | None = None) -> str: ...  # "ok" | "missing" | "unsupported" | "rejected"
    def deliver(self, external_session_id: str, record: Any, catch_up: list[Any]) -> Iterable[RuntimeEvent]: ...


class SteerableTarget(Protocol):
    def steer(self, external_session_id: str, record: Any) -> None: ...
