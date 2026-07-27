# peerhub Round 2 — ag.deepthink critique of cx's draft

**Status**: Round 2 Critique & Resolution  
**Author**: `ag.deepthink` (Antigravity voice)  
**Target**: Critique of `cx.deepthink`'s Round 1 draft (`peerhub-architecture-debate.md`)

---

### Executive Verdict

**Verdict: CONVERGED**

Upon thorough evidence-based review of `cx.deepthink`'s Round 1 draft, `ag` accepts `cx`'s core positions on all four major architectural points—authoritative SQLite storage, pure domain hexagonal layering, 3-layer task outcome modeling, and a foreground/stdio service model—with minor practical packaging consolidations. The two drafts are fully compatible and reconcile into a single, unified target architecture.

---

### Detailed Analysis of the 4 Key Divergence Points

#### 1. Authoritative Storage: SQLite vs. JSON Files

- **cx's Position**: Single local SQLite database per `PeerHubHome` using `BEGIN IMMEDIATE` write transactions, UNIQUE CAS constraints, and an atomic outbox table.
- **ag's Round 1 Position**: Sketched a flatter module layout with atomic file locks (`_get_lock`) and JSON files, mirroring `hub.py`'s current file storage.
- **Evidence-Based Evaluation**:
  - `hub.py`'s current JSON-file storage relies on bespoke file locks (`_get_lock`), `_read_json`, `_write_json`, and atomic `os.replace` temp files ([`hub.py:644-700`](file:///P:/_sys/core/hub.py#L644-L700)). On Windows, atomic file replaces suffer from transient `PermissionError` (WinError 32 sharing violations), requiring exponential backoff retries.
  - More critically, multi-file updates (e.g. updating a request status in `task_registry.json`, opening a lease in `leases.json`, and writing session state in `state.json`) cannot be committed in a single atomic transaction without global multi-file locks. This structural limitation was the direct cause of the T83 session-state lost-update race.
  - SQLite provides single-file ACID transactions, native UNIQUE constraints (`(proposal_kind, workspace_scope, finding_fingerprint)` for T89; `(round_id, voter_id)` for consensus vote immutability), built-in WAL mode concurrency, and an atomic outbox table.
- **Adopted Resolution (AGREE WITH CX)**: **Adopt SQLite as the v1 authoritative store.** SQLite replaces hundreds of lines of fragile custom lock/replace code with Python's standard library `sqlite3`.
  - *Operational Constraint*: `peerhub` must enforce that the SQLite database file resides on a local filesystem (not SMB/NFS network shares) to guarantee POSIX/Windows WAL lock safety.

---

#### 2. Layering Depth: Hexagonal Split vs. Flatter Layout

- **cx's Position**: 5-layer hexagonal structure (`protocol` / `domain` / `application` / `ports` / `infrastructure`), keeping `domain` 100% pure (zero I/O, pure state functions).
- **ag's Round 1 Position**: Flatter package structure (`core`, `adapters`, `dispatch`, `routing`, `consensus`, `health`, `ipc`, `governance`).
- **Evidence-Based Evaluation**:
  - `hub.py`'s most persistent defects (T83 lease clobber, T87 telemetry short-circuit, T89 proposal flood) occurred because side effects (filesystem writes, subprocess spawning, clock reads) were interleaved directly inside control-flow loops.
  - Keeping domain state reducers (request FSM, lease FSM, consensus FSM, health FSM) 100% pure allows every state transition to be unit-tested deterministically in memory with zero I/O or filesystem mocking.
  - However, maintaining five distinct top-level package directories (`ports/`, `infrastructure/`, etc.) for a single-machine coordinator can introduce unnecessary directory depth if over-abstracted.
- **Adopted Resolution (SYNTHESIS / CONDENSED HEXAGONAL)**: **Adopt pure domain reducers and single-entry ApplicationService, but condense the physical directory structure.**
  - We enforce `cx`'s rule: `domain/` contains pure state transition logic with zero I/O. `application/service.py` is the sole mutating entrance.
  - To prevent file clutter, `ports` protocols can be co-located with their respective functional abstractions (e.g. `adapters/contract.py` holds port definitions, `infrastructure/sqlite` handles storage), preserving 100% architectural purity without deep package nesting.

---

#### 3. Task-Completion Outcome Model: 3 Outcome Layers vs. Single Adapter Validation

- **cx's Position**: 3 fully separate outcome layers:
  1. *Execution Outcome*: Process exit code, timeout, crash.
  2. *Peer-Protocol Outcome*: Valid stream parsing, vendor completion marker, vendor error.
  3. *Task Outcome*: `SUCCEEDED_VERIFIED`, `DELIVERED_UNVERIFIED`, `INCOMPLETE`, `FAILED`.
  Adapters parse vendor protocol evidence (`PeerProtocolResult`) but are NEVER allowed to declare semantic task success (`SUCCEEDED_VERIFIED`).
- **ag's Round 1 Position**: `PeerAdapter.validate_output()` returning `OutputValidationResult(is_valid, is_truncated, failure_reason)`.
- **Evidence-Based Evaluation**:
  - `ag`'s Round 1 `validate_output()` method placed task verification inside the adapter. This was a conceptual flaw: a peer adapter (e.g. `AgyAdapter` or `CodexAdapter`) can verify that CLI stdout was parsed cleanly, but CANNOT know if the user's task was actually fulfilled unless an explicit completion contract (artifact check, test pass, schema validation) was verified.
  - *T88 Evidence*: Under heavy load, `ag.opus` returned `exit 0` with a 171-character reply announcing internal subagent delegation without delivering content. To an adapter, stdout was valid UTF-8 and exited 0. If the adapter determines success, it misreports task completion.
  - `cx`'s 3-layer model correctly separates "did the process exit 0?" from "did the adapter parse valid reply text?" from "did the caller's completion contract verify?". Exit 0 + text produces at most `DELIVERED_UNVERIFIED`. `SUCCEEDED_VERIFIED` requires explicit contract verification by the core engine.
- **Adopted Resolution (AGREE WITH CX)**: **Adopt cx's 3-layer outcome model.** Adapters emit protocol evidence; `ApplicationService` evaluates task outcome. This structurally resolves T88.

---

#### 4. Service / Process Model: Resident Daemon vs. Foreground / Stdio Service

- **cx's Position**: Flags "resident daemon vs. foreground/stdio service" as an open decision, leaning toward foreground `serve` / `Client` in-process first.
- **ag's Round 1 Position**: Did not explicitly address service topology, but Phase 5 implied an in-process wrapper facade.
- **Evidence-Based Evaluation**:
  - The 2026-07-27 User Directive explicitly severed standalone desktop/daemon packaging from Engram/`peerhub`. A background OS daemon (systemd service or Windows service) introduces installer complexity, auto-start management, and platform service registration—the exact productization tax we agreed to eliminate.
  - `peerhub` needs two interaction modes:
    1. *In-Process Library*: `from peerhub import Client`. `Client` invokes `ApplicationService` directly in the same Python process.
    2. *Foreground Service*: `peerhub serve --stdio` or local IPC socket server for separate CLI wrappers (`codex_entry.py`, `claude_entry.py`, `agy_entry.py`).
- **Adopted Resolution (AGREE WITH CX)**: **Adopt foreground `serve` (stdio / local IPC) and in-process `Client` as the v1 service model.** Resident OS daemons are explicitly out of scope for v1.

---

### Review of cx's §2.2 (Live Behavior) & §2.3 (Source Corrections)

1. **Pre-Spawn `dispatch_intent` & Birth Identity (§2.2)**: `ag` fully endorses `cx`'s requirement to persist `dispatch_intent` before process spawn and record platform process-creation identity alongside PID. This prevents PID-reuse kill bugs and ambiguous crash replays.
2. **Decoupled Health & Admission (§2.2)**: `ag` endorses separating `Availability` (measured evidence) from `Admission/Quarantine` (policy projection), ensuring rate-limit cooldowns do not overwrite underlying health states.
3. **Source Location Correction (§2.3)**: Accepted. `cx` correctly identified that `engram-refactor-blueprint-2026-07-20.md` ends at §10, and §13.15/§13.16 material is located in `phase2-arch-general-specific-2026-07-22.md`.

---

### Summary of Consensus Points

| Area | Consensus Decision |
|---|---|
| **Authoritative Store** | Single local SQLite DB per `PeerHubHome` (local filesystem enforced, WAL mode, atomic outbox). |
| **Architecture / Layering** | Hexagonal architecture with 100% pure domain state reducers and single `ApplicationService` entry point. |
| **Outcome Model** | 3-layer model: Execution outcome, Peer-protocol outcome, Task outcome (`DELIVERED_UNVERIFIED` default for prose). |
| **Service Model** | In-process `Client` and foreground `peerhub serve --stdio`. No background OS daemon. |
| **Adapter Contract** | `PeerAdapter` + optional `UsageProvider` split. Adapters translate protocol; core engine owns orchestration. |
| **Defect Safeguards** | Pipeline fan-out for T87; 3-layer outcome model for T88; DB unique index + SHA-256 fingerprinting for T89. |

**Final Verdict**: **CONVERGED** — `ag` and `cx` designs are fully unified. Ready for `cc` synthesis and finalization.
