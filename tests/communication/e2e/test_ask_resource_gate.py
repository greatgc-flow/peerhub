from pathlib import Path
from types import SimpleNamespace

import pytest

from peerhub.cli import ask_cmd
from peerhub.core import load_limits
from peerhub.extensions import diag_running


@pytest.mark.parametrize("outcome", ["idle", "clears", "expires"])
@pytest.mark.parametrize("pressure", ["cpu", "ram"])
def test_ask_gate_waits_only_with_active_leases(monkeypatch, capsys, outcome, pressure):
    busy = (load_limits.ResourceSample(cpu_busy_pct=load_limits.CPU_BUSY_WARN_PCT) if pressure == "cpu"
            else load_limits.ResourceSample(ram_available_bytes=1))
    sleeps = []
    monkeypatch.setattr(ask_cmd, "ASK_LOAD_MAX_WAIT_SECONDS", 6)
    monkeypatch.setattr(ask_cmd, "time", SimpleNamespace(monotonic=lambda: sum(sleeps), sleep=sleeps.append))
    monkeypatch.setattr(load_limits, "read_resources",
                        lambda: load_limits.ResourceSample() if outcome == "clears" and sleeps else busy)
    monkeypatch.setattr(diag_running, "running_report", lambda db, limit: {
        "rows": [] if outcome == "idle" else [{"peer_id": "cx", "relation": "other"}],
    })

    ask_cmd._wait_for_ask_resources(Path("core.db"))

    assert sleeps == {"idle": [], "clears": [3], "expires": [3, 3]}[outcome]
    stderr = capsys.readouterr().err.splitlines()
    assert sum(line.startswith("Waiting") for line in stderr) == (outcome != "idle")
    assert sum(line.startswith("Warning") for line in stderr) == (outcome != "clears")
