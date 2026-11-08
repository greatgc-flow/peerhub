# Task (answer with ONE python code block only; no tools, no prose)
Implement `parse_duration(s: str) -> fractions.Fraction` returning the number of SECONDS for an ISO-8601-style duration, with these exact rules:
- Optional leading sign '+' or '-', then 'P'. Designators in order: Y M W D, then optionally 'T' followed by H M S. 'M' before 'T' = months, after 'T' = minutes.
- Units: Y=365 days, M=30 days, W=7 days, D=86400 s, H=3600 s, M(inute)=60 s, S=1 s.
- Each number is a non-empty decimal: digits with an optional fractional part using '.' or ',' as separator (at least one digit on each side of the separator). Only the LAST present component may have a fraction.
- At least one component is required. 'T' must be followed by at least one of H/M/S. Each designator at most once, order enforced. No spaces, no exponents, uppercase only.
- Return an exact Fraction (no float rounding). Raise ValueError for anything invalid.
