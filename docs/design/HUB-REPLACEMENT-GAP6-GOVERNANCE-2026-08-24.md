# Gap 6 Design: Governance, Learning, Proposal, Alert Commands (DRAFT — architecture proposed, 12 items need ratification)

Status: first-round draft from `cx`, 2026-08-24, grounded directly in real
`_sys/docs-v2/general/learning.md` and `_sys/docs-v2/ops/governance.md`
(cx confirmed reading these; could not access `peerhub` source). Covers
`directive-add/list/clear`, `lessons-list/propose/activate/retire`,
`lesson-broadcast/sweep/inject`, `proposal-add/vote/list`,
`feedback-add/list/resolve`, `alert-raise`, `arbiter-review`.

## 1. Artifact distinctions

**Directives** — two materially different layers: **user directive**
(human-authored standing rule, indefinite until revoked, user-only
mutation) vs **runtime directive** (system-generated mitigation, TTL-bound,
`active/resolved/expired`). `directive-add/list/clear` operate on RUNTIME
directives only unless explicitly extended — must never silently write
human directives. Runtime directives are immediate mitigations, not
institutional knowledge, and don't require consensus; a permanent fix
becomes a proposal → (after approval) config/code/doc change.

**Lessons** — normalized institutional knowledge from an observed
mistake/failure/feedback item: durable (not incident-local), approved
before activation, injected into future peer contexts, scoped
global/workspace, independently retired/expired. Live registry =
`active-lessons.jsonl`; the documented raw-event/delivery-pack layers are
**planned, not fully implemented** — don't claim every lesson has raw-event
provenance.

**Proposals** — a pending governance decision/change request, NOT itself
an active rule. States: `PENDING → ACCEPTED|REJECTED|EXPIRED|STALE`.
Accepted proposals stay auditable in the archive, referencing the
resulting change/artifact.

**`proposal-vote` vs `consensus-vote`**: NOT two independent voting
engines — gap-2 owns the canonical append-only consensus event model;
`consensus-vote` = native low-level command for any consensus round;
`proposal-vote` = a domain-level façade that validates the proposal,
resolves its required R-level, and appends the corresponding consensus
vote event; the proposal projection derives its outcome from those
events. A proposal's `required_consensus` determines R:5/8/10 — **not
inherently lighter-weight**, that's a property of the proposal, not the
command.

## 2. Lesson lifecycle

States: `PROPOSED → APPROVED → ACTIVE → RETIRED`, plus `REJECTED`,
`EXPIRED`, `SUPERSEDED`, `QUARANTINED` (malformed/unsafe/
provenance-insufficient, not injectable), `DELIVERY_PENDING`/`DELIVERED`
(per-peer delivery projections, not global lesson states).

- **`lessons-propose`**: creates a candidate — lesson ID, title/rule,
  category, scope (global/workspace), affected peers, severity, source
  evidence, proposer, created time, optional expiry, remediation method.
  Must NOT make the lesson injectable.
- **Approval**: source docs specify `approved_by: user` — activation
  requires explicit human approval OR a ratified governance proposal
  whose outcome authorizes it. Peer agreement alone ≠ human approval
  unless policy explicitly grants that authority.
- **`lessons-activate`**: `APPROVED → ACTIVE`. Preconditions: valid
  schema, nonempty rule, known scope, provenance or explicit
  human-authored exception, approval evidence, no conflicting active
  replacement, safety checks passed. Idempotent, event-recorded.
- **`lessons-retire`**: `ACTIVE → RETIRED` or `→ SUPERSEDED` (with
  replacement). Reasons: no-longer-true, incorporated into
  config/code, superseded, excessive false positives, expired, human
  withdrawal. Stops future injection, preserves history.
- **`lesson-inject`**: a **dispatch-time operation**, not a free-standing
  mutation — at ask-construction time, selects lessons that are
  active+approved+non-expired+applicable-to-workspace+applicable-to-target-peer+within
  injection limits. Prompt section includes a lesson-pack hash; delivery
  projection records ask/request ID, peer/profile, selected lesson IDs,
  pack hash, timestamp, optional ack. **No lesson injects merely because
  proposed or broadcast.**
- **`lesson-broadcast`**: propagates an active lesson's availability/
  compiled pack to eligible peers — does NOT activate, does NOT override
  applicability filters. Steps: create propagation event → compile/
  invalidate pack → mark intended deliveries pending → inject on next
  eligible dispatch → record delivery/failure.
- **`lesson-sweep`**: maintenance — marks expired proposals/lessons,
  retires/supersedes stale entries, identifies malformed records,
  reconciles delivery-pending, optionally archives per retention policy.
  Must NOT silently delete governance history (governance policy requires
  archival + human-only final deletion).

## 3. Feedback design

Distinct operator-facing observation/issue layer — feedback is NOT
automatically a lesson or directive. Lifecycle: `OPEN → TRIAGED →
{RESOLVED, WONT_FIX, DUPLICATE}`, optional `CONVERTED` (produced a linked
lesson/directive/proposal).

- **`feedback-add`**: feedback ID, reporter, timestamp, category,
  severity, affected task/request/attempt/peer, observed vs expected
  behavior, evidence refs, recurrence count, related artifact IDs.
- **`feedback-list`**: read-only filtered projection (state, severity,
  category, peer, workspace, age, linked artifact).
- **`feedback-resolve`**: resolution type, resolver, evidence, linked
  permanent artifact if applicable, timestamp.

Learning loop: `feedback/telemetry → analysis → temporary runtime
directive (if immediate mitigation needed) → proposal or lesson candidate
→ approval/consensus → active lesson or permanent change`. Preserves the
distinction between observation, immediate mitigation, and durable rule.

## 4. Alerts and arbiter review

**Alert triggers** (measured evidence, not speculation): repeated peer
failures above threshold, health reaching RED, consensus
disagreement/unresolved tie, high-risk proposal needing escalation,
safety-invariant violation, suspected stale lease/conflicting authority,
lesson recurrence indicating ineffectiveness, failed post-apply
validation, human-requested escalation. Alert fields: ID, trigger type,
source event/request/proposal, severity, affected room/workspace/peer,
evidence refs, created time, dedup key, state. States: `OPEN →
ACKNOWLEDGED → UNDER_REVIEW → MITIGATED → RESOLVED`, plus `ESCALATED`,
`SUPPRESSED`, `DUPLICATE`, `EXPIRED`.

**Who is the arbiter**: two DISTINCT concepts, not to be conflated — (1)
the final arbiter under DIR-005 is a configured premium model/profile
(e.g. `cc.fable`) used sparingly to resolve cheap-peer dissent or
high-risk decisions; (2) human approval remains required for actions
policy reserves to the user (user directives, lesson activation per
`learning.md`). `arbiter-review` = a structured review invocation by the
configured final-arbiter mechanism — must NOT grant unrestricted mutation
authority. Arbiter may: classify severity, recommend mitigation, resolve
a cheap-peer tie, recommend human escalation, produce an advisory or (only
under DIR-005 conditions) a final opinion. **May NOT directly mutate
files, activate lessons, alter user directives, or execute remediation**
unless a separate approved command + authorization gate permits it.

`arbiter-review` inputs: alert/decision ID, compact evidence bundle, risk
classification, relevant consensus/proposal state, configured arbiter
policy. Outputs: advisory/canonical opinion, rationale, recommended next
action, confidence, whether human approval still required, provenance.
**An arbiter recommendation alone does not auto-resolve an alert** unless
policy explicitly defines that class as auto-resolvable.

## 5. Native command surface + legacy mapping

| Legacy | Native | Compat behavior |
|---|---|---|
| `directive-add` | `runtime-directive.create` | TTL-bound runtime directive; reject mutation of user directives |
| `directive-list` | `runtime-directive.list` | Query active/expired/resolved/all |
| `directive-clear` | `runtime-directive.resolve` | Resolve by ID + reason |
| `lessons-propose` | `lesson.propose` | Create `PROPOSED` |
| `lessons-activate` | `lesson.activate` | Require approval evidence → `ACTIVE` |
| `lessons-retire` | `lesson.retire` | Retire/supersede, preserve history |
| `lesson-broadcast` | `lesson.propagate` | Invalidate/compile packs, mark delivery intent |
| `lesson-sweep` | `lesson.maintenance-sweep` | Expire/quarantine/reconcile/archive |
| `lesson-inject` | `lesson.select-for-dispatch` | Dispatch-time read op, no activation |
| `proposal-add` | `proposal.create` | `PENDING` proposal, required R-level + TTL |
| `proposal-vote` | `consensus.cast-vote` (proposal-bound) | Appends canonical consensus vote after proposal validation |
| `proposal-list` | `proposal.query` | Lifecycle state + vote summary |
| `feedback-add` | `feedback.create` | — |
| `feedback-list` | `feedback.query` | Filtered projection |
| `feedback-resolve` | `feedback.resolve` | Close/classify + evidence |
| `alert-raise` | `alert.raise` | Deduplicated, from measured evidence |
| `arbiter-review` | `arbiter.review` | Advisory unless DIR-005 applies |

Native records need stable IDs, timestamps, causation/correlation IDs,
provenance — legacy flags/output preserved where practical.

## 6. Event model (append-only, gap-2 pattern)

`RuntimeDirectiveCreated/Resolved`, `LessonProposed/Approved/Activated/
Retired/Superseded/Expired/PackBuilt/DeliveryAttempted/Delivered`,
`ProposalCreated`, `ConsensusVoteCast`, `ProposalAccepted/Rejected/
Expired/MarkedStale`, `FeedbackCreated/Triaged/Resolved`,
`AlertRaised/Acknowledged/Escalated/Resolved`,
`ArbiterReviewRequested/OpinionRecorded`. Projections: runtime directive
registry, lesson registry, lesson delivery status, proposal registry,
feedback registry, alert registry, arbiter-review audit trail. **No
projection is the sole source of truth.**

## Open questions requiring ratification (12)

1. Proposal authority levels — R:5/8/10 named but active voter set + quorum calc per level not fully defined for THIS model.
2. Lesson approval authority — can a ratified proposal substitute for direct human approval (`learning.md` says `approved_by: user`)?
3. `directive-add` scope — legacy name implies user directives, actual behavior is runtime-directive creation; needs explicit disambiguation (rename natively?).
4. Raw feedback storage — 3-layer learning design's raw-event/delivery-pack layers aren't implemented; introduce now or keep direct lesson maintenance?
5. Lesson-broadcast semantics — immediate peer notification, pack invalidation, or just next-dispatch eligibility?
6. Lesson acknowledgement — hash-ACK compression described but required peer ack protocol + retry behavior unspecified.
7. Sweep authority — can `lesson-sweep` auto-retire stale lessons, or only mark for review?
8. Alert ownership — which gap-4 role is authorized to ack/resolve/escalate each alert class?
9. Arbiter authority mapping — DIR-005 permits override only for unresolved cheap-peer dissent/high-risk; need explicit alert-class → {advisory-only, canonical-opinion, human-mandatory} mapping.
10. Proposal/alert relationship — must every high-severity alert create a proposal, or can alerts resolve operationally without governance change?
11. Cross-workspace lesson scope — conflict resolution when a workspace lesson contradicts a global one?
12. Retention/archival — exact archive layout for lessons/feedback/alerts/arbiter-reviews (proposal archive is specified, these aren't).

## Ratifiable core

One canonical event/vote substrate; separate projections/lifecycles for
directives, lessons, proposals, feedback, alerts; human/consensus gates
preserved; no automatic mutation hidden behind propagation/sweep/
injection/arbiter commands.
