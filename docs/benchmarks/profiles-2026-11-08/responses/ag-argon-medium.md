```python
from fractions import Fraction
import re

_DURATION_RE = re.compile(
    r"^([+-])?P"
    r"(?:([0-9]+(?:[.,][0-9]+)?)Y)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)M)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)W)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)D)?"
    r"(?:(T)"
    r"(?:([0-9]+(?:[.,][0-9]+)?)H)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)M)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)S)?"
    r")?$",
    re.ASCII,
)

_UNIT_SECONDS = (
    365 * 86400,  # Y
    30 * 86400,   # M (month)
    7 * 86400,    # W
    86400,        # D
    3600,         # H
    60,           # M (minute)
    1,            # S
)


def parse_duration(s: str) -> Fraction:
    if not isinstance(s, str):
        raise ValueError("Duration must be a string")

    m = _DURATION_RE.fullmatch(s)
    if m is None:
        raise ValueError(f"Invalid ISO-8601 duration: {s!r}")

    sign, y, mo, w, d, t_sep, h, mi, sec = m.groups()

    if t_sep is not None and h is None and mi is None and sec is None:
        raise ValueError("Time designator 'T' must be followed by H, M, or S")

    raw_parts = (y, mo, w, d, h, mi, sec)
    present = [
        (val, unit)
        for val, unit in zip(raw_parts, _UNIT_SECONDS)
        if val is not None
    ]

    if not present:
        raise ValueError("Duration must contain at least one component")

    for val, _ in present[:-1]:
        if "." in val or "," in val:
            raise ValueError("Only the last present component may be fractional")

    total = sum(
        (Fraction(val.replace(",", ".")) * unit for val, unit in present),
        Fraction(0),
    )
    return -total if sign == "-" else total
```
