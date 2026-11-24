"""P21: the rich compact monitor frame (pill, rules, bars, glyphs, the 256-colour palette; the rich --expand frame is
pinned in test_monitor_expand_rich.py); P9: line 1 and POOLS of one redraw agree; P6: a store that never had a quota
refresh is shown as no evidence."""
from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from typing import Any

import pytest

from peerhub.cli import monitor_view as mv
from peerhub.cli.monitor_view import FRAME_HEADS, FRAME_SECTIONS, FRAME_SECTION_ORDER, render_grouped_frame, _width
from peerhub.extensions.observation_model import LOW_QUOTA_FRACTION
from tests.communication.diag.test_monitor_activity_view import NOW, activity_data, ask, pool
from tests.communication.diag.test_monitor_leads_view import lead, sess
from tests.communication.diag.test_monitor_plain_golden import blocks, empty_payload, full_payload, quiet_payload

SGR = re.compile(r"\x1b\[([0-9;]*)m")
OUR_CODES = {"0", *mv.RICH_SGR.values(), *mv.PILL_SGR.values()}  # #414: a child bar's empty cells
U, A = mv.RICH_GLYPHS[True], mv.RICH_GLYPHS[False]
POOLS_HEAD = FRAME_SECTIONS["pools"]
SUGGEST_HEAD = FRAME_SECTIONS["suggest"]
_RULE_HEADS = "|".join(re.escape(head) for head in FRAME_HEADS if head != SUGGEST_HEAD)
_PLAIN_HEADS = "|".join(re.escape(FRAME_SECTIONS[key]) for key in FRAME_SECTION_ORDER if key not in {"suggest", "alerts"})
_RULE_HEAD = re.compile(rf"^(?:──|--) ((?:{_RULE_HEADS}) \S+)")
_PLAIN_HEAD = re.compile(rf"^({_PLAIN_HEADS}) (\S+)")


def strip(text: str) -> str:
    return SGR.sub("", text)


def rich(payload: dict[str, Any], *, color: bool = True, unicode: bool = True, width: int = 100,
         height: int | None = None, **kw: Any) -> str:
    return render_grouped_frame(deepcopy(payload), view="rich", color=color, unicode=unicode, width=width, height=height,
                                **kw)


def plain(payload: dict[str, Any], *, width: int = 100, height: int | None = None, **kw: Any) -> str:
    return render_grouped_frame(deepcopy(payload), view="plain", unicode=False, width=width, height=height, **kw)


def rule_head(line: str) -> str | None:
    """The head a rule line carries (`POOLS 9`, `RUNNING 4`, ...), or None for a line that is not a rule."""
    match = _RULE_HEAD.match(line)
    return match[1] if match else None


def row_of(screen: str, needle: str) -> str:
    """The first line below line 1 that holds `needle` (line 1 names the min-quota pool too)."""
    return next(line for line in screen.splitlines()[1:] if needle in line)


def span(line: str, text: str) -> str | None:
    """The SGR code of the span that paints `text` in `line`, or None when it is not painted."""
    match = re.search(r"\x1b\[([0-9;]*)m[^\x1b]*" + re.escape(text), line)
    return match[1] if match and match[1] != "0" else None


@pytest.fixture(autouse=True)
def _colour_env(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("COLORTERM", raising=False)


# ---- order ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("build", [full_payload, quiet_payload, empty_payload])
def test_both_views_keep_the_same_section_order(build):
    plain_heads = [key for key in blocks(plain(build())) if key != "top"]
    assert plain_heads == [key for key in FRAME_HEADS if key in plain_heads] and plain_heads[0] == FRAME_HEADS[0]
    lines = strip(rich(build())).splitlines()
    heads = [(rule_head(line) or line).split()[0] for line in lines if rule_head(line) or line.startswith(SUGGEST_HEAD)]
    assert heads == plain_heads
    assert lines[1].startswith("PeerHub Monitor 2026-11-14 23:46Z")


def row_counts(lines: list[str]) -> dict[str, int]:
    """Data rows per section, in either view (a head is a word at column 0 or a rule)."""
    counts: dict[str, int] = {}
    key = ""
    for line in lines:
        head = rule_head(line) or (line if _PLAIN_HEAD.match(line) else None)
        if head:
            key = head.split()[0]
            counts[key] = 0
        elif key and line.startswith(" "):
            counts[key] += 1
        elif line.startswith(SUGGEST_HEAD):
            key = ""
    return counts


@pytest.mark.parametrize("view", ["plain", "rich"])
def test_the_fit_order_preserves_running_ahead_of_recent(view):
    seen: list[dict[str, int]] = []
    for height in range(30, 1, -1):
        options = {"width": 140, "height": height}
        screen = plain(full_payload(), **options) if view == "plain" else strip(rich(full_payload(), **options))
        assert len(screen.splitlines()) <= height - 1
        seen.append(row_counts(screen.splitlines()))
    assert seen[0]["RUNNING"] == 4
    for before, after in zip(seen, seen[1:]):
        assert all(after.get(key, 0) <= count for key, count in before.items())
    assert all(counts.get("RUNNING", 0) == 4 for counts in seen if "RECENT" in counts)
    assert any("RUNNING" in counts and "RECENT" not in counts for counts in seen)


# ---- C2: a rule replaces its head and never adds a line --------------------------------------------------------------
@pytest.mark.parametrize("build", [full_payload, quiet_payload, empty_payload])
@pytest.mark.parametrize("width", [40, 52, 60, 72, 80, 90, 100, 120])
def test_rich_frame_has_the_lines_and_rows_of_the_plain_frame(build, width):
    payload = build()
    observations = payload["groups"]["capacity"]["data"]["observations"]
    # Unavailable markers can wrap around the rich decoration. Test row parity on the quota data rows.
    observations[:] = [row for row in observations if row["state"] != "ABSENT"]
    for height in (None, *range(2, 30)):
        plain_lines = plain(payload, width=width, height=height).splitlines()
        rich_lines = strip(rich(payload, width=width, height=height)).splitlines()
        assert all(_width(line) < width for line in (*rich_lines, *plain_lines))
        if width < 80:
            continue  # G4: the state glyph can change when rows wrap on narrow screens.
        assert abs(len(rich_lines) - len(plain_lines)) <= 2
        for old, new in zip(plain_lines, rich_lines):
            if "POOLS" in old or "POOLS" in new or "gap vs " in old or "gap vs " in new:
                break  # G: the common quota cells are checked in test_monitor_pool_health.
            head = _PLAIN_HEAD.match(old)
            if head:  # the rule keeps the head's title, count and notes (a head too long for the line stays a head)
                assert new.removeprefix("── ").startswith(f"{head[1]} {head[2]}"), (width, height, old, new)
                assert rule_head(new) or _width(old) > min(100, width) - 4, (width, height, old, new)
                for part in old.split(" | ")[1:]:
                    assert part.replace("~", "…") in new, (width, height, old, new)
            elif old.startswith(("+", SUGGEST_HEAD, "next:", "no quota evidence", "DB:")):
                assert new == old
            elif old.startswith(" "):  # a data row: the same row (its first cells), whatever the decoration
                cells = [cell.lstrip("!") for cell in old.split() if re.match(r"^!?[A-Za-z0-9]", cell)][:2]
                shown = {cell.lstrip("!") for cell in new.split()}
                assert cells and all(cell in shown for cell in cells), (width, height, old, new)


def test_the_pools_rule_keeps_the_word_pools_and_the_column_titles():
    width = 110
    line = row_of(strip(rich(full_payload(), width=width)), f"{POOLS_HEAD} 9")
    assert line.startswith(f"── {POOLS_HEAD} 9 ─") and line.endswith("─") and _width(line) == width - 1
    assert mv.POOLS_USE_FIRST_HINT in line
    titles = [word for word in line.replace(mv.POOLS_USE_FIRST_HINT, "").split() if word in mv._POOL_COLUMNS]
    assert titles == ["STATE", "LEFT", "PACE(pp)", mv.POOL_MARGIN_COLUMN, "EXH/RESET", "FAMILY"]
    rows = [text for text in strip(rich(full_payload(), width=width)).splitlines() if "X-7D" in text]
    assert line.index("LEFT") + len("LEFT") == rows[0].index("42%") + len("42%")


@pytest.mark.parametrize("head", ["RUNNING 4 · ag 1 cc 2 cx 1",
                                  "LEADS 4 entries · partial (1 unreadable, 2 not read) · 3 sessions found",
                                  "RECENT 4 (4 running)"])
def test_the_other_rules_keep_their_head_content(head):
    payload = full_payload()
    payload["selected_groups"] = ["activity"]
    line = next(line for line in strip(rich(payload)).splitlines() if line.startswith(f"── {head.split(' · ')[0]}"))
    assert line.startswith(f"── {head} ─") and _width(line) == 99


def test_read_failed_notes_stay_in_the_rule():
    payload = full_payload()
    payload["groups"]["activity"] = {"status": "ERROR", "data": None}
    assert row_of(strip(rich(payload)), "RUNNING").startswith("── RUNNING ? · activity read failed ─")


# ---- budget: 24 lines, terminal width, 8,192 bytes with the SGR codes counted (C1) -----------------------------------
def worst_payload() -> dict[str, Any]:
    payload = full_payload()
    for item in payload["groups"]["capacity"]["data"]["observations"]:
        item["last_measurements"] = {"remaining_fraction": .05}
    payload["groups"]["activity"]["data"] = activity_data(
        [ask(name="이름" * 20, elapsed=60.0 * (i + 1), ask_line="요청" * 60, lead=("리드" * 20, "cc", f"{i:08x}"),
             alive=False) for i in range(9)]
        + [ask("error", name=f"done-{i}", age=60, exact=True, reply_line="答" * 80) for i in range(4)])
    data = payload["groups"]["activity"]["data"]
    data["leads"] = [lead("세션" * 20, lead_id=f"{i:08x}", running=i % 2,
                          session=sess(title="제목" * 60, model="claude-" + "모" * 30, context_pct=95, alive=None))
                     for i in range(12)]
    data["leads_read"] = {"state": "partial", "unreadable": 9, "not_read": 99}
    payload["alerts"] = [{"code": "a1", "kind": "empty", "subject": "가" * 120, "age_s": 1.0, "severity": "high"}] * 5
    return payload


@pytest.mark.parametrize("build", [full_payload, worst_payload, quiet_payload, empty_payload])
@pytest.mark.parametrize("width", [1, 2, 8, 19, 20, 26, 33, 40, 47, 48, 59, 64, 79, 80, 81, 99, 100, 101, 132, 250])
def test_rich_frame_stays_within_the_width_line_and_byte_budget(build, width):
    for unicode in (True, False):
        for height in (None, 0, 1, 2, 3, 5, 8, 12, 17, 24, 25, 40):
            screen = rich(build(), width=width, height=height, unicode=unicode)
            lines = screen.splitlines()
            assert len(lines) <= (24 if height is None else min(24, max(0, height - 1))), (width, height)
            assert all(_width(line) <= max(1, width - 1) for line in lines), (width, height, lines)
            assert len(screen.encode("utf-8")) <= 8192  # the painted frame: SGR codes are counted
            assert all(code in OUR_CODES for code in SGR.findall(screen))
            assert "\x1b" not in strip(screen)


def test_one_sgr_span_per_run_of_one_colour():
    screen = rich(worst_payload())
    for line in screen.splitlines():
        assert line.count("\x1b[0m") * 2 == line.count("\x1b["), line  # every span is closed, none is nested
        assert len(SGR.findall(line)) <= 12, line  # no per-cell gradient: a row has a handful of spans
    # G4 can omit the bar in the crowded frame. Check the span in a fixture where the bar fits.
    bar = row_of(rich(pools_payload(pool("cc", "C-7D", .63, pace=-20))), "C-7D")
    assert re.search(rf"\x1b\[[0-9;]+m[█░|]{{{mv.BAR_CELLS},{mv.BAR_MAX_CELLS}}}\x1b\[0m", bar)


def test_colour_never_changes_the_text_but_for_the_pill_padding():
    for build in (full_payload, worst_payload, quiet_payload, empty_payload):
        painted, bare = strip(rich(build())).splitlines(), rich(build(), color=False).splitlines()
        assert painted[1:] == bare[1:]
        head = re.match(r"^(OK\??|ATTN \d+\??)", bare[0])
        assert head and painted[0].startswith(f" {head[1]} ") and painted[0].split() == bare[0].split()


# ---- NO_COLOR, --view plain and the palette (C6) ---------------------------------------------------------------------
def test_no_color_keeps_the_rich_layout_with_unicode_and_no_sgr(monkeypatch):
    bare = rich(full_payload(), color=False, width=120)
    monkeypatch.setenv("NO_COLOR", "1")
    screen = rich(full_payload(), color=True, width=120)
    assert "\x1b" not in screen and screen == bare
    assert f"── {POOLS_HEAD} 9 ─" in screen and "█" in screen and U["ok"] in screen and " · " in screen.splitlines()[0]
    assert screen.splitlines()[0].startswith("ATTN 2 · gone: cc.fable activity-view 5m · pool_out: cx X-5H")


def test_view_plain_is_ascii_without_sgr_whatever_the_colour_flag(monkeypatch):
    for build in (full_payload, quiet_payload, empty_payload):
        screen = render_grouped_frame(build(), view="plain", color=True, unicode=False, width=100)
        assert "\x1b" not in screen and screen.isascii() and screen == plain(build())
    monkeypatch.setenv("NO_COLOR", "1")
    assert render_grouped_frame(full_payload(), view="plain", color=True, unicode=False, width=100) == plain(full_payload())


@pytest.mark.parametrize("colorterm", [None, "truecolor", "24bit"])
def test_the_palette_is_256_colour_with_semicolon_forms_whatever_colorterm_says(monkeypatch, colorterm):
    reference = rich(full_payload())
    if colorterm:
        monkeypatch.setenv("COLORTERM", colorterm)
    screen = rich(full_payload())
    assert screen == reference  # C6: no truecolor branch
    codes = set(SGR.findall(screen))
    assert codes <= OUR_CODES and ":" not in "".join(codes)
    for code in codes - {"0", "1", mv.RICH_SGR["out"], mv.RICH_SGR["spend"]}:  # M1  # SGR 2 dims a child bar's empty cells (#414)
        assert re.fullmatch(r"(?:1;)?38;5;\d{1,3}(?:;48;5;\d{1,3})?", code), code
        assert "38;2" not in code and "48;2" not in code
    assert not re.search(r"\x1b\[[0-9;:]*[^0-9;m]", screen) and "\x1b]" not in screen


def test_the_palette_has_one_accent_per_meaning():
    assert set(mv.RICH_SGR) == {"ok", "warn", "hot", "bad", "dim", "title", "out", "spend"}
    assert len(set(mv.RICH_SGR.values())) == len(mv.RICH_SGR)
    assert set(mv.PILL_SGR) == {"OK", "OK?", "ATTN"} and all("48;5;" in code for code in mv.PILL_SGR.values())


# ---- line 1: the pill, the facts, the time ---------------------------------------------------------------------------
@pytest.mark.parametrize("sources,alerts,pill,key", [
    ({}, [], " OK ", "OK"),
    ({"capacity": "stale"}, [], " OK? ", "OK?"),
    ({}, [{"code": "a3", "kind": "pool_out", "subject": "cx X-5H", "age_s": None, "severity": "high"}], " ATTN 1 ", "ATTN"),
    ({"capacity": "stale"}, [{"code": "a3", "kind": "pool_out", "subject": "cx X-5H", "age_s": None, "severity": "high"}],
     " ATTN 1? ", "ATTN"),
])
def test_line_1_starts_with_a_status_pill(sources, alerts, pill, key):
    payload = quiet_payload()
    payload["alert_sources"].update(sources)
    payload["alerts"] = alerts
    line = rich(payload).splitlines()[0]
    assert line.startswith(f"\x1b[{mv.PILL_SGR[key]}m{pill}\x1b[0m")
    assert strip(line).startswith(pill + (" (quota stale)" if sources and not alerts else "") + " · ")


def test_line_1_facts_are_separated_by_a_dim_dot_and_the_time_is_right_aligned():
    line = rich(quiet_payload()).splitlines()[0]
    dim = mv.RICH_SGR["dim"]
    assert strip(line) == " OK  · 0 running · 0 leads · min quota 63% cc C-7D ok".ljust(93) + "23:46Z"
    assert line.count(f"\x1b[{dim}m · \x1b[0m") == 3 and line.endswith(f"\x1b[{dim}m23:46Z\x1b[0m")
    assert rich(quiet_payload(), unicode=False, color=False).splitlines()[0].startswith("OK | 0 running | 0 leads | min")


def test_the_time_gives_way_before_any_fact_of_line_1():
    timed = []
    for width in range(30, 101):  # the facts are those of the plain line 1 with two cells less: the pill's padding
        old = plain(full_payload(), width=width - 2).splitlines()[0].split(" | ")
        new = strip(rich(full_payload(), width=width, color=False)).splitlines()[0]
        body = re.sub(r" +\d\d:\d\dZ$", "", new)
        assert body.split(" · ") == [field.replace("~", "…") for field in old], width
        timed.append(new != body)
        if new != body:  # at the right edge, two cells or more after the last fact
            assert _width(new) == width - 1 and new.endswith("23:46Z") and new[len(body):].startswith("  ")
        else:
            assert _width(body) + 2 + len("23:46Z") > width - 3
    assert True in timed and False in timed
    payload = quiet_payload()
    payload.pop("read_at")
    assert "Z" not in rich(payload, color=False).splitlines()[0]  # no time is invented


def test_line_2_is_dim_structure():
    line = rich(quiet_payload()).splitlines()[1]
    assert line == f"\x1b[{mv.RICH_SGR['dim']}mPeerHub Monitor 2026-11-14 23:46Z · DB:env · quota age 10s\x1b[0m"


# ---- POOLS rows: bar, glyph and state word, the row's one colour ------------------------------------------------------
def pools_payload(*rows: dict[str, Any]) -> dict[str, Any]:
    payload = quiet_payload()
    payload["groups"]["capacity"]["data"]["observations"] = list(rows)
    return payload


@pytest.mark.parametrize("remaining", [1.0, .63, .5, .04, .001, 0.0])
def test_bar_draws_what_is_left_at_its_allocated_width(remaining):
    for unicode in (True, False):
        line = row_of(strip(rich(pools_payload(pool("cc", "C-7D", remaining)), unicode=unicode)), "C-7D")
        glyphs = mv.RICH_GLYPHS[unicode]
        chars = re.escape(glyphs["full"] + glyphs["empty"] + ("▏" if unicode else ":"))
        bar = re.search(rf"[{chars}]{{{mv.BAR_CELLS},{mv.BAR_MAX_CELLS}}}", line)
        assert bar and bar[0] == mv._bar_quota(remaining, len(bar[0]), unicode)[1:-1]


@pytest.mark.parametrize("remaining,pace,colour", [(.8, 3, "ok"), (.5, -12, "spend"), (.25, 21, "warn"), (0, 25, "out")])
def test_m1_the_bar_colour_is_the_shared_pace_band(remaining, pace, colour):
    line = row_of(rich(pools_payload(pool("cc", "C-7D", remaining, pace=pace))), "C-7D")
    assert re.search(r"\x1b\[" + mv.RICH_SGR[colour] + rf"m[█░|]{{{mv.BAR_CELLS},{mv.BAR_MAX_CELLS}}}\x1b\[0m", line), line


def test_an_aged_or_unknown_pool_is_dim_never_green():
    aged = pool("cc", "C-7D", .9)
    aged.update(state="STALE", age_seconds=999, measurements=None)
    unknown = pool("cx", "X-7D", None)
    unknown.update(state="UNKNOWN", stored_state="UNKNOWN", measurements=None, last_measurements=None)
    screen = rich(pools_payload(aged, unknown))
    dim = mv.RICH_SGR["dim"]
    aged_line, unknown_line = row_of(screen, "C-7D"), row_of(screen, "X-7D")
    assert mv.RICH_SGR["ok"] not in screen
    bar = re.search(rf"\x1b\[{dim}m({U['none']}{{{mv.BAR_CELLS},{mv.BAR_MAX_CELLS}}})\x1b\[0m", unknown_line)
    assert bar and "%" not in strip(unknown_line)
    faded = mv._bar_quota(.9, len(bar[1]))[1:-1]
    assert f"\x1b[{dim}m{faded}\x1b[0m" in aged_line and "90%" in aged_line
    assert f"\x1b[{dim}m{U['aged']} aged\x1b[0m" in aged_line and f"\x1b[{dim}m{U['aged']} ?\x1b[0m" in unknown_line


def test_m1_a_fast_state_and_its_pace_use_separate_colours():
    row = pool("cx", "X-7D", .5, fast=True, pace=12.5)
    line = row_of(rich(pools_payload(row)), "X-7D")
    assert f"\x1b[{mv.RICH_SGR['warn']}m{U['fast']} fast\x1b[0m" in line
    assert f"\x1b[{mv.RICH_SGR['ok']}m+12.5\x1b[0m" in line


@pytest.mark.parametrize("state,glyph", [("ok", "ok"), ("fast", "fast"), ("low", "low"), ("out", "out"), ("aged", "aged"),
                                         ("?", "aged"), ("reset", "aged")])  # n/a is never a row (#400)
def test_every_pool_state_has_its_glyph_beside_the_state_word(state, glyph):
    row = {"ok": pool("cc", "C-7D", .9), "fast": pool("cc", "C-7D", .5, fast=True, pace=12.5), "low": pool("cc", "C-7D", .05),
           "out": pool("cc", "C-7D", 0.0), "aged": pool("cc", "C-7D", .9), "?": pool("cc", "C-7D", None),
           "reset": pool("cc", "C-7D", .9, reset=-1)}[state]
    if state == "aged":
        row.update(state="STALE", age_seconds=999, measurements=None)
    if state == "?":
        row.update(state="UNKNOWN", stored_state="UNKNOWN", measurements=None, last_measurements=None)
    payload = pools_payload(row)
    assert f" {state} " in row_of(plain(payload), "C-7D") + " "
    assert f"  {U[glyph]} {state}  " in row_of(strip(rich(payload)), "C-7D")
    assert f"  {A[glyph]} {state}  " in row_of(strip(rich(payload, unicode=False)), "C-7D")


def test_m1_the_family_cell_is_dim_and_left_is_neutral():
    line = row_of(rich(pools_payload(pool("cc", "C-7D", .63, pace=-20))), "C-7D")
    assert f"\x1b[{mv.RICH_SGR['dim']}mclaude: cc.effort\x1b[0m" in line
    assert span(line, "63%") is None
    assert f"\x1b[{mv.RICH_SGR['spend']}m-20.0\x1b[0m" in line


# ---- RUNNING, LEADS and RECENT rows ----------------------------------------------------------------------------------
def test_running_rows_start_with_their_state_glyph_and_word():
    payload = full_payload()
    payload["selected_groups"] = ["activity"]  # the 24 lines hold every RUNNING row without POOLS and its suggestion
    screen = rich(payload)
    lines = strip(screen).splitlines()
    start = next(i for i, line in enumerate(lines) if rule_head(line) == "RUNNING 4")
    rows = lines[start + 1:start + 5]
    assert [row.split()[0] for row in rows] == [U["run"], U["gone"], "?", "!"]
    assert rows[1].split()[1] == "gone" and rows[3].split()[1] == "overdue"
    assert rows[0].split()[1] == "16m"  # the elapsed time follows directly
    painted = screen.splitlines()[start + 1:start + 5]
    assert painted[0].startswith(f" \x1b[{mv.RICH_SGR['ok']}m{U['run']}\x1b[0m")
    assert painted[1].startswith(f" \x1b[{mv.RICH_SGR['bad']}m{U['gone']} gone\x1b[0m")
    assert painted[2].startswith(f" \x1b[{mv.RICH_SGR['warn']}m?\x1b[0m")
    assert painted[3].startswith(f" \x1b[{mv.RICH_SGR['warn']}m! overdue\x1b[0m")
    ascii_rows = strip(rich(payload, unicode=False)).splitlines()[start + 1:start + 5]
    assert [row.split()[0] for row in ascii_rows] == [">", "x", "?", "!"]


@pytest.mark.parametrize("over,cell,colour", [
    ({"context_pct": 46}, "ctx ■■□□□ 46%", None), ({"context_pct": 0}, "ctx □□□□□ 0%", None),
    ({"context_pct": 79}, "ctx ■■■■□ 79%", None), ({"context_pct": 80}, "ctx ■■■■□ 80%!", "bad"),
    ({"context_pct": 100}, "ctx ■■■■■ 100%!", "bad"),
    ({"context_pct": None, "context_window": None}, "ctx ───── 106k", "dim"),
    ({"context_pct": None, "context_window": None, "context_tokens": None}, "ctx ───── ?", "dim"),
])
def test_the_context_meter_has_five_cells_and_is_defined_for_every_ctx_form(over, cell, colour):
    payload = quiet_payload()
    payload["groups"]["activity"]["data"]["leads"] = [lead("docs-site", session=sess(**over))]
    line = row_of(rich(payload), "docs-site")
    assert f"  {cell}  " in strip(line)
    meter = cell.split()[1]
    assert span(line, meter) == (mv.RICH_SGR[colour] if colour else None)
    ascii_cell = cell.replace("■", "#").replace("□", ".").replace("─", "-")
    assert f"  {ascii_cell}  " in row_of(rich(payload, unicode=False, color=False), "docs-site")


def test_the_meter_goes_before_a_leads_column_drops():
    payload = quiet_payload()
    payload["groups"]["activity"]["data"]["leads"] = [
        lead("l" * 14, session=sess(alive=None, active_at=NOW - 720, context_pct=100, title=None))]
    for width in range(100, 30, -1):
        old = row_of(plain(payload, width=width), "l" * 14).split()
        new = row_of(strip(rich(payload, width=width)), "l" * 14).split()
        assert [cell for cell in new if not re.fullmatch("[■□]+", cell)] == [cell.replace("~", "…") for cell in old], width
    assert row_of(strip(rich(payload, width=40)), "l" * 14).split() == ["l" * 14, "?busy", "12m", "ctx", "100%!"]


@pytest.mark.parametrize("state,colour", [("ok", "ok"), ("error", "bad"), ("empty", "bad"), ("unsure", "warn")])
def test_recent_rows_have_a_state_dot_in_the_state_colour(state, colour):
    payload = quiet_payload()
    payload["groups"]["activity"]["data"] = activity_data([ask(state, name="done-1", age=120, exact=True, reply_line="r")])
    line = row_of(rich(payload), "done-1")
    assert line.startswith(f" \x1b[{mv.RICH_SGR[colour]}m{U['dot']} {state}\x1b[0m  ")
    assert row_of(rich(payload, unicode=False, color=False), "done-1").startswith(f" {A['dot']} {state}  ")


# ---- glyphs (C5) -----------------------------------------------------------------------------------------------------
def test_every_glyph_is_one_cell_and_has_an_ascii_fallback():
    assert set(U) == set(A)
    for name, glyph in U.items():
        assert len(glyph) == 1 and _width(glyph) == 1, name
        assert unicodedata.east_asian_width(glyph) not in ("W", "F"), name
        assert len(A[name]) == 1 and A[name].isascii(), name
    for banned in "▶✖▰▱◌":  # C5: these are replaced
        assert banned not in "".join(U.values())
    assert (U["run"], U["gone"], U["on"], U["off"], U["aged"]) == ("►", "×", "■", "□", "○")
    assert (U["ok"], U["fast"], U["low"], U["out"]) == ("●", "▲", "▼", "×")


def test_the_rich_view_without_unicode_is_ascii_decoration():
    screen = rich(quiet_payload(), unicode=False, color=False)
    assert screen.isascii()
    assert re.search(rf"-- {POOLS_HEAD} 2\s+STATE", screen) and f"{A['ok']} ok" in screen
    assert re.search(rf"(?<![#.|])[#.|]{{{mv.BAR_CELLS},{mv.BAR_MAX_CELLS}}}(?![#.|])", row_of(screen, "C-7D"))
    assert "-- RUNNING 0 -" in screen and screen.splitlines()[0].startswith("OK | 0 running | 0 leads | min quota")
    painted = rich(quiet_payload(), unicode=False)
    assert strip(painted).isascii() and strip(painted).splitlines()[1:] == screen.splitlines()[1:]


# ---- C4: only our own SGR reaches the terminal -----------------------------------------------------------------------
ESCAPES = ["\x1b[35mX\x1b[0m", "\x1b[2J", "\x1b]0;title\x07", "\x9b35m", "\x1b[38;5;78m", "a\x1bb", "\u202eabc", "\x08\x7f"]


@pytest.mark.parametrize("where", ["ask_line", "reply_line", "lead_title", "name", "lead_label", "profile", "note",
                                   "alert_subject", "pool", "model"])
@pytest.mark.parametrize("dirty", ESCAPES)
def test_no_escape_from_a_text_cell_reaches_the_rich_output(where, dirty):
    payload = quiet_payload()  # small enough that every row is printed
    payload["groups"]["activity"]["data"] = activity_data([
        ask(elapsed=41), ask("empty", name="ended", age=120, exact=True, ask_line=None, note="denied: command"),
        ask("ok", name="done", age=60, exact=True, reply_line="fine")])
    payload["alerts"] = [{"code": "a1", "kind": "empty", "subject": "cc.effort ended", "age_s": 120.0, "severity": "high"}]
    data = payload["groups"]["activity"]["data"]
    data["leads"] = [lead("docs-site", running=1, session=sess())]
    text = f"be{dirty}fore"
    if where == "ask_line":
        data["asks"][0]["ask_line"] = text
    elif where == "reply_line":
        data["asks"][2]["reply_line"] = text
    elif where == "lead_title":
        data["leads"][0]["session"]["title"] = text
    elif where == "name":
        data["asks"][0]["name"] = data["asks"][2]["name"] = text
    elif where == "lead_label":
        data["leads"][0]["label"] = text
        data["asks"][0]["lead"]["label"] = text
    elif where == "profile":
        data["asks"][0]["profile"] = data["asks"][2]["profile"] = text
    elif where == "note":
        data["asks"][1]["note"] = text
    elif where == "alert_subject":
        payload["alerts"][0]["subject"] = text
    elif where == "pool":
        payload["groups"]["capacity"]["data"]["observations"][1]["pool_ref"] = f"quota:cx:{text}"
    else:
        data["leads"][0]["session"]["model"] = text
    for width in (100, 60):
        for expand in (False, True):
            screen = render_grouped_frame(deepcopy(payload), view="rich", color=True, unicode=True, width=width,
                                          expand=expand)
            assert "be" in screen or width < 100  # the cell is printed (a narrow frame may drop its column)
            for raw in ("\x1b[35m", "\x1b[2J", "\x1b]", "\x07", "\x9b", "\u202e", "\x08", "\x7f"):
                assert raw not in screen, (where, width, expand, raw)
            assert "\x1b" not in strip(screen)
            if not expand:  # the compact frame paints with the palette only
                assert all(code in OUR_CODES for code in SGR.findall(screen))
                assert dirty != "\x1b[38;5;78m" or "be\x1b[38;5;78m" not in screen


# ---- --expand: the rich frame is pinned in test_monitor_expand_rich.py; the plain one is never painted ---------------
@pytest.mark.parametrize("build", [full_payload, quiet_payload])
@pytest.mark.parametrize("height", [None, 30])
def test_plain_expand_is_never_painted_and_the_rich_one_uses_the_palette_only(build, height):
    plain_expand = render_grouped_frame(build(), view="plain", color=True, unicode=False, width=120, height=height,
                                        expand=True)
    assert "\x1b" not in plain_expand and plain_expand.isascii()
    painted = render_grouped_frame(build(), view="rich", color=True, unicode=True, width=120, height=height, expand=True)
    assert set(SGR.findall(painted)) <= OUR_CODES and "\x1b" not in strip(painted)
    assert all(_width(line) <= 119 for line in painted.splitlines())


# ---- P9: line 1 and POOLS of one redraw come from the same rows ------------------------------------------------------
def human_payload(*rows: dict[str, Any], min_quota: dict[str, Any] | None) -> dict[str, Any]:
    """What the read model gives the human monitor: its observations for the frame and `min_quota` as of the read."""
    payload = pools_payload(*rows)
    payload["_human_observations"] = payload["groups"]["capacity"]["data"]["observations"]
    payload["min_quota"] = min_quota
    return payload


def verdict(payload: dict[str, Any], **kw: Any) -> str:
    return plain(payload, **kw).splitlines()[0]


def test_at_the_read_line_1_says_what_the_read_model_says():
    payload = human_payload(pool("cc", "C-7D", .12), pool("cx", "X-7D", .89),
                            min_quota={"pool": "cc C-7D", "left_pct": 12, "state": "ok", "aged": False})
    assert verdict(payload).endswith("| min quota 12% cc C-7D ok")
    assert " ok " in row_of(plain(payload), " cc C-7D")


def test_a_redraw_after_the_ttl_shows_the_same_aged_state_on_line_1_and_in_pools():
    payload = human_payload(pool("cc", "C-7D", .12), pool("cx", "X-7D", .89),
                            min_quota={"pool": "cc C-7D", "left_pct": 12, "state": "ok", "aged": False})
    payload["display_elapsed_seconds"] = 300  # age 10 s + 300 s passes the TTL of 300 s during the countdown
    assert " aged " in row_of(plain(payload), " cc C-7D")
    assert verdict(payload).endswith("| min quota 12% cc C-7D aged")
    low = human_payload(pool("cc", "C-7D", .05), min_quota={"pool": "cc C-7D", "left_pct": 5, "state": "low", "aged": False})
    low["display_elapsed_seconds"] = 300
    assert " low " in row_of(plain(low), " cc C-7D") and verdict(low).endswith("| min quota 5% cc C-7D low (aged)")


def test_a_redraw_after_the_reset_moves_min_quota_to_the_lowest_pool_that_still_has_a_value():
    payload = human_payload(pool("cc", "C-7D", .12, reset=30.0), pool("cx", "X-7D", .89),
                            min_quota={"pool": "cc C-7D", "left_pct": 12, "state": "ok", "aged": False})
    payload["display_elapsed_seconds"] = 60
    assert " reset " in row_of(plain(payload), " cc C-7D") and "12%" not in plain(payload)
    assert verdict(payload).endswith("| min quota 89% cx X-7D ok")
    only = human_payload(pool("cc", "C-7D", .12, reset=30.0),
                         min_quota={"pool": "cc C-7D", "left_pct": 12, "state": "ok", "aged": False})
    only["display_elapsed_seconds"] = 60
    assert "min quota" not in verdict(only)


def test_min_quota_of_the_rows_mirrors_the_read_model_rule():
    from peerhub.extensions import monitor_read_model as mrm

    rows = [pool("ag", "G-5H", .01), pool("ag", "G-7D", .40), pool("cc", "C-7D", .40), pool("cx", "X-7D", .07),
            pool("zz", "Q-30D", .02)]
    payload = human_payload(*rows, min_quota=None)
    assert verdict(payload).endswith("| min quota 7% cx X-7D low")  # 7-day pools only; the 5-hour and 30-day rows do not count
    states = mrm.pool_states([{**row, "kind": "quota", "reset_in_seconds": 86400.0} for row in rows], {}, NOW, {})
    assert mrm.min_quota(states) == {"pool": "cx X-7D", "left_pct": 7, "state": "low", "aged": False}
    tie = human_payload(pool("cx", "X-7D", .40), pool("ag", "G-7D", .40), min_quota=None)
    assert verdict(tie).endswith("| min quota 40% ag G-7D ok")  # a tie goes to the first name, as in the read model


def test_a_payload_without_human_observations_keeps_the_min_quota_of_the_read_model():
    payload = pools_payload(pool("cc", "C-7D", .12))
    payload["min_quota"] = {"pool": "ag 3P-7D", "left_pct": 1, "state": "low", "aged": True}
    payload["display_elapsed_seconds"] = 300
    assert verdict(payload).endswith("| min quota 1% ag 3P-7D low (aged)")
    expanded = render_grouped_frame(payload, view="plain", unicode=False, width=120, expand=True)
    assert expanded.splitlines()[0].endswith("| min quota 1% ag 3P-7D low (aged)")  # the same rule in --expand


@pytest.mark.parametrize("elapsed,expected", [(0, "min quota 12% cc C-7D ok"), (300, "min quota 12% cc C-7D aged")])
@pytest.mark.parametrize("view", ["plain", "rich"])
def test_expand_line_1_takes_min_quota_from_the_aged_rows_as_the_compact_frame_does(elapsed, expected, view):
    # One rule in both frames (P9): with the human observations, line 1 names the pool as the rows are at this redraw.
    # (Until 0.24.18's last follow-up --expand kept the read model's `min_quota`, frozen at the read.)
    payload = human_payload(pool("cc", "C-7D", .12), pool("cx", "X-7D", .89),
                            min_quota={"pool": "ag 3P-7D", "left_pct": 1, "state": "low", "aged": True})
    payload["display_elapsed_seconds"] = elapsed
    before = deepcopy(payload)
    sep = " | " if view == "plain" else " · "
    for height in (None, 40):
        expanded = render_grouped_frame(payload, view=view, color=False, unicode=view == "rich", width=120, height=height,
                                        expand=True).splitlines()
        line_1 = re.sub(r" +\d\d:\d\dZ$", "", expanded[0])  # the rich view sets the time at the right edge
        assert line_1.endswith(sep + expected), (height, expanded[0])
        compact = render_grouped_frame(payload, view="plain", unicode=False, width=120, height=height).splitlines()
        assert compact[0].split(" | ")[-1] == line_1.split(sep)[-1] == expected  # line 1 agrees in both frames
    assert payload == before


def test_expand_line_1_moves_min_quota_after_a_reset_and_drops_it_without_a_value():
    payload = human_payload(pool("cc", "C-7D", .12, reset=30.0), pool("cx", "X-7D", .89),
                            min_quota={"pool": "cc C-7D", "left_pct": 12, "state": "ok", "aged": False})
    payload["display_elapsed_seconds"] = 60
    expanded = render_grouped_frame(payload, view="plain", unicode=False, width=120, expand=True).splitlines()
    assert expanded[0].endswith("| min quota 89% cx X-7D ok")
    payload["_human_observations"] = payload["groups"]["capacity"]["data"]["observations"] = [pool("cc", "C-7D", .12, reset=30.0)]
    expanded = render_grouped_frame(payload, view="plain", unicode=False, width=120, expand=True).splitlines()
    assert "min quota" not in expanded[0]


def test_the_rich_view_takes_line_1_and_pools_from_the_same_rows_too():
    payload = human_payload(pool("cc", "C-7D", .12), pool("cx", "X-7D", .89),
                            min_quota={"pool": "cc C-7D", "left_pct": 12, "state": "ok", "aged": False})
    payload["display_elapsed_seconds"] = 300
    screen = strip(rich(payload))
    assert "min quota 12% cc C-7D aged" in screen.splitlines()[0] and f"{U['aged']} aged" in row_of(screen, " cc C-7D")


# ---- P6: a store that never had a quota refresh ----------------------------------------------------------------------
def never_refreshed() -> dict[str, Any]:
    """What the read model gives for such a store: the capacity group read with nothing in it, no error, and the
    capacity, accounts and facts sources marked `no_evidence`."""
    payload = empty_payload()
    payload.update(_human_observations=[],
                   alert_sources={"activity": "read", "capacity": "no_evidence", "accounts": "no_evidence",
                                  "facts": "no_evidence", "reasons": {"capacity": "no quota evidence"}})
    payload["groups"]["capacity"] = {"status": "OK", "data": {"observations": [], "reset_credits": [], "sample_size": 0,
                                                              "truncated": False}}
    return payload


def test_a_never_refreshed_store_shows_the_no_evidence_wording_not_a_read_error():
    payload = never_refreshed()
    before = deepcopy(payload)
    lines = plain(payload).splitlines()
    assert lines[0] == "OK? (quota, accounts, facts not read) | 1 running | 1 lead"
    start = lines.index(f"{POOLS_HEAD} 0")
    assert lines[2] == "quota: no reading -> peerhub observation refresh"
    assert lines[start:start + 3] == [f"{POOLS_HEAD} 0",
                          "no quota evidence; next: peerhub observation refresh",
                          f"{SUGGEST_HEAD} no eligible profiles; next: peerhub observation refresh"]
    assert not any(line.startswith("DB:") for line in lines)  # the store path is printed for a read error only
    assert payload == before
    expanded = render_grouped_frame(payload, view="plain", unicode=False, width=120, expand=True)
    assert "source capacity: no quota evidence" in expanded.splitlines()
    assert "-- QUOTA [OK] | measured remaining -" in expanded and "Quota capacity: no observations" in expanded
    assert "PeerHub Monitor 2026-11-14 23:46Z | DB:env | quota age ?" in expanded  # #412: the compact title
    for height in (30, 60):
        bounded = render_grouped_frame(payload, view="plain", unicode=False, width=120, height=height, expand=True)
        assert "database read failed" not in bounded and "data unavailable" not in bounded


@pytest.mark.parametrize("sources", [{"activity": "read", "capacity": "error", "accounts": "error", "facts": "error"}, None])
def test_a_capacity_read_error_stays_an_error(sources):
    payload = never_refreshed()
    payload.update(status="PARTIAL", errors={"capacity": "OperationalError"})
    payload["groups"]["capacity"] = {"status": "ERROR", "data": None}
    if sources is None:
        payload.pop("alert_sources")
    else:
        payload["alert_sources"] = sources
    payload["alerts"] = [{"code": "a6", "kind": "store", "subject": "capacity: OperationalError", "age_s": None,
                          "severity": "high"}]
    lines = plain(payload).splitlines()
    assert lines[0].startswith("ATTN 1? | store: capacity: OperationalError") and "DB: /w/.peerhub/core.db" in lines
    expanded = render_grouped_frame(payload, view="plain", unicode=False, width=120, expand=True)
    assert "QUOTA [ERROR] | measured remaining" in expanded
    bounded = render_grouped_frame(payload, view="plain", unicode=False, width=120, height=60, expand=True)
    assert "OperationalError: database read failed" in bounded
