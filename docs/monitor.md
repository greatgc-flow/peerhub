# Reading the PeerHub monitor

This page is the target of the `Guide` link at the top right of `peerhub monitor` (rich view, on a terminal) and of the
`Guide:` line that ends `peerhub monitor --view plain --expand`. It describes what the screen shows. The same facts are
in `peerhub explain monitor-view` (layout and look) and `peerhub explain monitor-states` (states, PACE, `suggest:`).

Watch reads committed evidence by default. Pass `--probe` to collect quota
during watch. Probes may spend provider quota. With `--probe`, `--collect-every K`
sets the collection cadence. `--snapshot` reads one frame and rejects `--probe`.
For a command you can run, use `peerhub suggest --tier standard` and see
`peerhub explain suggest` for its quota rules.

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
- With `--probe`, a live monitor puts its refresh state and a countdown before them: `REFRESHING`, `REFRESH OK`, `REFRESH WARN`
  (`· n warnings`), `STOPPED · AGY TOKEN USE`, and `refresh in 42s`. A redraw between two reads shows the same snapshot
  aged by the time since its read.
- `DB:<source>` names where the store came from (`env` for `PEERHUB_DB`, `discovered`, `explicit` for `--db`, ...). With
  `--expand` the full path is one line near the end (`DB:env (<path>)`); the compact frame names it under line 2 only
  when it is not the nearest store or the read had errors.
- `quota age` is the age of the oldest quota evidence of a shown pool. It is the first thing to look at when a number
  looks wrong.
- Fields that do not fit are dropped from the right, and a line `Fields hidden · --expand` says so.
- The `Guide` link is the right end of this line, rich view on a terminal only (see [The Guide link](#the-guide-link)).

## POOLS

`POOLS n` counts the pools the table can show (a pool that the plan no longer reports is never a row and is not
counted). The compact frame shows at most eight rows, `--expand` shows all of them.

| Column | Meaning |
|---|---|
| POOL | peer and pool, for example `cc C-7D`. A child pool is indented under its parent with `└─` / `├─` (ASCII `` `- `` / `\|-`). |
| STATE | a glyph and the state word, see below. |
| bar | what is left, 20 cells (rich view only); see [Bar glyphs](#bar-glyphs). |
| LEFT | remaining quota in percent (`<1%` for a tiny rest; `-` when there is no number). |
| RESET | time until the window resets (`passed` when it has). |
| PACE | used% minus elapsed% of the window, in points. `+` is ahead of an even burn (over-use), `-` behind it. A direction glyph `▲` / `▼` (ASCII `^` / `v`) comes before a non-zero number. |
| FAMILY | the pool's meaning, and for a child pool its dependency note (`limited by C-7D (own 26%)`, `C-7D out`, `needs C-7D`). |

**State words.** The word is always shown; colour and glyph never tell a state alone.

| State | Glyph | Meaning |
|---|---|---|
| `ok` | `●` | enough left. |
| `low` | `▼` | more than 0% and at most 10% left. |
| `fast` | `▲` | more than 10% left, but the projection says `threat` (it would run out before the reset) with at least two samples. |
| `out` | `×` | 0% left. |
| `blocked` | `×` | a child pool whose parent is out; LEFT shows the parent's 0%, its own value is in FAMILY. |
| `aged` | `○` | the evidence is older than its time to live. `low` and `fast` win over `aged`. |
| `reset` | `○` | the reset time has passed, so the number is no longer true; refresh. |
| `n/a` | | the plan no longer reports this window (or the provider switched it off). It is not a POOLS or QUOTA row; QUOTA names it after its peer, `(C-5H n/a)`. |
| `?` | `○` | unknown: no number. |

`threat` is a projection, not a POOLS word: it is the FORECAST of the QUOTA details (`threat`, `on_pace`, `safe`,
`insuff` for insufficient data), and a `threat` with two samples makes the row `fast`.

**Order.** `out`, then `low`, then binding (a 7-day pool whose 5-hour sub-row is shown), then `fast`, then the rest;
within a class by peer, family, window length and name.

**Child pools and the F-7D / C-7D rule.** A 5-hour pool (`C-5H`, `3P-5H`, `G-5H`) is shown under its 7-day pool of the
same family when it has less left than the 7-day pool or is `low` or `out` (`--expand` shows it always). Fable's
scoped weekly pool `F-7D` is a child of the all-models weekly pool `C-7D` only when the configured profiles confirm it:
exactly one `C-7D` and one `F-7D` for the peer, and the profiles that use `F-7D` are a strict subset of the profiles
that use `C-7D`. Equal sets, duplicate names or different accounts never make a parent. Then the child's effective
value is the lower of the two: while `C-7D` has less left, `F-7D` shows its LEFT, PACE and state, the nearer reset, and
its own value in FAMILY. An aged, unknown, reset or missing parent never limits it. The same rule drives POOLS, QUOTA,
PEERS, `suggest:` and `peerhub diag quota`.

### Bar glyphs

| View | Parent pool (filled / empty) | Child pool (filled / empty) |
|---|---|---|
| rich, Unicode | `█░` | `▬╌` |
| rich, ASCII output | `#.` | `=-` |
| plain `--expand` QUOTA `[BAR]` (8 cells) | `#.` (`█░` on a Unicode stream) | `=-` (`▬╌`) |

- The filled part is what is left. A pool with something left always shows at least one filled cell, so `<1%` is not an
  empty bar.
- A child's filled cells have the state colour and its empty cells are dim (SGR `2`), so a child never looks like its
  parent, even without colour (`NO_COLOR` keeps the glyphs). An empty cell is never a space, so 0% still draws.
- A row without a number draws `─` (ASCII `-`) in the bar's width. In ASCII a child at 0% and a row without a number
  both draw dashes; the state word tells them apart.
- Colour of the bar, the state and the PACE arrow: green above 50% left, yellow above 25%, orange above 10%, red at
  10% or less, out or threatened, dim for `aged`, `?` and `reset`.
- The bar is 20 cells; when the terminal is narrower it shrinks to 4 cells, then goes. This happens before a data
  column drops. The plain view's QUOTA bar has 8 cells and a `|` that marks the even-burn level.
- `▬` and `╌` are East Asian Narrow, one cell wide, like `█` and `░`. A terminal that draws such glyphs two cells wide
  should use `--view plain` or `PEERHUB_MONITOR_VIEW=plain`.

`suggest:` (under POOLS) is advisory: `suggest: eco=cc.standard normal=cc.effort|cx.effort ...`. `a|b` is the best
eligible pick and the best eligible alternative from another peer. Like `peerhub suggest`, this line skips profiles
with `suggest = false` in the effective profile configuration. Profiles are eligible by default if they meet the quota checks.

## RECENT

`RECENT n (k running)` lists the finished asks, newest first (five in the compact frame, eight entries of two lines in
`--expand`). A running ask stays in RUNNING.

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
| ctx | context at the end of the ask, `62k/6%` (tokens and share of the model's maximum input). Only `cc` has one; `-` for `cx` and `ag`. |
| `c91%` | the cache-read share of the ask's input. |
| reply | the reply's first words, in quotes. |

The values of the session are those of the stream's newest session now; a stream without one shows `-`, never 0. A
narrow terminal drops the cache share first, then the lead, then the mode and uses, then the context; the stream name
and the reply stay.

## RUNNING

`RUNNING n | cc 2 cx 1` lists asks that have not ended (five rows in the compact frame; the head counts all of them). A
row is the elapsed time, profile, lead, the ask's first words and the stream. A flag comes first: `►` (`>`) running
normally, `!overdue` (the lease ran out), `!gone` / `×` (the caller process is gone) and `?` (liveness unknown).
`--expand` lists every field of every running ask.

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

## LEADS

A lead is the session that launched asks: a Claude Code session read from the local session files of the registered
folders (`CLAUDE_CONFIG_DIR` or `~/.claude`, plus `PEERHUB_LEAD_DIRS`), joined with the ask history. `LEADS n entries |
k sessions found` counts entries before the display cap (three rows in the compact frame). Columns: label (16 cells),
state (`busy`, `?busy` = alive not confirmed, `?` unknown, `-` no session), age of the last activity, `ctx`, running
asks, model, title. `ctx 11%` is a share of the model's maximum input, `ctx 106k` tokens with an unknown window, `ctx ?`
unknown; `!` after the percentage means 80% or more (a mark, not an alert). The rich view draws a five-cell meter
(`■■□□□`, ASCII `##...`). `PEERHUB_MONITOR_TEXT=off` hides names, titles and prompts.

## The detail sections (`--expand`)

| Section | What it shows |
|---|---|
| QUOTA | one row per pool: STATE (as POOLS), FORECAST (`threat`, `on_pace`, `safe`, `insuff`), REM, RESET-IN, PACE, EXHAUST-IN, AGE and the profiles that use the pool. The plain view adds the 8-cell `[BAR]`. `reset credits` rows and the notes for pools that are `n/a` or whose evidence is not usable follow. |
| PEERS | per peer: its quota, profile, session reuse, context and the account plan (display text, not billing). Up to eight peers. |
| EXECUTION | bridge delivery certainty (not started, may have started, started, terminal), session reuse and delivery latency. It measures delivery, not task success. |
| SESSIONS | retained sessions: lifecycle, fresh or reused, profile, model, effort. |
| USAGE | provider-reported token usage and how much of it is reported. |
| CONTEXT | context occupancy; an estimate for `cc` asks says `est` and its age, and is not a live measurement. |

The sampled sections (EXECUTION, SESSIONS, USAGE, CONTEXT) say `sample N/M` when the read was capped. The last line
is `Coverage: complete|partial; peerhub explain <code>`.

## Compact and expand

- **Compact** (the default) is at most 24 lines, 99 columns and 8,192 bytes: line 1, line 2, POOLS (eight rows),
  `suggest:`, RECENT (five rows), RUNNING, LEADS.
- **`--expand`** is the same top, with every pool and the whole suggestion, RECENT as entries of two lines, then RUNNING,
  ALERTS, LEADS, QUOTA, PEERS, EXECUTION, SESSIONS, USAGE, CONTEXT, any other group, and the store path. It uses the
  terminal width less one column. In a terminal it ends with `+N rows hidden · peerhub monitor --snapshot --group all
  --expand | more` when it does not fit.
- `--view plain` (the default for pipes) is ASCII and never coloured; `--view rich` is the default on a terminal.
  `--snapshot` draws one frame and exits.

## Narrow and small terminals

- **Width.** A table drops whole columns from the right, FAMILY first (it shrinks down to its widest family name
  before it goes). The POOLS bar shrinks from 20 cells to 4 and then goes, before any column drops. A state glyph stays
  only while the columns of the plain table stay. Line 2 drops fields from the right and says `Fields hidden`. The
  `Guide` link is the first thing to go, before any data, and it is never shown when it does not fit.
- **Height.** The frame never exceeds the terminal. Rows go first, from the bottom up: LEADS to one row, RUNNING to
  three, RECENT to three, POOLS to four, then RUNNING to one, RECENT to one, POOLS to one, then the lower sections, then
  the suggestion and what hangs on line 2. Line 1, line 2, the POOLS header and RECENT's header with a row go last.
- **Never cut.** A row that does not fit is shortened with `…` (ASCII `~`) or loses whole fields; a link is never cut.

## The alternate screen and LIVE_ROW_MARGIN

A live monitor draws on the terminal's alternate screen (like `less` or `vim`): the first frame switches to it with
`ESC [ ? 1049 h` and leaving the monitor switches back, so the command line and the scroll-back stay as they were and
a pinned command (VS Code's sticky scroll) cannot cover line 1. Every row is written at its own screen position, so a
glyph that a terminal draws wider than one cell cannot push the later rows down and scroll `ATTN` away.

The frame has the terminal height less `LIVE_ROW_MARGIN` (2) rows, and at least 8: terminals report one row more than
they show. A terminal without VT (or `TERM=dumb`) gets the frame appended, without escapes; a pipe gets one completed
frame per cycle.

## The Guide link

The word `Guide` at the right end of line 2 is an OSC 8 hyperlink to this page for the version you run:
`https://github.com/greatgc-flow/peerhub/blob/v<version>/docs/monitor.md`.

- It is drawn in the rich view on a terminal only, in the compact and the `--expand` frame. The plain view and any
  output that is not a terminal have no link bytes.
- A development build (a version with `dev` or a `+local` part) has no tag to point at, so the link is not drawn.
- It is added after the layout: widths and truncation ignore its bytes, it is never cut, and it is dropped before any
  data when line 2 has no room.
- The plain `--expand` frame ends with a plain line `Guide: <url>` (the `main` branch's page for a build without a tag),
  which many terminals and editors (VS Code) turn into a link. It is in the frame printed whole (a pipe, `| more`), when
  it fits the width; a height-bounded frame keeps its rows for data and has no such line.

## Environment

| Variable | Effect |
|---|---|
| `PEERHUB_MONITOR_VIEW` | `auto`, `rich` or `plain`: the default of `--view` (an explicit `--view` wins). `plain` is the fix for a terminal that draws the glyphs two cells wide. |
| `PEERHUB_NO_LINKS` | `1` (any value except empty, `0`, `false`, `no`, `off`) leaves the `Guide` link out. `NO_COLOR` does too (no escape bytes at all). |
| `PEERHUB_GUIDE_URL` | tests and mirrors: an `http(s)` URL used instead of the version's page (a control character or space makes it ignored). |
| `NO_COLOR` | no colour; the rich layout and its glyphs stay. |
| `PEERHUB_MONITOR_TEXT` | `off` hides the session name, the title and the conversation lines. |
| `PEERHUB_LEAD_DIRS` | extra folders that LEADS reads. |
| `PEERHUB_DB` | the store to read (`DB:env`). |
