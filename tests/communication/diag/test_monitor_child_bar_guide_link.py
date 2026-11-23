"""#414: child pool bars (`▬╌`, `=-`) and the one Guide link (OSC 8) at the right end of the title line."""
from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from peerhub.cli import monitor_view as mv
from peerhub.cli import view
from peerhub.cli.view import FrameOutput
from tests.communication.diag.test_monitor_tree import frame_for

ROOT = Path(__file__).resolve().parents[3]
URL = "https://example.test/blob/vX/docs/monitor.md"
OPEN = f"\x1b]8;;{URL}\x1b\\"
CLOSE = "\x1b]8;;\x1b\\"
SGR = re.compile(r"\x1b\[([0-9;]*)m")
OSC = re.compile(r"\x1b\]8;[^\x07\x1b]*(?:\x1b\\|\x07)")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NO_COLOR", "PEERHUB_NO_LINKS", "PEERHUB_GUIDE_URL"):
        monkeypatch.delenv(name, raising=False)


def tree() -> dict[str, Any]:
    """cc: C-7D (20% left) with its 5-hour child C-5H (10% left)."""
    return frame_for({"cc.effort": ["C-5H", "C-7D"]})


def render(frame: dict[str, Any], **kw: Any) -> str:
    kw = {"view": "rich", "width": 120, **kw}
    return mv.render_grouped_frame(deepcopy(frame), **kw)


def row(screen: str, pool: str) -> str:
    return next(line for line in screen.splitlines() if re.search(rf"\b{pool}\b", line) and "QUOTA" not in line)


def bars(line: str, full: str, empty: str) -> str:
    match = re.search(f"[{re.escape(full)}{re.escape(empty)}]+", mv._ANSI.sub("", line))
    assert match, line
    return match[0]


# ---- child pool bars -------------------------------------------------------------------------------------------------

def test_glyphs_are_one_cell_narrow() -> None:
    for char in "▬╌":
        assert unicodedata.east_asian_width(char) in {"N", "Na"}, (char, unicodedata.east_asian_width(char))
        assert mv._width(char) == 1
    assert mv.RICH_GLYPHS[True]["cfull"] == "▬" and mv.RICH_GLYPHS[True]["cempty"] == "╌"
    assert mv.RICH_GLYPHS[False]["cfull"] == "=" and mv.RICH_GLYPHS[False]["cempty"] == "-"


@pytest.mark.parametrize("cells", [20, 12, 4])
@pytest.mark.parametrize("unicode", [True, False])
def test_pool_bar_child_and_parent_use_distinct_glyphs(cells: int, unicode: bool) -> None:
    item = {"state": "ok", "remaining": 0.5, "health": {"even_left_pct": None}}
    full, empty = ("█", "░") if unicode else ("#", ".")
    cfull, cempty = ("▬", "╌") if unicode else ("=", "-")
    half = int(0.5 * cells + 0.5)
    assert mv._pool_bar(item, cells, unicode) == full * half + empty * (cells - half)
    assert mv._pool_bar(item, cells, unicode, child=False) == full * half + empty * (cells - half)
    assert mv._pool_bar(item, cells, unicode, child=True) == cfull * half + cempty * (cells - half)


@pytest.mark.parametrize("unicode", [True, False])
@pytest.mark.parametrize("remaining,filled", [(0.0, 0), (0.001, 1), (0.024, 1), (0.025, 1), (0.074, 1), (0.075, 2),
                                              (0.5, 10), (0.974, 19), (0.975, 20), (1.0, 20)])
def test_pool_bar_child_edges_never_use_a_space_and_keep_one_cell(remaining: float, filled: int, unicode: bool) -> None:
    cfull, cempty = ("▬", "╌") if unicode else ("=", "-")
    bar = mv._pool_bar({"state": "ok", "remaining": remaining, "health": {"even_left_pct": None}}, 20, unicode, child=True)
    assert bar == cfull * filled + cempty * (20 - filled) and " " not in bar and len(bar) == 20


def test_pool_bar_child_without_a_value_is_the_none_bar() -> None:
    assert mv._pool_bar({"state": "?", "remaining": None, "health": {"even_left_pct": None}}, 6, True, child=True) == "─" * 6
    assert mv._pool_bar({"state": "n/a", "remaining": None, "health": {"even_left_pct": None}}, 6, False, child=True) == "-" * 6


def test_m1_rich_child_bar_uses_one_pace_colour() -> None:
    screen = render(tree(), color=True, expand=True)
    child, parent = row(screen, "C-5H"), row(screen, "C-7D")
    dim = mv.RICH_SGR["dim"]  # the fixture has no anchored pace for either pool
    assert f"\x1b[{dim}m▬{'╌' * (mv.BAR_CELLS - 1)}\x1b[0m" in child
    assert f"\x1b[{dim}m██{'░' * (mv.BAR_CELLS - 2)}\x1b[0m" in parent


def test_m1_rich_child_at_zero_percent_is_bold_red() -> None:
    frame = tree()
    frame["groups"]["capacity"]["data"]["observations"][0]["measurements"]["remaining_fraction"] = 0
    child = row(render(frame, color=True, expand=True), "C-5H")
    assert f"\x1b[{mv.RICH_SGR['out']}m{'╌' * mv.BAR_CELLS}\x1b[0m" in child and "▬" not in child


def test_g_rich_child_at_full_has_no_empty_cells() -> None:
    frame = tree()
    frame["groups"]["capacity"]["data"]["observations"][0]["measurements"]["remaining_fraction"] = 1
    child = row(render(frame, color=True, expand=True), "C-5H")
    assert "▬" * mv.BAR_CELLS in child and "╌" not in child


def test_no_color_keeps_the_glyphs_and_prints_no_escape() -> None:
    screen = render(tree(), color=False, expand=True)
    assert "\x1b" not in screen
    assert bars(row(screen, "C-5H"), "▬", "╌") == "▬" + "╌" * (mv.BAR_CELLS - 1)
    assert bars(row(screen, "C-7D"), "█", "░") == "██" + "░" * (mv.BAR_CELLS - 2)


def test_ascii_child_bar_is_equals_and_dash_parent_stays_hash_and_dot() -> None:
    screen = render(tree(), color=False, unicode=False, expand=True)
    assert "=" + "-" * (mv.BAR_CELLS - 1) in row(screen, "C-5H")
    assert "##" + "." * (mv.BAR_CELLS - 2) in row(screen, "C-7D")


@pytest.mark.parametrize("unicode", [True, False])
def test_g_child_and_parent_bars_use_one_fixed_width_or_yield(unicode: bool) -> None:
    full, empty = ("█", "░") if unicode else ("#", ".")
    cfull, cempty = ("▬", "╌") if unicode else ("=", "-")
    seen: set[int] = set()
    for width in range(32, 110):
        screen = render(tree(), color=True, unicode=unicode, expand=True, width=width)
        top = screen.split("suggest:", 1)[0]
        child, parent = mv._ANSI.sub("", row(top, "C-5H")), mv._ANSI.sub("", row(top, "C-7D"))
        child_bar = re.findall(f"[{re.escape(cfull)}{re.escape(cempty)}]{{4,}}", child)
        parent_bar = re.findall(f"[{re.escape(full)}{re.escape(empty)}]{{4,}}", parent)
        assert len(child_bar) == len(parent_bar) <= 1, (width, child, parent)
        if parent_bar:
            assert len(child_bar[0]) == len(parent_bar[0]) and len(parent_bar[0]) == mv.BAR_CELLS
            seen.add(len(parent_bar[0]))
        else:
            seen.add(0)
        assert all(mv._width(line) <= width - 1 for line in screen.splitlines())
    assert seen == {0, mv.BAR_CELLS}


def test_expand_plain_quota_bar_distinguishes_child_from_parent() -> None:
    screen = render(tree(), view="plain", unicode=False, expand=True, width=180)
    child, parent = row(screen[screen.index("PEER"):], "C-5H"), row(screen[screen.index("PEER"):], "C-7D")
    child_bar = re.search(r"\blow +([=|:-]+)(?= )", child)
    parent_bar = re.search(r"(?<= )[#|:.]+(?= )", parent)
    assert child_bar and parent_bar
    assert len(child_bar[1]) == len(parent_bar[0]) > mv.BAR_CELLS
    assert "=" in child_bar[1] and "#" in parent_bar[0]
    uni = render(tree(), view="plain", unicode=True, expand=True, width=180)
    uni_child = row(uni[uni.index("PEER"):], "C-5H")
    assert "▬" in uni_child and "█" not in uni_child


# ---- the Guide link --------------------------------------------------------------------------------------------------

def title_line(screen: str) -> str:
    return screen.splitlines()[1]


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("color", [False, True])
def test_guide_link_sits_at_the_right_end_of_the_title_line(monkeypatch: pytest.MonkeyPatch, expand: bool, color: bool) -> None:
    monkeypatch.setenv("PEERHUB_GUIDE_URL", URL)
    screen = render(tree(), links=True, color=color, expand=expand, width=100)
    line = title_line(screen)
    assert line.count(OPEN) == 1 and line.count(CLOSE) == 1 and f"{OPEN}Guide{CLOSE}" in line
    assert line.endswith(CLOSE + "\x1b[0m" if color else CLOSE)  # always closed; the dim span (colour on) closes after it
    visible = mv._ANSI.sub("", line)
    assert visible.startswith("PeerHub Monitor") and visible.endswith(" Guide") and mv._width(visible) == 99
    assert view.visible_len(line) == mv._width(line) == 99  # both width helpers ignore the link bytes


def test_no_link_without_the_links_flag_or_with_the_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEERHUB_GUIDE_URL", URL)
    assert "\x1b]8" not in render(tree(), expand=True) and "\x1b]8" not in render(tree())
    monkeypatch.setenv("PEERHUB_NO_LINKS", "1")
    assert "\x1b]8" not in render(tree(), links=True, expand=True) and "\x1b]8" not in render(tree(), links=True)
    monkeypatch.setenv("PEERHUB_NO_LINKS", "0")
    assert "\x1b]8" in render(tree(), links=True)  # only a set value opts out


def test_plain_view_has_no_osc_bytes_even_on_a_supported_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEERHUB_GUIDE_URL", URL)
    for expand in (False, True):
        screen = render(tree(), view="plain", unicode=False, links=True, tty=True, expand=expand)
        assert "\x1b" not in screen and "Guide" not in title_line(screen)


def test_plain_expand_ends_with_one_plain_guide_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEERHUB_GUIDE_URL", URL)
    screen = render(tree(), view="plain", unicode=False, expand=True, tty=True, width=120)
    assert screen.splitlines()[-1] == f"Guide: {URL}" and screen.count("Guide:") == 1
    with monkeypatch.context() as disabled:
        disabled.setenv("PEERHUB_NO_LINKS", "1")
        screen = render(tree(), view="plain", unicode=False, expand=True, tty=True, links=True, width=120)
        assert screen.splitlines()[-1] == f"Guide: {URL}" and "\x1b" not in screen
    bounded = render(tree(), view="plain", unicode=False, expand=True, tty=True, height=60, width=120)
    assert bounded.splitlines()[-1] == f"Guide: {URL}" and bounded.splitlines()[-2].startswith("Coverage:")
    compact = render(tree(), view="plain", unicode=False, width=120, tty=True)
    assert compact.splitlines()[-1] == f"Guide: {URL}"
    assert "Guide:" not in render(tree(), view="rich", expand=True, links=True)  # rich has the link instead
    monkeypatch.delenv("PEERHUB_GUIDE_URL")
    monkeypatch.setattr("peerhub._version.__version__", "0.0.1.dev0")  # no tag: the main branch
    last = render(tree(), view="plain", unicode=False, expand=True, tty=True, width=140).splitlines()[-1]
    assert last == "Guide: https://github.com/greatgc-flow/peerhub/blob/main/docs/monitor.md"
    monkeypatch.setattr("peerhub._version.__version__", "0.0.1")
    last = render(tree(), view="plain", unicode=False, expand=True, tty=True, width=140).splitlines()[-1]
    assert last == "Guide: https://github.com/greatgc-flow/peerhub/blob/v0.0.1/docs/monitor.md"


def test_plain_guide_line_is_never_cut_and_never_wider_than_the_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEERHUB_GUIDE_URL", URL)
    narrow = render(tree(), view="plain", unicode=False, expand=True, tty=True, width=40)
    assert "Guide:" not in narrow and all(mv._width(line) <= 39 for line in narrow.splitlines())


def test_guide_urls() -> None:
    base = "https://github.com/greatgc-flow/peerhub/blob/"
    assert mv.guide_link_url({}, version="0.24.30") == f"{base}v0.24.30/docs/monitor.md"
    assert mv.guide_text_url({}, version="0.24.30") == f"{base}v0.24.30/docs/monitor.md"
    for dev in ("0.24.30.dev1", "0.24.30+local", "0.25.0.dev0+g1234", "0.24.30-dev"):
        assert mv.guide_link_url({}, version=dev) is None
        assert mv.guide_text_url({}, version=dev) == f"{base}main/docs/monitor.md"
    assert mv.guide_link_url({"PEERHUB_GUIDE_URL": URL}, version="0.24.30.dev1") == URL  # the override wins (tests)
    assert mv.guide_link_url({"PEERHUB_GUIDE_URL": URL, "PEERHUB_NO_LINKS": "1"}, version="0.24.30") is None
    assert mv.guide_link_url({"PEERHUB_GUIDE_URL": "http://x/\x1b]8;;evil"}, version="0.24.30") == f"{base}v0.24.30/docs/monitor.md"
    assert mv.guide_link_url({"PEERHUB_GUIDE_URL": "javascript:alert(1)"}, version="0.24.30") == f"{base}v0.24.30/docs/monitor.md"


def test_the_released_version_builds_the_tag_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from peerhub._version import __version__

    expected = mv.guide_link_url({}, version=__version__)
    assert expected is None or expected == f"https://github.com/greatgc-flow/peerhub/blob/v{__version__}/docs/monitor.md"


def test_visible_len_and_width_ignore_osc8() -> None:
    link = f"{OPEN}Guide{CLOSE}"
    assert view.visible_len(link) == 5 == mv._width(link)
    assert view.visible_len(f"\x1b[2mTitle {link}\x1b[0m") == 11 == mv._width(f"\x1b[2mTitle {link}\x1b[0m")
    bel = f"\x1b]8;;{URL}\x07Guide\x1b]8;;\x07"  # the BEL terminator
    assert view.visible_len(bel) == 5 == mv._width(bel)
    assert mv._ANSI.sub("", f"a{link}b") == "aGuideb" == view._ANSI.sub("", f"a{link}b")


def test_truncation_never_cuts_inside_a_link() -> None:
    line = f"PeerHub Monitor 2026-11-20 10:00Z   {OPEN}Guide{CLOSE}"
    for width in range(1, mv._width(line) + 2):
        cut = mv._fit(line, width, True, painted=True)
        assert mv._width(cut) <= width
        assert cut.count("\x1b]8") in (0, 2) and (cut.count("\x1b]8") == 0 or cut.endswith(CLOSE))
        assert "Guide" not in cut or f"{OPEN}Guide{CLOSE}" in cut
    assert mv._fit_fields(f"{OPEN}Guide{CLOSE} | b | c", 8, True, painted=True).count("\x1b]8") == 0


@pytest.mark.parametrize("expand", [False, True])
def test_narrow_width_fits_the_status_around_the_whole_link(monkeypatch: pytest.MonkeyPatch, expand: bool) -> None:
    monkeypatch.setenv("PEERHUB_GUIDE_URL", URL)
    for width in range(12, 130):
        plain = render(tree(), expand=expand, width=width)
        linked = render(tree(), links=True, expand=expand, width=width)
        flat = OSC.sub("", linked).splitlines()
        base = [line for line in plain.splitlines() if not line.startswith(f"{mv.GUIDE_TEXT}: ")]
        assert [line for i, line in enumerate(flat) if i != 1] == [line for i, line in enumerate(base) if i != 1], width
        assert title_line(linked).count(OPEN) == title_line(linked).count(CLOSE) == 1
        assert linked.count("\x1b]8;;") == 2 * linked.count(CLOSE)
        assert flat[1].endswith(" " + mv.GUIDE_TEXT)
        assert mv._width(flat[1]) == (min(100, width) if not expand else width) - 1


def test_link_requires_room_for_the_status_separator_and_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEERHUB_GUIDE_URL", URL)
    width = mv._width(mv.GUIDE_TEXT) + 2  # separator and autowrap column, but no status column yet
    assert "\x1b]8" not in render(tree(), links=True, width=width)
    assert "\x1b]8" in render(tree(), links=True, width=width + 1)


def test_anchored_frame_output_rows_keep_alignment(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("PEERHUB_GUIDE_URL", URL)
    out = FrameOutput(interactive=False, anchor_rows=True)
    out.live = True  # a VT terminal without a real TTY
    screen = render(tree(), links=True, color=True, expand=True, width=100, height=30)
    out.write(screen)
    out.close()
    written = capsys.readouterr().out
    rows = re.findall(r"\x1b\[(\d+);1H(.*?)\x1b\[K", written)
    assert [int(n) for n, _ in rows] == list(range(1, len(screen.splitlines()) + 1))
    assert [text for _, text in rows] == screen.splitlines()  # the frame goes out row for row
    link_rows = [text for _, text in rows if OPEN in text]
    assert len(link_rows) == 1 and link_rows[0].endswith(CLOSE + "\x1b[0m")
    assert all(view.visible_len(text) <= 99 for _, text in rows)
    assert view.visible_len(link_rows[0]) == 99
    assert written.count(OPEN) == 2
    assert written.count("\x1b]8;;") == 2 * written.count(CLOSE)


# ---- the doc ---------------------------------------------------------------------------------------------------------

def test_monitor_guide_doc_exists_and_the_link_targets_it() -> None:
    doc = ROOT / "docs" / "monitor.md"
    assert doc.is_file()
    url = mv.guide_link_url({}, version="1.2.3")
    assert url is not None and url.endswith("/blob/v1.2.3/docs/monitor.md")
    assert (ROOT / url.split("/blob/v1.2.3/")[1]) == doc
    assert mv.guide_text_url({}, version="1.2.3.dev0").endswith("/blob/main/docs/monitor.md")
    text = doc.read_text(encoding="utf-8")
    assert "\r" not in text
    for needle in ("`▬╌`", "`█░`", "`=-`", "`#.`", "PEERHUB_NO_LINKS", "PEERHUB_MONITOR_VIEW", "LIVE_ROW_MARGIN",
                   "version_drift", "F-7D", "alternate screen"):
        assert needle in text, needle
    for word in ("ok", "fast", "threat", "out", "aged", "n/a"):
        assert f"`{word}`" in text, word
    for code in ("a1", "a2", "a3", "a4", "a5", "a6"):
        assert f"`{code}`" in text, code


def test_readme_and_the_explain_lesson_point_at_the_doc() -> None:
    from peerhub.cli.lessons import lesson_by_id  # noqa: PLC0415

    assert "docs/monitor.md" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/monitor.md" in lesson_by_id("monitor-view")[0]["fix"]
