import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from functools import partial
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Barrier

import pytest

from peerhub.cli import ask_cmd
from peerhub.cli.app import main
from peerhub.core import load_limits
from peerhub.extensions import bridge_schema
from peerhub.extensions.bridge_claims import ClaimStore, ClaimToken, LaunchBusyError
from peerhub.extensions.bridge_ledger import BridgeLedger
from peerhub.extensions.caller import detect_caller
from tests.communication.fakes import FakeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness


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


def test_ask_cli_waits_for_an_active_claim_under_load(tmp_path, monkeypatch, capsys):
    harness = BridgeHarness(tmp_path / "ws")
    harness.create_peer({"peer_id": "cx", "adapter_ref": "cx"})
    harness.create_stream({"stream_id": "first", "members": ["cx"]})
    claims = ClaimStore(harness.db_path, generation=harness.ws.generation)
    first = claims.acquire("cx", "first", "first", 60, caller=detect_caller())

    target = FakeRuntimeTarget().script_deliver(("started", "second-exec"), ("terminal", {"response": "answer"}))
    monkeypatch.setattr("peerhub.extensions.ask.build_ask_target", lambda *a, **kw: target)
    monkeypatch.setattr("peerhub.cli.monitor_refresh.refresh_dispatch_quota", lambda *a, **kw: None)
    busy = load_limits.ResourceSample(cpu_busy_pct=load_limits.CPU_BUSY_WARN_PCT)
    monkeypatch.setattr(load_limits, "read_resources", lambda: busy)
    sleeps = []

    def finish_first(seconds):
        assert target.count("deliver") == 0
        sleeps.append(seconds)
        claims.release(first)

    monkeypatch.setattr(ask_cmd, "_wait_for_ask_resources",
                        partial(ask_cmd._wait_for_ask_resources, monotonic=lambda: sum(sleeps), sleep=finish_first))
    assert main(["--db", str(harness.db_path), "ask", "cx", "second prompt", "--stream", "second",
                 "--request-id", "second", "--json"]) == 0
    output = capsys.readouterr()
    assert sleeps == [ask_cmd.ASK_LOAD_POLL_SECONDS]
    assert "Waiting to start ask while other asks are active" in output.err
    result = json.loads(output.out)
    assert result["status"] == "delivered" and result["response"] == "answer"
    assert target.count("deliver") == 1


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
