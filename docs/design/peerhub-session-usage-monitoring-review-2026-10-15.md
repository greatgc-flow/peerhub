# Peer and Session Usage Monitoring Review (2026-10-15)

## Decision and scope

This review refines the existing session-monitoring proposal in
[`plan-session-monitoring-2026-10-13.md`](plan-session-monitoring-2026-10-13.md).
It is a pre-TDD design addendum, not implementation approval. Keep monitoring
observational: report durable session state, recent execution health, provider
reported usage when its meaning is known, context evidence, and account quota
evidence. Do not turn the dashboard into billing, budget planning, forecasting,
or model-routing policy.

The existing proposal covers session inventory/state, profile/model/effort
binding, fresh/reuse counts, context evidence, account-pool quota evidence,
recent confirmed calls, expiry/eligibility, and freshness/completeness. This
addendum's first implementation slice now persists provider usage in the
existing Bridge telemetry receipt and exposes safe binding, count, and latest
usage fields in `session list/show`. Full grouped monitoring, preferences,
coverage rollups, and quota/model/context dependency drill-down remain planned.

**Current-decision correction (2026-10-15):** older paragraphs in the Oct. 13
proposal describe the absence of a normalized durable ledger. That was accurate
for the proposal's then-current state, but is superseded here: TDD will extend
the existing unique `bridge_evidence.kind=session_telemetry` receipt to schema
v2 and build bounded projections from it. The single-receipt authority remains
unchanged; no parallel facts table is approved.

**Implementation status (2026-10-15):** the delivered slice uses a versioned
provider-candidate contract and the existing unique `session_telemetry`
receipt. It strips candidate data before public result snapshots, binds
delivery scope to the fenced terminal delivery, and marks session-cumulative
CC/CX/AG values unattributed. `session list/show` reads safe typed telemetry
from that receipt. This does not complete the broader monitoring catalog or
projection work itemized below; remaining requirements stay design/TDD work.

CC, CX, and AG separately challenged provider scope/normalization, public usage
labels, CI selection/concurrency, release SHA/tag binding, history depth, and
release DAG contracts. The fixes were reviewed to approval; current cumulative
usage is never differenced, public snapshots label scope/attribution, and the
release matrix requires all eight current candidate JUnit files. Local focused
tests and pyright passed. GitHub CI for this revision remains to be run after
push; the prior Windows full-suite run remains historical evidence only.

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

### CX cross-review (2026-10-15)

The independent read-only CX review found a stale AG resume caveat and two TDD
handoff ambiguities. The document now records the observed cumulative result
scope for `agy 1.3.5` after exact process-boundary resume, while retaining
USM-15b as the stricter gate for deriving per-delivery deltas. The review also
required separating AG stream RED tests from the later transport implementation
and specifying the live CC/CX usage-scope call budget; both are now explicit in
the TDD sequence and live matrix. The final read-through found no blocker; the
overlap between usage-scope and exact-selection call budgets is also explicit.
Its optional suggestion to omit step summaries from the initial
scope was considered: the user requirement is to preserve CLI-measurable data,
so the v2 receipt keeps an optional bounded step shape, but adapters only
populate it when their event scope is validated; absent/partial step coverage
stays explicit, and unsafe/raw events are never persisted. The illustrative
CLI version is marked synthetic. This review is a design cross-review, not a
peer consensus vote or implementation approval.

## Metric catalogue

| Surface | Include | Meaning and source | Guardrail |
|---|---|---|---|
| Peer overview | retained sessions by state; currently eligible count; fresh/reuse counts and completeness; bounded recent confirmed-call outcomes; latest activity age/duration; latest quota pools and freshness | Session history and writer-owned projections; call receipts; bounded existing activity projection; quota observations | State the selected stream/window scope. Do not present bounded recent activity as an all-time total. |
| Session row/detail | PeerHub handle, stream, generation, state, latest pointer, created/last-used/expiry, profile/model/effort binding, fresh/reuse counts, latest confirmed call mode/result, context tokens/window/percentage/source/age, quota pool observations | Retained session history, binding snapshot, call/telemetry receipts, pinned quota observations | Never expose vendor session IDs, prompt/response text, raw adapter output, credentials, or account identifiers. Unknown and stale are explicit. |
| Recent usage | Provider-reported input/output/cache counters when present; include source/model, reporting coverage, time, and outcome | Typed existing `session_telemetry` receipt; broader bounded coverage/rollup remains proposed | Keep provider-native counters distinguishable. Session-cumulative values are unattributed and never become per-delivery aggregates. |
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
| Total tokens used over a session or lifetime | Adapters parse some usage fields; results can be attached to response metadata. Bridge keeps a raw latest usage snapshot for native sessions and calculates deltas only under specific conditions. | Providers expose different field semantics; some counters are cumulative, some per-turn, some fields absent. Missing usage is not a zero. Current v1 receipts do not yet provide the proposed normalized schema-v2 usage contract or completeness projection. | Provider-reported usage may be shown for an individual attributable delivery after validation; no exact universal cumulative total. |
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
| AGY / Antigravity CLI | Its JSON result documents `duration_seconds`, `num_turns`, and input/output/thinking/cache/total usage. `stream-json` adds per-step usage and duration. In a continuous stream-input process, result usage, turns, and duration are cumulative session counters; individual `step_update` usage/duration is per step. The 2026-10-15 live probe also observed cumulative result counters in one exact `--conversation` resume across a process boundary. | PeerHub currently parses the final JSON result. A stream parser could capture validated step facts, but must discard `text_delta`, `tool_info`, subagent details, and other payloads to preserve privacy. The single resume pair establishes scope for that observed version and simple task, not safe deltas under model switches, concurrent use, compaction, retries, or tool/subagent-heavy asks; those remain gated by USM-15b/29 and the live matrix below. No cost field is documented. [Headless mode docs](https://www.agy.dev/docs/cli/headless/) |
| CC / Claude Code | Official Agent SDK docs specify per-turn `usage` scope and cumulative `total_cost_usd` / `modelUsage` behavior, including resumed sessions and subagent coverage differences. `total_cost_usd` is a client-side estimate from a bundled/selected price table, not authoritative billing. It can support a clearly labeled estimate if the installed CLI JSON output exposes it and the version/scope is validated. | The exact installed `claude -p` JSON shape/version must be checked against PeerHub's adapter before relying on those SDK fields. Resumed-session cost is cumulative in current versions (before v2.1.277 it covered only that call), so a delivery metric needs a baseline/delta and version-aware handling. The Anthropic Usage and Cost Admin API provides org-level, time-bucketed usage/cost dimensions, with data typically appearing within five minutes; it requires suitable organization credentials and has no PeerHub session/delivery key, so it cannot establish per-session billing. [SDK cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking), [Usage and Cost API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api) |
| CX / Codex CLI | Current upstream `exec --json` code emits `turn.completed` usage from the latest accumulated total usage snapshot. The separate app-server protocol exposes `last`, `total`, and `modelContextWindow`, which can provide clearer per-turn/cumulative usage and context-window evidence. | Current PeerHub uses exec JSON, not that app-server telemetry. Context window and `last` usage therefore require an adapter/protocol change, then versioned integration tests. The completion event does not provide an authoritative per-call USD amount or prove provider-resolved model identity. [Codex exec JSONL exporter](https://github.com/openai/codex/blob/main/codex-rs/exec/src/event_processor_with_jsonl_output.rs), [app-server token usage schema](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/json/v2/ThreadTokenUsageUpdatedNotification.json) |

## Telemetry and evidence register

This register separates values PeerHub can observe from facts learned through
provider documentation and from facts that still require local validation or
cannot currently be attributed. “Measurable” means the adapter can parse the
value; it does not mean the monitoring CLI already displays it or that the
provider's semantics are interchangeable. The initial source review did not
query provider binaries; subsequent paid, version-pinned probes are recorded in
the linked CLI audit and are not release qualification.

### Common PeerHub measurements

| Item | PeerHub evidence available now | Coverage / interpretation |
|---|---|---|
| Session inventory, state, age, expiry, configured binding | Local session history and binding snapshots | Exact only within the selected stream/peer and retained-history scope. Configured profile/model/effort is not proof of provider-resolved execution. |
| Fresh/reuse counts | Writer-maintained per-stream/per-peer projections | Use only when completeness metadata permits; state selected scope. |
| Confirmed call and terminal outcome | Durable Bridge call/evidence receipts | Counts Bridge deliveries/calls, not internal provider model turns. Preserve success, failed, uncertain, and pre-start categories separately. |
| Latest operation duration and recent activity | `operation_elapsed_seconds` and bounded activity sample (currently at most 50 per peer) | End-to-end PeerHub operation time; includes local work and process startup. Activity is not an all-time durable call count. |
| Context occupancy/window | Session telemetry receipt schema exists, but current writer records null token/window/percentage with `source=unknown` | Not presently measured. Never substitute usage tokens or a public model's advertised maximum. |
| Quota observations | Existing quota probe/projection at peer/account-pool level | Shared-pool observation with source/freshness; no session allocation. |
| Durable provider usage | Adapter parses versioned, allowlisted usage candidates; the Bridge terminal transaction stores safe provider usage in the authoritative `session_telemetry` receipt | Delivery-scoped facts are eligible only when the provider/schema contract says delivery scope. Cumulative CC/CX/AG snapshots are displayed as unattributed observations. No replay/result snapshot is a monitoring source. |
| PeerHub all-time error totals | Durable delivery evidence exists, but activity is bounded and no complete all-time outcome projection is present | Requires a writer-maintained aggregate and migration/completeness treatment; do not scan all history on each monitor read. |

### Per-peer telemetry

| Peer | Parsed/measurable in current adapter path | Official information that can be looked up | Requires PeerHub change or local validation | Still unknown / not inferable |
|---|---|---|---|---|
| **CC (Claude Code)** | The adapter validates final JSONL `result.usage`; because exact resumed `claude -p` scope has not been validated against the installed CLI, the current contract conservatively declares it session-cumulative. Persisted usage is tagged `unattributed`, not differenced into per-delivery usage. | Agent SDK documentation describes its own `usage` as per-turn and `modelUsage`/`total_cost_usd` as potentially cumulative. That does not by itself establish the scope of PeerHub's CLI result across resumed invocations. Anthropic's Admin Usage/Cost API is organization-level and cannot attribute spend to a PeerHub session. | Future delivery-scope support requires a documented, versioned CLI source contract and fresh/resumed live evidence. Any reference price estimate requires exact resolved model and eligible delivery usage. | Actual invoice amount per PeerHub delivery/session; provider quota/spend attributable to one session; exact model/backend for hidden subagents; current exact context occupancy absent a verified field; inference-only latency. |
| **CX (Codex CLI)** | The adapter validates `turn.completed.usage`; current exec JSON counters are session-cumulative on resume and stored as `unattributed`. The app-server schema separately exposes last/total usage and `modelContextWindow`, but PeerHub has not adopted that protocol. | Official upstream exporter and app-server schema document these distinct fields. | A future app-server adapter may add last-turn/context-window evidence under its own versioned lifecycle and privacy tests. | Exact context occupancy/window through current exec adapter; invoice-level USD cost for a CLI delivery; provider-resolved backend/model for every internal turn; model-only latency; session-level quota allocation. |
| **AG (Antigravity CLI)** | The adapter validates result usage and currently records the versioned result counters as session-cumulative/unattributed. It never subtracts snapshots into delivery usage. | Official headless docs enumerate result usage, `duration_seconds`, and `num_turns`; current results do not document cost. | Safe per-step usage may be added only if a future stream contract binds it to the fenced terminal delivery. | Actual billed cost; provider quota attributable to one session; exact model-only latency; exact provider-side context occupancy absent a supported field. |

### CLI option and paid-canary findings

The installed CLI option matrix, exact probes, event/count observations, and
update procedure live in the [Peer CLI option audit](peer-cli-option-audit-2026-10-15.md).
These findings change the telemetry and session design as follows:

- The 2026-10-15 public ask canary passed 12 tests across CC/CX/AG; 3 skips
  were only the separately opted-in fresh/exact four-turn selector tests.
  Basic usage, native resume recall, writable sentinel creation, and process
  cancellation were exercised. Per-test wall times are saved in the option
  audit but represent whole test scenarios, not model-only latency.
- CC `--permission-mode plan` and AG `--mode plan` denied a live write request
  in direct probes while returning normal successful text results. Treat plan
  refusal as task-level denial, not necessarily process failure. Pin these
  modes only after PeerHub integration tests verify read/search capability,
  writable-mode transitions, and binding fingerprints. The legacy CC
  `permission-mode default` spelling is no longer accepted by this installed
  version and must not be copied into the adapter.
- Both CC and AG accept streamed JSON user input and produce per-turn terminal
  results in one continuous process; a small two-turn live probe confirmed
  native conversational recall. AG terminal usage and `duration_seconds` are
  cumulative over session turns, while the tested agent-response step reports
  per-turn usage. Separate-process AG `--conversation` resume also returned a
  cumulative total. Readers must not count these cumulative totals as one
  delivery without a verified baseline or complete per-turn event ledger.
- One paired, single-sample probe per CC/AG showed lower total wall time for
  two turns in one process than two explicit-resume processes (CC: 5.47 s vs
  9.78 s; AG: 10.41 s vs 19.32 s). This justifies a repeated benchmark and
  process-reuse design spike, not a default or product speed claim. Persistent
  child processes would change Bridge lifecycle, cancellation, per-delivery
  terminal framing, session serialization, and crash recovery; do not include
  them in the monitoring-table migration.
- AG streamed input is also a candidate replacement for its 30 KB Windows
  prompt-argument ceiling. Test it first as a per-delivery process with one
  stdin message and a closed stdin, independently of long-lived process
  reuse. This isolates prompt transport/output framing from process pooling.
- Local CLI versions were CC 2.1.299, CX 0.162.1, AG 1.3.5. These are
  environment measurements, not package compatibility policy. Exact
  per-delivery quota depletion and invoice cost remain unknown.

### Updated classification of unknowns

- **Can be settled by documentation:** AGY continuous-process cumulative result semantics; CC estimate-vs-billing distinction, subagent inclusion, and resumed-session cumulative scope/version caveat; Codex app-server availability of last/total usage and model context window.
- **Can be made observable with PeerHub changes:** AGY per-step token/time facts via allowlisted stream parsing; CC provider-estimated cost with explicit estimate/scope/version labels and baseline handling; CX context and last-turn usage if PeerHub adopts app-server telemetry; exact all-time PeerHub error totals through writer-maintained projections.
- **Still not knowable from public docs alone:** tool/subagent-heavy AG per-turn usage completeness; whether these observed versions represent other supported machines; exact invoice-level cost attributable to every PeerHub delivery across peers; model-inference-only latency; provider-side session validity without attempting resume; CX backend-resolved model per internal turn. These require more local/versioned tests, provider/account-specific evidence, or remain unavailable.

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

## Unified monitoring and telemetry design (pre-TDD)

This section resolves the expanded request to browse all useful monitoring
items, select groups, choose defaults, and retain measurements as evidence for
future quality comparisons. It extends the existing CLI/monitoring contract in
[`peerhub-cli-monitoring-inspection-2026-10-14.md`](peerhub-cli-monitoring-inspection-2026-10-14.md).
It is a design target only; no CLI, schema, or adapter implementation is
authorized by this document.

### Decisions and boundaries

1. `diag` remains the read-only snapshot command, `diag --live` remains the
   read-only watcher, `observation refresh` remains the explicit quota
   collector, and `monitor` remains the combined collection-and-display
   workflow. Do not create a second provider collector or change these side
   effects. The monitor snapshot reads one pinned read-only DB snapshot;
   provider work stays in its explicit refresh phase.
2. Monitoring exposes every **safe, measurable, attributable scalar or
   categorical fact** emitted by PeerHub or an allowlisted provider CLI. “All
   CLI info” does not mean persisting raw stdout, prompts, responses, tool
   arguments/results, environment variables, paths, credentials, account IDs,
   vendor session IDs, or opaque provider payloads. Unknown fields remain
   absent/null with coverage; they are never reconstructed from text.
3. Separate immutable measured evidence, writer-maintained projections for
   bounded totals/trends, and read-only report rendering. Store allowlisted
   evidence before deriving dashboard values. Derived averages/percentages
   carry denominator, scope, coverage, and source semantics. Display
   preferences do not belong in the telemetry database.
4. Reuse existing durable sources where sufficient: quota `observations`,
   Bridge delivery/evidence/session receipts, activity rollups, and provider
   usage already attached to terminal/response metadata. Add versioned
   telemetry facts only where current evidence loses provenance, fields,
   scope, or step granularity. Do not create a parallel source of truth or
   retain raw and normalized copies without a declared reason.
5. Do not add an accounting/budget/forecasting/routing subsystem. Optional
   reference-cost estimates are informational only: accept a provider-reported
   estimate when its CLI schema/scope is validated, or calculate an
   API-equivalent estimate from a versioned public price snapshot when exact
   model and token categories are known. Persist the source, URL, currency,
   effective date/version, calculation basis, and coverage. Do not fetch prices
   during monitor rendering. Label these as estimates, never billed amounts;
   never infer subscription spend/quota depletion or sum unlike peer estimates.
   Existing quota pace remains conditional pool-level evidence, not spend or
   per-request cost.

### MECE group catalog and default selection

Every displayed field has exactly one primary group. Summary cards may repeat
derived totals only when they link to the owning group's source and coverage.

| Stable group ID | Owns | Canonical source | Examples / exclusions |
|---|---|---|---|
| `overview` | Cross-group peer summary and highest-priority state | Derived from groups below; no independent counters | Peer state, retained/current session counts, last activity age, health warnings. |
| `execution` | PeerHub delivery and operation health | `bridge_deliveries`, `bridge_evidence`, bounded activity receipts | Confirmed deliveries, terminal success/failure, uncertain/pre-start counts, bounded end-to-end latency; not provider inference-turn count. |
| `sessions` | Retained PeerHub session lifecycle and reuse | `bridge_session_history`, `bridge_sessions`, session receipts and writer projections | Handle, state, configured binding, age/expiry, fresh/reuse, eligibility and completeness; never vendor IDs. Full inventory remains paginated. |
| `usage` | Provider-reported token and usage facts | Validated telemetry facts linked to a Bridge delivery | Per-delivery input/output/cache/thinking/total where emitted, CC model usage/cost estimate if validated, reporting coverage; not context occupancy or billed cost. |
| `context` | Runtime context occupancy evidence | Validated telemetry facts or terminal telemetry receipt | Occupancy tokens, matching window, percentage, source and age; never billing input or advertised model maximum. |
| `capacity` | Provider/account-pool quota observations | `observations`, resource-pool identity, existing quota projections | Used/remaining, reset, freshness, 7-day pace and conditional ETA when supported; never allocated to a session. |
| `runtime` | Selected configuration and adapter provenance | Session binding snapshots and safe adapter invocation metadata | Configured provider/model/profile/effort, CLI version when safely observed, adapter/schema version, selected-vs-reported distinction; no hidden-backend claims. |
| `coverage` | Data quality and completeness | Telemetry/session projection metadata and source freshness | Measured/missing/unsupported counts, stale/unknown sources, legacy gaps, counter resets, truncation and provenance. |
| `store` | PeerHub storage and diagnostic integrity | Read-only diagnostic report and projection audits | DB/schema status, projection integrity, bounded record/observation totals; full integrity scans remain explicit `diag integrity`. |

Default view: `overview`, `execution`, `sessions`, `usage`, `context`,
`capacity`, and `coverage`, in that order. `runtime` and `store` remain
available on demand. Defaults affect rendering only, never collection or
persistence. `--group all` includes every group.

### Quota, model, session, usage, and context relationships

The groups above are MECE ownership boundaries, not independent facts. The
monitor must preserve the following relationship graph while keeping each
measurement at its actual scope:

```mermaid
flowchart LR
    P[Peer / provider identity] -->|observed subject| Q[Quota pools]
    P -->|configured runtime| B[Profile and model binding]
    B -->|snapshot per generation| S[PeerHub session generation]
    S -->|dispatch belongs to generation| D[Bridge delivery]
    D -->|provider-declared delivery scope only| UD[Delivery-scoped usage]
    S -->|point-in-time snapshot| UC[Session-cumulative usage]
    S -->|time-bound evidence| C[Runtime context evidence]
    D -->|when explicitly reported| M[Provider-resolved model identity]
    UD -->|usage basis| E[Reference cost estimate]
    M -->|required identity| E
```

The `session-cumulative usage` edge is not a delivery-attribution edge. The
outer Bridge receipt may link that observation to the delivery that was in
flight when it was captured, but this is correlation only.

| Relationship | Cardinality and owner | Required attribution | Forbidden inference |
|---|---|---|---|
| Peer subject → quota pool | A PeerHub peer subject can expose zero or many provider-owned pools; provider account identity is distinct and is not collected by default. A pool can be shared by sessions, model choices, other clients, or another peer subject. `capacity` owns the observation. | Preserve peer subject, provider, stable pool reference/type when explicitly exposed, window, source, captured time, freshness, and completeness. Keep independent pool observations separate; show subject-to-pool membership only when evidence establishes it. | Never assume one peer equals one provider account or pool, allocate pool movement to a model/delivery/session, or sum unlike/overlapping pools. |
| Peer → configured profile/model | A peer can have many explicitly configured profiles; profiles are local selection policy, not provider entitlement records. `runtime` owns the declarative catalog and per-key source/provenance; `sessions` owns the selected binding snapshot. | Expose provider-qualified profile, selection mode, configured model/alias, effort, source layer, and observed CLI/adapter version. Snapshot the selected binding on a generation and effective per-delivery override where applicable. Keep configured model, provider-reported resolved model, and unknown resolution as separate fields. | Do not infer quota access, context window, model availability, or resolved backend from a profile/configured alias. Add entitlement or window to profile only when an explicit versioned provider source establishes that relation. |
| Binding → session generation → delivery | One generation has one compatibility binding snapshot and many deliveries; each delivery references at most one accepted generation. | Use PeerHub handles/generation and delivery receipts as local join keys. Keep effective mode, binding fingerprint, and timestamps at dispatch/terminal boundaries. | Do not join by vendor session IDs in monitor output or silently attach an unbound/legacy delivery. |
| Delivery/session generation → usage facts | A provider-reported delivery-scoped receipt attaches to its delivery. A session-cumulative receipt is a point-in-time observation attached to the generation; the delivery in flight is correlation metadata only. `usage` owns counters; `coverage` owns evidence quality. | Persist source/schema/version, generation, observation time, reported scope, and model-identity evidence. For session-cumulative sources, display the latest snapshot per generation/source as **unattributed cumulative usage**; never sum snapshots or difference them into delivery usage. Only a receipt whose provider contract explicitly defines delivery scope and binds the reported counters to that delivery is eligible for per-delivery metrics. | Correlation, monotonicity, or serialized local calls alone do not prove provider usage attribution: hidden/provider-side activity may exist. Do not infer call deltas, averages, or cost from cumulative snapshots; do not treat missing as zero or add overlapping cache/reasoning subsets. |
| Session generation/model → context sample | A generation may have many time-bound context samples; each sample may identify a reported model and matching window or remain unknown. `context` owns occupancy evidence. | Store sample time, source, reported model/binding, occupancy tokens, window, and freshness together. Only derive percentage from occupancy and window from the same compatible sample. | Do not use account quota, token billing usage, public advertised model limits, or `last` turn usage as current occupancy. A Codex app-server `modelContextWindow` proves a window value; `last`/`total` remain usage fields until live/versioned validation proves a context-percent formula. |
| Usage/model → reference cost estimate | An estimate is derived, never a source observation. Per-delivery estimates are eligible only for explicitly delivery-scoped provider usage and resolved model identity. | Require compatible token categories and provider-resolved model identity; retain estimate kind, currency, price source/URL, price version/effective date, calculation time, and usage coverage. A provider CLI estimate is shown only at its declared scope. Label API-equivalent estimates separately from provider-reported estimates. Same-basis averages require an explicit covered-delivery denominator. | A configured model/alias alone cannot select a price row. Session-cumulative snapshots are ineligible for per-delivery cost/averages and are surfaced as unattributed usage. Unresolved model means `null`/unattributed. Never call estimates actual spend/billing or infer subscription quota consumption. |

The navigable view should follow `peer → quota pools` and `peer → configured
profiles/models → session generations → deliveries → usage/context evidence`.
Profile inspection shows the effective local source layer and configuration;
it does not mutate profiles or claim provider entitlement. Quota is
a parallel peer/account-capacity branch, not a parent of the model/session
chain. Cross-links may join the branches only where a provider supplies an
explicit, versioned relationship. This preserves queryability without
pretending that a shared quota pool is session-attributable.

For `session_cumulative` providers, monitoring shows the newest provider
snapshot for each session generation and provider source, marked unattributed
to individual deliveries. Older snapshots remain audit evidence, not additive
rows. Per-turn averages and per-delivery reference costs exclude these
counters and report excluded/unattributed coverage explicitly.

CLI selection contract:

- `peerhub monitor --group usage --group sessions` selects exactly those
  groups and overrides defaults for that invocation. Repeating a group is
  idempotent. `--group all` cannot be combined with named groups. Unknown IDs
  fail before DB open or provider spawn.
- `peerhub monitor groups` prints the stable catalog, order, default status,
  and collection effects; it is read-only and works without a DB.
- `peerhub monitor defaults show`, `defaults set --group ...`, and `defaults
  reset` inspect/change display preferences without provider calls or Core DB
  writes. `--global` explicitly selects the global preference; otherwise
  changes apply to the workspace.
- Defaults resolve workspace `.peerhub/config/monitor.toml` over global
  `PEERHUB_CONFIG_HOME/monitor.toml` (or `~/.peerhub/config/monitor.toml`),
  over packaged defaults. Keep this separate from `models.toml`; validate IDs
  and atomically replace only this preference file. Show the effective source.
- Existing `--view`, `--json`, interval/cycle behavior, store selection, first
  committed snapshot, and refresh cadence remain intact. Group selection may
  not trigger extra probes. Each JSON frame carries `selected_groups`,
  `available_groups`, preference source, and coverage per selected group.
  Changes to section inclusion bump monitor/dashboard schema versions.
- TTY output uses group headers and concise summaries; JSON/NDJSON uses
  structured group objects, never scraped terminal text. `all` stays bounded:
  detail is paginated/capped with continuation and coverage. `session
  list/show` remains the complete retained-session inventory.

### Durable evidence model

| Layer | Contents | Rule |
|---|---|---|
| Existing facts | `bridge_deliveries`, append-only `bridge_evidence`, session history/receipts, quota `observations`, activity observations | Keep current sources authoritative; preserve certainty and idempotency. |
| Provider telemetry fact | Versioned allowlist from confirmed CLI result or safe terminal step, keyed to a Bridge delivery and local session handle when known | Extend the existing unique `bridge_evidence.kind=session_telemetry` receipt. Persist in the same fenced terminal transaction; dedupe by delivery and deterministic local step identity. Store provider kind, safe CLI version if available, adapter/schema version, source field, capture time, scope (`delivery`, `session_cumulative`, `step`), and numeric/enum values. No raw payloads or opaque IDs. |
| Coverage projection | By peer/provider/model/profile/window: eligible, measured, missing, unsupported, reset/non-monotonic counts; outcome counts; latest sequence and coverage start | Update transactionally with facts; legacy backfill declares exact coverage or incomplete. Never recount full history in monitor. |
| Usage/health rollups | Bounded-window or calendar-bucket sums/counts and latency distribution inputs for attributable facts | Writer-maintained, idempotent, with denominator and semantics version. Never aggregate across incomparable providers/models/fields. Percentiles name the bounded sample or approximation. |
| Monitor preferences | Selected default group IDs only | TOML config, not Core DB; no secrets or provider-state values. |

Physical schema decision for TDD: extend the existing append-only
`bridge_evidence.kind=session_telemetry` receipt. The accepted Oct. 13 PH-T091
contract already provides one immutable telemetry receipt per delivery and a
unique partial index; adding a second measurement ledger would contradict that
authority and duplicate delivery metadata. Keep `bridge_deliveries.result_json`
and response metadata as immutable API/replay snapshots, not as dashboard
sources. Monitoring reads the typed receipt and writer-maintained projections;
no new `bridge_telemetry_facts` table is added.

Version the JSON detail contract (`schema_version=3`) and validate it in the
Bridge writer. Preserve the existing session/context fields and add provider
identity, source kind, adapter/CLI/schema versions, capture time, configured
binding, metric scope, provider-native allowlisted counters, normalized
counters/formula version, and explicit per-field evidence/coverage states.
The configured model and provider-resolved model are distinct fields;
`resolved_model` remains null unless the provider reports it. Schema v2
receipts stay immutable and are interpreted according to their original
shape; readers must branch by schema version rather than silently relabel the
old `model` field.
When stream events are enabled, embed a bounded list of per-delivery safe
numeric step summaries keyed by deterministic local `step_index`/step type;
collapse repeated ACTIVE/DONE updates to one terminal step. Cap at 512 steps
and 64 KiB per receipt; on overflow preserve terminal result and typed
provider-receipt counters only when their declared scope supports them, mark
step coverage truncated, and include dropped count when known.
Never store raw lines, response fragments, tool inputs/outputs, subagent
payloads, opaque provider event/session IDs, or secrets. Missing usage remains
explicitly unknown. Existing `idx_be_session_telemetry_once(delivery_id)`
enforces one authoritative receipt; session facts and any writer projection
update in the same fenced terminal transaction. Projections are rebuildable
from these receipts and carry denominator/semantics version. Add only
projection tables required by bounded monitor queries; never recount all
evidence on monitor reads.

Canonical v3 detail shape (illustrative keys; no raw event object):

```json
{
  "schema_version": 3,
  "handle": "local-session-handle",
  "generation": 3,
  "observed_at": 1792047600.0,
  "last_input_tokens": null,
  "context_window": null,
  "context_pct": null,
  "source": "unknown",
  "window_reported_at": null,
  "configured_model": "configured-model",
  "resolved_model": null,
  "provider_kind": "ag",
  "captured_at": 1792047600.0,
  "adapter_version": "2",
  "provider_cli_version": "example-version",
  "configured_binding": {"profile": "ag.standard", "model": "gemini-3.8-flash-low", "effort": null},
  "usage": {
    "scope": "delivery",
    "source": "agy.result.usage",
    "reported": {"input_tokens": 12861, "output_tokens": 1, "total_tokens": 12862},
    "normalized": {"provider_input_tokens": 12861, "output_tokens": 1},
    "field_evidence": {
      "provider_input_tokens": {"state": "MEASURED", "scope": "delivery", "formula_version": null},
      "cache_read_tokens": {"state": "ABSENT", "scope": "delivery", "formula_version": null}
    },
    "coverage": {"state": "MEASURED", "reason": null}
  },
  "steps": [],
  "step_coverage": {"state": "ABSENT", "measured": 0, "dropped": 0}
}
```

All IDs, timestamps, values, bindings, and versions in this schema example are
synthetic placeholders, not production IDs or compatibility guarantees. In
this example AG `total_tokens` and `num_turns` are preserved in the native
  reported map, while no per-delivery projection is inferred from the cumulative
  result. Public response snapshots retain the legacy flat `usage` value only
  with explicit `usage_scope=session_cumulative` and
  `usage_attribution=unattributed`; the typed receipt remains the monitor source. The `source`
and state vocabulary must use the current adapter/source identity and
`Observation` semantics; the sample values are synthetic and are not a claim
that context was measured. Existing receipt consumers remain backward
compatible with v1 detail; v2 fields are additive and unknown fields are
rejected by the new writer validator.

The delivery receipt is the immutable measured fact and indexed Bridge-owned
telemetry authority; health assessments and monitor aggregates remain derived
layers. Reuse CoreStore's existing schema-version/migration path and Bridge
writer ownership. Never read vendor DBs. No provider metric is dual-written
to a second evidence table or copied into generic terminal evidence details.

Initial provider field allowlists:

| Peer | Durable safe facts | Scope and gate |
|---|---|---|
| CC | Input/output/cache-read/cache-write; per-model counters and estimated amount/basis only if present in installed JSON; turns/duration only if returned and validated | Keep current CLI `result.usage` as a conservative session-cumulative observation until exact resumed CLI scope is verified. SDK `usage` semantics do not prove the CLI result envelope's resume semantics. Estimates are never billed amounts. |
| CX | Current `turn.completed.usage`; if a supported app-server path is later adopted, `last`, `total`, and `modelContextWindow` distinctly | Current exec completion derives from a cumulative total snapshot on resumed threads. Preserve generation/time provenance; a local baseline/reset signal does not make it delivery-scoped. App-server is a separate adapter decision; never inspect private session files. |
| AG | Current result input/output/cache-read plus currently unpersisted thinking/total, `duration_seconds`, and `num_turns`; optionally per-step numeric usage/duration from stream-json | `num_turns` is a CLI conversation-turn count, not an inference-call count. Report duration as CLI-reported elapsed time, not model-only latency. Docs and the 2026-10-15 simple live probe show cumulative result counters across continuous turns and one exact fresh-process `--conversation` resume. Never derive delivery deltas from cumulative result counters; any future per-step field needs a versioned delivery-binding contract. Stream parser accepts only safe numeric fields and drops text/tool/subagent payloads. |

Keep provider-native counters and versioned normalized mappings side by side;
do not merge fields into universal “tokens used” until semantics are validated.
CC cache-read/cache-write and uncached input stay distinct. Missing CX/AG
counters are not zero. A context percentage requires a matching occupancy
numerator/window, not billing usage.

The first normalization must be lossless for current adapter facts. In
particular, CC currently combines `input_tokens`, cache-read, and cache-write
into one adapter `input_tokens` value after parsing; the adapter contract must
retain each original allowlisted field before deriving any optional total.
Historical deliveries for which only the combined value survives must keep
the component fields unknown; do not reverse-engineer them.
Define separate native counters and normalized fields such as
`provider_input_tokens`, `uncached_input_tokens` (only where provider
semantics prove it), `cache_read_input_tokens`,
`cache_creation_input_tokens`, `output_tokens`, `thinking_tokens`, and
`provider_total_tokens`. Every derived total records its formula/version and
never overwrites native values. CX's CLI `input_tokens` must not be relabeled
uncached without source evidence. Store `duration_seconds`/`num_turns` only as
provider-reported CLI result facts and do not interpret them as pure inference
duration or model invocation count.

### Improvement indicators

Persist facts for later PeerHub-version comparisons, but label results
observational rather than causal. Cohort by PeerHub version, adapter/schema
version, provider, configured model/profile/effort, time window, and scope.
Initial indicators are:

- delivery success, terminal failure, uncertain, and pre-start rates;
- fresh/reuse share and resume acceptance where resume was attempted;
- end-to-end operation latency median/p95 for a named peer/model/window;
- provider-reported per-delivery token fields and coverage, separately by
  provider/model/field semantics;
- optional CC estimated amount, separately labeled and estimate-basis/version
  specific;
- context occupancy only after direct-source validation, with matching window
  and age;
- quota freshness, observed headroom/pace, and missing-pool coverage;
- telemetry completeness, source availability, counter reset/decrease rate,
  projection completeness, and sample truncation.

Do not interpret token reductions alone as quality or speed improvement:
prompt/task, model/effort, cache, retries, and reporting coverage can change.
Show configuration cohorts and completeness beside trends.

### Cross-check disposition

The earlier session proposal, provider-document research, current adapters,
Bridge receipts, monitor cadence, and recorded peer-review findings reconcile
as follows:

- Retain bounded read-only snapshots and explicit collection boundaries; reuse
  quota observations and activity/session projections instead of adding a
  collector.
- Retain confirmed-call semantics, bounded history, explicit freshness and
  coverage, privacy allowlists, and one-snapshot reads.
- Expand “usage may be shown” into a durable, versioned, attributable telemetry
  fact and completeness contract. Existing response metadata alone is not a
  sufficient monitor ledger.
- Keep exact cross-provider cost, per-session quota, hidden provider model
  identity, and pure inference latency out of claims. CC estimates can be an
  optional provider-specific observation after local validation.
- Do not introduce an SDK, OpenTelemetry exporter, or vendor DB parser for
  this slice. Existing CLI JSON and SQLite writer contracts are the narrowest
  adequate path; revisit only if a named metric cannot be obtained there.
- Group selection affects rendering only. It does not alter acquisition,
  retention, or collection cadence.

The initial document cross-check was source-backed. Follow-up paid probes and
their precise scope are recorded in the linked CLI option audit. CX, CC, and
AG cross-reviews were subsequently run; their first two rounds remain
conditional, then the final amended packet received explicit approval from CX,
CC, and AG. The unanimous vote authorizes local tests-only RED as specified
below; it does not authorize production changes before their RED tests exist.

### Legacy-branch research reconciliation

The audit used `origin/legacy/v0-main-final` at
`57a137cd6a0cc7e89124ea2b87b72995087627a5`, including the ratified quota
research, the old diagnostics architecture, and telemetry contract/tests.
These are design evidence, not a directive to restore the old implementation.

- **Adopt:** source-specific collection followed by loss-aware normalization
  and a read-only renderer; immutable measured evidence with rebuildable
  writer projections; explicit provenance, observation/capture time, scope,
  freshness, and completeness; and a strict distinction between observations
  and health/admission assessments. Current `Observation` already provides
  `MEASURED`, `ABSENT`, `UNAVAILABLE`, `ERROR`, `STALE`, and `UNKNOWN`; reuse
  these meanings for monitor coverage rather than inventing zero-valued
  substitutes. Keep quota observations as pool-scoped evidence, not request
  cost or session allocation.
- **Adopt as provider-field rules:** Codex `input_tokens` is inclusive where
  the inspected source says so; cached input is a subset and must not be added
  again. Preserve provider-native inclusive input/output and subset counters
  (cache and reasoning) separately. A CLI event name does not prove
  per-delivery scope. Distinguish a complete ask from its internal model/tool
  steps; “last response usage” can undercount an ask. Cumulative values may
  only be differenced against an exact same-session/generation pre-dispatch
  baseline, with reset, compaction, concurrency, retry, model-switch, and
  missing-baseline cases left unknown or explicitly partial. Source
  disagreements and null/unknown scope must not be silently summed.
- **Adopt as metric boundaries:** provider token counts, runtime context
  occupancy, provider quota-pool usage, and billed/provider-estimated cost are
  separate measures. A public context limit does not measure current
  occupancy; API billing does not establish subscription quota consumption;
  provider cost estimates remain estimates with source/version/basis.
- **Do not port legacy collectors:** statusline-log scraping, private vendor
  state databases, account identifiers, private account/credit inventory,
  or mutation commands hidden behind diagnostics. Those old source paths are
  outside current PeerHub's safe CLI telemetry boundary. Credit consumption
  was separately audited in legacy and remains outside read-only monitoring.
- **Use legacy claims with their adjudication:** the ratified CX research
  records unsupported claims and unresolved runtime behaviors; raw AG/CX/CC
  discussion drafts are not independent proof. Historical cost/token logs
  with null scope or incomplete coverage are not a trustworthy backfill
  denominator. Backfill only from attributable current Bridge evidence and
  mark historical coverage incomplete otherwise.

Relevant legacy references: `docs/design/quota-efficiency-RATIFIED-cx-astra-2026-09-13.md`,
`docs/design/HUB-REPLACEMENT-GAP7-DIAGNOSTICS-2026-08-24.md`,
`docs/history/from-engram-repo/protocol-docs/engram-peer-governance/diag-telemetry-architecture.md`,
`peerhub/telemetry/contract.py`, and its immutable evidence/projection tests.
The current architecture has since moved: apply the semantics above through
current adapters, `bridge_evidence`/session receipts, and CoreStore migrations;
do not reintroduce legacy private-state collectors.

### TDD entry gate

The physical-schema decision is closed by this addendum: reuse the current
unique receipt and extend its versioned detail. Before implementation, prepare
version-tagged golden fixtures from documented schemas and locally observed
CLI output. Fixtures use synthetic IDs and secret sentinels. Provider tests
stay opt-in and disclose token/quota risk; deterministic tests never spawn
provider CLIs or access the network. Add these contracts to existing USM-01–08:

- **USM-09 — Full group catalog:** every exposed field has one primary group
  and catalog metadata (ID, title, order, fields, default, collection effect).
- **USM-10 — Group selection:** exact named selection, idempotent duplicates,
  exclusive `all`, invalid input fails before DB/provider work, and selection
  never changes collection.
- **USM-11 — Group preferences:** packaged/global/workspace precedence;
  show/set/reset; invalid TOML/IDs fail closed; atomic writes preserve files on
  failure and touch neither Core DB nor providers.
- **USM-12 — Output schema:** groups, coverage, source, status, and selected
  defaults appear in stable JSON/NDJSON; bump versions when inclusion/meaning
  changes; one snapshot contract is shared where applicable.
- **USM-13 — Safe capture matrix:** CC/CX/AG fixtures persist only allowlisted
  numeric/enum facts with source, scope, timestamp, binding, and versions.
- **USM-14 — Atomicity/idempotency:** terminal and telemetry facts commit or
  roll back together; retries cannot duplicate delivery or step facts;
  uncertain calls acquire no fabricated usage.
- **USM-15 — Counter semantics:** fixtures cover per-turn/cumulative, resume,
  model change, decrease/reset, omissions, and partial reports; invalid deltas
  remain unknown and record a coverage reason.
- **USM-15a — Inclusive/subset arithmetic:** Codex inclusive input and output
  remain unchanged when cached/reasoning subset fields are present; derived
  totals declare formulas and never double-count subsets. Complete ask usage
  is distinct from the last internal response; duplicate/retried steps cannot
  inflate it.
- **USM-15b — Cumulative baseline non-attribution:** exact session, generation,
  model, and monotonic pre-dispatch baselines still do not make a cumulative
  provider observation delivery-scoped. Persist/reset-diagnose those snapshots
  as generation/time evidence, but never subtract them into delivery usage.
  Only a separate, versioned provider source that natively binds usage to the
  fenced delivery may qualify for delivery metrics; missing or invalid evidence
  is unknown/partial.
- **USM-16 — Step dedupe/privacy:** repeated ACTIVE/DONE events count once;
  prompts, responses, tool payloads, paths, raw JSON, vendor IDs, and credentials
  never persist or render. Per-row size/step caps preserve terminal results and
  report dropped-step coverage instead of silently truncating.
- **USM-17 — Context semantics:** percentage is null if occupancy/window is
  missing, stale, or mismatched; usage and public model maximum never substitute.
- **USM-18 — Coverage denominator:** missing/unsupported/failure differs from
  zero; reports include eligible, measured, missing, stale, truncated counts
  and named time/scope.
- **USM-19 — Projection consistency:** facts update rollups exactly once in
  the same transaction; explicit audit catches drift; monitor never recounts
  all history.
- **USM-19a — Receipt schema/immutability:** extend the existing
  `session_telemetry` detail with schema v3 under the current unique
  delivery-receipt index; a repeated terminal callback cannot create another
  receipt or mutate the first. No parallel provider-facts table or duplicate
  evidence kind exists. Replay `result_json`/response metadata remains
  immutable API output and is not queried as the monitor ledger. Migration
  rollback preserves the prior user-version and all evidence if DDL/backfill
  fails.
- **USM-20 — Improvement cohorts:** versions, provider, binding, and coverage
  differences remain visible and cannot masquerade as regressions.
- **USM-21 — Bounded all-groups view:** large stores stay within page/window
  query bounds; `all` does no provider work or N+1 reads; history drills down
  through cursors.
- **USM-22 — Legacy completeness:** backfill marks exact coverage only where
  evidence proves it; unknown history stays null/incomplete; upgrade is atomic.
- **USM-23 — Partial group failure:** unavailable groups carry reason while
  other groups remain usable; status and exit code follow diagnostic contracts.
- **USM-24 — Indicator validity:** config/version/provider/window cohorts are
  separate; no cross-provider token or cost averages appear.
- **USM-25 — Preference/backup safety:** config stores group IDs only; backup
  behavior for the new preference is explicit; no secrets/account/path data.
- **USM-26 — Provider option matrix:** argv snapshots assert current and
  proposed commands for each peer, read-only/writable, fresh/resume, model,
  effort, OS, and supported CLI versions. The retired CC `default` permission
  mode and unsafe/bypass flags cannot reappear unnoticed.
- **USM-27 — Read-only plan modes:** CC/AG plan invocations can read a known
  workspace fixture, cannot create or modify a sentinel, preserve output
  parsing and resume recall, and are distinguished from task-level denial
  even when CLI exit is zero. Writable sentinel tests still pass. Mode is in
  the session binding/fingerprint and mode changes cannot silently resume.
- **USM-28 — AG stdin stream transport:** one JSON user message through
  `--input-format stream-json --output-format stream-json` has one associated
  terminal result, correct conversation ID and session receipt, bounded line
  sizes, and canonical response/usage decoding. Malformed/truncated input or
  output remains uncertain as specified by the delivery certainty contract.
  Prompts over the current Windows argv limit pass through stdin without
  leaking raw prompt text to telemetry/logs. This can ship independently from
  persistent child processes.
- **USM-29 — Stream usage scope:** CC results are attributed to the current
  input turn only for versioned output contracts that prove this; AG
  `step_update` facts are validated as per-step and terminal result counters
  as session-cumulative for the versioned continuous/resume paths that prove
  this (the current live evidence covers `agy 1.3.5` and simple asks).
  Duplicate ACTIVE/DONE events, multiple agent responses, tools/subagents,
  omitted usage, reset, retry, and model-switch fixtures cannot double-count
  or invent complete ask usage.
- **USM-30 — Persistent-process experiment gate:** before implementing process
  reuse, run at least 20 randomized matched pairs per peer/profile; require
  at least 15% lower median end-to-end time with a paired confidence interval
  excluding zero, no p95 regression over 5%, and no more than 5% higher
  attributable token usage. These are proposed go/no-go thresholds, not
  observed optima. Process lease/serialization, crash-to-explicit-resume,
  cancellation, per-delivery commit, and replay/idempotency tests must all pass
  before enabling reuse; otherwise keep one child per ask.
- **USM-31 — Evidence freshness:** all live probe entries include exact local
  CLI versions, profile/model, invocation mode, request count, wall-time
  definition, results, skips/failures, and measurement caveats. Raw prompts,
  outputs, paths, session/vendor IDs, credentials, and request IDs never enter
  committed evidence. A new CLI version appends a new run without overwriting
  prior results.
- **USM-32 — Live compatibility matrix:** for each changed provider path, an
  explicitly opted-in live test records version/model/profile, invocation,
  request count, expected recall/output, observed usage scope, elapsed-time
  definition, and sanitized outcome. Cover one fresh call, native resume, and
  exact prior-handle selection after a separate fresh session. Add plan/read-only
  or stream transport cases only for adapters whose argv/parser changes. A
  live pass supplements, never replaces, deterministic fixtures; a skipped or
  unavailable provider is reported as unverified, not pass.
- **USM-33 — Fault and ambiguity matrix:** deterministic fake-process tests
  inject missing executable, auth/rate-limit-shaped nonzero exit, zero-exit
  refusal, malformed/empty/truncated JSONL, unexpected event order, duplicate
  terminal events, partial usage, output/line cap, timeout, cancellation,
  process-tree exit, resume-not-found, session-ID mismatch, model/mode change,
  concurrent use, compaction/reset/decrease, and crash between terminal result
  and commit. Assert exact delivery certainty, no fabricated usage, no leaked
  sentinel secrets, child cleanup, and atomic rollback/idempotent retry. Never
  deliberately exhaust real quota or invalidate a real authentication state.
- **USM-34 — Live usage-scope discriminator:** the paid AG probe uses a fresh
  two-turn stream process and a separate exact `--conversation` resumed
  process, compares each `step_update.usage` with cumulative result usage, and
  verifies session identity and monotonic/reset behavior. For CC and CX, live
  resume captures first and second result usage to classify per-turn versus
  cumulative semantics. The 2026-10-15 AG probe already supplies the documented
  2-turn stream + 2-turn exact-resume sample for `agy 1.3.5`; it is evidence,
  not a substitute for testing a changed PeerHub parser. On an unchanged
  binary/path, reuse that evidence rather than spending quota again. If CC/CX
  usage parsing or CLI version changes, budget 2 asks per affected peer (one
  fresh, one resumed); if AG stream parsing changes, budget 3 asks (two in one
  stream process, one exact resume). Tool/subagent-heavy, refusal, and
  multi-step behavior is first covered with sanitized fixtures; paid variants
  are required only when supported parsing behavior changes. A scope
  observation alone does not authorize delta accounting; USM-15b remains the
  gate. If a full 4-ask exact-selection matrix is already required for the
  same peer and unchanged run, its initial fresh/resume pair satisfies the
  2-ask CC/CX scope check; do not add those calls again.
- **USM-35 — Failure-isolated monitor:** inject one provider/section error,
  corrupt optional preference config, stale quota data, and incomplete history;
  unaffected groups remain readable from one snapshot, unavailable values
  retain reason/freshness, and exit status follows the CLI diagnostic contract.
- **USM-36 — Cumulative attribution isolation:** CC/CX/AG fixtures assert the
  declared scope. For `session_cumulative`, store a generation/time observation
  correlated to the in-flight delivery, render only the latest snapshot per
  generation/source as explicitly unattributed, never sum or difference
  snapshots, and exclude it from per-delivery averages/cost. Delivery usage
  requires a provider-contract delivery-scoped receipt bound to that delivery.
  The sole binding key is the outer `session_telemetry.delivery_id` supplied
  by the currently fenced Bridge terminal callback; the adapter contract must
  declare delivery scope for that provider/schema. `session_cumulative` rows
  carry the same outer ID only as a correlation link and must render a
  different attribution state. No second per-delivery fact table or write path
  is allowed; derived monitor rollups read this receipt only.
- **USM-37 — Resolved-model cost gate:** configured profile/model alias alone,
  provider fallback, missing or ambiguous resolved model, incompatible token
  categories, and cumulative usage all produce null/unattributed per-delivery
  cost. A deterministic dated public-price fixture calculates only when exact
  provider-resolved model identity and compatible delivery usage are both
  evidenced. No test performs network lookup or presents estimates as billing.
- **USM-38 — Peer/profile/quota relationship integrity:** profile inventory
  exposes provider-qualified ID, selection mode, configured model/alias,
  effort, effective source/provenance, and runtime version when observed;
  session generation retains the selected binding. Reuse the existing
  observation model: `subject_ref` is the PeerHub peer subject,
  `resource_pool_ref` points to the globally identified pool, and registered
  pool metadata carries provider/kind; do not add a parallel pool registry.
  Preserve each observation's kind, source, window, effective/captured time,
  freshness, and coverage. Shared pool observations remain linked but are not
  double-counted. Tests
  prove profile edits/selections do not infer or mutate entitlement, quota
  allocation, model availability, or context window; aliases never become
  resolved models. No provider account identifier is collected by default.
- **USM-39 — Context dependency integrity:** context occupancy, reported
  context-window size, usage counters, and quota are separately typed and
  sourced. A percentage requires occupancy and window from compatible,
  time-bounded evidence for the same reported model. Codex `last`/`total` usage
  and a profile model maximum cannot be substituted for occupancy. Missing,
  mismatched, or stale evidence remains explicitly unknown.
- **USM-40 — Receipt transport/privacy boundary:** the adapter may return a
  bounded internal `provider_telemetry` candidate, but Bridge validates and
  extracts it into the single `session_telemetry` receipt in the fenced
  terminal transaction. The internal field is removed before result digest,
  `bridge_deliveries.result_json`, replay/public response, and response
  metadata. Preserve existing generic `usage` compatibility behavior, but
  prove monitor reads only the typed receipt. Duplicate/conflicting terminal
  callbacks cannot replace the first committed telemetry fact; failure rolls
  back terminal and receipt atomically. Secret/raw payload sentinels never
  appear in persisted or rendered data. A RED assertion verifies the receipt's
  `delivery_id` exactly equals the terminal callback's current delivery ID,
  the runtime/provider/schema contract permits the declared scope, and no
  other table or metadata field stores normalized usage for monitor accounting.
  A malicious/unexpected provider-supplied delivery ID is ignored or rejected;
  it can never override the fenced Bridge `did`. Invalid/mis-scoped candidates
  fail closed to a bounded `UNKNOWN` coverage reason with no token counters or
  raw candidate; they do not fail the user's otherwise-valid terminal result.
  A receipt persistence/transaction failure does fail the transaction and
  rolls back terminal plus receipt together. Coverage projections retain
  bounded validation-reason counts by provider/schema/version and expose their
  measured/eligible ratio, so schema drift cannot silently erase coverage.

#### Normative precedence for the new dependency requirements

The following final decisions supersede any earlier wording in this document
that proposes deriving delivery usage by subtracting cumulative snapshots or
implies that the local absence of concurrent calls proves provider attribution:

1. Provider scope is authoritative. `delivery` requires provider documentation
   or a versioned provider contract that defines per-invocation/per-turn
   counters. Bridge binds that terminal invocation's candidate to the exact
   current `delivery_id` in the one typed receipt; there is no provider-supplied
   delivery ID to trust. `session_cumulative` is only a
   generation/time observation; no subtraction, per-delivery average, or
   per-delivery price is allowed for current CC/CX/AG sources.
2. The monitor shows the latest cumulative snapshot per session generation and
   provider source as `unattributed`; older snapshots are audit evidence and
   never add to totals. A correlated Bridge delivery is a navigation link, not
   an accounting attribution. Report measured, absent, unsupported, and
   unattributed coverage separately so omitted values do not disappear.
   `session_telemetry` is the only immutable usage-fact source; summary rollups
   are rebuildable projections and must not become another receipt ledger.
3. A price estimate based on public prices requires both explicit
   delivery-scoped usage and the provider-resolved model ID evidenced for that
   same delivery. Configured profile/model aliases, model catalogs, and
   fallback assumptions are insufficient. Unknown resolution yields null.
4. Profiles stay declarative model-selection configuration. Profile list/show
   reuse the existing provider-qualified profile resolver and expose
   selection mode, model/alias, effort, per-key source layer, and CLI/adapter
   version where known. Session history snapshots the binding used. Do not
   expand `models.toml` with quota/account entitlements absent a versioned
   provider source. Quota pool
   observations are an independent capacity branch; never encode inferred
   quota/model/context entitlement in `models.toml`.
5. Context window and current occupancy remain distinct. The Codex app-server
   protocol currently exposes `last`, `total`, and nullable
   `modelContextWindow`; it does not make `last`/`total` occupancy values.
   The Gemini CLI headless reference documents result-level aggregated stats
   and per-model token breakdowns. Anthropic's current monitoring reference
   documents optional OpenTelemetry usage/cost signals, which require explicit
   opt-in configuration and do not establish a PeerHub subscription quota.
   Use these as schema discovery only; validate provider scope against the
   exact supported local CLI versions and sanitized live evidence before
   changing a parser.
6. Candidate validation is fail-closed but does not block an otherwise valid
   user response: unsupported provider/schema/scope, malformed candidate,
   provider-supplied delivery ID, or invalid counters persist no candidate
   values and map to a bounded `UNKNOWN` reason in the receipt. A database or
   projection failure remains an atomic transaction failure and rolls back
   terminal plus receipt; these two error classes are tested separately.
   Coverage queries and tests count each bounded rejection reason by
   provider/schema version and report eligible versus measured coverage; do
   not invent an alert threshold before a representative baseline exists.

Research sources checked 2026-10-15: [Codex app-server token usage schema](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/json/v2/ThreadTokenUsageUpdatedNotification.json), [Gemini CLI headless output](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md), and [Claude Code monitoring](https://code.claude.com/docs/en/monitoring-usage). These sources document available signals and shapes; PeerHub's local fixtures and versioned CLI contract determine what is safe to persist.

### Dependency-addendum consensus and TDD boundary

CC, CX, and AG unanimously approved USM-36–40 and the normative precedence
above on 2026-10-15, including exact fenced-delivery binding, spoofed-ID
rejection, non-blocking fail-closed telemetry validation, atomic receipt
failure, and reason-level coverage. The initial implementation began with
tests-only RED. Subsequent adversarial review corrected CC usage to
session-cumulative/unattributed, made receipt normalization authoritative,
preserved valid partial native counters, and tightened CI/release evidence
gates. Current tests and review status are recorded below; the broader monitor
reader/projection roadmap remains future work.

After the contracts and migration/index/query budgets are reviewed, implement
in this order: (1) versioned field semantics and golden fixtures, (2) selected
typed telemetry receipt and atomic writes, (3) writer projections and exact
coverage, (4) bounded group reader/JSON, (5) text rendering and preferences,
(6) adapter permission/session changes after their canary contracts, and (7)
the separate AG stdin transport slice after its RED tests exist. Run live
canaries only after the implementation under test is present. Persistent CC/AG
child-process pooling stays behind USM-30 and is not a prerequisite for
complete monitoring. Each slice updates command inventory,
schemas, evidence register, and release notes together.

### TDD handoff status (2026-10-15; initial-plan notes below are historical)

CC, CX, and AG unanimously approved the refined A1–A4 design and exact TDD
boundaries after three compact adversarial rounds. The approval is scoped to
the decisions below; it does not imply completion of the broader monitor CLI.
The current work has passed focused GREEN for the A1–A4 tests: 107 tests passed
across receipt, adapter/provider usage, monitor coverage, extension
architecture, and M2/M3 isolation. Pyright reports zero errors. Full CI and
the peer review limitations recorded below remain before release qualification.

| Decision | Normative contract | RED/GREEN evidence now present |
|---|---|---|
| A1 — atomic initialization | Do not introduce another extension-version runner. Keep DDL, registry seed, and bridge registry initialization in the existing fenced transaction. | Migration fault injection asserts DDL/data rollback, unchanged `PRAGMA user_version`, and no surviving minted evidence; an ordinary reopen must not enter historical recount. |
| A2 — usage denominator | Coverage counts eligible terminal (`certainty='TERMINAL'`) deliveries through a `LEFT JOIN` to the unique telemetry receipt. Missing receipt is `missing`, never synthetic `UNKNOWN`, `ABSENT`, or zero usage. No session snapshot means no receipt. | Scope filters exclude nonterminal/other-peer/other-stream deliveries; terminal legacy delivery without receipt increments `eligible` and `missing`. |
| A3 — legacy rebuild | Historic projection repair occurs only when absent/legacy, not on ordinary open. One indexed scope scan returns per-generation and scope counts; no per-session/per-delivery query loop. Incomplete attribution remains unknown; a nonempty scope containing only pre-start deliveries is known zero, matching the previous recount semantics. | Representative fixture: 5,000 sessions × 10 deliveries (50,000 deliveries); at most two scope reads, `EXPLAIN QUERY PLAN` must use `idx_bd_scope_generation`, ordinary open fails if the recount function is reached, and a pre-start-only scope remains complete known zero. |
| A4 — provider semantics and model dependency | Keep provider-native `reported` fields lossless. Share one allowlisted normalizer between parser and persistence. Use provider-qualified common names only where semantics are documented. Keep configured profile/model separate from provider-resolved model; unresolved stays null. Shape changes require receipt `schema_version=3`; do not keep an ambiguous `model` alias. | CC cache/input bucket distinctions, CX/AG native fields, candidate revalidation, configured-vs-resolved model tests, and schema-version assertions. Unknown or unsupported semantics stay unknown; no cost is inferred. |

The relationship remains **quota pool → provider/model catalog → configured
profile → session-generation binding → accepted delivery → usage/context
receipt**. Each edge carries its own source, scope, time, and confidence. A
profile is selection policy; it cannot establish entitlement or context
window. A session binding snapshots configured values; it cannot establish
provider resolution. A cumulative usage receipt is an observation and remains
unattributed to a delivery. Usage coverage uses delivery eligibility only for
the denominator and does not relabel cumulative usage as per-call usage.

CC, CX, and AG separately reached unanimity on schema v3 after comparing a
version bump with a deprecated `model` alias. All selected v3: keeping the
alias under v2 could make existing readers interpret configured selection as
provider-resolved execution. CX's first two code-review attempts failed with
the adapter's `AuthRequired` error for its tldraw OAuth MCP transport; a
no-tools schema vote succeeded, but a final code-diff vote was unavailable in
this round. CC and AG approved the final summarized diff.

The new shared normalizer and read-model module are classified as M1 and are
covered by the extension import allowlist and M2/M3 isolation gate. The design
is still a partial implementation: grouped monitor presentation, coverage
rollups/projections beyond the read-only coverage aggregate, selectable group
preferences, complete profile/quota/context drill-down, and GitHub status-check
migration remain outstanding. The live evidence below is version-bound
support for adapter contracts, not release qualification or statistical
performance proof.

The sequence that follows is the remaining design roadmap, not a claim that
those slices have already been implemented. Keep each one independently
reviewable; RED tests may fail locally, but remain uncommitted and unpushed
until their slice reaches GREEN.

0. **Release/CI evidence integrity (PH-T074 and G6 wiring):** RED then GREEN.
   Add/extend `tests/communication/e2e/test_live_gate_tool.py` so dirty trees
   are refused with exit 2 before provider spawn, including when the removed
   legacy `--allow-dirty` flag is supplied; no JUnit/G3 stamp may be written.
   Retain a clean-tree positive control that stamps exact HEAD. In
   `tests/communication/soak/test_soak_ci_wiring.py`, invert the assertion
   that currently requires job-level `continue-on-error: true`; assert
   correctness failures remain job failures, evidence upload stays
   `always()`, and the release graph still treats G6 as non-blocking. Dirty
   canary runs are refused too; do not add an unstamped dirty bypass.
   **GREEN owner:** remove `--allow-dirty` from `tools/live_gate.py`, enforce
   dirty-tree refusal for every stage before provider spawn, and remove the
   job-level `continue-on-error` from `.github/workflows/soak.yml`. Keep the
   artifact upload `if: always()` and G6 outside publish dependencies.

1. **Provider parser/schema fixtures:** tests only, before receipt migration.
   Store sanitized golden input fixtures under
   `tests/communication/fixtures/telemetry/`; record the accepted AG event and
   numeric-field allowlist in this design contract. Finalize it before tests
   whose oracle depends on AG step fields. For the documented AG
   `stream-json` contract, accept only `init`, `step_update`, and terminal
   `result` envelopes; for a step accept `step_index` (nonnegative integer),
   `state` (`ACTIVE`/`DONE`), `step_type` (the observed enum
   `user_input`/`agent_response`/`tool`/`checkpoint`), `duration_seconds`
   (finite nonnegative number), and `usage.{input_tokens,output_tokens,
   thinking_tokens,cache_read_tokens,total_tokens}` (nonnegative integers).
   Capture usage only when present on completed steps, preserve step index and
   type, and do not sum across steps until a provider-specific formula is
   proven. Treat `conversation_id` as transient correlation only. Discard
   `text_delta`, `tool_name`, `tool_info`, `subagent_info`, `cwd`, `tools`,
   and all unknown/raw fields; unknown enum/value shapes become incomplete
   coverage, never a parse crash or fabricated zero. The official [AGY
   headless stream-json contract](https://www.agy.dev/docs/cli/headless/#streaming-json)
   documents these event and field shapes; the version-pinned sanitized live
   fixture remains the test oracle. Cover CC/CX output
   fixtures and AG schema/parser behavior without persistence, including
   missing, duplicate, reordered, truncated, and oversized events. This
   establishes the parser contract needed by the receipt schema.

2. **Receipt contract:** USM-13, USM-15/15a/15b, USM-16/17/18, and USM-19a.
   With schema finalized, add synthetic CC/CX/AG golden fixtures. Assert exact native fields,
   scope, null/unknown behavior, secret rejection, deterministic step
   collapsing, byte/step caps, immutable one-receipt uniqueness, migration
   rollback, and counter-baseline validity. Do not depend on a live CLI.
3. **Write atomicity and projections:** USM-14, USM-19, and USM-22. Exercise
   Bridge terminal transaction/fencing, retry/replay, projection idempotency,
   incomplete legacy backfill, and an audit/rebuild oracle. Keep the existing
   delivery/result snapshot contract unchanged.
4. **Read model and output:** USM-01–08, USM-20–24, and USM-35. Prove one pinned
   snapshot, bounded query counts, explicit denominators/freshness, no N+1,
   and no provider work. Group rendering consumes that read model rather than
   adding another collector.
5. **Preferences and CLI:** USM-09–12 and USM-25. Validate precedence,
   atomic TOML writes, invalid-input fail-closed behavior, stable JSON/NDJSON,
   and read-only `show`/`reset` behavior without DB or provider writes.
6. **Adapter contracts and live-boundary RED tests:** USM-26/27/28/29/31–34.
   First derive golden JSONL/CLI fixtures from sanitized captured shapes and
   write argv/parser and exception tests; then implement the AG transport in
   its own RED/green slice. Add opt-in canary definitions, but run a canary
   only after the matching adapter slice is implemented. Preserve current
   public ask defaults until that slice passes.
7. **Performance experiment only:** USM-30 is a separate decision gate, not a
   prerequisite to monitoring. Run randomized matched pairs in a clean
   workspace with identical task/model/profile, capture per-turn and
   end-to-end metrics, and apply its stated thresholds before enabling any
   persistent process reuse. The 2026-10-15 two-turn smoke comparison is not
   enough to pass this gate.

TDD exit requires the deterministic suite and migration checks to pass, a
reviewed trace from each USM contract to its RED test, documented query/receipt
bounds, and updated evidence/CLI references. Only then should implementation
proceed to the next slice. Paid probes remain explicitly opt-in and their
request count and quota risk must be disclosed at invocation.

Each slice's RED phase is local and uncommitted; push, merge, or required CI
starts only after that slice's GREEN changes make the deterministic suite pass.
For slice 0, GREEN owns both `tools/live_gate.py` and `.github/workflows/soak.yml`
as named above. For extension architecture, no new M2/M3 module is assumed in
this design. If implementation introduces one, that same slice must register
it in `tests/continuity/test_m2_m3_exit_gates.py` and classify its imports in
`tests/communication/architecture/test_arch_extension_graph.py`; if it reuses
existing modules, verify those architecture gates remain green. Trace each
PH-T074/G6 criterion alongside USM contracts to its RED test.

### Live and exceptional-case test plan

Live tests answer only questions a fixture cannot settle: installed CLI argv
compatibility, native session continuity, actual output/event shape, and the
provider's observed counter scope. All parsing, failure, privacy, migration,
fencing, and certainty rules are tested deterministically with fake
executables/fixtures; the default CI suite never spends provider quota. Tests
must use a temporary isolated workspace, synthetic marker prompts, bounded
timeouts/output, and cleanup in `finally`, and must assert the persistent
workspace digest before/after read-only cases.

| Stage | Required cases | Pass oracle / exception handling | Quota policy |
|---|---|---|---|
| Preflight (no provider call) | Supported executable/version, help/argv snapshot, config/profile resolution, sandbox/permission argument, session fingerprint | Fail before spawn for invalid profile/options; unsupported versions are explicit `UNVERIFIED`; never print credentials | No quota |
| Basic live ask (CC/CX/AG) | Fresh handle, text success, returned usage when available, one terminal result, cancellation after confirmed start | Correct peer/session binding and certainty; absent usage remains unknown; zero-exit refusal is not counted as task success; worktree is cleaned | One bounded ask per changed peer path |
| Resume and selection (CC/CX/AG) | Fresh then native default resume/recall; create another fresh session; explicitly select first handle and recall its marker | Same native session identity on resume, generation changes only on fresh, exact selection unaffected by intervening session; resume-not-found/mismatch fails closed | Separate opt-in; 4 asks per peer when full matrix is needed; first fresh/resume pair also covers USM-34 scope checks. Don't rerun on unchanged path when version-pinned evidence still applies |
| Read-only/write mode (CC/AG when changed) | Read a known fixture, attempt sentinel create/modify/delete in plan mode, then authorized writable sentinel operation; repeat resume with mode fingerprint | Plan cannot mutate; writable control succeeds; CLI exit 0 alone is not the oracle; no other repo files change | Opt-in, minimum calls for both modes |
| AG stream and usage scope (AG when changed) | Two user messages in one stream process; one exact resume in another process; duplicate/terminal event behavior; long stdin prompt; cumulative result vs per-step counters | Correct conversation ID, one receipt per delivery, per-step usage not confused with cumulative totals, no raw event/text/tool content persisted. Tool/subagent completeness stays `PARTIAL/UNKNOWN` unless proven | Opt-in; 3 asks per changed path. Existing 2026-10-15 evidence used 4 turns (two-turn stream + two-turn separate-process resume); no speed claim |
| CC/CX usage scope (when parser/version changes) | Fresh result followed by exact resumed result for the same native session; compare raw provider counters and source scope | Scope matches the pinned CLI contract; current cumulative sources remain unattributed and never produce baseline deltas | Opt-in; 2 asks per affected peer (one fresh, one resumed), included in the 4-ask full selection matrix when both run together; evidence reuse is allowed only for unchanged binary/path |
| Fault injection (all peers) | Missing binary, nonzero exit, zero-exit refusal, malformed/truncated output, timeout/cancel, output caps, interrupted process, stale/wrong session, retry/replay | Deterministic fakes assert certainty transitions, cleanup, rollback, no duplicate evidence, and no invented counters; live providers are not forced into auth/rate-limit failure | No quota |
| Migration and monitor isolation | Old DB with complete/incomplete history, duplicate callback, failed migration, provider section unavailable, corrupt preference TOML, large unrelated history | Atomic rollback; legacy coverage honest; one bounded snapshot/no N+1/no provider invocation; other groups remain available with reason | No quota |

Run live cases only after their deterministic RED/green parser and state
contract is stable, and only for the CLI path whose behavior changed. Record
the exact case IDs and totals in the evidence ledger; never treat an optional
skip as successful verification. Any live failure blocks enabling that argv
or telemetry path, while unrelated monitor groups may still ship if their
contracts pass.

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

Do not implement live price crawling, quotas-per-session, forecasting, budget
controls, or adaptive routing as part of this monitoring work. A small,
versioned public price snapshot may support informational API-equivalent
estimates after the model and token-category mapping is validated; it must not
be a runtime network dependency or be presented as subscription spend. Same-
basis estimated per-delivery averages are optional and require explicit
coverage/denominator. Revisit them only after normalized adapter semantics and
coverage are demonstrated in fixtures for each supported peer.

## Cross-review amendments required before TDD

These are binding acceptance criteria for the plan and first RED tests. They
resolve source-backed CX/CC/AG findings; they do not authorize implementation
until all three peers approve this exact amended packet.

- **Outcome denominator and evidence authority:** `bridge_deliveries` remains
  the denominator for confirmed, terminal-failed, uncertain, and pre-start
  outcomes. `bridge_evidence(kind=session_telemetry)` remains the single
  immutable authority for measured usage facts; do not create synthetic
  telemetry for pre-start or uncertain deliveries. Distinguish “no measured
  receipt” from zero usage and show coverage against the delivery ledger.
  Reuse PH-T082 for atomic rollback when the receipt counter update affects
  zero rows; reuse PH-T093 for invocation/resolution uniqueness, unknown-mode
  coverage, idempotent evidence folds, and missing projection-row failure.
  Add regression cases proving a missing expected projection row cannot
  silently succeed or emit fabricated usage.
- **Counter scope and session deltas:** current CC/CX/AG result counters are
  conservatively session-cumulative. Display the latest snapshot with
  `session_cumulative` / `unattributed` labels; never difference current CLI
  snapshots into a delivery value, even when a local baseline looks monotonic.
  Response metadata retains its legacy flat `usage` shape with additive scope
  and attribution labels. Future delivery metrics require a provider contract
  that natively binds the counters to the fenced delivery.
- **Schema and field ownership:** finalize the sanitized AG step schema from
  parser fixtures before migration/transport work. Define one owner for every
  displayed field: session owns configured binding; runtime owns observed
  adapter/CLI provenance; coverage is completeness metadata; store is DB
  integrity. Summary cards link to the owner and must not duplicate facts.
  Context remains selectable and explicitly unknown until an actual matching
  occupancy source is proven; do not imply a current measurement.
- **Bounded query/read oracle:** before choosing indexes or adding projections,
  name each required query, filter/order, maximum rows, returned bytes, and
  expected query count. Test `EXPLAIN QUERY PLAN`, row/byte caps, and absence
  of raw step blobs in summary reads against representative large history.
  Keep one immutable evidence authority; add only rebuildable projections
  needed by named bounded queries. Do not use an unsupported wall-clock SLO.
- **Privacy and input validation:** test that no prompt/response/tool payload,
  vendor/session identifier, credential, raw path, or request ID reaches
  telemetry or rendered monitor output. Bound and sanitize profile/model/
  version/stream display fields, including control characters and oversized
  values. This does not change the separate replay-response retention
  contract.
- **Live gate evidence integrity:** current `tools.live_gate --allow-dirty`
  can stamp G3 against `HEAD` while testing uncommitted source, and G5 cannot
  detect that mismatch. Remove this bypass; reject every dirty worktree before
  provider spawn, including canary mode and the legacy flag, and write no JUnit
  or G3 stamp. Clean candidate live evidence must match the exact candidate
  SHA, with a positive control that a clean candidate can still stamp.
- **Soak failure semantics:** `.github/workflows/soak.yml` currently applies
  job-level `continue-on-error: true`, hiding hard correctness-oracle failures
  despite the workflow comment. Change the CI contract so correctness failures
  fail the soak job while G6 remains non-blocking to publishing; keep evidence
  upload on `always()` and test missing evidence separately.
- **Live exceptions and scope:** deterministic fakes cover prerequisite
  absence, unsupported CLI version, auth/rate-limit-shaped errors, refusal,
  malformed/truncated events, duplicate/out-of-order events, timeout,
  cancellation, process-tree cleanup, changed session/model/mode, concurrent
  use, reset/decrease, interrupted commit, and output caps. Live skips carry
  machine-readable peer/cause and remain `UNVERIFIED`; never turn a skip into
  a pass. No test deliberately exhausts quota or invalidates credentials.
- **Existing CLI and test evidence:** `session list/show` with cursor paging
  already exists; test its boundaries as current functionality instead of
  proposing a new command. Existing PH-T082 and PH-T093 contracts are
  authoritative and should be extended, not duplicated under new IDs.

## Approved implementation gate

CX, CC, and AG unanimously approved this plan, its amendments, and the RED
ordering on 2026-10-15. Implementation proceeds as independent test-first
slices. Each slice starts with local tests-only RED; tests and implementation
are committed together only after the matching GREEN tests pass. Each RED maps
to one USM/PH contract; schema migration, monitor CLI, adapter transport, and
process reuse remain separate slices. After deterministic tests pass, run only
the minimum affected live cases, record skips as unverified, then run the
complete local suite and CI. Do not enable new adapter defaults or persistent
process reuse based only on monitoring tests.

### Implementation progress (2026-10-15)

| Slice | Result | Verification and review |
|---|---|---|
| 0 — PH-T074 / G6 evidence integrity | Commit `70c958d2`: dirty live-gate evidence is rejected before provider spawn, including the removed legacy bypass; soak correctness failures remain failures. | RED reproduced first. Targeted tests: 15 passed. Release/soak regression set: 123 passed, 2 deselected. |
| 1 — AG stream parser contract | Commit `6fd3daff`: bounded allowlist parser, sanitized golden fixture, duplicate-key rejection, fail-closed ordering/state/size checks. | Parser, adjacent adapter, and architecture set: 57 passed; pyright: 0 errors. CX, CC, and AG unanimously approved the final diff. |
| 2 — Telemetry receipt v2 scaffold | Commit `a2c7c748`: extends the existing unique `session_telemetry` receipt with version, provider/adapter provenance, sanitized binding, and explicit unknown usage/step coverage. Generic result usage is deliberately not promoted. | Targeted adapter/receipt/bridge set: 135 passed; retained-session/adapter subset: 79 passed; pyright: 0 errors. CX, CC, and AG approved. |
| 3 — PH-T082 projection atomicity | Commit `7649cd1a`: terminal telemetry persistence fails and rolls back if its stats projection is missing; registry initialization reconstructs missing projection rows from durable evidence and leaves unprovable historical counts unknown. | Retained-session tests: 37 passed; pyright: 0 errors; architecture gate: 4 passed. CX, CC, and AG approved. |
| 4 — A1–A4 receipt semantics and bounded rebuild | Commit `3d59c6ff`: schema-v3 configured/resolved model separation; shared provider-native usage normalization; terminal-delivery usage-coverage aggregate; and indexed per-scope historical projection rebuild. No extension-version runner or parallel telemetry ledger was added. | Tests-only RED preceded implementation. Focused Windows set: 107 passed; pyright: 0 errors; architecture/M2-M3 gates and 5k-session/50k-delivery query plan pass. CC and AG approved the final summary and A3 edge correction. CX unanimously approved the design and schema-v3 choice; full-diff review failed repeatedly with the Codex MCP adapter's `AuthRequired` OAuth error. Draft PR #103 CI run `38722958886` is in progress. |

Slice 4 now persists allowlisted provider usage using a shared normalizer and
reports terminal-delivery receipt coverage. Provider usage remains
session-cumulative/unattributed where documented; step transport and context
occupancy/window are still `UNKNOWN` until their adapter contracts are proven.
Schema-v2 receipts remain immutable and readers must branch by schema version.

Remaining approved slices are: broad bounded monitor group reader/output and
complete coverage projections (USM-01–08/14/19–24/35); group catalog,
selection/default preferences and CLI rendering (USM-09–12/25); validated
step transport and context-window/occupancy adapters with live exception
gates (USM-15a/15b/26–34); GitHub required-status configuration and full PR
CI; followed by the separate persistent-process experiment gate (USM-30).
No new adapter default or persistent process reuse has been enabled. Draft
PR #103 exists to run the full repository CI; do not merge or release it as
the completed monitoring feature until these remaining slices are done.
