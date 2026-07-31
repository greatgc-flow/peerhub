"""Execution certainty, process terminal evidence, and deadline primitives."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExecutionCertainty(str, Enum):
    """Canonical execution-certainty vocabulary frozen by Protocol V1."""

    NOT_STARTED = "NOT_STARTED"
    MAY_HAVE_STARTED = "MAY_HAVE_STARTED"
    STARTED = "STARTED"
    TERMINAL = "TERMINAL"


@dataclass(frozen=True)
class ProcessTerminalEvidence:
    """Terminal evidence observed from a child process execution."""

    exit_code: int
    signal: int | None = None
    terminated_at: int = 0

    def __post_init__(self) -> None:
        if type(self.exit_code) is not int:
            raise ValueError("exit_code must be an integer")
        if self.signal is not None and type(self.signal) is not int:
            raise ValueError("signal must be None or an integer")
        if type(self.terminated_at) is not int or self.terminated_at < 0:
            raise ValueError("terminated_at must be a nonnegative integer")


@dataclass(frozen=True)
class Deadline:
    """Execution deadline and remaining budget in milliseconds."""

    expires_at: int
    budget_ms: int

    def __post_init__(self) -> None:
        if type(self.expires_at) is not int or self.expires_at < 0:
            raise ValueError("expires_at must be a nonnegative integer")
        if type(self.budget_ms) is not int or self.budget_ms < 0:
            raise ValueError("budget_ms must be a nonnegative integer")

    def is_expired(self, now: int) -> bool:
        """Return True if the deadline has elapsed by the given timestamp."""

        return now >= self.expires_at
