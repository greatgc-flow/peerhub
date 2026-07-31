# Phase 1 Kickoff R1

Status: ratified. Produced by a 2-round unanimous adversarial
mutual-critique between ag.deepthink and cx.deepthink (2026-07-31),
reconciled by cc, unanimous ACK from both peers. Lifts "no package
implementation is authorized" specifically and only for the scope named
below (`ARCHITECTURE.md` §15 Phase 1: pure domain and store kernel). Does
not authorize real peer adapters, provider calls, process execution, IPC
surfaces, legacy cutover, or dual-write operation -- those remain gated
by their own future Phase 2+ decisions. The frozen Phase 0 corpus is
unaffected and remains immutable.

## Process summary

Round 1: ag and cx independently resolved `ARCHITECTURE.md` §16.1's 5
Phase-0-decision blockers and independently recommended a first Phase 1
implementation slice. Part A converged immediately (both reached the
same 4 of 5 answers; cx's health-policy answer was more concrete and
adopted by ag in Round 2). Part B diverged: ag recommended starting with
`core/` + SL (session/lease); cx recommended `core/` + GB (governance
broker), arguing GB more completely covers Phase 1's actual stated
mandate (CAS, idempotency, atomic outbox, transaction-boundary fault
injection) in one already-proven SQLite-backed fixture family with zero
OPEN backlog, while SL carries 5 open generalization items and legacy
`peer_id` vocabulary needing normalization before becoming production
types.

Round 2: ag directly inspected `broker.py` and `session_lease.py`
against both arguments and fully conceded to cx's GB-first position. cx,
asked to steelman ag's "session identity is foundational" intuition,
acknowledged it's real but distinguished "foundational domain
dependency" from "best first implementation seam" -- Phase 1's own
stated scope needs CAS/idempotency/outbox proven first, and GB's fixture
family maps almost exactly onto that mandate with fewer open questions.
cx re-verified GB-01/03/04/05's fixture spec directly and found two minor
qualifications (GB-02 only proves reaction to already-stale CAS
evidence, not normalize-inside-drain ordering; "pending journal"
vocabulary should be renamed to the architecture's canonical
outbox/effect-intent terms) that don't change the recommendation.

## Ratified decisions

### Part A -- the 5 §16.1 blockers

1. **State scope**: one `PeerHubHome` and one SQLite database per
   resolved workspace identity (`workspace_home_id`), not per user.
   Cross-workspace coordination is an explicit outbox-correlated saga,
   never an atomic cross-database transaction. Grounded in
   `AUTHORITY-CUTOVER-CONTRACT.md` §2, `ARCHITECTURE.md` §4/§7.3,
   `UNIFIED-SETTINGS-SURFACE-R1.md` §4.
2. **`UsageProvider` granularity**: aggregate account/quota-pool window
   headroom only (used/remaining fraction, window start/reset,
   freshness) -- never a per-request token/dollar cost estimate in v1.
   Grounded in `ARCHITECTURE.md` §6.4/§11, `QUOTA-PERIOD-SCALING-POLICY-R1.md`,
   AC-09's evidence.
3. **Health policy**: a versioned, injected `HealthPolicy` revision
   (`v1-health-default-r1`), never hardcoded into reducer logic. Concrete
   v1 defaults: readiness freshness 7200s; automatic recovery-probe
   backoff ladder 30/60/120/240/480/900s (capped); deterministic ±20%
   jitter derived from incident ID (reproducible in tests); readiness
   threshold exactly one current, integrity-verified, correctly-scoped
   observation; administrative recovery grants exactly one probe and
   never writes `HEALTHY`/`OPEN` directly; `RATE_LIMITED` ends at
   authoritative `retry_after`/reset time (no invented fallback
   duration) and never opens a health circuit
   (`policy_action: null`); manual/security/policy quarantines require
   their designated authority, never cleared by a liveness receipt.
   Grounded in `HR-01-03-HEALTH-RECOVERY-CLASSIFICATION-SPEC-R1.md`,
   `HR-03-POLICY-ACTION-EXTENSION-R1.md`, `health.py`'s HR-04..06
   reducer evidence.
4. **Budget authority**: none in v1. No `BudgetReservation` state
   machine, budget package, reservation table, or budget reducer. Ships
   in explicit `NO_BUDGET` routing mode. Grounded in `ARCHITECTURE.md`
   §4/§11/§16.1, AC-09.
5. **`DELIVERED_UNVERIFIED` wording**: primary label `"Delivered"`,
   secondary text "Delivered -- response received; completion was not
   independently verified," neutral/positive styling, no failure icon,
   CLI exit 0. Machine status stays exactly `DELIVERED_UNVERIFIED`.
   Grounded in `ARCHITECTURE.md` §9.

### Part B -- first Phase 1 implementation slice

**Slice 1: governance-broker (GB) store kernel.** Build the pure
mutation reducers, the `StateStore`/`UnitOfWork` port, and the SQLite
backend against GB-01/03/04/05's already-proven fixture spec (atomic
transition+receipt+outbox with rollback; stale-CAS rejection;
same-payload idempotency hit vs. different-payload conflict rejection;
post-commit recovery without replay; exclusive-claim contention with an
immutable terminal receipt).

Files to create (matching `ARCHITECTURE.md` §2's tree):

```text
peerhub/
  __init__.py                    # __version__ = "0.1.0"
  runtime.py                     # sole production wiring point

  core/
    __init__.py
    context.py                   # RuntimeContext, PathLayout, injected Clock/IdSource
    protocol.py                  # command/event envelopes, ErrorCode, CommandID
    errors.py                    # internal exception hierarchy + protocol-code mapping

  state/
    __init__.py
    contract.py                  # feature-independent StateStore/UnitOfWork ports

  governance/
    __init__.py
    contract.py                  # published mutation/receipt DTOs
    mutations.py                 # pure mutation lifecycle reducers
    broker.py                    # CAS planning, idempotency handling, commit/recovery orchestration

  persistence/
    __init__.py
    sqlite.py                    # SQLite WAL backend implementing state.contract
    migrations/
      __init__.py
      0001_phase1_kernel.sql     # command ledger, mutation request/plan/receipt, outbox tables

tests/
  fakes.py
  contract/test_phase0_gb_compatibility.py   # frozen GB-01..06 positive vectors adapted as compatibility tests
  unit/governance/test_mutations.py
  integration/persistence/test_sqlite_kernel.py
  integration/persistence/test_sqlite_fault_boundaries.py
```

Do not import or reuse the Phase 0 fixture runner's `broker.py` as
production code -- its tables prove behavior, not final schema. Rename
"pending journal" vocabulary to the architecture's canonical
outbox/effect-intent terms in production types (cx's Round 2
qualification).

**Slice 2 (SL session/lease kernel)**, consuming the now-proven
persistence engine; adds `core/execution.py`,
`dispatch/{contract,model,service}.py`, normalized `instance_id`/
`owner_principal_id`/`process_birth_identity` types (replacing SL's
legacy `peer_id` vocabulary), `0002_dispatch_session_lease.sql`.

**Slice 2 authorization addendum (2026-07-31)**: this document originally
named Slice 2 for sequencing only, explicitly not authorized by this
kickoff round. The user directed proceeding to Slice 2 the same day.
Implementation surfaced real defects on first pass (found by
cx.deepthink's review of ag.deepthink's draft: a session-binding CAS
with no revision guard -- a genuine concurrent-overwrite race; an
`ExecutionCertainty` enum violating `PROTOCOL-V1-FREEZE.md`'s
already-frozen vocabulary, the same recurring mistake class as
DP-06/CJ/RT-03/HR-03; integration tests silently bypassing the real
production repository via a test-local duplicate class, which is why
the CAS bug wasn't caught; a fence-check bypass via an empty
`owner_peer_id` default; a backwards evidence-certainty mapping in
lease recovery; and SL-02/RESERVED-state/coordinator-epoch gaps versus
`ARCHITECTURE.md`'s full §7.2/§7.3 state machines). Presented with the
findings, the user explicitly chose full rework with honest scope
narrowing over a partial patch or abandoning the slice. cx.deepthink
reworked all 6 confirmed issues; cc independently re-verified each
directly against the live files; ag.deepthink gave a clean final ACK
after its own independent line-by-line check. 39/39 tests pass. This
retroactively constitutes Slice 2's authorization, at the human-decision
altitude the original scope note reserved it for -- not a peer-only ACK
substituting for that decision.

Full subsequent order (unchanged from `ARCHITECTURE.md` §15, sequencing
only, each slice its own future authorization checkpoint): GB -> SL ->
DP (request/attempt) + CJ (command idempotency) -> HR (health/admission)
+ RT (routing) -> CS (consensus) -> proposal/T89 dedup -> remaining
repository/concurrency/fault-boundary coverage. CR (coordination) stays
Phase 3.5; AC (authority-cutover) stays its own future cutover work; no
adapter/pipe/PTY/provider/IPC/budget/legacy-writer work belongs in
Phase 1.

### Implementation order for Slice 1

1. Write failing GB-01..06 compatibility + reducer tests first.
2. Implement the pure mutation reducers.
3. Define the feature-independent `state.contract` port.
4. Implement the migration and SQLite repositories.
5. Fault-inject immediately before/after every commit.
6. Add broker idempotency/recovery orchestration.
7. Wire through `runtime.py`.

Slice 1 is complete only when stale-CAS and idempotency-conflict attempts
produce zero mutation to target, receipt, effect-intent, and outbox
together, while successful transitions commit all of them atomically.
