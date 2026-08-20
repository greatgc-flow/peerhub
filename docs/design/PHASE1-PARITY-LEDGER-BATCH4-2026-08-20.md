# Phase 1 Parity Ledger - Batch 4 (18 Actions)

## 1. task-status
* **Input Schema:** `ai_root: Path`, `task_id: str | None = None` (via `--task-id` or `--id`). Validation: None (pure read). Authorization: Unrestricted read-only inspection.
* **Normalized Envelope:** Success (single task found): Exit 0, prints formatted 2-space indented JSON object of task record (`data.get(task_id, {})`) to stdout. Success (all tasks list): Exit 0, prints TSV header `task_id	owner	status	updated_at	checkpoints` followed by tab-delimited records to stdout. Success (no task registry file): Exit 0, prints `No task registry records found.` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Reads `_task_registry_path(ai_root)` (`ai_root/task_registry.json`). No file mutations or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. Lock-free read. Crash resilient (safe on missing or partial file).
* **Redaction/Ordering:** Stdout emits either formatted JSON payload or TSV header and tabular lines.
* **Comparator:** NORMALIZED (ISO timestamps, JSON key ordering).
* **Specific Argv Comparators:**
  * **Safety:** Read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Formats active task coordination state from `task_registry.json`.
* **Fixtures:** Positive: `fix-task-status-pos-01` (Not yet implemented), Invalid: `fix-task-status-inv-01` (Not yet implemented), Auth: `fix-task-status-auth-01` (Not yet implemented), Recovery: `fix-task-status-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 2. task-failover
* **Input Schema:** `ai_root: Path`, `task_id: str` (via `--task-id` or `--id`, required), `to_peer: str` (via `--peer` or `--agent`, required), `reason: str = ""` (via `--reason` or `--detail`). Validation: Requires non-empty `task_id` and `to_peer` (exits 1 if missing). Validates target peer health via `_healthy_peer(to_peer)` (exits 2 if unhealthy). Requires existing task record in `task_registry.json` (exits 1 if not found). Authorization: Task orchestration and failure mitigation interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] TASK-FAILOVER {task_id} | {old_owner or 'unknown'} -> {to_peer}` to stdout. Error (missing arguments): Exit 1, prints `[HUB:ERROR] task-failover requires --task-id and --peer` to stderr. Error (unhealthy peer): Exit 2, prints `[HUB:ERROR] failover target {to_peer} is not healthy status={status}` to stderr. Error (task not found): Exit 1, prints `[HUB:ERROR] task {task_id} not found` to stderr.
* **State Changes:** Before: Task in `task_registry.json` owned by original peer. After: Mutates `task_registry.json` under lock `task_registry` setting `owner = to_peer`, `status = "ACTIVE"`, `updated_at = _now()`, and appending checkpoint `{"peer": to_peer, "note": f"failover from {old_owner or 'unknown'}: {reason or 'manual'}", "at": _now()}`. Appends failover record to `ACTIVE_THREADS` in `handoff.md`. External effects: Reassigns operational task responsibility to healthy peer.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent re-assignment for identical parameters. Target health gate prevents failing over to degraded peer. Concurrency serialized under lock `task_registry`. Atomic write via `_write_task_registry`.
* **Redaction/Ordering:** Stdout confirms failover source, destination, and task ID.
* **Comparator:** NORMALIZED (ISO timestamps, reason strings).
* **Specific Argv Comparators:**
  * **Safety:** Peer health precondition check (Exit 2 on failure) and `task_registry` lock serialization.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Reassigns task ownership in task registry and logs transition in handoff.
* **Fixtures:** Positive: `fix-task-failover-pos-01` (Not yet implemented), Invalid: `fix-task-failover-inv-01` (Not yet implemented), Auth: `fix-task-failover-auth-01` (Not yet implemented), Recovery: `fix-task-failover-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 3. approval-request
* **Input Schema:** `ai_root: Path`, `from_peer: str` (via `--from`, `--peer`, `--agent`, defaults "unknown"), `action: str` (via `--subject` or `--msg`), `auth_needed: str` (via `--auth-needed`), `scope: str` (via `--scope` or `--file`), `risk: str` (via `--severity`, defaults "workspace-write"), `fallback: str = ""` (via `--fallback`). Validation: Guarded by `_role_guard` (allowed roles: `coordinator`, `implementer`, `researcher`, `documenter`). Resolves human interface peer via `_select_human_interface_peer(ai_root)` (exits 2 if no eligible peer). Authorization: Role-guarded approval escalation mechanism.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] APPROVAL-REQUEST {from_peer} -> {target}` to stdout. Error (role guard failure): Exit 1 (via `_role_guard`). Error (no eligible human interface peer): Exit 2, prints `[HUB:ERROR] no eligible human_interface_peer for approval request` to stderr.
* **State Changes:** Before: Pending approval state. After: Records routing metric `human_interface_peer_selection` in `routing_metrics.jsonl`. Invokes `action_send` dispatching structured `APPROVAL_REQUEST` message with `priority="CRITICAL"` to target peer. Appends entry to `PENDING_ISSUES` in `handoff.md`. External effects: Queues critical approval notification in human interface terminal/coordinator inbox.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Durable message delivery through mailbox/maildir system. Metric logging tracks selection eligibility. Handled gracefully under crashed mailbox via atomic directory operations.
* **Redaction/Ordering:** Stdout confirms approval request dispatch to resolved target.
* **Comparator:** NORMALIZED (target peer resolution, timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Role authorization guard and human-interface peer availability check.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Escalates high-risk action request to human interface terminal peer.
* **Fixtures:** Positive: `fix-approval-request-pos-01` (Not yet implemented), Invalid: `fix-approval-request-inv-01` (Not yet implemented), Auth: `fix-approval-request-auth-01` (Not yet implemented), Recovery: `fix-approval-request-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 4. file-lock
* **Input Schema:** `ai_root: Path`, `name: str` (via `--name` or `--file`, required), `owner: str` (via `--peer` or `--agent`, required), `scope: str = ""` (via `--scope` or `--section`, defaults "file"). Validation: Requires non-empty `name` and `owner` (exits 1 if missing). Checks existing lock in `file_locks.json`: if locked by different owner, rejects with Exit 1. Authorization: Concurrency coordination / lock management.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] FILE-LOCK {name} | owner={owner}` to stdout. Error (missing arguments): Exit 1, prints `[HUB:ERROR] file-lock requires --name and --peer/--agent` to stderr. Error (lock conflict): Exit 1, prints `[HUB:ERROR] {name} is locked by {existing_owner}` to stderr.
* **State Changes:** Before: Lock state in `_file_locks_path(ai_root)` (`ai_root/file_locks.json`). After: Updates `file_locks.json` under lock `file_locks` storing `{"name": name, "owner": owner, "scope": scope or "file", "locked_at": existing_locked_at or _now()}`. External effects: Claims exclusive file reservation across collaborating peers.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Re-entrant for identical owner (preserves original `locked_at`). Serialized concurrency under local lock `file_locks`. Atomic persistence via `_write_json`.
* **Redaction/Ordering:** Stdout confirms file lock acquisition and owner.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Local lock `file_locks` and ownership conflict detection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Grants and persists exclusive named file lock to requesting peer.
* **Fixtures:** Positive: `fix-file-lock-pos-01` (Not yet implemented), Invalid: `fix-file-lock-inv-01` (Not yet implemented), Auth: `fix-file-lock-auth-01` (Not yet implemented), Recovery: `fix-file-lock-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 5. file-unlock
* **Input Schema:** `ai_root: Path`, `name: str` (via `--name` or `--file`, required), `owner: str = ""` (via `--peer` or `--agent`). Validation: Requires non-empty `name` (exits 1 if missing). If lock exists and `owner` is specified, validates that `existing.owner == owner` (exits 1 on mismatch). If file is not locked, prints warning and exits 0. Authorization: Concurrency coordination / lock release.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] FILE-UNLOCK {name}` to stdout. Warning (not locked): Exit 0, prints `[HUB:WARN] {name} is not locked` to stdout. Error (missing name): Exit 1, prints `[HUB:ERROR] file-unlock requires --name` to stderr. Error (owner mismatch): Exit 1, prints `[HUB:ERROR] {name} is locked by {existing_owner}, not {owner}` to stderr.
* **State Changes:** Before: Target file lock held in `file_locks.json`. After: Mutates `file_locks.json` under lock `file_locks` removing key `name`. External effects: Releases exclusive file reservation for other peers.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent release when lock is already absent. Owner verification guard prevents unauthorized lock releases. Concurrency guarded by local lock `file_locks`. Atomic persistence via `_write_json`.
* **Redaction/Ordering:** Stdout confirms file lock release.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Owner verification check and lock `file_locks` serialization.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Releases named file lock from lock registry.
* **Fixtures:** Positive: `fix-file-unlock-pos-01` (Not yet implemented), Invalid: `fix-file-unlock-inv-01` (Not yet implemented), Auth: `fix-file-unlock-auth-01` (Not yet implemented), Recovery: `fix-file-unlock-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 6. lock-status
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: Unrestricted read-only inspection.
* **Normalized Envelope:** Success (active locks exist): Exit 0, prints TSV header `name	owner	scope	locked_at` followed by tab-delimited records to stdout. Success (no active locks or no file): Exit 0, prints `No active file locks.` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation against `_file_locks_path(ai_root)`. No state mutations or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. Lock-free read.
* **Redaction/Ordering:** Stdout emits TSV header and lock rows or fallback message.
* **Comparator:** NORMALIZED (ISO timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Formats active file lock table from `file_locks.json`.
* **Fixtures:** Positive: `fix-lock-status-pos-01` (Not yet implemented), Invalid: `fix-lock-status-inv-01` (Not yet implemented), Auth: `fix-lock-status-auth-01` (Not yet implemented), Recovery: `fix-lock-status-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 7. profile-validate
* **Input Schema:** `node_id: str | None = None` (via `--peer`). Validation: Cross-checks configuration in `_load_model_profiles()` against `_sys/ai/status_checks.json` peer entries and `_default_nodes()`. Validates profile node mapping, `invoke_overrides` against `known_overrides`, and flag parity via `_check_flag_parity()`. Exits 1 if any validation error is detected. Authorization: System diagnostic and configuration validation.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] PROFILE-VALIDATE OK ({len(targets)} nodes checked, parity verified)` to stdout. Error: Exit 1, prints one or more `[HUB:PROFILE:ERR] {err}` lines to stderr.
* **State Changes:** Before / After: Pure read-only diagnostic validation. Evaluates static configuration files and adapter commands. No mutations or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent static configuration analysis.
* **Redaction/Ordering:** Stderr outputs all aggregated validation errors before exit. Stdout emits success confirmation summary on pass.
* **Comparator:** NORMALIZED (node count in confirmation string).
* **Specific Argv Comparators:**
  * **Safety:** Non-destructive configuration validation.
  * **Cwd/Env/Stdin:** Resolves paths relative to `_sys` core directory. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Validates model profile integrity, adapter flag parity, and status probe alignment.
* **Fixtures:** Positive: `fix-profile-validate-pos-01` (Not yet implemented), Invalid: `fix-profile-validate-inv-01` (Not yet implemented), Auth: `fix-profile-validate-auth-01` (Not yet implemented), Recovery: `fix-profile-validate-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 8. lease-status
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: Process lease lifecycle inspection.
* **Normalized Envelope:** Success (no leases file): Exit 0, prints `[HUB] No leases.json found.` to stdout. Success (empty leases file): Exit 0, prints `[HUB] No active leases.` to stdout. Success (active leases present): Exit 0, prints formatted table header `Peer     Status     PID      Alive  Expires              Heartbeat           ` and divider `------------------------------------------------------------------------------` followed by fixed-width rows to stdout. Status includes ` !` if expired or ` !INVALID_TIMESTAMP` if malformed. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only inspection of `_leases_path(ai_root)` (`ai_root/leases.json`) cross-referenced with live PID liveness probe (`psutil.pid_exists(pid)`). No file mutations or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. Non-destructive process liveness verification. Gracefully handles missing `psutil` or inaccessible PIDs.
* **Redaction/Ordering:** Fixed-width formatted table to stdout.
* **Comparator:** NORMALIZED (PIDs, ISO timestamps, process liveness states).
* **Specific Argv Comparators:**
  * **Safety:** Read-only process table query.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Renders active process leases with expiry and OS-level liveness status.
* **Fixtures:** Positive: `fix-lease-status-pos-01` (Not yet implemented), Invalid: `fix-lease-status-inv-01` (Not yet implemented), Auth: `fix-lease-status-auth-01` (Not yet implemented), Recovery: `fix-lease-status-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 9. lease-sweep
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: Zero-token exempt system watchdog action (`_SYSTEM_EXEMPT_ACTIONS`).
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] lease-sweep complete.` to stdout. Warnings: May print `[HUB:WARN] lease {lease_id} quarantined (reason=invalid_timestamp, peer={peer}, pid={pid}): {detail}` or `[HUB:WARN] expired lease {lease_id} has invalid pid={raw_pid!r}; kill skipped` to stderr. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: `leases.json` contains open leases with expired timestamps or invalid formats. After: Mutates `leases.json` under lock `leases` updating expired entries to `status = "expired"` and invalid timestamps to `status = "invalid_timestamp", quarantined = True`. After releasing lock, kills orphaned process trees via `_kill_process_tree(pid)`, records ask failure via `_record_ask_failure(root_peer_id, "lease_expired", ...)`, appends failure record to `ask_history.jsonl`, and emits p2p log `_log_p2p("SWEEP", ...)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Serialized state transition under lock `leases`. Tree-killing and failure recording run outside lock to avoid lock inversion/deadlocks. Idempotent watchdog execution.
* **Redaction/Ordering:** Quarantined lease warnings to stderr, P2P sweep log emitted, stdout confirms completion.
* **Comparator:** NORMALIZED (timestamps, process IDs).
* **Specific Argv Comparators:**
  * **Safety:** Guarded lock acquisition and psutil-safe process-tree reaper.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution; terminates orphaned child process trees.
  * **Observed Semantics:** Reaps expired or invalid process leases and updates peer health/history.
* **Fixtures:** Positive: `fix-lease-sweep-pos-01` (Not yet implemented), Invalid: `fix-lease-sweep-inv-01` (Not yet implemented), Auth: `fix-lease-sweep-auth-01` (Not yet implemented), Recovery: `fix-lease-sweep-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 10. model-status
* **Input Schema:** None (reads orchestration SSOT). Validation: None. Authorization: Unrestricted read-only inspection.
* **Normalized Envelope:** Success: Exit 0, prints TSV header `peer	status	profile	model	effort	cost	context	capabilities` followed by tab-delimited peer entries to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Reads `_load_orchestration()["hub_nodes"]` and cross-references with `_peer_effective_health(peer)`. Derives model, effort, cost, and context from default profile SSOT. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. Lock-free inspection.
* **Redaction/Ordering:** Stdout outputs TSV header followed by enabled peer records.
* **Comparator:** NORMALIZED (health status values, capabilities comma-delimited ordering).
* **Specific Argv Comparators:**
  * **Safety:** Read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Displays root-peer model defaults and operational health status.
* **Fixtures:** Positive: `fix-model-status-pos-01` (Not yet implemented), Invalid: `fix-model-status-inv-01` (Not yet implemented), Auth: `fix-model-status-auth-01` (Not yet implemented), Recovery: `fix-model-status-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 11. transient-scan
* **Input Schema:** `ai_root: Path`. Validation: None. Iterates parent directory of `ai_root` (`ai_root.parent`) searching for transient root files matching pattern `[A-Za-z0-9_-]{4,12}\.(?:tmp|log|txt)` or 8-char 4-byte files containing byte payload `b"blat"`. Authorization: Workspace hygiene inspection.
* **Normalized Envelope:** Success (candidates found): Exit 0, prints header `transient-candidates` followed by matching filenames, one per line to stdout. Success (no candidates found): Exit 0, prints `No transient root-file candidates found.` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure filesystem read scan. No files modified or deleted. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read.
* **Redaction/Ordering:** Stdout outputs header and candidate filenames list.
* **Comparator:** NORMALIZED (candidate filename list ordering).
* **Specific Argv Comparators:**
  * **Safety:** Non-destructive read scan.
  * **Cwd/Env/Stdin:** Resolves parent of `ai_root`. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Scans workspace root directory for leftover temporary or probe files.
* **Fixtures:** Positive: `fix-transient-scan-pos-01` (Not yet implemented), Invalid: `fix-transient-scan-inv-01` (Not yet implemented), Auth: `fix-transient-scan-auth-01` (Not yet implemented), Recovery: `fix-transient-scan-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 12. directive-add
* **Input Schema:** `ai_root: Path`, `rule: str` (via `--rule` or `--text`, required), `source_peer: str` (via `--peer` or `--from`, defaults "system"), `ttl_hours: int = 6` (via `--ttl-hours`), `clear_condition: str = "manual"` (via `--clear-condition`). Validation: Requires non-empty `rule` (exits 1 if missing). Authorization: Standing governance / runtime directive administration.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] DIRECTIVE-ADD {entry['id']} | source={source_peer} | expires_in={ttl_hours}h | rule={rule[:80]}` to stdout. Error (missing rule): Exit 1, prints `[HUB:ERROR] directive-add requires --rule` to stderr.
* **State Changes:** Before: Existing runtime directives in `_runtime_directives_path(ai_root)` (`_sys/ai/runtime-directives.jsonl`). After: Appends JSON object with fields `id` (`RD-YYYYMMDD-NNN`), `rule`, `source_peer`, `trigger_reason: "manual"`, `detail: ""`, `ttl_hours`, `clear_condition`, `status: "active"`, `created_at`, `expires`. External effects: Activates standing runtime directive for automatic prompt injection across peer asks.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Append-only durability. Sequential ID calculation per calendar date prefix.
* **Redaction/Ordering:** Stdout confirms directive ID, source, TTL, and truncated rule string.
* **Comparator:** NORMALIZED (generated ID, timestamp, expiration date).
* **Specific Argv Comparators:**
  * **Safety:** Input validation on required rule string.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Appends active runtime directive record to `runtime-directives.jsonl`.
* **Fixtures:** Positive: `fix-directive-add-pos-01` (Not yet implemented), Invalid: `fix-directive-add-inv-01` (Not yet implemented), Auth: `fix-directive-add-auth-01` (Not yet implemented), Recovery: `fix-directive-add-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 13. directive-list
* **Input Schema:** `ai_root: Path`. Validation: None. Filters lines from `runtime-directives.jsonl` where `status == "active"` and `expires > now`. Authorization: Standing governance / directive inspection.
* **Normalized Envelope:** Success (active directives present): Exit 0, prints TSV header `id	status	source_peer	expires	clear_condition	rule` followed by tab-delimited records (rule truncated to 60 chars) to stdout. Success (no active directives): Exit 0, prints `No active runtime directives.` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation against `runtime-directives.jsonl`. No state mutations or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. Tolerant of missing or corrupt JSONL lines.
* **Redaction/Ordering:** Stdout outputs TSV header and active directive rows.
* **Comparator:** NORMALIZED (ISO expiration timestamps, rule snippets).
* **Specific Argv Comparators:**
  * **Safety:** Read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Lists currently unexpired, active runtime directives.
* **Fixtures:** Positive: `fix-directive-list-pos-01` (Not yet implemented), Invalid: `fix-directive-list-inv-01` (Not yet implemented), Auth: `fix-directive-list-auth-01` (Not yet implemented), Recovery: `fix-directive-list-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 14. directive-clear
* **Input Schema:** `ai_root: Path`, `directive_id: str` (via `--directive-id` or `--round-id`, required). Validation: Requires non-empty `directive_id` (exits 1 if missing). Requires `runtime-directives.jsonl` to exist and contain `directive_id` (exits 1 if not found). Authorization: Standing governance / directive administration.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] DIRECTIVE-CLEAR {directive_id} | status=resolved` to stdout. Error (missing directive ID): Exit 1, prints `[HUB:ERROR] directive-clear requires --directive-id` to stderr. Error (missing file): Exit 1, prints `[HUB:ERROR] no runtime directives file found` to stderr. Error (directive not found): Exit 1, prints `[HUB:ERROR] directive ID {directive_id} not found` to stderr.
* **State Changes:** Before: Active directive in `runtime-directives.jsonl`. After: Rewrites `runtime-directives.jsonl` updating matching entry with `status = "resolved"` and `resolved_at = _now()`. External effects: Deactivates runtime directive from future prompt injection.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** In-place file update. Second call fails with not found error (Exit 1). Crash resilient via complete line-by-line rewrite.
* **Redaction/Ordering:** Stdout confirms directive resolution.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Input validation and ID existence check.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Marks specific runtime directive as resolved in `runtime-directives.jsonl`.
* **Fixtures:** Positive: `fix-directive-clear-pos-01` (Not yet implemented), Invalid: `fix-directive-clear-inv-01` (Not yet implemented), Auth: `fix-directive-clear-auth-01` (Not yet implemented), Recovery: `fix-directive-clear-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 15. lessons-list
* **Input Schema:** `ai_root: Path`, `peer_id: str | None = None` (via `--peer` or `--to`). Validation: None. Loads lessons from `_knowledge_root()/general/active-lessons.jsonl` and `ai_root/knowledge/active-lessons.jsonl`. If `peer_id` is supplied, filters via `_filter_lessons_for_peer`. Authorization: Lessons repository inspection.
* **Normalized Envelope:** Success (with peer filter): Exit 0, prints `Active lessons for {peer_id} ({len(lessons)} of {len(all_lessons)} total):` followed by formatted lines `  [{SEV}] {id} ({scope}, peers={peers}): {title}` sorted by severity (critical, high, medium, low) to stdout. Success (unfiltered): Exit 0, prints `Active lessons ({len(lessons)} total):` followed by sorted formatted lines to stdout. Success (no lessons): Exit 0, prints `  (none)` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Evaluates global and workspace lesson repositories. No mutations or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read.
* **Redaction/Ordering:** Stdout outputs header and lesson entries sorted by severity ranking.
* **Comparator:** NORMALIZED (counts, lesson IDs, ordering).
* **Specific Argv Comparators:**
  * **Safety:** Read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Lists active lessons sorted by severity with peer applicability filters.
* **Fixtures:** Positive: `fix-lessons-list-pos-01` (Not yet implemented), Invalid: `fix-lessons-list-inv-01` (Not yet implemented), Auth: `fix-lessons-list-auth-01` (Not yet implemented), Recovery: `fix-lessons-list-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 16. lessons-propose
* **Input Schema:** `ai_root: Path`, `title: str` (via `--text` or `--title`, required), `rule: str` (via `--rule`, required), `category: str` (via `--category`, required), `severity: str = "medium"` (via `--severity`), `scope: str = "workspace"` (via `--scope`), `peer_ids: list[str] | None = None` (via `--peers`), `enforcement_artifact: str | None = None` (via `--enforcement-artifact`), `expires_at: str | None = None` (via `--expires-at`). Validation: Requires non-empty `title`, `rule`, and `category` (exits 1 if missing). Authorization: Continuous learning / lesson proposal interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] LESSON-PROPOSE {id} | scope={scope} | status=candidate | title={title[:60]}` followed by `      Activate with: hub.py lessons-activate --lesson-id {id}` and optional review broadcast notification `[HUB] LESSON-PROPOSE review notified → {members}` to stdout. Error (missing required fields): Exit 1, prints `[HUB:ERROR] lessons-propose requires --title --rule --category` to stderr.
* **State Changes:** Before: Existing lessons repository. After: Generates ID `LL-YYYYMMDD-NNN`. Appends candidate lesson entry to `ai_root/knowledge/active-lessons.jsonl` (if scope == "workspace") or `_knowledge_root()/general/active-lessons.jsonl` (if global). If room members exist in `state.json`, broadcasts `LESSON_REVIEW` message with priority `P2` to all members via `action_broadcast`. External effects: Registers candidate lesson and requests peer review.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Append-only durability. Sequential ID generation per calendar date. Automatic peer review notification broadcast.
* **Redaction/Ordering:** Stdout outputs proposal confirmation, activation instructions, and review notification line.
* **Comparator:** NORMALIZED (generated lesson ID, timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Required field validation and candidate status gating.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Appends candidate lesson to repository and broadcasts review request.
* **Fixtures:** Positive: `fix-lessons-propose-pos-01` (Not yet implemented), Invalid: `fix-lessons-propose-inv-01` (Not yet implemented), Auth: `fix-lessons-propose-auth-01` (Not yet implemented), Recovery: `fix-lessons-propose-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 17. lessons-activate
* **Input Schema:** `ai_root: Path`, `lesson_id: str` (via `--lesson-id` or `--round-id`, required). Validation: Requires non-empty `lesson_id` (exits 1 if missing). Searches global and workspace `active-lessons.jsonl` for candidate lesson with `id == lesson_id` (exits 1 if not found or already active). Gates activation via `_lesson_activation_blocker`: requires either an explicit non-empty advisory `expires_at` date or an enforcement artifact reporting passing verdict (`passed: true`, `status: "pass"|"verified"`) (exits 1 if blocked). Authorization: Lesson governance / auto-approval interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] LESSON-ACTIVATE {lesson_id} | approved_by=coordinator` to stdout. Error (missing lesson ID): Exit 1, prints `[HUB:ERROR] lessons-activate requires --lesson-id` to stderr. Error (blocked by gating rule): Exit 1, prints `[HUB:ERROR] lesson {lesson_id} activation blocked: {blocker}` to stderr. Error (not found or already active): Exit 1, prints `[HUB:ERROR] lesson {lesson_id} not found or already active` to stderr.
* **State Changes:** Before: Candidate lesson in `active-lessons.jsonl`. After: Rewrites matching `active-lessons.jsonl` updating item with `status = "active"`, `approval.approved_by = "coordinator"`, `approval.approved_at = now_str`, `approval.record_ref = "approval-log.jsonl"`. Appends entry to `_knowledge_root()/logs/approval-log.jsonl`. Triggers best-effort p2p lesson broadcast to room members via `_try_lesson_broadcast`. External effects: Activates standing lesson for active prompt injection and logs approval audit record.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strict G-bridge gating validation prevents activation of unvalidated lessons. Atomic line-by-line file update. Re-attempt on activated lesson fails with already active error.
* **Redaction/Ordering:** Stdout confirms activation and approving role.
* **Comparator:** EXACT (lesson ID, approval role) / NORMALIZED (timestamps).
* **Specific Argv Comparators:**
  * **Safety:** G-bridge enforcement artifact verification and advisory expiry validation.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Promotes candidate lesson to active status and logs approval.
* **Fixtures:** Positive: `fix-lessons-activate-pos-01` (Not yet implemented), Invalid: `fix-lessons-activate-inv-01` (Not yet implemented), Auth: `fix-lessons-activate-auth-01` (Not yet implemented), Recovery: `fix-lessons-activate-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 18. lessons-retire
* **Input Schema:** `ai_root: Path`, `lesson_id: str` (via `--lesson-id` or `--round-id`, required), `reason: str = ""` (via `--reason`). Validation: Requires non-empty `lesson_id` (exits 1 if missing). Searches global and workspace `active-lessons.jsonl` for active lesson with `id == lesson_id` (exits 1 if not found). Authorization: Lesson retirement / lifecycle governance.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] LESSON-RETIRE {lesson_id} | reason={reason or 'manual'}` to stdout. Error (missing lesson ID): Exit 1, prints `[HUB:ERROR] lessons-retire requires --lesson-id` to stderr. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Active lesson in `active-lessons.jsonl`. After: Rewrites matching `active-lessons.jsonl` updating item with `status = "retired"`, `retirement.retired_at = now_str`, and optional `retirement.retire_reason = reason`. External effects: Deactivates lesson from future prompt injections.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** In-place file update. Idempotent on already-retired lessons (skips update if status != active).
* **Redaction/Ordering:** Stdout confirms retirement and reason.
* **Comparator:** NORMALIZED (timestamps, reason strings).
* **Specific Argv Comparators:**
  * **Safety:** Input validation and active state verification.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Retires active lesson in lesson repository.
* **Fixtures:** Positive: `fix-lessons-retire-pos-01` (Not yet implemented), Invalid: `fix-lessons-retire-inv-01` (Not yet implemented), Auth: `fix-lessons-retire-auth-01` (Not yet implemented), Recovery: `fix-lessons-retire-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`
