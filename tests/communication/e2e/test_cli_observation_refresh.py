import json
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
    assert cli.main(["--db", str(db), "diag"]) == 0
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.split()[:2] == ["cx", "5h"])
    assert "25%" in row and ("expired" in row or "stale" in row)
    assert "FORECAST" in out and "Probes" in out
    assert "Threats: unknown" in out and "No threats" not in out
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == before


def test_fresh_quota_dashboard_row_is_not_stale(tmp_path, capsys):
    import time

    from peerhub import cli

    db = tmp_path / "core.db"
    CoreStore(db)
    now = time.time()
    value = SimpleNamespace(quota_pool_scope="account:5h", remaining_fraction=0.75,
                            window_started_at=now - 4500, resets_at=now + 13500)
    evidence = SimpleNamespace(state=EvidenceState.MEASURED, value=value, evidence_ref="probe:test",
                               source_tag="fake_usage", observed_at=now, captured_at=now)
    refresh_quota(db, ["cx"], pollers={"cx": lambda **kw: [SimpleNamespace(evidence=evidence)]})
    # Other peers can lack evidence: OK (0) and PARTIAL (4) both allow
    # checking the freshness of this measured row.
    assert cli.main(["--db", str(db), "diag"]) in (0, 4)
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.split()[:2] == ["cx", "5h"])
    assert "25%" in row and "stale" not in row


def test_probe_failure_persists_error_without_measurements(tmp_path):
    CoreStore(tmp_path / "core.db")
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


def test_default_refresh_in_non_repository_refuses_missing_store(tmp_path, monkeypatch, capsys):
    from peerhub.cli.app import main
    from peerhub.cli.store_select import StoreSelection
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PEERHUB_DB", raising=False)
    monkeypatch.setattr("peerhub.cli.app._select_db", lambda *a, **kw: StoreSelection(".peerhub/core.db", "default"))
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: pytest.fail("missing-store refresh must not probe"))
    assert main(["observation", "refresh", "--peers", "cx"]) == 5
    cap = capsys.readouterr()
    assert json.loads(cap.out)["status"] == "UNAVAILABLE"
    assert "database not found: .peerhub/core.db" in cap.err
    assert not (tmp_path / ".peerhub").exists()


def test_collector_missing_store_never_calls_poller(tmp_path):
    db = tmp_path / "missing" / "core.db"
    with pytest.raises(FileNotFoundError, match="database not found"):
        refresh_quota(db, ["cx"], pollers={"cx": lambda **kw: pytest.fail("probe")})
    assert not db.parent.exists()


def test_collector_does_not_recreate_store_removed_during_open(tmp_path, monkeypatch):
    from peerhub.extensions.observation import ObservationStore
    db = tmp_path / "core.db"
    CoreStore(db)

    def remove_before_open(path, **kwargs):
        db.unlink()
        return ObservationStore(path, **kwargs)

    monkeypatch.setattr("peerhub.extensions.quota_capture.ObservationStore", remove_before_open)
    with pytest.raises(sqlite3.OperationalError):
        refresh_quota(db, ["cx"], pollers={"cx": lambda **kw: pytest.fail("probe")})
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
    db = tmp_path / ".peerhub" / "core.db"
    assert main(["--db", str(db), "observation", "refresh", "--peers", "cx"]) == 0
    assert main(["--db", str(db), "diag"]) == 0
