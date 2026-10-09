"""FakeRuntimeTarget: scripted, deterministic, records an ordered call log. Never spawns a process."""
from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from peerhub.extensions.bridge import ContextLostError, PrespawnError, RuntimeTargetError, SessionError


class FakeRuntimeTarget:
    runtime_kind = "fake"

    def __init__(self, fingerprint: str = "F1", resumable: bool = True, binding: str = "model-a/profile-1", *, native: bool = False) -> None:
        self.native = native
        self._fp = fingerprint
        self._binding = binding
        self.resumable = resumable
        self.calls: list[tuple] = []  # ordered: ("create",) ("resume", id) ("deliver", session, record_id, n_catch_up)
        self.sessions: set[str] = set()
        self._n = 0
        self._create: deque = deque()   # "ok" | Exception ; empty -> ok
        self._resume: deque = deque()   # "ok"|"missing"|"unsupported"|"rejected" ; empty -> ok if live else missing
        self._deliver: deque = deque()  # list of events per deliver call
        self.catch_ups: list[list[str]] = []
        self.catch_up_meta: list[Any] = []
        self.supports = {"interrupt": True, "terminate": True, "steer": True}
        self._effects: dict[str, deque] = {"interrupt": deque(), "terminate": deque(), "steer": deque()}

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

    def capabilities(self, **caps: bool) -> "FakeRuntimeTarget":
        self.supports.update(caps)
        return self

    def script_effect(self, name: str, *items: Any) -> "FakeRuntimeTarget":
        """Per-call behaviour of interrupt/terminate/steer: "ok" | Exception to raise | callable run during the call."""
        self._effects[name].extend(items)
        return self

    def lose_context(self) -> None:
        self.sessions.clear()

    @property
    def supports_interrupt(self) -> bool:
        return self.supports["interrupt"]

    @property
    def supports_terminate(self) -> bool:
        return self.supports["terminate"]

    @property
    def supports_steer(self) -> bool:
        return self.supports["steer"]

    def _effect(self, name: str, *args: Any) -> str:
        self.calls.append((name, *args))
        item = self._effects[name].popleft() if self._effects[name] else "ok"
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item()
        return "ok"

    def interrupt(self, external_session_id: str) -> str:
        return self._effect("interrupt", external_session_id)

    def terminate(self, external_session_id: str) -> str:
        return self._effect("terminate", external_session_id)

    def steer(self, external_session_id: str, record: Any) -> str:
        return self._effect("steer", external_session_id, record.record_id)

    def count(self, name: str) -> int:
        return sum(1 for c in self.calls if c[0] == name)

    # --- RuntimeTarget
    def set_binding(self, b: str) -> None:
        self._binding = b

    def binding(self) -> str:
        return self._binding

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

    def resume_session(self, external_session_id: str, *, vendor_session_id: str | None = None) -> str:
        self.calls.append(("resume", external_session_id))
        if self._resume:
            return self._resume.popleft()
        return "ok" if external_session_id in self.sessions else "missing"

    def deliver(self, external_session_id: str, record: Any, catch_up: list) -> Iterable[tuple]:
        self.calls.append(("deliver", external_session_id, record.record_id, len(catch_up)))
        self.catch_ups.append([r.record_id for r in catch_up])
        self.catch_up_meta.append(getattr(catch_up, "boundary", None))
        result = {"response": "ok"}
        if self.native:
            result["vendor_session"] = {"id": f"native-{external_session_id}"}
        script = self._deliver.popleft() if self._deliver else [("started", f"x-{record.record_id}"), ("terminal", result)]
        for ev in script:
            if ev[0] == "prespawn_error":
                raise PrespawnError(ev[1])
            if ev[0] == "context_lost":
                raise ContextLostError(ev[1])
            if ev[0] == "runtime_error":
                raise RuntimeTargetError(ev[1])
            if ev[0] == "call":  # test hook executed in the middle of a delivery (e.g. let the lease expire)
                ev[1]()
                continue
            yield ev


class NativeRuntimeTarget(FakeRuntimeTarget):
    """Native IDs survive local runtime instances; opt-in belongs to this runtime."""

    def __init__(self, *args, resume: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.resume = resume

    def resume_session(self, external_session_id: str, *, vendor_session_id: str | None = None) -> str:
        self.vendor_ids = getattr(self, "vendor_ids", []) + [vendor_session_id]
        self.calls.append(("resume", external_session_id))
        if self._resume:
            return self._resume.popleft()
        return "ok" if self.resume and self.resumable and vendor_session_id else "unsupported"

    def deliver(self, external_session_id: str, record: Any, catch_up: list) -> Iterable[tuple]:
        if not self._deliver:
            self.script_deliver(("started", f"x-{record.record_id}"),
                                ("terminal", {"response": "ok", "vendor_session": {"id": f"native-{external_session_id}"}}))
        yield from super().deliver(external_session_id, record, catch_up)
