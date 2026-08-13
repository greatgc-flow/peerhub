# Phase 3 dispatch-loop shared contract surface (2026-08-12)

Status: **RATIFIED (draft 3, commit `d267750`). Section 5 increments 1,
2, and 3 are IMPLEMENTED; increments 4 and 5 are not started.**
Increment 1 shipped as `bfdd8b2` (1a, classification plumbing) and
`bf9f4ad` (1b, `VENDOR_ERROR` emission); increment 2 shipped as
`f516760` (2a, workflow capability gate), `dda4956` (2b, Claude
RESUME), `f4b2907` (2c, Codex RESUME + ID capture), and `c3d6ceb`
(2d, Agy RESUME + ID capture). Two post-hoc corrections then landed
against that range: `858aec6` wired `classify_attempt_failure()` into
production, which increments 1a/1b had never done, and `3b317f0`
re-grounded 1b's vendor-error patterns in real `[cli_live]` captures.

This document was originally the artifact a closing ratification check
ratified, not an implementation plan already approved. It is now also
the implementation record for increments 1 and 2; sections describing
work that has since landed say so inline, and where the implementation
deliberately diverged from what was ratified, the divergence is called
out rather than quietly edited away (see Section 2.6).

Scope: Phase 3's five coupled roadmap facets (adapter-wiring loop,
session continuation, streaming decode, error-taxonomy mapping, and
tool-call parsing). This document ratifies only the shared contract
surface. Each facet is implemented as a separately verified L2 increment
against that surface.

Evidence base: two split source investigations covered all five facets.
The streaming investigation also ran a live Codex JSONL probe
(`[cli_live]`). Two review rounds then checked the synthesis against the
concrete types, real decoders, workflow, and persistence codec. Section 6
records every finding and its disposition; Section 7 records a
design-validation step folded into the production mapper.

---

## 1. Corrected premise: most inner machinery already exists

The roadmap describes all five facets as not started. Source inspection
showed that the inner single-attempt path is already substantial; the
missing work is at its boundaries.

### 1.1 Base adapter wiring

Single-attempt dispatch already resolves a real adapter, admits and
prepares a request, plans and supervises a process, decodes output,
assesses completion, and commits a terminal result
(`application/direct_ask.py:129-290`,
`application/workflows.py:544-947`). All three real adapters are in the
runtime registry (`adapters/registry.py:42-48`). Line citations
throughout this document are as of commit `3b317f0`.

The missing "loop" is an outer bounded orchestrator. The existing
`authorize_retry()` workflow (`application/workflows.py:490-542`) is
still not called after a failed attempt; increment 5 has not started. The future outer loop must consume the
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
feature still requires all of the following. **Gaps 1-3 were closed by
increment 2 (`f516760`/`dda4956`/`f4b2907`/`c3d6ceb`); gap 4 is still
open.** Line citations below are re-verified against the tree at
`3b317f0`.

1. ~~Every real adapter currently rejects any non-null `SessionHint`.~~
   **RESOLVED (`f516760`).** The three `if session is not None: raise
   ValueError(...)` stubs are gone; the workflow gate in gap 2 is now
   the single rejection point. Each adapter instead raises `ValueError`
   only when `SessionAction.RESUME` arrives without an
   `external_session_id` (`agy_adapter.py:178-180`,
   `claude_adapter.py:162-164`, `codex_adapter.py:202-204`).
2. ~~`dispatch_and_execute()` hardcodes `session=None` when it invokes
   `plan_invocation()` and exposes no parameter through which a caller
   can supply a session.~~ **RESOLVED (`f516760`).** The public
   signature takes `session: SessionHint | None = None`
   (`application/workflows.py:562`), the capability gate runs at
   `application/workflows.py:585-590`, and the hint is passed through
   unchanged at `application/workflows.py:593-598`.
3. ~~No real adapter advertises `Capability.SESSION`; all three
   descriptor capability sets are empty.~~ **RESOLVED
   (`dda4956`/`f4b2907`/`c3d6ceb`).** All three descriptors now carry
   `capabilities=frozenset({Capability.SESSION})`
   (`agy_adapter.py:54`, `claude_adapter.py:54`, `codex_adapter.py:54`),
   each added only in the increment that gave that adapter a real
   resume plan.
4. **STILL OPEN.** `direct_ask.py` always constructs `AdapterRequest`
   with `SessionAction.NONE` (`application/direct_ask.py:243-251`) and
   never passes a `session` to `dispatch_and_execute()`. Nothing in the
   product entry path can request continuation yet; increment 2 built
   the mechanism, not a caller for it.

The three verified CLI mechanisms remain vendor-specific behind the
adapter boundary: Claude uses `--resume <id>`, Codex places
`resume <id>` after `exec`, and Agy uses `--conversation <id>`
(`[cli_live]`). General dispatch must not branch on peer identity. All
three are now implemented exactly this way; see Section 2.5 for the
as-built per-adapter table.

### 1.3 Streaming is implemented for Codex only

`OutputDecoder.feed()` and `finalize()` already had the correct
incremental shape (`adapters/contract.py:583-597`). Increment 3
(`dfde073`) connected that shape to live execution for Codex: the pipe
runner now serializes channel-tagged chunks through one ordered consumer,
and `dispatch_and_execute()` feeds the invocation decoder and forwards
its events to an optional sink before process exit.

The pipe runner already reads stdout and stderr concurrently for
supervision (`dispatch/pipe.py:116-168`), but
`ProcessSupervisor.on_chunk()` records only bytes and timestamp, losing
the channel (`dispatch/process.py:589-607`). A live Codex probe emitted
pre-terminal JSONL such as `thread.started`, `turn.started`, vendor error
events, `item.completed`, and `turn.failed` (`[cli_live]`). Claude and
Agy are invoked in terminal-only JSON modes today, so only Codex is an
streaming implementation target. That Codex-only path is now implemented
by `dfde073`; Claude and Agy remain terminal-only.

### 1.4 Failure information is computed and then discarded

PeerHub already has three distinct layers:

- `TerminalClassification` records mechanical process outcomes.
- `ErrorCode` records stable protocol/application codes.
- `OperationalFailureCategory` records measured environment/provider
  categories.

`ProcessSupervisor` centrally computes `EXIT_NON_ZERO` and
`OUTPUT_LIMIT_EXCEEDED`, as well as start uncertainty and both timeout
kinds (`dispatch/process.py:21-33, 700-765`).
`dispatch_and_execute()` had the resulting
`ProcessSupervisionOutcome.terminal_classification` in scope when it
constructed `AskResult`, but dropped it.

> **RESOLVED, in two steps.** Increment 1a (`bfdd8b2`) added the
> `AskResult` fields and the mapper, but did not call either from
> production; the workflow still discarded the classification for
> another six commits. `858aec6` closed that, computing
> `terminal_classification` and `classify_attempt_failure()` immediately
> before `AskResult` is constructed
> (`application/workflows.py:898-916`). See Section 5 increment 1 for
> why the intervening review rounds did not catch it.

Separately, all three adapters wrote `ErrorCode.INTERNAL_ERROR` into the
protocol-only `ProtocolAssessment.protocol_failure` for a nonzero exit,
malformed output, and empty response. That was not merely a missing
additive field; the mapping increment had to change existing adapter
behavior so process failure is no longer mislabeled as protocol failure.

> **RESOLVED (`bf9f4ad`).** Each adapter's `interpret_output()` now sets
> `protocol_failure=None if has_vendor_error else
> ErrorCode.INTERNAL_ERROR` (`agy_adapter.py:236, 244, 252`,
> `claude_adapter.py:226, 234`, `codex_adapter.py:279, 287`), so a
> parseable vendor error is treated as parsed protocol. Malformed and
> empty output still yield `INTERNAL_ERROR`, which is the intended
> remaining behavior, not residual drift.

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

The function derives an operational category only from an allowlisted,
normalized `VENDOR_ERROR` payload in the current invocation's
`decoded_output` (or leaves it `None`). Correlation comes from the
workflow passing the output of the per-invocation decoder, not from a
decoder-authored correlation flag. An adapter decoder does produce the
payload, so this is normalization discipline enforced by the central
classifier and cross-adapter fixtures, not a type boundary that makes a
dishonest adapter assertion impossible. Arbitrary payload keys or
unnormalized text are ignored.

The total mechanical mapping is:

| `TerminalClassification` | Base `ErrorCode` | Phase |
|---|---|---|
| `START_UNCERTAIN` | `START_UNCERTAIN` | `POST_SPAWN` |
| `SILENCE_TIMEOUT` | `SILENCE_TIMEOUT` | `POST_SPAWN` |
| `PROCESS_TIMEOUT` | `PROCESS_TIMEOUT` | `POST_SPAWN` |
| `EXIT_NON_ZERO` | `INTERNAL_ERROR` as the generic fallback; the terminal classification preserves the exact process fact | `POST_SPAWN` |
| `OUTPUT_LIMIT_EXCEEDED` | `PROCESS_KILLED`; the terminal classification preserves the output-limit cause | `POST_SPAWN` |
| `None` with non-null `protocol.protocol_failure` | That protocol `ErrorCode` | `ASSESSMENT` |
| `None` with no protocol failure | Return `None` | n/a |

The terminal-classification rows take precedence when a process failure
and a protocol failure coexist. The two `None` rows make the function
total over its declared input domain: an exit-zero empty, malformed, or
otherwise protocol-failed response produces an assessment-phase
classification, while an attempt with neither failure signal produces
no failure classification.

Recognized structured vendor evidence may refine the generic
`EXIT_NON_ZERO` code inside this same function:

- invalid/expired/missing resume identity -> new `SESSION_INVALID`;
- an invalid model, flag, operand, or other proof that the identical
  invocation plan is deterministically rejected -> new
  `INVOCATION_PLAN_REJECTED`;
- measured auth/network/provider/rate/quota evidence -> keep the base
  code and set the matching existing `OperationalFailureCategory`.

Absent corroborating evidence, the category remains `None`. No arbitrary
substring is promoted to a measured category. Increment 1 makes the two
refinement codes reachable: every real decoder must inspect both its
structured vendor output and the existing invocation-owned
`ProcessSupervisionOutcome.canonical_stream`, match only fixture-backed
vendor error patterns, and emit `VENDOR_ERROR`. The canonical stream
already contains both stdout and stderr bytes even though it does not yet
preserve their channel; this reaches the plain-stderr
`model_operand_invalid` case without pulling ordered channel plumbing out
of the later streaming increment.

The normalized payload has exactly two classifier-consumed keys:
`normalized_kind` is one of `session_invalid`,
`invocation_plan_rejected`, `auth_unavailable`, `network_unavailable`,
`provider_unavailable`, `quota_exhausted`, or `rate_limited`; and
`evidence_source` is `structured_vendor_output` or
`known_terminal_pattern`. The central mapper, not the decoder, converts
those allowlisted values to an `ErrorCode` or
`OperationalFailureCategory`.

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
nonzero. A parseable vendor error is still parsed protocol; increment 1
requires it to emit the already-existing
`DecoderEventKind.VENDOR_ERROR`. Malformed, truncated, or empty protocol
output remains expressible through the existing assessment booleans and
an appropriate protocol failure.

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

The workflow owns the capability gate. Before calling
`PeerAdapter.plan_invocation()`, it checks
`Capability.SESSION in peer_adapter.descriptor.capabilities` whenever
`session` is non-null. Absence raises a typed
`UnsupportedCapabilityError`, added in `core/errors.py` as a
`PeerHubError` mapped to `ErrorCode.INVALID_PARAMS`, carrying the adapter
ID and requested capability; the adapter's current bare `ValueError` is
not the contract. Only after that gate does the workflow pass `session`
unchanged to `plan_invocation()`.

**IMPLEMENTED (increment 2).** `f516760` (2a) added the optional
keyword, the workflow-owned gate, and `UnsupportedCapabilityError`
(`core/errors.py`), and deleted the three adapters' now-unreachable
non-null-session `ValueError` stubs. `dda4956`/`f4b2907`/`c3d6ceb`
(2b/2c/2d) then implemented the three adapter-specific resume plans and
advertised `Capability.SESSION`, each only in the increment that gave
that adapter a real resume mechanism:

| adapter | RESUME invocation | session-ID capture | `Capability.SESSION` |
| --- | --- | --- | --- |
| `cc` / `claude.cmd` (`dda4956`) | `--resume <id>` appended to the `-p ... --output-format json` argv | none -- see Section 2.6 | `claude_adapter.py:54` |
| `cx` / `codex.cmd` (`f4b2907`) | `exec resume --json <id> <prompt>` | `SESSION_IDENTITY` from `thread.started`'s `thread_id` (`codex_adapter.py:97-107`) | `codex_adapter.py:54` |
| `ag` / `agy.exe` (`c3d6ceb`) | `--conversation <id>` appended (never `--continue`, which is ambient most-recent state and cannot be bound to a `SessionHint`) | `SESSION_IDENTITY` from the top-level `conversation_id` (`agy_adapter.py:95-102`) | `agy_adapter.py:54` |

Two things did **not** land and are deliberately still open:

- `SessionAction.CREATE` is unimplemented for all three adapters. Each
  `plan_invocation()` branches only on `RESUME`, so `CREATE` falls
  through the `else` and plans an ordinary no-session invocation.
- The gate is **action-agnostic**. It tests only `session is not None`
  (`application/workflows.py:585-590`) and never inspects
  `request.requested_session_action`, so a `CREATE` request carrying a
  non-null session passes the gate and silently produces a no-session
  invocation instead of being rejected. This is a defect in 2a that
  2b/2c/2d's CREATE-deferral choices surfaced; it is tracked in
  Section 5 under increment 2.

Threading a session from a real caller also remains open -- see
Section 1.2 gap 4.

### 2.6 Streaming and tool-call surface

> **Superseded as designed.** This section originally prescribed adding
> a `session_id: str | None = None` field to `DecodedOutput`. That field
> was never implemented and should not be. Increment 2c found that the
> already-existing `DecoderEventKind.SESSION_IDENTITY` reaches the
> caller through the existing `DecodedOutput.events` ->
> `ExecutionWorkflowResult.decoded_output` path, so a second parallel
> channel for the same fact would have been redundant. Codex and Agy
> emit `SESSION_IDENTITY` with a single-key `{"session_id": <id>}`
> payload (`f4b2907`, `c3d6ceb`); `DecodedOutput` keeps its three-field
> shape (`adapters/contract.py:553-563`).

**Why Claude never emits `SESSION_IDENTITY` -- permanent, not a gap.**
Codex and Agy mint a session ID server-side during the run, so the only
way to learn it is to capture it out of the output after the fact.
Claude's is the opposite: `claude.cmd --session-id <uuid>` accepts a
caller-chosen UUID for the conversation (`[cli_live]`, verified against
`claude.cmd --help` 2026-08-13), so whenever `CREATE` is implemented for
Claude the ID will already be known to the caller before the process
starts. There is nothing for `ClaudeOutputDecoder` to discover. This is
an architectural asymmetry between the vendors, not unfinished work --
do not "complete" Claude's decoder by making it re-parse an ID the
caller supplied.

`DecoderEventKind` adds `TOOL_CALL`. **IMPLEMENTED (increment 4).** Added to
`DecoderEventKind` and emitted by `CodexOutputDecoder`. The existing
`DecoderEvent.payload: Mapping[str, JsonValue]` carries the vendor-
normalized call name and arguments; execution semantics remain deferred.

**Why Claude and Agy never emit `TOOL_CALL` -- until their invocation formats change and are measured.**
Agy and Claude's `--output-format json` mode never exposes tool-call events at all
(only the final response text and metadata), confirmed via live invocation with a
tool-triggering prompt. They do expose tool calls via `stream-json` mode, but that
is not the mode currently used. There is nothing to normalize for these two adapters
in their current invocation mode. This increment is Codex-only until their invocation
formats change and are measured.

**Codex `item.completed` conflation.**
Codex's `item.completed` event carries both the original call arguments AND the
result data (`aggregated_output`, `exit_code`, `status`) in the same payload.
The normalized `TOOL_CALL` event is constructed by stripping the result fields
from `item.completed` (only `item.completed` is used to avoid emitting the
same logical call twice) rather than re-emitting the whole `item` payload verbatim.

**IMPLEMENTED (increment 3, `dfde073`).** No `OutputDecoder` or
`PeerAdapter` signature change was needed. `dispatch_and_execute()` now
accepts an optional ordered event sink and `pipe.py` carries
channel/sequence/timestamp-tagged internal process chunks. The pipe runner
serializes both reader threads through one consumer before the workflow
calls the mutable decoder, maps the runner-owned channel to the adapter's
`OutputChannel`, and never calls one decoder concurrently.

Codex now parses JSONL incrementally with remainder buffering and emits
events before process exit, so only its descriptor advertises
`Capability.STREAM`. Claude and Agy remain terminal-only until their
invocation formats change and are measured.

---

## 3. Failure corpus and mapping scope

The ratification evidence is the failure distribution below, not the
ever-growing absolute corpus size. A point-in-time recount at
`2026-08-12T11:56:23+09:00` found 2,515 valid records, 318 failures, and
zero malformed rows in `P:\.ai\ask_history.jsonl` (`[empirical_probe]`).
The round-2 reviewer later observed 2,516 total records while confirming
an exact match for the failure distribution, including the corrected
`lease_expired=9` (`[empirical_probe:round-2-review]`). New ratification
asks can change the absolute total without invalidating this snapshot.

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

> **Drift note (2026-08-12 P:↔peerhub gap analysis).** This section
> models hub.py's `_classify_ask_failure`/`_TRANSIENT_REASONS`
> vocabulary as of this design's drafting. hub.py commit `b878450`
> (landed after this draft) added `"timeout"` and `"lease_expired"`
> to `_TRANSIENT_REASONS` explicitly, so exact provider-side phrases
> now route to profile-scoped health instead of a peer-wide quarantine
> -- a real, unincorporated behavior change. This doesn't invalidate the
> mapping above (PeerHub's own `PROCESS_TIMEOUT`/`LEASE_EXPIRED` codes
> already exist independently of hub.py's transient-set membership), but
> whoever implements the classification-plumbing increment should
> re-check hub.py's current `_TRANSIENT_REASONS` for any further
> additions before treating this table as final.

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
3. legacy JSON without the new keys remains explicitly unknown and is
   never interpreted as retry-safe, whether the implementation selects a
   codec/version-presence marker or reconciliation plus fail-closed;
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
   fixtures. In the same increment, all three real decoders add
   `VENDOR_ERROR` emission from structured output and fixture-backed
   patterns in the existing merged terminal stream, including invalid
   session and invalid invocation-plan evidence; the two new codes
   therefore have a production path from the first increment that
   defines them.
   **DONE** -- `bfdd8b2` (1a, data layer) and `bf9f4ad` (1b,
   `VENDOR_ERROR` emission), with two post-hoc corrections below.
   *(Note: 1b's pattern-matching logic and the central mapper were
   implemented and unit-tested against synthetic fixtures. That
   synthetic-evidence gap is now partly closed: `3b317f0` replaced the
   guessed shapes with real `[cli_live]` captures for Codex's flat
   `type=="error"` and `turn.failed` cases and Agy's stderr-preamble
   case. Agy's top-level string-`error` path could not be reproduced
   live and remains marked `TEST NEEDED` per DIR-004.)*
   *(Note: `classify_attempt_failure()` shipped in 1a with zero
   production call sites and stayed that way through 1b and all of
   increment 2 -- every real dispatched attempt recorded
   `terminal_classification=None` and `failure_classification=None`, so
   `SESSION_INVALID` and `INVOCATION_PLAN_REJECTED` were unreachable in
   practice despite 1b having built their decoder path. Fixed in
   `858aec6`. Three independent review rounds tested the mapper and the
   codec in isolation and none checked the production call site; that
   is the reusable lesson, not the one-line fix.)*
   *(Open: an unrecognized-but-well-formed vendor `error_type` still
   yields a generic `INTERNAL_ERROR`. All three adapters' vendor-error
   handling is a closed `if/elif` chain with no `else`
   (`claude_adapter.py:108-115`, `codex_adapter.py:128-153`,
   `agy_adapter.py:120-129`), so a vendor error the chain does not
   recognize emits no `VENDOR_ERROR` event at all and the mapper falls
   back to `EXIT_NON_ZERO` -> `INTERNAL_ERROR`. Still true after
   `3b317f0`, which added more branches but no fallback arm. Note that
   a catch-all arm would need care: `_normalized_vendor_kind()` only
   consumes allowlisted `normalized_kind` values, so an honest fallback
   means an explicit "recognized as a vendor error, unclassifiable"
   signal, not an invented category.)*
   *(Open: Codex's two failure branches read the message from different
   places. The `type=="error"` branch reads top-level
   `parsed.get("message")` (`codex_adapter.py:126`) while the
   `turn.failed` branch reads nested `parsed["error"]["message"]`
   (`codex_adapter.py:143-145`). Both shapes are `[cli_live]`-observed
   as written, but if Codex ever nests the message on the error branch
   too, that branch's substring classification would silently see an
   empty string and emit nothing. Found during `3b317f0`'s own final
   verification; not reproduced live, no fix applied.)*
2. **Session continuation:** add/thread the optional workflow parameter,
   add the workflow-owned typed capability gate, implement the three
   adapter-specific resume plans, extract final session IDs, and
   advertise `Capability.SESSION` only where proven.
   **DONE** -- `f516760` (2a, gate), `dda4956` (2b, Claude),
   `f4b2907` (2c, Codex), `c3d6ceb` (2d, Agy). See the as-built table
   in Section 2.5.
   *(Open: 2a's capability gate is action-agnostic. It checks only
   `session is not None`, never `requested_session_action`
   (`application/workflows.py:585-590`), so a `SessionAction.CREATE`
   request with a non-null session now passes the gate and silently
   constructs a no-session invocation instead of being rejected. The
   hole is newly reachable rather than pre-existing: before 2a the
   workflow hardcoded `session=None`, so no caller could express this
   combination at all. This is a defect in 2a, surfaced by 2b/2c/2d each
   choosing to defer CREATE. The natural fix lands with whatever
   increment first implements CREATE for any adapter, since that is
   when the gate has to distinguish the two actions anyway.)*
   *(Open: no caller requests a session yet -- `direct_ask.py` still
   hardcodes `SessionAction.NONE`; Section 1.2 gap 4.)*
   *(Open, source comment: the explanatory comment on Claude's
   non-RESUME branch (`claude_adapter.py:168-173`) was accurate when 2b
   wrote it and has since gone stale in three ways. It says
   `SESSION_IDENTITY` "exists and is unused" -- 2c and 2d now emit it;
   it defers Claude's CREATE-path ID capture to "the increment that
   implements Section 2.6 (`DecodedOutput.session_id` + decoder
   emission)", but that field was abandoned (Section 2.6); and it frames
   Claude's CREATE-path capture as deferred work when Claude needs no
   post-hoc capture at any point, because `--session-id <uuid>` is
   caller-supplied. The comment should be rewritten to state the
   permanent asymmetry instead of a deferral. Not fixed here because
   this pass is documentation-only.)*
3. **Codex streaming:** add the ordered runner-to-decoder event path and
   implement incremental JSONL parsing with remainder buffering.
   **IMPLEMENTED** -- `dfde073`; Codex alone advertises
   `Capability.STREAM`, while Claude and Agy remain terminal-only.
4. **Tool-call capture:** add normalized `TOOL_CALL` events and measured
   per-CLI fixtures; execution/approval semantics remain a later design.
   **IMPLEMENTED** -- `TOOL_CALL` added and emitted by Codex. (Note: capture is
   currently scoped to `command_execution` items; other Codex tool-call item types
   like `file_change` are not yet normalized, a natural extension for a later pass).
   Claude and Agy hide tool-call events until their invocation formats change and
   are measured, so this is Codex-only.
5. **Outer retry/resume/failover loop:** centrally adjudicate
   `RetryDisposition`, call the existing retry workflow, bound attempts,
   and return a structured multi-attempt result. **NOT STARTED** --
   `authorize_retry()` still has no caller after a failed attempt.

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

## 6. Review record

### 6.1 Round 1 (draft 1 to draft 2)

Reviewer: `cc.deepthink`. Direction approved; two type/signature defects
were blocking. Both blockers were independently confirmed against the
source before revision.

| ID | Finding | Disposition in draft 2 |
|---|---|---|
| B | `AskResult.error_detail: ErrorDetail` would force the one-attempt layer to provide required `RetryDisposition` | **Fixed with option B(i).** `AttemptFailureClassification` has only code, phase, and operational category. Neither it nor `AskResult` can carry a disposition (Section 2.1) |
| D | Proposed process codes duplicated centrally computed `TerminalClassification` and discarded existing evidence | **Fixed.** Surface the existing enum, relocate/re-export it to avoid a circular import, and define one central mapper. Withdraw both duplicate codes (Sections 2.2-2.3) |
| A | Measured corpus count was incomplete and omitted 84 governance failures | **Fixed and freshly recounted.** Section 3 lists every category and treats the absolute total as a point-in-time snapshot rather than a ratification invariant |
| E-1 | Nonzero mapping changes current adapter behavior, so the design is not purely additive | **Fixed.** Section 1.4 and 2.4 explicitly require all three adapters to stop writing `INTERNAL_ERROR` as protocol failure solely for nonzero exit and record split-era persistence semantics |
| E-2 | Session continuation omitted adapter rejection, the workflow's hardcoded `session=None`, public signature impact, and empty capabilities | **Fixed.** All four concrete gaps and the optional public workflow parameter are listed in Sections 1.2 and 2.5 |
| F | Hand-written `AskResult` codec creates legacy-`None` ambiguity | **Recorded.** Section 4 names both codec directions, the ambiguity, and the fail-closed requirement before retry history is consumed |
| Q2 | `model_operand_invalid` cannot be distinguished from a transient generic nonzero exit | **Fixed.** Add only `INVOCATION_PLAN_REJECTED` and `SESSION_INVALID`; identical-plan rejection semantics, evidence requirements, and the increment-1 decoder path are explicit (Sections 2.3, 3, and 5) |
| Q2 gap | `rate_or_session_limit` conflates distinct conditions | **Recorded as `TEST NEEDED`.** No specific category is assigned without invocation-correlated evidence (Section 3) |
| Q5a | Deferring the loop contradicted making full `ErrorDetail` mandatory now | **Fixed by B(i).** The ratified surface is disposition-free, so the loop and its adjudication signature can remain a later increment (Sections 2.1 and 5) |
| Q5b | All-adapter fixture coverage is required when adapter protocol behavior changes | **Adopted.** Fast recorded-byte tests for all three adapters are mandatory with classification plumbing; only slow live tests remain deferred (Section 4) |
| Q1 | Retry authority must remain central with no adapter exception | **Settled as proposed**, now enforced by the type split and adapter protocol (Sections 2.1-2.3) |
| Q3 | Codex-only initial streaming scope | **Settled as proposed.** Codex emits JSONL; Claude/Agy are invoked in terminal-only JSON modes (Sections 1.3 and 2.6) |

No finding was silently dropped. The material change from draft 1 is
that failure **classification** and retry **adjudication** are now
different types produced in different increments. That separation is
the central invariant draft 1 stated in prose but failed to encode.

### 6.2 Round 2 (draft 2 to draft 3)

Reviewer: `cc.deepthink`. Verdict: ready for ratification conditional on
one blocking reachability correction, with four small non-blocking
corrections. Source inspection independently confirmed that the three
real decoders currently emit only `ASSISTANT_TEXT`.

| ID | Finding | Disposition in draft 3 |
|---|---|---|
| B2 | `SESSION_INVALID` and `INVOCATION_PLAN_REJECTED` were unreachable because no ratified increment made a real decoder emit `VENDOR_ERROR` | **Fixed with option (a).** Classification increment 1 now includes `VENDOR_ERROR` emission from structured vendor output and fixture-backed patterns over the existing merged terminal stream in all three real decoders, explicitly including the stderr-originating `model_operand_invalid` bytes. The central mapper alone converts the normalized event to a stable code (Sections 2.3, 2.4, and 5) |
| N1 | The mapper was total over the enum but not over its declared optional terminal-classification input | **Fixed.** `None` plus a protocol failure maps to that code at `ASSESSMENT`; `None` without a protocol failure returns `None`. Terminal rows take precedence when both signals exist (Section 2.3) |
| N2 | The design overstated adapter isolation because free-form decoder payloads can steer classification | **Fixed.** The text now calls this central normalization discipline backed by allowlists and cross-adapter fixtures, not an impossible-to-forge type boundary (Section 2.3) |
| N3 | Compatibility test 3 depended on one of two persistence alternatives that the design had not selected | **Fixed.** The test now asserts only their common safety property: legacy absence remains unknown and is never interpreted as retry-safe (Section 4) |
| N4 | The non-null-session capability rejection had no assigned owner and current adapters raise bare `ValueError` | **Fixed.** The workflow checks `Capability.SESSION` before planning and raises typed `UnsupportedCapabilityError`; adapters receive a session only after the gate passes (Sections 2.5 and 5) |
| C | The absolute ask-history count changes during ratification | **Fixed editorially.** Section 3 makes the failure distribution the evidence, retains the absolute count only as a timestamped snapshot, and records the round-2 exact-match recount |
| P | A small classifier prototype was preferred over another paper-only round | **Completed.** Section 7 identifies the design-validation step that has since been superseded by and folded into the production `classify_attempt_failure()` mapper and `tests/unit/dispatch/test_model.py` |

No round-2 finding was silently dropped. Option (a) is selected because
Q2 exists specifically to distinguish a deterministic invalid invocation
now; defining the codes as unreachable until later would preserve the
defect while labeling it fixed.

---

## 7. Design-validation step

This section records a design-validation step that has since been
superseded by and folded into the production `classify_attempt_failure()`
mapper and `tests/unit/dispatch/test_model.py`. This section previously
noted that `classify_attempt_failure()` had zero production call sites
and called that expected at that stage of the increment. It was not
expected -- it was the gap `858aec6` later fixed; the mapper is now
called from `application/workflows.py:899-904`. The historical record of
what the prototype validated (the empirical probe's conclusion) is
preserved below.

The deleted prototype module
validated an isolated pure implementation of the ratified mapper shape using
production contracts plus a local enum for the proposed codes. It verified
all five terminal rows, both `None` branches, both new-code refinements,
an operational-category refinement, and negative cases proving that
assistant text or an unnormalized vendor payload cannot trigger a stable
refinement. The evidence fixtures used the same allowlisted
`normalized_kind` and `evidence_source` schema specified in Section 2.3.

Focused execution produced **13 passed**, and focused Pyright validation
produced **0 errors, 0 warnings** (`[empirical_probe]`).

The validation step proved that the mapping is implementable and total and
that normalized `VENDOR_ERROR` evidence reaches both proposed codes.

---

## 8. Closing ratification assessment

Draft 3 passed the closing independent ratification check and was
ratified as `d267750`. The one blocking contradiction is closed in the
same increment that introduces the new codes; the mapper covers its full
optional input domain; decoder trust is described at the level actually
enforced; the legacy test has one implementation-independent safety
assertion; and session capability rejection has a named workflow owner
and typed failure.

No round 4 was warranted on the evidence available then, and none is
warranted now: the surface held up in implementation. What implementation
did expose is not shared-contract defects but integration and
evidence-quality gaps that paper review structurally cannot find -- a
mapper nobody called (`858aec6`) and vendor patterns that had never met a
real failure (`3b317f0`). Both are recorded in Section 5's increment
notes rather than smoothed over here.
