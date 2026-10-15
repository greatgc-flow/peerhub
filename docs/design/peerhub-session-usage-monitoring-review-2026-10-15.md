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
   discount, tier, currency, or billing source is currently persisted. A
   provider's explicitly estimated amount may only be added later as a
   provider-specific estimate, with its estimate basis, adapter/CLI version,
   session-vs-delivery scope, and coverage. Never call it billed cost or combine
   estimates across providers. Authoritative account-level billing evidence
   belongs to a separately scoped accounting feature.
4. Do not attribute account quota to a session or divide it among sessions.
   Show quota only at its observed account/pool scope, with state, age, and
   reset evidence.

## What current evidence cannot tell us exactly

“Not exposed in monitoring yet” is different from “not knowable from stored
evidence.” These limits apply to the current source, before a session reader is
implemented:

| Requested fact | Current evidence | Exactness limit | Safe statement today |
|---|---|---|---|
| Current context occupancy and context-window size | Terminal `session_telemetry` receipts are written with null token/window/percentage values and `source=unknown` | No verified, attributable occupancy/window measurement is wired into the receipt. Record-position watermarks are not tokens. | Unknown; do not infer from billing usage. |
| Total tokens used over a session or lifetime | Adapters parse some usage fields; results can be attached to response metadata. Bridge keeps a raw latest usage snapshot for native sessions and calculates deltas only under specific conditions. | Providers expose different field semantics; some counters are cumulative, some per-turn, some fields absent. Missing usage is not a zero. The session monitor has no normalized durable token ledger or completeness projection. | Provider-reported usage may be shown for an individual attributable delivery after validation; no exact universal cumulative total. |
| Average tokens per turn | Same incomplete, mixed adapter reports | The denominator is unknown when some turns omit counters; cross-model/provider fields may differ. | No general average. A future bounded same-semantics sample may report coverage and a conditional average. |
| Actual monetary/unit/average billed cost | No persisted authoritative billing amount, invoice linkage, contract tier, discount, or per-request billing evidence | CC documents a client-side estimate, and its Admin API can provide organization/time-bucketed billing evidence for eligible accounts; neither currently provides a reliable amount attributed to each PeerHub delivery across peers. Token counts alone cannot reconstruct actual charges. | Actual per-delivery billed cost is unknown. A CC estimate is a distinct possible metric and must be explicitly labeled, versioned, and scope-validated. |
| Number of actual model inference turns inside a delivery | Bridge records confirmed deliveries/calls | A CLI delivery may include provider-internal tool/model loops that adapters do not consistently expose. One `session_call` is not necessarily one model inference. | Count confirmed Bridge calls, label them as calls/deliveries, not model turns. |
| Exact all-time error totals | Durable delivery evidence exists; current activity diagnostic keeps only the newest 50 best-effort ask observations per peer | The activity sample is bounded and can omit events; no all-time peer outcome/error projection currently exists. Legacy certainty and terminal classification can also be incomplete. | Show recent sample counts with its window. All-time error counts require a writer-maintained counter and completeness flag. |
| Provider-only generation latency / time to first token | `operation_elapsed_seconds` measures end-to-end ask operation duration | It includes local work such as claim/Bridge processing, process startup, and other overhead; separate provider timings are not consistently measured. | Label as end-to-end operation latency; do not call it inference latency. |
| Session actually resumable at the provider right now | Local retained state, binding, last use, and eligibility evidence | The remote provider can expire, revoke, or invalidate a session between calls. Only a later resume attempt can confirm acceptance, and probing may have side effects/cost. | Show local eligibility/last-known state, not guaranteed provider availability. |
| Quota or spend attributable to a particular session | Provider quota observations at peer/account-pool scope | Accounts and pools can be shared across sessions and external clients; providers do not report a reliable session allocation. | Show shared quota evidence at its observed scope only. |
| Exact whole-peer session/error totals in every live dashboard frame | The design bounds monitor reads to selected recent streams and session rows | Scanning every retained/tombstoned session or delivery each frame breaks bounded-work guarantees; current projections are per stream+peer for fresh/reuse, not every requested outcome. | Mark monitor totals with scope/coverage. Complete inventory remains paginated; whole-peer exact rollups need dedicated projections. |
| Provider-resolved model/effort actually used for every sub-turn | Session binding stores selected profile/model/effort attributes | A configured alias or CLI target is not proof of a provider's resolved backend or hidden internal sub-turn settings. | Report the stored selection/binding as configured; do not claim internal provider execution details. |

The reliable current facts are narrower: PeerHub session handles and local
state; recorded binding attributes when present; fresh/reuse delivery counts
when the projection says history is complete; confirmed Bridge-call receipts;
and quota observations with source/freshness when collected. These are still
not all exposed through the monitoring CLI today.

## Official provider documentation research (2026-10-15)

Internet research can settle several field semantics, but it cannot prove what
the installed CLI versions on a particular machine emit. These findings are
from official provider documentation and current upstream schemas/source; the
repository's installed binaries and live accounts were not queried.

| Peer/source | Resolvable from official documentation | Remaining work or limitation |
|---|---|---|
| AGY / Antigravity CLI | Its JSON result documents `duration_seconds`, `num_turns`, and input/output/thinking/cache/total usage. `stream-json` adds per-step usage and duration. In a continuous stream-input process, result usage, turns, and duration are cumulative session counters; individual `step_update` usage/duration is per step. | PeerHub currently parses the final JSON result. A stream parser could capture validated step facts, but must discard `text_delta`, `tool_info`, subagent details, and other payloads to preserve privacy. Docs say `--conversation` resumes in a new process but do not specify whether its result counters are cumulative across that process boundary; verify this with a narrow live test before delta accounting. No cost field is documented. [Headless mode docs](https://www.agy.dev/docs/cli/headless/) |
| CC / Claude Code | Official Agent SDK docs specify per-turn `usage` scope and cumulative `total_cost_usd` / `modelUsage` behavior, including resumed sessions and subagent coverage differences. `total_cost_usd` is a client-side estimate from a bundled/selected price table, not authoritative billing. It can support a clearly labeled estimate if the installed CLI JSON output exposes it and the version/scope is validated. | The exact installed `claude -p` JSON shape/version must be checked against PeerHub's adapter before relying on those SDK fields. Resumed-session cost is cumulative in current versions (before v2.1.277 it covered only that call), so a delivery metric needs a baseline/delta and version-aware handling. The Anthropic Usage and Cost Admin API provides org-level, time-bucketed usage/cost dimensions, with data typically appearing within five minutes; it requires suitable organization credentials and has no PeerHub session/delivery key, so it cannot establish per-session billing. [SDK cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking), [Usage and Cost API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api) |
| CX / Codex CLI | Current upstream `exec --json` code emits `turn.completed` usage from the latest accumulated total usage snapshot. The separate app-server protocol exposes `last`, `total`, and `modelContextWindow`, which can provide clearer per-turn/cumulative usage and context-window evidence. | Current PeerHub uses exec JSON, not that app-server telemetry. Context window and `last` usage therefore require an adapter/protocol change, then versioned integration tests. The completion event does not provide an authoritative per-call USD amount or prove provider-resolved model identity. [Codex exec JSONL exporter](https://github.com/openai/codex/blob/main/codex-rs/exec/src/event_processor_with_jsonl_output.rs), [app-server token usage schema](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/json/v2/ThreadTokenUsageUpdatedNotification.json) |

## Telemetry and evidence register

This register separates values PeerHub can observe from facts learned through
provider documentation and from facts that still require local validation or
cannot currently be attributed. “Measurable” means the adapter can parse the
value; it does not mean the monitoring CLI already displays it or that the
provider's semantics are interchangeable. No live provider binary/version was
queried for this review.

### Common PeerHub measurements

| Item | PeerHub evidence available now | Coverage / interpretation |
|---|---|---|
| Session inventory, state, age, expiry, configured binding | Local session history and binding snapshots | Exact only within the selected stream/peer and retained-history scope. Configured profile/model/effort is not proof of provider-resolved execution. |
| Fresh/reuse counts | Writer-maintained per-stream/per-peer projections | Use only when completeness metadata permits; state selected scope. |
| Confirmed call and terminal outcome | Durable Bridge call/evidence receipts | Counts Bridge deliveries/calls, not internal provider model turns. Preserve success, failed, uncertain, and pre-start categories separately. |
| Latest operation duration and recent activity | `operation_elapsed_seconds` and bounded activity sample (currently at most 50 per peer) | End-to-end PeerHub operation time; includes local work and process startup. Activity is not an all-time durable call count. |
| Context occupancy/window | Session telemetry receipt schema exists, but current writer records null token/window/percentage with `source=unknown` | Not presently measured. Never substitute usage tokens or a public model's advertised maximum. |
| Quota observations | Existing quota probe/projection at peer/account-pool level | Shared-pool observation with source/freshness; no session allocation. |
| Durable per-delivery provider usage | Adapter parses usage; Bridge persists normalized usage in terminal evidence/response metadata where available | Semantics and fields vary by peer; no complete normalized durable ledger/coverage projection yet. |
| PeerHub all-time error totals | Durable delivery evidence exists, but activity is bounded and no complete all-time outcome projection is present | Requires a writer-maintained aggregate and migration/completeness treatment; do not scan all history on each monitor read. |

### Per-peer telemetry

| Peer | Parsed/measurable in current adapter path | Official information that can be looked up | Requires PeerHub change or local validation | Still unknown / not inferable |
|---|---|---|---|---|
| **CC (Claude Code)** | Current parser reads final JSONL `result.usage`: input, cache-read, cache-write, and output token fields. It combines input categories only when all required input fields exist. The configured session binding/model/effort, delivery outcome, and end-to-end duration are also locally recorded. | Agent SDK docs define usage scope, per-model `modelUsage`, and `total_cost_usd` as a client-side price-table estimate. Current resumed sessions can report cumulative session estimate; before CLI v2.1.277 resumed print/SDK totals covered only that call. SDK docs distinguish top-level `usage` (excluding subagents) from cumulative cost/model usage (including subagents). Anthropic's Admin Usage/Cost API documents organization-level, time-bucketed reports grouped/filtered by model, workspace, service tier, and API key; data is typically available within five minutes. | Confirm installed `claude` version and exact `claude -p --output-format json` result schema. If estimate is exposed and desired, persist it separately from billed amounts, label estimate basis/version, and derive delivery deltas only from validated session baselines. Add a context-window source only if a documented, versioned output field is verified. Org API integration would need explicit credentials, scope, refresh/caching, and must stay outside read-only monitor snapshots. | Actual invoice amount per PeerHub delivery/session; provider quota/spend attributable to one session; exact model/backend for every hidden subagent or internal turn; current exact context occupancy unless returned by a verified runtime field; inference-only latency. |
| **CX (Codex CLI)** | Current parser reads `turn.completed.usage` fields (input, cached input, cache-write input, output). On resumed threads these are raw cumulative counters; Bridge stores a baseline and derives a delta only if the values remain monotonic. Binding, outcomes, and end-to-end duration are local. | Upstream CLI exporter source shows the completion event is populated from its latest total-token snapshot. The distinct app-server schema exposes `last`, `total`, and nullable `modelContextWindow`. | Pin/test the installed Codex version and raw event fixtures. To capture context size and provider-reported last-turn usage, adopt a supported app-server telemetry path or wait for the exec event to expose equivalent fields; add protocol lifecycle and privacy tests. Do not read vendor databases. | Exact context occupancy/window through the current exec adapter; invoice-level USD cost for a CLI delivery; provider-resolved backend/model actually serving every internal turn; exact model-only latency; session-level quota allocation. |
| **AG (Antigravity CLI)** | Current parser reads one JSON result's input, cache-read, and output usage fields. `--conversation` binding, delivery outcome, and end-to-end duration are local. The parser does not currently persist AG-reported thinking/total token fields as normalized usage. | Official headless docs enumerate result usage (input/output/thinking/cache-read/total), `duration_seconds`, and `num_turns`. Stream-json exposes per-step usage/duration. In a continuous stream-input process, each step's usage/duration is per-step, while result counters, turns, and duration are cumulative over the session. No result cost field is documented. | Decide and test a narrow `--conversation` resume case to establish counter scope across separate processes before computing delivery deltas. If step metrics are needed, add an allowlist stream parser and persist only safe numeric fields; omit response fragments, tool inputs/outputs, subagent identifiers, and raw stream records. Validate actual binary version/output. | Whether result counters are cumulative when a new process resumes via `--conversation`; actual billed cost; provider quota attributable to one session; exact model-only latency; exact provider-side context occupancy unless a supported output field is verified. |

### Updated classification of unknowns

- **Can be settled by documentation:** AGY continuous-process cumulative result semantics; CC estimate-vs-billing distinction, subagent inclusion, and resumed-session cumulative scope/version caveat; Codex app-server availability of last/total usage and model context window.
- **Can be made observable with PeerHub changes:** AGY per-step token/time facts via allowlisted stream parsing; CC provider-estimated cost with explicit estimate/scope/version labels and baseline handling; CX context and last-turn usage if PeerHub adopts app-server telemetry; exact all-time PeerHub error totals through writer-maintained projections.
- **Still not knowable from public docs alone:** AGY usage scope across a fresh process resuming `--conversation`; the installed binaries' actual versions/output; exact invoice-level cost attributable to every PeerHub delivery across peers; model-inference-only latency; provider-side session validity without attempting resume; CX backend-resolved model per internal turn. These require local version/output validation, a safe empirical test, provider/account-specific evidence, or remain unavailable.

### Maintenance rules

- Keep this register in English beside the monitoring design, with one row per
  supported peer and shared rows for PeerHub-owned evidence.
- For each claim, record its evidence class (local parser/writer, provider
  documentation, live versioned test, or unknown), last-verified date, and a
  direct official source link. A documentation lookup is not a local runtime
  verification.
- Recheck official links and version caveats when an adapter, CLI contract, or
  provider billing/usage API changes. Update the register in the same change
  that changes parsing or persistence semantics.
- Promote a value from “unknown” only after a versioned fixture or safe live
  measurement proves its meaning and attribution. Preserve null/unknown when
  coverage is absent; do not infer zero or fill gaps from public model limits.
- Keep the final “Still unknown” cells as the user-facing unresolved list; do
  not repeat settled documentation findings in that list.

AGY stream events contain response fragments and tool invocation inputs/outputs in
addition to useful telemetry. Any future implementation must parse an allowlist
and must not persist or expose raw stream events.

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
