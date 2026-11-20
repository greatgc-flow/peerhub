# M1

Reply with exactly one fenced Python code block tagged `python`. Use the
standard library only. Do not use tools or include prose outside the block.

Implement `shift_date(y: int, m: int, d: int, delta: int) -> tuple[int, int, int, int] | None`.

Use the proleptic Gregorian calendar with astronomical year numbering (year `0` exists between `-1` and `1`).
A year `y` is a leap year iff `y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)` (Python floor modulo: `0` and `-400` are leap; `100` and `-100` are not).

- If `(y, m, d)` is invalid (`m` not in `1..12` or `d` not in `1..days_in_month(y, m)`), return `None`.
- Otherwise add `delta` days (positive, zero, or negative) and return `(y2, m2, d2, wd)` where `wd` is weekday (`0=Monday .. 6=Sunday`), anchored by `(2000, 1, 1)` being Saturday (`5`).

Limits: `|y|, |delta| <= 10**18`. Must run in O(1) time.
The year in the result may lie outside the input year bounds.
