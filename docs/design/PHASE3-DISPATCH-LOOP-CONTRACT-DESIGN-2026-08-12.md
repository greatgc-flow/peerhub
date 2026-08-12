# Phase 3 dispatch-loop shared contract surface (2026-08-12)

Status: **draft 2, with round-1 findings applied; awaiting round-2
ratification.** This document is the artifact a ratification round votes
on, not an implementation plan already approved.

Scope: Phase 3's five coupled roadmap facets (adapter-wiring loop,
session continuation, streaming decode, error-taxonomy mapping, and
tool-call parsing). This document ratifies only the shared contract
surface. Each facet is implemented as a separately verified L2 increment
against that surface.

Evidence base: two split source investigations covered all five facets.
The streaming investigation also ran a live Codex JSONL probe
(`[cli_live]`). Round 1 then checked the synthesis against the concrete
types and persistence codec. Section 6 records every finding and its
disposition.

---

## 1. Corrected premise: most inner machinery already exists

The roadmap describes all five facets as not started. Source inspection
showed that the inner single-attempt path is already substantial; the
missing work is at its boundaries.

### 1.1 Base adapter wiring

Single-attempt dispatch already resolves a real adapter, admits and
prepares a request, plans and supervises a process, decodes output,
assesses completion, and commits a terminal result
(`application/direct_ask.py:139-288`,
`application/workflows.py:540-926`). All three real adapters are in the
runtime registry (`adapters/registry.py:42-48`).

The missing "loop" is an outer bounded orchestrator. The existing
`authorize_retry()` workflow (`application/workflows.py:486-538`) is not
called after a failed attempt. The future outer loop must consume the
whole `ExecutionWorkflowResult`, because pre-dispatch and start-uncertain
returns have an `AttemptSnapshot.terminal_error_code` but no `AskResult`.
For a terminal executed attempt it additionally consumes the persisted
`AskResult` classification described in Section 2.

`dispatch_and_execute()` remains exactly one attempt. The outer loop may
call it repeatedly only after the existing retry-authorization workflow
has produced a fresh lease and admitted the next attempt.

### 1.2 Session continuation has four concrete gaps

Some session vocabulary already exists:
`AdapterRequest.requested_session_action`, `SessionHint`, and
`Capability.SESSION` are defined in `adapters/contract.py`. Enabling the
feature still requires all of the following:

1. Every real adapter currently rejects any non-null `SessionHint`
   (`agy_adapter.py:128`, `claude_adapter.py:135`,
   `codex_adapter.py:146`).
2. `dispatch_and_execute()` hardcodes `session=None` when it invokes
   `plan_invocation()` (`application/workflows.py:581-586`) and exposes no
   parameter through which a caller can supply a session.
3. No real adapter advertises `Capability.SESSION`; all three descriptor
   capability sets are empty (`*_adapter.py:52`).
4. `direct_ask.py` always constructs `AdapterRequest` with
   `SessionAction.NONE` (`application/direct_ask.py:243-251`).

The three verified CLI mechanisms remain vendor-specific behind the
adapter boundary: Claude uses `--resume <id>`, Codex places
`resume <id>` after `exec`, and Agy uses `--conversation <id>`
(`[cli_live]`). General dispatch must not branch on peer identity.

### 1.3 Streaming is present as a protocol shape, not as behavior

`OutputDecoder.feed()` and `finalize()` already have the correct
incremental shape (`adapters/contract.py:583-597`). Every real decoder's
`feed()` currently buffers and returns no events, while
`dispatch_and_execute()` constructs the decoder only after
`run_process()` has returned and feeds the full merged stream once
(`application/workflows.py:857-874`).

The pipe runner already reads stdout and stderr concurrently for
supervision (`dispatch/pipe.py:116-168`), but
`ProcessSupervisor.on_chunk()` records only bytes and timestamp, losing
the channel (`dispatch/process.py:589-607`). A live Codex probe emitted
pre-terminal JSONL such as `thread.started`, `turn.started`, vendor error
events, `item.completed`, and `turn.failed` (`[cli_live]`). Claude and
Agy are invoked in terminal-only JSON modes today, so only Codex is an
immediate streaming implementation candidate.

### 1.4 Failure information is computed and then discarded

PeerHub already has three distinct layers:

- `TerminalClassification` records mechanical process outcomes.
- `ErrorCode` records stable protocol/application codes.
- `OperationalFailureCategory` records measured environment/provider
  categories.

`ProcessSupervisor` centrally computes `EXIT_NON_ZERO` and
`OUTPUT_LIMIT_EXCEEDED`, as well as start uncertainty and both timeout
kinds (`dispatch/process.py:21-33, 700-765`).
`dispatch_and_execute()` has the resulting
`ProcessSupervisionOutcome.terminal_classification` in scope when it
constructs `AskResult`, but drops it (`application/workflows.py:857-895`).

Separately, all three adapters currently write
`ErrorCode.INTERNAL_ERROR` into the protocol-only
`ProtocolAssessment.protocol_failure` for a nonzero exit, malformed
output, and empty response. That is not merely a missing additive field;
the mapping increment must change existing adapter behavior so process
failure is no longer mislabeled as protocol failure.

### 1.5 Tool-call parsing is greenfield

No tool-call fixtures exist for any real CLI. Current adapters extract
only the final response fields and discard other tool-call data. The
shared surface needs a typed event kind, but not yet a policy for
executing or approving a captured tool call.

---

## 2. Proposed shared contract surface

Draft 1 incorrectly described the proposal as purely additive. Draft 2
adds fields and enum values, but also relocates one enum without changing
its identity, changes adapter use of `ProtocolAssessment`, and adds an
optional parameter to a public workflow.

### 2.1 Retry-neutral attempt classification

Choose round-1 correction B(i). Add a frozen classification DTO in
`dispatch/contract.py`, next to `AskResult`, that cannot express a retry
decision:

```python
@dataclass(frozen=True)
class AttemptFailureClassification:
    code: ErrorCode
    phase: ErrorPhase
    operational_failure_category: OperationalFailureCategory | None
```

Add to `AskResult`:

```python
terminal_classification: TerminalClassification | None = None
failure_classification: AttemptFailureClassification | None = None
```

Do **not** add `ErrorDetail` to `AskResult`.
`ErrorDetail.retry_disposition` is required today
(`core/protocol.py:724-738`). If the single-attempt workflow constructed
an `ErrorDetail`, it would have to decide retry policy at exactly the
layer this design prohibits from doing so.

The type-level invariant is therefore:

> Neither `AskResult` nor `AttemptFailureClassification` has a
> `RetryDisposition` field. `dispatch_and_execute()` cannot accept one
> and cannot return one. Only the future outer-loop adjudicator may
> combine an attempt classification with execution certainty, replay
> safety, reconciliation, session state, and current routing state to
> construct the existing full `ErrorDetail`.

This selects a retry-neutral surface now and leaves the complete outer
loop implementation for its own L2 increment without contradiction.

### 2.2 Surface the existing process classification

`TerminalClassification` currently lives in `dispatch/process.py`, which
imports `ExecutionOutcome` from `dispatch/contract.py`. Typing a new
`AskResult.terminal_classification` field by importing the enum in the
opposite direction would create a circular import.

Move the canonical enum definition to `dispatch/contract.py` and have
`dispatch/process.py` import and re-export that exact class object.
Existing imports such as
`from peerhub.dispatch.process import TerminalClassification` continue to
work and preserve enum identity. This mirrors the existing
`ProtocolAssessment` relocation/re-export pattern rather than defining a
second enum.

### 2.3 One central mapping function

Exactly one pure function in `dispatch/model.py` maps process
classification plus available terminal evidence into the retry-neutral
DTO:

```python
def classify_attempt_failure(
    *,
    terminal_classification: TerminalClassification | None,
    execution: ExecutionOutcome,
    protocol: ProtocolAssessment,
    decoded_output: DecodedOutput | None,
) -> AttemptFailureClassification | None: ...
```

It is called where `AskResult` is assembled. Adapters cannot supply
`TerminalClassification`: `PeerAdapter.interpret_output()` receives only
`ProcessTerminalEvidence` and returns only `ProtocolAssessment`. This is
a structural boundary, not a prose convention.

The function derives an operational category only from normalized,
invocation-correlated `VENDOR_ERROR` evidence in `decoded_output` (or
leaves it `None`). It does not accept an adapter-declared category as an
input, so a vendor adapter cannot promote its own assertion into a
measured operational fact.

The total mechanical mapping is:

| `TerminalClassification` | Base `ErrorCode` | Phase |
|---|---|---|
| `START_UNCERTAIN` | `START_UNCERTAIN` | `POST_SPAWN` |
| `SILENCE_TIMEOUT` | `SILENCE_TIMEOUT` | `POST_SPAWN` |
| `PROCESS_TIMEOUT` | `PROCESS_TIMEOUT` | `POST_SPAWN` |
| `EXIT_NON_ZERO` | `INTERNAL_ERROR` as the generic fallback; the terminal classification preserves the exact process fact | `POST_SPAWN` |
| `OUTPUT_LIMIT_EXCEEDED` | `PROCESS_KILLED`; the terminal classification preserves the output-limit cause | `POST_SPAWN` |

Recognized structured vendor evidence may refine the generic
`EXIT_NON_ZERO` code inside this same function:

- invalid/expired/missing resume identity -> new `SESSION_INVALID`;
- an invalid model, flag, operand, or other proof that the identical
  invocation plan is deterministically rejected -> new
  `INVOCATION_PLAN_REJECTED`;
- measured auth/network/provider/rate/quota evidence -> keep the base
  code and set the matching existing `OperationalFailureCategory`.

Absent corroborating evidence, the category remains `None`. No
substring guess is promoted to a measured category.

`SESSION_INVALID` and `INVOCATION_PLAN_REJECTED` are the only new
`ErrorCode` values in this design. Draft 1's proposed
`PROCESS_EXIT_NONZERO` and `OUTPUT_LIMIT_EXCEEDED` codes are withdrawn;
they duplicated existing `TerminalClassification` values.

`INVOCATION_PLAN_REJECTED` is distinct from `PROFILE_UNAVAILABLE`: the
peer/profile may exist and route successfully while the concrete plan's
model or operand is invalid. Its defining fact is that replaying the
identical plan cannot repair it. That fact is classification evidence,
not by itself a retry decision.

### 2.4 Protocol assessment behavior changes

`ProtocolAssessment` keeps its exact five-field shape and remains about
framing/protocol evidence only. Each real adapter must stop setting
`protocol_failure=INTERNAL_ERROR` merely because the process exited
nonzero. A parseable vendor error is still parsed protocol; it can emit
the already-existing `DecoderEventKind.VENDOR_ERROR`. Malformed,
truncated, or empty protocol output remains expressible through the
existing assessment booleans and an appropriate protocol failure.

Persisted attempts will straddle both behaviors:

- legacy rows may contain `ProtocolAssessment.protocol_failure ==
  INTERNAL_ERROR` for a nonzero exit and have no terminal/failure
  classification fields;
- new rows carry process classification separately and reserve
  `protocol_failure` for protocol/framing failure.

Migration and readers must not pretend these histories are semantically
identical.

### 2.5 Session surface

Add the following optional keyword to the public one-attempt workflow:

```python
dispatch_and_execute(
    ...,
    session: SessionHint | None = None,
) -> ExecutionWorkflowResult
```

The workflow passes it unchanged to `PeerAdapter.plan_invocation()`.
The later session increment changes each adapter's current rejection,
threads the selected session from the outer loop/direct caller, and
advertises `Capability.SESSION` only after the adapter supports its real
resume mechanism. A non-null session must still be rejected when that
capability is absent.

### 2.6 Streaming and tool-call surface

`DecodedOutput` adds:

```python
session_id: str | None = None
```

`DecoderEventKind` adds `TOOL_CALL`. The existing
`DecoderEvent.payload: Mapping[str, JsonValue]` can carry the vendor-
normalized call name and arguments; execution semantics remain deferred.

No `OutputDecoder` or `PeerAdapter` signature change is needed for
streaming. Add an optional ordered event sink to
`dispatch_and_execute()` and a channel/sequence/timestamp-tagged internal
process-chunk DTO. The pipe runner serializes both reader threads through
one consumer before calling the mutable decoder. The workflow maps the
runner-owned channel value to the adapter's `OutputChannel` and never
calls one decoder concurrently.

`Capability.STREAM` is advertised only after an adapter emits events
before process exit. The first implementation is Codex-only because the
live CLI emits JSONL; Claude and Agy remain terminal-only until their
invocation formats change and are measured.

---

## 3. Failure corpus and mapping scope

Fresh recount at `2026-08-12T11:56:23+09:00` found **2,515 valid JSONL
records and 318 failures** in `P:\.ai\ask_history.jsonl`, with zero
malformed records (`[empirical_probe]`). The round-1 reviewer measured
2,513/317 and `lease_expired=8`; two records, including one additional
`lease_expired`, were appended before this recount.

| Durable `failure_reason` | Count | Phase-3 disposition |
|---|---:|---|
| `nonzero_exit` | 70 | `TerminalClassification.EXIT_NON_ZERO`; central evidence may refine code/category |
| `terminal_timeout` | 70 | Existing `PROCESS_TIMEOUT` or `SILENCE_TIMEOUT`; PeerHub preserves the distinction hub history loses |
| `GOVERNED_MUTATION_VIOLATION` | 64 | Governance/effect-surface enforcement, outside adapter taxonomy; retain as a higher-layer failure |
| `timeout` | 33 | Existing process/silence timeout according to measured supervisor evidence |
| `rate_or_session_limit` | 24 | **Measurement gap:** do not map without more evidence; see below |
| `UNATTRIBUTED_GOVERNED_CHANGE` | 20 | Governance attribution enforcement, outside adapter taxonomy; retain as a higher-layer failure |
| `query_file_missing` | 16 | Coordinator/input-artifact failure, not vendor taxonomy |
| `lease_expired` | 9 | Existing `LEASE_EXPIRED` |
| `auth_error` | 6 | Existing `AUTH_UNAVAILABLE` operational category when evidence is present |
| `empty_response` | 5 | Protocol assessment: `response_present=False` |
| `model_operand_invalid` | 1 | New `INVOCATION_PLAN_REJECTED` |

The two governance categories are 84 of 318 failures (26.4%). Omitting
them from draft 1 made its supposedly complete measured summary
incorrect. They remain outside the adapter mapping because they are
enforcement outcomes above the peer process, not because they are
unimportant.

`rate_or_session_limit` is not safe to translate as one category. The
hub classifier groups rate/quota/context-capacity and some network/
timeout patterns under that reason, while a rate limit, a quota limit,
and an invalid session require different handling. The durable history
does not retain enough evidence to split the 24 rows. This remains
`TEST NEEDED`; new PeerHub attempts set a specific category only from
invocation-correlated evidence and otherwise record `None`.

---

## 4. Persistence and compatibility obligations

`AskResult` is hand-encoded and decoded in
`persistence/sqlite_dispatch.py:98-214`. The implementation increment
must update both directions for `terminal_classification` and
`failure_classification`; changing only the dataclass is insufficient.

Defaulting new fields to `None` creates a known forward-compatibility
ambiguity: after decode, an old row lacking the keys is indistinguishable
from a new row that explicitly recorded no classification. This does not
block the shared surface because the outer retry loop is not yet reading
these fields, but it is a mandatory input to that loop's increment. A
loop must not interpret an absent legacy classification as evidence that
retry is safe. It must add a codec/version-presence marker or require
reconciliation and fail closed for legacy failures.

Fast compatibility tests required with the mapping increment:

1. all `TerminalClassification` members round-trip through `AskResult`;
2. every row in Section 2.3's central mapping is covered;
3. legacy JSON without the new keys decodes under the documented
   ambiguous/unknown behavior rather than being treated as retryable;
4. fixture-based bytes for Agy, Claude, and Codex prove that nonzero exit
   no longer writes `INTERNAL_ERROR` into `ProtocolAssessment` solely due
   to the exit code, while malformed/empty protocol behavior remains
   covered;
5. `SESSION_INVALID` and `INVOCATION_PLAN_REJECTED` require explicit
   evidence fixtures and do not trigger from unrelated text.

The cheap fixture tests for all three adapters are in scope with the
mapping behavior change. Only slow live Claude/Codex end-to-end tests are
deferred.

---

## 5. Increment boundaries after surface ratification

The contract surface is shared; implementation remains staged:

1. **Classification plumbing:** relocate/re-export
   `TerminalClassification`, add the retry-neutral DTO and `AskResult`
   fields, implement the sole central mapper, update the SQLite codec,
   and change all three adapters' protocol-failure behavior with fast
   fixtures.
2. **Session continuation:** add/thread the optional workflow parameter,
   implement the three adapter-specific resume plans, extract final
   session IDs, and advertise `Capability.SESSION` only where proven.
3. **Codex streaming:** add the ordered runner-to-decoder event path and
   implement incremental JSONL parsing with remainder buffering.
4. **Tool-call capture:** add normalized `TOOL_CALL` events and measured
   per-CLI fixtures; execution/approval semantics remain a later design.
5. **Outer retry/resume/failover loop:** centrally adjudicate
   `RetryDisposition`, call the existing retry workflow, bound attempts,
   and return a structured multi-attempt result.

The following are explicitly not ratified here:

- a disposition algorithm or full retry-loop function signature;
- adapter-owned retry decisions;
- slow live Claude/Codex coverage as a prerequisite for the type change;
- streaming claims for Claude or Agy;
- tool-call execution semantics.

Deferring the disposition signature is coherent only because Section
2.1 selects the retry-neutral option: no ratified type requires a
disposition before the outer-loop increment exists.

---

## 6. Round-1 review record (draft 1 to draft 2)

Reviewer: `cc.deepthink`. Direction approved; two type/signature defects
were blocking. Both blockers were independently confirmed against the
source before revision.

| ID | Finding | Disposition in draft 2 |
|---|---|---|
| B | `AskResult.error_detail: ErrorDetail` would force the one-attempt layer to provide required `RetryDisposition` | **Fixed with option B(i).** `AttemptFailureClassification` has only code, phase, and operational category. Neither it nor `AskResult` can carry a disposition (Section 2.1) |
| D | Proposed process codes duplicated centrally computed `TerminalClassification` and discarded existing evidence | **Fixed.** Surface the existing enum, relocate/re-export it to avoid a circular import, and define one central mapper. Withdraw both duplicate codes (Sections 2.2-2.3) |
| A | Measured corpus count was incomplete and omitted 84 governance failures | **Fixed and freshly recounted.** Section 3 lists every category; current corpus is 2,515/318, with the reviewer-to-draft delta explained |
| E-1 | Nonzero mapping changes current adapter behavior, so the design is not purely additive | **Fixed.** Section 1.4 and 2.4 explicitly require all three adapters to stop writing `INTERNAL_ERROR` as protocol failure solely for nonzero exit and record split-era persistence semantics |
| E-2 | Session continuation omitted adapter rejection, the workflow's hardcoded `session=None`, public signature impact, and empty capabilities | **Fixed.** All four concrete gaps and the optional public workflow parameter are listed in Sections 1.2 and 2.5 |
| F | Hand-written `AskResult` codec creates legacy-`None` ambiguity | **Recorded.** Section 4 names both codec directions, the ambiguity, and the fail-closed requirement before retry history is consumed |
| Q2 | `model_operand_invalid` cannot be distinguished from a transient generic nonzero exit | **Fixed.** Add only `INVOCATION_PLAN_REJECTED` and `SESSION_INVALID`; identical-plan rejection semantics and evidence requirements are explicit (Sections 2.3 and 3) |
| Q2 gap | `rate_or_session_limit` conflates distinct conditions | **Recorded as `TEST NEEDED`.** No specific category is assigned without invocation-correlated evidence (Section 3) |
| Q5a | Deferring the loop contradicted making full `ErrorDetail` mandatory now | **Fixed by B(i).** The ratified surface is disposition-free, so the loop and its adjudication signature can remain a later increment (Sections 2.1 and 5) |
| Q5b | All-adapter fixture coverage is required when adapter protocol behavior changes | **Adopted.** Fast recorded-byte tests for all three adapters are mandatory with classification plumbing; only slow live tests remain deferred (Section 4) |
| Q1 | Retry authority must remain central with no adapter exception | **Settled as proposed**, now enforced by the type split and adapter protocol (Sections 2.1-2.3) |
| Q3 | Codex-only initial streaming scope | **Settled as proposed.** Codex emits JSONL; Claude/Agy are invoked in terminal-only JSON modes (Sections 1.3 and 2.6) |

No finding was silently dropped. The material change from draft 1 is
that failure **classification** and retry **adjudication** are now
different types produced in different increments. That separation is
the central invariant draft 1 stated in prose but failed to encode.

---

## 7. Round-2 verification gates

Round 2 should verify these concrete questions rather than reopen the
settled direction:

1. Does the retry-neutral DTO make it impossible for
   `dispatch_and_execute()` or an adapter to smuggle a
   `RetryDisposition` into `AskResult`?
2. Is relocating and re-exporting `TerminalClassification` sufficient to
   preserve existing imports while avoiding the contract/process import
   cycle?
3. Is Section 2.3's mapping total over the existing enum, and are
   `SESSION_INVALID`/`INVOCATION_PLAN_REJECTED` the only justified new
   stable codes?
4. Do the codec compatibility rule and all-adapter fixture obligations
   close the split-era behavior introduced by E-1?
5. Does the optional `session` workflow parameter accurately expose the
   missing session seam without prematurely claiming adapter capability?
