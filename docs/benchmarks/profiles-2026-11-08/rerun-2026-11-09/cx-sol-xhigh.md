```python
import re
from fractions import Fraction


def parse_duration(s: str) -> Fraction:
    number = r"([0-9]+(?:[.,][0-9]+)?)"
    pattern = (
        r"([+-]?)P"
        + "".join(rf"(?:{number}{unit})?" for unit in "YMWD")
        + r"(?:T"
        + "".join(rf"(?:{number}{unit})?" for unit in "HMS")
        + r")?"
    )

    if not isinstance(s, str):
        raise ValueError("Duration must be a string")

    match = re.fullmatch(pattern, s)
    if match is None:
        raise ValueError("Invalid duration syntax")

    sign, *values = match.groups()
    if not any(value is not None for value in values):
        raise ValueError("At least one component is required")
    if "T" in s and not any(value is not None for value in values[4:]):
        raise ValueError("T requires a time component")

    scales = (365 * 86400, 30 * 86400, 7 * 86400, 86400, 3600, 60, 1)
    components = [
        (value, scale)
        for value, scale in zip(values, scales)
        if value is not None
    ]

    if any("." in value or "," in value for value, _ in components[:-1]):
        raise ValueError("Only the last component may have a fraction")

    total = sum(
        (Fraction(value.replace(",", ".")) * scale for value, scale in components),
        Fraction(0),
    )
    return -total if sign == "-" else total
```