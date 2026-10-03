"""Child-process entrypoint: run one bridge delivery cycle and die hard (os._exit) at a named crash point."""
from __future__ import annotations

import os


def crash_cycle_worker(ws_path: str, peer_id: str, stream_id: str, crash_point: str, script: list, start_clock: float) -> None:
    from tests.m1.fakes import FakeRuntimeTarget
    from tests.m1.harness.bridge import BridgeHarness
    from tests.m1.harness.clock import ManualClock

    def hook(point: str) -> None:
        if point == crash_point:
            os._exit(17)

    h = BridgeHarness(ws_path, ManualClock(start_clock), fault_hook=hook)
    rt = FakeRuntimeTarget()
    rt.script_deliver(*[tuple(e) for e in script])
    h.delivery_cycle(peer_id, stream_id, rt)
    os._exit(0)  # reached only if the crash point never fired
