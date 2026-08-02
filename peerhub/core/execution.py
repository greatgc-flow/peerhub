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


class TransportKind(str, Enum):
    """Process transport kinds (ARCHITECTURE.md module inventory, line ~85:
    "execution.py -- NEW (Round 6-7): shared TransportKind/TransportLimits/
    ProcessTerminalEvidence/... -- adapters AND dispatch import from here;
    neither imports the other for these types"). Originally placed in
    adapters/contract.py by mistake; moved here 2026-08-02 per cx's
    cross-review citation of this exact inventory line."""

    PIPE = "PIPE"
    PTY = "PTY"


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
class TransportLimits:
    """Relative execution budgets for one dispatch invocation.

    Lives here rather than in ``dispatch`` or ``adapters`` (ARCHITECTURE.md
    Round 6-7, Finding 3): ``PeerAdapter.plan_invocation`` and
    ``InvocationPlan`` (adapters-owned) both need this type, and
    ``dispatch.service`` also needs it, but ``adapters`` must never import
    ``dispatch`` -- a shared leaf module both sides can import avoids the
    cycle. Values are relative budgets; the runner converts them to
    absolute deadlines (``Deadline``) using its own injected clock.
    """

    process_timeout_ms: int
    silence_timeout_ms: int
    max_output_bytes: int

    def __post_init__(self) -> None:
        # Nonnegative, not positive: neither ARCHITECTURE.md nor the
        # SLICE5-KICKOFF-R1.md synthesis establishes a positivity floor for
        # these fields, and this file's own sibling Deadline.budget_ms
        # already allows 0 for the same "budget in milliseconds" concept --
        # cross-review finding, cx, 2026-08-02 (was `< 1`, an unratified
        # constraint this file didn't otherwise follow).
        for name in (
            "process_timeout_ms",
            "silence_timeout_ms",
            "max_output_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(
                    f"{name} must be a nonnegative integer"
                )


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
