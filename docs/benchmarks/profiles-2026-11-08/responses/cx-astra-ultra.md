```python
import fractions
import re


_DURATION_RE = re.compile(
    r"(?P<sign>[+-]?)P"
    r"(?:(?P<years>[0-9]+(?:[.,][0-9]+)?)Y)?"
    r"(?:(?P<months>[0-9]+(?:[.,][0-9]+)?)M)?"
    r"(?:(?P<weeks>[0-9]+(?:[.,][0-9]+)?)W)?"
    r"(?:(?P<days>[0-9]+(?:[.,][0-9]+)?)D)?"
    r"(?:(?P<time>T)"
    r"(?:(?P<hours>[0-9]+(?:[.,][0-9]+)?)H)?"
    r"(?:(?P<minutes>[0-9]+(?:[.,][0-9]+)?)M)?"
    r"(?:(?P<seconds>[0-9]+(?:[.,][0-9]+)?)S)?"
    r")?"
)

_SCALES = (
    ("years", 365 * 86400),
    ("months", 30 * 86400),
    ("weeks", 7 * 86400),
    ("days", 86400),
    ("hours", 3600),
    ("minutes", 60),
    ("seconds", 1),
)


def parse_duration(s: str) -> fractions.Fraction:
    if not isinstance(s, str):
        raise ValueError("Duration must be a string")

    match = _DURATION_RE.fullmatch(s)
    if match is None:
        raise ValueError("Invalid duration")

    components = [
        (match[name], scale)
        for name, scale in _SCALES
        if match[name] is not None
    ]
    if not components:
        raise ValueError("At least one component is required")

    if match["time"] is not None and all(
        match[name] is None for name in ("hours", "minutes", "seconds")
    ):
        raise ValueError("T must be followed by a time component")

    if any("." in number or "," in number for number, _ in components[:-1]):
        raise ValueError("Only the last component may have a fraction")

    total = sum(
        (
            fractions.Fraction(number.replace(",", ".")) * scale
            for number, scale in components
        ),
        fractions.Fraction(0),
    )
    return -total if match["sign"] == "-" else total
```
