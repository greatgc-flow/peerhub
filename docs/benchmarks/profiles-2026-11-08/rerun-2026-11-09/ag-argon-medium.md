```python
import fractions
import re

_DURATION_RE = re.compile(
    r"\A([+-])?P"
    r"(?:([0-9]+(?:[.,][0-9]+)?)Y)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)M)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)W)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)D)?"
    r"(?:T"
    r"(?:([0-9]+(?:[.,][0-9]+)?)H)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)M)?"
    r"(?:([0-9]+(?:[.,][0-9]+)?)S)?"
    r")?\Z"
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


def parse_duration(s: str) -> fractions.Fraction:
    if not isinstance(s, str):
        raise ValueError("Duration must be a string")

    m = _DURATION_RE.fullmatch(s)
    if m is None:
        raise ValueError(f"Invalid ISO-8601 duration: {s!r}")

    sign = m.group(1)
    parts = m.groups()[1:]

    present_indices = [i for i, val in enumerate(parts) if val is not None]
    if not present_indices:
        raise ValueError(f"Duration must contain at least one component: {s!r}")

    if "T" in s and present_indices[-1] < 4:
        raise ValueError(f"Time designator 'T' must be followed by H, M, or S: {s!r}")

    last_idx = present_indices[-1]
    total = fractions.Fraction(0)

    for idx in present_indices:
        raw = parts[idx]
        if idx != last_idx and ("." in raw or "," in raw):
            raise ValueError(f"Only the last component may have a fractional part: {s!r}")
        value = fractions.Fraction(raw.replace(",", "."))
        total += value * _UNIT_SECONDS[idx]

    return -total if sign == "-" else total
```
