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
- **Outbox table split -- COMPLETE** (tracked here for the first time
  2026-08-11; the progress notes in `OUTBOX-SPLIT-PROGRESS-2026-08-09.md`
  were never folded into this roadmap). The additive/dual-write
  increments (`a40c301`, `88c53e6`, `b652543`) and all three steps that
  document lists under "What's left" have since landed. **That document
  is now stale and should be read as history, not as an open work item.**
  Verified 2026-08-11 against a freshly-migrated head database
  (`empirical_probe`): `outbox_events` absent, `outbox_checkpoints`
  absent, `effect_deliveries` present, and `effect_receipts`' FK targets
  `effect_deliveries(event_id)` -- i.e. the read-path cutover (Step E3),
  the previously-deferred FK (migration 0015 / Step E1), and the table
  drop (migration 0017 / Step F) are all done. `sqlite_governance.py`'s
  `list_outbox_events*` methods retain the legacy *name* but read from
  `event_log`/`effect_deliveries`; the naming is cosmetic debt, not
  pending cutover work.
- Alembic Scope Note: Alembic cutover remains strictly scoped to Phase 2 (Structural
  debt). The bespoke runner (`peerhub/persistence/migrations/*.sql`) is the sole
  runtime migration engine in Phase 1.


## Phase 2 — Structural debt
Status: in progress. Scoped 2026-08-10 via cx (read-only, GitHub-mirror
analysis) + ag.effort (local verification/correction) dialectical pass --
see `project_step_ef_ratified_2026_08_10.md` and this session's history
for the scoping conversation itself.

### Read/write UnitOfWork split
Status: **DONE.** Every read used to open a write transaction via
`BEGIN IMMEDIATE`; migrated to an additive `ReadUnitOfWork`
(`PRAGMA query_only=ON` + deferred `BEGIN`), one small commit per domain:
- PH-UOW-READ-01 (`7bad9ff`): `DispatchService.count_active_leases`.
- PH-UOW-READ-02 (`99bbce0`): `GovernanceBroker.get_target`/
  `get_outbox_event`/`get_effect_receipt`/`recover_pending_effects`.
- PH-UOW-READ-03 (`d38e113`): `DispatchService.get_lease`/
  `get_request_and_attempt`/`get_request`;
  `SessionLeaseCoordinator.check_lease_fence`.
- PH-UOW-READ-04 (`d2eeef8`): `RoutingService.get_route_decision`;
  `OperationalProjectionService.get` + `project_pending`'s read-only
  fetch phase (its `_project_one()` write stays a write UoW by design).

**Scope correction found during PH-UOW-READ-04**: the original 13-site
audit miscategorized `HealthService.freeze_admission_snapshot` as
read-only. It's actually an atomic read+write transaction (reads policy/
projections, inserts an admission snapshot, commits together) that an
existing rollback test (`test_health_service_fault_boundaries.py:453`)
requires stay atomic. cx caught this before editing and correctly left
it as a write UoW rather than forcing the wrong migration. Final tally:
**12 read-only call sites migrated, 1 correctly retained as write.**

Every commit independently re-verified by cc (pyright + full pytest),
not just accepted on the implementing peer's self-report.

### Bespoke migration runner → Alembic full cutover
Status: increment 1 (consolidated baseline) completed; increment 2
(runtime cutover) investigated 2026-08-11 and **blocked on a user
decision** — see the increment 2 subsection below. The earlier “schema
v17” wording was
superseded when capability-lease migrations 0018 and 0019 landed: a
fresh authoritative bespoke database now has 19 migration rows and
`PRAGMA user_version = 19`.

Increment 1 replaces the exploratory ~v12 Alembic chain with the single
root revision `v19_consolidated`. The regression proof builds one fresh
database through bespoke migrations 0001-0019 and another through that
Alembic revision, then compares normalized table constraints, columns,
types/defaults/nullability/PKs, foreign keys, explicit and automatic
indexes, `schema_migrations`, `user_version`, and `foreign_key_check`.
The domain schemas match; Alembic adds only its expected
`alembic_version` control table. `docs/migrations.md` defines and tests
the existing-database path: verify bespoke v19, then `alembic stamp
v19_consolidated` without executing baseline DDL.

The runtime still invokes only the bespoke runner. Increment 2 must
switch that ownership explicitly; this baseline increment does not alter
runtime initialization or migration dispatch.

#### Increment 2 (runtime cutover) — RATIFIED 2026-08-11: HOLD, WITH NAMED TRIGGERS

Status: **investigated and dialectically ratified 2026-08-11. Decision:
hold. No runtime code changed, none planned until a trigger fires.**

Two independent peer reviewers (ag.deepthink, cx.deepthink) plus the
original investigator (cc.deepthink) each formed an independent round-1
position, then cross-critiqued a synthesis in round 2. Full convergence:
all three agreed "do not implement Option 1 or Option 2 now." The
decisive new fact, found while re-examining the investigation for round 1:
a machine-wide census (`P:\`, `D:\Engram&Peerhub`, user home) found 212
`peerhub.sqlite3` files total, **all of them test artifacts** (130 pytest
tmpdir, 82 `.pytest_cache`) — **zero live, non-test peerhub databases
exist anywhere on this machine today**, consistent with hub.py still
being the primary system in daily use. That finding both zeroes out
Option 1's "flag day" cost and removes the only real argument for paying
Alembic's runtime cost now.

**Ratified decision:**
1. Keep the bespoke SQL runner as the sole runtime migration engine.
2. Keep increment 1's `v19_consolidated` baseline as a **dev-only**
   artifact — parity-proven schema-of-record and documented stamp target,
   not imported at runtime.
3. Revisit only when one of two named triggers fires:
   - **(a)** peerhub adopts SQLAlchemy Core/ORM for its persistence layer
     (makes Alembic's autogenerate value real; today's layer is
     hand-written `sqlite3` with no declarative models, so Alembic's main
     selling point is unused).
   - **(b)** peerhub is about to become the primary dispatch path and
     hold irreplaceable operational data — fire this **as a pre-cutover
     gate, before** that data accumulates, not after (cx's refinement).
4. If a trigger fires, **Option 1 (freeze the then-current schema as the
   floor, delete the bespoke runner) is the default answer, not Option
   2** — increment 1 proved baseline consolidation is mechanical and
   repeatable at any future schema version, so there's no lock-in
   pressure to act early. Re-run the machine-wide census immediately
   before any flag day (cx's refinement: the "zero anywhere" finding
   covers only scanned volumes/profiles on this machine, not other
   hosts or network paths). Option 2 (a full incremental revision chain)
   is justified only if that future census finds a supported below-head
   database that cannot first be upgraded via the bespoke runner.
5. A genuinely separate, smaller defect surfaced during the investigation
   and belongs in its own future increment, **not bundled into this
   decision**: the bespoke runner is atomic per-migration but not across
   a sequence (a reproduced failure advanced `user_version` 12→13 then
   died on 0014, leaving a durable stranded intermediate state with no
   record a longer sequence was intended). Fix before peerhub becomes the
   primary dispatch path.

##### Measured behaviour of `SqliteStateStore.initialize()` today

Every row below was produced by running the real `initialize()` against a
purpose-built database, not reasoned about. States were constructed by
applying the genuine bespoke migration scripts in order (so a "v18"
database really is v18, not a v19 database with rows deleted).

| State | Before | `initialize()` | After |
| --- | --- | --- | --- |
| A fresh workspace, no db file | — | OK | v19, 39 tables, rows 1-19 |
| E db file exists, zero tables | empty file | OK | v19, 39 tables, rows 1-19 |
| B existing bespoke v19 | v19 | OK (no-op) | v19, unchanged |
| C existing bespoke v18 | v18 | OK | migrated to v19 |
| C2 existing bespoke v12 | v12, 37 tables | OK | migrated to v19, 39 tables |
| D1 v19 + `alembic_version` = head | v19 + marker | OK (marker ignored) | unchanged |
| D2 `alembic_version` = head, rows to 12 | v12 + marker | `OperationalError: table event_log already exists` | **left at v13** |
| D3 v19 + `alembic_version` = deleted revision | v19 + stale marker | OK (marker ignored) | unchanged |
| D4 v19 + empty `alembic_version` table | v19 + empty marker | OK (marker ignored) | unchanged |
| D5 v19 + two `alembic_version` rows | v19 + branched marker | OK (marker ignored) | unchanged |

Two facts follow directly. First, `alembic_version` is **completely
invisible** to the runtime today — `grep -rn alembic peerhub/ tools/`
returns nothing — so states D1/D3/D4/D5 are all currently harmless, and
any cutover that starts *reading* that table converts four benign states
into potential failure modes. Second, D2 exposes a pre-existing property
worth recording separately: the bespoke runner is atomic **per
migration** but not **across the sequence**. It advanced user_version
12 → 13, then died applying 0014, leaving a durable intermediate state.
That is not caused by the cutover, but any cutover design inherits it.

##### Why the obvious design is unsafe

The natural shape — "if `alembic_version` is present and at head, skip
the bespoke runner; otherwise run the bespoke runner and then auto-stamp"
— fails the no-silent-corruption bar, for a reason specific to where the
project stands right now:

`docs/migrations.md` § *Authoring New Bespoke Migrations* currently, and
correctly, instructs the next schema change to be authored as bespoke
`0020_*.sql` plus a new version guard in `initialize()`. Under the design
above, a database stamped at `v19_consolidated` would take the skip
branch and **never receive migration 0020**, with no error — the
application would then run against a schema missing 0020's objects. That
is precisely the silent-corruption class the increment is supposed to
avoid. Gating the skip on "alembic at head AND bespoke max version == 19"
technically avoids it, but only by making the Alembic marker
non-authoritative: two version authorities that must be hand-synchronised
on every future migration, which is worse than either engine alone.

Auto-stamping considered on its own is *not* the sharp edge here. After
the bespoke runner completes, the database is at v19 by exactly the trust
standard the runner already uses (`if 19 not in versions`), and
increment 1 proved bespoke-v19 and `v19_consolidated` produce identical
schemas. Writing that marker inside the existing `BEGIN IMMEDIATE` adds
no new trust assumption and needs no Alembic import. The problem is that
it also buys nothing: no Alembic revision 0020 exists, nothing reads the
marker, and the write is **not reversible** — reverting the commit would
still leave `alembic_version` rows in every database that ran the new
code. A durable write to every user's database in exchange for zero
present benefit is the wrong trade.

##### Hard blockers to Alembic actually owning runtime invocation

These are not judgement calls; each was verified against the tree.

1. **Alembic assets are not packaged.** `alembic/` and `alembic.ini` live
   at the repository root, outside `[tool.setuptools.packages.find]`'s
   `include = ["peerhub*"]`, and `peerhub.egg-info/SOURCES.txt` contains
   zero `alembic` matches. Runtime invocation works today only because
   the install is editable; from a built wheel there would be no
   `alembic.ini` and no `versions/` directory. Fixing this means moving
   the migration assets inside the `peerhub` package, which also
   invalidates the paths in `docs/migrations.md` and the parity test.
2. **Alembic is a dev-only extra.** Runtime dependencies are
   `psutil` and `pydantic`; `alembic>=1.13.0` sits in the `dev` extra.
   Making it the runtime engine promotes Alembic **and SQLAlchemy**
   (2.0.51 in this environment) to hard runtime dependencies of a project
   whose persistence layer is deliberately stdlib `sqlite3`. This is a
   dependency-surface decision, not a refactor.
3. **Measured startup cost.** `import alembic.command` costs ~578 ms
   standalone and adds ~439 ms on top of `import peerhub.cli`, whose own
   cumulative import time is ~375 ms (`python -X importtime`, this
   machine). `initialize()` runs on every `create_runtime()`
   (`peerhub/runtime.py:78`), i.e. every CLI invocation, so an
   unconditional import roughly doubles CLI startup latency. A lazy
   import behind an "is a migration actually needed?" check avoids most
   of this, but is an explicit design requirement, not a freebie.
4. **`env.py` resolves the database from the process cwd.**
   `alembic/env.py:61-63` builds the URL from
   `Path(os.getcwd()) / ".peerhub" / "peerhub.sqlite3"`, ignoring the
   store's own `database_path`. Runtime invocation with cwd ≠ workspace
   root would migrate, or create, the wrong file. The parity test only
   passes because it `monkeypatch.chdir`s first. Runtime use requires
   `env.py` to accept a caller-supplied connection via
   `config.attributes["connection"]`.
5. **`env.py` builds its own engine**, so Alembic's connection would not
   carry the store's `foreign_keys = ON`, `busy_timeout`, or
   `synchronous = FULL` PRAGMAs (`sqlite.py:358-371`). With
   `busy_timeout` at SQLite's default of 0, concurrent `initialize()`
   calls would fail fast on `SQLITE_BUSY` rather than waiting.
   Concurrent-stamp behaviour under Alembic's own transaction handling is
   **untested and must be proven before any implementation**, not assumed
   safe.
6. **Alembic cannot serve state C at all.** `v19_consolidated` is a
   single root revision with `down_revision = None`; its `upgrade()`
   creates the full final schema. There is no incremental path from v1-18,
   and running it against such a database collides on the first existing
   table. So "Alembic is the runtime engine" today can only mean "Alembic
   for fresh databases, bespoke for everything else" — dual engines, not
   a cutover.

##### The decision the user or next session must make

Blocker 6 is the load-bearing one, and it forks into two coherent
end-states. Both are legitimate; they cannot be blended safely.

- **Option 1 — declare bespoke v19 the minimum supported schema.**
  `initialize()` fails closed on any database below v19 with an
  actionable message pointing at the documented upgrade-then-stamp
  procedure. The bespoke runner is then deleted outright rather than kept
  as a fallback, Alembic owns fresh creation and all future revisions,
  and there is exactly one version authority. Clean, but it is a real
  flag day and needs an answer to: **do any live workspaces sit below
  v19?** If the answer is "only developer scratch databases", this is
  cheap; the roadmap should not guess.
- **Option 2 — author Alembic revisions covering 0001→0019 as a real
  chain**, so `upgrade head` can carry a v12 database forward. No flag
  day and every state gets one engine, but it discards increment 1's
  single-baseline decision and means re-deriving and re-parity-proving 19
  revisions.

Either option additionally requires blockers 1-5 resolved: package the
Alembic assets, promote the dependency with user sign-off, lazy-import,
rework `env.py` to take an injected connection, and prove concurrent
`initialize()` behaviour.

One further item to settle before Alembic is runtime-wired and packaged:
`v19_consolidated.downgrade()` drops all 39 domain tables. That is
correct for a baseline revision, but it puts a one-command total-data-loss
path inside the shipped package. Decide whether to guard it or accept it.

**Recommendation: do not implement increment 2 until Option 1 or Option 2
is chosen.** In the meantime the status quo is genuinely fine — the
bespoke runner handles all four real-world states correctly today (rows
A-D1 above), and increment 1's baseline retains its value as the
parity-proven schema-of-record and the documented stamp target.

#### Bespoke runner sequence-derivation fix (`a493843`, landed 2026-08-11)
The "cross-sequence atomicity" hypothesis from the increment 2
investigation was itself wrong: a mid-sequence crash already raises
loudly and resumes cleanly on retry (per-migration transactions +
`schema_migrations` already made this safe). The real, previously
invisible silent-desync class was code/file desync — `initialize()`
applied migrations off a hand-maintained ladder of `if N not in
versions:` guards in `sqlite.py`, so shipping a new `.sql` file without
also adding its guard returned normally while silently never applying
it. Fixed by deriving the applied sequence from the packaged migrations
directory itself, plus two new fail-closed checks: a database recording
migrations this build doesn't ship is rejected (previously invisible
downgrade-in-place risk), and any migration that runs without recording
itself in `schema_migrations` now fails loudly instead of silently
re-running forever. 9 new tests, each guard mutation-tested.

**Follow-up fixed (`b6a827a`, 2026-08-12).** Migrations 0016-0020's
documented "fail-closed" in-transaction `PRAGMA foreign_key_check` was a
no-op -- SQLite returns violation rows rather than raising, and
`executescript()` discards results. L1 triage first investigated all 20
migrations empirically for any that intentionally rely on a transient
cross-migration FK violation (none do), then `_apply_migration()` was
given its own Python-side `PRAGMA foreign_key_check` after each
migration's `executescript()`, raising immediately and naming the exact
migration (version + filename) if violations exist. Independently
reproduced by a second peer: the real improvement is attribution and
fail-fast (a violating migration is now caught before any later
migration can run on top of the bad state), not detection-from-zero --
`initialize()`'s pre-existing end-of-sequence check was already a
backstop. 9-case migration-sequence suite stayed green throughout.

### Capability-lease design implementation
Status: **APPROVED, implementation COMPLETE.** All 5 increments of
Section 7.5's plan are committed (increment 5 closed as a deliberate
evidence audit, not new code -- see below):
- Increment 1 (`c91cc0b`): `CapabilityTier`/`EnforcementLevel` enums,
  frozen `CapabilityLease`/`CapabilityGrantDecision` DTOs,
  `validate_capability_binding()`, `mandatory_enforcement_floor()`, 24
  negative unit tests. Pure/isolated, no call sites touched.
- Increment 2 (`df1ef77`): migration 0018 (`capability_leases` table),
  `dispatch_requests.required_capability_tier` (nullable/no-default),
  UoW/repository read+write methods, rollback + replay-identity tests.
- Increment 3 (`ca5862e`): `required_capability_tier` threaded end-to-end
  (API payload → CLI `--capability-tier` flag → routing/route-decision
  digest → admission → durable request), migration 0019
  (`route_decisions.required_capability_tier`, same nullable/no-default/
  fail-closed-on-legacy-row pattern).
- Increment 4 (`ad56938`): the actual enforcement gate. Authoritative
  atomic lease issuance in `AdmissionCoordinator` (new
  `peerhub/dispatch/capability_policy.py` for the minimal concrete
  `CapabilityPolicy`/`PeerEnforcementEvidenceProvider` -- every built-in
  peer resolves to `enforcement_ceiling=None` today, so mutating
  dispatches fail closed by construction, not a peer-specific
  carve-out); replay-path validation instead of re-minting; the
  pre-spawn `DispatchService.require_dispatch_capability()` gate wired
  into `dispatch_and_execute()`, re-checking policy revision (closes
  cx's revocation-window finding), adapter `peer_kind`, and the
  mandatory floor immediately before any subprocess can spawn. The
  security-property tests (`aadfb33`) prove this: mutating `ag` denied
  before `plan_invocation()`/`run_process()` (spy-verified call counts,
  mutation-tested -- gate-removed and gate-reordered variants confirmed
  to fail correctly before being reverted), READ_ONLY succeeds, replay
  returns the identical lease (tagged `IdSource` so a re-mint can't
  vacuously match), and boundary fail-closed including an `IntEnum`
  type-confusion risk found while writing the tests (an unguarded
  validator would silently accept raw `1` as `WORKTREE_WRITE`). No
  production bugs found this pass.
  `AuthenticatedSubject`/`CallerIdentityProvider` (`e99ad67`) replaced
  `direct_ask.py`'s forgeable `"cli-user"`/`actor_authorized=True` pair
  with a real OS-process-owner-derived identity
  (`peerhub/core/identity.py`) that fails closed if unresolvable, flows
  through the whole chain as an opaque object instead of being
  reconstructed at each layer, and has no CLI override flag.
  Post-plan `InvocationEnforcementReceipt` + `record_dispatch_intent*()`
  revalidation (`e8f7745` + test `c652513`) closes the errata 7.2 final-
  paragraph gap: re-runs `validate_capability_binding()` +
  `CapabilityPolicy.revalidate()` inside `record_dispatch_intent*()`'s
  own write transaction, proven to reject a policy-revision change that
  happens between the pre-plan gate and the dispatch-intent commit (the
  actual TOCTOU window, not a re-test of the pre-plan gate).
  **Increment 4 is now fully complete** -- every item in errata Section 7
  is implemented and tested.
- **Increment 5 (adapter translation) complete as an audit, not an
  implementation** (`f9580f0`, errata Section 8): investigated whether any
  real adapter (`RealAgyAdapter`/`RealClaudeAdapter`/`RealCodexAdapter`)
  can supply DIR-004-qualifying evidence for `InvocationEnforcementReceipt`.
  Finding: none can. All three construct argv with no sandbox/confinement
  control and an empty environment delta, and the receipt is committed
  before process spawn, so post-spawn observation can't retroactively
  justify it. The placeholder `"unverified"` tags (workflows.py, per
  `e8f7745`'s comment) are correct as-is and were deliberately left in
  place rather than replaced with fabricated evidence. Section 8 records
  the 4 prerequisites (attested pre-spawn control, plan-bound digest,
  empirical negative probe, receipt corroboration at the post-plan gate)
  required before any adapter's receipt can honestly go positive. **This
  closes out the capability-lease design's Section 7.5 increment list --
  no further increments are planned until those prerequisites exist.**

Below is the design-history/ratification record (kept for context on
*why* the design looks the way it does -- skip to "Status: APPROVED"
further down if you just need the current state).

Two design passes: `docs/design/CAPABILITY-LEASE-DESIGN-2026-08-08.md` (original) →
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

**Status update 2026-08-10 (same day, later): HOLD addressed, ready for
implementation review.** cx wrote a normative Section 7 addendum to the
same errata file (commit `11adb7a`) directly resolving all 4 blockers --
coordinator-owned atomic lease issuance (`AdmissionCoordinator.
admit_request()` mints; `_load_admission()` becomes replay-validator
only), one shared `validate_capability_binding()` +
`CapabilityPolicy.revalidate()` pair invoked identically at fresh
admission/replay/pre-plan/final-dispatch-intent, a real typed
`required_capability_tier` threaded end-to-end from the API payload
through routing/admission/the durable lease (explicitly NOT reusing
`CommandDescriptor.mutability`, a different axis), and a code-owned
mandatory enforcement floor that denies `ag` mutation outright (not
advisory-logs it) until `CONFINED` evidence exists. Also closed two
things cx found during its own consistency pass: a revocation-window gap
(policy could change between pre-plan and final-intent with no re-check)
and a real auth bypass in `direct_ask.py` (hardcoded
`authenticated_principal="cli-user"` / `actor_authorized=True`, verified
at direct_ask.py:201-202 -- not real authority evidence).

Section 7.5 lays out 5 independently-reviewable implementation
increments (enums/DTOs/validators → schema+repository → tier threading →
enforcement gate → adapter translation) with required negative-test
coverage. Section 7.6 explicitly separates the fail-closed security
decisions (no ambiguity, ready to implement) from one genuine future
product choice left open (a convenience CLI default tier) -- correctly
not invented here.

**Status: APPROVED for implementation (2026-08-10, closing round).**
`ag.opus` (who wrote the original errata Section 7 corrects) hit a real
429 `RESOURCE_EXHAUSTED` quota error and couldn't do the counter-check;
`ag.gptoss` did it instead (same peer, different profile/pool). Verdict:
endorsed -- no disagreement with Section 7's security logic, only
mechanical implementation caveats (call-site signature ripple, DI wiring
for the new `CapabilityPolicy`/evidence-provider dependencies, CLI flag
must be required not optional). **Caveat on this approval's weight**:
`ag.gptoss` is a lower-capability profile than `ag.opus` -- treat this as
a real but less rigorous confirmation than the Step E/F ratification
rounds got from `ag.effort`/`ag.deepthink`. If `ag.opus` becomes
available again, a quick sanity pass from it would strengthen this
closure but is not currently blocking.

This is the mutation-authorization mechanism hub.py's own preflight
system was found to NOT actually enforce (see
`project_mutation_lease_design_2026_08_08.md`); peerhub should not repeat
that gap. Implementation can proceed per Section 7.5's 5 increments
whenever picked up -- start with increment 1 (enums/DTOs/pure validators
+ negative unit tests), no code exists yet.

### Resolved, not part of Phase 2 itself
2 pre-existing test failures (`test_client_never_imports_persistence`,
`test_generator_runs_and_produces_valid_manifest`), confirmed unrelated
to any Phase 1/2 work via git-stash bisection, were fixed in `ac26ed9`
(first `cc.*`-profile peer dispatch used this session) -- see
`docs/design/OVERNIGHT-INFRA-LESSONS-2026-08-10.md` for detail.

## Phase 3 — Real orchestration loop (the actual hub.py replacement)
Status: not started. **This is the critical path** -- nothing before this
phase makes peerhub capable of doing what hub.py does today (dispatch a
query to a peer CLI, get a structured response back, track session/
context/quota state, retry/failover on peer trouble).
- **Dispatch-loop contract surface -- DESIGN RATIFIED 2026-08-12
  (commit `d267750`); T1 increments 1, 2, and 3 are now BUILT.** Adapter
  wiring, session continuation, streaming decode, error-taxonomy
  mapping, and tool-call parsing were bundled into one design topic and
  taken through 3 dialectical rounds -- see
  `PHASE3-DISPATCH-LOOP-CONTRACT-DESIGN-2026-08-12.md`. Corrected
  premise: most of the inner machinery already existed (single-attempt
  dispatch, `authorize_retry()`,
  `AdapterRequest.requested_session_action`, `OutputDecoder.feed()`'s
  incremental shape, `ErrorCode`/`OperationalFailureCategory`) -- the
  design ratifies an all-additive-except-2-real-behavior-changes
  contract surface (retry-neutral `AttemptFailureClassification`,
  `TerminalClassification` surfaced on `AskResult`, an optional
  `session` parameter on `dispatch_and_execute()`) plus one hard
  invariant (adapter classification can never itself authorize a retry,
  enforced by type shape). Validated by a design-validation step that
  has since been superseded by and folded into the production
  `classify_attempt_failure()` mapper and
  `tests/unit/dispatch/test_model.py`.
  **Of the design's Section 5 increments: 1 (classification plumbing),
  2 (session continuation), 3 (Codex streaming), and 4 (tool-call
  capture) are DONE; 5 (outer retry/resume/failover loop) is not
  started.** Two surface items the design prescribed did NOT land as
  written: `DecodedOutput.session_id` was abandoned in favour of
  `DecoderEventKind.SESSION_IDENTITY` (which already existed), and
  `DecoderEventKind.TOOL_CALL` landed in increment 4.
  - **T1 increment 1a -- classification plumbing, data layer
    (`bfdd8b2`). DONE.** Relocates `TerminalClassification` to
    `dispatch/contract.py` (re-exported from `dispatch/process.py`),
    adds the frozen `AttemptFailureClassification` DTO and the two
    `AskResult` fields, adds `ErrorCode.SESSION_INVALID` and
    `ErrorCode.INVOCATION_PLAN_REJECTED`, implements the central
    `classify_attempt_failure()` mapper
    (`peerhub/dispatch/model.py:1400-1428`), and round-trips both new
    fields through the SQLite codec.
  - **T1 increment 1b -- `VENDOR_ERROR` emission (`bf9f4ad`). DONE.**
    All three real adapters stop writing
    `ProtocolAssessment.protocol_failure=INTERNAL_ERROR` merely because
    the process exited nonzero, and each decoder emits
    `DecoderEventKind.VENDOR_ERROR` with a normalized
    `{normalized_kind, evidence_source}` payload, giving the two new
    codes a production path. Shipped with synthetic byte patterns,
    marked `TEST NEEDED` per DIR-004 -- since corrected by `3b317f0`
    below.
  - **T1 increment 2a -- workflow-owned session capability gate
    (`f516760`). DONE.** `dispatch_and_execute()` gains
    `session: SessionHint | None = None`, checks
    `Capability.SESSION` before planning, and raises the new typed
    `UnsupportedCapabilityError` instead of the adapters' old bare
    `ValueError` (`peerhub/application/workflows.py:562, 585-598`).
  - **T1 increments 2b/2c/2d -- per-adapter session RESUME
    (`dda4956` / `f4b2907` / `c3d6ceb`). DONE.** All three real
    adapters now advertise `Capability.SESSION` and plan a real resume
    invocation. The three CLIs are genuinely asymmetric, so the
    adapters are too:

    | adapter | RESUME invocation | session-ID capture | CREATE |
    | --- | --- | --- | --- |
    | `cc` / `claude.cmd` (`dda4956`) | `--resume <id>` | none, and none is needed -- the ID is caller-pregenerated via `--session-id <uuid>` before invocation | deferred |
    | `cx` / `codex.cmd` (`f4b2907`) | `exec resume --json <id> <prompt>` | `SESSION_IDENTITY` from the `thread.started` line's `thread_id` | deferred |
    | `ag` / `agy.exe` (`c3d6ceb`) | `--conversation <id>` | `SESSION_IDENTITY` from the top-level `conversation_id` field | deferred |

    Claude's empty capture cell is a permanent architectural
    asymmetry, not a gap: Codex and Agy mint a new ID server-side that
    can only be read back out of the output, while Claude's is chosen
    by the caller beforehand. `SessionAction.CREATE` is deferred for
    all three; see the design doc's Section 5 for the gate defect that
    deferral exposes.
  - **T1 increment 3 -- Codex streaming (`dfde073`). DONE.** Adds the
    ordered runner-to-decoder event path in `pipe.py`, incremental JSONL
    remainder buffering in `CodexOutputDecoder`, and advertises
    `Capability.STREAM` for Codex only; Claude and Agy remain
    terminal-only.
  - **T1 increment 4 -- tool-call capture (`9ff7813`). DONE.**
    `DecoderEventKind.TOOL_CALL` added; Codex's decoder normalizes
    `command_execution` items via `item.completed`, stripping conflated
    result fields. Codex-only, same structural reason as increment 3's
    streaming scope -- Claude/Agy don't expose tool calls in their
    current `--output-format json` invocation mode, though they do via
    unused `stream-json` mode.
  - **T1 increment 5A -- disposition mapper + DTOs + adjudicator implementing the Section 3 state-treatment table (`a5556a2`/`24102f8`/`a6118a9`). DONE.**
  - **T1 increment 5B -- authorization plan ratified (`04250ff`, simplified `e5d9566`) and IN PROGRESS.**
    Closes 2 empirically-confirmed blocking seams: 9.1 (retry rotates the
    request lease but capability validation still checked the
    pre-rotation lease) and 9.2 (no failover target-selection
    mechanism). Sub-increments:
    - 5B-1a (`7d12578`) -- migration `0022_retry_authority.sql`
      (versions `capability_leases` by `authorized_attempt_number`/
      `previous_attempt_id`; new `retry_policies` table) + persistence
      ports. DONE.
    - 5B-1b (`31f5794`) -- `CapabilityLease`/`ValidatedCapabilityLease`
      gain the new fields with safe attempt-1 defaults;
      `validate_capability_binding()` generalized to branch on
      `authorized_attempt_number` (closes seam 9.1's validation half).
      DONE.
    - 5B-1c (`246fb8c`) -- rewired the 3 remaining
      `get_capability_lease_by_command_id()` call sites (now removed
      entirely) to unambiguous attempt/session-lease-keyed lookups
      (`admission.py`, `attempt_lifecycle.py`, `service.py`); added the
      missing authorization gate to `create_attempt()`, closing seam
      9.1's enforcement half. DONE.
    - 5B-2 (same-target atomic `authorize_retry()`, tagged-union
      `SameTargetRoute`) and 5B-3 (failover route selection + atomic
      rebinding, closes seam 9.2) remain.
    5C (outer-loop integration) remains, blocked on 5B.
  - **Post-hoc correction: `classify_attempt_failure()` was never
    wired into production (`858aec6`).** Increments 1a/1b shipped a
    fully unit-tested classifier that the only production
    `AskResult` construction site never called, so every real
    dispatched attempt recorded `terminal_classification=None` and
    `failure_classification=None` and both new codes were unreachable
    in practice. Three prior independent-review rounds tested the
    classifier and codec in isolation and missed the absent
    integration call; a later source/test completeness check found it.
    Now called at `peerhub/application/workflows.py:898-916`, with
    three end-to-end tests through the real `dispatch_and_execute()`
    entry point. This is a real gap 1a/1b shipped with, not a
    refinement.
  - **Post-hoc correction: 1b's vendor-error patterns were grounded in
    real captures (`3b317f0`).** Live probes showed 1b's synthetic
    fixtures did not match real failures: Agy's decoder assumed the
    JSON `error` field is always an object (a real auth failure
    returned a string) and broke when a stderr preamble preceded the
    JSON; Codex's only recognised a nested `error.code` shape (a real
    network failure emits a flat `{"type":"error","message":...}`) and
    never handled `turn.failed`. New fixtures use `[cli_live]`
    captures where a live failure was reproducible; the one case that
    was not (Agy's string-error path) stays marked `TEST NEEDED`. The
    work's own review rounds caught 2 further real bugs it had
    introduced.
- Wire the 3 real PeerAdapters (agy/claude/codex, landed Stage 3) into an
  actual dispatch loop that: sends a query, decodes the peer's real
  output via the adapter, returns a structured result to the caller.
  (Single-attempt dispatch works today; the missing piece is the outer
  bounded retry/failover orchestrator -- design Section 5 increment 5.)
- Streaming decode is implemented for Codex in T1 increment 3
  (`dfde073`); Claude and Agy remain terminal-only.
- Tool-call parsing (peers that invoke their own tools mid-response --
  not yet handled at all).
- Health/quota tracking equivalent to `diag.py` -- peerhub's CLI `status`
  command currently only reports migration count + active lease count,
  nothing like diag's EXH/context/pool breakdown. Needs its own design
  pass on where that data would even come from for peerhub-native
  dispatches (hub.py's diag reads CLI-native stat files per peer; a
  peerhub-orchestrated dispatch would need to either read the same files
  or maintain its own).
- **Multi-peer broadcast/consensus -- gap identified 2026-08-11; designed,
  revised through three dialectical review rounds, and closed by a
  validated prototype the same day (commit `8650314`). See
  `PEERHUB-MULTIPEER-BROADCAST-DESIGN-2026-08-11.md`.** Primitive A's
  correlation schema has landed as migration `0020_broadcast_correlation`
  with 7 passing tests
  (`tests/integration/persistence/test_broadcast_correlation_schema.py`);
  the design doc's Sections 7.3 and 8 record round 3 and the prototype
  evidence. **`BroadcastCoordinator.fan_out()` is now BUILT (T3
  increments 1-3, commits `75dafcd`/`914def5`/`d5cc70e`/`d357b2d`)**:
  happy-path sequential fan-out with `wave_of` two-wave threading,
  partial-failure disposition (`all_completed`/`partial`/
  `none_completed`, correct across a mix of completed/failed/timed_out
  legs), deadline-based skipping of not-yet-dispatched legs (migration
  `0021_broadcast_leg_timeout_state` widens the leg-state constraint),
  and a tripwire test proving response content is never persisted in
  the correlation tables. **Crash-linkage recovery (resuming an
  interrupted round after a coordinator crash) is investigated and
  deliberately deferred as its own increment 4** (commit `be26484`,
  design doc Section 6.1) -- `fan_out()` has no resume entry point
  today; real recovery needs a genuinely new read-before-write
  reconciliation capability, not a quick addition.
  hub.py has real primitives for this
  that Phase 3's scope above doesn't mention: `ask-all`
  (parallel-broadcasts one query to every active peer, threaded, collects
  and prints all responses -- `hub.py:7564`), a full
  `consensus-propose`/`-vote`/`-check`/`-sweep` subsystem (round-based,
  voter-health-eligibility filtering, a frozen `quorum_snapshot`,
  `collab_rate`-driven unanimous-vs-majority decision, `MAX_ROUNDS=3`
  before forced human escalation -- `hub.py:7676` on), plus
  `ask-coordinator`, room/thread primitives, and `new-topic`.

  The design pass **split the gap in two and recommends building only
  half of it now**, on measured grounds:

  - *Broadcast is justified and should be built.* Every dialectical
    round run this session was a two-wave fan-out (N independent
    positions, then a cross-critique wave over a synthesis) with no
    voting anywhere in it. The design makes each leg an ordinary
    dispatch -- own `CommandID`, own admission, own capability lease,
    own attempt -- so broadcast inherits the capability-lease
    enforcement gate instead of sitting beside it. The IPC file-reuse
    defect hit this session cannot recur, though *not* for the reason
    draft 1 gave: round 1 verified that all three real adapters inline
    the prompt into `argv` and materialize no prompt artifact at all
    (`artifacts=()`), so the property holds because the broadcast
    contract accepts prompt *content* and never a caller-owned
    query-file path -- there is no shared file to reuse.
  - *Formal consensus is designed but deliberately NOT built yet.* A
    census of `.ai/consensus/*.json` found 65 real rounds, 61 of them in
    July and **zero in August** -- the Alembic increment-2 ratification
    itself, an R:10-class `governed_decision`, opened no formal round at
    all. Timestamp analysis shows 44% of historical rounds had every
    vote land within 2 seconds, i.e. batch-written by one actor after
    the deliberation concluded elsewhere; the round file is functioning
    as a ratification receipt, not as the decision mechanism. Porting
    voting machinery whose current adoption is zero, while hub.py still
    owns protocol enforcement, would produce two consensus systems that
    can disagree about the same decision.

  R:10 plus `protocol.md` §4.4 genuinely cannot be expressed without
  per-voter voting records, so Primitive B (`ConsensusRound`) is
  specified in full in the design doc against a named trigger. **It is
  tracked under Phase 4 as a blocking gate, not here** -- round 1 of the
  design review moved that trigger to the correct side of the line. See
  the Phase 4 entry below.

  The two review rounds cut the build down rather than growing it, each
  time off a verified defect. Round 1 invalidated draft 1's durability
  argument: `AskResult` carries only outcome metadata, and
  `DecodedOutput.canonical_text` -- the actual response text -- is
  persisted nowhere, so a correlation row alone would record that a
  response existed with no way to read it. Round 2 then found three
  integrity gaps in draft 2's fix (the artifact subsystem is
  input-oriented and cannot capture output; a crash window sits between
  attempt terminalization and spill; a non-null reference column proves
  nothing about retrievability), plus a **proven** cycle-safety bug --
  SQLite defers FK checking to end-of-statement, so a single multi-row
  INSERT could create a `wave_of` cycle that draft 2's CHECK+FK claimed
  to prevent.

  Draft 3 therefore **descopes durable response transcripts out of the
  initial increment** to their own separately-ratified increment, with
  those three gaps recorded as its known open problems. The deciding
  argument is layering, not cost: draft 2 would have given broadcast a
  stronger durability guarantee than the single-peer dispatch path it
  wraps, when `direct_ask` drops response text today. Transcript
  durability is a dispatch-layer property; broadcast should inherit it
  the same way it inherits the capability-lease gate. Primitive A is now
  one coordinator, two correlation tables, and a sequential fan-out;
  cycle safety is enforced by a `BEFORE INSERT` trigger measured across
  seven cases.

  Round 3 then found two further defects -- `INSERT OR REPLACE` could
  bypass the `wave_of` immutability trigger, and the idempotency-key
  namespace was unspecified so two legs could alias one command -- which
  met the pre-committed stop-paper threshold. Both are fixed and
  empirically tested in migration `0020`. Draft 3's two remaining open
  questions are resolved: correlation-durable is the ratified floor (the
  landed schema stores no response content), and recovery is
  resubmission-with-matching-digest (the leg idempotency key is bound
  into `envelope.params`, so a changed prompt fails by digest).
  **Deferred, each with a named trigger:** durable response transcripts
  (a dispatch-layer increment, three known open problems recorded in the
  design doc's Section 3.3.3), parallel fan-out (blocked on measuring
  SQLite write contention), and Primitive B (Phase 4 gate, below).

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
- **BLOCKING GATE: `ConsensusRound` / Primitive B must be built, tested,
  and ratified BEFORE the first `r10_requires_finalized_for` decision
  class is routed to peerhub.** Moved here from the Phase 3 backlog
  2026-08-11 per the broadcast design doc's own Section 5.1 correction:
  the trigger is the *decision* to route R:10 traffic, not its arrival.
  Starting Primitive B after R:10 traffic already flows would leave a
  window in which peerhub carries governed decisions it cannot
  mechanically ratify. Fully specified in
  `PEERHUB-MULTIPEER-BROADCAST-DESIGN-2026-08-11.md` Section 4 (durable
  state, decision function, failure/timeout, `deliberation_ref`);
  deliberately unbuilt because a census found zero consensus rounds
  opened in August 2026 and hub.py still owns protocol enforcement.
  Ordering constraint: the deferred durable-response-transcript increment
  must land **before or with** Primitive B, or `deliberation_ref` ships
  as an evidence field that cannot be audited.

## Cross-cutting tracked items (not owned by a single phase)

Added 2026-08-11 -- these were ratified or recorded in design docs but
had never appeared in this roadmap.

- **`tools/peerhub_facts/` -- BUILT (`dd7e3f4`).** Implements the
  fact-refresh procedure from `FACT-REFRESH-PROCEDURE-R1.md`: resolves
  each real peer CLI (ag/cc/cx), captures version/help/dependency facts
  via live (non-mocked) probes, compares against `docs/compatibility/`'s
  shipped contracts, and reports drift with per-fact evidence metadata.
  Two interrupted implementation sessions left a substantial but untested
  partial version; a fresh pass verified the architecture matched spec
  and fixed 5 real gaps (help-token substring-matching false positives,
  decoder protocol/field comparison never actually running, a skippable
  mandatory test run, `pip check` failure not mapping to the contracted
  exit code 2, missing per-fact evidence metadata in the Markdown
  report) rather than either blindly trusting or discarding the partial
  work. Independently verified by a second peer, including running the
  live probes itself (ag `1.1.12`, cc `2.1.222`, cx `0.147.0`, all
  correctly resolved) and confirming the tool immediately found real
  drift on this machine: `torch 2.12.1` requires `setuptools<82`, but
  `83.0.0` is installed. Full suite 716 passed (up from 655), fresh
  pyright clean (note: `tools/` and `tests/` are excluded from pyright's
  `include` scope machine-wide, same as all 6 sibling `tools/*`
  packages -- existing convention, not a gap introduced here).
  **Scope limit:** it checks CLI/dependency/decoder/pytest drift only. It
  does not check design-doc status headers or open-questions accuracy, so
  a green run is never evidence that the docs are current -- see
  `FACT-REFRESH-PROCEDURE-R1.md`, "What this routine does NOT check".
- **Capability-lease enforcement-evidence prerequisites -- zero code,
  trigger-gated.** `CAPABILITY-LEASE-DESIGN-2026-08-08-ERRATA.md`
  Section 8 concluded that no real adapter can supply DIR-004-qualifying
  enforcement evidence today, so every provider keeps
  `enforcement_ceiling=None` / `source_tag="absent"` and
  `dispatch_and_execute()` keeps its honest `"unverified"` receipt
  fields. **Trigger: before any adapter's receipt is changed to a
  positive enforcement claim, all four of the following must exist** --
  (1) a machine-owned launcher/evidence provider that prepares and
  attests the realized control for the exact invocation before the
  process can execute, rather than echoing adapter argv; (2) that
  observation bound to a canonical digest of the exact materialized
  plan/process identity, carrying a DIR-004 source tag; (3) an empirical
  negative probe demonstrating the claimed boundary actually blocks the
  prohibited mutation for that adapter/profile/control combination; and
  (4) the post-plan gate comparing the receipt against that independent
  observation before dispatch intent commits (the current revalidation
  hook checks the capability binding and policy revision but does not yet
  corroborate `controls_description`, `evidence_source_tag`, or
  `plan_digest`). Until then, changing a receipt would fabricate evidence
  and violate DIR-004.

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
- **Doc accuracy is part of every increment, not a later cleanup pass.**
  A design doc's own status header and open-questions/gaps section must
  reflect actual implementation state at the time the increment lands --
  update them in the same commit as the code, the way a schema change
  updates its codec. Two concrete precedents on this project:
  `PEERHUB-MULTIPEER-BROADCAST-DESIGN-2026-08-11.md` carried a stale
  "No code written" header and a stale Section 5.3 well after code
  existed; and worse, across T1's first six increments (`bfdd8b2`,
  `bf9f4ad`, `f516760`, `dda4956`, `f4b2907`, `c3d6ceb`) the status
  claims in the two design docs being implemented were never updated at
  all. Two of the six did touch a design doc, but only for narrow
  local edits; the roadmap still read "implementation not yet built"
  and the contract design's Section 1.2 still listed gaps that had been
  closed several commits earlier. Four increments changed no design doc
  whatsoever. Nothing in the normal per-commit discipline caught it; it
  took a dedicated doc-completeness audit run after the fact. Assume a stale
  status header will be believed by the next reader, including a future
  peer with no session context.
- Session-context rotation: once a peer/profile's dispatch session
  context exceeds ~75%, route the next dispatch through
  `--session-policy fresh`.
- EXH ceilings: cc<=2.0 (minimal direct work), ag/cx<=4.0 (used
  actively), ag/cx's EXH must stay numerically above cc's -- if it drops
  below, that's a signal to delegate more, not less.
