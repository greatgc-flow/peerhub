# Phase 1 Parity Ledger - Batch 2 (18 Actions)

## 1. register-node
* **Input Schema:** `node_id: str` (via `--name`), `tier: int = 4` (via `--tier`), `node_type: str = "agent"` (via `--node-type`), `invoke: str = ""` (via `--invoke`), `invoke_args_str: str = "-p,{query}"` (via `--invoke-args`), `memory: str = "short-term"` (via `--memory`), `timeout: int = 0` (via `--timeout`). Validation: splits `invoke_args_str` by comma; rejects malformed types. Authorization: System/Host administration level, requires write access to `ai_root/nodes.json`.
* **Normalized Envelope:** Success: Exit 0, prints `[REGISTER] {node_id} (tier={tier}, invoke={invoke})` to stdout. Error: Exit 1, prints error traceback or stderr message.
* **State Changes:** Before: `ai_root/nodes.json` contains existing node registry. After: `nodes.json` updated under lock `nodes` with `data["nodes"][node_id]` containing node configuration. External effects: Newly registered node becomes discoverable and dispatchable in `_load_nodes(ai_root)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent for identical parameters (overwrites existing node record). Concurrency guarded by `_get_lock(ai_root, "nodes")`. Crash resilient via atomic write to `nodes.json`.
* **Redaction/Ordering:** Stdout prints registration confirmation line immediately upon release of lock.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Relies on local file lock `nodes`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Exact dictionary mutation in `nodes.json`.
* **Fixtures:** Positive: `fix-register-node-pos-01` (Not yet implemented), Invalid: `fix-register-node-inv-01` (Not yet implemented), Auth: `fix-register-node-auth-01` (Not yet implemented), Recovery: `fix-register-node-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 2. list-nodes
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: Routine read-only action, exempt from role guards.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] NODES ({len(nodes)})` header followed by sorted/iterated lines `  {nid}: tier={cfg.get('tier', '-')} type={cfg.get('type', '-')} invoke={cfg.get('invoke', '-')}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Fallback returns `_default_nodes()` if `nodes.json` is missing. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. Lock-free read.
* **Redaction/Ordering:** Stdout outputs header first, followed by node lines in iteration order.
* **Comparator:** NORMALIZED (node ordering and dynamic count).
* **Specific Argv Comparators:**
  * **Safety:** Read-only operation.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Formats active nodes registry from `nodes.json`.
* **Fixtures:** Positive: `fix-list-nodes-pos-01` (Not yet implemented), Invalid: `fix-list-nodes-inv-01` (Not yet implemented), Auth: `fix-list-nodes-auth-01` (Not yet implemented), Recovery: `fix-list-nodes-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 3. health-update
* **Input Schema:** `peer_id: str = "cc"` (via `--peer`), `status: str = "GREEN"` (via `--status`, e.g. "GREEN", "YELLOW", "RED", "AUTO"), `jsonl_mb: float = 0.0` (via `--jsonl-mb`), `failures: int = 0` (via `--failures`), `extra: dict | None = None`, `availability: dict | None = None`. Validation: `peer_id` validated against enabled peers in `_load_orchestration()`. If peer disabled/unknown, prints refusal and exits 0. Authorization: Zero-token exempt system action (`_SYSTEM_EXEMPT_ACTIONS`).
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] HEALTH-UPDATE {peer_id} | status={computed_status} jsonl={jsonl_mb:.2f}MB` to stdout. Refusal: Exit 0, prints `[HUB] HEALTH-UPDATE REFUSED: {peer_id} is not an enabled peer.`. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Peer health in `_sys/<peer>/health.json`. After: `health.json` updated under lock `health_{peer_id}`. Sets `context_health.status`, `jsonl_mb`, `checked_at`. Updates `session_health.consecutive_failures`, `session_count_today`, `last_success_at` (on success). Infers or merges `availability` (`gate_open`, `entrypoint_ok`, `authenticated`). Emits log to `_log_p2p("HEALTH", ...)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent update for identical inputs. Automatic status resolution when status is `"AUTO"` based on protocol config thresholds. Uses local lock `health_{peer_id}`.
* **Redaction/Ordering:** Stdout prints single status confirmation line.
* **Comparator:** NORMALIZED (timestamps and floating-point MB representations vary).
* **Specific Argv Comparators:**
  * **Safety:** Local lock `health_{peer_id}` around health file write.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Writes deterministic health telemetry to peer's local `health.json`.
* **Fixtures:** Positive: `fix-health-update-pos-01` (Not yet implemented), Invalid: `fix-health-update-inv-01` (Not yet implemented), Auth: `fix-health-update-auth-01` (Not yet implemented), Recovery: `fix-health-update-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 4. health-check
* **Input Schema:** `peer_filter: str | None = None` (via `--peer`), `ai_root: Path | None = None`, `recover: bool = False` (via `--recover`). Validation: Filters to valid peer names if specified. Authorization: System exempt (`_SYSTEM_EXEMPT_ACTIONS`), zero-token read/reconcile.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB:GATE] HEALTH | {peer_1}={status}({mb}MB) ...` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: `health.json` files on disk. After: Without `--recover`, read-only state check with live PID liveness verification (`_pid_alive`). With `--recover`, if effective state is STALE or dead PID is attached to GREEN status, marks status as STALE, updates `stale_marked_at`, removes dead PID, and writes `health.json`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent evaluation and safe reconciliation. Non-blocking PID verification via `os.kill(pid, 0)`.
* **Redaction/Ordering:** Stdout emits single-line space-separated summary for all enabled peers.
* **Comparator:** NORMALIZED (health status strings, memory sizes).
* **Specific Argv Comparators:**
  * **Safety:** Non-destructive probe; writes only on explicit `--recover` flag.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution without child process spawning.
  * **Observed Semantics:** Reconciles nominal health status with OS-level process state.
* **Fixtures:** Positive: `fix-health-check-pos-01` (Not yet implemented), Invalid: `fix-health-check-inv-01` (Not yet implemented), Auth: `fix-health-check-auth-01` (Not yet implemented), Recovery: `fix-health-check-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 5. peer-status
* **Input Schema:** `node_id: str | None = None` (via `--peer`), `include_all: bool = False` (via `--all`). Validation: Validates `node_id` against normalized orchestration; prints error if unknown. Authorization: System zero-token inspection.
* **Normalized Envelope:** Success: Exit 0, prints TSV header `PEER	LIFECYCLE	GATE	HEALTH	VERSION	DETAILS` followed by tab-separated peer records to stdout. Error: Exit 1 on unhandled exception; prints `[HUB:ERROR] unknown peer: {node_id}` to stderr if node not found.
* **State Changes:** Before: Peer health on disk. After: Triggers `_refresh_peer_health_live()` and runs safe status check probes (`version_only` class). Persists discovered `cli_version`, `cli_version_source`, and `cli_version_checked_at` into `health.json`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent status check with live version cache update. Subprocess execution strictly restricted to allowlisted probe classes (`version_only`, `local_config_presence`, `local_session_listing`).
* **Redaction/Ordering:** Stdout prints TSV header line followed by deterministic tab-separated rows.
* **Comparator:** NORMALIZED (CLI versions, timestamps, health status).
* **Specific Argv Comparators:**
  * **Safety:** Safe subprocess invocation restricted to allowlisted commands.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin.
  * **Transport/Process-Tree:** Spawns isolated short-lived version probe subprocesses.
  * **Observed Semantics:** Outputs tabular lifecycle, gate, health, version, and failure details.
* **Fixtures:** Positive: `fix-peer-status-pos-01` (Not yet implemented), Invalid: `fix-peer-status-inv-01` (Not yet implemented), Auth: `fix-peer-status-auth-01` (Not yet implemented), Recovery: `fix-peer-status-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 6. context-fill
* **Input Schema:** `ai_root: Path`, `sections: list[str] | None = None` (via `--sections`, comma-separated string), `frame: bool = False` (via `--frame`). Defaults: Protocol config sections or `["GOAL", "PENDING_ISSUES", "KEY_DECISIONS", "ACTIVE_THREADS"]`. Authorization: System exempt (`_SYSTEM_EXEMPT_ACTIONS`), zero-token read.
* **Normalized Envelope:** Success: Exit 0, prints formatted markdown context block bounded by `<!-- context-fill | room={room_id} | sections={wanted} -->` and `<!-- /context-fill -->`. (If special section `"lessons"` requested, prints compiled lessons block). No active room/handoff: Exit 0, prints notice `[HUB] CONTEXT-FILL: no active room` or `[HUB] CONTEXT-FILL: no handoff.md found`. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Reads `state.json`, `sessions/{room_id}/handoff.md`, or knowledge base lessons. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. When `frame=True`, injects non-imperative reference-state neutralizer header to prevent conversational LLM prompt drift.
* **Redaction/Ordering:** Stdout outputs framing header (if enabled), XML opening marker, selected section blocks in order, and closing XML marker.
* **Comparator:** EXACT (for identical handoff content) / NORMALIZED.
* **Specific Argv Comparators:**
  * **Safety:** Pure read-only operation.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Extracts and renders requested markdown sections from active room handoff.
* **Fixtures:** Positive: `fix-context-fill-pos-01` (Not yet implemented), Invalid: `fix-context-fill-inv-01` (Not yet implemented), Auth: `fix-context-fill-auth-01` (Not yet implemented), Recovery: `fix-context-fill-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 7. checkpoint
* **Input Schema:** `ai_root: Path`, `agent: str = "unknown"` (via `--agent`), `note: str` (via `--msg`, required). Validation: Requires non-empty `--msg`; exits 1 if missing. Authorization: Permitted for active room participants.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] CHECKPOINT {agent} | room={room_id} | {note[:80]}` to stdout. Error: Exit 1, prints `[HUB] checkpoint requires --msg` or `[HUB] CHECKPOINT: no active room` to stderr.
* **State Changes:** Before: `sessions/{room_id}/handoff.md` contains active thread entries. After: Appends timestamped entry `[{ts}] ({agent}) {note}` to `ACTIVE_THREADS` section of `handoff.md`. Emits p2p log `_log_p2p("CHECKPOINT", ...)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Append-only mutation. Correlated with active `room_id` in `state.json`.
* **Redaction/Ordering:** Truncates note to 80 characters in stdout confirmation and 60 characters in p2p log.
* **Comparator:** NORMALIZED (timestamps and room IDs vary).
* **Specific Argv Comparators:**
  * **Safety:** Appends to local handoff markdown file.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Records in-session progress checkpoint in room handoff.
* **Fixtures:** Positive: `fix-checkpoint-pos-01` (Not yet implemented), Invalid: `fix-checkpoint-inv-01` (Not yet implemented), Auth: `fix-checkpoint-auth-01` (Not yet implemented), Recovery: `fix-checkpoint-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 8. peer-quarantine
* **Input Schema:** `ai_root: Path`, `peer_id: str` (via `--peer` or `--target`), `reason: str = ""` (via `--reason`). Validation: Identifies target peer directory. Authorization: System exempt (`_SYSTEM_EXEMPT_ACTIONS`), operational safety control.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] PEER-QUARANTINE {peer_id} | reason={reason or 'manual'}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Peer health nominal or degraded. After: Mutates `_sys/<peer>/health.json` with `context_health.status = "RED"`, `checked_at = _now()`, `session_health.last_failure_reason = reason`, `availability.gate_open = False`, `availability.quarantined = True`. Appends quarantine event to `PENDING_ISSUES` in `handoff.md`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent quarantine enforcement. Immediately isolates peer from task dispatching and auto-routing.
* **Redaction/Ordering:** Stdout confirms quarantine action and recorded reason.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Modifies peer health state and active handoff.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Closes dispatch gate and flags peer as quarantined.
* **Fixtures:** Positive: `fix-peer-quarantine-pos-01` (Not yet implemented), Invalid: `fix-peer-quarantine-inv-01` (Not yet implemented), Auth: `fix-peer-quarantine-auth-01` (Not yet implemented), Recovery: `fix-peer-quarantine-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 9. peer-recover
* **Input Schema:** `ai_root: Path`, `peer_id: str` (via `--peer` or `--target`, accepts specific peer ID or `"all"`), `reason: str = ""` (via `--reason`). Validation: Validates target peer ID. Authorization: System exempt (`_SYSTEM_EXEMPT_ACTIONS`).
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] PEER-RECOVER {peer_id} | reason={reason or 'manual'}` to stdout (iterates for each peer if `"all"`). Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Quarantined or failing peer health state. After: Mutates `_sys/<peer>/health.json` resetting `context_health.status = "GREEN"`, `session_health.consecutive_failures = 0`, `last_failure_reason = None`, `last_success_at = _now()`, `availability.gate_open = True`, `availability.quarantined = False`, `availability.rate_limit_state = "ok"`, removes error flags (`sandbox_blocked`, `workspace_not_trusted`, `retry_hint`). Resets profile gates. Appends recovery item to `RECENT_COMPLETED` in `handoff.md`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent recovery reset. Reopens peer routing gates across all profiles.
* **Redaction/Ordering:** Stdout confirms recovery.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Resets failure counters and quarantine status.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Restores peer health to GREEN and unlocks routing gate.
* **Fixtures:** Positive: `fix-peer-recover-pos-01` (Not yet implemented), Invalid: `fix-peer-recover-inv-01` (Not yet implemented), Auth: `fix-peer-recover-auth-01` (Not yet implemented), Recovery: `fix-peer-recover-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 10. new-topic
* **Input Schema:** `ai_root: Path`, `subject: str = ""` (via `--subject` or `--mission`). Validation: None. Authorization: Room governance action.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] NEW-TOPIC {new_room} | from={old_room or 'none'} | subject={subject}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Existing room session in `state.json`. After: Archives old room handoff to `_archive/rooms/{old_room}_handoff.md` if policy enabled. Generates new `room_id` (`room-xxxx`) and member short IDs. Updates `state.json` under lock `state` (`room_id`, `members`, `mission = subject`, `blocked = None`, `phase = "new-topic"`, `updated_at`). Creates `sessions/{new_room}/handoff.md` carrying forward specified sections (e.g. `KEY_DECISIONS`). Clears peer sessions across all routable root peers.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Lifecycle transition action. Creates new correlation room ID. Flushes outdated peer session scopes. Atomic state updates under lock `state`.
* **Redaction/Ordering:** Stdout prints transition summary including new and old room IDs.
* **Comparator:** NORMALIZED (generated room ID and timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Global state lock `state`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Session rotation with selective handoff propagation.
* **Fixtures:** Positive: `fix-new-topic-pos-01` (Not yet implemented), Invalid: `fix-new-topic-inv-01` (Not yet implemented), Auth: `fix-new-topic-auth-01` (Not yet implemented), Recovery: `fix-new-topic-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 11. clear-room
* **Input Schema:** `ai_root: Path`, `subject: str = ""` (via `--subject` or `--mission`). Validation: None. Authorization: Room governance action.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] CLEAR-ROOM {new_room} | from={old_room or 'none'} | subject={subject}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Active room and messages in `mailbox.json`. After: Archives `mailbox.json` to `_archive/mailbox/{old_room}_mailbox.json` if configured. Empties `mailbox.json` (`{"messages": [], "unread_count": 0}`) and cleans orphaned payloads under lock `mailbox`. Generates fresh `room_id`. Updates `state.json` under lock `state` (`phase = "clear-room"`). Initializes fresh `sessions/{new_room}/handoff.md`. Clears peer session scopes.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Complete room and mailbox reset while preserving audit archive. Atomic updates guarded by `mailbox` and `state` locks.
* **Redaction/Ordering:** Stdout confirms room clearance and new room ID.
* **Comparator:** NORMALIZED (generated room ID and timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Double lock (`mailbox` and `state`).
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Complete room reset, mailbox wipe, and payload garbage collection.
* **Fixtures:** Positive: `fix-clear-room-pos-01` (Not yet implemented), Invalid: `fix-clear-room-inv-01` (Not yet implemented), Auth: `fix-clear-room-auth-01` (Not yet implemented), Recovery: `fix-clear-room-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 12. preflight
* **Input Schema:** `ai_root: Path`, `cmd: str` (via `--cmd`, required), `shell: str | None = None` (via `--shell`), `peer: str | None = None` (via `--peer` or `--agent`). Validation: Requires non-empty `--cmd`; exits 1 if missing. Authorization: Command classification guard, safe inspection.
* **Normalized Envelope:** Success: Exit 0, prints formatted JSON object with classification fields (`command`, `shell`, `classification`, `allowed`, `matched_rule`, `reason`) to stdout. Error: Exit 1, prints `[HUB] preflight requires --cmd` to stderr if `--cmd` omitted.
* **State Changes:** Before: Operational state. After: Pure classification engine matching against regex patterns (`blocked_patterns`, `mutating_patterns`, `read_only_patterns`). If `peer` is provided and `allowed == False`, records operational error via `action_report_error`. Otherwise read-only.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent classification logic. Subprocess-free pure regex evaluation.
* **Redaction/Ordering:** Stdout outputs indented 2-space JSON representation.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Pure regex inspection without shell execution.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Evaluates command string against guard matrix policies.
* **Fixtures:** Positive: `fix-preflight-pos-01` (Not yet implemented), Invalid: `fix-preflight-inv-01` (Not yet implemented), Auth: `fix-preflight-auth-01` (Not yet implemented), Recovery: `fix-preflight-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 13. context-hash
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: System exempt (`_SYSTEM_EXEMPT_ACTIONS`), read-only state hash.
* **Normalized Envelope:** Success: Exit 0, prints single hex digest string to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Reads configured state files (e.g. `state.json`, room handoffs) with newline normalization (`\r\n` -> `\n`) and computes incremental SHA-256 hash. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent. Deterministic cryptographic hash of active context state.
* **Redaction/Ordering:** Stdout prints exclusively the hex digest string.
* **Comparator:** EXACT (for identical underlying context files).
* **Specific Argv Comparators:**
  * **Safety:** Pure read-only operation.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Cryptographic context digest computation.
* **Fixtures:** Positive: `fix-context-hash-pos-01` (Not yet implemented), Invalid: `fix-context-hash-inv-01` (Not yet implemented), Auth: `fix-context-hash-auth-01` (Not yet implemented), Recovery: `fix-context-hash-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 14. report-error
* **Input Schema:** `ai_root: Path`, `peer: str = "unknown"` (via `--peer` or `--agent`), `pattern: str = "unknown"` (via `--pattern` or `--reason`), `detail: str = ""` (via `--detail`), `severity: str = "warn"` (via `--severity`). Validation: None. Authorization: Operational error reporting interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] operational-error recorded peer={peer} pattern={pattern} count={count}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: `_sys/data/operational_errors.jsonl` contains prior error logs. After: Appends JSON line with `ts`, `peer`, `pattern`, `severity`, `detail` to `operational_errors.jsonl`. Reads file to compute matching `(peer, pattern)` error count; if count >= threshold (default 3), triggers automatic `action_peer_quarantine`. Integrates with `_HubError` if available for error/fatal severities.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Append-only error journal. Threshold-based automated quarantine escalation.
* **Redaction/Ordering:** Stdout confirms recorded error pattern and cumulative count.
* **Comparator:** NORMALIZED (timestamps and incrementing error count).
* **Specific Argv Comparators:**
  * **Safety:** Appends to operational log; triggers peer quarantine upon threshold breach.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Records operational failure and enforces error escalation policy.
* **Fixtures:** Positive: `fix-report-error-pos-01` (Not yet implemented), Invalid: `fix-report-error-inv-01` (Not yet implemented), Auth: `fix-report-error-auth-01` (Not yet implemented), Recovery: `fix-report-error-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 15. feedback-add
* **Input Schema:** `ai_root: Path`, `source_peer: str = "unknown"` (via `--peer` or `--from`), `category: str = "other"` (via `--category`), `severity: str = "medium"` (via `--severity`), `title: str = "unknown gap"` (via `--subject` or `--msg`), `detail: str = ""` (via `--detail`). Validation: None. Authorization: Feedback loop reporting by peer or coordinator.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] FEEDBACK-ADD {event['id']} | peer={source_peer} | title={title}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: `feedback.jsonl` contains existing feedback entries. After: Computes next sequential ID `GAP-YYYYMMDD-NNN`. Appends new JSON record (`id`, `ts`, `source_peer`, `category`, `severity`, `title`, `detail`, `status: "open"`, `owner: None`) to `feedback.jsonl` under lock `feedback`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Append-only structured feedback registry. Sequential ID generation serialized under lock `feedback`.
* **Redaction/Ordering:** Stdout outputs feedback ID and title confirmation line.
* **Comparator:** NORMALIZED (generated GAP ID and timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Serialized under local file lock `feedback`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Appends gap item to structured feedback journal.
* **Fixtures:** Positive: `fix-feedback-add-pos-01` (Not yet implemented), Invalid: `fix-feedback-add-inv-01` (Not yet implemented), Auth: `fix-feedback-add-auth-01` (Not yet implemented), Recovery: `fix-feedback-add-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 16. feedback-list
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: Read-only feedback review.
* **Normalized Envelope:** Success: Exit 0, prints TSV header `id	status	severity	category	title` followed by feedback rows to stdout (or `No feedback records found.` if file missing). Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Reads `feedback.jsonl`. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. Lock-free read.
* **Redaction/Ordering:** Stdout outputs TSV header followed by tab-delimited records.
* **Comparator:** NORMALIZED (feedback records list).
* **Specific Argv Comparators:**
  * **Safety:** Pure read-only operation.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Tabular rendering of stored feedback entries.
* **Fixtures:** Positive: `fix-feedback-list-pos-01` (Not yet implemented), Invalid: `fix-feedback-list-inv-01` (Not yet implemented), Auth: `fix-feedback-list-auth-01` (Not yet implemented), Recovery: `fix-feedback-list-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 17. feedback-resolve
* **Input Schema:** `ai_root: Path`, `feedback_id: str` (via `--feedback-id` or `--round-id`), `status: str = "done"` (via `--status`), `owner: str | None = None` (via `--agent` or `--peer`). Validation: `feedback_id` must exist in `feedback.jsonl`. Exits 1 if file missing or ID not found. Authorization: Feedback management.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] FEEDBACK-RESOLVE {feedback_id} | status={status}` to stdout. Error: Exit 1, prints `[HUB:ERROR] feedback file not found` or `[HUB:ERROR] feedback ID {feedback_id} not found` to stderr.
* **State Changes:** Before: Targeted feedback item has `status: "open"`. After: Modifies `feedback.jsonl` under lock `feedback` updating `status = status`, `resolved_at = _now()`, and `owner` (if provided).
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent resolution status updater. Concurrency serialized via lock `feedback`.
* **Redaction/Ordering:** Stdout confirms resolution status for target feedback ID.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Guarded by local file lock `feedback`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Updates status and resolution timestamp in feedback file.
* **Fixtures:** Positive: `fix-feedback-resolve-pos-01` (Not yet implemented), Invalid: `fix-feedback-resolve-inv-01` (Not yet implemented), Auth: `fix-feedback-resolve-auth-01` (Not yet implemented), Recovery: `fix-feedback-resolve-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 18. artifact-claim
* **Input Schema:** `ai_root: Path`, `artifact_name: str` (via `--name`), `owner: str = "unknown"` (via `--peer` or `--agent`). Validation: If artifact is already claimed by another owner and status != "finalized", rejects with Exit 1. Authorization: Governed artifact lifecycle management.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] ARTIFACT-CLAIM {artifact_name} | owner={owner or 'unknown'}` to stdout. Error: Exit 1, prints `[HUB:ERROR] artifact {artifact_name} is already claimed by {existing_owner}` to stderr.
* **State Changes:** Before: `artifact_metadata.json` has existing artifact dictionary or empty state. After: Updates `artifact_metadata.json` under lock `artifact` with artifact entry (`artifact`, `owner`, `mode = "single_owner_merge"`, `drafts`, `status = "claimed"`, `claimed_at = _now()`, `hash = ""`).
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent re-claim for the same owner. Mutual exclusion between distinct peers on unfinalized artifacts. Atomic write under lock `artifact`.
* **Redaction/Ordering:** Stdout confirms claimed artifact name and owner.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Guarded by local lock `artifact`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Claims artifact ownership and prevents concurrent conflicting claims.
* **Fixtures:** Positive: `fix-artifact-claim-pos-01` (Not yet implemented), Invalid: `fix-artifact-claim-inv-01` (Not yet implemented), Auth: `fix-artifact-claim-auth-01` (Not yet implemented), Recovery: `fix-artifact-claim-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`
