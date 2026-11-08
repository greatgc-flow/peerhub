```python
from fractions import Fraction
import re

_HEAD_RE = re.compile(r"\A([+-])?P(.*)\Z", re.DOTALL)
_DATE_RE = re.compile(
    r"\A"
    r"(?:([0-9]+(?:[.,][0-9]+)?)Y)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)M)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)W)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)D)?"
    r"\Z"
)
_TIME_RE = re.compile(
    r"\A"
    r"(?:([0-9]+(?:[.,][0-9]+)?)H)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)M)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)S)?"
    r"\Z"
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

    head_match = _HEAD_RE.match(s)
    if not head_match:
        raise ValueError(f"Invalid duration: {s!r}")

    sign, body = head_match.groups()
    if "T" in body:
        if body.count("T") > 1:
            raise ValueError(f"Multiple 'T' designators in duration: {s!r}")
        date_part, time_part = body.split("T")
        if not time_part:
            raise ValueError(f"Empty time part after 'T' in duration: {s!r}")
    else:
        date_part, time_part = body, ""

    date_match = _DATE_RE.match(date_part)
    time_match = _TIME_RE.match(time_part)
    if not date_match or not time_match:
        raise ValueError(f"Invalid duration components: {s!r}")

    raw_values = date_match.groups() + time_match.groups()
    present = [
        (val, unit)
        for val, unit in zip(raw_values, _UNIT_SECONDS)
        if val is not None
    ]

    if not present:
        raise ValueError(f"Duration has no components: {s!r}")

    for val, _ in present[:-1]:
        if "." in val or "," in val:
            raise ValueError(
                f"Only the last component may be fractional: {s!r}"
            )

    total = sum(
        (Fraction(val.replace(",", ".")) * unit for val, unit in present),
        Fraction(0),
    )
    return -total if sign == "-" else total
```
