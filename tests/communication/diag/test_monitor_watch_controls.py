"""Interactive watch keys, terminal cleanup and copyable RUNNING details."""
from __future__ import annotations

from contextlib import closing, nullcontext
import os
from pathlib import Path
import shlex
import sqlite3
import sys

import pytest

from peerhub import _console
from peerhub.cli import monitor, monitor_keys, monitor_refresh
from peerhub.cli.app import main
from peerhub.cli.monitor_running_view import running_details
from peerhub.core.models import Peer, Stream
from peerhub.core.shell import format_shell_command
from peerhub.core.store import CoreStore
from peerhub.extensions import monitor_read_model
from tests.communication.diag.test_monitor_activity import NOW, claim, db  # noqa: F401
from tests.communication.diag.test_monitor_activity_view import ask
from tests.communication.diag.test_monitor_plain_golden import _payload


@pytest.mark.parametrize("expand", [False, True])
def test_watch_keys_refresh_reschedule_inspect_and_quit(monkeypatch, capsys, expand):
    now = 0.0
    reads, frames = [], []
    # None lets the watch reach its next automatic tick.
    keys = iter(["+", None, None, None, None, "-", None, None, "r", "\r", "\n", "i" if expand else "q"])
    row = {**ask(name="inline"), "_record_target": ("inline", 3)}

    def key(timeout):
        nonlocal now
        pressed = next(keys)
        if pressed is None:
            now += timeout
        return pressed

    def read(*args, **kwargs):
        reads.append(now)
        return _payload([row], [])

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr(_console, "enable_vt", lambda: True)
    monkeypatch.setattr(monitor, "watch_input", lambda enabled: nullcontext(key if enabled else None))
    monkeypatch.setattr(monitor, "monotonic", lambda: now)
    monkeypatch.setattr(monitor_read_model, "render_monitor_frame", read)
    monkeypatch.setattr(monitor_refresh.RefreshJob, "start", lambda *a, **k: pytest.fail("probe during --no-probe"))
    monkeypatch.setattr(_console.FrameOutput, "write", lambda self, frame: frames.append(frame))
    monkeypatch.setattr(_console.FrameOutput, "geometry", staticmethod(lambda: (100, 24)))
    assert main(["--db", __file__, "monitor", "--no-probe", "--group", "activity", "--interval-seconds", "2",
                 *(["--expand"] if expand else [])]) == 0
    assert reads == [0, 4, 6, 6, 6, 6]
    hints = [frame.splitlines()[-1] for frame in frames]
    assert hints[0] == hints[-1] == monitor_keys.WATCH_HINT.format(interval=2)
    assert hints[1] == monitor_keys.WATCH_HINT.format(interval=4)
    assert all(len(frame.splitlines()) <= 24 for frame in frames)
    details = capsys.readouterr().out
    assert ("RUNNING details 1/1" in details) == expand
    assert ("--after 2 --limit 1" in details) == expand


def test_keyboard_and_cli_share_interval_bounds_and_documented_hint():
    from peerhub.cli.app import build_parser

    parser = build_parser()
    for bound, action in [(monitor_keys.MIN_INTERVAL, "shorter"), (monitor_keys.MAX_INTERVAL, "longer")]:
        monitor.validate_monitor_args(parser, parser.parse_args(["monitor", "--interval-seconds", str(bound)]))
        assert monitor_keys.adjusted_interval(bound, action) == bound
    args = parser.parse_args(["monitor"])
    guide = (Path(__file__).resolve().parents[3] / "docs/monitor.md").read_text(encoding="utf-8")
    assert monitor_keys.WATCH_HINT.format(interval=args.interval) in guide
    assert set(monitor_keys.WATCH_KEYS) == {"r", "\r", "\n", "q", "+", "-", "i"}


def test_details_include_inline_and_file_asks_beyond_the_compact_cap():
    rows = [{**ask(name=f"ask-{n}"), "_record_target": (f"stream {n}", n + 1)} for n in range(7)]
    rows[0]["_caller_file"] = {"query_file": "prompt.txt"}
    payload = _payload([*rows, ask("ok"), ask(superseded=True)], [])
    database = "/a store/" + "long path/" * 15 + "core.db"
    payload["store_selection"] = {"path": database}
    commands = running_details(payload).splitlines()
    assert commands[0] == "RUNNING details 7/7"
    assert commands[2::2] == [format_shell_command(["peerhub", "--db", database, "record", "read",
                              f"--stream=stream {n}", "--after", str(n), "--limit", "1"]) for n in range(7)]
    assert all("\x1b" not in line for line in commands)


def test_interval_change_keeps_retry_cooldown():
    schedule = monitor_refresh.RefreshSchedule({"cc": set()}, forced_interval=60)
    schedule.update([], 0)
    schedule.completed(("cc",), {"status": "FAILED"}, 0)
    schedule.set_forced_interval(1, 1)
    assert schedule.due(2) == ()
    assert schedule.due(monitor_refresh.QUOTA_REFRESH_COOLDOWN_SECONDS) == ("cc",)


@pytest.mark.parametrize("expand", [False, True])
def test_scripted_frames_skip_input_and_details(monkeypatch, capsys, expand):
    row = {**ask(name="inline"), "_record_target": ("inline", 3)}
    monkeypatch.setattr(monitor_read_model, "render_monitor_frame", lambda *a, **k: _payload([row], []))
    monkeypatch.setattr(_console, "enable_vt", lambda: False)
    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(monitor, "sleep", interrupt)
    argv = ["--db", __file__, "monitor", "--group", "activity", "--no-probe", *(["--expand"] if expand else [])]
    for tty, options in [(False, []), (False, ["--snapshot"]), (True, ["--cycles", "1"]), (True, ["--cycles", "0"])]:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: tty)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: pytest.fail("scripted monitor polled stdin"))
        assert main([*argv, *options]) == 0
        output = capsys.readouterr().out
        assert "RUNNING 1" in output and "record read" not in output and "r/Enter" not in output
    assert main([*argv, "--snapshot"]) == 0
    snapshot = capsys.readouterr().out
    assert snapshot.count("--after 2 --limit 1") == 1 and "r/Enter" not in snapshot
    assert snapshot.index("record read") < snapshot.index("PeerHub Monitor")


def test_running_details_read_the_stored_prompt(db, capsys):
    store = CoreStore(db)
    store.register_peer(Peer(peer_id="user"))
    stream = "reviewer's  stream"
    store.create_stream(Stream(stream_id=stream))
    records = [store.append_record(stream_id=stream, author_peer_id="user", kind="prompt", body=body,
                                   created_at="2026-11-27T00:00:00Z", idempotency_key=body)
               for body in ("prior", "inspect this", "later")]
    record = records[1]
    claim(db, stream)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("INSERT INTO bridge_deliveries(delivery_id,seq,stream_id,peer_id,record_id,record_position,"
                     "attempt,certainty,claim_generation) VALUES ('ask',1,?,'cx',?,?,1,'NOT_STARTED',1)",
                     (stream, record.record_id, record.position))
        conn.commit()
    payload = monitor_read_model.render_monitor_frame(str(db), ["activity"], read_at=NOW, human=True)
    payload["store_selection"] = {"path": str(db)}
    command = running_details(payload).splitlines()[-1]
    argv = ["peerhub", "--db", str(db), "record", "read", f"--stream={stream}", "--after", "1", "--limit", "1"]
    assert command == format_shell_command(argv)
    assert "\x1b" not in command
    assert main((argv if os.name == "nt" else shlex.split(command))[1:]) == 0
    read = capsys.readouterr().out
    assert read.count("record_id: ") == 1 and f"record_id: {record.record_id}" in read
    assert "body: inspect this" in read


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal")
@pytest.mark.parametrize("interrupt", [False, True])
def test_posix_keyboard_restores_terminal(monkeypatch, interrupt):
    import pty
    import termios

    master, slave = pty.openpty()
    with os.fdopen(master, "wb", buffering=0) as keyboard, os.fdopen(slave) as terminal:
        monkeypatch.setattr(sys, "stdin", terminal)
        before = termios.tcgetattr(slave)
        with pytest.raises(KeyboardInterrupt) if interrupt else nullcontext():
            with monitor_keys.watch_input(True) as key:
                assert key is not None and key(0) is None
                assert not termios.tcgetattr(slave)[3] & (termios.ICANON | termios.ECHO)
                assert termios.tcgetattr(slave)[3] & termios.ISIG
                keyboard.write(b"r")
                assert key(.5) == "r"
                if interrupt:
                    raise KeyboardInterrupt
        assert termios.tcgetattr(slave) == before


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console")
def test_windows_keyboard_skips_extended_keys_and_handles_interrupt(monkeypatch):
    import msvcrt

    keys = iter(["\xe0", "q", "r", "\x03"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(msvcrt, "kbhit", lambda: True)
    monkeypatch.setattr(msvcrt, "getwch", lambda: next(keys))
    with monitor_keys.watch_input(True) as read:
        assert read is not None and read(0) == "r"
        with pytest.raises(KeyboardInterrupt):
            read(0)
