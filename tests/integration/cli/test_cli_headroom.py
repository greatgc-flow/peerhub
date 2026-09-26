"""`diag --headroom` honours the tiered surface and computes 24h reliability from real attempts."""
import json
import sqlite3
from pathlib import Path

import pytest

import peerhub.cli as cli_mod
from peerhub.cli import main


@pytest.fixture
def ws(tmp_path: Path, monkeypatch):
    assert main(["workspace", "init", "--workspace", str(tmp_path)]) == 0
    monkeypatch.setattr(cli_mod, "_refresh_usage_projections", lambda *a, **k: [])
    return tmp_path


def test_none_prints_nothing_basic_states_absence_full_shows_reliability(ws, capsys):
    assert main(["diag", "--headroom", "--headroom-surface", "none", "--workspace", str(ws)]) == 0
    assert capsys.readouterr().out == ""
    assert main(["diag", "--headroom", "--headroom-surface", "basic", "--workspace", str(ws)]) == 0
    assert "No telemetry data" in capsys.readouterr().out
    assert main(["diag", "--headroom", "--headroom-surface", "full", "--workspace", str(ws)]) == 0
    out = capsys.readouterr().out
    assert "24h reliability" in out and "cx.standard: fail rate: no definitive attempts" in out
    assert "cx.standard: 0 ok" not in out  # absence is never a fabricated zero


def test_full_counts_real_attempts_and_policy_default_is_basic(ws, capsys):
    import time
    db = ws / ".peerhub" / "peerhub.sqlite3"
    conn = sqlite3.connect(db)
    now = int(time.time())
    conn.execute("PRAGMA foreign_keys=OFF")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(dispatch_requests)")]
    special = {"command_id": "cmd-1", "selected_peer_instance_id": "cx", "selected_profile_id": "cx.standard",
               "state": "ADMITTED", "required_capability_tier": "READ_ONLY", "restore_quarantined": 0,
               "terminal_error_code": None, "revision": 1, "created_at": 0, "updated_at": 0}
    conn.execute(
        f"INSERT INTO dispatch_requests({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
        [special[c] if c in special else ("{}" if c.endswith("_json") else "x") for c in cols],
    )
    acols = [r[1] for r in conn.execute("PRAGMA table_info(dispatch_attempts)")]
    for i, state in enumerate(("SUCCEEDED_VERIFIED", "SUCCEEDED_VERIFIED", "FAILED")):
        vals = []
        for c in acols:
            vals.append({"attempt_id": f"a{i}", "command_id": "cmd-1", "attempt_number": i + 1,
                         "state": state, "execution_certainty": "TERMINAL", "revision": 1,
                         "reconciliation_complete": 1, "created_at": now, "updated_at": now,
                         "result_json": "{}", "lease_id": "lease-x"}.get(c))
        conn.execute(f"INSERT INTO dispatch_attempts({','.join(acols)}) VALUES({','.join('?' for _ in acols)})", vals)
    conn.commit()
    conn.close()
    assert main(["diag", "--headroom", "--headroom-surface", "full", "--json", "--workspace", str(ws)]) == 0
    data = json.loads(capsys.readouterr().out)
    r = data["reliability"]["cx/cx.standard"]
    assert (r["succeeded"], r["failed"]) == (2, 1) and r["fail_rate"] == pytest.approx(1 / 3)
    assert main(["diag", "--headroom", "--workspace", str(ws)]) == 0  # default surface: basic
    assert "24h reliability" not in capsys.readouterr().out
