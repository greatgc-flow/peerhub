# Code Quality Audit: peerhub (Whole-Package)

### 1. [HIGH] Dead / Unreferenced Business Logic

**CORRECTION (cc, 2026-09-19, after individual investigation of all 5 occurrences):** the original framing above -- "appear to have been orphaned during refactoring," implying these are safe deletion candidates -- is **not accurate for any of the 5**. Each was independently investigated (git-adjacent context, sibling call sites, docs/STATUS.md) and turns out to be a deliberately-built capability that was never wired to its integration point, not accidental refactoring debris. None were deleted. Findings per item:

- `recover_interrupted_attempt` / `translate_outbox_to_journal` (`peerhub/dispatch/service.py:83,137`): a real, designed crash-recovery mechanism (translates outbox events into an abstract journal vocabulary, then classifies a post-`INTENT_PERSISTED`-crash shape). `START_UNCERTAIN`/interrupted-attempt concepts ARE live elsewhere in the real dispatch pipeline (`attempt_lifecycle.py`, `model.py`, `retry.py`), so this pair may be either (a) genuinely superseded by that machinery, or (b) still-needed and simply never connected -- distinguishing the two requires tracing the full attempt-lifecycle state machine, which is real design work, not a quick grep. **Recommendation: needs a design decision, not autonomous deletion or autonomous wiring** -- crash-recovery correctness is the wrong place for a guess.
- `compute_directive_digest` (`peerhub/governance/directive_digest.py:3`): `DirectiveService.propose()` stores `rule_markdown` directly today with **no digest computed at all** -- this function was clearly built to backfill that gap (content-addressed integrity/dedup for directive text) but was never called from `propose()`. **Recommendation: a real, scoped feature addition** (wire `compute_directive_digest(rule_markdown)` into `DirectiveService.propose()`, decide what the digest is used for -- dedup? integrity check on read?), not something to delete.
- `read_reset_credits` (`peerhub/telemetry/codex_credit.py:238`): **already explicitly documented as intentional** in `docs/STATUS.md`'s 2026-09-09 entry: "Codex reset-credit read/consume primitives were added ... CLI exposure deliberately deferred." This is not a finding at all -- the audit missed existing project documentation that already explains it. **Recommendation: no action; leave exactly as-is.**
- `format_duty_row` (`peerhub/telemetry/domain_rows.py:59`): its 3 siblings (`format_consensus_row`, `format_task_row`, `format_task_row_narrow`) are actively called from `peerhub/cli/commands/daily.py`'s domain-snapshot builder, which lists consensus/task/lesson rows -- but that same function's `duty_leases` field is hardcoded to `{"status": "unavailable", "reason": "cross-room duty lease enumeration is not implemented"}`, **twice**, matching a genuinely missing capability, not a missed formatter call. Confirmed why: `DutyLeaseUnitOfWork` (`peerhub/dispatch/duty_lease.py`) only supports per-`(room_id, role)` lookups and `list_expired_duty_leases` -- there is no "list all active leases across all rooms" method at the persistence-protocol level at all. Wiring `format_duty_row` in requires adding that query method to both the Protocol and its concrete SQLite implementation first -- real persistence-layer feature work, not a cleanup task. **Recommendation: scope as its own small feature (new `list_active_duty_leases()` persistence method + wiring), not a quality-audit line item.**

**Original description (superseded by the correction above):** Several public domain functions are defined (and often heavily tested in `tests/`), but are completely unreferenced by any actual application code in `peerhub/`. They appear to have been orphaned during refactoring.
**Occurrences**:
- `recover_interrupted_attempt` (`peerhub/dispatch/service.py:83`)
- `translate_outbox_to_journal` (`peerhub/dispatch/service.py:137`)
- `compute_directive_digest` (`peerhub/governance/directive_digest.py:3`)
- `read_reset_credits` (`peerhub/telemetry/codex_credit.py:238`)
- `format_duty_row` (`peerhub/telemetry/domain_rows.py:59`)
**Commands used to verify**: 
`rg -w "recover_interrupted_attempt" -g "!tests/**" peerhub/` (returns only the definition signature, 0 callers)

### 2. [HIGH] Test Coverage Gaps (Critical Logic)
**Description**: Highly-business-critical functions governing core behaviors lack any tests in the `tests/` directory.
**Occurrences**:
- `assemble_ask_prompt` (`peerhub/application/direct_ask.py:212`): 205-line monolithic prompt assembler aggregating directives, context, and lessons with no tests.
- `validate_retry_authorizable` (`peerhub/dispatch/model.py:791`): The primary gatekeeper logic for whether a failed attempt can be safely retried has zero unit tests.
- `collect_room_status` (`peerhub/application/status.py:10`): Extracts room metadata for the CLI status display, completely untested.
**Commands used to verify**:
`rg -w "assemble_ask_prompt" tests/` (returns 0 matches)

**RESOLVED (cc, 2026-09-19):**
- `validate_retry_authorizable`: 8 new focused unit tests added (`tests/unit/dispatch/test_validate_retry_authorizable.py`) covering the happy path, command/lease-id mismatch rejection, non-retryable request/attempt states, the "no replay-safety evidence" rejection, and both ways to satisfy replay safety (`reconciliation_complete`, `completion_contract.replay_safe`).
- `collect_room_status`: 3 new unit tests added (`tests/unit/application/test_status.py`). Found 2 real behavioral details while writing them that the audit (and this doc) hadn't noted: (1) it has no not-found guard of its own -- calling it on a room that doesn't exist raises `RecordNotFoundError` via the underlying `RoomsService.count_unread_messages()`, it doesn't return an empty/None result; (2) a freshly-created room's `room_summary` is `None` until `update_room_summary()` is called at least once -- summary reflects an explicit update, not mere room existence.
- `assemble_ask_prompt`: **not a true 0-coverage gap** -- `tests/integration/application/test_direct_ask.py` (~line 368-403) already asserts on the fully-assembled prompt's exact content (directives, lessons, hub context, user query sections all present with real text), exercising this function's main content-assembly path end-to-end even though nothing calls it directly in isolation. Left without an additional narrow unit test given the existing integration coverage's genuine strength; a future pass could still add unit tests for specific edge cases (missing room/task context, empty directive/lesson lists) if that granularity becomes valuable.

### 3. [MEDIUM] Genuinely Duplicated Logic (`_verify_dctx_credential`)
**Description**: The internal function `_verify_dctx_credential` (which opens a SQLite connection, queries the `workspace_identity` table, validates singleton status, and delegates to `verify_credential_for_actor`) is exactly copy-pasted as a nested closure inside two different integration boundaries.
**Occurrences**:
- `peerhub/runtime.py:187` (inside `_compose_runtime`)
- `peerhub/cli/__init__.py:491` (inside `_run_consensus`)

### 4. [HIGH] Correctness Smells: Unsafe Exception Swallowing
**Description**: Core domain and parsing logic contains bare `except Exception: pass` blocks that silently swallow `KeyError`, `TypeError`, or unexpected logic bugs instead of catching specific expected exceptions (like `FileNotFoundError`). 
**Occurrences**:
- `peerhub/telemetry/presenter.py:112`: Silently swallows *all* exceptions during model token usage extraction, and silently returns a hardcoded fallback tuple `(15000, 258400, 5.8)` to the user, masking telemetry parsing failures.
- `peerhub/application/direct_ask.py:280`: Swallows exceptions during lesson block construction, silently excluding lessons from the payload if an internal formatting bug occurs.
- `peerhub/application/direct_ask.py:332`: Swallows errors during room context handoff parsing.
- `peerhub/application/direct_ask.py:367`: Swallows errors during continuity block extraction.
- `peerhub/core/binary_resolution.py:31`, `peerhub/core/binary_resolution.py:42`: Swallows `Path.resolve()` file-system resolution exceptions rather than catching specific `OSError` or `PermissionError`.

### 5. [MEDIUM] Overly Complex God Function (`dispatch_and_execute`)
**Description**: The `dispatch_and_execute` method in `peerhub/application/workflows.py:609` is 462 lines long and accepts 16 distinct keyword arguments.
**Concrete Problem**: This single function tightly couples artifact parsing, retry loop state mutation, transport I/O, OS process supervision (`cancellation_hook`), and SQL transaction boundaries. The concrete consequence is that it is impossible to unit-test edge cases of one phase (e.g. artifact parsing limits or cancellation signals) without mocking 15 distinct dependencies just to satisfy the method signature. It should be decomposed into pipeline stages.
