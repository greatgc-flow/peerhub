# Slice 5 Kickoff R1 — Fake-Peer Vertical Dispatch Slice (Phase 2)

Status: proposed design document, not yet ratified. Drafted by
ag.deepthink; revised once after cx.deepthink's independent ratification
review found 11 real corrections (cc verified each directly against the
repo before relaying them back for revision). Pending one more
cross-review pass before implementation begins, following the same
discipline used for every prior slice in this project.

## Process summary

The ratified scope for Slice 5 is `ARCHITECTURE.md` section 15's **Phase
2 -- "fake-peer vertical dispatch slice."** This slice was chosen over 3
other candidates (external interface, adapters, coordination) because
section 15 already ratifies this exact sequencing (Phase 2 before Phase
3 adapters, before Phase 3.5 coordination, before Phase 6 external
interface) -- ag.deepthink and cx.deepthink independently converged on
this after cx caught that ag's first-round analysis had missed section
15 entirely (2026-08-01).

This slice transitions dispatch from pure state machine reducers (built
in Slices 1-3) to real OS-level process execution. The mandate includes:
building one fake pipe and one fake PTY executable, artifact
materialization, handling incremental events (chunks/exit), deadlines,
cancellation, capturing process-birth identity, lease heartbeats, and
crash recovery. It must also prove the three-layer outcome model
(Execution, Protocol, Completion) and enforce that no model call ever
occurs under a store transaction.

## Ratified decisions

1. **Fake executables as Python scripts.** `V1-CONTROLLED-FAKE-
   CONFORMANCE-SPEC-R1.md` defines deterministic event *scripts*, not
   Python OS executables -- implementing the "fake pipe"/"fake PTY"
   runner binaries as isolated Python scripts is a new Slice 5 design
   choice layered on top of that spec, not something R1 itself
   prescribes. It guarantees real OS process boundaries (PIDs, process
   trees, actual pipes) necessary to TDD the supervisor while
   interpreting the deterministic scripts, without requiring
   cross-compiled C/Go binaries.
2. **Three-layer outcome model.** Slice 5 will fully materialize
   `AskResult`'s three layers:
   - *ExecutionOutcome*: populated by the process supervisor
     (`dispatch.process`, `dispatch.pipe`, `dispatch.pty`), which
     observes process start, `EXIT`, and timeouts (`PROCESS_TIMEOUT`,
     `SILENCE_TIMEOUT` -- `PROCESS_TIMEOUT` replaces the retired
     `HARD_TIMEOUT` term per `CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md`).
   - *ProtocolAssessment*: populated by a fake adapter structurally
     satisfying `ARCHITECTURE.md` section 6.2's `PeerAdapter` interface,
     parsing the deterministic `CHUNK` output and protocol framing only
     -- it does *not* interpret `EXIT` or timeouts, which belong to
     `ExecutionOutcome`.
   - *CompletionAssessment*: populated by a purely functional
     `dispatch.completion.CompletionAssessor`. It never directly
     inspects mutable filesystem paths (`PROTOCOL-V1-FREEZE.md` forbids
     reopening a path after verification) -- artifact I/O produces
     verified identity/digest evidence first, and this pure assessor
     consumes only that evidence against the `CompletionContract`.
3. **Transaction/execution separation & dispatch integration.**
   Execution (spawning, waiting, reading pipes) happens strictly outside
   any `unit_of_work()`. The existing dispatch state machine
   (`peerhub/dispatch/model.py`/`service.py`) imposes real preconditions
   this slice must respect, not redesign: `create_attempt()` must
   precede `record_dispatch_intent()`; terminal transitions must pass
   through `begin_assessment()` before `complete_attempt()`;
   cancellation uses `begin_cancellation()`; `START_UNCERTAIN` is
   nonterminal and carries no `AskResult`. Crash recovery is currently
   incomplete at this exact boundary: `record_start_uncertain()` leaves
   the lease `RESERVED` without process identity, which breaks the
   existing `FENCED`/`IDENTITY_MISMATCH` recovery paths (both require
   process identity); `ABANDONED_PRE_SPAWN` exists in the state enum but
   has no implemented transition. **This slice must extend
   `dispatch/model.py` and `dispatch/service.py` directly** to close
   these gaps -- workflow-only integration (`application/workflows.py`
   alone) is insufficient.
4. **Crash recovery (DP-06) boundary.** The exact boundary is not the
   SQLite commit -- it is the durable **isolated-journal append of
   `INTENT_PERSISTED`** (`CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md` section
   3). If an interruption occurs after this durable append but before
   event reduction, with no later `SPAWNED`/`EXIT`/terminal evidence,
   the attempt classifies as `START_UNCERTAIN` / `MAY_HAVE_STARTED` /
   `UNKNOWN`. Recovery additionally requires **no automatic replay** of
   an uncertain external dispatch, and **retention of the journal
   digest** -- both distinct requirements from the classification itself
   and both must be separately proven.

## Reducer set

- `peerhub/dispatch/artifacts.py`: `resolve_workspace_paths`,
  `generate_materialization_manifest` (pure functions mapping contracts
  to filesystem bounds).
- `peerhub/dispatch/completion.py`: `assess_completion` (pure evaluation
  of `ExecutionOutcome` + `ProtocolAssessment` + already-verified
  artifact digest evidence against the `CompletionContract` -- never
  reopens a filesystem path itself).
- `peerhub/dispatch/process.py`: pure state machines for OS signal
  translation, exit code normalization, and cancellation escalation
  ladders, distinguishing `PROCESS_DEADLINE`, `PROCESS_TIMEOUT`, and
  `SILENCE_TIMEOUT` (not `HARD_TIMEOUT`, which is retired).
- `peerhub/builtins/fake_adapter.py`: `interpret_output` (pure
  translation of the fake-peer deterministic script's byte output and
  protocol framing into `ProtocolAssessment`; never interprets `EXIT` or
  timeout events).

## SQLite schema

A new migration (numbered sequentially after Slice 4's `0007`) is
required for two distinct additions:

- **Artifact metadata**: durable lifecycle/identity/digest fields so a
  `VERIFIED -> CONSUMED` transition commits atomically with dispatch
  intent (per `PROTOCOL-V1-FREEZE.md`); `evidence_refs` alone is
  insufficient, and current attempt/lease rows have no artifact fields
  at all today.
- **Durable incremental-event journal**: `record_dispatch_intent()`,
  `record_running()`, and `record_start_uncertain()` must append
  durable journal records (or corresponding outbox events) establishing
  the exact DP-06 isolated-journal boundary -- this durable event design
  does not exist in the current dispatch service and is a prerequisite
  for DP-06's fault-injection proof, not an optional nicety.

## File list

- **Create**:
  - `peerhub/dispatch/process.py` (process supervision types)
  - `peerhub/dispatch/pipe.py` (concrete pipe runner)
  - `peerhub/dispatch/pty.py` (concrete PTY runner)
  - `peerhub/dispatch/artifacts.py` (artifact materialization)
  - `peerhub/dispatch/completion.py` (three-layer outcome assessor)
  - `peerhub/builtins/fake_adapter.py` (fake adapter satisfying the
    `PeerAdapter` interface)
  - `tools/fake_peer/pipe_executable.py`,
    `tools/fake_peer/pty_executable.py` (the actual OS processes)
  - `peerhub/persistence/migrations/0008_dispatch_artifacts_journal.sql`
    (exact number to be confirmed against HEAD at implementation time)
  - `tests/contract/test_fake_runner_compatibility.py` (DP-06, DT-01
    through DT-06, bound to `DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md`'s
    JSON vectors/oracles for DT-02..05, not just R1's summaries)
  - `tests/unit/dispatch/test_process.py`, `test_completion.py`,
    `test_artifacts.py`
  - `tests/integration/dispatch/test_vertical_dispatch.py` (E2E
    fake-peer slice)
- **Extend**:
  - `peerhub/dispatch/model.py` and `peerhub/dispatch/service.py` (fix
    the `START_UNCERTAIN`/`ABANDONED_PRE_SPAWN` crash-recovery gaps,
    add durable event-journal appends)
  - `peerhub/application/workflows.py` (the dispatch orchestrator loop
    bridging `dispatch.service` and the process runners)
  - `peerhub/core/execution.py` (adapter contracts, signal/deadline
    vocabulary, if anything is still missing)
  - `peerhub/persistence/sqlite.py` (repositories for artifact metadata
    and the incremental journal)

## Implementation order (TDD, 7 steps)

1. **Compatibility tests**: port the `DP-06` and `DT-01` through `DT-06`
   vectors as failing tests. For `DT-02..05`, bind to the existing JSON
   vectors and domain oracles in
   `docs/design/phase0/DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md`, not just
   `V1-CONTROLLED-FAKE-CONFORMANCE-SPEC-R1.md`'s one-line summaries.
2. **Contracts**: solidify `ExecutionOutcome`, `ProtocolAssessment`, and
   `CompletionAssessment` boundaries; formally declare the `PeerAdapter`
   interface.
3. **Pure reducers**: implement `assess_completion` (consuming only
   already-verified evidence), artifact path resolvers, and the fake
   adapter's `interpret_output`; add the missing `dispatch/model.py`
   state transitions (`ABANDONED_PRE_SPAWN`, corrected `START_UNCERTAIN`
   recovery).
4. **Migrations + repositories**: add the artifact-metadata and
   incremental-journal migration plus repository support needed for
   DP-06's isolated-journal boundary.
5. **Services**: build the OS-level process runners (`pipe.py`,
   `pty.py`, `process.py`), the artifact I/O layer (producing verified
   evidence, never re-inspecting raw paths from the pure assessor), and
   the corresponding `dispatch/service.py` extensions.
6. **Workflow integration**: update `application/workflows.py` to bridge
   `dispatch.service` and the process runners, enforcing the strict
   progression `create_attempt` -> `record_dispatch_intent` -> spawn ->
   `record_running` -> `begin_assessment` -> `complete_attempt`.
7. **Fault injection**: fault-inject the exact DP-06 boundary (durable
   isolated-journal append of `INTENT_PERSISTED`). Assert strict
   `MAY_HAVE_STARTED` classification, enforce **no automatic replay**,
   verify **journal-digest retention**, and prove zero SQLite
   transactions remain open during the blocking OS execution.

## Explicit open questions

1. **Lease heartbeating mechanism.** Should `application.workflows`'
   dispatch loop multiplex the pipe `read()` with lease heartbeat
   renewals on a single thread (e.g., non-blocking I/O or `select`), or
   does heartbeating require a dedicated background thread while the
   main thread blocks on the runner? Existing documents define renewal
   fencing and transaction separation but do not choose a threading
   model -- genuinely open.
2. **PTY-on-Windows backend.** `ARCHITECTURE.md` section 16.2 rules out
   named-pipe simulation as sufficient PTY coverage and requires a
   native Windows PTY path. The exact backend (ConPTY via `ctypes` vs. a
   `winpty` binding) remains an open implementation spike -- the fake
   Python executable itself need not implement ConPTY, but
   `dispatch.pty` must attach it to a real PTY.

## Step 2 progress (2026-08-01): process contracts shipped, adapter
## contracts genuinely blocked -- not guessed

cx.deepthink drafted Step 2 and correctly stopped short of the adapter
boundary rather than invent a shape for it (same "stop and ask rather
than guess" discipline used throughout Slice 4). cc verified the
grounding citations and applied only the unblocked portion:

**Shipped**: `peerhub/dispatch/process.py`
(`TerminalClassification`, `ProcessCleanupEvidence`,
`ProcessSupervisionOutcome`, `InterruptedAttemptRecoveryOutcome`,
`CancellationLadder`, `ProcessSupervisor` -- all method bodies raise
`NotImplementedError("implemented in Slice 5 Step 5")`, matching this
project's discipline of not skipping ahead of the current TDD step);
`recover_interrupted_attempt` stub in `dispatch/service.py`. Running
Step 1's compatibility suite now shows 6 of 7 tests correctly
progressing from `ImportError` to `NotImplementedError` (the expected
Step 2 state), full suite 160 passed / 7 failed as intended.

**Blocked, left open, not guessed** -- `peerhub/adapters/contract.py`
and `peerhub/builtins/fake_adapter.py` (the 7th test,
`test_dt02_incremental_framing_split_boundaries`, still correctly fails
with `ImportError` for this reason):

1. The full `TerminalClassification` vocabulary is explicitly listed as
   an open decision in
   `docs/design/phase0/DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md` (line
   ~112); that same document classifies both zero and nonzero exits as
   one `EXITED` value (line ~48), conflicting with this slice's test
   requiring a distinct `EXIT_NON_ZERO`. Only the 4 test-required
   members are defined for now; clean exit temporarily has no
   classification value until `EXITED` vs. `EXIT_ZERO` is ratified.
2. Step 1's tests model process identity as PID-only, but the
   authoritative production identity is PID **plus** process-creation
   time (`dispatch/contract.py` `ProcessBirthIdentity`, line ~761). The
   Step 2 stub accepts the test's PID-only signature (with an optional
   `process_creation_time` param), but Step 5 must not treat a bare PID
   as publication/fencing evidence.
3. `canonical_lines` (used by Step 1's DT-02 test) is not part of the
   real, already-shipped `ProtocolAssessment` (`dispatch/contract.py`
   line ~269, frozen to 5 specific facts per `ARCHITECTURE.md` line
   ~500). It's transcript/decoder output and belongs in a separate
   decoder-result type, not a silent expansion of `ProtocolAssessment`.
4. The `PeerAdapter` interface `ARCHITECTURE.md` line ~345 documents
   references `PromptPolicy`, `AdapterRequest`, `SessionHint`,
   `TransportLimits`, `InvocationPlan`, and `OutputDecoder` -- none of
   which are formally defined anywhere in the repository yet
   (`TransportLimits` isn't even in `core/execution.py`). Inventing
   their fields, or weakening them to `Any`, would not be a
   ratification-ready interface.
5. `ARCHITECTURE.md` line ~372 forbids `adapters` from importing
   `dispatch`-owned types -- so `FakePeerAdapter` cannot simply import
   today's `dispatch.contract.ProtocolAssessment` as a shortcut; the
   adapter-owned output shape needs its own ratified definition.

These 5 items need an explicit ratification round (a genuine
architectural decision, not a mechanical fill-in) before
`peerhub/adapters/contract.py` and `peerhub/builtins/fake_adapter.py`
can be written. Also note: `docs/design/SLICE5-KICKOFF-R1.md`'s own
status line still reads "proposed... not yet ratified" even though the
*scope* (Phase 2) is unanimously ratified -- the doc's remaining
"proposed" status refers to these unresolved *content* details, not the
scope choice.

## Adapter-boundary ratification proposals (2026-08-01) -- NOT YET
## APPLIED, code work deferred to next session

ag.deepthink and cx.deepthink were both dispatched the same 5-item
brief independently. cx's proposal is adopted as the working resolution
below (more precisely cited throughout, and its answer to item 5 avoids
a risky breaking change ag's version would have caused); ag's dissent
on item 1 is recorded since it's a genuine disagreement, not yet
tie-broken by a third read.

1. **`TerminalClassification` stays as shipped (cx; ag dissents).** cx:
   keep the 4 already-shipped members (`START_UNCERTAIN`/
   `SILENCE_TIMEOUT`/`PROCESS_TIMEOUT`/`EXIT_NON_ZERO`) unchanged --
   `DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md` line ~49 tags the older
   `EXITED` rule only `OBS` (observational), not `MUST`, and full
   enumeration is explicitly deferred (line ~112) regardless. A clean
   exit leaves `terminal_classification=None` (already legal). ag
   instead proposed renaming `EXIT_NON_ZERO` to `EXITED` to match the
   older rule -- not adopted here since it would rename an
   already-shipped, tested member for a rule the spec itself marks
   non-mandatory, but flagged for a tie-break read before Step 3.
2. **Process identity: structural enforcement via `ProcessBirthIdentity`
   (cx), not just a required field (ag).** For Step 5,
   `ProcessSupervisor.on_spawned` takes `identity: ProcessBirthIdentity`
   (the existing dispatch/contract.py type, already requiring PID +
   creation time) rather than a bare `pid: int` with a required
   `process_creation_time`. A separate fake-script identity-token API
   (if needed for the deterministic test executables) must be named
   distinctly and must never be treated as lease-fencing evidence.
   Both peers agree PID-only can never be real production spawn
   evidence.
3. **New `DecodedOutput`/`DecoderEvent` types own `canonical_lines`,
   distinct from `ProtocolAssessment`.** `ProtocolAssessment` stays
   frozen to its existing 5 protocol facts (dispatch/contract.py line
   ~269). New types per `ARCHITECTURE.md` line ~380's `OutputDecoder`
   evidence categories: `DecoderEventKind` (enum:
   `PROGRESS`/`ASSISTANT_TEXT`/`SESSION_IDENTITY`/`USAGE_HINT`/
   `VENDOR_ERROR`/`COMPLETION_MARKER`), `DecoderEvent` (kind + payload
   mapping), `DecodedOutput` (canonical_text, canonical_lines,
   events tuple). Step 1's DT-02 test must be updated to call a
   `finalize_decoded_output()` method returning `DecodedOutput`, not
   `finalize_protocol_assessment()` -- keeping the old method name while
   returning decoder data would repeat the original category error.
4. **Minimal real shapes for all 6 `PeerAdapter`-referenced types**
   (cx's proposal is adopted in full -- more complete than ag's
   partial-defer answer): `PromptPolicy` (policy_id,
   max_inline_utf8_bytes, artifact_reference_supported);
   `SessionAction` enum (`NONE`/`CREATE`/`RESUME`) +
   `CompletionContractView` (a structural `Protocol` carrying just
   `contract_id`, letting `adapters.contract` type `AdapterRequest`
   without importing `dispatch`) + `AdapterRequest` (request_id, exactly
   one of prompt_content/prompt_reference, workspace_scope, profile_id,
   requested_session_action, completion_contract); `SessionHint`
   (external_session_id, adapter_fingerprint, session_generation --
   the fake adapter rejects non-`None` hints, since session support is
   optional per `ARCHITECTURE.md` line ~382); `TransportKind` enum
   (`PIPE`/`PTY`) + `TransportLimits` (process_timeout_ms,
   silence_timeout_ms, max_output_bytes -- relative budgets, the runner
   converts to absolute deadlines using its injected clock);
   `InvocationPlan` (argv, cwd_reference, environment_delta, transport,
   stdin_payload, limits, redacted_display, an `artifacts` tuple of a
   new `ArtifactSpec` type, session_action) + `ArtifactSpec`
   (artifact_id, placeholder, exactly one of content_bytes/
   content_reference, sha256_hex, expected_length, access_mode,
   lifecycle); `OutputChannel` enum (`STDOUT`/`STDERR`/`PTY`) +
   `OutputDecoder` (`Protocol` with `feed(chunk, *, channel) ->
   tuple[DecoderEvent, ...]` and `finalize() -> DecodedOutput`).
   Vendor-specific graceful-cancel recipes remain deferred -- the fake
   descriptor does not declare `GRACEFUL_CANCEL`.
5. **Adapter-owned types live in a new `peerhub/adapters/contract.py`;
   `dispatch.contract` re-exports `ProtocolAssessment` unchanged rather
   than duplicating it.** `adapters.contract` owns `PeerAdapter`,
   `PeerDescriptor`/`ProfileDescriptor` (exact fields already documented
   at `ARCHITECTURE.md` line ~280), the item-4 types above, the item-3
   decoder types, and the existing 5-field `ProtocolAssessment` (moved
   there, not redefined). `peerhub/dispatch/contract.py` then does
   `from peerhub.adapters.contract import ProtocolAssessment` -- a
   re-export, so every existing `from peerhub.dispatch.contract import
   ProtocolAssessment` import across the already-shipped Slice 1-4
   codebase keeps working unchanged (same class object, not a
   parallel/drifting copy). The dependency direction is `core -> 
   adapters.contract -> dispatch.contract/service`: `ARCHITECTURE.md`
   line ~372 forbids `adapters` importing `dispatch`, but `dispatch`
   importing `adapters.contract` is intentional and expected (the
   architecture already states `dispatch.service` consumes
   `PeerAdapter`) -- only the reverse direction is prohibited, so this
   is not a cycle.

**Not yet applied (as of 2026-08-01 21:43).** This is a synthesis of
both peers' proposals, not yet written to code -- cc's own context was
too depleted this session to safely apply ~14 new types plus the
`ProtocolAssessment` re-export migration without risking a sloppy or
incomplete edit. Next session: apply this ratified shape (tie-breaking
item 1 first, e.g. via a third independent read or user call), update
Step 1's DT-02 test for the renamed decoder method, then resume Step 2
for `peerhub/adapters/contract.py` and `peerhub/builtins/fake_adapter.py`.

## Applied (2026-08-02)

Written to code: `peerhub/adapters/contract.py` (new, ~14 ratified
types + moved `ProtocolAssessment`), `peerhub/builtins/fake_adapter.py`
(new, Step 2 stub matching `dispatch/process.py`'s precedent),
`peerhub/core/execution.py` (added `TransportLimits` *and*
`TransportKind` -- see correction below), `peerhub/dispatch/contract.py`
(`ProtocolAssessment` re-export), and the DT-02 test update. Verified:
`pytest tests/` gives 160 passed / 7 failed, all 7 the intentional Step
2 placeholder `NotImplementedError`s (6 Step 5, 1 now correctly in
`fake_adapter.py` for Step 3) -- 7 of 7 compatibility tests now progress
past `ImportError` as designed.

Cross-reviewed by ag.effort and cx.effort independently before this was
considered done (this project's standard discipline). ag returned a
clean pass. cx returned **REQUEST CHANGES** with 4 real findings, all
independently verified by cc against the cited lines and fixed before
commit:

1. **High.** `FakePeerAdapter`'s descriptor declared `Capability.SESSION`
   while `SessionHint`'s own docstring says this fake rejects every
   non-null session hint -- and ARCHITECTURE.md states a descriptor
   declaring an unimplemented capability is a load-time error. Fixed:
   removed `SESSION`, descriptor now declares only `STREAM`.
2. **Medium.** The updated DT-02 test compared `DecodedOutput.canonical_lines`
   (a tuple) against a list literal -- passes only because Step 3 doesn't
   exist yet to actually populate it; would have silently failed the
   moment Step 3 landed. Fixed: expected value is now a tuple.
3. **Medium.** `TransportKind` was placed in `adapters/contract.py`, but
   ARCHITECTURE.md's own module inventory (line ~85) explicitly lists it
   alongside `TransportLimits` as a `core.execution` shared type --
   exactly the reasoning already applied to `TransportLimits` itself, just
   missed for its sibling enum. Fixed: moved to `core/execution.py`.
4. **Low.** Several fields (`TransportLimits`' three fields,
   `PromptPolicy.max_inline_utf8_bytes`, `ArtifactSpec.expected_length`)
   rejected `0` via an unratified positivity constraint -- neither doc
   requires this, and this file's own sibling `Deadline.budget_ms` already
   permits `0` for the same "budget in milliseconds" concept. Fixed: all
   five loosened to nonnegative.

No third review round was dispatched after these fixes (all four were
citation-grounded, mechanical corrections, not new design decisions) --
consistent with this project's practice of not re-litigating settled
ground after a round finds and cc fixes concrete, verifiable defects.

**Next**: Step 3 (pure reducers) -- `assess_completion`, artifact path
resolvers, and `FakePeerAdapter`'s actual `interpret_chunk`/
`finalize_decoded_output` logic (currently `NotImplementedError` stubs).

## Step 3 progress (2026-08-02): FakePeerAdapter decode shipped, artifacts/completion genuinely blocked -- not guessed

Same discipline as Step 2's own stop on the adapter boundary. Shipped:
`FakePeerAdapter.interpret_chunk`/`finalize_decoded_output` (commit
`b33ad45`) -- the only Step 3 item with a concrete, testable oracle
(DT-02). Cross-reviewed by ag.effort + cx.effort (both explicitly
profile-pinned via `--to peer.profile`, not `--agent` -- see this
session's hub.py routing-bug finding); both independently found the
same `str.splitlines()`-too-broad issue, plus cx additionally caught a
discarded-events bug. Both fixed; 180 passed / 6 failed (unchanged Step
5 placeholders) after.

**Blocked, left open, not guessed** -- `peerhub/dispatch/artifacts.py`
(`resolve_workspace_paths`, `generate_materialization_manifest`) and
`peerhub/dispatch/completion.py` (`assess_completion`):

1. Unlike DT-02, no test currently exercises either module and no vector/
   fixture gives a concrete oracle for their exact input/output shape --
   Step 1 only ported DP-06/DT-01..06 (process-lifecycle), never added
   artifact- or completion-layer compatibility tests.
2. `AdapterRequest.workspace_scope` was typed `str` in Step 2 (the
   ratified item-4 synthesis text just says "workspace_scope" with no
   type). A citation found while scoping this step --
   `ARCHITECTURE.md` line ~256, "Round 4-5 fix: `scope` is
   `WorkspaceScope | GlobalScope`, not a bare `workspace_scope`" -- may
   mean `resolve_workspace_paths` needs a real `WorkspaceScope` type as
   input, not a plain string, and/or that Step 2's `AdapterRequest` field
   itself needs revisiting. Not yet resolved: unclear whether that
   citation is about the same `workspace_scope` concept adapters use or
   a different (coordinator-command-routing) one -- needs a grounded
   read, not an assumption either way.
3. `ArtifactMaterializer` (`ARCHITECTURE.md` line ~378: "replaces
   placeholders, creates files with create-new semantics, verifies
   digest/length round trips, records ownership, deletes only after the
   supervised process tree is terminal") is named but never given a
   concrete type/method signature anywhere -- `resolve_workspace_paths`/
   `generate_materialization_manifest` are pure functions that presumably
   feed it, but the boundary between "pure manifest generation" (this
   step) and "the stateful materializer that acts on it" (unclear which
   step) isn't specified.
4. `dispatch/model.py`'s `ABANDONED_PRE_SPAWN` transition (enum value
   exists, `LeaseState`, no implemented transition) and the
   `START_UNCERTAIN` recovery-path correction are still untouched --
   `dispatch/model.py`/`service.py` are large (1334/1748 lines),
   already-shipped, already-tested core state machines the doc itself
   says "this slice must extend directly... not redesign." Needs careful
   study of the existing state machine and its tests before any edit, not
   a same-session bolt-on.

Same pattern as before: next session (or this one, budget permitting)
should ratify artifacts.py/completion.py's exact contract shape via an
ag+cx design round (mirroring how the adapter boundary itself got
ratified before Step 2 could safely write code), resolving the
`workspace_scope`/`WorkspaceScope` citation question as part of that
round, before writing pure-reducer code against an assumed shape.

## Item 1 tie-break resolution (2026-08-02, user call)

Presented to the user directly rather than dispatching a third peer
read (both options were legitimate per-precedent choices, and this is
a naming call over an already-shipped, tested member -- exactly the
kind of low-ambiguity-value, high-cost-of-another-round decision this
project's docs elsewhere flag as appropriate for a direct user call
rather than burning another ag/cx round). **Decision: cx's proposal
adopted -- `TerminalClassification` keeps its 4 shipped members
unchanged (`EXIT_NON_ZERO` stays, not renamed to `EXITED`).** Rationale
per cx, independently verified by cc against
`DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md`: the `EXITED` rule at line
~49 is tagged `OBS` (observational, established by one fixture's
`expect` block), not `MUST`; line ~112's open item 11 explicitly states
"no authoritative closed list exists" for `terminal_classification`
today, so neither name is spec-mandated, and renaming an
already-shipped, already-tested member for a non-binding rule is pure
churn risk with no correctness upside. ag's dissent (rename for
consistency with the spec's own fixture-observed term) remains a
reasonable minority view but is not adopted.

## Step 3 continued (2026-08-02, second pass): ABANDONED_PRE_SPAWN shipped, durable-journal outbox writes attempted and reverted

Dispatched to ag.deepthink with a generous budget: design+draft the 3
remaining blocked items. Result, cc-verified against the real codebase
before applying anything:

- **`workspace_scope` typing**: ag's answer -- `AdapterRequest.workspace_scope: str`
  (Step 2) is correct as shipped, no follow-up needed. ARCHITECTURE.md's
  "Round 4-5 fix: scope is `WorkspaceScope | GlobalScope`" (~line 256) is
  about coordinator command-routing scope (`CommandEnvelope.scope`), a
  different concept from an already-workspace-routed adapter request. Not
  independently re-verified line-by-line by cc this round (time budget);
  flag for a second look before treating as fully closed.
- **`dispatch/artifacts.py` / `dispatch/completion.py`**: still BLOCKED,
  same conclusion as the first pass -- `ArtifactMaterializer` has no
  concrete type anywhere, and no test/vector fixture exists for either
  module's exact shape. ag correctly declined to guess.
- **`dispatch/model.py`'s `ABANDONED_PRE_SPAWN`**: SHIPPED. Added a new
  branch to `expire_and_recover_lease()`: a lease whose
  `fence.owner_process_birth_identity is None` (never reached a
  process-identity-bearing state) now recovers as `ABANDONED_PRE_SPAWN` /
  `RecoveryDecision.MARK_INTERRUPTED` / `ExecutionCertainty.MAY_HAVE_STARTED`,
  distinct from `IDENTITY_MISMATCH` (which requires a *recorded* identity
  that then disagreed). cc verified `owner_process_birth_identity`'s
  "may be null before RUNNING" contract directly against
  `LeaseFenceTuple`'s docstring before applying, and added
  `test_recovery_never_spawned_is_abandoned_pre_spawn` (using the
  existing-but-previously-untested `reserve_lease`/`LeaseReservationRequest`
  pre-spawn constructor, not the legacy `create_lease` which always sets a
  process identity) -- new test passes, full suite unaffected (181 passed,
  same 6 Step-5-only failures).
- **DP-06 durable-journal outbox writes** (`record_dispatch_intent`/
  `record_start_uncertain`/`record_running` appending an outbox event at
  the isolated-journal boundary): ag's diff used real, already-established
  symbols (`_dispatch_event`, `unit.add_outbox_event`,
  `FaultPoint.AFTER_OUTBOX_WRITE` -- all pre-existing, verified via grep
  before applying) and looked structurally sound, but **applying it broke
  3 previously-passing tests** (`test_full_request_attempt_lifecycle_round_trips`,
  and two `test_telemetry_feedback_kernel.py` projector tests asserting
  exact event counts) -- the new outbox events shift counts these tests
  didn't expect. Reverted rather than force through a fix without properly
  understanding the projector/telemetry side's expectations first. Still
  blocked; whoever picks this up next needs to either update those 3
  tests' expected counts (if the new events are correct and those tests
  were just never written for a world where these 3 functions emit
  events) or reconsider whether all 3 functions should emit at this exact
  point.
