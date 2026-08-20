# Phase 1 Parity Ledger - Batch 3 (18 Actions)

## 1. artifact-status
* **Input Schema:** `ai_root: Path`, `artifact_name: str | None = None` (via `--name`), `register_peer: str | None = None` (via `--peer` or `--agent`), `draft_path: str | None = None` (via `--draft-path`). Validation: If registering a draft (`register_peer`, `draft_path`, and `artifact_name` provided), requires `artifact_name` to be already claimed in `artifact_metadata.json` (exits 1 if unclaimed). Verifies workspace locality via `_is_workspace_local` (emits warning to stderr if outside). If querying, accepts specific `artifact_name` or None (lists all). Authorization: Routine artifact lifecycle inspection and draft registration.
* **Normalized Envelope:** Success (register draft): Exit 0, prints `[HUB] ARTIFACT-DRAFT {artifact_name} | peer={register_peer} | path={draft_path}` to stdout. Success (single query): Exit 0, prints formatted 2-space indented JSON object of artifact metadata to stdout. Success (list query): Exit 0, prints TSV header `artifact\towner\tstatus\tclaimed_at` followed by tab-delimited artifact records to stdout. Success (no metadata file): Exit 0, prints `No artifact metadata records found.` to stdout. Error: Exit 1, prints `[HUB:ERROR] artifact {artifact_name} has not been claimed yet` to stderr.
* **State Changes:** Before: `_sys/data/artifact_metadata.json` contains existing artifact records or is missing. After (on draft register): Updates `artifact_metadata.json` under lock `artifact` setting `drafts[register_peer] = draft_path`, `status = "draft"`, and `external_draft_warned = bool`. After (on query): Pure read-only operation. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent for queries and identical draft registrations (lines 11139-11164). For queries, lock-free read-only operation. For draft registration, overwrites `drafts[register_peer]` with `draft_path` under lock `artifact` without updating timestamps, appending logs, or modifying sequence numbers; on a second identical call, state and stdout remain identical. Concurrency guarded by local lock `artifact`. Atomic write via `_write_json`.
* **Redaction/Ordering:** Emits `[HUB:WARN] artifact draft path is outside workspace: {draft_path}` to stderr prior to draft registration if outside workspace (line 11151).
* **Comparator:** NORMALIZED (draft paths, ISO timestamps, JSON dictionary ordering).
* **Specific Argv Comparators:**
  * **Safety:** Guarded by local file lock `artifact` and workspace locality check.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Registers peer draft location or queries artifact lifecycle records.
* **Fixtures:**
  * **Positive (`fix-artifact-status-pos-01`, NYI):** Pre-state: `_sys/data/artifact_metadata.json` has `{"spec.md": {"owner": "cc", "status": "claimed", "claimed_at": "2026-08-20T10:00:00"}}`. Request: `artifact-status --name spec.md --peer cc --draft-path "docs/draft_spec.md"`. Expected exit: 0. Output: `[HUB] ARTIFACT-DRAFT spec.md | peer=cc | path=docs/draft_spec.md`. Post-state: `artifact_metadata.json` has `data["spec.md"]["drafts"]["cc"] == "docs/draft_spec.md"` and `status: "draft"`.
  * **Invalid (`fix-artifact-status-inv-01`, NYI):** Pre-state: `artifact_metadata.json` exists without `unclaimed.md`. Request: `artifact-status --name unclaimed.md --peer cx --draft-path "docs/unclaimed.md"`. Expected exit: 1. Output: `[HUB:ERROR] artifact unclaimed.md has not been claimed yet` to stderr. Post-state: `artifact_metadata.json` unchanged.
  * **Auth (`fix-artifact-status-auth-01`, NYI):** Pre-state: Write permission denied on `_sys/data/artifact_metadata.json`. Request: `artifact-status --name spec.md --peer cc --draft-path "docs/draft.md"`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-artifact-status-rec-01`, NYI):** Pre-state: `_sys/data/artifact_metadata.json` is missing. Request: `artifact-status`. Recovery injection: Hub handles missing file gracefully. Expected exit: 0. Output: `No artifact metadata records found.`. Post-state: Unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 2. artifact-finalize
* **Input Schema:** `ai_root: Path`, `artifact_name: str` (via `--name`, required), `file_path: str` (via `--file_path` / `--file`, required). Validation: Requires `file_path` to exist on disk (exits 1 if missing). Verifies workspace locality via `_is_workspace_local` (emits warning to stderr if outside). Requires `artifact_name` to be already claimed in `artifact_metadata.json` (exits 1 if unclaimed). Authorization: Governed artifact lifecycle finalization.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] ARTIFACT-FINALIZE {artifact_name} | hash={sha_str}` to stdout. Error (missing file): Exit 1, prints `[HUB:ERROR] file {file_path} not found for finalization` to stderr. Error (unclaimed artifact): Exit 1, prints `[HUB:ERROR] artifact {artifact_name} has not been claimed yet` to stderr.
* **State Changes:** Before: `_sys/data/artifact_metadata.json` has artifact in claimed/draft status. After: Reads file bytes, computes `sha256:{hash}` digest. Updates `artifact_metadata.json` under lock `artifact` setting `status = "finalized"`, `hash = sha_str`, `finalized_at = _now()`, `actual_path = str(actual_file.resolve())`. External effects: Resolves artifact final content hash and marks deliverable sealed.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent state mutation for unchanged source file (lines 11166-11185). On repeated calls with the same file, status remains finalized, hash and actual_path remain identical, producing identical stdout `[HUB] ARTIFACT-FINALIZE {artifact_name} | hash={sha_str}`, while `finalized_at` timestamp is updated to `_now()` (line 11182). Serialized under lock `artifact`. Atomic write via `_write_json`.
* **Redaction/Ordering:** Emits `[HUB:WARN] artifact final path is outside workspace: {file_path}` to stderr if outside workspace (line 11172). Stdout prints finalize confirmation with computed SHA256 digest.
* **Comparator:** EXACT (SHA256 hex digest, artifact name) / NORMALIZED (finalized timestamp).
* **Specific Argv Comparators:**
  * **Safety:** Guarded by local file lock `artifact`, file existence check, and workspace boundary check.
  * **Cwd/Env/Stdin:** Resolves `file_path` against local filesystem. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Finalizes artifact lifecycle record with cryptographic content digest.
* **Fixtures:**
  * **Positive (`fix-artifact-finalize-pos-01`, NYI):** Pre-state: `artifact_metadata.json` has `spec.md` with status `claimed`; `docs/spec.md` exists with content "Final Spec". Request: `artifact-finalize --name spec.md --file docs/spec.md`. Expected exit: 0. Output: `[HUB] ARTIFACT-FINALIZE spec.md | hash=sha256:...`. Post-state: `artifact_metadata.json` has `spec.md` with `status: "finalized"`, `hash: "sha256:..."`, `finalized_at` timestamp, and `actual_path`.
  * **Invalid (`fix-artifact-finalize-inv-01`, NYI):** Pre-state: `artifact_metadata.json` exists; `docs/missing.md` does not exist. Request: `artifact-finalize --name spec.md --file docs/missing.md`. Expected exit: 1. Output: `[HUB:ERROR] file docs/missing.md not found for finalization` to stderr. Post-state: `artifact_metadata.json` unchanged.
  * **Auth (`fix-artifact-finalize-auth-01`, NYI):** Pre-state: `artifact_metadata.json` does not contain `unclaimed.md`; `docs/unclaimed.md` exists. Request: `artifact-finalize --name unclaimed.md --file docs/unclaimed.md`. Expected exit: 1. Output: `[HUB:ERROR] artifact unclaimed.md has not been claimed yet` to stderr. Post-state: `artifact_metadata.json` unchanged.
  * **Recovery (`fix-artifact-finalize-rec-01`, NYI):** Pre-state: File lock `artifact` held during finalization. Request: `artifact-finalize --name spec.md --file docs/spec.md`. Recovery injection: Hub retries file lock acquisition, acquires lock, and writes finalized metadata. Expected exit: 0. Output: `[HUB] ARTIFACT-FINALIZE spec.md | hash=sha256:...`. Post-state: Finalized metadata written atomically.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 3. leader-yield
* **Input Schema:** `ai_root: Path`, `agent: str = "unknown"` (via `--agent`), `reason: str = ""` (via `--reason` or `--detail`). Validation: If `agent` does not match active leader, emits non-fatal warning to stderr and still executes vacancy transition. Authorization: Leadership governance interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] LEADER-YIELD {agent} | status=VACANT | reason={reason or 'none'}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Active coordinator/leader assigned in `state.json`. After: If reason contains pressure tokens (`context`, `health`, `rate`, `limit`, `failure`, `degraded`), triggers automated `_checkpoint_active_tasks` which appends checkpoint items to `handoff.md`. Mutates `state.json` under lock `state` setting `leader = None`, `active_coordinator = None`, `leadership = {"peer": None, "status": "VACANT", "yielded_by": agent, "yielded_at": _now(), "reason": reason or "none"}`, `updated_at = _now()`. Appends yield record `[{_now()}] ({agent}) [YIELD] yielded leadership. Reason: {reason or 'none'}` to `ACTIVE_THREADS` in `handoff.md`. Emits p2p log `_log_p2p("LEADER-YIELD", ...)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent (lines 8698-8728). On every invocation, unconditionally appends a new timestamped yield entry to `ACTIVE_THREADS` in `handoff.md` (line 8724), emits a new LEADER-YIELD event to `log.jsonl` via `_log_p2p` (line 8726), updates `yielded_at` and `updated_at` in `state.json` (lines 8717, 8720), and triggers task checkpoint appends if pressure reasons are present (lines 8701-8703). Atomic state update under lock `state`.
* **Redaction/Ordering:** Emits `[HUB:WARN] {agent} tried to yield leadership, but current leader is {current_leader}` to stderr if yielding non-leader (line 8709). Stdout confirms VACANT status.
* **Comparator:** NORMALIZED (timestamps, agent names, reason strings).
* **Specific Argv Comparators:**
  * **Safety:** Global lock `state`, automated task checkpointing on resource pressure.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Resets active leadership to VACANT and posts notice in handoff and p2p log.
* **Fixtures:**
  * **Positive (`fix-leader-yield-pos-01`, NYI):** Pre-state: `state.json` has `active_coordinator: "cc"`, `leader: "cc"`. Request: `leader-yield --agent cc --reason "context_exhausted"`. Expected exit: 0. Output: `[HUB] LEADER-YIELD cc | status=VACANT | reason=context_exhausted`. Post-state: `state.json` has `leader: null`, `active_coordinator: null`, `leadership.status: "VACANT"`; `handoff.md` `ACTIVE_THREADS` contains yield line; `log.jsonl` has LEADER-YIELD entry.
  * **Invalid (`fix-leader-yield-inv-01`, NYI):** Pre-state: `state.json` has `leader: "cc"`. Request: `leader-yield --agent cx --reason "voluntary"`. Expected exit: 0. Output: Warning `[HUB:WARN] cx tried to yield leadership, but current leader is cc` to stderr; `[HUB] LEADER-YIELD cx | status=VACANT | reason=voluntary` to stdout. Post-state: `state.json` set to VACANT.
  * **Auth (`fix-leader-yield-auth-01`, NYI):** Pre-state: Write permission denied on `state.json`. Request: `leader-yield --agent cc`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-leader-yield-rec-01`, NYI):** Pre-state: Active tasks in `task_registry.json` when yielding with reason "rate_limit_exceeded". Request: `leader-yield --agent cc --reason "rate_limit_exceeded"`. Recovery injection: Hub runs `_checkpoint_active_tasks` to protect in-flight tasks before vacancy transition. Expected exit: 0. Output: `[HUB] LEADER-YIELD cc | status=VACANT | reason=rate_limit_exceeded`. Post-state: Tasks checkpointed, leadership set to VACANT.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 4. leader-claim
* **Input Schema:** `ai_root: Path`, `agent: str = "unknown"` (via `--agent`), `reason: str = ""` (via `--reason` or `--detail`), `domain: str = ""` (via `--needs`). Validation: AP-20 Coordinator Monopoly Guard checks `coordinator_history` in `state.json` against `yield_failure_threshold` (default 3); rejects claim if agent has served max consecutive terms. Evaluates `_peer_effective_health` of existing leader: if active and healthy (status != RED/STALE) and outside challenge window, rejects claim with Exit 1. Authorization: Leadership governance interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] LEADER-CLAIM {agent} | status=PENDING | challenge_until={challenge_until}` to stdout (prints `[HUB] CHALLENGE: ...` if challenging pending claim). Error (AP-20 violation): Exit 1, prints `[HUB:ERR] AP-20 Violation: {agent} has been coordinator for {threshold} consecutive terms. Yield to others.` to stderr. Error (healthy leader active): Exit 1, prints `[HUB:ERR] Cannot claim leadership. {current_leader} is still active and healthy ({status}).` to stderr.
* **State Changes:** Before: Existing or vacant leadership state in `state.json`. After: Mutates `state.json` under lock `state` setting `leader = agent`, `active_coordinator = agent`, `leadership = {"peer": agent, "status": "PENDING", "domain": domain or reason or "general", "reason": reason or "manual_claim", "claimed_at": _now(), "challenge_until": challenge_until}`. Appends record to `coordinator_history` (retains last 10). Appends claim entry to `ACTIVE_THREADS` in `handoff.md`. Emits p2p log `_log_p2p("LEADER-CLAIM", ...)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent (lines 8730-8794). Every successful claim extends the challenge window deadline (line 8771), appends a new entry to `coordinator_history` (line 8784), appends a log entry to `ACTIVE_THREADS` in `handoff.md` (line 8790), emits a new event to `log.jsonl` via `_log_p2p` (line 8792), and repeated identical claims by the same agent will eventually trip the AP-20 Monopoly Guard (lines 8748-8751) and fail with exit code 1. Concurrency guarded by lock `state`.
* **Redaction/Ordering:** Stdout prints pending claim status and challenge deadline ISO timestamp.
* **Comparator:** NORMALIZED (ISO timestamps, challenge window deadlines, agent names).
* **Specific Argv Comparators:**
  * **Safety:** AP-20 monopoly guard, challenge window contestation logic, global lock `state`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Claims coordinator leadership in PENDING status subject to challenge window.
* **Fixtures:**
  * **Positive (`fix-leader-claim-pos-01`, NYI):** Pre-state: `state.json` leadership is VACANT, `coordinator_history` has no consecutive CC terms. Request: `leader-claim --agent cc --reason "planning_round"`. Expected exit: 0. Output: `[HUB] LEADER-CLAIM cc | status=PENDING | challenge_until=...`. Post-state: `state.json` has `leader: "cc"`, `status: "PENDING"`, `coordinator_history` has new entry; `handoff.md` and `log.jsonl` updated.
  * **Invalid (`fix-leader-claim-inv-01`, NYI):** Pre-state: `coordinator_history` in `state.json` contains 3 consecutive terms by `cc` (threshold=3). Request: `leader-claim --agent cc`. Expected exit: 1. Output: `[HUB:ERR] AP-20 Violation: cc has been coordinator for 3 consecutive terms. Yield to others.` to stderr. Post-state: `state.json` unchanged.
  * **Auth (`fix-leader-claim-auth-01`, NYI):** Pre-state: Active leader is `cc` (healthy GREEN), challenge window expired. Request: `leader-claim --agent cx`. Expected exit: 1. Output: `[HUB:ERR] Cannot claim leadership. cc is still active and healthy (GREEN).` to stderr. Post-state: `state.json` unchanged.
  * **Recovery (`fix-leader-claim-rec-01`, NYI):** Pre-state: Active leader `cc` has health status RED (or STALE). Request: `leader-claim --agent cx --reason "failover"`. Recovery injection: Hub allows takeover from unhealthy leader regardless of challenge window. Expected exit: 0. Output: `[HUB] LEADER-CLAIM cx | status=PENDING | challenge_until=...`. Post-state: `cx` claims leadership in `state.json`.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 5. elect-leader
* **Input Schema:** `ai_root: Path`, `needs: str = "general"` (via `--needs` or `--role`), `effort: str = "mid"` (via `--effort`), `reason: str = ""` (via `--reason` or `--detail`). Validation: Evaluates capability matching via `_matching_peers(needs, effort)`; falls back to `consensus.default_proposer` (default "cc") if no match. Authorization: System leadership election mechanism.
* **Normalized Envelope:** Success: Exit 0, forwards envelope to `action_leader_claim` (prints `[HUB] LEADER-CLAIM {candidate} | status=PENDING | challenge_until={challenge_until}` to stdout). Error: Exit 1, inherits AP-20 or healthy leader conflict errors from `action_leader_claim`.
* **State Changes:** Before: Existing leadership state and metrics log. After: Records routing metric via `_record_routing_metric(ai_root, "elect_leader", needs=..., effort=..., selected=candidate, candidates=...)`. Invokes `action_leader_claim(ai_root, candidate, reason, domain)` updating `state.json`, `coordinator_history`, `handoff.md`, and `_log_p2p`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent (lines 8953-8960). Unconditionally records routing metric (line 8959) and delegates to `action_leader_claim` which mutates state, challenge deadlines, handoff, p2p log, and accumulates `coordinator_history` entries.
* **Redaction/Ordering:** Follows stdout/stderr ordering of `action_leader_claim`.
* **Comparator:** NORMALIZED (candidate selection, timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Capability-based peer matching combined with AP-20 and health guards in `action_leader_claim`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Selects optimal peer matching needs/effort and executes leader claim.
* **Fixtures:**
  * **Positive (`fix-elect-leader-pos-01`, NYI):** Pre-state: Leadership VACANT; `cx` has highest match score for `code_analysis`. Request: `elect-leader --needs code_analysis --effort high`. Expected exit: 0. Output: `[HUB] LEADER-CLAIM cx | status=PENDING | challenge_until=...`. Post-state: `cx` claimed as leader; routing metric recorded in `_sys/data/routing_metrics.jsonl`.
  * **Invalid (`fix-elect-leader-inv-01`, NYI):** Pre-state: Candidate selected by matching has 3 consecutive terms in `coordinator_history`. Request: `elect-leader --needs general`. Expected exit: 1. Output: `[HUB:ERR] AP-20 Violation: ...` to stderr. Post-state: `state.json` unchanged.
  * **Auth (`fix-elect-leader-auth-01`, NYI):** Pre-state: Active leader `cc` is healthy GREEN and challenge window expired. Request: `elect-leader --needs review`. Expected exit: 1. Output: `[HUB:ERR] Cannot claim leadership. cc is still active and healthy ...` to stderr. Post-state: `state.json` unchanged.
  * **Recovery (`fix-elect-leader-rec-01`, NYI):** Pre-state: No peers match the specified `needs`. Request: `elect-leader --needs nonexistent_capability`. Recovery injection: Hub falls back to `default_proposer` ("cc") from orchestration config. Expected exit: 0. Output: `[HUB] LEADER-CLAIM cc | status=PENDING | challenge_until=...`. Post-state: `cc` claimed as leader.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 6. discover
* **Input Schema:** `ai_root: Path`, `needs: str` (via `--needs`, required), `effort: str = "mid"` (via `--effort`). Validation: Requires non-empty `--needs`; exits 1 if missing (lines 12179-12180). Authorization: System and peer discovery inspection.
* **Normalized Envelope:** Success (matching peers found): Exit 0, prints `[HUB:DISCOVER] Found {len(matches)} matching peer(s) for needs='{needs}':` followed by ranked lines `  - {node_id} (Score: {score}, Status: {status}, Cost: {cost_tier}, Tier: {model_tier}) | Capabilities: {capabilities}` to stdout. Success (no matches): Exit 0, prints `[HUB:DISCOVER] No matching peers found for needs='{needs}'. Fallback to default proposer: {fallback}` to stdout. Error: Exit 1, prints `[HUB] discover requires --needs` to stderr.
* **State Changes:** Before / After: Pure read-only operation. Evaluates `orchestration.json`, `protocol_config.json`, and peer health status. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read (lines 8939-8951). Lock-free peer capability evaluation without state mutations or log side-effects.
* **Redaction/Ordering:** Stdout outputs discover header and ranked peer list with capabilities.
* **Comparator:** NORMALIZED (ranking scores, capability order, peer health statuses).
* **Specific Argv Comparators:**
  * **Safety:** Pure read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Evaluates and recommends suitable peers for specified functional requirement.
* **Fixtures:**
  * **Positive (`fix-discover-pos-01`, NYI):** Pre-state: Peers `cc`, `cx`, `ag` configured with distinct capabilities. Request: `discover --needs coding --effort mid`. Expected exit: 0. Output: `[HUB:DISCOVER] Found ... matching peer(s) for needs='coding':` followed by formatted candidate lines. Post-state: Unchanged.
  * **Invalid (`fix-discover-inv-01`, NYI):** Pre-state: Standard environment. Request: `discover` (missing `--needs`). Expected exit: 1. Output: `[HUB] discover requires --needs` to stderr. Post-state: Unchanged.
  * **Auth (`fix-discover-auth-01`, NYI):** Pre-state: Read permission denied on `orchestration.json`. Request: `discover --needs coding`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-discover-rec-01`, NYI):** Pre-state: Specified `needs` matches zero registered peers. Request: `discover --needs quantum_cryptography`. Recovery injection: Hub reports fallback to `default_proposer` ("cc"). Expected exit: 0. Output: `[HUB:DISCOVER] No matching peers found for needs='quantum_cryptography'. Fallback to default proposer: cc`. Post-state: Unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 7. assign-role
* **Input Schema:** `ai_root: Path`, `role: str` (via `--role`, required), `peer: str` (via `--peer`, required). Validation: Requires non-empty `--role` and `--peer` (exits 1 if missing). Validates target peer health via `_healthy_peer(peer)` (exits 2 if unhealthy). Authorization: Role governance interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] ASSIGN-ROLE {role} -> {peer}` to stdout. Error (missing arguments): Exit 1, prints `[HUB:ERROR] assign-role requires --role and --peer` to stderr. Error (unhealthy peer): Exit 2, prints `[HUB:ERROR] cannot assign role to unhealthy peer {peer} status={status}` to stderr.
* **State Changes:** Before: Existing role assignments in `state.json`. After: Mutates `state.json` under lock `state` updating `role_assignments[role] = {"peer": peer, "status": "ACTIVE", "assigned_at": _now()}`, `roles = {k: v.get("peer") for k, v in assignments.items() if isinstance(v, dict)}`, `updated_at = _now()`. Appends role assignment record `{_now()} role:{role} assigned to {peer}` to `ACTIVE_THREADS` in `handoff.md`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent (lines 11188-11209). While role mapping in `state.json` converges to the specified peer, every invocation unconditionally appends a new timestamped role assignment line to `ACTIVE_THREADS` in `handoff.md` (line 11207) and refreshes `assigned_at` and `updated_at` timestamps in `state.json` (lines 11202, 11205). Health gate prevents assigning critical duties to degraded peers (line 11192). Guarded by global lock `state`.
* **Redaction/Ordering:** Stdout confirms role and assigned peer.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Peer health precondition check (Exit 2 on failure) and state lock serialization.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Assigns operational role to healthy peer and records mapping in state and handoff.
* **Fixtures:**
  * **Positive (`fix-assign-role-pos-01`, NYI):** Pre-state: Peer `cx` is healthy (GREEN); `state.json` has active room. Request: `assign-role --role implementer --peer cx`. Expected exit: 0. Output: `[HUB] ASSIGN-ROLE implementer -> cx`. Post-state: `state.json` has `role_assignments["implementer"]["peer"] == "cx"`, `roles["implementer"] == "cx"`; `handoff.md` `ACTIVE_THREADS` contains assignment entry.
  * **Invalid (`fix-assign-role-inv-01`, NYI):** Pre-state: Standard environment. Request: `assign-role --role implementer` (missing `--peer`). Expected exit: 1. Output: `[HUB:ERROR] assign-role requires --role and --peer` to stderr. Post-state: `state.json` unchanged.
  * **Auth (`fix-assign-role-auth-01`, NYI):** Pre-state: Peer `cx` has health status RED. Request: `assign-role --role implementer --peer cx`. Expected exit: 2. Output: `[HUB:ERROR] cannot assign role to unhealthy peer cx status=RED` to stderr. Post-state: `state.json` unchanged.
  * **Recovery (`fix-assign-role-rec-01`, NYI):** Pre-state: Lock `state` held by concurrent operation. Request: `assign-role --role documenter --peer ag`. Recovery injection: Hub waits on lock `state`, acquires, and updates role assignment atomically. Expected exit: 0. Output: `[HUB] ASSIGN-ROLE documenter -> ag`. Post-state: Role `documenter` assigned to `ag`.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 8. release-role
* **Input Schema:** `ai_root: Path`, `role: str` (via `--role`, required), `peer: str = ""` (via `--peer` or `--agent`). Validation: Requires non-empty `--role` (exits 1 if missing). If `role` is not currently assigned, emits warning to stdout and returns gracefully (exit 0). If `peer` is provided, verifies that the role currently belongs to that peer (exits 1 on ownership mismatch). Authorization: Role governance interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] RELEASE-ROLE {role}` to stdout. Warning (role not assigned): Exit 0, prints `[HUB:WARN] role {role} is not assigned` to stdout. Error (missing --role): Exit 1, prints `[HUB:ERROR] release-role requires --role` to stderr. Error (peer mismatch): Exit 1, prints `[HUB:ERROR] role {role} belongs to {current_peer}, not {peer}` to stderr.
* **State Changes:** Before: Target role assigned in `state.json`. After: Mutates `state.json` under lock `state` removing `role` from `role_assignments`, recalculating `roles`, and updating `updated_at = _now()`. Appends release entry `{_now()} role:{role} released` to `ACTIVE_THREADS` in `handoff.md`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent across state transitions (lines 11339-11360). On the first call when the role is active, it removes the role from `state.json` (lines 11353-11357), appends a release entry to `ACTIVE_THREADS` in `handoff.md` (line 11358), and prints `[HUB] RELEASE-ROLE {role}`. On a second identical call, the role is no longer assigned, so it short-circuits at line 11348, printing `[HUB:WARN] role {role} is not assigned` without mutating state or appending to handoff (exit 0). Concurrency guarded by lock `state`.
* **Redaction/Ordering:** Stdout confirms released role name on success or warning on already-unassigned role.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Owner verification guard (line 11350) and global lock `state`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Releases role assignment from system state and updates room handoff.
* **Fixtures:**
  * **Positive (`fix-release-role-pos-01`, NYI):** Pre-state: `state.json` has `role_assignments["implementer"] == {"peer": "cx", ...}`. Request: `release-role --role implementer --peer cx`. Expected exit: 0. Output: `[HUB] RELEASE-ROLE implementer`. Post-state: `implementer` removed from `role_assignments` and `roles` in `state.json`; `handoff.md` contains release note.
  * **Invalid (`fix-release-role-inv-01`, NYI):** Pre-state: Standard environment. Request: `release-role` (missing `--role`). Expected exit: 1. Output: `[HUB:ERROR] release-role requires --role` to stderr. Post-state: `state.json` unchanged.
  * **Auth (`fix-release-role-auth-01`, NYI):** Pre-state: `role_assignments["implementer"]` belongs to `cx`. Request: `release-role --role implementer --peer ag`. Expected exit: 1. Output: `[HUB:ERROR] role implementer belongs to cx, not ag` to stderr. Post-state: `state.json` unchanged.
  * **Recovery (`fix-release-role-rec-01`, NYI):** Pre-state: `role_assignments` does not contain `reviewer`. Request: `release-role --role reviewer`. Recovery injection: Hub handles unassigned role cleanly with warning and exits 0. Expected exit: 0. Output: `[HUB:WARN] role reviewer is not assigned`. Post-state: `state.json` unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 9. role-status
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: Routine read-only inspection.
* **Normalized Envelope:** Success (assignments present): Exit 0, prints TSV header `role\tpeer\tstatus\tassigned_at` followed by tab-delimited rows to stdout. Success (no assignments): Exit 0, prints `No active role assignments.` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation. Reads `role_assignments` from `state.json`. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read (lines 11211-11223). Lock-free read operation without state mutations or log side-effects.
* **Redaction/Ordering:** Stdout outputs TSV table or empty notice.
* **Comparator:** NORMALIZED (order of roles and timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Pure read-only inspection.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Renders active role assignments table.
* **Fixtures:**
  * **Positive (`fix-role-status-pos-01`, NYI):** Pre-state: `state.json` has `role_assignments` with `implementer` (`cx`) and `documenter` (`ag`). Request: `role-status`. Expected exit: 0. Output: TSV header `role\tpeer\tstatus\tassigned_at` followed by rows for `implementer` and `documenter`. Post-state: Unchanged.
  * **Invalid (`fix-role-status-inv-01`, NYI):** Pre-state: `state.json` has empty `role_assignments: {}`. Request: `role-status`. Expected exit: 0. Output: `No active role assignments.`. Post-state: Unchanged.
  * **Auth (`fix-role-status-auth-01`, NYI):** Pre-state: Read permission denied on `state.json`. Request: `role-status`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-role-status-rec-01`, NYI):** Pre-state: `role_assignments` contains legacy string mapping `{"implementer": "cx"}` instead of dictionary. Recovery injection: Hub handles non-dict value gracefully (line 11222) rendering default ACTIVE status. Expected exit: 0. Output: `implementer\tcx\tACTIVE\t`. Post-state: Unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 10. health-precheck
* **Input Schema:** `ai_root: Path`, `needs: str | None = None` (via `--needs`), `peers: str | None = None` (via `--peer`). Validation: Resolves target peer pool (either explicit comma-separated list, matching peers for `needs`, or all enabled peers in `orchestration.json`). Checks effective health and `gate_open` for each peer in scope. Authorization: Governance health pre-flight inspection.
* **Normalized Envelope:** Success: Exit 0, prints warnings for degraded peers (if any) and `[HUB] PRE-CHECK OK: scope={scope}` to stdout. Error (precheck failed): Exit 1, prints `[HUB:ERROR] Governance Health Pre-Check FAILED. Scope={scope}` to stderr.
* **State Changes:** Before / After: Pure read-only health evaluation. Reads peer health files via `_peer_effective_health(peer)`. External effects: None.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read-only health evaluation (lines 11224-11274). Fails closed (Exit 1) if any required peer has status RED (line 11256), gate closed (`gate_open=False`, line 11256), missing health file under explicit scope (line 11264), STALE status under explicit scope (line 11255), or if zero eligible peers are found (line 11265). Lock-free evaluation without state mutation or log emission.
* **Redaction/Ordering:** Stdout prints individual peer warnings (`[HUB:WARN] Pre-check ...`) followed by summary `PRE-CHECK OK`. Stderr emits failure line on exit 1.
* **Comparator:** EXACT / NORMALIZED (scope string and warning details).
* **Specific Argv Comparators:**
  * **Safety:** Fail-closed governance gate protecting downstream operations.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Evaluates health and dispatch gate readiness across peer pool.
* **Fixtures:**
  * **Positive (`fix-health-precheck-pos-01`, NYI):** Pre-state: Enabled peers `cc`, `cx`, `ag` are all GREEN with `gate_open: True`. Request: `health-precheck`. Expected exit: 0. Output: `[HUB] PRE-CHECK OK: scope=all`. Post-state: Unchanged.
  * **Invalid (`fix-health-precheck-inv-01`, NYI):** Pre-state: Explicit peer `cx` has status RED in `_sys/cx/health.json`. Request: `health-precheck --peer cx`. Expected exit: 1. Output: `[HUB:WARN] Pre-check failed for cx: status=RED, gate_open=True` to stdout; `[HUB:ERROR] Governance Health Pre-Check FAILED. Scope=cx` to stderr. Post-state: Unchanged.
  * **Auth (`fix-health-precheck-auth-01`, NYI):** Pre-state: Directory permission denied on `_sys/`. Request: `health-precheck`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-health-precheck-rec-01`, NYI):** Pre-state: Peer `ag` has status YELLOW (degraded memory usage) but `gate_open: True`. Request: `health-precheck --peer ag`. Recovery injection: Hub treats YELLOW as eligible with warning, allowing execution to pass. Expected exit: 0. Output: `[HUB:WARN] Pre-check warning for ag: status=YELLOW, gate_open=True` followed by `[HUB] PRE-CHECK OK: scope=ag`. Post-state: Unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 11. health-sweep
* **Input Schema:** `ai_root: Path`. Validation: None. Authorization: System maintenance sweep (`_SYSTEM_EXEMPT_ACTIONS`).
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] HEALTH-SWEEP stale={swept}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Peer health on disk in `_sys/<peer>/health.json`. After: Iterates enabled hub_nodes, evaluating `_peer_effective_health(peer, recover=True)`. For any peer whose status is STALE, updates `health.json` setting `context_health.status = "STALE"`, `context_health.stale_marked_at = _now()`. If newly stale (`not was_stale`), appends issue `{_now()} {peer}: health marked STALE by health-sweep` to `PENDING_ISSUES` in `handoff.md`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Convergent health reconciliation (lines 11465-11481). Automatically reconciles dead PIDs and stale heartbeats to STALE status. The `was_stale` guard (line 11477) prevents duplicate entries in `handoff.md` on repeated calls, while `stale_marked_at` timestamp is updated in `health.json` (line 11475). When all peers are healthy, pure read-only with no mutations (`swept=0`).
* **Redaction/Ordering:** Stdout confirms total count of peers marked stale.
* **Comparator:** NORMALIZED (swept count integer).
* **Specific Argv Comparators:**
  * **Safety:** Non-destructive status reconciliation; deduplicated logging to handoff pending issues.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Marks timed-out or dead peers STALE and logs newly stale transitions.
* **Fixtures:**
  * **Positive (`fix-health-sweep-pos-01`, NYI):** Pre-state: All enabled peers `cc`, `cx`, `ag` are active and healthy (GREEN). Request: `health-sweep`. Expected exit: 0. Output: `[HUB] HEALTH-SWEEP stale=0`. Post-state: Unchanged.
  * **Invalid (`fix-health-sweep-inv-01`, NYI):** Pre-state: `_sys/cc/health.json` has `active_pid: 99999` (dead PID) with status GREEN. Request: `health-sweep`. Expected exit: 0. Output: `[HUB] HEALTH-SWEEP stale=1`. Post-state: `cc` health updated to STALE; `handoff.md` `PENDING_ISSUES` contains stale notice.
  * **Auth (`fix-health-sweep-auth-01`, NYI):** Pre-state: Write-protected `_sys/cc/health.json`. Request: `health-sweep`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-health-sweep-rec-01`, NYI):** Pre-state: `cc` was already marked STALE in a previous sweep (`was_stale == True`). Request: `health-sweep`. Recovery injection: Hub updates `stale_marked_at` timestamp in `health.json` but `not was_stale` check skips duplicate append to `handoff.md`. Expected exit: 0. Output: `[HUB] HEALTH-SWEEP stale=1`. Post-state: `health.json` timestamp refreshed; `handoff.md` contains no duplicate entries.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 12. freshness-sweep
* **Input Schema:** `ai_root: Path`, `force: bool = False` (via `--force`), `now_ts: float | None = None`. Validation: Checks elapsed time since `last_run_ts` in `_sys/data/freshness_sweep_state.json`. If `force=False` and elapsed time < 20.0 hours (`_FRESHNESS_SWEEP_MIN_INTERVAL_HOURS`), skips execution. Authorization: System maintenance sweep (`_SYSTEM_EXEMPT_ACTIONS`).
* **Normalized Envelope:** Success (sweep executed): Exit 0, prints `[HUB] FRESHNESS-SWEEP checks={len(checks_run)} findings={len(findings)}` to stdout. Success (interval not expired): Exit 0, prints `[HUB] FRESHNESS-SWEEP skipped (last run {age_hours:.1f}h ago, min interval 20.0h; use --force to override)` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Drift state in `freshness_sweep_state.json`. After: Runs 3 detection-only sub-checks in `_sys/checks/` (`check_tool_updates.run(propose_diff=True)`, `check_cli_reality.auto_refresh_observed()`, `check_policy_ledger.check_policy_ledger()`). Updates `freshness_sweep_state.json` (`last_run_ts`, `last_run_at`, `checks_run`, `findings`). If findings discovered, appends each to `PENDING_ISSUES` in `handoff.md`. External effects: May write proposal diff artifacts under `_archive/` via `check_tool_updates`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Budget-gated and cadence-gated detection entry point (lines 11491-11585). When executed (first run or `--force`), it is NOT idempotent because it writes new state to `freshness_sweep_state.json` (lines 11576-11581), appends findings to `PENDING_ISSUES` in `handoff.md` (lines 11582-11583), and generates diff proposal files. When cadence-gated (within 20 hours and `force=False`), it is strictly idempotent, skipping sub-checks without mutations or log appends (lines 11516-11519).
* **Redaction/Ordering:** Stdout emits execution summary or skip notice. Findings recorded to room handoff.
* **Comparator:** NORMALIZED (timestamps, checks count, findings count).
* **Specific Argv Comparators:**
  * **Safety:** Strictly detection-only; sub-checks write proposals or read ledgers without mutating live config.
  * **Cwd/Env/Stdin:** Adds `_sys/checks` to `sys.path`. No stdin consumed.
  * **Transport/Process-Tree:** Direct Python module execution with budget-gated probes.
  * **Observed Semantics:** Audits tool versions, CLI reality, and policy ledger drift, populating PENDING_ISSUES.
* **Fixtures:**
  * **Positive (`fix-freshness-sweep-pos-01`, NYI):** Pre-state: `freshness_sweep_state.json` missing or `last_run_ts` > 20h ago; 0 drift findings. Request: `freshness-sweep`. Expected exit: 0. Output: `[HUB] FRESHNESS-SWEEP checks=3 findings=0`. Post-state: `freshness_sweep_state.json` written with `last_run_ts`, `checks_run: ["tool-updates", "cli-reality", "policy-ledger"]`.
  * **Invalid (`fix-freshness-sweep-inv-01`, NYI):** Pre-state: `last_run_ts` recorded 2.0 hours ago. Request: `freshness-sweep`. Expected exit: 0. Output: `[HUB] FRESHNESS-SWEEP skipped (last run 2.0h ago, min interval 20.0h; use --force to override)`. Post-state: `freshness_sweep_state.json` unchanged.
  * **Auth (`fix-freshness-sweep-auth-01`, NYI):** Pre-state: Write-protected `_sys/data/freshness_sweep_state.json`. Request: `freshness-sweep --force`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-freshness-sweep-rec-01`, NYI):** Pre-state: Sub-check `check_tool_updates` raises an exception during execution. Request: `freshness-sweep --force`. Recovery injection: Hub catches exception (lines 11541-11542), records finding `tool-updates: sweep failed: ...`, continues remaining checks, and records state cleanly. Expected exit: 0. Output: `[HUB] FRESHNESS-SWEEP checks=2 findings=1`. Post-state: Finding recorded in `freshness_sweep_state.json` and `handoff.md`.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 13. terminal-handoff
* **Input Schema:** `ai_root: Path`, `current_peer: str = "unknown"` (via `--agent`), `next_peer: str = "unknown"` (via `--peer`), `reason: str = ""` (via `--reason`), `profile: str | None = None`, `owner_pid: int | None = None`. Validation: None. Generates unique lease ID `term-lease-{uuid[:12]}` and calculates expiration from `_human_interface_freshness_minutes()`. Authorization: Terminal lease management and coordinator leadership assignment.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] TERMINAL-HANDOFF complete | to={next_peer} | lease={lease_id} | reason={reason or 'none'}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Existing terminal assignment and leadership in `state.json`. After: Mutates `state.json` under lock `state` setting `human_interface_peer = next_peer`, `active_console_peer = next_peer`, `human_interface_assignment = {"peer": next_peer, "profile": prof, "lease_id": lease_id, "assigned_at": _now(), "last_heartbeat_at": _now(), "expires_at": expires_dt.isoformat(), "owner_pid": owner_pid or os.getpid()}`, `leader = next_peer`, `active_coordinator = next_peer`, `leadership = {"peer": next_peer, "status": "ACTIVE", "domain": reason or "terminal_handoff", "reason": reason or "terminal_handoff", "claimed_at": _now()}`, `human_interface_assignment_time = _now()`, updates `coordinator_history` (keeps last 10), `updated_at = _now()`. Appends handoff event `[{_now()}] [TERMINAL-HANDOFF] {current_peer} handed off terminal duty to {next_peer}. Reason: {reason or 'none'}` to `ACTIVE_THREADS` in `handoff.md`. Emits p2p log `_log_p2p("TERMINAL-HANDOFF", ...)`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent (lines 8809-8866). Every invocation generates a fresh random lease ID (`term-lease-{uuid[:12]}`, line 8823), updates terminal and leadership leases in `state.json`, appends a new item to `coordinator_history` (line 8855), appends a handoff line to `ACTIVE_THREADS` in `handoff.md` (line 8862), and emits a new event to `log.jsonl` via `_log_p2p` (line 8864). Concurrency synchronized under lock `state`.
* **Redaction/Ordering:** Stdout confirms handoff recipient, generated lease ID, and reason.
* **Comparator:** NORMALIZED (lease ID, ISO timestamps, peer identifiers).
* **Specific Argv Comparators:**
  * **Safety:** Atomic assignment under lock `state`, lease expiry binding.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Transfers terminal duty and active coordinator role to designated peer.
* **Fixtures:**
  * **Positive (`fix-terminal-handoff-pos-01`, NYI):** Pre-state: `state.json` has `human_interface_peer: "cc"`. Request: `terminal-handoff --agent cc --peer cx --reason "operator_switch"`. Expected exit: 0. Output: `[HUB] TERMINAL-HANDOFF complete | to=cx | lease=term-lease-... | reason=operator_switch`. Post-state: `state.json` has `human_interface_peer: "cx"`, `active_coordinator: "cx"`, new lease record; `handoff.md` and `log.jsonl` updated.
  * **Invalid (`fix-terminal-handoff-inv-01`, NYI):** Pre-state: Standard environment. Request: `terminal-handoff`. Expected exit: 0. Output: `[HUB] TERMINAL-HANDOFF complete | to=unknown | lease=term-lease-... | reason=none`. Post-state: Terminal duty assigned to `unknown` with default profile.
  * **Auth (`fix-terminal-handoff-auth-01`, NYI):** Pre-state: Write-protected `state.json`. Request: `terminal-handoff --agent cc --peer cx`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-terminal-handoff-rec-01`, NYI):** Pre-state: Target peer `ag` does not have explicit `--profile` provided. Request: `terminal-handoff --agent cc --peer ag --reason "rotation"`. Recovery injection: Hub resolves default profile for `ag` via `_default_profile_for_peer` (line 8824) and assigns lease. Expected exit: 0. Output: `[HUB] TERMINAL-HANDOFF complete | to=ag | lease=term-lease-... | reason=rotation`. Post-state: Lease created with `profile: "ag.standard"`.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 14. terminal-duty-sweep
* **Input Schema:** `ai_root: Path`. Validation: Evaluates `resolve_terminal_identity(state, now=now)`. If terminal is currently active (`is_active_terminal=True`), returns immediately without action. Authorization: System terminal watchdog maintenance.
* **Normalized Envelope:** Success (active terminal exists): Exit 0 (no stdout). Success (replacement selected and handoff triggered): Exit 0, prints `[HUB:SWEEP] Terminal identity is not active ({status}: {reason}). Selecting replacement.` followed by `action_terminal_handoff` output to stdout. Success (no replacement found): Exit 0, prints `[HUB:SWEEP] Terminal identity is not active ... [HUB:SWEEP] No valid replacement found or replacement is the same.` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Stale, expired, or mismatched terminal identity in `state.json`. After: If terminal inactive and eligible replacement discovered (`_select_human_interface_peer`), invokes `action_terminal_handoff(ai_root, current_terminal, next_peer, reason="sweep_stale_terminal")` updating state, handoff, and p2p log. Otherwise pure read-only.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Watchdog evaluates recorded legacy pointer vs lease peer to heal lease mismatches and stale heartbeats (lines 11437-11464). When an active valid terminal exists, it is a strictly idempotent no-op (line 11444). When an inactive terminal is remediated, it triggers `action_terminal_handoff`, converging state so that a second immediate call finds `is_active_terminal=True` and returns silently (exit 0).
* **Redaction/Ordering:** Stdout outputs sweep trigger status and subsequent handoff line when replacement occurs.
* **Comparator:** NORMALIZED (lease IDs, ISO timestamps, peer identifiers).
* **Specific Argv Comparators:**
  * **Safety:** Non-destructive identity probe; delegates to atomic `action_terminal_handoff` when remediation needed.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Watchdog sweeps stale terminal leases and assigns replacement.
* **Fixtures:**
  * **Positive (`fix-terminal-duty-sweep-pos-01`, NYI):** Pre-state: `state.json` has active, unexpired terminal lease for `cc`. Request: `terminal-duty-sweep`. Expected exit: 0. Output: (no stdout output). Post-state: Unchanged.
  * **Invalid (`fix-terminal-duty-sweep-inv-01`, NYI):** Pre-state: `human_interface_assignment` in `state.json` is missing or null, and all candidate peers are unhealthy RED. Request: `terminal-duty-sweep`. Expected exit: 0. Output: `[HUB:SWEEP] Terminal identity is not active (none: no terminal assignment found). Selecting replacement.` followed by `[HUB:SWEEP] No valid replacement found or replacement is the same.`. Post-state: Unchanged.
  * **Auth (`fix-terminal-duty-sweep-auth-01`, NYI):** Pre-state: Read permission denied on `state.json`. Request: `terminal-duty-sweep`. Expected exit: 1. Output: PermissionError to stderr. Post-state: Unchanged.
  * **Recovery (`fix-terminal-duty-sweep-rec-01`, NYI):** Pre-state: `state.json` has expired lease for `cc` (`expires_at` in past); healthy peer `cx` is available. Request: `terminal-duty-sweep`. Recovery injection: Watchdog detects expired lease, invokes `action_terminal_handoff` to `cx`. Expected exit: 0. Output: `[HUB:SWEEP] Terminal identity is not active (expired: lease expired ...). Selecting replacement.` followed by `[HUB] TERMINAL-HANDOFF complete | to=cx ...`. Post-state: Terminal duty transferred to `cx` with fresh lease.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 15. terminal-heartbeat
* **Input Schema:** `ai_root: Path`, `peer: str = "unknown"` (via `--peer` or `--agent`), `lease_id: str = ""` (via `--lease-id`), `owner_pid: int | None = None` (via `--pid`). Validation: Requires `human_interface_assignment` in `state.json` to be an active dict. CAS Validation: Requires `lease_id` to exactly match `assignment["lease_id"]` (exits 1 on CAS rejection, lines 12205-12206). Authorization: Terminal leaseholder maintenance.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] TERMINAL-HEARTBEAT renewed | lease={lease_id} | expires={assignment['expires_at']}` to stdout. Error (missing assignment): Exit 1, prints `[HUB:WARN] terminal-heartbeat failed: no assignment lease found` to stderr. Error (CAS rejection): Exit 1, prints `[HUB:WARN] terminal-heartbeat CAS rejection for peer={peer}: stale lease_id={lease_id} != active lease_id={curr_lease_id}` to stderr.
* **State Changes:** Before: Existing terminal assignment in `state.json`. After: Mutates `state.json` under lock `state` updating `assignment["last_heartbeat_at"] = _now()`, `assignment["expires_at"] = (now_dt + timedelta(minutes=freshness_minutes)).isoformat()`, `assignment["owner_pid"] = owner_pid` (if provided), `state["updated_at"] = _now()`. External effects: Extends terminal lease window.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** CAS renewal probe (lines 8871-8909). Repeating the call with the matching active `lease_id` succeeds and extends `expires_at` while updating `last_heartbeat_at` and `updated_at` timestamps in `state.json` (lines 8899-8905) under lock `state`. Rejects mismatched or superseded leases with exit code 1 (lines 8887-8893, 12205-12206).
* **Redaction/Ordering:** Stdout prints renewal confirmation and new expiration timestamp. Stderr emits CAS failure warning.
* **Comparator:** NORMALIZED (lease ID, ISO expiration timestamp).
* **Specific Argv Comparators:**
  * **Safety:** CAS lease ID validation under global lock `state`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Extends expiration deadline for matching active terminal lease.
* **Fixtures:**
  * **Positive (`fix-terminal-heartbeat-pos-01`, NYI):** Pre-state: `state.json` has `human_interface_assignment` with `lease_id: "term-lease-abc123"`. Request: `terminal-heartbeat --peer cc --lease-id term-lease-abc123 --pid 1234`. Expected exit: 0. Output: `[HUB] TERMINAL-HEARTBEAT renewed | lease=term-lease-abc123 | expires=...`. Post-state: `expires_at` extended by configured freshness window; `owner_pid: 1234`.
  * **Invalid (`fix-terminal-heartbeat-inv-01`, NYI):** Pre-state: `state.json` has `human_interface_assignment` with `lease_id: "term-lease-current"`. Request: `terminal-heartbeat --peer cc --lease-id term-lease-stale`. Expected exit: 1. Output: `[HUB:WARN] terminal-heartbeat CAS rejection for peer=cc: stale lease_id=term-lease-stale != active lease_id=term-lease-current` to stderr. Post-state: `state.json` unchanged.
  * **Auth (`fix-terminal-heartbeat-auth-01`, NYI):** Pre-state: `human_interface_assignment` is null or missing in `state.json`. Request: `terminal-heartbeat --peer cc --lease-id term-lease-abc123`. Expected exit: 1. Output: `[HUB:WARN] terminal-heartbeat failed: no assignment lease found` to stderr. Post-state: `state.json` unchanged.
  * **Recovery (`fix-terminal-heartbeat-rec-01`, NYI):** Pre-state: Active lease exists but `owner_pid` argument is omitted. Request: `terminal-heartbeat --peer cc --lease-id term-lease-abc123`. Recovery injection: Hub preserves existing `owner_pid` and extends lease expiration. Expected exit: 0. Output: `[HUB] TERMINAL-HEARTBEAT renewed | lease=term-lease-abc123 | expires=...`. Post-state: `expires_at` updated.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 16. terminal-close
* **Input Schema:** `ai_root: Path`, `lease_id: str = ""` (via `--lease-id`), `reason: str = "closed"` (via `--reason`). Validation: Requires `human_interface_assignment` in `state.json`. CAS Validation: Requires `lease_id` to match `assignment["lease_id"]` (exits 1 on CAS mismatch, lines 12213-12214). Authorization: Terminal session termination interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] TERMINAL-CLOSE complete | lease={lease_id} | reason={reason}` to stdout. Error (missing or stale lease): Exit 1, prints `[HUB:WARN] terminal-close CAS rejection: stale lease_id={lease_id}` to stderr (or exits 1 if assignment missing).
* **State Changes:** Before: Active terminal assignment in `state.json`. After: Mutates `state.json` under lock `state` setting `assignment["expires_at"] = _now()`, `assignment["close_reason"] = reason`, `state["updated_at"] = _now()`. External effects: Immediately expires terminal lease.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** CAS atomic close (lines 8912-8937). For an uninterrupted active lease, repeating the call with the matching `lease_id` sets `expires_at = _now()` and `close_reason = reason`, printing identical stdout `[HUB] TERMINAL-CLOSE complete | lease={lease_id} | reason={reason}` and exiting 0. If a superseded lease ID is passed after reassignment, CAS rejects with exit code 1 (line 8925). Concurrency guarded by lock `state`.
* **Redaction/Ordering:** Stdout confirms terminal closure and recorded reason.
* **Comparator:** EXACT / NORMALIZED (lease ID).
* **Specific Argv Comparators:**
  * **Safety:** CAS validation under global lock `state`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Atomically marks active terminal lease expired and logs reason.
* **Fixtures:**
  * **Positive (`fix-terminal-close-pos-01`, NYI):** Pre-state: `state.json` has active lease `term-lease-abc123`. Request: `terminal-close --lease-id term-lease-abc123 --reason "session_logout"`. Expected exit: 0. Output: `[HUB] TERMINAL-CLOSE complete | lease=term-lease-abc123 | reason=session_logout`. Post-state: `human_interface_assignment["expires_at"]` set to current timestamp, `close_reason: "session_logout"`.
  * **Invalid (`fix-terminal-close-inv-01`, NYI):** Pre-state: `state.json` has active lease `term-lease-new`. Request: `terminal-close --lease-id term-lease-old`. Expected exit: 1. Output: `[HUB:WARN] terminal-close CAS rejection: stale lease_id=term-lease-old` to stderr. Post-state: `state.json` unchanged.
  * **Auth (`fix-terminal-close-auth-01`, NYI):** Pre-state: `state.json` has `human_interface_assignment: null`. Request: `terminal-close --lease-id term-lease-abc123`. Expected exit: 1. Output: Exits 1 (assignment is not a dict). Post-state: `state.json` unchanged.
  * **Recovery (`fix-terminal-close-rec-01`, NYI):** Pre-state: Lock `state` held by background thread. Request: `terminal-close --lease-id term-lease-abc123`. Recovery injection: Hub retries file lock acquisition, acquires lock, and marks lease expired. Expected exit: 0. Output: `[HUB] TERMINAL-CLOSE complete | lease=term-lease-abc123 | reason=closed`. Post-state: Lease atomically expired.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 17. append-handoff
* **Input Schema:** `ai_root: Path`, `section: str` (via `--section`, required), `text: str` (via `--text`, required). Validation: Requires non-empty `--section` and `--text` (exits 1 if missing). Requires active `room_id` in `state.json` (exits 1 if missing). Requires `sessions/{room_id}/handoff.md` to exist on disk (exits 1 if missing). Authorization: Room collaboration handoff logging.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] APPEND-HANDOFF to [{section.upper()}]` to stdout. Error: Exit 1, prints `[HUB:ERROR] append-handoff requires --section and --text`, `[HUB:ERROR] No active room`, or `[HUB:ERROR] {handoff_path} not found` to stderr.
* **State Changes:** Before: Active `sessions/{room_id}/handoff.md` markdown file. After: Reads `handoff.md` under lock `handoff`. If section header `## [{SECTION}]` exists, appends `- {text}` under it; if section header missing, appends new header `## [{SECTION}]` with bullet `- {text}` at bottom. Writes updated markdown back to disk. External effects: Updates persistent room handoff document.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent (lines 11275-11305). Every invocation unconditionally appends another `- {text}` line under `## [{SECTION}]` in `sessions/{room_id}/handoff.md` (lines 11298, 11302). Concurrency serialized under shared lock `handoff` (atomic check-then-read-then-write critical section, lines 11288-11303).
* **Redaction/Ordering:** Stdout confirms target uppercase section name.
* **Comparator:** EXACT.
* **Specific Argv Comparators:**
  * **Safety:** Serialized under shared file lock `handoff`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Appends bulleted line under specified markdown section in active room handoff.
* **Fixtures:**
  * **Positive (`fix-append-handoff-pos-01`, NYI):** Pre-state: Room `room-1234` active; `handoff.md` has `## [KEY_DECISIONS]`. Request: `append-handoff --section KEY_DECISIONS --text "Decision 1 finalized"`. Expected exit: 0. Output: `[HUB] APPEND-HANDOFF to [KEY_DECISIONS]`. Post-state: `handoff.md` has `- Decision 1 finalized` appended under `## [KEY_DECISIONS]`.
  * **Invalid (`fix-append-handoff-inv-01`, NYI):** Pre-state: Room `room-1234` active. Request: `append-handoff --section GOAL` (missing `--text`). Expected exit: 1. Output: `[HUB:ERROR] append-handoff requires --section and --text` to stderr. Post-state: `handoff.md` unchanged.
  * **Auth (`fix-append-handoff-auth-01`, NYI):** Pre-state: `state.json` has `room_id: null`. Request: `append-handoff --section GOAL --text "Test"`. Expected exit: 1. Output: `[HUB:ERROR] No active room` to stderr. Post-state: Unchanged.
  * **Recovery (`fix-append-handoff-rec-01`, NYI):** Pre-state: `handoff.md` exists but does not contain `## [CUSTOM_SECTION]`. Request: `append-handoff --section CUSTOM_SECTION --text "Custom note"`. Recovery injection: Hub creates header `## [CUSTOM_SECTION]` and appends bullet `- Custom note` cleanly. Expected exit: 0. Output: `[HUB] APPEND-HANDOFF to [CUSTOM_SECTION]`. Post-state: `handoff.md` contains new section header and item.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 18. task-checkpoint
* **Input Schema:** `ai_root: Path`, `task_id: str` (via `--task-id` or `--id`, required), `peer: str = "unknown"` (via `--peer` or `--agent`, required), `note: str` (via `--msg` or `--detail`, required). Validation: Requires non-empty `task_id`, `peer`, and `note` (exits 1 if missing). Authorization: Enforces `_role_guard(ai_root, peer, "task-checkpoint", {"coordinator", "implementer", "documenter"})`.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] TASK-CHECKPOINT {task_id} | peer={peer}` to stdout. Error: Exit 1, prints `[HUB:ERROR] task-checkpoint requires --id, --peer/--agent, and --msg` or role guard denial to stderr.
* **State Changes:** Before: Task record in `_sys/data/task_registry.json`. After: Updates `task_registry.json` under lock `task_registry` setting `task["owner"] = peer`, `task["status"] = "ACTIVE"`, `task["updated_at"] = _now()`, and appending `{"peer": peer, "note": note, "at": _now()}` to `task["checkpoints"]`. Appends entry `{_now()} task:{task_id} checkpoint by {peer}: {note[:120]}` to `ACTIVE_THREADS` in `handoff.md`. External effects: Updates task progress in registry and room handoff.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent (lines 11307-11323). Every invocation appends a new checkpoint object to `task["checkpoints"]` in `_sys/data/task_registry.json` (line 11319) and appends a new line to `ACTIVE_THREADS` in `handoff.md` (line 11321), while updating `updated_at` (line 11318). Guarded by `_role_guard` (line 11311) and local lock `task_registry`. Truncates note snippet to 120 characters in room handoff.
* **Redaction/Ordering:** Truncates note to 120 characters in room handoff. Stdout confirms task ID and reporting peer.
* **Comparator:** EXACT / NORMALIZED (task ID, peer name).
* **Specific Argv Comparators:**
  * **Safety:** Role authorization verification (`_role_guard`) and local lock `task_registry`.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Records timestamped task checkpoint in registry and posts progress summary to active threads.
* **Fixtures:**
  * **Positive (`fix-task-checkpoint-pos-01`, NYI):** Pre-state: Peer `cc` holds role `coordinator`; `task_registry.json` contains `TASK-101`. Request: `task-checkpoint --id TASK-101 --peer cc --msg "Completed step 2 verification"`. Expected exit: 0. Output: `[HUB] TASK-CHECKPOINT TASK-101 | peer=cc`. Post-state: `task_registry.json` has new checkpoint in `TASK-101["checkpoints"]`, `updated_at` refreshed; `handoff.md` `ACTIVE_THREADS` contains checkpoint snippet.
  * **Invalid (`fix-task-checkpoint-inv-01`, NYI):** Pre-state: Standard environment. Request: `task-checkpoint --id TASK-101 --peer cc` (missing `--msg`). Expected exit: 1. Output: `[HUB:ERROR] task-checkpoint requires --id, --peer/--agent, and --msg` to stderr. Post-state: `task_registry.json` unchanged.
  * **Auth (`fix-task-checkpoint-auth-01`, NYI):** Pre-state: Peer `cx` is not assigned `coordinator`, `implementer`, or `documenter` role. Request: `task-checkpoint --id TASK-101 --peer cx --msg "Attempted progress"`. Expected exit: 1. Output: Role guard error to stderr. Post-state: `task_registry.json` unchanged.
  * **Recovery (`fix-task-checkpoint-rec-01`, NYI):** Pre-state: `_sys/data/task_registry.json` is missing on disk. Request: `task-checkpoint --id TASK-202 --peer cc --msg "Initial task start"`. Recovery injection: Hub creates fresh dictionary with `created_at` and `task_id`, then appends initial checkpoint. Expected exit: 0. Output: `[HUB] TASK-CHECKPOINT TASK-202 | peer=cc`. Post-state: `task_registry.json` created with `TASK-202`.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`
