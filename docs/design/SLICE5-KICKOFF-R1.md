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

## artifacts.py/completion.py contract RATIFIED (2026-08-03, ag+cx unanimous)

Unblocks tasks #17/#18. Reached via a structured multi-round dialectic
(design proposal -> flag disagreement -> reconcile -> synthesize),
dispatched to ag.effort and cx.effort as independent voices throughout.
Full transcript excerpts kept in this session's memory
(`project_t91_resolved_2026_08_02.md`'s sibling notes); this section is
the durable, implementation-facing summary.

**`workspace_scope` typing citation -- resolved, unanimous, round 1:**
`ARCHITECTURE.md` line 256's `WorkspaceScope | GlobalScope` citation is
the coordinator-level `CommandEnvelope.scope` routing discriminator
(global vs. workspace-targeted IPC commands), a different concept from
`AdapterRequest.workspace_scope` (an already-routed, per-request scope
identifier) and `workspace_scope_id` (a persistence-layer string key).
**`AdapterRequest.workspace_scope` stays `str`.** Do not introduce the
coordinator `WorkspaceScope` type into adapters or artifact path
resolution.

**`assess_completion()` DELIVERY_ONLY/VERIFIED semantics -- resolved,
unanimous, round 3 (of 3):** A first round produced a straight 1-1 split
(can `DELIVERY_ONLY` reach `VERIFIED`, or does it always cap at
`UNVERIFIED`?). A naive "show peer B's argument to peer A" reconciliation
round produced a worthless result: **both reviewers flipped to the
opposite of their own round-1 position**, converging on nothing --
evidence this shows anchoring/deference to whichever argument is framed
as "the other reviewer said," not genuine reasoning from merits. Round 3
fixed the method: both arguments were shown to both peers simultaneously
and unattributed, with an explicit instruction to steelman-then-rebut the
losing side. That produced real, stable, reasoned convergence:

- **`DELIVERY_ONLY` CAN return `VERIFIED`** when the response is present
  and not truncated -- `VERIFIED` means "the declared contract's
  requirements were satisfied," and a zero-requirement contract that
  delivered is fully satisfied, not unproven.
- This is safe **only** if enforced structurally, not by convention:
  - `contract_kind` is a **required, non-nullable field** on
    `CompletionAssessment` (including at every serialization/IPC
    boundary -- round-trip tests must prove it's never dropped).
  - **No bare `state == VERIFIED` checks** anywhere outside the
    assessment module -- expose contract-aware predicates instead (e.g.
    an exhaustive `is_promotion_eligible(assessment)`), not a generic
    `is_verified`.
  - **Exhaustiveness tests** must fail when a new `contract_kind` is
    added without updating every promotion-logic and telemetry call
    site.
  - **Telemetry/aggregation must segment by `contract_kind`** --
    verification-rate dashboards must never aggregate `VERIFIED` counts
    across heterogeneous contract kinds (a `DELIVERY_ONLY` success and an
    `ARTIFACT_REQUIRED` success are not the same claim).
  - Without these guarantees, treat this as **not yet safe to ship** --
    the type signal reverts to erasable-by-convention, at which point the
    always-`UNVERIFIED` fallback is the safer default.

**`resolve_workspace_paths` / `generate_materialization_manifest`
signatures -- resolved, unanimous, final merge round:**

```python
@dataclass(frozen=True)
class WorkspacePaths:
    workspace_root: Path
    staging_dir: Path
    scope_id: str

def resolve_workspace_paths(
    request: AdapterRequest,
    plan: InvocationPlan,
    *,
    workspace_roots: Mapping[str, Path],
    artifact_staging_relative_root: Path = Path(".artifacts/staging"),
) -> WorkspacePaths: ...

def generate_materialization_manifest(
    plan: InvocationPlan,
    workspace: WorkspacePaths,
    *,
    attempt_id: str,
    artifacts: Sequence[ArtifactSpec] = (),
) -> MaterializationManifest: ...
```

Two safety mechanisms adopted, both non-stylistic per unanimous
agreement (a caller-resolved-root design was considered and rejected as
strictly weaker unless the caller's resolver is itself an equally
trusted, validated boundary -- simpler to make the containment
structural instead):

1. **`workspace_roots: Mapping[str, Path]` opaque lookup**, resolved only
   inside `resolve_workspace_paths`, unknown scopes rejected. A
   caller-controlled scope string is never used directly as/in a
   filesystem path -- it's purely a lookup key into host-trusted config.
2. **SHA-256-hashed physical staging filenames** (digest of stable
   identity, e.g. `attempt_id`/`artifact_id`), never a raw externally
   controlled ID or filename as a physical path segment -- eliminates the
   injection surface for *staging targets* rather than attempting to
   validate it. Artifact *source* paths still need real validation
   (reject absolute paths, traversal, disallowed separators, and require
   final resolution to stay beneath the intended root) since those
   aren't hash-generated.

`completion.py`'s `assess_completion` composes with the existing
`CompletionContract`/`ExecutionOutcome`/`ProtocolAssessment` DTOs (see
dispatch/contract.py); no new requirement grammar -- an
artifact/schema/field/custom-verifier/vendor-receipt validator produces
one `RequirementEvaluation` per frozen requirement, and the pure reducer
verifies complete, non-duplicated index coverage and aggregates the
result per the state table above.

**Process lesson for future ambiguous multi-peer design rounds:**
a reconciliation round that shows peer A only "peer B disagreed with
you" is worthless -- it measures anchoring, not correctness. Show both
arguments simultaneously, unattributed, with an explicit
steelman-then-rebut instruction, before trusting any "reconciled"
answer as real consensus.

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
  same 6 Step-5-only failures). cx.effort's scoped review of this diff then
  caught one more real test gap (a caller passing
  `process_identity_matches=True` alongside a null identity must still hit
  the new branch, not silently fall through to `FENCE_AND_CLOSE`) --
  `test_recovery_never_spawned_takes_precedence_over_stale_identity_match_flag`
  added to lock that in. Final committed state: **182 passed**, same 6
  Step-5-only failures (commit `14398b3`; corrected here 2026-08-02 after
  cx.deepthink's final cross-repo sanity pass caught this section still
  saying 181/one test post-commit).
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

## DP-06 outbox writes shipped (2026-08-02, third pass)

Dispatched to cx.deepthink to root-cause the exact breakage. Finding:
**no double-counting, no projector bug** -- `TelemetryProjector`
intentionally checkpoints every canonical outbox event, not just
terminal ones, so the normal lifecycle legitimately grows from 3 events
(`ADMITTED`, `SUCCEEDED_VERIFIED`, `AttemptTerminalObserved`) to 5
(adding `DISPATCH_INTENT`, `RUNNING`). The 3 tests' exact-count
assertions were simply written before this durable-journal requirement
existed and needed updating, not evidence the outbox-write approach was
wrong. Same service.py diff as the reverted attempt (re-applied,
identical), plus: `test_full_request_attempt_lifecycle_round_trips`
updated to expect 5 events including the 2 new kinds; a new
`START_UNCERTAIN`-journal assertion added to
`test_request_attempt_and_lease_cas_reject_stale_snapshots` (that path
wasn't covered by the first test); both `test_telemetry_feedback_kernel.py`
projector tests' `3` -> `5`. cx verified its own diff against an in-memory
SQLite harness (its sandbox couldn't run real pytest) before returning
it; cc re-verified against the real suite after applying: **182 passed,
6 known Step-5 failures, no new breakage** -- matches cx's own predicted
outcome exactly.

This closes the DP-06 durable-journal item. Still blocked, unchanged:
`dispatch/artifacts.py`, `dispatch/completion.py` (no test oracle for
either), and the `workspace_scope` typing answer from the second pass
(plausible, not independently re-verified line-by-line).

## Step 4 persistence contract RATIFIED (2026-08-03, ag+cx unanimous)

Unblocks the next slice: migration `0008` + `sqlite.py` artifact
repositories. `dispatch/artifacts.py` and `dispatch/completion.py`
landed earlier the same night (see the "contract RATIFIED" section
above); this closes the persistence gap cx's own overnight planning
pass flagged as genuinely design-round-worthy, not implementable by
inference. Reached via 2 rounds: independent parallel proposals, then
one targeted reconciliation on the single material disagreement found
(both peers explicitly agreed this did NOT need a full multi-round
dialectic -- correctly, per [[feedback_naive_reconciliation_causes_anchoring_flip]]'s
"don't manufacture ceremony where it isn't warranted" spirit; a single
targeted round was sufficient because the two proposals converged on
everything except one genuinely material point).

**Convergence, round 1 (no reconciliation needed):** both independently
proposed the same journal architecture -- `outbox_events` remains the
single, sole append-only DP-06 event journal (no second/parallel
journal table); a NEW artifact-metadata table (or table pair) holds
CAS-able current state; the outbox payload at `DISPATCH_INTENT` gains
artifact-recovery fields (manifest/artifact digest, `completion_contract_kind`
since that's now mandatory per the completion.py ratification above).

**Table shape -- adopt cx's two-table split** (manifest-level +
item-level), not ag's single flat table -- strictly more expressive,
no identified downside:
- `dispatch_artifact_manifests`: one row per attempt, PK `attempt_id`,
  FK to `dispatch_attempts`. Fields: `workspace_scope_id`,
  `staging_root_ref` (relative, trusted-config-derived -- never an
  absolute path), `manifest_digest` (SHA-256 over canonical JSON of
  the immutable manifest facts), `item_count`, `intent_event_id`
  (nullable, set only once `DISPATCH_INTENT` commits), `created_at`,
  `consumed_at`, `revision`.
- `dispatch_artifacts`: one row per artifact item, PK
  `(attempt_id, artifact_id)`, FK to the manifest row. Fields:
  `placeholder`, `staging_ref` (relative hashed target -- never an
  absolute `Path`, per the artifacts.py ratification's hashed-filename
  mechanism), `access_mode`, `declared_lifecycle`, `expected_sha256_hex`,
  `expected_length`, `verified_sha256_hex`/`verified_length` (nullable
  until verification), `verified_object_identity_json` (materializer-owned
  immutable descriptor, supports same-object checking -- not a
  substitute for the runner's live verified handle), `state`,
  `failure_code`, `declared_at`/`staged_at`/`verified_at`/`consumed_at`/
  `cleaned_at` (nullable timestamps), `revision`.
- Constraints: `UNIQUE (attempt_id, placeholder)`,
  `UNIQUE (workspace_scope_id, staging_ref)`, index `(attempt_id, state)`.
- The persisted staging reference is relative only -- absolute
  workspace paths, source paths, raw `content_bytes`, and live handles
  are never persisted (matches artifacts.py's pure-manifest boundary:
  this table stores facts *about* materialization, not materialization
  I/O itself).
- Migration file: `peerhub/persistence/migrations/0008_dispatch_artifact_metadata.sql`
  -- confirmed against HEAD by both peers independently as the correct
  next number (`SqliteStateStore.initialize()` currently stops at `0007`).
  **Re-confirm this number against HEAD immediately before implementing**
  if any other migration has landed in the meantime.

**Lifecycle states -- merged, 3-state commitment chain, unanimous after
reconciliation:** `VERIFIED -> RESERVED -> CONSUMED` (plus the
already-existing pre-verification states `DECLARED`/`STAGED`, and
`ORPHANED`/`CLEANED` for the crash/cleanup tail -- adopt cx's fuller
6+-state enum from its first-round proposal, with `RESERVED` inserted
between `VERIFIED` and `CONSUMED`).

This 3-state chain is the one point where round 1 produced a real,
material disagreement (not a naming difference): ag's first-round
proposal transitioned `VERIFIED -> CONSUMED` directly at **attempt
completion** (post-execution); cx's first-round proposal transitioned
`VERIFIED -> CONSUMED` directly at **`DISPATCH_INTENT`** (pre-spawn,
before the process even runs). The reconciliation round converged both
peers onto a third option neither had originally proposed -- genuine
synthesis, not deference:

- **`VERIFIED -> RESERVED`**: CAS transition atomically paired with
  inserting the `DISPATCH_INTENT` outbox event, *before* the provider
  process is spawned. Closes ag's identified gap in cx's original
  design (a duplicate dispatcher could otherwise claim the same
  artifacts) and closes cx's identified gap in ag's original design
  (artifacts sitting in `VERIFIED` for the entire attempt duration is
  ambiguous between "unclaimed" and "claimed by an in-flight or
  crashed process").
- **`RESERVED -> CONSUMED`**: CAS transition atomically paired with the
  attempt reaching a durable terminal outcome (completion or a
  determined-failed/determined-not-started state), alongside the
  terminal outbox event.
- **Physical deletion** (`CLEANED`) happens async, only ever for
  `CONSUMED` artifacts -- never for `VERIFIED` or `RESERVED`, closing
  ag's identified cleanup-hazard (an async GC sweep must never delete
  files a still-running or still-unresolved process might read).
- **Recovery rule (cx's addition, not in either original proposal):**
  **do not** automatically revert a crashed `RESERVED` back to
  `VERIFIED` -- spawn may already have happened. Reconcile the durable
  attempt/worker outcome first (via the existing DP-06 recovery
  machinery); only a *proven*-not-started attempt may release the
  reservation back to `VERIFIED`.

**Repository surface for `peerhub/persistence/sqlite.py`** (cx's
proposal adopted, `mark_artifact_consumed`/similar renamed to match the
3-state chain):
```python
def add_artifact_manifest(
    self,
    manifest: ArtifactManifestRecord,
    artifacts: tuple[ArtifactMetadata, ...],
) -> None: ...

def get_artifact_manifest(self, attempt_id: str) -> ArtifactManifestRecord | None: ...

def get_artifact_metadata(self, attempt_id: str, artifact_id: str) -> ArtifactMetadata | None: ...

def list_artifact_metadata(self, attempt_id: str) -> tuple[ArtifactMetadata, ...]: ...

def cas_update_artifact_metadata(
    self, current: ArtifactMetadata, updated: ArtifactMetadata,
) -> bool: ...

def reserve_verified_artifacts_for_dispatch(
    self, *, attempt_id: str, expected_manifest_digest: str,
    intent_event_id: str, reserved_at: int,
) -> bool:
    """VERIFIED -> RESERVED, all-or-nothing for the whole manifest --
    belongs in one SQL transaction, not a caller loop over
    cas_update_artifact_metadata()."""
    ...

def consume_reserved_artifacts(
    self, *, attempt_id: str, terminal_outcome_event_id: str, consumed_at: int,
) -> bool:
    """RESERVED -> CONSUMED, atomically with the attempt's terminal
    outbox event."""
    ...

def get_artifact_recovery_digest(self, attempt_id: str) -> ArtifactRecoveryDigest | None:
    """Recovery read model -- joins attempt/request, manifest, and
    ordered metadata rows; verifies a committed intent's
    intent_event_id resolves to the corresponding outbox event with
    matching digest/kind. Not another durable journal."""
    ...

def mark_artifacts_orphaned(
    self, *, attempt_id: str, expected_manifest_revision: int,
    orphaned_at: int, failure_code: str,
) -> bool: ...

def mark_artifact_cleaned(
    self, current: ArtifactMetadata, *, cleaned_at: int,
) -> bool: ...
```

**Process note:** both peers independently assessed this as needing a
design round but explicitly *not* a prolonged multi-round dialectic
("two independent proposals, one concise reconciliation only on a
material difference, then implementation" -- cx's own words), and that
assessment held: round 1 found near-total convergence plus exactly one
real disagreement, round 2 resolved it with a synthesis neither peer
had originally proposed. Contrast with the completion.py VERIFIED
question, which genuinely needed 3 rounds because the first
reconciliation attempt was methodologically broken (see
[[feedback_naive_reconciliation_causes_anchoring_flip]]) -- the lesson
there was about *how* to reconcile, not that every disagreement needs
maximal process. Match the round count to the actual complexity found,
not to a fixed ritual.

**Still open, not addressed by this round:** `ArtifactRecoveryDigest`'s
exact field-to-outbox-payload mapping is proposed but not yet
independently re-verified against the real `service.py`
`add_outbox_event` call sites; do that as part of implementation, not
by inference beforehand.

## ArtifactMaterializer contract RATIFIED (2026-08-03, ag+cx unanimous)

Unblocks the stateful I/O layer that actually performs file
materialization -- `dispatch/artifacts.py`'s pure manifest functions and
Step 4's persistence repositories (both landed earlier the same night)
compute paths and store facts, but neither one writes an actual file to
disk. Reached via round 1 (independent proposals) + one targeted
reconciliation, same pattern as the persistence round above. This round
also **found and closed a real gap in already-shipped Step 4 code**
(commit `d1f341b`) -- not just a forward-looking design decision.

**Scope boundary, unanimous:** the `ArtifactMaterializer` API is
decoupled from the Windows native PTY backend choice (`pipe.py`/`pty.py`
runner selection is a separate, still-open decision) -- the materializer
runs entirely pre-spawn and returns evidence + substituted argv; which
runner later consumes that is irrelevant to how files get staged and
verified.

**The gap cx found, ag independently verified against the real shipped
code before agreeing:** `mark_artifact_cleaned` (shipped in `d1f341b`)
enforces `WHERE state = 'CONSUMED'` -- confirmed directly against
`sqlite.py` and `test_mark_artifact_cleaned_rejects_non_consumed_artifact`.
This means an artifact that reaches `ORPHANED` (a pre-spawn
materialization failure, or a crash-recovery classification) has **no
persistence path back to `CLEANED`** -- an orphaned physical staging
file has nothing to reclaim it. Neither the original artifacts.py
ratification nor the Step 4 persistence ratification caught this because
neither one had reasoned through what happens to a *failed*
materialization's on-disk leftovers, only the happy path.

**Fix, unanimous:** add a new, narrow repository method rather than
weakening `mark_artifact_cleaned`'s existing safety guard:
```python
def reclaim_orphaned_artifact(
    self, current: ArtifactMetadata, *, cleaned_at: int,
) -> bool:
    """ORPHANED -> CLEANED, after a background GC pass has physically
    removed any leftover staging file. Deliberately separate from
    mark_artifact_cleaned (CONSUMED -> CLEANED) -- keeps the
    happy-path cleanup guard exactly as strict as Step 4 ratified it."""
    ...
```
Combined with an in-process rule: the materializer's own error handling
(a handled failure during staging/verification, not a crash) must roll
back and delete only the `.tmp.<uuid>` file *it just created itself* --
never a pre-existing or unproven file -- before ever calling
`mark_artifacts_orphaned`. This narrows how often a physical leftover
can even exist; `reclaim_orphaned_artifact` is the backstop for the
cases it can't prevent (a hard crash mid-write, before rollback can run).

**Materializer API (adopts cx's more complete proposal over ag's
simpler first draft -- ag agreed after review):**
```python
class ArtifactMaterializer:
    def __init__(
        self, *, store: StateStore, clock: Clock,
        file_identity: FileIdentityProvider,
    ) -> None: ...

    def materialize(
        self, manifest: MaterializationManifest,
    ) -> MaterializationResult: ...
```
- `MaterializationResult`: `attempt_id`, `manifest_digest`,
  `substituted_argv`, ordered per-item verified evidence (`artifact_id`,
  staging `Path`, SHA-256, byte length, canonical serialized object
  identity).
- `manifest_digest` is *derived* inside `materialize()` from only
  durable immutable facts (scope, relative staging root/ref, artifact
  id/placeholder, access/lifecycle text, expected digest/length) --
  never accepted from a caller. Absolute paths, source paths, and raw
  bytes are never persisted (same boundary as the artifacts.py
  ratification).
- State transitions use **narrow typed repository methods**, not the
  generic `cas_update_artifact_metadata`: `mark_artifact_staged`
  (`DECLARED -> STAGED`), `mark_artifact_verified` (`STAGED -> VERIFIED`,
  carries `verified_sha256_hex`/`verified_length`/
  `verified_object_identity_json`/`verified_at`), plus
  `reclaim_orphaned_artifact` above. Rationale (ag, after review):
  the generic CAS method "is overly permissive and exposes internal
  schema field mutations to domain callers" -- explicit methods keep the
  state machine encapsulated and can validate digest/size invariants
  inline.
- **Concurrent materialization** (two callers racing the same
  `attempt_id`): on a CAS loss, re-read. If the winner is `VERIFIED`
  with matching immutable facts and the on-disk file still verifies,
  return *its* successful result (idempotent, no double-write). Otherwise
  return a typed retryable conflict outcome. Do not silently retry via a
  fresh `attempt_id` (ag's original proposal) -- that discards the
  winner's already-verified work.
- **Crash-recovery rule for a target file present while metadata is
  still `DECLARED`** (left by a process that crashed mid-write, before
  ever updating metadata): reopen and verify against the manifest's
  expected digest/length. If it verifies, transition straight to
  `VERIFIED` (skip re-staging). If it doesn't, unlink the corrupt file
  and re-stage fresh. Never blindly overwrite a file that already exists
  at the target path.
- **Ownership boundary, unanimous:** the materializer owns
  `DECLARED -> STAGED -> VERIFIED` only. `VERIFIED -> RESERVED` and
  `RESERVED -> CONSUMED` belong to the dispatch service (already shipped
  in Step 4). Physical deletion (`CLEANED`) is owned exclusively by a
  separate async GC pass, never the materializer directly -- matches the
  Step 4 ratification's "physical deletion only ever for CONSUMED
  artifacts" rule, now extended to cover the `ORPHANED` case via
  `reclaim_orphaned_artifact` above.

**Failure-mode table (unanimous):**
| Failure | Outcome |
|---|---|
| `ENOSPC`/quota, transient I/O, sharing lock | Retryable; artifact stays `DECLARED`/`STAGED`, never dispatched. Retry exhaustion -> dispatch service terminalizes and orphans the attempt. |
| Permission denied / invalid staging root | Hard configuration failure, no retry. |
| Source missing or unreadable | Hard immutable-input failure. |
| Digest or length mismatch | Hard contract/integrity failure. Never reserve or launch. |
| Existing unexpected target / identity mismatch | Hard tamper/collision failure -- never delete a file whose ownership isn't proved. |
| Process crash after durable `RESERVED` | NOT a materializer concern -- existing DP-06 recovery rules apply (a crashed `RESERVED` never auto-reverts to `VERIFIED`, per the Step 4 ratification above). |

For a zero-artifact manifest, `materialize()` is a no-op success -- no
manifest row is created (the Step 4 `consume_reserved_artifacts` path
already rejects an empty artifact set, so this must stay consistent
with that, not create a phantom empty manifest).

**Process note:** ag's first-round solo assessment was "single-peer
sufficient, no remaining design ambiguities" -- cx's independent
proposal proved that wrong by finding a real gap in already-shipped
code plus four other unaddressed refinements (typed transition methods,
concurrency handling, DECLARED-with-existing-file recovery, strict
rollback scope). ag fully conceded after directly verifying the gap
against the real committed `sqlite.py`/test file, not just cx's
say-so. Lesson: a peer's own "this doesn't need reconciliation"
self-assessment is not a substitute for actually getting the second
opinion -- dispatch it anyway when in doubt, per
[[feedback_ratify_ambiguity_before_proceeding]].
