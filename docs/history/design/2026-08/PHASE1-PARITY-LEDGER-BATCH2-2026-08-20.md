# Phase 1 Parity Ledger - Batch 2 (18 Actions)

## 1. register-node
* **Input Schema:** `ai_root: Path`, `node_id: str` (via `--name`), `tier: int = 4` (via `--tier`), `node_type: str = "agent"` (via `--node-type`), `invoke: str = ""` (via `--invoke`), `invoke_args_str: str = "-p,{query}"` (via `--invoke-args`), `memory: str = "short-term"` (via `--memory`), `timeout: int = 0` (via `--timeout`). Validation: splits `invoke_args_str` by comma, stripping whitespace and empty tokens. Authorization: System/Host administration level, requires write access to `ai_root/nodes.json`.
* **Normalized Envelope:** Success: Exit 0, prints `[REGISTER] {node_id} (tier={tier}, invoke={invoke})` to stdout. Error: Exit 1 on unhandled exception; Exit 2 on argparse validation failure.
* **State Changes:** Before: `ai_root/nodes.json` contains existing node registry (or defaults to `_default_nodes()`). After: `nodes.json` updated under lock `nodes` with `data["nodes"][node_id]` populated. External effects: Newly registered node becomes discoverable and dispatchable in `_load_nodes(ai_root)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent for identical parameters (lines 1668-1677). Overwrites existing node record in `nodes.json` without incrementing sequence numbers, appending logs, or altering timestamps. Concurrency guarded by `_get_lock(ai_root, "nodes")`. Crash resilient via atomic write to `nodes.json`.
* **Redaction/Ordering:** Stdout prints registration confirmation line immediately upon release of lock.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Relies on local file lock `nodes`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Exact dictionary mutation in `nodes.json`.
* **Fixtures:**
  * **Positive (`fix-register-node-pos-01`, NYI):** Pre-state: `nodes.json` exists with default nodes. Request: `register-node --name test-worker --tier 3 --invoke python --invoke-args "-m,test_worker"`. Expected exit: 0. Output: `[REGISTER] test-worker (tier=3, invoke=python)`. Post-state: `nodes.json` contains `nodes["test-worker"]` with tier 3, invoke "python", invoke_args `["-m", "test_worker"]`.
  * **Invalid (`fix-register-node-inv-01`, NYI):** Pre-state: Standard `nodes.json`. Request: `register-node --tier invalid_num`. Expected exit: 2. Output: `argument --tier: invalid int value` to stderr. Post-state: `nodes.json` unchanged.
  * **Auth (`fix-register-node-auth-01`, NYI):** Pre-state: Read-only `nodes.json` / write permission denied on `.ai/`. Request: `register-node --name worker1`. Expected exit: 1. Output: PermissionError / error to stderr. Post-state: `nodes.json` unchanged.
  * **Recovery (`fix-register-node-rec-01`, NYI):** Pre-state: File lock `nodes` held by concurrent process. Request: `register-node --name worker2`. Recovery injection: Hub retries file lock acquisition with timeout and acquires. Expected exit: 0. Output: `[REGISTER] worker2 ...`. Post-state: `nodes.json` correctly updated with `worker2`.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 2. list-nodes
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: Routine read-only action, exempt from role guards.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] NODES ({len(nodes)})` header followed by sorted/iterated lines `  {nid}: tier={cfg.get('tier', '-')} type={cfg.get('type', '-')} invoke={cfg.get('invoke', '-')}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Fallback returns `_default_nodes()` if `nodes.json` is missing. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read (lines 1651-1655). Lock-free read. Does not mutate state or append logs.
* **Redaction/Ordering:** Stdout outputs header first, followed by node lines in dictionary iteration order.
* **Comparator:** NORMALIZED (node ordering and dynamic count).
* **Specific Argv Comparators:**
  * **Safety:** Read-only operation.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Formats active nodes registry from `nodes.json`.
* **Fixtures:**
  * **Positive (`fix-list-nodes-pos-01`, NYI):** Pre-state: `nodes.json` contains 3 configured nodes (`cc`, `cx`, `ag`). Request: `list-nodes`. Expected exit: 0. Output: Header `[HUB] NODES (3)` and 3 formatted node lines. Post-state: Unchanged.
  * **Invalid (`fix-list-nodes-inv-01`, NYI):** Pre-state: `nodes.json` is missing on disk. Request: `list-nodes`. Expected exit: 0. Output: Header `[HUB] NODES ({len(_default_nodes()['nodes'])})` with default fallback nodes. Post-state: Unchanged.
  * **Auth (`fix-list-nodes-auth-01`, NYI):** Pre-state: Read permission denied on `nodes.json`. Request: `list-nodes`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-list-nodes-rec-01`, NYI):** Pre-state: `nodes.json` contains unformatted whitespace lines. Request: `list-nodes`. Recovery injection: Hub loads JSON safely and renders nodes. Expected exit: 0. Output: Formatted node list. Post-state: Unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 3. health-update
* **Input Schema:** `peer_id: str = "cc"` (via `--peer`), `status: str = "GREEN"` (via `--status`, e.g. "GREEN", "YELLOW", "RED", "AUTO"), `jsonl_mb: float = 0.0` (via `--jsonl-mb`), `failures: int = 0` (via `--failures`), `extra: dict | None = None`, `availability: dict | None = None`. Validation: `peer_id` validated against enabled peers in `_load_orchestration()`. If peer disabled/unknown, prints refusal and exits 0. Authorization: Zero-token exempt system action (`_SYSTEM_EXEMPT_ACTIONS`).
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] HEALTH-UPDATE {peer_id} | status={computed_status} jsonl={jsonl_mb:.2f}MB` to stdout. Refusal: Exit 0, prints `[HUB] HEALTH-UPDATE REFUSED: {peer_id} is not an enabled peer.`. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Peer health in `_sys/<peer>/health.json`. After: `health.json` updated under lock `health_{peer_id}`. Sets `context_health.status`, `jsonl_mb`, `checked_at = _now()`. Updates `session_health.consecutive_failures`. If GREEN/YELLOW and failures==0, advances `last_success_at` and increments `session_count_today = session_count_today + 1`. Infers or merges `availability` (`gate_open`, `entrypoint_ok`, `authenticated`). Emits log to `_log_p2p("HEALTH", ...)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. On every successful call (`failures == 0` and status GREEN/YELLOW), increments `session_health.session_count_today` by 1 (line 8073), updates `checked_at` and `last_success_at` timestamps (lines 8063, 8072), and unconditionally appends a new HEALTH event to `log.jsonl` (line 8094). Automatic status resolution when status is `"AUTO"` based on protocol config thresholds. Uses local lock `health_{peer_id}`.
* **Redaction/Ordering:** Stdout prints single status confirmation line.
* **Comparator:** NORMALIZED (timestamps and floating-point MB representations vary).
* **Specific Argv Comparators:**
  * **Safety:** Local lock `health_{peer_id}` around health file write.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Writes deterministic health telemetry to peer's local `health.json` and appends p2p log.
* **Fixtures:**
  * **Positive (`fix-health-update-pos-01`, NYI):** Pre-state: `_sys/cc/health.json` exists with `session_count_today: 2`. Request: `health-update --peer cc --status GREEN --jsonl-mb 0.35 --failures 0`. Expected exit: 0. Output: `[HUB] HEALTH-UPDATE cc | status=GREEN jsonl=0.35MB`. Post-state: `health.json` has `status: GREEN`, `jsonl_mb: 0.35`, `session_count_today: 3`; `log.jsonl` contains new HEALTH entry.
  * **Invalid (`fix-health-update-inv-01`, NYI):** Pre-state: Orchestration enabled peers = `{cc, cx, ag}`. Request: `health-update --peer unknown_node`. Expected exit: 0. Output: `[HUB] HEALTH-UPDATE REFUSED: unknown_node is not an enabled peer.`. Post-state: Unchanged.
  * **Auth (`fix-health-update-auth-01`, NYI):** Pre-state: Write permission denied on `_sys/cc/health.json`. Request: `health-update --peer cc --status GREEN`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-health-update-rec-01`, NYI):** Pre-state: Lock `health_cc` held by concurrent thread. Request: `health-update --peer cc --status GREEN`. Recovery injection: Hub waits on lock, acquires, and writes atomically. Expected exit: 0. Output: `[HUB] HEALTH-UPDATE cc ...`. Post-state: Updated health record written.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 4. health-check
* **Input Schema:** `peer_filter: str | None = None` (via `--peer`), `ai_root: Path | None = None`, `recover: bool = False` (via `--recover`). Validation: Filters to target peer if specified; otherwise all enabled peers. Authorization: System exempt (`_SYSTEM_EXEMPT_ACTIONS`), zero-token read/reconcile.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB:GATE] HEALTH | {peer_1}={status}({mb}MB) ...` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: `health.json` files on disk. After: Without `--recover`, read-only state check with live PID liveness verification (`_pid_alive`). With `--recover`, if effective state is STALE or dead PID is attached to GREEN status, marks status as STALE, updates `stale_marked_at`, removes dead PID, and writes `health.json`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent evaluation and safe reconciliation (lines 8139-8168). Read-only by default; when `--recover` is specified, converges to stable STALE state without subsequent mutations or log emissions. Non-blocking PID verification via `os.kill(pid, 0)`.
* **Redaction/Ordering:** Stdout emits single-line space-separated summary for all enabled peers.
* **Comparator:** NORMALIZED (health status strings, memory sizes).
* **Specific Argv Comparators:**
  * **Safety:** Non-destructive probe; writes only on explicit `--recover` flag.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution without child process spawning.
  * **Observed Semantics:** Reconciles nominal health status with OS-level process state.
* **Fixtures:**
  * **Positive (`fix-health-check-pos-01`, NYI):** Pre-state: `_sys/cc/health.json` exists with `status: GREEN`, `jsonl_mb: 0.4`, no dead PID. Request: `health-check --peer cc`. Expected exit: 0. Output: `[HUB:GATE] HEALTH | cc=GREEN(0.4MB)`. Post-state: Unchanged.
  * **Invalid (`fix-health-check-inv-01`, NYI):** Pre-state: `health.json` missing for target peer. Request: `health-check --peer nonexistent_peer`. Expected exit: 0. Output: `[HUB:GATE] HEALTH | nonexistent_peer=UNKNOWN`. Post-state: Unchanged.
  * **Auth (`fix-health-check-auth-01`, NYI):** Pre-state: Directory permission denied on `_sys/`. Request: `health-check`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-health-check-rec-01`, NYI):** Pre-state: `health.json` has `status: GREEN` with `active_pid: 99999` (dead process). Request: `health-check --peer cc --recover`. Recovery injection: Hub detects dead PID via `_pid_alive`, updates `status` to STALE, records `stale_marked_at`, and removes `active_pid`. Expected exit: 0. Output: `[HUB:GATE] HEALTH | cc=STALE(0.4MB)`. Post-state: `health.json` persisted with status STALE.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 5. peer-status
* **Input Schema:** `node_id: str | None = None` (via `--peer`), `include_all: bool = False` (via `--all`). Validation: Validates `node_id` against normalized orchestration; prints error if unknown. Authorization: System zero-token inspection.
* **Normalized Envelope:** Success: Exit 0, prints TSV header `PEER	LIFECYCLE	GATE	HEALTH	VERSION	DETAILS` followed by tab-separated peer records to stdout. Error: Exit 1 on unhandled exception; prints `[HUB:ERROR] unknown peer: {node_id}` to stderr if node not found.
* **State Changes:** Before: Peer health on disk. After: Triggers `_refresh_peer_health_live()` and runs safe status check probes (`version_only` class). Persists discovered `cli_version`, `cli_version_source`, and `cli_version_checked_at` timestamp into `health.json` via `_write_peer_health`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent observation probe with live version metadata cache update (lines 8632-8658). Subprocess execution strictly restricted to allowlisted probe classes (`version_only`). Updates `cli_version_checked_at` timestamp in `health.json`. Output TSV table is stable across repeated invocations.
* **Redaction/Ordering:** Stdout prints TSV header line followed by deterministic tab-separated rows.
* **Comparator:** NORMALIZED (CLI versions, timestamps, health status).
* **Specific Argv Comparators:**
  * **Safety:** Safe subprocess invocation restricted to allowlisted commands.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin.
  * **Transport/Process-Tree:** Spawns isolated short-lived version probe subprocesses.
  * **Observed Semantics:** Outputs tabular lifecycle, gate, health, version, and failure details.
* **Fixtures:**
  * **Positive (`fix-peer-status-pos-01`, NYI):** Pre-state: Enabled peers `cc`, `cx`, `ag` with health files. Request: `peer-status`. Expected exit: 0. Output: TSV table starting with `PEER	LIFECYCLE	GATE	HEALTH	VERSION	DETAILS` and rows for enabled peers. Post-state: `health.json` cached with latest `cli_version_checked_at`.
  * **Invalid (`fix-peer-status-inv-01`, NYI):** Pre-state: Standard orchestration. Request: `peer-status --peer invalid_peer_name`. Expected exit: 0 (returns after printing error to stderr). Output: `[HUB:ERROR] unknown peer: invalid_peer_name` to stderr. Post-state: Unchanged.
  * **Auth (`fix-peer-status-auth-01`, NYI):** Pre-state: CLI version probe binary inaccessible. Request: `peer-status --peer cc`. Expected exit: 0. Output: TSV table with empty/absent version column. Post-state: `cli_version_source: absent` recorded.
  * **Recovery (`fix-peer-status-rec-01`, NYI):** Pre-state: `health.json` locked during probe write. Request: `peer-status`. Recovery injection: Hub handles write failure gracefully (`except Exception: pass`) and prints status table without crashing. Expected exit: 0. Output: Full TSV status table. Post-state: Retains prior valid health file.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 6. context-fill
* **Input Schema:** `ai_root: Path`, `sections: list[str] | None = None` (via `--sections`, comma-separated string), `frame: bool = False` (via `--frame`). Defaults: Protocol config sections or `["GOAL", "PENDING_ISSUES", "KEY_DECISIONS", "ACTIVE_THREADS"]`. Authorization: System exempt (`_SYSTEM_EXEMPT_ACTIONS`), zero-token read.
* **Normalized Envelope:** Success: Exit 0, prints formatted markdown context block bounded by `<!-- context-fill | room={room_id} | sections={wanted} -->` and `<!-- /context-fill -->` (or compiled lessons block if `lessons` section requested). No active room/handoff: Exit 0, prints notice `[HUB] CONTEXT-FILL: no active room` or `[HUB] CONTEXT-FILL: no handoff.md found`. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Reads `state.json`, `sessions/{room_id}/handoff.md`, or knowledge base lessons. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read (lines 9048-9095). Lock-free read. Does not mutate state or write logs. When `frame=True`, injects non-imperative reference-state neutralizer header to prevent conversational LLM prompt drift.
* **Redaction/Ordering:** Stdout outputs framing header (if enabled), XML opening marker, selected section blocks in order, and closing XML marker.
* **Comparator:** EXACT (for identical handoff content) / NORMALIZED.
* **Specific Argv Comparators:**
  * **Safety:** Pure read-only operation.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Extracts and renders requested markdown sections from active room handoff.
* **Fixtures:**
  * **Positive (`fix-context-fill-pos-01`, NYI):** Pre-state: Active room `room-a1b2` with `handoff.md` containing `## [GOAL]
Complete task`. Request: `context-fill --sections GOAL`. Expected exit: 0. Output: `<!-- context-fill | room=room-a1b2 | sections=GOAL -->

## [GOAL]
Complete task
<!-- /context-fill -->`. Post-state: Unchanged.
  * **Invalid (`fix-context-fill-inv-01`, NYI):** Pre-state: `state.json` has `room_id: null`. Request: `context-fill`. Expected exit: 0. Output: `[HUB] CONTEXT-FILL: no active room`. Post-state: Unchanged.
  * **Auth (`fix-context-fill-auth-01`, NYI):** Pre-state: `handoff.md` file permissions restricted. Request: `context-fill`. Expected exit: 1. Output: PermissionError / error to stderr. Post-state: Unchanged.
  * **Recovery (`fix-context-fill-rec-01`, NYI):** Pre-state: Special section `lessons` requested when no active room exists. Request: `context-fill --sections lessons`. Recovery injection: Hub handles lessons independently of room presence, loads active lessons, and formats lesson block. Expected exit: 0. Output: Compiled lessons markdown block. Post-state: Unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 7. checkpoint
* **Input Schema:** `ai_root: Path`, `agent: str = "unknown"` (via `--agent`), `note: str` (via `--msg`, required). Validation: Requires non-empty `--msg`; exits 1 if missing. Authorization: Permitted for active room participants.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] CHECKPOINT {agent} | room={room_id} | {note[:80]}` to stdout. Error: Exit 1, prints `[HUB] checkpoint requires --msg` or `[HUB] CHECKPOINT: no active room` to stderr.
* **State Changes:** Before: `sessions/{room_id}/handoff.md` contains active thread entries. After: Appends timestamped entry `[{ts}] ({agent}) {note}` to `ACTIVE_THREADS` section of `handoff.md`. Emits p2p log `_log_p2p("CHECKPOINT", ...)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Unconditionally appends a new timestamped checkpoint entry to `ACTIVE_THREADS` in `handoff.md` (line 8972) and emits a new CHECKPOINT event to `log.jsonl` (line 8973) on every call. Correlated with active `room_id` in `state.json`.
* **Redaction/Ordering:** Truncates note to 80 characters in stdout confirmation and 60 characters in p2p log.
* **Comparator:** NORMALIZED (timestamps and room IDs vary).
* **Specific Argv Comparators:**
  * **Safety:** Appends to local handoff markdown file and p2p log.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Records in-session progress checkpoint in room handoff.
* **Fixtures:**
  * **Positive (`fix-checkpoint-pos-01`, NYI):** Pre-state: Active room `room-1234` with `handoff.md`. Request: `checkpoint --agent cc --msg "Completed step 1"`. Expected exit: 0. Output: `[HUB] CHECKPOINT cc | room=room-1234 | Completed step 1`. Post-state: `handoff.md` `ACTIVE_THREADS` contains new timestamped entry; `log.jsonl` has CHECKPOINT log.
  * **Invalid (`fix-checkpoint-inv-01`, NYI):** Pre-state: Active room `room-1234`. Request: `checkpoint --agent cc` (missing `--msg`). Expected exit: 1. Output: `[HUB] checkpoint requires --msg` to stderr. Post-state: `handoff.md` unchanged.
  * **Auth (`fix-checkpoint-auth-01`, NYI):** Pre-state: No active room (`state.json` has `room_id: null`). Request: `checkpoint --agent cc --msg "Test"`. Expected exit: 1. Output: `[HUB] CHECKPOINT: no active room` to stderr. Post-state: Unchanged.
  * **Recovery (`fix-checkpoint-rec-01`, NYI):** Pre-state: `handoff.md` exists without an `ACTIVE_THREADS` section header. Request: `checkpoint --agent cc --msg "Recovery note"`. Recovery injection: Hub creates or appends `## [ACTIVE_THREADS]` header and inserts entry cleanly. Expected exit: 0. Output: `[HUB] CHECKPOINT cc | room=room-1234 | Recovery note`. Post-state: `handoff.md` contains well-formed section with new item.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 8. peer-quarantine
* **Input Schema:** `ai_root: Path`, `peer_id: str` (via `--peer` or `--target`), `reason: str = ""` (via `--reason`). Validation: Identifies target peer directory. Authorization: System exempt (`_SYSTEM_EXEMPT_ACTIONS`), operational safety control.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] PEER-QUARANTINE {peer_id} | reason={reason or 'manual'}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Peer health nominal or degraded. After: Mutates `_sys/<peer>/health.json` with `context_health.status = "RED"`, `checked_at = _now()`, `session_health.last_failure_reason = reason`, `session_health.last_failure_at = _now()`, `availability.gate_open = False`, `availability.quarantined = True`. Appends quarantine event to `PENDING_ISSUES` in `handoff.md` via `_append_handoff_item`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. While health flags converge to RED/quarantined, every invocation unconditionally appends a new timestamped quarantine line to `PENDING_ISSUES` in `handoff.md` (line 3507) and refreshes `checked_at` and `last_failure_at` timestamps (lines 3499, 3502). Immediately isolates peer from task dispatching and auto-routing.
* **Redaction/Ordering:** Stdout confirms quarantine action and recorded reason.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Modifies peer health state and active handoff.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Closes dispatch gate and flags peer as quarantined.
* **Fixtures:**
  * **Positive (`fix-peer-quarantine-pos-01`, NYI):** Pre-state: `cc` health is GREEN, `gate_open: True`, active room handoff exists. Request: `peer-quarantine --peer cc --reason "repeated timeout"`. Expected exit: 0. Output: `[HUB] PEER-QUARANTINE cc | reason=repeated timeout`. Post-state: `_sys/cc/health.json` has `status: RED`, `gate_open: False`, `quarantined: True`; `handoff.md` `PENDING_ISSUES` contains quarantine entry.
  * **Invalid (`fix-peer-quarantine-inv-01`, NYI):** Pre-state: Empty peer name passed. Request: `peer-quarantine --peer ""`. Expected exit: 0 (or handled gracefully). Output: `[HUB] PEER-QUARANTINE  | reason=manual`. Post-state: Default health file path handled.
  * **Auth (`fix-peer-quarantine-auth-01`, NYI):** Pre-state: Write permission denied on `_sys/cc/health.json`. Request: `peer-quarantine --peer cc`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-peer-quarantine-rec-01`, NYI):** Pre-state: No active room handoff exists (`state.json` missing room). Request: `peer-quarantine --peer cc --reason "probe failure"`. Recovery injection: `_append_handoff_item` safely skips handoff append when room is missing; health mutation succeeds. Expected exit: 0. Output: `[HUB] PEER-QUARANTINE cc | reason=probe failure`. Post-state: `health.json` updated with RED quarantine status.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 9. peer-recover
* **Input Schema:** `ai_root: Path`, `peer_id: str` (via `--peer` or `--target`, accepts specific peer ID or `"all"`), `reason: str = ""` (via `--reason`). Validation: Validates target peer ID. Authorization: System exempt (`_SYSTEM_EXEMPT_ACTIONS`).
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] PEER-RECOVER {peer_id} | reason={reason or 'manual'}` to stdout (iterates for each peer if `"all"`). Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Quarantined or failing peer health state. After: Mutates `_sys/<peer>/health.json` resetting `context_health.status = "GREEN"`, `checked_at = _now()`, `session_health.consecutive_failures = 0`, `last_failure_reason = None`, `last_success_at = _now()`, `availability.gate_open = True`, `availability.quarantined = False`, `availability.rate_limit_state = "ok"`, removes error flags (`sandbox_blocked`, `workspace_not_trusted`, `retry_hint`). Resets profile gates (`p_data["gate_open"] = True`). Appends recovery item to `RECENT_COMPLETED` in `handoff.md` via `_append_handoff_item`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. While health.json reset converges to GREEN/unlocked, every invocation unconditionally appends a new timestamped recovery entry to `RECENT_COMPLETED` in `handoff.md` (line 3537) and advances `checked_at` and `last_success_at` timestamps (lines 3521, 3525). Reopens peer routing gates across all profiles.
* **Redaction/Ordering:** Stdout confirms recovery for target peer (or each peer when `all`).
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Resets failure counters and quarantine status.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Restores peer health to GREEN and unlocks routing gate.
* **Fixtures:**
  * **Positive (`fix-peer-recover-pos-01`, NYI):** Pre-state: `_sys/cc/health.json` is RED, `quarantined: True`, `gate_open: False`. Request: `peer-recover --peer cc --reason "manual operator unblock"`. Expected exit: 0. Output: `[HUB] PEER-RECOVER cc | reason=manual operator unblock`. Post-state: `_sys/cc/health.json` is GREEN, `gate_open: True`, `quarantined: False`, `consecutive_failures: 0`; `handoff.md` `RECENT_COMPLETED` contains recovery entry.
  * **Invalid (`fix-peer-recover-inv-01`, NYI):** Pre-state: Standard environment. Request: `peer-recover --peer all`. Expected exit: 0. Output: `[HUB] PEER-RECOVER ...` printed for every configured peer. Post-state: All peer health files reset to GREEN.
  * **Auth (`fix-peer-recover-auth-01`, NYI):** Pre-state: Write-protected `health.json`. Request: `peer-recover --peer cc`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-peer-recover-rec-01`, NYI):** Pre-state: Peer health has profile-level gate closures (`availability.profiles.effort.gate_open = False`). Request: `peer-recover --peer cc`. Recovery injection: Hub traverses all sub-profiles and re-enables `gate_open = True` while stripping `rate_limit_state`. Expected exit: 0. Output: `[HUB] PEER-RECOVER cc | reason=manual`. Post-state: Root peer and all sub-profiles fully re-enabled.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 10. new-topic
* **Input Schema:** `ai_root: Path`, `subject: str = ""` (via `--subject` or `--mission`). Validation: None. Authorization: Room governance action.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] NEW-TOPIC {new_room} | from={old_room or 'none'} | subject={subject}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Existing room session in `state.json`. After: Archives old room handoff to `_archive/rooms/{old_room}_handoff.md` if policy enabled (`archive_current_handoff: True`). Generates new `room_id` (`room-xxxx`) and member short IDs. Updates `state.json` under lock `state` (`room_id`, `members`, `mission = subject`, `blocked = None`, `phase = "new-topic"`, `updated_at = _now()`). Creates `sessions/{new_room}/handoff.md` carrying forward specified sections (e.g. `KEY_DECISIONS`). Clears peer sessions across all routable root peers via `_clear_peer_sessions`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Generates a fresh random `room_id` (`_short_id("room-")`, line 3557), creates a new `sessions/{new_room}` directory, archives the prior handoff (line 3553), and clears peer session scopes on every call. Atomic state updates under lock `state`.
* **Redaction/Ordering:** Stdout prints transition summary including new and old room IDs and subject.
* **Comparator:** NORMALIZED (generated room ID and timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Global state lock `state`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Session rotation with selective handoff propagation.
* **Fixtures:**
  * **Positive (`fix-new-topic-pos-01`, NYI):** Pre-state: Active room `room-1111` with `handoff.md` containing `## [KEY_DECISIONS]
Decision A`. Request: `new-topic --subject "Phase 2 Architecture"`. Expected exit: 0. Output: `[HUB] NEW-TOPIC room-.... | from=room-1111 | subject=Phase 2 Architecture`. Post-state: `state.json` has new `room_id`, `phase: new-topic`, `mission: Phase 2 Architecture`; `_archive/rooms/room-1111_handoff.md` created; new handoff carries forward `KEY_DECISIONS`.
  * **Invalid (`fix-new-topic-inv-01`, NYI):** Pre-state: `state.json` missing or empty. Request: `new-topic`. Expected exit: 0. Output: `[HUB] NEW-TOPIC room-.... | from=none | subject=`. Post-state: Fresh room created with default goal.
  * **Auth (`fix-new-topic-auth-01`, NYI):** Pre-state: Write-protected `state.json` or `sessions/` directory. Request: `new-topic --subject "test"`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-new-topic-rec-01`, NYI):** Pre-state: Old room directory exists but lacks `handoff.md`. Request: `new-topic --subject "New Goal"`. Recovery injection: Hub skips archiving and initializes fresh handoff with default sections without failing. Expected exit: 0. Output: `[HUB] NEW-TOPIC room-.... | from=old-room | subject=New Goal`. Post-state: Fresh room directory and handoff created cleanly.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 11. clear-room
* **Input Schema:** `ai_root: Path`, `subject: str = ""` (via `--subject` or `--mission`). Validation: None. Authorization: Room governance action.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] CLEAR-ROOM {new_room} | from={old_room or 'none'} | subject={subject}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Active room and messages in `mailbox.json`. After: Archives `mailbox.json` to `_archive/mailbox/{old_room}_mailbox.json` if configured (`archive_mailbox: True`). Empties `mailbox.json` (`{"messages": [], "unread_count": 0}`) and cleans unreferenced payloads under lock `mailbox`. Generates fresh `room_id` (`room-xxxx`) and member short IDs. Updates `state.json` under lock `state` (`phase = "clear-room"`). Initializes fresh `sessions/{new_room}/handoff.md`. Clears peer session scopes via `_clear_peer_sessions`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Generates a fresh random `room_id` on every call (line 3588), creates a new session directory, archives `mailbox.json` (line 3584), wipes mailbox, and resets sessions. Atomic updates guarded by `mailbox` and `state` locks.
* **Redaction/Ordering:** Stdout confirms room clearance, new room ID, and subject.
* **Comparator:** NORMALIZED (generated room ID and timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Double lock (`mailbox` and `state`).
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Complete room reset, mailbox wipe, and payload garbage collection.
* **Fixtures:**
  * **Positive (`fix-clear-room-pos-01`, NYI):** Pre-state: Room `room-aaaa` has 5 messages in `mailbox.json`. Request: `clear-room --subject "Fresh start"`. Expected exit: 0. Output: `[HUB] CLEAR-ROOM room-.... | from=room-aaaa | subject=Fresh start`. Post-state: `_archive/mailbox/room-aaaa_mailbox.json` created; `mailbox.json` reset to 0 messages; fresh `room-bbbb` active in `state.json`.
  * **Invalid (`fix-clear-room-inv-01`, NYI):** Pre-state: `mailbox.json` does not exist. Request: `clear-room`. Expected exit: 0. Output: `[HUB] CLEAR-ROOM room-.... | from=none | subject=`. Post-state: Fresh room and empty `mailbox.json` initialized.
  * **Auth (`fix-clear-room-auth-01`, NYI):** Pre-state: Permission denied on `mailbox.json`. Request: `clear-room`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-clear-room-rec-01`, NYI):** Pre-state: Concurrent lock on `mailbox` during clearance. Request: `clear-room --subject "Wipe"`. Recovery injection: Hub acquires `mailbox` lock, writes empty array, cleans orphaned payloads, then acquires `state` lock to update room metadata. Expected exit: 0. Output: `[HUB] CLEAR-ROOM room-.... ...`. Post-state: Cleanly reset mailbox and state.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 12. preflight
* **Input Schema:** `ai_root: Path`, `cmd: str` (via `--cmd`, required), `shell: str | None = None` (via `--shell`), `peer: str | None = None` (via `--peer` or `--agent`). Validation: Requires non-empty `--cmd`; exits 1 if missing. Authorization: Command classification guard, safe inspection.
* **Normalized Envelope:** Success: Exit 0, prints formatted JSON object with classification fields (`command`, `shell`, `classification`, `allowed`, `matched_rule`, `reason`) to stdout. Error: Exit 1, prints `[HUB] preflight requires --cmd` to stderr if `--cmd` omitted.
* **State Changes:** Before: Operational state. After: Pure classification engine matching against regex patterns (`blocked_patterns`, `mutating_patterns`, `read_only_patterns`). If `peer` is provided and `allowed == False`, records operational error via `action_report_error` which appends to `operational_errors.jsonl` and can trigger quarantine. Otherwise read-only.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Conditionally idempotent (lines 9544-9547). When command is allowed (or `peer` is omitted), it is pure read-only and strictly IDEMPOTENT. When command is blocked (`allowed == False`) and `peer` is specified, it is NOT IDEMPOTENT because each call invokes `action_report_error` which appends a new error entry to `operational_errors.jsonl` and increments the quarantine failure counter.
* **Redaction/Ordering:** Stdout outputs indented 2-space JSON representation.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Pure regex inspection without executing command.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Evaluates command string against guard matrix policies.
* **Fixtures:**
  * **Positive (`fix-preflight-pos-01`, NYI):** Pre-state: Standard guard config. Request: `preflight --cmd "git status"`. Expected exit: 0. Output: JSON with `"classification": "read_only"`, `"allowed": true`. Post-state: Unchanged.
  * **Invalid (`fix-preflight-inv-01`, NYI):** Pre-state: Standard environment. Request: `preflight` (missing `--cmd`). Expected exit: 1. Output: `[HUB] preflight requires --cmd` to stderr. Post-state: Unchanged.
  * **Auth (`fix-preflight-auth-01`, NYI):** Pre-state: Standard guard config. Request: `preflight --cmd "rm -rf /" --peer cc`. Expected exit: 0. Output: JSON with `"allowed": false`, `"matched_rule": "blocked_patterns"`. Post-state: Operational error recorded for `cc` in `operational_errors.jsonl`.
  * **Recovery (`fix-preflight-rec-01`, NYI):** Pre-state: Custom shell specified with command string. Request: `preflight --cmd "Get-Process" --shell powershell`. Recovery injection: Hub evaluates powershell-specific rules. Expected exit: 0. Output: JSON evaluation with `"shell": "powershell"`. Post-state: Unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 13. context-hash
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: System exempt (`_SYSTEM_EXEMPT_ACTIONS`), read-only state hash.
* **Normalized Envelope:** Success: Exit 0, prints single hex digest string to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Reads configured state files (e.g. `state.json`, room handoffs) with newline normalization (`
` -> `
`, `` -> `
`) and computes incremental SHA-256 hash. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read (lines 9550-9577). Deterministic cryptographic hash of active context state files.
* **Redaction/Ordering:** Stdout prints exclusively the hex digest string.
* **Comparator:** EXACT (for identical underlying context files).
* **Specific Argv Comparators:**
  * **Safety:** Pure read-only operation.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Cryptographic context digest computation over resolved sources.
* **Fixtures:**
  * **Positive (`fix-context-hash-pos-01`, NYI):** Pre-state: `state.json` exists in `ai_root`. Request: `context-hash`. Expected exit: 0. Output: 64-character lowercase hex SHA-256 digest. Post-state: Unchanged.
  * **Invalid (`fix-context-hash-inv-01`, NYI):** Pre-state: Configured source files missing on disk. Request: `context-hash`. Expected exit: 0. Output: SHA-256 hash computed using `<missing>` token for missing files. Post-state: Unchanged.
  * **Auth (`fix-context-hash-auth-01`, NYI):** Pre-state: `state.json` unreadable. Request: `context-hash`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-context-hash-rec-01`, NYI):** Pre-state: Files contain Windows `
` line endings vs Unix `
`. Request: `context-hash`. Recovery injection: Hub normalizes newlines to `
` before hashing, producing identical digest across OS platforms. Expected exit: 0. Output: Deterministic cross-platform SHA-256 hash. Post-state: Unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 14. report-error
* **Input Schema:** `ai_root: Path`, `peer: str = "unknown"` (via `--peer` or `--agent`), `pattern: str = "unknown"` (via `--pattern` or `--reason`), `detail: str = ""` (via `--detail`), `severity: str = "warn"` (via `--severity`). Validation: None. Authorization: Operational error reporting interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] operational-error recorded peer={peer} pattern={pattern} count={count}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: `_sys/data/operational_errors.jsonl` contains prior error logs. After: Appends JSON line with `ts`, `peer`, `pattern`, `severity`, `detail` to `operational_errors.jsonl`. Reads file to compute matching `(peer, pattern)` error count; if count >= threshold (default 3), triggers automatic `action_peer_quarantine`. Integrates with `_HubError` if available for error/fatal severities.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Every call unconditionally appends a new JSON record to `operational_errors.jsonl` (line 9522), increments the matching failure count (lines 9525-9528), and triggers `action_peer_quarantine` once threshold is reached (line 9533).
* **Redaction/Ordering:** Stdout confirms recorded error pattern and cumulative count.
* **Comparator:** NORMALIZED (timestamps and incrementing error count).
* **Specific Argv Comparators:**
  * **Safety:** Appends to operational log; triggers peer quarantine upon threshold breach.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Records operational failure and enforces error escalation policy.
* **Fixtures:**
  * **Positive (`fix-report-error-pos-01`, NYI):** Pre-state: `operational_errors.jsonl` has 0 entries for `cx`. Request: `report-error --peer cx --pattern "syntax_error" --severity warn --detail "invalid token"`. Expected exit: 0. Output: `[HUB] operational-error recorded peer=cx pattern=syntax_error count=1`. Post-state: `operational_errors.jsonl` has 1 entry.
  * **Invalid (`fix-report-error-inv-01`, NYI):** Pre-state: Standard environment. Request: `report-error`. Expected exit: 0. Output: `[HUB] operational-error recorded peer=unknown pattern=unknown count=1`. Post-state: Default entry recorded.
  * **Auth (`fix-report-error-auth-01`, NYI):** Pre-state: Write-protected `operational_errors.jsonl`. Request: `report-error --peer cx --pattern "err"`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-report-error-rec-01`, NYI):** Pre-state: `operational_errors.jsonl` has 2 prior matching errors for `cx` with pattern `sandbox_violation` (threshold=3). Request: `report-error --peer cx --pattern "sandbox_violation"`. Recovery injection: Hub records 3rd error, detects threshold breach (`count >= 3`), and automatically triggers `action_peer_quarantine` for `cx`. Expected exit: 0. Output: `[HUB] operational-error recorded peer=cx pattern=sandbox_violation count=3` (with quarantine output). Post-state: `cx` marked RED/quarantined in `_sys/cx/health.json`.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 15. feedback-add
* **Input Schema:** `ai_root: Path`, `source_peer: str = "unknown"` (via `--peer` or `--from`), `category: str = "other"` (via `--category`), `severity: str = "medium"` (via `--severity`), `title: str = "unknown gap"` (via `--subject` or `--msg`), `detail: str = ""` (via `--detail`). Validation: None. Authorization: Feedback loop reporting by peer or coordinator.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] FEEDBACK-ADD {event['id']} | peer={source_peer} | title={title}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: `feedback.jsonl` contains existing feedback entries. After: Computes next sequential ID `GAP-YYYYMMDD-NNN`. Appends new JSON record (`id`, `ts`, `source_peer`, `category`, `severity`, `title`, `detail`, `status: "open"`, `owner: None`) to `feedback.jsonl` under lock `feedback`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Unconditionally allocates a new sequential ID `GAP-YYYYMMDD-NNN` (lines 9588-9605) and appends a new record to `feedback.jsonl` under lock `feedback` (lines 9615-9617) on every call.
* **Redaction/Ordering:** Stdout outputs feedback ID, source peer, and title confirmation line.
* **Comparator:** NORMALIZED (generated GAP ID and timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Serialized under local file lock `feedback`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Appends gap item to structured feedback journal.
* **Fixtures:**
  * **Positive (`fix-feedback-add-pos-01`, NYI):** Pre-state: `feedback.jsonl` empty or missing. Request: `feedback-add --peer cc --category tooling --severity high --subject "CLI flag parse error" --detail "details here"`. Expected exit: 0. Output: `[HUB] FEEDBACK-ADD GAP-20260820-001 | peer=cc | title=CLI flag parse error`. Post-state: `feedback.jsonl` contains new record with status `open`.
  * **Invalid (`fix-feedback-add-inv-01`, NYI):** Pre-state: Standard environment. Request: `feedback-add`. Expected exit: 0. Output: `[HUB] FEEDBACK-ADD GAP-20260820-001 | peer=unknown | title=unknown gap`. Post-state: Default entry appended.
  * **Auth (`fix-feedback-add-auth-01`, NYI):** Pre-state: Directory permission denied for `feedback.jsonl`. Request: `feedback-add --peer cc`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-feedback-add-rec-01`, NYI):** Pre-state: `feedback.jsonl` contains legacy unformatted lines and prior ID `GAP-20260820-005`. Request: `feedback-add --peer cc --subject "New gap"`. Recovery injection: Hub skips malformed lines, determines next sequential sequence number `006`, and writes under `feedback` lock. Expected exit: 0. Output: `[HUB] FEEDBACK-ADD GAP-20260820-006 | peer=cc | title=New gap`. Post-state: Sequence correctly incremented.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 16. feedback-list
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: Read-only feedback review.
* **Normalized Envelope:** Success: Exit 0, prints TSV header `id	status	severity	category	title` followed by feedback rows to stdout (or `No feedback records found.` if file missing). Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Reads `feedback.jsonl`. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read (lines 9621-9634). Lock-free read. Tolerates empty lines and malformed JSON entries defensively.
* **Redaction/Ordering:** Stdout outputs TSV header followed by tab-delimited records.
* **Comparator:** NORMALIZED (feedback records list).
* **Specific Argv Comparators:**
  * **Safety:** Pure read-only operation.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Tabular rendering of stored feedback entries.
* **Fixtures:**
  * **Positive (`fix-feedback-list-pos-01`, NYI):** Pre-state: `feedback.jsonl` contains 2 records (`GAP-20260820-001` and `GAP-20260820-002`). Request: `feedback-list`. Expected exit: 0. Output: Header `id	status	severity	category	title` followed by 2 TSV rows. Post-state: Unchanged.
  * **Invalid (`fix-feedback-list-inv-01`, NYI):** Pre-state: `feedback.jsonl` does not exist. Request: `feedback-list`. Expected exit: 0. Output: `No feedback records found.`. Post-state: Unchanged.
  * **Auth (`fix-feedback-list-auth-01`, NYI):** Pre-state: Unreadable `feedback.jsonl` permissions. Request: `feedback-list`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-feedback-list-rec-01`, NYI):** Pre-state: `feedback.jsonl` has corrupted JSON lines mixed with valid lines. Request: `feedback-list`. Recovery injection: Hub skips corrupted lines via `try/except json.JSONDecodeError` and renders all valid records cleanly. Expected exit: 0. Output: TSV table of all valid records. Post-state: Unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 17. feedback-resolve
* **Input Schema:** `ai_root: Path`, `feedback_id: str` (via `--feedback-id` or `--round-id`), `status: str = "done"` (via `--status`), `owner: str | None = None` (via `--agent` or `--peer`). Validation: `feedback_id` must exist in `feedback.jsonl`. Exits 1 if file missing or ID not found. Authorization: Feedback management.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] FEEDBACK-RESOLVE {feedback_id} | status={status}` to stdout. Error: Exit 1, prints `[HUB:ERROR] feedback file not found` or `[HUB:ERROR] feedback ID {feedback_id} not found` to stderr.
* **State Changes:** Before: Targeted feedback item has `status: "open"`. After: Modifies `feedback.jsonl` under lock `feedback` updating `status = status`, `resolved_at = _now()`, and `owner` (if provided).
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent in-place record status update (lines 9644-9663). Modifies the record for `feedback_id` in place, refreshing `resolved_at` timestamp. On a second identical call, the record is updated to the exact same status and owner, producing identical stdout output. Concurrency serialized via lock `feedback`.
* **Redaction/Ordering:** Stdout confirms resolution status for target feedback ID.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Guarded by local file lock `feedback`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Updates status and resolution timestamp in feedback file.
* **Fixtures:**
  * **Positive (`fix-feedback-resolve-pos-01`, NYI):** Pre-state: `feedback.jsonl` has item `GAP-20260820-001` with `status: "open"`. Request: `feedback-resolve --feedback-id GAP-20260820-001 --status done --agent cc`. Expected exit: 0. Output: `[HUB] FEEDBACK-RESOLVE GAP-20260820-001 | status=done`. Post-state: Item in `feedback.jsonl` has `status: "done"`, `owner: "cc"`, and `resolved_at` timestamp.
  * **Invalid (`fix-feedback-resolve-inv-01`, NYI):** Pre-state: `feedback.jsonl` exists but does not contain `GAP-99999999-999`. Request: `feedback-resolve --feedback-id GAP-99999999-999`. Expected exit: 1. Output: `[HUB:ERROR] feedback ID GAP-99999999-999 not found` to stderr. Post-state: `feedback.jsonl` unchanged.
  * **Auth (`fix-feedback-resolve-auth-01`, NYI):** Pre-state: `feedback.jsonl` does not exist on disk. Request: `feedback-resolve --feedback-id GAP-20260820-001`. Expected exit: 1. Output: `[HUB:ERROR] feedback file not found` to stderr. Post-state: Unchanged.
  * **Recovery (`fix-feedback-resolve-rec-01`, NYI):** Pre-state: Lock `feedback` held during resolution attempt. Request: `feedback-resolve --feedback-id GAP-20260820-001 --status dismissed`. Recovery injection: Hub retries file lock acquisition, acquires lock, and writes updated records. Expected exit: 0. Output: `[HUB] FEEDBACK-RESOLVE GAP-20260820-001 | status=dismissed`. Post-state: Feedback record updated to dismissed.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 18. artifact-claim
* **Input Schema:** `ai_root: Path`, `artifact_name: str` (via `--name`), `owner: str = "unknown"` (via `--peer` or `--agent`). Validation: If artifact is already claimed by another owner and status != "finalized", rejects with Exit 1. Authorization: Governed artifact lifecycle management.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] ARTIFACT-CLAIM {artifact_name} | owner={owner or 'unknown'}` to stdout. Error: Exit 1, prints `[HUB:ERROR] artifact {artifact_name} is already claimed by {existing_owner}` to stderr.
* **State Changes:** Before: `artifact_metadata.json` has existing artifact dictionary or empty state. After: Updates `artifact_metadata.json` under lock `artifact` with artifact entry (`artifact`, `owner`, `mode = "single_owner_merge"`, `drafts`, `status = "claimed"`, `claimed_at = existing.get("claimed_at") or _now()`, `hash = ""`).
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent for the same owner (lines 11120-11136). Preserves `claimed_at`, `drafts`, and `hash` on re-claims (`claimed_at: existing.get("claimed_at") or _now()`, line 11132). Mutual exclusion between distinct peers on unfinalized artifacts (exits 1 if claimed by a different peer). Atomic write under lock `artifact`.
* **Redaction/Ordering:** Stdout confirms claimed artifact name and owner.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Guarded by local lock `artifact`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Claims artifact ownership and prevents concurrent conflicting claims.
* **Fixtures:**
  * **Positive (`fix-artifact-claim-pos-01`, NYI):** Pre-state: `artifact_metadata.json` has no claim for `spec.md`. Request: `artifact-claim --name spec.md --peer cc`. Expected exit: 0. Output: `[HUB] ARTIFACT-CLAIM spec.md | owner=cc`. Post-state: `artifact_metadata.json` has `spec.md` with `owner: "cc"`, `status: "claimed"`.
  * **Invalid (`fix-artifact-claim-inv-01`, NYI):** Pre-state: `spec.md` is claimed by `cc` with status `claimed`. Request: `artifact-claim --name spec.md --peer cx`. Expected exit: 1. Output: `[HUB:ERROR] artifact spec.md is already claimed by cc` to stderr. Post-state: `artifact_metadata.json` unchanged.
  * **Auth (`fix-artifact-claim-auth-01`, NYI):** Pre-state: Write-protected `artifact_metadata.json`. Request: `artifact-claim --name spec.md --peer cc`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-artifact-claim-rec-01`, NYI):** Pre-state: `spec.md` was previously claimed by `cc` and finalized (`status: "finalized"`). Request: `artifact-claim --name spec.md --peer cx`. Recovery injection: Finalized artifacts can be re-claimed by a new owner for subsequent revisions. Expected exit: 0. Output: `[HUB] ARTIFACT-CLAIM spec.md | owner=cx`. Post-state: `spec.md` owner updated to `cx` with status `claimed`.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`
