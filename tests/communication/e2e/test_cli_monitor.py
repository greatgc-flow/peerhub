"""Tests for peerhub monitor command."""

import json
from unittest.mock import patch

import pytest

from peerhub.cli.app import main


@pytest.mark.parametrize("command", [
    ["monitor", "--view", "rich"], ["monitor", "--view", "plain"], ["monitor", "--json"],
    ["diag", "--view", "rich"], ["diag", "--view", "plain"], ["diag", "--json"],
    ["diag", "quota"], ["diag", "quota", "--json"], ["diag", "health"],
])
def test_missing_store_refused_before_probes(tmp_path, capsys, command):
    db = tmp_path / "absent" / "core.db"
    with patch("peerhub.extensions.quota_capture.refresh_quota", side_effect=AssertionError("probe")):
        assert main(["--db", str(db), *command]) == 5
    cap = capsys.readouterr()
    message = f"database not found: {db}"
    assert message in cap.err
    assert f"Create one explicitly: peerhub --db {db} peer register --peer <id> --name <name>" in cap.err
    if "--json" in command or "health" in command:
        envelope = json.loads(cap.out)
        assert envelope["status"] == "UNAVAILABLE" and envelope["error"] == message
    assert not db.parent.exists()


@pytest.mark.parametrize("view", ["rich", "plain"])
@pytest.mark.parametrize("tty,vt", [(False, True), (True, False), (True, True)])
@pytest.mark.parametrize("ending", ["normal", "interrupt", "error"])
def test_terminal_capability_and_cleanup(tmp_path, monkeypatch, view, tty, vt, ending):
    import io
    from peerhub.cli import monitor

    class Output(io.StringIO):
        @property
        def encoding(self):
            return "utf-8"

        def isatty(self):
            return tty

    output = Output()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub.cli.view.enable_vt", lambda: vt)
    monkeypatch.setenv("NO_COLOR", "")
    clock = [0.0]
    monkeypatch.setattr(monitor, "monotonic", lambda: clock[0])

    def sleep(seconds):
        if ending == "interrupt":
            raise KeyboardInterrupt
        if ending == "error":
            raise RuntimeError("render loop failed")
        clock[0] += seconds

    monkeypatch.setattr(monitor, "sleep", sleep)
    calls = []
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: calls.append("refresh") or {"status": "OK"})
    code = _run(_db(tmp_path), "--view", view, "--cycles", "2", "--interval-seconds", "2.5")
    assert code == (1 if ending == "error" else 0)
    out = output.getvalue()
    assert " [H" not in out
    if tty and vt:
        assert "\x1b[H\x1b[2J" in out and "\x1b[K" in out and "\x1b[J" in out
        assert out.endswith("\x1b[0m\x1b[?25h") and "\x1b[?25l" in out
        assert "\x1b[31m" not in out
    else:
        assert "\x1b" not in out
    assert len(calls) == (2 if ending == "normal" else 1)
    assert out.count("PeerHub") >= (3 if ending == "normal" else 1)


def test_json_on_tty_emits_only_one_object_per_cycle(tmp_path, monkeypatch):
    import io

    class Output(io.StringIO):
        def isatty(self):
            return True

    output = Output()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub.cli.view.enable_vt", lambda: pytest.fail("JSON must not enable VT"))
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: None)
    refreshes = []
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: refreshes.append(1) or {"status": "OK"})
    assert _run(_db(tmp_path), "--json", "--cycles", "3", "--collect-every", "2") == 0
    lines = output.getvalue().splitlines()
    frames = [json.loads(line) for line in lines]
    assert len(lines) == 4 and len(refreshes) == 2
    assert all(frame["schema_version"] == "1.1" for frame in frames)
    assert frames[0]["phase"] == "initial" and frames[0]["refresh"]["status"] == "NOT_STARTED"
    assert [frame["cycle"] for frame in frames[1:]] == [0, 1, 2]
    assert all(frame["snapshot"]["schema_version"] == "1.0" for frame in frames)
    assert "\x1b" not in output.getvalue() and " [H" not in output.getvalue()


def test_monitor_emits_committed_snapshot_before_calling_provider(tmp_path, monkeypatch, capsys):
    events = []
    from peerhub.extensions.diag import ReadonlyDiag
    original = ReadonlyDiag.render

    def tracked(self, *args, **kwargs):
        events.append("snapshot")
        return original(self, *args, **kwargs)

    def refresh(*args, **kwargs):
        events.append("probe")
        return {"status": "OK", "observations": [], "warnings": []}

    with patch.object(ReadonlyDiag, "render", tracked), \
         patch("peerhub.extensions.quota_capture.refresh_quota", refresh), \
         patch("peerhub.cli.monitor.sleep", lambda seconds: None):
        assert _run(_db(tmp_path), "--json", "--cycles", "1") == 0
    assert events == ["snapshot", "probe", "snapshot"]
    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert frames[0]["phase"] == "initial" and frames[0]["refresh"]["status"] == "NOT_STARTED"
    assert frames[1]["phase"] == "complete"
    assert frames[0]["snapshot"].keys() == frames[1]["snapshot"].keys()


@pytest.mark.parametrize(("diag_status", "error", "refresh_status", "expected"), [
    ("PARTIAL", None, "OK", 4),
    ("FAILED", "SchemaVersionError: unsupported schema", "OK", 6),
    ("FAILED", "sqlite3.DatabaseError: damaged", "OK", 4),
    ("OK", None, "PARTIAL", 1),
])
def test_monitor_exit_code_reports_diagnostic_failure_before_refresh_status(
    tmp_path, monkeypatch, diag_status, error, refresh_status, expected
):
    from peerhub.extensions.diag import DiagnosticReport

    empty = DiagnosticReport(diag_status, 0.0, {}, {}, error)
    monkeypatch.setattr("peerhub.extensions.diag.ReadonlyDiag.render", lambda *a, **kw: empty)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: {"status": refresh_status, "observations": [], "warnings": []})
    assert _run(_db(tmp_path), "--json", "--cycles", "1") == expected


def test_monitor_skips_elapsed_monotonic_slots_after_slow_probe(tmp_path, monkeypatch):
    from peerhub.cli import monitor
    clock = [0.0]
    sleeps = []
    monkeypatch.setattr(monitor, "monotonic", lambda: clock[0])

    def slow_probe(*args, **kwargs):
        clock[0] += 8.0
        return {"status": "OK", "observations": [], "warnings": []}

    with patch("peerhub.extensions.quota_capture.refresh_quota", slow_probe), \
         patch("peerhub.cli.monitor.sleep", lambda seconds: sleeps.append(seconds)):
        assert _run(_db(tmp_path), "--cycles", "2", "--interval-seconds", "5") == 0
    assert sleeps == []


def test_render_exception_restores_cursor(tmp_path, monkeypatch):
    import io
    from peerhub.cli import view

    class Output(io.StringIO):
        def isatty(self):
            return True

    output = Output()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr(view, "enable_vt", lambda: True)
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: None)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota", lambda *a, **kw: {"status": "OK"})
    original = view.format_frame
    calls = []

    def render(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("render failed")
        return original(*args, **kwargs)

    # Use the snapshot loop to reach the second render without a real clock.
    monkeypatch.setattr(view, "format_frame", render)
    from peerhub.extensions.diag_watch import dashboard_snapshots
    monkeypatch.setattr("peerhub.extensions.diag_watch.dashboard_snapshots",
                        lambda path, **kw: dashboard_snapshots(path, **kw, sleep=lambda seconds: None))
    assert main(["--db", str(_db(tmp_path)), "diag", "--live", "--cycles", "2"]) == 1
    assert len(calls) == 2
    assert output.getvalue().endswith("\x1b[0m\x1b[?25h")


def _db(tmp_path):
    """A real M1 database so the read-only dashboard renders for real (only refresh_quota is faked)."""
    from peerhub.core.store import CoreStore
    from peerhub.extensions.observation import ObservationStore
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "m.db"
    CoreStore(path)
    ObservationStore(path)
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
    ["--interval-seconds", "0"],
    ["--interval-seconds", "-1"],
    ["--cycles", "-1"],
    ["--timeout-seconds", "0"],
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
         patch("peerhub.cli.monitor.sleep", lambda s: sleeps.append(s)):
        assert _run(_db(tmp_path), "--cycles", "3", "--interval-seconds", "42") == 0
    assert sleeps == pytest.approx([42.0, 42.0], abs=0.1)
    assert calls == ["snapshot", "refresh", "snapshot", "refresh", "snapshot", "refresh", "snapshot"]


def test_collect_every_honoured(tmp_path, capsys):
    calls = []
    def fake_refresh(*a, **kw):
        calls.append("refresh")
        return {"status": "OK", "observations": [], "warnings": []}
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.diag.ReadonlyDiag.render", _spy_render(calls)), \
         patch("peerhub.cli.monitor.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--cycles", "4", "--collect-every", "2") == 0
    assert calls == ["snapshot", "refresh", "snapshot", "snapshot", "refresh", "snapshot", "snapshot"]


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
         patch("peerhub.cli.monitor.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--cycles", "5") == 1
    assert calls == ["snapshot", "refresh"]
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
         patch("peerhub.cli.monitor.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--cycles", "2", "--allow-agy-token-use") == 1
    assert calls == ["snapshot", "refresh", "snapshot", "refresh", "snapshot"]


def test_keyboard_interrupt_exit_0(tmp_path, capsys):
    calls = []

    def fake_refresh(*a, **kw):
        return {"status": "OK", "observations": [], "warnings": []}

    def sleep(*a):
        raise KeyboardInterrupt
    
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.diag.ReadonlyDiag.render", _spy_render(calls)), \
         patch("peerhub.cli.monitor.sleep", sleep):
        assert _run(_db(tmp_path), "--cycles", "0") == 0


def test_json_frames_parse_and_carry_stable_keys(tmp_path, capsys):
    calls = []

    def fake_refresh(*a, **kw):
        return {"status": "OK", "observations": [{"payload": {}}, {"payload": {}}], "warnings": ["w1"]}
    
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.diag.ReadonlyDiag.render", _spy_render(calls)), \
         patch("peerhub.cli.monitor.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--cycles", "2", "--collect-every", "2", "--json") == 0
    
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 3
    initial = json.loads(lines[0])
    assert initial["phase"] == "initial" and initial["refresh"]["status"] == "NOT_STARTED"
    f0 = json.loads(lines[1])
    assert f0["cycle"] == 0
    assert set(f0["refresh"].keys()) == {"status", "warnings", "observations"}
    assert f0["refresh"]["observations"] == 2
    assert "snapshot" in f0
    assert f0["snapshot"]["threats"]["state"] == "unknown"
    assert f0["snapshot"]["threats"]["fresh_pools"] == 0
    
    f1 = json.loads(lines[2])
    assert f1["cycle"] == 1
    assert set(f1["refresh"].keys()) == {"skipped"}
    assert f1["refresh"]["skipped"] is True


def test_snapshot_step_performs_no_writes(tmp_path, capsys):
    import hashlib
    from peerhub.core.store import CoreStore
    db_path = tmp_path / "db.sqlite"
    CoreStore(db_path)
    
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
         patch("peerhub.cli.monitor.sleep", record_hash):
        assert _run(db_path, "--cycles", "3", "--collect-every", "2") == 0

    # cycle 0 = refresh+snapshot, cycle 1 = snapshot only, cycle 2 = refresh+snapshot: the two sleeps bracket the snapshot-only cycle
    assert len(hashes) == 2
    assert hashes[0] == hashes[1]


def test_monitor_forwards_system_dir(tmp_path):
    from pathlib import Path
    calls = []

    def fake_refresh(db, peers, sys_dir=None, deadline_sec=15.0):
        calls.append(sys_dir)
        return {"status": "OK", "observations": [], "warnings": []}

    sys_path = tmp_path / "custom_sys"
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh):
        assert _run(_db(tmp_path), "--cycles", "1", "--system-dir", str(sys_path)) == 0

    assert calls == [Path(sys_path)]


def test_monitor_json_carries_projection_per_item(tmp_path, capsys):
    import time
    import uuid
    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import EvidenceState, Observation, ResourcePool

    db = _db(tmp_path)
    obs_store = ObservationStore(db)
    obs_store.register_resource_pool(ResourcePool(resource_pool_id="pool-1", provider="cx", kind="QUOTA"))
    t_meas = time.time() - 10.0
    meas_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_meas))
    start_t = t_meas - 7200.0
    reset_t = t_meas + 10800.0
    obs_store.persist(Observation(
        observation_id=uuid.uuid4().hex,
        subject_ref="cx",
        resource_pool_ref="pool-1",
        kind="quota",
        source="cx-cli",
        observed_at=meas_iso,
        captured_at=meas_iso,
        state=EvidenceState.MEASURED,
        payload={"remaining_fraction": 0.20, "window_started_at": start_t, "resets_at": reset_t},
    ))

    def fake_refresh(db, *a, **kw):
        return {"status": "OK", "observations": [], "warnings": []}

    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh):
        assert _run(db, "--cycles", "1", "--json") == 0

    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if l.strip() and json.loads(l).get("phase") == "complete")
    frame = json.loads(line)
    assert frame["store_selection"] == {"path": str(db), "source": "explicit"}
    items = frame["snapshot"]["sections"]["observations"]["data"]["items"]
    assert len(items) >= 1
    item = next(i for i in items if i.get("resource_pool_ref") == "pool-1")
    assert "projection" in item
    projection = item["projection"]
    assert set(projection) == {"pace_pt", "projected_at_reset", "state", "exhaustion_at", "measured_at", "anchor_basis", "reason"}
    assert projection["state"] == "threat"
    assert projection["anchor_basis"] == "reset_minus_duration_inferred"
    assert projection["exhaustion_at"] < reset_t
    assert projection["pace_pt"] == pytest.approx(40, abs=0.01)


def test_monitor_json_privacy_scrubs_sentinel_ids_and_payloads(tmp_path, capsys):
    import hashlib
    import time
    import uuid
    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import EvidenceState, Observation, ResourcePool

    sentinel_credit_id = "RateLimitResetCredit_SECRET_TOKEN_SENTINEL_MONITOR_888"
    sentinel_id_hash = hashlib.sha256(sentinel_credit_id.encode()).hexdigest()[:12]
    sentinel_credit_secret = "SENTINEL_MONITOR_CREDIT_SECRET"
    sentinel_activity_secret = "SENTINEL_MONITOR_ACTIVITY_SECRET"
    sentinel_quota_secret = "SENTINEL_MONITOR_QUOTA_SECRET"

    db = _db(tmp_path / "mon_privacy")
    obs_store = ObservationStore(db)
    obs_store.register_resource_pool(ResourcePool(resource_pool_id="credit:cx:reset", provider="cx", kind="ACCOUNT"))
    obs_store.register_resource_pool(ResourcePool(
        resource_pool_id="pool-1", provider="cx", kind="QUOTA",
        metadata={"private_detail": "SENTINEL_MONITOR_POOL_METADATA_LEAK"},
    ))
    now = time.time()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    obs_store.persist(Observation(
        observation_id=uuid.uuid4().hex,
        subject_ref="cx",
        resource_pool_ref="credit:cx:reset",
        kind="reset_credit",
        source="codex_app_server",
        observed_at=now_iso,
        captured_at=now_iso,
        state=EvidenceState.MEASURED,
        payload={
            "evidence_ref": "probe:cx:reset-credits",
            "available_count": 1,
            "nearest_expires_at": now + 7200,
            "leak": sentinel_credit_secret,
            "credits": [{
                "id_ref": sentinel_credit_id,
                "reset_type": "codexRateLimits",
                "status": "available",
                "granted_at": now - 3600,
                "expires_at": now + 7200,
                "title": "Weekly reset",
            }],
        },
    ))
    obs_store.persist(Observation(
        observation_id=uuid.uuid4().hex,
        subject_ref="cx",
        resource_pool_ref=None,
        kind="activity",
        source="session_bridge.ask",
        observed_at=now_iso,
        captured_at=now_iso,
        state=EvidenceState.MEASURED,
        payload={
            "status": "delivered",
            "operation_elapsed_seconds": 2.5,
            "prompt_text": sentinel_activity_secret,
            "reason": {"private_detail": "SENTINEL_MONITOR_NESTED_ACTIVITY_LEAK"},
        },
    ))
    obs_store.persist(Observation(
        observation_id=uuid.uuid4().hex,
        subject_ref="cx",
        resource_pool_ref="pool-1",
        kind="quota",
        source="cx-cli",
        observed_at=now_iso,
        captured_at=now_iso,
        state=EvidenceState.MEASURED,
        payload={
            "remaining_fraction": 0.5,
            "window_started_at": now - 3600,
            "resets_at": now + 14400,
            "internal_probe_details": sentinel_quota_secret,
        },
    ))

    with patch("peerhub.extensions.quota_capture.refresh_quota", lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []}):
        assert _run(db, "--cycles", "1", "--json") == 0

    raw_out = capsys.readouterr().out
    assert sentinel_credit_id not in raw_out
    assert sentinel_credit_secret not in raw_out
    assert sentinel_activity_secret not in raw_out
    assert sentinel_quota_secret not in raw_out
    assert "SENTINEL" not in raw_out
    assert sentinel_id_hash in raw_out
    completed = next(json.loads(line) for line in raw_out.splitlines() if json.loads(line).get("phase") == "complete")
    items = completed["snapshot"]["sections"]["observations"]["data"]["items"]
    by_kind = {item["kind"]: item for item in items}
    assert set(by_kind) == {"reset_credit", "activity", "quota"}
    assert by_kind["reset_credit"]["payload"]["credits"][0]["id_hash"] == sentinel_id_hash
    assert by_kind["activity"]["payload"] == {"status": "delivered", "operation_elapsed_seconds": 2.5}
    assert by_kind["quota"]["payload"]["remaining_fraction"] == 0.5
