from pathlib import Path

import pytest

from peerhub.cli import ask_cmd
from peerhub.core import load_limits
from peerhub.extensions import diag_running
from peerhub.extensions.liveness import PROCESSES_DEAD


@pytest.mark.parametrize("outcome", ["idle", "gone", "clears", "expires"])
@pytest.mark.parametrize("pressure", ["cpu", "ram"])
def test_ask_gate_waits_only_with_active_leases(monkeypatch, capsys, outcome, pressure):
    busy = (load_limits.ResourceSample(cpu_busy_pct=load_limits.CPU_BUSY_WARN_PCT) if pressure == "cpu"
            else load_limits.ResourceSample(ram_available_bytes=1))
    sleeps = []
    monkeypatch.setattr(ask_cmd, "ASK_LOAD_MAX_WAIT_SECONDS", 6)

    def read_resources():
        return load_limits.ResourceSample() if outcome == "clears" and sleeps else busy

    rows = [] if outcome == "idle" else [{"peer_id": "cx", "state": PROCESSES_DEAD if outcome == "gone" else "running"}]
    monkeypatch.setattr(diag_running, "running_report", lambda db, all_rows: {"rows": rows})

    ask_cmd._wait_for_ask_resources(Path("core.db"), resource_reader=read_resources,
                                    monotonic=lambda: sum(sleeps), sleep=sleeps.append)

    assert sleeps == {"idle": [], "gone": [], "clears": [3], "expires": [3, 3]}[outcome]
    stderr = capsys.readouterr().err.splitlines()
    assert sum(line.startswith("Waiting") for line in stderr) == (outcome in ("clears", "expires"))
    assert sum(line.startswith("Warning") for line in stderr) == (outcome != "clears")
