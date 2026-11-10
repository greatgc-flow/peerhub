# Profile benchmark (top tiers), 2026-11-10

## Purpose
The [hard benchmark](../profiles-hard-2026-11-10/README.md) separated the low tiers (cx.standard 34, ag.standard 95). But cc.standard, cc.effort, cc.deepthink and ag.effort all scored 100/100 on it. This benchmark aims to separate those top tiers. It keeps the same format: deterministic, one no-tools prompt, one Python module as the answer, graded automatically.

## Files
- `task.md`: the prompt, sent verbatim (2,780 bytes). It specifies `search(pattern, text)` for a small backtracking regex dialect. The answer must return spans for the match and every group, `None`, or `"ERROR"`.
- `grade.py`: the grader, with 115 hidden cases. It has the same isolated-worker design and CLI as the hard grader, and calls `search` instead of `evaluate`.
- `reference.md`: the reference solution, a parser plus a continuation-passing backtracker.

## How to run
```
python grade.py <response.md>        # last line: SCORE n/115
python grade.py <response.md> -v     # also lists each failing case
```
Return values are compared after normalization: tuples and lists are treated as equal, and span items must be `int`.

## Why it should separate top tiers
Every rule is in the spec, but the rules interact. A case usually depends on several of them at once, so one slip costs several cases:
- **Capture reset per iteration** (ECMAScript style) plus **restoring captures on backtracking**. For example, `(?:(a)|b)*\1` on `aba` gives `((0,2), None)`. Python `re` keeps the old capture, and a solver that mutates one capture array gets this case wrong.
- **The exact empty-iteration rule:** an empty iteration is allowed before the minimum is reached and fails after it. For example, `(a*)*` vs `(a*)+` on `b`, and `(|a){2,}`. A naive loop either never terminates or keeps the wrong capture.
- **Backreference rules:** a reference to an unset group matches the empty string, `\10` means `\1` followed by `0`, and forward references are legal.
- **Parsing strictness:** `[]` and `[^]`, the `-` range rules, a stray `{`, `}` or `]` is ERROR, quantifier bounds are at most 1000, and stacked quantifiers are ERROR.
- **ASCII-only rules:** `\d`, `\w` and `\s` are ASCII-only, `\s` is only space, tab and newline, `.` matches `\r`, and `$` matches only at the true end.
- **Texts up to 1,000 characters:** a recursive matcher has to handle a recursion limit of 1000.

Translating patterns to Python `re` should fail about 30 cases (an estimate; not measured). Expected range, unmeasured: 60–90% for strong models.

## Results (2026-11-10)
One run per profile (r1), one prompt (`task.md`, no tools), sent through `peerhub ask` with an 1800 s ask timeout. CLI versions: claude 2.1.322, codex 0.167.0, agy 1.5.4. The grader allows 2 s per case (the same per-case limit as the hard grader). Raw answers are in [`runs/`](runs/) and the bare scores in [`runs/results.txt`](runs/results.txt).

| Profile | Answer time | Score |
|---|---|---|
| cc.effort | 155 s | 115/115 |
| cc.standard | 201 s | 115/115 |
| cc.deepthink | 202 s | 115/115 |
| ag.standard | 32 s | 111/115 |
| cx.standard | 52 s | 110/115 |
| ag.effort | 442 s | 101/115 |
| cc.fable (claude-fable-5-1) | 169 s | 115/115 |
| claude-fable-5-2 (now `cc.fable`) | 946 s | 115/115 |

Fable rows measured 2026-11-10 after the F-7D reset (13:00Z); the 5.2 row came from a temporary `cc.fable52` overlay (identity ping answered claude-fable-5-2). One run each: 5.2 took 946 s against 169 s for 5.1 at the same score, a single sample, not a measured speed difference.

Repeat runs of the cc profiles (same prompt, fresh stream, same day; r1 is the table above):

| Profile | r1 | r2 | r3 |
|---|---|---|---|
| cc.standard | 115 (201 s) | 115 (156 s) | 114 (119 s) |
| cc.effort | 115 (155 s) | 115 (85 s) | 115 (148 s) |
| cc.deepthink | 115 (202 s) | 115 (225 s) | 115 (263 s) |

**Variance:** cc.standard dipped to 114 once (r3); cc.effort and cc.deepthink were 115/115 in all three runs. The answers are in `runs/` (`*-r2.md`, `*-r3.md`).

**The cc tiers tie at the ceiling again.** cc.standard, cc.effort and cc.deepthink all scored 115/115, so this benchmark, like the hard one, does not separate them (one sample each, not a ranking). Both Fable models also scored 115/115.

**On this task ag.effort scored below ag.standard** (101 vs 111) and took about 14 times as long (442 s vs 32 s). That is one run each, so it shows that the higher effort did not help here, not that it is generally worse. cx.standard (110) landed between the two ag profiles.

The failing cases of the three non-perfect profiles are not listed here; `python -I -B grade.py runs/<file>.md -v` prints them.

## Verification
Verified 2026-11-10: `reference.md` scores **115/115** after the case fix below.
- First run: 114/115. Case #75, `[\n-\r]` on `\v`, expected a match, but `\r` is not an escape in task.md, so the reference correctly returned ERROR. The case was wrong, not the reference. It is now `[\t-\n]+` on `"a\n\t\v"` → `((1,3),)`, which tests escaped range endpoints. task.md now also states that one-character escapes such as `\n` and `\-` may be range endpoints, while `\d` and the other class escapes may not.
- Before the run, the reference was traced by hand against all 115 cases, which corrected one expected value (`(?:a|ab){2,3}c` → `(0,6)`).
- Not yet done: a cross-check against a JavaScript engine (`du` flags) on the cases ECMAScript also defines.
