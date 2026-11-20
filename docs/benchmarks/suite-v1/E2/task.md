# E2

Reply with exactly one fenced Python code block tagged `python`. Use the
standard library only. Do not use tools or include prose outside the block.

Implement `diff_intervals(base: list[tuple[int, int]], remove: list[tuple[int, int]]) -> list[tuple[int, int]]`.

Each tuple `(a, b)` denotes the closed integer set `{x in Z : a <= x <= b}`; tuples with `a > b` are empty and ignored.
Return `(union of base) \ (union of remove)` as the unique sorted list of closed intervals `[(a_i, b_i), ...]` with `a_i <= b_i` and `b_i + 1 < a_{i+1}` (adjacent integer ranges must be merged, e.g. `[1,2]` and `[3,4]` become `[1,4]`).
Do not mutate `base` or `remove`.

Limits: `len(base), len(remove) <= 100_000`; `|a|, |b| <= 10**18`.
Use at most `O(k log(k + 1))` time, where `k` is the total number of intervals.
