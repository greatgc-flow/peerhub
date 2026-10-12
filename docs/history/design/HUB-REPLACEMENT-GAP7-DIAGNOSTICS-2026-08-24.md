# Gap 7 Design: Legacy Diagnostics/Telemetry Parity (DRAFT — mostly an adapter delta, not a redesign; 8 items need ratification)

Status: first-round draft from `cx`, 2026-08-24, final of the 7 gap
categories. `cx` had access to `_sys/docs-v2/ops/diag-telemetry-architecture.md`
AND `_sys/cli/diag.py` directly this round (broader sandbox access than
some prior rounds) but not `peerhub` source, so `peerhub diag`'s actual
current implementation state remains unverified.

## 1. Does the existing telemetry design cover the legacy surface?

Mostly yes, IF fully implemented: per-peer/profile health+routing,
context usage/capacity, 5H/7D quota windows+reset+freshness+source
confidence, session state/reuse/resume-risk/recent sessions, historical
token/cost consumption, profile/model/effort matrix, redacted
account/plan metadata, JSON/watch/live/detail/alert views.

**Not fully specified by the existing design**: (1) the legacy EXH
calculation's exact per-pool semantics; (2) credit/coupon inventory as a
first-class lifecycle domain (available credits, IDs, expiry,
eligibility, consumption, idempotency, post-consumption verification);
(3) the mutating `credit-consume` operation; (4) exact `model-status`
command contract/compat behavior; (5) whether legacy dashboard rows need
byte-for-byte compat or just semantic equivalence. The design mentions
credits/credit-adjusted EXH in later sections, but that isn't a complete
credit-management API.

## 2. What's actually implemented (real `diag.py`, verified)

`_sys/cli/diag.py` is substantially more advanced than the original
design doc's "reserved detail views" wording: `SUMMARY` renders
per-pool EXH/5H/7D; EXH calculated from reset/usage velocity, adjustable
for eligible reset credits; missing quota windows shown as absent/
cross-referenced (never fabricated); session rows + lease states shown;
recent token consumption aggregated from `cost-log.jsonl`; `--tokens
--sessions --accounts --profiles --project --watch --live --json` modes
exist (some reserved/compat-oriented); credit status surfaced where
available. Separate legacy commands: `hub.py model-status`, `hub.py
credit-status --peer`, `hub.py credit-consume --peer --credit-id
--confirm`.

**`peerhub diag` itself cannot be classified without its source** — name
alone isn't evidence of parity; this needs a direct peerhub-source check
before further ratification.

## 3. Remaining design delta (adapter delta, NOT a second telemetry architecture)

**A. Canonical telemetry contract**: `peerhub diag [--json|--watch
[s]|--live [s]|--tokens|--sessions|--profiles|--accounts|--project]`.
Normalized envelope retains: `schema_version`, peer/profile identity,
domain, source/timestamp/TTL/confidence, context, quota windows, session
state, health/gate, token/cost history, account metadata, alerts, pool
identity + raw-vs-effective values.

**B. EXH compatibility extension** — explicit quota-pool projection
(illustrative): `{pool, window, used_pct, reset_at,
exhaustion_index:{raw, effective, basis, credit_adjustment}}`.
Requirements: preserve raw AND effective EXH separately; never calculate
EXH when required inputs are unknown; identify pool-owned vs
borrowed/cross-referenced rows; preserve legacy ordering/5H-7D display
mapping through the compat adapter; **EXH is a diagnostic/routing signal,
never provider truth.**

**C. Credit/coupon subsystem** — distinct read/write contract:
`credit-status --peer`, `credit-consume --peer --credit-id --confirm`.
`credit-status` reports provider/peer, available count, stable IDs
(redacted where necessary), expiry, eligibility, observed source+timestamp,
unknown/unavailable status. `credit-consume` requires explicit human
confirmation, terminal-origin authorization, credit id, idempotency key,
preflight observation, append-only intent/result audit events,
post-action verification, explicit ambiguous-outcome state. **Must not be
hidden behind read-only `diag`.**

**D. `model-status` compatibility projection**: `hub.py model-status →
peerhub model-status`, exposing peer/profile, model, effort, context
limit, availability/gate, validation state, routing eligibility,
source/freshness — same fields as the profile matrix. Legacy names/exit
behavior preserved by the adapter; peerhub's internal event/projection
model stays canonical.

**E. Legacy compat mapping**

| Legacy | Native | Required behavior |
|---|---|---|
| `diag.py`/`diag.bat` | `peerhub diag` | Same dashboard semantics |
| `diag --json` | `peerhub diag --json` | Versioned normalized envelope |
| `diag --watch` | `peerhub diag --watch` | Freshness-aware streaming |
| `diag --live` | `peerhub diag --live` | Compact HUD |
| `hub.py model-status` | `peerhub model-status` | Profile/model projection |
| `hub.py credit-status` | `peerhub credit-status` | Read-only credit inventory |
| `hub.py credit-consume` | `peerhub credit-consume` | Explicit, audited mutation |
| Legacy EXH rows | Quota-pool projection | Raw/effective EXH + provenance |

## 4. Dependencies on gaps 1-6

Telemetry must stay readable even when upstream domains have no data.
**Rule: diagnostics may report `unknown`/`unavailable` when an upstream
subsystem is absent — it must never synthesize healthy/zero/idle values.**

- **Gap 1** (compat adapter): required for legacy names/flags/output/exit codes.
- **Gap 2** (consensus): only relevant if diagnostic-schema changes are consensus-governed; ordinary reads don't need a new round.
- **Gap 3** (session/room/thread): required for truthful session history, room association, resume risk, recent-session display.
- **Gap 4** (health/leadership/roles): **directly relevant** — telemetry needs NodeRegistry/PeerHealth state, gate status, quarantine state, profile ownership, routing eligibility.
- **Gap 5** (task lifecycle): required for task/request/attempt attribution, per-task token consumption, failure telemetry, meaningful recent-consumption history (basic provider-level usage can exist without it).
- **Gap 6** (governance/learning/alerts): required for alert persistence, ack, escalation, audit history, governance-visible diagnostic events.

## Open questions requiring ratification (8)

1. Semantic parity sufficient, or must legacy text layout stay byte-compatible?
2. Is EXH a stable public contract, or an adapter-only compat field?
3. Should credits be a generic provider capability, or a dedicated peerhub resource domain?
4. Which credit fields may be displayed to users (identifiers, expiry)?
5. Should `credit-consume` stay a direct compat command, or route through a general mutation broker?
6. Does `model-status` belong under `diag --profiles`, stay separate, or both?
7. Authoritative event source for token accounting when provider receipts and task-attempt events disagree?
8. Unavailable legacy fields: `null`, explicit `unknown`, or a structured absence reason?

## `cx`'s overall judgment across all 7 categories

> "Across the seven categories, the design layer now appears substantially
> sketched for a full `hub.py` replacement: the shared architecture —
> peerhub-owned semantics, compatibility adapters, versioned envelopes,
> append-only events, and materialized projections — covers the major
> control-plane and observability surfaces. Major implementation and
> ratification work remains, especially around exact contracts, event
> schemas, authority boundaries, and migration behavior, but the
> remaining risk is specification closure and conformance rather than an
> unrecognized architectural category."

## DIAG EXTENSION DESIGN (2026-08-26, cx): consensus/task/duty-lease rows, using tonight's concrete schemas

`cx` could not access `peerhub`/design docs directly this round (worked
from supplied context) — design is schema-driven and consistent with
everything established tonight; exact column names/row ordering remain
unverified against the real `presenter.py` layout.

### New SUMMARY rows (follow the existing snapshot→render, `_dw`/`_pad`, optional-color, <65-col-truncation contract)

**Consensus**: `CONSENSUS <phase> <votes_cast>/<votes_required> Q:<quorum>
T-<countdown>` (e.g. `CONSENSUS VOTING 2/3 Q:2 T-04:18`) — deadline via
the EXISTING `_format_countdown()` (already built for quota-reset
countdowns); `OVERDUE` after expiry, `—` when no deadline. **Task**:
`TASK <current_stage> <state> CP:<yes/no> AP:<yes/no>` (checkpoint
presence, approval-pending flag) — narrow-terminal collapse: `TASK
IMPLEMENT CP✓ AP—`. **Duty lease**: `DUTY <role> <term> HB:<countdown>
<room_id>` — `UNHELD` if no lease, `EXPIRED` if heartbeat lapsed;
`term` is an opaque token, never interpreted as a timestamp. Recommended
implementation boundary: one formatter function per domain
(`_format_consensus_row`/`_format_task_row`/`_format_duty_row`), not
domain logic embedded in the generic table renderer.

### `collect_live_snapshot()` new read dependencies

**UPDATE 2026-08-26 (direct peerhub source read, terminal): this
dependency is CONFIRMED ABSENT, not merely unverified.** Read
`peerhub/state/contract.py`, `peerhub/governance/broker.py`, and the real
`peerhub/persistence/sqlite_governance.py` backend directly — the broker
has zero listing/query capability anywhere (`get_target` is an exact-key
point lookup only; the backend's several real `list_*` methods are all
outbox/effect-delivery bookkeeping, none for targets). See the index
doc's "NEW CROSS-CUTTING FINDING 2026-08-26" banner for the two candidate
fixes. This diag-extension design cannot be implemented until gap-2/3/5
each add a real listing capability — treat that as a blocking
prerequisite, not a parallel/independent workstream.

Three new read-only inputs, **each an explicit dependency on gap-2/4/5
providing an authoritative "list active X" read path** — the diagnostic
layer must NOT reconstruct state by inference:
`list_active_consensus_rounds(room_id)` (phase, votes cast/required,
quorum, deadline), `list_active_tasks(room_id)` (current_stage, state,
checkpoint presence, approval-pending flag), `list_active_duty_leases(
room_id)` (room_id, role, term, heartbeat_expires_at — **stays separate
from the TargetState broker**, consistent with gap-4's dedicated-lease
decision). Snapshot collection should track per-domain source status
(`"available"|"absent"|"error"`) distinctly — **a missing implementation
must render `UNAVAILABLE`, never be silently treated as "no active
object"** (same "never synthesize a value you don't have" principle
from gap-4's health design).

### New ATTENTION alert classes (trigger conditions using confirmed schema fields)

`CRIT CONSENSUS OVERDUE` (deadline exists + now>deadline + non-terminal
phase); `WARN CONSENSUS NO PROGRESS`/`QUORUM RISK` (both need a policy-
supplied threshold — **not to be invented by the presenter**); `WARN
TASK APPROVAL PENDING` (needs `approval_pending_since` or equivalent age
field — if the schema only has a boolean, presenter can show status but
not correctly age-gate the alert — **flag: does gap-5's schema need this
field added?**); `WARN TASK CHECKPOINT MISSING` (needs stage-requires-
checkpoint POLICY metadata, not hardcoded); `CRIT TASK FAILED` (only if
`state` has a confirmed terminal-failure value); `CRIT DUTY LEASE
EXPIRED`; `CRIT DUTY LEASE UNHELD` (needs a required-role-per-room policy
source); `WARN DUTY LEASE NEAR EXPIRY` (configured warning window, not
guessed). Dedup keys: `("consensus-overdue", round_id)`,
`("task-approval", task_id)`, `("duty-expired", room_id, role, term)`.

### Updated gap-7 verdict (strengthened)

> "Gap-7 is an adapter-and-policy integration task, not a
> telemetry-engine redesign." The concrete upstream schemas remove most
> prior uncertainty — consensus/tasks/duty-leases each supply exactly
> the fields needed; the existing presenter already provides width
> handling, color fallback, truncation, snapshot/render separation.
> **Remaining complexity is POLICY integration, not rendering**:
> selecting which active round/task/lease to display when multiple
> exist, defining required roles, defining checkpoint-required stages,
> alert aging/warning-window thresholds, preserving the unavailable/
> empty/failed distinction. **Nothing indicates a need to replace the
> existing presenter or introduce a parallel diagnostics engine — subject
> to the confirmations below, gap-7 can reasonably be marked
> design-complete and ready for a small implementation/TDD pass.**

### Unresolved (needs source access or policy decisions)

Exact current SUMMARY columns/row ordering; does the presenter already
have a generic optional-row abstraction; exact `TargetState` query/read
API; are consensus/task identifiers+timestamps available in the
projections; how are multiple active rounds/tasks selected for display;
does the task schema need an `approval_pending_since` field added; which
duty roles are required per room; configured thresholds (approval age,
lease-near-expiry, consensus warning window); exact terminal-state
enumerations; `—`/`N/A`/`UNAVAILABLE` convention for a missing domain;
authoritative clock/timezone convention for deadline comparisons.
