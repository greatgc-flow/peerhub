"""Cycle defaults follow stdout TTY status for every monitor watch format."""
from __future__ import annotations

import json
import sqlite3
import sys

import pytest

from peerhub.cli.app import build_parser, main
from peerhub.core.store import CoreStore
from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.diag import DiagnosticReport
from peerhub.core.peer_kinds import QUOTA_CAPABLE_PEERS


@pytest.mark.parametrize("options", [[], ["--json"], ["--json", "--schema-version", "1.1"]])
def test_redirected_watch_requires_probe_opt_in(monkeypatch, capsys, options):
    calls = []
    import peerhub.cli.monitor_schema11  # import first: a lazy import under the patch would keep the fake name for good

    monkeypatch.setattr("peerhub.cli.store_select.report_missing_store", lambda *a, **kw: False)
    monkeypatch.setattr("peerhub.cli.monitor_schema11.report_missing_store", lambda *a, **kw: False)
    monkeypatch.setattr("peerhub.extensions.monitor_read_model.render_monitor_frame",
                        lambda *a, **kw: {"schema_version": "2.0", "status": "OK", "groups": {}})
    monkeypatch.setattr("peerhub.extensions.diag.ReadonlyDiag.render",
                        lambda *a, **kw: DiagnosticReport(status="OK", read_at=0, snapshot={}, sections={}))

    def refresh(db, peers, **kwargs):
        calls.append(peers)
        return {"status": "OK", "warnings": [], "observations": []}

    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota", refresh)
    argv = ["--db", "unused.db", "monitor", "--cycles", "1", *options]
    assert main(argv) == 0
    assert not calls and not capsys.readouterr().err
    assert main([*argv, "--probe"]) == 0
    assert calls == [list(QUOTA_CAPABLE_PEERS)]


def test_snapshot_rejects_probe(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--db", "unused.db", "monitor", "--snapshot", "--probe"])
    assert exc.value.code == 2
    assert "--snapshot cannot be combined with --probe" in capsys.readouterr().err


@pytest.mark.parametrize("options,tty,cycles,expected", [
    (options, tty, cycles, expected)
    for options in ([], ["--json"], ["--json", "--schema-version", "1.1"])
    for tty, cycles, expected in ((False, None, 1), (True, None, 2), (False, 0, 2), (True, 0, 2), (True, 1, 1))
    if options or not tty or cycles == 1  # Continuous human TTY cycles are covered by test_monitor_live_refresh.
])
def test_scripted_monitor_cycle_defaults_and_explicit_zero(tmp_path, monkeypatch, capsys, options, tty, cycles, expected):
    db = tmp_path / "core.db"
    CoreStore(db)
    ObservationStore(db)
    refreshes = []
    sleeps = []
    now = [0.0]

    def refresh(*args, **kwargs):
        refreshes.append(True)
        return {"status": "OK", "observations": [], "warnings": []}

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(sys.stdout, "isatty", lambda: tty)
    # Keep rendering append-only so these tests measure collection cycles, not VT redraws.
    monkeypatch.setattr("peerhub.cli.view.enable_vt", lambda: False)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota", refresh)
    monkeypatch.setattr("peerhub.cli.monitor.sleep", sleep)
    monkeypatch.setattr("peerhub.cli.monitor.monotonic", lambda: now[0])
    argv = ["--db", str(db), "monitor", "--probe", "--view", "plain", *options]
    if cycles is not None:
        argv.extend(["--cycles", str(cycles)])
    assert main(argv) in (0, 4)
    assert len(refreshes) == expected
    assert len(sleeps) == (0 if expected == 1 else 2)
    output = capsys.readouterr().out
    if "--json" in options:
        frames = [json.loads(line) for line in output.splitlines()]
        assert [frame["phase"] for frame in frames] == ["initial"] + ["complete"] * expected
        assert [frame["cycle"] for frame in frames] == [0] + list(range(expected))
    else:
        assert output.count("PeerHub Monitor") == expected + int(tty)


@pytest.mark.parametrize("failed", [False, True])
def test_non_tty_human_prints_only_completed_frame_and_keeps_diagnostics(tmp_path, monkeypatch, capsys, failed):
    db = tmp_path / "core.db"
    CoreStore(db)
    ObservationStore(db)
    calls = []

    def refresh(*args, **kwargs):
        calls.append("refresh")
        return {"status": "ERROR" if failed else "OK", "observations": [],
                "warnings": ["probe failed"] if failed else []}

    def render(*args, **kwargs):
        calls.append("snapshot")
        return {"schema_version": "2.0", "status": "FAILED" if failed else "OK",
                "groups": {"capacity": {"status": "ERROR" if failed else "OK", "data": {}}}}

    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota", refresh)
    monkeypatch.setattr("peerhub.extensions.monitor_read_model.render_monitor_frame", render)
    assert main(["--db", str(db), "monitor", "--probe", "--group", "capacity", "--view", "plain"]) == (4 if failed else 0)
    captured = capsys.readouterr()
    assert captured.out.count("PeerHub Monitor") == 1
    assert calls == ["refresh", "snapshot"]
    assert f"refresh: {'ERROR' if failed else 'OK'}" in captured.err
    if failed:
        assert "warning: probe failed" in captured.err
        assert "[diag_unavailable]" in captured.err and "peerhub diag health" in captured.err


def test_non_tty_human_future_schema_exits_six_without_probes(tmp_path, monkeypatch, capsys):
    db = tmp_path / "future.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version=999")
    conn.close()
    before = db.read_bytes()
    probes = []

    def probe(*args, **kwargs):
        probes.append(True)
        pytest.fail("future-schema monitor must not probe providers")

    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr("peerhub.extensions.quota_capture._probe_cli_version", probe)
    for name in ("poll_codex_usage", "poll_claude_usage", "poll_agy_usage"):
        monkeypatch.setattr(f"peerhub.extensions.quota_probes.{name}", probe)
    assert main(["--db", str(db), "monitor", "--probe", "--view", "plain", "--group", "capacity"]) == 6
    assert probes == [] and db.read_bytes() == before
    assert "[schema_version_error]" in capsys.readouterr().err


def test_cycles_parser_preserves_omission_and_explains_defaults(capsys):
    parser = build_parser()
    assert parser.parse_args(["monitor"]).cycles is None
    assert parser.parse_args(["monitor", "--cycles", "0"]).cycles == 0
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["monitor", "--help"])
    assert exc.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "default: 1 when stdout is not a TTY" in help_text
    assert "until interrupted on a TTY; 0 = until interrupted everywhere" in help_text
    assert "rich (colour), plain (ASCII override" in help_text
    assert "Both show PACE" in help_text
    assert "PEERHUB_MONITOR_VIEW=plain for a lasting ASCII fix" in help_text
    assert "explicit --view wins" in help_text
    assert "non-TTY appends completed frames; TTY without VT appends without escapes" in help_text
    assert "Render detailed grouped sections with bars, pace and forecasts" in help_text
    assert "Render all loaded detail rows" not in help_text


def test_negative_cycles_are_rejected(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--db", ".peerhub/work/no-such-monitor-test.db", "monitor", "--cycles", "-1"])
    assert exc.value.code == 2
    assert "--cycles must be >= 0" in capsys.readouterr().err
