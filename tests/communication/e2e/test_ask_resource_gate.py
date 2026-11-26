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


def test_ask_gate_warns_on_cpu_and_proceeds_immediately(capsys):
    token = ClaimToken("workspace", 1, "owner", "cx", "stream")
    calls = []
    result = ask_cmd._wait_for_ask_resources(
        lambda under_pressure: calls.append(under_pressure) or token,
        resource_reader=lambda: load_limits.ResourceSample(cpu_busy_pct=load_limits.CPU_BUSY_WARN_PCT),
    )
    assert result == token and calls == [False]
    assert "CPU load 80.0%" in capsys.readouterr().err


def test_ask_gate_keeps_bounded_ram_protection(capsys):
    token = ClaimToken("workspace", 1, "owner", "cx", "stream")
    calls = []
    sleeps = []

    def acquire(under_pressure):
        calls.append(under_pressure)
        return None if under_pressure else token

    samples = iter([
        load_limits.ResourceSample(cpu_busy_pct=load_limits.CPU_BUSY_WARN_PCT, ram_available_bytes=1),
        load_limits.ResourceSample(ram_available_bytes=1),
    ])
    assert ask_cmd._wait_for_ask_resources(
        acquire,
        resource_reader=lambda: next(samples, load_limits.ResourceSample(ram_available_bytes=1)),
        monotonic=lambda: sum(sleeps),
        sleep=sleeps.append,
    ) == token
    assert sleeps and sum(sleeps) == ask_cmd.ASK_RESOURCE_WAIT_SECONDS
    assert calls == [True] * (len(sleeps) + 1) + [False]
    err = capsys.readouterr().err
    assert "CPU load 80.0%" in err and "free RAM" in err and "waiting up to 120s" in err


def test_ask_cli_waits_for_an_active_claim_under_load(tmp_path, monkeypatch, capsys):
    harness = BridgeHarness(tmp_path / "ws")
    harness.create_peer({"peer_id": "cx", "adapter_ref": "cx"})
    harness.create_stream({"stream_id": "first", "members": ["cx"]})
    claims = ClaimStore(harness.db_path, generation=harness.ws.generation)
    first = claims.acquire("cx", "first", "first", 60, caller=detect_caller())

    target = (FakeRuntimeTarget()
              .script_deliver(("started", "cpu-exec"), ("terminal", {"response": "cpu-answer"}))
              .script_deliver(("started", "second-exec"), ("terminal", {"response": "answer"})))
    monkeypatch.setattr("peerhub.extensions.ask.build_ask_target", lambda *a, **kw: target)
    monkeypatch.setattr("peerhub.cli.monitor_refresh.refresh_dispatch_quota", lambda *a, **kw: None)
    busy = load_limits.ResourceSample(cpu_busy_pct=load_limits.CPU_BUSY_WARN_PCT)
    monkeypatch.setattr(load_limits, "read_resources", lambda: busy)
    sleeps = []

    def finish_first(seconds):
        assert target.count("deliver") == 1
        sleeps.append(seconds)
        claims.release(first)

    monkeypatch.setattr(ask_cmd, "_wait_for_ask_resources",
                        partial(ask_cmd._wait_for_ask_resources, monotonic=lambda: sum(sleeps), sleep=finish_first))
    assert main(["--db", str(harness.db_path), "ask", "cx", "cpu prompt", "--stream", "cpu",
                 "--request-id", "cpu", "--json"]) == 0
    cpu_output = capsys.readouterr()
    assert sleeps == []
    assert "CPU load" in cpu_output.err
    assert json.loads(cpu_output.out)["response"] == "cpu-answer"

    busy = load_limits.ResourceSample(cpu_busy_pct=load_limits.CPU_BUSY_WARN_PCT, ram_available_bytes=1)
    assert main(["--db", str(harness.db_path), "ask", "cx", "second prompt", "--stream", "second",
                 "--request-id", "second", "--json"]) == 0
    output = capsys.readouterr()
    assert sleeps == [ask_cmd.ASK_RESOURCE_POLL_SECONDS]
    assert "CPU load" in output.err and "free RAM" in output.err
    result = json.loads(output.out)
    assert result["status"] == "delivered" and result["response"] == "answer"
    assert target.count("deliver") == 2


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
            busy = BridgeLedger.launch_check(claims)
            ready.wait()
            try:
                return claims.acquire("cx", stream, stream, 60, caller=identity, busy=busy)
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
