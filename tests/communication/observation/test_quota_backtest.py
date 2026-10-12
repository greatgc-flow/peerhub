"""Backtest classification uses synthetic outcomes, never provider calls."""

import json
import sqlite3
from datetime import datetime, timezone

from tools.quota_backtest import ACCURACY_NOTICE, CLASSES, OPEN_NOTICE, backtest, main, read_samples


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
    monkeypatch.setattr("tools.quota_backtest.time.time", lambda: 1000)
    assert main(["--db", str(db), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["quiet&ok"] == 1
    assert ACCURACY_NOTICE in report["notices"]
    assert db.read_bytes() == before
    missing = tmp_path / "absent" / "core.db"
    assert main(["--db", str(missing)]) == 5
    assert not missing.parent.exists()
