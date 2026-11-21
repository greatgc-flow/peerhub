import io
import json
import os
import sqlite3
from contextlib import closing, nullcontext
from pathlib import Path
from threading import Event

import pytest

from peerhub.cli import monitor, monitor_view, support
from peerhub.cli.app import main
from peerhub.extensions.diag_quota import format_quota_table
from peerhub.extensions.observation_model import QUOTA_MAX_AGE_SECONDS, QUOTA_NOTICE_AGE_SECONDS


def _reading(age, **fields):
    return {"peer_id": "cc", "kind": "quota", "pool_ref": "quota:cc:C-7D", "state": "MEASURED",
            "age_seconds": age, "ttl_seconds": 300, "measurements": {"remaining_fraction": .72}, **fields}


def _frame(items):
    return {"status": "OK", "read_at": 1_800_000_000,
            "groups": {"capacity": {"status": "OK", "data": {"observations": items}}}}


@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("width", [39, 100])
@pytest.mark.parametrize("age,notice", [
    (None, "quota: no reading"), (QUOTA_NOTICE_AGE_SECONDS, ""),
    (QUOTA_NOTICE_AGE_SECONDS + 1, "quota 1h old"), (5 * 3600, "quota 5h old"),
    (86400, "quota 1d old"), (8 * 86400, "quota 8d old"),
])
def test_stale_notice_has_the_same_position_and_wording(age, notice, view, width):
    payload = _frame([] if age is None else [_reading(age)])
    for expand in (False, True):
        rendered = monitor_view.render_grouped_frame(payload, view=view, width=width, height=12, expand=expand)
        rows = monitor_view._ANSI.sub("", rendered).splitlines()
        if notice:
            assert " ".join(rows[2:]).startswith(notice + " -> peerhub observation refresh")
        else:
            assert "quota 1h old" not in rendered


@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("width", [39, 100])
@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("age,show_note", [(180, True), (QUOTA_MAX_AGE_SECONDS - 1, True), (QUOTA_MAX_AGE_SECONDS, False)])
def test_timeout_note_uses_recent_measured_pools(age, show_note, view, width, expand):
    reading = _reading(age, state="STALE" if age >= 300 else "MEASURED", stored_state="MEASURED")
    timeout = _reading(20, pool_ref=None, state="ERROR", measurements=None, reason="claude_usage_timeout")
    text = monitor_view.render_grouped_frame(_frame([reading, timeout]), view=view, width=width, expand=expand)
    if show_note:
        assert text.count(f"cc probe timeout (value {age // 60}m old)") == 1
        assert "72%" in text and "cc probe error" not in text
    else:
        assert "cc probe timeout (value" not in text


def test_diag_quota_uses_the_same_notice():
    report = {"source_db": "db", "status": "OK", "overall": "UNKNOWN", "error": None,
              "pools": [], "reset_credits": []}
    assert format_quota_table(report).splitlines()[0] == "quota: no reading -> peerhub observation refresh"


@pytest.fixture
def quota_age_store(monkeypatch, weekly_age):
    from peerhub.extensions import observation
    from peerhub.extensions.observation_model import NOT_REPORTED_REASON, epoch_to_iso

    now = 1_800_000_000
    connect = sqlite3.connect
    with closing(connect(":memory:", isolation_level=None)) as store:
        store.executescript(observation._DDL)
        for trigger in observation._TRIGGERS:
            store.execute(trigger)
        for peer, family, offset, short_state in [
            ("cc", "C", 0, "ABSENT"), ("cx", "X", 30, "ERROR"), ("ag", "A", 60, None),
        ]:
            for window, age, state, payload in [
                ("7D", (weekly_age or 0) - offset, "MEASURED" if weekly_age is not None else None,
                 {"remaining_fraction": .72, "resets_at": now + 86400}),
                ("5H", 8 * 86400 + 3600, short_state,
                 {"reason": NOT_REPORTED_REASON} if short_state == "ABSENT" else {"condition": "provider_probe_error"}),
            ]:
                pool = f"quota:{peer}:{family}-{window}"
                store.execute("INSERT INTO resource_pools(resource_pool_id,provider,kind) VALUES (?, ?, 'QUOTA')", (pool, peer))
                if state is not None:
                    stamp = epoch_to_iso(now - age)
                    store.execute("INSERT INTO observations(observation_id,subject_ref,kind,resource_pool_ref,source,state,"
                                  "payload_json,observed_at,captured_at,effective_at_us) VALUES (?, ?, 'quota', ?, ?, ?, ?, ?, ?, ?)",
                                  (pool, peer, pool, peer, state, json.dumps(payload), stamp, stamp, (now - age) * 1_000_000))

        def open_snapshot(*args, **kwargs):
            conn = connect(":memory:", isolation_level=None)
            store.backup(conn)
            return conn

        monkeypatch.setattr(sqlite3, "connect", open_snapshot)
        yield now


@pytest.mark.parametrize("weekly_age, expected_age, expected_notice", [
    (3780, "1h03m", "quota 1h old"), (570, "9m", None), (None, "?", "quota: no reading"),
])
@pytest.mark.parametrize("groups", [["capacity"], ["coverage"]])
def test_monitor_quota_age_uses_measured_pool_readings(quota_age_store, groups, expected_age, expected_notice):
    from peerhub.extensions.monitor_read_model import render_monitor_frame

    snapshot = render_monitor_frame(__file__, groups, human=True, read_at=quota_age_store)
    for expand in (False, True):
        rows = monitor_view.render_grouped_frame(snapshot, width=100, expand=expand).splitlines()
        assert rows[1].endswith(f"quota age {expected_age}")
        if expected_notice is None:
            assert not rows[2].startswith("quota")
        else:
            assert rows[2] == f"{expected_notice} -> peerhub observation refresh"


@pytest.mark.parametrize("weekly_age, expected_notice", [
    (3780, "quota 1h old"), (570, None), (None, "quota: no reading"),
])
def test_diag_quota_age_uses_measured_pool_readings(quota_age_store, expected_notice):
    from peerhub.cli.view import render_view
    from peerhub.extensions.diag import ReadonlyDiag
    from peerhub.extensions.diag_quota import quota_report

    report = quota_report(__file__, read_at=quota_age_store)
    rows = format_quota_table(report).splitlines()
    snapshot = ReadonlyDiag(__file__).render(["resource_pools", "observations"], read_at=quota_age_store)
    live_rows = render_view(snapshot, show_quota_age=True).splitlines()
    if expected_notice is None:
        assert rows[0].startswith("quota/rate-limit evidence") and live_rows[1].startswith("Threats:")
    else:
        assert rows[0] == live_rows[1] == f"{expected_notice} -> peerhub observation refresh"
    assert any("UNKNOWN" in row and "(no measurement)" in row for row in rows)


def test_scoped_timeout_uses_history_without_rewriting_the_snapshot(monkeypatch):
    from peerhub.extensions import monitor_read_model, observation
    from peerhub.extensions.observation_model import epoch_to_iso

    now = 1_800_000_000
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(observation._DDL)
    for trigger in observation._TRIGGERS:
        conn.execute(trigger)
    pool = "quota:cc:C-7D"
    conn.execute("INSERT INTO resource_pools(resource_pool_id,provider,kind) VALUES (?, 'cc', 'QUOTA')", (pool,))
    for age, state, payload in [
        (420, "MEASURED", {"remaining_fraction": .72, "resets_at": now + 3600}),
        (20, "ERROR", {"condition": "provider_probe_error", "reason": "timeout"}),
    ]:
        conn.execute("INSERT INTO observations(observation_id, subject_ref, kind, resource_pool_ref, source, state, "
                     "payload_json, observed_at, captured_at, effective_at_us) VALUES (?, 'cc', 'quota', ?, 'cc', ?, ?, ?, ?, ?)",
                     (state, pool, state, json.dumps(payload), epoch_to_iso(now - age), epoch_to_iso(now - age), (now - age) * 1_000_000))

    monkeypatch.setattr(monitor_read_model.sqlite3, "connect", lambda *args, **kwargs: conn)
    snapshot = monitor_read_model.render_monitor_frame(__file__, ["capacity"], human=True, read_at=now)
    latest, = snapshot["groups"]["capacity"]["data"]["observations"]
    assert latest["state"] == "ERROR" and latest["measurements"] is None
    for expand in (False, True):
        text = monitor_view.render_grouped_frame(snapshot, width=100, expand=expand)
        assert "72%" in text and text.count("cc probe timeout (value 7m old)") == 1
    assert latest["state"] == "ERROR" and latest["measurements"] is None


class _Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("options,disabled,age,expected", [
    ([], False, None, True), ([], False, support.MONITOR_QUOTA_MAX_AGE_SECONDS + 1, True),
    ([], False, support.MONITOR_QUOTA_MAX_AGE_SECONDS, False),
    (["--no-probe"], False, None, False), ([], True, None, False),
])
def test_live_watch_default_and_freshness(monkeypatch, options, disabled, age, expected):
    monkeypatch.setattr("sys.stdout", _Terminal())
    monkeypatch.setattr("peerhub.cli.view.enable_vt", lambda: False)
    monkeypatch.setattr("peerhub.cli.store_select.report_missing_store", lambda *a, **kw: False)
    monkeypatch.setattr("peerhub.extensions.monitor_read_model.render_monitor_frame", lambda *a, **kw: _frame([]))
    monkeypatch.setattr(support, "quota_items", lambda *a, **kw: [] if age is None else [
        {"kind": "quota", "subject_ref": "cc", "age_seconds": age}])
    monkeypatch.setattr(support, "quota_refresh_lock", lambda db: nullcontext(True), raising=False)
    monkeypatch.delenv("PEERHUB_MONITOR_PROBE", raising=False)
    if disabled:
        monkeypatch.setenv("PEERHUB_MONITOR_PROBE", "off")
    calls = []
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda db, peers, **kw: calls.append((peers, kw)) or {"status": "OK"})
    assert main(["--db", __file__, "monitor", "--group", "capacity", "--peers", "cc", "--cycles", "1", *options]) == 0
    assert bool(calls) is expected


def test_dispatch_skips_when_guard_is_busy_or_peer_refreshed(monkeypatch):
    monkeypatch.setattr(support, "refresh_with_profile_pools", lambda *a: pytest.fail("unexpected refresh"))
    monkeypatch.setattr(support, "quota_refresh_lock", lambda db: nullcontext(False), raising=False)
    monkeypatch.setattr(support, "quota_items", lambda *a, **kw: [])
    support.refresh_dispatch_quota(Path(__file__), "cc")
    monkeypatch.setattr(support, "quota_refresh_lock", lambda db: nullcontext(True))
    monkeypatch.setattr(support, "quota_items", lambda *a, **kw: [{"subject_ref": "cc", "age_seconds": 60}])
    support.try_quota_refresh(__file__, ["cc"])


@pytest.mark.parametrize("failure", ["reader", "collector"])
def test_dispatch_refresh_failure_is_quiet(monkeypatch, capsys, failure):
    calls = []

    def fail(*args, **kwargs):
        calls.append(failure)
        raise ValueError("invalid quota profile configuration")

    monkeypatch.setattr(support, "quota_refresh_lock", lambda db: nullcontext(True))
    monkeypatch.setattr(support, "quota_items", fail if failure == "reader" else lambda *a, **kw: [])
    monkeypatch.setattr(support, "refresh_with_profile_pools", fail)
    support.refresh_dispatch_quota(Path(__file__), "cc")
    assert calls == [failure]
    assert capsys.readouterr() == ("", "")


def test_dispatch_does_not_wait_for_a_blocked_collector(monkeypatch):
    collecting, release, done = Event(), Event(), Event()

    def collect(*args, **kwargs):
        collecting.set()
        release.wait(5)
        done.set()

    monkeypatch.setattr(support, "quota_refresh_lock", lambda db: nullcontext(True))
    monkeypatch.setattr(support, "quota_items", lambda *a, **kw: [])
    monkeypatch.setattr(support, "refresh_with_profile_pools", collect)
    try:
        support.refresh_dispatch_quota(Path(__file__), "cc")
        assert collecting.wait(1) and not done.is_set()
    finally:
        release.set()
    assert done.wait(1)


def test_live_watch_rechecks_after_acquiring_guard(monkeypatch):
    from argparse import Namespace

    readings = iter([[], [{"subject_ref": "cc", "age_seconds": 30}]])
    monkeypatch.setattr(support, "quota_items", lambda *_: next(readings))
    monkeypatch.setattr(support, "quota_refresh_lock", lambda db: nullcontext(True))
    monkeypatch.setattr(support, "refresh_with_profile_pools", lambda *a, **kw: pytest.fail("duplicate refresh"))
    assert support.refresh_monitor_quota(Namespace(db=__file__, peers=["cc"])) is None


@pytest.mark.parametrize("command", [["monitor", "--snapshot", "--group", "capacity"],
                                     ["diag", "quota"], ["diag", "--live", "--cycles", "1"]])
def test_snapshot_and_diag_never_refresh_on_a_tty(monkeypatch, command):
    from peerhub.extensions.diag import DiagnosticReport, Section

    terminal = _Terminal()
    monkeypatch.setattr("sys.stdout", terminal)
    monkeypatch.setattr("peerhub.cli.view.enable_vt", lambda: False)
    monkeypatch.setattr("peerhub.cli.store_select.report_missing_store", lambda *a, **kw: False)
    monkeypatch.setattr("peerhub.extensions.monitor_read_model.render_monitor_frame", lambda *a, **kw: _frame([]))
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota", lambda *a, **kw: pytest.fail("refresh in reader"))
    monkeypatch.setattr(support, "quota_refresh_lock", lambda *a: pytest.fail("lock in reader"))
    rep = DiagnosticReport(status="OK", read_at=1_800_000_000, snapshot={}, sections={
        "observations": Section("observations", "OK", {"items": []}),
        "resource_pools": Section("resource_pools", "OK", {"pools": []}),
    })
    monkeypatch.setattr("peerhub.extensions.diag.ReadonlyDiag.render", lambda *a, **kw: rep)
    assert main(["--db", __file__, *command]) == 0
    assert "quota: no reading -> peerhub observation refresh" in terminal.getvalue()


def test_automatic_collection_retries_timed_out_peer_before_next_peer(monkeypatch):
    from peerhub.extensions import quota_capture
    from peerhub.extensions.quota_probes import _fail_closed
    from peerhub.extensions.quota_types import EvidenceState

    class Store:
        def __init__(self, *args, **kwargs):
            pass

        def persist(self, obs):
            pass

    monkeypatch.setattr(quota_capture, "ObservationStore", Store)
    monkeypatch.setattr(quota_capture, "_read_latest_facts_report", lambda store: None)
    calls = []

    def poll(ids, instance_id, profile_id, **options):
        calls.append(instance_id)
        timeout = instance_id == "cc"
        return [_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR if timeout else EvidenceState.ABSENT,
                             1_800_000_000, 300, peer=instance_id,
                             extra={"reason": "claude_usage_timeout"} if timeout else None)]

    result = quota_capture.refresh_quota(__file__, ["cc", "cx"], pollers={"cc": poll, "cx": poll},
                                         version_checker=lambda kind: None, retry_timeouts=True)
    assert calls == ["cc", "cc", "cx"]
    assert [item["state"] for item in result["observations"]] == ["ERROR", "ABSENT"]


def test_pid_lock_reaps_dead_owner_and_bounds_wait_for_live_owner(tmp_path, monkeypatch):
    from peerhub.extensions.batch import store_scratch_dir

    db = tmp_path / "custom.db"
    lock_file = store_scratch_dir(db) / "quota-refresh.lock"
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text(str(os.getpid()), encoding="ascii")
    os.utime(lock_file, (1_700_000_000, 1_700_000_000))
    monkeypatch.setattr(support, "QUOTA_LOCK_WAIT_SECONDS", .02)
    with support.quota_refresh_lock(db) as locked:
        assert not locked
    monkeypatch.setattr("psutil.pid_exists", lambda pid: pid == os.getpid())
    lock_file.write_text(str(os.getpid() + 1), encoding="ascii")
    with support.quota_refresh_lock(db) as locked:
        assert locked
        assert lock_file.read_text(encoding="ascii") == str(os.getpid())
        with support.quota_refresh_lock(db) as second:
            assert not second
    with support.quota_refresh_lock(db) as locked:
        assert locked
