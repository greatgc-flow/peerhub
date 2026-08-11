"""Subprocess.Popen-based pipe runner for non-interactive dispatch.

This is the default, non-interactive runner backend per the ratified
Slice 5 design (SLICE5-KICKOFF-R1.md "Process runner backend + lease
heartbeat RATIFIED").  It spawns a process via ``subprocess.Popen``
with ``PIPE`` stdout/stderr, reads output, and calls
``ProcessSupervisor``'s existing callback methods with real data --
the concrete runner driving the pure reducer.

The PTY runner (``pty.py``) is deliberately not implemented, and that
decision is CLOSED, not pending.  The empirical PTY probe ran on
2026-08-03 against all three peer CLIs and found none of them requires
Windows ConPTY -- plain pipes suffice, with the one mitigation this
module already applies: ``stdin`` defaults to ``subprocess.DEVNULL``
when no payload is supplied, because ``ag`` and ``cx`` hang indefinitely
waiting for stdin EOF under an open, empty ``subprocess.PIPE``.  See
``SLICE5-KICKOFF-R1.md`` ("PTY probe run, empirical resolution: no peer
needs ConPTY") and ``docs/design/phase0/PTY-BUFFERING-PROBE-2026-08-03.md``.
``pty.py`` and ``pywinpty`` are therefore NOT dependencies;
``InvocationPlan.transport`` keeps ``PTY`` only for forward
compatibility with a future peer type that genuinely needs a TTY.
"""

from __future__ import annotations
from dataclasses import dataclass

import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Callable, IO, Protocol

from peerhub.dispatch.contract import ProcessBirthIdentity
from peerhub.dispatch.process import (
    CancellationAction,
    CancellationStage,
    ProcessSupervisor,
    ProcessSupervisionOutcome,
    TreeProcessObservation,
)


class TreeHandle(Protocol):
    """Opaque handle representing an observed or managed process tree."""

    @property
    def root_identity(self) -> ProcessBirthIdentity: ...


@dataclass(frozen=True)  # pyright: ignore[reportUntypedClassDecorator]
class TreeDispatchReceipt:
    """Receipt returned after dispatching a tree cancellation signal."""

    dispatched: bool
    signal_name: str
    target_identities: tuple[ProcessBirthIdentity, ...] = ()


class TreeController(Protocol):
    """Protocol for runner-level platform process signaling and observation.

    Implementations (step 2) MUST verify ProcessBirthIdentity atomically with
    each signal method to eliminate TOCTOU race conditions.
    """

    def bind_spawn(
        self,
        *,
        process: subprocess.Popen[bytes],
        root: ProcessBirthIdentity,
    ) -> TreeHandle: ...

    def soft_cancel(self, tree: TreeHandle) -> TreeDispatchReceipt: ...

    def terminate_tree(self, tree: TreeHandle) -> TreeDispatchReceipt: ...

    def kill_tree(self, tree: TreeHandle) -> TreeDispatchReceipt: ...

    def observe_tree(
        self,
        tree: TreeHandle,
    ) -> tuple[TreeProcessObservation, ...]: ...


def _time_ms() -> int:
    """Current wall-clock time in integer milliseconds."""
    return int(time.time() * 1000)


# Polling interval in seconds for the cancellation-aware wait loop.
_CANCELLATION_POLL_INTERVAL_S = 0.1


@dataclass(frozen=True)  # pyright: ignore[reportUntypedClassDecorator]
class PipeRunnerConfig:
    """Configuration for a pipe-based process runner invocation.

    ``argv`` is the command to execute.  ``cwd`` is the working directory
    (defaults to the current directory if ``None``).  ``env`` is an optional
    environment mapping (defaults to inheriting the parent environment).
    ``stdin_data`` is optional bytes to write to the subprocess's stdin
    (stdin is closed immediately after writing, or immediately if ``None``).
    """

    argv: Sequence[str]
    cwd: Path | str | None = None
    env: dict[str, str] | None = None
    stdin_data: bytes | None = None
    process_timeout_ms: int | None = None
    silence_timeout_ms: int | None = None
    max_output_bytes: int | None = None


class _StreamReader:
    """Background reader for a single subprocess pipe (stdout or stderr).

    Each chunk read from the pipe is forwarded to the ``ProcessSupervisor``
    via ``on_chunk``, preserving real wall-clock timestamps for the
    stream-ordering contract.
    """

    def __init__(
        self,
        stream: IO[bytes],
        supervisor: ProcessSupervisor,
        *,
        chunk_size: int = 4096,
    ) -> None:
        self._stream = stream
        self._supervisor = supervisor
        self._chunk_size = chunk_size
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
            name="pipe-reader",
        )
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    @property
    def error(self) -> BaseException | None:
        with self._lock:
            return self._error

    def _read_loop(self) -> None:
        try:
            while True:
                chunk = self._stream.read(self._chunk_size)
                if not chunk:
                    break
                self._supervisor.on_chunk(
                    chunk,
                    timestamp_ms=_time_ms(),
                )
        except BaseException as exc:
            with self._lock:
                self._error = exc


def _get_process_creation_time(pid: int) -> int:
    """Best-effort process creation time in milliseconds since epoch.

    On platforms where ``psutil`` is not available or the process has
    already exited, returns 0 (the ``ProcessBirthIdentity`` contract
    permits this as a nonnegative int -- the zero value signals
    "creation time unavailable" and is the same fallback the
    ``ProcessSupervisor.on_spawned(pid=...)`` path uses).
    """
    try:
        import psutil  # type: ignore[import-untyped]

        p = psutil.Process(pid)
        return int(p.create_time() * 1000)
    except Exception:
        return 0


def _dispatch_cancellation_action(
    action: CancellationAction,
    tree_controller: TreeController,
    tree_handle: TreeHandle,
) -> TreeDispatchReceipt | None:
    """Dispatch a single cancellation action to the tree controller.

    Returns the receipt, or ``None`` if the action requires no dispatch.
    """
    if action is CancellationAction.SOFT_CANCEL:
        return tree_controller.soft_cancel(tree_handle)
    elif action is CancellationAction.TERMINATE_TREE:
        return tree_controller.terminate_tree(tree_handle)
    elif action is CancellationAction.KILL_TREE:
        return tree_controller.kill_tree(tree_handle)
    return None


def _drive_cancellation_ladder(
    supervisor: ProcessSupervisor,
    tree_controller: TreeController,
    tree_handle: TreeHandle,
    get_time: Callable[[], int],
) -> None:
    """Step the cancellation ladder once: dispatch action + observe + feed back.

    Called from the main thread's polling loop when ``supervisor.cancellation_active``
    is ``True``.  Per Decision A the main thread drives the ladder synchronously
    during ``proc.wait()``, not a separate poller thread.
    """
    decision = supervisor.cancellation_decision
    if decision is None:
        return

    # If already completed, nothing to do.
    if decision.stage is CancellationStage.COMPLETED:
        return

    # Dispatch the action requested by the current decision.
    _dispatch_cancellation_action(
        decision.action, tree_controller, tree_handle,
    )

    # Observe the tree and feed observations back to the supervisor.
    observations = tree_controller.observe_tree(tree_handle)
    supervisor.on_tree_state(
        observations=list(observations),
        now_ms=get_time(),
    )


def run_process(
    config: PipeRunnerConfig,
    supervisor: ProcessSupervisor,
    *,
    clock_ms: type[int] | None = None,
    on_spawned: "Callable[[subprocess.Popen[bytes], ProcessBirthIdentity], None] | None" = None,
    tree_controller: TreeController | None = None,
) -> ProcessSupervisionOutcome:
    """Spawn a process and drive ``supervisor`` with real subprocess data.

    This function blocks until the process exits and all output has been
    read.  It performs no state-storage transactions -- it's pure OS
    execution driving the pure ``ProcessSupervisor`` reducer.

    When a cancellation has been triggered (via ``supervisor.begin_cancellation()``,
    called either from a timeout trigger or from the heartbeat-failure path),
    the main thread drives the cancellation ladder synchronously: it dispatches
    the tree controller action (``soft_cancel``/``terminate_tree``/``kill_tree``),
    observes the tree, and feeds observations back via ``supervisor.on_tree_state()``
    until the ladder reaches ``COMPLETED`` or the process exits.

    Parameters
    ----------
    config:
        What to spawn and how.
    supervisor:
        The ``ProcessSupervisor`` instance to drive.  Its callbacks
        ``on_spawned``, ``on_chunk``, ``on_exit``, and (on cleanup
        failure) ``on_cleanup_error`` are called with real data.
    clock_ms:
        Optional injectable clock for testing (returns int ms).
        Defaults to wall-clock ``_time_ms``.
    on_spawned:
        Optional callback invoked immediately after the process is
        spawned and ``supervisor.on_spawned()`` has been called, but
        **before** the blocking stdin-write / output-read / wait loop.
        Receives the live ``subprocess.Popen`` handle and the
        ``ProcessBirthIdentity``.  Intended for callers (e.g.
        workflows.py) that need to record RUNNING durably and start
        a ``HeartbeatWorker`` while the process is still executing.
    tree_controller:
        Optional ``TreeController`` for process tree binding and cancellation support.
        If provided or if constructed, ``bind_spawn`` is called after spawn.

    Returns
    -------
    ProcessSupervisionOutcome
        The finalized outcome from ``supervisor.finalize_execution_outcome()``.
    """
    get_time = clock_ms if clock_ms is not None else _time_ms

    extra_kwargs = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    argv = list(config.argv)
    proc = subprocess.Popen(  # pyright: ignore[reportCallIssue]
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if config.stdin_data is not None else subprocess.DEVNULL,
        cwd=config.cwd,
        env=config.env,
        **extra_kwargs,  # pyright: ignore[reportArgumentType]
    )

    # Record process birth identity immediately after spawn.
    pid = proc.pid
    creation_time = _get_process_creation_time(pid)
    identity = ProcessBirthIdentity(
        pid=pid,
        process_creation_time=creation_time,
    )
    supervisor.on_spawned(identity=identity)

    # Bind spawn with tree controller if provided or available
    if tree_controller is None:
        try:
            from peerhub.dispatch.tree_controller import RealTreeController

            tree_controller = RealTreeController()
        except Exception:
            pass

    tree_handle: TreeHandle | None = None
    if tree_controller is not None:
        tree_handle = tree_controller.bind_spawn(process=proc, root=identity)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        setattr(proc, "_tree_handle", tree_handle)
        setattr(proc, "_tree_controller", tree_controller)

    # Notify the caller with the live process handle + identity before
    # entering the blocking I/O loop.  This preserves the callback
    # ordering: on_spawned (supervisor) → on_spawned (caller) → on_chunk
    # → on_exit → on_cleanup_error → finalize.
    if on_spawned is not None:
        on_spawned(proc, identity)

    # Write stdin data if provided, then close.
    if config.stdin_data is not None and proc.stdin is not None:
        try:
            proc.stdin.write(config.stdin_data)
        except OSError:
            pass  # Process may have already exited.
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    # Start background readers for stdout and stderr.
    stdout_reader = _StreamReader(proc.stdout, supervisor)  # type: ignore[arg-type]
    stderr_reader = _StreamReader(proc.stderr, supervisor)  # type: ignore[arg-type]
    stdout_reader.start()
    stderr_reader.start()

    start_time = get_time()

    # Wait for the process to exit, driving the cancellation ladder from the
    # main thread when a cancellation is active (Decision A: synchronous
    # polling, no separate poller thread).
    while proc.poll() is None:
        now = get_time()
        
        if not supervisor.cancellation_active:
            if config.process_timeout_ms is not None and (now - start_time >= config.process_timeout_ms):
                supervisor.trigger_process_deadline(now_ms=now)
            elif config.silence_timeout_ms is not None and (now - (supervisor.last_activity_ms if supervisor.last_activity_ms is not None else start_time) >= config.silence_timeout_ms):
                supervisor.trigger_silence_timeout(now_ms=now)
            elif config.max_output_bytes is not None and supervisor.total_output_bytes > config.max_output_bytes:
                supervisor.trigger_output_limit_exceeded(now_ms=now)

        if (
            supervisor.cancellation_active
            and tree_controller is not None
            and tree_handle is not None
        ):
            _drive_cancellation_ladder(
                supervisor, tree_controller, tree_handle, get_time,  # pyright: ignore[reportUnknownArgumentType]
            )
            # Check if ladder completed; if so, give the process a brief
            # moment to exit from the signal before the next poll.
            decision = supervisor.cancellation_decision
            if (
                decision is not None
                and decision.stage is CancellationStage.COMPLETED
            ):
                # Ladder finished — wait a short time for the process to
                # exit, then break to avoid infinite polling.
                try:
                    proc.wait(timeout=_CANCELLATION_POLL_INTERVAL_S)
                except subprocess.TimeoutExpired:
                    pass
                break
        # Sleep briefly to avoid busy-waiting.
        try:
            proc.wait(timeout=_CANCELLATION_POLL_INTERVAL_S)
        except subprocess.TimeoutExpired:
            pass

    # Wait for readers to drain remaining buffered output.
    stdout_reader.join(timeout=10.0)
    stderr_reader.join(timeout=10.0)

    # Record exit.
    if proc.returncode is not None:
        supervisor.on_exit(exit_code=proc.returncode)
    else:
        # Process may still be running if ladder completed but process
        # hasn't exited yet.  Force-wait and record.
        proc.wait()
        supervisor.on_exit(exit_code=proc.returncode)  # pyright: ignore[reportArgumentType]

    # Check for reader errors as cleanup evidence.
    reader_errors: list[int] = []
    if stdout_reader.error is not None:
        reader_errors.append(pid)
    if stderr_reader.error is not None and pid not in reader_errors:
        reader_errors.append(pid)
    if reader_errors:
        supervisor.on_cleanup_error(unresolved_identities=reader_errors)

    return supervisor.finalize_execution_outcome()
