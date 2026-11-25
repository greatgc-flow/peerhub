"""C1, M1, M3–M5, M9 and G through both monitor presentations."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from peerhub.cli import monitor, monitor_format, monitor_layout, monitor_legend, monitor_links, monitor_view as mv
from peerhub.cli.monitor_legend import POOL_MARGIN_LEGEND
from peerhub.cli.view import PLAN_EARLY_TAG, plan_data, render_plan
from peerhub.core.quota_policy import LOW_QUOTA_FRACTION, PLAN_REST_PACE_PT, PLAN_SPEND_PACE_PT, QUOTA_TTL_SECONDS
from peerhub.extensions.adapters.profiles import list_profiles
from peerhub.extensions.pool_health import PACE_GAP_BASELINE, pace_gaps, pool_health
from tests.communication.diag.test_diag_plan_339 import DAY, HOUR, NOW, _item, _report
from tests.communication.diag.test_pool_health import CHILD_LEFT, HALF, WEEK


def reading(peer: str, pool: str, left: float, pace: float, *, age: float = 0,
            span: int = WEEK) -> dict[str, Any]:
    return _item(peer, pool, left, span=span, elapsed=span * (1 - left - pace / 100), age=age)


def frame(*readings: dict[str, Any], profiles: dict[str, list[str]] | None = None) -> dict[str, Any]:
    observations = [{**item, "pool_ref": item["resource_pool_ref"], "peer_id": item["subject_ref"],
                     "last_measurements": item["payload"], "measurements": item["payload"]}
                    for item in readings]
    return {"read_at": NOW, "selected_groups": ["capacity"],
            "configured_profiles": [{"profile": profile, "fields": {"pools": {"value": pools}}}
                                    for profile, pools in (profiles or {}).items()],
            "groups": {"capacity": {"status": "OK", "data": {"observations": observations}}}}


def pool_cells(screen: str, pool: str) -> list[list[str]]:
    return [re.split(r" {2,}", line.lstrip().split(pool, 1)[1].strip())
            for line in monitor_layout.ANSI_ESCAPE.sub("", screen).splitlines()
            if re.match(rf"^\s*(?:\w+ +)?(?:[└├]─ |[|`]- )?{pool} {{2,}}", line)]


def test_cross_view_health_equivalence() -> None:
    parent, child, expired, tiny = "C-7D", "F-7D", "3P-7D", "G-7D"
    standard, fable = "cc.standard", "cc.fable"
    profiles = {standard: [parent], fable: [parent, child]}
    by_pool = {pool: [name for name, pools in profiles.items() if pool in pools] for pool in (parent, child)}
    exhausted = reading("cc", parent, 0, PLAN_SPEND_PACE_PT + 1)
    limited = reading("cc", child, CHILD_LEFT, -PLAN_SPEND_PACE_PT - 1)
    passed = _item("ag", expired, .25, span=WEEK, elapsed=WEEK - 30, age=60)
    small = reading("ag", tiny, .004, 0)
    old_pool, stale_pool = "X-30D", "A-7D"
    old = reading("cx", old_pool, .93, 25, age=2 * QUOTA_TTL_SECONDS, span=30 * DAY)
    stale = reading("ag", stale_pool, .45, 0, age=QUOTA_TTL_SECONDS)
    for item in (old, stale):
        item.update(state="STALE", source="fixture", effective_at_us=int((NOW - item["age_seconds"]) * 1e6))
    current_pool = PACE_GAP_BASELINE
    current = reading("cx", current_pool, .65, -PLAN_SPEND_PACE_PT - 1)
    current.update(source="fixture", effective_at_us=int(NOW * 1e6))
    relative_pool = "G-30D"
    relative_reset = HOUR
    relative = _item("ag", relative_pool, None, span=5 * HOUR, elapsed=0, age=0)
    relative["payload"] = {"reset_in_seconds": relative_reset}
    items = (exhausted, limited, passed, small, old, stale, current, relative)
    data = frame(*items, profiles=profiles)
    plan_name = "pro"
    data["groups"]["capacity"]["data"]["accounts"] = [{"peer_id": "cx", "plan": plan_name, "state": "MEASURED"}]
    report = _report(*items, {"kind": "account", "subject_ref": "cx", "state": "MEASURED",
                              "payload": {"plan": plan_name, "plan_status": "reported"}})
    options = {"view": "plain", "width": 120}
    compact = mv.render_grouped_frame(data, **options)
    expanded = mv.render_grouped_frame(data, expand=True, **options)
    plan = render_plan(report, width=120, profiles_by_pool=by_pool)
    rows: Any = plan_data(report, {}, profiles_by_pool=by_pool)["pools"]
    json_rows = {row["pool"]: row for row in rows}
    plan_rows = {cells[1]: cells[2:] for line in plan.splitlines()
                 if len(cells := line.split(maxsplit=7)) == 8 and cells[1] in json_rows}
    for item, name, state, left in zip(items, (parent, child, expired, tiny),
                                       ("out", "blocked", "reset", "low"), ("0%", "0%", "—", "<1%!")):
        row = json_rows[name]
        effective = row["health"]
        assert effective["state"] == state
        assert row["left_pct"] == pytest.approx(item["payload"]["remaining_fraction"] * 100)
        pace_cell = monitor_format.format_pool_pace(effective["effective_pace_pt"]) + (
            "!" if effective["pace_band"] == "rest" else "")
        facts = pool_health(item, exhausted if name == child else None, NOW)
        assert row["exhaust_in_seconds"] == facts["exhaust_in_seconds"]
        assert row["exhaust_before_reset"] == facts["exhaust_before_reset"]
        assert row["exhaust_margin_seconds"] == facts["exhaust_margin_seconds"]
        assert effective["effective_reset_in_seconds"] == facts["reset_in_seconds"]
        exhaust_cell = monitor_format.format_exhaust_reset(facts)
        margin_cell = monitor_format.format_exhaust_margin(facts["exhaust_margin_seconds"])
        assert plan_rows[name][:3] == [left, pace_cell, exhaust_cell]
        for screen, count in ((compact, 1), (expanded, 2)):
            cells = pool_cells(screen, name)
            assert len(cells) == count
            assert all((row_cells[0], row_cells[2:6]) == (state, [left, pace_cell, margin_cell, exhaust_cell])
                       for row_cells in cells)
    child_json = json_rows[child]
    assert child_json["limited_by"] == exhausted["resource_pool_ref"]
    assert child_json["left_pct"] == child_json["own_left_pct"] == CHILD_LEFT * 100
    assert child_json["pace_pt"] != child_json["health"]["effective_pace_pt"]
    assert child_json["resets_in_seconds"] != child_json["health"]["effective_reset_in_seconds"]
    assert child_json["target_pct_per_hour"] == pytest.approx(CHILD_LEFT * 100 / (child_json["resets_in_seconds"] / HOUR))
    assert child_json["action"] == "blocked" and child_json["binding"] == parent
    assert child_json["reason"] == f"gate: {parent} out"
    assert plan_rows[child][3].startswith("0.00")
    assert plan_rows[child][4:] == ["blocked", child_json["reason"]]
    for screen in (compact, expanded):
        assert old_pool not in screen and "(stale)" not in screen
        assert all(f"~{QUOTA_TTL_SECONDS // 60}m" in " ".join(cells) for cells in pool_cells(screen, stale_pool))
    retired = json_rows[old_pool]
    assert retired["action"] == retired["health"]["state"] == "n/a"
    assert retired["left_pct"] is None
    assert json_rows[current_pool]["binding"] == current_pool and json_rows[current_pool]["action"] == "spend"
    assert "plan omits" in " ".join(plan_rows[old_pool]) and old_pool not in " ".join(plan_rows[current_pool])
    assert json_rows[stale_pool]["stale"] and json_rows[stale_pool]["health"]["state"] == "aged"
    assert f"~{QUOTA_TTL_SECONDS // 60}m" in " ".join(plan_rows[stale_pool])

    expected_reset = monitor_layout.format_duration(relative_reset).removesuffix("00m")
    expected_pair = f"{monitor_format.missing_pool_fact()}/{expected_reset}"
    assert json_rows[relative_pool]["health"]["effective_reset_in_seconds"] == relative_reset
    assert plan_rows[relative_pool][2] == expected_pair
    for screen, count in ((compact, 1), (expanded, 2)):
        cells = pool_cells(screen, relative_pool)
        assert len(cells) == count and all(expected_pair in row for row in cells)


def test_even_burn_tick_replaces_filled_cell_on_collision() -> None:
    pool = "G-7D"
    data = frame(reading("ag", pool, LOW_QUOTA_FRACTION, 0))
    compact, expanded = pool_cells(mv.render_grouped_frame(data, view="plain", unicode=False, expand=True), pool)
    assert compact == expanded[:-1]
    for cells in (compact, expanded):
        bar = cells[1]
        filled = int(LOW_QUOTA_FRACTION * len(bar) + .5)
        assert bar == "#" * (filled - 1) + "|" + "." * (len(bar) - filled)


@pytest.mark.parametrize("exhaust_minutes,reset_minutes,flag", [(5, 40, "!"), (40, 5, "")])
def test_near_tie_exhaustion_and_reset_show_minutes(exhaust_minutes: int, reset_minutes: int, flag: str) -> None:
    pool = "G-7D"
    base = 3 * DAY + 13 * HOUR
    exhaust, reset = base + exhaust_minutes * 60, base + reset_minutes * 60
    item = _item("ag", pool, HALF, span=exhaust + reset, elapsed=exhaust, age=0)
    expected = f"3d13h{exhaust_minutes:02d}m/3d13h{reset_minutes:02d}m{flag}"
    text = render_plan(_report(item), width=120)
    plan_row = next(line.split() for line in text.splitlines() if line.startswith("ag "))
    assert plan_row[4] == expected
    for expand in (False, True):
        rows = pool_cells(mv.render_grouped_frame(frame(item), expand=expand), pool)
        assert len(rows) == (2 if expand else 1)
        assert all(expected in cells for cells in rows)


def test_profile_added_in_models_toml_appears_in_both_family_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, profile = "G-7D", "ag.aaa"
    folder = Path(__file__).resolve().parent
    config = folder.parent / "fixtures" / "monitor" / "models.toml"
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(folder))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("PEERHUB_NO_LINKS", raising=False)
    data = frame(reading("ag", pool, .17, 3.8))
    data["store_selection"] = {"path": str(folder / "store.db"), "source": "env"}
    options = {"view": "rich", "width": 120, "height": None, "color": False, "expand": True, "links": True}
    before = pool_cells(monitor._render_human_frame(data, **options), pool)
    assert len(before) == 2 and all(not any(profile in cell for cell in row) for row in before)
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(config.parent))
    screen = monitor._render_human_frame(data, **options)
    after = pool_cells(screen, pool)
    assert len(after) == 2 and all(any(f"flash: {profile}," in cell for cell in row) for row in after)
    assert sum(monitor_links.osc_link(config.as_uri(), profile) in line
               for line in screen.splitlines() if "flash:" in line) == len(after)


@pytest.mark.parametrize("expand", [False, True])
def test_bounded_frame_evicts_gap_with_pools(expand: bool) -> None:
    data = frame(reading("ag", "G-7D", .5, 0))
    for height in range(3, 20):
        screen = mv.render_grouped_frame(data, view="plain", width=100, height=height, expand=expand)
        shown = "\nPOOLS " in screen
        assert shown == (f"\ngap vs {PACE_GAP_BASELINE}" in screen), (height, screen)
        if height == 5 + monitor_layout.FRAME_TRAILING_ROWS:  # verdict, title, POOLS heading, row and gap
            assert shown


@pytest.mark.parametrize("view", ["rich", "plain"])
@pytest.mark.parametrize("width", [80, 120])
def test_g_same_columns_and_cell_text_in_pools_and_quota(view: str, width: int) -> None:
    pool = "G-7D"
    data = frame(reading("ag", pool, .17, 3.8), profiles={"ag.neon": [pool], "ag.quality": [pool]})
    compact = mv.render_grouped_frame(data, view=view, width=width)
    expanded = mv.render_grouped_frame(data, view=view, width=width, expand=True)
    [compact_cells] = pool_cells(compact, pool)
    expanded_top, quota = pool_cells(expanded, pool)
    assert compact_cells == expanded_top
    header = next(line for line in expanded.splitlines() if line.lstrip().startswith("PEER POOL"))
    assert (quota[:-1] if "AGE" in header.split() else quota) == compact_cells
    assert {"17%", "+3.8"} <= set(quota)
    assert "|" in quota[1]  # M3/G: the even-burn tick in both bars
    if width == 120:
        assert any("ag.neon" in cell for cell in quota)
    else:
        assert any(re.search(r" \+\d+$", cell) for cell in quota)
    for screen in (compact, expanded):
        assert all(name in screen for name in ("STATE", "LEFT", "PACE(pp)", "EXH/RESET", "FAMILY"))
        assert "RESET-IN" not in screen and "REM " not in screen


@pytest.mark.parametrize("pace,colour,suffix", [
    (PLAN_REST_PACE_PT, "ok", ""), (PLAN_REST_PACE_PT + .3, "warn", "!"),
    (-.001, "ok", ""), (-PLAN_SPEND_PACE_PT - .3, "spend", ""),
])
def test_g_pace_precision_and_m1_independent_colours(pace: float, colour: str, suffix: str,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    pool = "G-7D"
    data = frame(reading("ag", pool, .17, pace))
    screen = mv.render_grouped_frame(data, view="rich", color=True, width=120, expand=True)
    expected = ("0.0" if abs(pace) < .01 else f"{pace:+.1f}") + suffix
    assert all(expected in cells for cells in pool_cells(screen, pool))
    assert screen.count(f"\x1b[{monitor_layout.RICH_SGR[colour]}m{expected}\x1b[0m") == 2
    assert not re.search(r"\x1b\[[0-9;]*m17%", screen)


@pytest.mark.parametrize("age,state,left,exhaust", [
    (0, "out", "0%", "OUT"), (QUOTA_TTL_SECONDS, "aged", f"0%! ~{QUOTA_TTL_SECONDS // 60}m", "—"),
])
def test_c1_m4_m9_fresh_and_stale_zero(age: float, state: str, left: str, exhaust: str) -> None:
    pool = "G-7D"
    data = frame(reading("ag", pool, 0, 25, age=age))
    screen = mv.render_grouped_frame(data, view="plain", width=120, expand=True)
    for cells in pool_cells(screen, pool):
        assert state in cells and left in cells
        assert any(cell.startswith(exhaust + "/") for cell in cells)


@pytest.mark.parametrize("pace,flag", [(0, ""), (15, "!")])
def test_m4_compares_shared_forecast_with_its_reset(pace: float, flag: str) -> None:
    pool = "G-7D"
    item = reading("ag", pool, LOW_QUOTA_FRACTION, pace)
    facts = pool_health(item, None, NOW)
    for cells in pool_cells(mv.render_grouped_frame(frame(item), width=120, expand=True), pool):
        [pair] = [cell for cell in cells if "/" in cell]
        assert pair == (f"{monitor_layout.format_duration(facts['exhaust_in_seconds']).removesuffix('00m')}/"
                        f"{monitor_layout.format_duration(facts['reset_in_seconds']).removesuffix('00m')}{flag}")
        assert f"{LOW_QUOTA_FRACTION:.0%}!" in cells


@pytest.mark.parametrize("margin,expected", [
    (-5 * HOUR - .5, "-5h"),
    (-DAY - 2 * HOUR - 30, "-1d02h"),
    (2 * DAY + 3 * HOUR + 30, "+2d03h"),
    (0, "0"),
])
def test_margin_matches_all_four_views(margin: float, expected: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    pool = "G-7D"
    item = _item("ag", pool, HALF, span=WEEK, elapsed=(WEEK + margin) / 2, age=0)
    report = _report(item)
    plan = render_plan(report, width=120)
    plan_row = next(line for line in plan.splitlines() if line.split()[:2] == ["ag", pool])
    assert monitor_legend.POOL_MARGIN_COLUMN not in plan
    assert (PLAN_EARLY_TAG in plan_row) is (margin < 0)
    json_rows: Any = plan_data(report, {})["pools"]
    row = json_rows[0]
    assert row["exhaust_margin_seconds"] == margin
    for expand in (False, True):
        screen = mv.render_grouped_frame(frame(item), view="rich", color=True, width=120, expand=expand)
        rows = pool_cells(screen, pool)
        assert len(rows) == (2 if expand else 1)
        assert all(expected in cells and (expected + "!") not in cells for cells in rows)
        assert screen.count(f"\x1b[{monitor_layout.RICH_SGR['warn']}m{expected}\x1b[0m") == (len(rows) if margin < 0 else 0)


def test_m5_child_lists_configured_top_profiles_and_omitted_count() -> None:
    parent, child = "G-7D", "G-5H"
    profiles = {"ag.neon": [parent, child], **{f"ag.zenith{i}": [parent, child] for i in range(15)}}
    data = frame(reading("ag", parent, .5, 0), reading("ag", child, .05, 5, span=5 * 3600),
                 profiles=profiles)
    children = pool_cells(mv.render_grouped_frame(data, width=110, expand=True), child)
    assert len(children) == 2
    for cells in children:
        assert any(re.fullmatch(r"flash: ag\.neon(?:,.*)? \+\d+", cell) for cell in cells)


def test_m5_packaged_neon_profile_appears_in_both_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    home = Path(__file__).with_suffix("")
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(home))
    data = frame(reading("ag", "G-7D", .17, 3.8))
    data["configured_profiles"] = list_profiles(home, include_meta=True)
    screen = mv.render_grouped_frame(data, view="plain", width=120, expand=True)
    rows = pool_cells(screen, "G-7D")
    assert len(rows) == 2 and all(any("ag.neon" in cell for cell in cells) for cells in rows)


def test_g_width_ladder_drops_details_but_keeps_core_columns() -> None:
    data = frame(reading("ag", "G-7D", .17, 3.8), profiles={"ag.neon": ["G-7D"]})
    seen: set[str] = set()
    margin_only = False
    for width in range(36, 221):
        screen = mv.render_grouped_frame(data, view="plain", width=width, expand=True)
        compact = next(line for line in screen.splitlines() if line.startswith("POOLS "))
        quota = next(line for line in screen.splitlines() if line.startswith("PEER POOL"))
        for column in ("STATE", "LEFT", "PACE(pp)", monitor_legend.POOL_MARGIN_COLUMN, "EXH/RESET", "FAMILY"):
            assert (column in compact) == (column in quota)
            if column in quota:
                seen.add(column)
            else:
                assert column not in seen
        assert all(name in compact for name in ("STATE", "LEFT", "PACE(pp)"))
        assert "AGE" not in quota or "FAMILY" in quota
        assert "FAMILY" not in quota or "EXH/RESET" in quota
        assert "EXH/RESET" not in quota or monitor_legend.POOL_MARGIN_COLUMN in quota
        margin_only |= monitor_legend.POOL_MARGIN_COLUMN in quota and "EXH/RESET" not in quota
        if "EXH/RESET" in quota:
            assert quota.index("PACE(pp)") < quota.index(monitor_legend.POOL_MARGIN_COLUMN) < quota.index("EXH/RESET")
    assert margin_only and "EXH/RESET" in seen


def test_m9_and_g_gap_order_and_single_legend_match_docs() -> None:
    readings = {"C-7D": reading("cc", "C-7D", .5, 2.2),
                PACE_GAP_BASELINE: reading("cx", PACE_GAP_BASELINE, .5, 0),
                "G-7D": reading("ag", "G-7D", .5, -1),
                "A-7D": reading("ag", "A-7D", .5, 20, age=QUOTA_TTL_SECONDS)}
    gaps = pace_gaps({pool: pool_health(item, None, NOW) for pool, item in readings.items()})
    cells = [f"{name} " + ("—" if gap is None else f"{gap:+.1f}")
             for name, gap in gaps["pools"].items() if name in readings]
    line = f"gap vs {PACE_GAP_BASELINE} (pp): " + ", ".join(cells)
    for expand in (False, True):
        screen = " ".join(mv.render_grouped_frame(frame(*readings.values()), width=160, expand=expand).split())
        assert screen.count(monitor_legend.POOL_LEGEND) == 1
        assert screen.index(line) < screen.index(monitor_legend.POOL_LEGEND) < screen.index("suggest:")
    assert POOL_MARGIN_LEGEND in monitor_legend.POOL_LEGEND
    assert monitor_legend.POOL_LEGEND in " ".join(Path("docs/monitor.md").read_text(encoding="utf-8").split())


def test_early_margin_when_exhaustion_and_reset_display_the_same_minute() -> None:
    pool = "G-7D"
    exhaust = 3 * DAY + 13 * HOUR + 5 * 60
    margin_seconds = 30
    reset = exhaust + margin_seconds
    item = _item("ag", pool, HALF, span=exhaust + reset, elapsed=exhaust, age=0)
    expected = f"3d13h05m/3d13h05m!{margin_seconds}s"
    for expand in (False, True):
        rows = pool_cells(mv.render_grouped_frame(frame(item), width=120, expand=expand), pool)
        assert rows and all(expected in cells for cells in rows)
    assert expected in render_plan(_report(item), width=120)


@pytest.mark.parametrize("unicode", [False, True])
def test_missing_pool_cells_match_the_record_and_docs(unicode: bool) -> None:
    pool = "G-7D"
    item = _item("ag", pool, None, span=WEEK, elapsed=HOUR, age=0)
    placeholder = monitor_format.missing_pool_fact(unicode)
    docs = Path("docs/monitor.md").read_text(encoding="utf-8").splitlines()
    left_doc = next(line for line in docs if line.startswith("| LEFT |"))
    pair_doc = next(line for line in docs if line.startswith("| EXH/RESET |"))
    margin_doc = next(line for line in docs if line.startswith(f"| {monitor_legend.POOL_MARGIN_COLUMN} |"))
    assert all(f"`{placeholder}`" in line for line in (left_doc, margin_doc, pair_doc))
    for expand in (False, True):
        screen = mv.render_grouped_frame(frame(item), width=100, unicode=unicode, expand=expand)
        assert "gap vs " not in screen
        with_notes = mv.render_grouped_frame(frame(item), width=100, unicode=unicode, expand=expand, show_quota_notes=True)
        assert f"{pool} {placeholder}" in next(line for line in with_notes.splitlines() if line.startswith("gap vs "))
        rows = pool_cells(screen, pool)
        assert rows and all(placeholder in cells for cells in rows)
        for cells in rows:
            pair_index = next(index for index, cell in enumerate(cells) if cell.startswith(f"{placeholder}/"))
            assert cells[pair_index - 1] == placeholder
    plan = next(line.split() for line in render_plan(_report(item), width=120, unicode=unicode).splitlines()
                if line.split()[:2] == ["ag", pool])
    assert plan[2] == placeholder
    assert plan[4].startswith(f"{placeholder}/")


def test_quota_bar_yields_before_age() -> None:
    pool = "G-7D"
    data = frame(reading("ag", pool, .17, 3.8), profiles={"ag.neon": [pool]})
    def quota(width: int) -> tuple[str, list[str]]:
        screen = mv.render_grouped_frame(data, expand=True, width=width, view="plain", unicode=False)
        return next(line for line in screen.splitlines() if line.startswith("PEER POOL")), pool_cells(screen, pool)[-1]
    wide, full = quota(100)
    narrow, reduced = quota(74)
    assert "AGE" in wide and "AGE" in narrow
    assert len(full[1]) >= monitor_format.BAR_CELLS
    assert not any("|" in cell for cell in reduced)
    assert "AGE" not in quota(45)[0]


@pytest.mark.parametrize("view", ["plain", "rich"])
def test_pool_bars_grow_before_family_in_both_modes(view: str) -> None:
    parent, child = "C-7D", "F-7D"
    profiles = {"cc.effort": [parent], "cc.fable": [parent, child], "cc.fast": [parent], "cc.pro": [parent]}
    parent_left = .26
    parent_reading = reading("cc", parent, parent_left, 12)
    data = frame(parent_reading, reading("cc", child, .65, 0), profiles=profiles)
    even = pool_health(parent_reading, None, NOW)["even_left_pct"]
    assert even is not None
    family = monitor_format.FAMILIES["C"] + ": " + ",".join(profiles)
    lengths: list[int] = []
    for width in (60, 80, 100, 120, 140, 160, 190, 220):
        compact, expanded = (mv.render_grouped_frame(data, view=view, width=width, unicode=False, expand=expand)
                             for expand in (False, True))
        assert all(monitor_layout.cell_width(line) < width for screen in (compact, expanded) for line in screen.splitlines())
        header = next(line for line in expanded.splitlines() if line.lstrip().startswith("PEER POOL"))
        for pool in (parent, child):
            [cells] = pool_cells(compact, pool)
            pools, quota = pool_cells(expanded, pool)
            assert cells == pools == (quota[:-1] if "AGE" in header else quota), (width, pool)
            bar = next((cell for cell in cells if re.fullmatch(r"[#.:=|-]+", cell)), "")
            if pool == parent:
                lengths.append(len(bar))
                if width >= 80:  # the compact view already had a bar at 80 and 100 columns
                    assert len(bar) >= monitor_format.BAR_CELLS, (width, cells)
                if width in (80, 100):
                    assert family not in cells and any(cell.startswith(monitor_format.FAMILIES["C"]) for cell in cells)
                if width >= 140:
                    assert family in cells
                if family in cells:
                    assert len(bar) >= monitor_format.BAR_MIDDLE_CELLS
            else:
                assert len(bar) == lengths[-1]
            if bar:
                tick = min(len(bar) - 1, max(0, int(even * len(bar) / 100)))
                assert bar.count("|") == 1 and bar[tick] == "|"
                if pool == child:
                    filled = int(parent_left * len(bar) + .5)
                    expected = "=" * filled + "-" * (len(bar) - filled)
                    assert bar == expected[:tick] + "|" + expected[tick + 1:]
        if "FAMILY" in header:
            quota_row = next(line for line in expanded[expanded.index(header):].splitlines()
                             if line.lstrip().startswith("cc C-7D"))
            assert quota_row.index("%") + 1 == header.index("LEFT") + len("LEFT")
            pools_header = next(line for line in compact.splitlines() if "POOLS " in line)
            compact_row = next(line for line in compact.splitlines() if line.lstrip().startswith("cc C-7D"))
            for column in ("STATE", "PACE(pp)", monitor_legend.POOL_MARGIN_COLUMN, "EXH/RESET", "FAMILY"):
                assert quota_row[header.index(column)] != " ", (width, column)
                assert compact_row[pools_header.index(column)] != " ", (width, column)
    assert lengths == sorted(lengths)
    assert all(length == 0 or monitor_format.BAR_CELLS <= length <= monitor_format.BAR_MAX_CELLS for length in lengths)
    assert lengths[0] == 0 and lengths[2] == monitor_format.BAR_MIDDLE_CELLS
    assert lengths[3] < lengths[-1] == monitor_format.BAR_MAX_CELLS


def test_full_family_appears_as_soon_as_it_fits_with_middle_bar() -> None:
    pool = "G-7D"
    profiles = {f"ag.{name}": [pool] for name in ("deepthink", "effort", "neon", "standard")}
    data = frame(reading("ag", pool, .26, 12), profiles=profiles)
    family = monitor_format.FAMILIES["G"] + ": " + ",".join(profiles)
    options = {"view": "plain", "unicode": False}
    wide = mv.render_grouped_frame(data, expand=True, width=200, **options)
    header = next(line for line in wide.splitlines() if line.startswith("PEER POOL"))
    bar = pool_cells(wide, pool)[-1][1]
    # Replace the wide bar with the middle size, then allow for AGE, the row indent and autowrap.
    first_fit = (header.index("FAMILY") - len(bar) + monitor_format.BAR_MIDDLE_CELLS + len(family)
                 + len(monitor_format._POOL_COLUMN_GAP) + len("AGE") + 2)
    for expand in (False, True):
        prior = mv.render_grouped_frame(data, expand=expand, width=first_fit - 1, **options)
        exact = mv.render_grouped_frame(data, expand=expand, width=first_fit, **options)
        prior_rows, exact_rows = pool_cells(prior, pool), pool_cells(exact, pool)
        assert prior_rows and all(family not in cells for cells in prior_rows)
        assert exact_rows and all(family in cells for cells in exact_rows)


def test_monitor_docs_describe_pool_bar_limits() -> None:
    docs = " ".join(Path("docs/monitor.md").read_text(encoding="utf-8").split())
    assert f"from {monitor_format.BAR_CELLS} to {monitor_format.BAR_MIDDLE_CELLS} cells" in docs
    assert f"up to {monitor_format.BAR_MAX_CELLS} cells" in docs
