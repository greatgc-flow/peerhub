# Code Quality Audit: peerhub (Whole-Package)

### 1. [HIGH] Dead / Unreferenced Business Logic
**Description**: Several public domain functions are defined (and often heavily tested in `tests/`), but are completely unreferenced by any actual application code in `peerhub/`. They appear to have been orphaned during refactoring.
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
