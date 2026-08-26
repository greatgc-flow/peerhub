# Gap 4 Design: Health, Quarantine, Leadership, Role Operations (DRAFT — architecture proposed, 11 items need ratification)

Status: first-round draft from `cx`, 2026-08-24. Covers `peer-quarantine`,
`peer-recover`, `health-update`, `health-check`, `health-precheck`,
`health-sweep`, `peer-status`, `freshness-sweep`, `register-node`,
`list-nodes`, `discover`, `elect-leader`, `leader-claim`, `leader-yield`,
`assign-role`, `release-role`, `role-status`, `profile-validate`,
`model-status`. `cx` could not access `peerhub`/prior gap docs from its
sandbox this round; grounded in real `_sys/docs-v2/` protocol/routing SSOT
where cited.

## 1. Native model — 3 separate projections, NOT one object

`NodeRegistry` ≠ `PeerHealth` ≠ `DutyAssignments` (may share an event log,
must not collapse into one state object).

**NodeRegistry**: `node_id`, `kind`, `status`, `capabilities`, `adapter`,
`profile_ids`, version fields. States: `DISCOVERED → REGISTERED → ACTIVE
↘ DISABLED`. `DISCOVERED` is observational only (not routable); `ACTIVE` =
eligible for health evaluation, not automatically healthy.

**PeerHealth**: `context_status`, `gate`, `quarantine`, `staleness`,
`consecutive_failures`, `quota{state,source}`, `exh{state,source}`,
`evidence[]`. Canonical routability predicate: `registered AND active AND
context_status∈{GREEN,YELLOW} AND gate=OPEN AND quarantine=NONE AND
staleness≠STALE`. **Quota/EXH are provenance-bearing observations only —
never inferred from health failures/elapsed time/routing behavior; `UNKNOWN`
if no measured source.**

**Relationship to gap-3's terminal-duty leases**: genuinely SEPARATE
concepts. Health answers "may this node receive work?"; a duty lease
answers "which node owns this duty?" A healthy peer may hold no duty; a
lease may expire while the peer stays healthy. Health is node-scoped +
evidence-derived; duty is scope/task/room-scoped + an ownership/concurrency
control. They interact via admission rules: duty claim requires routable
health; a health transition to RED/STALE/quarantine fences or revokes
eligible duties; lease expiry triggers reassignment eligibility, never
automatic replay.

## 2. Quarantine/recovery state machine

`NONE → {AUTO_QUARANTINED (automatic trigger) | OPERATOR_QUARANTINED
(operator cmd) | DISABLED (registry node disabled)}`. Recovery:
`AUTO_QUARANTINED|OPERATOR_QUARANTINED → RECOVERY_PENDING → PROBING →
NONE` (or back to `QUARANTINED` on failed probe). `DISABLED` is a
registry/lifecycle state, NOT clearable by ordinary `peer-recover`.

**Automatic quarantine triggers**: critical transport failure (missing
CLI/spawn denial), failure threshold reached, explicit gate closure,
repeated health-update/state-integrity failure, unreconcilable stale
ownership, identity/session-fingerprint mismatch, repeated recovery
failure. Exact thresholds must come from config/measured policy — never
invented.

**Operator quarantine** records `{reason, actor, at, source:"operator",
expected_recovery}` and takes precedence over automatic recovery — a
successful probe must NOT silently clear it.

**Recovery requirements**: quarantine reason+prior state present; root-cause
remediation declared/externally completed; a minimal local diagnostic
succeeds; identity/fingerprint unchanged or explicitly re-registered;
health evidence fresh; gate reopened; quarantine cleared only if it was
automatic, or an explicit operator override is supplied for operator
quarantine. **Recovery is NOT equivalent to `health-update --status
GREEN`.**

**Cooldown/backoff**: `quarantine → cooldown → probe → recover or extend
cooldown`, bounded and config-driven; event record includes attempt
number + next eligible probe time. No automatic retry replays tasks or
claims leadership.

## 3. Leadership and roles

**Conclusion**: leadership and general roles use the SAME underlying
assignment substrate as gap-3's terminal-duty leases, but with different
semantic types/policies — `DutyAssignment{terminal_duty,
coordinator_leadership, implementer, verifier, researcher, documenter,
observer}`. One concurrency-safe ownership mechanism, distinct semantics
per type.

**Why not fully identical to gap-3's duty**: leadership has EXTRA rules
ordinary roles don't need — challenge window for `leader-claim`,
coordinator history, consecutive-term limit, election scoring,
health/quota-based eligibility, graceful yield vs forced failover
semantics, human escalation when no suitable leader exists. So: same
lease/assignment substrate + role-specific policy layered on top.

Assignment record: `assignment_id`, `scope{room_id,task_id}`, `role`,
`peer_id`, `status`, `fencing_token`, `claimed_at`, `expires_at`,
`challenge_until`, `reason`, `version`. `PENDING` required for leadership
claims during the challenge window; ordinary roles may go straight to
`ACTIVE` unless policy requires review. Leadership eligibility requires:
registered+ACTIVE, health routable, no active quarantine, no conflicting
fenced assignment, term-limit policy satisfied. **A yield is voluntary
release; a failover is forced replacement (health/expiry/fencing) — must
stay distinguishable in the event log.**

## 4. Registration and discovery

Per the real routing SSOT: no generated node list exists; registration is
represented via configured node IDs + `peers.json`; routing/peer-target
resolution is only partially available internally (matches the original
gap audit's finding). Proposed model: durable registration is explicit;
discovery is observational; resolution produces candidates; health
admission decides routability; routing selection decides the target.
Pipeline: `discover → observed candidates → register/reconcile →
health-precheck → capability/profile resolution → routable candidate set`.
**Discovery must NOT silently mutate durable registration** unless
explicitly requested — `discover`=read-only observation,
`register-node`=durable mutation, `list-nodes`=registry projection.
Registration idempotent by stable `node_id`+protocol/profile identity; a
changed fingerprint produces a reconciliation event, never a silent
overwrite.

## 5. Native command surface + legacy mapping

| Legacy | Native | Mutation |
|---|---|---|
| `peer-quarantine` | `health.quarantine.request` | Yes |
| `peer-recover` | `health.recovery.request` | Yes |
| `health-update` | `health.observation.record` | Yes |
| `health-check` | `health.projection.read` | No |
| `health-precheck` | `health.admission.evaluate` | No |
| `health-sweep` | `health.staleness.sweep` | Bounded system mutation |
| `peer-status` | `peer.status.read` | No |
| `freshness-sweep` | `health.freshness.sweep` | Bounded system mutation |
| `register-node` | `registry.node.register` | Yes |
| `list-nodes` | `registry.nodes.list` | No |
| `discover` | `registry.discovery.observe` | No by default |
| `elect-leader` | `assignment.leader.select` | Yes |
| `leader-claim` | `assignment.leader.claim` | Yes |
| `leader-yield` | `assignment.leader.release` | Yes |
| `assign-role` | `assignment.role.assign` | Yes |
| `release-role` | `assignment.role.release` | Yes |
| `role-status` | `assignment.roles.read` | No |
| `profile-validate` | `registry.profile.validate` | No |
| `model-status` | `capability.model_status.read` | No |

Envelope: `{protocol_major, protocol_minor, schema_version:"health-role.v1",
operation, request_id, idempotency_key, scope, payload, evidence,
extensions}` — same gap-1 boundary, compat layer translates, peerhub owns
semantics.

**Important distinctions to preserve, NOT alias**: `peer-status`
(canonical operator projection, orchestration-filtered) ≠ `health-check`
(diagnostic/audit projection, may include maintenance/recovery detail).
`health-sweep` (recompute health transitions + quarantine consequences) ≠
`freshness-sweep` (identify stale observations/leases/registrations/
discovery records). `model-status` must report ONLY measured or
declared-with-provenance model facts — never infer quota/EXH/availability
from health.

## 6. Event/projection requirements

All mutations append events + update materialized projections via the
existing journal/CAS discipline. Representative events: `node.registered`,
`node.reconciled`, `node.disabled`, `health.observed`,
`health.transitioned`, `health.quarantined`, `health.recovery_requested`,
`health.recovery_probe_passed`, `health.recovered`, `health.stale_detected`,
`leader.claim_pending/claimed/challenged/yielded`, `role.assigned/released`,
`assignment.fenced`. Every event carries `event_id`, `event_type`,
`occurred_at`, `actor`, `node_id`/`peer_id`, `scope`, `prior_version`,
`new_version`, `fencing_token` (where applicable), `source/evidence`,
`reason`. Projections must be rebuildable from events — a stale/corrupt
projection must NEVER be repaired by manually setting `GREEN`, assigning a
leader, or clearing quarantine without a corresponding event.

## Open questions requiring ratification (11)

1. peerhub's actual source model — does it already have equivalent registry/health/assignment primitives (needs checking real peerhub source)?
2. Canonical identity: peer ID, node ID, installation ID, adapter/profile fingerprint, or composite?
3. Health ownership: adapter-owned, peerhub-owned, or both with provenance rules?
4. Automatic-quarantine authority: which subsystem may quarantine automatically vs requires operator approval?
5. Exact recovery policy: failure thresholds, cooldown duration, retry/backoff limits, can operator quarantine ever auto-clear?
6. Lease interaction: does RED/STALE immediately revoke all assignments, or just prevent renewal pending a fencing sweep?
7. Leadership scope: room-scoped, task-scoped, global, or multi-scope simultaneously?
8. Role multiplicity: can a peer hold multiple roles, can a role have multiple peers?
9. Discovery trust: can discovery create registrations, or only report candidates for explicit registration?
10. Quota/EXH semantics: does peerhub own these measurements, or just store adapter-provided observations (no inference)?
11. Model-status authority: which source wins when config declarations and live adapter observations disagree?

## Ratifiable design position

Adopt a unified append-only event+projection architecture with 3 separate
semantic domains (`NodeRegistry ≠ PeerHealth ≠ DutyAssignment`). Use one
fenced assignment substrate for terminal duty, leadership, and general
roles, with leadership-specific challenge/election/term-limit policy
layered on top. Treat health/quarantine as node admission state, not a
lease. Preserve strict evidence provenance, explicit recovery, bounded
sweeps, and gap-1's compat translation for the full legacy command surface.
Defer the 11 items above pending peerhub-source inspection and user input.

## RECONCILIATION AGAINST REAL SOURCE (2026-08-24)

Per `HUB-REPLACEMENT-REAL-SOURCE-GROUNDTRUTH-2026-08-24.md`, the terminal
read `peerhub/health/contract.py`/`model.py` directly and fed the real
types to `cx` for reconciliation. **Gap-4's generic `NONE|
AUTO_QUARANTINED|OPERATOR_QUARANTINED|DISABLED` quarantine state machine
is SUPERSEDED — use the real types below instead.**

### Mapping (gap-4 concept → real substrate)

| Gap-4 concept | Real peerhub substrate |
|---|---|
| `NodeRegistry` | **No direct equivalent found** — genuinely still undesigned (see below) |
| `PeerHealth` | `AvailabilityState`, `ReadinessState`, `HealthStage`, `HealthStageStatus`, `HealthFailureClassification`, `HealthCircuitSnapshot`, `HealthProjectionSnapshot` |
| `DutyAssignment` | Partially = `SessionLeaseCoordinator` + lease/fence types; **leadership-specific semantics (election, challenge window, term limits) are NOT covered — still a real gap** |
| Generic quarantine enum | `AdmissionState.QUARANTINED` + `QuarantineAuthorityClass` (orthogonal dimension) |
| Recovery/probing | `PROBE_AUTHORIZED` + `RecoveryProbeGrant/Authorization/ClaimResult/Receipt/Application` + `ProbeResult`/`ProbeDisposition`/`ProbeTransition` |

**Real model is MORE precise than gap-4's draft — 4 separate orthogonal
axes, never collapse into one enum**: `AvailabilityState` (operational
availability: UNKNOWN/PROBING/HEALTHY/DEGRADED/UNAVAILABLE/STALE),
`ReadinessState` (is the latest evaluation usable:
READY/PROBE_INCONCLUSIVE/READINESS_STALE), `AdmissionState` (may work be
admitted: OPEN/PROBE_AUTHORIZED/RECOVERY_REQUIRED/COOLDOWN/QUARANTINED,
confirmed precedence order), `HealthStage` (WHERE validation
failed/succeeded — 6-stage pipeline), `CircuitState` (circuit-breaker:
CIRCUIT_OPEN/CIRCUIT_CLOSED), `QuarantineAuthorityClass` (WHY quarantine
exists: AUTOMATIC/MANUAL/SECURITY/POLICY — this IS the AUTO- vs
OPERATOR-quarantine distinction gap-4 wanted, plus 2 more cases).

### Usage/quota tracking

`HealthStage.CHECK_USAGE_ADMISSION` is the correct native replacement for
gap-4's "quota/EXH tracking" AT THE STAGE-CLASSIFICATION LEVEL only.
**Does NOT prove** a full quota/EXH data model (counters, windows,
exhaustion accounting, quota-family aggregation, reset timing,
reservation/consumption semantics) exists — that remains unverified
unless found in `HealthPolicy`/`PolicyAction` fields (not yet inspected).

### Canonical identity (open question #2) — NOT answered by `PolicyScope`

`PolicyScope` (ROOT/PROFILE/QUOTA_FAMILY/ENVIRONMENT) is policy TOPOLOGY,
not object identity — doesn't resolve peer-ID vs node-ID vs
installation-ID vs fingerprint vs composite. `EvidenceSubject`,
`HealthScopeBinding`, `HealthScopeMembershipSnapshot` look more relevant
to identity/scope-membership but their fields weren't inspected yet —
**RESOLVED later in this same document, see "FIELD-LEVEL CONFIRMATION"
below: canonical identity is a composite `(instance_id, profile_id)`
pair.** (Left as a historical record of the open question at the time
it was written, not a currently-open item.)

### `SessionLeaseCoordinator` for leadership — reuse the substrate, NOT a complete leadership implementation

Appropriate for fencing/lifetime mechanics (create/renew/close/expire-
recover/validate-fence + `LeaseSnapshot`/`SessionBindingKey`/
`SessionBindingSnapshot`/`RecoveryReceipt`/`RecoveryTrigger`). **Does
NOT demonstrate**: election/candidate ordering, challenge window, a
`PENDING` assignment sub-state, term numbers/epochs/limits, incumbent-
challenge rules, explicit role ownership, split-brain prevention beyond
lease-fence validation. A challenge window could be layered ABOVE the
coordinator (new leadership state/policy layer) — **no evidence `PENDING`
is already a supported lease sub-state; this needs new code or a
confirmed existing extension point.**

### Registration/discovery — CONFIRMED still genuinely undesigned

No supplied health/routing type is a direct counterpart to
`register-node`/`list-nodes`/`discover`. `HealthScopeBinding`/
`HealthScopeMembershipSnapshot` may support membership relationships but
don't establish node registration/discovery APIs. **Gap-4 should keep
`NodeRegistry` as an explicitly open design area — do not prematurely
spec it until canonical identity + membership types are inspected at the
field level.**

### Revised quarantine/recovery flow (replaces the original section)

`AdmissionState.OPEN` (normal) → `PROBE_AUTHORIZED` (recovery testing
explicitly authorized) → `RECOVERY_REQUIRED` (recovery needed before
normal admission) → `COOLDOWN` (repeated/policy-defined failure) →
`QUARANTINED`. Authority carried SEPARATELY via `QuarantineAuthorityClass`
(AUTOMATIC=health-driven, MANUAL=operator, SECURITY=security enforcement,
POLICY=policy enforcement). Recovery uses the real probe types; probe
execution = `ProbeResult`/`ProbeDisposition`; transitions include
`FAILURE_BACKOFF_INCREMENTED`, `SUCCESS_CIRCUIT_CLOSED`,
`STALE_PROBE_NO_OP`. **`AvailabilityState`/`ReadinessState`/
`CircuitState`/`AdmissionState` must stay 4 separate axes, never
collapsed into one enum** — a successful probe may affect circuit+admission
state but doesn't automatically mean healthy/ready.

**Gap-4 items retained only where NOT demonstrated by real types**:
explicit transition-audit requirements, authority/actor provenance (if
not already policy/evidence dataclass fields), any permanent-`DISABLED`
state (no real equivalent found), challenge-window/leadership-specific
recovery, detailed quota/EXH accounting beyond `CHECK_USAGE_ADMISSION`.

**Next step for this category**: field-level read of `HealthPolicy`,
`EvidenceSubject`, `HealthScopeBinding`, `HealthScopeMembershipSnapshot`,
`AdmissionSnapshotEntry`/`AdmissionSnapshot` to resolve canonical identity
and confirm/deny quota-accounting presence.

## FIELD-LEVEL CONFIRMATION (2026-08-24, terminal): SessionLeaseCoordinator's real lease type is session/attempt-SPECIFIC, not generic

Direct read of `peerhub/dispatch/contract.py`'s real `LeaseCreateRequest`:

```python
class LeaseCreateRequest:
    session_id: str                                    # REQUIRED
    owner_principal_id: str
    owner_instance_id: str
    owner_process_birth_identity: ProcessBirthIdentity  # process-specific
    heartbeat_timeout_ms: int
    command_id: CommandID                               # REQUIRED
    attempt_id: str                                      # REQUIRED
    authority_epoch: int                                 # = the fencing token concept
    owner_peer_id: str = ""
```

**This settles gap-4's open question about leadership reuse: NO, not
directly.** `LeaseCreateRequest` is tightly coupled to one session +
command + attempt execution (`session_id`/`command_id`/`attempt_id` are
all required, non-optional fields) — it cannot represent a room-scoped
leadership/terminal-duty lease that isn't tied to a single command
execution. **Gap-4's `DutyAssignment` (leadership/roles) needs its OWN
analogous-but-separate lease request type** — same PATTERN
(`authority_epoch` is exactly the fencing-token concept gap-4 wanted;
`owner_principal_id`/`owner_instance_id` are directly reusable identity
concepts), but not the same concrete type. This confirms (with field-level
evidence, not just a name-level guess) the earlier reconciliation's
caution that "genericity is not yet proven" — it's now proven NOT
generic, in its current form.

**Recommended path**: model a new `DutyLeaseCreateRequest` (or similar)
on this exact same field pattern minus the session/command/attempt-
specific fields, scoped to `room_id`/`role` instead. This is real,
concrete follow-up design work now that the "just reuse
SessionLeaseCoordinator" hope is field-level ruled out.

## FIELD-LEVEL CONFIRMATION (2026-08-24, terminal): canonical identity is a composite (instance_id, profile_id) pair

Direct read of `peerhub/health/contract.py`:

```python
class EvidenceSubject:
    scope: PolicyScope
    subject: str

class HealthScopeBinding:
    scope: PolicyScope
    subject: str
    members: tuple[tuple[str, str], ...]   # each member = (instance_id, profile_id)
```

**This resolves gap-4's open question #2 (canonical identity)**:
identity is NOT a single peer-ID string — it's consistently a
**composite `(instance_id, profile_id)` pair**, matching
`SessionBindingKey`'s own 4-part key (`workspace_scope_id, instance_id,
profile_id, conversation_scope` — see gap-3's reconciliation) which uses
the same two fields. A "subject" at the health layer is a
`(PolicyScope, subject_string)` pair, and scope-membership groups
multiple `(instance_id, profile_id)` members under one subject. This is
now confirmed, not inferred: any `NodeRegistry`/peer-identity design for
gap-4 should use `(instance_id, profile_id)` as its composite key,
consistent with the rest of the real codebase, not invent a different
identity shape.

## CONCRETE DESIGN (2026-08-26, cx): `DutyLeaseCreateRequest` — dedicated lease machinery, NOT the governed-mutation broker

**Decision: duty leases use their OWN dedicated lease-request/snapshot
types + a lightweight `DutyLeaseCoordinator`, modeled on
`SessionLeaseCoordinator`'s real shape — NOT the `TargetState`/
governed-mutation-broker substrate gap-2/5/6 use.** Reasoning: a
heartbeat every few seconds/minutes extends liveness, not a business-
state transition — needs to be cheap, frequent, immediately fence-
validated; routing every renewal through
`MutationRequest→MutationPlan→TransitionReceipt→OutboxEvent` would add
unnecessary protocol/storage pressure and blur liveness vs. governed
business mutations. **The generic broker MAY still govern slower
leadership-domain state** (election policy, candidate/challenge status,
audit-oriented leadership state) as its own `TargetState` — cross-
referenced with the lease record via `lease_id`/`(room_id,role)`/`term`,
never duplicating authority semantics in two independently-writable
records.

```python
@dataclass(frozen=True)
class DutyOwnerIdentity:
    instance_id: str
    profile_id: str

@dataclass(frozen=True)
class DutyLeaseCreateRequest:
    room_id: str            # coordination scope -- a duty belongs to a room, not a session/command
    role: str                # e.g. "terminal-duty", "domain-coordinator" -- lease key = (room_id, role)
    owner: DutyOwnerIdentity  # confirmed composite identity, matches HealthScopeBinding/SessionBindingKey
    owner_principal_id: str   # who/what authorized the holder (audit), distinct from runtime identity
    heartbeat_timeout_ms: int
    authority_epoch: int      # fencing token -- advances on every acquisition/recovery
    term: int = 1             # leadership generation/election term, monotonic per (room_id, role)
    challenge_until: int | None = None  # challenge/handoff window deadline -- policy state, NOT proof of ownership
```

**Excluded from real `LeaseCreateRequest` and why**: `session_id`
(duty is room/role-scoped, not session-scoped), `command_id`/`attempt_id`
(a duty spans many commands/attempts, must survive across them),
`owner_process_birth_identity` (canonical identity is confirmed
`(instance_id, profile_id)`; if terminal duty needs process-binding for
safety, that's a separate optional field — a real, undecided policy
choice, not established by current sources), `owner_peer_id` (redundant/
ambiguous with the composite identity).

`DutyLeaseSnapshot{lease_id, room_id, role, owner, owner_principal_id,
authority_epoch, term, challenge_until, state: DutyLeaseState,
heartbeat_expires_at, created_at, updated_at}`. Uniqueness: one active
lease per `(room_id, role)`, replaceable only when expired/released/
recovered-and-fenced.

### Operation set (mirrors real `SessionLeaseCoordinator` method names)

`DutyLeaseCoordinator.create_lease(request) -> DutyLeaseSnapshot`
(claims an unheld/expired/recoverable room+role duty);
`renew_lease(request: DutyLeaseRenewRequest, *, heartbeat_timeout_ms) ->
DutyLeaseSnapshot` (extends expiry if lease_id+owner+term+fence all
current); `close_lease(request: DutyLeaseCloseRequest) ->
DutyLeaseSnapshot` (voluntary release); `expire_and_recover_lease(
lease_id, *, recovery_actor_principal_id, trigger: RecoveryTrigger,
evidence_digest, policy_id, policy_revision) -> (DutyLeaseSnapshot,
RecoveryReceipt)` (fences an expired/stale holder, records recovery
evidence); `validate_lease_fence(request: DutyLeaseFenceCheckRequest) ->
(bool, tuple[str,...])` (validates lease_id+room+role+owner+term+
authority_epoch). Supporting request types (`DutyLeaseRenewRequest`/
`DutyLeaseCloseRequest`/`DutyLeaseFenceCheckRequest`) all carry the FULL
immutable fence context (lease_id, room_id, role, owner, term,
authority_epoch), not just a bare lease ID. **`create_lease` allocates
a new lease ID + fencing epoch atomically; a challenger does NOT acquire
the role merely because `challenge_until` passed — acquisition still
needs the expiry/recovery rule AND a strictly newer term.**

### CONFIRMED (2026-08-26, direct real `hub.py` source read): exact challenge protocol

Read `action_leader_claim()` / `action_leader_yield()`,
`P:\_sys\core\hub.py:8698-8793`, directly — answers all three parts of
the "exact challenge protocol" question below, plus surfaces one real
authority rule (AP-20) the design hadn't captured yet:

- **Who may challenge**: any peer that is not the current claimant, by
  simply calling leader-claim again — no membership/eligibility filter
  beyond that.
- **Can an incumbent veto**: **no.** There is no incumbent-veto code path.
  While `now < challenge_until`, a second claim is unconditionally
  accepted and overwrites the pending claim (logged as `CHALLENGE:
  {agent} is challenging {current_leader}'s pending claim`). **Real
  discrepancy worth flagging**: the inline comment above this branch says
  "Score 기반 경합" (score-based contention), but the actual code has no
  scoring — it is a bare last-writer-wins overwrite. This is a
  comment/behavior mismatch in the legacy system itself, not a
  misunderstanding on our part. **Policy decision needed**: should
  peerhub's native version faithfully replicate the current toothless
  overwrite-on-challenge behavior (byte-for-byte legacy fidelity), or
  actually implement the score-based arbitration the legacy comment
  describes but the legacy code never built (a genuine behavior
  improvement, not just a port)?
- **Is the challenge window advisory or mandatory**: **mandatory as an
  outer gate, permissive inside it.** Outside the window (`challenge_until`
  absent or expired), a claim is only accepted if the incumbent's health
  is `RED` or `STALE`; otherwise `sys.exit(1)` with "still active and
  healthy." Inside the window, any claim is accepted with no further
  check. So the window mandatorily blocks claims against a healthy
  incumbent, but does nothing to arbitrate between challengers once open.
- **New rule not previously in this design — AP-20 Coordinator Monopoly
  Guard**: before granting a claim, `hub.py` checks the last
  `yield_failure_threshold` (config: `protocol.json["leader_election"]
  ["yield_failure_threshold"]`, default 3) entries of `coordinator_history`;
  if all of them are the same claiming peer, the claim is rejected
  outright (`sys.exit(1)`, logged via `_record_ap20_runtime_directive`) —
  a peer cannot hold N consecutive terms without an intervening yield to
  someone else. **This must be carried into the native `DutyLeaseCoordinator`
  design as a required precondition on `create`/`claim`**, not just on
  `renew` — it was missing from the field list above and needs adding
  (e.g. a `recent_holders: list[tuple[peer_id, term]]` check, or an
  explicit `consecutive_terms` counter on the duty-lease projection).
- **Yield safety hook (confirmed, not previously documented)**:
  `action_leader_yield` checkpoints active tasks first (`_checkpoint_active_tasks`)
  whenever the yield `reason` contains any of `context/health/rate/limit/
  failure/degraded` — a pressure-yield triggers a checkpoint-before-release;
  a plain/manual yield does not. Native duty-lease `release` should
  preserve this same conditional-checkpoint behavior, not drop it as an
  unmodeled side effect.

### Unresolved (needs more source or explicit policy decision)

`SessionLeaseCoordinator`'s actual method BODIES weren't available this
round for the LEASE (not leadership) side (only signatures were used) —
its real design doesn't establish leadership-specific election semantics
beyond what's now confirmed above, so the lease-fencing mechanics (not
the challenge protocol, now resolved) remain a genuinely new design, not
a reconciliation. Should `term` and `authority_epoch` be
identical/related/independently-monotonic? (cx's recommendation: keep
both — `authority_epoch` fences stale records, `term` identifies the
leadership generation). Does duty ownership need process-incarnation-
binding (optional `ProcessBirthIdentity`) beyond the confirmed
`(instance_id, profile_id)` identity? Recovery-evidence fields/receipt
reuse for duty leases not yet defined. Should leadership's slower
election state be a separate `TargetState` or fields on
`DutyLeaseSnapshot`? (cx recommends: separate conceptual state, one
dedicated lease record as the liveness authority.) Should the native
version fix the score-vs-overwrite comment/behavior mismatch above, or
faithfully port the current overwrite behavior (explicit user policy
call, flagged above)?
