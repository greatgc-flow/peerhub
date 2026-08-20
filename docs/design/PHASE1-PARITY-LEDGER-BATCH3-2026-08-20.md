# Phase 1 Parity Ledger - Batch 3 (18 Actions)

## 1. artifact-status
* **Input Schema:** `ai_root: Path`, `artifact_name: str | None = None` (via `--name`), `register_peer: str | None = None` (via `--peer` or `--agent`), `draft_path: str | None = None` (via `--draft-path`). Validation: If registering a draft (`register_peer`, `draft_path`, and `artifact_name` provided), requires `artifact_name` to be already claimed in `artifact_metadata.json` (exits 1 if unclaimed). Verifies workspace locality via `_is_workspace_local` (emits warning if outside). If querying, accepts specific `artifact_name` or None (lists all). Authorization: Routine artifact lifecycle inspection and draft registration.
* **Normalized Envelope:** Success (register draft): Exit 0, prints `[HUB] ARTIFACT-DRAFT {artifact_name} | peer={register_peer} | path={draft_path}` to stdout. Success (single query): Exit 0, prints formatted 2-space indented JSON object of artifact metadata to stdout. Success (list query): Exit 0, prints TSV header `artifact\towner\tstatus\tclaimed_at` followed by tab-delimited artifact records to stdout. Success (no metadata file): Exit 0, prints `No artifact metadata records found.` to stdout. Error: Exit 1, prints `[HUB:ERROR] artifact {artifact_name} has not been claimed yet` to stderr.
* **State Changes:** Before: `_sys/data/artifact_metadata.json` contains existing artifact records or is missing. After (on draft register): Updates `artifact_metadata.json` under lock `artifact` setting `drafts[register_peer] = draft_path`, `status = "draft"`, and `external_draft_warned = bool`. After (on query): Pure read-only operation. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent draft registration for identical paths. Mutations guarded by local lock `artifact`. Atomic write via `_write_json`.
* **Redaction/Ordering:** Emits `[HUB:WARN] artifact draft path is outside workspace: {draft_path}` to stderr prior to draft registration if outside workspace.
* **Comparator:** NORMALIZED (draft paths, ISO timestamps, JSON dictionary ordering).
* **Specific Argv Comparators:**
  * **Safety:** Guarded by local file lock `artifact` and workspace locality check.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Registers peer draft location or queries artifact lifecycle records.
* **Fixtures:** Positive: `fix-artifact-status-pos-01` (Not yet implemented), Invalid: `fix-artifact-status-inv-01` (Not yet implemented), Auth: `fix-artifact-status-auth-01` (Not yet implemented), Recovery: `fix-artifact-status-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 2. artifact-finalize
* **Input Schema:** `ai_root: Path`, `artifact_name: str` (via `--name`, required), `file_path: str` (via `--file`, required). Validation: Requires `file_path` to exist on disk (exits 1 if missing). Verifies workspace locality via `_is_workspace_local` (emits warning to stderr if outside). Requires `artifact_name` to be already claimed in `artifact_metadata.json` (exits 1 if unclaimed). Authorization: Governed artifact lifecycle finalization.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] ARTIFACT-FINALIZE {artifact_name} | hash={sha_str}` to stdout. Error (missing file): Exit 1, prints `[HUB:ERROR] file {file_path} not found for finalization` to stderr. Error (unclaimed artifact): Exit 1, prints `[HUB:ERROR] artifact {artifact_name} has not been claimed yet` to stderr.
* **State Changes:** Before: `_sys/data/artifact_metadata.json` has artifact in claimed/draft status. After: Reads file bytes, computes `sha256:{hash}` digest. Updates `artifact_metadata.json` under lock `artifact` setting `status = "finalized"`, `hash = sha_str`, `finalized_at = _now()`, `actual_path = str(actual_file.resolve())`. External effects: Resolves artifact final content hash and marks deliverable sealed.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent finalization for unchanged source file. Concurrency serialized under lock `artifact`. Atomic write via `_write_json`.
* **Redaction/Ordering:** Emits `[HUB:WARN] artifact final path is outside workspace: {file_path}` to stderr if outside workspace. Stdout prints finalize confirmation with computed SHA256 digest.
* **Comparator:** EXACT (SHA256 hex digest, artifact name) / NORMALIZED (finalized timestamp).
* **Specific Argv Comparators:**
  * **Safety:** Guarded by local file lock `artifact`, file existence check, and workspace boundary check.
  * **Cwd/Env/Stdin:** Resolves `file_path` against local filesystem. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Finalizes artifact lifecycle record with cryptographic content digest.
* **Fixtures:** Positive: `fix-artifact-finalize-pos-01` (Not yet implemented), Invalid: `fix-artifact-finalize-inv-01` (Not yet implemented), Auth: `fix-artifact-finalize-auth-01` (Not yet implemented), Recovery: `fix-artifact-finalize-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 3. leader-yield
* **Input Schema:** `ai_root: Path`, `agent: str = "unknown"` (via `--agent`), `reason: str = ""` (via `--reason` or `--detail`). Validation: None. If `agent` does not match active leader, emits non-fatal warning to stderr and still executes vacancy transition. Authorization: Leadership governance interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] LEADER-YIELD {agent} | status=VACANT | reason={reason or 'none'}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Active coordinator/leader assigned in `state.json`. After: If reason contains pressure tokens (`context`, `health`, `rate`, `limit`, `failure`, `degraded`), triggers automated `_checkpoint_active_tasks`. Mutates `state.json` under lock `state` setting `leader = None`, `active_coordinator = None`, `leadership = {"peer": None, "status": "VACANT", "yielded_by": agent, "yielded_at": _now(), "reason": reason or "none"}`, `updated_at = _now()`. Appends yield record to `ACTIVE_THREADS` in `handoff.md`. Emits p2p log `_log_p2p("LEADER-YIELD", ...)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent vacancy transition. Pre-yield task checkpoints on pressure indicators protect in-flight tasks. Atomic write via `_write_state` under lock `state`.
* **Redaction/Ordering:** Emits `[HUB:WARN] {agent} tried to yield leadership, but current leader is {current_leader}` to stderr if yielding non-leader. Stdout confirms VACANT status.
* **Comparator:** NORMALIZED (timestamps, agent names, reason strings).
* **Specific Argv Comparators:**
  * **Safety:** Global lock `state`, automated task checkpointing on resource pressure.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Resets active leadership to VACANT and posts notice in handoff and p2p log.
* **Fixtures:** Positive: `fix-leader-yield-pos-01` (Not yet implemented), Invalid: `fix-leader-yield-inv-01` (Not yet implemented), Auth: `fix-leader-yield-auth-01` (Not yet implemented), Recovery: `fix-leader-yield-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 4. leader-claim
* **Input Schema:** `ai_root: Path`, `agent: str = "unknown"` (via `--agent`), `reason: str = ""` (via `--reason` or `--detail`), `domain: str = ""` (via `--needs`). Validation: AP-20 Coordinator Monopoly Guard checks `coordinator_history` in `state.json` against `yield_failure_threshold` (default 3); rejects claim if agent has served max consecutive terms. Evaluates `_peer_effective_health` of existing leader: if active and healthy (status != RED/STALE) and outside challenge window, rejects claim with Exit 1. Authorization: Leadership governance interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] LEADER-CLAIM {agent} | status=PENDING | challenge_until={challenge_until}` to stdout (prints `[HUB] CHALLENGE: ...` if challenging pending claim). Error (AP-20 violation): Exit 1, prints `[HUB:ERR] AP-20 Violation: {agent} has been coordinator for {threshold} consecutive terms. Yield to others.` to stderr. Error (healthy leader active): Exit 1, prints `[HUB:ERR] Cannot claim leadership. {current_leader} is still active and healthy ({status}).` to stderr.
* **State Changes:** Before: Existing or vacant leadership state in `state.json`. After: Mutates `state.json` under lock `state` setting `leader = agent`, `active_coordinator = agent`, `leadership = {"peer": agent, "status": "PENDING", "domain": domain or reason or "general", "reason": reason or "manual_claim", "claimed_at": _now(), "challenge_until": challenge_until}`. Appends record to `coordinator_history` (retains last 10). Appends claim entry to `ACTIVE_THREADS` in `handoff.md`. Emits p2p log `_log_p2p("LEADER-CLAIM", ...)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Challenge window concurrency window allows contested claims within configured time window (`challenge_window_minutes`, default 1m). Monopoly guard enforces fair rotation. Concurrency guarded by `state` lock.
* **Redaction/Ordering:** Stdout prints pending claim status and challenge deadline ISO timestamp.
* **Comparator:** NORMALIZED (ISO timestamps, challenge window deadlines, agent names).
* **Specific Argv Comparators:**
  * **Safety:** AP-20 monopoly guard, challenge window contestation logic, global lock `state`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Claims coordinator leadership in PENDING status subject to challenge window.
* **Fixtures:** Positive: `fix-leader-claim-pos-01` (Not yet implemented), Invalid: `fix-leader-claim-inv-01` (Not yet implemented), Auth: `fix-leader-claim-auth-01` (Not yet implemented), Recovery: `fix-leader-claim-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 5. elect-leader
* **Input Schema:** `ai_root: Path`, `needs: str = "general"` (via `--needs` or `--role`), `effort: str = "mid"` (via `--effort`), `reason: str = ""` (via `--reason` or `--detail`). Validation: Evaluates capability matching via `_matching_peers(needs, effort)`; falls back to `consensus.default_proposer` (default "cc") if no match. Authorization: System leadership election mechanism.
* **Normalized Envelope:** Success: Exit 0, forwards envelope to `action_leader_claim` (prints `[HUB] LEADER-CLAIM {candidate} | status=PENDING | challenge_until={challenge_until}` to stdout). Error: Exit 1, inherits AP-20 or healthy leader conflict errors from `action_leader_claim`.
* **State Changes:** Before: Existing leadership state and metrics log. After: Records routing metric via `_record_routing_metric(ai_root, "elect_leader", needs=..., effort=..., selected=candidate, candidates=...)`. Invokes `action_leader_claim(ai_root, candidate, reason, domain)` updating `state.json`, `coordinator_history`, `handoff.md`, and `_log_p2p`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Deterministic candidate scoring based on capability matching, cost tier, and health status. Delegates mutation to guarded `action_leader_claim`.
* **Redaction/Ordering:** Follows stdout/stderr ordering of `action_leader_claim`.
* **Comparator:** NORMALIZED (candidate selection, timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Capability-based peer matching combined with AP-20 and health guards in `action_leader_claim`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Selects optimal peer matching needs/effort and executes leader claim.
* **Fixtures:** Positive: `fix-elect-leader-pos-01` (Not yet implemented), Invalid: `fix-elect-leader-inv-01` (Not yet implemented), Auth: `fix-elect-leader-auth-01` (Not yet implemented), Recovery: `fix-elect-leader-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 6. discover
* **Input Schema:** `ai_root: Path`, `needs: str` (via `--needs`, required), `effort: str = "mid"` (via `--effort`). Validation: Requires non-empty `--needs`; exits 1 if missing. Authorization: System and peer discovery inspection.
* **Normalized Envelope:** Success (matching peers found): Exit 0, prints `[HUB:DISCOVER] Found {len(matches)} matching peer(s) for needs='{needs}':` followed by ranked lines `  - {node_id} (Score: {score}, Status: {status}, Cost: {cost_tier}, Tier: {model_tier}) | Capabilities: {capabilities}` to stdout. Success (no matches): Exit 0, prints `[HUB:DISCOVER] No matching peers found for needs='{needs}'. Fallback to default proposer: {fallback}` to stdout. Error: Exit 1, prints `[HUB] discover requires --needs` to stderr.
* **State Changes:** Before / After: Pure read-only operation. Evaluates `orchestration.json`, `protocol_config.json`, and peer health status. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. Lock-free peer evaluation.
* **Redaction/Ordering:** Stdout outputs discover header and ranked peer list with capabilities.
* **Comparator:** NORMALIZED (ranking scores, capability order, peer health statuses).
* **Specific Argv Comparators:**
  * **Safety:** Pure read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Evaluates and recommends suitable peers for specified functional requirement.
* **Fixtures:** Positive: `fix-discover-pos-01` (Not yet implemented), Invalid: `fix-discover-inv-01` (Not yet implemented), Auth: `fix-discover-auth-01` (Not yet implemented), Recovery: `fix-discover-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 7. assign-role
* **Input Schema:** `ai_root: Path`, `role: str` (via `--role`, required), `peer: str` (via `--peer`, required). Validation: Requires non-empty `--role` and `--peer` (exits 1 if missing). Validates target peer health via `_healthy_peer(peer)` (exits 2 if unhealthy). Authorization: Role governance interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] ASSIGN-ROLE {role} -> {peer}` to stdout. Error (missing arguments): Exit 1, prints `[HUB:ERROR] assign-role requires --role and --peer` to stderr. Error (unhealthy peer): Exit 2, prints `[HUB:ERROR] cannot assign role to unhealthy peer {peer} status={status}` to stderr.
* **State Changes:** Before: Existing role assignments in `state.json`. After: Mutates `state.json` under lock `state` updating `role_assignments[role] = {"peer": peer, "status": "ACTIVE", "assigned_at": _now()}`, `roles = {k: v.get("peer") for k, v in assignments.items() if isinstance(v, dict)}`, `updated_at = _now()`. Appends role assignment record to `ACTIVE_THREADS` in `handoff.md`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent role assignment for identical role and peer. Health gate prevents assigning critical duties to degraded peers. Guarded by global lock `state`.
* **Redaction/Ordering:** Stdout confirms role and assigned peer.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Peer health precondition check (Exit 2 on failure) and state lock serialization.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Assigns operational role to healthy peer and records mapping in state and handoff.
* **Fixtures:** Positive: `fix-assign-role-pos-01` (Not yet implemented), Invalid: `fix-assign-role-inv-01` (Not yet implemented), Auth: `fix-assign-role-auth-01` (Not yet implemented), Recovery: `fix-assign-role-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 8. release-role
* **Input Schema:** `ai_root: Path`, `role: str` (via `--role`, required), `peer: str = ""` (via `--peer` or `--agent`). Validation: Requires non-empty `--role` (exits 1 if missing). If `role` is not currently assigned, emits warning to stdout and returns gracefully (exit 0). If `peer` is provided, verifies that the role currently belongs to that peer (exits 1 on ownership mismatch). Authorization: Role governance interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] RELEASE-ROLE {role}` to stdout. Warning (role not assigned): Exit 0, prints `[HUB:WARN] role {role} is not assigned` to stdout. Error (missing --role): Exit 1, prints `[HUB:ERROR] release-role requires --role` to stderr. Error (peer mismatch): Exit 1, prints `[HUB:ERROR] role {role} belongs to {current_peer}, not {peer}` to stderr.
* **State Changes:** Before: Target role assigned in `state.json`. After: Mutates `state.json` under lock `state` removing `role` from `role_assignments`, recalculating `roles`, and updating `updated_at = _now()`. Appends release entry to `ACTIVE_THREADS` in `handoff.md`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent release when role is already vacant. Ownership check prevents accidental release of roles held by other peers. Concurrency guarded by lock `state`.
* **Redaction/Ordering:** Stdout confirms released role name.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Owner verification guard and global lock `state`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Releases role assignment from system state and updates room handoff.
* **Fixtures:** Positive: `fix-release-role-pos-01` (Not yet implemented), Invalid: `fix-release-role-inv-01` (Not yet implemented), Auth: `fix-release-role-auth-01` (Not yet implemented), Recovery: `fix-release-role-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 9. role-status
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: Routine read-only inspection.
* **Normalized Envelope:** Success (assignments present): Exit 0, prints TSV header `role	peer	status	assigned_at` followed by tab-delimited rows to stdout. Success (no assignments): Exit 0, prints `No active role assignments.` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Reads `role_assignments` from `state.json`. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. Lock-free read operation.
* **Redaction/Ordering:** Stdout outputs TSV table or empty notice.
* **Comparator:** NORMALIZED (order of roles and timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Pure read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Renders active role assignments table.
* **Fixtures:** Positive: `fix-role-status-pos-01` (Not yet implemented), Invalid: `fix-role-status-inv-01` (Not yet implemented), Auth: `fix-role-status-auth-01` (Not yet implemented), Recovery: `fix-role-status-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 10. health-precheck
* **Input Schema:** `ai_root: Path`, `needs: str | None = None` (via `--needs`), `peers: str | None = None` (via `--peer`). Validation: Resolves target peer pool (either explicit comma-separated list, matching peers for `needs`, or all enabled peers in `orchestration.json`). Checks effective health and `gate_open` for each peer in scope. Authorization: Governance health pre-flight inspection.
* **Normalized Envelope:** Success: Exit 0, prints warnings for degraded peers (if any) and `[HUB] PRE-CHECK OK: scope={scope}` to stdout. Error (precheck failed): Exit 1, prints `[HUB:ERROR] Governance Health Pre-Check FAILED. Scope={scope}` to stderr.
* **State Changes:** Before / After: Pure read-only health evaluation. Reads peer health files via `_peer_effective_health`. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent health evaluation. Fails closed (Exit 1) if any required peer has status RED, gate closed (`gate_open=False`), missing health file (under explicit scope), STALE status (under explicit scope), or if zero eligible peers are found.
* **Redaction/Ordering:** Stdout prints individual peer warnings (`[HUB:WARN] Pre-check ...`) followed by summary `PRE-CHECK OK`. Stderr emits failure line on exit 1.
* **Comparator:** EXACT / NORMALIZED (scope string and warning details).
* **Specific Argv Comparators:**
  * **Safety:** Fail-closed governance gate protecting downstream operations.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Evaluates health and dispatch gate readiness across peer pool.
* **Fixtures:** Positive: `fix-health-precheck-pos-01` (Not yet implemented), Invalid: `fix-health-precheck-inv-01` (Not yet implemented), Auth: `fix-health-precheck-auth-01` (Not yet implemented), Recovery: `fix-health-precheck-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 11. health-sweep
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: System maintenance sweep (`_SYSTEM_EXEMPT_ACTIONS`).
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] HEALTH-SWEEP stale={swept}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Peer health on disk in `_sys/<peer>/health.json`. After: Iterates enabled hub_nodes, evaluating `_peer_effective_health(peer, recover=True)`. For any peer whose status is STALE, updates `health.json` setting `context_health.status = "STALE"`, `context_health.stale_marked_at = _now()`. If newly stale, appends issue to `PENDING_ISSUES` in `handoff.md`. External effects: Reconciles dead/stale peers.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent health reconciliation. Automatically reconciles dead PIDs and stale heartbeats. Writes peer health via `_write_peer_health`.
* **Redaction/Ordering:** Stdout confirms total count of peers marked stale.
* **Comparator:** NORMALIZED (swept count integer).
* **Specific Argv Comparators:**
  * **Safety:** Non-destructive status reconciliation; logs transitions to handoff pending issues.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Marks timed-out or dead peers STALE and logs transitions.
* **Fixtures:** Positive: `fix-health-sweep-pos-01` (Not yet implemented), Invalid: `fix-health-sweep-inv-01` (Not yet implemented), Auth: `fix-health-sweep-auth-01` (Not yet implemented), Recovery: `fix-health-sweep-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 12. freshness-sweep
* **Input Schema:** `ai_root: Path`, `force: bool = False` (via `--force`), `now_ts: float | None = None`. Validation: Checks elapsed time since `last_run_ts` in `_sys/data/freshness_sweep_state.json`. If `force=False` and elapsed time < 20.0 hours (`_FRESHNESS_SWEEP_MIN_INTERVAL_HOURS`), skips execution. Authorization: System maintenance sweep (`_SYSTEM_EXEMPT_ACTIONS`).
* **Normalized Envelope:** Success (sweep executed): Exit 0, prints `[HUB] FRESHNESS-SWEEP checks={len(checks_run)} findings={len(findings)}` to stdout. Success (interval not expired): Exit 0, prints `[HUB] FRESHNESS-SWEEP skipped (last run {age_hours:.1f}h ago, min interval 20.0h; use --force to override)` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Drift state in `freshness_sweep_state.json`. After: Runs 3 detection-only sub-checks in `_sys/checks/` (`check_tool_updates.run(propose_diff=True)`, `check_cli_reality.auto_refresh_observed()`, `check_policy_ledger.check_policy_ledger()`). Updates `freshness_sweep_state.json` (`last_run_ts`, `last_run_at`, `checks_run`, `findings`). Appends all discovered findings to `PENDING_ISSUES` in `handoff.md`. External effects: None (strictly detection/proposal-only, never mutates production runtimes.json or orchestration.json).
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Budget-gated and cadence-gated detection entry point. Proposes diff artifacts under `_archive/` without in-place runtime mutation.
* **Redaction/Ordering:** Stdout emits execution summary or skip notice. Findings recorded to room handoff.
* **Comparator:** NORMALIZED (timestamps, checks count, findings count).
* **Specific Argv Comparators:**
  * **Safety:** Strictly detection-only; sub-checks write proposals or read ledgers without mutating live config.
  * **Cwd/Env/Stdin:** Adds `_sys/checks` to `sys.path`. No stdin consumed.
  * **Transport/Process-Tree:** Direct Python module execution with budget-gated probes.
  * **Observed Semantics:** Audits tool versions, CLI reality, and policy ledger drift, populating PENDING_ISSUES.
* **Fixtures:** Positive: `fix-freshness-sweep-pos-01` (Not yet implemented), Invalid: `fix-freshness-sweep-inv-01` (Not yet implemented), Auth: `fix-freshness-sweep-auth-01` (Not yet implemented), Recovery: `fix-freshness-sweep-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 13. terminal-handoff
* **Input Schema:** `ai_root: Path`, `current_peer: str = "unknown"` (via `--agent`), `next_peer: str = "unknown"` (via `--peer`), `reason: str = ""` (via `--reason`), `profile: str | None = None`, `owner_pid: int | None = None`. Validation: None. Generates unique lease ID `term-lease-{uuid[:12]}` and calculates expiration from `_human_interface_freshness_minutes()`. Authorization: Terminal lease management and coordinator leadership assignment.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] TERMINAL-HANDOFF complete | to={next_peer} | lease={lease_id} | reason={reason or 'none'}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Existing terminal assignment and leadership in `state.json`. After: Mutates `state.json` under lock `state` setting `human_interface_peer = next_peer`, `active_console_peer = next_peer`, `human_interface_assignment = {"peer": next_peer, "profile": prof, "lease_id": lease_id, "assigned_at": _now(), "last_heartbeat_at": _now(), "expires_at": expires_dt.isoformat(), "owner_pid": owner_pid or os.getpid()}`, `leader = next_peer`, `active_coordinator = next_peer`, `leadership = {"peer": next_peer, "status": "ACTIVE", "domain": reason or "terminal_handoff", "reason": reason or "terminal_handoff", "claimed_at": _now()}`, `human_interface_assignment_time = _now()`, updates `coordinator_history` (keeps last 10), `updated_at = _now()`. Appends handoff event to `ACTIVE_THREADS` in `handoff.md`. Emits p2p log `_log_p2p("TERMINAL-HANDOFF", ...)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Atomic lease generation and leadership transfer. Concurrency synchronized under lock `state`.
* **Redaction/Ordering:** Stdout confirms handoff recipient, generated lease ID, and reason.
* **Comparator:** NORMALIZED (lease ID, ISO timestamps, peer identifiers).
* **Specific Argv Comparators:**
  * **Safety:** Atomic assignment under lock `state`, lease expiry binding.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Transfers terminal duty and active coordinator role to designated peer.
* **Fixtures:** Positive: `fix-terminal-handoff-pos-01` (Not yet implemented), Invalid: `fix-terminal-handoff-inv-01` (Not yet implemented), Auth: `fix-terminal-handoff-auth-01` (Not yet implemented), Recovery: `fix-terminal-handoff-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 14. terminal-duty-sweep
* **Input Schema:** `ai_root: Path`. Validation: Evaluates `resolve_terminal_identity(state, now=now)`. If terminal is currently active (`is_active_terminal=True`), returns immediately without action. Authorization: System terminal watchdog maintenance.
* **Normalized Envelope:** Success (active terminal exists): Exit 0 (no stdout). Success (replacement selected and handoff triggered): Exit 0, prints `[HUB:SWEEP] Terminal identity is not active ({status}: {reason}). Selecting replacement.` followed by `action_terminal_handoff` output to stdout. Success (no replacement found): Exit 0, prints `[HUB:SWEEP] Terminal identity is not active ...
[HUB:SWEEP] No valid replacement found or replacement is the same.` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Stale, expired, or mismatched terminal identity in `state.json`. After: If terminal inactive and eligible replacement discovered (`_select_human_interface_peer`), invokes `action_terminal_handoff(ai_root, current_terminal, next_peer, reason="sweep_stale_terminal")` updating state, handoff, and p2p log. Otherwise pure read-only.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Watchdog evaluates recorded legacy pointer vs lease peer to heal lease mismatches. Automatic recovery for orphaned sessions.
* **Redaction/Ordering:** Stdout outputs sweep trigger status and subsequent handoff line.
* **Comparator:** NORMALIZED (lease IDs, ISO timestamps, peer identifiers).
* **Specific Argv Comparators:**
  * **Safety:** Non-destructive identity probe; delegates to atomic `action_terminal_handoff` when remediation needed.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Watchdog sweeps stale terminal leases and assigns replacement.
* **Fixtures:** Positive: `fix-terminal-duty-sweep-pos-01` (Not yet implemented), Invalid: `fix-terminal-duty-sweep-inv-01` (Not yet implemented), Auth: `fix-terminal-duty-sweep-auth-01` (Not yet implemented), Recovery: `fix-terminal-duty-sweep-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 15. terminal-heartbeat
* **Input Schema:** `ai_root: Path`, `peer: str = "unknown"` (via `--peer` or `--agent`), `lease_id: str = ""` (via `--lease-id`), `owner_pid: int | None = None` (via `--pid`). Validation: Requires `human_interface_assignment` in `state.json` to be an active dict. CAS Validation: Requires `lease_id` to exactly match `assignment["lease_id"]` (exits 1 on CAS rejection). Authorization: Terminal leaseholder maintenance.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] TERMINAL-HEARTBEAT renewed | lease={lease_id} | expires={assignment['expires_at']}` to stdout. Error (missing assignment): Exit 1, prints `[HUB:WARN] terminal-heartbeat failed: no assignment lease found` to stderr. Error (CAS rejection): Exit 1, prints `[HUB:WARN] terminal-heartbeat CAS rejection for peer={peer}: stale lease_id={lease_id} != active lease_id={curr_lease_id}` to stderr.
* **State Changes:** Before: Existing terminal assignment in `state.json`. After: Mutates `state.json` under lock `state` updating `assignment["last_heartbeat_at"] = _now()`, `assignment["expires_at"] = (now_dt + timedelta(minutes=freshness_minutes)).isoformat()`, `assignment["owner_pid"] = owner_pid` (if provided), `state["updated_at"] = _now()`. External effects: Extends terminal lease window.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** CAS (Compare-And-Swap) concurrency guard protects against renewing superseded leases. Serialized under lock `state`.
* **Redaction/Ordering:** Stdout prints renewal confirmation and new expiration timestamp. Stderr emits CAS failure warning.
* **Comparator:** NORMALIZED (lease ID, ISO expiration timestamp).
* **Specific Argv Comparators:**
  * **Safety:** CAS lease ID validation under global lock `state`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Extends expiration deadline for matching active terminal lease.
* **Fixtures:** Positive: `fix-terminal-heartbeat-pos-01` (Not yet implemented), Invalid: `fix-terminal-heartbeat-inv-01` (Not yet implemented), Auth: `fix-terminal-heartbeat-auth-01` (Not yet implemented), Recovery: `fix-terminal-heartbeat-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 16. terminal-close
* **Input Schema:** `ai_root: Path`, `lease_id: str = ""` (via `--lease-id`), `reason: str = "closed"` (via `--reason`). Validation: Requires `human_interface_assignment` in `state.json`. CAS Validation: Requires `lease_id` to match `assignment["lease_id"]` (exits 1 on CAS mismatch). Authorization: Terminal session termination interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] TERMINAL-CLOSE complete | lease={lease_id} | reason={reason}` to stdout. Error (missing or stale lease): Exit 1, prints `[HUB:WARN] terminal-close CAS rejection: stale lease_id={lease_id}` to stderr.
* **State Changes:** Before: Active terminal assignment in `state.json`. After: Mutates `state.json` under lock `state` setting `assignment["expires_at"] = _now()`, `assignment["close_reason"] = reason`, `state["updated_at"] = _now()`. External effects: Immediately expires terminal lease.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** CAS atomic close prevents closing superseded leases. Concurrency guarded by `state` lock.
* **Redaction/Ordering:** Stdout confirms terminal closure and recorded reason.
* **Comparator:** EXACT / NORMALIZED (lease ID).
* **Specific Argv Comparators:**
  * **Safety:** CAS validation under global lock `state`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Atomically marks active terminal lease expired and logs reason.
* **Fixtures:** Positive: `fix-terminal-close-pos-01` (Not yet implemented), Invalid: `fix-terminal-close-inv-01` (Not yet implemented), Auth: `fix-terminal-close-auth-01` (Not yet implemented), Recovery: `fix-terminal-close-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 17. append-handoff
* **Input Schema:** `ai_root: Path`, `section: str` (via `--section`, required), `text: str` (via `--text`, required). Validation: Requires non-empty `--section` and `--text` (exits 1 if missing). Requires active `room_id` in `state.json` (exits 1 if missing). Requires `sessions/{room_id}/handoff.md` to exist on disk (exits 1 if missing). Authorization: Room collaboration handoff logging.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] APPEND-HANDOFF to [{section.upper()}]` to stdout. Error: Exit 1, prints `[HUB:ERROR] append-handoff requires --section and --text`, `[HUB:ERROR] No active room`, or `[HUB:ERROR] {handoff_path} not found` to stderr.
* **State Changes:** Before: Active `sessions/{room_id}/handoff.md` markdown file. After: Reads `handoff.md` under lock `handoff`. If section header `## [{SECTION}]` exists, appends `- {text}` under it; if section header missing, appends new header `## [{SECTION}]` with bullet `- {text}` at bottom. Writes updated markdown back to disk. External effects: Updates persistent room handoff document.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Append-only structured markdown update. Concurrency serialized under shared lock `handoff` (atomic check-then-read-then-write critical section).
* **Redaction/Ordering:** Stdout confirms target uppercase section name.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Serialized under shared file lock `handoff`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Appends bulleted line under specified markdown section in active room handoff.
* **Fixtures:** Positive: `fix-append-handoff-pos-01` (Not yet implemented), Invalid: `fix-append-handoff-inv-01` (Not yet implemented), Auth: `fix-append-handoff-auth-01` (Not yet implemented), Recovery: `fix-append-handoff-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 18. task-checkpoint
* **Input Schema:** `ai_root: Path`, `task_id: str` (via `--task-id` or `--id`, required), `peer: str = "unknown"` (via `--peer` or `--agent`, required), `note: str` (via `--msg` or `--detail`, required). Validation: Requires non-empty `task_id`, `peer`, and `note` (exits 1 if missing). Authorization: Enforces `_role_guard(ai_root, peer, "task-checkpoint", {"coordinator", "implementer", "documenter"})`.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] TASK-CHECKPOINT {task_id} | peer={peer}` to stdout. Error: Exit 1, prints `[HUB:ERROR] task-checkpoint requires --id, --peer/--agent, and --msg` or role guard denial to stderr.
* **State Changes:** Before: Task record in `_sys/data/task_registry.json`. After: Updates `task_registry.json` under lock `task_registry` setting `task["owner"] = peer`, `task["status"] = "ACTIVE"`, `task["updated_at"] = _now()`, and appending `{"peer": peer, "note": note, "at": _now()}` to `task["checkpoints"]`. Appends entry `{_now()} task:{task_id} checkpoint by {peer}: {note[:120]}` to `ACTIVE_THREADS` in `handoff.md`. External effects: Updates task progress in registry and room handoff.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Role-guarded task progress tracking. Concurrency guarded by lock `task_registry`. Propagates 120-character truncated snippet to room handoff.
* **Redaction/Ordering:** Truncates note to 120 characters in room handoff. Stdout confirms task ID and reporting peer.
* **Comparator:** EXACT / NORMALIZED (task ID, peer name).
* **Specific Argv Comparators:**
  * **Safety:** Role authorization verification (`_role_guard`) and local lock `task_registry`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Records timestamped task checkpoint in registry and posts progress summary to active threads.
* **Fixtures:** Positive: `fix-task-checkpoint-pos-01` (Not yet implemented), Invalid: `fix-task-checkpoint-inv-01` (Not yet implemented), Auth: `fix-task-checkpoint-auth-01` (Not yet implemented), Recovery: `fix-task-checkpoint-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`
