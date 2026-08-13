# T1 Increment 5B: Atomic Retry/Failover Authorization Plan

Status: **RATIFIED (1 round; `ag.deepthink` cross-review; no revisions
required).**

Date: 2026-08-13

Planning baseline: `59929ee` (Increment 5A commits `a5556a2`, `24102f8`,
and `a6118a9` are present.)

Implementation status: **PLANNING ONLY. No 5B source or tests are implemented
by this document.**

Ratified parent contract:
`PHASE3-T1-INCREMENT5-RETRY-LOOP-DESIGN-R1-2026-08-13.md`, especially
Sections 6, 7, 9.1, 9.2, and 9.3.

---

## 1. Outcome and boundaries

Increment 5B will add one atomic authorization boundary with one explicit
entry point and two tagged route intents:

1. same-target retry: preserve the current route/target, rotate the session
   lease, and issue capability authority for the next durable attempt; and
2. failover retry: exclude the failed instance, select and persist a new route,
   update the request's route/target binding, rotate the session lease, and
   issue capability authority for the next durable attempt.

Both intents use the same private transaction kernel. A tagged union, rather
than loosely related optional arguments or two public methods, makes invalid
combinations unrepresentable at the call site. Unit 5B-2 can still land and
verify same-target authorization independently by typing the method to accept
only `SameTargetRoute`; Unit 5B-3 then expands that type to the full union and
adds the exhaustively checked failover branch. The common kernel prevents the
two paths from drifting on revision, attempt-bound, capability, or write-order
rules.

5B does not implement `dispatch_with_retries()`, DEFER scheduling/resume,
adapter/profile materialization, or process execution. Those remain 5C. It
also does not change `adjudicate_retry()`; the scenario below treats
`RETRY_SAME_TARGET` and `FAILOVER` as upstream decisions presented to 5B.

### 1.1 Source verification at the planning baseline

The supplied ground truth remains accurate at `59929ee`:

- `ApplicationWorkflows.authorize_retry()` still performs
  `_require_bound_route()` and `RoutingService.validate_route_for_dispatch()`
  before calling `DispatchService.authorize_retry()`.
- `DispatchService.authorize_retry()` still delegates directly to
  `AttemptLifecycleCoordinator.authorize_retry()`.
- `AttemptLifecycleCoordinator.authorize_retry()` still owns one real
  `store.unit_of_work()`, reserves a fresh lease, calls the pure
  `dispatch.model.authorize_retry()` reducer, inserts the lease, CAS-updates the
  request and optionally the previous attempt, and commits.
- That transaction does not read or issue capability authority.
- `require_dispatch_capability()` still loads the capability lease by command
  and `validate_capability_binding()` still requires capability, request,
  admission receipt, and session lease to carry the same lease ID.
- Migration `0018_capability_leases.sql` still enforces one capability row per
  command, one per admission receipt, and one per session lease. Consequently,
  a second immutable capability row cannot be inserted under the current
  schema.
- The current retry route path only validates the existing route decision. It
  has no failed-instance exclusion, replacement selection, or atomic route
  persistence/rebinding operation.

The schema and admission-replay observations are important: closing seam 9.1
requires more than adding `unit.add_capability_lease()` to the existing
transaction.

---

## 2. Design decisions fixed by the pre-implementation simulation

### 2.1 Capability authority is versioned by authorized attempt number

The immutable `AdmissionReceipt` remains the root provenance record and is
never updated. A retry capability continues to reference its original
`admission_receipt_id`, but capability authority becomes one row per authorized
attempt rather than one row per command.

`CapabilityLease` gains:

```python
authorized_attempt_number: int
previous_attempt_id: str | None
```

The invariant is:

```text
attempt 1 capability:
  authorized_attempt_number == 1
  previous_attempt_id is None
  capability.session_lease_id == admission_receipt.lease_id

retry capability N (N > 1):
  previous_attempt_id identifies durable attempt N - 1
  previous_attempt.command_id == capability.command_id
  previous_attempt.attempt_number + 1 == authorized_attempt_number
  capability.session_lease_id == request.lease_id == current session lease ID
  admission_receipt.lease_id remains the immutable attempt-1 lease ID
```

Versioning follows ratified capability-lease precedent rather than inventing a
new 5B invariant. `CAPABILITY-LEASE-DESIGN-2026-08-08-ERRATA.md` Sections
7.1-7.2 establish that the durable lease has no caller-writable fields after
issuance and that a new authority decision requires new command/admission
identity rather than silent renewal. A retry is a new authority decision: it
runs `CapabilityPolicy.decide()` again and may select a different instance,
profile, or enforcement floor. Updating the prior capability row in place
would therefore be exactly the in-place renewal that the errata forbids. 5B
keeps the original command/admission receipt as immutable provenance while
giving the new decision a fresh capability identity, session lease, and
attempt scope.

Versioning also preserves the current fail-closed default. Seam 9.1's measured
failure occurs because stale authority retains its old lease binding and no
longer matches the request. An UPDATE-in-place design would reverse that
property and create a TOCTOU race: a runner could hold a
`ValidatedCapabilityLease` for attempt 1/T1 after the read-only
`require_dispatch_capability()` check while a concurrent failover
authorization rewrites the same durable row to L2/T2. Dispatch-intent
revalidation would then reread the same row ID, and the opaque token carries no
lease or target with which to detect that substitution. Under versioning, the
token continues to resolve the attempt-1 row and the existing authorized-
attempt-number equality check rejects it.

Finally, `add_capability_lease()` is currently the capability table's only
mutation port and is insert-only. Versioning needs one bounded schema migration
but preserves that surface. UPDATE-in-place would add the first permanent
UPDATE path to a security-authority table.

The resulting chain prevents retry authorization from degrading to "any new
lease for this command." Previous capability rows remain unchanged and become
unusable for dispatch as soon as `RequestSnapshot.lease_id` advances.
`previous_attempt_id` remains explicit even though
`UNIQUE(command_id, authorized_attempt_number)` makes it derivable: it keeps
each row self-describing, lets `validate_capability_binding()` operate from a
single record plus its referenced attempt, provides database-level referential
integrity, and cross-checks two independently written fields.

The actual `AttemptSnapshot` for attempt N is still created by the existing
pre-spawn flow after `require_dispatch_capability()` succeeds. The capability
pre-authorizes exactly that next number. `create_attempt()` will verify inside
its own transaction that the capability selected by the request's current
lease authorizes `next_attempt_number(command_id)`. After the attempt exists,
dispatch-intent revalidation verifies that the capability's authorized number
equals that attempt's number.

This preserves the current security ordering:

```text
require capability -> plan invocation -> create attempt -> record intent -> spawn
```

It does not pre-create an attempt in 5B or require 5B to change 5C's
at-most-once dispatch orchestration.

### 2.2 Active capability lookup follows the request lease

The ambiguous `get_capability_lease_by_command_id()` and
`get_capability_lease_by_admission_receipt_id()` operations cannot survive a
one-to-many authority history. They are replaced at mutation/gate call sites
by:

```python
get_capability_lease(capability_lease_id)
get_capability_lease_by_session_lease_id(session_lease_id)
get_capability_lease_for_attempt(command_id, authorized_attempt_number)
```

`require_dispatch_capability()` loads the exact caller-supplied capability ID,
then proves it is the active row by matching its session lease to the current
request. Dispatch-intent revalidation loads the exact ID carried by
`ValidatedCapabilityLease`; it never selects an arbitrary capability by
command.

Admission/idempotency replay loads the current request, its current session
lease, and the capability found by that session lease. It returns the existing
active authority; it does not mint a replacement. For attempt 1, the old
receipt-lease equality remains mandatory. For a retry attempt, the durable
previous-attempt chain replaces only that one equality. All command, subject,
target, route digest, tier, and policy-revision equalities remain mandatory.

### 2.3 The frozen policy must already exist

5B adds persistence and an idempotent freeze operation for the existing
`RetryPolicyRecord(command_id, max_attempts)` shape. The outer loop in 5C will
call the freeze operation before executing attempt 1.

The authorization transaction never creates a missing policy opportunistically.
It must read an existing record and prove that the supplied `frozen_max_attempts`
equals the durable value. This prevents a retry caller from widening the budget
while authorizing the next attempt.

### 2.4 Routing selection is pure inside the authorization transaction

The 5B transaction must not call `RoutingService.select_route()` or
`RoutingService.validate_route_for_dispatch()`, because both open and own a
separate unit of work. 5B instead reuses the pure functions in
`routing.model` and the route repository methods already implemented by
`SqliteUnitOfWork`.

Health projection/freezing and construction of a `RouteRequest` remain
preparatory application work. The resulting immutable admission snapshot and
route request are reloaded/validated against durable records inside the one
authorization transaction before any authority record is written.

### 2.5 One public method accepts a tagged-union route intent

Planned public service surface:

```python
@dataclass(frozen=True)
class SameTargetRoute:
    route_decision_id: str
    current_route_request: RouteRequest

@dataclass(frozen=True)
class FailoverRoute:
    failed_route_decision_id: str
    failover_route_request: RouteRequest  # must carry FAILED_TARGET_EXCLUDED_BY_RETRY marks

RetryRouteIntent = SameTargetRoute | FailoverRoute

DispatchService.authorize_retry(
    command_id,
    previous_attempt_id,
    *,
    route_intent: RetryRouteIntent,
    expected_request_revision,
    expected_previous_attempt_revision,
    expected_highest_attempt_number,
    frozen_max_attempts,
    current_policy_revision,
    reconciliation_complete,
    heartbeat_timeout_ms,
) -> RetryAuthorizationBundle
```

Planned result:

```python
@dataclass(frozen=True)
class RetryAuthorizationBundle:
    request: RequestSnapshot
    previous_attempt: AttemptSnapshot
    session_lease: LeaseSnapshot
    capability_lease: CapabilityLease
    route_decision: RouteDecision
```

The capability carries `authorized_attempt_number`, so the bundle does not
duplicate it. For same-target retry, `route_decision` is the existing immutable
decision. For failover, it is the replacement decision inserted by this same
transaction.

The application layer exposes one matching
`ApplicationWorkflows.authorize_retry()` method taking the same tagged route
intent. It projects telemetry, freezes current health, composes the appropriate
current or failover route input, and calls the atomic service method. It does
not call the public routing service during authorization.

---

## 3. Typed failure contract for 5C

5B raises only typed, distinguishable expected boundary failures. It does not
translate them into retry-loop aggregate outcomes; 5C will do that.

| Condition | Type | Stable code/meaning | 5C treatment later |
|---|---|---|---|
| Request revision changed | existing `StaleRevisionError` | `REVISION_CONFLICT` | Reload and discard stale decision. |
| Previous-attempt revision changed | existing `StaleRevisionError` | `REVISION_CONFLICT` | Reload and discard stale decision. |
| Highest durable attempt number changed | existing `StaleRevisionError`, target ID `<command>:attempt-history` | `REVISION_CONFLICT` | Reload; normally classify concurrent attempt/authorization. |
| `highest + 1 > frozen max` | new `AttemptLimitReachedError` | new `ATTEMPT_LIMIT_REACHED` protocol code | Return normal `ATTEMPT_LIMIT_REACHED` aggregate. |
| Supplied max differs from frozen record | new `RetryPolicyConflictError` | `INVALID_PARAMS`; includes supplied and durable values | Reject caller/config drift; never widen the bound. |
| Policy record absent | existing `RecordNotFoundError("retry_policy", command_id)` | `RECORD_NOT_FOUND` | Programming/orchestration failure; 5C must freeze before attempt 1. |
| Current capability policy revision differs from request's frozen revision | existing `PolicyStaleError` | `POLICY_STALE` | No authority minted; caller must obtain a separately authorized policy path. |
| Current target/route is unavailable for same-target retry | new `RetryRouteUnavailableError` | carries `CONFIGURATION_STALE`, `PEER_UNAVAILABLE`, or `PROFILE_UNAVAILABLE` | Re-adjudicate/consider failover; never silently branch inside same-target authorization. |
| Failover has no eligible replacement | new `RouteExhaustedError` | `ROUTE_EXHAUSTED` | Return normal route-exhausted loop result. |
| Capability policy or mandatory enforcement rejects the selected target/tier | new `CapabilityAuthorizationDeniedError` | genuine authorization denial, separate from binding corruption | Stop/return authorization denial; do not classify as vendor/process failure. |
| Durable capability/request/receipt/lease chain is corrupt | existing `CapabilityLeaseViolation` | invariant/security failure | Propagate; do not normalize as ordinary denial. |
| Missing request, attempt, route, lease, receipt, or admission snapshot | existing `RecordNotFoundError` (or existing fatal consistency error for idempotency corruption) | `RECORD_NOT_FOUND` | Propagate as storage/invariant failure. |
| Caller asks to retry `CANCELLED`, `DELIVERED_UNVERIFIED`, verified-success, or another non-authorizable state | existing `InvalidStateTransitionError`/`InvalidMutationError` | caller or stale-policy misuse | 5C should normally prevent this; never authorize. |

`CapabilityAuthorizationDeniedError` is intentionally distinct from
`CapabilityLeaseViolation`. The former means a fresh decision was evaluated
and denied before mutation. The latter means supposedly durable authority does
not satisfy its immutable binding and is not safe to downgrade to a normal
route or retry outcome.

---

## 4. Preconditions common to both route-intent branches

Every item below is checked inside the single write `unit_of_work()` and before
`allocate_fencing_token()`, repository insertion, or CAS update.

1. Load the current `RequestSnapshot`; fail if absent.
2. Load `previous_attempt_id`; require the same `command_id`.
3. Compare `request.revision` with `expected_request_revision`. On mismatch,
   raise `StaleRevisionError` immediately.
4. Compare `previous_attempt.revision` with
   `expected_previous_attempt_revision`. On mismatch, raise
   `StaleRevisionError` immediately.
5. Load all attempts in monotonic order. Require nonempty history, exact
   numbers `1..N`, no duplicate/gap, and every attempt bound to the command.
   Gaps/duplicates are invariant failures.
6. Require `N == expected_highest_attempt_number` and require the supplied
   previous attempt to be attempt N. A changed N raises `StaleRevisionError`;
   a caller naming a non-highest attempt is an invalid mutation.
7. Load the durable retry-policy row. Require
   `stored.max_attempts == frozen_max_attempts`; reject missing or conflicting
   values with the typed failures in Section 3.
8. Check `N + 1 <= stored.max_attempts`. If false, raise
   `AttemptLimitReachedError` before allocating a fence, lease ID, capability
   ID, or route-decision ID.
9. Require the current request/previous-attempt state to remain retry
   authorizable. In particular, `CANCELLED`, `DELIVERED_UNVERIFIED`, and
   `SUCCEEDED_VERIFIED` are not accepted even though the legacy reducer's
   current state set is broader.
10. Reapply the reducer's replay-safety condition from durable evidence:
    `NOT_STARTED`, `reconciliation_complete`, or the frozen completion
    contract's `replay_safe`. If reconciliation would update the previous
    attempt, that prospective update is built in memory only.
11. Load the original admission receipt and the current session lease. Validate
    the existing active capability chain before replacing it; corrupted prior
    authority is not silently healed by issuing another capability.
12. Prove the current request is bound to the currently-bound route decision
    supplied by the route intent (`route_decision_id` for `SameTargetRoute` or
    `failed_route_decision_id` for `FailoverRoute`) using client request ID,
    configuration revision, required tier, selected instance/profile, and
    `canonical_route_decision_digest()`.
13. Apply the action-specific route checks in Sections 6 and 7.
14. Require `current_policy_revision == request.policy_revision`, resolve
    machine-owned enforcement evidence for the prospective target/profile,
    apply the mandatory floor, and obtain a fresh capability grant decision.
    A denial occurs before the first repository write.

Steps 11, 12, and 14 are one read pass with many checks, not three reload
passes. The kernel loads the request's authority, route, and policy context
once and reuses those exact records throughout precondition validation.

Only after all fourteen checks pass may the kernel allocate IDs/fencing state,
construct the prospective records, write, and commit.

SQLite's `BEGIN IMMEDIATE` serializes writers. The explicit expected-value
checks remain necessary for other stores, for semantic clarity after a DEFER
window, and so the second SQLite caller receives a typed stale result after it
acquires the lock rather than executing on the new state.

---

## 5. Unit-by-unit implementation plan

### Unit 5B-1 — Durable retry authority and typed fences

This unit changes the data/authority model but does not yet authorize a retry.
It is independently verifiable through model, migration, repository, and gate
tests.

#### Exact source scope

Change:

- `peerhub/core/protocol.py`
  - add `ErrorCode.ATTEMPT_LIMIT_REACHED` and its ordinary exit-code mapping;
- `peerhub/core/errors.py`
  - add `AttemptLimitReachedError`, `RetryPolicyConflictError`,
    `RetryRouteUnavailableError`, `RouteExhaustedError`, and
    `CapabilityAuthorizationDeniedError`;
- `peerhub/dispatch/capability.py`
  - extend `CapabilityLease` with `authorized_attempt_number` and
    `previous_attempt_id`;
  - split fresh target authorization from durable lease construction so policy
    denial can be decided before any repository write;
  - generalize `validate_capability_binding()` to validate attempt-1 receipt
    equality or the retry previous-attempt chain without relaxing other
    equalities;
  - add `authorized_attempt_number` to `ValidatedCapabilityLease`;
- `peerhub/dispatch/admission.py`
  - issue the initial capability with attempt number 1 and no previous attempt;
  - make `_load_admission()` resolve the active capability via the current
    request lease and validate the initial/retry branch correctly;
- `peerhub/dispatch/unit_of_work.py`
  - replace ambiguous capability lookups with the three lookups in Section
    2.2;
  - add `add_retry_policy()` and `get_retry_policy_max_attempts()` persistence
    ports;
- `peerhub/dispatch/attempt_lifecycle.py`
  - update `create_attempt()` and dispatch-intent revalidation to load the
    exact active capability and prove its authorized attempt number;
- `peerhub/dispatch/service.py`
  - update `require_dispatch_capability()` to load the supplied capability ID,
    its previous attempt when required, and the current attempt history;
  - add `freeze_retry_policy(command_id, max_attempts)` as an idempotent
    transaction: insert if absent, return if equal, raise
    `RetryPolicyConflictError` if different;
- `peerhub/persistence/migrations/0022_retry_authority.sql`
  - add `retry_policies(command_id PRIMARY KEY, max_attempts CHECK >= 1)`;
  - rebuild `capability_leases` without the `UNIQUE(command_id)` and
    `UNIQUE(admission_receipt_id)` constraints;
  - retain `UNIQUE(session_lease_id)`;
  - add `authorized_attempt_number INTEGER NOT NULL CHECK >= 1` and nullable
    `previous_attempt_id REFERENCES dispatch_attempts(attempt_id)`;
  - add a shape check requiring `(1, NULL)` for initial capability and
    `(>1, non-NULL)` for retry capability;
  - add `UNIQUE(command_id, authorized_attempt_number)`;
  - backfill all existing capability rows as attempt 1/no previous attempt;
- `peerhub/persistence/sqlite_dispatch.py` and `peerhub/persistence/sqlite.py`
  - implement the new capability and retry-policy ports;
- `tests/integration/persistence/test_migration_runner_sequence.py`
  - advance the packaged migration expectation from 21 to 22;
- focused model/repository tests in
  `tests/unit/dispatch/test_capability.py`,
  `tests/integration/persistence/test_capability_lease_persistence.py`, and a
  new `tests/integration/persistence/test_retry_authority_migration.py`.

Keep untouched in this unit:

- `peerhub/application/workflows.py` and all 5C DTO/orchestration;
- `peerhub/routing/service.py`, route selection behavior, and route schema;
- adapters, process supervision, materialization, session remediation, and
  transport code;
- the frozen Alembic v19 consolidated baseline. Migration 0022 remains on the
  active bespoke ladder, like migrations 0020/0021.

#### Independent verification target

Planned tests prove:

1. a v21 database migrates to v22 and preserves the existing capability as
   attempt-1 authority;
2. two capabilities may share command/admission provenance only when their
   authorized attempt numbers and session leases differ;
3. duplicate `(command_id, authorized_attempt_number)` and duplicate session
   lease IDs fail;
4. attempt 1 still requires capability/request/receipt/session lease equality;
5. retry capability requires the exact previous attempt and number N + 1;
6. changing subject, target, profile, route digest, tier, or policy revision is
   still rejected;
7. `freeze_retry_policy()` is idempotent for the same value and rejects a
   different value;
8. `create_attempt()` cannot create an attempt not authorized by the current
   request lease's capability; and
9. idempotent admission replay returns existing active authority and never
   mints a capability.

Existing databases that already contain the pre-5B partial state
`request.lease_id != admission_receipt.lease_id` remain fail-closed. Migration
0022 must not invent a capability grant for an empirically unbound lease. A
test records that such a legacy row is detected as `CapabilityLeaseViolation`,
not auto-repaired.

### Unit 5B-2 — Same-target atomic retry authorization

This unit closes seam 9.1 completely. It introduces the shared authorization
kernel in same-target-only form and is independently shippable/testable without
failover.

#### Exact source scope

Change/add:

- new `peerhub/dispatch/retry_authorization.py`
  - define `RetryAuthorizationBundle`;
  - define `SameTargetRoute`;
  - define `RetryAuthorizationUnitOfWork`, composing the dispatch and routing
    persistence operations required by one transaction;
  - add `RetryAuthorizationCoordinator.authorize_retry()` typed to accept
    `SameTargetRoute` only in this unit;
  - add the private common precondition loader/checker and common write kernel;
- `peerhub/dispatch/model.py`
  - tighten `authorize_retry()` so `CANCELLED`, `DELIVERED_UNVERIFIED`, and
    verified success cannot be authorized;
  - accept a validated route binding and update lease/state plus route fields
    in one request revision (for same target, those route fields are exactly
    unchanged);
- `peerhub/dispatch/unit_of_work.py`
  - expose route-decision/admission-snapshot reads needed by the composed 5B
    unit-of-work protocol;
  - add retry fault points after route (used by Unit 5B-3), lease, capability,
    request CAS, and previous-attempt CAS writes;
- `peerhub/dispatch/service.py`
  - construct the retry coordinator with the existing clock, ID source,
    capability policy, enforcement evidence, and fault injector;
  - replace the unsafe legacy implementation with atomic `authorize_retry()`,
    typed in this unit with `route_intent: SameTargetRoute` and the exact
    expected-value parameters in Section 2.5;
- `peerhub/application/workflows.py`
  - replace the old non-capability-aware `authorize_retry()` implementation
    with the atomic method taking `route_intent: SameTargetRoute` in this
    unit;
  - keep telemetry projection/health freezing/route-request composition outside
    the authority transaction;
  - stop calling `_require_bound_route()` and public
    `validate_route_for_dispatch()` for retry authorization; the transaction
    rechecks the binding itself;
  - return the capability lease in the retry authorization result so 5C can
    build the next `AttemptDispatchPlan`;
- planned unit/integration updates in
  `tests/unit/dispatch/test_request_attempt_model.py`,
  `tests/unit/dispatch/test_capability.py`,
  `tests/integration/persistence/test_request_attempt_kernel.py`,
  `tests/integration/persistence/test_missing_fault_boundaries.py`, and
  `tests/integration/application/test_workflows_kernel.py`;
- a new focused
  `tests/integration/dispatch/test_retry_authorization.py` for real SQLite
  atomicity and the two-caller race.

Keep untouched in this unit:

- failover candidate exclusion and replacement route persistence;
- `peerhub/routing/service.py` and its independent admission/pre-dispatch API;
- `application/retry.py` adjudication policy;
- `dispatch_with_retries()` and DEFER/resume handling;
- adapter, `SessionHint`, process, and materializer behavior.

#### Same-target transaction boundary and exact order

Within one
`authorize_retry(..., route_intent: SameTargetRoute, ...)` call and one
`with store.unit_of_work() as unit:`:

1. Execute all common reads and preconditions in Section 4.
2. Load the existing `RouteDecision` and selected candidate. Recompute its
   canonical digest and prove it matches all request route/target fields.
3. Validate configuration revision using pure routing validation.
4. Find the same instance/profile in the fresh `current_route_request` and
   require it remains eligible. Same-target authorization never silently
   switches candidates.
5. Obtain a fresh capability grant decision for that same instance/profile and
   the frozen request tier/policy revision. No repository write has occurred.
6. Allocate the database fencing token and fresh `lease_id`. Preserve the
   current hub-internal `session_id` and authenticated owner/fence identity;
   create a fresh RESERVED `LeaseSnapshot` with no attempt/process binding.
7. Call the tightened pure retry reducer with the unchanged route binding. It
   produces a PREPARED request with `lease_id = new_lease.lease_id`, one request
   revision increment, and any allowed reconciliation update to the previous
   attempt.
8. Construct capability C(N + 1) from the already-approved grant:
   original `admission_receipt_id`, new session lease ID, same target/profile,
   same route digest, `authorized_attempt_number=N+1`, and
   `previous_attempt_id=attempt N`.
9. Validate the complete prospective request/receipt/new-lease/previous-attempt/
   capability chain in memory.
10. Insert the new session lease.
11. Insert the new capability lease.
12. CAS-update the previous attempt if reconciliation changed it.
13. CAS-update the request from the exact loaded revision to the prospective
    PREPARED revision.
14. Hit `BEFORE_COMMIT`, commit once, exit the unit of work, then hit
    `AFTER_COMMIT`.

Any exception before step 14 rolls back lease, capability, previous-attempt,
and request changes together. Old admission receipt, old capability, old
session lease, existing route decision, and all completed attempt records are
never mutated. The old capability is inert because it no longer matches the
request's current lease.

The in-transaction bound check occurs before step 6, so a denied retry does not
consume a fencing token or leave an authority row. ID-source gaps after a
fault are permitted; an allocated but unpersisted opaque ID is not authority.

#### Independent verification target

Planned tests prove:

1. all three expected values (request revision, previous-attempt revision,
   highest attempt number) are independently enforced before allocation;
2. missing/conflicting retry policy and attempt-limit exhaustion write nothing;
3. policy/enforcement denial writes nothing and raises
   `CapabilityAuthorizationDeniedError` rather than a binding-corruption error;
4. faults after lease insert, capability insert, attempt CAS, request CAS, and
   before commit leave no partial durable records;
5. the request route/target/digest remain bit-for-bit unchanged;
6. the original admission receipt and capability remain immutable;
7. the new capability's session lease equals the new request lease and
   authorizes exactly N + 1;
8. a subsequent `require_dispatch_capability()` succeeds with the new
   capability and fails with the old one;
9. `create_attempt()` creates exactly N + 1 under that authority; and
10. two real SQLite callers using the same expected values produce one bundle
    and one typed `StaleRevisionError`, with only one new lease/capability row.

### Unit 5B-3 — Failover route selection and atomic rebinding

This unit extends the already-proven common kernel with the failover-specific
route branch and closes seam 9.2. It does not duplicate the common transaction.

#### Exact source scope

Change:

- `peerhub/dispatch/retry_authorization.py`
  - add `FailoverRoute` and expand `RetryRouteIntent` to
    `SameTargetRoute | FailoverRoute`;
  - expand `RetryAuthorizationCoordinator.authorize_retry()` to accept the
    union and add the `match`/`case` branch for `FailoverRoute`; the type
    checker must flag this newly required branch until it is implemented;
  - use pure `routing.model.select_route()` inside the existing transaction;
  - insert the replacement route decision before the lease/capability/request
    writes, under the same commit;
- `peerhub/dispatch/service.py`
  - expand `authorize_retry()`'s route-intent type to `RetryRouteIntent`;
- `peerhub/application/workflows.py`
  - expand `authorize_retry()` to accept `RetryRouteIntent` and prepare the
    `FailoverRoute` variant;
  - build an immutable failover `RouteRequest` by retaining failed-target
    candidates in the audit but marking every candidate whose `instance_id`
    equals the failed selected instance as ineligible with the fixed policy
    reason `FAILED_TARGET_EXCLUDED_BY_RETRY`;
  - pass the failed route decision ID and all expected-value fences to the
    atomic boundary;
- `tests/integration/dispatch/test_retry_authorization.py` and
  `tests/integration/application/test_workflows_kernel.py`
  - add failover happy, exhausted, rollback, and binding tests;
- routing unit tests only if a small pure helper is extracted for the immutable
  candidate-exclusion copy. Existing equal-weight selection behavior itself is
  reused unchanged.

Keep untouched in this unit:

- routing weights/seed policy and `RoutingService.select_route()`;
- external session remediation and cross-adapter session transfer;
- adapter registry/resolution and `AttemptDispatchPlan` construction (5C);
- process execution and DEFER scheduling;
- same-target transaction ordering and error semantics.

#### Failover-specific checks before mutation

After all common preconditions in Section 4:

1. Validate the fresh failover route request's client request ID, required
   capability tier, persisted admission snapshot identity/content, and current
   configuration input.
2. Require every candidate for the failed instance captured from the
   currently-bound route decision to be ineligible with
   `FAILED_TARGET_EXCLUDED_BY_RETRY`; merely removing the selected candidate ID
   or leaving another profile on the same failed instance eligible is invalid.
3. Run pure `select_route()` with a fresh decision ID/timestamp. If it returns
   `ROUTE_EXHAUSTED`, raise `RouteExhaustedError` before any route, fence, lease,
   capability, or request write.
4. Resolve the selected candidate and require its instance differs from the
   failed instance.
5. Compute the replacement decision digest and prospective request route
   binding: new configuration revision, selected instance, selected profile,
   and digest; required tier remains frozen and equal.
6. Obtain a fresh capability grant for the replacement instance/profile before
   the first repository write.

#### Failover write order in the shared transaction

After those checks:

1. Allocate a fencing token and create a fresh RESERVED session lease.
2. Use the pure retry reducer with the replacement route binding to create the
   prospective PREPARED request and allowed previous-attempt update.
3. Create capability C(N + 1) bound to the new lease, replacement target and
   profile, replacement route digest, original immutable admission receipt,
   and previous attempt N.
4. Validate the complete prospective authority chain in memory.
5. Insert the immutable replacement `RouteDecision` and candidate audits.
6. Insert the new session lease.
7. Insert the new capability lease.
8. CAS-update the previous attempt if required.
9. CAS-update the request once, changing lease and route/target fields in that
   same revision.
10. Commit once.

A fault at any point rolls back the replacement route and every authority
mutation. There is no durable route decision that appears selected by 5B while
the request/capability still point at the old target, and no new capability or
lease without the request rebind.

`LeaseSnapshot.session_id` is PeerHub's internal lease/session identity, not an
adapter's external `SessionHint`. 5B preserves the existing internal lease
identity semantics while rotating `lease_id`/fencing authority. For a
cross-adapter failover, 5C must construct the returned attempt plan with
`session=None` (and no `SessionAction.RESUME`) unless a separately authorized
session-remediation design supplies a replacement. 5B never copies an
external session ID across adapters.

#### Independent verification target

Planned tests prove:

1. the failed instance remains visible as explicitly excluded evidence in the
   replacement route audit;
2. the selected replacement instance differs from the failed instance;
3. route exhaustion and capability denial produce zero writes;
4. replacement route decision, request binding, session lease, and capability
   all commit together;
5. target/profile/digest tampering is rejected;
6. fault injection after the route, lease, capability, attempt CAS, request CAS,
   and before commit leaves none of the prospective rows durable;
7. the old route decision, admission receipt, capability, lease, and attempt
   history remain immutable;
8. `require_dispatch_capability()` succeeds for the returned replacement
   target/profile and new capability, and rejects the old target/capability;
9. the next created attempt is N + 1 and uses the new request lease; and
10. the two-caller race still has exactly one winner even when each caller
    independently constructed a replacement route input.

---

## 6. Scenario simulation 1 — Same-target retry, happy path

### 6.1 Starting durable state

Assume:

```text
request Q:
  state = FAILED_PRE_DISPATCH (or another 5A-authorized failure state)
  revision = R7
  lease_id = L1
  selected target/profile = T1/P1
  route digest = digest(D1)

attempt A1:
  attempt_number = 1
  revision = A4
  lease_id = L1
  execution certainty/evidence satisfies SAFE or UNSAFE-with-evidence policy

retry policy:
  command = Q
  max_attempts = 3

admission receipt AR1:
  lease_id = L1 (immutable)

capability C1:
  session_lease_id = L1
  authorized_attempt_number = 1
  previous_attempt_id = None
  target/profile/route = T1/P1/digest(D1)
```

5A adjudication returns `RetryAction.RETRY_SAME_TARGET` from the complete
execution result, durable attempt number 1, frozen max 3, and required safety
evidence.

### 6.2 Application preparation

1. 5C will retain the adjudicated snapshot values `(R7, A4, 1, 3)`.
2. `ApplicationWorkflows.authorize_retry()` projects pending telemetry and
   freezes a fresh health admission snapshot.
3. The route request factory builds the current `RouteRequest` for Q.
4. The application does not select a route and does not mutate dispatch state.
5. It calls `DispatchService.authorize_retry()` with
   `SameTargetRoute(route_decision_id=D1, current_route_request=fresh_request)`,
   current policy revision, and all four frozen values.

### 6.3 Single authorization transaction

1. `RetryAuthorizationCoordinator.authorize_retry()` matches the
   `SameTargetRoute` intent and enters one write unit of work (`BEGIN
   IMMEDIATE` for SQLite).
2. It reads Q, A1, all attempts, retry policy, AR1, L1, C1, D1, and the durable
   admission snapshot referenced by the fresh route request.
3. It checks Q revision R7 and A1 revision A4.
4. It validates history exactly `[A1(number=1)]`, checks expected highest 1,
   and proves A1 is the highest attempt.
5. It checks stored max 3 equals supplied frozen max 3 and `1 + 1 <= 3`.
6. It checks Q/A1 are still retry-authorizable and rechecks the durable safety
   condition.
7. It validates the existing Q/AR1/L1/C1 attempt-1 authority chain.
8. It recomputes `digest(D1)`, proves Q is bound to D1/T1/P1, validates current
   configuration revision, and proves T1/P1 remains eligible in the fresh route
   input.
9. It resolves measured enforcement evidence for T1/P1 and obtains a fresh
   least-privilege grant under Q's unchanged policy revision. No record has
   been written yet.
10. It allocates the fencing token and constructs fresh RESERVED lease L2.
11. The reducer constructs Q' with `lease_id=L2`, `state=PREPARED`,
    `revision=R8`, and unchanged T1/P1/digest(D1). It constructs A1' only if
    reconciliation evidence must be recorded.
12. It constructs C2 with `session_lease_id=L2`,
    `authorized_attempt_number=2`, `previous_attempt_id=A1`, and the same
    T1/P1/digest(D1).
13. Generalized binding validation proves:
    - C2/Q'/AR1/L2 have the same command and policy provenance;
    - C2, Q', and L2 all carry L2;
    - AR1 still carries L1 and is unchanged;
    - the allowed L1/L2 difference is justified only by C2's exact A1 ->
      attempt-2 chain; and
    - target/profile/route/tier/subject equalities all hold.
14. It inserts L2 and C2, CAS-updates A1 if needed, CAS-updates Q to Q', and
    commits once.
15. It returns `RetryAuthorizationBundle(Q', A1', L2, C2, D1)`.

### 6.4 Subsequent capability gate

1. 5C builds the same-target `AttemptDispatchPlan` with C2, T1/P1, and D1.
2. `dispatch_and_execute()` calls `require_dispatch_capability()` before
   planning.
3. The gate loads Q', exact supplied C2, AR1, L2, A1, and attempt history.
4. It proves C2 is active because `C2.session_lease_id == Q'.lease_id == L2`.
5. It proves C2 authorizes the next durable number: current highest is 1 and
   `C2.authorized_attempt_number == 2`.
6. It validates T1/P1/digest(D1), policy revision/expiry, and enforcement floor.
7. The gate succeeds. The old C1 would fail the active lease equality.
8. `create_attempt()` rechecks the active capability in its own transaction and
   inserts A2 with `attempt_number=2` and `lease_id=L2`.
9. Dispatch-intent revalidation checks C2's authorized number equals A2's
   number before any spawn.

Seam 9.1 is closed: the capability used by the subsequent dispatch is bound to
the new request/session lease, while AR1 remains immutable provenance.

---

## 7. Scenario simulation 2 — Failover retry, happy path

### 7.1 Starting durable state and decision

Use the same Q/A1/AR1/L1/C1/max-3 state, but assume the upstream decision is
`RetryAction.FAILOVER`. The failed target is T1/P1 and a fresh health snapshot
shows T2/P2 eligible.

### 7.2 Application failover input

1. `ApplicationWorkflows.authorize_retry()` prepares the `FailoverRoute`
   intent by projecting telemetry and freezing current health.
2. Its route factory builds the current candidate input.
3. The application creates an immutable copy that retains every T1 candidate
   but marks all candidates with `instance_id == T1` ineligible with
   `FAILED_TARGET_EXCLUDED_BY_RETRY`.
4. It does not choose T2 and does not call `RoutingService.select_route()`.
5. It calls `DispatchService.authorize_retry()` with
   `FailoverRoute(failed_route_decision_id=D1,
   failover_route_request=exclusion_bearing_request)`, current policy
   revision, and `(R7, A4, 1, 3)`.

### 7.3 Single failover authorization transaction

1. `RetryAuthorizationCoordinator.authorize_retry()` matches the
   `FailoverRoute` intent, enters one write unit of work, and reads Q, A1, all
   attempts, retry policy, AR1, L1, C1, D1, and the route request's persisted
   admission snapshot.
2. It performs the same revision, exact-history, frozen-policy, bound, state,
   safety, prior-authority, and current-route checks as Scenario 1.
3. It proves every route candidate on T1 is explicitly excluded.
4. It calls pure `routing.model.select_route()` with a fresh decision ID D2.
5. Pure selection returns T2/P2. The coordinator explicitly proves T2 != T1
   and computes `digest(D2)`.
6. It constructs the prospective request route binding
   `(configuration revision of D2, T2, P2, digest(D2))` while preserving Q's
   required capability tier and command/admission identity.
7. It resolves enforcement evidence for T2/P2 and obtains a fresh capability
   grant for T2/P2 under Q's current/frozen policy revision. No record has been
   written.
8. It allocates the fencing token and constructs fresh RESERVED lease L2.
9. The reducer constructs Q' with `lease_id=L2`, `state=PREPARED`, revision R8,
   and T2/P2/digest(D2). It constructs any allowed A1 reconciliation update.
10. It constructs C2 with L2, T2/P2/digest(D2), authorized attempt 2, previous
    A1, original AR1, and least-privilege tier.
11. It validates the entire prospective chain in memory.
12. It inserts D2 and its candidate audits (including the explicit T1
    exclusion), inserts L2, inserts C2, CAS-updates A1 if needed, CAS-updates Q
    once, and commits once.
13. It returns `RetryAuthorizationBundle(Q', A1', L2, C2, D2)`.

### 7.4 Subsequent dispatch proof

1. 5C resolves the adapter/profile from machine-owned D2 data and builds an
   `AttemptDispatchPlan` for T2/P2 with C2. It does not copy an external
   `SessionHint` from T1.
2. `require_dispatch_capability()` loads exact C2 and proves its L2,
   T2/P2/digest(D2), attempt-2, policy, tier, and enforcement bindings match Q'.
3. A call using T1, P1, C1, or D1 fails before invocation planning.
4. `create_attempt()` creates A2 on L2 and dispatch-intent revalidation binds C2
   to A2 before spawn.

Seam 9.2 is closed: D2, Q's target/route binding, L2, and C2 become durable in
one commit, with the failed instance excluded from replacement selection.

---

## 8. Scenario simulation 3 — Concurrent conflict

Assume callers X and Y both adjudicated from the same durable values:

```text
expected request revision = R7
expected previous-attempt revision = A4
expected highest attempt number = 1
frozen max attempts = 3
```

### 8.1 X wins the SQLite write lock

1. X enters `BEGIN IMMEDIATE`; Y blocks before it can read under a write
   transaction.
2. X reads R7/A4/highest 1/max 3 and passes every precondition.
3. X constructs and writes one new route only if failover, one lease L2, one
   capability C2, the optional A1 update, and Q revision R8.
4. X commits and returns the successful bundle.

### 8.2 Y resumes after X commits

1. Y acquires the write lock and starts its own unit of work.
2. Its first authoritative request read returns R8, not expected R7.
3. Y immediately raises `StaleRevisionError(command_id, R7, R8)` before
   allocating a fencing token or writing a route, lease, capability, request,
   or attempt.
4. The unit of work exits with rollback/no-op. Y receives the clean typed
   `REVISION_CONFLICT` outcome.
5. 5C will later catch only this typed conflict, reload state, observe that
   another authorization/attempt moved first, and return either
   `CONCURRENT_ATTEMPT_IN_PROGRESS` or the authoritative terminal outcome. 5B
   does not implement that translation.

### 8.3 Cancellation wins instead

If an external cancellation transaction commits first, it advances the
request and/or attempt revision. The retry caller then fails at step 3 or 4 of
the common preconditions with `StaleRevisionError`, before authority mutation.
After reload, `CANCELLED` wins and 5C returns `CONCURRENT_TERMINAL_STATE`; the
retry decision is discarded.

If the retry transaction commits first, the cancellation actor must confront
the new request revision/lease and its own CAS/state rules. It cannot silently
update the pre-retry snapshot. Exactly one transaction commits from the shared
expected state.

### 8.4 Non-SQLite/CAS fallback

For a store whose write transactions do not serialize reads as SQLite does,
both callers could initially load R7/A4. Both may construct prospective rows,
but only one request/attempt CAS can succeed. The loser raises the existing
typed stale revision error. Exiting the still-uncommitted unit of work rolls
back its prospective route, lease, and capability rows. Therefore the durable
outcome is still exactly one authorization, not two partial authorizations.

The unique `(command_id, authorized_attempt_number)` constraint is a final
storage backstop, not the primary concurrency signal. Normal contention is
reported through the explicit revision/highest checks or CAS, not by leaking a
raw SQLite unique-constraint exception.

---

## 9. Simulation findings folded back into the plan

The simulations forced five corrections that are now part of Units 5B-1 to
5B-3:

1. **A naive second capability row is impossible and insufficient.** Current
   uniqueness constraints reject it, and the immutable receipt still carries
   L1. Unit 5B-1 versions capability authority by attempt and validates an
   exact previous-attempt chain.
2. **The active capability cannot be selected by command after history becomes
   one-to-many.** All security gates select the exact supplied ID or the row
   matching the request's current lease.
3. **Failover cannot call the existing routing service inside the transaction.**
   The plan uses pure selection plus route repository writes in the 5B unit of
   work.
4. **A capability minted before an AttemptSnapshot exists still needs a durable
   scope.** It authorizes exactly `highest + 1`; both `create_attempt()` and
   dispatch-intent revalidation prove that number.
5. **Route exhaustion and capability denial must happen before fencing/record
   mutation.** Route selection and target grant evaluation precede
   `allocate_fencing_token()` and every repository write.

No known gap found by the three required simulations remains deferred to 5C.
5C owns only reload/translation/orchestration after receiving these typed 5B
outcomes.

---

## 10. 5B completion gate

5B is complete only when all of the following planned evidence exists:

1. Unit 5B-1 migration/model/gate tests pass, including initial and retry
   capability chains and immutable admission history.
2. Unit 5B-2 proves a same-target retry's new capability passes the next real
   dispatch gate and the old capability fails.
3. Unit 5B-3 proves failover excludes the failed instance and atomically binds
   the replacement route, request, lease, and capability.
4. The authoritative bound is checked inside both route-intent branches before
   fence or record allocation.
5. Request revision, previous-attempt revision, and highest-attempt number are
   all independently tested stale fences.
6. Fault injection proves rollback after every prospective write point for both
   paths.
7. A real two-caller SQLite test proves exactly one winner and one typed
   revision conflict.
8. The single service/application `authorize_retry()` surface accepts only the
   tagged route intent, and no test or source path calls the legacy
   non-capability-aware implementation/signature.
9. Admission receipt, prior route decisions, prior capabilities, prior leases,
   and completed attempt history remain immutable.
10. No 5C loop, DEFER scheduler, session remediation, or process retry is
    included in the 5B implementation commits.

This draft must receive the planned independent cross-review before any 5B
implementation begins.

---

## 11. Review record

Reviewer: `ag.deepthink`

Verdict: **READY FOR IMPLEMENTATION, no revisions required.** All seven review
items were checked against actual source at the planning baseline rather than
against this document's own claims. The Section 10 precondition above is
therefore satisfied; 5B implementation may begin from this plan as written.

| Review item | Independent result | Final disposition |
|---|---|---|
| Capability-lease schema claims (Section 1.1, Unit 5B-1) | Confirmed real: `0018_capability_leases.sql` does enforce `UNIQUE(command_id)` and `UNIQUE(admission_receipt_id)`, so a second capability row is genuinely impossible today | Ratified; the migration-0022 rebuild is a real requirement, not defensive over-design. |
| Transaction boundary feasibility (Sections 2.4, 4) | Confirmed the single-`unit_of_work()` design fits the existing store abstractions, and the operations that do **not** yet exist are correctly flagged as new ports rather than assumed present | Ratified unchanged. |
| Typed failure taxonomy (Section 3) | Confirmed complete and non-overlapping, with a strict precedence order between the revision, policy, bound, route, and capability failure classes | Ratified unchanged. |
| Concurrency scenario (Section 8) | Confirmed sound, including the pre-transaction route/health staleness question: freezing health outside the transaction is safe because every prospective binding is revalidated against durable records inside it | Ratified unchanged. |
| Unit boundaries (Section 5) | Confirmed 5B-1, 5B-2, and 5B-3 are each independently testable and shippable in the stated order | Ratified unchanged. |
| Scope discipline | Confirmed the "keep untouched" lists genuinely hold the 5C loop, DEFER/resume, adapter/materialization, and process execution out of 5B | Ratified unchanged. |
| Three additional silent-danger checks | Confirmed already covered by the plan: legacy retry-state tightening (Section 4 item 9 / Unit 5B-2 reducer change), external session-ID bleed prevention on cross-adapter failover (Section 5, Unit 5B-3), and `create_attempt()` TOCTOU closure via in-transaction re-proof of the active capability's authorized number (Section 2.1) | No additions needed; recorded as independently verified. |

No review item produced a required revision, so this plan's technical content
(units, transaction steps, write orders, and scenarios) is ratified exactly as
originally drafted.

Post-ratification simplification pass (`cc.deepthink` audit + `ag.deepthink`
contained review): 3 fixes applied -- corrected Section 2.1 rationale,
consolidated duplicate Section 4/Section 7 checks, and collapsed Section 2.5 to
one method with a tagged-union route intent. No transaction logic or safety
property changed.
