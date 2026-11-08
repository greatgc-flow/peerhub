```python
import re
from fractions import Fraction


def parse_duration(s: str) -> Fraction:
    if not isinstance(s, str):
        raise ValueError("Invalid duration")

    number = r"[0-9]+(?:[.,][0-9]+)?"
    match = re.fullmatch(
        rf"([+-]?)P"
        rf"(?:({number})Y)?"
        rf"(?:({number})M)?"
        rf"(?:({number})W)?"
        rf"(?:({number})D)?"
        rf"(?:T"
        rf"(?:({number})H)?"
        rf"(?:({number})M)?"
        rf"(?:({number})S)?"
        rf")?",
        s,
    )
    if match is None:
        raise ValueError("Invalid duration")

    sign, *components = match.groups()
    if not any(components) or ("T" in s and not any(components[4:])):
        raise ValueError("Invalid duration")

    scales = (31536000, 2592000, 604800, 86400, 3600, 60, 1)
    present = [
        (value, scale)
        for value, scale in zip(components, scales)
        if value is not None
    ]
    if any("." in value or "," in value for value, _ in present[:-1]):
        raise ValueError("Only the last component may have a fraction")

    def decimal_fraction(value: str) -> Fraction:
        whole, separator, fractional = value.replace(",", ".").partition(".")
        numerator = 0
        for digit in whole + fractional:
            numerator = numerator * 10 + (ord(digit) - ord("0"))
        denominator = 10 ** len(fractional) if separator else 1
        return Fraction(numerator, denominator)

    seconds = sum(
        (decimal_fraction(value) * scale for value, scale in present),
        Fraction(0),
    )
    return -seconds if sign == "-" else seconds
```
