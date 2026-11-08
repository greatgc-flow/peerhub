```python
import re
from fractions import Fraction

_NUMBER = r"[0-9]+(?:[.,][0-9]+)?"
_DURATION = re.compile(
    rf"(?P<sign>[+-]?)P"
    rf"(?:(?P<years>{_NUMBER})Y)?"
    rf"(?:(?P<months>{_NUMBER})M)?"
    rf"(?:(?P<weeks>{_NUMBER})W)?"
    rf"(?:(?P<days>{_NUMBER})D)?"
    rf"(?:(?P<t>T)"
    rf"(?:(?P<hours>{_NUMBER})H)?"
    rf"(?:(?P<minutes>{_NUMBER})M)?"
    rf"(?:(?P<seconds>{_NUMBER})S)?"
    rf")?"
)
_UNITS = (
    ("years", 365 * 86400),
    ("months", 30 * 86400),
    ("weeks", 7 * 86400),
    ("days", 86400),
    ("hours", 3600),
    ("minutes", 60),
    ("seconds", 1),
)


def parse_duration(s: str) -> Fraction:
    if not isinstance(s, str):
        raise ValueError("Duration must be a string")

    match = _DURATION.fullmatch(s)
    if match is None:
        raise ValueError("Invalid duration")

    components = [
        (match.group(name), scale)
        for name, scale in _UNITS
        if match.group(name) is not None
    ]
    if not components:
        raise ValueError("At least one component is required")

    if match.group("t") is not None and all(
        match.group(name) is None for name in ("hours", "minutes", "seconds")
    ):
        raise ValueError("T must be followed by a time component")

    if any("." in value or "," in value for value, _ in components[:-1]):
        raise ValueError("Only the last component may have a fraction")

    total = Fraction(0)
    for value, scale in components:
        whole, _, fractional = value.replace(",", ".").partition(".")

        # Accumulate digits without Python's integer-string conversion limit.
        numerator = 0
        for digit in whole + fractional:
            numerator = numerator * 10 + ord(digit) - ord("0")

        total += Fraction(numerator * scale, 10 ** len(fractional))

    return -total if match.group("sign") == "-" else total
```