"""Compact human monitor contract; JSON and expanded detail remain separate."""
from __future__ import annotations

from copy import deepcopy
import json
import re
from pathlib import Path

import pytest

from peerhub.cli.monitor_format import (
    BAR_CELLS,
    _bar_quota,
)
from peerhub.cli.monitor_layout import cell_width
from peerhub.cli.monitor_legend import (
    POOL_LEGEND,
    POOL_MARGIN_COLUMN,
)
from peerhub.cli.monitor_legend import FRAME_SECTIONS
from peerhub.cli.monitor_quota_view import POOLS_USE_FIRST_HINT
from peerhub.cli.monitor_view import render_grouped_frame
from tests.communication.diag.test_monitor_activity_view import anchor_pool
from peerhub.core.quota_policy import LOW_QUOTA_FRACTION

NOW = 1794134880.0  # 2026-11-08 10:48Z


@pytest.fixture
def compact_frame():
    def pool(peer, name, remaining, *, state="MEASURED", age=60, reset=3600):
        return {"observation_id": f"{peer}-{name}", "peer_id": peer, "pool_ref": f"quota:{peer}:{name}",
                "state": state, "stored_state": "MEASURED", "age_seconds": age, "ttl_seconds": 300,
                "measurements": {"remaining_fraction": remaining} if state == "MEASURED" else None,
                "last_measurements": {"remaining_fraction": remaining}, "resets_at": NOW + reset,
                "projection": {"state": "insufficient"}}
    rows = [pool("ag", "3P-5H", .01, state="STALE", age=540),
            pool("ag", "3P-7D", 0, state="STALE", age=540),
            pool("ag", "A-7D", .55, state="STALE", age=540),
            pool("ag", "G-5H", .80),
            pool("ag", "G-7D", .55, state="STALE", age=540, reset=0),
            pool("cc", "F-7D", None),
            pool("cx", "X-5H", None, state="ABSENT", reset=-1),
            pool("cx", "X-7D", .89)]
    rows[6].update(stored_state="ABSENT", reason="not reported by provider; plan=prolite")
    profiles = [{"profile": name, "fields": {"pools": {"value": pools}}} for name, pools in (
        ("ag.standard", ["G-5H", "G-7D"]), ("ag.probe", ["A-7D"]),
        ("ag.deepthink", ["3P-5H", "3P-7D"]), ("cc.effort", ["C-5H", "C-7D"]),
        ("cx.effort", ["X-5H", "X-7D"]))]
    return {"schema_version": "2.0", "status": "PARTIAL", "read_at": NOW,
            "selected_groups": ["capacity", "peers", "sessions", "execution", "usage"],
            "store_selection": {"path": "/work/.peerhub/core.db", "source": "discovered"},
            "_nearest_store_path": "/work/.peerhub/core.db",
            "configured_profiles": profiles, "groups": {
                "capacity": {"status": "OK", "data": {"observations": rows}},
                "peers": {"status": "OK", "data": {"peers": [{"peer_id": p} for p in ("ag", "cc", "cx")],
                    "facts_report": {"status": "ok", "checked_at": "2026-11-08T10:45:00Z"}}},
                "sessions": {"status": "OK", "data": {"items": [{"state": "ACTIVE"}, {"state": "ACTIVE"}],
                    "retained_sessions_by_peer": {"ag": {"value": 100, "complete": True}}}},
                "execution": {"status": "OK", "data": {"scopes": [{"certainty": {
                    "terminal": 166, "started": 15, "not_started": 2, "may_have_started": 1}}]}},
                "usage": {"status": "OK", "data": {"coverage_by_scope": [{"measured": 56, "terminal": 60}]}}
            }}


def _row(screen, pool):
    return next(line for line in screen.splitlines() if pool in line and re.search(r"(?:^|\s)(?:[|`]-\s|[├└]─\s)?" + re.escape(pool) + r"(?:\s|$)", line)
                and "plan " not in line and "PEERS" not in line)


def _header(screen):
    """The POOLS head: a line of words in the plain view, a rule in the rich view (P21); its titles sit over the columns."""
    return next(line for line in screen.splitlines() if line.startswith(("POOLS ", "── POOLS ", "-- POOLS ")))


def _solo(frame, drop=":G-7D", **kw):
    """Drop one row from the fixture before rendering."""
    frame = deepcopy(frame)
    rows = frame["groups"]["capacity"]["data"]["observations"]
    rows[:] = [row for row in rows if not str(row.get("pool_ref")).endswith(drop)]
    return render_grouped_frame(frame, **kw)


def test_pool_screen_layout_state_precedence_and_retired_reset(compact_frame):
    # G4: POOLS uses the shared columns and includes the nonbinding short window.
    screen = render_grouped_frame(compact_frame, view="plain", unicode=False, width=100)
    assert screen.splitlines()[1] == "PeerHub Monitor 2026-11-08 10:48Z | DB:discovered | quota age 9m"
    assert "/work/.peerhub/core.db" not in screen
    assert "next: peerhub observation refresh" in screen
    assert _header(screen).split() == ["POOLS", "6", *COMPACT_COLUMNS, *POOLS_USE_FIRST_HINT.split()]
    for name, state, value in (("3P-5H", "low", "1%"), ("3P-7D", "aged", "0%!"),
                                ("A-7D", "aged", "55%"), ("F-7D", "?", None),
                                ("X-7D", "ok", "89%")):
        row = _row(screen, name)
        assert state in row.split()
        assert "%" not in row if value is None else value in row
    assert screen.index(_row(screen, "3P-7D")) < screen.index(_row(screen, "3P-5H")) < screen.index(_row(screen, "X-7D"))
    assert "80%" in _row(screen, "G-5H")
    assert "G-7D" not in screen and "X-5H" not in screen
    assert "claude-via-ag" in _row(screen, "3P-7D")
    for old in ("EXECUTION [", "SESSIONS [", "USAGE [", "EXHAUST-IN", "Columns omitted", "latest session:", "ok enough",
                "PEERS", "facts ok", "sessions >=", "delivery", "telemetry", "unmapped", "deepthink 0%"):
        assert old not in screen


@pytest.mark.parametrize("width", [80, 100])
@pytest.mark.parametrize("view,unicode_mode", [("plain", False), ("rich", True)])
@pytest.mark.parametrize("pace,expected", [(19.4, "+19.40"), (-12.4, "-12.40"), (0, "0.00"), (-.1, "-0.10")])
@pytest.mark.parametrize("aged", [False, True])
def test_compact_pace_cells_keep_signed_measurement_for_aged_rows(
        compact_frame, width, view, unicode_mode, pace, expected, aged):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(projection={"state": "stale" if aged else "on_pace", "pace_pt": pace},
               state="STALE" if aged else "MEASURED", age_seconds=540 if aged else 60)
    anchor_pool(row, NOW, pace)  # C1 uses the raw anchors. G2 shows signed hundredths without arrows.
    screen = _solo(compact_frame, width=width, view=view, unicode=unicode_mode)
    header = _header(screen)
    assert header.index("PACE(pp)") < header.index("EXH/RESET")
    if width == 100 or "FAMILY" in header:
        assert header.index("EXH/RESET") < header.index("FAMILY")
    assert expected in _row(screen, "G-5H").split()
    assert "80%" in _row(screen, "G-5H")


@pytest.mark.parametrize("width", [80, 100])
@pytest.mark.parametrize("unusable", ["?", "missing", "nan", "inf", "bool", "invalid"])
def test_compact_pace_is_dash_without_usable_evidence(compact_frame, width, unusable):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row["projection"] = {"pace_pt": 46}
    if unusable == "?": row.update(state="UNKNOWN")
    if unusable == "missing": row["projection"] = {}
    if unusable == "nan": row["projection"]["pace_pt"] = float("nan")
    if unusable == "inf": row["projection"]["pace_pt"] = float("inf")
    if unusable == "bool": row["projection"]["pace_pt"] = True
    if unusable == "invalid": row.update(last_measurements={"remaining_fraction": 1.1})
    screen = _solo(compact_frame, width=width, view="plain")
    header = _header(screen)
    pool = row["pool_ref"].split(":")[-1]
    line = next(line for line in screen.splitlines() if f" {pool} " in line)
    assert "—" in line.split() and "PACE(pp)" in header  # C1/G: unavailable pace


@pytest.mark.parametrize("width", [80, 100])
@pytest.mark.parametrize("elapsed", [0, 60])
@pytest.mark.parametrize("reset_field", ["resets_at", "reset_in_seconds"])
def test_exact_reset_hides_short_window_without_weekly_parent(compact_frame, width, elapsed, reset_field):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(projection={"state": "on_pace", "pace_pt": 46})
    row.pop("resets_at")
    row[reset_field] = NOW + elapsed if reset_field == "resets_at" else elapsed
    compact_frame["groups"]["capacity"]["data"]["observations"] = [row]
    compact_frame["configured_profiles"] = [
        {"profile": "ag.effort", "fields": {"pools": {"value": ["G-5H"]}}}]
    compact_frame["display_elapsed_seconds"] = elapsed
    screen = render_grouped_frame(compact_frame, width=width, view="plain")
    assert "POOLS 0" in _header(screen)
    assert "G-5H" not in screen and "PACE(pp)" not in screen


@pytest.mark.parametrize("width", [60, 70])
def test_narrow_pool_table_keeps_pace_and_quota_facts(compact_frame, width):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    anchor_pool(row, NOW, 5)
    screen = _solo(compact_frame, width=width, view="plain", unicode=False)
    pool = _row(screen, "G-5H")
    assert "80%" in pool and "ok" in pool.split()
    cells = pool.split()
    header = _header(screen)
    assert POOL_MARGIN_COLUMN in header
    assert cells[cells.index("+5.00") + 1].startswith("-")
    assert any(cell.endswith("/1h!") for cell in cells) == ("EXH/RESET" in header)
    assert all(cell_width(line) <= width for line in screen.splitlines())


@pytest.fixture
def ten_pool_frame(compact_frame):
    rows = compact_frame["groups"]["capacity"]["data"]["observations"]
    for name in ("C-5H", "C-7D"):
        rows.append({**deepcopy(rows[3]), "observation_id": f"cc-{name}",
                     "peer_id": "cc", "pool_ref": f"quota:cc:{name}"})
    for index, row in enumerate(rows):
        row["projection"] = {"pace_pt": 46 if index % 2 else -12}
    return compact_frame


@pytest.mark.parametrize("width", [80, 100])
def test_ten_pool_compact_frame_stays_within_1800_bytes(ten_pool_frame, width):
    screen = render_grouped_frame(ten_pool_frame, width=width, view="plain", unicode=False)
    assert len(ten_pool_frame["groups"]["capacity"]["data"]["observations"]) == 10
    assert len(screen.encode("utf-8")) <= 1800
    assert "PACE" in screen and "precedence:" not in screen


@pytest.mark.parametrize("samples, expected", [(None, "ok"), (1, "ok"), (2, "fast")])
def test_projection_fast_requires_two_samples(compact_frame, samples, expected):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(projection_samples=samples)
    anchor_pool(row, NOW, 5)
    assert expected in _row(_solo(compact_frame, width=100), "G-5H").split()


@pytest.mark.parametrize("remaining,projection,samples,age,reset,expected", [
    (LOW_QUOTA_FRACTION, "safe", 0, 60, 3600, "low"),
    (LOW_QUOTA_FRACTION, "threat", 2, 301, 3600, "low"),
    (LOW_QUOTA_FRACTION + .000001, "threat", 2, 60, 3600, "fast"),
    (.8, "threat", 2, 301, 3600, "aged"),
    (.8, "threat", 1, 301, 3600, "aged"),
    (.8, "safe", 2, 60, 3600, "ok"),
    (0, "threat", 2, 301, 3600, "aged"),
    (.05, "threat", 2, 301, 0, "reset"),
    (None, "threat", 2, 60, -1, "?"),
])
def test_low_fast_and_higher_priority_states(compact_frame, remaining, projection, samples, age, reset, expected):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(last_measurements={"remaining_fraction": remaining}, projection={"state": projection},
               projection_samples=samples, age_seconds=age, resets_at=NOW + reset)
    if remaining is not None:
        anchor_pool(row, NOW, min(15, (1 - remaining) * 50) if projection == "threat" else 0)
    original = deepcopy(compact_frame)
    assert render_grouped_frame(compact_frame, view="plain", width=100) and compact_frame == original
    screen = _solo(compact_frame, view="plain", width=100)
    if expected == "reset":
        assert "G-5H" not in screen
    else:
        assert expected in _row(screen, "G-5H").split()


@pytest.mark.parametrize("stored_state,expected", [("ABSENT", "aged"), ("UNKNOWN", "?"), ("ERROR", "?")])
def test_stale_row_without_number_is_aged_only_for_absent(compact_frame, stored_state, expected):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(last_measurements={"remaining_fraction": None}, stored_state=stored_state, age_seconds=99999,
               resets_at=NOW + 3600)
    assert expected in _row(_solo(compact_frame, view="plain", width=100), "G-5H").split()


@pytest.mark.parametrize("view,unicode_mode", [("plain", False), ("rich", True)])
@pytest.mark.parametrize("width", [48, 80, 100])
def test_compact_legend_explains_states_and_fits_width(compact_frame, view, unicode_mode, width):
    # M9/G8: both modes render the shared quota legend.
    legend = "ok enough | low >0-10% | fast >10% + threat (2+ samples) | out 0% | aged stale | reset passed | n/a plan no longer reports it (diag quota may keep an older value) | ? unknown"
    screen = render_grouped_frame(compact_frame, view=view, unicode=unicode_mode, width=width)
    assert "ok enough" not in screen and POOL_LEGEND in " ".join(screen.split())
    expanded = render_grouped_frame(compact_frame, view=view, unicode=unicode_mode, width=width, expand=True)
    flat = " ".join(expanded.split())
    assert (legend if view == "plain" else legend.replace(" | ", " · ")) in flat  # the rich view's separator
    assert POOL_LEGEND in flat
    assert "the header time is the snapshot time" in flat
    assert "precedence:" not in screen
    assert all(cell_width(line) <= min(99, width - 1) for line in screen.splitlines())
    assert all(cell_width(line) <= width - 1 for line in expanded.splitlines())


def test_m1_fast_with_threat_forecast_uses_state_warning_colour(compact_frame, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(projection_samples=2)
    anchor_pool(row, NOW, 5)
    from peerhub.cli.monitor_layout import RICH_SGR

    screen = _solo(compact_frame, view="rich", color=True, width=100)
    line = next(line for line in screen.splitlines() if "G-5H" in line and "fast" in line)
    warn = f"\x1b[{RICH_SGR['warn']}m"
    assert not line.startswith("\x1b") and f"{warn}▲ fast\x1b[0m" in line
    assert f"\x1b[{RICH_SGR['ok']}m+5.00\x1b[0m" in line  # M1: the pace band is independent of STATE.


def test_ttl_boundary_and_next_absent_when_fresh(compact_frame):
    compact_frame["groups"]["capacity"]["data"]["observations"] = [
        compact_frame["groups"]["capacity"]["data"]["observations"][3]]
    row = compact_frame["groups"]["capacity"]["data"]["observations"][0]
    compact_frame["configured_profiles"] = [{"profile": "ag.standard", "fields": {"pools": {"value": ["G-5H"]}}}]
    row.update(age_seconds=599, ttl_seconds=600)
    assert "next:" not in render_grouped_frame(compact_frame)
    row["age_seconds"] = 600
    screen = render_grouped_frame(compact_frame)
    assert "aged" in _row(screen, "G-5H").split()
    assert "next: peerhub observation refresh" in screen


@pytest.mark.parametrize("source", ["discovered", "explicit", "env", "default"])
@pytest.mark.parametrize("same,status", [(True, "OK"), (False, "OK"), (True, "FAILED"), (True, "ERROR")])
def test_database_path_selection(compact_frame, source, same, status):
    compact_frame.update(status=status, store_selection={"path": "/work/.peerhub/core.db", "source": source},
                         _nearest_store_path="/work/.peerhub/core.db" if same else "/other/.peerhub/core.db")
    screen = render_grouped_frame(compact_frame, width=100)
    assert ("/work/.peerhub/core.db" in screen) == (not same or status != "OK")


@pytest.mark.parametrize("errors,show_path", [({"scope_cap": "truncated"}, False),
    ({"capacity": "OperationalError"}, True)])
def test_database_path_ignores_sampling_caps_but_shows_read_errors(compact_frame, errors, show_path):
    compact_frame["errors"] = errors
    screen = render_grouped_frame(compact_frame, view="plain")
    assert ("/work/.peerhub/core.db" in screen) == show_path


@pytest.mark.parametrize("width", [20, 32, 47, 48, 64, 80, 100])
def test_width_and_narrow_path_never_cut_numbers(compact_frame, width):
    compact_frame["configured_profiles"][0]["profile"] = "ag." + "long" * 60
    screen = render_grouped_frame(compact_frame, width=width, unicode=False)
    assert all(cell_width(line) <= width for line in screen.splitlines())
    # #374: the footer with "details: --expand" left the compact frame; below 26 columns POOLS keeps POOL and STATE
    # whole (LEFT drops as a column, never as a cut number).
    head = re.escape(FRAME_SECTIONS["pools"])
    pools = re.split(rf"\n(?:-- )?{head} ", screen, maxsplit=1)[1]  # P21: rich with ASCII glyphs draws a rule
    pools = pools.split("\nPACE(pp)=", 1)[0]
    hidden = re.search(r"(?m)^\+(\d+) pools", pools)
    if width == 20:
        assert {"0%!", "1%!"} <= set(pools.split())
        assert hidden and int(hidden[1]) == 6 - 3  # three visible rows, including their wrapped lines
    else:
        assert "55%" in pools and ("89%" in pools or hidden)  # G4: LEFT stays.
    if width < 48:
        assert "PEER  POOL" not in screen  # labelled vertical pool rows on narrow terminals


def test_unknown_and_all_absent_are_never_zero_or_unlimited(compact_frame):
    rows = compact_frame["groups"]["capacity"]["data"]["observations"]
    compact_frame["groups"]["capacity"]["data"]["observations"] = [rows[6]]
    screen = render_grouped_frame(compact_frame, width=100)
    assert "X-5H" not in screen and "POOLS 0" in screen
    assert "100%" not in screen and "%" not in screen


def test_human_render_does_not_mutate_json_and_expand_keeps_details(compact_frame):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(projection_samples=2)
    anchor_pool(row, NOW, 5)
    compact_frame["configured_profiles"].extend([
        {"profile": name, "fields": {"pools": {"value": pools}}} for name, pools in (
            ("ag.effort", ["G-5H"]), ("cx.standard", ["X-7D"]))])
    before = json.dumps(compact_frame, ensure_ascii=True, separators=(",", ":")).encode()
    original = deepcopy(compact_frame)
    screen = render_grouped_frame(compact_frame, width=100)
    assert "fast" in _row(_solo(compact_frame, width=100), "G-5H").split()
    assert "fast" in _row(screen, "G-5H").split()
    for view in ("plain", "rich"):
        expanded = render_grouped_frame(compact_frame, view=view, width=100, expand=True)
        assert "EXH/RESET" in expanded  # G3
        assert "SESSIONS [" in expanded and "USAGE [" in expanded
    assert json.dumps(compact_frame, ensure_ascii=True, separators=(",", ":")).encode() == before
    assert compact_frame == original


def test_human_only_last_measurements_are_read_only_and_json_bytes_unchanged(tmp_path, capsys, monkeypatch):
    from peerhub.cli.app import main
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.extensions.observation_model import EvidenceState, Observation, ResourcePool, epoch_to_iso
    from tests.communication.harness.observation import ObservationHarness
    h = ObservationHarness(tmp_path / "evidence")
    h.obs.register_resource_pool(ResourcePool(resource_pool_id="quota:ag:A-7D", provider="ag", kind="QUOTA"))
    h.obs.persist(Observation(observation_id="old", subject_ref="ag", resource_pool_ref="quota:ag:A-7D",
        kind="quota", source="test", state=EvidenceState.MEASURED,
        observed_at=epoch_to_iso(NOW - 540), captured_at=epoch_to_iso(NOW - 540),
        payload={"remaining_fraction": .55, "resets_at": NOW + 3600}))
    payload = render_monitor_frame(str(h.db_path), ["capacity"], read_at=NOW)
    assert payload["groups"]["capacity"]["data"]["observations"][0]["measurements"] is None
    human = render_monitor_frame(str(h.db_path), ["capacity"], read_at=NOW, human=True)
    monkeypatch.setattr("peerhub.extensions.monitor_read_model.render_monitor_frame",
                        lambda *a, **kw: deepcopy(human if kw.get("human") else payload))
    baseline = h.db_path.read_bytes()
    from peerhub.core.monitor_groups import GROUP_IDS
    expected = {**payload, "selected_groups": ["capacity"], "available_groups": list(GROUP_IDS),
                "preference": {"status": "OK", "source": "command", "reason": None},
                "store_selection": {"path": str(h.db_path.resolve()), "source": "explicit"}}
    expected_wire = json.dumps(expected, ensure_ascii=True, separators=(",", ":")) + "\n"
    for _ in range(2):
        assert main(["--db", str(h.db_path), "monitor", "--snapshot", "--group", "capacity", "--json"]) == 0
        wire = capsys.readouterr().out
        assert wire == expected_wire
    assert main(["--db", str(h.db_path), "monitor", "--snapshot", "--group", "capacity", "--view", "plain"]) == 0
    assert "55%" in capsys.readouterr().out
    assert h.db_path.read_bytes() == baseline
    from unittest.mock import patch
    from peerhub.cli.monitor import _render_human_frame
    with patch("sqlite3.connect", side_effect=AssertionError("redraw queried the database")):
        assert "55%" in _render_human_frame(human, view="plain", width=100, height=None, color=False)



@pytest.mark.parametrize("evidence", ["ERROR", "UNKNOWN", "UNAVAILABLE", "ABSENT"])
def test_unavailable_evidence_never_becomes_a_healthy_number(compact_frame, evidence):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(state=evidence, stored_state=evidence)
    screen = _solo(compact_frame, width=100)
    assert "?" in _row(screen, "G-5H").split()
    assert "80%" not in _row(screen, "G-5H")


@pytest.mark.parametrize("remaining", [None, float("nan"), float("inf"), False, -0.1, 1.1])
def test_invalid_measurements_are_unknown(compact_frame, remaining):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(last_measurements={"remaining_fraction": remaining}, measurements={"remaining_fraction": remaining})
    assert "?" in _row(_solo(compact_frame), "G-5H").split()


def test_stopped_token_use_warning_is_visible(compact_frame):
    compact_frame["refresh_state"] = "STOPPED · AGY TOKEN USE"
    screen = render_grouped_frame(compact_frame, width=100)
    assert "STOPPED" in screen and "--allow-agy-token-use" in screen


def test_projection_samples_only_count_the_current_window(tmp_path):
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.extensions.observation_model import EvidenceState, Observation, ResourcePool, epoch_to_iso
    from tests.communication.harness.observation import ObservationHarness
    h = ObservationHarness(tmp_path / "samples")
    h.obs.register_resource_pool(ResourcePool(resource_pool_id="quota:ag:G-5H", provider="ag", kind="QUOTA"))
    def persist(name, at, reset):
        h.obs.persist(Observation(observation_id=name, subject_ref="ag", resource_pool_ref="quota:ag:G-5H",
            kind="quota", source="test", state=EvidenceState.MEASURED,
            observed_at=epoch_to_iso(at), captured_at=epoch_to_iso(at),
            payload={"remaining_fraction": .8, "resets_at": reset}))
    persist("prior-window", NOW - 7200, NOW - 3600)
    persist("current-1", NOW - 60, NOW + 3600)
    def count():
        trace = []
        frame = render_monitor_frame(str(h.db_path), ["peers"], read_at=NOW, human=True, trace_callback=trace.append)
        batch = [q for q in trace if "WHERE o.observation_id IN (" in q]
        assert len(batch) == 1
        assert trace.index(batch[0]) < trace.index("ROLLBACK")
        assert "capacity" not in frame["groups"]
        return frame["_human_observations"][0]["projection_samples"]
    assert count() == 1
    persist("current-2", NOW - 30, NOW + 3600)
    assert count() == 2


def test_unknown_frame_time_is_not_fabricated(compact_frame):
    compact_frame.pop("read_at")
    assert "time unknown" in render_grouped_frame(compact_frame)


def test_tick_advances_header_age_and_ttl_without_mutation(compact_frame):
    compact_frame["display_elapsed_seconds"] = 60
    screen = render_grouped_frame(compact_frame, view="plain")
    assert "2026-11-08 10:49Z" in screen and "quota age 10m" in screen
    assert compact_frame["read_at"] == NOW


def test_absent_and_reset_windows_on_one_peer_are_both_hidden(compact_frame):
    rows = compact_frame["groups"]["capacity"]["data"]["observations"]
    absent, reset = rows[6], rows[7]
    reset.update(resets_at=NOW, age_seconds=540, state="STALE")
    compact_frame["groups"]["capacity"]["data"]["observations"] = [absent, reset]
    screen = render_grouped_frame(compact_frame, view="plain", width=100)
    assert "POOLS 0" in _header(screen)
    assert "X-5H" not in screen and "X-7D" not in screen and "89%" not in screen


def test_poolless_probe_does_not_poison_quota_age_or_refresh(compact_frame):
    rows = compact_frame["groups"]["capacity"]["data"]["observations"]
    compact_frame["configured_profiles"] = [{"profile": "ag.standard", "fields": {"pools": {"value": ["G-5H"]}}}]
    rows[:] = [rows[3], {"peer_id": "ag", "pool_ref": None, "state": "ERROR",
                         "age_seconds": 86400 * 8}]
    screen = render_grouped_frame(compact_frame, view="plain")
    assert "quota age 1m" in screen and "next:" not in screen
    assert "probe error" not in screen  # later successful pool evidence supersedes this failed attempt
    assert len([line for line in screen.splitlines() if line.startswith(" ag ")]) == 1  # #374: one POOLS row, no PEERS line
    rows[1]["age_seconds"] = 30
    screen = render_grouped_frame(compact_frame, view="plain")
    assert screen.count("probe error (value 1m old)") == 1
    assert "quota age 1m" in screen and "next:" not in screen


def test_missing_configured_pool_requests_refresh(compact_frame):
    compact_frame["groups"]["capacity"]["data"]["observations"] = []
    screen = render_grouped_frame(compact_frame, view="plain", tty=True)
    assert "POOLS 0" in screen.splitlines() and "no quota evidence; next: peerhub observation refresh" in screen
    rich = render_grouped_frame(compact_frame).splitlines()  # P21: the head is a rule; the notes are the same lines
    assert rich[2] == screen.splitlines()[2] == "quota: no reading -> peerhub observation refresh"
    assert rich[3] == "── POOLS 0 " + "─" * 88 and rich[4:] == screen.splitlines()[4:]
    assert screen.splitlines()[-1].startswith("Guide: ")
    compact_frame["selected_groups"] = ["peers"]
    assert "next: peerhub observation refresh" in render_grouped_frame(compact_frame)


def test_m5_configured_family_profiles_and_unknown_family(compact_frame):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][7]
    row["pool_ref"] = "quota:cx:X-UNK"
    screen = render_grouped_frame(compact_frame, view="plain", width=120)
    assert "ag.standard" in screen
    assert "?" in _row(screen, "X-UNK").split()


def test_expired_absent_is_aged_not_na(compact_frame):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][6]
    row.update(state="STALE", age_seconds=301)
    screen = _solo(compact_frame, drop=":X-7D", view="plain")
    assert "n/a" not in _row(screen, "X-5H").split()
    assert "aged" in _row(screen, "X-5H").split()


@pytest.mark.parametrize("aged", [True, False])
def test_peers_lines_left_the_compact_frame(compact_frame, aged):
    # #374: the PEERS summaries (and their "(aged)" suffix) are --expand only; an aged pool shows as `aged` in POOLS.
    rows = compact_frame["groups"]["capacity"]["data"]["observations"]
    if not aged:
        for row in rows:
            row.update(age_seconds=60, state=row["stored_state"])
    screen = render_grouped_frame(compact_frame, view="plain", width=100)
    assert "PEERS" not in screen and "(aged)" not in screen
    assert ("aged" in _row(screen, "A-7D").split()) == aged


def test_peers_only_uses_snapshot_capacity_support(compact_frame):
    standard = compact_frame["configured_profiles"][0]
    compact_frame["configured_profiles"].append({**standard, "profile": "ag.effort"})
    compact_frame["selected_groups"] = ["peers"]
    compact_frame["_human_observations"] = compact_frame["groups"].pop("capacity")["data"]["observations"]
    screen = render_grouped_frame(compact_frame, view="plain", tty=True)
    # #374: `peers` owns no compact section any more (its summaries are --expand only): lines 1-2 and the refresh hint.
    assert "POOLS" not in screen and "suggest:" not in screen and "argon" not in screen
    lines = screen.splitlines()
    assert lines[1].startswith("PeerHub Monitor") and len(lines[:-1]) <= 3
    assert lines[-1].startswith("Guide: ")


def test_cached_profiles_keep_public_fields_and_add_link_provenance(monkeypatch):
    from peerhub.cli.monitor import _cached_profiles, _profile_fingerprints
    from peerhub.extensions.adapters.profiles import list_profiles

    workspace = Path(__file__).resolve().parent
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(workspace))
    assert _cached_profiles(str(workspace), _profile_fingerprints(workspace)) == [
        {**profile, "_definition_layer": "packaged"}
        for profile in list_profiles(workspace)
    ]


def test_profile_cache_reuses_parse_and_invalidates_overlay(tmp_path, monkeypatch):
    from peerhub.cli.monitor import _render_human_frame
    calls = []
    monkeypatch.setattr("peerhub.extensions.adapters.profiles.list_profiles",
                        lambda root, *, include_meta: calls.append(root) or [])
    frame = {"store_selection": {"path": str(tmp_path / ".peerhub/core.db")}, "groups": {}}
    for _ in range(2):
        _render_human_frame(frame, view="plain", width=100, height=None, color=False)
    assert len(calls) == 1
    overlay = tmp_path / ".peerhub/config/models.toml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("schema_version=1\n", encoding="utf-8")
    _render_human_frame(frame, view="plain", width=100, height=None, color=False)
    assert len(calls) == 2


def test_compact_frame_has_no_footer_counts(compact_frame):
    # #374: the facts/sessions/telemetry footer and the delivery line are --expand only.
    screen = render_grouped_frame(compact_frame)
    assert "active" not in screen
    for gone in ("sessions >=", "delivery", "telemetry", "facts "):
        assert gone not in screen


def _pool_order(screen):
    """The pools of the QUOTA details of an --expand frame (below the compact POOLS block, #412), in their order."""
    order = []
    peer = ""
    lines = screen.splitlines()
    for line in lines[next((i for i, text in enumerate(lines) if "QUOTA [" in text), 0):]:
        if re.match(r"^(ag|cc|cx) \S+ (n/a|\?) ", line):
            continue  # reason notes have single-spaced peer/pool/state prefixes
        match = re.match(r"^(?:(ag|cc|cx)\s+|\s+)(?:[|`]-\s+)?((?:3P|[ACGFX])-\d+[HD])\s", line)
        if match:
            peer = match[1] or peer
            order.append(f"{peer} {match[2]}")
    return order


def _compact_pool_order(screen):
    order = []
    peer = ""
    for line in screen.splitlines():
        match = re.match(r"^ (?:(ag|cc|cx) |\s+(?:`-|└─) )((?:3P|[ACGFX])-\d+[HD])\s", line)
        if match:
            peer = match[1] or peer
            order.append(f"{peer} {match[2]}")
    return order


def test_compact_and_expand_each_keep_their_pool_order(compact_frame):
    # Unknown-PACE roots tie by peer and name in POOLS and QUOTA uses that rank within peer groups.
    compact = _compact_pool_order(render_grouped_frame(compact_frame, view="plain", unicode=False, width=100))
    expanded = _pool_order(render_grouped_frame(compact_frame, view="plain", unicode=False, width=120, expand=True))
    assert compact == ["ag 3P-7D", "ag 3P-5H", "ag A-7D", "ag G-5H", "cc F-7D", "cx X-7D"]
    assert expanded == compact


COMPACT_COLUMNS = ["STATE", "LEFT", "PACE(pp)", POOL_MARGIN_COLUMN, "EXH/RESET", "FAMILY"]


@pytest.mark.parametrize("width", [40, 60, 80, 100])
def test_g_pools_columns_drop_in_order_after_the_core_columns(compact_frame, width):
    screen = render_grouped_frame(compact_frame, view="plain", unicode=False, width=width)
    columns = _header(screen).partition("STATE")[2].removesuffix(POOLS_USE_FIRST_HINT).split()
    assert columns == COMPACT_COLUMNS[1:len(columns) + 1] and len(columns) >= 2
    assert "55% ~9m" in " ".join(screen.split())  # M9 badge stays with LEFT
    assert "Columns omitted" not in screen


def test_bar_even_burn_tick_positions_and_unknown():
    assert _bar_quota(.5, 8, True, .25) == "[██|█░░░░]"
    assert _bar_quota(.5, 8, False, .25) == "[##|#....]"
    assert _bar_quota(.25, 8, True, .75) == "[██░░░░|░]"
    assert _bar_quota(.5, 8, True, .5) == "[████|░░░]"
    assert _bar_quota(.5, 8, True, .45) == "[███|░░░░]"  # the tick sits on the filled edge
    assert _bar_quota(1.0, 8, False, 1.0) == "[#######|]"
    assert _bar_quota(0.0, 8, False, 0.9) == "[.......|]"
    assert _bar_quota(.5, 8, False, None) == "[####....]"
    assert _bar_quota(None, BAR_CELLS, True, .5) == "[" + "─" * BAR_CELLS + "]"
    assert _bar_quota(None, BAR_CELLS, False) == "[" + "-" * BAR_CELLS + "]"


def test_compact_state_uses_evidence_colours_only_when_colour_is_on(compact_frame, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    fast = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    fast.update(projection_samples=2)
    anchor_pool(fast, NOW, 5)
    from peerhub.cli.monitor_layout import RICH_SGR

    colored = _solo(compact_frame, view="rich", color=True, width=100)
    plain = _solo(compact_frame, view="rich", color=False, width=100)
    assert "\x1b" not in plain
    bare = re.sub(r"\x1b\[[0-9;]*m", "", colored).splitlines()
    assert bare[1:] == plain.splitlines()[1:]  # colour never changes the text or the widths
    assert bare[0].split() == plain.splitlines()[0].split() and bare[0].startswith(" OK? ")  # line 1: the pill's padding
    # C1/M1: the state uses its own colour and the bar/pace use the shared band's colour.
    for pool, state, colour in (("3P-5H", "▼ low", "warn"), ("3P-7D", "○ aged", "dim"),
                                ("A-7D", "○ aged", "dim"), ("G-5H", "▲ fast", "warn"), ("X-7D", "● ok", "ok")):
        line = _row(colored, pool)
        assert f"\x1b[{RICH_SGR[colour]}m{state}\x1b[0m" in line
        assert not line.startswith("\x1b") and cell_width(line) <= 99
    assert "\x1b" not in _solo(compact_frame, view="plain", color=True, width=100)
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\x1b" not in _solo(compact_frame, view="rich", color=True, width=100)


def test_expand_and_compact_keep_the_column_priority_order(compact_frame):
    expanded = render_grouped_frame(compact_frame, view="plain", unicode=False, width=140, expand=True)
    header = next(line for line in expanded.splitlines() if line.startswith("PEER "))
    assert header.split()[2:2 + len(COMPACT_COLUMNS)] == COMPACT_COLUMNS  # G4
    compact = _header(render_grouped_frame(compact_frame, view="plain", unicode=False, width=100))
    assert compact.partition("STATE")[2].removesuffix(POOLS_USE_FIRST_HINT).split() == COMPACT_COLUMNS[1:]  # STATE directly after POOL (#374 F5)


def test_wide_pools_table_keeps_every_column_and_prints_no_omission(compact_frame):
    screen = render_grouped_frame(compact_frame, view="plain", unicode=False, width=100)
    assert "Columns omitted" not in screen and "FAMILY" in _header(screen)
