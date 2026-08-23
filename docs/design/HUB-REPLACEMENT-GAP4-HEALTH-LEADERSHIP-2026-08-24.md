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
