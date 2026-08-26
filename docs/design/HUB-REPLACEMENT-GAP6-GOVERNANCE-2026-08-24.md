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
3. ~~`directive-add` scope~~ — **RESOLVED 2026-08-26** via direct read of `action_directive_add`, `P:\_sys\core\hub.py:10026`: its own docstring is explicit — `"Manually add a runtime directive (human-confirmed standing rule)"`. Confirmed as a runtime/session-scoped directive (has `ttl_hours` default 6 and `clear_condition`, both meaningless for a permanent user preference), not a user-preference store. Native disambiguation: name it `runtime_directive.create` (matches `LEGACY_CATALOG`'s `governance.*` convention), never `directive` alone or anything implying user-settings.
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

## RECONCILIATION AGAINST REAL SOURCE (2026-08-24)

Same real governance broker as gap-2's reconciliation (see that doc's
section for the full type list). All 5 artifact categories map
conceptually onto `TargetState` + domain-specific `MutationRequest`
payloads through the SAME generic broker:

| Artifact | Target | Mutation examples |
|---|---|---|
| Directive | directive target | propose, activate, supersede, revoke |
| Lesson | lesson target | draft, validate, activate, deprecate |
| Proposal | proposal target | submit, revise, approve, reject, adopt |
| Feedback | feedback target | submit, acknowledge, classify, resolve |
| Alert | alert target | raise, acknowledge, suppress, resolve |

Broker supplies (common to all 5): mutation idempotency, expected-revision
handling, transition receipts, outbox notification, effect tracking,
recovery. **Broker does NOT supply the domain model itself** — directive
precedence/scope, lesson evidence/activation criteria, proposal
semantics/quorum policy, feedback classification/linkage, alert
severity/dedup/escalation policy all remain domain-layer responsibilities
gap-6 must still design (its existing lifecycle-state-machine sections
for each artifact type stand — only the EVENT-PERSISTENCE framing
changes).

**Remove any implication that each artifact type owns a separate
append-only event stream** — gap-6's originally-listed events
(`RuntimeDirectiveCreated`, `LessonProposed`, `ProposalCreated`, etc.)
remain useful as projection/audit-vocabulary names, not as a second,
parallel persistence mechanism alongside the real broker.

**`proposal-vote` reconciliation** (also recorded in gap-2's doc): confirmed
to reuse the same broker + consensus/quorum logic as gap-2's rounds. Two
possible shapes not yet distinguishable from available evidence: a
proposal owns its own voting lifecycle in its own `TargetState`, or
references a separate consensus-round target whose resolution mutates the
proposal target — needs field-level source inspection to resolve, not
assumable.

Same verified/inferred/test-needed split as gap-2's reconciliation
applies here (see that doc) — nothing artifact-type-specific changes the
uncertainty level.

## CONFIRMED (2026-08-24, terminal, field-level read): same CAS/accumulating-target model applies to all 5 artifact types

See gap-2's doc for the full field-level confirmation (`TargetState{target_id,
revision, state: Mapping[str,JsonValue], updated_at}`,
`MutationRequest{..., expected_revision, operation: str, desired_state:
Mapping}`, `MutationPlan` enforcing `next_revision == previous_revision +
1`). This applies identically to directives/lessons/proposals/feedback/
alerts: each artifact's full lifecycle state lives in its `TargetState.state`
JSON blob, each lifecycle transition (`propose`, `activate`, `resolve`,
etc.) is a `MutationRequest` with the appropriate `operation` string and
`desired_state`, and the broker's revision-CAS handles concurrent-mutation
safety generically. **No per-artifact-type event-sourcing machinery is
needed** — this was already gap-6's post-reconciliation conclusion, now
confirmed at the field level rather than inferred from class names alone.

## CONCRETE SCHEMA DESIGN (2026-08-26, cx): lesson artifact `TargetState.state`

`cx` could not access this doc directly this round (worked from a
summary); the terminal independently verified its one concrete file
citation (`_sys/checks/check_lesson_enforcement.py`) exists on disk —
citation is valid.

Proposed envelope (`schema: "peerhub.lesson.v1"`), wrapped in
`TargetState{target_id: "lesson:<id>", revision, state, updated_at}`:

```json
{
  "schema": "peerhub.lesson.v1",
  "lesson_id": "LL-20260826-001",
  "lifecycle": "PROPOSED",
  "content": {"title": "...", "rule": "...", "category": "runtime-reality", "severity": "HIGH"},
  "scope": {"kind": "global", "workspace_id": null},
  "affected_peers": ["cc", "cx", "ag"],
  "source_evidence": [{"evidence_id": "EV-...", "kind": "empirical_probe", "uri": "...", "sha256": "...", "summary": "..."}],
  "provenance": {"proposer": {"actor_id": "cx", "actor_type": "peer"}, "proposed_at": "...", "source_command": "lessons-propose"},
  "approval": null,
  "enforcement": {"artifact_id": null, "artifact_uri": null, "validation_status": "NOT_REQUIRED"},
  "validity": {"expires_at": "...", "retired_at": null, "superseded_by": null},
  "delivery": {"mode": "separate_targets", "required": true}
}
```

At `ACTIVE`, `approval` is populated:

```json
"approval": {
  "method": "ratified_governance_proposal",
  "approved_by": [{"actor_id": "coordinator", "actor_type": "human", "approved_at": "..."}],
  "authority": {
    "target_id": "consensus-round:proposal-20260826-0042",
    "resolution": "RESOLVED",
    "outcome": "AUTHORIZE_LESSON_ACTIVATION",
    "resolved_at": "...",
    "resolution_sha256": "..."
  }
}
```

At `RETIRED`: `lifecycle:"RETIRED"`, `validity.retired_at` set,
`validity.retirement_reason` (e.g. `"SUPERSEDED"`),
`validity.superseded_by` pointing to the replacement lesson ID.

**Delivery tracking is deliberately a SEPARATE `TargetState` per
(lesson, peer)** — `target_id: "lesson-delivery:<lesson_id>:<peer_id>"`,
own envelope (`peerhub.lesson-delivery.v1`) with `status`,
`delivery_revision`, `delivered_at`, `delivery_method`,
`delivery_evidence{command_id, correlation_id, result_sha256}`. **This
keeps the canonical lesson revision independent from per-peer retries,
partial delivery, and quarantine — `DELIVERY_PENDING`/`DELIVERED` are
projection states on the delivery target, not the lesson's own lifecycle
states.**

### Operations → transitions

| Operation string | Transition |
|---|---|
| `lessons-propose` | absent → `PROPOSED`, assigns immutable `lesson_id`. |
| `lessons-approve` | stays `PROPOSED`, populates `approval` (human or authorized resolved proposal). |
| `lessons-activate` | `PROPOSED`/`APPROVED` → `ACTIVE`, requires valid `approval` present. |
| `lessons-retire` | `ACTIVE` → `RETIRED`, sets `retired_at`+reason. |
| `lessons-supersede` | `ACTIVE` → `SUPERSEDED`, sets `superseded_by`. |
| `lessons-quarantine` | any non-terminal → `QUARANTINED`, records reason+evidence+actor. |

Every transition follows `next_revision == previous_revision + 1` (same
CAS rule as gap-2's consensus rounds); the transition function must
**reject invalid combinations rather than silently repairing them**.

### Consensus-round approval reference — pointer + immutable snapshot, both

```json
"approval": {
  "method": "ratified_governance_proposal",
  "authority": {"target_id": "consensus-round:proposal-...", "target_revision": 6, "resolution": "RESOLVED", "outcome": "AUTHORIZE_LESSON_ACTIVATION", "resolved_at": "...", "approved_by": ["coordinator"], "resolution_sha256": "..."},
  "snapshot": {"proposal_id": "...", "final_phase": "resolved", "quorum_reached": true, "outcome": "AUTHORIZE_LESSON_ACTIVATION", "authority_type": "human_ratified_governance_proposal"}
}
```

The pointer gives provenance/lookup; the immutable snapshot makes the
lesson self-auditing even if the consensus record is later archived.
**The snapshot must never reinterpret peer votes as human approval** — a
peer-agreement-only result is sufficient ONLY if governing policy
explicitly authorizes that specific proposal type to grant
lesson-activation authority; otherwise `lessons-activate` must reject it.

### Unresolved (needs source access or explicit policy decision)

Does the lifecycle include a persisted `APPROVED` intermediate state, or
is approval just metadata on `PROPOSED` until activation? Exact
severity/category enum values. Does expiry auto-transition to `EXPIRED`
or just block future activation? Are `SUPERSEDED`/`QUARANTINED`
terminal? Are enforcement artifacts mandatory for every lesson or only
enforceable-classified ones? Exact actor schema/timestamp format. Is
`lessons-approve` an existing command or a new proposed one? Does
superseding require separate approval for the replacement lesson? Exact
consensus-round field names/hash semantics (pending the same
`mutations.py` body-level read gap-2's doc already flagged as open).
Does a global lesson's empty `affected_peers` mean "all current and
future peers"? Must human approval reference a separate auditable
artifact, or can it be recorded directly by `actor_id`?

## DEFINITIVE CONFIRMATION (2026-08-26, terminal): same real CAS mechanism applies to all governance artifact TargetStates

See gap-2's doc for the full `validate_expected_revision`/`plan_mutation`/
`apply_mutation_plan` code. Applies identically to directives/lessons/
proposals/feedback/alerts: `desired_state` is always the COMPLETE next
artifact state (not a patch); `StaleRevisionError` on CAS conflict;
`expected_revision=0` for the first mutation (e.g. `lessons-propose`).
