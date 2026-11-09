"""Cycle defaults follow stdout TTY status for every monitor watch format."""
from __future__ import annotations

import json
import sys

import pytest

from peerhub.cli.app import build_parser, main
from peerhub.core.store import CoreStore
from peerhub.extensions.observation import ObservationStore


@pytest.mark.parametrize("options", [[], ["--json"], ["--json", "--schema-version", "1.1"]])
@pytest.mark.parametrize("tty,cycles,expected", [
    (False, None, 1), (True, None, 2),
    (False, 0, 2), (True, 0, 2), (True, 1, 1),
])
def test_monitor_cycle_defaults_and_explicit_zero(tmp_path, monkeypatch, capsys, options, tty, cycles, expected):
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
    argv = ["--db", str(db), "monitor", "--view", "plain", *options]
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


def test_negative_cycles_are_rejected(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--db", ".peerhub/work/no-such-monitor-test.db", "monitor", "--cycles", "-1"])
    assert exc.value.code == 2
    assert "--cycles must be >= 0" in capsys.readouterr().err
