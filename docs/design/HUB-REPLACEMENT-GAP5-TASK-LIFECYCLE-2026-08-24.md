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
