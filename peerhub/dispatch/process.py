"""Process-supervision contracts for Slice 5.

This module contains pure reducers and types for DT-01 through DT-06 event reduction,
timeout selection, process tree observation, and cancellation escalation ladders.
"""

from __future__ import annotations

import threading

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from peerhub.core.execution import CancellationGrace, ExecutionCertainty
from peerhub.core.protocol import ErrorCode, require_text

from .contract import (
    ExecutionOutcome,
    ProcessBirthIdentity,
    TerminalClassification,
)


class ObservationState(str, Enum):
    """Observed state of a process identity within a supervised process tree."""

    TERMINATED = "TERMINATED"
    RUNNING = "RUNNING"
    IDENTITY_UNCERTAIN = "IDENTITY_UNCERTAIN"


TreeState = ObservationState


@dataclass(frozen=True)
class TreeProcessObservation:
    """Per-identity observation of a process in a process tree."""

    identity: ProcessBirthIdentity
    state: ObservationState
    observed_creation_time: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ProcessBirthIdentity):  # pyright: ignore[reportUnnecessaryIsInstance]
            if type(self.identity) is int and self.identity > 0:
                creation_time = (
                    self.observed_creation_time
                    if self.observed_creation_time is not None
                    else 0
                )
                object.__setattr__(
                    self,
                    "identity",
                    ProcessBirthIdentity(
                        pid=self.identity,
                        process_creation_time=creation_time,
                    ),
                )
            else:
                raise ValueError(
                    "identity must be ProcessBirthIdentity or positive int PID"
                )
        if not isinstance(self.state, ObservationState):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("state must be ObservationState")

    @property
    def pid(self) -> int:
        return self.identity.pid


class CancellationStage(str, Enum):
    """5-step cancellation ladder stages."""

    IDLE = "IDLE"
    SOFT_CANCEL = "SOFT_CANCEL"
    TERMINATE_TREE = "TERMINATE_TREE"
    KILL_TREE = "KILL_TREE"
    RECONCILE_TREE = "RECONCILE_TREE"
    COMPLETED = "COMPLETED"


class CancellationAction(str, Enum):
    """Action requested by the pure cancellation ladder reducer."""

    NONE = "NONE"
    SOFT_CANCEL = "SOFT_CANCEL"
    TERMINATE_TREE = "TERMINATE_TREE"
    KILL_TREE = "KILL_TREE"
    RECONCILE_TREE = "RECONCILE_TREE"


@dataclass(frozen=True)
class CancellationDecision:
    """Outcome of a cancellation ladder reduction step."""

    stage: CancellationStage
    action: CancellationAction
    next_deadline_ms: int | None
    unresolved_identities: tuple[int, ...] = ()
    all_terminated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.stage, CancellationStage):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("stage must be CancellationStage")
        if not isinstance(self.action, CancellationAction):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("action must be CancellationAction")
        if self.next_deadline_ms is not None and (
            type(self.next_deadline_ms) is not int
            or self.next_deadline_ms < 0
        ):
            raise ValueError(
                "next_deadline_ms must be a nonnegative int or None"
            )
        object.__setattr__(
            self,
            "unresolved_identities",
            tuple(self.unresolved_identities),
        )
        if type(self.all_terminated) is not bool:
            raise ValueError("all_terminated must be a boolean")


@dataclass(frozen=True)
class CancellationState:
    """Immutable state container for the cancellation ladder reducer."""

    stage: CancellationStage = CancellationStage.IDLE
    deadline_ms: int | None = None
    grace: CancellationGrace = field(default_factory=CancellationGrace)


class CancellationLadder:
    """Pure reducer for the 5-step process tree cancellation ladder.

    Signature: (state, observations, now_ms) -> (new_state, decision)
    States follow the 5-step sequence:
    PROCESS_DEADLINE/SILENCE_TIMEOUT -> SOFT_CANCEL -> TERMINATE_TREE -> KILL_TREE -> RECONCILE_TREE
    """

    def __init__(self, grace: CancellationGrace | None = None) -> None:
        self._grace = grace if grace is not None else CancellationGrace()

    @property
    def grace(self) -> CancellationGrace:
        return self._grace

    def start(
        self, now_ms: int = 0
    ) -> tuple[CancellationState, CancellationDecision]:
        """Initiate cancellation ladder from IDLE -> SOFT_CANCEL."""
        deadline = now_ms + self._grace.soft_cancel_grace_ms
        new_state = CancellationState(
            stage=CancellationStage.SOFT_CANCEL,
            deadline_ms=deadline,
            grace=self._grace,
        )
        decision = CancellationDecision(
            stage=CancellationStage.SOFT_CANCEL,
            action=CancellationAction.SOFT_CANCEL,
            next_deadline_ms=deadline,
            unresolved_identities=(),
            all_terminated=False,
        )
        return new_state, decision

    def step(
        self,
        current_state: CancellationState,
        observations: Sequence[TreeProcessObservation] = (),
        now_ms: int = 0,
    ) -> tuple[CancellationState, CancellationDecision]:
        """Pure reduction step: (state, observations, now) -> (new_state, decision)."""
        if current_state.stage is CancellationStage.IDLE:
            return self.start(now_ms)

        if current_state.stage is CancellationStage.COMPLETED:
            decision = CancellationDecision(
                stage=CancellationStage.COMPLETED,
                action=CancellationAction.NONE,
                next_deadline_ms=None,
                unresolved_identities=(),
                all_terminated=True,
            )
            return current_state, decision

        unresolved = [
            obs.identity.pid
            for obs in observations
            if obs.state
            in (ObservationState.RUNNING, ObservationState.IDENTITY_UNCERTAIN)
        ]
        all_terminated = len(observations) > 0 and len(unresolved) == 0

        if all_terminated:
            new_state = CancellationState(
                stage=CancellationStage.COMPLETED,
                deadline_ms=None,
                grace=self._grace,
            )
            decision = CancellationDecision(
                stage=CancellationStage.COMPLETED,
                action=CancellationAction.NONE,
                next_deadline_ms=None,
                unresolved_identities=(),
                all_terminated=True,
            )
            return new_state, decision

        deadline_expired = (
            current_state.deadline_ms is not None
            and now_ms >= current_state.deadline_ms
        )

        stage = current_state.stage
        if stage is CancellationStage.SOFT_CANCEL:
            if deadline_expired:
                next_deadline = now_ms + self._grace.terminate_tree_grace_ms
                new_state = CancellationState(
                    stage=CancellationStage.TERMINATE_TREE,
                    deadline_ms=next_deadline,
                    grace=self._grace,
                )
                decision = CancellationDecision(
                    stage=CancellationStage.TERMINATE_TREE,
                    action=CancellationAction.TERMINATE_TREE,
                    next_deadline_ms=next_deadline,
                    unresolved_identities=tuple(unresolved),
                    all_terminated=False,
                )
                return new_state, decision
            else:
                decision = CancellationDecision(
                    stage=CancellationStage.SOFT_CANCEL,
                    action=CancellationAction.SOFT_CANCEL,
                    next_deadline_ms=current_state.deadline_ms,
                    unresolved_identities=tuple(unresolved),
                    all_terminated=False,
                )
                return current_state, decision

        elif stage is CancellationStage.TERMINATE_TREE:
            if deadline_expired:
                next_deadline = now_ms + 1000
                new_state = CancellationState(
                    stage=CancellationStage.KILL_TREE,
                    deadline_ms=next_deadline,
                    grace=self._grace,
                )
                decision = CancellationDecision(
                    stage=CancellationStage.KILL_TREE,
                    action=CancellationAction.KILL_TREE,
                    next_deadline_ms=next_deadline,
                    unresolved_identities=tuple(unresolved),
                    all_terminated=False,
                )
                return new_state, decision
            else:
                decision = CancellationDecision(
                    stage=CancellationStage.TERMINATE_TREE,
                    action=CancellationAction.TERMINATE_TREE,
                    next_deadline_ms=current_state.deadline_ms,
                    unresolved_identities=tuple(unresolved),
                    all_terminated=False,
                )
                return current_state, decision

        elif stage is CancellationStage.KILL_TREE:
            if deadline_expired or bool(observations):
                new_state = CancellationState(
                    stage=CancellationStage.RECONCILE_TREE,
                    deadline_ms=None,
                    grace=self._grace,
                )
                decision = CancellationDecision(
                    stage=CancellationStage.RECONCILE_TREE,
                    action=CancellationAction.RECONCILE_TREE,
                    next_deadline_ms=None,
                    unresolved_identities=tuple(unresolved),
                    all_terminated=False,
                )
                return new_state, decision
            else:
                decision = CancellationDecision(
                    stage=CancellationStage.KILL_TREE,
                    action=CancellationAction.KILL_TREE,
                    next_deadline_ms=current_state.deadline_ms,
                    unresolved_identities=tuple(unresolved),
                    all_terminated=False,
                )
                return current_state, decision

        elif stage is CancellationStage.RECONCILE_TREE:
            new_state = CancellationState(
                stage=CancellationStage.COMPLETED,
                deadline_ms=None,
                grace=self._grace,
            )
            decision = CancellationDecision(
                stage=CancellationStage.COMPLETED,
                action=CancellationAction.NONE,
                next_deadline_ms=None,
                unresolved_identities=tuple(unresolved),
                all_terminated=len(unresolved) == 0,
            )
            return new_state, decision

        return current_state, CancellationDecision(
            stage=current_state.stage,
            action=CancellationAction.NONE,
            next_deadline_ms=current_state.deadline_ms,
            unresolved_identities=tuple(unresolved),
            all_terminated=False,
        )


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
        if not isinstance(self.execution_outcome, ExecutionOutcome):  # pyright: ignore[reportUnnecessaryIsInstance]
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
            and not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
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
            and not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
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
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.terminal_classification,
            TerminalClassification,
        ):
            raise ValueError(
                "terminal_classification must be "
                "TerminalClassification"
            )
        if not isinstance(self.execution_outcome, ExecutionOutcome):  # pyright: ignore[reportUnnecessaryIsInstance]
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


class ProcessSupervisor:
    """Process supervisor for DT-01 through DT-06 event reduction.

    Thread-safety: a ``threading.Lock`` protects all mutable state fields
    that are accessed across thread boundaries (stream-reader threads via
    ``on_chunk``, main thread via ``on_exit``/``finalize``, heartbeat
    thread via ``begin_cancellation``).
    """

    def __init__(
        self,
        *,
        cancellation_ladder: CancellationLadder | None = None,
    ) -> None:
        if (
            cancellation_ladder is not None
            and not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
                cancellation_ladder,
                CancellationLadder,
            )
        ):
            raise ValueError(
                "cancellation_ladder must be CancellationLadder or null"
            )
        self._lock = threading.Lock()
        self._cancellation_ladder = cancellation_ladder or CancellationLadder()
        self._cancellation_state = CancellationState(
            grace=self._cancellation_ladder.grace
        )
        self._cancellation_decision: CancellationDecision | None = None
        self._identity: ProcessBirthIdentity | None = None
        self._chunks: list[tuple[bytes, int]] = []
        self._last_timestamp_ms: int | None = None
        self._total_bytes: int = 0
        self._stream_events_ordered: bool = True
        self._exit_code: int | None = None
        self._timed_out: bool = False
        self._cancelled: bool = False
        self._terminal_classification: TerminalClassification | None = None
        self._cleanup_evidence: ProcessCleanupEvidence | None = None

    @property
    def identity(self) -> ProcessBirthIdentity | None:
        with self._lock:
            return self._identity

    @property
    def total_output_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    @property
    def last_activity_ms(self) -> int | None:
        with self._lock:
            return self._last_timestamp_ms

    @property
    def cancellation_decision(self) -> CancellationDecision | None:
        with self._lock:
            return self._cancellation_decision

    @property
    def cancellation_active(self) -> bool:
        """Whether a cancellation has been triggered (thread-safe query)."""
        with self._lock:
            return self._cancellation_state.stage is not CancellationStage.IDLE

    def on_spawned(
        self,
        *,
        pid: int | None = None,
        process_creation_time: int | None = None,
        identity: ProcessBirthIdentity | None = None,
    ) -> None:
        if identity is not None:
            if not isinstance(identity, ProcessBirthIdentity):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ValueError("identity must be a ProcessBirthIdentity")
            with self._lock:
                self._identity = identity
        elif pid is not None:
            creation_time = (
                process_creation_time if process_creation_time is not None else 0
            )
            with self._lock:
                self._identity = ProcessBirthIdentity(
                    pid=pid,
                    process_creation_time=creation_time,
                )
        else:
            raise ValueError("on_spawned requires either pid or identity")

    def on_chunk(
        self,
        chunk: bytes,
        *,
        timestamp_ms: int,
    ) -> None:
        if type(chunk) is not bytes:
            raise ValueError("chunk must be bytes")
        if type(timestamp_ms) is not int:
            raise ValueError("timestamp_ms must be int")
        with self._lock:
            if (
                self._last_timestamp_ms is not None
                and timestamp_ms < self._last_timestamp_ms
            ):
                self._stream_events_ordered = False
            self._last_timestamp_ms = timestamp_ms
            self._chunks.append((chunk, timestamp_ms))
            self._total_bytes += len(chunk)

    def on_exit(self, *, exit_code: int) -> None:
        if type(exit_code) is not int:
            raise ValueError("exit_code must be int")
        with self._lock:
            self._exit_code = exit_code

    def on_tree_state(
        self,
        *,
        observations: Sequence[TreeProcessObservation | int] | None = None,
        initial_identities: Sequence[int] | None = None,
        now_ms: int = 0,
    ) -> CancellationDecision:
        raw_obs = observations if observations is not None else initial_identities
        if raw_obs is None:
            raw_obs = ()

        norm_observations: list[TreeProcessObservation] = []
        for item in raw_obs:
            if isinstance(item, TreeProcessObservation):
                norm_observations.append(item)
            elif isinstance(item, int):  # pyright: ignore[reportUnnecessaryIsInstance]
                norm_observations.append(
                    TreeProcessObservation(
                        identity=ProcessBirthIdentity(
                            pid=item, process_creation_time=0
                        ),
                        state=ObservationState.RUNNING,
                    )
                )
            else:
                raise ValueError(f"Invalid observation item: {item}")

        with self._lock:
            if self._cancellation_state.stage is CancellationStage.IDLE:
                self._begin_cancellation_locked(now_ms=now_ms)

            self._cancellation_state, self._cancellation_decision = (
                self._cancellation_ladder.step(
                    self._cancellation_state,
                    observations=norm_observations,
                    now_ms=now_ms,
                )
            )

            unresolved = self._cancellation_decision.unresolved_identities
            if unresolved and (
                self._cancellation_decision.stage
                in (CancellationStage.RECONCILE_TREE, CancellationStage.COMPLETED)
                or self._cancellation_decision.action is CancellationAction.RECONCILE_TREE
            ):
                self._cleanup_evidence = ProcessCleanupEvidence(
                    unresolved_identities=unresolved
                )
            elif self._cancellation_decision.all_terminated:
                self._cleanup_evidence = None

            return self._cancellation_decision

    def on_cleanup_error(
        self,
        *,
        unresolved_identities: Sequence[int],
    ) -> None:
        with self._lock:
            self._cleanup_evidence = ProcessCleanupEvidence(
                unresolved_identities=unresolved_identities
            )

    def _begin_cancellation_locked(self, *, now_ms: int = 0) -> CancellationDecision:
        """Internal begin_cancellation that assumes the lock is already held."""
        if self._terminal_classification is None and not self._timed_out:
            self._cancelled = True

        if self._cancellation_state.stage is CancellationStage.IDLE:
            self._cancellation_state, self._cancellation_decision = (
                self._cancellation_ladder.start(now_ms=now_ms)
            )
        elif self._cancellation_decision is None:
            self._cancellation_state, self._cancellation_decision = (
                self._cancellation_ladder.step(
                    self._cancellation_state, now_ms=now_ms
                )
            )

        return self._cancellation_decision

    def begin_cancellation(self, *, now_ms: int = 0) -> CancellationDecision:
        with self._lock:
            return self._begin_cancellation_locked(now_ms=now_ms)

    def trigger_silence_timeout(self, *, now_ms: int = 0) -> None:
        with self._lock:
            if self._terminal_classification is None:
                self._terminal_classification = (
                    TerminalClassification.SILENCE_TIMEOUT
                )
            self._timed_out = True
            self._begin_cancellation_locked(now_ms=now_ms)

    def trigger_process_deadline(self, *, now_ms: int = 0) -> None:
        with self._lock:
            if self._terminal_classification is None:
                self._terminal_classification = (
                    TerminalClassification.PROCESS_TIMEOUT
                )
            self._timed_out = True
            self._begin_cancellation_locked(now_ms=now_ms)

    def trigger_output_limit_exceeded(self, *, now_ms: int = 0) -> None:
        with self._lock:
            if self._terminal_classification is None:
                self._terminal_classification = (
                    TerminalClassification.OUTPUT_LIMIT_EXCEEDED
                )
            self._cancelled = True
            self._begin_cancellation_locked(now_ms=now_ms)

    def finalize_execution_outcome(
        self,
    ) -> ProcessSupervisionOutcome:
        with self._lock:
            started = self._identity is not None
            if not started:
                certainty = ExecutionCertainty.NOT_STARTED
            elif (
                self._exit_code is not None
                and not self._timed_out
                and not self._cancelled
            ):
                certainty = ExecutionCertainty.TERMINAL
            elif self._timed_out or self._cancelled:
                certainty = ExecutionCertainty.MAY_HAVE_STARTED
            else:
                certainty = ExecutionCertainty.STARTED

            exec_outcome = ExecutionOutcome(
                started=started,
                exit_code=self._exit_code,
                timed_out=self._timed_out,
                cancelled=self._cancelled,
                execution_certainty=certainty,
            )
            canonical_stream = b"".join(c[0] for c in self._chunks)
            term_class = self._terminal_classification
            if (
                term_class is None
                and self._exit_code is not None
                and self._exit_code != 0
            ):
                term_class = TerminalClassification.EXIT_NON_ZERO

            return ProcessSupervisionOutcome(
                execution_outcome=exec_outcome,
                stream_events_ordered=self._stream_events_ordered,
                canonical_stream=canonical_stream,
                terminal_classification=term_class,
                cleanup_evidence=self._cleanup_evidence,
            )


