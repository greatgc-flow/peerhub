"""Scalar validation at untyped data boundaries."""
from __future__ import annotations

import math


def finite_number(value: object, *, overflow_is_missing: bool = False) -> float | None:
    """Return a finite float for an int or float, excluding booleans.

    Evidence readers can treat integers outside float range as missing.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        if overflow_is_missing:
            return None
        raise
    return number if math.isfinite(number) else None
