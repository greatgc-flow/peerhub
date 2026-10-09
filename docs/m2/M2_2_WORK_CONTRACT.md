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

---

## Closure update (2026-10-08)

Written after the M2/M3 closure work (`docs/implementation/M2_M3_CLOSURE_2026-10-08.md`) and checked against the code and tests. Where it conflicts with the text above, **this section wins**. The state-machine and exception JSON catalogs carry the same update.

Section 1: replace the projection-store and full-rebuild bullets:

```markdown
  - **Projection Store:** SQLite table `ext_work_items` stores the derived WorkItem, including its checkpoint and artifact links.
  - **Full Rebuildability:** Replaying ordered Records reconstructs the logical Work projection. The replacement is atomic: records are folded before existing rows are deleted, and deletion plus replacement writes occur in one SQLite transaction.
```

Section 2: replace the checkpoint/link error lines:

```markdown
  Errors: `WorkNotFoundError`, `WorkRevisionConflictError`, `ForbiddenTransitionError`, `InvalidWorkPayloadError`.
```

```markdown
  Errors: `WorkNotFoundError`, `WorkRevisionConflictError`, `InvalidWorkPayloadError`.
```

Section 4: replace the expected-revision sentence and add:

```markdown
- State mutation requests require `expected_revision`. CAS decisions use the state reconstructed from ordered authoritative stream Records, not the cached projection revision. Stale or missing projection rows are repaired before mutation checks.
- After appending a transition, checkpoint, or artifact-link Record, the mutation succeeds only if the authoritative reducer accepted that specific Record. A losing change raises `WorkRevisionConflictError`; it is not acknowledged merely because append succeeded.
- An append idempotency conflict is translated into `WorkRevisionConflictError`, and the caller's projection is refreshed from authoritative Records.
- **Creation-Crash Recovery:** If the creation Record exists but the projection row is missing, mutation locates the work item through authoritative streams and reconstructs its row. Retrying creation with the same stream, title, and specification repairs the missing row without another creation Record when the authoritative revision remains 1; conflicting content raises `WorkAlreadyExistsError`.
- **Cached Reads:** `get_work` and `list_work` read the projection and may remain stale until mutation repair or explicit rebuild.
```

Section 5: add:

```markdown
| Creation Record durable, projection save fails | Work is absent from cached reads | Locate it from authoritative streams on mutation; an identical revision-1 creation retry repairs the row without another creation Record. | WRK-014 |
| Rebuild record source fails | Existing projection remains | Fold fails before replacement begins; preserve existing rows. | WRK-015 |
| Rebuild replacement write fails | Transaction is rolled back | Restore the existing projection, including rows deleted inside the transaction. | WRK-015 |
```

Replace the malformed-payload row’s recovery text with:

```markdown
Reducer ignores invalid records and preserves the previously reduced valid state.
```

**Wrong/obsolete:** CAS against the “current projection revision”; a separate `ext_work_checkpoints` table; unconditional creation idempotency; `TerminalStateError` for checkpointing; `InvalidArtifactReferenceError` for malformed links; and claims that the reducer logs/flags invalid events. The reducer silently skips them. Artifact linking also has no terminal-state rejection, so do not claim all mutations are forbidden on terminal items.
