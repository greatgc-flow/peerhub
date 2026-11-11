"""Offline initialization baseline for #335. Uses disposable stores, never a provider.

Run from a checkout: python -m tools.bridge_init_bench --rounds 5
The reported latencies include SQLite lock waiting, not isolated lock-wait time.
No pass/fail latency threshold is inferred from this synthetic benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sqlite3
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from typing import Any

from peerhub.core.store import CoreStore
from peerhub.core.workspace import Workspace
from peerhub.extensions.bridge import Bridge
from peerhub.extensions.bridge_claims import ClaimStore


def _open(db: Path, ws: Workspace) -> dict[str, float]:
    start = time.perf_counter()
    store = CoreStore(db)
    core_done = time.perf_counter()
    claims = ClaimStore(db, ws.generation)
    claims_done = time.perf_counter()
    Bridge(store, claims)
    done = time.perf_counter()
    return {"core_ms": (core_done-start)*1000, "claims_ms": (claims_done-core_done)*1000,
            "bridge_ms": (done-claims_done)*1000, "total_ms": (done-start)*1000}


def _cookie(db: Path) -> int:
    with closing(sqlite3.connect(db)) as conn:
        return int(conn.execute("PRAGMA schema_version").fetchone()[0])


def benchmark(rounds: int = 5, workers: tuple[int, ...] = (1, 2, 4, 8)) -> dict[str, Any]:
    if type(rounds) is not int or not 1 <= rounds <= 20:
        raise ValueError("rounds must be an integer from 1 through 20")
    if not workers or any(type(n) is not int or not 1 <= n <= 8 for n in workers):
        raise ValueError("worker counts must be integers from 1 through 8")
    if len(set(workers)) != len(workers):
        raise ValueError("worker counts must be unique")
    rows: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="peerhub-init-bench-") as directory:
        for n in workers:
            root = Path(directory) / str(n)
            ws = Workspace(root)
            db = root / "core.db"
            cold = _open(db, ws)
            before = _cookie(db)
            samples: list[dict[str, float]] = []
            for _ in range(rounds):
                barrier = Barrier(n)
                def work(_index: int) -> dict[str, float]:
                    barrier.wait(timeout=30)
                    return _open(db, ws)
                with ThreadPoolExecutor(max_workers=n) as pool:
                    samples.extend(pool.map(work, range(n)))
            values = sorted(sample["total_ms"] for sample in samples)
            rows.append({"workers": n, "samples": len(samples), "cold_ms": cold,
                         "p50_ms": statistics.median(values),
                         "p95_ms": values[math.ceil(len(values)*0.95)-1],
                         "phase_p50_ms": {key: statistics.median(s[key] for s in samples)
                                          for key in samples[0]},
                         "schema_cookie_delta": _cookie(db)-before})
    sources = ("core/store.py", "extensions/bridge.py", "extensions/bridge_claims.py")
    product = Path(__file__).resolve().parents[1] / "peerhub"
    return {"schema_version": 1, "python": platform.python_version(), "os": platform.system(),
            "sqlite": sqlite3.sqlite_version, "provider_calls": 0, "rounds": rounds,
            "source_sha256": {name: hashlib.sha256((product/name).read_bytes()).hexdigest() for name in sources},
            "scope": "synthetic warm initialization; no provider, no user database; not a throughput SLA",
            "cases": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        report = benchmark(args.rounds)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"initialization benchmark failed: {type(exc).__name__}: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
