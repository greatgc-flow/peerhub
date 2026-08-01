# Slice 4 Kickoff R1 — Health/Admission + Routing (HR+RT)

Status: ratified. Produced by a 2-round adversarial mutual-critique between
ag.deepthink and cx.deepthink (2026-07-31), reconciled by cc. Continues
Slice 1 -> Slice 2 -> Slice 3 -> **Slice 4** sequencing from
`PHASE1-KICKOFF-R1.md`'s "Full subsequent order".

## Process summary

Round 1: ag and cx independently proposed HR+RT designs. cx's proposal was
substantially more rigorous, citing exact `ARCHITECTURE.md`/
`PROTOCOL-V1-FREEZE.md`/Phase 0 fixture-spec line numbers and finding 9
real gaps ag's Round 1 proposal did not surface (a spec/fixture-oracle
mismatch on RT-06's drift check; an unresolved vocabulary mapping between
HR fixture readiness states and `ARCHITECTURE.md`'s availability-state
enum; the routing seed identity should be `client_request_id` not
`command_id`, the same mistake class as Slice 3's; a missing telemetry
prerequisite event; HR-02's out-of-scope revalidation branch; an
underspecified backoff-jitter canonicalization; deferred clearance-
authority semantics; RT-04's weighting policy not being general; and the
configuration-SSOT gap).

Round 2: ag independently re-verified every one of cx's 9 points directly
against the cited files/specs and conceded on all 9, updating its own
reducer signatures and file list accordingly.

## Ratified decisions

1. **RT-06 drift check narrowed to configuration-revision only for this
   slice.** The Phase 0 oracle (`tools/phase0_fixture_runner/domain/
   routing.py`) never implemented the admission-snapshot-drift half of
   RT-06's prose. Slice 4 implements only the proven configuration-
   revision check; admission-snapshot drift detection is out of scope
   until a Phase 0 fixture vector is separately ratified.

2. **Readiness-state vocabulary requires an explicit mapping layer.** HR
   fixture outputs (`READY`/`PROBE_INCONCLUSIVE`/`READINESS_STALE`, gate
   `OPEN`/`CLOSED`) are distinct from `ARCHITECTURE.md`'s availability
   enum (`UNKNOWN`/`PROBING`/`HEALTHY`/`DEGRADED`/`UNAVAILABLE`/`STALE`)
   and its separate admission state machine. `health/model.py` maps
   fixture-domain outputs into the architecture's enum before anything
   is persisted; a circuit's `CIRCUIT_OPEN` must never be conflated with
   admission `OPEN`.

3. **Routing seed identity is `client_request_id`, never `command_id`**
   (`PROTOCOL-V1-FREEZE.md`: `command_id` mints only post-admission;
   routing happens during admission, before it exists). Same lesson class
   as Slice 3's command_id correction.

4. **Minimal telemetry prerequisite is in-scope and load-bearing.**
   `peerhub/dispatch/service.py`'s current terminal outbox payload
   (`command_id`/`state`/`lease_id`/`terminal_error_code`) is not the
   narrow `AttemptTerminalObserved` operational event `ARCHITECTURE.md`
   requires health to consume — it must be added in this slice.
   `peerhub/telemetry/{contract,projections}.py` are created; telemetry
   reads the existing canonical outbox by `outbox_position` and advances
   its own checkpoint, never touching the governance claim/consume
   columns (those are effect-delivery state, telemetry is an independent
   replayable consumer). Health reads only through a
   `TelemetryProjectionReader`, never dispatch/request tables directly.

5. **HR-02's automatic safe-revalidation branch is out of scope**
   (no real adapter probe exists in Phase 1). `evaluate_readiness_evidence`
   returns `REVALIDATION_REQUIRED` instead of attempting a live probe.

6. **Backoff-jitter canonicalization deferred to implementation-time
   ratification.** The ±20% incident-derived jitter ladder is frozen
   (`PHASE1-KICKOFF-R1.md`), but the exact byte/modulo derivation is not
   — must be pinned before golden fixture tests are written, as its own
   small ratified addendum during implementation.

7. **HR-03 clearance-authority semantics stay deferred.** `kind`/
   `opened_by`/`required_clearer` were already left as open backlog in
   `HR-03-POLICY-ACTION-EXTENSION-R1.md`. `authorize_recovery_probe` only
   grants a probe; a production manual-clearance override is not built
   until authority semantics are separately ratified.

8. **RT-04 does not freeze a general weighting policy** (its fixture
   fixes eligible weight to 1 and proves only role-based exclusion).
   Slice 4's routing narrows to boolean `ELIGIBLE`/`EXCLUDED` evaluation
   plus deterministic equal-weight selection; cost/latency/terminal-tier
   weighting is deferred to a future versioned `RoutingPolicy`.

9. **No configuration CRUD in this slice.** `ARCHITECTURE.md`'s full
   `PeerInstanceConfig`/`PeerProfileBinding` SQLite SSOT is not built
   here. Slice 4 consumes an injected, immutable `ConfigurationSnapshot`
   (revision/digest only) rather than inventing configuration storage.

## Reducer set

`peerhub/health/model.py`: `evaluate_readiness_evidence` (frozen HR
states -> mapped to architecture's availability enum, `REVALIDATION_REQUIRED`
for HR-02's out-of-scope branch), `classify_health_failure`,
`derive_policy_action`, `apply_policy_action`, `apply_automatic_clearance`,
`authorize_recovery_probe`, `claim_recovery_probe`,
`apply_recovery_probe_result` (validates identity — incident, gate
generation, timestamp, fingerprint — before branching on success/failure;
do not copy the Phase 0 oracle's success/failure-first ordering, add a
stale-fingerprint-failure no-op test), `evaluate_cooldown`,
`freeze_admission_snapshot`.

`peerhub/routing/model.py`: `evaluate_route_candidates`, `select_route`
(returns `ROUTE_EXHAUSTED` — frozen error — when nothing is selectable),
`select_equal_weight_candidate` (seeded by `client_request_id` +
snapshot digest), `validate_route_for_dispatch` (returns frozen
`CONFIGURATION_STALE` on any drift), `replan_route` (re-invokes selection,
never repairs a stale decision).

Missing usage evidence stays `ABSENT`/`UNAVAILABLE`, never coerced to
zero (`PROTOCOL-V1-FREEZE.md`'s evidence algebra).

## SQLite schema (migration `0004_health_routing.sql`)

Immutable: `health_policy_revisions`, `readiness_observations`,
`operational_observations`. Mutable (revision-guarded CAS,
`rowcount == 1`): `operational_projections`, `health_circuits`
(`scope`/`subject` scoped — distinct fact from `health_projections`,
which answers per-instance/profile live availability/admission),
`recovery_probe_grants` (`WHERE grant_id=? AND revision=? AND
consumed_at IS NULL`), `recovery_probe_receipts`. Immutable audit:
`admission_snapshots`, `admission_snapshot_entries`, `route_decisions`,
`route_candidate_decisions` — replanning inserts a new decision, never
overwrites a stale one. Reuses the existing canonical `outbox_events`/
`outbox_checkpoints` from Slice 3; no second health-specific outbox.

No new public `ErrorCode` values — reuse frozen `ADMISSION_CLOSED`,
`PEER_UNAVAILABLE`, `PROFILE_UNAVAILABLE`, `ROUTE_EXHAUSTED`,
`CONFIGURATION_STALE`, `POLICY_STALE`, `REVISION_CONFLICT`. Fixture-only
reason strings (`READINESS_STALE`, `TERMINAL_TIER`,
`PROBE_GRANT_EXHAUSTED`) stay internal, never promoted to wire error
enums without separate ratification.

## File list

Create: `peerhub/core/evidence.py`; `peerhub/telemetry/{__init__,
contract,projections}.py`; `peerhub/health/{__init__,contract,model,
service}.py`; `peerhub/routing/{__init__,contract,model,service}.py`;
`peerhub/application/{__init__,workflows}.py`;
`peerhub/persistence/migrations/0004_health_routing.sql`;
`tests/contract/test_phase0_hr_compatibility.py`,
`test_phase0_rt_compatibility.py`; `tests/unit/health/test_model.py`,
`tests/unit/routing/test_model.py`, `tests/unit/telemetry/
test_projections.py`; `tests/integration/persistence/
test_health_routing_kernel.py`, `test_health_routing_fault_boundaries.py`,
`test_telemetry_feedback_kernel.py`; `tests/integration/application/
test_health_routing_workflow.py`.

Extend: `peerhub/core/protocol.py`, `peerhub/core/errors.py` (mappings
only, no new `ErrorCode` unless separately frozen),
`peerhub/dispatch/service.py` (add the `AttemptTerminalObserved` terminal
event), `peerhub/persistence/sqlite.py`, `peerhub/runtime.py`.

Do not import the Phase 0 fixture-runner oracle modules
(`health.py`/`routing.py`) as production code — proof of behavior only,
same rule as every prior slice.

## Implementation order (TDD, 7 steps)

1. Port HR-01..06/RT-04..06 vectors as failing compatibility tests
   (RT-06 narrowed to configuration-revision only per decision 1; add a
   stale-fingerprint-failed-probe no-op test per the reducer note above).
2. Add contracts only (`EvidenceValue`, `HealthPolicy`, observations,
   projections, snapshots, routing request/decision, the narrow terminal
   event) — freeze any vocabulary gap before defining enums, don't guess.
3. Implement pure HR reducers.
4. Implement pure RT reducers.
5. Add migration `0004` and real production repositories; test immutable
   uniqueness, every revision guard, grant single-use contention, stale-
   receipt no-op, snapshot immutability, route audit completeness,
   reopen/migration behavior, outbox-position checkpoint CAS.
6. Implement the telemetry projector, health/routing services, and the
   application workflow (terminal event -> projection -> health snapshot
   -> route -> Slice 3 admission -> pre-dispatch recheck/replan); prove
   no sibling-feature-service imports and no health mutation by routing.
7. Fault-inject every write boundary; run concurrent CAS/checkpoint/grant
   tests plus the full Slice 1/2/3 regression suite. Completion requires
   atomic rollback cleanliness, deterministic golden route decisions, no
   external I/O inside transactions, zero real adapter/provider/process
   behavior.

## Progress note (2026-08-01): Steps 1-5 shipped; a real cx outage and an
## ag.deepthink citation-reliability incident, for the record

Steps 1-5 are implemented and committed (`56e830d` Steps 1-2 + first HR
reducer via an ag.deepthink-vs-ag.effort bake-off; `ba773dc` remaining
HR-03..06 + RT-04..06 reducers; `7af2d29` migration `0005_health_routing`
+ repository layer — renumbered from the kickoff doc's stale `0004` after
it collided with the Slice-3-defect-fix migration; see that commit
message and each step's own git history for full detail). 95/95 tests
passing. Steps 6-7 (telemetry projector, health/routing services,
application workflow, fault injection) remain.

Two things happened mid-Step-5 that are worth recording here because they
bear directly on how much to trust which peer's unverified claims on this
slice's remaining work, not because they're about peerhub's own
architecture:

1. **cx hit a real Codex account-level quota exhaustion** partway through
   Step 5, confirmed only by ag.deepthink actually executing the raw
   Codex CLI command and reading the literal usage-limit error (reading
   logs/hub.py source alone could not have distinguished this from a
   bug). Resolved same day via a `hub.py credit-consume` coupon
   redemption cc found sitting unused, plus a required `peer-recover`
   afterward (the account-level fix does not by itself clear hub.py's
   own cached per-profile block). Full technical detail, including a
   follow-up R:10-ratified design extension to `diag.py`'s EXH display
   and `hub.py`'s credit-consume flow, lives in the portable-dev-env repo
   at `_sys/docs-v2/ops/pretdd-prep-2026-07-21-diag-quota-metrics.md`'s
   2026-08-01 Addendum (not duplicated here — that system isn't part of
   peerhub's own scope).
2. **ag.deepthink made a flatly false claim** while reviewing that same
   incident: it asserted a live, working `hub.py` capability (`credit-
   status`/`credit-consume`) did not exist in the source, based on (by
   its own later admission) "a failed `grep` tool execution," and
   recommended against correcting a stale doc on that false basis. cc
   caught it by re-running the same grep and `git log` checks directly;
   ag.deepthink then re-verified for itself in a second round, conceded
   plainly, and its corrected position converged independently with
   cx.deepthink's (and later ag.effort's) already-accurate analysis.
   This is the same failure class as this slice's original Slice 3
   incident (ag's first implementation attempt truncated ~800 lines and
   invented vocabulary) and as the standing `feedback_verify_peer_
   citations` lesson: **never apply an ag (or any peer) claim about what
   does/doesn't exist in this codebase without an independent check**,
   even a claim stated with full confidence. It does not change the
   Step 1-5 bake-off result (ag.effort was, separately, the stronger of
   the two ag profiles on implementation correctness+robustness) — this
   is specifically about citation/fact-checking reliability, a different
   axis, and worth tracking separately for Steps 6-7.
