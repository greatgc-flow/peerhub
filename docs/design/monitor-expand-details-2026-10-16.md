# Height-adaptive monitor detail rows (2026-10-16)

**Status:** Slice 1 implemented (`peerhub monitor --expand`: human output, all loaded detail rows rendered, stable session handle included, DB query cap 100 with truncation disclosure, snapshot unconstrained).  
**Review base:** `c717ee0097c7a1d89fa8d72616207d0dd0db7588` on `fix/monitor-detail-expansion`.

## Scope and Intent

Fix the presentation boundary and group inspection for the five record-detail groups in catalog order (`sessions`, `usage`, `context`, `capacity`, `runtime`, per `peerhub/cli/monitor_preferences.py:11–21`). Keep SQLite storage queries and provider collection unchanged.

- **Option Resolution:** Omitted `--group` honors `effective_preferences()` (`workspace > global > packaged`), resolving user and workspace overrides before falling back to packaged defaults.
- **Inspection Mode:** Explicit `--group` selection signals inspection intent. In unbounded/redirected output, it prints all loaded candidate detail rows without aggregate summaries. In bounded TTY mode, it allocates viewport rows to selected groups plus aggregate summaries.
- **History Reachability:** The in-memory sample cap (100 sessions, 100 quota observations) limits monitor snapshots. History beyond the cap remains accessible via `peerhub stats`, Bridge session queries, and direct database queries. Inspection rows for `sessions` display the stable session handle.

## Layout and Responsive Rules

- **Minimum Width:** Minimum dashboard width is 32 columns. When terminal width is `< 32`, render a single clipped notice `_fit("Monitor: terminal too narrow", width - 1)`. Micro-width ladders (<32 columns), 1-cell status codes (`P/C/U/p/c`), and width 1–8 tables are deleted.
- **Coverage Suffixes and Fitting Priority:** Summaries display canonical source completeness: `PARTIAL`, `COMPLETE`, or `UNKNOWN`. At width >=48, use the long form (`source partial`, `source complete`, `source unknown`). At widths 32–47, use plain uppercase (`PARTIAL`, `COMPLETE`, `UNKNOWN`). Suffix width is reserved first; count prefix fits by priority: `N loaded · V visible · R off-screen · <suffix>`, then `V/N shown · +R · <suffix>`, then `V/N · <suffix>`, then `<suffix>` alone. Derived `VIA` codes (`VP`, `VC`, `p`, `c`) and `SRC ` prefixes are removed.
- **Summary Budget in Bounded TTY:**
  - Non-capacity groups (`sessions`, `usage`, `context`, `runtime`): exactly ONE summary line reserved after heading (aggregate count + source suffix, or semantic empty-state text + suffix).
  - `capacity` group: exactly TWO summary lines reserved after heading before detail allocation:
    1. *Aggregate counts:* `N loaded · V visible · R off-screen · <suffix>`.
    2. *Freshness & Urgency headline:* Reports exhaustive disjoint freshness counts (`measured`, `stale`, `error`, `unavailable`, `absent/unknown`, satisfying `M + S + E + U + A = total`) and lowest-remaining fresh pool. Mandatory items are urgency pool identity+percent (or `none`) and `<M>/<total>`.
       - *Drop order:* Drop zero-valued counts (`0 unk`, `0 unavail`, `0 err`, `0 stale`) -> shorten labels (`measured` -> `meas`, `error` -> `err`, `unavailable` -> `unavail`, `absent/unknown` -> `unk`) -> drop non-zero counts in reverse priority (`unk` -> `unavail` -> `err` -> `stale`) -> omit `(loaded sample)` qualification only below width 80 (lineage requires `(loaded sample)` at width >= 80 and permits omission only below 80 because line 1 displays `N loaded`) -> keep mandatory items -> final `_fit` clip.
       - *Band >= 80 (C >= 77):* Verified <= 77 cells, retaining `(loaded sample)`:
         - Fresh pool: `Urgency: ag 3P-7D 19% · 1/9 measured · 8 stale (loaded sample)` (62 cells).
         - No fresh pool: `Urgency: none · 0/9 measured · 9 stale (loaded sample)` (54 cells).
       - *Band 48–79 (C >= 45):* Verified <= 45 cells (sample qualification omitted):
         - Fresh pool: `Urgency: ag 3P-7D 19% · 1/9 meas · 8 stale` (42 cells).
         - No fresh pool: `Urgency: none · 0/9 meas · 9 stale` (34 cells).
       - *Band 32–47 (C >= 29):* Verified <= 29 cells (sample qualification omitted):
         - Fresh pool: `Urgency: ag 3P-7D 19% · 1/9` (27 cells).
         - No fresh pool: `Urgency: none · 0/9 meas` (24 cells).
         - Long pool ID fallback: Mandatory `<M>/<total>` must never be dropped; reserve percentage and denominator first (`19% · 1/9`), clipping identity only with `_fit`: `Urgency: ag quot... 19% · 1/9` (29 cells).
- **Urgency and Credits:** Urgency ranks only fresh, reset-valid, fractional `MEASURED` pools. Ties are broken by peer then pool ID. Expired or stale pools are never ranked. `reset_credit` observations are rendered as separate credit detail rows with count/expiry semantics and are never ranked for urgency.
- **Short-Height Omission & Warning Reservations:**
  - Reserve AGY continuation and refresh warning rows ahead of detail row allocation.
  - In tiny viewports where available height cannot fit all visible group headings and summaries, omit lowest-severity groups using established severity ranking.
  - Reserve exactly 1 overflow row when groups are omitted. Fitting follows an ordered fallback sequence (names list -> '+N groups' -> guidance only) with verified cell widths:
    1. *Names list with guidance (C >= 51):* `+2 omitted: capacity, runtime (see monitor --group)` (51 cells).
    2. *Count with guidance (C >= 39):* `+4 groups omitted (see monitor --group)` (39 cells).
    3. *Guidance only (C >= 24, fits width 32):* `See monitor --group <id>` (24 cells).
- **Temporal Reflow:** Live countdown/resize reflow recomputes freshness via pure temporal projection with no database reads: an observation flips to `STALE` when `age + elapsed >= effective_ttl`. Observations with TTL 0 or invalid TTL evaluate to `STALE`. Negative age (clock skew relative to `captured_at`/`observed_at`) evaluates to `UNKNOWN` ahead of TTL expiry. When reflow crosses reset (`read_at + elapsed >= resets_at`), pre-reset values transition to expired. The renderer never parses raw timestamps; it consumes pre-calculated timestamps, ages, and read-model flags.
- **Last-Known Values:** Stale quota observations with `resets_at <= read_at` (or invalid reset) are flagged with `expired: true` by the read model. The renderer never displays or headlines expired last-known values. Valid pre-reset last-known values may be shown as historical context, explicitly labeled `last-known · STALE`.
- **Derived Group Coverage:** Derived groups (`usage`, `context`, `runtime`) always carry the shared `session_sample` coverage DTO (`dimension=session_sample`) regardless of whether `sessions` is selected. The "sessions absent => UNKNOWN" rule is deleted. Coverage precedence is: `ERROR`/unavailable > `partial` (query cap reached) > `complete`; `unknown` when evidence is missing. Source completeness is reported locally in group summaries; `coverage` owns delivery/telemetry scope completeness only.
- **Detail Allocation:** Spare rows in bounded TTY are allocated in deterministic round-robin passes across visible groups in catalog order (`sessions`, `usage`, `context`, `capacity`, `runtime`), at most one row per group per pass. Each emitted detail row is prefixed with its group label (e.g., `SESSIONS · ...`, `CAPACITY · ...`). Candidate rows are formatted against `content_width = columns - 3`. If a candidate does not fit, its slot in that pass is consumed and the planner proceeds to the next group.

## TDD Acceptance Fixtures and Arithmetic Derivation

Deterministic pure-renderer tests use five selected groups in catalog order (`sessions, usage, context, capacity, runtime`), width 80, no database path, no AGY hints, no refresh warnings, and no overflow line. Input sample: 100 records for session-derived groups and 9 capacity observations.

### Reserved Row Budget
- Title line: 1 row
- Non-capacity groups (4 groups × 2 rows: heading + 1 summary): 8 rows
- Capacity group (1 heading + 2 summaries: aggregate + freshness/urgency): 3 rows
- **Total reserved rows:** `1 + 8 + 3 = 12 rows`

### 80x24 Viewport Derivation
- Usable frame height: `24 - 1 = 23 rows`
- Available detail slots: `23 - 12 = 11 slots`
- Pass 1 (5 slots): `sessions=1, usage=1, context=1, capacity=1, runtime=1` (6 slots remaining)
- Pass 2 (5 slots): `sessions=2, usage=2, context=2, capacity=2, runtime=2` (1 slot remaining)
- Pass 3 (1 slot): `sessions=3` (0 slots remaining)
- **Visible counts:** `(sessions=3, usage=2, context=2, capacity=2, runtime=2)` (total 11)
- **Off-screen counts (`loaded - visible`):** `(sessions=97, usage=98, context=98, capacity=7, runtime=98)`
- **Loaded counts:** `(100, 100, 100, 9, 100)`

### 80x60 Viewport Derivation
- Usable frame height: `60 - 1 = 59 rows`
- Available detail slots: `59 - 12 = 47 slots`
- Full passes: `47 // 5 = 9 passes` (45 slots allocated, 9 per group). Capacity has 9 loaded records, so all 9 are visible (`off-screen = 0`).
- Remainder slots: `47 % 5 = 2 slots`, allocated in catalog order to `sessions` (9 + 1 = 10) and `usage` (9 + 1 = 10).
- **Visible counts:** `(sessions=10, usage=10, context=9, capacity=9, runtime=9)` (total 47)
- **Off-screen counts (`loaded - visible`):** `(sessions=90, usage=90, context=91, capacity=0, runtime=91)`
- **Loaded counts:** `(100, 100, 100, 9, 100)`

### Acceptance Test Cases
1. **Bounded Slot Allocations:** Assert exact 80x24 (11 details) and 80x60 (47 details) visible and off-screen tuples in catalog order derived above.
2. **Width Boundary Fitting:** Test outer widths >=80 (`Urgency: ... (loaded sample)`, 62 cells <= 77), 48–79 (`Urgency: ...` compact, 42 cells <= 45), 32–47 (`Urgency: ... · 1/9`, 27 cells <= 29; long pool clipped identity `Urgency: ag quot... 19% · 1/9` retaining ratio, 29 cells <= 29), and width <32 (single clipped narrow notice). Require plain uppercase `PARTIAL`/`COMPLETE`/`UNKNOWN` without `SRC ` prefixes.
3. **Capacity Two-Line Summary:** Verify aggregate line and freshness/urgency headline (with exhaustive disjoint counts `M+S+E+U+A=total` and width-band fallbacks) precede capacity details. Verify unranked `reset_credit` rows.
4. **Pure Reflow Expiry:** Verify that simulated elapsed time flips fresh observations to `STALE` when `age + elapsed >= TTL` without disk/database I/O, that negative age evaluates to `UNKNOWN`, and reset crossing expires last-known values.
5. **Short-Height Overflow Fitting:** Verify reservation of AGY continuation and warning rows ahead of details, and test overflow line fallback levels: names list at width 80 (51 cells <= 77), count-only at width 48 (39 cells <= 45), and guidance-only at width 32 (24 cells <= 29).
6. **Inspection Rendering:** Verify explicit `--group sessions` renders all loaded detail rows unbounded, with session handles included.
