# Phase 1 Parity Ledger - Batch 4 (18 Actions)

## 1. task-status
* **Input Schema:** `ai_root: Path`, `task_id: str | None = None` (via `--task-id` or `--id`). Validation: None (pure read-only). Authorization: Unrestricted read-only inspection.
* **Normalized Envelope:** Success (single task found): Exit 0, prints formatted 2-space indented JSON object of task record (`data.get(task_id, {})`) to stdout. Success (all tasks list): Exit 0, prints TSV header `task_id	owner	status	updated_at	checkpoints` followed by tab-delimited records to stdout. Success (no task registry file): Exit 0, prints `No task registry records found.` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation against `_task_registry_path(ai_root)` (`ai_root/task_registry.json`). No file mutations or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read-only inspection (lines 11325-11337). Lock-free read operation. Does not append logs, mutate timestamps, or alter registry state. Handles missing `task_registry.json` cleanly by returning exit 0 with fallback message.
* **Redaction/Ordering:** Stdout emits either formatted JSON payload or TSV header and tabular lines in dictionary iteration order.
* **Comparator:** NORMALIZED (ISO timestamps, JSON key ordering, TSV row ordering).
* **Specific Argv Comparators:**
  * **Safety:** Read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Formats active task coordination state from `task_registry.json`.
* **Fixtures:**
  * **Positive (`fix-task-status-pos-01`, NYI):** Pre-state: `ai_root/task_registry.json` contains `{"T-100": {"task_id": "T-100", "owner": "cx", "status": "ACTIVE", "updated_at": "2026-08-20T12:00:00", "checkpoints": [{"peer": "cx", "note": "started", "at": "2026-08-20T12:00:00"}]}}`. Request: `task-status --task-id T-100`. Expected exit: 0. Output: Formatted 2-space indented JSON representation of task `T-100`. Post-state: Unchanged.
  * **Invalid (`fix-task-status-inv-01`, NYI):** Pre-state: `ai_root/task_registry.json` exists without `T-999`. Request: `task-status --task-id T-999`. Expected exit: 0. Output: `{}` (empty JSON object). Post-state: Unchanged.
  * **Auth (`fix-task-status-auth-01`, NYI):** Pre-state: Read permission denied on `task_registry.json`. Request: `task-status`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-task-status-rec-01`, NYI):** Pre-state: `ai_root/task_registry.json` does not exist on disk. Request: `task-status`. Recovery injection: Hub handles missing file gracefully. Expected exit: 0. Output: `No task registry records found.`. Post-state: Unchanged.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 2. task-failover
* **Input Schema:** `ai_root: Path`, `task_id: str` (via `--task-id` or `--id`, required), `to_peer: str` (via `--peer` or `--agent`, required), `reason: str = ""` (via `--reason` or `--detail`). Validation: Requires non-empty `task_id` and `to_peer` (exits 1 if missing). Validates target peer health via `_healthy_peer(to_peer)` (exits 2 if unhealthy). Requires existing task record in `task_registry.json` (exits 1 if not found). Authorization: Task orchestration and failure mitigation interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] TASK-FAILOVER {task_id} | {old_owner or 'unknown'} -> {to_peer}` to stdout (line 11388). Error (missing arguments): Exit 1, prints `[HUB:ERROR] task-failover requires --task-id and --peer` to stderr (line 11364). Error (unhealthy target): Exit 2, prints `[HUB:ERROR] failover target {to_peer} is not healthy status={status}` to stderr (line 11368). Error (task not found): Exit 1, prints `[HUB:ERROR] task {task_id} not found` to stderr (line 11375).
* **State Changes:** Before: Task in `task_registry.json` owned by original peer (`old_owner`). After: Mutates `task_registry.json` under lock `task_registry` updating `owner = to_peer`, `status = "ACTIVE"`, `updated_at = _now()`, and appending new checkpoint `{"peer": to_peer, "note": f"failover from {old_owner or 'unknown'}: {reason or 'manual'}", "at": _now()}`. Appends failover record `f"{_now()} task:{task_id} failover {old_owner or 'unknown'} -> {to_peer} ({reason or 'manual'})"` to `ACTIVE_THREADS` in `handoff.md`. External effects: Reassigns operational task ownership to target peer.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent (lines 11362-11389). On a second identical call with the same parameters, `old_owner` is read as the current owner (`to_peer`), and the action unconditionally appends another checkpoint to `task["checkpoints"]` (lines 11381-11385), refreshes `updated_at` to `_now()` (line 11380), appends another failover entry to `ACTIVE_THREADS` in `handoff.md` (line 11387), and emits stdout with the updated transition `[HUB] TASK-FAILOVER {task_id} | {to_peer} -> {to_peer}` (line 11388). Target health gate prevents failing over to degraded peer (lines 11366-11369). Concurrency serialized under lock `task_registry`. Atomic write via `_write_task_registry`.
* **Redaction/Ordering:** Stdout confirms failover source, destination, and task ID.
* **Comparator:** NORMALIZED (ISO timestamps, reason strings, old owner on subsequent calls).
* **Specific Argv Comparators:**
  * **Safety:** Target peer health precondition check (Exit 2 on failure) and `task_registry` lock serialization.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Reassigns task ownership in task registry, appends checkpoints, and logs transition in handoff.
* **Fixtures:**
  * **Positive (`fix-task-failover-pos-01`, NYI):** Pre-state: `ai_root/task_registry.json` contains `{"T-1": {"task_id": "T-1", "owner": "cc", "status": "ACTIVE", "checkpoints": []}}`; peer `cx` is healthy (GREEN). Request: `task-failover --task-id T-1 --peer cx --reason "stalled"`. Expected exit: 0. Output: `[HUB] TASK-FAILOVER T-1 | cc -> cx`. Post-state: `task_registry.json` has `data["T-1"]["owner"] == "cx"`, 1 checkpoint appended; `handoff.md` `ACTIVE_THREADS` contains failover note.
  * **Invalid (`fix-task-failover-inv-01`, NYI):** Pre-state: `task_registry.json` exists without `T-missing`. Request: `task-failover --task-id T-missing --peer cx`. Expected exit: 1. Output: `[HUB:ERROR] task T-missing not found` to stderr. Post-state: `task_registry.json` unchanged.
  * **Auth (`fix-task-failover-auth-01`, NYI):** Pre-state: Peer `ag` has health status RED. Request: `task-failover --task-id T-1 --peer ag`. Expected exit: 2. Output: `[HUB:ERROR] failover target ag is not healthy status=RED` to stderr. Post-state: `task_registry.json` unchanged.
  * **Recovery (`fix-task-failover-rec-01`, NYI):** Pre-state: Lock `task_registry` held by concurrent operation. Request: `task-failover --task-id T-1 --peer cx --reason "retry"`. Recovery injection: Hub retries file lock acquisition, acquires lock, and persists task update. Expected exit: 0. Output: `[HUB] TASK-FAILOVER T-1 | cc -> cx`. Post-state: Task failover committed.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 3. approval-request
* **Input Schema:** `ai_root: Path`, `from_peer: str` (via `--from`, `--peer`, `--agent`, defaults "unknown"), `action: str` (via `--subject` or `--msg`), `auth_needed: str` (via `--auth-needed`), `scope: str` (via `--scope` or `--file`, defaults ""), `risk: str` (via `--severity`, defaults "workspace-write"), `fallback: str = ""` (via `--fallback`). Validation: Guarded by `_role_guard` (allowed roles: `coordinator`, `implementer`, `researcher`, `documenter`). Resolves human interface peer via `_select_human_interface_peer(ai_root)` (exits 2 if no eligible peer). Authorization: Role-guarded approval escalation mechanism.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] APPROVAL-REQUEST {from_peer} -> {target}` to stdout (line 11836). Error (role guard failure): Exit 1 (via `_role_guard`, prints `[HUB:ERROR] role guard: {agent} has role '{current_role}' but action requires one of: ...` to stderr). Error (no eligible human interface peer): Exit 2, prints `[HUB:ERROR] no eligible human_interface_peer for approval request` to stderr (line 11807).
* **State Changes:** Before: Existing mailbox and handoff state. After: Records routing metric `human_interface_peer_selection` in `routing_metrics.jsonl` (lines 11816-11823). Invokes `action_send` dispatching structured `APPROVAL_REQUEST` message with `priority="CRITICAL"` to target peer's maildir (line 11834). Appends entry `f"{_now()} approval requested by {from_peer or 'unknown'} for {action or 'unspecified'}"` to `PENDING_ISSUES` in `handoff.md` (line 11835). External effects: Delivers critical approval notification into human interface peer inbox.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent (lines 11802-11837). Every invocation creates and delivers a new uniquely identified message via `action_send` into the target's mailbox (line 11834), appends a new timestamped entry to `PENDING_ISSUES` in `handoff.md` (line 11835), and writes a new routing metric record to `routing_metrics.jsonl` (line 11816). Concurrency and durable delivery handled by atomic mailbox file operations and `_role_guard`.
* **Redaction/Ordering:** Stdout confirms approval request dispatch to resolved target.
* **Comparator:** NORMALIZED (target peer resolution, timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Role authorization guard (`_role_guard`) and human-interface peer availability check.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Escalates high-risk action request to human interface terminal peer.
* **Fixtures:**
  * **Positive (`fix-approval-request-pos-01`, NYI):** Pre-state: Peer `cc` holds role `coordinator`; human interface peer `cc` is eligible and healthy. Request: `approval-request --from cc --subject "deploy-prod" --auth-needed "admin" --scope "prod" --severity "high" --fallback "abort"`. Expected exit: 0. Output: `[HUB] APPROVAL-REQUEST cc -> cc`. Post-state: `PENDING_ISSUES` in `handoff.md` has approval entry; message file written to `_sys/cc/inbox/`; `routing_metrics.jsonl` has metric.
  * **Invalid (`fix-approval-request-inv-01`, NYI):** Pre-state: No peer configured as human interface terminal or all candidates unhealthy. Request: `approval-request --from cc --subject "write" --auth-needed "fs"`. Expected exit: 2. Output: `[HUB:ERROR] no eligible human_interface_peer for approval request` to stderr. Post-state: `routing_metrics.jsonl` records eligible=False; handoff and maildir unchanged.
  * **Auth (`fix-approval-request-auth-01`, NYI):** Pre-state: Peer `unknown_bot` has unassigned or unauthorized role. Request: `approval-request --from unknown_bot --subject "drop-db"`. Expected exit: 1. Output: `[HUB:ERROR] role guard: ...` to stderr. Post-state: Unchanged.
  * **Recovery (`fix-approval-request-rec-01`, NYI):** Pre-state: Primary human interface terminal is busy; secondary candidate available. Request: `approval-request --from cx --subject "merge" --auth-needed "git"`. Recovery injection: Hub dynamically selects highest-ranked eligible secondary terminal peer. Expected exit: 0. Output: `[HUB] APPROVAL-REQUEST cx -> ...`. Post-state: Approval message delivered to fallback human interface peer.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 4. file-lock
* **Input Schema:** `ai_root: Path`, `name: str` (via `--name` or `--file`, required), `owner: str` (via `--peer` or `--agent`, required), `scope: str = ""` (via `--scope` or `--section`, defaults "file"). Validation: Requires non-empty `name` and `owner` (exits 1 if missing). Checks existing lock in `file_locks.json`: if locked by different owner, rejects with Exit 1. Authorization: Concurrency coordination / lock management.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] FILE-LOCK {name} | owner={owner}` to stdout (line 11404). Error (missing arguments): Exit 1, prints `[HUB:ERROR] file-lock requires --name and --peer/--agent` to stderr (line 11393). Error (lock conflict): Exit 1, prints `[HUB:ERROR] {name} is locked by {existing.get('owner')}` to stderr (line 11400).
* **State Changes:** Before: Lock registry state in `_file_locks_path(ai_root)` (`ai_root/file_locks.json`). After: Updates `file_locks.json` under lock `file_locks` storing `data[name] = {"name": name, "owner": owner, "scope": scope or "file", "locked_at": existing.get("locked_at") if existing else _now()}`. External effects: Claims exclusive named file lock across collaborating peers.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent / re-entrant for identical owner (lines 11391-11405). When called again by the same owner, preserves original `locked_at` timestamp (line 11402) without appending logs or incrementing sequence counters, yielding identical state and stdout `[HUB] FILE-LOCK {name} | owner={owner}`. Rejects acquisition with Exit 1 if locked by another owner (line 11401). Serialized under local file lock `file_locks`. Atomic persistence via `_write_json`.
* **Redaction/Ordering:** Stdout confirms file lock acquisition and owner.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Local lock `file_locks` and ownership conflict detection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Grants and persists exclusive named file lock to requesting peer.
* **Fixtures:**
  * **Positive (`fix-file-lock-pos-01`, NYI):** Pre-state: `ai_root/file_locks.json` is empty or does not contain `docs/spec.md`. Request: `file-lock --name docs/spec.md --peer cc --scope section_a`. Expected exit: 0. Output: `[HUB] FILE-LOCK docs/spec.md | owner=cc`. Post-state: `file_locks.json` contains `docs/spec.md` with owner `cc`, scope `section_a`, and `locked_at` timestamp.
  * **Invalid (`fix-file-lock-inv-01`, NYI):** Pre-state: Standard environment. Request: `file-lock --name docs/spec.md` (missing `--peer`). Expected exit: 1. Output: `[HUB:ERROR] file-lock requires --name and --peer/--agent` to stderr. Post-state: `file_locks.json` unchanged.
  * **Auth (`fix-file-lock-auth-01`, NYI):** Pre-state: `docs/spec.md` is locked in `file_locks.json` with owner `cx`. Request: `file-lock --name docs/spec.md --peer cc`. Expected exit: 1. Output: `[HUB:ERROR] docs/spec.md is locked by cx` to stderr. Post-state: `file_locks.json` remains owned by `cx`.
  * **Recovery (`fix-file-lock-rec-01`, NYI):** Pre-state: `docs/spec.md` already locked by `cc` at `2026-08-20T10:00:00`. Request: `file-lock --name docs/spec.md --peer cc`. Recovery injection: Hub detects same owner re-entry and preserves existing `locked_at`. Expected exit: 0. Output: `[HUB] FILE-LOCK docs/spec.md | owner=cc`. Post-state: `file_locks.json` unchanged with original `locked_at: 2026-08-20T10:00:00`.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 5. file-unlock
* **Input Schema:** `ai_root: Path`, `name: str` (via `--name` or `--file`, required), `owner: str = ""` (via `--peer` or `--agent`). Validation: Requires non-empty `name` (exits 1 if missing). If lock exists and `owner` is specified, validates that `existing.owner == owner` (exits 1 on mismatch). If file is not locked, prints warning and exits 0. Authorization: Concurrency coordination / lock release.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] FILE-UNLOCK {name}` to stdout (line 11423). Warning (not locked): Exit 0, prints `[HUB:WARN] {name} is not locked` to stdout (line 11416). Error (missing name): Exit 1, prints `[HUB:ERROR] file-unlock requires --name` to stderr (line 11409). Error (owner mismatch): Exit 1, prints `[HUB:ERROR] {name} is locked by {existing.get('owner')}, not {owner}` to stderr (line 11419).
* **State Changes:** Before: Target file lock held in `file_locks.json`. After: Mutates `file_locks.json` under lock `file_locks` removing key `name` (`data.pop(name, None)`). External effects: Releases exclusive file reservation for other peers.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Convergent release across state transitions (lines 11407-11424). First call on held lock removes the key and prints `[HUB] FILE-UNLOCK {name}` (lines 11421-11423). On a second call when the lock is already absent, it short-circuits at line 11416, printing `[HUB:WARN] {name} is not locked` and exiting 0 without mutating the file. Owner verification guard (line 11418) prevents unauthorized lock releases. Concurrency guarded by local lock `file_locks`. Atomic persistence via `_write_json`.
* **Redaction/Ordering:** Stdout confirms file lock release on success or warning when not locked.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Owner verification check and lock `file_locks` serialization.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Releases named file lock from lock registry.
* **Fixtures:**
  * **Positive (`fix-file-unlock-pos-01`, NYI):** Pre-state: `file_locks.json` contains `{"docs/spec.md": {"name": "docs/spec.md", "owner": "cc"}}`. Request: `file-unlock --name docs/spec.md --peer cc`. Expected exit: 0. Output: `[HUB] FILE-UNLOCK docs/spec.md`. Post-state: `docs/spec.md` removed from `file_locks.json`.
  * **Invalid (`fix-file-unlock-inv-01`, NYI):** Pre-state: Standard environment. Request: `file-unlock` (missing `--name`). Expected exit: 1. Output: `[HUB:ERROR] file-unlock requires --name` to stderr. Post-state: `file_locks.json` unchanged.
  * **Auth (`fix-file-unlock-auth-01`, NYI):** Pre-state: `docs/spec.md` locked by `cc`. Request: `file-unlock --name docs/spec.md --peer cx`. Expected exit: 1. Output: `[HUB:ERROR] docs/spec.md is locked by cc, not cx` to stderr. Post-state: `docs/spec.md` remains locked by `cc`.
  * **Recovery (`fix-file-unlock-rec-01`, NYI):** Pre-state: `docs/spec.md` is not present in `file_locks.json`. Request: `file-unlock --name docs/spec.md`. Recovery injection: Hub handles missing lock cleanly with warning and exits 0. Expected exit: 0. Output: `[HUB:WARN] docs/spec.md is not locked`. Post-state: `file_locks.json` unchanged.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 6. lock-status
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: Unrestricted read-only inspection.
* **Normalized Envelope:** Success (active locks exist): Exit 0, prints TSV header `name	owner	scope	locked_at` followed by tab-delimited records to stdout (lines 11432-11434). Success (no active locks or no file): Exit 0, prints `No active file locks.` to stdout (line 11430). Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation against `_file_locks_path(ai_root)`. No state mutations or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read-only operation (lines 11426-11435). Lock-free read. Returns consistent TSV output across repeated calls with unchanged file locks.
* **Redaction/Ordering:** Stdout emits TSV header and lock rows or fallback message.
* **Comparator:** NORMALIZED (ISO timestamps, lock entry iteration ordering).
* **Specific Argv Comparators:**
  * **Safety:** Read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Formats active file lock table from `file_locks.json`.
* **Fixtures:**
  * **Positive (`fix-lock-status-pos-01`, NYI):** Pre-state: `file_locks.json` contains locks for `docs/spec.md` (owner `cc`) and `src/main.py` (owner `cx`). Request: `lock-status`. Expected exit: 0. Output: TSV header `name	owner	scope	locked_at` followed by rows for `docs/spec.md` and `src/main.py`. Post-state: Unchanged.
  * **Invalid (`fix-lock-status-inv-01`, NYI):** Pre-state: `file_locks.json` is empty `{}`. Request: `lock-status`. Expected exit: 0. Output: `No active file locks.`. Post-state: Unchanged.
  * **Auth (`fix-lock-status-auth-01`, NYI):** Pre-state: Read permission denied on `file_locks.json`. Request: `lock-status`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-lock-status-rec-01`, NYI):** Pre-state: `_file_locks_path(ai_root)` does not exist on disk. Request: `lock-status`. Recovery injection: Hub handles missing file gracefully. Expected exit: 0. Output: `No active file locks.`. Post-state: Unchanged.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 7. profile-validate
* **Input Schema:** `node_id: str | None = None` (via `--peer`). Validation: Cross-checks configuration in `_load_model_profiles()` against `_sys/ai/status_checks.json` peer entries and `_default_nodes()`. Validates profile node mapping, `invoke_overrides` against `known_overrides`, and flag parity via `_check_flag_parity()`. Exits 1 if any validation error is detected. Authorization: System diagnostic and configuration validation.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] PROFILE-VALIDATE OK ({len(targets)} nodes checked, parity verified)` to stdout (line 11710). Error: Exit 1, prints one or more `[HUB:PROFILE:ERR] {err}` lines to stderr (lines 11708-11709).
* **State Changes:** Before / After: Pure read-only diagnostic validation. Evaluates static configuration files and adapter commands. No file mutations or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent static configuration analysis (lines 11663-11710). Lock-free validation without side effects or state changes.
* **Redaction/Ordering:** Stderr outputs all aggregated validation errors before exit. Stdout emits success confirmation summary on pass.
* **Comparator:** NORMALIZED (node count in confirmation string).
* **Specific Argv Comparators:**
  * **Safety:** Non-destructive configuration validation.
  * **Cwd/Env/Stdin:** Resolves paths relative to `_sys` core directory. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Validates model profile integrity, adapter flag parity, and status probe alignment.
* **Fixtures:**
  * **Positive (`fix-profile-validate-pos-01`, NYI):** Pre-state: Consistent `model_profiles.json`, `status_checks.json`, and `nodes.json` configuration. Request: `profile-validate`. Expected exit: 0. Output: `[HUB] PROFILE-VALIDATE OK (N nodes checked, parity verified)`. Post-state: Unchanged.
  * **Invalid (`fix-profile-validate-inv-01`, NYI):** Pre-state: Profile contains `invoke_overrides: {"invalid_opt": "value"}` not registered in `status_checks.json`. Request: `profile-validate`. Expected exit: 1. Output: `[HUB:PROFILE:ERR] ...: invoke_override 'invalid_opt' not in status_checks known_overrides.invalid_opt_flag` to stderr. Post-state: Unchanged.
  * **Auth (`fix-profile-validate-auth-01`, NYI):** Pre-state: Read permission denied on `status_checks.json`. Request: `profile-validate`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-profile-validate-rec-01`, NYI):** Pre-state: Specific peer `cx` requested. Request: `profile-validate --peer cx`. Recovery injection: Hub restricts validation scope to targeted node. Expected exit: 0. Output: `[HUB] PROFILE-VALIDATE OK (1 nodes checked, parity verified)`. Post-state: Unchanged.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 8. lease-status
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: Process lease lifecycle inspection.
* **Normalized Envelope:** Success (no leases file): Exit 0, prints `[HUB] No leases.json found.` to stdout (line 11717). Success (empty leases file): Exit 0, prints `[HUB] No active leases.` to stdout (line 11721). Success (active leases present): Exit 0, prints formatted table header `Peer     Status     PID      Alive  Expires              Heartbeat           ` and divider `------------------------------------------------------------------------------` followed by fixed-width rows to stdout (lines 11723-11745). Status includes ` !` if expired or ` !INVALID_TIMESTAMP` if malformed. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only inspection of `_leases_path(ai_root)` (`ai_root/leases.json`) cross-referenced with live PID liveness probe (`psutil.pid_exists(pid)`). No file mutations or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read-only operation (lines 11713-11746). Non-destructive process liveness verification. Gracefully handles missing `psutil` or inaccessible PIDs (marks `alive="ERR"`).
* **Redaction/Ordering:** Fixed-width formatted table to stdout.
* **Comparator:** NORMALIZED (PIDs, ISO timestamps, process liveness states).
* **Specific Argv Comparators:**
  * **Safety:** Read-only process table query.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Renders active process leases with expiry and OS-level liveness status.
* **Fixtures:**
  * **Positive (`fix-lease-status-pos-01`, NYI):** Pre-state: `ai_root/leases.json` contains active lease for `cc` with PID of running process. Request: `lease-status`. Expected exit: 0. Output: Formatted table with header, divider, and row showing `Peer: cc`, `Status: open`, `Alive: YES`. Post-state: Unchanged.
  * **Invalid (`fix-lease-status-inv-01`, NYI):** Pre-state: `leases.json` exists with empty object `{}`. Request: `lease-status`. Expected exit: 0. Output: `[HUB] No active leases.`. Post-state: Unchanged.
  * **Auth (`fix-lease-status-auth-01`, NYI):** Pre-state: Read permission denied on `leases.json`. Request: `lease-status`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-lease-status-rec-01`, NYI):** Pre-state: `leases.json` has entry with malformed `expires_at: "not-a-date"`. Request: `lease-status`. Recovery injection: Hub catches parsing error and annotates status with ` !INVALID_TIMESTAMP`. Expected exit: 0. Output: Table row displaying `open !INVALID_TIMESTAMP`. Post-state: Unchanged.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 9. lease-sweep
* **Input Schema:** `ai_root: Path | None`. Validation: None. Authorization: Zero-token exempt system watchdog action (`_SYSTEM_EXEMPT_ACTIONS`).
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] lease-sweep complete.` to stdout (line 12237). Warnings: May print `[HUB:WARN] lease {lease_id} quarantined (reason=invalid_timestamp, peer={peer}, pid={pid}): {detail}` (lines 10994-10998) or `[HUB:WARN] expired lease {lease_id} has invalid pid={raw_pid!r}; kill skipped` (lines 11015-11019) to stderr. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: `leases.json` contains open leases with expired timestamps or invalid formats. After: Mutates `leases.json` under lock `leases` updating expired entries to `status = "expired"` and invalid timestamps to `status = "invalid_timestamp", quarantined = True`. After releasing lock, terminates orphaned process trees via `_kill_process_tree(pid)`, records ask failure via `_record_ask_failure(root_peer_id, "lease_expired", ...)`, appends failure record to `ask_history.jsonl`, and emits p2p log `_log_p2p("SWEEP", ...)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Watchdog / convergent state reconciliation (lines 10953-11053, 12235-12237). On the first run with expired leases, transitions status to `expired`/`invalid_timestamp`, terminates processes, updates failure history, and logs sweep events. On a second run when all expired leases have already been marked, no mutations or kills occur (`changed=False`), yielding pure exit 0 with `[HUB] lease-sweep complete.`. Serialized state updates under lock `leases`; process termination and telemetry logging run outside lock to prevent lock contention.
* **Redaction/Ordering:** Quarantined lease warnings to stderr, P2P sweep log emitted, stdout confirms completion.
* **Comparator:** NORMALIZED (timestamps, process IDs).
* **Specific Argv Comparators:**
  * **Safety:** Guarded lock acquisition and psutil-safe process-tree reaper.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution; terminates orphaned child process trees.
  * **Observed Semantics:** Reaps expired or invalid process leases and updates peer health/history.
* **Fixtures:**
  * **Positive (`fix-lease-sweep-pos-01`, NYI):** Pre-state: `leases.json` contains open lease `L-1` for `cx` with `expires_at: "2026-08-01T00:00:00"` (past) and `pid: 12345`. Request: `lease-sweep`. Expected exit: 0. Output: `[HUB] lease-sweep complete.`. Post-state: `leases.json` has `L-1` status `"expired"`; `ask_history.jsonl` and `log.jsonl` contain sweep records.
  * **Invalid (`fix-lease-sweep-inv-01`, NYI):** Pre-state: `leases.json` contains open lease `L-2` with `expires_at: "garbage_time"`. Request: `lease-sweep`. Expected exit: 0. Output: `[HUB:WARN] lease L-2 quarantined (reason=invalid_timestamp...` to stderr; `[HUB] lease-sweep complete.` to stdout. Post-state: `L-2` status set to `"invalid_timestamp"`, `quarantined: True`.
  * **Auth (`fix-lease-sweep-auth-01`, NYI):** Pre-state: Write permission denied on `leases.json`. Request: `lease-sweep`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-lease-sweep-rec-01`, NYI):** Pre-state: `leases.json` contains expired lease with non-existent/already-dead PID 99999. Request: `lease-sweep`. Recovery injection: Hub handles non-existent PID gracefully without error and marks lease expired. Expected exit: 0. Output: `[HUB] lease-sweep complete.`. Post-state: Lease marked expired.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 10. model-status
* **Input Schema:** None (reads orchestration SSOT). Validation: None. Authorization: Unrestricted read-only inspection.
* **Normalized Envelope:** Success: Exit 0, prints TSV header `peer	status	profile	model	effort	cost	context	capabilities` followed by tab-delimited peer entries to stdout (lines 11755-11773). Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Reads `_load_orchestration()["hub_nodes"]` and cross-references with `_peer_effective_health(peer)`. Derives model, effort, cost, and context from default profile SSOT. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read-only inspection (lines 11748-11773). Lock-free inspection without state mutations or log emissions.
* **Redaction/Ordering:** Stdout outputs TSV header followed by enabled peer records.
* **Comparator:** NORMALIZED (health status values, capabilities comma-delimited ordering).
* **Specific Argv Comparators:**
  * **Safety:** Read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Displays root-peer model defaults and operational health status.
* **Fixtures:**
  * **Positive (`fix-model-status-pos-01`, NYI):** Pre-state: `orchestration.json` defines hub nodes `cc`, `cx`, `ag` with default profiles. Request: `model-status`. Expected exit: 0. Output: TSV table starting with header `peer	status	profile	model	effort	cost	context	capabilities` followed by rows for `cc`, `cx`, `ag`. Post-state: Unchanged.
  * **Invalid (`fix-model-status-inv-01`, NYI):** Pre-state: `orchestration.json` has `hub_nodes` with `enabled: false` for `ag`. Request: `model-status`. Expected exit: 0. Output: TSV table omitting disabled node `ag`. Post-state: Unchanged.
  * **Auth (`fix-model-status-auth-01`, NYI):** Pre-state: Read permission denied on `orchestration.json`. Request: `model-status`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-model-status-rec-01`, NYI):** Pre-state: Peer `cx` health file missing on disk. Request: `model-status`. Recovery injection: Hub reports default/unknown health status and displays configured SSOT profile metadata. Expected exit: 0. Output: TSV row for `cx` with fallback health status. Post-state: Unchanged.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 11. transient-scan
* **Input Schema:** `ai_root: Path`. Validation: None. Iterates parent directory of `ai_root` (`ai_root.parent`) searching for transient root files matching pattern `[A-Za-z0-9_-]{4,12}\.(?:tmp|log|txt)` or 8-char 4-byte files containing byte payload `b"blat"`. Authorization: Workspace hygiene inspection.
* **Normalized Envelope:** Success (candidates found): Exit 0, prints header `transient-candidates` followed by matching filenames, one per line to stdout (lines 11797-11799). Success (no candidates found): Exit 0, prints `No transient root-file candidates found.` to stdout (line 11795). Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure filesystem read scan. No files modified, deleted, or locked. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read-only scan (lines 11776-11800). Lock-free scan without mutations or side effects. Gracefully handles unreadable candidate files.
* **Redaction/Ordering:** Stdout outputs header and candidate filenames list.
* **Comparator:** NORMALIZED (candidate filename list ordering).
* **Specific Argv Comparators:**
  * **Safety:** Non-destructive read scan.
  * **Cwd/Env/Stdin:** Resolves parent of `ai_root`. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Scans workspace root directory for leftover temporary or probe files.
* **Fixtures:**
  * **Positive (`fix-transient-scan-pos-01`, NYI):** Pre-state: `ai_root.parent` contains leftover temporary file `temp_probe_01.tmp`. Request: `transient-scan`. Expected exit: 0. Output: Header `transient-candidates` followed by `temp_probe_01.tmp`. Post-state: Unchanged.
  * **Invalid (`fix-transient-scan-inv-01`, NYI):** Pre-state: Clean workspace with no transient files matching criteria. Request: `transient-scan`. Expected exit: 0. Output: `No transient root-file candidates found.`. Post-state: Unchanged.
  * **Auth (`fix-transient-scan-auth-01`, NYI):** Pre-state: Directory read permission denied on `ai_root.parent`. Request: `transient-scan`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-transient-scan-rec-01`, NYI):** Pre-state: 8-character file `abcd1234` exists with size 4 bytes but locked against read. Request: `transient-scan`. Recovery injection: Hub catches OSError gracefully and skips binary probe check. Expected exit: 0. Output: Clean scan completion. Post-state: Unchanged.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 12. directive-add
* **Input Schema:** `ai_root: Path`, `rule: str` (via `--rule` or `--text`, required), `source_peer: str` (via `--peer` or `--from`, defaults "system"), `ttl_hours: int = 6` (via `--ttl-hours`), `clear_condition: str = "manual"` (via `--clear-condition`). Validation: Requires non-empty `rule` (exits 1 if missing). Authorization: Standing governance / runtime directive administration.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] DIRECTIVE-ADD {entry['id']} | source={source_peer} | expires_in={ttl_hours}h | rule={rule[:80]}` to stdout (line 10033). Error (missing rule): Exit 1, prints `[HUB:ERROR] directive-add requires --rule` to stderr (line 10029).
* **State Changes:** Before: Existing runtime directives in `_runtime_directives_path(ai_root)` (`_sys/ai/runtime-directives.jsonl`). After: Appends JSON object with fields `id` (`RD-YYYYMMDD-NNN`), `rule`, `source_peer`, `trigger_reason: "manual"`, `trigger_detail: ""`, `effective`, `expires`, `ttl_hours`, `trigger_count: 1`, `clear_condition`, `status: "active"`. External effects: Activates standing runtime directive for automatic prompt injection across peer asks.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent (lines 10026-10034, 9874-9907). On every invocation, increments sequence number `seq` for the current date prefix, creates a new unique directive ID `RD-YYYYMMDD-NNN`, and appends a new JSON line to `runtime-directives.jsonl` (line 9905). Plain append-only file persistence without lock.
* **Redaction/Ordering:** Stdout confirms directive ID, source, TTL, and truncated rule string.
* **Comparator:** NORMALIZED (generated ID, timestamp, expiration date).
* **Specific Argv Comparators:**
  * **Safety:** Input validation on required rule string.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Appends active runtime directive record to `runtime-directives.jsonl`.
* **Fixtures:**
  * **Positive (`fix-directive-add-pos-01`, NYI):** Pre-state: `_sys/ai/runtime-directives.jsonl` is empty or exists. Request: `directive-add --rule "Always verify git status" --peer cc --ttl-hours 4`. Expected exit: 0. Output: `[HUB] DIRECTIVE-ADD RD-20260820-001 | source=cc | expires_in=4h | rule=Always verify git status`. Post-state: `runtime-directives.jsonl` contains new active entry with ID `RD-20260820-001`.
  * **Invalid (`fix-directive-add-inv-01`, NYI):** Pre-state: Standard environment. Request: `directive-add` (missing `--rule`). Expected exit: 1. Output: `[HUB:ERROR] directive-add requires --rule` to stderr. Post-state: `runtime-directives.jsonl` unchanged.
  * **Auth (`fix-directive-add-auth-01`, NYI):** Pre-state: Write permission denied on `_sys/ai/runtime-directives.jsonl`. Request: `directive-add --rule "Test"`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-directive-add-rec-01`, NYI):** Pre-state: `_sys/ai/` directory does not exist. Request: `directive-add --rule "Init directory"`. Recovery injection: Hub creates parent directory recursively (`path.parent.mkdir(parents=True, exist_ok=True)`). Expected exit: 0. Output: `[HUB] DIRECTIVE-ADD RD-...`. Post-state: Directory created and file written.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 13. directive-list
* **Input Schema:** `ai_root: Path`. Validation: None. Filters lines from `runtime-directives.jsonl` where `status == "active"` and `expires > now`. Authorization: Standing governance / directive inspection.
* **Normalized Envelope:** Success (active directives present): Exit 0, prints TSV header `id	status	source_peer	expires	clear_condition	rule` followed by tab-delimited records (rule truncated to 60 chars) to stdout (lines 10043-10045). Success (no active directives): Exit 0, prints `No active runtime directives.` to stdout (line 10041). Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation against `runtime-directives.jsonl`. No state mutations or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read-only inspection (lines 10036-10046). Lock-free read. Ignores expired or non-active directives, and safely skips invalid JSON lines.
* **Redaction/Ordering:** Stdout outputs TSV header and active directive rows.
* **Comparator:** NORMALIZED (ISO expiration timestamps, rule snippets).
* **Specific Argv Comparators:**
  * **Safety:** Read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Lists currently unexpired, active runtime directives.
* **Fixtures:**
  * **Positive (`fix-directive-list-pos-01`, NYI):** Pre-state: `runtime-directives.jsonl` contains active, unexpired directive `RD-20260820-001`. Request: `directive-list`. Expected exit: 0. Output: TSV header `id	status	source_peer	expires	clear_condition	rule` followed by row for `RD-20260820-001`. Post-state: Unchanged.
  * **Invalid (`fix-directive-list-inv-01`, NYI):** Pre-state: `runtime-directives.jsonl` has only resolved or expired directives. Request: `directive-list`. Expected exit: 0. Output: `No active runtime directives.`. Post-state: Unchanged.
  * **Auth (`fix-directive-list-auth-01`, NYI):** Pre-state: Read permission denied on `runtime-directives.jsonl`. Request: `directive-list`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-directive-list-rec-01`, NYI):** Pre-state: `runtime-directives.jsonl` does not exist on disk. Request: `directive-list`. Recovery injection: Hub handles missing file gracefully. Expected exit: 0. Output: `No active runtime directives.`. Post-state: Unchanged.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 14. directive-clear
* **Input Schema:** `ai_root: Path`, `directive_id: str` (via `--directive-id` or `--round-id`, required). Validation: Requires non-empty `directive_id` (exits 1 if missing). Requires `runtime-directives.jsonl` to exist and contain `directive_id` (exits 1 if not found). Authorization: Standing governance / directive administration.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] DIRECTIVE-CLEAR {directive_id} | status=resolved` to stdout (line 10076). Error (missing directive ID): Exit 1, prints `[HUB:ERROR] directive-clear requires --directive-id` to stderr (line 10051). Error (missing file): Exit 1, prints `[HUB:ERROR] no runtime directives file found` to stderr (line 10055). Error (directive not found): Exit 1, prints `[HUB:ERROR] directive ID {directive_id} not found` to stderr (line 10073).
* **State Changes:** Before: Directive in `runtime-directives.jsonl` (active or already resolved). After: Rewrites `runtime-directives.jsonl` via `path.write_text(...)` setting matching entry `status = "resolved"` and `resolved_at = _now()`. External effects: Deactivates runtime directive from prompt injection.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent in-place update; succeeds on repeated calls (lines 10048-10077). On a second call with the same `directive_id`, the matching item is still found by ID (`if item.get("id") == directive_id:`, lines 10064-10067) because the check does not filter on `status == "active"`. It re-assigns `item["status"] = "resolved"`, updates `resolved_at = _now()`, rewrites the file via plain `write_text` (line 10075), prints `[HUB] DIRECTIVE-CLEAR {directive_id} | status=resolved`, and exits 0 again. Note: persistence uses direct `write_text` without temporary-file atomic replacement or lock serialization.
* **Redaction/Ordering:** Stdout confirms directive resolution.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Input validation and directive ID existence check.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Marks specific runtime directive as resolved in `runtime-directives.jsonl`.
* **Fixtures:**
  * **Positive (`fix-directive-clear-pos-01`, NYI):** Pre-state: `runtime-directives.jsonl` contains active directive `RD-20260820-001`. Request: `directive-clear --directive-id RD-20260820-001`. Expected exit: 0. Output: `[HUB] DIRECTIVE-CLEAR RD-20260820-001 | status=resolved`. Post-state: `RD-20260820-001` has `status: "resolved"` and `resolved_at` timestamp.
  * **Invalid (`fix-directive-clear-inv-01`, NYI):** Pre-state: `runtime-directives.jsonl` exists without `RD-nonexistent`. Request: `directive-clear --directive-id RD-nonexistent`. Expected exit: 1. Output: `[HUB:ERROR] directive ID RD-nonexistent not found` to stderr. Post-state: `runtime-directives.jsonl` unchanged.
  * **Auth (`fix-directive-clear-auth-01`, NYI):** Pre-state: Write permission denied on `runtime-directives.jsonl`. Request: `directive-clear --directive-id RD-20260820-001`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-directive-clear-rec-01`, NYI):** Pre-state: `runtime-directives.jsonl` contains already resolved directive `RD-20260820-001`. Request: `directive-clear --directive-id RD-20260820-001`. Recovery injection: Hub re-processes matching entry without crashing. Expected exit: 0. Output: `[HUB] DIRECTIVE-CLEAR RD-20260820-001 | status=resolved`. Post-state: Directive remains resolved with updated timestamp.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 15. lessons-list
* **Input Schema:** `ai_root: Path`, `peer_id: str | None = None` (via `--peer` or `--to`). Validation: None. Loads lessons from `_knowledge_root()/general/active-lessons.jsonl` and `ai_root/knowledge/active-lessons.jsonl`. If `peer_id` is supplied, filters via `_filter_lessons_for_peer`. Authorization: Lessons repository inspection.
* **Normalized Envelope:** Success (with peer filter): Exit 0, prints `Active lessons for {peer_id} ({len(lessons)} of {len(all_lessons)} total):` followed by formatted lines `  [{SEV}] {id} ({scope}, peers={peers}): {title}` sorted by severity (critical, high, medium, low) to stdout (lines 10084, 10098). Success (unfiltered): Exit 0, prints `Active lessons ({len(lessons)} total):` followed by sorted formatted lines to stdout (lines 10087, 10098). Success (no lessons): Exit 0, prints `  (none)` to stdout (line 10089). Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Evaluates global and workspace lesson repositories. No file mutations or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read-only operation (lines 10079-10099). Lock-free inspection without side effects or mutations. Gracefully combines global and workspace lesson repositories.
* **Redaction/Ordering:** Stdout outputs header and lesson entries sorted by severity ranking (`critical` -> `high` -> `medium` -> `low`).
* **Comparator:** NORMALIZED (counts, lesson IDs, ordering).
* **Specific Argv Comparators:**
  * **Safety:** Read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Lists active lessons sorted by severity with peer applicability filters.
* **Fixtures:**
  * **Positive (`fix-lessons-list-pos-01`, NYI):** Pre-state: `active-lessons.jsonl` contains active lesson `LL-001` with severity "high" applicable to "cc". Request: `lessons-list --peer cc`. Expected exit: 0. Output: `Active lessons for cc (1 of 1 total):` followed by `  [HIGH] LL-001 ...`. Post-state: Unchanged.
  * **Invalid (`fix-lessons-list-inv-01`, NYI):** Pre-state: Lesson repository is empty. Request: `lessons-list`. Expected exit: 0. Output: `Active lessons (0 total):` followed by `  (none)`. Post-state: Unchanged.
  * **Auth (`fix-lessons-list-auth-01`, NYI):** Pre-state: Read permission denied on `active-lessons.jsonl`. Request: `lessons-list`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-lessons-list-rec-01`, NYI):** Pre-state: Workspace lesson file exists alongside global lesson file with duplicate entries. Request: `lessons-list`. Recovery injection: Hub merges and deduplicates entries cleanly. Expected exit: 0. Output: Formatted lesson list. Post-state: Unchanged.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 16. lessons-propose
* **Input Schema:** `ai_root: Path`, `title: str` (via `--text` or `--title`, required), `rule: str` (via `--rule`, required), `category: str` (via `--category`, required), `severity: str = "medium"` (via `--severity`), `scope: str = "workspace"` (via `--scope`), `peer_ids: list[str] | None = None` (via `--peers`), `enforcement_artifact: str | None = None` (via `--enforcement-artifact`), `expires_at: str | None = None` (via `--expires-at`). Validation: Requires non-empty `title`, `rule`, and `category` (exits 1 if missing). Authorization: Continuous learning / lesson proposal interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] LESSON-PROPOSE {id} | scope={scope} | status=candidate | title={title[:60]}` followed by `      Activate with: hub.py lessons-activate --lesson-id {id}` and optional review broadcast notification `[HUB] LESSON-PROPOSE review notified → {members}` to stdout (lines 10161-10175). Error (missing required fields): Exit 1, prints `[HUB:ERROR] lessons-propose requires --title --rule --category` to stderr (line 10114).
* **State Changes:** Before: Existing lessons repository. After: Generates sequential ID `LL-YYYYMMDD-NNN`. Appends candidate lesson entry to `ai_root/knowledge/active-lessons.jsonl` (if scope == "workspace") or `_knowledge_root()/general/active-lessons.jsonl` (if global). If room members exist in `state.json`, broadcasts `LESSON_REVIEW` message with priority `P2` to all members via `action_broadcast` (lines 10168-10175). External effects: Registers candidate lesson and requests peer review.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent (lines 10101-10178). On every invocation, calculates next sequence number `seq`, generates a new unique lesson ID `LL-YYYYMMDD-NNN`, appends a new candidate line (line 10160), and broadcasts a new `LESSON_REVIEW` message to room members (lines 10174-10175). Plain append-only file persistence without lock.
* **Redaction/Ordering:** Stdout outputs proposal confirmation, activation instructions, and review notification line.
* **Comparator:** NORMALIZED (generated lesson ID, timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Required field validation and candidate status gating.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Appends candidate lesson to repository and broadcasts review request.
* **Fixtures:**
  * **Positive (`fix-lessons-propose-pos-01`, NYI):** Pre-state: `state.json` has active room with members `["cc", "cx"]`. Request: `lessons-propose --title "Measure before claiming" --rule "DIR-004 compliance" --category "governance" --severity "high" --scope "workspace"`. Expected exit: 0. Output: `[HUB] LESSON-PROPOSE LL-20260820-001 | scope=workspace | status=candidate | title=Measure before claiming` followed by `      Activate with: hub.py lessons-activate --lesson-id LL-20260820-001` and `[HUB] LESSON-PROPOSE review notified → cc,cx`. Post-state: `ai_root/knowledge/active-lessons.jsonl` contains new candidate record `LL-20260820-001`.
  * **Invalid (`fix-lessons-propose-inv-01`, NYI):** Pre-state: Standard environment. Request: `lessons-propose --title "Incomplete"` (missing `--rule` and `--category`). Expected exit: 1. Output: `[HUB:ERROR] lessons-propose requires --title --rule --category` to stderr. Post-state: Repository unchanged.
  * **Auth (`fix-lessons-propose-auth-01`, NYI):** Pre-state: Write permission denied on knowledge directory. Request: `lessons-propose --title "T" --rule "R" --category "C"`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-lessons-propose-rec-01`, NYI):** Pre-state: `knowledge/` subdirectory missing. Request: `lessons-propose --title "T" --rule "R" --category "C"`. Recovery injection: Hub creates parent directory automatically (`target.parent.mkdir(parents=True, exist_ok=True)`). Expected exit: 0. Output: Proposal confirmation to stdout. Post-state: Directory created and candidate lesson appended.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 17. lessons-activate
* **Input Schema:** `ai_root: Path`, `lesson_id: str` (via `--lesson-id` or `--round-id`, required). Validation: Requires non-empty `lesson_id` (exits 1 if missing). Searches global and workspace `active-lessons.jsonl` for candidate lesson with `id == lesson_id` and `status == "candidate"` (exits 1 if not found or already active). Gates activation via `_lesson_activation_blocker`: requires either an explicit non-empty advisory `expires_at` date or an enforcement artifact reporting passing verdict (`passed: true`, `status: "pass"|"verified"`) (exits 1 if blocked). Authorization: Lesson governance / auto-approval interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] LESSON-ACTIVATE {lesson_id} | approved_by=coordinator` to stdout (line 10299). Error (missing lesson ID): Exit 1, prints `[HUB:ERROR] lessons-activate requires --lesson-id` to stderr (line 10257). Error (blocked by gating rule): Exit 1, prints `[HUB:ERROR] lesson {lesson_id} activation blocked: {blocker}` to stderr (line 10278). Error (not found or already active): Exit 1, prints `[HUB:ERROR] lesson {lesson_id} not found or already active` to stderr (line 10302).
* **State Changes:** Before: Candidate lesson in `active-lessons.jsonl`. After: Rewrites matching `active-lessons.jsonl` via `write_text` updating item with `status = "active"`, `approval.approved_by = "coordinator"`, `approval.approved_at = now_str`, `approval.record_ref = "approval-log.jsonl"`. Appends approval entry to `_knowledge_root()/logs/approval-log.jsonl` (lines 10287-10291). Triggers best-effort p2p lesson broadcast to room members via `_try_lesson_broadcast` (line 10300). External effects: Activates standing lesson for active prompt injection and logs approval audit record.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent (lines 10254-10304). On first successful invocation, promotes status from `"candidate"` to `"active"`, appends to `approval-log.jsonl`, rewrites `active-lessons.jsonl`, and broadcasts notification. On a second call with the same `lesson_id`, the item has `status == "active"` (not `"candidate"`), so the candidate match fails at line 10275, `updated` remains `False`, and the action exits 1 with `[HUB:ERROR] lesson {lesson_id} not found or already active` (line 10302). G-bridge gating validation (`_lesson_activation_blocker`, lines 10231-10252) prevents activation of unvalidated lessons.
* **Redaction/Ordering:** Stdout confirms activation and approving role.
* **Comparator:** EXACT (lesson ID, approval role) / NORMALIZED (timestamps).
* **Specific Argv Comparators:**
  * **Safety:** G-bridge enforcement artifact verification and advisory expiry validation.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Promotes candidate lesson to active status and logs approval.
* **Fixtures:**
  * **Positive (`fix-lessons-activate-pos-01`, NYI):** Pre-state: `active-lessons.jsonl` contains candidate lesson `LL-20260820-001` with `retirement.expires_at: "2026-09-01"`. Request: `lessons-activate --lesson-id LL-20260820-001`. Expected exit: 0. Output: `[HUB] LESSON-ACTIVATE LL-20260820-001 | approved_by=coordinator`. Post-state: `active-lessons.jsonl` entry updated to `status: "active"`; `approval-log.jsonl` contains approval record.
  * **Invalid (`fix-lessons-activate-inv-01`, NYI):** Pre-state: Candidate lesson `LL-20260820-002` has no `expires_at` and missing enforcement artifact. Request: `lessons-activate --lesson-id LL-20260820-002`. Expected exit: 1. Output: `[HUB:ERROR] lesson LL-20260820-002 activation blocked: missing allowed enforcement artifact and advisory expiry` to stderr. Post-state: Lesson remains candidate.
  * **Auth (`fix-lessons-activate-auth-01`, NYI):** Pre-state: Lesson `LL-20260820-001` does not exist. Request: `lessons-activate --lesson-id LL-missing`. Expected exit: 1. Output: `[HUB:ERROR] lesson LL-missing not found or already active` to stderr. Post-state: Unchanged.
  * **Recovery (`fix-lessons-activate-rec-01`, NYI):** Pre-state: Enforcement artifact exists with valid JSON and `{"passed": true}`. Request: `lessons-activate --lesson-id LL-20260820-003`. Recovery injection: Hub parses enforcement artifact, verifies passing verdict, and approves activation. Expected exit: 0. Output: `[HUB] LESSON-ACTIVATE LL-20260820-003 | approved_by=coordinator`. Post-state: Lesson activated.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`

## 18. lessons-retire
* **Input Schema:** `ai_root: Path`, `lesson_id: str` (via `--lesson-id` or `--round-id`, required), `reason: str = ""` (via `--reason`). Validation: Requires non-empty `lesson_id` (exits 1 if missing). Searches global and workspace `active-lessons.jsonl` for active lesson with `id == lesson_id` and `status == "active"` (exits 1 if not found or not active). Authorization: Lesson retirement / lifecycle governance.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] LESSON-RETIRE {lesson_id} | reason={reason or 'manual'}` to stdout (line 10350). Error (missing lesson ID): Exit 1, prints `[HUB:ERROR] lessons-retire requires --lesson-id` to stderr (line 10321). Error (not found or not active): Exit 1, prints `[HUB:ERROR] lesson {lesson_id} not found or not active` to stderr (line 10352).
* **State Changes:** Before: Active lesson in `active-lessons.jsonl`. After: Rewrites matching `active-lessons.jsonl` via `write_text` updating item with `status = "retired"`, `retirement.retired_at = now_str`, and optional `retirement.retire_reason = reason` (lines 10338-10343). External effects: Deactivates lesson from future prompt injections.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent (lines 10318-10354). On the first call, finds the lesson with `status == "active"`, sets `status = "retired"`, rewrites the file via `write_text`, and prints `[HUB] LESSON-RETIRE {lesson_id} | reason=...` (exit 0). On a second call with the same `lesson_id`, the lesson status is now `"retired"`, so the check `if item.get("id") == lesson_id and item.get("status") == "active":` (line 10338) evaluates to False. As a result, `updated` remains `False`, and the function exits 1 with `[HUB:ERROR] lesson {lesson_id} not found or not active` (line 10352). Plain `write_text` file persistence without lock.
* **Redaction/Ordering:** Stdout confirms retirement and reason.
* **Comparator:** NORMALIZED (timestamps, reason strings).
* **Specific Argv Comparators:**
  * **Safety:** Input validation and active status prerequisite check (`status == "active"`).
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Retires active lesson in lesson repository.
* **Fixtures:**
  * **Positive (`fix-lessons-retire-pos-01`, NYI):** Pre-state: `active-lessons.jsonl` contains active lesson `LL-001` (`status: "active"`). Request: `lessons-retire --lesson-id LL-001 --reason "superseded"`. Expected exit: 0. Output: `[HUB] LESSON-RETIRE LL-001 | reason=superseded`. Post-state: `LL-001` entry in `active-lessons.jsonl` has `status: "retired"`, `retirement.retired_at` timestamp, and `retirement.retire_reason: "superseded"`.
  * **Invalid (`fix-lessons-retire-inv-01`, NYI):** Pre-state: Standard environment. Request: `lessons-retire` (missing `--lesson-id`). Expected exit: 1. Output: `[HUB:ERROR] lessons-retire requires --lesson-id` to stderr. Post-state: Lesson file unchanged.
  * **Auth (`fix-lessons-retire-auth-01`, NYI):** Pre-state: Lesson `LL-missing` does not exist in repository. Request: `lessons-retire --lesson-id LL-missing`. Expected exit: 1. Output: `[HUB:ERROR] lesson LL-missing not found or not active` to stderr. Post-state: Unchanged.
  * **Recovery (`fix-lessons-retire-rec-01`, NYI):** Pre-state: Lesson `LL-001` was already retired in a prior call (`status: "retired"`). Request: `lessons-retire --lesson-id LL-001`. Recovery injection: Hub detects non-active status and exits 1 with explanatory error. Expected exit: 1. Output: `[HUB:ERROR] lesson LL-001 not found or not active` to stderr. Post-state: Unchanged.
* **Legacy Digest:** `f748b095ecbe2ad4a14fc97443e513dbe132a53e94d61cdfb9a905cb6864a238` | **Proof Ref:** `[PROOF_REF_TBD]`
