"""Is the ask that wrote a caller row still running? (#374, design v4 F6: fail closed.)

True only for an exact identity match on this host and pid namespace; False only when no process has the pid;
None for everything else (another host or namespace, a reused pid or a stepped clock, missing values, errors).
"""
from __future__ import annotations

import math
from typing import Callable

from peerhub.extensions import caller

ProcessInfo = Callable[[int], "float | None"]


def _psutil_created(pid: int) -> float | None:
    import psutil
    try:
        return float(psutil.Process(pid).create_time())
    except psutil.ZombieProcess:
        raise  # an exited process whose pid is still held: neither alive nor provably gone
    except psutil.NoSuchProcess:
        return None


def _number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def ask_alive(host: object, ns: object, pid: object, created: object, *, this_host: str | None = None,
              this_ns: str | None = None, process_info: ProcessInfo | None = None) -> bool | None:
    try:
        here = caller.host_id() if this_host is None else this_host
        here_ns = caller.pid_namespace() if this_ns is None else this_ns
        if not isinstance(host, str) or not host or host != here:
            return None
        if not isinstance(ns, str) or not ns or ns != here_ns:
            return None
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return None
        if isinstance(created, bool) or not isinstance(created, (int, float)) or not math.isfinite(created):
            return None
        actual = (process_info or _psutil_created)(pid)
        if actual is None:
            return False
        if not _number(actual):
            return None
        return True if float(actual) == float(created) else None
    except Exception:
        return None
