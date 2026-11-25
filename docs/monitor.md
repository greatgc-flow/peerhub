# Reading the PeerHub monitor

`peerhub monitor` is the human screen. An AI reads `peerhub diag --plan` instead (pools plus the load line, about 350 tokens).

`peerhub diag --plan --json` reports the same difference as `exhaust_margin_seconds`, using the effective reset for a parent-limited pool. The plan text marks early exhaustion with `!early`.

This page is the target of the `Guide` link at the top right of `peerhub monitor` on supported terminals and of the
`Guide:` line in snapshots and terminal frames without a Guide link, including redirected rich output. For quick offline help, use `peerhub explain monitor-view` or `peerhub explain monitor-states`.

Continuous terminal watch checks each selected peer every second and refreshes it in the background when its oldest
required quota reading reaches 210 seconds (90 seconds before expiry) or is missing. Failed or partial peers retry after 60 seconds.
If agy reports model-token use, watch stops unless `--allow-agy-token-use` was passed.
Use `--no-probe` or set `PEERHUB_MONITOR_PROBE=off` to disable automatic refresh. Redirected watch reads committed evidence
by default. Explicit `--probe` also schedules collection on the watch cadence. `--collect-every K` applies to
that cadence and scripted cycles. `--snapshot`, `diag quota`, and `diag --live` read committed evidence. Use `monitor --probe --cycles 1`
to collect and show one completed cycle.
With `--cycles N` and `N > 0`, scripted runs keep synchronous refresh and the same output as before, while continuous TTY watch refreshes in the background.

On Windows, background quota refresh puts each probe in a Job Object before it can start children.
Synchronous `peerhub observation refresh` also uses Job Objects for Claude and Codex account and quota probes (#453).
Cancellation, timeouts and normal completion clean up each job even if its wrapper has already exited.
If Windows refuses containment, the probe still runs and cleanup uses the wrapper's process handle to stop it.
Background refresh includes a warning with the API and WinError in its result.

Continuous watch, automatic collection in scripted watch, suggest, and ask dispatch share the PID lock
`quota-refresh.lock` in the store's scratch directory. After three seconds of contention they continue with committed
evidence. Automatic collection rechecks quota under the lock before probing.
For a command you can run, use `peerhub suggest --tier standard` and see
`peerhub explain suggest` for its quota rules.

## Commands and output

- `monitor groups` lists all eleven group IDs and whether the effective defaults select them. The defaults select nine groups. Use `--group GROUP` (repeatable), `--group all`, or `monitor defaults show|set|reset` to inspect or change the selection.
- `monitor --snapshot` renders one read-only frame. Use `monitor --snapshot --expand --group sessions --group execution` for the retained-session and execution tables. `monitor --no-probe --interval-seconds 60` watches committed evidence. Pass the global `--db PATH` before `monitor` to choose another store.
- Without `--cycles`, a terminal watches until interrupted and redirected output runs one cycle. Explicit `--cycles 0` watches everywhere until interrupted. Positive values bound the watch.
- `--json` emits schema 2.0: one object for a snapshot, or one `initial` frame plus a `complete` frame per watch cycle (NDJSON). Older scripts can select `--json --schema-version 1.1`.
- `--view plain` uses an ASCII frame. `--view rich` uses terminal glyphs and colour when available. See [CLI help](commands.md#guide-and-explain) and the [probe contract](implementation/OBSERVATION_PROBES.md).

## The top lines

```
 OK? · 2 running · 3 leads · min quota 68% cc C-7D ok                                   14:02Z
PeerHub Monitor 2026-11-20 14:02Z · DB:env · quota age 3m                                   Guide
```

**Line 1 (the verdict)** starts with `OK`, `OK?` or `ATTN n`:

- `OK`: no alert, and every source the alerts need was read.
- `OK? (quota, facts not read)`: no alert, but a source was not read, so a quiet line proves less.
- `ATTN n`: n alerts. The first two are named, `+k` counts the rest. The codes are in [ALERTS](#alerts).

After the status come `N running` asks, `N leads` and `min quota 68% cc C-7D ok`: the lowest 7-day pool that has a
number, with the state POOLS shows for it (`(aged)` when its evidence is stale). The right-hand fields drop first on a
narrow terminal; the rich view puts the snapshot time at the right edge when it fits.

**Line 2 (the title)** is `PeerHub Monitor <UTC time> · DB:<source> · quota age <age>`:

- The time is when the store was read (the snapshot), not when the quota was measured.
- Continuous terminal watch adds a rotating heartbeat and the oldest selected quota age, with a `next refresh`
  countdown, the peers currently refreshing and their elapsed time, or the time since failure and the retry countdown.
  Agy token use can still show `STOPPED · AGY TOKEN USE`.
- `DB:<source>` names where the store came from (`env` for `PEERHUB_DB`, `discovered`, `explicit` for `--db`, ...). With
  `--expand` the full path is directly below the title (`DB:env (<path>)`); the compact frame names it under line 2 only
  when it is not the nearest store or the read had errors.
- In snapshots and scripted frames, `quota age` is the age of the oldest quota evidence of a shown pool.
- Fields that do not fit are dropped from the right. If the status needs more than a full line, the compact view adds
  `Fields hidden · --expand`.

Below the title, both layouts show `quota 5h old -> peerhub observation refresh` when the oldest quota reading is more
than an hour old. Ages of a day or more use days. Without quota readings, the line says `quota: no reading -> peerhub observation refresh`.
The per-pool `aged` state keeps its own freshness rule. Human `diag quota` and `diag --live` show the same notice.
After a timeout, both monitor layouts keep a measured value less than 15 minutes old and show one note per affected peer,
such as `cc probe timeout (value 3m old)`.
A quota probe that still times out under high CPU load is labeled `delayed by load`.

## POOLS

`POOLS n` counts pools selected for the current view before row limits are applied. Both layouts hide unavailable
(`n/a`) windows by default, including the legacy Antigravity weekly bucket when the provider omits it from a
complete report. Add `--show-quota-notes` to count and name them inline and show reasons in compact POOLS or
expanded QUOTA. This display option applies to snapshots and watches. See [Antigravity quota pools](adapters/ag.md#quota-pools).
Readings past their reset or older than their window are also shown as `n/a` and excluded from the header age.

The compact view applies `POOLS_CAP` and omits nonbinding short windows unless they have a scoped parent.
The expanded view includes all short windows, with visible rows limited by the frame height.

| Column | Meaning |
|---|---|
| POOL | peer and pool, for example `cc C-7D`. A child pool is indented under its parent with `└─` / `├─` (ASCII `` `- `` / `\|-`). |
| STATE | a glyph and the state word, see below. |
| bar | remaining quota and its even-burn tick. See [Bar glyphs](#bar-glyphs). |
| LEFT | remaining quota rounded to the nearest whole percent. Positive values that would round to zero show `<1%`. An actual zero in the sampled window shows `0%`. Missing values and readings from a window that has reset show `—` (ASCII `?`). A low reserve has a trailing `!`. Stale evidence has a `~Xm` badge. |
| PACE(pp) | percentage points ahead of (`+`) or behind (`-`) even burn. A trailing `!` marks the rest band. |
| MARGIN | time from reset to forecast exhaustion. A negative duration (yellow with colour) means exhaustion is expected before reset, a positive duration means the pool is expected to last past reset, and `0` means the times coincide. Shows `—` (`?` in ASCII) when either time is unavailable. |
| EXH/RESET | forecast time until exhaustion and time until reset. Different times that display the same way gain one unit of precision, for example `3d13h05m/3d13h40m!`. A `!` means the forecast is earlier than reset. When the displayed times still tie, the margin follows it (for example `!30s`). Either side can show `—` when unavailable (`?` in ASCII), as can a weekly pace gap. `OUT` marks a fresh zero and `past est.` marks a forecast that has already passed while measured quota remains positive. |
| FAMILY | the family label and configured profiles using the pool, including child pools. For example, `flash: ag.effort,ag.neon +1` shows two profiles and counts one more. A child pool also shows its dependency, for example `limited by C-7D (own 26%)`. |

The gap line compares weekly parent pools with readings against `X-7D` in percentage points. It shows `—` when a
comparison cannot be calculated, for example when the baseline reading is missing or a reading is stale.
`--show-quota-notes` also includes pools without readings in the gap line. The gap line leaves the frame with POOLS.
All pools use the even-burn reference
of 0 pp. The design rejected per-pool targets in `[pool_targets]` and `[pools.<ID>]` (spend, rest and priority settings).
The legend under the gap line reads:

```text
PACE(pp)=EVN-LEFT, 0=even burn (|), <=-10 spend, >+20 rest! | EXH/RESET ! = early | MARGIN=EXH-RESET, - = early | EVN=even-burn remaining
```

<a id="state-words"></a>

**State words.** The word is always shown; colour and glyph never tell a state alone.

| State | Glyph | Meaning |
|---|---|---|
| `ok` | `●` | enough left. <a id="state-ok"></a> |
| `low` | `▼` | more than 0% and at most 10% left. <a id="state-low"></a> |
| `fast` | `▲` | more than 10% left, but the projection says `threat` (it would run out before the reset) with at least two samples. <a id="state-fast"></a> |
| `out` | `×` | fresh evidence shows 0% left. <a id="state-out"></a> |
| `blocked` | `×` | a child pool whose parent is out; LEFT shows the parent's 0%, its own value is in FAMILY. <a id="state-blocked"></a> |
| `aged` | `○` | the evidence has reached its time to live, or is marked stale if it has no TTL. `low` and `fast` win over `aged`. <a id="state-aged"></a> |
| `reset` | `○` | the reset time has passed, so the number is no longer true; refresh. <a id="state-reset"></a> |
| `n/a` | | the plan no longer reports this window (or the provider switched it off). Shown in the human frame with `--show-quota-notes`. <a id="state-na"></a> |
| `?` | `○` | unknown: no number. <a id="state-unknown"></a> |

**Order.** POOLS sorts families by ascending 7D PACE of the parent, then shows unknown PACE and finally `out` pools.
Ties sort by peer and pool name. The most negative PACE shows the most under-used weekly pool. The heading shows
`use first` when there is room. Compact POOLS keeps the first `POOLS_CAP` rows and gives the remaining count as `+N pools`.

**Child pools and the F-7D / C-7D rule.** A 5-hour pool (`C-5H`, `3P-5H`, `G-5H`) is shown under its 7-day pool of the
same family when it has less left than the 7-day pool or is `low` or `out` (`--expand` shows it always). Fable's
scoped weekly pool `F-7D` is a child of the all-models weekly pool `C-7D` only when the configured profiles confirm it:
exactly one `C-7D` and one `F-7D` for the peer, and the profiles that use `F-7D` are a strict subset of the profiles
that use `C-7D`. Equal sets, duplicate names or different accounts never make a parent. Then the child's effective
value is the lower of the two: while `C-7D` has less left, `F-7D` shows its LEFT, PACE and state, the nearer reset, and
its own value in FAMILY. An aged, unknown, reset or missing parent never limits it. The same rule drives POOLS, QUOTA,
PEERS, `suggest:` and `peerhub diag quota`.

### Bar glyphs

| Output | Parent pool (filled / empty) | Child pool (filled / empty) |
|---|---|---|
| Unicode | `█░` | `▬╌` |
| ASCII | `#.` | `=-` |

- The filled part shows remaining quota. A positive reading reserves at least one fill cell before the tick is drawn.
- The `|` tick marks even-burn remaining when that fact is available, including when it covers a filled cell.
- POOLS and QUOTA right-align the LEFT value and left-align STATE, PACE(pp), MARGIN, EXH/RESET, and FAMILY. After the left data
  columns fit, the bar grows from 8 to 16 cells while FAMILY uses its abbreviated form. FAMILY then expands to show
  all profile IDs and inline unavailable-window and parent-limit notes as space permits. Any remaining width grows
  the bar up to 40 cells. The tick scales with the bar, including on parent-limited child rows.
- PACE and the bar use cyan for spend, green for ok and yellow for rest. Fresh zero uses bold red.
  LEFT uses neutral text, a yellow low-reserve `!` at 10% or less, or bold red at fresh zero.
- The bar gives way when its minimum width no longer fits beside the left data and FAMILY labels. Data columns give way in order of AGE,
  FAMILY, EXH/RESET and MARGIN. STATE, LEFT and PACE stay.

`suggest:` (under POOLS) is advisory: `suggest: eco=cc.standard normal=cc.effort|cx.effort ...`. `a|b` is the best
eligible pick and the best eligible alternative from another peer. Like `peerhub suggest`, this line skips profiles
with `suggest = false` in the effective profile configuration. Profiles are eligible by default if they meet the quota checks.

## RECENT

`RECENT n (k running)` lists finished asks by end time. Compact shows up to `RECENT_CAP` rows. Bounded `--expand`
uses `RECENT_EXPAND` before fitting the terminal. Unbounded `--expand` shows up to `TEXT_NEWEST_ROWS` finished asks
plus the superseded asks in the read model's `SUPERSEDED_CAP` sample. A running ask stays in RUNNING.

```
 ● ok 3m cc.effort lead7 s-ab12 R u3 62k/6% c91% "reply"
```

| Field | Meaning |
|---|---|
| state | `ok`, `unsure`, `empty`, `error`, ... The dot is green for `ok`, yellow for `unsure` and `overdue`, red otherwise. |
| age | time since the ask ended; `?` when the end is not exact. |
| profile | the profile that was asked (the peer when there is none), `cc.effort`. |
| lead | the caller's label (cut to 16 cells). |
| stream | the stream's name, `s-ab12`. |
| `R` / `F` | the ask resumed an existing session (`R`) or started a fresh one (`F`). |
| `u<n>` | how many times the stream's session has been used. |
| ctx | context at the end of the ask, `62k/6%` (tokens and share of the model's maximum input). For `cx` and `ag`, the `CONTEXT_ESTIMATE_LABEL` (`est`) marks a fallback when the SESSIONS sample provides one. |
| `c91%` | the cache-read share of the ask's input. |
| reply | the reply's first words, in quotes. |

The newest ask per stream and peer can show the live session's generation, mode, use count and context. A narrow
compact row drops the cache share first, then the lead, mode and uses, and context. The stream name and reply get
the remaining space.

## RUNNING

`RUNNING n | cc 2 cx 1` lists asks that have not ended (five rows in the compact frame; the head counts all of them). A
row is the elapsed time, profile, lead, the ask's first words and the stream. A flag comes first: `►` (`>`) running
normally, `!overdue` (the lease ran out), `!gone` / `×` (the caller process is gone) and `?` (liveness unknown).
`--expand` lists every field of every running ask.

Continuous terminal watch reads activity every `min(5 s, --interval-seconds)` using `RUNNING_REFRESH_SECONDS`.
Full reads and quota probes keep their existing schedules. Scripted cycles still use `--interval-seconds`.

## ALERTS

Alerts reach line 1 as `ATTN n` and are listed in the `--expand` frame under `ALERTS n`, most severe first. The window
for an ended or overdue ask is 30 minutes.

| Code | Meaning |
|---|---|
| `a1` | an ask ended badly (`empty`, `error` or `unsure`) in the last 30 minutes. Another lineage's ask is never an alert. |
| `a2` | an ask is `gone` (its caller process is gone) or `overdue` (its lease expired, within 30 minutes). |
| `a3` | `pool_out`: a pool has 0% left. |
| `a4` | not used by this version (the number is kept free). |
| `a5` | an account: `login` (logged out), `token_expired`, or (low severity) `token_expiring` within 48 hours. The expiry is the login token's, not billing. |
| `a6` | `store`: a group could not be read; or (low severity) `facts`: the model facts need a recheck. Its reasons are `<peer>: version_drift` (the installed CLI's version differs from the one the facts were checked against, for example `cc: version_drift`) and `ag: unknown_pool` (agy reported a pool the facts do not know). The footer adds `DRIFT:cc,ag`. |
| `a7` | the live monitor stopped before an `ag` quota probe that would use a token; `--allow-agy-token-use` continues. |

After the alerts, `--expand` lists every source that was not read completely (`source quota: stale`, then a `hint:`).
`OK?` / `ATTN n?` on line 1 means such a source exists.

A peer-level probe failure clears when a newer measured pool row from the same peer and probe source arrives.

## LEADS

A lead is the session that launched asks: a Claude Code session read from the local session files of the registered
folders (`CLAUDE_CONFIG_DIR` or `~/.claude`, plus `PEERHUB_LEAD_DIRS`), joined with the ask history. `LEADS n entries |
k sessions found` counts entries before the display cap (three rows in the compact frame). Columns: label (16 cells),
state (`busy`, `?busy` = alive not confirmed, `?` unknown, `-` no session), age of the last activity, `ctx`, running
asks, model, title. `ctx 11%` is a share of the model's maximum input, `ctx 106k` tokens with an unknown window, `ctx ?`
unknown; `!` after the percentage means 80% or more (a mark, not an alert). The rich view draws a five-cell meter
(`■■□□□`, ASCII `##...`). `PEERHUB_MONITOR_TEXT=off` hides names, titles and prompts. Historical lead-session measurements used Claude Code 2.1.323 and 2.1.325 on Windows 11. See the [packaged CLI version observations](../peerhub/config_data/model-defaults.toml).

## The detail sections (`--expand`)

| Section | What it shows |
|---|---|
| QUOTA | the same STATE, bar, LEFT, PACE(pp), MARGIN, EXH/RESET and FAMILY as POOLS, plus AGE when it fits. The bar uses the space left after the data columns. Reset credits and evidence notes follow. |
| PEERS | per peer: its quota, profile, session reuse, context and the account plan (display text, not billing). Up to eight peers. |
| EXECUTION | bridge delivery certainty (not started, may have started, started, terminal), session reuse and delivery latency. It measures delivery, not task success. |
| SESSIONS | retained sessions: lifecycle, fresh or reused, profile, model, effort. |
| USAGE | provider-reported token usage and how much of it is reported. |
| CONTEXT | context occupancy; an estimate for `cc` asks says `est` and its age, and is not a live measurement. |

The sampled sections (EXECUTION, SESSIONS, USAGE, CONTEXT) say `sample N/M` when the read was capped. The last line
is `Coverage: complete|partial; peerhub explain <code>`.

### Accounts

`--expand` and JSON `capacity.accounts` report the plan, login state and login-token expiry. Logged-out, expired and within-48-hour tokens raise a line-1 alert. The observation is aged after six hours. `?` means unknown and `n/a` means unavailable for that login type. Agy reports access to `/credits` with plan `n/a`. An expiry omitted by the provider leaves login state separate. The refresh stores no account names, IDs or tokens.

A fresh account plan can fill in a quota note via `display_plan` and `plan_source: "account"` without replacing the stored reason. A newer ABSENT quota observation for the same login can report a changed plan via `plan_changed: true` and `plan_source: "quota"`. Account re-probes wait until the reading is at least five minutes old.

## Compact and expand

- **Compact** (the default) uses `COMPACT_MAX_LINES` and `COMPACT_MAX_BYTES`. The header is followed by POOLS,
  the suggestion, ALERTS, RUNNING, RECENT and LEADS. POOLS and RUNNING use `POOLS_CAP` and `RUNNING_CAP`.
- **`--expand`** adds the full DB path under the title and lists every pool. After LEADS it shows QUOTA, PEERS,
  EXECUTION, SESSIONS, USAGE, CONTEXT and any other selected group. When space allows, a clipped frame reports
  `+N rows hidden` and gives the command to print the full frame.
- `--view plain` (the default for pipes) is ASCII and never coloured; `--view rich` is the default on a terminal.
  `--snapshot` draws one frame and exits.

## Narrow and small terminals

ALERTS and RUNNING take priority over RECENT when space is limited.

- **Width.** POOLS and QUOTA use the same width budget for their shared columns. The bar gives way before
  FAMILY, EXH/RESET and MARGIN. AGE is optional in QUOTA. Line 2 drops status fields from the right and reserves room
  for the `Guide` link when enabled.
- **Height.** Short frames shrink and drop sections from the bottom up in both layouts. Expanded RECENT switches to
  single-line rows before giving up rows. POOLS keeps its heading, a row and the weekly gap together when space allows.
  The title and verdict have the highest priority.
- **Never cut.** A row that does not fit is shortened with `…` (ASCII `~`) or loses whole fields; a link is never cut.

## The alternate screen and LIVE_ROW_MARGIN

A continuous VT watch uses the full alternate screen and updates the clock and countdowns at most once a second.
The monitor rewrites changed rows at fixed screen positions with auto-wrap disabled. It repaints the full frame
when the row count changes and every 60 frames. On exit it restores the normal screen and prints the final frame there.

Finite watches use the normal screen. Bounded normal-screen frames reserve `LIVE_ROW_MARGIN` rows.
A terminal without VT (or `TERM=dumb`) appends each frame. A pipe receives one completed frame per cycle.

## The Guide link

In the rich view on a terminal with OSC 8 enabled, `Guide` stays at the right end of line 2 during watch and in
snapshots, in both layouts. The status fields on the left yield room to the link. It opens this page for the release
you run. Snapshots and terminal frames without a Guide link end with a copyable `Guide: <url>` line when it fits.
That URL uses `main` for development builds.

The rich view also links the database path and profile names to their files, and recorded query text to its local file.
The quota state in the verdict and the STATE words in POOLS and QUOTA link to the state descriptions for the package version. On supported terminals, the source shown by
`peerhub monitor defaults show` links to its config file. Local destinations must exist. These details keep the same
visible text when links are unavailable, and commands remain copyable text.

## Environment

| Variable | Effect |
|---|---|
| `PEERHUB_MONITOR_VIEW` | `auto`, `rich` or `plain`: the default of `--view` (an explicit `--view` wins). `plain` is the fix for a terminal that draws the glyphs two cells wide. |
| `PEERHUB_NO_LINKS` | `1` (any value except empty, `0`, `false`, `no`, `off`) disables OSC 8 and uses the plain Guide trailer when it fits. `NO_COLOR` also disables OSC 8 links. |
| `PEERHUB_GUIDE_URL` | Tests and mirrors can point the Guide to an HTTPS URL. The plain trailer also accepts HTTP. An override with controls or spaces is ignored. |
| `NO_COLOR` | no colour; the rich layout and its glyphs stay. |
| `PEERHUB_MONITOR_TEXT` | `off` hides the session name, the title and the conversation lines. |
| `PEERHUB_LEAD_DIRS` | extra folders that LEADS reads. |
| `PEERHUB_DB` | the store to read (`DB:env`). |
