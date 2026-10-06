"""Wave 6 child-process entrypoints (spawn): real process death via os._exit at named points, restart/recovery workers,
and the Core-only launcher. Workers put exactly ONE dict on out_q unless they die on purpose."""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
from typing import Any

CRASH_EXIT = 17
BARRIER_TIMEOUT = 40.0


def spawn_exitcode(target, args, timeout: float = 90.0) -> int:
    """Run `target` in a fresh spawned process and return its exit code (bounded wait, no sleeps)."""
    p = mp.get_context("spawn").Process(target=target, args=args, daemon=True)
    p.start()
    p.join(timeout)
    alive = p.is_alive()
    if alive:
        p.terminate()
    assert not alive, "child hung"
    return p.exitcode


def _err(e: BaseException) -> dict[str, str]:
    return {"type": type(e).__name__, "msg": str(e)}


def core_append_crash_worker(db_path: str, request: dict, crash_point: str) -> None:
    """Append through a real CoreStore and die hard (os._exit) at `crash_point`; exit 0 only if the point never fired."""
    from peerhub.core.store import CoreStore

    def hook(point: str) -> None:
        if point == crash_point:
            os._exit(CRASH_EXIT)

    CoreStore(db_path, fault_hook=hook).append_record(**request)
    os._exit(0)


def cycles_worker(ws_path: str, peer_id: str, stream_id: str, clock: float, script: list, max_cycles: int, out_q: Any) -> None:
    """Fresh process = restart: run delivery cycles until idle/non-delivering; report statuses and the fake runtime call log."""
    out: dict[str, Any] = {}
    try:
        from tests.communication.fakes import FakeRuntimeTarget
        from tests.communication.harness.bridge import BridgeHarness
        from tests.communication.harness.clock import ManualClock

        h = BridgeHarness(ws_path, ManualClock(clock))
        rt = FakeRuntimeTarget()
        for ev in script:
            rt.script_deliver(*[tuple(e) for e in ev])
        statuses = []
        for _ in range(max_cycles):
            res = h.delivery_cycle(peer_id, stream_id, rt)
            statuses.append(res.status)
            if res.status not in ("delivered", "recovered_terminal"):
                break
        out = {"statuses": statuses, "calls": [list(c) for c in rt.calls]}
    except BaseException as e:
        out["fatal"] = _err(e)
    out_q.put(out)


def recovery_worker(ws_path: str, peer_id: str, stream_id: str, clock: float, owner: str, barrier: Any, out_q: Any) -> None:
    """One recovering Bridge process: wait at the barrier, run ONE delivery cycle with its own owner id."""
    out: dict[str, Any] = {"owner": owner}
    try:
        from peerhub.extensions.bridge_claims import ClaimHeldError
        from tests.communication.fakes import FakeRuntimeTarget
        from tests.communication.harness.bridge import BridgeHarness
        from tests.communication.harness.clock import ManualClock

        h = BridgeHarness(ws_path, ManualClock(clock), owner_id=owner)
        rt = FakeRuntimeTarget()
        barrier.wait(BARRIER_TIMEOUT)
        try:
            res = h.delivery_cycle(peer_id, stream_id, rt)
            out["status"] = res.status
            out["response_record_id"] = res.response_record_id
        except ClaimHeldError as e:
            out["claim_err"] = _err(e)
        out["deliver_calls"] = rt.count("deliver")
    except BaseException as e:
        out["fatal"] = _err(e)
    out_q.put(out)
