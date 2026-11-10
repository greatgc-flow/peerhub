"""tools/quota_report.py: per-pool quota utilization verdicts from sampled evidence (read-only)."""
from tools.quota_report import EXHAUSTED, build_report, main, render_markdown

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
    assert "12h" in md and "15%" in md and str(EXHAUSTED) in md


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
    import tools.quota_report as qr

    seen = {}
    monkeypatch.setattr(qr, "read_samples", lambda db: [])
    monkeypatch.setattr(qr, "build_report", lambda samples, now, since: seen.setdefault("since", since) and {} or {})
    monkeypatch.setattr(qr, "render_markdown", lambda report: "")
    db = tmp_path / "x.db"
    db.write_bytes(b"")
    main(["--db", str(db), "--since", "2026-11-10T09:00+09:00"])
    assert seen["since"] == main.__globals__["datetime"](2026, 11, 10, 0, 0, tzinfo=main.__globals__["UTC"]).timestamp()
