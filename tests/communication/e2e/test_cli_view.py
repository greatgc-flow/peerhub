"""Rich monitor view: literal-output checks on a real read-only snapshot."""
import pytest

from peerhub.cli.view import bar, human_duration, render_view, use_color


def _report(items, tmp_path):
    from peerhub.cli.app import main
    db = str(tmp_path / "v.db")
    assert main(["--db", db, "peer", "register", "--peer", "a"]) == 0
    from peerhub.extensions.diag import ReadonlyDiag
    return ReadonlyDiag(db).render(["peers", "streams", "resource_pools", "observations"])


def test_empty_report_is_all_clear_and_ascii_safe(tmp_path):
    out = render_view(_report([], tmp_path), width=64, unicode=False, tz=None)
    assert "QUOTA" in out and "all clear" in out
    assert all(ord(c) < 128 for c in out) and max(map(len, out.split("\n"))) <= 80


def test_same_report_renders_identically(tmp_path):
    r = _report([], tmp_path)
    assert render_view(r, width=80) == render_view(r, width=80)


def test_helpers():
    assert human_duration(3700) == "1h01m"
    assert len(bar(0.5, 10, True)) == 10
    assert use_color(True, {"NO_COLOR": "1", "FORCE_COLOR": "1"}) is False
    assert use_color(False, {"FORCE_COLOR": "1"}) is True
    assert use_color(False, {}) is False


@pytest.mark.parametrize("color", [False, True])
def test_untrusted_peer_pool_and_reason_cannot_emit_terminal_controls(color):
    from peerhub.cli.view import _Row, _quota_lines, _credit_lines, _activity_lines, _safe_text
    payload = "peer\x1b[2J\x00\x07\x85pool"
    assert _safe_text(payload) == "peer[2Jpool"
    item = {"subject_ref": payload, "resource_pool_ref": "quota:" + payload,
            "kind": "quota", "state": "ERROR", "payload": {"reason": payload}}
    alerts = []
    lines = _quota_lines([_Row(item, 0)], 100, color, False, alerts)
    lines += _credit_lines([{**item, "kind": "reset_credit"}], 0, color, False, alerts)
    lines += _activity_lines([{**item, "kind": "activity", "payload": {
        "status": payload, "certainty": payload}}], 3, color)
    text = "\n".join(lines + alerts)
    assert "\x1b[2J" not in text
    assert all(c not in text for c in ("\x00", "\x07", "\x85"))
    assert "peer[2Jpool" in text


# ---------------------------------------------------------------- per-peer ask history (activity section) and live ticks
def _asks(db, peer, statuses, took=10.0):
    import uuid
    from datetime import datetime, timezone

    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import EvidenceState, Observation

    store = ObservationStore(db)
    for i, st in enumerate(statuses):
        now = datetime.now(timezone.utc).isoformat()
        store.persist(Observation(observation_id=uuid.uuid4().hex, subject_ref=peer, kind="activity", source="session_bridge.ask",
                                  observed_at=now, captured_at=now, state=EvidenceState.MEASURED,
                                  payload={"operation_elapsed_seconds": took + i, "status": st, "certainty": "TERMINAL", "record_id": f"r{i}"}))


def _render(db):
    from peerhub.extensions.diag import ReadonlyDiag

    return ReadonlyDiag(db).render(["peers", "streams", "resource_pools", "observations", "activity"])


def test_activity_section_counts_are_mutually_exclusive_and_exact(tmp_path):
    from peerhub.cli.app import main as cli

    db = str(tmp_path / "a.db")
    assert cli(["--db", db, "peer", "register", "--peer", "ag"]) == 0
    _asks(db, "ag", ["delivered", "delivered", "recovered_terminal", "uncertain", "boom"], took=10.0)
    _asks(db, "cx", ["delivered"], took=3.0)
    peers = {p["peer"]: p for p in _render(db).sections["activity"].data["peers"]}
    ag = peers["ag"]
    assert (ag["asks"], ag["ok"], ag["uncertain"], ag["failed"]) == (5, 3, 1, 1)
    assert ag["ok"] + ag["uncertain"] + ag["failed"] == ag["asks"]
    assert ag["median_seconds"] == 12.0 and ag["last_status"] == "boom" and 0 <= ag["last_age_seconds"] < 60
    assert peers["cx"]["median_seconds"] == 3.0


def test_activity_window_keeps_only_the_newest_asks(tmp_path):
    from peerhub.cli.app import main as cli
    from peerhub.extensions.diag import ACTIVITY_WINDOW

    db = str(tmp_path / "w.db")
    cli(["--db", db, "peer", "register", "--peer", "ag"])
    _asks(db, "ag", ["boom"] * 5 + ["delivered"] * ACTIVITY_WINDOW)
    ag = _render(db).sections["activity"].data["peers"][0]
    assert ag["asks"] == ACTIVITY_WINDOW and ag["ok"] == ACTIVITY_WINDOW and ag["failed"] == 0  # older failures fall out of the window


def test_default_render_is_unchanged_activity_is_opt_in(tmp_path):
    from peerhub.cli.app import main as cli
    from peerhub.extensions.diag import ReadonlyDiag

    db = str(tmp_path / "d.db")
    cli(["--db", db, "peer", "register", "--peer", "ag"])
    assert list(ReadonlyDiag(db).render().sections) == ["peers", "streams", "resource_pools", "observations", "log"]


def test_view_shows_a_per_peer_asks_table_and_alerts_on_a_bad_last_ask(tmp_path):
    from peerhub.cli.app import main as cli

    db = str(tmp_path / "v.db")
    cli(["--db", db, "peer", "register", "--peer", "ag"])
    _asks(db, "ag", ["delivered"] * 9 + ["uncertain"], took=40.0)
    out = render_view(_render(db), width=100, unicode=False)
    assert "ASKS (newest 50 per peer)" in out
    row = next(line for line in out.splitlines() if line.strip().startswith("ag "))
    assert row.split()[:6] == ["ag", "10", "9", "1", "0", "90%"]
    assert "ag last ask uncertain" in out and "PACE = used% - window elapsed%" in out


def test_advance_report_moves_clock_and_ages_without_rereading(tmp_path):
    from peerhub.cli.app import main as cli
    from peerhub.cli.view import advance_report

    db = str(tmp_path / "t.db")
    cli(["--db", db, "peer", "register", "--peer", "ag"])
    _asks(db, "ag", ["delivered"])
    rep = _render(db)
    later = advance_report(rep, rep.read_at + 90)
    assert later.read_at == rep.read_at + 90
    assert later.sections["activity"].data["peers"][0]["last_age_seconds"] == pytest.approx(
        rep.sections["activity"].data["peers"][0]["last_age_seconds"] + 90)
    assert rep.sections["activity"].data["peers"][0]["last_age_seconds"] < 60  # the original snapshot is untouched
    assert advance_report(rep, rep.read_at - 5).read_at == rep.read_at  # never moves backwards


def test_tick_redraws_each_second_with_a_counting_down_next_refresh():
    from peerhub.cli import monitor

    clock = {"t": 0.0}
    sleeps, frames = [], []
    monitor.time.monotonic, real_mono = (lambda: clock["t"]), monitor.time.monotonic
    monitor.time.sleep, real_sleep = (lambda s: (sleeps.append(s), clock.__setitem__("t", clock["t"] + s))), monitor.time.sleep
    try:
        class Rep:
            read_at = 100.0

        monitor._tick(Rep(), lambda rep, nxt: frames.append((rep, nxt)), 3.0, 10.0, lambda rep, now: now)
    finally:
        monitor.time.monotonic, monitor.time.sleep = real_mono, real_sleep
    assert sleeps == [1.0, 1.0, 1.0] and [f[0] for f in frames] == [101.0, 102.0, 103.0]
    assert [f[1] for f in frames] == [9.0, 8.0, 7.0]


def test_error_rows_show_the_recorded_reason():
    from peerhub.cli.view import _Row, _badge

    row = _Row({"subject_ref": "cc", "kind": "quota", "state": "ERROR", "payload": {"reason": "claude_usage_timeout"}}, 0.0)
    assert _badge(row, False, False) == "x ERROR claude_usage_timeout"
    bare = _Row({"subject_ref": "cc", "kind": "quota", "state": "ERROR", "payload": {}}, 0.0)
    assert _badge(bare, False, False) == "x ERROR"


def test_dashboard_flags_are_honoured_or_rejected_never_silently_ignored(tmp_path, capsys):
    import json as _json

    from peerhub.cli.app import main as cli

    db = str(tmp_path / "f.db")
    cli(["--db", db, "peer", "register", "--peer", "a"])
    capsys.readouterr()
    assert cli(["--db", db, "diag", "--json", "quota"]) in (0, 5)  # parent --json is no longer lost
    assert _json.loads(capsys.readouterr().out)["schema_version"] == "1.0"
    for argv in (["diag", "--view", "rich", "health"], ["diag", "--json", "health"]):
        with pytest.raises(SystemExit) as exc:
            cli(["--db", db, *argv])
        assert exc.value.code == 2
    assert "diag" in capsys.readouterr().err


def test_diag_health_reports_how_the_store_was_chosen(tmp_path, capsys):
    import json as _json

    from peerhub.cli.app import main as cli

    db = tmp_path / "w.db"
    assert cli(["--db", str(db), "peer", "register", "--peer", "a"]) == 0
    capsys.readouterr()
    assert cli(["--db", str(db), "diag", "health"]) == 0
    assert _json.loads(capsys.readouterr().out)["store_selection"] == {"path": str(db), "source": "explicit"}


def test_render_view_shows_eta_projection_and_header_threat(tmp_path):
    import time
    import uuid

    from peerhub.cli.app import main as cli
    from peerhub.extensions.diag import ReadonlyDiag
    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import EvidenceState, Observation, ResourcePool

    db = str(tmp_path / "v_proj.db")
    assert cli(["--db", db, "peer", "register", "--peer", "cx"]) == 0
    assert cli(["--db", db, "peer", "register", "--peer", "cc"]) == 0

    obs_store = ObservationStore(db)
    obs_store.register_resource_pool(ResourcePool(resource_pool_id="pool-threat", provider="cx", kind="QUOTA"))
    obs_store.register_resource_pool(ResourcePool(resource_pool_id="pool-safe", provider="cc", kind="QUOTA"))

    t0 = 1000000.0
    meas_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0 + 7200.0))
    # pool-threat: 80% used in 2h of 5h window (exhausts in 2.5h)
    obs_store.persist(Observation(
        observation_id=uuid.uuid4().hex,
        subject_ref="cx",
        resource_pool_ref="pool-threat",
        kind="quota",
        source="cx-cli",
        observed_at=meas_iso,
        captured_at=meas_iso,
        state=EvidenceState.MEASURED,
        payload={"remaining_fraction": 0.20, "window_started_at": t0, "resets_at": t0 + 18000.0},
    ))
    # pool-safe: 10% used in 2h of 5h window
    obs_store.persist(Observation(
        observation_id=uuid.uuid4().hex,
        subject_ref="cc",
        resource_pool_ref="pool-safe",
        kind="quota",
        source="cc-cli",
        observed_at=meas_iso,
        captured_at=meas_iso,
        state=EvidenceState.MEASURED,
        payload={"remaining_fraction": 0.90, "window_started_at": t0, "resets_at": t0 + 18000.0},
    ))

    rep = ReadonlyDiag(db).render(["peers", "streams", "resource_pools", "observations"], read_at=t0 + 7200.0)
    out = render_view(rep, width=100, unicode=False, store_path=db, store_source="explicit")
    assert f"{db} (explicit)" in out
    assert "THREAT: cx/pool-threat exhausts ~" in out
    assert "before reset" in out
    assert "ETA: exhausts ~" in out
    assert "ETA: safe until reset" in out

