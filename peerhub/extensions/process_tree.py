"""Process identities, descendants, and termination shared by runtime and observation."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psutil import Process

PROCESS_TREE_KILL_TIMEOUT_SECONDS = 10.0


def process_created(pid: int) -> float | None:
    """Return the process start time, or None for an absent or exited process."""
    import psutil

    try:
        process = psutil.Process(pid)
        if sys.platform != "win32" and process.status() == psutil.STATUS_ZOMBIE:
            return None
        return float(process.create_time())
    except psutil.NoSuchProcess:
        return None


def _is_provider(process: Process) -> bool:
    name = Path(process.name()).stem.lower()
    if name in {"codex", "claude", "agy", "ant"} or name.startswith("codex-"):
        return True
    if name in {"node", "bun"}:
        # npm's Windows .cmd launchers can leave a JavaScript CLI behind.
        return any(Path(arg).name.lower() in {"codex.js", "claude.js"}
                   or "claude-code" in {part.lower() for part in Path(arg).parts}
                   for arg in process.cmdline()[1:2])
    return False


def provider_descendant(wrapper: int, wrapper_created: float | None, provider: int | None,
                        provider_created: float | None) -> bool:
    """Find a live provider through surviving parent links or its POSIX launch session."""
    import psutil

    parents: dict[int, list[psutil.Process]] = {}
    for process in psutil.process_iter():
        try:
            parents.setdefault(process.ppid(), []).append(process)
            if (sys.platform != "win32" and provider is not None and provider_created is not None
                    and os.getsid(process.pid) == provider and process.create_time() >= provider_created
                    and process.status() != psutil.STATUS_ZOMBIE and process.is_running()):
                return True  # the adapter starts the provider in its own session
        except (psutil.NoSuchProcess, ProcessLookupError):
            continue
        except psutil.AccessDenied:
            continue

    roots = {wrapper: wrapper_created}
    if provider is not None:
        roots[provider] = provider_created
    seen = set(roots)
    pending = list(roots.items())
    while pending:
        parent, earliest = pending.pop()
        for process in parents.get(parent, ()):
            if process.pid in seen:
                continue
            seen.add(process.pid)
            try:
                born = process.create_time()
                if earliest is None or born < earliest or not process.is_running():
                    continue
                if sys.platform == "win32" and parent in roots:
                    # A child of a replacement PID belongs to it if born on or after it.
                    replacement = process_created(parent)
                    if replacement is not None and born >= replacement:
                        continue
                if _is_provider(process) and process.status() != psutil.STATUS_ZOMBIE:
                    return True
                pending.append((process.pid, born))
            except psutil.NoSuchProcess:
                continue
    return False


def kill_process_tree(proc: subprocess.Popen[bytes] | subprocess.Popen[str], *,
                      timeout: float = PROCESS_TREE_KILL_TIMEOUT_SECONDS) -> bool:
    """Return whether the OS accepted a whole-tree kill (exit is observed separately)."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                                    capture_output=True, timeout=timeout, check=False)
            if result.returncode == 0:
                return True
            proc.kill()  # best-effort cleanup, but cannot claim whole-tree termination
            return False
        os.killpg(proc.pid, signal.SIGKILL)  # callers create a private session/process group
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def creation_ticks(pid: int) -> int:
    """The creation FILETIME of `pid` as one int (Windows), or -1 when it cannot be read or on another platform."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return -1
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not k32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
                return -1
            return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        finally:
            k32.CloseHandle(handle)

    return -1
