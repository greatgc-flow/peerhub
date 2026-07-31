"""Deterministic test dependencies."""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Iterable
from uuid import UUID

from peerhub.governance.broker import FaultPoint


def deterministic_uuid4(seed: str) -> str:
    """Derive a stable test-only UUIDv4 from a readable seed."""

    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return str(UUID(bytes=digest[:16], version=4))


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
        value = self._values.popleft()
        if namespace == "outbox-event":
            return deterministic_uuid4(value)
        return value


class DeterministicClock:
    """Auto-incrementing deterministic clock starting from a fixed value."""

    def __init__(self, start: int = 0) -> None:
        self._next = start

    def now(self) -> int:
        """Return the next timestamp, incrementing by one each call."""

        value = self._next
        self._next += 1
        return value


class SequentialIdSource:
    """Generate deterministic, namespace-prefixed sequential identifiers."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def new_id(self, namespace: str) -> str:
        """Return the next sequential identifier for the given namespace."""

        count = self._counters.get(namespace, 0) + 1
        self._counters[namespace] = count
        value = f"{namespace}-{count}"
        if namespace == "outbox-event":
            return deterministic_uuid4(value)
        return value


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
