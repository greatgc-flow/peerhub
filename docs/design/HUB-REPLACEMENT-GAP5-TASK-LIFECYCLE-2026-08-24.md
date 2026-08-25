# Gap 5 Design: Task Lifecycle and Failover CLI (DRAFT — architecture proposed, 12 items need ratification)

Status: first-round draft from `cx`, 2026-08-24. Covers `task-checkpoint`,
`task-status`, `task-failover`, `approval-request`. `cx` could not access
`peerhub` source directly this round; claims some evidence of existing
retry-loop/failover/checkpoint machinery from "local PeerHub session
records" — **treat this claim as unverified until cross-checked against
real peerhub source**, unlike gap-2's protocol.md citations which the
terminal independently confirmed.

## Central conclusion

> Gap 5 is NOT a replacement for peerhub's existing retry/failover
> machinery. It's a **task-level lifecycle + CLI façade above that
> machinery**, adding durable checkpoints, human approval gates, and
> task-scoped reassignment while reusing the existing fenced
> attempt/routing substrate.

## 1. Meaning of "task"

A **task** is broader than a request or attempt: `request → 1+ attempts →
terminal result`, vs `task → multiple stages/checkpoints → multiple
requests/attempts → completion`. Inferred from command semantics:
`task-checkpoint` implies progress surviving beyond one invocation;
`task-status` implies an independently addressable lifecycle object;
`task-failover` implies reassigning unfinished work, not just retrying one
request; `approval-request` implies a task can pause awaiting an external
decision. **A task = a durable orchestration aggregate owning 1+
requests/attempts — never aliased to a single request/attempt.**

## 2. Task lifecycle state machine

Task record: `task_id`, scope (tenant/room/session), objective/spec,
`current_stage`, `state`, executor assignment, checkpoint reference,
pending-approval reference, child request IDs, active attempt ID,
failure/failover counters, timestamps, `fencing_epoch`. Durable,
event-backed — current state is a projection, never independently
mutable truth.

States: `CREATED → READY → RUNNING → {CHECKPOINTED, AWAITING_APPROVAL,
FAILOVER_PENDING, SUCCEEDED, FAILED, CANCELLED}`; `CHECKPOINTED → READY`
(resume possible); `FAILOVER_PENDING → READY` (new executor may claim);
`AWAITING_APPROVAL → READY` (approval granted) or `→ CANCELLED` (rejected/
expired). No transition mutates state in place without an append-only
event + fencing/CAS condition.

**Relationship to request/attempt**: reusable as the execution substrate —
each task stage creates a request, each request may have multiple
attempts, retries stay request-level, task-level state tracks progress
across requests — but request/attempt machinery alone is NOT a complete
task lifecycle.

## 3. Checkpoints

Durable, versioned progress artifact (not a log message): `task_id`,
`checkpoint_id`, monotonic sequence, completed stages, current stage,
resume input/artifact references, output/side-effect receipts, source
request/attempt IDs, executor identity, fencing epoch, content hash.
**Idempotent** — repeating with the same idempotency key returns the
existing checkpoint, never duplicates. Must not claim arbitrary external
side effects are reversible — records the last durable progress boundary
+ receipts needed to judge safe resumption. **Resume must begin from the
checkpoint's declared stage boundary, never an ambiguous point inside an
attempt.** If a stage may have produced duplicate external effects, the
task enters explicit recovery/operator-approval rather than silently
retrying.

## 4. `approval-request` — human authorization gate, distinct from consensus

Triggers: stage marked approval-gated by policy; action is
destructive/irreversible/externally-visible/materially consequential;
retry/failover risks duplicate side effects; checkpoint recovery can't
establish prior-attempt completion; operator explicitly requests approval;
policy detects a privilege/scope/target change. **Approval must NOT be
inferred merely because peers disagree** — peer consensus (gap-2) governs
system decisions, approval governs a human-controlled authorization
boundary; these are separate mechanisms.

Approval object: `approval_id`, `task_id`, `stage`, `requested_action`,
risk classification, evidence/checkpoint refs, requester, eligible-approver
policy, `state`, `decision`, approver identity, decision timestamp,
`expiry`, idempotency key. States: `REQUESTED → {APPROVED, REJECTED,
EXPIRED, WITHDRAWN}`. Bound to the task + exact stage + relevant
checkpoint + task-spec/action hash + current fencing epoch — **approval
for one version must not authorize a materially different version after
failover/mutation.**

Approver classes (policy-defined): named human operator, role-based
operator, room/session owner, configured approval group, emergency
approver (if enabled). **Peer processes must not impersonate human
approval** — a peer may recommend/gather-evidence/route, but the grant
must identify an authorized human or external authority.

While awaiting approval: task is NOT considered failed; execution lease
may be released/retained per policy; no new executor may continue the
gated stage without approval; checkpoints remain valid but aren't
permission to perform the gated action; failover may move
monitoring/resumption responsibility but must NOT bypass the approval
gate. If the approver is unavailable, the task may be failovered as an
administrative responsibility while staying `AWAITING_APPROVAL`.

## 5. `task-failover`

Triggers: explicit operator request; executor health/quarantine state
from gap-4; lease expiry/missed heartbeat; retry exhaustion where policy
permits reassignment; executor shutdown/capability loss; detected
fencing conflict/stale executor; approval-gated task whose executor is
gone. **Must not trigger on a single ordinary request failure** —
request-level retry is the first response unless failure classification
says the executor is unsuitable.

Protocol: read task projection + latest checkpoint → confirm current
assignment stale/revoked/expired/relinquished → append failover-intent
event under the task's fencing epoch → select candidate executor via
gap-4's health/capability/quarantine/role projections → create new fenced
task assignment → preserve old assignment as historical evidence (never
rewrite its ownership fields) → resume from latest valid checkpoint →
create new request/attempt tied to the task → emit
failover-completed/blocked event → require approval if recovery
uncertainty/side-effect-duplication risk exists.

Transfer set: task spec, current stage, latest valid checkpoint, artifact
refs, request/attempt history, approval state, policy/retry counters,
side-effect receipts, failure classification, fencing epoch/assignment
version. **Credentials, process handles, in-memory executor state are
NEVER transferred as durable task state** — new executor gets only
reconstructed, authorized state.

**Difference from gap-4's DutyAssignment failover**: gap-4 changes who
holds a *system duty* (leadership, room ownership, a role); task failover
changes who *executes one particular durable task*. Same fenced-assignment
substrate, separate projections — `DutyAssignment ≠ TaskAssignment`. A
peer may be healthy enough to hold a duty but unsuitable for a specific
task capability (and vice versa). Task failover consults `NodeRegistry`/
`PeerHealth` but must NOT mutate `DutyAssignment` merely because a task
moved.

## 6. Native command surface

`task create|status <id>|list [filters]|checkpoint <id> [stage]|resume
<id>|failover <id> [--target]|cancel <id>|approve <approval_id>|reject
<approval_id> [--reason]|approvals [filters]`. All mutating commands
support `--idempotency-key --expected-version --json`. Response fields:
`task_id, state, stage, executor, checkpoint_id, approval_id, request_id,
attempt_id, event_cursor, fencing_epoch`. CLI returns a **distinct
conflict result** for stale versions/lost fencing, not a generic failure.

## 7. Legacy compat mapping

| Legacy | Native | Notes |
|---|---|---|
| `task-status` | `task status` | Read projection; optionally include active request/attempt + checkpoint. |
| `task-checkpoint` | `task checkpoint` | Idempotent durable checkpoint for current stage. |
| `task-failover` | `task failover` | Task-level reassignment via the shared fenced-assignment substrate. |
| `approval-request` | `task approval-request`/`approve-request` | Creates an approval gate — NOT a consensus round. |

Adapter translates legacy args/output into gap-1's versioned envelope;
legacy semantics must never bypass native authorization/event-log/
fencing/approval checks. Richer native error codes exposed internally:
`TASK_NOT_FOUND`, `TASK_STATE_CONFLICT`, `CHECKPOINT_CONFLICT`,
`FAILOVER_NOT_ELIGIBLE`, `NO_EXECUTOR_AVAILABLE`, `APPROVAL_REQUIRED`,
`APPROVAL_EXPIRED`, `STALE_FENCE`, `DUPLICATE_SIDE_EFFECT_RISK`.

## Open questions requiring ratification (12)

1. Does peerhub already have a durable aggregate above requests, or only request/attempt projections (needs real source check)?
2. Do "task" identifiers already exist in persistence/CLI contracts?
3. Do checkpoint artifacts already have a canonical schema?
4. Can existing retry authorization be safely reused for task failover, or is task-level authorization needed separately?
5. May a task span rooms or sessions?
6. May tasks contain dependencies or multiple parallel stages?
7. Must an executor lease stay held while awaiting approval?
8. Authoritative approver model: named users, roles, room owners, or external identity provider?
9. Approval expiry behavior: auto-reject, re-request, or indefinite pending?
10. Are operator-selected failover targets allowed, or must routing always select the target?
11. Exact legacy exit codes/output formats for these 4 commands?
12. Policy for uncertain external side effects after executor loss?

## RECONCILIATION AGAINST REAL SOURCE (2026-08-24)

Real `peerhub/dispatch/retry_authorization.py` (`SameTargetRoute`,
`FailoverRoute`, `RetryAuthorizationBundle`, `RetryAuthorizationUnitOfWork`,
`RetryAuthorizationCoordinator`), `admission.py` (`AdmissionCoordinator`),
`artifact_coordination.py` (`ArtifactCoordinator`) confirmed via direct
read.

**`RetryAuthorizationCoordinator` does NOT eliminate the proposed Task
aggregate — it covers a different, narrower layer.**

| Concern | Existing likely coverage |
|---|---|
| Retry one failed execution | `RetryAuthorizationCoordinator` |
| Stay on same target | `SameTargetRoute` |
| Move execution elsewhere | `FailoverRoute` |
| Retry authorization/unit of work | `RetryAuthorizationUnitOfWork` |
| Multi-stage durable task identity | **Not established** |
| Several requests under one task | **Not established** |
| Task-wide checkpoints | **Not established** |
| Task-wide approval gates | **Not established** |
| Task history/progress across attempts | **Not established** |

**The Task aggregate is unnecessary ONLY IF a "task" is a thin alias for
one request + its attempts. It remains justified if a task means durable,
multi-stage work spanning multiple requests/attempts** — this is exactly
the distinction gap-5's original draft made from the command names
(`task-checkpoint` implies progress beyond one invocation), and nothing
in the real retry/failover code contradicts that reasoning; it just
confirms the EXECUTION-level retry/failover mechanics already exist
separately. Needs a body-level read of `RetryAuthorizationUnitOfWork` to
see if it already aggregates more than one attempt (would narrow or close
the remaining Task-aggregate justification).

**`ArtifactCoordinator` is NOT confirmed as checkpoint substrate** — the
original audit's `artifact-claim`/`artifact-status`/`artifact-finalize`
family suggests artifact (output/product) lifecycle management, not
task-progress checkpointing. A checkpoint also needs task/phase identity,
resumable execution position, causal parent attempt, checkpoint validity
state, recovery/resume policy — none demonstrated by `ArtifactCoordinator`
alone. Treat as unconfirmed pending a field/body read; a checkpoint might
REFERENCE/publish an artifact without artifact coordination itself
providing checkpoint semantics.

**`AdmissionCoordinator` is PROBABLY operational/capability admission,
NOT gap-5's human `approval-request` gate** — context: gap-4 already has
`AdmissionState`, `CHECK_USAGE_ADMISSION` is a health/admission stage;
this points to peer/request admission based on usage/capacity/capability/
health, materially different from a human authorization workflow. Do
NOT conflate without implementation evidence — `approval-request` remains
undesigned unless `AdmissionCoordinator`'s fields explicitly contain
approver identity, approval state, expiry, denial, audit provenance
(needs a body/field-level read).

### Revised "Task-failover" section

Request/attempt retry and failover should use the existing
`RetryAuthorizationCoordinator`/`RetryAuthorizationUnitOfWork`
(`SameTargetRoute` = retry on original target, `FailoverRoute` =
authorized route to another executor) — this covers the
EXECUTION-LEVEL portion of `task-failover`. A separate Task aggregate
remains necessary only when the product-level task spans multiple
requests/stages/attempts and needs durable progress, checkpoints,
approval state, or task-wide recovery history — this boundary must be
verified from the coordinator's actual fields/transition logic, not
assumed. **`task-failover` should NOT be implemented as a second failover
engine — either expose the existing request/attempt failover path, or add
a clearly-defined task-level orchestration layer above it (not a
replacement for it).**

### Revised "Native command surface" section

Native commands should expose existing dispatch primitives, not duplicate
their mechanics: task execution/retry → `RetryAuthorizationCoordinator`;
same-target retry → `SameTargetRoute`; executor failover → `FailoverRoute`;
retry decision/UoW state → `RetryAuthorizationUnitOfWork`; session
continuity → `SessionLeaseCoordinator`; liveness →
`HeartbeatWorker`/`LeaseRenewer`; artifact lifecycle → `ArtifactCoordinator`
(only where genuinely artifact-scoped); operational admission →
`AdmissionCoordinator`. **Still-missing (genuine gaps)**: durable
multi-stage Task identity (if required), task checkpoint/resume
semantics, human `approval-request`+resolution, explicit room/thread
operations (shared with gap-3), terminal-duty handoff + no-auto-replay
policy (shared with gap-3).

## FIELD-LEVEL CONFIRMATION (2026-08-24, terminal): Task aggregate is CONFIRMED necessary, not redundant

Direct read of `peerhub/dispatch/retry_authorization.py`'s real
`RetryAuthorizationBundle`:

```python
class RetryAuthorizationBundle:
    """The records committed by one retry-authorization transaction."""
    request: RequestSnapshot
    previous_attempt: AttemptSnapshot
    session_lease: LeaseSnapshot
    capability_lease: CapabilityLease
    route_decision: RouteDecision
```

This is scoped to exactly ONE request + its previous attempt + one new
route decision — there is no concept anywhere in this bundle of multiple
requests grouped under a shared durable identity. **This definitively
answers gap-5's central open question: the proposed Task aggregate is
CONFIRMED necessary, not redundant with `RetryAuthorizationCoordinator`**
— the real retry/failover machinery operates one level below where a
multi-stage task (spanning several requests, needing durable checkpoints
and task-wide progress) would need to live. `RetryAuthorizationCoordinator`
remains the right substrate for retrying/failing-over ONE request within
a task's execution, but a Task itself (using the same `TargetState`/
`MutationRequest` governed-mutation pattern confirmed in gap-2/gap-6's
field-level check) is real, necessary, not-yet-designed-in-code work.

This closes the loop on gap-5's design: **a Task = a `TargetState` (like
consensus rounds and governance artifacts) whose state blob tracks stage/
checkpoint/approval progress, and whose stage transitions each dispatch a
request through the existing request/attempt/retry-authorization
machinery** — the same layering pattern now confirmed across gaps 2, 3,
4, 5, and 6: **generic governed-mutation broker + generic request/attempt
execution machinery underneath, with each gap's own domain aggregate
(`TargetState` instance) and domain-specific coordinator logic on top.**

## CONCRETE SCHEMA DESIGN (2026-08-26, cx): task `TargetState.state`

Mirrors gap-2/gap-6's treatment. `TargetState.state` is the canonical
task snapshot; attempts/requests/checkpoints/approval gates stay
separately addressable objects (same "keep canonical revision
independent from conversational back-and-forth" principle as gap-6's
lesson-delivery targets).

Envelope (`schema: "peerhub.task-state/v1"`): `task_id`, `objective{summary,
spec}`, `current_stage`, `state`, `executor{binding_state, coordinator,
session_lease_id, capability_lease_id, route_decision_id,
active_request_id}`, `checkpoint` (null until first checkpoint),
`child_request_ids[]`, `active_attempt_id`, `failure{count,
last_failure_id/class/at}`, `failover{count, last_failover_id/reason/at}`,
`approval{active_request_target_id, required}`, `timestamps{created_at,
started_at, completed_at, updated_at}`.

**`executor` references the real dispatch substrate directly** — points
to the `SessionLeaseCoordinator` binding result (session lease/capability
lease/route decision), NOT `RetryAuthorizationCoordinator` (which stays
request-scoped, referenced per individual attempt/request instead).

At `CHECKPOINTED`: `checkpoint{checkpoint_id, stage, request_id,
attempt_id, captured_at, resume_token_ref, state_digest,
completed_units[], remaining_units[]}` populated, `executor.binding_state:
"BOUND"`.

At `AWAITING_APPROVAL`: `approval{active_request_target_id: "approval-...",
required: true}` — **the task stores ONLY the approval target reference
and gate status; the approval object owns its own conversation and
revision history as a separate TargetState.**

### Operations → transitions

| Operation | Computation |
|---|---|
| `task.create` | Initial snapshot, `state="CREATED"`, empty children, no executor/checkpoint, zero counters. |
| `task.claim_start` | Validate `state∈{CREATED,READY}`; create/attach stage's first request+attempt; bind `SessionLeaseCoordinator` lease/route/capability; `state="RUNNING"`. |
| `task.checkpoint` | Validate active request/attempt+digest; persist checkpoint ref+completed/remaining units; `state="CHECKPOINTED"`; preserve executor binding if lease still valid. |
| `task.request_approval` | Create separate `governance.approval.request` target; `approval.required=true`+target ID; `state="AWAITING_APPROVAL"`. |
| `task.approval_granted` | Require terminal approval record with validated authority evidence; clear/archive approval ref; `approval.required=false`; → `READY`/`RUNNING`. |
| `task.approval_rejected` | Require valid terminal rejection record; clear gate; → `FAILED`/`CANCELLED` per policy; record as terminal failure/reason. |
| `task.request_failover` | Validate current request/attempt can't continue; increment `failover.count`; record event; invalidate/retire old executor binding; → `FAILOVER_PENDING`/`READY`. |
| `task.complete` | Validate all required stages/outputs; `state="SUCCEEDED"`; clear active attempt; set `completed_at`; retain final refs for audit. |
| `task.fail` | Validate terminal failure classification; increment `failure.count`; record metadata; `state="FAILED"`; clear active attempt; `completed_at`. |
| `task.cancel` | Validate not already terminal; record reason/actor; `state="CANCELLED"`; clear execution; `completed_at`. |

State machine: `CREATED → {READY,RUNNING,CANCELLED}`; `READY →
{RUNNING,CANCELLED}`; `RUNNING → {CHECKPOINTED, AWAITING_APPROVAL,
FAILOVER_PENDING, SUCCEEDED, FAILED, CANCELLED}`; `CHECKPOINTED →
{READY,RUNNING,AWAITING_APPROVAL}`; `FAILOVER_PENDING →
{READY,RUNNING,FAILED,CANCELLED}`; `AWAITING_APPROVAL →
{READY,RUNNING,FAILED,CANCELLED}`. Every op is CAS-checked
(`next_revision == previous_revision+1`), rejected if invalid for the
current snapshot.

### Approval gate = its OWN separate `TargetState` (confirmed choice, same principle as gap-6)

`governance.approval.request:<approval_id>`, envelope
`peerhub.governance.approval-request-state/v1`: `approval_id,
approval_type, subject{task_target_id, task_revision, operation, stage,
requested_effect}, state, requested_by{actor_id, request_id,
requested_at}, authority_policy{required_role, required_scope,
required_count}, decision, audit{created_at, updated_at, event_ids[]}`.
States: `PENDING → {GRANTED, REJECTED, EXPIRED, WITHDRAWN}`.

**Decision requires real authority evidence, never a bare claim**:
`decision.authority{actor_id: "human:<id>", authority_class:
"human_authority", credential_evidence{evidence_type: "unresolved" for
now, credential_id, verification_status, verified_by, verified_at},
scope[]}`. **`actor_id:"human"` alone is never sufficient — the broker
or an authority-verifier must validate `credential_evidence` + scope +
verification status. A peer may submit a request or relay a human
decision, but cannot manufacture one.** Exact credential format/
verification service deliberately left as an unresolved adapter
boundary — not guessed.

### Stage ≠ exactly one request (confirmed)

A stage is a task-level unit that may need one request, multiple
sequential requests, retries (multiple attempts), failover to a new
request, or compensating/verification requests:
`task → stage → {request → attempt(s)}`, potentially multiple requests
per stage. Because `RetryAuthorizationCoordinator` binds exactly ONE
request+its previous attempt (field-confirmed earlier), **request-level
retry = same request + new authorized attempt**, while **task-level
failover is structurally broader**: retire/invalidate the failed
request/attempt binding → select/create a NEW request for the current
stage → obtain a new request-scoped retry-authorization bundle →
obtain/bind a new execution lease+route decision → continue the task.
**`task-failover` is NOT an alias for request retry — it changes task
execution ownership and may create a new child request while preserving
the task's stage/objective/history/aggregate identity.**
`child_request_ids` should be append-only (retired requests stay
listed); `active_request_id` identifies the current owner.

### Unresolved (needs source access or policy decision)

Exact canonical field names/IDs `SessionLeaseCoordinator` actually
emits; are capability leases/route decisions independently addressable
targets or embedded records; the authoritative task-state enum and
whether `READY` is mandatory post-creation/failover; is checkpoint data
an artifact reference, opaque resume token, or both; exact legacy-
command-to-operation mapping for every task command; does
`approval_rejected` mean terminal `FAILED`, terminal `CANCELLED`, or
policy-dependent nonterminal; authority credential format/verification
service; are approval expiry/withdrawal required in v1; is
`current_stage` free-form or must reference a versioned stage-plan
object; may `child_request_ids` include parallel requests or is
execution strictly sequential; canonical failure/failover event-history
representation (examples here only keep counters+last-event summary — a
separate append-only audit stream may still be needed).
