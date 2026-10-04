"""CrashInjector: raises a BaseException at a named point (state written before the point is durable; nothing after it runs)."""
from __future__ import annotations


class CrashInjected(BaseException):
    """Simulated process death (BaseException so no `except Exception` in production code can swallow it)."""


class CrashInjector:
    def __init__(self, *points: str) -> None:
        self.points = set(points)
        self.fired: list[str] = []
        self.seen: list[str] = []

    def arm(self, *points: str) -> "CrashInjector":
        self.points |= set(points)
        return self

    def disarm(self) -> None:
        self.points.clear()

    def __call__(self, point: str) -> None:
        self.seen.append(point)
        if point in self.points:
            self.fired.append(point)
            raise CrashInjected(point)
