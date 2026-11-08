```python
import fractions
import re

_NUMBER = r"([0-9]+(?:[.,][0-9]+)?)"
_DURATION = re.compile(
    r"([+-]?)P"
    + rf"(?:{_NUMBER}Y)?"
    + rf"(?:{_NUMBER}M)?"
    + rf"(?:{_NUMBER}W)?"
    + rf"(?:{_NUMBER}D)?"
    + rf"(?:T(?:{_NUMBER}H)?(?:{_NUMBER}M)?(?:{_NUMBER}S)?)?"
)
_SECONDS = (365 * 86400, 30 * 86400, 7 * 86400, 86400, 3600, 60, 1)


def parse_duration(s: str) -> fractions.Fraction:
    if not isinstance(s, str):
        raise ValueError("Duration must be a string")

    match = _DURATION.fullmatch(s)
    if match is None or s.endswith("T"):
        raise ValueError("Invalid duration")

    components = [
        (value, unit)
        for value, unit in zip(match.groups()[1:], _SECONDS)
        if value is not None
    ]
    if not components:
        raise ValueError("At least one component is required")
    if any("." in value or "," in value for value, _ in components[:-1]):
        raise ValueError("Only the last component may have a fraction")

    total = fractions.Fraction(0)
    for value, unit in components:
        whole, separator, decimal = value.replace(",", ".").partition(".")
        # Accumulate digits directly to avoid integer-string conversion limits.
        numerator = 0
        for digit in whole + decimal:
            numerator = numerator * 10 + ord(digit) - ord("0")
        denominator = 10 ** len(decimal) if separator else 1
        total += fractions.Fraction(numerator * unit, denominator)

    return -total if match.group(1) == "-" else total
```
