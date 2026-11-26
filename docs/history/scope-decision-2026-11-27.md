# Scope decision, 2026-11-27

Two-round debate among three independent reviewers (ag.argon, ag.deepthink, cx.deepthink), with the facts given inline. The owner asked for a decision covering the features proposed this week and the features already shipped. Principles: P1 "so very simple" (prefer not building), P2 PeerHub gives facts and the AI decides, P3 compact/expand parity and no hardcoding, P4 release bar, P5 peers learn flow, state and next steps from the repo.
Verdicts are unanimous unless a split is shown; a split is settled by majority.

## This week's proposals

| Item | Decision |
|---|---|
| Root `NOW.md` (handoff) | KEEP, simplified, peerhub first. Sections: State, Recent flow (at most 5), Next (at most 8), Rules that bite (at most 8); under 60 lines. The test checks headings, caps and the line cap. It does not pin the version (3/3): a pin forces a ceremonial edit on every bump, which is how `docs/HANDOFF.md` drifted. Engram and VerySimpleGuide adopt it after one peerhub release shows it stays accurate (3/3). |
| Repeatable `ask --query-file` | KEEP (3/3). Serves inline asks in the private vote workspace; ag in a repo workspace reads `NOW.md` itself. |
| `explain handoff` lesson | CUT (3/3). The `AGENTS.md` pointer already says it. |
| Monitor watch keyboard controls (#488 item 4) | NOT MERGED (3/3). Watch mode already auto-refreshes. The interactive TUI stays in the deferred backlog. |
| RUNNING inspection | KEEP, simplified. Each RUNNING row shows a short ask ID, and one footer line gives the command that opens an ask by ID. Compact and expand both get it (argon's blend of the per-row and footer forms). |
| #492 fixture under pytest `tmp_path` | KEEP (3/3). |
| Requirements registry (R1-R17) | KEEP (2/3; deepthink would cut the table down to ID and test only). The owner asked for recurrence prevention, and the check costs almost nothing. |

## Existing features

| Item | Decision |
|---|---|
| `diag --live`, `--view`, `--cycles` (diag watch mode) | CUT (3/3; the largest simplification for its risk). `monitor` is the human watch, and AIs read `diag --plan` once per turn. |
| Monitor JSON schema 1.1 | RETIRE (3/3); 2.0 remains. |
| `monitor defaults` (saved preferences) and the `monitor groups` subcommand | CUT. `--group` stays and its help lists the groups. |
| `monitor --collect-every`, `--probe` | CUT (3/3). |
| `monitor --show-quota-notes` | CUT (2/3): quota notes always show in `--expand`. |
| `diag --plan --json --compact` | CUT (3/3). |
| `batch run --response-task` | CUT (3/3): `--responses` is enough. |
| `ask --final-only` | KEEP (2/3). |
| `suggest` command | DEPRECATE now and remove in the next batch (3/3 to retire it). It is a second selection policy next to `diag --plan`. The one-line `suggest:` hint in `diag --plan` stays. |
| CLI aliases (`ask --prompt-file`, `peer show`, `profile -w`) | KEEP (2/3). |
| `ask` waiting up to 120 s under high CPU | Becomes a warning (3/3): print the load facts and proceed. Bounded low-memory protection and the batch admission gate stay. |
| `explain a1`-`a6` | KEEP (3/3). The monitor footer points to them, and they come from one source. |
| Lessons | CONSOLIDATE into clusters (monitor, ag, quota, cc/cx hazards, review and votes). Every existing name still resolves, as an alias of its cluster, so references in docs and AGENTS files keep working. |
| API billing observations | DEFER: no new work (3/3). |

Every removal ships with a CHANGELOG "Removed" line that names the replacement. The version becomes 0.27.0.
