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
