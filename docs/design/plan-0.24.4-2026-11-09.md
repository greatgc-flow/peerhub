# Plan 0.24.4 - pace in the compact monitor + AI-readable balancing (2026-11-09)

Consensus: cx.astra, cc.fable, ag.argon (r1 independent audits, r2 cross-critique; amendments below accepted).
Problem: the 0.24.0 compact screen removed PACE; AIs balancing quota from `peerhub monitor` + `peerhub guide` saw only LEFT/RESETS/STATE. Non-TTY runs also printed two human frames.

PACE = used% - elapsed% of the window (cumulative vs even burn): + = ahead of even burn (over-use), - = behind (under-use). It is not recent activity.

## Changes (human text only; JSON 1.1/2.0 byte-identical)
P1 Non-TTY human watch prints only the completed frame (monitor.py initial frame skipped); TTY and `--json` framing unchanged; refresh failure / token-use stop / interrupt keep their diagnostics.
P2 Compact POOLS gets `PACE` after RESETS: signed integer from `projection.pace_pt` (`+46`, `-12`, `0`), `-` when no valid anchor (n/a, ?, reset, unusable). Shown for aged rows like LEFT. Both the wide table and the narrow wrapped rows; no column-dropping logic (PROFILES truncates first). Legend defines the sign.
P3 Guide: Load line gets the balance recipe (suggest is advisory; reject stale/unknown/reset/<5% pools; prefer pools without `fast`; all-fast = slow/defer; compare 7D PACE, 5H only gates; PACE needs fresh AGE); monitor line drops TTY/cycles trivia; duplicate "lowest tier" merged; stale "~900 tokens" claim replaced by measured bytes. <=60 lines, <=5600 B.
P4 Stale text: monitor.py help (--view rich, --expand), README monitor/frame wording, lessons.toml monitor-states + `explain pace` / `explain monitor pace` (formula, signs, freshness, H scope).
P5 suggest ordering: eligibility unchanged; key = (candidate has an applicable `fast` pool, existing worst-pool H score, profile name). All-fast tier still yields a pick (no refresh hint).
P6 docs/batch.md rule H: vocabulary `?`/`aged`/`fast` (was UNKNOWN/stale), one ordering sentence. No "lowest PACE" tie-break (rejected: duplicates policy).
P7 Workspace AGENTS.md (not shipped): owner target 7D pace cc <= ag == cx, per-family 7D comparison.
P8 Drop the `precedence:` legend line from the compact frame (kept in `explain monitor`).

## Non-goals
EXH column in compact, new flags, new score formula, JSON changes, auto-routing/throttling, inlining hidden profiles in guide, DB-open error relabel, removing the generic ag2 clause (owner cap ag1 stays local policy).

## Objective 100-gates (scorecard)
1 Pace visibility: tests - PACE cell correct/`-` at widths 80 and 100 for MEASURED, STALE(aged), n/a, ?, reset rows; narrow path keeps quota facts.
2 Balancing: suggest scenarios - non-fast before fast, all-fast still picks, <5% excluded, low(5-10%) eligible, aged/?/reset excluded, name tie.
3 Guide accuracy: <=60 lines, every claim maps to code/docs, recipe present.
4 Guide size: `peerhub guide` <= 5600 B (LF-normalized).
5 Monitor size: fixed 10-pool fixture <= 1800 B at widths 80/100, exactly one human frame on non-TTY success.
6 AI-readability: legend defines every state word + PACE sign/unit; `precedence:` absent.
7 Docs consistency: zero hits for "Render all loaded detail rows", "appends once every N s", "UNKNOWN or stale"; `explain pace` and `explain monitor pace` resolve; docs/lessons.md regenerated.
8 Tests: new tests red->green; full pytest + pyright clean.
9 Compat: JSON 1.1/2.0 + frame-sequence tests unchanged.
10 Release: 0.24.4 version/CHANGELOG/STATUS agree, CI + live gate + package evidence per release recipe.
