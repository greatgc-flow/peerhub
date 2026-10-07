"""ManualClock (FIXTURES_FAKES_HARNESS.md): deterministic lease/TTL time, no wall-clock sleeps."""
from __future__ import annotations


class ManualClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        self._now += float(seconds)
        return self._now

    def set(self, value: float) -> None:
        self._now = float(value)
