# Phase 1 Parity Ledger - Batch 1 (18 Actions)

## 1. init-session
* **Input Schema:** `agent: str`, `room_id: str | None = None`. Validation: `agent` canonicalized via `_canonical_admission_identity`.
* **Normalized Envelope:** Success: Exit 0, short SID to stdout. Error: Exit 1, `[HUB:ERR]` to stderr.
* **State Changes:** Before: N/A. After: `state.json` gets `room_id` (if missing), agent added to `members`. `sessions/{room_id}` created. `_log_p2p` JOIN emitted.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent for same agent/room. Sweeps old leases prior to init. Resilient to crash via atomic `_write_state`.
* **Redaction/Ordering:** Stdout emits short SID exactly.
* **Comparator:** NORMALIZED (SID and timestamps vary).
* **Specific Argv Comparators:**
  * **Safety:** Relies on global lock `_get_lock(ai_root, "state")`.
  * **Cwd/Env/Stdin:** Tolerates any CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline execution.
  * **Observed Semantics:** Exact state mutation expected.
* **Fixtures:** Positive: `fix-init-session-pos-01` (Not yet implemented), Invalid: `fix-init-session-inv-01` (Not yet implemented), Auth: `fix-init-session-auth-01` (Not yet implemented), Recovery: `fix-init-session-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 2. end-session
* **Input Schema:** `agent: str`.
* **Normalized Envelope:** Success: Exit 0, `[END] {agent} 세션 종료 완료` to stdout.
* **State Changes:** Removes agent from `state.json` members. Appends to `sessions/{room_id}/handoff.md`. Recalculates `mailbox.json` unread count. Emits `EXIT` to `_log_p2p`.
* **Behaviors:** Idempotent. Session state gracefully closed.
* **Redaction/Ordering:** Stdout confirms end.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Safety/Cwd/Env same as init-session.
* **Fixtures:** Pos: `fix-end-session-pos-01` (NYI), Inv: `fix-end-session-inv-01` (NYI), Auth: `fix-end-session-auth-01` (NYI), Rec: `fix-end-session-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 3. send
* **Input Schema:** `from_: str, to: str, msg: str, thread_id: str|None, msg_type: str="MSG", cc_list: list[str]=[], ref_id: int|None, priority: str|None`. Validates roles. Checks `lifecycle_policy.json`.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] SENT ...`. Error: Exit 1, `[HUB:ERR]` to stderr if policy denied.
* **State Changes:** Appends to `mailbox.json` and maildir. Offloads `msg` to `payloads/*.json` if > `_LARGE_PAYLOAD_THRESHOLD`. Prunes old messages.
* **Behaviors:** Maildir append is durable. Thread correlation via `thread_id`.
* **Redaction/Ordering:** Stdout prints sent confirmation.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Strict policy enforcement based on config.
* **Fixtures:** Pos: `fix-send-pos-01` (NYI), Inv: `fix-send-inv-01` (NYI), Auth: `fix-send-auth-01` (NYI), Rec: `fix-send-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 4. broadcast
* **Input Schema:** `from_: str, msg: str, targets: list[str]|None, msg_type: str="MSG", priority: str|None`.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] BROADCAST ...`.
* **State Changes:** Resolves targets from `state.json` if None. Invokes `action_send` for each target. Offloads payload once if large.
* **Behaviors:** Idempotent message delivery to multiple peers.
* **Redaction/Ordering:** Stdout details broadcast targets.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Cwd/Env independent.
* **Fixtures:** Pos: `fix-broadcast-pos-01` (NYI), Inv: `fix-broadcast-inv-01` (NYI), Auth: `fix-broadcast-auth-01` (NYI), Rec: `fix-broadcast-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 5. mark-read
* **Input Schema:** `target: str, all_: bool, msg_id: int|None`.
* **Normalized Envelope:** Success: Exit 0, prints `[READ] {count}개 메시지 읽음 처리`.
* **State Changes:** Modifies `mailbox.json` and `maildir` status to `read`. Updates `unread_count`.
* **Behaviors:** Idempotent. Lock `mailbox` used.
* **Redaction/Ordering:** Outputs modified count.
* **Comparator:** EXACT.
* **Specific Argv Comparators:** Cwd/Env independent.
* **Fixtures:** Pos: `fix-mark-read-pos-01` (NYI), Inv: `fix-mark-read-inv-01` (NYI), Auth: `fix-mark-read-auth-01` (NYI), Rec: `fix-mark-read-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 6. append-log
* **Input Schema:** `axis: str, script: str, status: str, detail: str`.
* **Normalized Envelope:** Success: Exit 0, prints `[LOG] {axis} {script} → {status}`.
* **State Changes:** Appends to `log.jsonl` under `log` lock.
* **Behaviors:** Append-only logging.
* **Redaction/Ordering:** Stdout mirrors log entry.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Cwd/Env independent.
* **Fixtures:** Pos: `fix-append-log-pos-01` (NYI), Inv: `fix-append-log-inv-01` (NYI), Auth: `fix-append-log-auth-01` (NYI), Rec: `fix-append-log-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 7. archive-file
* **Input Schema:** `name: str, file_path: str`.
* **Normalized Envelope:** Success: Exit 0, prints `[ARCHIVE] ...`. Error: Exit 1 if file missing.
* **State Changes:** Copies `file_path` to `_archive/{name}-{date}.json` and `_archive/{name}-latest.json`.
* **Behaviors:** Disk I/O intensive, overwrites `-latest.json`.
* **Redaction/Ordering:** N/A.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Relies on filesystem permissions.
* **Fixtures:** Pos: `fix-archive-file-pos-01` (NYI), Inv: `fix-archive-file-inv-01` (NYI), Auth: `fix-archive-file-auth-01` (NYI), Rec: `fix-archive-file-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 8. update-status
* **Input Schema:** `mission: str, blocked: str|None, phase: str|None`.
* **Normalized Envelope:** Success: Exit 0, prints `[STATUS] mission={mission}`.
* **State Changes:** Updates `state.json` (mission, blocked, phase) atomically. Logs via `_log_p2p`.
* **Behaviors:** Overwrites current status completely.
* **Redaction/Ordering:** Output matches updated mission.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Cwd/Env independent.
* **Fixtures:** Pos: `fix-update-status-pos-01` (NYI), Inv: `fix-update-status-inv-01` (NYI), Auth: `fix-update-status-auth-01` (NYI), Rec: `fix-update-status-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 9. check
* **Input Schema:** `target: str`.
* **Normalized Envelope:** Success: Exit 0, prints messages or `[HUB] READ 0 messages...`.
* **State Changes:** Read-only. Re-hydrates payloads from `payloads/*.json`.
* **Behaviors:** No locks acquired (lock-free read).
* **Redaction/Ordering:** Prints unread messages formatted.
* **Comparator:** SEMANTIC (Format structure remains, content is arbitrary).
* **Specific Argv Comparators:** Cwd/Env independent.
* **Fixtures:** Pos: `fix-check-pos-01` (NYI), Inv: `fix-check-inv-01` (NYI), Auth: `fix-check-auth-01` (NYI), Rec: `fix-check-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 10. status
* **Input Schema:** None.
* **Normalized Envelope:** Success: Exit 0, prints markdown formatted status.
* **State Changes:** Read-only (except implicit lease sweep). Reads state, mailbox, task registry, locks, consensus.
* **Behaviors:** Aggregates overall system status.
* **Redaction/Ordering:** Predictable markdown headers.
* **Comparator:** SEMANTIC (Summarized state depends on myriad files).
* **Specific Argv Comparators:** Standard stdout printing.
* **Fixtures:** Pos: `fix-status-pos-01` (NYI), Inv: `fix-status-inv-01` (NYI), Auth: `fix-status-auth-01` (NYI), Rec: `fix-status-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 11. check-gate
* **Input Schema:** `agent: str`.
* **Normalized Envelope:** Success (ON): Exit 0, `[GATE] {agent}=ON`. Success (OFF): Exit 1, `[GATE] {agent}=OFF`.
* **State Changes:** Read-only. Parses `peers.json` and evaluates gate condition.
* **Behaviors:** Deterministic exit code based on external gate file.
* **Redaction/Ordering:** N/A.
* **Comparator:** EXACT.
* **Specific Argv Comparators:** Fast fail semantics.
* **Fixtures:** Pos: `fix-check-gate-pos-01` (NYI), Inv: `fix-check-gate-inv-01` (NYI), Auth: `fix-check-gate-auth-01` (NYI), Rec: `fix-check-gate-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 12. ask
* **Input Schema:** `to: str, query: str, query_file: str|None, timeout_sec: int, ...`.
* **Normalized Envelope:** Success: Exit 0, response stdout. Error: Exit 1. Soft-Skip: Exit 7 (`SOFT_SKIP_EXIT`).
* **State Changes:** Invokes target peer profile, may trigger escalation logic (`[ESCALATE]`), logs to `ask_history.jsonl` and `routing_metrics.jsonl`.
* **Behaviors:** High-complexity IPC binding. Supports load balancing, context fill, session policy.
* **Redaction/Ordering:** Model-generated answers outside strict equality.
* **Comparator:** INTENTIONAL_DIVERGENCE (Model answers vary inherently).
* **Specific Argv Comparators:** Passes `HUB_ORIGIN` and profile data via env. Sandbox limits.
* **Fixtures:** Pos: `fix-ask-pos-01` (NYI), Inv: `fix-ask-inv-01` (NYI), Auth: `fix-ask-auth-01` (NYI), Rec: `fix-ask-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 13. ask-all
* **Input Schema:** `query: str, query_file: str|None, timeout_sec: int, exclude: list[str]|None`.
* **Normalized Envelope:** Exits with highest non-zero code or `SOFT_SKIP_EXIT`.
* **State Changes:** Broadcasts query to all active peers in parallel via subprocesses.
* **Behaviors:** Threaded execution. Gathers outputs into unified stdout representation.
* **Redaction/Ordering:** Output ordering dependent on thread completion.
* **Comparator:** INTENTIONAL_DIVERGENCE.
* **Specific Argv Comparators:** Subprocesses receive `HUB_PEER_TIER=standard` env.
* **Fixtures:** Pos: `fix-ask-all-pos-01` (NYI), Inv: `fix-ask-all-inv-01` (NYI), Auth: `fix-ask-all-auth-01` (NYI), Rec: `fix-ask-all-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 14. ask-coordinator
* **Input Schema:** `query: str, query_file: str|None, timeout_sec: int, from_peer: str`.
* **Normalized Envelope:** Forwards envelope to `action_ask`. Error: Exit 1 or 2 if no coordinator.
* **State Changes:** Discovers coordinator via `state.json`. Evaluates health.
* **Behaviors:** Re-routes via `action_ask`.
* **Redaction/Ordering:** Follows `action_ask`.
* **Comparator:** SEMANTIC.
* **Specific Argv Comparators:** Envelope wrapper applied.
* **Fixtures:** Pos: `fix-ask-coordinator-pos-01` (NYI), Inv: `fix-ask-coordinator-inv-01` (NYI), Auth: `fix-ask-coordinator-auth-01` (NYI), Rec: `fix-ask-coordinator-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 15. consensus-propose
* **Input Schema:** `subject: str, voters: list[str], proposed_by: str`.
* **Normalized Envelope:** Success: Exit 0, `[HUB] PROPOSE ...`. Error: Exit 1 if active, Exit 3 if rejected >= 3.
* **State Changes:** Creates new round in `consensus/{round_id}.json`. Computes `quorum_snapshot`.
* **Behaviors:** Enforces limits on repeat proposals. Snapshots live voters list.
* **Redaction/Ordering:** Stdout emits proposal details.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Lock `consensus_propose`.
* **Fixtures:** Pos: `fix-consensus-propose-pos-01` (NYI), Inv: `fix-consensus-propose-inv-01` (NYI), Auth: `fix-consensus-propose-auth-01` (NYI), Rec: `fix-consensus-propose-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 16. consensus-vote
* **Input Schema:** `round_id: str, voter: str, vote_val: str, reason: str`. Vote valid: `agree`, `disagree`, `abstain`.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] VOTE ...`. Error: Exit 1 for invalid vote/voter.
* **State Changes:** Updates round file. If finalized, writes `.capsule.json` and updates `handoff.md`.
* **Behaviors:** `SandboxRenameDeniedError` routes vote to broker queue for host merge. Evaluates closure via `_decide_consensus`.
* **Redaction/Ordering:** Stdout confirms vote cast.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Lock `consensus_{round_id}`. Emits capsule upon closure.
* **Fixtures:** Pos: `fix-consensus-vote-pos-01` (NYI), Inv: `fix-consensus-vote-inv-01` (NYI), Auth: `fix-consensus-vote-auth-01` (NYI), Rec: `fix-consensus-vote-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 17. consensus-check
* **Input Schema:** `round_id: str | None`.
* **Normalized Envelope:** Success: Exit 0, prints round status.
* **State Changes:** Read-only formatting of `consensus/*.json`.
* **Behaviors:** Defensive schema parsing (handles legacy/malformed rounds safely).
* **Redaction/Ordering:** Markdown-styled stdout block.
* **Comparator:** SEMANTIC.
* **Specific Argv Comparators:** Standard stdout.
* **Fixtures:** Pos: `fix-consensus-check-pos-01` (NYI), Inv: `fix-consensus-check-inv-01` (NYI), Auth: `fix-consensus-check-auth-01` (NYI), Rec: `fix-consensus-check-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 18. consensus-sweep
* **Input Schema:** `timeout_minutes: int = 30`.
* **Normalized Envelope:** Success: Exit 0.
* **State Changes:** Auto-escalates rounds older than timeout to `human_gate_timeout`.
* **Behaviors:** Operates globally on consensus dir to prune stalled processes.
* **Redaction/Ordering:** Silent or logs swept count.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Time-based sweep.
* **Fixtures:** Pos: `fix-consensus-sweep-pos-01` (NYI), Inv: `fix-consensus-sweep-inv-01` (NYI), Auth: `fix-consensus-sweep-auth-01` (NYI), Rec: `fix-consensus-sweep-rec-01` (NYI).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`
