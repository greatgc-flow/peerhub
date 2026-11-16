# PeerHub

A durable communication layer for collaborating AI peers. The Core owns only Peer, Stream, Record and Offset. Runtime delivery, observations, diagnostics, work continuity and collaboration capabilities are extensions.

## Install

Requires Python 3.11–3.15. Choose one:

```powershell
pipx install peerhub
uv tool install peerhub
pip install peerhub
```

A winget package is not provided.

## Develop from this checkout

Requires Python 3.11–3.15.

```powershell
python -m pip install -e ".[dev]"
peerhub --help
peerhub --version
```

See [STATUS](docs/STATUS.md) for current source and release status. The public command is `peerhub`; the temporary milestone-specific entrypoint has been retired. `python -m peerhub` and `python -m peerhub.cli` use the same public CLI.

## Core recipe

The Core stores Peers, Streams, immutable Records, and per-peer Offsets.
These commands use a new `core-demo` Stream in the selected store and call no provider:

```powershell
peerhub --db .peerhub/core.db peer register --peer reader --name "Demo reader"
peerhub stream create --stream core-demo --members reader
peerhub record append --stream core-demo --author-peer reader --kind message --body '"Hello"' --idempotency-key core-demo-001 --created-at 2026-10-10T00:00:00Z
peerhub record read --stream core-demo --after 0 --limit 10
peerhub offset get --peer reader --stream core-demo
peerhub offset advance --peer reader --stream core-demo --position 1 --revision 1
peerhub offset get --peer reader --stream core-demo
```

On an existing Stream, advance to the position you actually read and pass the
revision returned by `offset get`; the revision is a compare-and-swap guard.
Keep the same body, timestamp, and idempotency key when retrying an append.

## Quick reference & guide

Run `peerhub guide` (or bare `peerhub` with no arguments) for a concise, offline reference covering profile tables (by tier), two-asks session reuse, delivery safety, discovery commands, and drift recovery. Run `peerhub explain <CODE>` for detailed recovery guidance on any ask outcome code:

```powershell
peerhub guide
peerhub explain ask_blocked_uncertain
```

CLI output defaults to compact human text, including when redirected. Use `--json` for scripts; `--format` is a hidden compatibility alias. Errors carry an outcome code and `next:` hint.

## Everyday commands

[Account-bound peers](docs/accounts.md) isolate cc/cx vendor roots behind immutable peer identities; `peerhub account list` inspects bindings.

[Batch runs](docs/batch.md): `peerhub batch run tasks.json --batch-id review-001 --max-parallel 2 --dry-run` validates a flat batch before execution.

```powershell
# Persist the prompt, execute one provider, and persist its response.
peerhub ask cx "Summarize this repository" --stream review --request-id review-001

# Explicit collection may contact provider CLIs.
peerhub observation refresh --peers cx cc ag
peerhub observation refresh --peers cc --system-dir D:\tools\portable  # explicit provider installation directory

# Read committed observations without probing or writing.
peerhub diag
peerhub diag quota --json
peerhub diag health
peerhub diag --live --interval-seconds 2
peerhub diag --view rich

# List every monitor group and see what each ID means; --json emits the same catalog.
peerhub monitor groups
peerhub monitor groups --json

# Show a read-only snapshot once, without provider refresh or writes.
peerhub monitor --snapshot
peerhub monitor --snapshot --group sessions --group execution

# Watch the nine default groups: overview, activity, peers, execution, sessions,
# usage, context, capacity, and coverage. Collection may call provider CLIs
# (including agy /usage and /credits) and consume provider quota.
peerhub monitor --interval-seconds 60
peerhub monitor --view plain --cycles 1

# For an AI session: line 1 (the verdict), the suggestion and the pools only; no text of other sessions.
peerhub monitor --snapshot --group capacity

# Keep the schema 1.1 quota/dashboard output for existing scripts.
peerhub monitor --json --schema-version 1.1 --cycles 1

# Customize grouped watch and snapshot defaults (workspace by default).
peerhub monitor defaults set --group overview activity peers execution sessions usage context capacity coverage
peerhub monitor defaults show
peerhub monitor defaults reset   # saved before 0.24.16? reset to get the new `activity` group

# Review with a second provider using a declared profile.
peerhub ask cc "Explain the Core Peer, Stream, Record and Offset flow" --stream learning --request-id learning-001 --profile cc.standard --json
```

The two `ask` examples require the corresponding vendor CLI to be installed and
authenticated, and spend provider quota. See the [adapter guide](docs/adapters/README.md).

Builtin provider names are `cx/codex`, `cc/claude` and `ag/agy`, and the API peers `ca`, `oa` and `ga` (see [API peers](#api-peers-ca-oa-and-ga)). Custom Peer identities can be registered with `peer register --peer worker --adapter cx`.

`ask` supports explicit `--profile`, `--model`, `--effort`, `--writable`, `--timeout-seconds`, optional `--silence-timeout-seconds`, `--max-output-bytes`, `--author-peer` and `--json`. Profile policy resolves workspace → global → packaged defaults; it does not assert measured model availability. Each `ask` stream is scoped to a single peer: targeting a different peer on an existing ask stream is rejected with `stream_peer_mismatch` (`ask_stream_peer_mismatch`). Retrying the same `--request-id` reuses a completed response. A different prompt, binding or session selector conflicts, and an uncertain execution requires explicit reconciliation before another execution. By default, each ask reuses the latest compatible retained session of this peer/stream; otherwise it starts a fresh session with bounded Stream catch-up. Mutually exclusive `--fresh` and `--session HANDLE` select a new generation or one exact retained session. Exact selection rejects incompatibility or context overflow without allocating a substitute. Fresh still receives Stream history; use a dedicated stream for an independent task. CLI peers (cc, cx, ag) retain native vendor continuity. Older native mappings with unproven context are invalidated on upgrade; existing request replay and uncertain-execution blocking remain intact. `--json` always includes `session` (accepted handle, generation, mode, reason and telemetry, or null before allocation) and `error` (null on success). Text output ends with one `session:` line after allocation, including on failure or replay. Existing delivery details remain available. In the ask result, context occupancy is unknown: billing usage is not an occupancy measurement (the estimate for cc asks is described below).

`--writable` is off by default. It maps to cc `--permission-mode acceptEdits`, cx `-s workspace-write` (default `-s read-only`), and ag `--mode accept-edits`. Because the `agy` CLI does not enforce a read-only sandbox when `--mode accept-edits` is omitted, native `ag` execution fails closed pre-spawn unless `--writable` is explicitly passed (`#140`). cx resume passes `-c sandbox_mode="workspace-write"` (default `read-only`) because resume does not accept `-s`; writable mode is therefore part of the session binding, and changing it starts a fresh generation.

Quota is collected separately from diagnostics. `diag --fresh` has been replaced by `observation refresh`. Unmeasured values remain `UNKNOWN` or another explicit evidence state, and expired measurements become `STALE`.
`--workspace` is an `ask` option; for `monitor` and `observation refresh` use the global `--db` before the subcommand, e.g. `peerhub --db <workspace>/.peerhub/core.db monitor`. The default human monitor view is compact (with `PACE`); to inspect expanded per-peer quota/profile/session chains, bars with the even-burn mark, `EXHAUST-IN`, `sessions` rows or the estimated context, pass `--expand` (e.g. `peerhub monitor --snapshot --expand --group peers`; `diag` sections are `peers`, `streams`, `resource_pools`, `observations`, `activity`, `log`).

`diag --live` repeatedly reads committed snapshots only and keeps its `--cycles 0` default (until interrupted, including non-TTY stdout). `--cycles N` bounds the number of frames; `--json` emits one JSON object per line in live mode. Watching never refreshes quota or changes stored data.

`monitor groups` lists all eleven stable group IDs, their meaning, and whether each
is selected by the effective defaults. The default monitor includes running and
recent asks (`activity`), quota capacity (`capacity`), retained session state and
configured binding (`sessions`), per-peer dependency chains (`peers`), and Bridge
delivery certainty (`execution`).
Delivery certainty describes whether a request was not started, may have
started, started, or reached a terminal delivery state; `terminal` does not
mean the model answered the task successfully. PeerHub does not currently
classify semantic task success/error. Context occupancy of asks: for cc it is estimated at the end of each ask, from the tokens of the last main-chain assistant message and the context window that the result reports for that model; it is an estimate with its time (`state: "ESTIMATED"`, `observed_at`; `latest_telemetry.context` in `peerhub session show --json`), and it stays unknown when either value is missing or does not fit. `--expand` shows it as `2% est, 3m ago` (see below). For cx it is not implemented (its rollout file has it). For ag it is not measured. `runtime` and `store` remain available with
`--group` or `--snapshot --group`.

The default human monitor is a compact frame of at most 24 lines, 99 columns (100 minus the autowrap column) and 8,192 bytes (colour codes included), in this order: line 1, line 2, `POOLS`, the `suggest:` line, `RUNNING`, `LEADS` and `RECENT`. The quota comes first since 0.24.18 (0.24.17 printed RUNNING, LEADS, RECENT, the suggestion, then POOLS); what is cut first on a small terminal is unchanged. Line 1 is the verdict: `OK | 3 running | 2 leads | min quota 1% ag 3P-7D low` when every alert source was read, `OK? (asks not read)`, `OK? (quota stale)` or `OK? (quota unknown)` when one was not (with `peerhub monitor defaults reset` when saved defaults lack the `activity` group; `min quota ... low (aged)` marks a stale pool; `quota unknown` means that no 7-day pool the provider reports has a number, all being unknown or in error; sources combine, for example `OK? (asks not read; quota unknown)`), or `ATTN 3 | gone: cc.effort rank-374-r1/cc 41s | empty: ag.standard denied-postinstall 3m | +1` (or `ATTN 3?` when an alert source was not read; `--expand` lists which), which names at most two alerts in importance order (a store or read failure, a running ask whose `peerhub ask` process is gone, an overdue ask, an ask that ended empty, in error or unsure in the last 30 minutes, a login or expired token, a pool that is out, the agy token-use stop, then a token expiring within 48 hours and model facts that need a recheck). Line 2 is the header (time of the snapshot, `DB:<source>`, quota age, live refresh state). `POOLS n` (STATE, LEFT, RESET, `PACE`, family) is ordered out, low, binding, fast, then the rest; a 5-hour pool shows under its 7-day pool only when it has less left or is low or out. Then the `suggest:` line. `RUNNING n` (with the count per peer) lists at most five running asks: a flag (`!gone`, `!overdue`, `?` for unknown liveness), elapsed time, profile, the label of the lead that launched it, the first line of the request and the name. `RECENT n` lists the two most important ended asks: state, age, profile, lead, the last line of the reply (or the note) and the name. When the terminal is small, lines go in one fixed order (recent rows first, line 1 last): although POOLS is printed first, its rows still go after the recent, lead and running rows. Each header keeps its total. On a live terminal, `min quota` on line 1 and the POOLS rows always show the same moment: every redraw (also the `REFRESHING` and `STOPPED` frames) takes both from the same rows, so when a pool passes its freshness limit or its reset during the countdown, both change together. `--expand` takes `min quota` on its line 1 from the pools as they are at each redraw too. A store that never had a quota refresh is not an error: the frame shows `POOLS 0` with `no quota evidence; next: peerhub observation refresh`, line 1 says `OK? (quota, accounts, facts not read)`, and the monitor exits 0 (also with `--json`). `peerhub monitor --group capacity` prints lines 1-2, the suggestion and POOLS, without the text of other sessions; AI sessions should use it. Request and reply lines come from the local store, cleaned of secrets known to the environment; `PEERHUB_MONITOR_TEXT=off` hides them. The label of a lead comes from `PEERHUB_LEAD`, else the name of the working folder. `LEADS` is described below. `low` means more than 0% and at most 10% remaining (0% is `out`); `fast` means ahead of pace with a threat projection supported by at least two samples and more than 10% remaining. `aged` numbers are last-known evidence, not current capacity; refresh before relying on them. Passed reset windows show no capacity number. The `suggest:` line picks an eligible profile per tier; `|<profile>` adds the best eligible alternative from a different peer in that tier (see [balancing guidance](docs/batch.md)). The header always shows the date and `DB:<source>`; the full DB path appears only when it differs from the nearest upward store or on error. `--expand` prints everything: line 1, ALERTS (every alert with its age), the activity section (RUNNING and RECENT with every field, the 20 newest ended asks plus superseded ones, and LEADS), then the detailed grouped sections as before (pools with bars, 5-hour rows and `EXHAUST-IN` forecasts, PEERS, ACCOUNTS, execution, sessions, usage, context), the legend (`PACE` is used% minus elapsed% in percentage points, `+` ahead of even burn; printed when the output is not a terminal, for example `peerhub monitor --snapshot --expand | more`) and the coverage footer; the header time is the snapshot time. `peerhub explain <code>` covers its short codes.

The compact frame has two views with the same lines and rows. The plain view (`--view plain`, and the default when the output is not a VT terminal, for example a pipe or an AI session) is the text of 0.24.17 in the new order: ASCII frame lines, no colour. The rich view (`--view rich`, the default on a VT terminal) draws the same frame in a new look:

- Line 1 starts with a status pill: ` OK ` on green, ` OK? ` on yellow, ` ATTN 3 ` on red. The facts are separated by a dim ` · `, and the time (`23:46Z`) stands at the right edge when there is room. Line 2 is dim.
- A section head is a rule on its own line, never an extra line, and keeps its content: `── POOLS 5 ─ STATE ─── LEFT  RESET  PACE  FAMILY ───`, `── RUNNING 2 · ag 1 cx 1 ───`, `── LEADS 1 · partial (...) ───`, `── RECENT 2 ───`. The line is dim and the title bold.
- A POOLS row has the pool, a glyph beside the state word (`●` ok, `▲` fast, `▼` low, `×` out, `○` aged, reset, n/a or `?`), a bar of 20 cells (`█` left, `░` used; `─` when there is no number), LEFT, RESET, PACE and the family (dim). The state, the bar and the PACE arrow share one colour: green above 50 % left, yellow above 25 %, orange above 10 %, red at 10 % or less and for a pool that is out or threatened, dim for an aged, unknown, reset or n/a pool (never green). On a narrow terminal the bar shrinks to 4 cells, then goes, before any column drops.
- A RUNNING row starts with `►` (green), `× gone` (red), `! overdue` (yellow) or `?` (yellow). A LEADS row shows the context as a meter of five cells: `ctx ■■□□□ 46%`, red with `!` at 80 % or more, and a dim `ctx ───── 106k` or `ctx ───── ?` when the percentage is unknown. A RECENT row starts with a dot and its state: green `● ok`, yellow for unsure, red for empty or error.
- No state is shown by colour or glyph alone: the words of the plain view stay. The colours come from the 256-colour palette (semicolon SGR codes only; `COLORTERM` changes nothing). `NO_COLOR` keeps the rich layout with its Unicode glyphs and prints no colour. Output that cannot show Unicode gets the same layout with ASCII glyphs (`--`, `#`, `.`, `*`, `^`, `v`, `x`, `o`, `>`). A terminal that draws these glyphs two cells wide should use `--view plain` (or `PEERHUB_MONITOR_VIEW=plain`).
- `--expand` keeps its content; with colour it gets the same pill and rules, and no new bars or glyphs.
- `peerhub explain monitor-view` sums up the order and the look; `peerhub explain monitor-states` explains the states, PACE and `suggest:`.

An example of the rich view, shown here without colour (the uncoloured frame, as `NO_COLOR` prints it):

```text
OK · 2 running · 1 lead · min quota 18% cx X-7D ok                                           23:46Z
PeerHub Monitor 2026-11-14 23:46Z · DB:env · quota age 10s
── POOLS 5 ─ STATE ────────────────────── LEFT  RESET  PACE  FAMILY ───────────────────────────────
 ag G-7D     ● ok   ██████████████░░░░░░   71%  1d00h   ▼-6  flash
    └─ G-5H  ● ok   ████████░░░░░░░░░░░░   40%  1d00h     -  flash
 cc C-7D     ● ok   █████████████░░░░░░░   63%  1d00h  ▼-20  claude
    └─ C-5H  ● ok   ██████░░░░░░░░░░░░░░   30%  1d00h     -  claude
 cx X-7D     ● ok   ████░░░░░░░░░░░░░░░░   18%  1d00h   ▲+9  codex
suggest: eco=ag.standard normal=cc.effort|cx.effort quality=- top=-
── RUNNING 2 · ag 1 cx 1 ──────────────────────────────────────────────────────────────────────────
 ►  16m  cx.effort    docs-site  Review the importer change and its tests  review-api
 ►   1m  ag.standard  docs-site  Vote on the release plan                  vote-1
── LEADS 1 ────────────────────────────────────────────────────────────────────────────────────────
 docs-site  busy  12s  ctx ■■□□□ 46%  2 run  fable-5-2  Importer docs
── RECENT 2 ───────────────────────────────────────────────────────────────────────────────────────
 ● ok      3m  cc.effort  docs-site  All 42 links resolve.  check-links
 ● unsure   ?  cx.effort  docs-site  (no reply)             shipped-check
```

The plain view of the same frame starts `OK | 2 running | 1 lead | min quota 18% cx X-7D ok`, has `POOLS 5      STATE  LEFT  RESET  PACE  FAMILY` as its head and ` cc C-7D     ok      63%  1d00h  v-20  claude` as a row.

The estimated context of cc asks shows only in `--expand`, never in the compact frame and never as live data. It reads `<percent>% est, <age> ago`, for example `2% est, 3m ago`: `est` means an estimate of the context at the end of that session's last ask, as a share of the model's maximum input, and the age is counted from that ask's end to the time of the frame. It appears as the field `ctx 2% est, 3m ago` on the row of the newest ended ask of each stream and peer, only when that ask produced the estimate: it comes from the session of the same stream and peer with the highest generation, and must have been read at or after the ask started (only when the `sessions` group is selected). An ask that failed before it started, or that ended without a reading, shows none, and neither does an earlier ended ask, a running ask or a superseded row. The estimate also appears as the last field of a SESSIONS row (dropped whole when the line is too narrow), as a CONTEXT row (`cc 2% est, 3m ago ... | 20,113 of 1,000,000 tokens`, never with the word `MEASURED`) and on the PEERS line (`-> context: 2% est, 3m ago`); in these three places it reads `2% est, age ?` when its time is unknown. The legend explains `CTX est`. `--expand` also lists `source capacity: unknown (no 7-day pool has a number)` for `OK? (quota unknown)`, and `source capacity: no quota evidence` for a store that never had a quota refresh.

`LEADS` shows the lead sessions of Claude Code, read from Claude Code's own local files. No model is called and nothing is stored. The section sits between RUNNING and RECENT. A row has, from the left: the label (cut to 16 characters); the state; the age of the last activity; `ctx`; the running asks; the model; the session title. `busy` is the state Claude Code writes. `?busy` means PeerHub could not confirm that the process is alive. `?` alone means the state is unknown. `-` means the lead has a caller row but no session was found. `ctx 11%` is the share of the model's input window that the last answer used. `ctx 106k` shows the tokens when the window of the model is not known, and `ctx ?` when the tokens are not known. A `!` after the percentage (`ctx 82%!`) means 80% or more. It is a mark in the row, not an alert, because Claude Code compacts by itself. A narrow terminal drops the title, then the model, then the running asks; `ctx` stays. The title column is left out when no shown lead has a title. A lead with caller rows keeps the caller's label; a lead without caller rows is labelled by its session name, else its folder name, else `?`. At most three rows show, then `+N leads (--expand)`. The section is left out when no lead has a session or a running ask and the read was complete.

The header has five forms: `LEADS 2`; `LEADS 2 | partial (1 unreadable, 2 not read)` when a file could not be read or a limit cut the list; `LEADS 2 | read failed` when a folder could not be listed; and `LEADS ? | partial (...)` or `LEADS ? | read failed` when the read was not complete and no row can be shown. In a row, an unknown state or context shows `?`, an unknown age or model shows `-`, and a missing title is left out. A lead with a session but no transcript shows `-` for age and model. There is no new alert, and line 1 and its lead count are as before. `--expand` prints every field of every lead: tool and short id, state and since when, age, alive or `alive ?`, process id, entry point, Claude Code version, start time, model, tokens against the maximum input, running asks, asks of the last 24 hours, last ask, session name, working folder, title, the first line of the last prompt and the last line of the last answer. A lead that has no session says `no session found (ended, or its Claude folder is not in PEERHUB_LEAD_DIRS)`. A caller that is not Claude Code says `no session (not a Claude Code lead)`. `--json` carries the lead's identity and ask totals in `groups.activity.data.leads[]`, the session facts in its `session` (an object, or null) and the read result in `groups.activity.data.leads_read` (`state` is `read`, `partial` or `error`, with `unreadable` and `not_read`). The schema stays 2.0. A caller row without a session id (an ask started outside Claude Code) never merges with a session.

PeerHub reads the Claude Code folder of the user (`CLAUDE_CONFIG_DIR`, else `~/.claude`) and the folders named in `PEERHUB_LEAD_DIRS`, separated by the path separator of the system. Nothing else is opened. No path comes from the store or from the content of a file, and a link or junction below a registered folder is not followed. Examples:

```powershell
# Windows (PowerShell): two more Claude folders
$env:PEERHUB_LEAD_DIRS = "D:\work\claude-a;D:\work\claude-b"
```

```bash
# POSIX shell
export PEERHUB_LEAD_DIRS="$HOME/work/claude-a:$HOME/work/claude-b"
```

Limits: 8 folders, the 64 newest session files per folder, 16 KB per session file, 2,000 project folders per folder and the last 256 KB of a transcript. A larger set is cut and the header says `partial`. `PEERHUB_MONITOR_TEXT=off` also hides the session name, the title and the two conversation lines. Strings from the files are cleaned, and the secrets of the environment are removed before they are shown.

What was measured and what was not. Measured on Windows 11 with Claude Code 2.1.323 and 2.1.325: the session files, the transcripts, the model catalog and the creation time of the process. `alive` is confirmed on Windows only: the creation time that the session file records must equal the exact creation time of the process, and the file must carry the name of this machine. On Linux and macOS no identity can be compared yet, so the flag is `?`. A session whose file names another machine, or whose process cannot be inspected or compared, shows `?` for up to 24 hours; a file whose process no longer exists is left out. A session that cannot be checked is shown only when its transcript was written in the last 24 hours. The start time in a session file belongs to the process, not to the session. After `/clear` a row may show the old context until the file changes (the age column shows how old it is). It was not measured whether `/clear` rewrites the file. If it gives a new session id, one terminal shows two rows for a while: the old lead with `no session found` and the new one. An invalid old session file whose process id now belongs to another process shows `partial` until that id is free. Not measured: the entry point of IDE and desktop sessions, the files on Linux and macOS, and a plan whose window is smaller than the maximum input of the model. Claude Code's files are private formats; after a format change the cells show `?`. `ctx` is measured against the model's maximum input, which a plan may cut. A session id is read once per folder, so its unreadable transcript counts once in `unreadable`. When several files have the same session id, a row whose process could not be confirmed is replaced by a later file of that id whose process is confirmed; a confirmed row is never replaced. Codex and agy lead sessions: not measured; discovery of Codex and agy lead sessions is not implemented.

`--expand` also shows the `ACCOUNTS` rows (fields in JSON `capacity.accounts`; a logged-out peer, an expired login token or one expiring within 48 hours also reaches line 1): per peer the plan (display data only), login state and the **login token** expiry, which is not billing. Example: `cc login ok | plan pro | login token expiry 2026-11-26 | auth claude.ai | age 30s [MEASURED]`. `?` means it could not be determined and `n/a` that the provider does not expose it (a missing expiry never means logged out; API-key, Bedrock and Vertex logins show `n/a`; `ag` shows endpoint access only). A failed probe keeps the last good reading shown `aged` with a `probe error` note; account evidence ages after 6 hours. Only normalized fields are stored (never an email, organization, account id, token or any identity-derived hash). They are collected, in parallel under the refresh deadline and skipped while still fresh, by `observation refresh` and monitor collection cycles from `claude auth status`, codex app-server `account/read` and `agy /credits` (no model turn); `--snapshot` stays read-only.

`low`/`fast` take precedence over `aged`; staleness still shows in the line-2 quota age and, with `--expand`, in AGE and the PEERS `(aged)` tag. See `peerhub explain monitor-states` for state meanings and precedence.

The bare `monitor` command shows the compact human dashboard (with `PACE`), redrawing in place on a VT TTY and printing one completed frame per cycle when redirected; use `--expand` for bars with the even-burn mark, `EXHAUST-IN` and detailed `sessions`/group sections. `--view rich` gives the rich look described above (colour only on a terminal); `--expand` has its bars in both views;
`--view plain` selects ASCII-safe output: the frame's own lines and marks are ASCII, while request, reply, lead and alert text keeps every character the output encoding can show (Korean, Kana and CJK stay readable; ambiguous-width characters become `?`; the cut mark is `~`). Add repeatable `--group` options to
watch one or more groups, or `--group all` to include all eleven. `monitor groups`
lists group IDs and meanings; `monitor defaults show|set|reset` inspects and
changes the default selection. `monitor --snapshot` renders one read-only
dashboard without refreshing providers. In a terminal, `--snapshot --expand`
fits the screen; piping shows all rows, for example
`peerhub monitor --snapshot --group all --expand | more`.
For automation, `--json` emits schema
2.0 NDJSON frames for watch and one JSON object for snapshot. The frozen 0.16.1
machine contract remains available as `--json --schema-version 1.1`; the hidden
deprecated `--legacy` alias was removed in 0.20.0 after its 0.19.x sunset.
Use the explicit `--json --schema-version 1.1` options for the frozen machine contract.

The `diag` dashboard shows a threat summary, a stable pool
table with usage, PACE in percentage points, reset countdown, conditional
forecast and freshness, then credits, latest asks, probe health and one
legend/controls line. PACE is measured usage minus elapsed window percentage;
positive means faster than even. Forecasts require 5 percentage points used
and 10% elapsed (20% for windows of at least one day); projected reset use of at
least 105% is a threat. Below the support gates the screen says “not enough
data”. Inferred anchors remain conditional and predictive accuracy is not
established.

Monitor runs collection cycles every N seconds (`--interval-seconds`), collecting
initially and then every Kth cycle (`--collect-every`). With `--cycles` omitted,
stdout on a TTY watches until interrupted; non-TTY stdout runs one cycle.
Explicit `--cycles 0` watches until interrupted everywhere; positive values
bound collection cycles. The legacy dashboard and
live diagnostics update countdowns at most 1 Hz; PACE and forecast ETA remain
anchored to the measurement. Non-TTY human watch prints one completed frame
per cycle without escapes (skipping the initial pre-refresh frame), while unavailable VT on a TTY appends without escapes; schema 2.0 JSON watch emits one `initial` frame plus
one `complete` frame per cycle (default non-TTY JSON = two lines). `NO_COLOR` suppresses
colour while retaining supported cursor controls.

## API peers: ca, oa and ga

`ca` (the Claude API), `oa` (the OpenAI Responses API) and `ga` (the Gemini API) answer one text question per ask. Use them for votes, text reviews and documentation checks when the text fits in the prompt, so that the subscription pools of cc, cx and ag are kept for work with tools and files. They are paid from API credits, not from a subscription pool: they have no POOLS row, `suggest:` never names them, and `diag readiness`, `observation refresh` and the accounts leave them out.

Can PeerHub read the credit balance? No. None of the three APIs gives the balance to the login or key that PeerHub uses. The Claude cost and usage reports need an admin key and report spend, not the balance. The OpenAI cost report needs a key with a usage scope, and the credit grants are readable in the browser only. For a Gemini API key no balance endpoint is known. Read the balance in the console of each provider: the Claude Console, the OpenAI Platform and Google AI Studio.

Setup:

- `ca` runs the `ant` CLI. Put it on PATH and sign in once with `ant auth login`. PeerHub only runs `ant`; it never reads, stores or prints a credential, and it removes every `ANTHROPIC_*` variable from the environment of `ant`, so the call always uses the `ant` login and the official endpoint.
- `oa` reads the key from the environment variable `OPENAI_API_KEY` at the moment of the call. Set it in the environment of the process that runs PeerHub, for example from your secret store. There is no option for the key: never put it on a command line or in a file for PeerHub. The key is sent only in the `Authorization` header and is removed from any error text.
- `ga` reads the key from the environment variable `GEMINI_API_KEY` at the moment of the call, in the same way: there is no option for the key, so never put it on a command line or in a file for PeerHub. The key is sent only in the `x-goog-api-key` header, never in the URL, and is removed from any error text. `GOOGLE_API_KEY` is not read.
- An API key in the environment is visible to every peer process that PeerHub starts, except that an account-bound cc or cx peer gets no `OPENAI_*` variable ([accounts](docs/accounts.md)). With the Codex and agy CLIs logged in with an account, an invalid key in the environment did not change the answer (measured with codex-cli 0.169.0 and agy 1.5.9; this does not tell "ignored" from "tried, then fell back"). Set the keys only where the API peers are used.

```powershell
peerhub ask ca --query-file review.md --profile ca.effort                        # its own stream: peer:ca:<request-id>
peerhub ask oa --query-file review.md --stream vote-oa-001 --profile oa.standard
peerhub ask ga --query-file review.md --stream vote-ga-001 --profile ga.standard
```

Profiles: `ca.standard` (claude-haiku-5-5), `ca.effort` (claude-sonnet-5-5), `ca.deepthink` (claude-opus-5-5), `oa.standard` (gpt-6-luna), `oa.effort` (gpt-6.1-sol), `oa.deepthink` (gpt-6-astra), `ga.standard` (gemini-3.5-flash-lite), `ga.effort` (gemini-3.6-flash); `peerhub explain ca.standard` shows a card. `ga` has no deep profile: use a stronger model with `--model <id>` when the key has quota for it. `--model` must match `^claude-[a-z0-9.-]{1,64}$` for `ca`, `^gpt-[a-z0-9.-]{1,64}$` for `oa` and `^gemini-[a-z0-9.-]{1,64}$` for `ga`. A custom peer uses `--adapter ca`, `--adapter oa` or `--adapter ga`.

What they can and cannot do:

- One ask is one API call, and PeerHub never retries it. There are no tools, no files, no workspace access and no session: inline the text. `-w` is still accepted, but nothing in it is read.
- One ask per stream. An ask on a stream that already has any record (another ask, delivered, failed, uncertain or still running, or a record put there some other way) is refused: `ca is stateless; use a new stream`. Only the same ask may come again: the same request-id and the same author (`--author-peer`) replay the stored result; another author with that request-id is refused the same way. Without `--stream`, every ask gets its own stream, `peer:<peer>:<request-id>` (PeerHub generates the request id when none is given), and the text output prints it before the `session:` line, for example `stream: peer:ca:<request-id>`; `--json` has it in `stream_id`. Other peers keep the default `peer:<peer>:chat`.
- Refused before anything is stored or sent, with `request_rejected` and exit status 2: no `--profile` and no `--model`, a model that does not match, `--effort`, and `--writable`. See `peerhub explain request_rejected`.
- The `@` rule: `ant` replaces a string that starts with `@` by the content of the file it names. The text that PeerHub sends to `ant` always starts with the fixed line `PeerHub ask (text only, no tools, no files).`, so a prompt that starts with `@<path>` is sent as that literal text, and no file is read.
- Limits: replies up to 16,000 output tokens for `ca` and `oa` and up to 32,000 for `ga`; prompts up to 1,000,000 bytes; `peerhub guide` advises at most 3 asks at once per API peer (nothing enforces it). The limit of `ga` is higher because a Gemini model counts its thinking against the output limit: in a real call with a limit of 20 tokens the thinking used them all, and the reply came back cut and without text.
- `oa` always calls `https://api.openai.com/v1/responses`, and `ga` always calls `https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent`. `OPENAI_BASE_URL` and the proxy variables are ignored, redirects are not followed and TLS is always verified. A network that needs a proxy is not supported. For `oa` and `ga`, `--timeout-seconds` is a total over the whole call (connecting, the headers and the body): when it passes, the ask ends `uncertain`. `--silence-timeout-seconds`, when given, limits one network operation.

Outcomes:

- `delivered`: a complete reply, with `input_tokens` and `output_tokens` in `usage`. An empty reply is `delivered` with `empty_response: true`. For `ga`, `output_tokens` is the reply plus the thinking, the thinking is never part of the reply text, and `usage` is left out (never shown as zero) when the answer carries no usable token counts.
- `truncated`: the reply was cut at the output limit (16,000 tokens for `ca` and `oa`, 32,000 for `ga`). `incomplete`: the reply ended for another reason, for example a refusal or, for `ga`, a blocked prompt (the error names it). Both exit with status 1, keep the text in `partial_response` and store no response; see `peerhub explain truncated` and `peerhub explain incomplete`. If the ask was interrupted after that result was stored but before it was acknowledged, repeating the same request returns the saved status with `partial_response`, without a new call.
- `not_executed` with the reason `quota_exhausted` and no reset time: the API credits are used up. For `ga` this is an HTTP 402 or 429 answer with the status `RESOURCE_EXHAUSTED`. With 429 Google uses that one status for a rate limit, for a quota that is spent and for a model that the key has no quota for, so PeerHub cannot tell them apart; with 402 the prepay balance of the key's project is depleted. Read the message, then wait and ask again on a new stream, or check the plan and billing of the key (the prepay balance is managed in Google AI Studio).
- `failed_not_started`: `ant` is not on PATH (`ant executable not found`), `ant` is not signed in, `OPENAI_API_KEY` or `GEMINI_API_KEY` is not set or not usable in a header, or the API answered with an error object that proves that nothing ran. For `ca` and `oa` that is HTTP 400, 401, 403, 404, 413 or 429. For `ga` the HTTP status and the status name in the error must agree: 400 with `INVALID_ARGUMENT` or `FAILED_PRECONDITION`, 401 with `UNAUTHENTICATED`, 403 with `PERMISSION_DENIED`, 404 with `NOT_FOUND` (for example an unknown model), 413 with any name, or one of 400, 401, 403, 404, 413 and 429 without a name. Without a valid login the Claude API answers HTTP 401 `authentication_error`, and the message says `ant is not signed in or its login expired (run: ant auth login): HTTP 401: authentication_error: ...`. Nothing ran; once the cause is fixed (for example after `ant auth login`), the same command with the same request-id may be repeated.
- `uncertain`: anything else (a 5xx answer, a timeout, a broken answer, an `ant` error without an HTTP status and an error object, and for `ga` an HTTP status and a status name that do not agree). A reply of the wrong shape is `uncertain` too, never a reply: for `ca`, `content` must be a list of objects and a text block's `text` a string; for `oa`, `output` must be a list of objects with a `type`, a message's `content` a list of objects and an `output_text` block's `text` a string; and an `oa` answer that is `completed` but carries an `error` object is `uncertain`. For `ga`, the first candidate must be an object with a `finishReason` string, its `content` (when present) an object, `parts` (when present) a list of objects and a part's `text` (when present) a string; an answer without candidates and without a block reason, and an HTTP 200 answer with an `error`, are `uncertain` too. The call may have run; continue on a new stream.

What was measured: `ca.standard` answered a real call on 2026-11-15, and a real prompt whose first line was `@<path>` came back as the literal path, without the file's content. `oa` was checked against a local test server and without a key; a real reply through `oa` has not been recorded yet.

For `ga`, measured on 2026-11-16 with one key, through PeerHub on a temporary store and directly against the API:

- `ga.standard` (gemini-3.5-flash-lite) answered through PeerHub (9 input tokens, 1 output token).
- `ga.effort` (gemini-3.6-flash) answered 4 of 4 calls (1.6 s, 1.6 s, 3.5 s and 17.5 s). A fifth call, through PeerHub at 01:46Z, was delivered (9 input, 112 output tokens). In the same minutes gemini-3.5-flash answered 4 of 4 (1.1 s, 1.1 s, 4.1 s and 9.8 s).
- gemini-3.8-flash answered 1 of 10 tries between 00:29Z and 01:41Z: once in 12.7 s; HTTP 503 `UNAVAILABLE` ("This model is currently experiencing high demand") twice; no answer within 40 to 90 s seven times, three of them through PeerHub, which ended `uncertain`. gemini-3.7-flash answered 503 once. That is why `ga.effort` is gemini-3.6-flash. It says which model answered for that key, not which model is better: no benchmark was run through the API, and every pro-class model answers 429 for that key, so the result may be particular to that key's tier. gemini-3.8-flash is used with `--model gemini-3.8-flash`.
- gemini-4-argon, gemini-4-carbon, gemini-pro-latest and gemini-3.1-pro-preview answered HTTP 429 `RESOURCE_EXHAUSTED` ("You exceeded your current quota, please check your plan and billing details") for that key. PeerHub ends such an ask `not_executed` with `quota_exhausted` and keeps the message. This status does not tell a rate limit from a key without quota for the model.
- On 2026-11-16 04:24Z, after the billing setup of the key's project changed, every model answered HTTP 402 `RESOURCE_EXHAUSTED` ("Your prepayment credits are depleted"). PeerHub ends such an ask `not_executed` with `quota_exhausted` and keeps the message (0.24.19 ended it `uncertain`). The prepay balance is managed in Google AI Studio.
- gemini-2.5-flash and gemini-2.5-pro answered HTTP 404 `NOT_FOUND` ("no longer available to new users").
- Without `GEMINI_API_KEY` the ask ended `failed_not_started`. A model name with `/` was refused before anything was sent. The key's bytes were not in the store.

## Store selection and existing data

The store is `.peerhub/core.db`. Discovery picks the nearest one (current directory, then parents); `--db PATH` takes precedence over `PEERHUB_DB`, then discovery (nearest wins). If discovery misses inside a Git worktree, PeerHub asks Git for the common directory and searches the main checkout and its parents. This fallback requires Git >=2.36. A local store still wins. `peerhub diag health` reports `store_selection` (the path and how it was chosen: `explicit`, `env`, `discovered`, `default`). Developer scratch (live-gate JUnit etc.) goes under `.peerhub/work/`; the top of `.peerhub/` holds the database and its `restore.epoch` metadata.

Use global `--db PATH` before the command, or `PEERHUB_DB`, to select a store explicitly. An explicit missing path is never redirected to another store. `ask --workspace DIR` searches only that directory, then Git's main checkout root on a miss (never its parents), unless a database was explicitly selected. A main root already searched is skipped. Without an explicit selection, a discovery miss exits 5 with `[store_missing]` and a `next:` hint before creating files or contacting providers. To bootstrap a new store, including with `peer register` in a fresh directory, pass `--db .peerhub/core.db` or set `PEERHUB_DB`. Read-only commands do not create a missing store even when explicitly selected.

Stop clients before restoring a store and reopen them afterward. Snapshot and
bundle restores fence old CoreStore instances from reads and writes using
`workspace.generation`; changes to the bundle rollback marker `restore.epoch`
also fence old instances. A stale instance raises `StoreReplacedError`.

Monitor and diagnostics refuse a missing selected database with exit 5 before
any probes. Create it explicitly with `peerhub --db <path> peer register --peer
<id> --name <name>`. Observation refresh also refuses a missing automatically
selected store, including in a non-repository directory; an explicit `--db` or
`PEERHUB_DB` retains its existing collection bootstrap behaviour.

## Common tasks

### Continue across two turns

To continue an active multi-turn conversation with the same vendor session:

```powershell
# Turn 1: Initial prompt on a dedicated stream
peerhub ask cx "Explain the store schema" --stream convo-01 --request-id turn-001

# Turn 2: Continue using the native session
peerhub ask cx "Now show an append example" --stream convo-01 --request-id turn-002
```

The Bridge retains up to 20 non-tombstoned sessions per peer/Stream and expires
sessions after more than 30 days of inactivity. Tombstones remain audit rows;
generations never rewind. Compatible history can be reused after a binding
changes back. Request-id replay returns its original session snapshot even if
the handle later expires. Historical asks lacking complete selector identity metadata are
inspectable as Records but cannot be replayed through the new interface; do not
retry uncertain work. Use `peerhub session list` and `peerhub session show`
for bounded, read-only session inspection.
See the [registry contract](docs/implementation/RETAINED_SESSIONS.md).

To create another generation on the same Stream, then return to the first
session, copy its full handle from the first result:

```powershell
peerhub ask cx "Start another conversation with Stream context" --stream convo-01 --request-id turn-003 --fresh
peerhub ask cx "Continue the first conversation" --stream convo-01 --request-id turn-004 --session <handle-from-turn-001>
```

Exact reuse includes intervening eligible Records, including the other
session's responses. Python uses `ask(..., fresh=True)` or
`ask(..., session="sess_0123456789abcdef")`. Handles belong to one store,
Stream, Peer and workspace lineage; full binding and continuity must match.

### Recover from a blocked stream

When a turn encounters a timeout, silence abort, or disconnect, the stream may enter `blocked_uncertain`. Retrying the same `--request-id` cannot replay an uncertain execution because it may have partially executed remotely. To recover safely, start subsequent turns on a **new** stream:

```powershell
# Inspect the current health and failure state
peerhub diag health --stream convo-01

# Recover: include a short summary of the committed context in the new prompt
peerhub ask cx "Context: We reviewed the Core schema; Peer, Stream, Record and Offset are the four primitives. Next, show an append example." --stream convo-02 --request-id rec-001
```

A fresh stream cannot see the old stream's records. Replace the example context
with a short summary of your committed work and the next safe action. Inspect
the old records with `peerhub record read --stream convo-01 --after 0 --limit 10`
if needed; an uncertain remote action may already have occurred, so do not
assume it failed or replay its request ID.

### Offline read-only checks

`diag` inspects local database state and committed snapshots without contacting provider networks or consuming quota:

```powershell
# Read-only store and stream diagnostics
peerhub diag
peerhub diag health
peerhub diag quota --json

# Lease-active asks (a lease is not proof a process is alive)
peerhub diag running

# Per-pool spend/hold/rest/blocked with target %/h until reset (stored observations only)
peerhub diag --plan
```

### Upgrade

To upgrade PeerHub safely:

1. **Stop `peerhub monitor`**: Stop any active monitor process before upgrading. A running monitor locks `peerhub.exe` on Windows and keeps old code running. Also wait for running `peerhub ask` calls to finish: they keep dependency files such as `pydantic_core` loaded, so an in-place upgrade can fail and leave a half-deleted install. If peerhub crashes with `ModuleNotFoundError` after an upgrade, reinstall it and check the result records before re-sending an ask.
2. **Run upgrade**:
   ```powershell
   pip install -U peerhub
   # or
   pipx upgrade peerhub
   # or
   uv tool upgrade peerhub
   ```
   > [!NOTE]
   > If pip says no matching version right after a release, retry after a few minutes or install the file URL directly (PyPI index lag).
3. **Restart monitor**:
   ```powershell
   peerhub monitor
   ```

### Uninstall

```powershell
pipx uninstall peerhub
# or
uv tool uninstall peerhub
# or
pip uninstall peerhub
```

> **Note**: Uninstalling the package removes executable entrypoints but retains the local database and artifacts in `.peerhub/`. To delete store data, remove the `.peerhub/` directory manually.

## Package structure

```text
peerhub/
  core/          Peer, Stream, Record, Offset; SQLite storage
  extensions/    Bridge, Observation, Diag, Artifact, Work, Search, Routing, …
  cli/           public parser and command handlers
  config_data/   packaged provider model defaults
tests/
  communication/ Core, delivery, observations, migration and package gates
  continuity/    extension host, artifacts, work, skills, backup and eval
  collaboration/ federation, memory, search, routing, orchestration and approval
tools/           maintainer scripts
  a2a_sdk_echo_server.py  a2a_sdk_interop.py      ci_platform.py
  command_inventory.py    lint_changelog.py       live_gate.py
  model_ping.py           quota_report.py         release_evidence.py
  release_notes.py        traceability.py
```

Milestones describe development progress, not runtime ownership. Frozen specifications, historical evidence and existing durable wire/storage identifiers retain their original names. See [the cleanup decisions and AG review](docs/implementation/STRUCTURE_CLEANUP.md).

## Where model facts come from and how to refresh them

Profiles (model, effort, pools, and a short evidence-backed card: use for / avoid for / traits; `peerhub profile show ID`) and observed CLI versions live in `peerhub/config_data/model-defaults.toml` (the single source); `docs/model-profiles/model-profiles.json` is the refresh recipe (sources, tier rules, update targets).
Run the `refresh-model-profiles` skill (or query installed CLIs per manifest instructions) to verify models, update `observed_at`/`observed_versions`, and bump the defaults revision.
Observations are evidence, not policy: profile model and effort definitions require explicit review and approval.

## Doc map

See the [documentation map](docs/index.md) for all documentation.

## Limitations

- On Windows, cx `--writable` cannot write into directories with owner-only ACLs (e.g. pytest tmp dirs, `tempfile.mkdtemp`); use a normally created directory.
- Paths containing spaces or special characters can expose quoting problems in
  external Windows launchers. The recorded path hazards are in the removed review
  `git show 465bfe98de7d6931864da4991e45cc1993e342e6:docs/history/reviews/mece-audit-2026-09-20-cross-repo.md`
  (see the [archival ledger](docs/history/ledger-2026-10-17.md)).
- Node.js based CLIs depend on their runtime and launcher installation; portable
  layouts may resolve differently. See the [CLI observations](docs/compatibility/peer-cli-observations.md)
  and [adapter reference](docs/adapters/peer-cli-reference.md).
- Windows console or pipe encoding can affect non-ASCII output. See the
  [recorded encoding failure](docs/m1_impl/wave7_report.md) and
  [adapter reference](docs/adapters/peer-cli-reference.md#external-limitations).

These are environment warnings; historical observations do not establish a
current failure on every installation.

## Validation

```powershell
python -m pytest -q
pyright
python tools/command_inventory.py --check
python tools/traceability.py --upto 9

# Real provider canaries spend quota: local only, never in CI (refuses when CI is set).
python -m tools.live_gate --yes
```

Real providers are opt-in: set `PEERHUB_LIVE=1` and select `live`, `slow`, `e2e` or `canary` under `tests/communication/live`; live fixtures refuse CI. `-m live` selects exactly the six M1 live-tier tests; slow/e2e release cases use their own markers. The gate partitions those stages. Optional `canary` tests are outside the release catalog and measure usage, native resume recall, writable sentinel creation and cancellation using standard profiles. Use `python -m tools.live_gate --yes --only canary --peer cx` for lower-cost measurements; these results are not release evidence. See the [local live gate guidance](CONTRIBUTING.md#local-live-gate) for call budgets and skip behavior. Soak is separately opt-in with `PEERHUB_SOAK=1`. Local checks do not promote a milestone or authorize a release; candidate-matched CI, live and package evidence still apply.

Historical evidence is retained separately: the 2026-10-04 archived snapshot has [VERIFIED-LOCAL records](docs/m1_impl/matrix_evidence/windows-py3.11-3.13.json), and the recorded CI head has [VERIFIED-CI records](docs/m1_impl/matrix_evidence/ci-run-37189754726.json). Those records preserve their original paths and apply only to the recorded snapshot/head, not this changed worktree.
