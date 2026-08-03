"""Subprocess.Popen-based pipe runner for non-interactive dispatch.

This is the default, non-interactive runner backend per the ratified
Slice 5 design (SLICE5-KICKOFF-R1.md "Process runner backend + lease
heartbeat RATIFIED").  It spawns a process via ``subprocess.Popen``
with ``PIPE`` stdout/stderr, reads output, and calls
``ProcessSupervisor``'s existing callback methods with real data --
the concrete runner driving the pure reducer.

The PTY runner (``pty.py``) is deliberately out of scope -- it is
blocked on the empirical PTY probe per DIR-004.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, IO, Protocol

from peerhub.dispatch.contract import ProcessBirthIdentity
from peerhub.dispatch.process import (
    ProcessSupervisor,
    ProcessSupervisionOutcome,
    TreeProcessObservation,
)


class TreeHandle(Protocol):
    """Opaque handle representing an observed or managed process tree."""

    @property
    def root_identity(self) -> ProcessBirthIdentity: ...


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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

    argv = list(config.argv)
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if config.stdin_data is not None else subprocess.DEVNULL,
        cwd=config.cwd,
        env=config.env,
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
        tree_handle = tree_controller.bind_spawn(process=proc, root=identity)
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

    # Wait for the process to exit.
    proc.wait()

    # Wait for readers to drain remaining buffered output.
    stdout_reader.join(timeout=10.0)
    stderr_reader.join(timeout=10.0)

    # Record exit.
    supervisor.on_exit(exit_code=proc.returncode)

    # Check for reader errors as cleanup evidence.
    reader_errors: list[int] = []
    if stdout_reader.error is not None:
        reader_errors.append(pid)
    if stderr_reader.error is not None and pid not in reader_errors:
        reader_errors.append(pid)
    if reader_errors:
        supervisor.on_cleanup_error(unresolved_identities=reader_errors)

    return supervisor.finalize_execution_outcome()
