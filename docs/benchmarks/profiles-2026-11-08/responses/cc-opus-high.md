```python
import re
from fractions import Fraction

_NUM = r"([0-9]+(?:[.,][0-9]+)?)"
_PATTERN = re.compile(
    r"([+-])?P"
    rf"(?:{_NUM}Y)?"
    rf"(?:{_NUM}M)?"
    rf"(?:{_NUM}W)?"
    rf"(?:{_NUM}D)?"
    rf"(T(?:{_NUM}H)?(?:{_NUM}M)?(?:{_NUM}S)?)?",
    re.ASCII,
)

_DAY = 86400
_UNITS = (
    365 * _DAY,  # Y
    30 * _DAY,   # M (months)
    7 * _DAY,    # W
    _DAY,        # D
    3600,        # H
    60,          # M (minutes)
    1,           # S
)


def _to_fraction(num: str) -> Fraction:
    num = num.replace(",", ".")
    if "." in num:
        int_part, frac_part = num.split(".", 1)
        return Fraction(int(int_part)) + Fraction(int(frac_part), 10 ** len(frac_part))
    return Fraction(int(num))


def parse_duration(s: str) -> Fraction:
    if not isinstance(s, str):
        raise ValueError("duration must be a string")
    m = _PATTERN.fullmatch(s)
    if m is None:
        raise ValueError(f"invalid duration: {s!r}")

    sign = m.group(1)
    t_part = m.group(6)
    values = [m.group(i) for i in (2, 3, 4, 5, 7, 8, 9)]

    present = [i for i, v in enumerate(values) if v is not None]
    if not present:
        raise ValueError(f"invalid duration (no components): {s!r}")

    if t_part is not None and all(values[i] is None for i in (4, 5, 6)):
        raise ValueError(f"invalid duration ('T' without time components): {s!r}")

    last = present[-1]
    for i in present:
        v = values[i]
        if i != last and ("." in v or "," in v):
            raise ValueError(f"invalid duration (fraction only allowed on last component): {s!r}")

    total = Fraction(0)
    for i in present:
        total += _to_fraction(values[i]) * _UNITS[i]

    if sign == "-":
        total = -total
    return total
```
