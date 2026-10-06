"""FakeObservationSource + SequentialIdSource (FIXTURES_FAKES_HARNESS.md). Never touches a provider."""
from __future__ import annotations

from collections import deque

from peerhub.extensions.observation_model import EvidenceState, SourceReading


class SequentialIdSource:
    def __init__(self, prefix: str = "obs") -> None:
        self.prefix, self.n = prefix, 0

    def __call__(self) -> str:
        self.n += 1
        return f"{self.prefix}-{self.n}"


class FakeObservationSource:
    """Scripted source. `probe()` returns the next scripted item (SourceReading / any raw object) or raises it if it is an exception."""

    name = "fake-source"

    def __init__(self, *items) -> None:
        self.items = deque(items)
        self.calls = 0

    def script(self, *items) -> "FakeObservationSource":
        self.items.extend(items)
        return self

    def probe(self):
        self.calls += 1
        item = self.items.popleft()
        if isinstance(item, BaseException):
            raise item
        return item


def measured(payload, observed_at=None, semantic=None):
    return SourceReading(EvidenceState.MEASURED, dict(payload), observed_at, semantic)


def absent(payload=None, observed_at=None):
    return SourceReading(EvidenceState.ABSENT, dict(payload or {}), observed_at)


def unknown(payload=None, observed_at=None):
    return SourceReading(EvidenceState.UNKNOWN, dict(payload or {}), observed_at)


def unavailable(condition="executable_not_found", observed_at=None, **extra):
    return SourceReading(EvidenceState.UNAVAILABLE, {"condition": condition, **extra}, observed_at)
