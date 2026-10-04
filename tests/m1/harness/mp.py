"""Process/thread runners with bounded timeouts and diagnostic dumps (no sleeps-as-sync)."""
from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
from typing import Any, Callable

import pytest

CTX = mp.get_context("spawn")
TIMEOUT = 120.0
BARRIER = object()  # placeholders substituted in worker args
OUTQ = object()


def run_procs(specs: list[tuple[Callable, tuple]], *, timeout: float = TIMEOUT) -> list[dict]:
    """Spawn one real OS process per spec; BARRIER/OUTQ placeholders become the shared barrier/result queue."""
    q = CTX.Queue()
    barrier = CTX.Barrier(len(specs))
    procs = []
    for target, args in specs:
        a = tuple(barrier if x is BARRIER else (q if x is OUTQ else x) for x in args)
        p = CTX.Process(target=target, args=a, daemon=True)
        p.start()
        procs.append(p)
    results: list[dict] = []
    deadline = time.monotonic() + timeout
    try:
        while len(results) < len(procs):
            try:
                results.append(q.get(timeout=max(deadline - time.monotonic(), 0.01)))
            except queue.Empty:
                break
        for p in procs:
            p.join(max(deadline - time.monotonic(), 1.0))
        if len(results) < len(procs) or any(p.is_alive() for p in procs):
            dump = {"results": results, "exitcodes": [p.exitcode for p in procs], "alive": [p.is_alive() for p in procs]}
            pytest.fail(f"multiprocess timeout; diagnostic dump: {dump}")
    finally:
        for p in procs:
            if p.is_alive():
                p.terminate()
    bad = [r for r in results if "fatal" in r]
    assert not bad, f"worker fatal: {bad}"
    return results


def run_one(target: Callable, args: tuple, *, timeout: float = TIMEOUT) -> dict:
    """Single fresh process; returns its result dict (or fails with exit code dump)."""
    return run_procs([(target, args)], timeout=timeout)[0]


def run_threads(fns: list[Callable[[], Any]], *, timeout: float = 60.0) -> list[Any]:
    """Real threads; each fn returns a value or its exception object (callers assert on exact types)."""
    out: list[Any] = [None] * len(fns)

    def wrap(i: int) -> None:
        try:
            out[i] = fns[i]()
        except BaseException as e:
            out[i] = e

    ts = [threading.Thread(target=wrap, args=(i,), daemon=True) for i in range(len(fns))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout)
    alive = [i for i, t in enumerate(ts) if t.is_alive()]
    assert not alive, f"threads still alive after {timeout}s: {alive}"
    return out
