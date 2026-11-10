# Profile benchmark (hard), 2026-11-10

## Purpose
In the [2026-11-08 profile benchmark](../profiles-2026-11-08.md), all 7 top configurations scored 45/45, so it could not tell them apart. This benchmark is a harder replacement. It stays deterministic, needs one no-tools prompt, and expects one Python module as the answer. It is graded automatically.

## Files
- `task.md`: the prompt, sent verbatim (about 2.1 KB). It specifies `evaluate(src)` for a tiny integer language: lexing, a grammar with precedence and associativity, and five exact error codes with fixed priority.
- `grade.py`: the grader, with 100 hidden cases.
- `reference.md`: the reference solution, which must score 100/100.

## How to run
```
python grade.py <response.md>        # last line: SCORE n/100
python grade.py <response.md> -v     # also lists each failing case
```
The CLI matches the 2026-11-08 grader. It takes the first ```` ```python ```` block (falling back to the first fenced block, then the whole file).

The code runs in a separate `python -I -B` worker process. Each case has a 2 s limit. A worker that hangs or crashes is killed, and a fresh worker resumes at the next case. All exceptions, including `RecursionError` and `MemoryError`, count as a failed case.

The worker installs an audit hook that blocks sockets, subprocesses, file writes, deletes and renames, and `ctypes`. The grader itself writes no files and opens no network connections. This is enough for local use with model output, but it is not a security sandbox.

## What makes it hard
No obscure knowledge is needed: every rule is in the spec. Points are lost through small deviations that a quick implementation makes:
- **Unicode traps:** `str.isdigit()`, `str.isspace()` and `\d`/`\s` accept characters that the spec rejects (`²`, `٣`, fullwidth digits, `\r`, NBSP, `\v`). `int("1_000")` also succeeds where the spec says SYNTAX.
- **Python defaults vs. the spec:** Python `//` and `%` floor, but the spec truncates. `int()` refuses literals over 4,300 digits, and the case expects OVERFLOW.
- **Precedence corners:** `-2^2`, `2^-1^2`, `-2^-0`, `2*3%4`, and right-associative `^`.
- **Error ordering:** SYNTAX anywhere beats runtime errors, operands are evaluated left before right, there is no short-circuit (`0*(1/0)`), and an overflow in an intermediate value or literal counts even when the final value would fit (`0*10^19`, `10^18*10/10`).
- **Performance:** `9^9^9` and `2^10^18` must return OVERFLOW quickly instead of being computed. A 3,000-term chain breaks recursive evaluators of a left-deep tree.
- **State:** variables must not leak between calls.

## Expected discrimination
Each trap family is worth 2 to 7 cases, so a model that misses one family typically loses 3 to 7 points. Strong models were expected to land at 80–97/100, and weaker or rushed answers at 60–85/100. The first runs (below) did not bear that out at the top.

## Results (2026-11-10)
One run per profile, one prompt (`task.md`, no tools), sent through `peerhub ask` with an 1800 s ask timeout. CLI versions: claude 2.1.322, codex 0.167.0, agy 1.5.4. The grader allows 2 s per case. The raw answers are in [`runs/`](runs/) (the trailing `session:` line that `ask` prints was removed from each file); the bare scores are in [`runs/results.txt`](runs/results.txt).

| Profile | Answer time | Score |
|---|---|---|
| cc.standard | 65 s | 100/100 |
| cc.effort | 42 s | 100/100 |
| cc.deepthink | 44 s | 100/100 |
| ag.effort | 416 s | 100/100 |
| ag.standard | 22 s | 95/100 |
| cx.standard | 40 s | 34/100 |
| cc.fable (claude-fable-5-1) | 58 s | 100/100 |
| claude-fable-5-2 (now `cc.fable`) | 75 s | 100/100 |

Fable rows measured 2026-11-10 after the F-7D reset (13:00Z); the 5.2 row came from a temporary `cc.fable52` overlay (identity ping answered claude-fable-5-2).

Repeat runs (same prompt, fresh stream, same day; r1 is the table above):

| Profile | r1 | r2 | r3 | r4 |
|---|---|---|---|---|
| cc.standard | 100 (65 s) | 100 (55 s) | 98 (60 s) | 100 (66 s) |
| cc.effort | 100 (42 s) | 100 (34 s) | 100 (41 s) | 100 (29 s) |
| cc.deepthink | 100 (44 s) | 100 (61 s) | 100 (56 s) | 100 (41 s) |

**Variance:** cc.effort and cc.deepthink scored 100/100 in all four runs and cc.standard in three of four (98 once, r3), so the 2-point drop is within run-to-run noise for a single sample and does not rank cc.standard below the others. The ag and cx profiles have one run each. The raw scores are in [`runs/results.txt`](runs/results.txt) and every answer is in `runs/` (`*-r2.md` to `*-r4.md` for the repeats).

**The purpose was not met at the top.** Four of the six profiles scored 100/100, so this benchmark still cannot separate cc.standard, cc.effort, cc.deepthink and ag.effort: 100 is a ceiling for cc and for ag.effort. It does separate the low tiers (cx.standard 34, ag.standard 95). One run each is a single sample, not a ranking. Both Fable models also scored 100/100 (one run each). A harder task is needed to tell the top profiles apart.

**cx.standard (34/100) is a real bug in the answer, not a grader problem.** In `runs/cx-standard.md`, six nested parser functions (`parse_term`, `parse_expr`, `ast_power`, `ast_unary`, `ast_term`, `ast_expr`) do `pos += 1` without declaring `nonlocal pos`. Python then treats `pos` as a local variable there and raises `UnboundLocalError` as soon as one of those lines runs, which happens for every binary operator, unary sign and `^`. Programs without them (for example a single literal) can still pass. The sibling functions `accept`, `parse_primary` and `parse_stmt` do declare `nonlocal`, which is why the file looks plausible at a glance.

**ag.standard (95/100) loses 5 cases** (`grade.py runs/ag-standard.md -v`, 2026-11-10): #40 and #41, the 3,000-term `1+1+...` / `1-1-...` chains, return SYNTAX (a recursive parser hitting the recursion limit); #63 `２` (fullwidth digit) and #64 `٣+1` (Arabic-Indic digit) are evaluated instead of SYNTAX (`isdigit()`/`int()` accept them); #99, a literal longer than 4,300 digits, returns SYNTAX instead of OVERFLOW (`int()` digit limit).

## Verification (2026-11-10)
- `reference.md`: SCORE 100/100, re-run 2026-11-10 after the case-27 fix below (`python -I -B grade.py reference.md`).
- Buggy variant (not committed): 85/100, measured 2026-11-10. It used `isdigit()`/`isspace()` lexing, Python floor `//`/`%`, a naive `a ** b`, and no literal length guard. This was measured before the case-27 fix. It also failed the old case 27, so against the current case set it would likely score 86/100.
- Case 27 fix: the original case `(-1)^1000000000000000001` expected -1. But its exponent literal is 10^18+1, which task.md already defines as OVERFLOW, so the reference was correct and the case was wrong. The case is now `(-1)^999999999999999999` = -1, and task.md states both outcomes explicitly.
