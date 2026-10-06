import sqlite3
from types import SimpleNamespace

import pytest

from peerhub.extensions.observation_model import EvidenceState
from peerhub.extensions.quota_capture import refresh_quota
from peerhub.core.store import CoreStore


def test_quota_refresh_and_readonly_dashboard(tmp_path, monkeypatch, capsys):
    from peerhub import cli
    db = tmp_path / "core.db"
    CoreStore(db)
    value = SimpleNamespace(quota_pool_scope="account:5h", remaining_fraction=0.75,
                            window_started_at=100, resets_at=18100)
    evidence = SimpleNamespace(state=EvidenceState.MEASURED, value=value, evidence_ref="probe:test",
                               source_tag="fake_usage", observed_at=100, captured_at=100)
    poller = lambda **kw: [SimpleNamespace(evidence=evidence)]
    result = refresh_quota(db, ["cx"], pollers={"cx": poller})
    assert result["observations"][0]["payload"]["remaining_fraction"] == 0.75
    with sqlite3.connect(db) as conn:
        before = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert not hasattr(cli, "legacy_main")
    monkeypatch.delenv("PEERHUB_CLI", raising=False)
    assert cli.main(["--db", str(db), "diag"]) == 0
    out = capsys.readouterr().out
    assert "headroom=75.0%" in out and "STALE" in out and "window=5h" in out
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == before


def test_probe_failure_persists_error_without_measurements(tmp_path):
    def fail(**kw):
        raise TimeoutError("secret provider error")
    result = refresh_quota(tmp_path / "core.db", ["cx"], pollers={"cx": fail})
    obs = result["observations"][0]
    assert result["status"] == "PARTIAL" and obs["state"] == "ERROR"
    assert "remaining_fraction" not in obs["payload"] and "secret" not in str(obs)


def test_diag_missing_db_does_not_create_it(tmp_path, capsys):
    from peerhub.cli import main
    db = tmp_path / "missing.db"
    assert main(["--db", str(db), "diag", "--json"]) == 5
    assert not db.exists()


def test_diag_fresh_is_not_a_mutating_compatibility_escape(tmp_path, monkeypatch):
    from peerhub import cli
    assert not hasattr(cli, "legacy_main")
    with pytest.raises(SystemExit) as exc:
        cli.main(["--db", str(tmp_path / "core.db"), "diag", "--fresh"])
    assert exc.value.code == 2
    assert not (tmp_path / "core.db").exists()


def test_first_refresh_bootstraps_a_usable_workspace(tmp_path, monkeypatch, capsys):
    from peerhub.cli.app import main
    from peerhub.extensions import quota_probes
    evidence = SimpleNamespace(state=EvidenceState.ABSENT, value=None, source_tag="fake", evidence_ref="test",
                               observed_at=100, captured_at=100)
    monkeypatch.setattr(quota_probes, "poll_codex_usage", lambda **kw: [SimpleNamespace(evidence=evidence)])
    db = tmp_path / ".peerhub" / "m1.db"
    assert main(["--db", str(db), "observation", "refresh", "--peers", "cx"]) == 0
    assert main(["--db", str(db), "diag"]) == 0
