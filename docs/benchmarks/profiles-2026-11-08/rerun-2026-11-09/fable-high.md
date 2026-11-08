```python
import re
from fractions import Fraction

_NUM = r'(\d+(?:[.,]\d+)?)'
_RE = re.compile(
    r'^([+-]?)P'
    r'(?:' + _NUM + r'Y)?'
    r'(?:' + _NUM + r'M)?'
    r'(?:' + _NUM + r'W)?'
    r'(?:' + _NUM + r'D)?'
    r'(?:T'
    r'(?:' + _NUM + r'H)?'
    r'(?:' + _NUM + r'M)?'
    r'(?:' + _NUM + r'S)?'
    r')?$'
)

_FACTORS = (
    365 * 86400,  # Y
    30 * 86400,   # M (months)
    7 * 86400,    # W
    86400,        # D
    3600,         # H
    60,           # M (minutes)
    1,            # S
)


def parse_duration(s: str) -> Fraction:
    if not isinstance(s, str):
        raise ValueError("duration must be a string")
    m = _RE.fullmatch(s)
    if not m:
        raise ValueError(f"invalid duration: {s!r}")
    sign = m.group(1)
    parts = m.groups()[1:]
    present = [(i, p) for i, p in enumerate(parts) if p is not None]
    if not present:
        raise ValueError(f"invalid duration (no components): {s!r}")
    # 'T' must be followed by at least one time component
    if 'T' in s and not any(i >= 4 for i, _ in present):
        raise ValueError(f"invalid duration ('T' without time components): {s!r}")
    total = Fraction(0)
    last_idx = present[-1][0]
    for i, p in present:
        if ('.' in p or ',' in p) and i != last_idx:
            raise ValueError(f"invalid duration (fraction not in last component): {s!r}")
        val = Fraction(p.replace(',', '.'))
        total += val * _FACTORS[i]
    if sign == '-':
        total = -total
    return total
```