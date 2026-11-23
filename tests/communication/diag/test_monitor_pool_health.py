"""C1, M1, M3–M5, M9 and G through both monitor presentations."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from peerhub.cli import monitor_view as mv
from peerhub.core.quota_policy import LOW_QUOTA_FRACTION, PLAN_REST_PACE_PT, PLAN_SPEND_PACE_PT, QUOTA_TTL_SECONDS
from peerhub.extensions.adapters.profiles import list_profiles
from peerhub.extensions.pool_health import PACE_GAP_BASELINE, pace_gaps, pool_health
from tests.communication.diag.test_diag_plan_339 import NOW, _item
from tests.communication.diag.test_pool_health import WEEK


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
            for line in mv._ANSI.sub("", screen).splitlines()
            if re.match(rf"^\s*(?:\w+ +)?(?:[└├]─ |[|`]- )?{pool} {{2,}}", line)]


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
    assert quota[:len(compact_cells)] == compact_cells
    assert {"17%", "+3.8"} <= set(quota)
    assert "|" in quota[1]  # M3/G: the even-burn tick in both bars
    assert any("ag.neon" in cell for cell in quota)
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
    assert screen.count(f"\x1b[{mv.RICH_SGR[colour]}m{expected}\x1b[0m") == 2
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
        assert pair == (f"{mv._format_duration(facts['exhaust_in_seconds']).removesuffix('00m')}/"
                        f"{mv._format_duration(facts['reset_in_seconds']).removesuffix('00m')}{flag}")
        assert f"{LOW_QUOTA_FRACTION:.0%}!" in cells


def test_m5_child_lists_configured_top_profiles_and_omitted_count() -> None:
    parent, child = "G-7D", "G-5H"
    profiles = {"ag.neon": [parent, child], **{f"ag.zenith{i}": [parent, child] for i in range(15)}}
    data = frame(reading("ag", parent, .5, 0), reading("ag", child, .05, 5, span=5 * 3600),
                 profiles=profiles)
    children = pool_cells(mv.render_grouped_frame(data, width=80, expand=True), child)
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
    for width in range(36, 105):
        screen = mv.render_grouped_frame(data, view="plain", width=width, expand=True)
        compact = next(line for line in screen.splitlines() if line.startswith("POOLS "))
        quota = next(line for line in screen.splitlines() if line.startswith("PEER POOL"))
        for column in ("STATE", "LEFT", "PACE(pp)", "EXH/RESET", "FAMILY"):
            assert (column in compact) == (column in quota)
        assert all(name in compact for name in ("STATE", "LEFT", "PACE(pp)"))
        assert "AGE" not in quota or "FAMILY" in quota
        assert "FAMILY" not in quota or "EXH/RESET" in quota


def test_m9_and_g_gap_order_and_single_legend_match_docs() -> None:
    readings = {"C-7D": reading("cc", "C-7D", .5, 2.2),
                PACE_GAP_BASELINE: reading("cx", PACE_GAP_BASELINE, .5, 0),
                "G-7D": reading("ag", "G-7D", .5, -1),
                "A-7D": reading("ag", "A-7D", .5, 20, age=QUOTA_TTL_SECONDS)}
    gaps = pace_gaps({pool: pool_health(item, None, NOW) for pool, item in readings.items()})
    cells = [f"{name} " + ("—" if gap is None else f"{gap:+.1f}") for name, gap in gaps["pools"].items()]
    line = f"gap vs {PACE_GAP_BASELINE} (pp): " + ", ".join(cells)
    for expand in (False, True):
        screen = " ".join(mv.render_grouped_frame(frame(*readings.values()), width=160, expand=expand).split())
        assert screen.count(mv.POOL_LEGEND) == 1
        assert screen.index(line) < screen.index(mv.POOL_LEGEND) < screen.index("suggest:")
    assert mv.POOL_LEGEND in Path("docs/monitor.md").read_text(encoding="utf-8")
