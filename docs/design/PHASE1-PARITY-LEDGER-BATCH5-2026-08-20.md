# Phase 1 Parity Ledger - Batch 5 (18 Actions)

## 1. lesson-broadcast
* **Input Schema:** `ai_root: Path`, `lesson_id: str` (via `--lesson-id` or `--round-id`, required), `from_peer: str = "system"` (via `--from` or `--peer`). Validation: Requires non-empty `lesson_id` (exits 1 if missing). Searches active lessons across global and workspace `active-lessons.jsonl` for `id == lesson_id` (exits 1 if not found or inactive). Resolves target room members from `state.json` excluding `from_peer` (no-op if empty). Authorization: Continuous learning / lesson notification broadcast interface.
* **Normalized Envelope:** Success (broadcast dispatched): Exit 0, prints `[HUB] LESSON-BROADCAST {lesson_id} -> {targets}` to stdout. Success (no other room members): Exit 0, prints `[HUB] LESSON-BROADCAST {lesson_id} | no targets (no other room members)` to stdout. Error (missing lesson ID): Exit 1, prints `[HUB:ERROR] lesson-broadcast requires --lesson-id` to stderr. Error (lesson not found): Exit 1, prints `[HUB:ERROR] lesson {lesson_id} not found or not active` to stderr.
* **State Changes:** Before: Active lesson in repository; target peers have not received the notification. After: Calls `action_broadcast` dispatching message `[LESSON:{lesson_id}] {SEV} — {title} | Rule: {rule}` with `type="LESSON"`, `priority="P1"` to each target peer's mailbox / maildir. External effects: Dispatches broadcast lesson notifications into peer mailboxes.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent re-broadcast capability. Lock-free read of active lessons. Durable message delivery via mailbox system.
* **Redaction/Ordering:** Stdout confirms broadcast destination targets or no-target notification.
* **Comparator:** NORMALIZED (target list ordering, lesson ID, timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Lesson existence validation and non-empty ID enforcement.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Broadcasts active lesson summary and rule payload to all active room members.
* **Fixtures:** Positive: `fix-lesson-broadcast-pos-01` (Not yet implemented), Invalid: `fix-lesson-broadcast-inv-01` (Not yet implemented), Auth: `fix-lesson-broadcast-auth-01` (Not yet implemented), Recovery: `fix-lesson-broadcast-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 2. lesson-sweep
* **Input Schema:** `ai_root: Path`, `min_triggers: int = 3` (via `--min-triggers`), `stale_days: int = 14` (via `--days`). Validation: Parses integer trigger threshold (`min_triggers`, default 3) and staleness window (`stale_days`, default 14). Authorization: Autonomous knowledge lifecycle maintenance / governance sweep.
* **Normalized Envelope:** Success: Exit 0, prints zero or more promotion lines `[HUB] LESSON-SWEEP promote {id} (triggers={count})`, retirement lines `[HUB] LESSON-SWEEP retire {id} (triggers={count}/{min_triggers})`, skip-retire lines `[HUB] LESSON-SWEEP skip-retire {id} (sticky=true)`, and summary `[HUB] LESSON-SWEEP done | promoted={promoted} retired={retired}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Lessons in global and workspace `active-lessons.jsonl` with various usage metrics. After: (1) For lessons with `trigger_count >= min_triggers` not already promoted, creates runtime directive in `runtime-directives.jsonl` via `_save_runtime_directive` (`ttl_hours=48`, `clear_condition="manual"`, `source_peer="lesson-sweep"`) and marks `promoted_to_directive = True`. (2) For lessons with `trigger_count < min_triggers` and `last_triggered` older than `cutoff` (`now - timedelta(days=stale_days)`), marks `status = "retired"`, `retirement.retired_at`, and `retirement.retire_reason`, unless `sticky == True` (which protects the lesson from retirement). (3) Rewrites `active-lessons.jsonl` files atomically. External effects: Promotes frequent lessons to active runtime directives and retires stale unreferenced lessons.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent watchdog sweep. Sticky lessons are guaranteed immune from automated retirement. Atomic line-by-line rewrite of lesson repository files.
* **Redaction/Ordering:** Stdout outputs individual action logs followed by summary metric counts.
* **Comparator:** NORMALIZED (trigger counts, lesson IDs, ISO timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Sticky flag protection and threshold validation.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Evaluates lesson frequency metrics, promotes high-frequency rules to directives, and retires stale unreferenced lessons.
* **Fixtures:** Positive: `fix-lesson-sweep-pos-01` (Not yet implemented), Invalid: `fix-lesson-sweep-inv-01` (Not yet implemented), Auth: `fix-lesson-sweep-auth-01` (Not yet implemented), Recovery: `fix-lesson-sweep-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 3. lesson-inject
* **Input Schema:** `ai_root: Path`, `peer_id: str = "cc"` (via `--peer` or `--to`). Validation: None. Loads active lessons via `_load_active_lessons(workspace_ai_root=ai_root)`, filters for `peer_id` via `_filter_lessons_for_peer`, and compiles the prompt injection block via `_compile_lessons_block`. Authorization: Read-only startup context injection interface.
* **Normalized Envelope:** Success (lessons available): Exit 0, prints formatted multi-line `[PEER LESSONS (peer={peer_id})]` block to stdout. Success (no active lessons for peer): Exit 0, prints `[HUB] No active lessons for peer={peer_id}` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation against global and workspace `active-lessons.jsonl`. No file modifications or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. Lock-free context compilation. Model-generated answer text is excluded from equality comparison.
* **Redaction/Ordering:** Formatted markdown block or fallback message printed to stdout.
* **Comparator:** NORMALIZED (lesson ordering by severity and ID).
* **Specific Argv Comparators:**
  * **Safety:** Pure read-only context generation.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Compiles peer-specific active lessons into a system prompt injection block.
* **Fixtures:** Positive: `fix-lesson-inject-pos-01` (Not yet implemented), Invalid: `fix-lesson-inject-inv-01` (Not yet implemented), Auth: `fix-lesson-inject-auth-01` (Not yet implemented), Recovery: `fix-lesson-inject-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 4. thread-new
* **Input Schema:** `ai_root: Path`, `topic: str` (via `--topic`, required), `from_peer: str = "cc"` (via `--from` or `--peer`), `msg: str = ""` (via `--msg`). Validation: Requires non-empty `topic` (exits 1 if missing). Sanitizes `topic` to slug `re.sub(r"[^\w-]", "-", topic.lower())[:40]`. Checks if `.ai/sessions/{room}/threads/{topic_slug}.jsonl` already exists (if exists, returns 0 with guidance message). Authorization: Inter-peer collaboration / persistent thread creation.
* **Normalized Envelope:** Success (thread created): Exit 0, prints `[HUB] THREAD-NEW '{topic_slug}' | from={from_peer} | file={path.name}` to stdout. Success (thread already exists): Exit 0, prints `[HUB] Thread '{topic_slug}' already exists. Use thread-append to add messages.` to stdout. Error (missing topic): Exit 1, prints `[HUB] thread-new requires --topic` to stderr.
* **State Changes:** Before: Thread file absent. After: Creates `.ai/sessions/{room}/threads/{topic_slug}.jsonl` with initial `THREAD_CREATE` record (`id`, `from`, `ts`, `type: "THREAD_CREATE"`, `topic`, `content`, `reactions: {}`). Appends opening event to `ACTIVE_THREADS` in `handoff.md`. External effects: Registers new discussion thread in room session.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Safe collision handling (prevents accidental overwriting of existing thread files). Append-only thread file creation. Handoff log synchronization.
* **Redaction/Ordering:** Stdout confirms topic slug, author peer, and file name.
* **Comparator:** EXACT (slug, author) / NORMALIZED (short message ID, timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Topic slug sanitization and collision prevention.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Initializes durable topic thread and records it in session handoff.
* **Fixtures:** Positive: `fix-thread-new-pos-01` (Not yet implemented), Invalid: `fix-thread-new-inv-01` (Not yet implemented), Auth: `fix-thread-new-auth-01` (Not yet implemented), Recovery: `fix-thread-new-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 5. thread-append
* **Input Schema:** `ai_root: Path`, `topic: str` (via `--topic`, required), `from_peer: str = "cc"` (via `--from` or `--peer`), `msg: str` (via `--msg`, required content). Validation: Requires non-empty `topic` (exits 1 if missing). Sanitizes `topic` to slug. Validates that `.ai/sessions/{room}/threads/{topic_slug}.jsonl` exists (exits 1 if not found: `[HUB:ERROR] thread '{topic_slug}' not found. Create with thread-new first.`). Authorization: Inter-peer collaboration / thread messaging.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] THREAD-APPEND '{topic_slug}' | from={from_peer} | id={entry['id']}` to stdout. Error (missing topic): Exit 1, prints `[HUB] thread-append requires --topic` to stderr. Error (thread not found): Exit 1, prints `[HUB:ERROR] thread '{topic_slug}' not found. Create with thread-new first.` to stderr.
* **State Changes:** Before: Existing thread `.jsonl` file. After: Appends message record `{"id": _short_msg_id(), "from": from_peer, "ts": _now(), "type": "MSG", "topic": topic_slug, "content": msg, "reactions": {}}` to the thread file. External effects: Records message in durable room thread history.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Append-only durability. Generates unique short message ID per call. Multiple invocations append distinct messages in chronological order.
* **Redaction/Ordering:** Stdout confirms topic slug, author peer, and generated message ID.
* **Comparator:** EXACT (slug, author) / NORMALIZED (short message ID, timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Thread existence verification.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Appends message to an active topic thread.
* **Fixtures:** Positive: `fix-thread-append-pos-01` (Not yet implemented), Invalid: `fix-thread-append-inv-01` (Not yet implemented), Auth: `fix-thread-append-auth-01` (Not yet implemented), Recovery: `fix-thread-append-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 6. thread-react
* **Input Schema:** `ai_root: Path`, `topic: str` (via `--topic`, required), `from_peer: str = "cc"` (via `--from` or `--peer`), `emoji: str` (via `--emoji`, required, e.g. `ACK`, `NACK`, `BLOCKED`, `IDEA`, `DONE`), `msg_id: int | None = None` (via `--id`). Validation: Requires non-empty `topic` and `emoji` (exits 1 if missing). Validates that thread file exists and is not empty (exits 1 if missing or empty). If `msg_id` is supplied, validates that target message exists in thread (exits 1 if not found). Defaults to reacting to the last message if `msg_id` is None. Authorization: Inter-peer consensus reaction interface.
* **Normalized Envelope:** Success (reaction recorded): Exit 0, prints `[HUB] THREAD-REACT '{topic_slug}' | {from_peer}:{emoji} -> msg={msg_id}` to stdout. Success (unanimous ACK consensus triggered): Exit 0, prints `[HUB] THREAD-REACT ...` followed by `[HUB] CONSENSUS_REACHED on thread '{topic_slug}' msg={msg_id} | voters={voter1,voter2...}` to stdout. Error (missing args): Exit 1, prints `[HUB] thread-react requires --topic and --emoji` to stderr. Error (thread not found / empty / invalid msg ID): Exit 1, prints `[HUB:ERROR] ...` to stderr.
* **State Changes:** Before: Target message in thread `.jsonl` without this reaction. After: Updates `reactions[from_peer] = emoji` on target message and rewrites thread file. If all configured `r10_voters` have reacted with `"ACK"`, logs consensus achievement to `CONSENSUS_HISTORY` in `handoff.md`. External effects: Records reaction and registers consensus milestone in session handoff.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Idempotent update for identical peer and emoji. Atomic file rewrite of thread file. Automatic R10 consensus evaluation.
* **Redaction/Ordering:** Stdout outputs reaction confirmation and optional consensus announcement.
* **Comparator:** EXACT (emoji, peer, topic slug) / NORMALIZED (timestamps, voter list).
* **Specific Argv Comparators:**
  * **Safety:** Message ID resolution and thread validation.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Sets reaction emoji on thread message and evaluates R10 consensus trigger.
* **Fixtures:** Positive: `fix-thread-react-pos-01` (Not yet implemented), Invalid: `fix-thread-react-inv-01` (Not yet implemented), Auth: `fix-thread-react-auth-01` (Not yet implemented), Recovery: `fix-thread-react-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 7. thread-promote
* **Input Schema:** `ai_root: Path`, `msg_id: str` (via `--msg-id`, required), `to_thread_id: str = "general"` (via `--thread-id`), `agent: str = "unknown"` (via `--agent` or `--peer`). Validation: Searches `mailbox.json` under lock `mailbox` for message matching `id == msg_id` (exits 1 if not found: `[HUB:ERROR] message {msg_id} not found in mailbox`). Authorization: Mailbox message lifecycle / thread promotion interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] Message {msg_id} promoted to thread {topic_slug}` to stdout. Emits P2P log `THREAD-PROMOTE`. Error (message not found): Exit 1, prints `[HUB:ERROR] message {msg_id} not found in mailbox` to stderr. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Transient message in `mailbox.json`. After: Under lock `mailbox`: (1) Appends promoted message record `{"id": _short_msg_id(), "ts": found_msg.ts, "from": found_msg.from, "type": "MSG_PROMOTED", "content": f"[PROMOTED from {msg_id}] {found_msg.msg}", "promoted_by": agent, "promoted_at": _now(), "reactions": {}}` to `.ai/sessions/{room}/threads/{topic_slug}.jsonl`. (2) Marks `found_msg["promoted_to"] = topic_slug` in `mailbox.json` and writes atomically via `_write_json_atomic`. (3) Emits P2P log entry `_log_p2p("THREAD-PROMOTE", ...)`. External effects: Elevates transient mailbox notification into durable room thread history.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Serialized concurrency under lock `mailbox`. Atomic write of `mailbox.json`. Idempotent promotion marking.
* **Redaction/Ordering:** Stdout confirms message promotion to target topic slug.
* **Comparator:** EXACT (message ID, topic slug) / NORMALIZED (timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Mailbox lock serialization and atomic state persistence.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Copies mailbox message into durable thread and marks source message.
* **Fixtures:** Positive: `fix-thread-promote-pos-01` (Not yet implemented), Invalid: `fix-thread-promote-inv-01` (Not yet implemented), Auth: `fix-thread-promote-auth-01` (Not yet implemented), Recovery: `fix-thread-promote-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 8. alert-raise
* **Input Schema:** `ai_root: Path`, `agent: str = "unknown"` (via `--agent` or `--peer`), `severity: str = "P1"` (via `--severity`, accepts `"P0"` or `"P1"`), `msg: str = ""` (via `--msg`). Validation: Validates `severity.upper()` is in `("P0", "P1")` (exits 1 if invalid: `[HUB:ERROR] invalid severity '{severity}'; must be P0 or P1`). Authorization: Emergency circuit-breaker / high-severity alert interface.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] !!! {severity.upper()} ALERT RAISED by {agent} !!!: {msg}` to stdout. Emits P2P log `ALERT-RAISE`. Dispatches critical alert messages to all other room members. Error (invalid severity): Exit 1, prints `[HUB:ERROR] invalid severity '{severity}'; must be P0 or P1` to stderr.
* **State Changes:** Before: System unblocked or in previous alert state. After: Under lock `state`: (1) Updates `state.json` setting `alert_active = {"id": _short_id("alert-"), "ts": _now(), "severity": severity.upper(), "from": agent, "msg": msg, "status": "OPEN", "ack_pending": list(members.keys())}`, `blocked = f"{severity.upper()} Alert: {msg[:40]}..."`, `updated_at = _now()`. (2) Emits P2P log `ALERT-RAISE`. (3) Calls `action_send` sending `[CRITICAL-ALERT] {severity}: {msg}` with `msg_type="ALERT"` and `priority="CRITICAL"` to every other room member. External effects: Activates global workflow block across room peers and queues critical alert in peer inboxes.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Serialized concurrency under lock `state`. Immediate emergency gate activation. Fan-out notification dispatch.
* **Redaction/Ordering:** Stdout outputs high-visibility alert banner.
* **Comparator:** EXACT (severity, agent, message).
* **Specific Argv Comparators:**
  * **Safety:** Strict severity enum validation (`P0`/`P1`) and state lock acquisition.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Blocks governance workflows and dispatches critical alert notifications to all members.
* **Fixtures:** Positive: `fix-alert-raise-pos-01` (Not yet implemented), Invalid: `fix-alert-raise-inv-01` (Not yet implemented), Auth: `fix-alert-raise-auth-01` (Not yet implemented), Recovery: `fix-alert-raise-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 9. proposal-add
* **Input Schema:** `ai_root: Path`, `subject: str` (via `--subject`, required), `from_peer: str = "cc"` (via `--from` or `--peer`), `impact: str = "med"` (via `--impact`), `rationale: str = ""` (via `--rationale` or `--detail`), `text: str = ""` (via `--text`). Validation: Requires non-empty `subject` (exits 1 if missing: `[HUB] proposal-add requires --subject`). Filters healthy voters (`not in RED, STALE`) from protocol config `r10_voters`. Authorization: Architecture and governance proposal creation.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] PROPOSAL-ADD {proposal_id} | from={from_peer} | impact={impact.upper()}` followed by `      Vote with: hub.py proposal-vote --proposal-id {proposal_id} --vote agree --voter <peer>` to stdout. Error (missing subject): Exit 1, prints `[HUB] proposal-add requires --subject` to stderr.
* **State Changes:** Before: Proposal file does not exist in `_sys/ai/proposals/`. After: (1) Generates `proposal_id = f"{YYYYMMDD}-{slug}-{seq:03d}"`. (2) Writes markdown proposal document to `_sys/ai/proposals/{proposal_id}.md` with metadata header (Author, Date, Impact, Subject, Rationale, Changes, and initial `Votes:` block listing `- {peer}: PENDING` for each eligible healthy voter). (3) Appends entry to `PENDING_ISSUES` in `handoff.md`. External effects: Registers pending governance proposal and requests peer votes.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Sequential collision-free proposal ID generation. Health-aware voter list pre-filtering. Handoff log synchronization.
* **Redaction/Ordering:** Stdout confirms proposal ID, author peer, impact level, and voting CLI instructions.
* **Comparator:** NORMALIZED (proposal ID, date timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Subject validation and voter health filtering.
  * **Cwd/Env/Stdin:** Resolves `_proposals_dir()`. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Creates structured governance proposal file and logs pending issue.
* **Fixtures:** Positive: `fix-proposal-add-pos-01` (Not yet implemented), Invalid: `fix-proposal-add-inv-01` (Not yet implemented), Auth: `fix-proposal-add-auth-01` (Not yet implemented), Recovery: `fix-proposal-add-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 10. proposal-vote
* **Input Schema:** `ai_root: Path`, `proposal_id: str` (via `--proposal-id`, required), `voter: str = "cc"` (via `--voter`, `--peer`, `--agent`), `vote: str` (via `--vote`, required, e.g. `agree`, `disagree`, `abstain`, `need_more_info`), `reason: str = ""` (via `--reason`). Validation: Requires non-empty `proposal_id` and `vote` (exits 1 if missing: `[HUB] proposal-vote requires --proposal-id and --vote`). Locates proposal file in `_sys/ai/proposals/` matching `{proposal_id}*.md` (exits 1 if not found: `[HUB:ERROR] proposal '{proposal_id}' not found in {p_dir}`). Normalizes vote value (`AGREE`, `DISAGREE`, `ABSTAIN`, `NEED_MORE_INFO`). Evaluates consensus gates: total voters < 2 (ESCALATED), mid-round gate closure (ESCALATED), any DISAGREE (NACK), unanimous AGREE with non-proposer >= 1 (CONSENSUS_OK), proposer self-finalization alone (ESCALATED). Authorization: Architecture governance / consensus voting interface.
* **Normalized Envelope:** Success (vote recorded): Exit 0, prints `[HUB] PROPOSAL-VOTE {proposal_id} | {voter}:{vote_upper}` to stdout, followed by consensus outcome line: `[HUB] PROPOSAL CONSENSUS_OK {proposal_id} | unanimous agree: {agreed}`, `[HUB] PROPOSAL NACK {proposal_id} | disagreed: {disagreed}`, `[HUB] PROPOSAL ESCALATED {proposal_id} | N < 2 (human_gate)`, `[HUB] PROPOSAL ESCALATED {proposal_id} | mid-round gate closure (human_gate)`, or `[HUB] PROPOSAL ESCALATED {proposal_id} | proposer self-finalization blocked (human_gate)`. Error (missing args): Exit 1, prints `[HUB] proposal-vote requires --proposal-id and --vote` to stderr. Error (proposal not found): Exit 1, prints `[HUB:ERROR] proposal '{proposal_id}' not found in {p_dir}` to stderr.
* **State Changes:** Before: Proposal markdown file has pending vote status for voter. After: (1) Updates proposal markdown file replacing `- {voter}: PENDING` with `- {voter}: {VOTE}` and appending optional `Reason ({voter}): {reason}`. (2) Logs consensus outcome transition to `CONSENSUS_HISTORY` in `handoff.md`. (3) On `CONSENSUS_OK`: Triggers D-1 invariant writer: if `Target Doc:` exists (default `10-invariants.md`), extracts changes, computes next `INV-NN` ID, appends invariant row to the doc table, and writes atomically via `tempfile` + `os.replace`. External effects: Records vote, resolves proposal lifecycle, and automatically writes ratified invariants to architectural docs.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Safe in-place regex updates. Prevents proposer self-finalization. Atomically mutates target invariant document on unanimous agreement.
* **Redaction/Ordering:** Stdout outputs vote confirmation followed by consensus resolution line.
* **Comparator:** EXACT (vote value, voter ID) / NORMALIZED (voter lists, timestamps).
* **Specific Argv Comparators:**
  * **Safety:** Proposal file resolution, voter validation, and atomic invariant writer.
  * **Cwd/Env/Stdin:** Resolves `_proposals_dir()` and `docs-v2`. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Records peer vote on proposal, evaluates consensus conditions, and commits ratified invariants.
* **Fixtures:** Positive: `fix-proposal-vote-pos-01` (Not yet implemented), Invalid: `fix-proposal-vote-inv-01` (Not yet implemented), Auth: `fix-proposal-vote-auth-01` (Not yet implemented), Recovery: `fix-proposal-vote-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 11. proposal-list
* **Input Schema:** `ai_root: Path`. Validation: None. Reads `_proposals_dir()` (`_sys/ai/proposals/`). If empty, prints `No proposals found.` and returns. Authorization: Read-only governance inspection.
* **Normalized Envelope:** Success (proposals found): Exit 0, prints header `Proposal                                      Status          Votes` and divider `--------------------------------------------------------------------------------` followed by formatted fixed-width lines `{stem:<45} {status:<15} agree={agreed}/{total}` to stdout. Status is `CONSENSUS_OK`, `PENDING`, or `PARTIAL`. Success (no proposals): Exit 0, prints `No proposals found.` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation against `_sys/ai/proposals/`. No file mutations or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. Lock-free proposal listing.
* **Redaction/Ordering:** Fixed-width formatted table to stdout.
* **Comparator:** NORMALIZED (proposal stems, agree ratios).
* **Specific Argv Comparators:**
  * **Safety:** Read-only inspection.
  * **Cwd/Env/Stdin:** Resolves `_proposals_dir()`. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Formats governance proposal inventory and current voting status.
* **Fixtures:** Positive: `fix-proposal-list-pos-01` (Not yet implemented), Invalid: `fix-proposal-list-inv-01` (Not yet implemented), Auth: `fix-proposal-list-auth-01` (Not yet implemented), Recovery: `fix-proposal-list-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 12. broker-submit
* **Input Schema:** `ai_root: Path`, `target: str` (via `--file` or `--name`, required), `payload_text: str` (via `--text` or `--payload-file`, required), `origin: str = "unknown"` (via `--from`, `--peer`, `--agent`). Validation: Requires non-empty `target` and non-empty `payload_text` (exits 1 if missing: `[HUB] broker-submit requires --file and --text or --payload-file`). Validates `payload_text` is valid JSON (exits 1 if invalid: `[HUB:ERROR] broker-submit: invalid JSON payload: {exc}`). Validates `target` is in broker whitelist via `_validate_broker_payload(ai_root, target, payload)` (whitelisted targets: `state.json`, `task_registry.json`, `mailbox.json`, `leases.json`, `nodes.json`, `sessions/{room}/handoff.json`, `consensus/{round}.json`). Computes `expected_revision` (SHA-256 hash if target exists, or `"ABSENT"` if target does not exist). Authorization: Governed file mutation broker submission.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] BROKER-SUBMIT {request_id} | target={rel} | pending={path.name}` to stdout. Error (missing args): Exit 1, prints `[HUB] broker-submit requires --file and --text or --payload-file` to stderr. Error (invalid JSON / schema error / unwhitelisted target): Exit 1, prints `[HUB:ERROR] broker-submit: ...` to stderr.
* **State Changes:** Before: No pending request for this submission. After: Writes structured broker request JSON `{"schema_version": 1, "request_id": f"br-{stamp}-{uuid}", "created_at": _now(), "origin": origin, "operation": "json_replace", "target": rel, "payload": payload, "expected_revision": expected_revision}` to `.ai/broker/pending/{stamp}-{request_id}.json` using crash-safe temp file + atomic rename (`os.replace`). External effects: Queues validated mutation request in broker pending directory.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Append-only queue submission. CAS revision capture prevents stale overwrites at drain time. Crash-safe write via `.tmp` file and atomic rename. Unique request ID generated per call.
* **Redaction/Ordering:** Stdout confirms request ID, relative target path, and pending filename.
* **Comparator:** NORMALIZED (request ID, timestamp, pending filename).
* **Specific Argv Comparators:**
  * **Safety:** Whitelist target verification, schema payload validation, and CAS revision capture.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. Reads payload from file if `--payload-file` provided.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Validates and atomically enqueues governed file mutation request into pending broker queue.
* **Fixtures:** Positive: `fix-broker-submit-pos-01` (Not yet implemented), Invalid: `fix-broker-submit-inv-01` (Not yet implemented), Auth: `fix-broker-submit-auth-01` (Not yet implemented), Recovery: `fix-broker-submit-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 13. broker-drain
* **Input Schema:** `ai_root: Path`, `limit: int = 50` (via `--limit`), `origin: str = "broker"`, `force_tier0: bool = False` (via `--force-tier0`). Validation: Ensures broker directories (`pending`, `done`, `error`). Serializes processing under lock `broker_drain`. Iterates sorted pending files up to `limit`. Validates request schema and CAS `expected_revision` against current target file revision. Authorization: Governed file mutation execution coordinator.
* **Normalized Envelope:** Success (per committed request): Exit 0, prints `[HUB] BROKER-COMMIT {request_id} | done={archived.name}` to stdout. Error (per failed request): Prints `[HUB] BROKER-ERROR {req_path.name} | error={exc} | moved={archived.name}` to stdout/stderr and moves request to `error/` directory. Summary: Prints `[HUB] BROKER-DRAIN processed={processed} committed={committed} failed={failed}` to stdout. Final exit: Exit 0 upon completing loop.
* **State Changes:** Before: Pending requests in `.ai/broker/pending/`. After: Under lock `broker_drain`: (1) For each valid request: applies mutation to target governed file (`_commit_hub_mutation_request`), records journal entry in `_sys/ai/journals/`, and archives request file to `.ai/broker/done/`. (2) For each failed request: records journal error entry and archives request file to `.ai/broker/error/`. (3) Runs post-finalize arbiter check `_maybe_run_arbiter_on_finalize` if consensus rounds finalized. External effects: Applies governed state mutations and archives processed queue items.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Serialized concurrency under lock `broker_drain`. Deterministic sorted filename ordering. Rejects stale revisions (CAS failure). Moves malformed or failed requests to `error/` directory preserving queue liveness.
* **Redaction/Ordering:** Stdout outputs per-item commit/error lines followed by drain summary counts.
* **Comparator:** NORMALIZED (request IDs, counts, archived filenames).
* **Specific Argv Comparators:**
  * **Safety:** Lock `broker_drain`, CAS expected revision verification, and isolated error quarantine.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Sequentially processes and applies pending broker mutation requests with journal logging.
* **Fixtures:** Positive: `fix-broker-drain-pos-01` (Not yet implemented), Invalid: `fix-broker-drain-inv-01` (Not yet implemented), Auth: `fix-broker-drain-auth-01` (Not yet implemented), Recovery: `fix-broker-drain-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 14. broker-status
* **Input Schema:** `ai_root: Path`. Validation: None. Reads directory contents of `.ai/broker/{pending,done,error}`. Authorization: Read-only broker queue inspection.
* **Normalized Envelope:** Success: Exit 0, prints `broker pending={len(pending)} done={len(done)} error={len(error)}` followed by `pending\t{filename}` for the first 20 pending items to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before / After: Pure read-only operation against broker directories. No file modifications or external effects.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. Lock-free queue inspection. Safe if broker directories do not exist.
* **Redaction/Ordering:** Stdout outputs queue summary count line followed by tab-delimited pending file names.
* **Comparator:** NORMALIZED (counts, filenames).
* **Specific Argv Comparators:**
  * **Safety:** Read-only directory scan.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Reports counts of pending, done, and error broker requests.
* **Fixtures:** Positive: `fix-broker-status-pos-01` (Not yet implemented), Invalid: `fix-broker-status-inv-01` (Not yet implemented), Auth: `fix-broker-status-auth-01` (Not yet implemented), Recovery: `fix-broker-status-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 15. update-signatures
* **Input Schema:** None. Validation: Extracts public API signatures of `hub.py` via `_extract_hub_signatures()` (parses all `action_*` functions, `_lease_cfg`, `_build_session_cmd`, etc.). Authorization: Developer tool / contract signature synchronization.
* **Normalized Envelope:** Success: Exit 0, prints `[HUB] update-signatures: wrote {count} signatures → {snapshot_path}` followed by `  Next: update test_contracts.py to match any changed signatures.` to stdout. Error: Exit 1 on unhandled exception.
* **State Changes:** Before: Existing `_sys/ai/snapshots/hub_api.json`. After: Writes updated signature snapshot JSON to `_sys/ai/snapshots/hub_api.json` containing `generated_at`, `source: "hub.py"`, `count`, and `signatures` mapping function names to parameter lists and annotations. External effects: Updates API signature SSOT used for drift detection in unit tests (`test_signatures.py`).
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent generation. Atomic directory creation and file overwrite. Enforces DIR-003 contract synchronization.
* **Redaction/Ordering:** Stdout outputs written signature count and path.
* **Comparator:** EXACT (signature count and format) / NORMALIZED (generated timestamp).
* **Specific Argv Comparators:**
  * **Safety:** Introspective static signature extraction.
  * **Cwd/Env/Stdin:** Resolves `_sys/ai/snapshots/`. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution.
  * **Observed Semantics:** Regenerates `hub_api.json` snapshot of public `hub.py` function signatures.
* **Fixtures:** Positive: `fix-update-signatures-pos-01` (Not yet implemented), Invalid: `fix-update-signatures-inv-01` (Not yet implemented), Auth: `fix-update-signatures-auth-01` (Not yet implemented), Recovery: `fix-update-signatures-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 16. arbiter-review
* **Input Schema:** `ai_root: Path`, `round_id: str` (via `--round-id`, required). Validation: Requires non-empty `--round-id` and existing round file `.ai/consensus/{round_id}.json` (exits 1 if missing: `[HUB:ERROR] arbiter-review: consensus round not found: {round_id}`). Evaluates arbiter configuration via `_final_arbiter_config()`: if disabled, returns `{"fired": False, "reason": "arbiter_disabled"}`. Detects dissent via `detect_dissent()`. Evaluates budget and selection via `arbiter_decide()`. Authorization: DIR-005 Smartest-Model Final Arbiter review trigger.
* **Normalized Envelope:** Success (review executed or skipped): Exit 0, prints formatted 2-space indented JSON result object to stdout (e.g. `{"fired": true, "final_opinion": {...}}` or `{"fired": false, "reason": "..."}`). Error (missing round ID or file not found): Exit 1, prints `[HUB:ERROR] arbiter-review: consensus round not found: {round_id}` to stderr.
* **State Changes:** Before: Consensus round in dissent or high-risk status. After: If trigger fires: (1) Invokes arbiter model (e.g. `cc.fable`) via isolated subprocess `hub.py ask --to <arbiter> --query-file <qf>` with condensed dissent input. (2) Appends `FINAL_OPINION` record to `.ai/final_opinions.jsonl`. (3) Records invocation in rolling 5-hour budget window. External effects: Persists arbiter verdict and consumes one arbiter budget unit. (Model-generated verdict text is kept outside equality comparison).
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Gated by rolling window budget guard (max 5 calls / 5h). Failure during invocation does not consume budget. Subprocess invocation avoids `action_ask` re-entrancy.
* **Redaction/Ordering:** Stdout outputs JSON decision and opinion record.
* **Comparator:** NORMALIZED (JSON key ordering, timestamps, token counts; model-generated text excluded from equality).
* **Specific Argv Comparators:**
  * **Safety:** DIR-005 budget limiter, dissent detection gate, and isolated subprocess execution.
  * **Cwd/Env/Stdin:** Independent of CWD if `ai_root` is resolved. No stdin consumed.
  * **Transport/Process-Tree:** Executes isolated child `hub.py ask` subprocess.
  * **Observed Semantics:** Triggers smart-model arbiter review for dissenting or high-risk consensus rounds.
* **Fixtures:** Positive: `fix-arbiter-review-pos-01` (Not yet implemented), Invalid: `fix-arbiter-review-inv-01` (Not yet implemented), Auth: `fix-arbiter-review-auth-01` (Not yet implemented), Recovery: `fix-arbiter-review-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 17. credit-status
* **Input Schema:** `peer: str` (via `--peer`, required), `as_json: bool = False` (via `--json`). Validation: Validates that peer supports reset credits via `supports_reset_credits(peer)` (exits 3 if unsupported: `[HUB:ERROR] reset credits are not declared for peer {peer!r}`). Queries rate limits via `CodexAccountClient().read_rate_limits()`. Authorization: Read-only inspection of rate-limit reset credit quota.
* **Normalized Envelope:** Success (human-readable): Exit 0, prints `availableCount={count} credits={credits}` or `rateLimitResetCredits: absent` or `rateLimitResetCredits: null` to stdout. Success (JSON mode): Exit 0, prints JSON object string of rate limit response to stdout. Error (missing peer): Exit 3, prints `[HUB] credit-status requires --peer` to stderr. Error (unsupported peer): Exit 3, prints `[HUB:ERROR] reset credits are not declared for peer {peer!r}` to stderr.
* **State Changes:** Before / After: Pure read-only query to app-server. Never consumes credits or modifies local/remote state.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Strictly idempotent read. Context-manager connection lifecycle (`CodexAccountClient`).
* **Redaction/Ordering:** Stdout outputs human-readable summary line or raw JSON.
* **Comparator:** NORMALIZED (credit counts, JSON format).
* **Specific Argv Comparators:**
  * **Safety:** Declared capability guard (`supports_reset_credits`) and read-only enforcement.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution via app-server IPC client.
  * **Observed Semantics:** Reads available rate-limit reset credit balance for a supported peer.
* **Fixtures:** Positive: `fix-credit-status-pos-01` (Not yet implemented), Invalid: `fix-credit-status-inv-01` (Not yet implemented), Auth: `fix-credit-status-auth-01` (Not yet implemented), Recovery: `fix-credit-status-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

## 18. credit-consume
* **Input Schema:** `peer: str` (via `--peer`, required), `credit_id: str` (via `--credit-id`, required), `confirm: bool` (via `--confirm`, required flag), `idempotency_key: str | None = None` (via `--idempotency-key`), `as_json: bool = False` (via `--json`), `origin: str = "terminal"`. Validation & Auth: (1) Requires `origin == "terminal"` (exits 3 if non-terminal). (2) Requires `supports_reset_credits(peer)` (exits 3 if unsupported). (3) Requires non-empty `credit_id` and `confirm is True` (exits 3). (4) Validates `idempotency_key` format as canonical UUID if provided (exits 3 if invalid). (5) Performs preflight read `client.read_rate_limits()` and validates `credit_id` is an available credit in preflight payload (exits 3 if invalid). Authorization: Strict human-origin confirmation gate (`terminal` + `--confirm`) for irreversible credit consumption.
* **Normalized Envelope:** Success (verified reset): Exit 0, prints `credit-consume: reset (verified)` or JSON `{"outcome": "reset", "credit_id": "...", "idempotency_key": "...", "verified": true}` to stdout. Backend No-Op: Exit 2, prints `credit-consume: {outcome} (no-op, no reset performed)` or JSON `{"outcome": outcome, "credit_id": "...", "idempotency_key": "..."}` to stdout (when outcome is `nothingToReset` or `noCredit`). Error (transport failure / ambiguous / post-verification failure): Exit 1, prints `[HUB:ERROR] ...` to stderr or JSON error. Error (preflight failure / auth / missing confirm / invalid uuid): Exit 3, prints `[HUB:ERROR] ...` to stderr.
* **State Changes:** Before: Reset credit available on account; rate limit exhausted. After: (1) Appends audit log line `intent` via `_audit_credit_step("intent", ...)`. (2) Calls `client.consume_reset_credit(credit_id, idempotency_key)`. (3) Performs post-verification `client.read_rate_limits()` and `_verify_reset_credit()`. External effects: Irreversibly consumes account rate-limit reset credit and resets quota on upstream service.
* **Behaviors (Idempotency/Correlation/Lease/Crash):** Correlated via canonical UUID `idempotency_key`. Multi-stage preflight -> intent audit -> consume -> post-verification workflow. Exit-code contract: Exit 0 (verified success), Exit 1 (transport/verification failure), Exit 2 (backend no-op), Exit 3 (preflight/auth rejection).
* **Redaction/Ordering:** Stdout outputs verified outcome or JSON payload. Stderr captures error details.
* **Comparator:** EXACT (outcome status, boolean verified) / NORMALIZED (UUID, credit ID).
* **Specific Argv Comparators:**
  * **Safety:** Strict terminal-origin guard, `--confirm` requirement, UUID validation, and 3-step preflight/post-verify cycle.
  * **Cwd/Env/Stdin:** Independent of CWD. No stdin consumed.
  * **Transport/Process-Tree:** Direct inline Python execution via app-server IPC client.
  * **Observed Semantics:** Irreversibly redeems rate-limit reset credit with complete preflight validation and post-verification.
* **Fixtures:** Positive: `fix-credit-consume-pos-01` (Not yet implemented), Invalid: `fix-credit-consume-inv-01` (Not yet implemented), Auth: `fix-credit-consume-auth-01` (Not yet implemented), Recovery: `fix-credit-consume-rec-01` (Not yet implemented).
* **Legacy Digest:** `[DIGEST_TBD]` | **Proof Ref:** `[PROOF_REF_TBD]`

---

## Combined 90-Action Parity Ledger Coverage Summary

With the completion of Batch 5, all ninety (90) actions from the original PeerHub Phase 1 surface manifest are fully codified across five parity ledger documents without gaps or double-counting:

* **Batch 1 (18 actions):** `init-session`, `end-session`, `send`, `broadcast`, `mark-read`, `append-log`, `archive-file`, `update-status`, `check`, `status`, `check-gate`, `ask`, `ask-all`, `ask-coordinator`, `consensus-propose`, `consensus-vote`, `consensus-check`, `consensus-sweep`.
* **Batch 2 (18 actions):** `register-node`, `list-nodes`, `health-update`, `health-check`, `peer-status`, `context-fill`, `checkpoint`, `peer-quarantine`, `peer-recover`, `new-topic`, `clear-room`, `preflight`, `context-hash`, `report-error`, `feedback-add`, `feedback-list`, `feedback-resolve`, `artifact-claim`.
* **Batch 3 (18 actions):** `artifact-status`, `artifact-finalize`, `leader-yield`, `leader-claim`, `elect-leader`, `discover`, `assign-role`, `release-role`, `role-status`, `health-precheck`, `health-sweep`, `freshness-sweep`, `terminal-handoff`, `terminal-duty-sweep`, `terminal-heartbeat`, `terminal-close`, `append-handoff`, `task-checkpoint`.
* **Batch 4 (18 actions):** `task-status`, `task-failover`, `approval-request`, `file-lock`, `file-unlock`, `lock-status`, `profile-validate`, `lease-status`, `lease-sweep`, `model-status`, `transient-scan`, `directive-add`, `directive-list`, `directive-clear`, `lessons-list`, `lessons-propose`, `lessons-activate`, `lessons-retire`.
* **Batch 5 (18 actions):** `lesson-broadcast`, `lesson-sweep`, `lesson-inject`, `thread-new`, `thread-append`, `thread-react`, `thread-promote`, `alert-raise`, `proposal-add`, `proposal-vote`, `proposal-list`, `broker-submit`, `broker-drain`, `broker-status`, `update-signatures`, `arbiter-review`, `credit-status`, `credit-consume`.

**Total Unique Actions Documented:** Exactly 90 actions (18 × 5 = 90). Unanimously addresses finding R2-05 from cx's Round 2 critique.
