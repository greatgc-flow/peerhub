# T1 Increment 5C: Outer-Loop Integration and Durable Resume Plan

Status: **RATIFIED (Cross-reviewed by ag.deepthink)**

Date: 2026-08-14

Planning baseline rechecked by the independent reviewer: `573fdd7`. Increment
5A and 5B are **FULLY DONE**. Seams 9.1 and 9.2 are closed, and atomic retry
authorization for both `SameTargetRoute` and `FailoverRoute` is implemented.

Ratified parent contract:
`PHASE3-T1-INCREMENT5-RETRY-LOOP-DESIGN-R1-2026-08-13.md`, especially Sections
5 through 11.

---

## 0. Independent source verification and review disposition

The original 5C draft was checked against current production source rather
than treated as authoritative. The following facts materially change its
implementation plan:

| Draft claim | Current-source result | Required plan treatment |
|---|---|---|
| Loop stops use `SUCCESS` and `DEFERRED`. | `RetryLoopStopReason` contains `VERIFIED_SUCCESS` and `CONDITION_DEFERRED`; it has no `SUCCESS` or `DEFERRED`. | Use the existing canonical members. |
| Capability denial maps to `AUTHORIZATION_DENIED`. | `RetryLoopStopReason.AUTHORIZATION_DENIED` does not exist. | Adding that member is an explicit 5C DTO decision and test change, not an implicit mapping. |
| History opens a read-only UOW and calls `list_attempts()`. | `list_attempts()` exists on `DispatchUnitOfWork` and both write backends, but not on `DispatchReadUnitOfWork` or `SqliteReadUnitOfWork`. | Add the read port and SQLite read delegation in 5C-1. Do not use a write transaction for history reads. |
| `ApplicationWorkflows` opens the UOW. | `ApplicationWorkflows` deliberately owns services, not a store. | Add one consistent-snapshot read method on `DispatchService`; the application calls that method. |
| The 5B error table is complete. | The live coordinator can also raise `PolicyStaleError`, `RetryPolicyConflictError`, `RecordNotFoundError`, `CapabilityLeaseViolation`, `InvalidStateTransitionError`, and `InvalidMutationError`. | Define treatment for every typed failure; propagate invariant/security/storage failures. |
| `RetryRouteUnavailableError` can simply "re-adjudicate or consider failover." | `adjudicate_retry()` never produces `RetryAction.FAILOVER`; its only positive action is `RETRY_SAME_TARGET`. No route-policy helper currently converts same-target unavailability into failover. | Ratify one bounded route-action transition; never spin or retry the same stale decision. |
| Failover adapter/profile comes from `RouteDecision` machine data. | `RouteDecision` carries instance/profile, but no peer kind. The committed retry capability carries `selected_peer_kind`. | Resolve from the complete bundle binding: capability peer kind plus selected route instance/profile, with cross-checks. |
| Durable history can be mapped directly to `MultiAttemptExecutionResult`. | `AttemptSnapshot` does not preserve the historical `RequestSnapshot`, process outcome, decoded output, materialization results, or prior `RetryWorkflowResult` required by `AttemptExecutionRecord.execution`. | Choose and ratify a durable aggregate projection instead of fabricating exact historical objects. This remains blocker B1. |
| A resumed post-failover loop can validate the current route. | `RequestSnapshot` and `CapabilityLease` retain only the route digest, while same-target authorization requires a route decision ID. There is no read-by-digest route port. | Add a durable route-decision lookup by request binding/digest, or persist the decision ID. This remains part of blocker B2. |
| 5B alone guarantees exactly one `dispatch_and_execute()` call. | 5B CAS/uniqueness permits one authorization, and `create_attempt()` plus the attempt/capability constraints permit one durable next attempt. They do not prevent two concurrent callers from entering the method before one wins attempt creation. | State the real guarantee (one durable attempt/process spawn), and add a typed loser path if exact method-entry ownership is required. This remains blocker B3. |

Because B1 through B4 in Section 7 change public DTO/API or orchestration
semantics, this cross-review does not silently pick an answer. The document
records the necessary decisions and recommended direction for the next
ratification round.

---

## 1. Outcome and boundaries

Increment 5C delivers `dispatch_with_retries()` on `ApplicationWorkflows`. It
connects 5A's pure replay adjudication, 5B's atomic retry authorization, and
the existing `dispatch_and_execute()` single-attempt primitive.

5C must deliver:

- the parent design's complete 14-step loop, with every step assigned to a
  sub-increment;
- durable `max_attempts` freeze before attempt 1;
- fresh durable history reload after DEFER and concurrency conflicts;
- idempotent initial-attempt handling on resume;
- explicit CONDITIONAL evidence handling without guessed recovery or delay;
- same-target validation and a separately explicit failover transition;
- adapter/profile/session plan construction from the complete committed
  authorization binding;
- normal aggregate outcomes for exhaustion, deferral, authorization denial,
  and the two required concurrency cases; and
- at-most-once durable attempt creation and process spawn for each authorized
  attempt number.

The earlier wording "exactly one `dispatch_and_execute()` call per authorized
attempt" is too strong globally. Two processes can enter the method after the
same stale read; the database fences the second at attempt creation. The final
plan must either ratify the narrower durable-attempt/spawn guarantee or add an
explicit durable attempt-claim API before calling the primitive.

5C does **not** deliver:

- automatic session remediation, replacement, or cross-adapter transfer;
- invented cooldown/backoff values, blocking sleeps, or a scheduler;
- mutation of the invocation after `INVOCATION_PLAN_REJECTED` or
  `OUTPUT_LIMIT_EXCEEDED`;
- retry of classification-unknown legacy failures;
- changes to 5B's authorization transaction or route-selection weights; or
- adapter-specific interpretation beyond 5A's frozen classification.

---

## 2. Required design decisions

### 2.1 Consistent durable loop-state read

`ApplicationWorkflows` must not reach into a service's private `_store`.
5C-1 adds a public read method on `DispatchService`, provisionally:

```python
@dataclass(frozen=True)
class RetryLoopState:
    request: RequestSnapshot
    max_attempts: int | None
    attempts: tuple[AttemptSnapshot, ...]
    current_lease: LeaseSnapshot
    current_capability: CapabilityLease

def load_retry_loop_state(
    self,
    command_id: CommandID | str,
) -> RetryLoopState: ...
```

The method performs one `read_unit_of_work()` snapshot and loads the request,
retry policy, monotonic attempts, request-selected lease, and capability found
by that lease. `DispatchReadUnitOfWork` and `SqliteReadUnitOfWork` therefore
gain the already-existing `list_attempts(command_id)` read.

The loader distinguishes these states before any execution:

1. no attempts and attempt-1 authority active: the initial plan is eligible;
2. a terminal highest attempt: rebuild/adjudicate it; never execute the initial
   plan again;
3. an active highest attempt: return `CONCURRENT_ATTEMPT_IN_PROGRESS`;
4. capability authority for N+1 but only attempts 1..N: authorization already
   committed and dispatch is pending; do not authorize N+2; and
5. any gap, duplicate, command mismatch, missing active lease/capability, or
   incompatible authority number: invariant failure.

`validate_attempt_history()` currently rejects an empty tuple. The loader must
handle the pre-attempt empty case explicitly and call that helper only for
nonempty history.

Durable route recovery must also be resolved. Same-target authorization needs
the current `route_decision_id`, but the request stores only a digest. The
recommended 5C-1 direction is a read-only
`get_route_decision_by_binding(client_request_id, route_decision_digest)` port
that returns exactly one digest-matching immutable decision or fails closed on
zero/multiple matches. This avoids pretending the caller's original route ID
is still current after failover.

The exact aggregate reconstruction shape is not yet ratified; see B1. Until it
is, 5C-1 cannot truthfully claim to rebuild `AttemptExecutionRecord` objects.

### 2.2 Exact stop-reason and typed-error mapping

Existing adjudication reasons map to the existing stop vocabulary:

| Decision/outcome | `RetryLoopStopReason` |
|---|---|
| verified success | `VERIFIED_SUCCESS` |
| honest unverified delivery | `DELIVERED_UNVERIFIED` |
| authoritative cancellation | `AUTHORITATIVE_CANCELLATION` |
| `NEVER` disposition | `NEVER_DISPOSITION` |
| unsafe without evidence | `UNSAFE_NO_EVIDENCE` |
| CONDITIONAL condition unmet / `RetryAction.DEFER` | `CONDITION_DEFERRED` |
| durable attempt limit | `ATTEMPT_LIMIT_REACHED` |
| no failover route | `ROUTE_EXHAUSTED` |
| legacy classification ambiguity | `LEGACY_CLASSIFICATION_UNKNOWN` |
| concurrent terminalization | `CONCURRENT_TERMINAL_STATE` |
| concurrent authorization/attempt | `CONCURRENT_ATTEMPT_IN_PROGRESS` |

Capability denial needs one explicit additive enum member:

```text
RetryLoopStopReason.AUTHORIZATION_DENIED
```

That public DTO extension belongs in 5C-2 with an exhaustive-vocabulary test.
There is no new `SUCCESS` or `DEFERRED` alias.

Every expected 5B boundary failure is treated as follows:

| Error | 5C treatment |
|---|---|
| `StaleRevisionError` | Reload once and classify the authoritative state under Section 2.5; never reuse the stale decision. |
| `AttemptLimitReachedError` | Return `ATTEMPT_LIMIT_REACHED`. |
| `RouteExhaustedError` | Return `ROUTE_EXHAUSTED`. |
| `CapabilityAuthorizationDeniedError` | Return `AUTHORIZATION_DENIED`; never reclassify as a process/vendor failure. |
| `RetryRouteUnavailableError` | Discard the same-target route attempt and perform at most one freshly prepared failover transition under Section 2.3. No repeated same-target authorization and no spin. |
| `RetryPolicyConflictError` | Propagate as caller/configuration conflict; never widen or normalize the frozen bound. |
| `PolicyStaleError` | Propagate as caller/policy-revision drift; no authority was minted. |
| `RecordNotFoundError` | Propagate. A missing policy after the freeze point or missing request/attempt/route/lease/receipt/snapshot is an orchestration/storage invariant failure. |
| `CapabilityLeaseViolation` | Propagate as a security/binding invariant; never downgrade it to ordinary authorization denial or concurrency merely by message matching. |
| `InvalidStateTransitionError` / `InvalidMutationError` | Propagate after a source reload confirms no typed concurrency outcome. 5C should normally prevent these calls. |
| arbitrary adapter/storage/programming exception | Propagate; never turn it into a retryable attempt classification. |

### 2.3 Route action and next-plan construction

The current 5A adjudicator establishes replay safety but does not choose a
replacement target. The 5C plan must make the route transition explicit:

1. An adjudicated `RETRY_SAME_TARGET` first submits `SameTargetRoute` with
   freshly projected/frozen route facts.
2. If 5B returns `RetryRouteUnavailableError`, discard that route input and
   prepare exactly one `FailoverRoute` from a new freeze. Change the recorded
   action to `FAILOVER` while preserving the adjudicated safety reason and
   conditions.
3. 5B either commits one replacement or raises `RouteExhaustedError`. The
   outer loop does not call same-target authorization again for that attempt.
4. Any `StaleRevisionError` at either boundary restarts from durable history,
   not from step 2.

This is the recommended bounded interpretation of "consider failover." If the
ratifiers want caller-selected route action instead, the public loop signature
must gain an explicit route-intent policy input. Leaving the phrase ambiguous
is not acceptable.

`RouteDecision` alone is insufficient to resolve a failover adapter. The
committed `RetryAuthorizationBundle` must be cross-checked as follows:

```text
bundle.request.selected instance/profile/digest
  == selected route candidate instance/profile/digest(decision)
  == bundle.capability_lease instance/profile/digest
```

The adapter peer kind comes from
`bundle.capability_lease.selected_peer_kind`. 5C needs an injected/testable
`RetryTargetResolver(peer_kind, instance_id, profile_id)` rather than assuming
that instance ID is a peer kind. An unresolved target yields
`FAILOVER_UNAVAILABLE` without process execution; the next round must decide
whether candidates are preflight-resolved before 5B commits or whether a
committed-but-not-yet-executable authority is a supported resumable state (B4).

Plan construction rules:

- same-target: retain the current plan's adapter, profile, adapter request, and
  valid `SessionHint`; replace route/capability IDs with the committed bundle;
- failover: use the resolved adapter/profile; copy only invariant logical
  request inputs; set the adapter request's profile to the replacement profile,
  set `requested_session_action=SessionAction.NONE`, and set `session=None`;
  never carry an external session identity across adapters; and
- both paths: use the capability lease ID and the exact route decision returned
  by the same atomic bundle, never IDs from preparatory route input.

### 2.4 CONDITIONAL evidence and session-invalid behavior

Step 8 is partly implemented by 5A: `adjudicate_retry()` checks the ordinary
UNSAFE gate, requires matching `RetryConditionEvidence`, and returns `DEFER`
when evidence is absent, mismatched, or unsatisfied. 5C still must provide a
fresh evidence source and honor the action.

The ratified parent `dispatch_with_retries()` signature has no evidence input,
and the current `HealthService` does not expose the application-facing cooldown
boundary promised to the scheduling follow-up. Therefore the original
scenario's claim that health "now shows recovered" automatically reaches the
adjudicator is false.

Recommended amendment: add an optional, injected
`RetryConditionEvidenceProvider` callback to `dispatch_with_retries()`. Invoke
it only after each durable reload for the latest attempt; never cache evidence
across DEFER or a revision conflict. `None` means no authoritative evidence and
therefore `CONDITION_DEFERRED`, with no guessed time.

`SESSION_INVALID` is stricter. A satisfied boolean condition is not itself a
replacement session plan. Because 5C explicitly has no session-remediation
input, `SESSION_REPLACED_OR_REMOVED` remains unmet in 5C and returns
`CONDITION_DEFERRED`. The loop must not reuse the rejected `SessionHint`, clear
only `session` while leaving `SessionAction.RESUME`, or silently turn RESUME
into NONE. A later separately ratified remediation API must replace/remove both
values together.

### 2.5 At-most-once and concurrency mechanism

The current durable protections are real but narrower than the original draft
claimed:

- 5B's `UNIQUE(command_id, authorized_attempt_number)` plus request/attempt
  expected-value checks allow one authorization winner for N+1;
- `create_attempt()` rechecks that the request-selected capability authorizes
  the computed next number;
- `UNIQUE(command_id, attempt_number)` and the one-active-attempt partial index
  allow one durable attempt row; and
- dispatch-intent capability revalidation occurs before spawn.

Together these guarantee at most one durable N+1 attempt and process spawn.
They do not guarantee that only one concurrent caller enters
`dispatch_and_execute()`, because attempt creation happens inside that method
after capability validation and adapter planning.

The loop must never catch `CapabilityLeaseViolation` text to infer a race. The
recommended B3 resolution is a typed expected-attempt claim: pass the expected
authorized attempt number into the attempt-creation seam and raise a typed
revision/concurrent-attempt result when another caller already consumed it,
while retaining `CapabilityLeaseViolation` for actual authority corruption.
After that typed result, reload and return `CONCURRENT_ATTEMPT_IN_PROGRESS` if
the durable state shows pending/active N+1; otherwise propagate.

After any `StaleRevisionError`, one fresh read classifies:

- non-retryable terminal advancement (especially cancellation, verified
  success, or delivered-unverified) -> `CONCURRENT_TERMINAL_STATE`;
- current capability authorizes N+1 while history remains 1..N -> another
  runner already authorized N+1 -> `CONCURRENT_ATTEMPT_IN_PROGRESS`;
- highest attempt is N+1 and is active ->
  `CONCURRENT_ATTEMPT_IN_PROGRESS`;
- highest attempt is N+1 and terminal -> rebuild/adjudicate that result; and
- no authoritative advancement -> re-adjudicate once from the fresh state,
  with an explicit bounded retry count so storage livelock cannot spin.

---

## 3. Unit-by-unit exact source scope

The units are ordered and independently testable, but no intermediate unit is
independently shippable as the completed retry loop.

### Unit 5C-1 -- Durable read model, route recovery, and policy freeze

Source scope:

- `peerhub/application/retry.py`: add the ratified durable reconstruction type
  after B1 is resolved; keep `validate_attempt_history()` as the nonempty
  history validator.
- `peerhub/dispatch/unit_of_work.py`: add `list_attempts()` to
  `DispatchReadUnitOfWork` and the route-binding read required by B2.
- `peerhub/persistence/sqlite.py` and
  `peerhub/persistence/sqlite_dispatch.py`: expose those read-only operations.
- routing read port/backend files if B2 uses digest-to-decision recovery.
- `peerhub/dispatch/service.py`: add `load_retry_loop_state()` as one
  consistent read snapshot; retain the existing idempotent
  `freeze_retry_policy()`.
- `peerhub/application/workflows.py`: add `_ensure_frozen_retry_policy()` and
  the read/rebuild helper through the public dispatch service.
- focused unit and real-SQLite integration tests.

Independent verification target:

1. missing request and corrupt history fail closed;
2. empty history is accepted only as the pre-attempt state;
3. numbers 1..N load in exact order; gaps/duplicates fail;
4. policy freeze inserts once, returns the equal value, and rejects conflict;
5. request, policy, attempts, active lease/capability, and route binding come
   from one read snapshot;
6. a post-failover restart recovers the replacement route decision, not the
   caller's original route; and
7. historical output contains only durably reconstructable facts agreed in
   B1--no fabricated request revisions or process/decoder results.

### Unit 5C-2 -- Complete single-run loop and plan materialization

Source scope:

- `peerhub/application/retry.py`: add
  `RetryLoopStopReason.AUTHORIZATION_DENIED`, the route-action helper or policy
  chosen under Section 2.3, and any resolver/evidence protocols ratified under
  B2/B4.
- `peerhub/application/workflows.py`: implement `dispatch_with_retries()` for
  parent steps 1 through 11, 13, and 14 in the no-concurrent-mutation case.
  Step 3 (initial-plan idempotency) and step 8 (CONDITIONAL/DEFER) are explicit
  scope; they were missing from the original draft.
- target resolver composition wherever `ApplicationWorkflows` is constructed.
- integration tests for fresh initial execution, resumed terminal attempt,
  same-target retry, failover retry, every stop reason, and session behavior.

Unit 5C-2 must handle every `RetryAction` exhaustively, including DEFER. It may
let a typed `StaleRevisionError` propagate temporarily for the focused unit;
5C-3 owns its reload translation. It must not omit a match branch that happens
to be supplied only by later tests.

Independent verification target:

1. initial plan executes only when no durable attempt represents it;
2. a resumed terminal attempt is adjudicated without replaying attempt 1;
3. verified success and honest unverified delivery stop canonically;
4. CONDITIONAL without fresh matching evidence returns
   `CONDITION_DEFERRED` and does not sleep;
5. `SESSION_INVALID` never reuses or partially clears RESUME state;
6. same-target success uses the fresh capability and unchanged valid session;
7. same-target unavailability attempts at most one fresh failover;
8. failover plan uses capability peer kind, selected route instance/profile,
   replacement profile ID, `SessionAction.NONE`, and `session=None`;
9. exhaustion and capability denial return their exact aggregate reasons; and
10. invariant/security/caller errors propagate under Section 2.2.

### Unit 5C-3 -- Durable resume, typed attempt claim, and concurrency

Source scope:

- the dispatch attempt-claim seam selected under B3;
- `peerhub/application/workflows.py`: catch typed authorization/attempt claim
  concurrency only, reload, and apply Section 2.5 outcomes;
- integration tests with real concurrent SQLite callers and deterministic
  barriers at authorization and attempt creation.

Independent verification target:

1. a stale decision is never resubmitted;
2. concurrent cancellation returns `CONCURRENT_TERMINAL_STATE`;
3. concurrent authorization with no N+1 row returns
   `CONCURRENT_ATTEMPT_IN_PROGRESS`;
4. concurrent active N+1 returns `CONCURRENT_ATTEMPT_IN_PROGRESS`;
5. a terminal N+1 is rebuilt and adjudicated rather than executed again;
6. exactly one N+1 attempt row and at most one process spawn occur;
7. the loser creates no lease/capability/attempt/process side effects;
8. DEFER/resume refetches condition evidence and route facts;
9. repeated conflicts are bounded and cannot spin; and
10. same-target and failover two-caller races have equivalent loser semantics.

---

## 4. Parent 14-step algorithm coverage

| Parent step | Owner | Required behavior |
|---|---|---|
| 1 | 5C-1/5C-2 | Load one consistent durable snapshot, validate history, rebuild the ratified durable aggregate. |
| 2 | 5C-1/5C-2 | Freeze/use command-global max, reject conflict, advisory bound pre-check. |
| 3 | 5C-2 | Execute initial plan only when no attempt represents it. |
| 4 | 5C-2 | Add fresh or reconstructed durable attempt record. |
| 5 | 5A helper + 5C-2 | Derive state from complete attempt/request evidence, never null classification alone. |
| 6 | 5A helper + 5C-2 | Map every STOP decision to the exact existing stop enum. |
| 7 | 5A `adjudicate_retry()` + 5C-2 | Pass durable certainty/reconciliation/completion facts. |
| 8 | 5A `adjudicate_retry()` + 5C-2 | Supply fresh matching condition evidence and honor DEFER; session-invalid special rule remains fail-closed. |
| 9 | 5C-2 + 5B | Fresh route preparation, bounded same-target-to-failover action, explicit failed-target exclusion. |
| 10 | 5C-2 | Pass the exact request/attempt/history/max expected values used for adjudication. |
| 11 | 5B (already done), invoked by 5C-2 | Atomic lease/route/capability/request commit. |
| 12 | 5C-3 | Typed conflict reload and concurrency outcome. |
| 13 | 5C-2, hardened by 5C-3 | Resolve the complete committed target binding and execute under typed attempt claim. |
| 14 | 5C-2 | Iterate from the returned durable attempt; never reuse stale pre-DEFER state. |

The original 5C-2 list omitted steps 3 and 8. Step 8 is not "already fully
done" merely because 5A implements the pure check: 5C must source evidence,
pass it, and obey DEFER. Step 12 correctly belongs to 5C-3.

---

## 5. Required scenario simulations

### 5.1 CONDITIONAL DEFER, fresh evidence, and success

Preconditions must be explicit: attempt A1 has a CONDITIONAL
`PROVIDER_UNAVAILABLE` classification **and** passes the ordinary replay-safety
gate (for example, the frozen completion contract is `replay_safe`). Without
that safety fact, 5A correctly returns `UNSAFE_NO_EVIDENCE`, not DEFER.

1. First invocation freezes `max_attempts=3` and executes A1.
2. Fresh matching provider evidence is absent or unsatisfied.
3. `adjudicate_retry()` returns `RetryAction.DEFER` and 5C returns
   `CONDITION_DEFERRED`; no sleep occurs.
4. A later invocation reloads Q, policy, A1, active authority, and route. It
   does not reuse the prior decision or evidence.
5. The injected evidence provider now returns matching, satisfied
   `PROVIDER_RECOVERED` evidence.
6. 5C prepares a route action and calls 5B with the freshly loaded expected
   revisions/history number.
7. Authorization commits C2/L2; 5C constructs the plan and A2 succeeds.
8. The result stops at `VERIFIED_SUCCESS`, not the nonexistent `SUCCESS`.

The earlier example of a thread "adding a tag" is removed because no such
request mutation exists in current source.

### 5.2 Concurrent cancellation after resume

1. A resumed caller loads request revision R2 and A1.
2. Another actor cancels Q before 5B acquires its write transaction, producing
   R3 and `CANCELLED`.
3. 5B raises `StaleRevisionError(R2, R3)` before any write.
4. 5C reloads; authoritative cancellation wins.
5. It returns `CONCURRENT_TERMINAL_STATE` and does not authorize or execute A2.

### 5.3 Concurrent next attempt in progress

This second concurrency simulation is mandatory before ratification.

1. Runners X and Y load the same terminal A1, request revision R2, and highest
   attempt number 1.
2. X wins 5B authorization for attempt 2, committing request R3, L2, and C2.
3. Y's stale authorization raises `StaleRevisionError` and writes nothing.
4. Before X creates A2, Y reloads and sees attempts still `1..1` but the
   request-selected C2 authorizes attempt 2. Y returns
   `CONCURRENT_ATTEMPT_IN_PROGRESS`; it must not try to authorize attempt 3.
5. Variant: X has already created active A2. Y reloads attempts `1..2`, sees A2
   active, and returns the same outcome.
6. A real two-thread SQLite test proves one authority bundle, one A2 row, and
   at most one spawn. The losing path produces no extra route, lease,
   capability, attempt, or process record.

The cancellation simulation alone does not cover this state and is therefore
insufficient.

---

## 6. Deliberately not decided

Carried forward unchanged from the parent design:

- default `max_attempts`;
- delay/jitter values and external scheduler implementation;
- automatic authentication, network, provider, or quota repair;
- automatic session replacement, `SessionAction.CREATE`, or cross-adapter
  session transfer;
- mutation of model, flags, prompt, limits, or other invocation semantics after
  deterministic plan/limit rejection;
- automatic reconciliation criteria;
- retrying legacy classification-unknown failures;
- adapter-owned retry policy; and
- tool-call execution semantics.

These non-decisions do not permit the loop to guess recovery evidence, invent
a delay, reinterpret adapter errors, or silently edit an invocation. The
optional evidence-provider and target-resolver boundaries proposed above
consume explicit facts; they do not implement the deferred repair/scheduler
policies themselves.

---

## 7. Blocking decisions for the next ratification round (RESOLVED)

### B1 -- Durable aggregate shape

**Decision: RATIFIED as proposed.**
A durable attempt-record DTO will be added containing the authoritative `AttemptSnapshot`, durably recoverable request/authority/route facts, retry decision, and optional same-process execution detail. `MultiAttemptExecutionResult` and its tests will be explicitly amended. We will not fabricate exact historical `ExecutionWorkflowResult` objects from incomplete snapshots.

### B2 -- Current route recovery and route-action policy

**Decision: RATIFIED as proposed.**
The digest/binding-to-route-decision recovery read port (`get_route_decision_by_binding`) and the bounded same-target-unavailable -> fresh failover transition in Section 2.3 are ratified. A post-failover restart must not reuse the original route ID.

### B3 -- Exact concurrency claim at attempt creation

**Decision: RATIFIED as proposed.**
We ratify the narrower, source-backed guarantee of one durable attempt/process spawn, with a typed expected-attempt loser result at the attempt-creation seam. This accurately reflects the DB fencing behavior without requiring artificial method-level locking. We will not parse `CapabilityLeaseViolation` strings to infer concurrency.

### B4 -- Fresh condition evidence and replacement-target resolution APIs

**Decision: RATIFIED as proposed.**
The additive optional `RetryConditionEvidenceProvider` and injected `RetryTargetResolver` are ratified. An unresolved failover target yields `FAILOVER_UNAVAILABLE` without process execution.

### Architectural Impact on 5B

**Crucially, none of these 4 resolutions require reopening 5B (the atomic authorization transaction).**
- B1 only affects the in-memory reconstruction in `application/retry.py`.
- B2 adds a read-only port to fetch the `route_decision_id` *before* calling 5B's `authorize_retry()`, which expects that ID.
- B3 adds a typed claim at the attempt creation seam (after authorization).
- B4 adds injected callbacks to the outer loop orchestration (`dispatch_with_retries`), passing evidence to `adjudicate_retry` and resolving adapters for `dispatch_and_execute`, both outside 5B's write transaction.

With B1 through B4 resolved, the unit scopes and scenario simulations in this document are fully consistent with the ratified parent design.

**The plan is now fully RATIFIED and implementation of Unit 5C-1 may begin.**
