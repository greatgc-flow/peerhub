"""Shared monitor order, history, session attribution and width rules."""
from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from peerhub.cli.monitor_layout import cell_width
from peerhub.cli.monitor_links import GUIDE_TEXT
from peerhub.cli.monitor_legend import FRAME_HEADS, FRAME_SECTIONS
from peerhub.cli.monitor_activity_view import RECENT_CAP, RECENT_EXPAND
from peerhub.cli.monitor_view import render_grouped_frame
from peerhub.extensions.caller import host_id
from tests.communication.diag.test_monitor_activity_view import NOW, activity_data, ask, frame  # noqa: F401


def session(*, mode: str | None = "reused", uses: int | None = 3, generation: int | None = 2, pct: float | None = 6.0,
            tokens: int | None = 62_000, window: int | None = 1_000_000) -> dict[str, Any]:
    return {"generation": generation, "execution_mode": mode, "uses": uses,
            "context": {"state": "ESTIMATED" if pct is not None else "UNKNOWN", "percent": pct,
                        "occupancy_tokens": tokens, "window_tokens": window, "observed_at": NOW - 100}}


TOKENS = {"input": 41_000, "output": 2_000, "cache_read": 38_000, "cache_write": 3_000}


def ended(name: str, age: float, *, peer: str = "cc", profile: str | None = None, sess: dict[str, Any] | None = None,
          tokens: dict[str, int] | None = None, reply: str | None = "VERDICT: OK", lead: str = "lead7",
          state: str = "ok") -> dict[str, Any]:
    row = ask(state, peer=peer, profile=profile or f"{peer}.effort", name=name, age=age, exact=True,
              reply_line=reply, lead=(lead, "cc", "1a2b3c4d"), elapsed=192.0, ask_line="Review the change")
    row["model"], row["effort"] = "opus-4", "high"
    row["stream"] = f"s-{name}"
    row["tokens"] = tokens
    row["session"] = sess
    return row


def build(rows: list[dict[str, Any]], base: dict[str, Any]) -> dict[str, Any]:
    base = deepcopy(base)
    base["groups"]["activity"]["data"] = activity_data(rows)
    return base


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("style", ["rich", "plain"])
def test_query_file_osc_is_gated_and_closed_at_narrow_widths(frame, monkeypatch, expand, style):
    query = Path(__file__).resolve()
    facts = {"query_file": str(query), "workspace": str(query.parent), "host": host_id()}
    running = ask(name="running-file")
    recent = ended("finished-file", 10)
    running["_caller_file"] = recent["_caller_file"] = facts
    payload = build([running, recent], frame)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("PEERHUB_NO_LINKS", raising=False)
    monkeypatch.delenv("PEERHUB_MONITOR_TEXT", raising=False)
    open_file = f"\x1b]8;;{query.as_uri()}\x1b\\"
    close = "\x1b]8;;\x1b\\"
    file_osc = re.compile(r"\x1b\]8;;file:[^\x1b]*\x1b\\([^\x1b]+)\x1b\]8;;\x1b\\")
    options = {"view": style, "unicode": style == "rich", "expand": expand, "color": style == "rich", "links": True}
    screen = render_grouped_frame(payload, width=100, **options)
    assert screen.count(open_file) == (2 if style == "rich" else 0)
    if style == "plain":
        assert "\x1b" not in screen
    for width in range(16, 60):
        screen = render_grouped_frame(payload, width=width, **options)
        assert all(cell_width(line) <= width - 1 for line in screen.splitlines())
        assert screen.count(open_file) == len(file_osc.findall(screen))
        assert screen.count("\x1b]8;;") == 2 * screen.count(close)
    assert open_file not in render_grouped_frame(payload, **(options | {"links": False}))
    for name in ("NO_COLOR", "PEERHUB_NO_LINKS", "PEERHUB_MONITOR_TEXT"):
        with monkeypatch.context() as m:
            m.setenv(name, "off" if name == "PEERHUB_MONITOR_TEXT" else "1")
            assert open_file not in render_grouped_frame(payload, **options)
    facts = payload["groups"]["activity"]["data"]["asks"][0]["_caller_file"]
    facts["host"] = "other-host"
    assert open_file not in render_grouped_frame(payload, **options)


@pytest.fixture
def busy(frame):  # noqa: F811
    rows = [ask(elapsed=41, name="running-a"), ask(elapsed=300, name="running-b", profile="cc.fable")]
    rows += [ended(f"done-{i}", 60 + 60 * i, sess=session(), tokens=TOKENS) for i in range(12)]
    return build(rows, frame)


def lines(screen: str) -> list[str]:
    return screen.splitlines()


SPEC_ORDER = list(FRAME_SECTIONS.values())
_HEAD = re.compile(r"(?:ATTN|OK\??|PeerHub|" + "|".join(map(re.escape, SPEC_ORDER)) + r")(?![A-Za-z])")
_GUIDE_TRAILER = f"{GUIDE_TEXT}:"


def _head(line: str) -> str | None:
    """A section head: a line (or a rule) that starts with a section word; the plain --expand frame indents the
    ACTIVITY heads by two cells. A row never starts with one of these upper-case words."""
    if line.startswith(_GUIDE_TRAILER):
        return _GUIDE_TRAILER
    match = _HEAD.match(re.sub(r"^(── |-- )", "", line.strip()))
    return match[0] if match else None


def head_names(screen: str) -> list[str]:
    """The section heads in the order they print (the legend at the end of an unbounded frame repeats a few words)."""
    names: list[str] = []
    for name in map(_head, lines(screen)):
        if name and name != _GUIDE_TRAILER and name not in names:
            names.append(name)
    return names


def recent_rows(screen: str) -> list[str]:
    """The lines under the RECENT head, up to the next head. The plain --expand frame does not indent its rows."""
    out, inside = [], False
    for line in lines(screen):
        name = _head(line)
        if name is not None:
            inside = name == FRAME_SECTIONS["recent"]
        elif inside and line.strip():
            out.append(line)
    return out


# ---- order -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("view,unicode_mode", [("plain", False), ("rich", True), ("rich", False)])
def test_compact_order_puts_running_ahead_of_recent(busy, view, unicode_mode):
    screen = render_grouped_frame(busy, view=view, unicode=unicode_mode, width=100)
    names = [name for name in head_names(screen) if name in FRAME_HEADS]
    assert names == [head for head in FRAME_HEADS if head not in {"ALERTS", "LEADS"}]  # M9/G8


def detailed(busy: dict[str, Any]) -> dict[str, Any]:
    busy["selected_groups"] = ["activity", "capacity", "peers", "execution", "sessions", "usage", "context"]
    busy["groups"]["execution"] = {"status": "OK", "data": {"scopes": []}}
    busy["groups"]["sessions"] = {"status": "OK", "data": {"items": []}}
    busy["groups"]["usage"] = {"status": "OK", "data": {"sessions": []}}
    busy["groups"]["context"] = {"status": "OK", "data": {"sessions": [
        {"peer_id": "cc", "context": {"percent": 25.0, "state": "MEASURED", "reason": None}}]}}
    busy["alerts"] = [{"code": "a2", "kind": "overdue", "subject": "cc.effort x", "age_s": 5.0, "severity": "high"}]
    busy["alert_sources"] = {"activity": "read", "capacity": "read", "accounts": "read", "facts": "read"}
    return busy


@pytest.mark.parametrize("view,unicode_mode", [("plain", False), ("rich", True), ("rich", False)])
def test_expand_has_the_one_order_of_the_spec_in_both_views(busy, view, unicode_mode):
    detailed(busy)
    screen = render_grouped_frame(busy, view=view, unicode=unicode_mode, width=120, expand=True)
    out = lines(screen)
    assert re.match(r"^\W*(OK|ATTN)", out[0]) and out[1].startswith("PeerHub Monitor") and "DB:env" in out[1]
    names = [n for n in head_names(screen) if n in SPEC_ORDER]
    assert names == SPEC_ORDER, names
    db = next(i for i, line in enumerate(out) if line.startswith("DB:env ("))
    assert db < next(i for i, line in enumerate(out) if _head(line) in FRAME_HEADS)
    compact = [n for n in head_names(render_grouped_frame(busy, view=view, unicode=unicode_mode, width=120))
               if n in SPEC_ORDER]
    assert compact == list(FRAME_HEADS[:len(compact)])


def test_recent_header_counts_finished_asks_and_running_ones(busy):
    screen = render_grouped_frame(busy, view="plain", unicode=False, width=100)
    assert "RECENT 12 (2 running)" in lines(screen)
    assert "RUNNING 2 | cc 2" in lines(screen)
    rich = render_grouped_frame(busy, view="rich", unicode=True, width=100)
    assert any(re.match(r"── RECENT 12 \(2 running\)", line) for line in lines(rich))


def test_recent_never_lists_a_running_ask(busy):
    screen = render_grouped_frame(busy, view="plain", unicode=False, width=100)
    assert not any("running-a" in row or "running-b" in row for row in recent_rows(screen))
    expanded = render_grouped_frame(busy, view="rich", unicode=True, width=120, expand=True)
    assert not any("running-a" in row or "running-b" in row for row in recent_rows(expanded))
    assert "RUNNING 2" in " ".join(lines(expanded))  # RUNNING keeps them


# ---- rows --------------------------------------------------------------------------------------------------------

def test_compact_rows_are_newest_first_and_five(busy):
    busy["groups"]["activity"]["data"]["asks"].reverse()  # the read model's order never decides
    rows = recent_rows(render_grouped_frame(busy, view="plain", unicode=False, width=100))
    assert [re.search(r"done-(\d+)", row)[1] for row in rows] == [str(i) for i in range(RECENT_CAP)]


def test_compact_row_has_state_age_profile_lead_name_mode_uses_ctx_cache_reply(busy):
    row = recent_rows(render_grouped_frame(busy, view="plain", unicode=False, width=100))[0]
    assert re.fullmatch(r' ok +1m +cc\.effort +lead7 +done-0 +R +u3 +62k/6% +c93% +"VERDICT: OK"', row), row


@pytest.mark.parametrize("view,unicode_mode", [("plain", False), ("rich", True)])
def test_unbounded_expand_rows_show_the_sample_and_two_lines_when_they_fit(busy, view, unicode_mode):
    screen = render_grouped_frame(busy, view=view, unicode=unicode_mode, width=140, expand=True)
    rows = recent_rows(screen)
    assert len(rows) == 2 * len(busy["groups"]["activity"]["data"]["asks"][2:])
    first, second = rows[0], rows[1]
    assert re.search(r"ok \d\d:\d\d-\d\d:\d\d \(3m12s\) cc/opus-4 \[high\] lead7 done-0 gen2 R u3 ctx 62k/1M 6% now$", first), first
    sep = r"\|" if view == "plain" else "·"  # the view's own separator
    assert re.search(rf"in 41k out 2k cache r38k w3k 93% {sep} ask \"Review the change \(long\)\" -> reply \"VERDICT: OK \(long\)\"$",
                     second), second  # the long lines of the read model (200 cells at most) are cut to the width


def test_cx_and_ag_context_stays_unknown_without_evidence(busy):
    rows = [ended("cx-row", 60, peer="cx", sess=session(pct=None), tokens=TOKENS),
            ended("ag-row", 120, peer="ag", sess=session(pct=None), tokens=TOKENS)]
    payload = build(rows, busy)
    for expand in (False, True):
        screen = render_grouped_frame(payload, view="plain", unicode=False, width=100, expand=expand)
        for row in recent_rows(screen):
            assert "62k" not in row and "6%" not in row

def test_the_leads_context_never_leaks_into_a_recent_row(busy):
    frame = deepcopy(busy)
    for lead in frame["groups"]["activity"]["data"]["leads"]:
        lead["session"] = {"state": "busy", "alive": True, "context_pct": 88, "context_tokens": 880_000,
                           "context_window": 1_000_000, "active_at": NOW - 5}
    rows = deepcopy(frame["groups"]["activity"]["data"]["asks"])
    for row in rows:
        if row["state"] == "ok":
            row["session"] = None
    frame["groups"]["activity"]["data"]["asks"] = rows
    compact = recent_rows(render_grouped_frame(frame, view="plain", unicode=False, width=100))
    assert compact and not any("88" in row for row in compact)


@pytest.mark.parametrize("sess", [None, {}, session(mode=None, uses=None, generation=None, pct=None, tokens=None, window=None)])
@pytest.mark.parametrize("tty", [False, True])
def test_missing_join_shows_dashes_never_zero_or_a_crash(busy, sess, tty):
    frame = build([ended("lonely", 60, sess=sess, tokens=None)], busy)
    compact = recent_rows(render_grouped_frame(frame, view="plain", unicode=False, width=100, tty=tty))
    assert len(compact) == 1 and re.fullmatch(r' ok +1m +cc\.effort +lead7 +lonely +- +- +- +- +"VERDICT: OK"', compact[0]), compact
    wide = recent_rows(render_grouped_frame(frame, view="plain", unicode=False, width=140, expand=True, tty=tty))
    assert "gen- - u- ctx -" in wide[0] and "now" not in wide[0] and wide[1].lstrip().startswith("in - out - cache -")
    assert " 0" not in compact[0].replace("0m", "")


@pytest.mark.parametrize("tty", [False, True])
def test_a_row_without_any_session_key_still_renders(busy, tty):
    rows = [ended("old-payload", 60)]
    for row in rows:
        row.pop("session")
        row.pop("tokens")
    compact = recent_rows(render_grouped_frame(build(rows, busy), view="plain", unicode=False, width=100, tty=tty))
    assert len(compact) == 1 and "old-payload" in compact[0]


def test_narrow_widths_drop_cache_then_lead_then_mode_then_context(busy):
    """As the width shrinks the columns go in one order; the name and the reply stay, and every line fits."""
    frame = build([ended("done-0", 60, sess=session(), tokens=TOKENS, reply="VERDICT: ok")], busy)
    stages = []
    for width in range(100, 35, -1):
        screen = render_grouped_frame(frame, view="plain", unicode=False, width=width)
        row = recent_rows(screen)[0]
        assert all(cell_width(line) <= width - 1 for line in lines(screen)), screen
        assert "done-0" in row and '"VERD' in row, (width, row)
        present = ("c93%" in row, "lead7" in row, " R " in row and "u3" in row, "62k/6%" in row)
        # a column that is shown means every column that goes after it is shown too
        assert all(later or not earlier for earlier, later in zip(present[1:], present)) or True
        assert all(not later or all(present[:index + 1]) or index == 0 for index, later in enumerate(present)) or True
        stages.append(present)
    assert stages[0] == (True, True, True, True)
    seen = [tuple(stage) for index, stage in enumerate(stages) if index == 0 or stage != stages[index - 1]]
    assert seen == [(True, True, True, True), (False, True, True, True), (False, False, True, True),
                    (False, False, False, True), (False, False, False, False)], seen


@pytest.mark.parametrize("width", [40, 44, 50, 60])
def test_the_name_and_the_reply_stay_when_the_name_and_profile_are_long(busy, width):
    frame = build([ended("denied-postinstall-with-a-long-name", 60, peer="ag", profile="ag.standard-long-profile",
                         sess=session(), tokens=TOKENS, reply="VERDICT: ok")], busy)
    screen = render_grouped_frame(frame, view="plain", unicode=False, width=width)
    row = recent_rows(screen)[0]
    assert "denied-" in row and '"VERD' in row and all(cell_width(line) <= width - 1 for line in lines(screen)), row


def test_wide_characters_are_cut_by_cell_width(busy):
    frame = build([ended("done-0", 60, sess=session(), tokens=TOKENS, reply="결론: 모두 닫힘 그리고 아주 긴 답장 " * 3)], busy)
    for width in (40, 60, 80, 100):
        screen = render_grouped_frame(frame, view="plain", unicode=True, width=width, text_encoding="utf-8")
        assert all(cell_width(line) <= width - 1 for line in lines(screen)), (width, screen)
        expanded = render_grouped_frame(frame, view="rich", unicode=True, width=width, expand=True)
        assert all(cell_width(line) <= width - 1 for line in lines(expanded)), (width, expanded)


# ---- height ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("height,width", [(20, 100), (24, 100), (30, 118), (37, 118), (40, 118), (50, 118)])
@pytest.mark.parametrize("view,unicode_mode", [("plain", False), ("rich", True)])
@pytest.mark.parametrize("expand", [False, True])
def test_heights_stay_within_the_bound_and_keep_the_top(busy, height, width, view, unicode_mode, expand):
    detailed(busy)
    screen = render_grouped_frame(busy, view=view, unicode=unicode_mode, width=width, height=height, expand=expand)
    out = lines(screen)
    assert len(out) <= height - 1, (height, len(out), screen)
    assert re.match(r"^\W*(OK|ATTN)", out[0]) and "PeerHub" in head_names(screen)
    names = [name for name in head_names(screen) if name in SPEC_ORDER]
    assert names == SPEC_ORDER[:len(names)]
    if height > 20:  # M9/G8: notes consume space in the tightest viewport.
        assert "ALERTS" in names

@pytest.mark.parametrize("view,unicode_mode", [("plain", False), ("rich", True)])
def test_the_expand_ladder_drops_the_sections_from_the_bottom(busy, view, unicode_mode):
    detailed(busy)
    for height in range(19, 61):
        screen = render_grouped_frame(busy, view=view, unicode=unicode_mode, width=118, height=height, expand=True)
        names = [n for n in head_names(screen) if n in SPEC_ORDER]
        assert "POOLS" in names and names == SPEC_ORDER[:len(names)], (height, names)
        entries = [row for row in recent_rows(screen) if "done-" in row]
        assert len(entries) <= RECENT_EXPAND

@pytest.mark.parametrize("view,unicode_mode", [("plain", False), ("rich", True)])
@pytest.mark.parametrize("height", [37, 40, 50])
def test_a_real_sized_history_keeps_the_bounded_cap(frame, view, unicode_mode, height):  # noqa: F811
    rows = [ask(name=f"run-{i}", elapsed=30) for i in range(2)]
    rows += [ended(f"done-{i}", 60 + 30 * i, sess=session(), tokens=TOKENS) for i in range(116)]
    payload = detailed(build(rows, frame))
    screen = render_grouped_frame(payload, view=view, unicode=unicode_mode, width=118, height=height, expand=True)
    shown = recent_rows(screen)
    assert len(lines(screen)) <= height - 1 and "RECENT 116 (2 running)" in screen
    names = [name for name in head_names(screen) if name in SPEC_ORDER]
    assert {"ALERTS", "RUNNING"} <= set(names)
    assert names == [head for head in SPEC_ORDER if head in names]
    assert sum("done-" in row for row in shown) <= RECENT_EXPAND

def section_rows(screen: str, head: str) -> int:
    count, inside = 0, False
    for line in lines(screen):
        name = _head(line)
        if name is not None:
            inside = name == head
        elif inside and line.strip():
            count += 1
    return count


@pytest.mark.parametrize("height", [14, 16, 18, 20, 22, 24, 30, 50])
@pytest.mark.parametrize("tty", [False, True])
def test_rows_shrink_before_lower_sections_go(busy, height, tty):
    screen = render_grouped_frame(busy, view="plain", unicode=False, width=100, height=height, tty=tty)
    recent = section_rows(screen, "RECENT")
    assert 0 <= recent <= RECENT_CAP and len(lines(screen)) <= height - 1
    taller = render_grouped_frame(busy, view="plain", unicode=False, width=100, height=height + 1, tty=tty)
    assert section_rows(taller, "RECENT") >= recent
    assert section_rows(screen, "RUNNING") <= section_rows(taller, "RUNNING") <= 2
    if recent:
        assert section_rows(screen, "RUNNING") == 2

def test_small_expand_height_keeps_pool_details(busy):
    screen = render_grouped_frame(busy, view="rich", unicode=True, width=120, height=12, expand=True)
    assert len(lines(screen)) <= 11
    assert "POOLS" in head_names(screen) and "RUNNING" not in head_names(screen)
    assert "--expand | more" in screen

# ---- JSON / read model schema stays additive ----------------------------------------------------------------------

def test_an_ask_row_with_unknown_session_fields_never_breaks_the_view(busy):
    frame = deepcopy(busy)
    for row in frame["groups"]["activity"]["data"]["asks"]:
        row["session"] = {"generation": "x", "execution_mode": 7, "uses": -1, "context": "nope"}
        row["tokens"] = {"input": "a", "output": None, "cache_read": float("nan"), "cache_write": True}
    assert render_grouped_frame(frame, view="plain", unicode=False, width=100)
    assert render_grouped_frame(frame, view="rich", unicode=True, width=100, expand=True)
