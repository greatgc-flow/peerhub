"""Dependency-free process-tree termination shared by runtime and observation."""
from __future__ import annotations

import os
import signal
import subprocess
import sys


def kill_process_tree(proc: subprocess.Popen[bytes] | subprocess.Popen[str]) -> bool:
    """Return whether the OS accepted a whole-tree kill (exit is observed separately)."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                                    capture_output=True, timeout=10, check=False)
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
