# Work Projection Contract (M2.2)

## 1. Authority & Storage Layout
- **Authority:** Core Event Stream Records are the sole, immutable source of truth. Work items are pure, derived state machines. The Work Projection is a strictly rebuildable read-model (Decision #6, Invariant 6, Invariant 7).
- **Storage Layout:**
  - **Authoritative Stream:** A home stream in Core containing ordered `Record`s with kind prefixes:
    - `m2.work.created`: Creates a new work item with specification, title, and initial state (`OPEN`).
    - `m2.work.transitioned`: Advances lifecycle state with CAS revision check.
    - `m2.work.checkpointed`: Saves intermediate progress/resume payload.
    - `m2.work.artifact_linked`: Binds an immutable CAS artifact digest to the work item.
  - **Projection Store:** SQLite database table `ext_work_items` and `ext_work_checkpoints` maintaining fast indexed lookup for tasks.
  - **Full Rebuildability:** Dropping all `ext_work_*` tables and replaying the home stream's ordered records from offset 0 yields a 100% logically and structurally equivalent projection state.

## 2. Port Protocols & Error Types
The Work Projection engine exposes a clean, typed public interface:
- `create_work(stream_id, work_id, title, spec, initial_state="OPEN") -> WorkItem`
  Errors: `WorkAlreadyExistsError`, `InvalidWorkPayloadError`.
- `transition_work(work_id, expected_revision, target_state, reason=None) -> WorkItem`
  Errors: `WorkNotFoundError`, `WorkRevisionConflictError`, `ForbiddenTransitionError`.
- `checkpoint_work(work_id, expected_revision, checkpoint_data) -> WorkItem`
  Errors: `WorkNotFoundError`, `WorkRevisionConflictError`, `TerminalStateError`.
- `link_artifact(work_id, expected_revision, digest) -> WorkItem`
  Errors: `WorkNotFoundError`, `WorkRevisionConflictError`, `InvalidArtifactReferenceError`.
- `get_work(work_id) -> WorkItem`:
  Errors: `WorkNotFoundError`.
- `rebuild_projection(records) -> int`:
  Wipes and re-evaluates projection tables from an ordered record iterable, returning count of reduced records.

## 3. Work Item State Machine
```text
           ┌──────────────────────┐
           │         OPEN         │
           └──────────┬───────────┘
                      │
                      ▼
           ┌──────────────────────┐
    ┌─────►│        ACTIVE        │◄─────┐
    │      └────┬─────┬──────┬────┘      │
    │           │     │      │           │
    ▼           │     ▼      │           ▼
┌────────┐      │  ┌──────┐  │      ┌─────────┐
│ PAUSED │◄─────┘  │ DONE │  └─────►│ BLOCKED │
└───┬────┘         └──────┘         └────┬────┘
    │                 ▲                  │
    │                 │                  │
    └──────────┐      │      ┌───────────┘
               ▼      │      ▼
          ┌───────────────────────┐
          │   FAILED / CANCELLED  │ (Terminal)
          └───────────────────────┘
```
- **Active / Working:** `OPEN` -> `ACTIVE`.
- **Suspension:** `ACTIVE` <-> `PAUSED`, `ACTIVE` <-> `BLOCKED`.
- **Completion / Termination:**
  - `ACTIVE` -> `DONE`
  - `ACTIVE` -> `FAILED`
  - `OPEN` | `ACTIVE` | `PAUSED` | `BLOCKED` -> `CANCELLED`
- **Terminal States:** `DONE`, `FAILED`, `CANCELLED` cannot transition further; any transition attempt raises `ForbiddenTransitionError`.

## 4. Ordering & CAS Revision Rules
- Every work item starts at `revision = 1` upon `m2.work.created`.
- Every successful state transition, checkpoint, or artifact link increments `revision` strictly by 1.
- State mutation requests require `expected_revision`. If `expected_revision != current_revision`, the operation is rejected with `WorkRevisionConflictError`.
- The Record reducer deterministically processes records ordered by stream position. Any out-of-order or revision-conflicted record is cleanly discarded or flagged as an invalid transition event without corrupting prior state.

## 5. Crash Matrix & Recovery
| Crash Point | Resulting State | Recovery Action | Covering Test |
| :--- | :--- | :--- | :--- |
| Crash after Record append, before Projection update | Stream has record; Projection outdated | Reducer re-scans stream from last processed offset and catches up projection. | WRK-001 |
| Projection DB deleted or corrupted | No projection records | Rebuilds full projection cleanly from stream records (`rebuild_projection`). | WRK-002 |
| Concurrent transition race on same revision | Race condition | Reducer applies winner; loser's record fails expected_revision check and is rejected. | WRK-005 |
| Malformed payload in stream record | Stream has unparseable record | Reducer logs warning, marks event ignored, and preserves valid projection state. | WRK-008 |

## 6. Null/Empty/Unknown Semantics
- `work_id` must be non-empty ASCII string matching `^[a-zA-Z0-9_-]+$`.
- `title` must be non-empty string.
- `checkpoint_data` must be JSON-serializable dictionary.
- Unknown fields in record payloads are preserved in raw dict but do not trigger schema failure.
