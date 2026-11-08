```python
import re
from fractions import Fraction

_NUMBER = r"[0-9]+(?:[.,][0-9]+)?"
_DURATION = re.compile(
    rf"([+-]?)P"
    rf"(?:({_NUMBER})Y)?"
    rf"(?:({_NUMBER})M)?"
    rf"(?:({_NUMBER})W)?"
    rf"(?:({_NUMBER})D)?"
    rf"(?:T"
    rf"(?:({_NUMBER})H)?"
    rf"(?:({_NUMBER})M)?"
    rf"(?:({_NUMBER})S)?"
    rf")?"
)
_SECONDS = (31536000, 2592000, 604800, 86400, 3600, 60, 1)


def parse_duration(s: str) -> Fraction:
    if not isinstance(s, str):
        raise ValueError("Duration must be a string")

    match = _DURATION.fullmatch(s)
    if match is None:
        raise ValueError("Invalid duration")

    sign, *values = match.groups()
    components = [
        (value, seconds)
        for value, seconds in zip(values, _SECONDS)
        if value is not None
    ]

    if not components:
        raise ValueError("At least one component is required")
    if "T" in s and all(value is None for value in values[4:]):
        raise ValueError("T requires a time component")
    if any("." in value or "," in value for value, _ in components[:-1]):
        raise ValueError("Only the last component may have a fraction")

    total = sum(
        (Fraction(value.replace(",", ".")) * seconds
         for value, seconds in components),
        Fraction(0),
    )
    return -total if sign == "-" else total
```