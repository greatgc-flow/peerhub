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
