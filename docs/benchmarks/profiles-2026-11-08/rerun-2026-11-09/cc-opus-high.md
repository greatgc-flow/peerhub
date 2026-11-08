```python
import re
from fractions import Fraction

_NUM = r"([0-9]+(?:[.,][0-9]+)?)"
_PATTERN = re.compile(
    r"([+-])?P"
    r"(?:" + _NUM + r"Y)?"
    r"(?:" + _NUM + r"M)?"
    r"(?:" + _NUM + r"W)?"
    r"(?:" + _NUM + r"D)?"
    r"(T"
    r"(?:" + _NUM + r"H)?"
    r"(?:" + _NUM + r"M)?"
    r"(?:" + _NUM + r"S)?"
    r")?"
)

_DAY = 86400
# Group indices (1-based) of each numeric component and its multiplier in seconds.
_COMPONENTS = [
    (2, 365 * _DAY),  # Y
    (3, 30 * _DAY),   # M (months)
    (4, 7 * _DAY),    # W
    (5, _DAY),        # D
    (7, 3600),        # H
    (8, 60),          # M (minutes)
    (9, 1),           # S
]


def _to_fraction(text: str) -> Fraction:
    text = text.replace(",", ".")
    if "." in text:
        int_part, frac_part = text.split(".")
        return Fraction(int(int_part)) + Fraction(int(frac_part), 10 ** len(frac_part))
    return Fraction(int(text))


def parse_duration(s: str) -> Fraction:
    if not isinstance(s, str):
        raise ValueError("duration must be a string")
    m = _PATTERN.fullmatch(s)
    if m is None:
        raise ValueError(f"invalid duration: {s!r}")

    present = [(m.group(g), mult) for g, mult in _COMPONENTS if m.group(g) is not None]
    if not present:
        raise ValueError(f"duration has no components: {s!r}")

    if m.group(6) is not None and all(m.group(g) is None for g in (7, 8, 9)):
        raise ValueError(f"'T' must be followed by H, M or S: {s!r}")

    for text, _ in present[:-1]:
        if "." in text or "," in text:
            raise ValueError(f"only the last component may have a fraction: {s!r}")

    total = Fraction(0)
    for text, mult in present:
        total += _to_fraction(text) * mult

    if m.group(1) == "-":
        total = -total
    return total
```