# Phase 1 Parity Ledger - Batch 1 (18 Actions)

## 1. init-session
* **Input Schema:** `agent: str`, `room_id: str | None = None`. Validation: `agent` canonicalized via `_canonical_admission_identity`.
* **Normalized Envelope:** Success: Exit 0, short SID to stdout. Error: Exit 1, `[HUB:ERR]` to stderr.
* **State Changes:** Before: N/A. After: `state.json` gets `room_id` (if missing), agent added to `members`. `sessions/{room_id}` created. `_log_p2p` JOIN emitted.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Generates a new SID and unconditionally emits a new JOIN log entry to `log.jsonl` on every call. Sweeps old leases. Atomic `_write_state` ensures crash resilience.
* **Redaction/Ordering:** Stdout emits short SID exactly.
* **Comparator:** NORMALIZED (SID and timestamps vary).
* **Specific Argv Comparators:**
  * **Safety:** Relies on global lock `_get_lock(ai_root, "state")`.
  * **Cwd/Env/Stdin:** Tolerates any CWD if `ai_root` is resolved. No stdin.
  * **Transport/Process-Tree:** Direct inline execution.
  * **Observed Semantics:** Exact state mutation expected.
* **Fixtures:**
  * **Positive (`fix-init-session-pos-01`, NYI):** Pre-state: Empty `state.json` without `room_id`. Request: `init-session cc`. Expected exit: 0. Output: Short SID string. Post-state: `state.json` contains `room_id` and `cc` in `members`; JOIN log in `log.jsonl`.
  * **Invalid (`fix-init-session-inv-01`, NYI):** Pre-state: Standard empty state. Request: Invalid agent name `@#$`. Expected exit: 1. Output: `[HUB:ERR]` to stderr. Post-state: Unchanged.
  * **Auth (`fix-init-session-auth-01`, NYI):** Pre-state: Unauth environment. Request: `init-session cc`. Expected exit: 1. Output: Denied. Post-state: Unchanged.
  * **Recovery (`fix-init-session-rec-01`, NYI):** Pre-state: Concurrent writer holds `state.json` lock. Request: `init-session cc`. Recovery injection: lock file held externally, hub retries and succeeds. Expected exit: 0. Output: Short SID. Post-state: Successfully merged into state.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 2. end-session
* **Input Schema:** `agent: str`.
* **Normalized Envelope:** Success: Exit 0, `[END] {agent} ?�션 종료 ?�료` to stdout.
* **State Changes:** Removes agent from `state.json` members. Appends to `sessions/{room_id}/handoff.md`. Recalculates `mailbox.json` unread count. Emits `EXIT` to `_log_p2p`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Appends another completion record to `handoff.md` and unconditionally emits an EXIT log entry to `log.jsonl` on every call. Removes agent from state, recalculates unread counts.
* **Redaction/Ordering:** Stdout confirms end.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Safety/Cwd/Env same as init-session.
* **Fixtures:**
  * **Positive (`fix-end-session-pos-01`, NYI):** Pre-state: `state.json` contains `cc`, `handoff.md` exists. Request: `end-session cc`. Expected exit: 0. Output: `[END] cc ?�션 종료 ?�료`. Post-state: `cc` removed from `state.json`, `handoff.md` appended, EXIT logged.
  * **Invalid (`fix-end-session-inv-01`, NYI):** Pre-state: Empty room. Request: `end-session not_an_agent`. Expected exit: 0. Output: End message. Post-state: `handoff.md` appended for non-existent agent.
  * **Auth (`fix-end-session-auth-01`, NYI):** Pre-state: Standard room. Request: `end-session`. Expected exit: 2. Output: Missing arg. Post-state: Unchanged.
  * **Recovery (`fix-end-session-rec-01`, NYI):** Pre-state: Concurrent `state.json` writer. Request: `end-session cc`. Recovery injection: Hub retries atomic state write and mailbox write. Expected exit: 0. Post-state: Correctly processed.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 3. send
* **Input Schema:** `from_: str, to: str, msg: str, thread_id: str|None, msg_type: str="MSG", cc_list: list[str]=[], ref_id: int|None, priority: str|None`. Validates roles. Checks `lifecycle_policy.json`.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] SENT ...`. Error: Exit 1, `[HUB:ERR]` to stderr if policy denied.
* **State Changes:** Appends to `mailbox.json` and maildir. Offloads `msg` to `payloads/*.json` if > `_LARGE_PAYLOAD_THRESHOLD`. Prunes old messages.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Unconditionally assigns a new message ID, appends a new message to `mailbox.json`, writes a new maildir file, and logs a SEND event on every call. Maildir append is durable. Thread correlation via `thread_id`. Large payloads offloaded automatically.
* **Redaction/Ordering:** Stdout prints sent confirmation.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Strict policy enforcement based on config.
* **Fixtures:**
  * **Positive (`fix-send-pos-01`, NYI):** Pre-state: Valid sender/recipient in state. Request: `send cc cx "hello"`. Expected exit: 0. Output: `[HUB] SENT ...` to stdout. Post-state: Mailbox updated with new msg, maildir file created.
  * **Invalid (`fix-send-inv-01`, NYI):** Pre-state: Policy forbids send. Request: `send cc cx "hello"`. Expected exit: 1. Output: `[HUB:ERR] ...`. Post-state: Unchanged.
  * **Auth (`fix-send-auth-01`, NYI):** Pre-state: Missing sender role. Request: `send unknown cx "hello"`. Expected exit: 1. Output: `[HUB:ERR] ...`. Post-state: Unchanged.
  * **Recovery (`fix-send-rec-01`, NYI):** Pre-state: Mailbox lock held. Request: `send cc cx "hello"`. Recovery injection: Hub retries lock. Expected exit: 0. Post-state: Message stored properly.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 4. broadcast
* **Input Schema:** `from_: str, msg: str, targets: list[str]|None, msg_type: str="MSG", priority: str|None`.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] BROADCAST ...`.
* **State Changes:** Resolves targets from `state.json` if None. Invokes `action_send` for each target. Offloads payload once if large.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Generates a new thread ID, unconditionally emits an OFFLOAD log (if large payload), and calls `action_send` for each target which unconditionally appends new messages on every call.
* **Redaction/Ordering:** Stdout details broadcast targets.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Cwd/Env independent.
* **Fixtures:**
  * **Positive (`fix-broadcast-pos-01`, NYI):** Pre-state: Multiple members in room. Request: `broadcast cc "hello everyone"`. Expected exit: 0. Output: `[HUB] BROADCAST ...`. Post-state: New messages appended for each target in mailbox.
  * **Invalid (`fix-broadcast-inv-01`, NYI):** Pre-state: Policy disabled broadcast. Request: `broadcast cc "hello"`. Expected exit: 1. Output: Error. Post-state: Unchanged.
  * **Auth (`fix-broadcast-auth-01`, NYI):** Pre-state: Invalid member. Request: `broadcast unknown "hello"`. Expected exit: 1. Output: Error. Post-state: Unchanged.
  * **Recovery (`fix-broadcast-rec-01`, NYI):** Pre-state: Mailbox lock held. Request: `broadcast cc "hello"`. Recovery injection: Hub retries lock for each send. Expected exit: 0. Post-state: Delivered to all.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 5. mark-read
* **Input Schema:** `target: str, all_: bool, msg_id: int|None`.
* **Normalized Envelope:** Success: Exit 0, prints `[READ] {count}�?메시지 ?�음 처리`.
* **State Changes:** Modifies `mailbox.json` and `maildir` status to `read`. Updates `unread_count`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent. Checks if the message is already read; if it is, it safely ignores it. Uses mailbox lock. Syncs to maildir.
* **Redaction/Ordering:** Outputs modified count.
* **Comparator:** EXACT.
* **Specific Argv Comparators:** Cwd/Env independent.
* **Fixtures:**
  * **Positive (`fix-mark-read-pos-01`, NYI):** Pre-state: Mailbox has unread msg ID 1 for `cx`. Request: `mark-read cx --msg-id 1`. Expected exit: 0. Output: `[READ] 1�?메시지 ?�음 처리`. Post-state: Msg ID 1 status is `read`, unread count decremented.
  * **Invalid (`fix-mark-read-inv-01`, NYI):** Pre-state: Mailbox empty. Request: `mark-read cx --msg-id 999`. Expected exit: 0. Output: `[READ] 0�?메시지 ?�음 처리`. Post-state: Unchanged.
  * **Auth (`fix-mark-read-auth-01`, NYI):** Pre-state: Mailbox has unread msg. Request: `mark-read`. Expected exit: 2. Output: argparse error. Post-state: Unchanged.
  * **Recovery (`fix-mark-read-rec-01`, NYI):** Pre-state: Mailbox lock held. Request: `mark-read cx --all`. Recovery injection: Hub retries lock. Expected exit: 0. Post-state: All read.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 6. append-log
* **Input Schema:** `axis: str, script: str, status: str, detail: str`.
* **Normalized Envelope:** Success: Exit 0, prints `[LOG] {axis} {script} ??{status}`.
* **State Changes:** Appends to `log.jsonl` under `log` lock.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Unconditionally opens `log.jsonl` in append mode and writes a new JSON log entry on every call. Uses `log` lock.
* **Redaction/Ordering:** Stdout mirrors log entry.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Cwd/Env independent.
* **Fixtures:**
  * **Positive (`fix-append-log-pos-01`, NYI):** Pre-state: `log.jsonl` exists. Request: `append-log SYNC sync.py OK "Done"`. Expected exit: 0. Output: `[LOG] ...`. Post-state: `log.jsonl` has new entry.
  * **Invalid (`fix-append-log-inv-01`, NYI):** Pre-state: Standard. Request: Missing args. Expected exit: 2. Output: Argparse err. Post-state: Unchanged.
  * **Auth (`fix-append-log-auth-01`, NYI):** Pre-state: Auth constraints (if any). Request: `append-log SYNC sync.py OK "Done"`. Expected exit: 1. Output: Auth error. Post-state: Unchanged.
  * **Recovery (`fix-append-log-rec-01`, NYI):** Pre-state: Log file locked. Request: `append-log ...`. Recovery injection: Hub retries lock. Expected exit: 0. Post-state: Appended.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 7. archive-file
* **Input Schema:** `name: str, file_path: str`.
* **Normalized Envelope:** Success: Exit 0, prints `[ARCHIVE] ...`. Error: Exit 1 if file missing.
* **State Changes:** Copies `file_path` to `_archive/{name}-{date}.json` and `_archive/{name}-latest.json`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Unconditionally copies the target file, overwriting `-latest.json` but writing to the same `-<date>.json` file if called on the same day. Performs I/O every time.
* **Redaction/Ordering:** N/A.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Relies on filesystem permissions.
* **Fixtures:**
  * **Positive (`fix-archive-file-pos-01`, NYI):** Pre-state: File `test.txt` exists. Request: `archive-file myarch test.txt`. Expected exit: 0. Output: `[ARCHIVE] ...`. Post-state: `_archive/myarch-latest.json` and date-based copy created.
  * **Invalid (`fix-archive-file-inv-01`, NYI):** Pre-state: File does not exist. Request: `archive-file myarch missing.txt`. Expected exit: 1. Output: Missing file. Post-state: Unchanged.
  * **Auth (`fix-archive-file-auth-01`, NYI):** Pre-state: No access to target. Request: `archive-file myarch test.txt`. Expected exit: 1. Output: Access denied. Post-state: Unchanged.
  * **Recovery (`fix-archive-file-rec-01`, NYI):** Pre-state: Destination folder lacks permissions. Request: `archive-file myarch test.txt`. Recovery injection: Validate failure mode cleanly. Expected exit: 1. Post-state: Original intact.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 8. update-status
* **Input Schema:** `mission: str, blocked: str|None, phase: str|None`.
* **Normalized Envelope:** Success: Exit 0, prints `[STATUS] mission={mission}`.
* **State Changes:** Updates `state.json` (mission, blocked, phase) atomically. Logs via `_log_p2p`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Atomically updates `state.json`, but unconditionally emits a new STATUS log entry to `log.jsonl` on every call.
* **Redaction/Ordering:** Output matches updated mission.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Cwd/Env independent.
* **Fixtures:**
  * **Positive (`fix-update-status-pos-01`, NYI):** Pre-state: `state.json` has old mission. Request: `update-status "new mission"`. Expected exit: 0. Output: `[STATUS] mission=new mission`. Post-state: `state.json` updated, STATUS logged.
  * **Invalid (`fix-update-status-inv-01`, NYI):** Pre-state: Standard. Request: `update-status`. Expected exit: 2. Output: argparse error. Post-state: Unchanged.
  * **Auth (`fix-update-status-auth-01`, NYI):** Pre-state: Unauth status update. Request: `update-status "m"`. Expected exit: 1. Output: Err. Post-state: Unchanged.
  * **Recovery (`fix-update-status-rec-01`, NYI):** Pre-state: Concurrent state write. Request: `update-status "m"`. Recovery injection: Hub retries atomic state write. Expected exit: 0. Post-state: Updated.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 9. check
* **Input Schema:** `target: str`.
* **Normalized Envelope:** Success: Exit 0, prints messages or `[HUB] READ 0 messages...`.
* **State Changes:** Read-only. Re-hydrates payloads from `payloads/*.json`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Read-only. Idempotent. Lock-free read of maildir/mailbox. Re-hydrates payloads safely.
* **Redaction/Ordering:** Prints unread messages formatted.
* **Comparator:** SEMANTIC (Format structure remains, content is arbitrary).
* **Specific Argv Comparators:** Cwd/Env independent.
* **Fixtures:**
  * **Positive (`fix-check-pos-01`, NYI):** Pre-state: Unread messages exist for `cx`. Request: `check cx`. Expected exit: 0. Output: Formatted message blocks. Post-state: Unchanged.
  * **Invalid (`fix-check-inv-01`, NYI):** Pre-state: Inbox empty. Request: `check cx`. Expected exit: 0. Output: `[HUB] READ 0 messages...`. Post-state: Unchanged.
  * **Auth (`fix-check-auth-01`, NYI):** Pre-state: Unauth check target. Request: `check cx`. Expected exit: 1. Output: Denied. Post-state: Unchanged.
  * **Recovery (`fix-check-rec-01`, NYI):** Pre-state: Mailbox JSON corrupted. Request: `check cx`. Recovery injection: maildir fallback is used. Expected exit: 0. Post-state: Unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 10. status
* **Input Schema:** None.
* **Normalized Envelope:** Success: Exit 0, prints markdown formatted status.
* **State Changes:** Read-only (except implicit lease sweep). Reads state, mailbox, task registry, locks, consensus.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Read-only check, mostly idempotent (though it implicitly sweeps leases which mutates `leases.json` if stale leases exist).
* **Redaction/Ordering:** Predictable markdown headers.
* **Comparator:** SEMANTIC (Summarized state depends on myriad files).
* **Specific Argv Comparators:** Standard stdout printing.
* **Fixtures:**
  * **Positive (`fix-status-pos-01`, NYI):** Pre-state: Standard room with tasks/locks. Request: `status`. Expected exit: 0. Output: Markdown status report. Post-state: Stale leases swept, otherwise unchanged.
  * **Invalid (`fix-status-inv-01`, NYI):** Pre-state: Corrupted task registry. Request: `status`. Expected exit: 0. Output: Status output without crashing. Post-state: Unchanged.
  * **Auth (`fix-status-auth-01`, NYI):** Pre-state: Cannot access state. Request: `status`. Expected exit: 1. Output: Error. Post-state: Unchanged.
  * **Recovery (`fix-status-rec-01`, NYI):** Pre-state: State lock held. Request: `status`. Recovery injection: Uses lock-free read fallback if needed. Expected exit: 0. Post-state: Unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 11. check-gate
* **Input Schema:** `agent: str`.
* **Normalized Envelope:** Success (ON): Exit 0, `[GATE] {agent}=ON`. Success (OFF): Exit 1, `[GATE] {agent}=OFF`.
* **State Changes:** Read-only. Parses `peers.json` and evaluates gate condition.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Read-only. Deterministic and idempotent check of gate conditions.
* **Redaction/Ordering:** N/A.
* **Comparator:** EXACT.
* **Specific Argv Comparators:** Fast fail semantics.
* **Fixtures:**
  * **Positive (`fix-check-gate-pos-01`, NYI):** Pre-state: Gate file for `agent` is ON. Request: `check-gate agent`. Expected exit: 0. Output: `[GATE] agent=ON`. Post-state: Unchanged.
  * **Invalid (`fix-check-gate-inv-01`, NYI):** Pre-state: Gate file OFF. Request: `check-gate agent`. Expected exit: 1. Output: `[GATE] agent=OFF`. Post-state: Unchanged.
  * **Auth (`fix-check-gate-auth-01`, NYI):** Pre-state: None. Request: `check-gate`. Expected exit: 2. Output: Argparse. Post-state: Unchanged.
  * **Recovery (`fix-check-gate-rec-01`, NYI):** Pre-state: Gate file missing. Request: `check-gate agent`. Recovery injection: Defaults to ON or gracefully fails to OFF. Expected exit: 0/1 depending on logic.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 12. ask
* **Input Schema:** `to: str, query: str, query_file: str|None, timeout_sec: int, ...`.
* **Normalized Envelope:** Success: Exit 0, response stdout. Error: Exit 1. Soft-Skip: Exit 7 (`SOFT_SKIP_EXIT`).
* **State Changes:** Invokes target peer profile, may trigger escalation logic (`[ESCALATE]`), logs to `ask_history.jsonl` and `routing_metrics.jsonl`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Unconditionally logs to `ask_history.jsonl` and `routing_metrics.jsonl` on every call, in addition to dispatching non-deterministic model queries.
* **Redaction/Ordering:** Model-generated answers outside strict equality.
* **Comparator:** INTENTIONAL_DIVERGENCE (Model answers vary inherently).
* **Specific Argv Comparators:** Passes `HUB_ORIGIN` and profile data via env. Sandbox limits.
* **Fixtures:**
  * **Positive (`fix-ask-pos-01`, NYI):** Pre-state: Valid peer. Request: `ask cc "query" --timeout 10`. Expected exit: 0. Output: Model response. Post-state: `ask_history` and `routing_metrics` appended.
  * **Invalid (`fix-ask-inv-01`, NYI):** Pre-state: Unroutable peer. Request: `ask unknown "query"`. Expected exit: 1. Output: routing failure. Post-state: Failure logged.
  * **Auth (`fix-ask-auth-01`, NYI):** Pre-state: Strict governed guard active. Request: `ask cc "query"`. Recovery injection: Mutates files secretly. Expected exit: 1. Output: Guard violation error. Post-state: Ask recorded as failed.
  * **Recovery (`fix-ask-rec-01`, NYI):** Pre-state: Model timeout. Request: `ask cc "query"`. Recovery injection: Subprocess terminates. Expected exit: 1 or 7. Post-state: Metric logged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 13. ask-all
* **Input Schema:** `query: str, query_file: str|None, timeout_sec: int, exclude: list[str]|None`.
* **Normalized Envelope:** Exits with highest non-zero code or `SOFT_SKIP_EXIT`.
* **State Changes:** Broadcasts query to all active peers in parallel via subprocesses.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Unconditionally spawns threads and dispatches queries to all peers, each of which logs to metrics/history. Results are inherently non-deterministic.
* **Redaction/Ordering:** Output ordering dependent on thread completion.
* **Comparator:** INTENTIONAL_DIVERGENCE.
* **Specific Argv Comparators:** Subprocesses receive `HUB_PEER_TIER=standard` env.
* **Fixtures:**
  * **Positive (`fix-ask-all-pos-01`, NYI):** Pre-state: Multiple peers active. Request: `ask-all "query"`. Expected exit: 0 (or max code). Output: Combined responses. Post-state: Metrics appended for all peers.
  * **Invalid (`fix-ask-all-inv-01`, NYI):** Pre-state: No active peers. Request: `ask-all "query"`. Expected exit: 7. Output: No peers found. Post-state: Unchanged.
  * **Auth (`fix-ask-all-auth-01`, NYI):** Pre-state: N/A. Request: `ask-all`. Expected exit: 2. Output: Argparse err. Post-state: Unchanged.
  * **Recovery (`fix-ask-all-rec-01`, NYI):** Pre-state: One peer hangs. Request: `ask-all "query" --timeout 2`. Recovery injection: Thread timeout triggers. Expected exit: 1. Output: Includes `[TIMEOUT]`. Post-state: Partial metrics logged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 14. ask-coordinator
* **Input Schema:** `query: str, query_file: str|None, timeout_sec: int, from_peer: str`.
* **Normalized Envelope:** Forwards envelope to `action_ask`. Error: Exit 1 or 2 if no coordinator.
* **State Changes:** Discovers coordinator via `state.json`. Evaluates health.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Resolves the coordinator and delegates to `action_ask`, but also unconditionally logs routing metrics prior to delegation.
* **Redaction/Ordering:** Follows `action_ask`.
* **Comparator:** SEMANTIC.
* **Specific Argv Comparators:** Envelope wrapper applied.
* **Fixtures:**
  * **Positive (`fix-ask-coordinator-pos-01`, NYI):** Pre-state: Coordinator is active. Request: `ask-coordinator "query" --from cx`. Expected exit: 0. Output: Model response. Post-state: Metrics appended.
  * **Invalid (`fix-ask-coordinator-inv-01`, NYI):** Pre-state: No healthy coordinator available. Request: `ask-coordinator "query"`. Expected exit: 2. Output: No routable coordinator error. Post-state: Unchanged.
  * **Auth (`fix-ask-coordinator-auth-01`, NYI):** Pre-state: N/A. Request: `ask-coordinator`. Expected exit: 2. Output: Args. Post-state: Unchanged.
  * **Recovery (`fix-ask-coordinator-rec-01`, NYI):** Pre-state: Primary coordinator red. Request: `ask-coordinator "query"`. Recovery injection: Fallback to healthy voter list. Expected exit: 0. Post-state: Proxied successfully.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 15. consensus-propose
* **Input Schema:** `subject: str, voters: list[str], proposed_by: str`.
* **Normalized Envelope:** Success: Exit 0, `[HUB] PROPOSE ...`. Error: Exit 1 if active, Exit 3 if rejected >= 3.
* **State Changes:** Creates new round in `consensus/{round_id}.json`. Computes `quorum_snapshot`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Not idempotent. Fails if an active round for the subject exists. Otherwise unconditionally creates a new consensus round file and emits a PROPOSE log.
* **Redaction/Ordering:** Stdout emits proposal details.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Lock `consensus_propose`.
* **Fixtures:**
  * **Positive (`fix-consensus-propose-pos-01`, NYI):** Pre-state: No active round for subject. Request: `consensus-propose "subj" --voters cc cx --proposed-by cc`. Expected exit: 0. Output: `[HUB] PROPOSE r-...`. Post-state: `consensus/r-...json` created, PROPOSE logged.
  * **Invalid (`fix-consensus-propose-inv-01`, NYI):** Pre-state: Active round for "subj" exists. Request: `consensus-propose "subj"`. Expected exit: 1. Output: Already exists error. Post-state: Unchanged.
  * **Auth (`fix-consensus-propose-auth-01`, NYI):** Pre-state: Invalid proposer. Request: `consensus-propose "s"`. Expected exit: 1. Output: Error. Post-state: Unchanged.
  * **Recovery (`fix-consensus-propose-rec-01`, NYI):** Pre-state: Subject rejected 3 times. Request: `consensus-propose "subj"`. Recovery injection: Rejection limit hit. Expected exit: 3. Output: ESCALATE to human. Post-state: Unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 16. consensus-vote
* **Input Schema:** `round_id: str, voter: str, vote_val: str, reason: str`. Vote valid: `agree`, `disagree`, `abstain`.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] VOTE ...`. Error: Exit 1 for invalid vote/voter.
* **State Changes:** Updates round file. If finalized, writes `.capsule.json` and updates `handoff.md`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent short-circuit if identical vote and reason are submitted by the same voter. Otherwise modifies the round file and can trigger irreversible side-effects (finalization).
* **Redaction/Ordering:** Stdout confirms vote cast.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Lock `consensus_{round_id}`. Emits capsule upon closure.
* **Fixtures:**
  * **Positive (`fix-consensus-vote-pos-01`, NYI):** Pre-state: Round `r-123` active, `cc` has not voted. Request: `consensus-vote r-123 cc agree "looks good"`. Expected exit: 0. Output: `[HUB] VOTE r-123 ...`. Post-state: `r-123.json` updated with vote.
  * **Invalid (`fix-consensus-vote-inv-01`, NYI):** Pre-state: Voter already cast a DIFFERENT vote. Request: `consensus-vote r-123 cc disagree "no"`. Expected exit: 1. Output: `VOTE_ALREADY_CAST`. Post-state: Unchanged.
  * **Auth (`fix-consensus-vote-auth-01`, NYI):** Pre-state: Voter not in voter list. Request: `consensus-vote r-123 unknown agree`. Expected exit: 1. Output: Not registered error. Post-state: Unchanged.
  * **Recovery (`fix-consensus-vote-rec-01`, NYI):** Pre-state: Sandbox rename denied. Request: `consensus-vote r-123 cc agree`. Recovery injection: Write fails, triggers broker fallback. Expected exit: 0. Output: Queued via broker. Post-state: Vote written to broker queue.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 17. consensus-check
* **Input Schema:** `round_id: str | None`.
* **Normalized Envelope:** Success: Exit 0, prints round status.
* **State Changes:** Read-only formatting of `consensus/*.json`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Read-only formatting of consensus state. Idempotent.
* **Redaction/Ordering:** Markdown-styled stdout block.
* **Comparator:** SEMANTIC.
* **Specific Argv Comparators:** Standard stdout.
* **Fixtures:**
  * **Positive (`fix-consensus-check-pos-01`, NYI):** Pre-state: Active round exists. Request: `consensus-check r-123`. Expected exit: 0. Output: Markdown representation of round state. Post-state: Unchanged.
  * **Invalid (`fix-consensus-check-inv-01`, NYI):** Pre-state: Legacy malformed round file. Request: `consensus-check r-123`. Expected exit: 0. Output: Handles missing keys defensively. Post-state: Unchanged.
  * **Auth (`fix-consensus-check-auth-01`, NYI):** Pre-state: N/A. Request: `consensus-check`. Expected exit: 0. Output: Standard. Post-state: Unchanged.
  * **Recovery (`fix-consensus-check-rec-01`, NYI):** Pre-state: Directory missing. Request: `consensus-check`. Recovery injection: Exits gracefully. Expected exit: 0. Post-state: Unchanged.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`

## 18. consensus-sweep
* **Input Schema:** `timeout_minutes: int = 30`.
* **Normalized Envelope:** Success: Exit 0.
* **State Changes:** Auto-escalates rounds older than timeout to `human_gate_timeout`.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent over repeated calls (a swept round has `status="escalated"` and is skipped in future sweeps), but mutates state for any stalled rounds.
* **Redaction/Ordering:** Silent or logs swept count.
* **Comparator:** NORMALIZED.
* **Specific Argv Comparators:** Time-based sweep.
* **Fixtures:**
  * **Positive (`fix-consensus-sweep-pos-01`, NYI):** Pre-state: Round older than timeout. Request: `consensus-sweep`. Expected exit: 0. Output: `[HUB] SWEEP ... ESCALATED`. Post-state: Round status changed to escalated.
  * **Invalid (`fix-consensus-sweep-inv-01`, NYI):** Pre-state: No rounds older than timeout. Request: `consensus-sweep`. Expected exit: 0. Output: No stalled rounds. Post-state: Unchanged.
  * **Auth (`fix-consensus-sweep-auth-01`, NYI):** Pre-state: N/A. Request: `consensus-sweep`. Expected exit: 0. Output: ok. Post-state: Unchanged.
  * **Recovery (`fix-consensus-sweep-rec-01`, NYI):** Pre-state: Round locked. Request: `consensus-sweep`. Recovery injection: Skips or retries lock. Expected exit: 0. Post-state: Escalated eventually.
* **Legacy Digest:** `3b2d750381a440a70138bdcbca819e9cb55bebf9dc596d551c5b18b87bc6ae3f` | **Proof Ref:** `[No explicit proof artifact yet; hash verified locally against P:\workspace\Engram]`
