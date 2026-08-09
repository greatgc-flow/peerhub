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
Status: not started.
- Read/write UnitOfWork split (every read currently opens a write
  transaction via BEGIN IMMEDIATE -- fine for correctness, costs
  unnecessary write-lock contention under real concurrent load).
- Bespoke migration runner → Alembic full cutover (currently both coexist,
  Alembic additive-only since Tier-2). Needs a plan for the bespoke
  runner's existing applied-migration history to be legible to Alembic
  without re-running anything.
- Capability-lease design implementation
  (`docs/design/CAPABILITY-LEASE-DESIGN-2026-08-08.md`) -- currently
  ratified-not-implemented. This is the mutation-authorization mechanism
  hub.py's own preflight system was found to NOT actually enforce
  (see `project_mutation_lease_design_2026_08_08.md`); peerhub should not
  repeat that gap.

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
