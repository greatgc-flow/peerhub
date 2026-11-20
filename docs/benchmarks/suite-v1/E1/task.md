# E1

Reply with exactly one fenced Python code block tagged `python`. Use the
standard library only. Do not use tools or include prose outside the block.

Implement `roman_to_int(s: str) -> int`.

Return the integer value in `1..3999` if `s` is a strictly canonical uppercase Roman numeral, else `-1`.

Rules
- Only ASCII `I, V, X, L, C, D, M` are allowed (no whitespace, no lowercase).
- A string is canonical iff it equals the standard greedy encoding of some integer `n` in `1..3999` using `(1000,M), (900,CM), (500,D), (400,CD), (100,C), (90,XC), (50,L), (40,XL), (10,X), (9,IX), (5,V), (4,IV), (1,I)`.
- Limits: `len(s) <= 1000`.
