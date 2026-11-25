# CLI, Monitoring, and Inspection Renewal (2026-10-14)

## Status and scope

Consensus design addendum for the renewal in
[`peerhub-pre-tdd-renewal-2026-10-14.md`](peerhub-pre-tdd-renewal-2026-10-14.md).
The initial source review was against `cfb28aba` on `feat/session-reuse`; the
first implementation slice and its tests are recorded below. This document
supersedes PH-T046/047 in that renewal and resolves the CLI, monitoring, and
inspection decisions below. It does not reopen the Core schema or imply that
the remaining renewal slices are implemented.

The goal is a CLI that is easy to discover for people and safe to consume for
automation. Defaults should show useful information immediately, preserve
truth/freshness, and avoid hidden writes or provider calls. Human-readable TTY
views and stable JSON are both first-class; provider availability is never
inferred from configuration.

## Source-backed findings

The parser in `peerhub/cli/app.py` and generated
`docs/implementation/command_inventory.json` are the current command
authorities; `python tools/command_inventory.py --check` passed. The source
summary at the top of `app.py` is stale: it omits `ask`, `observation refresh`,
`monitor`, and the dashboard form of `diag`. `monitor` help says it watches
quota evidence but does not disclose that every scheduled cycle calls the
provider collector and persists observations. Its `--view` default also differs
from `diag`.

Read-only Core verbs (`peer get`, `stream show`, `record read`, `offset get`)
currently pass through `CoreStore`, whose constructor preflights and runs
migrations. A query against a missing database can therefore create it, and a
query against an older supported format can migrate it. Diagnostics already
have a separate `mode=ro`, `query_only`, authorizer, and missing-store guard.
This violates the expected read/write boundary and the README's missing-store
promise.

There is no public `peer list`, `stream list`, `profile list/show`, or
`session list/show`. `diag` is the only broad peer/stream view, while callers
must know a profile name before using `ask --profile` and a session handle
before selecting it. The runtime profile resolver merges packaged, global, and
workspace TOML per field but returns no field provenance. The prior rejection
of a mutable Runtime Target Catalog remains valid; a read-only view of resolved
TOML is a different operation.

Routine diagnostic rendering is much more expensive than its read-only label
suggests. `ReadonlyDiag._open` runs full-database `PRAGMA quick_check` on every
render. `_observations` materializes all historical observations to choose the
latest per identity; `_activity` fetches the complete activity history before
keeping 50 per peer; `_pools` aggregates all observations; `_streams` reads all
streams and issues repeated per-stream queries for records, members, offsets,
and refusal evidence. The renderer also executes full `COUNT(*)` queries for
records and observations. `diag --live` repeats these operations every two
seconds. These are proven query shapes; the review did not claim a benchmarked
latency or a database-size threshold.

Monitoring adds a separate wait. `run_monitor` synchronously calls
`refresh_quota` before its first diagnostic frame. That collector probes cx,
cc, and ag sequentially. In the 2026-10-14 refresh, the first persisted result
was captured at 10:03:40 UTC and the last at 10:03:49 UTC. This nine-second
span is the observed provider work alone, not a full startup benchmark; the
monitor places that work before its first frame. It then sleeps the configured
interval after work, so a slow cycle extends the nominal period. The dashboard
renders even if its diagnostic report is degraded, while the final exit status
reflects only collection.

`diag health` also reports `active_streams` as the first five streams sorted by
ID, without checking stream state. The JSON field is inaccurate. The diagnostic
JSON and monitor JSON wrap the same dashboard differently, making consumers
branch on which command produced a snapshot. Finally, the current store
selector gives `PEERHUB_DB` priority over `--db`; the accepted renewal candidate
PH-T006 specifies an explicitly supplied `--db` wins and is never redirected.

## Command boundaries and default behavior

The command tree remains recognizable and keeps current entry points. Its
semantic leaves are divided by resource or operation:

| Surface | Owns | Default effect |
|---|---|---|
| `peer`, `stream`, `record`, `offset` | Core durable entities and records | Explicit mutations only; reads never create or migrate a store |
| `profile` | Effective model/effort configuration | Read-only, local files only |
| `session` | Retained Bridge sessions | Read-only, one SQLite snapshot |
| `ask` | One provider delivery through the Bridge | Explicit provider execution and durable records |
| `observation refresh` | One quota collection | Explicit provider probes and append-only observations |
| `diag` | One-shot health, quota, and dashboard snapshots | Read-only; `diag --live` remains a read-only watch |
| `monitor` | Collection plus dashboard workflow | Provider collection is explicit in the command name/help; refresh result and snapshot health stay separate; default collection is every cycle and `--collect-every` remains available |

`monitor` is intentionally a convenience workflow that composes the canonical
collector and diagnostic reader. It does not own a second probe/parser or a
second source of truth. Keep `diag --live` as the pure read-only watcher. Remove
the *proposed but unshipped* `monitor --read-only` contract: it duplicates
`diag --live`. Do not remove or repurpose either existing command. `observation
refresh` remains the one-shot collector.

Use the best presentation automatically for new list/show surfaces with
`--format {auto,table,json}`: `auto` selects a concise table on an interactive
terminal and stable JSON when stdout is redirected. The explicit formats
override auto selection. Existing command output defaults remain unchanged
unless a separately versioned contract says otherwise. JSON includes a schema
version, source store/config layer, explicit
unknown/stale values, and structured errors. No presentation mode changes
side-effects.

Bare `peerhub diag` remains the one-shot dashboard; `diag --live` remains
opt-in. `monitor` draws the latest committed snapshot before starting its first
refresh, then announces refresh progress and updates the frame when collection
finishes. With no stored observations it shows explicit UNKNOWN states rather
than waiting silently. Collection remains serial in this slice; no background
daemon, detached worker, or concurrent provider probing is introduced. The
monitor uses monotonic deadlines, skips missed slots rather than accumulating
drift, and keeps refresh status separate from diagnostic status. In JSON/NDJSON,
each line is a complete versioned frame: the initial committed snapshot is
marked as not yet refreshed, and subsequent frames carry the completed refresh
result. Invalid diagnostic data cannot be reported as a successful monitor
cycle merely because collection returned success.

## Read surfaces

- Add `peer list` and `stream list`, each with deterministic primary-key order,
  default limit 50, hard maximum 200, and an opaque cursor. A page reads at
  most the requested number of entities; limits outside 1–200 fail before SQL.
  Existing point reads remain available.
- Add `profile list` and `profile show <provider.profile>`. Both resolve the
  packaged → global → workspace TOML layers with exactly the same merge and
  validation rules as `ask --profile`. Each effective field reports its source
  layer. Invalid config is explicit and never silently falls back. These
  commands neither inspect provider catalogs nor claim that a configured model
  is currently available. They do not write the DB, config, or capability
  registry. The mutable catalog rejection remains in force.
- Add the already proposed bounded `session list` and `session show`. List has
  default limit 20 and a cursor bound to store, lineage, filter, ordering/index
  version, and first-page visibility. Show/list use only PeerHub handles and
  redacted session data. There are no public delete, tombstone, or prune verbs;
  retention stays behind the existing fenced writer.
- All Core read verbs and all new inspection verbs reject a missing selected
  store without creating a file. They open a verified read-only snapshot and
  never run migrations. Mutating Core verbs retain explicit bootstrap/migration
  behavior. Explicit `--db` precedence and missing-store behavior follow
  PH-T006; a selected missing path is never redirected.
- Rename the health sample field to a truthful deterministic name such as
  `stream_sample`; do not imply that alphabetic order means activity. Version
  the diagnostic JSON change and include the sample limit. Keep counts separate
  from the bounded sample.

## Snapshot cost, evidence, and errors

Routine snapshots do bounded work independent of unrelated historical rows:

1. Remove full `quick_check` from ordinary snapshot opening. The explicit
   `peerhub diag integrity` operation performs SQLite `quick_check`; application
   receipt and counter recounts remain separate checks with separate outcomes.
2. Select latest observations and activity with indexed bounded queries. Preserve
   the current effective-time-then-capture-sequence ordering and surface corrupt
   latest evidence as an error; an optimization must not silently select an
   older row as though it were current.
3. Batch stream aggregates and related members/offsets/evidence rather than
   issuing per-stream queries. Bound dashboard samples and report total counts
   only through maintained counts or a deliberate summary path, not a repeated
   full-history scan on every frame.
4. Share one snapshot data schema between `diag --json`, `diag --live --json`,
   and the nested snapshot in `monitor --json`. Monitor adds a separate refresh
   envelope; it does not redefine snapshot fields. Bump the schema when renaming
   `active_streams` or changing result meaning.
5. Keep collection outcome, snapshot outcome, evidence freshness, and derived
   assessment distinct. Stale evidence is not fresh evidence; unavailable
   provider data is not a successful measurement. Exit codes and in-band status
   agree: `diag health` returns 4 for ERROR/PARTIAL, 5 for a missing store, 6
   for unsupported schema, and 0 for OK. This intentionally changes the pinned
   legacy ERROR/PARTIAL exit 0; release notes must call out that stdout still
   contains the full report. A requested stream that does not exist returns 1,
   matching Core point reads. Monitor status has separate `refresh` and
   `snapshot` fields; its exit precedence is unavailable store (5), unsupported
   schema (6), diagnostic storage/report failure (4), incomplete collection
   (1), success (0). This is versioned with the monitor frame schema.

Do not invent millisecond budgets before measuring representative stores. The
RED contract is bounded query/row work as unrelated history grows; performance
budgets are set from an instrumented baseline during implementation. Keep the
existing pace R1 math and show stale/unknown anchors honestly. Collection health
and 7-day pool pace are not per-request token attribution.

## Pre-TDD contract additions and supersessions

The existing PH-T033/036/037 contracts remain the bounded-reader and distinct
integrity-check foundation. PH-T053–057 remain the session-list foundation.
Replace PH-T046 with a test that `monitor` uses the shared collector, displays
the committed snapshot before refresh, and exposes independent refresh and
diagnostic outcomes. Replace PH-T047 with a test that `diag --live` remains
available and performs no provider subprocesses or writes. These supersede the
proposed `monitor --read-only` and `diag --live` removal contracts.

Add these RED contracts before implementation:

- **PH-T095 — Read-only Core commands:** GIVEN an absent, old, or current store,
  WHEN `peer get`, `stream show`, `record read`, or `offset get` runs, THEN an
  absent store is not created, an old store is not migrated, and a current store
  is not modified; failure/status/exit are explicit.
- **PH-T096 — Bounded Core listings:** GIVEN more rows than the page limit,
  WHEN `peer list` or `stream list` runs without options, THEN it returns at
  most 50 deterministic rows plus a cursor; limits outside 1–200 fail before
  SQL, and a request for 200 is capped at 200 rows.
- **PH-T097 — Effective profile inspection:** GIVEN packaged/global/workspace
  values with per-field overrides, WHEN `profile list/show` runs, THEN each
  winning value and source matches `ask` resolution; malformed config is
  surfaced, with zero provider subprocesses and zero DB/config writes.
- **PH-T098 — Bounded session inspection:** Extend PH-T053–057 to assert default
  limit 20, a stable cursor, no provider IDs, and no mutation on list/show.
- **PH-T099 — Bounded routine snapshots:** GIVEN unrelated observation, activity,
  record, and stream history grows, WHEN routine snapshots repeat, THEN visited
  rows and statements remain bounded by requested output/scope; SQLite
  `quick_check` runs only through `peerhub diag integrity`, while application
  receipt and counter recounts remain separately invokable and reported.
- **PH-T100 — Stream query shape:** GIVEN many streams, WHEN one dashboard
  snapshot runs, THEN stream data comes from a bounded sample and a fixed bounded
  query set rather than per-stream SQL fanout; exact totals do not trigger an
  unbounded recount on each frame.
- **PH-T101 — Shared JSON frame:** GIVEN one diagnostic snapshot, WHEN requested
  through one-shot diag, live diag, or monitor, THEN snapshot objects validate
  against the same schema/version; monitor refresh metadata remains separate;
  every NDJSON line parses independently.
- **PH-T102 — Monitor first frame/cadence:** GIVEN a slow provider probe and a
  stored snapshot, WHEN monitor starts, THEN the committed snapshot is emitted
  before probing; missed monotonic deadlines are skipped; no refresh result is
  attributed to snapshot health or vice versa.
- **PH-T103 — Honest health names:** GIVEN open and closed streams in mixed ID
  order, WHEN health JSON is rendered, THEN its bounded sample is named and
  described as a sample, not as “active”; the versioned response is consistent.
- **PH-T104 — CLI documentation/inventory:** GIVEN parser changes, WHEN the
  generated command inventory and command help are checked, THEN every leaf,
  side effect, output default, and exit status is represented once and the
  inventory check passes.
- **PH-T105 — Health exit status:** GIVEN health reports with OK, PARTIAL,
  ERROR, missing-store, and unsupported-schema outcomes, WHEN `diag health`
  runs, THEN exits are respectively 0, 4, 4, 5, and 6 while valid diagnostic
  JSON remains on stdout. Update the existing test that pins ERROR with exit 0
  and document the 0.15.2 compatibility change in the 0.16.0 release notes.
  A scoped stream query returning NOT_FOUND exits 1, matching `peer get` and
  `stream show`.

Implementation starts only after these contracts are translated into the
repository's TDD suite and the existing renewal's unresolved owner decisions
are closed. Any source change to CLI groups/options also updates the generated
command inventory and effect proofs; any new extension module is registered in
the continuity and architecture gates.

## Implementation progress (2026-10-14)

The first implementation slice is in the working tree; it is not released:

- Core point reads and new peer/stream listings use a verified SQLite
  read-only path. Missing stores are not created, older stamped stores are not
  migrated by reads, and future schema versions fail closed. Peer/stream list
  uses deterministic keyset pages (default 50, hard maximum 200), opaque
  kind-bound cursors, and TTY-table/redirected-JSON defaults.
- `profile list/show` resolves the existing TOML layers and reports each
  effective field's source. It does not query providers or open/create the Core
  DB.
- `session list/show` is read-only and bounded (default page size 20). Its
  cursor binds the store identity, workspace generation, filters, ordering, and
  first-page row high-water. Public session projections omit vendor and
  external session identifiers.
- Routine diagnostic snapshots no longer run the whole-file SQLite
  `quick_check`; `diag integrity` exposes that explicit read-only operation.
  `diag health` now emits schema 2.0 with `stream_sample`, returns 4 for
  ERROR/PARTIAL and 1 for a missing stream.
- `monitor` emits the last committed snapshot before provider collection and
  schedules against monotonic deadlines, skipping elapsed slots. JSON frames
  identify the initial and completed phases.

Focused TDD and regression validation passed for these slices. PH-T095/096,
PH-T097, PH-T098, PH-T103–105, and the session-inspection portion of
PH-T053–057 are implemented. The relevant first-frame/cadence and independent
outcome portions of PH-T102 are implemented. Stream details now use a fixed
query set for aggregates, members, offsets, and latest refusal evidence; a
regression test exercises nine streams and asserts at most six reads without
per-stream SQL. Activity is capped at 50 rows per peer, and latest observations
and activity use writer-maintained projections when available. Pool counts and
exact observation totals come from per-identity rollups. Core record totals and
per-stream record counts still scan record history. Older observation stores
without projections use the compatibility full-scan path until their next
ObservationStore writer initializes them.

Monitor JSON now wraps the shared `dashboard_data` snapshot contract
(`schema_version: 1.0`) used by one-shot, live, and monitor JSON. Its enclosing
frame is version 1.1. Completed-cycle exit precedence is unsupported schema
(6), diagnostic failure/partial (4), incomplete refresh (1), and success (0);
the missing-store guard remains 5. `diag integrity` now audits both projections
against immutable observations without repairing them. The
initial local synthetic diagnostic store with 5,003 observations rendered five
warmed full snapshots in 69.21–89.42 ms (median 71.29 ms). After the projection
change, five warmed all-section snapshots on local synthetic stores with
roughly 5k and 25k observations plus the same number of Core records measured
2.30ms and 4.88ms median respectively. This is consistent with observation
history cost being reduced; exact record recounts still scale with history.
These samples set no production SLO. Full validation passed after the projection
change: 2,381 tests passed, 1 skipped, 29 deselected in 695.97 seconds; pyright
reported zero errors, command inventory and diff checks passed, and the sealed
M1 package validator returned PASS.

## Observation projection design and implementation

The optimization is a writer-maintained `observation_rollups` projection
owned by `ObservationStore`, with one row per `(subject_ref, kind,
resource_pool_ref)` identity. It stores the row count and the capture sequence of
the latest row under the existing `(effective_at_us, capture_seq)` ordering.
An insert trigger updates this projection in the same SQLite transaction as
the immutable observation append. Creating the projection on an existing store
performs a one-time backfill in the writer transaction; routine read-only
diagnostics never create or repair it. If the projection does not exist, the
reader retains the legacy full-scan path so old stores remain readable.

This keeps source evidence append-only and avoids changing Core's five-table
schema or Core schema version. The projection limits routine observation
decoding and pool aggregation to distinct observation identities, but does
not make the initial upgrade page visits bounded. Exact
record totals remain an independent Core concern. `diag integrity` audits
projection consistency explicitly; the routine dashboard trusts SQLite's
transactional trigger invariant, just as it trusts the existing append-only
constraints. TDD pins nullable pool identities, effective-time ties, historical
rows newer/older than arrivals, malformed latest evidence, direct SQL inserts
through triggers, read-only fallback, atomic backfill, and projection audit.
