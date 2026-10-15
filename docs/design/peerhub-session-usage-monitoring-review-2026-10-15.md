# Peer and Session Usage Monitoring Review (2026-10-15)

## Decision and scope

This review refines the existing session-monitoring proposal in
[`plan-session-monitoring-2026-10-13.md`](plan-session-monitoring-2026-10-13.md).
It is a pre-TDD design addendum, not implementation approval. Keep monitoring
observational: report durable session state, recent execution health, provider
reported usage when its meaning is known, context evidence, and account quota
evidence. Do not turn the dashboard into billing, budget planning, forecasting,
or model-routing policy.

The existing proposal already covers most requested dimensions: session
inventory and state, profile/model/effort binding, fresh/reuse counts, last
request context occupancy, per-session quota evidence, recent confirmed calls,
expiry/eligibility, and explicit freshness/completeness. Source review confirms
that Bridge writers already persist session-call and terminal-telemetry
receipts, plus lifetime fresh/reuse projections. The bounded read model and
session CLI/diagnostic surfaces remain proposed; `session_receipts.py` is a
writer projection, not a completed monitoring reader.

## Peer review

CC and CX recommend excluding monetary cost, cross-provider token averages,
cumulative session tokens, and per-session quota allocation until their
measurement semantics and sources are reliable. AG agrees on excluding cost
and message payloads, and suggests reported-token accumulation where adapters
provide it. Reconcile this by allowing normalized, attributable per-turn token
facts as a later monitoring metric, while keeping cumulative totals and
averages out of the initial view until coverage and provider semantics can be
proved. Missing values remain unknown; they are never zero-filled.

All three reviews called out the same risks: partial token reporting biases
averages downward; context occupancy is not billing usage; account quota is
shared rather than session-attributed; raw provider payloads and vendor session
IDs must not leak; and per-session or per-frame N+1 reads threaten bounded
latency.

## Metric catalogue

| Surface | Include | Meaning and source | Guardrail |
|---|---|---|---|
| Peer overview | retained sessions by state; currently eligible count; fresh/reuse counts and completeness; bounded recent confirmed-call outcomes; latest activity age/duration; latest quota pools and freshness | Session history and writer-owned projections; call receipts; bounded existing activity projection; quota observations | State the selected stream/window scope. Do not present bounded recent activity as an all-time total. |
| Session row/detail | PeerHub handle, stream, generation, state, latest pointer, created/last-used/expiry, profile/model/effort binding, fresh/reuse counts, latest confirmed call mode/result, context tokens/window/percentage/source/age, quota pool observations | Retained session history, binding snapshot, call/telemetry receipts, pinned quota observations | Never expose vendor session IDs, prompt/response text, raw adapter output, credentials, or account identifiers. Unknown and stale are explicit. |
| Recent usage | Per-turn reported input/output/cache counters, if attributable and normalized; include source/model, reporting coverage, time, and delivery outcome | Adapter usage attached to durable response metadata today; proposed bounded receipt/rollup for monitor reads | Keep provider-native counters distinguishable. Do not aggregate fields with different semantics or imply full coverage. |
| Execution health | confirmed calls, successful terminal calls, failed terminal calls, unresolved/uncertain calls, recent latency distribution | Durable delivery/call receipts and bounded indexes | Distinguish `ask` attempts from confirmed provider calls. Failed-before-start requests are not provider calls. Label latency as end-to-end PeerHub operation time unless provider-only timing is measured. |

The default dashboard should show concise peer summaries and a small bounded
session sample. `session list/show` remains the complete paginated inventory.
Machine-readable output may carry the full bounded summary with explicit
coverage metadata. Do not add a second collector: read the same pinned,
read-only diagnostic snapshot used by `diag` and `diag --live`; `monitor` may
compose its explicit quota refresh with that snapshot.

## Token and cost rules

1. Preserve existing context semantics: `last_input_tokens` is last-request
   context occupancy, not total billed input. Calculate occupancy percentage
   only from a matching reported context window. Do not use watermarks or
   cumulative provider usage as a substitute.
2. A future token section may show measured per-turn counters when the adapter
   provides a validated normalized shape. It must carry provider/model/source,
   measured-turn count, eligible-turn count, and coverage. Never silently add
   missing turns as zero. Do not show an average unless every turn in the named
   bounded window has the same validated counter semantics; otherwise show
   totals only for the measured subset with coverage, or omit the aggregate.
   Never sum across providers or models merely because field names match. A
   truncated window must report dropped/unmeasured turns in its coverage.
3. Do not show unit cost or average cost from token counts. No stable price,
   discount, tier, currency, or billing source is currently persisted. A future
   direct provider-reported cost may be shown as reported cost with currency,
   source, and timestamp; price-table estimates belong to a separately scoped
   accounting feature.
4. Do not attribute account quota to a session or divide it among sessions.
   Show quota only at its observed account/pool scope, with state, age, and
   reset evidence.

## Counting and freshness contract

- **Session total** means retained PeerHub session handles in the selected
  stream/peer scope, including lost and tombstoned rows when the view is an
  inventory. “Active” means current eligibility under the existing session
  contract, not merely `state=ACTIVE`.
- **Confirmed call** means one distinct delivery with durable STARTED evidence
  and a `session_call` receipt. It counts even if execution later fails. A
  request replay does not add a call; a new delivery does. `NOT_STARTED` and
  unconfirmed `MAY_HAVE_STARTED` are excluded.
- **Outcome counts** must distinguish successful terminal, failed terminal,
  unresolved/uncertain, and excluded pre-start failures. Never combine
  uncertain work with failure or success. If complete all-time peer totals are
  needed, add writer-maintained counters with explicit migration completeness;
  do not scan all deliveries on each frame.
- **Recent activity** is a bounded sample (initially latest 50 per peer, or a
  smaller declared monitor window). Expose sample size, time range, and
  truncation/completeness. Existing `activity_rollups` has the bound but is
  best-effort ask activity, so it must not be described as exact durable call
  accounting.
- **Latency** from `operation_elapsed_seconds` covers end-to-end PeerHub ask
  operation timing, not model generation alone. Existing diagnostic output
  provides median over its bounded ask sample. Add p95 only if the same bounded
  sample is explicitly named; no lifetime percentile claim.
- **Context and quota** values always carry evidence source and age/state. A
  later call without terminal telemetry invalidates an older context value as
  current. Quota is selected at the snapshot time, never copied from an old
  ask result.

## Bounded-read and privacy requirements

The reader must use one pinned read-only SQLite snapshot, writer-maintained
counts, and indexed bounded recent-call/usage queries. No provider subprocess,
network request, vendor database read, migration, or repair may run from
`diag`, `diag --live`, `session list/show`, or the snapshot portion of
`monitor`. Avoid one query per session: load page rows and their latest
call/telemetry/quota evidence in a bounded batch. Existing `session list`
pagination and monitor scope/coverage rules remain authoritative.

Reconstruct public JSON from field allowlists. Treat user-controlled stream
names as potentially sensitive. Never serialize raw `detail`, response
metadata, adapter output, or provider/account identifiers.

## Pre-TDD additions

Add the following RED contracts to the existing session-monitoring test plan
before implementing the read model:

- **USM-01 — Scope and completeness:** fixture with multiple peers, streams,
  live/lost/tombstoned sessions, and legacy incomplete projections; peer
  overview reports exact retained inventory scope and nulls unknown totals.
- **USM-02 — Call/outcome semantics:** replay, confirmed success/failure,
  unresolved execution, and pre-start rejection; each delivery contributes at
  most once to its correct category.
- **USM-03 — Activity versus calls:** best-effort ask observations differ from
  durable call receipts; output labels each source/window and never claims the
  50-row activity sample is an all-time count.
- **USM-04 — Usage coverage:** mixed adapters, missing fields, cumulative
  counters, and model changes; counters remain attributable, unknown values are
  null, and no incomparable average is emitted.
- **USM-05 — Context/quota separation:** billing usage exceeds context
  occupancy and quota is shared; context percentage uses only matching
  occupancy/window evidence and quota remains account/pool scoped.
- **USM-06 — Latency label:** operation timing includes Bridge overhead; output
  labels end-to-end latency and bounds median/p95 to the named sample.
- **USM-07 — Bounded snapshot:** grow unrelated delivery/evidence history and
  session count; query/row work stays within the declared page/window bound,
  with no per-session N+1 reads or live provider work.
- **USM-08 — Privacy allowlist:** sentinel prompts, responses, vendor IDs,
  paths, tokens, and raw adapter payloads never appear in text or JSON views.

Do not implement price catalogs, monetary averages, quotas-per-session,
forecasting, budget controls, or adaptive routing as part of this monitoring
work. Revisit per-turn token totals only after normalized adapter semantics and
coverage are demonstrated in fixtures for each supported peer.
