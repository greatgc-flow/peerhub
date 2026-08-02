"""Process-supervision contracts for Slice 5.

This module contains importable Step 2 contract shapes only. Process-event
reduction, timeout selection, cancellation escalation, and OS supervision
are implemented in later Slice 5 steps.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import ErrorCode, require_text

from .contract import ExecutionOutcome


class TerminalClassification(str, Enum):
    """Currently ratified process classifications used by Slice 5 tests.

    This is intentionally not claimed to be the complete vocabulary. The
    Phase 0 contract still lists full terminal-classification closure as an
    open decision.
    """

    START_UNCERTAIN = "START_UNCERTAIN"
    SILENCE_TIMEOUT = "SILENCE_TIMEOUT"
    PROCESS_TIMEOUT = "PROCESS_TIMEOUT"
    EXIT_NON_ZERO = "EXIT_NON_ZERO"


@dataclass(frozen=True, init=False)
class ProcessCleanupEvidence:
    """Attached process-tree cleanup failure evidence.

    Identity values here are controlled-fake identity tokens. Production
    process fencing continues to require ProcessBirthIdentity.
    """

    _unresolved_identities: tuple[int, ...] = field(repr=False)
    _reason: ErrorCode = field(repr=False)

    def __init__(
        self,
        unresolved_identities: Sequence[int],
    ) -> None:
        normalized = tuple(unresolved_identities)
        for identity in normalized:
            if type(identity) is not int or identity < 1:
                raise ValueError(
                    "unresolved identities must be positive integers"
                )

        object.__setattr__(
            self,
            "_unresolved_identities",
            normalized,
        )
        object.__setattr__(
            self,
            "_reason",
            ErrorCode.CANCELLATION_CLEANUP_FAILED,
        )

    @property
    def unresolved_identities(self) -> list[int]:
        """Return a defensive list matching the compatibility surface."""

        return list(self._unresolved_identities)

    @property
    def reason(self) -> str:
        """Return the stable Protocol v1 error-code value."""

        return self._reason.value


@dataclass(frozen=True)
class ProcessSupervisionOutcome:
    """Execution facts plus process-supervisor evidence.

    ``ExecutionOutcome`` remains the sole owner of start, exit, timeout,
    cancellation, and execution-certainty facts.
    """

    execution_outcome: ExecutionOutcome
    stream_events_ordered: bool
    canonical_stream: bytes
    terminal_classification: TerminalClassification | None
    cleanup_evidence: ProcessCleanupEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.execution_outcome, ExecutionOutcome):
            raise ValueError(
                "execution_outcome must be ExecutionOutcome"
            )
        if type(self.stream_events_ordered) is not bool:
            raise ValueError(
                "stream_events_ordered must be a boolean"
            )
        if type(self.canonical_stream) is not bytes:
            raise ValueError("canonical_stream must be bytes")
        if (
            self.terminal_classification is not None
            and not isinstance(
                self.terminal_classification,
                TerminalClassification,
            )
        ):
            raise ValueError(
                "terminal_classification must be "
                "TerminalClassification or null"
            )
        if (
            self.cleanup_evidence is not None
            and not isinstance(
                self.cleanup_evidence,
                ProcessCleanupEvidence,
            )
        ):
            raise ValueError(
                "cleanup_evidence must be "
                "ProcessCleanupEvidence or null"
            )

    @property
    def exit_code(self) -> int | None:
        return self.execution_outcome.exit_code

    @property
    def effect_certainty(self) -> ExecutionCertainty:
        return self.execution_outcome.execution_certainty

    @property
    def cleanup_failed(self) -> bool:
        return self.cleanup_evidence is not None


@dataclass(frozen=True)
class InterruptedAttemptRecoveryOutcome:
    """Safe classification returned by interrupted-attempt recovery."""

    terminal_classification: TerminalClassification
    execution_outcome: ExecutionOutcome
    automatic_replay_authorized: bool
    journal_digest: str

    def __post_init__(self) -> None:
        if not isinstance(
            self.terminal_classification,
            TerminalClassification,
        ):
            raise ValueError(
                "terminal_classification must be "
                "TerminalClassification"
            )
        if not isinstance(self.execution_outcome, ExecutionOutcome):
            raise ValueError(
                "execution_outcome must be ExecutionOutcome"
            )
        if type(self.automatic_replay_authorized) is not bool:
            raise ValueError(
                "automatic_replay_authorized must be a boolean"
            )
        object.__setattr__(
            self,
            "journal_digest",
            require_text(self.journal_digest, "journal_digest"),
        )

        if (
            self.effect_certainty
            is ExecutionCertainty.MAY_HAVE_STARTED
            and self.automatic_replay_authorized
        ):
            raise ValueError(
                "MAY_HAVE_STARTED recovery cannot authorize "
                "automatic replay"
            )

        if (
            self.terminal_classification
            is TerminalClassification.START_UNCERTAIN
        ):
            if self.execution_outcome.started:
                raise ValueError(
                    "START_UNCERTAIN cannot assert observed process start"
                )
            if (
                self.effect_certainty
                is not ExecutionCertainty.MAY_HAVE_STARTED
            ):
                raise ValueError(
                    "START_UNCERTAIN requires MAY_HAVE_STARTED"
                )

    @property
    def effect_certainty(self) -> ExecutionCertainty:
        return self.execution_outcome.execution_certainty


class CancellationLadder:
    """Importable Step 2 marker for the later pure ladder reducer."""

    __slots__ = ()


class ProcessSupervisor:
    """Importable process-supervisor shape; implementation is Step 5."""

    def __init__(
        self,
        *,
        cancellation_ladder: CancellationLadder | None = None,
    ) -> None:
        if (
            cancellation_ladder is not None
            and not isinstance(
                cancellation_ladder,
                CancellationLadder,
            )
        ):
            raise ValueError(
                "cancellation_ladder must be CancellationLadder or null"
            )
        self._cancellation_ladder = cancellation_ladder

    def on_spawned(
        self,
        *,
        pid: int,
        process_creation_time: int | None = None,
    ) -> None:
        raise NotImplementedError("implemented in Slice 5 Step 5")

    def on_chunk(
        self,
        chunk: bytes,
        *,
        timestamp_ms: int,
    ) -> None:
        raise NotImplementedError("implemented in Slice 5 Step 5")

    def on_exit(self, *, exit_code: int) -> None:
        raise NotImplementedError("implemented in Slice 5 Step 5")

    def on_tree_state(
        self,
        *,
        initial_identities: Sequence[int],
    ) -> None:
        raise NotImplementedError("implemented in Slice 5 Step 5")

    def on_cleanup_error(
        self,
        *,
        unresolved_identities: Sequence[int],
    ) -> None:
        raise NotImplementedError("implemented in Slice 5 Step 5")

    def begin_cancellation(self) -> None:
        raise NotImplementedError("implemented in Slice 5 Step 5")

    def trigger_silence_timeout(self) -> None:
        raise NotImplementedError("implemented in Slice 5 Step 5")

    def trigger_process_deadline(self) -> None:
        raise NotImplementedError("implemented in Slice 5 Step 5")

    def finalize_execution_outcome(
        self,
    ) -> ProcessSupervisionOutcome:
        raise NotImplementedError("implemented in Slice 5 Step 5")
