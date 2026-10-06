"""Repeated independent read-only snapshots; no provider collection or repair."""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Callable, Iterator

from peerhub.extensions.diag import DiagnosticReport, ReadonlyDiag


def dashboard_snapshots(db_path: str | Path, *, interval_s: float = 2, count: int = 0,
                        sleep: Callable[[float], None] = time.sleep) -> Iterator[DiagnosticReport]:
    if not math.isfinite(interval_s) or interval_s <= 0:
        raise ValueError("snapshot interval must be finite and positive")
    if type(count) is not int or count < 0:
        raise ValueError("snapshot count must be a non-negative integer")
    rendered = 0
    while count == 0 or rendered < count:
        yield ReadonlyDiag(db_path).render(["peers", "streams", "resource_pools", "observations"])
        rendered += 1
        if count == 0 or rendered < count:
            sleep(interval_s)
