# Configuration and store selection

This page describes the current public CLI. The legacy `config`, `node
bind-profile`, and `backup` CLI commands were retired; historical designs are
retained in [the design index](design/README.md).

## Store

The store is `.peerhub/core.db`. Discovery picks the nearest one (current directory, then parents); `--db PATH` takes precedence over `PEERHUB_DB`, then discovery (nearest wins). If discovery misses inside a Git worktree, PeerHub asks Git for the common directory and searches the main checkout and its parents. This fallback requires Git >=2.36. A local store still wins. `peerhub diag health` reports `store_selection` (the path and how it was chosen: `explicit`, `env`, `discovered`, `default`).

Put the global `--db PATH` before the subcommand. An explicit missing path is never redirected to another store. `ask --workspace DIR` searches only that directory, then Git's main checkout root on a miss (never its parents), unless a database was explicitly selected. A main root already searched is skipped. Without an explicit selection, a discovery miss exits 5 with `[store_missing]` and a `next:` hint before creating files or contacting providers. To bootstrap a new store, including with `peer register` in a fresh directory, pass `--db .peerhub/core.db` or set `PEERHUB_DB`. Read-only commands do not create a missing store even when explicitly selected.

See [store recovery and upgrades](../README.md#upgrade) before restoring a backup. Stop clients before restoring a store and reopen them afterward. Snapshot and
bundle restores fence old CoreStore instances from reads and writes using
`workspace.generation`; changes to the bundle rollback marker `restore.epoch`
also fence old instances. A stale instance raises `StoreReplacedError`.

`observation refresh` also needs an explicit `--db` or `PEERHUB_DB` to bootstrap a missing store.

Repeat the same `--db PATH` on every command in a recipe to keep `PEERHUB_DB` from redirecting a later step.

## Model profiles

`ask --profile` resolves profile fields from workspace, then global, then
packaged defaults (highest priority first):

| Layer | File |
|---|---|
| Workspace | `<workspace>/.peerhub/config/models.toml` |
| Global | `~/.peerhub/config/models.toml` |
| Packaged | `peerhub/config_data/model-defaults.toml` |

`PEERHUB_CONFIG_HOME` replaces the global config directory. A missing file in
that directory contributes no values; it does not select another global root.
Malformed selected files are errors. Profile entries merge by field, with
`schema_version = 1` and entries under `[profiles."<peer>.<profile>"]`.
An explicit `--model` or `--effort` overrides the resolved profile defaults.
Without `--profile`, the vendor CLI supplies its defaults unless explicitly
overridden. Configuration does not prove model availability.

### Profile cards

Each packaged profile carries a short card for AIs choosing a profile: `use_for` and
`avoid_for` (at most 80 characters each), `traits` (1-3 items, at most 40 characters each),
`evidence` (lesson ids, repo paths or `#issue` numbers) and `card_checked` (`YYYY-MM-DD`).
Setting any card key requires `evidence` and `card_checked`. Cards hold evidence, never pools.
A card is bound to the binding it was checked against: when a higher layer changes
`selection_mode`, `model` or `reasoning_effort` (a different value, not a repeated one), the
inherited card is dropped and only that layer's own card keys apply. `peerhub explain ID` (an exact
profile id is matched before lessons) and `peerhub profile show ID` print the effective card with the
layer of each key, `profile list` shows a truncated `USE FOR`
column, and `--json` carries the full `card`. The loader checks card format only; the repository
test suite checks that packaged evidence paths and lesson ids exist (issue refs: format only) and that
`card_checked` is not older than the profile's `checked` date.

Four more optional profile keys (0.25.0): `alias_of`, `effort_override`, `suggest` and `measurement`
(see [model profiles](model-profiles/README.md)). They merge by field like the others and are not binding keys,
so setting them does not drop the card.
An alias resolves effort in layer order across its `effort_override`, its `reasoning_effort`, and the
target profile's `reasoning_effort`. Within one layer, those keys win in that order.

See [model profiles](model-profiles/README.md) for packaged values and the refresh procedure, and
[profiles.py](../peerhub/extensions/adapters/profiles.py) for the loader.
Keep credentials with the vendor CLI's authentication mechanism.

## Profile measurements and stream budgets

`peerhub profile stats [--json]` reads the selected SQLite store on demand. Each stored
ask prompt with a named profile counts once, including asks without a measurement.
The latest delivery attempt supplies the measurement. Model and effort come from the
stored binding, session mode from the accepted session, and wall time from the fenced
start and terminal timing. Token counters come from each delivery's provider telemetry.
The existing `bridge_session_history.usage_json` stores the session's latest usage.
`bridge_session_usage_coverage_*` and `bridge_session_latency_daily` roll up monitor
metrics by stream and peer. Profile stats use the underlying delivery evidence, and
quota readings come from the existing `observations` table. The additive migration
installs only the outcome history and attributable quota deltas.
Delivery-scoped counters apply directly to the ask. For session-cumulative counters,
PeerHub subtracts the previous delivery's values for the same stream, peer, and session
handle. A counter is unknown when either value is missing or the current value is lower,
as can happen after compaction. Stats average each metric over asks with an attributable
value. Zero counts toward coverage. Cache-read percentage divides the sum of per-ask
cache reads by total prompt tokens over asks with both counters and a positive prompt
total. Claude reports fresh input separately from its cache counters. The p50 uses
measured wall times.

The text table shows one row per profile, with compact durations, rounded input and output
averages (using `k` and `M`), and the latest benchmark date. `TOK%` is the share of asks
with both input and output counts. Missing values appear as `-`. JSON retains the
per-metric coverage and full measurement values, with `null` for unknowns and
`token_coverage_pct` for the table's token coverage.

Outcomes start as `unknown`. `peerhub outcome <ask-id> accepted|rejected|retried`
appends a correction. The ask ID is `requested_record_id` in `ask --json` and the
first field of the receipt. The latest mark supplies the outcome. Pool cost divides
the sum of valid percentage-point deltas by the number of accepted asks in the profile,
including accepted asks without a delta. A result requires both a valid delta and an
accepted ask. A delta uses the longest interval of comparable readings inside a
confirmed call with no overlapping call on the same pool in the selected store.
PeerHub prefers a weekly pool when more than one pool qualifies. A reset, uncertain
overlap, or missing pool evidence leaves that interval's delta unknown.

A profile is stale when the effective model or effort differs from its latest measured
binding. `profile list` still shows the catalog when telemetry cannot be read. It displays
`unavailable` for stats and uses `null` for `stats.n` and `stats.stale` in JSON.
`profile check` flags profiles without a current measurement in 30 days and
models missing from available `codex debug models` or `agy.exe models` output. It skips
unavailable catalogs. Stats read `docs/benchmarks/suite-v1/results*.jsonl` from the
workspace when available. A result line uses `profile`, `finished_at` (RFC 3339),
and the effective `model` and `effort` (`null` for provider defaults).

When stderr is a terminal and text output is selected, `ask` prints a receipt with the
per-ask token counts and cache-read percentage. Alias notices use the same conditions.
Set `PEERHUB_NO_RECEIPT=1` to silence both.
Inlined query files over 64 KB produce one warning and are passed in full.

Set these keys in `models.toml` at the same workspace or global scope as profiles.
The workspace value wins per key. Both values are positive integers.

```toml
[stream_budget]
prompt_tokens = 128000
resumed_turns = 12
```

Before a resumed ask, PeerHub checks the previous delivery in the selected session for
per-ask prompt tokens (fresh input, cache reads, and cache writes), using the same
scope and delta rules as stats. If the previous ask's prompt total is unknown, PeerHub
checks how many resumed calls the selected session has already had. Reaching either applicable
threshold prints one warning with a fresh-stream command and a handoff checklist. The
ask proceeds with the selected session.
