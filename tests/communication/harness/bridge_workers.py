"""Child-process entrypoint: run one bridge delivery cycle and die hard (os._exit) at a named crash point."""
from __future__ import annotations

import os


def crash_cycle_worker(ws_path: str, peer_id: str, stream_id: str, crash_point: str, script: list, start_clock: float) -> None:
    from tests.communication.fakes import FakeRuntimeTarget
    from tests.communication.harness.bridge import BridgeHarness
    from tests.communication.harness.clock import ManualClock

    def hook(point: str) -> None:
        if point == crash_point:
            os._exit(17)

    h = BridgeHarness(ws_path, ManualClock(start_clock), fault_hook=hook)
    rt = FakeRuntimeTarget()
    rt.script_deliver(*[tuple(e) for e in script])
    h.delivery_cycle(peer_id, stream_id, rt)
    os._exit(0)  # reached only if the crash point never fired


def control_crash_worker(ws_path: str, record_id: str, peer_id: str, crash_point: str, start_clock: float) -> None:
    """Child process: handle one control Record and die hard (os._exit) at a named crash point."""
    from tests.communication.fakes import FakeRuntimeTarget
    from tests.communication.harness.bridge import BridgeHarness
    from tests.communication.harness.clock import ManualClock

    def hook(point: str) -> None:
        if point == crash_point:
            os._exit(17)

    h = BridgeHarness(ws_path, ManualClock(start_clock), fault_hook=hook)
    h.handle_control(record_id, FakeRuntimeTarget(), peer_id)
    os._exit(0)  # reached only if the crash point never fired


def halt_crash_worker(ws_path: str, peer_id: str, stream_id: str, halt_kind: str, crash_point: str, start_clock: float) -> None:
    """Child process: a control Record commits between the invocation marker and the invoke, then the process dies at `crash_point`."""
    from tests.communication.control_helpers import ctl
    from tests.communication.fakes import FakeRuntimeTarget
    from tests.communication.harness.bridge import BridgeHarness
    from tests.communication.harness.clock import ManualClock

    fired: list[str] = []

    def hook(point: str) -> None:
        if point == "bridge.before_runtime_invoke" and not fired:
            fired.append(point)
            ctl(h, halt_kind, "late")
        if point == crash_point:
            os._exit(17)

    h = BridgeHarness(ws_path, ManualClock(start_clock), fault_hook=hook)
    h.delivery_cycle(peer_id, stream_id, FakeRuntimeTarget())
    os._exit(0)
