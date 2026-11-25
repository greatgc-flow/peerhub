"""Local process liveness for caller badges and abandoned ask detection."""
from __future__ import annotations

import json
import math
from typing import Callable, Literal, cast

from peerhub.extensions import caller, process_tree

ProcessInfo = Callable[[int], "float | None"]


def _number(value: object) -> float | None:
    if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _pid(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**32 else None


def _detail(raw: object) -> dict[str, object]:
    value: object = json.loads(raw) if isinstance(raw, str) else None
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _local(host: object, ns: object, this_host: str | None, this_ns: str | None) -> bool:
    return (isinstance(host, str) and bool(host) and host == (this_host if this_host is not None else caller.host_id())
            and isinstance(ns, str) and bool(ns) and ns == (this_ns if this_ns is not None else caller.pid_namespace()))


def _identity_alive(pid: int, created: float | None, probe: ProcessInfo, *, reused: bool | None) -> bool | None:
    actual = probe(pid)
    if actual is None:
        return False
    if created is None or _number(actual) is None:
        return None
    return True if actual == created else reused


def ask_alive(host: object, ns: object, pid: object, created: object, *, this_host: str | None = None,
              this_ns: str | None = None, process_info: ProcessInfo | None = None) -> bool | None:
    """Report the caller badge's liveness, leaving creation time mismatches unknown."""
    try:
        identity, born = _pid(pid), _number(created)
        if identity is not None and born is not None and _local(host, ns, this_host, this_ns):
            return _identity_alive(identity, born, process_info or process_tree.process_created, reused=None)
    except Exception:
        return None
    return None


PROCESSES_DEAD = "gone, processes dead"
ORPHAN_RUNNING = "orphan running"


def ask_process_state(caller_detail: object, provider_detail: object, *, this_host: str | None = None,
                      this_ns: str | None = None, process_info: ProcessInfo | None = None
                      ) -> Literal["alive", "gone, processes dead", "orphan running"] | None:
    """Classify the local lease's recorded processes. None means the evidence is incomplete."""
    try:
        detail = _detail(caller_detail)
        if detail.get("v") != 1 or not _local(detail.get("host"), detail.get("ns"), this_host, this_ns):
            return None
        wrapper = _pid(detail.get("ask_pid"))
        parent = _pid(detail.get("caller_pid", wrapper))  # legacy rows named the ask wrapper as the caller
        if wrapper is None or parent is None:
            return None
        probe = process_info or process_tree.process_created
        wrapper_created = _number(detail.get("ask_created"))
        if wrapper_created is None:
            return None  # without the wrapper's start time, we cannot attribute a surviving child
        wrapper_alive = _identity_alive(wrapper, wrapper_created, probe, reused=False)
        parent_alive = (wrapper_alive if parent == wrapper else
                        _identity_alive(parent, _number(detail.get("caller_created")), probe, reused=False))
        if wrapper_alive is True or parent_alive is True:
            return "alive"
        if wrapper_alive is not False or parent_alive is not False:
            return None
        started = _detail(provider_detail)
        provider = _pid(started.get("provider_pid"))
        provider_created = _number(started.get("provider_created"))
        if provider is not None:
            provider_alive = _identity_alive(provider, provider_created, probe, reused=False)
            if provider_alive is True:
                return ORPHAN_RUNNING
        else:
            provider_alive = False
        if process_tree.provider_descendant(wrapper, wrapper_created, provider, provider_created):
            return ORPHAN_RUNNING
        return PROCESSES_DEAD if provider_alive is False else None
    except Exception:  # inaccessible or invalid process evidence cannot justify release
        return None
