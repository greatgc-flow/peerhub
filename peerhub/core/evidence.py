"""Shared typed evidence algebra frozen by Protocol v1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, NewType, TypeVar

from .protocol import require_text


T = TypeVar("T")

EvidenceRef = NewType("EvidenceRef", str)


class EvidenceState(str, Enum):
    """The complete Protocol v1 evidence-state vocabulary."""

    MEASURED = "MEASURED"
    ABSENT = "ABSENT"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"
    STALE = "STALE"


def _require_nonnegative(
    value: int,
    name: str,
) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


@dataclass(frozen=True)
class EvidenceValue(Generic[T]):
    """One typed fact together with its source and freshness metadata.

    Non-measured states remain explicit. In particular, ABSENT and
    UNAVAILABLE are not converted into a zero, healthy, or unlimited
    measurement.
    """

    state: EvidenceState
    source_tag: str
    provider_id: str
    provider_version: str
    observed_at: int | None
    captured_at: int
    freshness_ttl: int
    evidence_ref: EvidenceRef
    value: T | None

    def __post_init__(self) -> None:
        if not isinstance(self.state, EvidenceState):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("state must be EvidenceState")

        for name in (
            "source_tag",
            "provider_id",
            "provider_version",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )

        if self.observed_at is not None:
            _require_nonnegative(
                self.observed_at,
                "observed_at",
            )

        _require_nonnegative(
            self.captured_at,
            "captured_at",
        )
        _require_nonnegative(
            self.freshness_ttl,
            "freshness_ttl",
        )

        if (
            self.observed_at is not None
            and self.captured_at < self.observed_at
        ):
            raise ValueError(
                "captured_at cannot precede observed_at"
            )

        object.__setattr__(
            self,
            "evidence_ref",
            EvidenceRef(
                require_text(
                    self.evidence_ref,
                    "evidence_ref",
                )
            ),
        )

        if self.state is EvidenceState.MEASURED:
            if self.observed_at is None:
                raise ValueError(
                    "MEASURED evidence requires observed_at"
                )
            if self.value is None:
                raise ValueError(
                    "MEASURED evidence requires a value"
                )

        if self.state is EvidenceState.STALE:
            if self.observed_at is None:
                raise ValueError(
                    "STALE evidence requires observed_at"
                )

        if self.state in {
            EvidenceState.ABSENT,
            EvidenceState.UNAVAILABLE,
            EvidenceState.ERROR,
        } and self.value is not None:
            raise ValueError(
                f"{self.state.value} evidence cannot carry a value"
            )
