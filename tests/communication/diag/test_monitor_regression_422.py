"""Monitor rendering and terminal regressions from #422."""
from __future__ import annotations

from argparse import Namespace
from copy import deepcopy
import re
from typing import Any

import pytest

from peerhub.cli import monitor, monitor_view as mv, view
from peerhub.extensions import monitor_read_model
from tests.communication.diag.test_monitor_na_hidden_400 import na_payload
from tests.communication.diag.test_monitor_plain_golden import full_payload
from tests.communication.diag.test_monitor_recent_layout import (
    NOW, activity_data, ask, ended, head_names, recent_rows, session,
)


@pytest.mark.parametrize("width", [60, 118])
@pytest.mark.parametrize("style", ["plain", "rich"])
@pytest.mark.parametrize("expand", [False, True])
def test_active_work_survives_history_pressure(width, style, expand):
    payload = full_payload()
    activity = payload["groups"]["activity"]["data"]
    activity["asks"].extend(ended(f"history-{i}", i) for i in range(mv.RECENT_EXPAND * 2))
    screen = mv.render_grouped_frame(payload, view=style, unicode=style == "rich", width=width, height=28, expand=expand)
    heads = head_names(screen)
    assert heads.index("ALERTS") < heads.index("RUNNING")
    after_running = screen.split("RUNNING", 1)[1]
    next_heading = r"\n(?:── |-- )?(?:" + "|".join(map(re.escape, mv.FRAME_HEADS)) + r") "
    running = re.split(next_heading, after_running, maxsplit=1)[0]
    assert "pool_out:" in screen and "cx.deepthink" in running
    if "RECENT" in heads:
        assert heads.index("RUNNING") < heads.index("RECENT")
    if expand:
        assert payload["store_selection"]["path"] in screen.split("ALERTS", 1)[0]


@pytest.mark.parametrize("width", [60, 118])
@pytest.mark.parametrize("style", ["plain", "rich"])
def test_expanded_history_restores_finished_and_sampled_superseded_rows(width, style):
    payload = full_payload()
    cap = monitor_read_model.TEXT_NEWEST_ROWS
    payload["groups"]["activity"]["data"] = activity_data([
        *(ended(f"history-{i:02}", i) for i in range(cap + 1)),
        ask("unsure", name="replaced-ask", superseded=True, age=4000, exact=True),
    ])
    options: dict[str, Any] = dict(view=style, unicode=style == "rich", width=width, expand=True)
    before = deepcopy(payload)
    whole = "\n".join(recent_rows(mv.render_grouped_frame(payload, **options)))
    bounded = "\n".join(recent_rows(mv.render_grouped_frame(payload, height=100, **options)))
    assert f"history-{cap - 1:02}" in whole and f"history-{cap:02}" not in whole
    assert "replaced-ask" in whole and mv.SUPERSEDED_MARK in whole
    assert f"history-{mv.RECENT_EXPAND:02}" not in bounded and "replaced-ask" not in bounded
    assert payload == before


@pytest.mark.parametrize("width", [60, 118])
@pytest.mark.parametrize("style", ["plain", "rich"])
@pytest.mark.parametrize("expand", [False, True])
def test_unavailable_pool_count_and_quota_reason(width, style, expand):
    payload = na_payload()
    screen = mv.render_grouped_frame(payload, view=style, unicode=style == "rich", width=width, expand=expand)
    pools = screen.split("POOLS ", 1)[1].split("QUOTA [", 1)[0]
    assert pools.startswith("4 ")
    assert all(f"({pool} {mv.QUOTA_NA})" in pools for pool in ("C-5H", "X-30D"))
    assert ("not reported (plan prolite)" in screen) == expand


@pytest.mark.parametrize("width", [64, 118])
@pytest.mark.parametrize("style", ["plain", "rich"])
@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("peer", ["cx", "ag"])
def test_only_newest_ask_uses_live_session_context(width, style, expand, peer):
    old = ended("old", 120, peer=peer, sess=session(), reply="ok")
    new = ended("new", 60, peer=peer, sess=session(pct=None), reply="ok")
    new["started_at"] = NOW - 70
    old["started_at"] = NOW - 130
    old["stream"] = new["stream"]
    payload = full_payload()
    payload["groups"]["activity"]["data"] = activity_data([old, new])
    payload["groups"]["sessions"] = {"status": "OK", "data": {"items": [
        {"stream_id": new["stream"], "peer_id": peer, "provider": peer, "generation": 2, "state": "ACTIVE",
         "telemetry": {"context": {**session(tokens=71_000, pct=7.0)["context"], "observed_at": NOW - 60}}},
    ]}}
    screen = mv.render_grouped_frame(payload, view=style, unicode=style == "rich", width=width, expand=expand)
    lines = recent_rows(screen)
    start = next(i for i, line in enumerate(lines) if " new " in line)
    prior = next(line for line in lines if " old " in line)
    current = "\n".join(lines[start:lines.index(prior)])
    assert "71k" in current and mv.CONTEXT_ESTIMATE_LABEL in current
    assert all(value not in prior for value in ("71k", "62k", "u3", "gen2", "now"))


@pytest.mark.parametrize("tty,no_links", [(False, False), (True, True), (True, False)])
def test_plain_guide_respects_terminal_and_opt_out(monkeypatch, tty, no_links):
    monkeypatch.setenv("PEERHUB_NO_LINKS", str(int(no_links)))
    monkeypatch.setenv("PEERHUB_GUIDE_URL", "https://example.test/monitor")
    screen = mv.render_grouped_frame(full_payload(), view="plain", width=118, expand=True, tty=tty)
    assert (f"{mv.GUIDE_TEXT}:" in screen) == (tty and not no_links)


@pytest.mark.parametrize("height", [24, 40])
def test_normal_screen_alone_needs_a_margin(height):
    assert monitor._live_rows(height, alternate=True) == height
    assert monitor._live_rows(height) == height - monitor.LIVE_ROW_MARGIN


@pytest.mark.parametrize("alternate", [False, True])
def test_terminal_leaves_last_frame_on_normal_screen(monkeypatch, capsys, alternate):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(view, "enable_vt", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    output = view.FrameOutput(anchor_rows=True, alternate=alternate)
    output.write("first\n")
    output.write("last\n")
    output.close()
    written = capsys.readouterr().out
    assert ("\x1b[?1049h" in written) == alternate
    assert ("\x1b[?1049l" in written) == alternate
    assert written.rsplit("\x1b[?25h", 1)[-1] == ("last\n" if alternate else "\r\n")


@pytest.mark.parametrize("width", [60, 118])
@pytest.mark.parametrize("style", ["plain", "rich"])
@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("cycles", [0, 1])
def test_watch_uses_the_selected_screen_budget_and_leaves_a_final_frame(monkeypatch, capsys, width, style, expand, cycles):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(view, "enable_vt", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr(view.FrameOutput, "geometry", staticmethod(lambda: (width, 24)))
    monkeypatch.setattr("peerhub.cli.store_select.report_missing_store", lambda *args, **kwargs: False)
    monkeypatch.setattr(monitor_read_model, "render_monitor_frame", lambda *args, **kwargs: full_payload())
    monkeypatch.setattr(monitor, "_cached_profiles", lambda *args: [])
    rendered: list[str] = []
    heights: list[int] = []
    render_frame = monitor._render_human_frame

    def render(payload, **kwargs):
        heights.append(kwargs["height"])
        frame = render_frame(payload, **kwargs)
        rendered.append(frame)
        return frame

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(monitor, "_render_human_frame", render)
    monkeypatch.setattr(monitor, "sleep", interrupt)
    args = Namespace(db=__file__, db_source="explicit", json=False, view=style, expand=expand, cycles=cycles,
                     interval=1.0, collect_every=1, probe=False, no_probe=True)
    assert monitor._run_monitor_grouped_watch(args, ["activity", "capacity"], {}) == 0
    written = capsys.readouterr().out
    continuous = cycles == 0
    assert all(height == 24 - (0 if continuous else monitor.LIVE_ROW_MARGIN) for height in heights)
    assert rendered and all(len(frame.splitlines()) <= height for frame, height in zip(rendered, heights))
    assert ("\x1b[?1049h" in written) == continuous
    assert ("\x1b[?1049l" in written) == continuous
    if continuous:
        assert written.endswith(rendered[-1])
    else:
        rows = re.findall(r"\x1b\[\d+;1H(.*?)\x1b\[K", written)
        final = rendered[-1].splitlines()
        assert rows[-len(final):] == final
        assert written.endswith("\x1b[?25h\r\n")
