# Slice 5 Follow-Up Work Plan & Next Steps (2026-08-04)

> **Document Status**: Concrete, skimmable planning document for the next work session.
> **Scope**: Post-Slice-5 implementation follow-up, reconciling shipped work against `docs/design/SLICE5-KICKOFF-R1.md`, detailing the remaining heartbeat-to-cancellation gap, and defining the roadmap for E2E fault-injection tests.
> **Authoritative Baseline**: Full test suite green (324 passed, 0 failed across unit and contract tests).

---

## 1. The Named Gap: Heartbeat Failure → Cancellation Ladder → TreeController Wiring

### Context & Current State
In `peerhub/application/workflows.py`, `ApplicationWorkflows.dispatch_and_execute` instantiates `HeartbeatWorker` inside the `_on_spawned` callback. However, it does not currently pass an `on_failure` callback:

```python
# peerhub/application/workflows.py (lines 679-685)
heartbeat_worker = HeartbeatWorker(
    process=proc,
    identity=identity,
    initial_lease=running_lease,
    renewer=dispatch_service,
    heartbeat_timeout_ms=heartbeat_timeout_ms,
)
```

`HeartbeatWorker` (defined in `peerhub/dispatch/heartbeat.py`, line 69) accepts:
```python
on_failure: Callable[[HeartbeatFailure], None] | None = None
```
When `HeartbeatWorker` detects a failure (e.g., `RENEWAL_FAILED`, `PROCESS_DEAD`, `HEARTBEAT_TASK_CRASH`), it invokes `self._on_failure(failure)` on its background thread (`line 213`), which currently logs the failure and sets `self._lease_owned = False`. Because `on_failure` is omitted in `workflows.py`, no signal is sent to `ProcessSupervisor.begin_cancellation()` or `TreeController`.

### Specific Functions & Methods Requiring Changes

1. **`peerhub/dispatch/heartbeat.py`**:
   - `HeartbeatWorker._record_failure(reason: str, detail: str) -> None` (line 198): Currently invokes `self._on_failure(failure)` inside a `try/except` block.
   - *Signature*: Unchanged, but caller needs to supply `on_failure: Callable[[HeartbeatFailure], None]`.

2. **`peerhub/application/workflows.py`**:
   - `ApplicationWorkflows.dispatch_and_execute(...)` (line 500):
   - Inside `_on_spawned` (line 667), construct `HeartbeatWorker` with `on_failure=_on_heartbeat_failure`.
   - Implement `_on_heartbeat_failure(failure: HeartbeatFailure)` to trigger process cancellation via the supervisor and tree controller.

3. **`peerhub/dispatch/process.py`**:
   - `ProcessSupervisor.begin_cancellation(now_ms: int = 0) -> CancellationDecision` (line 640): Starts the cancellation ladder from `IDLE` → `SOFT_CANCEL`.
   - `ProcessSupervisor.on_tree_state(*, observations, now_ms=0) -> CancellationDecision` (line 581): Steps the cancellation ladder (`SOFT_CANCEL` → `TERMINATE_TREE` → `KILL_TREE` → `RECONCILE_TREE`).
   - *Requirement*: `ProcessSupervisor` must be made thread-safe with an internal `threading.Lock` because `on_chunk()` runs on stream-reader threads, `on_exit()` runs on the main execution thread, and `begin_cancellation()` runs on the background heartbeat thread.

4. **`peerhub/dispatch/pipe.py`**:
   - `run_process(config, supervisor, *, clock_ms=None, on_spawned=None, tree_controller=None) -> ProcessSupervisionOutcome` (line 168):
   - Currently instantiates `RealTreeController` (lines 232-238) and calls `bind_spawn` to attach `_tree_handle` and `_tree_controller` to `proc`.
   - Expose active `TreeController` and `TreeHandle` to `on_spawned` callback or `supervisor` so cancellation actions (`soft_cancel`, `terminate_tree`, `kill_tree`, `observe_tree`) can be dispatched to OS processes.

5. **`peerhub/dispatch/tree_controller.py`**:
   - `RealTreeController` (line 165):
     - `soft_cancel(tree: TreeHandle) -> TreeDispatchReceipt`
     - `terminate_tree(tree: TreeHandle) -> TreeDispatchReceipt`
     - `kill_tree(tree: TreeHandle) -> TreeDispatchReceipt`
     - `observe_tree(tree: TreeHandle) -> tuple[TreeProcessObservation, ...]`
   - Implementation is complete and unit-tested in `tests/unit/dispatch/test_tree_controller.py`.

### Mini Design Decisions Flagged for Implementation

* **Decision A: Ladder Stepping Loop Ownership (Async vs Synchronous)**:
  `begin_cancellation()` returns a `CancellationDecision` containing `action` (`SOFT_CANCEL`, `TERMINATE_TREE`, etc.) and `next_deadline_ms`.
  - *Option 1*: The heartbeat thread invokes `tree_controller.soft_cancel(tree_handle)` immediately on failure detection, and a timer/poller thread steps the ladder until `decision.stage == CancellationStage.COMPLETED`.
  - *Option 2*: `pipe.run_process` main thread checks for active cancellation decisions in a loop during `proc.wait()`, driving ladder steps synchronously with time deltas.
  - *Recommendation*: Option 2 is simpler and avoids extra worker threads, as `run_process` is already blocking on process completion.

* **Decision B: Thread-Safety in `ProcessSupervisor`**:
  `ProcessSupervisor` state fields (`_cancellation_state`, `_cancellation_decision`, `_chunks`) are modified across 3 different thread contexts (background pipe readers, main thread, heartbeat worker thread). A `threading.Lock` must wrap state mutations in `ProcessSupervisor`.

---

## 2. Re-check of the Original 7-Step Implementation Order

Below is an honest reconciliation of the 7-step implementation plan from `docs/design/SLICE5-KICKOFF-R1.md` against what has actually shipped in the codebase as of tonight:

| Step | Planned Component | Status | Reality & Shipped Code Artifacts |
|---|---|---|---|
| **1** | Compatibility tests | `[GENUINELY COMPLETE]` | Ported `DP-06` and `DT-01..DT-06` vectors. All pass in `tests/contract/test_phase0_dp_dt_compatibility.py` and `test_phase0_dp_cj_compatibility.py`. |
| **2** | Contracts | `[GENUINELY COMPLETE]` | Shipped `ExecutionOutcome`, `ProtocolAssessment`, `CompletionAssessment`, `PeerAdapter`, `TreeController`, `CancellationLadder`, `CancellationGrace` in `core/execution.py`, `adapters/contract.py`, `dispatch/contract.py`, and `dispatch/process.py`. |
| **3** | Pure Reducers | `[GENUINELY COMPLETE]` | Shipped `assess_completion` (`completion.py`), `resolve_workspace_paths`/`generate_materialization_manifest` (`artifacts.py`), `interpret_chunk`/`finalize_decoded_output` (`fake_adapter.py`), `ABANDONED_PRE_SPAWN` transition (`model.py`), `recover_interrupted_attempt` (`service.py`), and `CancellationLadder` (`process.py`). |
| **4** | Migrations & Repositories | `[GENUINELY COMPLETE]` | Shipped Migration `0008_dispatch_artifact_metadata.sql`. `sqlite.py` supports manifest & item metadata tables, `reserve_verified_artifacts_for_dispatch`, `consume_reserved_artifacts`, `reclaim_orphaned_artifact`, and outbox event appends (`DISPATCH_INTENT`, `RUNNING`, `START_UNCERTAIN`). |
| **5** | Services | `[PARTIALLY COMPLETE]` | Shipped `pipe.py` (`run_process`), `materializer.py` (`ArtifactMaterializer`), `heartbeat.py` (`HeartbeatWorker`), `tree_controller.py` (`RealTreeController`), and `service.py` composite operations. Missing: `pty.py` (intentionally skipped per empirical probe) and wiring `on_failure` to cancellation ladder. |
| **6** | Workflow Integration | `[SUBSTANTIALLY COMPLETE]` | Shipped `ApplicationWorkflows.dispatch_and_execute` in `peerhub/application/workflows.py` with full unit coverage (`tests/unit/application/test_workflows_dispatch_and_execute.py`). The remaining gap is the `on_failure` cancellation callback wiring. |
| **7** | Fault Injection (E2E) | `[NOT STARTED / READY NOW]` | `tests/integration/dispatch/test_vertical_dispatch.py` is not yet created. |

---

## 3. Status of `tests/integration/dispatch/test_vertical_dispatch.py`

### Assessment: `[READY NOW FOR HAPPY-PATH & DP-06; BLOCKED ON CANCELLATION WIRING]`

### Specific Prerequisites & Blockers

1. **`tools/fake_peer/pipe_executable.py` (Prerequisite)**:
   - *Status*: Missing. `tools/` currently contains only `phase0_fixture_runner`.
   - *Requirement*: Needs a small, deterministic Python CLI script that parses standard test environment variables/arguments and outputs deterministic stdout/stderr chunks and exit codes per `V1-CONTROLLED-FAKE-CONFORMANCE-SPEC-R1.md`.

2. **Heartbeat Failure & Cancellation Ladder Wiring (Blocker for Cancellation Tests)**:
   - *Status*: Open (Section 1 gap).
   - *Citation*: `peerhub/application/workflows.py:679-685` does not pass `on_failure` to `HeartbeatWorker`. Until this is wired, E2E tests for silence timeouts, process deadlines, and lease renewal failures cannot verify tree cancellation via `TreeController`.

3. **`tests/integration/dispatch/test_vertical_dispatch.py` Harness (Ready to Build)**:
   - *Status*: File and directory do not exist yet.
   - *Target Scenarios to Cover*:
     - **E2E Clean Vertical Dispatch**: `dispatch_and_execute` → spawn fake peer → read output → assess completion (`VERIFIED`) → consume artifacts & close lease.
     - **DP-06 Fault Injection**: Inject interruption after `DISPATCH_INTENT` outbox write. Verify attempt recovers as `MAY_HAVE_STARTED`, automatic replay is unauthorized (`automatic_replay_authorized=False`), journal digest is retained, and 0 SQLite transactions remain open during OS process execution.
     - **Process Timeout / Cancellation**: Wire `on_failure` → trigger cancellation → assert `RealTreeController` terminates child process tree and records cleanup evidence.

---

## 4. Stale Items & Design Doc Drift Audit (`SLICE5-KICKOFF-R1.md`)

Reading through `docs/design/SLICE5-KICKOFF-R1.md` in full reveals several places where the kickoff doc drifted from what actually shipped tonight:

1. **Doc Status Header (Lines 3-8)**:
   - *Doc states*: "proposed design document, not yet ratified... Pending one more cross-review pass".
   - *Actual*: Fully ratified across all 1355 lines of decisions, ratifications, and user calls. Header is stale.

2. **PTY Runner Requirement (Lines 126, 193-199)**:
   - *Doc states*: `peerhub/dispatch/pty.py` and `tools/fake_peer/pty_executable.py` are required open items.
   - *Actual*: Empirical probe (`docs/design/phase0/PTY-BUFFERING-PROBE-2026-08-03.md`, cited in lines 1077-1107) proved plain pipes handle all active peers (`cc`, `ag`, `cx`) without Windows ConPTY when `stdin=subprocess.DEVNULL`. `pty.py` was intentionally skipped.

3. **Migration File Name (Lines 131, 738-742)**:
   - *Doc states*: `0008_dispatch_artifacts_journal.sql`.
   - *Actual*: Shipped as `0008_dispatch_artifact_metadata.sql` (commit `d1f341b`), separating manifest and item tables into `dispatch_artifact_manifests` and `dispatch_artifacts`.

4. **Unlisted Shipped Modules (Lines 124-153)**:
   - *Doc omits*: `peerhub/dispatch/materializer.py`, `peerhub/dispatch/tree_controller.py`, and `peerhub/dispatch/heartbeat.py`.
   - *Actual*: All three were created, ratified, and shipped with green unit test suites tonight.

5. **Open Questions Section (Lines 186-199)**:
   - *Open Question 1 (Lease heartbeating)*: Resolved by Option B (`heartbeat.py` dedicated thread).
   - *Open Question 2 (PTY backend)*: Resolved by empirical PTY probe (plain pipes suffice).

6. **Terminal Classification Naming (Lines 280-282 vs 583-602)**:
   - *Doc notes*: Potential conflict between `EXITED` and `EXIT_NON_ZERO`.
   - *Actual*: User call ratified keeping `EXIT_NON_ZERO` as shipped in `TerminalClassification`.

---

## 5. Actionable Roadmap for Next Session

Below is the skimmable, prioritized task list for the next session.

### `[READY NOW]` (Actionable immediately without design rounds)

1. **Wire `HeartbeatWorker.on_failure` to Cancellation Ladder in `workflows.py`**:
   - Edit `peerhub/application/workflows.py`: Pass `on_failure` callback to `HeartbeatWorker`.
   - Add `threading.Lock` inside `ProcessSupervisor` (`peerhub/dispatch/process.py`) for multi-thread callback safety.
   - Connect `on_failure` → `supervisor.begin_cancellation()` → `RealTreeController`.

2. **Create `tools/fake_peer/pipe_executable.py`**:
   - Write deterministic fake peer script handling `CHUNK` streaming, `EXIT` codes, and configurable execution delays.

3. **Build `tests/integration/dispatch/test_vertical_dispatch.py`**:
   - Create directory `tests/integration/dispatch/`.
   - Implement E2E vertical dispatch test suite (Happy path, DP-06 isolated journal fault injection, cancellation & timeout handling).

4. **Update `docs/design/SLICE5-KICKOFF-R1.md` Header**:
   - Update header status from "proposed" to "ratified & shipped".

### `[NEEDS DESIGN ROUND FIRST]`

* **None for Phase 2 completion.** Phase 2 scope is fully ratified and closed. Any future Phase 3 work (e.g., real peer CLI adapters, external coordinator interfaces) will initiate its own kickoff design round when scheduled.
