"""Local process liveness for caller badges and abandoned ask detection."""
from __future__ import annotations

import json
import math
import re
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


def _linux_identity(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"linux:[0-9a-f-]{36}:[0-9]+", value) else None


def _identity_alive(pid: int, created: float | None, stable: str | None, probe: ProcessInfo) -> bool | None:
    if stable is not None:
        return stable.split(":")[1] == process_tree.boot_id() and process_tree.process_identity(pid) == stable
    if created is None:
        return None
    actual = probe(pid)
    if actual is None:
        return False
    if _number(actual) is None:
        return None
    return True if actual == created else None


def ask_alive(host: object, ns: object, pid: object, created: object, *, this_host: str | None = None,
              this_ns: str | None = None, process_info: ProcessInfo | None = None,
              stable: object = None) -> bool | None:
    """Report the caller badge's liveness, leaving creation time mismatches unknown."""
    try:
        identity, born = _pid(pid), _number(created)
        if identity is not None and _local(host, ns, this_host, this_ns):
            return _identity_alive(identity, born, _linux_identity(stable), process_info or process_tree.process_created)
    except Exception:
        return None
    return None


PROCESSES_DEAD = "gone, processes dead"
ORPHAN_RUNNING = "orphan running"


def claim_wrapper_alive(caller_detail: object) -> bool | None:
    """Before invocation only the process holding the claim can dispatch."""
    try:
        detail = _detail(caller_detail)
        if detail.get("v") == 1:
            return ask_alive(detail.get("host"), detail.get("ns"), detail.get("ask_pid"),
                             detail.get("ask_created"), stable=detail.get("ask_identity"))
    except Exception:
        pass
    return None


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
        wrapper_alive = _identity_alive(wrapper, wrapper_created, _linux_identity(detail.get("ask_identity")), probe)
        parent_alive = (wrapper_alive if parent == wrapper else
                        _identity_alive(parent, _number(detail.get("caller_created")),
                                        _linux_identity(detail.get("caller_identity")), probe))
        if wrapper_alive is True or parent_alive is True:
            return "alive"
        if wrapper_alive is not False or parent_alive is not False:
            return None
        started = _detail(provider_detail)
        provider = _pid(started.get("provider_pid"))
        provider_created = _number(started.get("provider_created"))
        provider_identity = _linux_identity(started.get("provider_identity"))
        if provider is None:
            return None
        provider_alive = _identity_alive(provider, provider_created, provider_identity, probe)
        if provider_alive is True:
            return ORPHAN_RUNNING
        if provider_alive is None:
            return None
        descendant = process_tree.provider_descendant(wrapper, wrapper_created, provider, provider_created, provider_identity)
        if descendant is True:
            return ORPHAN_RUNNING
        return PROCESSES_DEAD if descendant is False else None
    except Exception:  # inaccessible or invalid process evidence cannot justify release
        return None
