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

## Addendum (2026-08-01): Step 6A terminal-observation mapping — ratified

Step 6 requires emitting the narrow `AttemptTerminalObserved` event from
`dispatch/service.py` and consuming it in a telemetry projector, but
mapping Slice 1-3's existing execution facts onto Slice 4's frozen event
shape was not fully mechanical. cx.deepthink correctly stopped rather
than guess and proposed the following, each citation independently
verified by cc against live source before ratifying:

1. `transport` is not derivable from any Slice 1-3 contract (`ARCHITECTURE.md`
   confirms it belongs to the not-yet-built `InvocationPlan`, "transport
   kind" among its immutable fields). Resolved: `fail_pre_dispatch`/
   `complete_attempt` accept `transport: str` as an explicit required
   caller-supplied argument for Phase 1; dispatch must never infer it
   from peer/instance configuration.
2. No frozen mapping exists from Slice 3's process-level `ErrorCode`
   (`SPAWN_FAILED`, `PROCESS_TIMEOUT`, etc.) to Slice 4's
   `OperationalFailureCategory` — confirmed against `HR-01-03-HEALTH-
   RECOVERY-CLASSIFICATION-SPEC-R1.md`, whose oracle "independently
   derives, never accepts as injected" a category, only from HR-03's
   canonical health-probe stage evidence, a different semantic layer
   than a dispatched attempt's process-level failure. Resolved:
   `operational_failure_category` is an optional (default `None`)
   caller-supplied argument; dispatch must never translate its own
   `ErrorCode` into this vocabulary.
3. `started_at` uses the `updated_at` returned by the successful
   `RUNNING` transition (`record_running`'s own snapshot) — the only
   precise start-of-execution timestamp Slice 1-3 tracks; `AttemptSnapshot.
   created_at` is admission-adjacent, not execution-start. `latency =
   terminal_at - started_at`; both are `None` for a proven pre-dispatch
   failure (never started).
4. `process_integrity` has no existing source either; resolved as an
   explicit caller-supplied fact (`fail_pre_dispatch` always passes
   `True` — a proven `NOT_STARTED` transition itself required
   integrity-verified evidence; `complete_attempt` requires it as a
   caller-supplied required argument).

Implemented: `dispatch/service.py` (`_attempt_terminal_event` helper,
both terminal methods now emit it alongside the existing dispatch-state
event in the same transaction), `persistence/sqlite.py` + `governance/
broker.py` (`list_outbox_events` gained `after_position` for
starvation-free incremental consumption — cx's own fresh finding, cc
verified the projector would otherwise never advance past its first
`limit` batch), and `telemetry/projections.py` (`TelemetryProjector`:
replayable, checkpoint-CAS'd, ARCHITECTURE.md-compliant — consumes only
the canonical outbox by position, never imports `dispatch` or reads
request tables). 99/99 tests passing (4 new, covering full-pipeline
projection, checkpoint advancement over unrelated events, failure-streak
increment/reset across a retry, and observation+projection+checkpoint
atomicity) — cc additionally ran a standalone manual end-to-end script
(admit → run to completion → project → verify) before trusting the
pytest suite alone.

## Addendum (2026-08-01): Step 3 completion + a real shipped-code defect fixed

Before starting Step 6B (health/service.py), cx.deepthink stopped and
found two real problems in already-committed Step 3 work, both
independently verified by cc before this fix:

1. **`apply_automatic_clearance` violated HR-04.** `HR-04-06-V1-FIXTURE-
   SPEC-R1.md` line 5: "auto health circuit may be cleared only by a
   matching current receipt." The shipped reducer did `del
   clearance_receipt` with a comment (written by cc) claiming no spec
   grounded a receipt check. That comment was wrong -- cc had grepped
   for one specific reason-string an earlier peer draft used
   (`CLEARANCE_RECEIPT_MISMATCH`), found no literal match, and wrongly
   concluded the entire behavioral rule was ungrounded rather than
   reading HR-04's actual text. Fixed: clearance now requires
   `clearance_receipt == circuit.receipt` (full identity-fence equality,
   matching `apply_recovery_probe_result`'s established pattern) in
   addition to `AUTOMATIC` authority; mismatch returns
   `CLEARANCE_RECEIPT_MISMATCH` and leaves the circuit unchanged. See
   `feedback_literal_string_grep_insufficient_citation_check` in the
   collaboration memory for the generalized lesson.
2. **3 reducers the kickoff's own "Reducer set" section requires
   (`apply_policy_action`, `evaluate_cooldown`, `freeze_admission_
   snapshot`) were never implemented** -- Step 3's dispatch was scoped
   against the compatibility test file's coverage, not cross-checked
   against this document's full canonical reducer list, and missed
   them. Now implemented: `apply_policy_action` (creates or updates a
   `HealthCircuitSnapshot` from a `PolicyAction`; same-incident actions
   preserve backoff/cooldown, a new incident resets both, determined by
   `receipt.incident` equality); `evaluate_cooldown` (uses the
   unjittered backoff-ladder value at the circuit's `backoff_count`,
   capped at the ladder's last entry, as the cooldown boundary --
   deterministic jitter stays deferred per decision 6, explicitly not
   guessed here); `freeze_admission_snapshot` (pure construction from
   caller-supplied entries + digest -- digest byte canonicalization is
   deliberately left to the service layer, a separate open question for
   Step 6B/C, not resolved here).

103/103 tests passing (4 new: `apply_policy_action` create/repeat/reopen,
clearance accept/reject-on-mismatch, cooldown state transitions across
all 4 branches, admission-snapshot construction).

## Addendum (2026-08-01): Step 6B pre-service design -- ratified R:10

Before `health/service.py` could be written, cx.deepthink identified 4
remaining ambiguities and correctly stopped rather than guess. Full R:10
round: ag.deepthink and cx.deepthink proposed independently from the same
brief; on item 2 they genuinely disagreed in Round 1, cc gave ag cx's
counter-citation, ag re-verified directly and conceded with an
additional confirming citation of its own. All 4 below are unanimous
(cc + ag.deepthink + cx.deepthink).

1. **Readiness gate -> persisted `admission_state`: an aggregate, not an
   overwrite.** `evaluate_readiness_evidence`'s `gate_state`
   (`OPEN`/`CLOSED`) maps to a *baseline* admission input:
   `READY`+`OPEN` -> `OPEN`; `PROBE_INCONCLUSIVE`+`CLOSED` and
   `READINESS_STALE`+`CLOSED` both -> `RECOVERY_REQUIRED` baseline
   (distinguishable via `readiness_state`/reason_code, not via a
   different admission value -- neither implies `QUARANTINED`, no
   quarantine authority/receipt is supplied by readiness evaluation;
   neither implies `COOLDOWN`, no retry boundary is supplied). This
   baseline is one input to a new aggregate admission reducer (item 2)
   -- a fresh `READY` observation must never itself clear an effective
   circuit-derived `QUARANTINED`/`COOLDOWN`/`RECOVERY_REQUIRED` state;
   only the circuit-side reducers (`apply_policy_action`,
   `apply_automatic_clearance`, `evaluate_cooldown`,
   `apply_recovery_probe_result`) may relax admission. This is an
   explicit state-machine extension beyond `ARCHITECTURE.md`'s base
   graph (which has no direct evidence-loss transition into
   `RECOVERY_REQUIRED`), ratified here rather than silently added.

2. **Circuit-scope -> affected-projection resolution: a new injected
   `HealthScopeMembershipSnapshot`, required now, not deferrable.**
   Initially proposed as an out-of-scope narrowing (defer non-PROFILE
   circuit propagation), this was rejected on cross-check:
   `docs/design/phase0/RUNTIME-HEALTH-RECOVERY-ADDENDUM-R3-2026-07-28.md`
   establishes quota-family/root/environment-scoped health gates as a
   real, already-designed Phase 0 concept (not hypothetical), and its
   own "Additional mandatory tests" section requires "a verified 429
   family condition gates all and only profiles mapped to that family"
   -- silently dropping non-PROFILE circuit effects would leave
   admission_state stale for every profile that should be blocked,
   breaking the actual gate, not just deferring a cosmetic feature (a
   materially different situation than HR-02's genuinely-deferred
   automatic-revalidation branch). Resolution: `health/service.py`
   accepts an injected, immutable `HealthScopeMembershipSnapshot`
   (bound to the same `configuration_revision`/`configuration_digest`
   as `ConfigurationSnapshot`; `scope`+`subject` -> member
   `(instance_id, profile_id)` bindings; sorted, no duplicates; produced
   by the configuration-authority/composition root, never by
   `health.service` itself). This does NOT reopen kickoff decision 9
   ("no configuration CRUD") -- it's an injected immutable fact exactly
   like `ConfigurationSnapshot` itself, not stored/mutable configuration.
   On any circuit-affecting transition, `health.service` resolves
   affected `(instance_id, profile_id)` pairs via this snapshot and
   recomputes each affected projection in the same unit of work, via a
   new pure aggregate reducer with fixed precedence (most to least
   severe): `QUARANTINED > COOLDOWN > RECOVERY_REQUIRED >
   PROBE_AUTHORIZED > OPEN`. `PROBE_AUTHORIZED` only applies when the
   live grant is the sole remaining blocker for that pair -- it can
   never mask a different circuit's quarantine/cooldown on the same
   pair.

3. **Admission-snapshot digest: SHA-256 over `canonical_json_bytes`,
   reusing the Slice 3 digest convention** (`dispatch/model.py`'s
   `canonical_payload_digest`, `routing/model.py`'s audit-seed digest).
   Canonical projection: `{configuration_revision, policy_id,
   policy_revision, entries: [{instance_id, profile_id,
   health_projection_id, health_projection_revision,
   availability_state, admission_state, evidence_refs}, ...]}`, entries
   sorted by `(instance_id, profile_id)`, `evidence_refs` preserved
   verbatim (never reordered/deduplicated). Excludes `snapshot_id`,
   `revision`, and `created_at` -- minted/audit metadata, not the
   health/configuration content being frozen (an unchanged semantic
   freeze must not change the RT-05 routing seed merely because it got
   a new ID or timestamp). Implemented once as a pure
   `canonical_admission_snapshot_digest(...)` helper shared by the
   service and its golden tests.

4. **Recovery-grant single-flight: new migration
   `0006_recovery_probe_single_flight.sql`**, not an edit to the
   already-committed `0005` (`SqliteStateStore.initialize()` skips
   already-recorded versions permanently -- editing 0005 would leave
   any version-5 database unprotected forever; matches the established
   Slice 3 precedent of correcting a shipped migration with a new one,
   `0004` after `0003`, never rewriting the original). Exact fix:
   `CREATE UNIQUE INDEX recovery_probe_grants_one_live_per_circuit ON
   recovery_probe_grants(circuit_id) WHERE consumed_at IS NULL` --
   mirrors the existing partial-unique-index idiom already used for
   one-active-dispatch-attempt in `0003`. cx independently verified this
   exact predicate against the real bundled SQLite runtime (rejects a
   second live grant, accepts one after the first is consumed) before
   proposing it. Migration must fail closed (not silently pick a
   winner) if a pre-existing database already has duplicate live
   grants for one circuit.

Not yet implemented: `HealthScopeMembershipSnapshot` contract + the
aggregate admission reducer, the digest helper, migration `0006`, and
`health/service.py` itself, in that dependency order.

**Implemented (2026-08-01):** all 4 pre-service pieces are now shipped --
`HealthScopeBinding`/`HealthScopeMembershipSnapshot` in
`health/contract.py`; `resolve_admission_state` (item 1's aggregate
reducer, with `_ADMISSION_STATE_PRECEDENCE`) and
`canonical_admission_snapshot_digest` (item 3) in `health/model.py`;
`persistence/migrations/0006_recovery_probe_single_flight.sql` (item 4),
registered in `SqliteStateStore.initialize()` alongside the existing
version-5 check. Compatibility tests added for scope-membership
normalization/duplicate-rejection, admission-state precedence (including
the `PROBE_AUTHORIZED`-never-masks-a-worse-circuit case), and digest
determinism/order-invariance/change-sensitivity (cx's hand-quoted golden
hash was not trusted as-is -- it was 65 hex characters, one too many for
a SHA-256 hex digest -- so the digest is independently verified via
direct execution and via order-invariance/change-sensitivity assertions
rather than pinned to a hand-computed literal). A new integration test
(`test_recovery_probe_single_flight.py`) verifies the single-flight
constraint end-to-end against the real bundled SQLite runtime. Full
suite: 107/107 passing. `health/service.py` itself is next.

## Addendum (2026-08-01): `health/service.py` shipped -- 6 gaps found,
## ratified, and closed; ag skipped this round (quota-critical)

cx.deepthink was dispatched to design and deliver `health/service.py`
against the 4 pieces above. It correctly stopped instead of guessing:
implementing the service as briefed would have silently invented new
persisted-schema/policy behavior in 6 places. cc independently verified
every one of the 6 claims by reading the exact cited files/lines directly
(not by trusting the report) before treating any of them as real:

1. `HealthProjectionSnapshot` persisted only `readiness_observation_id`
   (a pointer), never the full `ReadinessEvaluation` or the
   `sealed_runtime_revision`/`adapter_declares_probe_safe` comparison
   inputs `evaluate_readiness_evidence` needs -- so a later circuit-
   triggered recompute could not re-derive a projection's baseline.
2. `recovery_probe_grants` supported only insert and grant-ID lookup --
   no way to discover a circuit's live grant during aggregate recompute.
3. `HealthProjectionSnapshot.cooldown_until` is a single field, but
   multiple circuits can affect one pair with different `retry_after`
   values -- undefined how to combine them.
4. No typed conflict error existed for the migration-`0006` single-flight
   unique-index violation; it would have leaked a raw
   `sqlite3.IntegrityError`.
5. `ConfigurationSnapshot` is `{revision, digest}` only and
   `HealthScopeMembershipSnapshot` enumerated only non-PROFILE scope
   groupings -- no source listed the complete configured
   instance/profile population needed to freeze a full admission
   snapshot.
6. No rule existed for composing one projection's `evidence_refs` from
   readiness evidence plus operational evidence.

ag (this slice's usual second reviewing voice) was `QUOTA_CRITICAL`
(account-level 3P-pool at 100% used, confirmed via `diag`) for this
entire round and was skipped per the fallback protocol -- logged here,
not silently dropped. cx.deepthink proposed one resolution per gap in a
follow-up round; cc independently verified every proposal against the
live repository (types, reducer signatures, repository method
signatures, dataclass fields) before applying anything, matching the
`feedback_verify_peer_citations` discipline. All 6 held up:

1. Persist the complete `ReadinessEvaluation` plus
   `sealed_runtime_revision`/`adapter_declares_probe_safe` directly on
   `HealthProjectionSnapshot` (new nullable columns via migration
   `0007`; wholly-present-or-wholly-absent validation). A projection
   missing this context fails closed (`InvalidMutationError`) rather
   than silently re-deriving policy.
2. Added `get_live_recovery_probe_grant(circuit_id)` (`consumed_at IS
   NULL`), matching migration `0006`'s own predicate exactly.
3. When aggregate admission is `COOLDOWN`, `cooldown_until` is the
   maximum contributing `retry_after` across every circuit affecting
   that pair (new pure reducer `resolve_projection_cooldown_until`);
   every other aggregate state stores `None`.
4. Added `RecoveryProbeGrantConflictError` (`UNIQUE_CONSTRAINT_VIOLATED`),
   matching the existing `ActiveAttemptExistsError`/
   `ExclusiveClaimConflictError` shape exactly. `add_recovery_probe_grant`
   now checks proactively and also translates the SQLite constraint at
   the boundary.
5. Extended `HealthScopeMembershipSnapshot` with `configured_members`
   (the complete configuration-bound population). PROFILE membership is
   derived from it directly (`subject == profile_id`); an explicit
   `HealthScopeBinding` for `PolicyScope.PROFILE` is now rejected as
   redundant, and every non-PROFILE binding's members must be a subset
   of `configured_members`. Does not reopen kickoff decision 9 -- still
   an injected immutable fact, no CRUD.
6. New pure reducer `compose_health_projection_evidence_refs`: readiness
   evidence first, then operational evidence in its existing order,
   first-occurrence deduplication. Admission-snapshot freezing preserves
   the result verbatim, matching the already-shipped digest helper's
   contract.

Shipped: `health/service.py` (`HealthService`, transactional, one
`store.unit_of_work()` per public method, following `dispatch/service.py`'s
existing idiom -- pure reducers stay pure, no sibling-feature imports);
migration `0007_health_projection_readiness_context.sql`; the contract/
model/error additions above. 13 new integration tests
(`test_health_service_kernel.py`) cover: readiness persistence and
re-evaluation, unconfigured-pair rejection, circuit open -> COOLDOWN ->
RECOVERY_REQUIRED, the full authorize/claim/apply-result recovery cycle
closing a circuit, grant single-flight conflict at the service boundary,
receipt-fenced automatic clearance (both matching and mismatched
receipts), a non-PROFILE (`QUOTA_FAMILY`) circuit correctly propagating
to its bound PROFILE pair's projection with no PROFILE circuit ever
opened directly, full admission-snapshot freezing with independently
recomputed digest verification, and one transaction-rollback fault-
injection case. Full suite: 120/120 passing.

Remaining for this slice: `routing/service.py` (Step 6C) and
`application/workflows.py` (terminal event -> projection -> health
snapshot -> route -> Slice 3 admission -> pre-dispatch recheck/replan),
then Step 7 fault injection across the full stack.

## Addendum (2026-08-01): Step 6C `routing/service.py` shipped -- a
## second reducer-set scoping gap found+closed the same way

Before writing `routing/service.py`, cc found the same defect class as
Step 3's originally-missed 3 HR reducers: this doc's own "Reducer set"
section (line 102) names 5 routing reducers --
`evaluate_route_candidates`, `select_route`, `select_equal_weight_
candidate`, `validate_route_for_dispatch`, `replan_route` -- but
`routing/model.py` had only 3. `select_route` and `replan_route` were
never implemented; `ErrorCode.ROUTE_EXHAUSTED` existed in
`core/protocol.py` and was already accepted by `dispatch/model.py`'s
`reject_request_policy`, but nothing in routing produced it.

cx.deepthink (sole voice again -- ag still `QUOTA_CRITICAL`, logged not
silently skipped) independently confirmed the gap by its own repo-wide
symbol search before proposing anything, then proposed:

- A typed `RoutePlanResult(decision, error_code)` result instead of a
  raised exception for exhaustion -- exhaustion is a valid routing
  outcome, not malformed input, and `RouteDecision`/the SQLite schema
  already deliberately support an all-`None` selection triple for this
  exact case (`0005_health_routing.sql` line 211's `CHECK` constraint).
- `select_route` seeds `select_equal_weight_candidate` with
  `request.admission_snapshot.digest` (the semantic freeze digest, not
  `snapshot_id`/`revision`) -- matches this doc's own line 398 rationale
  for why the RT-05 seed must not change on an unchanged semantic
  freeze.
- `replan_route` is mechanical re-invocation only: verifies the stale
  decision is genuinely configuration-stale, requires a new
  `decision_id` and the same `client_request_id`, then calls
  `select_route` again -- never patches the old row, matching this doc's
  own line 121-122 ("replanning inserts a new decision, never overwrites
  a stale one").
- A new `RouteRequest` invariant: `admission_snapshot.configuration_
  revision` must equal `configuration.revision`, preventing one durable
  decision from recording internally inconsistent frozen inputs (no
  existing test constructed `RouteRequest` yet, so this was a safe
  addition, not a breaking one).
- Confirmed no lookup-by-`client_request_id` is needed -- intentional
  per the replan-inserts-a-new-row design, not a gap.

cc independently verified every claim (reducer/contract signatures,
the FK schema, the exact cited doc lines) against the live repo before
applying. Shipped: `routing/model.py`'s `select_route`/`replan_route`
plus a guard in `validate_route_for_dispatch` rejecting an unselected
decision; `routing/contract.py`'s `RoutePlanResult`/
`RoutePreDispatchResult` plus the new `RouteRequest` invariant;
`routing/service.py` (`RoutingService`, same `dispatch/service.py`-
derived transactional idiom as `health/service.py`, no sibling-feature
imports -- it reads health's frozen `AdmissionSnapshot` as a pre-supplied
immutable input, never calling into `health/service.py` itself). No
schema/migration changes were needed -- the exhausted-audit shape was
already there, unused. 9 new integration tests
(`test_routing_service_kernel.py`) cover: successful selection,
persisted exhaustion, missing/mismatched admission snapshot, no-drift
pass-through, configuration-drift replanning (new decision ID, old row
provably untouched), refusing to validate an exhausted decision, missing
decision, and a rollback fault-injection case. Full suite: 129/129
passing.

Remaining for this slice: `application/workflows.py` (terminal event ->
projection -> health snapshot -> route -> Slice 3 admission ->
pre-dispatch recheck/replan), then Step 7 fault injection across the
full stack.

## Addendum (2026-08-01): `application/workflows.py` shipped -- Step 6
## complete, all 4 feature services now composed

cx.deepthink (sole voice again -- ag still `QUOTA_CRITICAL` throughout,
logged not silently skipped) resolved 2 ambiguities cc raised plus
surfaced 4 more unprompted, all independently verified by cc against
the live repo (every cited file/line checked out) before applying:

1. **`route_decision_digest` needs a new canonical digest, not
   `RouteSelection.audit_seed` reused.** `audit_seed` hashes only
   `{client_request_id, admission_snapshot.digest}` -- it does not bind
   configuration, candidates, exclusions, routing policy, or the
   decision ID, so two structurally different decisions sharing a
   request/snapshot pair would collide. Added
   `canonical_route_decision_digest` to `routing/contract.py` (not
   `routing/model.py` -- `ARCHITECTURE.md` line 185: "a feature may
   import a sibling's `contract.py`, never its `model.py`").
2. **RT-06 recheck sits between `admit_request` and `prepare_request`,
   and again before every `authorize_retry`.** `admission freezes
   peer/profile, route decision, policy revision` (`ARCHITECTURE.md`
   line 433) -- there is no reducer that repairs a stale decision
   in-place, so a drifted decision is rejected
   (`ErrorCode.CONFIGURATION_STALE` via `reject_policy`, legal only from
   `ADMITTED`) and replanned as a brand-new `RouteDecision`/`decision_id`
   (never overwriting the stale row, matching kickoff line 121-122); a
   fresh envelope re-enters admission from scratch. `authorize_retry`
   reuses the existing request's frozen routing fields unchanged, so it
   must be RT-06-rechecked separately before every retry, not just once
   at initial admission.
3. **Known open gap, documented not silently ignored:**
   `freeze_admission_snapshot()` only freezes currently-persisted health
   projections; it does not itself re-run readiness evaluation from
   fresh telemetry. Calling `telemetry.project_pending()` right before
   freezing updates the telemetry projection table but does not yet
   guarantee the frozen health snapshot reflects it -- a health-refresh
   command or explicit readiness-input factory is still needed before
   this pipeline is fully closed-loop. Not blocking Step 6 (routing
   already correctly reads whatever health snapshot exists), but real
   follow-up work, not yet scheduled.
4. `RouteRequest` gained an invariant rejecting a request whose
   `admission_snapshot.configuration_revision` disagrees with its own
   `configuration.revision` (prevents one durable decision recording
   internally inconsistent frozen inputs; no existing caller broke).

Shipped: `peerhub/application/__init__.py` + `workflows.py`
(`ApplicationWorkflows` -- the one place cross-feature `service.py`
composition is architecturally sanctioned, per `ARCHITECTURE.md` line
185's own carve-out; it never reaches into a sibling service's
store/unit-of-work directly, only calls its public methods).
`admit_request` projects telemetry, freezes a health snapshot, routes,
and admits -- correctly short-circuiting on `ROUTE_EXHAUSTED` without
calling `dispatch.admit_request`. `prepare_for_dispatch` and
`authorize_retry` both apply the RT-06 recheck/replan/reject sequence
above. 7 new integration tests (`test_workflows_kernel.py`) cover: full
successful admission, `ROUTE_EXHAUSTED` short-circuit, no-drift
preparation, drift-triggered rejection+replan with the new decision ID
and old row provably untouched, a full fail-pre-dispatch -> retry cycle
both with and without drift, and cross-decision binding-mismatch
rejection. Full suite: 136/136 passing.

**Step 6 is now complete in full** (telemetry projector, health service,
routing service, application workflows). Remaining for this slice: Step
7 -- fault-injection across the full stack plus the complete Slice 1/2/3
regression suite, per the kickoff doc's own Step 7 completion criteria
(atomic rollback cleanliness, deterministic golden route decisions, no
external I/O inside transactions, zero real adapter/provider/process
behavior).

## Addendum (2026-08-01): Step 7 complete -- Slice 4 (Health/Admission +
## Routing) shipped in full

Solo cc this round (no ambiguity requiring peer ratification -- purely
mechanical fault-injection/concurrency test authoring against the
already-ratified, already-shipped Step 6 services). Checked against the
4 Step 7 completion criteria:

1. **Atomic rollback cleanliness.** Two new dedicated fault-boundary
   test files parametrize every named `FaultPoint` on both new Step 6
   services and assert complete rollback (every touched table reverts,
   a clean follow-up call succeeds from scratch) plus one
   `AFTER_COMMIT` case per service proving already-committed state
   survives a post-commit fault:
   - `test_health_service_fault_boundaries.py` (15 tests): all 11 of
     `HealthService`'s `FaultPoint`s across readiness persistence,
     circuit-open, recovery authorize/claim/apply-result, and
     admission-snapshot freezing.
   - `test_routing_service_fault_boundaries.py` (4 tests): all 3 of
     `RoutingService`'s `FaultPoint`s across `select_route` and
     `replan_route`, including proving a faulted replan leaves the
     stale decision row completely untouched.
   - Slice 3's own dispatch fault-boundary suite
     (`test_request_attempt_fault_boundaries.py`,
     `test_session_lease_fault_boundaries.py`,
     `test_sqlite_fault_boundaries.py`) continues passing unchanged.
2. **Concurrent CAS safety.** A new `ThreadPoolExecutor`-based test
   (`test_concurrent_circuit_open_for_same_incident_merges_safely` in
   `test_health_service_kernel.py`) races two threads opening a health
   circuit for the identical incident. Empirically verified before
   committing (not assumed): exactly one circuit row results, its
   revision correctly advances to 2, and `backoff_count` is preserved
   (same-incident semantics) rather than reset -- `HealthService`
   methods are safe under concurrency by construction, because every
   public method's read-decide-write sequence happens inside one
   `unit_of_work()` that SQLite's own transaction locking fully
   serializes; there is no external TOCTOU window at the service
   boundary to exploit. Recovery-probe-grant single-flight concurrency
   was already covered by `test_recovery_probe_single_flight.py`
   (Step 6B); telemetry checkpoint CAS by the pre-existing Step 6A
   suite.
3. **Deterministic golden route decisions.** Already pinned at Step 4
   (`test_phase0_rt_compatibility.py`'s
   `test_rt05_equal_weight_selection_is_golden`, a hardcoded
   `audit_seed` hash). Step 6C's `select_route`/`replan_route` are thin
   wrappers around the same already-golden-tested
   `select_equal_weight_candidate` reducer and do not alter its hashing
   -- no new golden test needed.
4. **No external I/O inside transactions; zero real adapter/provider/
   process behavior.** True by construction and confirmed by direct
   reading of every Slice 4 service file this session
   (`health/service.py`, `routing/service.py`,
   `application/workflows.py`, `telemetry/projections.py`): none import
   `peerhub.adapters`, `peerhub.core.execution`'s process-spawning
   surface, or perform any I/O other than SQLite calls already scoped
   inside `store.unit_of_work()`.

Full suite: **156/156 passing** (136 Step 6 + 20 new Step 7 tests; this
number already includes the complete Slice 1/2/3 regression suite,
since this repo's tests are not slice-siloed -- every `pytest` run from
repo root has always exercised all four slices together).

**Slice 4 (Health/Admission + Routing) is now complete.** All 7 steps of
the ratified TDD implementation order are shipped: compatibility tests,
contracts, HR reducers, RT reducers, migrations + repositories,
telemetry/health/routing/application services, and fault injection.

## Addendum (2026-08-01): post-completion cross-review found+fixed a real
## `admit_request` idempotency defect

ag.deepthink had been unavailable (account-level 3P-pool `QUOTA_CRITICAL`)
for the entire Step 6B/6C/6/7 stretch, so it had never reviewed any of
it. Once it was confirmed that CRIT was scoped to `ag.opus`/`ag.gptoss`'s
3P-pool only -- `ag.deepthink` draws from the separate, healthy G-pool
and was usable the whole time -- cc dispatched a full independent
cross-review of Slice 4 to it. Four findings came back; cc independently
verified each before trusting any of them (per this slice's standing
discipline):

- Two were already-correct defensive behavior, not gaps: `resolve_
  admission_state` already wraps its lookup in try/except and raises a
  clear `ValueError` rather than an unhandled `KeyError`; `validate_
  route_for_dispatch` raising on an exhausted decision's `None`
  `selected_candidate_id` is intentional fail-fast, since no code path
  in this repo can ever hand an exhausted decision to that reducer.
- One was already-ratified scope, misread as a gap: `evaluate_route_
  candidates` not deriving eligibility from `usage_evidence`/
  `in_flight_reservations` is exactly kickoff decision 8 ("cost/latency/
  terminal-tier weighting is deferred to a future versioned
  RoutingPolicy") -- the caller decides `eligible`, this reducer only
  audits/weights what's already decided.
- **One was real and serious**: `ApplicationWorkflows.admit_request`
  broke on every retry of an identical envelope, not just under a
  network-fault scenario. `canonical_route_decision_digest` embeds each
  `RouteDecision`'s freshly-minted `decision_id`, so two calls can never
  produce the same digest even for byte-identical routing inputs; the
  workflow's binding check compared the JUST-recomputed digest against
  whatever `dispatch.admit_request` actually returned (the frozen
  original admission, per `ARCHITECTURE.md`'s "admission freezes" rule)
  and always raised `InvalidMutationError` on the second call. cc
  reproduced this directly with a standalone script before trusting the
  citation: two identical `admit_request` calls, second one crashes.

ag.deepthink also designed the fix (a second dispatch round, still using
its available G-pool quota): add `DispatchService.peek_idempotent_
admission` (thin wrapper around the same internal lookup `admit_request`
already uses) so the workflow checks for an existing admission *before*
doing any telemetry/health/routing work, and replace the strict
post-admission raise with a graceful "digest doesn't match what we just
computed -> treat as an idempotent replay" fallback for the residual
race (two identical envelopes admitted concurrently, both missing the
up-front peek). A real consequence, flagged by ag.deepthink and accepted
as an explicit, documented trade-off rather than solved with a schema
change: `RequestSnapshot` durably records only `route_decision_digest`,
never `decision_id`, so the original `RouteDecision` is not retrievable
on a replay -- `AdmissionWorkflowResult.admission_snapshot`/`.route` are
now `Optional` and `None` on any replay path. A new regression test
(`test_admit_request_is_idempotent_on_retry`) locks this in. Full suite:
157/157 passing.
