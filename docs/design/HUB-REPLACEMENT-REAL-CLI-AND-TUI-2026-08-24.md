# Real peerhub CLI Surface + TUI/Diagnostics Layer — Ground Truth & Detailed Design (2026-08-24)

Status: terminal-authored from direct reads of `peerhub/cli.py`,
`peerhub/telemetry/presenter.py`, `peerhub/telemetry/statusline.py`,
`peerhub/dispatch/capability.py`. Produced in response to the user's
explicit request to detail "UI(TUI) 등 모든 세부사항까지 빠짐없이"
(UI/TUI and every other detail, nothing missed) before TDD. Complements
`HUB-REPLACEMENT-REAL-SOURCE-GROUNDTRUTH-2026-08-24.md`.

## 1. The EXACT real CLI surface (`peerhub/cli.py`, verified by direct read)

5 subcommands, exact arguments:

**`status`**: `--workspace <path>` (default `.`), mutually-exclusive
`--peer <name>` | `--all`.

**`diag`**: `--workspace <path>`, `--live` (continuous monitoring loop —
REAL, already implemented), `--fresh` (bypass telemetry cache),
`--no-color`, `--json`.

**`broadcast <prompt>`**: `--peers` (default `"ag,cx"`),
`--capability-tier` (required-shape, choices from the real `CapabilityTier`
IntEnum: `READ_ONLY`(0) / `WORKTREE_WRITE`(1) / `GIT_MUTATE`(2) /
`REMOTE_MUTATE`(3) — "increasing downstream peer authority granted to one
dispatch"), `--workspace`, `--timeout-seconds` (default 60),
`--silence-timeout-seconds` (default 60), `--max-output-bytes` (default
1,000,000), `--json`.

**`ask <peer> <prompt>`**: `--capability-tier` (REQUIRED, same enum),
`--workspace`, `--profile` (explicit profile ID), `--timeout-seconds`
(60), `--silence-timeout-seconds` (60), `--max-output-bytes` (1,000,000),
`--json`.

**`statusline`**: `--peer` (choices `ag`/`cc`/`cx`, default `ag`),
`--workspace`.

### Corrections this establishes for gaps 1 and 7

- Gap-7 speculated legacy-parity flags (`--tokens`, `--sessions`,
  `--profiles`, `--accounts`, `--project`, separate `--watch`) on
  `peerhub diag` — **none of these exist in the real CLI today.** Real
  `diag` has exactly: `--workspace --live --fresh --no-color --json`.
  `--live` appears to already cover the "continuous loop" concept gap-7
  called `--watch`; there is no evidence of a separate `--watch` mode.
- `--capability-tier` being REQUIRED on `ask` (and present with a default
  on `broadcast`) is real, load-bearing authorization already in the CLI
  layer — any native command surface gaps 2-6 propose should follow this
  same pattern (an explicit, required-where-mutating capability-tier
  argument), not invent a separate authorization mechanism.
- `--timeout-seconds` / `--silence-timeout-seconds` / `--max-output-bytes`
  are real, already-present per-dispatch controls — directly relevant to
  gap-5's task/approval design (a task's underlying dispatch already has
  timeout and output-size bounds; task-level checkpointing should compose
  with these, not duplicate them) and to CLAUDE.md's documented
  zombie/silence-timeout discipline for this whole project.

## 2. The real TUI/diagnostics rendering engine

`peerhub/telemetry/presenter.py` (615 lines) — `TelemetryPresenter`
class. This is a genuinely substantial, already-built rendering engine,
not a stub:

- **ANSI color table** (`reset/bold/dim/green/yellow/red/cyan/magenta`),
  auto-disabled when not a TTY or `NO_COLOR` is set (respects the
  standard convention).
- **CJK/emoji-aware display-width math** (`_dw`/`_pad`): correctly
  computes terminal column width for wide (CJK) and emoji characters,
  strips ANSI escape sequences before measuring, skips combining marks —
  this is why this session's own `diag.bat` SUMMARY table renders
  correctly aligned despite mixing 🔴/🟢 emoji, Korean text, and ASCII
  numbers in the same columns.
- **Portable path resolution** (`_find_sys_dir`): workspace-relative
  first, falls back to `PEERHUB_SYS_DIR` env var, never a hardcoded drive
  letter — consistent with this whole project's portability requirement.
- **`collect_live_snapshot()`**: reads `orchestration.json`, ag's
  statusline stdin log (2 fallback paths), and injected
  `UsageProjectionSnapshot` sequences partitioned by peer instance
  (`cc`/`cx`/presumably `ag`) — this is the actual data-collection path
  behind the SUMMARY table's per-peer/pool rows.
- **Narrow-terminal handling**: truncates lines with `...` only below 65
  columns — a real, considered responsive-rendering rule.
- `--live` in `cli.py` drives this presenter in a loop (continuous
  monitoring); exact refresh-interval/redraw mechanics not yet read line
  by line — **follow-up needed** to confirm refresh cadence and whether
  it clears/redraws the screen (`\033[2J` / cursor-home) or just appends.

`peerhub/telemetry/statusline.py` — narrow, currently ag-specific
(`format_statusline_ag(stdin_data: str) -> str`), matching gap-7's
earlier note. No `cc`/`cx` statusline formatter found yet — **real gap**,
not a design-only one.

No `curses`/`rich`/`textual`/`blessed` dependency found anywhere in
`peerhub/` — the existing TUI is hand-rolled ANSI + width math, not built
on a TUI framework. This is a real architectural fact for the "aesthetic
and functional beauty" review the user asked for: any new interactive
surface (gap-2's consensus status, gap-5's task list, gap-6's alert feed)
should either extend this same hand-rolled approach for consistency, or
a deliberate decision to adopt a TUI framework needs to be raised as its
own ratifiable design choice — not silently mixed.

## 3. Detailed TUI/UI design across all 7 gap categories (new — not previously covered)

The existing `diag`/`--live`/`TelemetryPresenter` pattern is the one
real, proven interactive-display precedent in this codebase. The design
below extends it consistently to the domains gaps 2-6 introduce, rather
than inventing a new UI paradigm per category.

### 3.1 Rendering contract (applies to every new display surface)

- Every live/watch view is a **pure function of a snapshot struct**
  (mirrors `collect_live_snapshot()` → render, not an inline
  print-as-you-go loop) — this is what makes `--json` a real sibling
  output mode rather than a parallel implementation: the same snapshot
  feeds both a human table renderer and `json.dumps`.
  - **Every gap-2..6 native command that has both a human and `--json`
    mode MUST follow this same snapshot-then-render split.**
- Column width/alignment goes through `_dw`/`_pad` (or a shared successor
  of them) — never raw `len()` or manual spacing — so CJK/emoji-heavy
  output (this project routes through Korean-speaking humans and
  emoji-status peers) stays aligned.
- Color is optional and auto-detected (`isatty()` + `NO_COLOR`), never
  assumed. Every color use has a non-color fallback that's still legible
  (status words, not just color, e.g. "CRIT"/"OK" text alongside 🔴/🟢).
- Narrow-terminal truncation is a real requirement, not an edge case —
  reuse the existing <65-column truncation threshold and `...` convention
  for any new table.
- `--fresh` (cache bypass) and `--live` (continuous loop) are the
  established flag vocabulary — new domains needing a live view should
  reuse these exact flag names, not invent `--refresh`/`--stream`/etc.

### 3.2 Per-category TUI additions needed

- **Gap 1 (compat/migration)**: a `peerhub migrate` status view — table
  of discovered callers with `status: discovered|adapted|verified|
  cutover|rolled_back`, following the same snapshot+render+`--json`
  pattern. No live/watch mode needed (migration status changes slowly,
  operator-driven).
- **Gap 2 (consensus)**: `peerhub consensus status --round <id>` and
  `--room <id> --active` need a live view — voters, votes cast/missing,
  quorum math, blocking reasons, deadline countdown. The deadline
  countdown should reuse `presenter.py`'s existing `_format_countdown()`
  (already built for quota-reset countdowns — same concept, different
  domain). A room with multiple active rounds needs a summary table
  (round_id, subject, state, votes-so-far/required, deadline) — same
  column-width discipline as the existing SUMMARY table.
- **Gap 3 (session/room/thread/duty)**: `peerhub terminal status` (who
  holds duty, lease expiry countdown, last heartbeat) as a compact
  always-visible strip — this is a natural `statusline`-family addition,
  extending `format_statusline_ag`'s pattern to show duty-lease state,
  not just quota. Thread/room views are lower-frequency (operator
  browsing, not live-monitoring) — table + `--json`, no live loop needed.
- **Gap 4 (health/leadership)**: this is the CLOSEST extension of the
  EXISTING `diag` SUMMARY table — add `AdmissionState`/
  `AvailabilityState`/`QuarantineAuthorityClass` columns per peer/pool,
  reusing the exact row format already in `presenter.py`. Leadership/role
  status is a new small table (room, role, holder, fencing epoch,
  challenge-window countdown if pending).
- **Gap 5 (task lifecycle)**: `peerhub task status <id>` (single-task
  detail view: stage, checkpoint, executor, approval state if pending)
  and `peerhub task list [filters]` (table, same discipline). Approval
  gates should surface prominently — likely worth a distinct color/marker
  (not just another table row) since they block progress on a human
  decision, mirroring how the existing SUMMARY table's 🔴/🟡/🟢 markers
  draw attention to CRIT/WARN rows.
- **Gap 6 (governance/alerts)**: `peerhub alert list --active` deserves
  the same attention-drawing treatment as `diag`'s existing "ATTENTION"
  section (`[CRIT]`/`[WARN]`/`[INFO]` lines already printed above the
  SUMMARY table per this session's own `diag.bat` output) — alerts should
  literally render into that same ATTENTION block, not a separate command
  a human has to remember to run. Lesson/directive/proposal views are
  operator-browsing tables, no live mode needed.
- **Gap 7 (diagnostics)**: extend the existing `SUMMARY` table with the
  new EXH/credit/model-status projections already scoped in gap-7's own
  design, using the exact same rendering primitives — this is genuinely
  the smallest incremental UI work of the 7 categories, confirming gap-7's
  own "adapter delta, not redesign" conclusion, now down to the pixel/
  column level.

### 3.3 Open questions for this UI/TUI layer (new)

1. ~~Does `--live`'s refresh loop clear-and-redraw or append?~~ —
   **RESOLVED**, see "RESOLVED (2026-08-24): `--live` loop mechanics"
   section directly below: clear-and-redraw, 2.0s interval.
2. Should the ATTENTION block (CRIT/WARN/INFO lines above SUMMARY) become
   a formally named, reusable primitive that every gap's alerts/blocking
   states feed into, or stay diag-specific?
3. Is a TUI framework (rich/textual) ever worth adopting, given the
   codebase currently has zero framework dependency and a working
   hand-rolled approach — or is extending the hand-rolled `_dw`/`_pad`/
   ANSI approach the deliberate, permanent choice? (Not decided either
   way — flagging as a real choice, not defaulting silently.)
4. Should `statusline` gain `cc`/`cx` formatters (parallel to the
   existing `format_statusline_ag`), and if so, do they show the same
   fields or peer-specific ones?
5. Aesthetic/consistency question the user explicitly asked about: is the
   current `diag` output (SUMMARY table + ATTENTION block + SESSIONS &
   CONSUMPTION section, per this session's own repeated `diag.bat`
   observations) actually the right visual model to extend to 6 more
   domains, or does cramming gap-2..6's new tables into the same command
   risk an overloaded, hard-to-scan single view? (A `peerhub diag
   --section consensus|health|tasks|governance` filter is one option, not
   yet decided.)

## RESOLVED (2026-08-24): `--live` loop mechanics, confirmed by direct read of `_run_diag`

`peerhub/cli.py`'s `_run_diag` (`--live` branch): **clear-and-redraw**,
not append — `os.system("cls")` on Windows (`os.name == "nt"`),
`\033[2J\033[H` (ANSI clear-screen + cursor-home) elsewhere. **2.0-second
refresh interval**, polling for a keypress every 0.05s (`msvcrt.kbhit()`
on Windows) so ESC/`q`/`Q`/Ctrl+C exits promptly without waiting a full
interval. Snapshot→render split confirmed exactly as assumed in section
3.1 above: `presenter.collect_live_snapshot()` then either
`json.dumps(snapshot)` or `presenter.render(snapshot)` — the same
snapshot feeds both output modes, no duplicate collection logic.
`--json` mode does NOT loop/clear even under `--live` in the current
code path shown (it still prints once per 2s tick inside the same while
loop, just via `json.dumps` instead of `render`) — same refresh cadence
applies to both.

This confirms open question 1 from section 3.3 above. **Extend this
exact pattern (clear-and-redraw, 2s interval, ESC/q/Ctrl+C exit,
snapshot→render split) to any new `--live` view gaps 2-6 add**, rather
than inventing a different refresh mechanism per domain.
