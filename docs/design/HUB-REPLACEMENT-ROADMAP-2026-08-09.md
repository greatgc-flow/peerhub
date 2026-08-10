# peerhub → hub.py replacement roadmap

Status: living document, updated as phases land. Started 2026-08-09 after
GovernanceBroker cutover Steps A-D completed.

Goal: peerhub reaches functional parity with `P:\_sys\core\hub.py` closely
enough that it can become the primary dispatch/coordination path, with
hub.py demoted to a fallback (not deleted outright -- that's a separate,
later decision requiring explicit user sign-off given hub.py is still the
live system in daily use).

## Ordering rationale

Phases are sequenced by dependency, not just risk. Persistence-layer
cleanup (Phase 1) has no dependents blocking on it functionally, but doing
it first keeps the schema stable before the orchestration loop (Phase 3)
starts writing against it seriously. Phase 3 is the actual critical path
for "can replace hub.py" -- everything before it is groundwork, everything
after it is safety/validation before a real cutover.

## Phase 1 — Governance schema cleanup (Steps E/F)
Status: COMPLETED (Steps E1, E2, E3, and F completed).
- Step E1 (DONE): Rebuild `effect_receipts` FK to target `effect_deliveries(event_id)`
  instead of `outbox_events(event_id)` via migration `0015_effect_receipts_delivery_fk.sql`.
- Step E2 (DONE): Rebuild `dispatch_artifact_manifests` FK (migration 0008) to target
  `event_log(event_id)` instead of `outbox_events(event_id)` via migration
  `0016_dispatch_artifact_manifests_event_log_fk.sql`; rewire the remaining raw-SQL
  reader in `sqlite_dispatch.py` (line 1678 in `get_artifact_recovery_digest`) to query `event_log`.
  - Mandatory fail-closed template: every table recreation/migration script MUST
    execute `PRAGMA foreign_key_check;` *inside* the transaction before `COMMIT;`
    (remedying the post-commit verification gap identified in migration 0015).
- Step E3 (DONE): Zero-reader & zero-writer tripwire gate. Removed legacy mirror writes in
  `sqlite_governance.py` and `sqlite.py`; updated tests; verified with a SQLite authorizer
  tripwire test (`test_outbox_zero_access_tripwire.py`) that exactly 0 reads and 0 writes
  touch `outbox_events` and `outbox_checkpoints`.
- Step F (DONE): Dropped `outbox_events` and `outbox_checkpoints` via migration `0017_drop_legacy_outbox.sql`.
  - Verification gate: Implemented automated DB backup/restore test fixture
    (`test_migration_0017_drop_legacy_outbox.py`) taking an online SQLite `.backup()` snapshot at v16,
    applying migration 0017, asserting tables dropped and foreign keys intact, then restoring
    and verifying pre-drop v16 operational state.
- Alembic Scope Note: Alembic cutover remains strictly scoped to Phase 2 (Structural
  debt). The bespoke runner (`peerhub/persistence/migrations/*.sql`) is the sole
  runtime migration engine in Phase 1.


## Phase 2 — Structural debt
Status: in progress. Scoped 2026-08-10 via cx (read-only, GitHub-mirror
analysis) + ag.effort (local verification/correction) dialectical pass --
see `project_step_ef_ratified_2026_08_10.md` and this session's history
for the scoping conversation itself.

### Read/write UnitOfWork split
Status: in progress. Every read used to open a write transaction via
`BEGIN IMMEDIATE`; migrating to an additive `ReadUnitOfWork`
(`PRAGMA query_only=ON` + deferred `BEGIN`) one call site at a time.
13 total call sites identified (ag's local audit corrected cx's initial
remote-mirror-derived count of 10):
- **Done**: `DispatchService.count_active_leases` (PH-UOW-READ-01,
  commit `7bad9ff`); `GovernanceBroker.get_target`/`get_outbox_event`/
  `get_effect_receipt`/`recover_pending_effects` (PH-UOW-READ-02, commit
  `99bbce0`).
- **Remaining (8)**: Dispatch -- `get_lease`, `get_request_and_attempt`,
  `get_request` (service.py); `SessionLeaseService.check_lease_fence`
  (session_lease.py). Routing -- `get_route_decision` (routing/service.py).
  Telemetry -- `get`, `project_pending` (telemetry/projections.py).
  Health -- `freeze_admission_snapshot` (health/service.py). Each is a
  small, independently-verifiable increment following PH-UOW-READ-01/02's
  pattern -- migrate a domain's read methods + protocol + tests in one
  commit, same as the two already done.

### Bespoke migration runner → Alembic full cutover
Status: not started, but timing resolved. Both cx and ag independently
confirmed post-Phase-1 schema v17 (current) is the correct point to
generate a single consolidated Alembic baseline -- do NOT backport
parity revisions for bespoke migrations 13-17 (Alembic is not invoked
anywhere in the runtime or test suite; incremental parity would be
unexercised double-maintenance). Baseline generation and the actual
runtime cutover should be separate increments; before cutover, prove
fresh-Alembic-v17 == fresh-bespoke-v17, define the stamping path for
existing bespoke-v17 databases, and fix `docs/migrations.md`'s
now-corrected guidance (Alembic explicitly frozen/unsupported until this
lands, see that file).

### Capability-lease design implementation
Status: **HOLD -- not authorized for implementation.** Two design passes
so far: `docs/design/CAPABILITY-LEASE-DESIGN-2026-08-08.md` (original) →
`docs/design/CAPABILITY-LEASE-DESIGN-2026-08-08-ERRATA.md` (commit
`d7017e9`, ag.opus's re-ratification resolving the anchor point,
enforcement gate location, and a 3-level EnforcementLevel model) → cx's
final cross-check (2026-08-10) found the errata itself has 4 concrete,
code-verified gaps and issued a HOLD, not approval:
1. The proposed `AdmissionCoordinator` defense-in-depth hook
   (`_load_admission()`, admission.py:150) is only reachable via
   `_find_idempotent_admission()`'s replay path -- it does NOT execute
   on fresh admission, the normal direct-ask case. Verified directly:
   the early return at admission.py:139 exits before `_load_admission`
   is ever called when no prior binding exists.
2. **"A required argument is not authorization."** The errata's
   `CapabilityLease` parameter has no binding to `command_id`, principal,
   adapter/profile, workspace, authority epoch, or admission receipt --
   a caller could construct `REMOTE_MUTATE + ADVISORY` and satisfy the
   type signature with no real constraint enforced.
3. There is no authoritative "requested capability" data reaching the
   gate to check the lease against -- `direct_ask.py:114` sets
   `requested_capabilities=()` (verified), and `CommandDescriptor.
   mutability` belongs to a separate path that never reaches
   `dispatch_and_execute()`.
4. `ADVISORY` (proposed for `ag`, which lacks enforceable confinement)
   is audit-only and permits dispatch after a violation -- it doesn't
   resolve the original ag-enforceability concern, just relabels it. cx's
   proposed correction: mutating `ag` dispatches must fail closed
   (blocked, not merely logged) until `CONFINED` actually exists.

Both load-bearing claims (1 and 3) were independently spot-verified in
the actual code before accepting this verdict, not just taken on faith.
Migrations 0016/0017 confirmed to introduce no incompatibility (no
capability-lease code references either legacy table).

**Next step**: either a corrective design pass addressing all 4 points
(authoritative issuance + durable admission binding, validation on both
fresh admission AND replay, a structured capability-tier field reaching
the gate, and a fail-closed enforcement floor for unconfined peers), or a
counter-response from ag.opus if it disagrees with cx's verdict -- not
yet obtained (`ag`'s EXH was at/over ceiling on both pools when this
verdict landed). Do not authorize implementation on the errata as
currently written. This is the mutation-authorization mechanism hub.py's
own preflight system was found to NOT actually enforce (see
`project_mutation_lease_design_2026_08_08.md`); peerhub should not repeat
that gap -- which is exactly what cx's verdict is protecting against.

### Known-open, not part of Phase 2 itself
2 pre-existing test failures (`test_client_never_imports_persistence`,
`test_generator_runs_and_produces_valid_manifest`) confirmed unrelated to
any Phase 1/2 work via git-stash bisection -- see
`docs/design/OVERNIGHT-INFRA-LESSONS-2026-08-10.md` for detail. Fix when
convenient, not blocking.

## Phase 3 — Real orchestration loop (the actual hub.py replacement)
Status: not started. **This is the critical path** -- nothing before this
phase makes peerhub capable of doing what hub.py does today (dispatch a
query to a peer CLI, get a structured response back, track session/
context/quota state, retry/failover on peer trouble).
- Wire the 3 real PeerAdapters (agy/claude/codex, landed Stage 3) into an
  actual dispatch loop that: sends a query, decodes the peer's real
  output via the adapter, returns a structured result to the caller.
- Session continuation (currently explicitly out of scope per Stage 3 --
  each adapter call is a fresh CLI invocation; hub.py's
  --session-policy auto/reuse/fresh behavior has no peerhub equivalent
  yet).
- Streaming decode (codex.cmd emits JSONL events as they happen; current
  adapter only parses the full output after the process exits).
- Detailed error-taxonomy mapping (hub.py distinguishes zombie/timeout/
  quota-exhausted/malformed-output/etc.; peerhub adapters currently only
  have a coarse success/failure split).
- Tool-call parsing (peers that invoke their own tools mid-response --
  not yet handled at all).
- Health/quota tracking equivalent to `diag.py` -- peerhub's CLI `status`
  command currently only reports migration count + active lease count,
  nothing like diag's EXH/context/pool breakdown. Needs its own design
  pass on where that data would even come from for peerhub-native
  dispatches (hub.py's diag reads CLI-native stat files per peer; a
  peerhub-orchestrated dispatch would need to either read the same files
  or maintain its own).

## Phase 4 — Shadow validation before real cutover
Status: not started (this is "Stage 4" from earlier Stage-numbered
planning, renamed into this roadmap's phase numbering for continuity).
- Shadow-by-ownership-cluster: route a subset of real dispatches through
  peerhub in parallel with hub.py, compare outcomes on the same input,
  without peerhub's result being authoritative yet.
- Same-revision comparison + rollback proof: prove that if peerhub's
  shadow path misbehaves, disabling it is instant and lossless (no
  in-flight work stranded).
- This phase needs its own dedicated design pass -- larger and riskier
  than anything in Phase 1-3, since it's the first point where peerhub
  touches real dispatch traffic instead of only its own test/CLI usage.

## Working discipline for all phases (carried forward from tonight)
- Small, independently-verifiable increments -- no single dispatch
  attempting a whole phase (see `reference_ag_open_ended_task_failure_mode.md`
  data point 6: a single large dispatch on a fully-ratified design still
  broke the repo badly; retrying as 3 small increments worked cleanly).
- Every commit: fresh `pyright`, fresh full `pytest -q`, full diff read,
  scope-boundary files checked for zero-diff where a step shouldn't touch
  them. Never trust a peer's self-reported pass/fail count.
- For write-path/interface-removing changes (the D2/D3-risk tier): tag a
  rollback anchor before starting, and after committing (before pushing)
  actually run `git revert --no-commit` and re-verify the reverted tree
  matches the prior known-good state, not just that the revert applies
  cleanly.
- Session-context rotation: once a peer/profile's dispatch session
  context exceeds ~75%, route the next dispatch through
  `--session-policy fresh`.
- EXH ceilings: cc<=2.0 (minimal direct work), ag/cx<=4.0 (used
  actively), ag/cx's EXH must stay numerically above cc's -- if it drops
  below, that's a signal to delegate more, not less.
