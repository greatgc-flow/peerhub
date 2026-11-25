import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Barrier

import pytest

from peerhub.cli import ask_cmd
from peerhub.core import load_limits
from peerhub.extensions import bridge_schema
from peerhub.extensions.bridge_claims import ClaimStore, ClaimToken, LaunchBusyError
from peerhub.extensions.bridge_ledger import BridgeLedger
from peerhub.extensions.caller import detect_caller


@pytest.mark.parametrize("outcome", ["idle", "clears", "expires"])
@pytest.mark.parametrize("pressure", ["cpu", "ram"])
def test_ask_gate_waits_only_with_active_leases(monkeypatch, capsys, outcome, pressure):
    busy = (load_limits.ResourceSample(cpu_busy_pct=load_limits.CPU_BUSY_WARN_PCT) if pressure == "cpu"
            else load_limits.ResourceSample(ram_available_bytes=1))
    sleeps = []
    monkeypatch.setattr(ask_cmd, "ASK_LOAD_MAX_WAIT_SECONDS", 6)

    def read_resources():
        return load_limits.ResourceSample() if outcome == "clears" and sleeps else busy

    calls = []
    token = ClaimToken("workspace", 1, "owner", "cx", "stream")

    def acquire(under_pressure):
        calls.append(under_pressure)
        return None if under_pressure and outcome != "idle" else token

    assert ask_cmd._wait_for_ask_resources(acquire, resource_reader=read_resources,
                                          monotonic=lambda: sum(sleeps), sleep=sleeps.append) == token

    assert sleeps == {"idle": [], "clears": [3], "expires": [3, 3]}[outcome]
    assert calls == {"idle": [True], "clears": [True, False], "expires": [True, True, False]}[outcome]
    stderr = capsys.readouterr().err.splitlines()
    assert sum(line.startswith("Waiting") for line in stderr) == (outcome in ("clears", "expires"))
    assert sum(line.startswith("Warning") for line in stderr) == (outcome != "clears")


def test_two_busy_asks_on_distinct_streams_cannot_both_reserve_the_store():
    with NamedTemporaryFile(suffix=".db", delete=False) as file:
        db = Path(file.name)
    try:
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("CREATE TABLE streams (stream_id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO streams VALUES ('one'),('two')")
            conn.execute("CREATE TABLE peers (peer_id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO peers VALUES ('cx')")
            for stmt in bridge_schema._DDL:
                conn.execute(stmt)
        claims = ClaimStore(db, lambda: "workspace")
        identity = detect_caller()
        ready = Barrier(2, timeout=10)

        def reserve(stream):
            ready.wait()
            try:
                return claims.acquire("cx", stream, stream, 60, caller=identity, busy=BridgeLedger.has_active_asks)
            except LaunchBusyError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(reserve, ("one", "two"))
        assert (first is None) != (second is None)
        waiting_stream = "one" if first is None else "two"
        # After the bounded wait, the override can reserve while the winner is still active.
        assert claims.acquire("cx", waiting_stream, waiting_stream, 60, caller=identity).stream_id == waiting_stream
    finally:
        db.unlink()
