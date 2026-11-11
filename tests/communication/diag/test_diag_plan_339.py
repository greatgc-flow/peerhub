"""#339: `peerhub diag --plan` - read-only per-pool spend/hold/rest/blocked from stored observations."""
from __future__ import annotations

import hashlib
import json
import socket
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.cli.view import plan_action, plan_data, render_plan
from peerhub.extensions.diag import DiagnosticReport, Section
from tests.communication.harness.observation import ObservationHarness

NOW = 1794134880.0  # 2026-11-08 10:48Z
DAY, HOUR = 86400.0, 3600.0


# --- action classification (pure) ----------------------------------------------------------------

@pytest.mark.parametrize("left,pace,forecast,action", [
    (None, None, "unknown", "blocked"),        # unknown / n/a evidence
    (0.0, 30.0, "exhausted", "blocked"),       # out
    (0.0, None, "unknown", "blocked"),         # 0% left even without a projection
    (55.0, -5.0, "expired", "blocked"),        # window already reset: left is not current
    (40.0, 2.0, "threat", "rest"),             # forecast exhausts before reset
    (40.0, -12.0, "threat", "rest"),           # forecast wins over a spend-sized PACE
    (40.0, 10.0001, "on_pace", "rest"),        # just above +10
    (40.0, 10.0, "on_pace", "hold"),           # boundary +10 is still hold
    (40.0, 0.0, "on_pace", "hold"),
    (40.0, -9.9999, "on_pace", "hold"),        # just above -10
    (40.0, -10.0, "on_pace", "spend"),         # boundary -10 is spend
    (90.0, -30.0, "insufficient", "spend"),
    (80.0, -20.0, "stale", "spend"),           # stale evidence keeps the action, reason says stale
    (40.0, None, "unknown", "hold"),           # left known, PACE unknown
])
def test_plan_action_branches_and_boundaries(left, pace, forecast, action):
    got, reason = plan_action(left, pace, forecast)
    assert got == action and reason
    if forecast == "stale":
        assert "stale" in reason


# --- report-level rows ---------------------------------------------------------------------------

def _item(peer, pool, remaining, *, span, elapsed, state="MEASURED", reason=None, age=10.0):
    measured = NOW - age
    start = measured - elapsed
    payload = {"remaining_fraction": remaining, "window_started_at": start, "resets_at": start + span}
    if reason is not None:
        payload = {"reason": reason}
    return {"kind": "quota", "subject_ref": peer, "resource_pool_ref": f"quota:{peer}:{pool}", "state": state,
            "age_seconds": age, "ttl_seconds": 300.0, "observed_at": datetime.fromtimestamp(measured, timezone.utc).isoformat(),
            "payload": payload}


def _report(*items):
    return DiagnosticReport("OK", NOW, {}, {"observations": Section("observations", "OK", {"items": list(items)}),
                                            "resource_pools": Section("resource_pools", "OK", {"pools": []})})


def _by_pool(report):
    return {(p["peer"], p["pool"]): p for p in plan_data(report, {"path": "x", "source": "explicit"})["pools"]}


def test_target_rate_is_left_over_hours_to_reset():
    # 84% left, 6d21h to reset -> 84 / 165 = 0.509 %/h (field example in #339)
    row = _by_pool(_report(_item("cc", "C-7D", .84, span=7 * DAY, elapsed=3 * HOUR - 10)))[("cc", "C-7D")]
    assert row["left_pct"] == pytest.approx(84.0)
    assert row["resets_in_seconds"] == pytest.approx(7 * DAY - 3 * HOUR)
    assert row["target_pct_per_hour"] == pytest.approx(84.0 / 165.0)
    assert row["binding"] == "C-7D"


def test_spare_5h_is_capped_by_its_7d_pool():
    # G-5H looks spare (84%, under pace) but G-7D runs over pace: both rows rest, binding G-7D.
    rows = _by_pool(_report(_item("ag", "G-5H", .84, span=5 * HOUR, elapsed=2 * HOUR),
                            _item("ag", "G-7D", .45, span=7 * DAY, elapsed=2.5 * DAY)))
    assert rows[("ag", "G-7D")]["pace_pt"] > 10
    assert rows[("ag", "G-5H")]["action"] == rows[("ag", "G-7D")]["action"] == "rest"
    assert rows[("ag", "G-5H")]["binding"] == rows[("ag", "G-7D")]["binding"] == "G-7D"
    assert "G-7D" in rows[("ag", "G-5H")]["reason"]
    assert rows[("ag", "G-5H")]["reason"].count("G-7D") == 1  # the decided window is not repeated as "; binding G-7D"


def test_empty_5h_gates_a_spare_7d():
    rows = _by_pool(_report(_item("ag", "G-5H", 0.0, span=5 * HOUR, elapsed=2 * HOUR),
                            _item("ag", "G-7D", .80, span=7 * DAY, elapsed=3.5 * DAY)))
    assert rows[("ag", "G-7D")]["action"] == rows[("ag", "G-5H")]["action"] == "blocked"
    assert rows[("ag", "G-7D")]["binding"] == "G-5H"
    assert "G-5H" in rows[("ag", "G-7D")]["reason"]


@pytest.mark.parametrize("state", ["UNKNOWN", "ABSENT"])
def test_unknown_or_na_5h_does_not_gate_its_7d(state):
    rows = _by_pool(_report(_item("cx", "X-5H", None, span=5 * HOUR, elapsed=HOUR, state=state),
                            _item("cx", "X-7D", .90, span=7 * DAY, elapsed=3.5 * DAY)))
    assert rows[("cx", "X-5H")]["action"] == "blocked"   # its own evidence is unknown
    assert rows[("cx", "X-7D")]["action"] == "spend" and rows[("cx", "X-7D")]["binding"] == "X-7D"


def test_expired_5h_blocks_only_its_own_row():
    # Stored 10 min ago, the 5H window reset 5 min ago: its 10% left is no longer current.
    rows = _by_pool(_report(_item("cx", "X-5H", .10, span=5 * HOUR, elapsed=5 * HOUR - 300, age=600.0),
                            _item("cx", "X-7D", .90, span=7 * DAY, elapsed=3.5 * DAY)))
    assert rows[("cx", "X-5H")]["forecast"] == "expired" and rows[("cx", "X-5H")]["action"] == "blocked"
    assert rows[("cx", "X-7D")]["action"] == "spend" and rows[("cx", "X-7D")]["binding"] == "X-7D"
    assert "X-5H" not in rows[("cx", "X-7D")]["reason"]


@pytest.mark.parametrize("reason,shown", [
    ("not reported by provider; plan=prolite", "n/a (plan omits)"),
    ("disabled by provider: 3P quota switched off", "n/a (disabled by provider)"),
])
def test_not_applicable_window_is_na_and_never_gates(reason, shown):
    rows = _by_pool(_report(_item("cx", "X-5H", None, span=5 * HOUR, elapsed=HOUR, state="ABSENT", reason=reason),
                            _item("cx", "X-7D", .90, span=7 * DAY, elapsed=3.5 * DAY)))
    na = rows[("cx", "X-5H")]
    assert na["action"] == "n/a" and na["reason"] == shown and "refresh" not in na["reason"]
    assert rows[("cx", "X-7D")]["action"] == "spend" and rows[("cx", "X-7D")]["binding"] == "X-7D"
    out = render_plan(_report(_item("cx", "X-5H", None, span=5 * HOUR, elapsed=HOUR, state="ABSENT", reason=reason)),
                      width=100, store_path="p", store_source="env")
    row = next(line for line in out.splitlines() if line.startswith("cx "))
    assert row.split()[2:7] == ["-", "-", "-", "-", "-"] and shown in row and "blocked" not in row


def test_aged_not_applicable_evidence_stays_blocked():
    # The monitor shows an aged n/a as "?" (needs refresh): the plan keeps it blocked.
    rows = _by_pool(_report(_item("cc", "C-5H", None, span=5 * HOUR, elapsed=HOUR, state="ABSENT",
                                  reason="not reported by provider; plan=unknown", age=600.0)))
    assert rows[("cc", "C-5H")]["action"] == "blocked"


def test_legend_fits_default_width():
    out = render_plan(_report(), width=100, store_path="p", store_source="default")
    assert all(word in out.splitlines()[-1] for word in ("spend", "hold", "rest", "blocked", "- n/a", "TARGET"))


def test_5h_never_drives_spend_over_a_holding_7d():
    rows = _by_pool(_report(_item("cc", "C-5H", .95, span=5 * HOUR, elapsed=4 * HOUR),
                            _item("cc", "C-7D", .50, span=7 * DAY, elapsed=3.5 * DAY)))
    assert rows[("cc", "C-5H")]["action"] == rows[("cc", "C-7D")]["action"] == "hold"


def test_unrelated_families_do_not_bind_each_other():
    rows = _by_pool(_report(_item("ag", "G-7D", .90, span=7 * DAY, elapsed=3.5 * DAY),
                            _item("ag", "A-7D", 0.0, span=7 * DAY, elapsed=3.5 * DAY)))
    assert rows[("ag", "G-7D")]["action"] == "spend" and rows[("ag", "A-7D")]["action"] == "blocked"


def test_json_shape():
    data = plan_data(_report(_item("cx", "X-7D", .15, span=7 * DAY, elapsed=4 * DAY)), {"path": "p", "source": "env"})
    assert set(data) == {"schema_version", "status", "read_at", "store_selection", "legend", "pools"}
    assert data["store_selection"] == {"path": "p", "source": "env"}
    (row,) = data["pools"]
    assert set(row) == {"peer", "pool", "resource_pool_ref", "left_pct", "resets_in_seconds", "target_pct_per_hour",
                        "pace_pt", "forecast", "binding", "action", "reason"}
    assert row["action"] in {"spend", "hold", "rest", "blocked"}
    json.dumps(data)


def test_plain_table_columns_and_legend():
    out = render_plan(_report(_item("cx", "X-7D", .15, span=7 * DAY, elapsed=4 * DAY)), width=120,
                      store_path="D:/w/.peerhub/core.db", store_source="explicit")
    lines = out.splitlines()
    head = next(line for line in lines if line.startswith("PEER"))
    assert head.split() == ["PEER", "POOL", "LEFT", "RESETS", "PACE", "TARGET%/h", "ACTION", "REASON"]
    assert "D:/w/.peerhub/core.db (explicit)" in lines[0]
    assert all(word in lines[-1] for word in ("spend", "hold", "rest", "blocked", "PACE"))
    assert all(len(line) <= 120 for line in lines)


# --- CLI: read-only, no refresh, no provider call -------------------------------------------------

def _seed(db: Path) -> None:
    now = time.time()
    rows = [("cc", "quota:cc:C-7D", .90, now - 3.5 * DAY, now + 3.5 * DAY),
            ("cx", "quota:cx:X-7D", .15, now - 4 * DAY, now + 3 * DAY)]
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 5))
    with closing(sqlite3.connect(db)) as c:
        for i, (peer, pool, rf, start, reset) in enumerate(rows):
            c.execute("INSERT INTO observations (observation_id, subject_ref, resource_pool_ref, kind, source, state, payload_json, "
                      "observed_at, captured_at, effective_at_us) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (f"o{i}", peer, pool, "quota", "test", "MEASURED",
                       json.dumps({"remaining_fraction": rf, "window_started_at": start, "resets_at": reset}), ts, ts, int(now * 1e6)))
        c.commit()


@pytest.fixture
def plan_store(tmp_path, monkeypatch):
    db = Path(ObservationHarness(tmp_path / "ws").db_path)
    _seed(db)

    def forbid(*_a, **_k):
        raise AssertionError("diag --plan must not refresh or call a provider")
    # Class-level guards: provider CLIs are subprocesses, provider APIs are sockets; refresh needs one of them.
    monkeypatch.setattr(subprocess.Popen, "__init__", forbid)
    monkeypatch.setattr(socket.socket, "connect", forbid)
    monkeypatch.setenv("COLUMNS", "200")
    return db


def test_cli_plan_json_is_read_only(plan_store, capsys):
    before = hashlib.sha256(plan_store.read_bytes()).hexdigest()
    assert main(["--db", str(plan_store), "diag", "--plan", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    rows = {p["pool"]: p for p in data["pools"]}
    assert rows["C-7D"]["action"] == "spend" and rows["X-7D"]["action"] in {"rest", "blocked"}
    assert data["store_selection"] == {"path": str(plan_store), "source": "explicit"}
    assert hashlib.sha256(plan_store.read_bytes()).hexdigest() == before


def test_cli_plan_table(plan_store, capsys):
    assert main(["--db", str(plan_store), "diag", "--plan"]) == 0
    out = capsys.readouterr().out
    assert "TARGET%/h" in out and "C-7D" in out and "spend" in out


@pytest.mark.parametrize("status,error,code", [("PARTIAL", None, 4), ("FAILED", "SchemaVersionError: v99", 6)])
def test_cli_plan_exit_codes(plan_store, capsys, monkeypatch, status, error, code):
    from peerhub.extensions.diag import ReadonlyDiag
    monkeypatch.setattr(ReadonlyDiag, "render", lambda self, sections, read_at=None: DiagnosticReport(status, NOW, {}, {}, error))
    assert main(["--db", str(plan_store), "diag", "--plan", "--json"]) == code
    assert json.loads(capsys.readouterr().out)["status"] == status
    assert main(["--db", str(plan_store), "diag", "--plan"]) == code


@pytest.mark.parametrize("extra", [["--live"], ["quota"]])
def test_cli_plan_rejects_live_and_subcommands(plan_store, extra):
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(plan_store), "diag", "--plan", *extra])
    assert exc.value.code == 2
