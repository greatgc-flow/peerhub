"""Deterministic test dependencies."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from peerhub.governance.broker import FaultPoint


class FakeClock:
    """Return a fixed sequence of timestamps."""

    def __init__(self, values: Iterable[int]) -> None:
        self._values = deque(values)
        self.calls = 0

    def now(self) -> int:
        """Return the next configured timestamp."""

        if not self._values:
            raise AssertionError("FakeClock is exhausted")
        self.calls += 1
        return self._values.popleft()


class FakeIdSource:
    """Return a fixed sequence of identifiers."""

    def __init__(self, values: Iterable[str]) -> None:
        self._values = deque(values)
        self.namespaces: list[str] = []

    def new_id(self, namespace: str) -> str:
        """Return the next configured identifier."""

        if not self._values:
            raise AssertionError("FakeIdSource is exhausted")
        self.namespaces.append(namespace)
        return self._values.popleft()


class RaisingFaultInjector:
    """Raise at exactly one named transaction boundary."""

    def __init__(self, point: str) -> None:
        if point not in {
            FaultPoint.AFTER_TARGET_WRITE,
            FaultPoint.BEFORE_COMMIT,
            FaultPoint.AFTER_COMMIT,
        }:
            raise ValueError(f"unsupported fault point: {point}")
        self._point = point
        self.hits: list[str] = []

    def hit(self, point: str) -> None:
        """Raise when the configured point is reached."""

        self.hits.append(point)
        if point == self._point:
            raise RuntimeError(f"injected fault at {point}")
