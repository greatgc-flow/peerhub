"""Public CLI regressions from the 0.27.1 review."""
from contextlib import closing, nullcontext
import json
import sqlite3

import pytest

from peerhub.cli.app import main
from peerhub.cli.guide import GUIDE_MAX_BYTES, render_guide
from peerhub.cli.lessons import lesson_by_id
from peerhub.cli.monitor_view import render_grouped_frame
from peerhub.extensions.diag_running import format_running
from tests.communication.diag.test_monitor_na_hidden_400 import absent
from tests.communication.diag.test_monitor_plain_golden import _payload, pool


def test_suggest_command_and_lesson_are_removed(capsys):
    with pytest.raises(SystemExit) as error:
        main(["suggest", "--tier", "effort"])
    assert error.value.code == 2
    assert "[invalid_arguments]" in capsys.readouterr().err
    assert not lesson_by_id("suggest")
    guide = render_guide()
    assert "suggest" not in guide and len((guide + "\n").encode("utf-8")) <= GUIDE_MAX_BYTES


@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("width", [80, 140])
def test_monitor_pool_membership_matches_in_both_layouts(view, width):
    payload = _payload([], [
        pool("ag", "G-7D", .5, pace=-5), pool("ag", "G-5H", .96, reset=3600),
        pool("ag", "3P-7D", .5, pace=4), pool("ag", "3P-5H", .39, reset=3600),
        pool("cx", "X-7D", .5, pace=3), pool("cc", "C-7D", .4, pace=-8),
        pool("cc", "C-5H", .4, reset=3600),
        absent("ag", "A-7D"), absent("ag", "A-5H"), absent("cx", "X-5H"), absent("cx", "X-30D"),
    ], selected_groups=["capacity"])
    gaps = []
    for expand in (False, True):
        screen = render_grouped_frame(payload, view=view, width=width, expand=expand)
        assert "POOLS 7 (+4 n/a)" in screen
        assert any("G-5H" in line and "96%" in line for line in screen.splitlines())
        assert any("3P-5H" in line and "39%" in line for line in screen.splitlines())
        gap = next(line for line in screen.splitlines() if line.startswith("gap vs "))
        assert "A-7D" not in gap
        gaps.append(gap)
        assert "suggest:" not in screen
    assert gaps[0] == gaps[1]


def test_monitor_open_distinguishes_unknown_ids_from_non_prompt_records(monkeypatch, capsys):
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("CREATE TABLE records (record_id TEXT PRIMARY KEY, kind TEXT, body_json TEXT)")
        conn.executemany("INSERT INTO records VALUES (?, ?, ?)", [
            ("rec-123456789abc", "prompt", json.dumps("inspect this")),
            ("rec-123456789abd", "message", json.dumps("other record")),
        ])
        monkeypatch.setattr("peerhub.cli.store_select.report_missing_store", lambda *args, **kwargs: False)
        monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: conn)
        monkeypatch.setattr("peerhub.extensions.monitor_queries.closing", nullcontext)
        for record_id, code, label in (("zzz", 1, "ask_not_found"), ("123456789abd", 2, "invalid_arguments")):
            assert main(["--db", "unused.db", "monitor", "open", record_id, "--json"]) == code
            output = capsys.readouterr()
            assert not output.out and f"[{label}]" in output.err
        assert main(["--db", "unused.db", "monitor", "open", "123456789abc"]) == 0
        assert json.loads(capsys.readouterr().out) == "inspect this"


@pytest.mark.parametrize("args", [
    ["ask", "cx"], ["ask", "--json"], ["explain", "nosuchtopic"], ["explain", "cx.nosuchprofile"],
    ["profile", "show", "cx.nosuchprofile"],
])
def test_invalid_arguments_use_exit_two(args, capsys):
    try:
        status = main(args)
    except SystemExit as error:
        status = error.code
    assert status == 2
    assert "[invalid_arguments]" in capsys.readouterr().err


def test_diag_running_formats_elapsed_and_lease_at_narrow_and_wide_widths():
    report = {"status": "OK", "rows": [{
        "peer_id": "cx", "profile": "cx.standard", "stream_id": "review",
        "request_id": "request-123", "delivery_id": "delivery-456", "certainty": "STARTED",
        "started_at": 1760000000, "elapsed_s": 17696, "lease_expires_in_s": 75,
        "caller": None, "relation": "unknown", "relation_basis": None,
    }], "truncated": False}
    narrow = format_running(report, width=80)
    assert all(len(line) <= 80 for line in narrow.splitlines())
    assert "delivery=delivery-456" in narrow and "elapsed=4h55m  lease=1m15s" in narrow
    wide = format_running(report, width=200)
    assert "ELAPSED" in wide and "4h55m" in wide and "1m15s" in wide
    assert report["rows"][0]["elapsed_s"] == 17696
