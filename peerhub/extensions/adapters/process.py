"""Bounded subprocess runner (extension side). Explicit argv, never a shell; bounded time and output."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence, cast

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


def check_argv(argv: Sequence[Any]) -> None:
    """Reject argv that cannot be passed safely (checked BEFORE any spawn; raises ValueError)."""
    if not argv or not all(isinstance(a, str) and a and "\0" not in a for a in argv):
        raise ValueError("argv must be a non-empty list of non-empty strings without NUL")
    if sys.platform == "win32" and os.path.splitext(argv[0])[1].lower() in (".cmd", ".bat"):
        if any(_CMD_UNSAFE & set(a) for a in argv[1:]):  # Windows runs .cmd through cmd.exe: metacharacters would be interpreted
            raise ValueError("argument contains characters unsafe for a .cmd wrapper")


def _kill_tree(proc: "subprocess.Popen[bytes]") -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10, check=False)
        proc.kill()
    except (OSError, subprocess.SubprocessError):
        pass


class BoundedProcess:
    """start() raises SpawnFailure only when no process was created; wait() never raises for post-spawn failures."""

    def __init__(self, argv: Sequence[str], *, stdin: bytes | None, cwd: str, env: Mapping[str, str],
                 timeout_s: float = DEFAULT_TIMEOUT_S, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._argv, self._stdin, self._cwd, self._env = list(argv), stdin, cwd, dict(env)
        self._timeout_s, self._max_bytes = timeout_s, max_bytes
        self._proc: subprocess.Popen[bytes] | None = None
        self._t0 = 0.0
        self._bufs: dict[str, bytearray] = {"out": bytearray(), "err": bytearray()}
        self._exceeded = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def pid(self) -> int:
        assert self._proc is not None
        return self._proc.pid

    def start(self) -> "BoundedProcess":
        check_argv(self._argv)
        self._t0 = time.monotonic()
        try:
            self._proc = subprocess.Popen(self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          cwd=self._cwd, env=self._env, shell=False)
        except (OSError, ValueError) as e:
            raise SpawnFailure(f"{type(e).__name__}: {getattr(e, 'strerror', None) or e}") from e
        p = self._proc
        # default bufsize=-1 => the pipes are io.BufferedReader (read1 available)
        out, err = cast(io.BufferedReader, p.stdout), cast(io.BufferedReader, p.stderr)
        self._threads = [threading.Thread(target=self._pump, args=(out, "out"), daemon=True),
                         threading.Thread(target=self._pump, args=(err, "err"), daemon=True),
                         threading.Thread(target=self._feed, args=(p,), daemon=True)]
        for t in self._threads:
            t.start()
        return self

    def _pump(self, stream: io.BufferedReader, key: str) -> None:
        try:
            while True:
                chunk = stream.read1(65536)
                if not chunk:
                    return
                room = self._max_bytes - len(self._bufs[key])
                self._bufs[key].extend(chunk[:max(room, 0)])
                if len(chunk) > room:
                    self._exceeded.set()
                    return
        except (OSError, ValueError):
            return

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

    def abort(self) -> None:
        """Kill and reap (used when the consumer stops iterating a delivery)."""
        if self._proc is not None and self._proc.poll() is None:
            _kill_tree(self._proc)
        self.wait()

    def wait(self) -> ProcessResult:
        p = self._proc
        assert p is not None
        timed_out = False
        deadline = self._t0 + self._timeout_s
        while p.poll() is None and not self._exceeded.is_set():
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.02)
        if p.poll() is None:
            _kill_tree(p)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        for t in self._threads:
            t.join(timeout=5)
        for s in (p.stdout, p.stderr):
            try:
                if s is not None:
                    s.close()
            except OSError:
                pass
        return ProcessResult(pid=p.pid, returncode=p.returncode, stdout=bytes(self._bufs["out"]), stderr=bytes(self._bufs["err"]),
                             timed_out=timed_out, output_exceeded=self._exceeded.is_set(), duration_s=time.monotonic() - self._t0)


def run_bounded(argv: Sequence[str], *, stdin: bytes | None, cwd: str, env: Mapping[str, str],
                timeout_s: float = DEFAULT_TIMEOUT_S, max_bytes: int = DEFAULT_MAX_BYTES) -> ProcessResult:
    return BoundedProcess(argv, stdin=stdin, cwd=cwd, env=env, timeout_s=timeout_s, max_bytes=max_bytes).start().wait()
