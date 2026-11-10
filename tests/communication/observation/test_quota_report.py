"""tools/quota_report.py: per-pool quota utilization verdicts from sampled evidence (read-only)."""
import json
import sqlite3
from datetime import datetime, timezone

from tools.quota_report import (
    ACCURACY_NOTICE,
    CLASSES,
    EXHAUSTED,
    OPEN_NOTICE,
    backtest,
    build_report,
    main,
    read_samples,
    render_markdown,
)

H = 3600.0
D7 = 7 * 24 * H


def s(pool, t, left, reset, start=None, state="MEASURED"):
    return dict(pool=pool, state=state, measured_at=t, used_fraction=1 - left,
                window_start=start if start is not None else reset - (D7 if pool.endswith("7D") else 5 * H),
                resets_at=reset)


def verdicts(samples, now, since=None):
    return {(w["pool"], w["resets_at"]): w for w in build_report(samples, now=now, since=since)["closed_7d"]}


def test_exhausted_early_wins_and_reports_bounds():
    r = 10 * D7
    out = verdicts([s("quota:ag:A-7D", r - 60 * H, 0.2, r), s("quota:ag:A-7D", r - 50 * H, 0.0, r),
                    s("quota:ag:A-7D", r - 1 * H, 0.0, r)], now=r + H)
    w = out[("quota:ag:A-7D", r)]
    assert w["verdict"] == "EXHAUSTED_EARLY"
    assert w["first_zero_between"] == [r - 60 * H, r - 50 * H]
    assert w["observed_exhausted_hours"] == 49.0


def test_possibly_unused_used_and_unknown():
    r = 10 * D7
    out = verdicts([s("quota:cc:C-7D", r - 2 * H, 0.40, r),
                    s("quota:cx:X-7D", r - 3 * H, 0.10, r),
                    s("quota:ag:G-7D", r - 30 * H, 0.50, r)], now=r + H)
    assert out[("quota:cc:C-7D", r)]["verdict"] == "POSSIBLY_UNUSED"
    assert out[("quota:cc:C-7D", r)]["left_at_last_sample"] == 0.40
    assert out[("quota:cx:X-7D", r)]["verdict"] == "USED"
    assert out[("quota:ag:G-7D", r)]["verdict"] == "UNKNOWN"


def test_zero_close_to_reset_is_not_early():
    r = 10 * D7
    out = verdicts([s("quota:cc:F-7D", r - 6 * H, 0.0, r)], now=r + H)
    assert out[("quota:cc:F-7D", r)]["verdict"] == "USED"


def test_drifting_reset_anchors_are_one_window():
    r = 10 * D7
    out = verdicts([s("quota:cc:C-7D", r - 90 * H, 0.5, r - 0.6 * H), s("quota:cc:C-7D", r - 3 * H, 0.05, r)], now=r + H)
    assert len(out) == 1 and next(iter(out.values()))["samples"] == 2


def test_open_window_pace_and_staleness():
    r = 10 * D7
    now = r - D7 / 2  # half elapsed
    rep = build_report([s("quota:cx:X-7D", now - H, 0.18, r), s("quota:ag:G-7D", now - 30 * H, 0.9, r)], now=now)
    opened = {w["pool"]: w for w in rep["open_7d"]}
    assert opened["quota:cx:X-7D"]["pace_state"] == "fast" and round(opened["quota:cx:X-7D"]["pace_pp"]) == 33
    assert opened["quota:ag:G-7D"]["pace_state"] == "UNKNOWN" and "stale" in opened["quota:ag:G-7D"]["reason"]


def test_since_marks_window_incomplete_and_5h_counts():
    r = 10 * D7
    rep = build_report([s("quota:cc:C-7D", r - 2 * H, 0.1, r), s("quota:ag:G-5H", 100 * H, 0.0, 101 * H)],
                       now=r + H, since=r - 5 * H)
    assert rep["closed_7d"][0]["incomplete"] is True and rep["five_hour"] == {}
    rep = build_report([s("quota:ag:G-5H", 100 * H, 0.0, 101 * H), s("quota:ag:G-5H", 106 * H, 0.5, 107 * H)], now=r)
    assert rep["five_hour"]["quota:ag:G-5H"] == {"observed_windows": 2, "observed_exhausted_windows": 1}


def test_invalid_samples_are_ignored_and_markdown_has_thresholds():
    r = 10 * D7
    rep = build_report([s("quota:cc:C-7D", r - 2 * H, 0.1, r, state="STALE")], now=r + H)
    assert rep["closed_7d"] == [] and rep["invalid_samples"] == 1
    md = render_markdown(rep)
    assert "12h" in md and "15%" in md and "measured 0% left" in md and EXHAUSTED == 1.0


def test_open_window_already_exhausted_is_not_paced():
    r = 10 * D7
    now = r - 20 * H
    rep = build_report([s("quota:cc:F-7D", r - 40 * H, 0.0, r), s("quota:cc:F-7D", now - H, 0.0, r)], now=now)
    w = rep["open_7d"][0]
    assert w["pace_state"] == "EXHAUSTED_EARLY" and "40.0h before reset" in w["reason"] and "pace_pp" not in w


def test_open_window_exhausted_near_reset_is_used():
    r = 10 * D7
    rep = build_report([s("quota:cc:F-7D", r - 6 * H, 0.0, r)], now=r - 5 * H)
    assert rep["open_7d"][0]["pace_state"] == "USED"


def test_since_honours_an_explicit_offset(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    import tools.quota_report as qr

    seen: dict[str, float | None] = {}

    def fake_build(samples, *, now, since=None):
        seen["since"] = since
        return {}

    monkeypatch.setattr(qr, "read_samples", lambda db: [])
    monkeypatch.setattr(qr, "build_report", fake_build)
    monkeypatch.setattr(qr, "render_markdown", lambda report: "")
    db = tmp_path / "x.db"
    db.write_bytes(b"")
    assert main(["--db", str(db), "--since", "2026-11-10T09:00+09:00"]) == 0
    assert seen["since"] == datetime(2026, 11, 10, 0, 0, tzinfo=UTC).timestamp()


def test_slowly_drifting_resets_do_not_chain_into_one_window():
    r = 10 * D7
    step = 0.09 * D7  # each step is inside the 10% tolerance, the chain is not
    samples = [s("quota:cx:X-7D", r + k * step - 2 * H, 0.5, r + k * step) for k in range(4)]
    assert len(build_report(samples, now=r + 4 * D7)["closed_7d"]) == 2


def test_stale_open_window_is_unknown_even_after_exhaustion():
    r = 10 * D7
    now = r - 50 * H
    rep = build_report([s("quota:ag:3P-7D", now - 24 * H, 0.0, r)], now=now)
    assert rep["open_7d"][0]["pace_state"] == "UNKNOWN"


def test_positive_capacity_is_never_exhausted():
    r = 10 * D7
    out = verdicts([s("quota:ag:3P-7D", r - 60 * H, 0.003, r), s("quota:ag:3P-7D", r - 2 * H, 0.003, r)], now=r + H)
    assert out[("quota:ag:3P-7D", r)]["verdict"] == "USED"
    rep = build_report([s("quota:ag:3P-7D", r - 60 * H, 0.003, r)], now=r - 59 * H)
    assert rep["open_7d"][0]["pace_state"] in ("fast", "on-pace", "slow")


# --backtest: forecast classes against synthetic outcomes, never provider calls


def series(pool, used, times=(100, 200, 300, 400, 900), reset=1000):
    return [dict(pool=pool, state="MEASURED", used_fraction=u, measured_at=t,
                 window_start=reset - 1000, resets_at=reset,
                 anchor_basis="reset_minus_duration_inferred") for t, u in zip(times, used)]


def test_synthetic_windows_cover_all_outcomes_and_open_windows():
    samples = series("burst-then-cool", [.3, .4, .4, .4, .4])
    samples += series("exhausted", [.2, .4, .6, .8, 1])
    samples += series("missed", [.01, .01, .01, .01, 1])
    samples += series("expired", [.01, .02, .03, .04, .1])
    samples += series("open", [.2, .4, .6, .8, 1], times=(1100, 1200, 1300, 1400, 1500), reset=2000)
    # Idle provider windows move their reset anchor until first use.
    samples += [dict(pool="idle", state="MEASURED", used_fraction=0,
                     measured_at=t, resets_at=t + 1000, window_start=t) for t in range(100, 700, 100)]
    samples += series("quantised-1%", [.01] * 5, times=(79, 150, 200, 300, 900))
    samples += series("too-short", [.2] * 4)
    report = backtest(samples, now=1600)
    assert report["summary"] == {label: 1 for label in CLASSES}
    assert report["completed_exhausted_windows"] == 2
    assert report["open_windows"] == 1 and report["skipped_windows"] == 8
    assert OPEN_NOTICE in report["notices"]
    windows = {w["pool"]: w for w in report["windows"]}
    assert windows["missed"]["projection_states"] == {"insufficient": 4, "exhausted": 1}
    assert windows["expired"]["classification"] == "quiet&ok"
    assert windows["open"]["exhausted"] is None and not windows["open"]["scored"]


def test_no_completed_exhaustion_disclaims_accuracy_and_deduplicates_samples():
    samples = series("burst", [.3, .4, .4, .4, .4])
    report = backtest(samples + samples, now=1000)
    assert report["windows"][0]["samples"] == 5
    assert report["summary"]["threat&ok (FALSE ALARM)"] == 1
    assert ACCURACY_NOTICE in report["notices"]
    samples[0]["state"] = "ERROR"
    assert backtest(samples, now=1000)["invalid_samples"] == 1


def test_read_only_store_and_json_cli(tmp_path, monkeypatch, capsys):
    db = tmp_path / "quota.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE observations (capture_seq INTEGER PRIMARY KEY, resource_pool_ref TEXT, "
                     "state TEXT, payload_json TEXT, observed_at TEXT, kind TEXT)")
        for i, sample in enumerate(series("expired", [.01, .02, .03, .04, .1])):
            payload = dict(remaining_fraction=1 - sample["used_fraction"], window_started_at=0, resets_at=1000)
            observed = datetime.fromtimestamp(sample["measured_at"], timezone.utc).isoformat()
            conn.execute("INSERT INTO observations VALUES (?, ?, ?, ?, ?, 'quota')",
                         (i, sample["pool"], "MEASURED", json.dumps(payload), observed))
    before = db.read_bytes()
    connect = sqlite3.connect

    def tracked_connect(target, **kwargs):
        assert target.endswith("?mode=ro") and kwargs["uri"] is True
        conn = connect(target, **kwargs)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    assert len(read_samples(db)) == 5
    monkeypatch.setattr("tools.quota_report.time.time", lambda: 1000)
    assert main(["--db", str(db), "--backtest", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["quiet&ok"] == 1
    assert ACCURACY_NOTICE in report["notices"]
    assert db.read_bytes() == before
    missing = tmp_path / "absent" / "core.db"
    assert main(["--db", str(missing), "--backtest"]) == 5
    assert not missing.parent.exists()
