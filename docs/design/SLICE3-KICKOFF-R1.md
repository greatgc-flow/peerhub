# Slice 3 Kickoff R1 — Request/Attempt Reducers + Command Idempotency (DP+CJ)

**CLOSED 2026-07-31** (commit `1b55826`). ag's first implementation attempt
was rejected in full after cx's independent review found it deleted ~800
lines of shipped Slice 1/2 persistence code behind an "omitted for
brevity" placeholder and invented four non-frozen vocabulary enums; cc
reverted it fully before rework began. cx reimplemented from scratch
across 4 batches (to avoid the same truncation failure mode), cc verifying
syntax/vocabulary/truncation after each batch and fixing one small
test-fixture ID-source collision directly (not a production defect).
ag then gave an independent from-scratch final review, actually executing
`pytest` itself (not just reading code) and returning **CLEAN ACK**, 75/75
passing, matching the same closure step Slice 1 and Slice 2 each received.

Status: ratified. Produced by a 2-round adversarial mutual-critique between
ag.deepthink and cx.deepthink (2026-07-31), reconciled by cc with direct
independent verification of every load-bearing citation against the live
`docs/design/phase0/PROTOCOL-V1-FREEZE.md` and `peerhub/dispatch/contract.py`
files (not accepted from either peer at face value). Continues the same
Slice 1 -> Slice 2 -> **Slice 3** sequencing named in
`PHASE1-KICKOFF-R1.md`'s "Full subsequent order", authorized by the user's
explicit "Phase 1로 복귀" direction on 2026-07-31 following the ag.deepthink
model-pin incident fix.

## Process summary

Round 1: ag and cx independently proposed a Slice 3 design from the same
brief (ARCHITECTURE.md §7.1/§5/§609, `CJ-02-05-V1-FIXTURE-SPEC-R1.md`,
`DP-DT-*` Phase 0 fixture specs, and the already-shipped Slice 2 code).
Both flagged the same tension around `command_id` generation but diverged
on resolution: ag left it as an open question; cx traced it to
`PROTOCOL-V1-FREEZE.md` §9 ("Required correction set (CX adversarial
review)"), which explicitly states it overrides earlier conflicting draft
wording including `ARCHITECTURE.md`:254's stale "caller-generated
idempotency key" text.

cc independently read `PROTOCOL-V1-FREEZE.md` §9 directly (not trusting
either peer's citation) and confirmed cx's citations accurate.

Round 2: ag was given cc's independent confirmation and cx's specific
citations, and instructed to verify directly rather than defer. ag read
`PROTOCOL-V1-FREEZE.md` §9 itself and conceded on all four contested
points (command_id minting, reducer granularity, LeaseFenceTuple/RESERVED
extension, canonical outbox generalization) with its own independent
file:line evidence, matching cx's conclusions.

## Ratified decisions

1. **Command ID ownership** (`PROTOCOL-V1-FREEZE.md` §9 item 1, overrides
   `ARCHITECTURE.md`:254): submissions carry caller-minted
   `client_request_id` + `correlation_id`, never `command_id`. The server
   atomically mints `command_id` only after authorization/admission.
   Pre-admission errors have a null command ID and a server diagnostic ID.
   Reusing `(client_id, client_request_id)` with changed intent is
   `DUPLICATE_ID_CONTENT_MISMATCH`.

2. **Reducer set** (`peerhub/dispatch/model.py`, pure, matching
   `ARCHITECTURE.md` §7.1's frozen state names 1:1 — cx's granular list,
   adopted over ag's coarser Round 1 list):
   `validate_submission`, `admit_request`, `reject_request_policy`,
   `prepare_request`, `create_attempt`, `fail_pre_dispatch`,
   `record_dispatch_intent`, `record_start_uncertain`, `record_running`,
   `begin_cancellation`, `begin_assessment`, `complete_attempt`,
   `authorize_retry`. `SessionBindingSnapshot`/`LeaseSnapshot` (Slice 2)
   are observed CAS inputs only — reducers never look them up or mutate
   them; the service/repository atomically applies the returned revision.

3. **Idempotency schema** (`PROTOCOL-V1-FREEZE.md` §9 items 1/2/11,
   migration `0003`): two independently enforced identities —
   `(client_id, client_request_id)` binds one submitted intent;
   `(client_id, command_type, idempotency_key)` binds the canonical
   payload digest, server `command_id`, and admission receipt. Same
   key/digest returns the existing state; changed digest is
   `IDEMPOTENCY_PAYLOAD_MISMATCH`. `(command_id, attempt_number)` is
   additionally unique with a partial unique index enforcing at most one
   nonterminal attempt per command.

4. **Slice 2 lease extension** (`PROTOCOL-V1-FREEZE.md` §9 item 13):
   `LeaseFenceTuple` (`peerhub/dispatch/contract.py`) gains `command_id`,
   `attempt_id`, `authority_epoch`. `owner_process_birth_identity` becomes
   optional to support a pre-spawn `RESERVED` lease state (admission
   reserves the lease; `DISPATCH_INTENT` binds it to the attempt;
   `RUNNING` supplies and requires process-birth identity).

5. **Canonical outbox** (`PROTOCOL-V1-FREEZE.md` §9 items 5/12): Slice 1's
   `outbox_events` table lacks the frozen `outbox_position`
   monotonic-workspace semantics and carries governance-specific FKs.
   Migration `0003` generalizes/rebuilds it into one canonical
   protocol-wide outbox reused by both governance and dispatch, not a
   second dispatch-only outbox table. Governance receipts retain their
   link via `outbox_event_id`.

6. **Scope narrowing** (unanimous, matching Slice 1/2's own honest-scope
   discipline): DP-04 output-cap enforcement, DP-05 live deadlines, real
   process creation/PTY transport, and actual cancellation remain Phase 2
   — Slice 3 only reduces *injected* observations into the frozen outcome
   states. No budget reservation/`NO_BUDGET` logic (already excluded,
   re-confirmed). The active DP-06 orphaned-intent recovery *sweep* is
   deferred to the later Fault Boundary/Health slice; Slice 3 only builds
   the capability to represent `START_UNCERTAIN`/`MAY_HAVE_STARTED`.

## Addendum (2026-07-31): `attempt_id` nullability on the lease CAS tuple

cx.deepthink, implementing directly from this document, correctly stopped
before writing any code and flagged a genuine internal contradiction this
document did not resolve: decision 4 ratifies a pre-spawn `RESERVED` lease
created at admission, before any attempt exists (the reducer order is
`admit_request -> prepare_request -> create_attempt -> record_dispatch_intent`),
yet `PROTOCOL-V1-FREEZE.md` §9 item 13 requires `attempt_id` in the lease
CAS tuple. A `RESERVED` lease cannot carry a mandatory `attempt_id` before
`create_attempt` has run. This document did not authorize an implementer
to resolve that silently, and cx did not -- it stopped and asked, per its
brief.

**Resolved, mirroring the already-ratified treatment of
`owner_process_birth_identity`:** `attempt_id` on the lease/`LeaseFenceTuple`
is `str | None`, absent while `RESERVED`, and becomes mandatory (checked at
the reducer and CAS layers) from `DISPATCH_INTENT` onward -- the same
optional-until-bound shape already ratified for process identity, applied
consistently to the other field with the identical RESERVED-time gap. This
does not reopen the ratified reducer/lifecycle ordering (decision 2); it
only fixes the DTO/CAS shape to match that ordering. Re-dispatched to
cx.deepthink for implementation with this resolution given explicitly.

## Addendum 2 (2026-07-31): outbox migration-0003 compatibility policy

On retry, cx.deepthink again stopped correctly rather than guessing:
decision 5's "generalize/rebuild" instruction did not specify how existing
Slice 1 `outbox_events`/`effect_receipts` rows and foreign keys survive
the migration, nor how `PROTOCOL-V1-FREEZE.md` §9's required
`outbox_position` consumer checkpointing is persisted (no checkpoint
table existed).

**Resolved, accepting cx's own proposed schema (cc judgment call, not a
full second peer-debate round, to keep momentum -- correctness is proven
by the full Slice 1/2/3 regression suite after implementation, not by
further debate):**
- Rebuild `outbox_events` in place: add `outbox_position INTEGER PRIMARY
  KEY AUTOINCREMENT`, `event_id TEXT NOT NULL UNIQUE`, protocol/schema/
  correlation/event fields, and make `request_id`/`transition_receipt_id`
  nullable (governance rows keep them populated; dispatch rows leave them
  null).
- Rebuild `effect_receipts` in the same migration so its FK targets the
  rebuilt table; preserve existing governance rows in deterministic
  `(created_at, event_id)` order.
- Add `outbox_checkpoints(consumer_id PRIMARY KEY, outbox_position,
  event_id, revision)` with revision-guarded CAS, satisfying §9's
  checkpoint requirement (previously unspecified).
- One physical outbox table serves both governance and dispatch;
  governance recovery filters on non-null `transition_receipt_id`.

Re-dispatched to cx.deepthink a second time with this resolution given
explicitly. If implementation surfaces the full Slice 1 regression suite
failing against this schema, that is the actual falsification test for
this addendum, and it must be treated as such (fix the schema, not the
tests).

## File list

Create:
- `peerhub/persistence/migrations/0003_command_request_attempt.sql`
- `tests/contract/test_phase0_dp_cj_compatibility.py`
- `tests/unit/dispatch/test_request_attempt_model.py`
- `tests/integration/persistence/test_command_idempotency_kernel.py`
- `tests/integration/persistence/test_request_attempt_kernel.py`
- `tests/integration/persistence/test_request_attempt_fault_boundaries.py`

Extend: `peerhub/core/protocol.py`, `peerhub/core/errors.py`,
`peerhub/dispatch/contract.py`, `peerhub/dispatch/model.py`,
`peerhub/dispatch/service.py`, `peerhub/persistence/sqlite.py`,
`peerhub/governance/contract.py`/`broker.py` (outbox consumption only),
`peerhub/runtime.py`, and any existing Slice 1/2 tests affected by the
shared-outbox migration.

The Phase 0 fixture-runner modules (`command_authz.py`, `dispatch_pipe.py`,
`dispatch_pipe_recovery.py`) remain compatibility evidence only —
production must not copy their tables or reducer implementations
(same rule as GB/SL).

## Implementation order (TDD, 7 steps)

1. Write failing compatibility tests porting CJ-02..05 and DP-01..06 as
   pytest vectors, plus contract tests for caller/server ID ownership and
   digest projection.
2. Correct protocol DTOs: `CommandEnvelope`/submission types, request/
   attempt snapshots, completion contract, errors, shared frozen state
   enum.
3. Implement pure reducers covering every legal transition, illegal edge,
   lease/session mismatch, start-uncertainty, retry admissibility, and
   terminal assessment.
4. Add migration `0003`: command/request/attempt tables, extended leases,
   generalized outbox with `outbox_position`.
5. Implement repository kernels: command/client-request/idempotency
   lookups, request/attempt CAS, full fence completion CAS, monotonic
   attempt allocation, one-active-attempt enforcement.
6. Implement transactional service orchestration; prove admission/
   idempotency/request/reservation/outbox atomicity, same-digest hits,
   changed-digest rejection, CAS losers, unauthorized zero-write paths.
7. Fault-inject every insert/update boundary; run concurrent duplicate/CAS
   tests, compatibility tests, migration/reopen tests, and the full
   Slice 1/2 regression suite. Completion requires rollback cleanliness,
   byte-stable canonical payloads, no external I/O inside transactions,
   and zero Phase 2 process behavior.
