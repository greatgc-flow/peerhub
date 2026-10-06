"""Provider probe evidence DTOs. No v0 dispatch, telemetry projection or runtime imports."""
from dataclasses import dataclass
import math
from typing import Generic, NewType, Protocol, TypeVar

from peerhub.extensions.observation_model import EvidenceState

T = TypeVar("T")
EvidenceRef = NewType("EvidenceRef", str)


class IdSource(Protocol):
    def new_id(self, prefix: str) -> str: ...


@dataclass(frozen=True)
class UsageMeasurement:
    quota_pool_scope: str
    used_fraction: float
    remaining_fraction: float
    window_started_at: int
    resets_at: int

    def __post_init__(self):
        if not self.quota_pool_scope:
            raise ValueError("quota pool must have a scope")
        for value in (self.used_fraction, self.remaining_fraction):
            if type(value) is not float or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("quota fractions must be finite and between 0 and 1")
        if self.window_started_at < 0 or self.resets_at < self.window_started_at:
            raise ValueError("invalid quota window")


@dataclass(frozen=True)
class EvidenceValue(Generic[T]):
    state: EvidenceState
    source_tag: str
    provider_id: str
    provider_version: str
    observed_at: int | None
    captured_at: int
    freshness_ttl: int
    evidence_ref: EvidenceRef
    value: T | None

    def __post_init__(self):
        if self.state == EvidenceState.MEASURED and (self.value is None or self.observed_at is None):
            raise ValueError("measured evidence requires a value and source time")
        if self.state not in (EvidenceState.MEASURED, EvidenceState.STALE) and self.value is not None:
            raise ValueError("unmeasured evidence must not carry a measurement")
        if self.captured_at < 0 or self.freshness_ttl < 0:
            raise ValueError("invalid capture time or freshness TTL")
        if self.observed_at is not None and not 0 <= self.observed_at <= self.captured_at:
            raise ValueError("invalid observation time")


@dataclass(frozen=True)
class UsageObserved:
    observation_id: str
    instance_id: str
    profile_id: str
    evidence: EvidenceValue[UsageMeasurement]
