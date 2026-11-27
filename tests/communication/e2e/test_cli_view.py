"""Plain diagnostic snapshots and shared terminal output."""
import pytest

from peerhub._console import use_color
from peerhub.cli.view import bar, human_duration, render_view


def _report(items, tmp_path):
    from peerhub.cli.app import main
    db = str(tmp_path / "v.db")
    assert main(["--db", db, "peer", "register", "--peer", "a"]) == 0
    from peerhub.extensions.diag import ReadonlyDiag
    return ReadonlyDiag(db).render(["peers", "streams", "resource_pools", "observations"])


def test_empty_report_is_unknown_and_ascii_safe(tmp_path):
    out = render_view(_report([], tmp_path), width=64, tz=None)
    assert "FORECAST" in out and "Threats: unknown" in out
    assert all(ord(c) < 128 for c in out) and max(map(len, out.split("\n"))) <= 80


def test_same_report_renders_identically(tmp_path):
    r = _report([], tmp_path)
    assert render_view(r, width=80) == render_view(r, width=80)


def test_helpers():
    assert human_duration(3700) == "1h02m"
    assert len(bar(0.5, 10, True)) == 10
    assert use_color(True, {"NO_COLOR": "1", "FORCE_COLOR": "1"}) is False
    assert use_color(False, {"FORCE_COLOR": "1"}) is False
    assert use_color(True, {"NO_COLOR": ""}) is False
    assert use_color(False, {}) is False


def test_untrusted_peer_pool_and_reason_cannot_emit_terminal_controls():
    from peerhub.core.text import strip_controls
    from peerhub.cli.view import _activity_lines, _credit_lines, _quota_table
    from peerhub.extensions.diag_read_model import quota_rows
    payload = "peer\x1b[2J\x00\x07\x85pool"
    assert strip_controls(payload) == "peer[2Jpool"
    item = {"subject_ref": payload, "resource_pool_ref": "quota:" + payload,
            "kind": "quota", "state": "ERROR", "payload": {"reason": payload}}
    alerts = []
    lines = _quota_table(quota_rows([item], [], 0), 100, now=0)
    lines += _credit_lines([{**item, "kind": "reset_credit"}], 0, alerts)
    lines += _activity_lines([{**item, "kind": "activity", "payload": {
        "status": payload, "certainty": payload}}])
    text = "\n".join(lines + alerts)
    assert "\x1b[2J" not in text
    assert all(c not in text for c in ("\x00", "\x07", "\x85"))
    assert "peer[2Jpool" in text


# ---------------------------------------------------------------- per-peer ask history (activity section)
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
    assert ag["last_seconds"] == 14.0 and peers["cx"]["last_seconds"] == 3.0


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
    out = render_view(_render(db), width=100)
    assert "Asks ag uncertain" in out
    assert "ago, took 49s" in out
    assert "counts unavailable" not in out
    assert "conditional window-average" in out


def test_resize_relayouts_grouped_frame_and_erases_shrunken_frame(monkeypatch):
    import io
    import os
    from peerhub._console import FrameOutput
    from peerhub.cli.monitor_expanded import render_expanded_frame as render_grouped_frame

    class Output(io.StringIO):
        def isatty(self):
            return True

        def fileno(self):
            return 23

    output = Output()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub._console.enable_vt", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    sizes = iter([os.terminal_size((120, 24)), os.terminal_size((80, 20))])
    monkeypatch.setattr("peerhub._console.os.get_terminal_size", lambda fd: next(sizes))
    payload = {"status": "OK", "selected_groups": ["store"],
               "groups": {"store": {"status": "OK", "data": {"schema_version": 7,
                                                                  "database_size_bytes": 10}}}}
    sink = FrameOutput()
    try:
        for _ in range(2):
            width, height = FrameOutput.geometry()
            sink.write(render_grouped_frame(payload, view="rich", width=width, height=height))
    finally:
        sink.close()
    assert "STORE [OK]" in output.getvalue()
    assert "\x1b[H" in output.getvalue() and "\x1b[K\x1b[J" in output.getvalue()
    assert output.getvalue().endswith("\x1b[0m\x1b[?25h")


@pytest.mark.platform_sensitive
def test_frame_output_anchors_rows_so_terminal_wrapping_cannot_accumulate(monkeypatch):
    import io
    from peerhub import _console
    from peerhub._console import FrameOutput

    class Output(io.StringIO):
        def isatty(self):
            return True

    output = Output()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub._console.enable_vt", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    sink = FrameOutput(anchor_rows=True)
    try:
        # These ambiguous-width rules can occupy two terminal rows in CJK consoles.
        # Absolute row positions keep a wrapped rule from shifting all later rows.
        sink.write("ATTN 4\n" + "\n".join(["─" * 79] * 5) + "\nDB:env\n")
        first = output.getvalue()
        # The alternate screen: no command line above the frame, so a pinned command cannot cover ATTN.
        # Auto-wrap is off while the frame is live: a too-wide row is clipped, never spilled onto the next row.
        assert first.startswith("\x1b[?1049h\x1b[?25l" + _console.NO_AUTOWRAP + "\x1b[H\x1b[2J\x1b[1;1HATTN 4")
        assert "\x1b[7;1HDB:env\x1b[K\x1b[J" in first
        assert "\r" not in first and "\n" not in first
        for row in range(2, 7):
            assert f"\x1b[{row};1H" + "─" * 79 + "\x1b[K" in first

        output.seek(0)
        output.truncate(0)
        sink.write("ATTN 1\nshort\n")
        assert output.getvalue() == "\x1b[H\x1b[1;1HATTN 1\x1b[K\x1b[2;1Hshort\x1b[K\x1b[J"
    finally:
        sink.close()
    assert output.getvalue().endswith("\x1b[?1049l" + _console.AUTOWRAP + "\x1b[0m\x1b[?25hATTN 1\nshort\n")


@pytest.mark.platform_sensitive
def test_frame_output_default_preserves_scrolling(monkeypatch):
    import io
    from peerhub._console import FrameOutput

    class Output(io.StringIO):
        def isatty(self):
            return True

    output = Output()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub._console.enable_vt", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    # Unanchored frames retain CRLF and can scroll when they exceed the terminal height.
    lines = [f"diagnostic row {row}" for row in range(40)]
    sink = FrameOutput()
    try:
        sink.write("\n".join(lines))
    finally:
        sink.close()
    assert output.getvalue() == (
        "\x1b[?25l\x1b[H\x1b[2J"
        + "\x1b[K\r\n".join(lines)
        + "\x1b[K\x1b[J\x1b[0m\x1b[?25h"
    )


def test_frame_output_uses_stable_geometry_when_stdout_is_not_a_tty(monkeypatch):
    import io
    from peerhub._console import FrameOutput

    class Pipe(io.StringIO):
        def isatty(self):
            return False

        def fileno(self):
            return 21

    monkeypatch.setattr("sys.stdout", Pipe())
    monkeypatch.setenv("COLUMNS", "15")
    monkeypatch.setenv("LINES", "2")
    monkeypatch.setattr("peerhub._console.os.get_terminal_size",
                        lambda _fd: pytest.fail("must not use another terminal's geometry"))

    assert FrameOutput.geometry() == (100, 24)
    assert FrameOutput.width() == 15  # retain the existing COLUMNS override for append-only output


def test_frame_output_reads_live_geometry_instead_of_stale_size_environment(monkeypatch):
    import io
    import os
    from peerhub._console import FrameOutput

    class Output(io.StringIO):
        def isatty(self):
            return True

        def fileno(self):
            return 17

    monkeypatch.setattr("sys.stdout", Output())
    monkeypatch.setenv("COLUMNS", "20")
    monkeypatch.setenv("LINES", "3")
    monkeypatch.setattr("peerhub._console.os.get_terminal_size",
                        lambda fd: os.terminal_size((91, 27)))

    assert FrameOutput.geometry() == (91, 27)


def test_term_dumb_never_enables_vt(monkeypatch):
    import io
    from peerhub._console import FrameOutput

    class Output(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdout", Output())
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr("peerhub._console.enable_vt", lambda: pytest.fail("VT must stay disabled"))
    output = FrameOutput()
    output.write("plain\n")
    output.close()
    assert not output.vt


def test_term_dumb_tty_keeps_columns_override_for_append_width(monkeypatch):
    import io
    from peerhub._console import FrameOutput

    class TTY(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdout", TTY())
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("COLUMNS", "53")

    assert FrameOutput.width() == 53


def test_probe_errors_have_their_own_line_without_a_pool():
    from peerhub.cli.view import _probe_line
    from peerhub.extensions.diag_read_model import quota_rows

    item = {"subject_ref": "cc", "kind": "quota", "state": "ERROR", "payload": {"reason": "claude_usage_timeout"}}
    assert quota_rows([item], [], 0) == []
    assert _probe_line([item]) == "Probes cc:ERR(claude_usage_timeout)"


def test_parent_json_flag_applies_to_quota(tmp_path, capsys):
    import json as _json

    from peerhub.cli.app import main as cli

    db = str(tmp_path / "f.db")
    cli(["--db", db, "peer", "register", "--peer", "a"])
    capsys.readouterr()
    assert cli(["--db", db, "diag", "--json", "quota"]) in (0, 5)  # parent --json is no longer lost
    assert _json.loads(capsys.readouterr().out)["schema_version"] == "1.0"


def test_diag_health_reports_how_the_store_was_chosen(tmp_path, capsys):
    import json as _json

    from peerhub.cli.app import main as cli

    db = tmp_path / "w.db"
    assert cli(["--db", str(db), "peer", "register", "--peer", "a"]) == 0
    capsys.readouterr()
    assert cli(["--db", str(db), "diag", "health", "--json"]) == 4
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
    out = render_view(rep, width=100, store_path=db, store_source="explicit")
    assert "(explicit)" in out
    assert "Threats: 1 threat - earliest cx/pool-threat" in out
    assert "(in 30m)" in out
    assert "! exhausts" in out and "conditional window-average" in out
    assert "= on pace" in out
    assert "ETA:" not in out
    assert max(map(len, out.splitlines())) <= 100


def test_header_korean_filename_does_not_split():
    from peerhub._console import visible_len
    from peerhub.extensions.diag import DiagnosticReport

    rep = DiagnosticReport("OK", 1000000.0, {}, {})
    # Width 45 with a Korean filename: visible_len exceeds character count
    frame = render_view(rep, width=45, store_path="D:/workspace/한국어.db", store_source="explicit")
    lines = frame.splitlines()
    header = lines[:next(i for i, line in enumerate(lines) if line.startswith("Threats"))]  # #338: wraps, never truncates
    assert header[0].startswith("PeerHub OK")
    assert header[-1].endswith("(explicit)")
    assert any("D:/workspace/한국어.db" in line for line in header)
    assert all(visible_len(line) <= 45 for line in header)


def test_frame_output_leaves_the_alternate_screen_on_close_only_for_anchored_frames(monkeypatch):
    import io
    from peerhub._console import FrameOutput

    class Output(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("peerhub._console.enable_vt", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    for anchored in (True, False):
        output = Output()
        monkeypatch.setattr("sys.stdout", output)
        sink = FrameOutput(anchor_rows=anchored)
        sink.write("ATTN 1\n")
        sink.close()
        text = output.getvalue()
        assert ("\x1b[?1049h" in text) is anchored and ("\x1b[?1049l" in text) is anchored
        assert text.endswith("\x1b[0m\x1b[?25h" + ("ATTN 1\n" if anchored else ""))


@pytest.mark.platform_sensitive
def test_frame_output_clears_and_repaints_every_row_after_a_resize(monkeypatch):
    import io
    import os
    from peerhub._console import FrameOutput

    class Output(io.StringIO):
        def isatty(self):
            return True

    output = Output()
    size = [os.terminal_size((120, 30))]
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub._console.enable_vt", lambda: True)
    monkeypatch.setattr("peerhub._console.shutil.get_terminal_size", lambda fallback=None: size[0])
    monkeypatch.setenv("TERM", "xterm")
    sink = FrameOutput(anchor_rows=True)
    try:
        sink.write("ATTN 1\npools\n")
        output.seek(0)
        output.truncate(0)
        sink.write("ATTN 1\npools\n")
        assert output.getvalue() == ""  # unchanged rows, same size: nothing is written
        size[0] = os.terminal_size((80, 30))  # narrower: the terminal kept old cells right of column 80
        sink.write("ATTN 1\npools\n")
        assert output.getvalue() == "\x1b[H\x1b[2J\x1b[1;1HATTN 1\x1b[K\x1b[2;1Hpools\x1b[K\x1b[J"
    finally:
        sink.close()
