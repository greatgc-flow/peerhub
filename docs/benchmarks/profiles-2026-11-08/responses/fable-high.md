```python
import re
from fractions import Fraction

_NUM = r"\d+(?:[.,]\d+)?"
_RE = re.compile(
    r"^(?P<sign>[+-])?P"
    r"(?:(?P<Y>" + _NUM + r")Y)?"
    r"(?:(?P<Mo>" + _NUM + r")M)?"
    r"(?:(?P<W>" + _NUM + r")W)?"
    r"(?:(?P<D>" + _NUM + r")D)?"
    r"(?:T"
    r"(?:(?P<H>" + _NUM + r")H)?"
    r"(?:(?P<Mi>" + _NUM + r")M)?"
    r"(?:(?P<S>" + _NUM + r")S)?"
    r")?$"
)

_MULT = {
    "Y": 365 * 86400,
    "Mo": 30 * 86400,
    "W": 7 * 86400,
    "D": 86400,
    "H": 3600,
    "Mi": 60,
    "S": 1,
}
_ORDER = ("Y", "Mo", "W", "D", "H", "Mi", "S")


def parse_duration(s: str) -> Fraction:
    if not isinstance(s, str):
        raise ValueError("duration must be a string")
    m = _RE.match(s)
    if m is None:
        raise ValueError(f"invalid duration: {s!r}")
    present = [k for k in _ORDER if m.group(k) is not None]
    if not present:
        raise ValueError(f"invalid duration (no components): {s!r}")
    # 'T' must be followed by at least one time component.
    if "T" in s and not any(k in present for k in ("H", "Mi", "S")):
        raise ValueError(f"invalid duration (empty time part): {s!r}")
    total = Fraction(0)
    last = present[-1]
    for k in present:
        text = m.group(k)
        if ("." in text or "," in text) and k != last:
            raise ValueError(
                f"invalid duration (fraction only allowed in last component): {s!r}"
            )
        value = Fraction(text.replace(",", "."))
        total += value * _MULT[k]
    if m.group("sign") == "-":
        total = -total
    return total
```
