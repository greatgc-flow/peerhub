"""Public telemetry observations, measurements, and reader port."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from peerhub.core.evidence import (
    EvidenceRef,
    EvidenceState,
    EvidenceValue,
)
from peerhub.core.protocol import (
    AttemptTerminalObserved,
    OperationalFailureCategory,
    require_text,
)
from peerhub.dispatch.contract import SessionBindingKey


def _require_nonnegative(
    value: int,
    name: str,
) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _require_positive(
    value: int,
    name: str,
) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _normalize_refs(
    values: tuple[EvidenceRef, ...],
) -> tuple[EvidenceRef, ...]:
    return tuple(
        EvidenceRef(require_text(value, "evidence_ref"))
        for value in values
    )


@dataclass(frozen=True)
class ReadinessMeasurement:
    """Measured readiness facts owned by a readiness provider."""

    runtime_revision: str
    issued_at: int
    valid_until: int
    integrity_verified: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "runtime_revision",
            require_text(
                self.runtime_revision,
                "runtime_revision",
            ),
        )
        _require_nonnegative(self.issued_at, "issued_at")
        _require_nonnegative(self.valid_until, "valid_until")
        if self.valid_until < self.issued_at:
            raise ValueError(
                "valid_until cannot precede issued_at"
            )
        if type(self.integrity_verified) is not bool:
            raise ValueError(
                "integrity_verified must be a boolean"
            )


@dataclass(frozen=True)
class UsageMeasurement:
    """Aggregate quota-pool/window usage, never per-request cost."""

    quota_pool_scope: str
    used_fraction: float
    remaining_fraction: float
    window_started_at: int
    resets_at: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "quota_pool_scope",
            require_text(
                self.quota_pool_scope,
                "quota_pool_scope",
            ),
        )

        for name in (
            "used_fraction",
            "remaining_fraction",
        ):
            value = getattr(self, name)
            if (
                type(value) is not float
                or not math.isfinite(value)
                or value < 0.0
                or value > 1.0
            ):
                raise ValueError(
                    f"{name} must be a finite fraction"
                )

        _require_nonnegative(
            self.window_started_at,
            "window_started_at",
        )
        _require_nonnegative(
            self.resets_at,
            "resets_at",
        )
        if self.resets_at < self.window_started_at:
            raise ValueError(
                "resets_at cannot precede window_started_at"
            )


UsageEvidence: TypeAlias = EvidenceValue[UsageMeasurement]


@dataclass(frozen=True)
class ReadinessObserved:
    """Immutable typed readiness observation."""

    observation_id: str
    instance_id: str
    profile_id: str
    evidence: EvidenceValue[ReadinessMeasurement]

    def __post_init__(self) -> None:
        for name in (
            "observation_id",
            "instance_id",
            "profile_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )

        if self.evidence.state in {
            EvidenceState.MEASURED,
            EvidenceState.STALE,
        }:
            if not isinstance(
                self.evidence.value,
                ReadinessMeasurement,
            ):
                raise ValueError(
                    "readiness evidence must carry "
                    "ReadinessMeasurement"
                )


@dataclass(frozen=True)
class UsageObserved:
    """Immutable typed usage observation."""

    observation_id: str
    instance_id: str
    profile_id: str
    evidence: UsageEvidence

    def __post_init__(self) -> None:
        for name in (
            "observation_id",
            "instance_id",
            "profile_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )

        if (
            self.evidence.state
            in {EvidenceState.MEASURED, EvidenceState.STALE}
            and self.evidence.value is not None
            and not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
                self.evidence.value,
                UsageMeasurement,
            )
        ):
            raise ValueError(
                "usage evidence must carry UsageMeasurement"
            )


@dataclass(frozen=True)
class OperationalObservation:
    """Immutable observation derived from one terminal outbox event."""

    observation_id: str
    source_event_id: str
    outbox_position: int
    terminal_event: AttemptTerminalObserved

    def __post_init__(self) -> None:
        for name in (
            "observation_id",
            "source_event_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        _require_positive(
            self.outbox_position,
            "outbox_position",
        )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.terminal_event,
            AttemptTerminalObserved,
        ):
            raise ValueError(
                "terminal_event must be AttemptTerminalObserved"
            )


@dataclass(frozen=True)
class OperationalProjectionSnapshot:
    """Revisioned rebuildable aggregate of operational observations.

    This projection owns empirical facts only. It deliberately has no
    availability, admission, quarantine, or completion-assessment field.
    """

    projection_id: str
    instance_id: str
    profile_id: str
    failure_category: EvidenceValue[
        OperationalFailureCategory
    ]
    process_integrity: EvidenceValue[bool]
    latency: EvidenceValue[int]
    usage: UsageEvidence
    failure_streak: int
    last_terminal_at: int | None
    evidence_refs: tuple[EvidenceRef, ...]
    revision: int
    updated_at: int

    def __post_init__(self) -> None:
        for name in (
            "projection_id",
            "instance_id",
            "profile_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )

        _require_nonnegative(
            self.failure_streak,
            "failure_streak",
        )
        if self.last_terminal_at is not None:
            _require_nonnegative(
                self.last_terminal_at,
                "last_terminal_at",
            )
        _require_positive(self.revision, "revision")
        _require_nonnegative(self.updated_at, "updated_at")

        object.__setattr__(
            self,
            "evidence_refs",
            _normalize_refs(self.evidence_refs),
        )


@runtime_checkable
class TelemetryProjectionReader(Protocol):
    """Read-only boundary used by health and routing services."""

    def get(
        self,
        instance_id: str,
        profile_id: str,
    ) -> OperationalProjectionSnapshot:
        """Return the current projection for an instance/profile."""
        ...

    def get_session_context(
        self,
        workspace_scope_id: str,
        instance_id: str,
        profile_id: str,
        generation_id: int,
    ) -> "SessionContextProjectionSnapshot | None":
        """Return context occupancy or None if no exact-attribution evidence exists."""
        ...


@dataclass(frozen=True)
class SessionContextObserved:
    """Distinct, exact-attribution event for session context occupancy."""

    observation_id: str
    binding_key: SessionBindingKey
    generation_id: int
    observed_tokens: int
    window_tokens: int
    source: str
    observed_at: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observation_id",
            require_text(self.observation_id, "observation_id"),
        )
        if not isinstance(self.binding_key, SessionBindingKey):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("binding_key must be SessionBindingKey")
        _require_positive(self.generation_id, "generation_id")
        _require_nonnegative(self.observed_tokens, "observed_tokens")
        _require_positive(self.window_tokens, "window_tokens")
        object.__setattr__(
            self,
            "source",
            require_text(self.source, "source"),
        )
        _require_nonnegative(self.observed_at, "observed_at")


@dataclass(frozen=True)
class SessionContextProjectionSnapshot:
    """Idempotent projection of session context occupancy."""

    projection_id: str
    binding_key: SessionBindingKey
    generation_id: int
    observed_tokens: int
    window_tokens: int
    source: str
    observed_at: int
    revision: int
    updated_at: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "projection_id",
            require_text(self.projection_id, "projection_id"),
        )
        if not isinstance(self.binding_key, SessionBindingKey):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("binding_key must be SessionBindingKey")
        _require_positive(self.generation_id, "generation_id")
        _require_nonnegative(self.observed_tokens, "observed_tokens")
        _require_positive(self.window_tokens, "window_tokens")
        object.__setattr__(
            self,
            "source",
            require_text(self.source, "source"),
        )
        _require_nonnegative(self.observed_at, "observed_at")
        _require_positive(self.revision, "revision")
        _require_nonnegative(self.updated_at, "updated_at")
