"""Tests for peerhub monitor command."""

import json
from unittest.mock import patch

import pytest

from peerhub.cli.app import main


def _db(tmp_path):
    """A real M1 database so the read-only dashboard renders for real (only refresh_quota is faked)."""
    from peerhub.core.store import CoreStore
    path = tmp_path / "m.db"
    CoreStore(path)
    return path


def _spy_render(calls):
    from peerhub.extensions.diag import ReadonlyDiag
    original = ReadonlyDiag.render

    def render(self, *a, **kw):
        calls.append("snapshot")
        return original(self, *a, **kw)
    return render


def _run(db, *args):
    try:
        return main(["--db", str(db), "monitor", *args])
    except SystemExit as exc:
        return exc.code


@pytest.mark.parametrize("args", [
    ["--interval", "0"],
    ["--interval", "-1"],
    ["--cycles", "-1"],
    ["--timeout", "0"],
    ["--collect-every", "0"],
])
def test_invalid_args_rejected_before_creation(tmp_path, capsys, args):
    calls = []
    def fake_refresh(*a, **kw):
        calls.append(kw)
        return {"status": "OK"}
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh):
        assert _run(tmp_path / "m.db", *args) == 2
    assert calls == []
    assert not (tmp_path / "m.db").exists()


def test_sleep_called_count_minus_1_times(tmp_path, capsys):
    calls = []
    sleeps = []
    def fake_refresh(*a, **kw):
        calls.append("refresh")
        return {"status": "OK", "observations": [], "warnings": []}
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.diag.ReadonlyDiag.render", _spy_render(calls)), \
         patch("time.sleep", lambda s: sleeps.append(s)):
        assert _run(_db(tmp_path), "--cycles", "3", "--interval", "42") == 0
    assert sleeps == [42.0, 42.0]
    assert calls == ["refresh", "snapshot", "refresh", "snapshot", "refresh", "snapshot"]


def test_collect_every_honoured(tmp_path, capsys):
    calls = []
    def fake_refresh(*a, **kw):
        calls.append("refresh")
        return {"status": "OK", "observations": [], "warnings": []}
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.diag.ReadonlyDiag.render", _spy_render(calls)), \
         patch("time.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--cycles", "4", "--collect-every", "2") == 0
    assert calls == ["refresh", "snapshot", "snapshot", "refresh", "snapshot", "snapshot"]


def test_guard_stop_after_first_violation(tmp_path, capsys):
    calls = []
    def fake_refresh(*a, **kw):
        calls.append("refresh")
        return {
            "status": "PARTIAL",
            "observations": [{"payload": {"evidence_ref": "agy_usage_consumed_tokens"}}],
            "warnings": []
        }
    
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.diag.ReadonlyDiag.render", _spy_render(calls)), \
         patch("time.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--cycles", "5") == 1
    assert calls == ["refresh"]
    err = capsys.readouterr().err
    assert "agy /usage consumed model tokens" in err


def test_guard_continue_with_flag(tmp_path, capsys):
    calls = []
    def fake_refresh(*a, **kw):
        calls.append("refresh")
        return {
            "status": "PARTIAL",
            "observations": [{"payload": {"evidence_ref": "agy_usage_consumed_tokens"}}],
            "warnings": []
        }
    
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.diag.ReadonlyDiag.render", _spy_render(calls)), \
         patch("time.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--cycles", "2", "--allow-agy-token-use") == 1
    assert calls == ["refresh", "snapshot", "refresh", "snapshot"]


def test_keyboard_interrupt_exit_0(tmp_path, capsys):
    calls = []

    def fake_refresh(*a, **kw):
        return {"status": "OK", "observations": [], "warnings": []}

    def sleep(*a):
        raise KeyboardInterrupt
    
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.diag.ReadonlyDiag.render", _spy_render(calls)), \
         patch("time.sleep", sleep):
        assert _run(_db(tmp_path), "--cycles", "0") == 0


def test_json_frames_parse_and_carry_stable_keys(tmp_path, capsys):
    calls = []

    def fake_refresh(*a, **kw):
        return {"status": "OK", "observations": [{"payload": {}}, {"payload": {}}], "warnings": ["w1"]}
    
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.diag.ReadonlyDiag.render", _spy_render(calls)), \
         patch("time.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--cycles", "2", "--collect-every", "2", "--json") == 0
    
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 2
    
    f0 = json.loads(lines[0])
    assert f0["cycle"] == 0
    assert set(f0["refresh"].keys()) == {"status", "warnings", "observations"}
    assert f0["refresh"]["observations"] == 2
    assert "snapshot" in f0
    
    f1 = json.loads(lines[1])
    assert f1["cycle"] == 1
    assert set(f1["refresh"].keys()) == {"skipped"}
    assert f1["refresh"]["skipped"] is True


def test_snapshot_step_performs_no_writes(tmp_path, capsys):
    import hashlib
    db_path = tmp_path / "db.sqlite"
    
    def fake_refresh(db, *a, **kw):
        from peerhub.core.store import CoreStore
        from peerhub.extensions.observation import ObservationStore
        CoreStore(db)
        ObservationStore(db)
        return {"status": "OK", "observations": [{"payload": {}}], "warnings": []}

    hashes = []
    def record_hash(*args):
        hashes.append(hashlib.sha256(db_path.read_bytes()).hexdigest())

    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("time.sleep", record_hash):
        assert _run(db_path, "--cycles", "3", "--collect-every", "2") == 0

    # cycle 0 = refresh+snapshot, cycle 1 = snapshot only, cycle 2 = refresh+snapshot: the two sleeps bracket the snapshot-only cycle
    assert len(hashes) == 2
    assert hashes[0] == hashes[1]
