"""FakeRuntimeTarget: scripted, deterministic, records an ordered call log. Never spawns a process."""
from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from peerhub.extensions.bridge import PrespawnError, RuntimeTargetError, SessionError


class FakeRuntimeTarget:
    runtime_kind = "fake"

    def __init__(self, fingerprint: str = "F1", resumable: bool = True) -> None:
        self._fp = fingerprint
        self.resumable = resumable
        self.calls: list[tuple] = []  # ordered: ("create",) ("resume", id) ("deliver", session, record_id, n_catch_up)
        self.sessions: set[str] = set()
        self._n = 0
        self._create: deque = deque()   # "ok" | Exception ; empty -> ok
        self._resume: deque = deque()   # "ok"|"missing"|"unsupported"|"rejected" ; empty -> ok if live else missing
        self._deliver: deque = deque()  # list of events per deliver call
        self.catch_ups: list[list[str]] = []

    # --- scripting
    def set_fingerprint(self, fp: str) -> None:
        self._fp = fp

    def script_create(self, *items: Any) -> "FakeRuntimeTarget":
        self._create.extend(items)
        return self

    def script_resume(self, *items: str) -> "FakeRuntimeTarget":
        self._resume.extend(items)
        return self

    def script_deliver(self, *events: tuple) -> "FakeRuntimeTarget":
        """One deliver() call worth of events: ("prespawn_error",msg) ("started",id) ("output",chunk) ("terminal",resp)
        ("timeout",) ("disconnect",) ("error",code,msg) ("runtime_error",msg)."""
        self._deliver.append(list(events))
        return self

    def count(self, name: str) -> int:
        return sum(1 for c in self.calls if c[0] == name)

    # --- RuntimeTarget
    def fingerprint(self) -> str:
        return self._fp

    def create_session(self) -> str:
        self.calls.append(("create",))
        item = self._create.popleft() if self._create else "ok"
        if isinstance(item, BaseException):
            raise item
        self._n += 1
        sid = f"ext-{self._n}"
        self.sessions.add(sid)
        return sid

    def resume_session(self, external_session_id: str) -> str:
        self.calls.append(("resume", external_session_id))
        if self._resume:
            return self._resume.popleft()
        return "ok" if external_session_id in self.sessions else "missing"

    def deliver(self, external_session_id: str, record: Any, catch_up: list) -> Iterable[tuple]:
        self.calls.append(("deliver", external_session_id, record.record_id, len(catch_up)))
        self.catch_ups.append([r.record_id for r in catch_up])
        script = self._deliver.popleft() if self._deliver else [("started", f"x-{record.record_id}"), ("terminal", {"response": "ok"})]
        for ev in script:
            if ev[0] == "prespawn_error":
                raise PrespawnError(ev[1])
            if ev[0] == "runtime_error":
                raise RuntimeTargetError(ev[1])
            if ev[0] == "call":  # test hook executed in the middle of a delivery (e.g. let the lease expire)
                ev[1]()
                continue
            yield ev
