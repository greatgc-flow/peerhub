"""Bounded subprocess runner (extension side). Explicit argv, never a shell; bounded time and output."""
from __future__ import annotations

import io
import math
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence, cast

from peerhub.extensions.process_tree import kill_process_tree

DEFAULT_TIMEOUT_S = 120.0
DEFAULT_MAX_BYTES = 1_048_576
_CMD_UNSAFE = set('&|<>^%"\r\n\0!')


class SpawnFailure(OSError):
    """The OS refused to create the process (nothing ran): the only genuine pre-spawn failure."""


@dataclass(frozen=True)
class ProcessResult:
    pid: int
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    output_exceeded: bool
    duration_s: float
    silence_timed_out: bool = False
    aborted: bool = False
    termination_confirmed: bool | None = None
    output_truncated: bool = False
    stdout_truncated: bool = False
    drain_timed_out: bool = False
    display_truncated: bool = False
    parser_failed: bool = False


class _HeadTailBuffer:
    """Bounded byte buffer implementing a head + tail retention policy.

    If total bytes received <= max_bytes, all bytes are preserved.
    If total bytes received > max_bytes, at most max_bytes are retained:
    the head retains the first (max_bytes // 2) bytes, and the tail retains
    the most recent (max_bytes - max_bytes // 2) bytes. Middle bytes are discarded.
    Memory is bounded at all times by (max_bytes + chunk_size).
    """

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.head_limit = max(0, max_bytes // 2)
        self.tail_limit = max(0, max_bytes - self.head_limit)
        self.head = bytearray()
        self.tail = bytearray()
        self.total_bytes = 0

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.total_bytes += len(chunk)
        if len(self.head) < self.head_limit:
            room = self.head_limit - len(self.head)
            self.head.extend(chunk[:room])
            tail_chunk = chunk[room:]
        else:
            tail_chunk = chunk
        if tail_chunk and self.tail_limit > 0:
            self.tail.extend(tail_chunk)
            if len(self.tail) > self.tail_limit:
                del self.tail[:-self.tail_limit]

    def get_bytes(self) -> bytes:
        return bytes(self.head) + bytes(self.tail)

    @property
    def truncated(self) -> bool:
        return self.total_bytes > self.max_bytes


def check_argv(argv: Sequence[Any]) -> None:
    """Reject argv that cannot be passed safely (checked BEFORE any spawn; raises ValueError)."""
    if not argv or not all(isinstance(a, str) and a and "\0" not in a for a in argv):
        raise ValueError("argv must be a non-empty list of non-empty strings without NUL")
    if sys.platform == "win32" and os.path.splitext(argv[0])[1].lower() in (".cmd", ".bat"):
        if any(_CMD_UNSAFE & set(a) for a in argv[1:]):  # Windows runs .cmd through cmd.exe: metacharacters would be interpreted
            raise ValueError("argument contains characters unsafe for a .cmd wrapper")


class BoundedProcess:
    """start() raises SpawnFailure only when no process was created; wait() never raises for post-spawn failures."""

    def __init__(self, argv: Sequence[str], *, stdin: bytes | None, cwd: str, env: Mapping[str, str],
                 timeout_s: float = DEFAULT_TIMEOUT_S, max_bytes: int = DEFAULT_MAX_BYTES,
                 silence_timeout_s: float | None = None,
                 on_stdout: Callable[[bytes], None] | None = None,
                 on_display: Callable[[bytes], None] | None = None) -> None:
        if silence_timeout_s is not None and (not math.isfinite(silence_timeout_s) or silence_timeout_s <= 0):
            raise ValueError("silence timeout must be finite and positive")
        self._argv, self._stdin, self._cwd, self._env = list(argv), stdin, cwd, dict(env)
        self._timeout_s, self._max_bytes = timeout_s, max_bytes
        self._silence_timeout_s = silence_timeout_s
        self._activity_lock = threading.Lock()
        self._last_output = 0.0
        self._on_stdout = on_stdout
        self._on_display = on_display
        self._proc: subprocess.Popen[bytes] | None = None
        self._t0 = 0.0
        self._bufs: dict[str, _HeadTailBuffer] = {"out": _HeadTailBuffer(max_bytes), "err": _HeadTailBuffer(max_bytes)}
        self._aborted = threading.Event()
        self._kill_lock = threading.Lock()
        self._termination_confirmed: bool | None = None
        self._threads: list[threading.Thread] = []
        self._drain_threads: list[threading.Thread] = []
        self._display_queue: queue.Queue[bytes | None] = queue.Queue()
        self._display_thread: threading.Thread | None = None
        self._display_truncated = False
        self._display_bytes = 0
        self._display_lock = threading.Lock()
        self._display_max_bytes = max(0, max_bytes)
        self._parser_failed = False

    @property
    def pid(self) -> int:
        assert self._proc is not None
        return self._proc.pid

    @property
    def termination_confirmed(self) -> bool | None:
        return self._termination_confirmed

    @property
    def display_truncated(self) -> bool:
        return self._display_truncated

    def mark_display_truncated(self) -> None:
        self._display_truncated = True

    @property
    def parser_failed(self) -> bool:
        return self._parser_failed

    def start(self) -> "BoundedProcess":
        check_argv(self._argv)
        self._t0 = time.monotonic()
        self._last_output = self._t0
        try:
            self._proc = subprocess.Popen(self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          cwd=self._cwd, env=self._env, shell=False,
                                          start_new_session=sys.platform != "win32")
        except (OSError, ValueError) as e:
            raise SpawnFailure(f"{type(e).__name__}: {getattr(e, 'strerror', None) or e}") from e
        p = self._proc
        # default bufsize=-1 => the pipes are io.BufferedReader (read1 available)
        out, err = cast(io.BufferedReader, p.stdout), cast(io.BufferedReader, p.stderr)
        out_thread = threading.Thread(target=self._pump, args=(out, "out"), daemon=True)
        err_thread = threading.Thread(target=self._pump, args=(err, "err"), daemon=True)
        feed_thread = threading.Thread(target=self._feed, args=(p,), daemon=True)
        self._drain_threads = [out_thread, err_thread]
        self._threads = [out_thread, err_thread, feed_thread]
        if self._on_display is not None:
            self._display_thread = threading.Thread(target=self._drain_display, daemon=True)
            self._display_thread.start()
        for t in self._threads:
            t.start()
        return self

    def _drain_display(self) -> None:
        while True:
            chunk = self._display_queue.get()
            if chunk is None:
                break
            with self._display_lock:
                self._display_bytes = max(0, self._display_bytes - len(chunk))
            if self._on_display is not None:
                try:
                    self._on_display(chunk)
                except Exception:
                    self._on_display = None
                    self._display_truncated = True


    def _pump(self, stream: io.BufferedReader, key: str) -> None:
        buf = self._bufs[key]
        try:
            while True:
                chunk = stream.read1(65536)
                if not chunk:
                    return
                with self._activity_lock:
                    self._last_output = time.monotonic()
                buf.feed(chunk)
                if key == "out":
                    if self._on_stdout is not None:
                        try:
                            self._on_stdout(chunk)
                        except Exception:
                            self._parser_failed = True
                            self._on_stdout = None
                    if self._on_display is not None:
                        with self._display_lock:
                            room = max(0, self._display_max_bytes - self._display_bytes)
                            if room > 0:
                                kept = chunk[:room]
                                self._display_queue.put_nowait(kept)
                                self._display_bytes += len(kept)
                                if len(chunk) > room:
                                    self._display_truncated = True
                            else:
                                self._display_truncated = True
        except (OSError, ValueError):
            return
        finally:
            if key == "out":
                self._display_queue.put_nowait(None)

    def _feed(self, p: "subprocess.Popen[bytes]") -> None:
        try:
            stdin = p.stdin
            if stdin is None:
                return
            if self._stdin:
                stdin.write(self._stdin)
            stdin.close()
        except (OSError, ValueError):
            pass

    def terminate(self) -> None:
        """Kill the tree and observe exit without consuming delivery output on this thread."""
        with self._kill_lock:
            p = self._proc
            if p is None or p.poll() is not None:
                self._termination_confirmed = False
                raise OSError("nothing running")
            # Even a racing zero exit must not become a successful delivery after cancellation.
            self._aborted.set()
            killed = kill_process_tree(p)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired as e:
                self._termination_confirmed = False
                raise OSError("process exit could not be confirmed") from e
            if not killed:
                self._termination_confirmed = False
                if sys.platform == "win32":
                    raise OSError("process-tree termination could not be confirmed "
                                  "(taskkill reported an unkillable child; the main process exited)")
                raise OSError("process-tree termination could not be confirmed")
            self._termination_confirmed = True

    def abort(self) -> None:
        """Kill and reap (used when the consumer stops iterating a delivery)."""
        with self._kill_lock:
            self._aborted.set()
            p = self._proc
            if p is not None and self._termination_confirmed is None:
                if p.poll() is not None:
                    # A parent that exited while a child survived cannot be confirmed.
                    self._termination_confirmed = False
                else:
                    killed = kill_process_tree(p)
                    try:
                        p.wait(timeout=10)
                        self._termination_confirmed = killed
                    except subprocess.TimeoutExpired:
                        self._termination_confirmed = False
        self.wait()

    def wait(self) -> ProcessResult:
        p = self._proc
        assert p is not None
        timed_out = False
        silence_timed_out = False
        deadline = self._t0 + self._timeout_s
        while p.poll() is None and not self._aborted.is_set():
            if time.monotonic() >= deadline:
                timed_out = True
                break
            with self._activity_lock:
                last_output = self._last_output
            if self._silence_timeout_s is not None and time.monotonic() - last_output >= self._silence_timeout_s:
                silence_timed_out = True
                break
            time.sleep(0.02)
        with self._kill_lock:
            needed = timed_out or silence_timed_out or self._aborted.is_set()
            if needed and self._termination_confirmed is None:
                if p.poll() is not None:
                    # A parent that exited while a child survived cannot be confirmed.
                    self._termination_confirmed = False
                else:
                    killed = kill_process_tree(p)
                    wait_timeout = 10.0 if killed else 0.2
                    try:
                        p.wait(timeout=wait_timeout)
                        self._termination_confirmed = killed
                    except subprocess.TimeoutExpired:
                        self._termination_confirmed = False
            elif not needed:
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        drain_timed_out = False
        if p.poll() is None:
            drain_timed_out = True
        else:
            deadline = time.monotonic() + 5.0
            for t in self._drain_threads:
                rem = max(0.0, deadline - time.monotonic())
                t.join(timeout=rem)
                if t.is_alive():
                    drain_timed_out = True
        for t in self._threads:
            if t not in self._drain_threads:
                t.join(timeout=0.2 if p.poll() is None else 5.0)
        if self._display_thread is not None:
            self._display_thread.join(timeout=0.1)
            if self._display_thread.is_alive():
                self._display_truncated = True
                self._on_display = None

        for s in (p.stdout, p.stderr):
            try:
                if s is not None:
                    s.close()
            except OSError:
                pass
        out_bytes = self._bufs["out"].get_bytes()
        err_bytes = self._bufs["err"].get_bytes()
        stdout_truncated = self._bufs["out"].truncated
        stderr_truncated = self._bufs["err"].truncated
        truncated = stdout_truncated or stderr_truncated
        return ProcessResult(pid=p.pid, returncode=p.returncode, stdout=out_bytes, stderr=err_bytes,
                             timed_out=timed_out, output_exceeded=truncated, output_truncated=truncated,
                             stdout_truncated=stdout_truncated,
                             duration_s=time.monotonic() - self._t0, silence_timed_out=silence_timed_out,
                             aborted=self._aborted.is_set(), termination_confirmed=self._termination_confirmed,
                             drain_timed_out=drain_timed_out,
                             display_truncated=self._display_truncated,
                             parser_failed=self._parser_failed)


def run_bounded(argv: Sequence[str], *, stdin: bytes | None, cwd: str, env: Mapping[str, str],
                timeout_s: float = DEFAULT_TIMEOUT_S, max_bytes: int = DEFAULT_MAX_BYTES,
                silence_timeout_s: float | None = None,
                on_stdout: Callable[[bytes], None] | None = None,
                on_display: Callable[[bytes], None] | None = None) -> ProcessResult:
    return BoundedProcess(argv, stdin=stdin, cwd=cwd, env=env, timeout_s=timeout_s, max_bytes=max_bytes,
                          silence_timeout_s=silence_timeout_s,
                          on_stdout=on_stdout, on_display=on_display).start().wait()
