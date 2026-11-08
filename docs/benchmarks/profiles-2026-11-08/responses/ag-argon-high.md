```python
from fractions import Fraction
import re

_DATE_ORDER = "YMWD"
_TIME_ORDER = "HMS"
_DATE_UNITS = {
    "Y": 365 * 86400,
    "M": 30 * 86400,
    "W": 7 * 86400,
    "D": 86400,
}
_TIME_UNITS = {
    "H": 3600,
    "M": 60,
    "S": 1,
}
_TOKEN_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)([A-Z])")


def _parse_part(
    part: str, order: str, units: dict[str, int]
) -> list[tuple[str, int]]:
  pos = 0
  last_idx = -1
  items: list[tuple[str, int]] = []
  while pos < len(part):
    m = _TOKEN_RE.match(part, pos)
    if m is None:
      raise ValueError(f"Invalid duration syntax: {part!r}")
    num_str, unit = m.group(1), m.group(2)
    idx = order.find(unit)
    if idx <= last_idx:
      raise ValueError(f"Invalid or out-of-order designator: {unit!r}")
    last_idx = idx
    items.append((num_str, units[unit]))
    pos = m.end()
  return items


def parse_duration(s: str) -> Fraction:
  if not isinstance(s, str) or not s:
    raise ValueError("Duration must be a non-empty string")

  neg = False
  idx = 0
  if s[0] in "+-":
    neg = s[0] == "-"
    idx = 1

  if idx >= len(s) or s[idx] != "P":
    raise ValueError("Duration must start with optional sign followed by 'P'")

  body = s[idx + 1 :]
  if not body:
    raise ValueError("Duration must contain at least one component")

  if "T" in body:
    date_part, time_part = body.split("T", 1)
    if not time_part:
      raise ValueError("'T' must be followed by at least one time component")
  else:
    date_part, time_part = body, ""

  items = _parse_part(date_part, _DATE_ORDER, _DATE_UNITS) + _parse_part(
      time_part, _TIME_ORDER, _TIME_UNITS
  )
  if not items:
    raise ValueError("Duration must contain at least one component")

  for num_str, _ in items[:-1]:
    if "." in num_str or "," in num_str:
      raise ValueError("Only the last component may have a fractional part")

  total = Fraction(0)
  for num_str, unit_seconds in items:
    total += Fraction(num_str.replace(",", ".")) * unit_seconds

  return -total if neg else total
```
