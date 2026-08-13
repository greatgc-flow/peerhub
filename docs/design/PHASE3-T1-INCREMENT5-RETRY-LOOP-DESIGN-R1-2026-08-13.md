# T1 Increment 5: Retry/Resume/Failover Loop — Ratified Design R1

Status: **RATIFIED (2 rounds; `ag.deepthink` cross-review; both critical
blocking seams independently confirmed).**

Date: 2026-08-13

Implementation status: **5A DONE (`a5556a2`/`24102f8`/`a6118a9`); 5B and 5C not started.**

*Implementation Note: `adjudicate_retry()` required 4 fix rounds after adversarial review found real cross-branch policy bugs the original tests missed. This is worth recording as a genuine lesson on the necessity of adversarial probing over relying solely on original unit tests.*

Source baseline rechecked for ratification: `cb8da37`

This is the standalone implementation contract for Phase 3 T1 increment 5.
The historical ratified record in
`PHASE3-DISPATCH-LOOP-CONTRACT-DESIGN-2026-08-12.md` remains unchanged.

---

## 1. Core decision

Retry classification and retry authorization remain separate:

```text
AttemptFailureClassification
        ↓ pure mapping
RetryDisposition
        ↓ combined with execution certainty, replay safety,
          reconciliation, session state, timing, and routing
RetryDecision
        ↓ existing/new authorization workflow
next attempt or terminal loop result
```

`AttemptFailureClassification` remains frozen at exactly three fields. No
disposition is added to it or to `AskResult`.

Two functions are ratified:

```python
def map_retry_disposition(
    failure: AttemptFailureClassification,
    *,
    terminal_classification: TerminalClassification | None,
) -> RetryDisposition: ...
```

```python
def adjudicate_retry(
    execution_result: ExecutionWorkflowResult,
    *,
    durable_attempt_number: int,
    max_attempts: int,
    reconciliation_complete: bool,
    condition_evidence: RetryConditionEvidence | None = None,
) -> RetryDecision: ...
```

*Amendment 2026-08-13 (L1): `condition_evidence` is optional (`| None = None`) to avoid forcing callers to construct a meaningless value for non-CONDITIONAL paths.*

The first function is a pure policy mapping. The second is the outer-loop
adjudicator and is the only layer allowed to decide whether another attempt
may be requested. Neither function itself authorizes a mutation or creates a
lease.

Disposition meanings:

- `SAFE`: no failure-specific prerequisite, although fresh routing, leasing,
  capability validation, optimistic-concurrency validation, and the durable
  attempt bound still apply.
- `UNSAFE`: blind replay may duplicate an effect; retry requires
  `NOT_STARTED`, evidence-backed reconciliation, or an explicitly replay-safe
  completion contract.
- `CONDITIONAL`: the ordinary safety gate applies, plus a failure-specific
  condition must be proven.
- `NEVER`: replaying the same logical request and invocation policy is not an
  allowed repair.

`RetryDisposition` describes intrinsic replay policy. It is not permission to
run. Only the central workflow may combine it with current durable state and
obtain a new attempt authorization.

---

## 2. Required disposition matrix

| Terminal classification | Code | Category | Disposition | Rationale |
|---|---|---|---|---|
| None—verified success | — | — | No disposition | A verified success terminates the loop without asking a retry question. |
| `START_UNCERTAIN` | `START_UNCERTAIN` | None | `UNSAFE` | The process may have accepted the request or produced effects, so blind replay creates a real double-invocation risk. |
| `SILENCE_TIMEOUT` | `SILENCE_TIMEOUT` | None | `UNSAFE` | Silence proves neither non-execution nor absence of effects, matching Protocol V1's rule that post-spawn timeouts are unsafe to replay blindly. |
| `PROCESS_TIMEOUT` | `PROCESS_TIMEOUT` | None | `UNSAFE` | Exceeding the overall deadline does not prove the prior attempt had no effects, even when the process was later terminated. |
| `EXIT_NON_ZERO` | `INTERNAL_ERROR` | None | `UNSAFE` | An unclassified nonzero exit provides no evidence that the failure was transient or that effects did not land. |
| `EXIT_NON_ZERO` | `SESSION_INVALID` | None | `CONDITIONAL` | Identical replay with the rejected session will fail again; retry is meaningful only after an explicitly authorized session replacement or removal. |
| `EXIT_NON_ZERO` | `INVOCATION_PLAN_REJECTED` | None | `NEVER` | The classification explicitly means the identical invocation plan cannot repair itself. |
| `EXIT_NON_ZERO` | `INTERNAL_ERROR` | `AUTH_UNAVAILABLE` | `CONDITIONAL` | Retry requires measured restored authentication or a newly authorized target with independent credentials. |
| `EXIT_NON_ZERO` | `INTERNAL_ERROR` | `NETWORK_UNAVAILABLE` | `CONDITIONAL` | Retry requires current route/network recovery evidence or failover to a separately healthy path. |
| `EXIT_NON_ZERO` | `INTERNAL_ERROR` | `PROVIDER_UNAVAILABLE` | `CONDITIONAL` | Immediate duplication is prohibited; a current health/cooldown boundary or an alternate healthy provider is required. |
| `EXIT_NON_ZERO` | `INTERNAL_ERROR` | `QUOTA_EXHAUSTED` | `CONDITIONAL` | Retry requires measured quota availability, an authoritative reset boundary, or failover to an independent quota pool. |
| `EXIT_NON_ZERO` | `INTERNAL_ERROR` | `RATE_LIMITED` | `CONDITIONAL` | Retry requires an authoritative retry boundary or an independently admitted alternate route, not a guessed sleep interval. |
| `OUTPUT_LIMIT_EXCEEDED` | `PROCESS_KILLED` | None | `NEVER` | Replaying the identical request with the identical byte limit deterministically repeats the same safety-limit failure. |
| No terminal classification; protocol failure | Protocol code | None, `ASSESSMENT` | `UNSAFE` | Malformed, truncated, or empty protocol output is not proof that the task failed before producing effects. |

No classifier row maps to `SAFE`. That is intentional because this classifier
predominantly describes post-spawn outcomes. Proven `NOT_STARTED` failures,
such as `SPAWN_FAILED`, are handled from the enclosing
`ExecutionWorkflowResult`, not forced through
`AttemptFailureClassification`.

---

## 3. A missing classification does not mean success

`failure_classification is None` does **not** by itself mean success.

The current mapper returns `None` whenever there is no process or protocol
failure, even when completion derives:

- `DELIVERED_UNVERIFIED`
- `INCOMPLETE`

The outer loop must therefore inspect `request.state` and the complete
`AskResult`:

| State with no classification | Outer-loop treatment |
|---|---|
| `SUCCEEDED_VERIFIED` | Stop successfully. |
| `DELIVERED_UNVERIFIED` | Return the honest terminal delivery; never auto-retry. |
| `INCOMPLETE` | Treat replay as unsafe unless the frozen completion contract declares `replay_safe`. |
| `CANCELLED` | Stop. A user or authoritative external cancellation wins and must never be converted into a retry. |
| Failed legacy row with classification presence unknown | Fail closed; do not infer retry safety. |
| Early `FAILED_PRE_DISPATCH` or `START_UNCERTAIN` with no `AskResult` | Use the attempt's state, certainty, and terminal error code. |

This is why the loop consumes the whole `ExecutionWorkflowResult`, as the
ratified Phase 3 document already requires. It also means the loop must not
delegate state interpretation to the current reducer's broad set of states
accepted by `authorize_retry()`; the central adjudicator applies the stricter
rules above first.

---

## 4. Result and decision types

```python
@dataclass(frozen=True)
class RetryDecision:
    disposition: RetryDisposition | None
    action: RetryAction
    reason: RetryDecisionReason
    required_conditions: tuple[RetryCondition, ...]
    not_before: int | None
```

`RetryAction` contains at least:

```text
STOP
RETRY_SAME_TARGET
FAILOVER
DEFER
```

Each attempt is returned with its post-attempt decision:

```python
@dataclass(frozen=True)
class AttemptExecutionRecord:
    execution: ExecutionWorkflowResult
    error_detail: ErrorDetail | None
    retry_decision: RetryDecision
    retry_authorization: RetryWorkflowResult | None
```

```python
@dataclass(frozen=True)
class MultiAttemptExecutionResult:
    command_id: CommandID
    attempts: tuple[AttemptExecutionRecord, ...]
    stop_reason: RetryLoopStopReason
```

The final attempt is derived as `attempts[-1]`, avoiding a second independently
stored final result.

Normal exhaustion, conditional deferral, route exhaustion, concurrent
advancement by another runner, and a non-retryable failure return this
aggregate rather than raising. Exceptions remain reserved for invariant
violations and programming or storage failures. A typed optimistic-concurrency
failure raised by the authorization boundary is caught only as the signal to
reload authoritative state; it is never reclassified as an attempt failure.

The loop result must distinguish at least these concurrency outcomes:

- `CONCURRENT_TERMINAL_STATE`: another actor terminalized or cancelled the
  request;
- `CONCURRENT_ATTEMPT_IN_PROGRESS`: another actor already authorized or began
  the next attempt;
- `ATTEMPT_LIMIT_REACHED`: the fresh durable attempt number reached the frozen
  command-global bound.

---

## 5. Outer-loop signature and per-attempt plan

The loop is an `ApplicationWorkflows` method because it coordinates dispatch,
routing, health, leases, capability enforcement, and persistence:

```python
def dispatch_with_retries(
    self,
    command_id: CommandID | str,
    *,
    initial_attempt: AttemptDispatchPlan,
    route_request_factory: RouteRequestFactory,
    current_policy_revision: RevisionValue,
    materializer: ArtifactMaterializer,
    limits: TransportLimits,
    workspace_roots: Mapping[str, Path],
    content_providers: Mapping[str, Callable[[], bytes]],
    completion_contract: CompletionContract,
    heartbeat_timeout_ms: int,
    max_attempts: int,
    transport: str = "pipe",
    service: DispatchService | None = None,
    event_sink: Callable[[DecoderEvent], None] | None = None,
) -> MultiAttemptExecutionResult: ...
```

The per-attempt parameters cannot remain independent fixed arguments if real
failover is supported. They are grouped as:

```python
@dataclass(frozen=True)
class AttemptDispatchPlan:
    route_decision_id: str
    capability_lease_id: str
    peer_instance_id: str
    adapter_request: AdapterRequest
    peer_adapter: PeerAdapter
    profile: ProfileDescriptor
    session: SessionHint | None
```

The initial plan contains the same variable inputs presently passed to
`dispatch_and_execute()`. Every authorized retry returns a new plan. A
same-target retry may preserve most fields; failover supplies a newly
route-bound adapter, profile, capability authorization, and session decision.

Pure disposition policy and these DTOs live in `application/retry.py`; the
cross-feature saga remains a method on `ApplicationWorkflows`.

---

## 6. Durable bound and restart behavior

- `max_attempts` is mandatory, must be at least one, and counts the first
  attempt.
- No default is ratified because no measured policy currently establishes one.
- The first loop invocation freezes `max_attempts` in a durable command-scoped
  retry-policy record. A resumed or concurrent invocation uses that stored
  value and rejects a conflicting caller value; process restart cannot widen or
  reset the budget.
- The used count comes from durable `AttemptSnapshot.attempt_number`, never a
  loop-local counter. Gaps or duplicate numbers are invariant failures, not an
  invitation to infer a count.
- The loop may pre-check the bound from a fresh read for an early normal return,
  but that check is advisory. The authoritative check occurs again inside the
  retry-authorization write transaction.
- The 5B authorization boundary accepts the frozen `max_attempts`, reads the
  fresh highest durable attempt number in the same unit of work, and refuses
  authorization when `highest_attempt_number + 1 > max_attempts`.
- That check occurs before allocating a fencing token, reserving or inserting a
  session lease, issuing capability authority, rebinding a route, or creating
  an attempt. A denied retry leaves no orphan authority record.
- `authorize_retry()` does not currently perform this check. Stage 5B owns the
  required service/persistence addition; 5A owns the policy type, durable
  command-scoped bound, and pure adjudication behavior.
- If the bound is reached, the aggregate returns `ATTEMPT_LIMIT_REACHED` with
  every persisted attempt and the last decision.
- Concurrent loop runners remain subject to request/attempt CAS and the
  one-active-attempt constraint. Exactly one runner may win authorization; no
  loser may spawn independently.

This two-level check is deliberate: the application check provides an ordinary
decision result, while the in-transaction check prevents stale or concurrent
callers from exceeding the durable bound.

---

## 7. Backoff, DEFER, and optimistic concurrency

`RetryDisposition` does not grow a time field. Timing belongs in
`RetryDecision.not_before` and must come from authoritative health or provider
evidence.

Increment 5 does not invent a default delay and does not block a worker thread
with an internal sleep:

- If an authoritative boundary exists and has elapsed, adjudication may
  proceed.
- If the boundary is in the future, return `DEFER`.
- If no authoritative boundary is available, return `DEFER` with
  `not_before=None`; do not estimate.
- A later invocation resumes from durable attempt history and re-evaluates
  current health.

DEFER creates a real concurrency window. During it, cancellation, another loop
runner, reconciliation, or another authorized mutation may change the request,
attempt, or revision. The resumed loop must therefore:

1. perform a fresh read of the request, retry-policy record, and all attempts;
2. rebuild the aggregate and re-adjudicate from that state rather than reuse the
   pre-DEFER `RetryDecision`;
3. pass the request revision, previous-attempt revision, and highest durable
   attempt number used for adjudication into the 5B authorization boundary;
4. have that boundary compare those expected values inside its write
   transaction before any lease, route, capability, or attempt mutation;
5. catch only the typed revision-conflict result (`StaleRevisionError` /
   `REVISION_CONFLICT`), reload, and discard the stale decision;
6. stop on an authoritative terminal state, especially `CANCELLED`;
7. return `CONCURRENT_ATTEMPT_IN_PROGRESS` when another actor already created
   or began the next attempt; and
8. re-adjudicate only when the fresh state is still eligible and no attempt is
   active.

The loop must not spin on CAS failure, repeat authorization with stale evidence,
or convert a revision conflict into a retryable vendor/process failure.

A named follow-up, **Retry Scheduling and Authoritative Cooldown Exposure**,
should expose health's existing cooldown boundary through an
application-facing port and add resumable scheduling. Exact backoff constants
and jitter remain owned by health policy, not by the retry loop.

---

## 8. Session handling

For this increment:

- A caller-supplied valid session may be preserved for a same-adapter retry.
- External session IDs must never be transferred across adapters during
  failover.
- `SESSION_INVALID` remains `CONDITIONAL`, but the condition is currently
  unmet unless a separately authorized plan replaces or removes both the
  `SessionHint` and the corresponding `SessionAction.RESUME`.
- The loop must not silently convert `RESUME` to `NONE`, mint a session, or
  infer that continuing a partially executed session is safer than replay.
- Captured `SESSION_IDENTITY` events remain evidence in the attempt result; they
  do not themselves authorize continuation.

Until session remediation exists, `SESSION_INVALID` produces a conditional
deferral or terminal `SESSION_REMEDIATION_UNAVAILABLE` result; it never produces
an automatic identical replay.

Automatic session repair and `SessionAction.CREATE` are deferred to **Session
Retry Remediation**, including how a changed session plan remains bound to the
original logical request.

---

## 9. Blocking seams confirmed at current HEAD `cb8da37`

### 9.1 Fresh retry leases currently invalidate capability authorization

`authorize_retry()` rotates `RequestSnapshot.lease_id`, but the immutable
admission receipt and capability lease still bind the original session lease.
`require_dispatch_capability()` ultimately calls
`validate_capability_binding()`, which requires equality between the capability
lease, request lease, admission-receipt lease, and current session lease.

An `[empirical_probe]` against `cb8da37` authorized a retry after a proven
`SPAWN_FAILED`, then immediately revalidated its original capability lease. It
failed with:

```text
capability session_lease_id must match request lease_id
```

The round-2 reviewer independently confirmed this seam by tracing
`ApplicationWorkflows.authorize_retry()` through
`AttemptLifecycleCoordinator.authorize_retry()` and
`validate_capability_binding()`.

Therefore increment 5 cannot merely call the existing two public methods in
sequence. Before the loop can ship, retry authorization must atomically issue
attempt-scoped capability authorization bound to the fresh session lease. It
must preserve the original admission receipt as immutable history rather than
rewriting it.

### 9.2 Current retry authorization cannot fail over

The current path:

- requires the request to remain bound to the original route decision;
- stores the selected peer/profile and route digest on the request and
  capability lease;
- validates only configuration-revision drift before retry; and
- reserves a fresh lease using the existing owner and session identity.

It does not select a replacement candidate, exclude the failed target, or
atomically rebind the request and capability authority to a replacement route.
The round-2 reviewer independently confirmed this by tracing
`_require_bound_route()`, `validate_route_for_dispatch()`, and the current retry
service.

Increment 5 is complete only when a failover authorization transaction can
persist the replacement route decision, update request route/target bindings,
issue fresh attempt-scoped capability authority, and reserve the matching lease
as one logical mutation. Until that exists, the loop reports
`FAILOVER_UNAVAILABLE`; calling a same-target retry failover would be incorrect.

### 9.3 Required implementation stages and ownership

The three stages are mandatory:

1. **5A — policy and durable loop state**
   - disposition mapper, adjudicator, decision/aggregate DTOs;
   - durable command-scoped `max_attempts` policy and history reconstruction;
   - state treatment for verified, unverified, incomplete, cancelled, legacy,
     and early failures;
   - no lease, route, or process mutation.
2. **5B — atomic retry/failover authorization**
   - attempt-scoped capability authorization and route rebinding;
   - the authoritative fresh durable-bound check before any authority is
     minted;
   - expected request/attempt revisions and expected highest-attempt number as
     mutation preconditions;
   - rollback and CAS tests proving no orphan lease/capability/route artifacts;
   - same-target and failover authorization paths.
3. **5C — outer-loop integration and durable resume**
   - `dispatch_with_retries()` orchestration;
   - DEFER/resume and concurrent-mutation handling;
   - restart, route exhaustion, attempt exhaustion, and end-to-end aggregate
     tests;
   - proof that each authorization reaches `dispatch_and_execute()` at most
     once.

5A is not permission to call the current retry mutation blindly. 5C must not
land before 5B closes both blocking seams and the durable bound fence.

---

## 10. Loop algorithm

1. Load the request, durable retry-policy record, and all durable attempts.
   Validate their monotonic numbering and rebuild the aggregate.
2. Use the stored command-global `max_attempts`. Reject a conflicting caller
   value and pre-check the fresh highest durable attempt number.
3. Execute the initial plan only if no prior attempt already represents it.
4. Append the returned `ExecutionWorkflowResult` to the aggregate.
5. Derive success/failure from request state and complete attempt evidence, not
   from classification nullability.
6. Stop on verified success, honest unverified delivery, authoritative
   cancellation, `NEVER`, or exhausted attempts.
7. For `UNSAFE`, require `NOT_STARTED`, evidence-backed reconciliation, or
   `completion_contract.replay_safe`.
8. For `CONDITIONAL`, also require the category-specific condition. Return
   `DEFER` when a potentially satisfiable condition is not yet proven; do not
   guess a wait time or replay unchanged.
9. Re-freeze health and select or validate the next route. Failover excludes
   the failed target only through explicit route input/evidence and produces a
   new immutable route decision.
10. Submit a 5B authorization request carrying the expected request revision,
    expected previous-attempt revision, expected highest attempt number, and
    frozen `max_attempts`.
11. Inside one write transaction, reload those records; reject state drift;
    enforce the attempt bound; then atomically create the fresh session lease,
    route binding, and attempt-scoped capability authorization.
12. If authorization reports a revision conflict, discard the decision, reload
    authoritative history, and apply Section 7's concurrency outcomes. Never
    spawn from the losing path.
13. Resolve the next adapter/profile from machine-owned route data and invoke
    `dispatch_and_execute()` once.
14. Repeat from step 4.

The loop must never catch an arbitrary exception and classify it as retryable.
Only specifically typed, expected concurrency and authorization-denial outcomes
are translated into normal aggregate results.

---

## 11. Deliberately not decided

This design does not decide:

- a default `max_attempts`;
- backoff durations or jitter;
- automatic authentication or network repair;
- automatic session replacement, `CREATE`, or cross-adapter session transfer;
- mutation of model, flags, prompt, transport limits, or output limit after
  `INVOCATION_PLAN_REJECTED` or `OUTPUT_LIMIT_EXCEEDED`;
- retrying legacy failures whose classification-field presence is unknown;
- automatic reconciliation or what evidence proves reconciliation complete;
- adapter-owned retry policy; or
- tool-call execution semantics.

Those omissions are intentional. Each changes the invocation, authority, or
evidence model and receives its own ratified contract rather than being hidden
inside a retry loop.

The loop nevertheless **must** handle, and therefore does not defer:

- fresh durable attempt-bound enforcement before every retry authorization;
- optimistic-concurrency failure and state drift after DEFER;
- authoritative cancellation or terminalization by another actor;
- another runner winning the next-attempt authorization;
- exact preservation of prior attempts and immutable admission history; and
- fail-closed handling of legacy classification ambiguity.

---

## 12. Round 2 review record

Reviewer: `ag.deepthink`

Verdict: direction exceptionally sound; ratification approved after folding in
two additional concurrency/durability requirements. The reviewer found no
defect in the disposition matrix or mandatory 5A/5B/5C staging.

| Review item | Independent result | Final disposition |
|---|---|---|
| Section 9.1 lease-rotation/capability mismatch | Confirmed by independent source trace from retry lease rotation through capability-binding equality checks | Retained as a critical blocker; 5B must atomically issue attempt-scoped capability authority for the fresh lease. |
| Section 9.2 absence of failover routing | Confirmed by independent source trace of original-route validation, persisted target/profile bindings, and same-session lease reservation | Retained as a critical blocker; 5B adds atomic replacement-route rebinding, and the loop reports `FAILOVER_UNAVAILABLE` until then. |
| Disposition matrix | Endorsed as domain-accurate | Ratified unchanged. |
| `DELIVERED_UNVERIFIED` / `INCOMPLETE` claim | Confirmed against `AskResult.effective_status` and completion-contract source | Retained; classification nullability is never treated as success. |
| Proposed function/type signatures | Confirmed to fit current application and dispatch boundaries | Ratified, with `attempts_used` tightened to `durable_attempt_number` to make the evidence source explicit. |
| Gap 1: state drift during DEFER/backoff | Confirmed: request and attempt revisions may change before resumed authorization | Added Section 7's expected-revision mutation fence, typed conflict handling, authoritative reload, cancellation precedence, and concurrent-attempt outcome. |
| Gap 2: bound not enforced by `authorize_retry()` | Confirmed: current retry authorization rotates a lease without checking durable `attempt_number` against a bound | Added Section 6's frozen durable policy plus a 5B in-transaction fresh bound check before any lease, route, capability, or attempt authority is minted. |

Both new gaps are required implementation behavior, not deferred questions. No
round-2 finding is omitted. With those additions, the two independently
confirmed critical seams remain explicit preconditions rather than hidden
follow-up work, and this document is the ratified implementation contract for
increment 5.
