# PeerHub Process & Memory Leak Safety Audit
Date: 2026-08-31

## 1. Process-Spawning Safety

### What's Protected
*   **Core Dispatch (`peerhub/dispatch/pipe.py`)**: 
    Every process spawned through the core `run_process` pipeline is explicitly bound to a Windows Job Object. 
    *   **Evidence**: `peerhub/dispatch/pipe.py:374-386` dynamically loads `RealTreeController` and calls `tree_controller.bind_spawn()`. This ensures that even if the Python host crashes, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` handles orphan termination OS-level.
    *   **Cancellation Ladder**: `peerhub/dispatch/process.py` implements a robust 5-step cancellation ladder (`SOFT_CANCEL` -> `TERMINATE_TREE` -> `KILL_TREE` -> `RECONCILE_TREE`) to aggressively prune subprocess trees on timeout or manual cancellation.

### Real Gaps & Acceptable Risks
*   **Telemetry Spawning (`peerhub/telemetry/quota_polling.py`)**: 
    The `poll_claude_usage` and `poll_codex_usage` methods use raw `subprocess.Popen` directly, bypassing `ProcessSupervisor` and Job Objects. 
    *   **Mitigation**: This is an acceptable risk because `quota_polling.py` implements a rigorous manual fallback: it runs `taskkill /F /T` and explicitly sweeps for `node.exe`/`claude.exe`/`codex.exe` orphans using `psutil.process_iter` on timeout.
*   **Fast Statusline (`peerhub/telemetry/statusline.py`)**:
    Uses `subprocess.run(["git", ...], timeout=1)` which is generally safe for `git rev-parse`.

## 2. Memory & Resource Leak Patterns

### What's Protected
*   **Unbounded Caches (Leadership History)**: 
    The `LeadershipService` correctly bounds its in-memory and persisted `coordinator_history`.
    *   **Evidence**: `peerhub/application/leadership.py:504-507` slices the history array with `[-self._policy.history_limit:]` (default 10) before committing.
*   **Subprocess Output Buffering**: 
    `ProcessSupervisor` stores all process output chunks in memory (`self._chunks.append(...)` at `peerhub/dispatch/process.py:595`). 
    *   **Mitigation**: This does NOT grow unboundedly because `peerhub/dispatch/pipe.py:464-465` enforces a hard memory limit. If `supervisor.total_output_bytes > config.max_output_bytes`, it triggers an `OUTPUT_LIMIT_EXCEEDED` cancellation.
*   **Observer Registrations**: 
    No unmanaged observer accumulation or `EventBus` memory leak patterns exist. Communication is primarily request-driven or polled, with state managed via SQLite rather than long-lived in-memory callbacks.

### Real Gaps (CRITICAL)
*   **SQLite Connection Leak in Context Managers**: 
    There is a strict memory/resource leak vulnerability in `peerhub/persistence/sqlite.py` regarding connection lifecycles inside `SqliteUnitOfWork` and `SqliteReadUnitOfWork`.
    *   **Evidence**: In `SqliteUnitOfWork.__enter__` (`sqlite.py:783-784`), the code opens a connection `self._connection = self._store._connect()` and then calls `self._connection.execute("BEGIN IMMEDIATE")`. 
    *   **The Gap**: If `execute("BEGIN IMMEDIATE")` raises an exception (e.g., `sqlite3.OperationalError` due to database lock/busy), Python context manager semantics dictate that `__exit__` will **never be called**. The open connection is leaked and never explicitly closed until garbage collection, which can lead to file descriptor exhaustion or prolonged lock contention. The exact same vulnerability exists in `SqliteReadUnitOfWork.__enter__` (`sqlite.py:472-473`) with `execute("BEGIN")`.

## 3. Summary & Follow-Up
*   **Protected**: OS-level job objects for core process dispatch; rigorous fallback reapers for telemetry process dispatch; memory limits on stdout/stderr streaming; bounded slice history limits.
*   **Fixed (2026-08-31, same day, by the terminal):** both `SqliteUnitOfWork.__enter__` and `SqliteReadUnitOfWork.__enter__` now wrap the `BEGIN`/`BEGIN IMMEDIATE` call in `try/except BaseException: connection.close(); raise`, and only assign `self._connection` after the transaction successfully begins -- so a failed enter never leaves a broken connection reachable from the instance, and the raw connection object is always closed before the exception propagates. Verified with 2 new regression tests (`test_write_unit_of_work_closes_connection_when_begin_fails`, `test_read_unit_of_work_closes_connection_when_begin_fails` in `tests/integration/persistence/test_sqlite_kernel.py`) that force a real `BEGIN` failure (by closing the connection immediately after creation, since `sqlite3.Connection` is an immutable C type its methods can't be monkeypatched) and assert `self._connection is None` afterward -- the pre-fix code would have left it non-None, pointing at a leaked, already-broken connection.
