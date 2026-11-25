"""Scalar validation at untyped data boundaries."""
from __future__ import annotations

import math
from typing import TypeGuard


def is_finite_number(value: object, *, overflow_is_missing: bool = False) -> TypeGuard[int | float]:
    """Validate a numeric input without replacing it with its float conversion."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        if overflow_is_missing:
            return False
        raise


def finite_number(value: object, *, overflow_is_missing: bool = False) -> float | None:
    """Validate the input before converting it to a float."""
    try:
        return float(value) if is_finite_number(value) else None
    except OverflowError:
        if overflow_is_missing:
            return None
        raise


def finite_converted_number(value: object) -> float | None:
    """Return the float conversion only when the converted value is finite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None
