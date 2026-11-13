"""Compact human monitor contract; JSON and expanded detail remain separate."""
from __future__ import annotations

from copy import deepcopy
import json
import re
from pathlib import Path

import pytest

from peerhub.cli.monitor_view import render_grouped_frame, _width

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
            pool("ag", "G-7D", .55, state="STALE", age=540, reset=-1),
            pool("cc", "F-7D", None),
            pool("cx", "X-5H", None, state="ABSENT", reset=-1),
            pool("cx", "X-7D", .89)]
    rows[6].update(stored_state="ABSENT", reason="not reported by provider; plan=prolite")
    profiles = [{"profile": name, "fields": {"pools": {"value": pools}}} for name, pools in (
        ("ag.standard", ["G-5H", "G-7D"]), ("ag.argon", ["A-7D"]),
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
    return next(line for line in screen.splitlines() if pool in line and line.lstrip().startswith(("ag ", "cc ", "cx ")))


def test_eight_pool_screen_layout_state_precedence_and_no_number_reset(compact_frame):
    screen = render_grouped_frame(compact_frame, view="plain", unicode=False, width=100)
    assert "PeerHub Monitor 2026-11-08 10:48Z | DB:discovered | quota age 9m" in screen
    assert "/work/.peerhub/core.db" not in screen
    assert "next: peerhub observation refresh" in screen
    for heading in ("PEER", "POOL", "MEANING", "LEFT", "RESETS", "PACE", "AGE", "STATE", "PROFILES"):
        assert heading in screen
    for name, state, value in (("3P-5H", "low", "1%"), ("3P-7D", "out", "0%"),
                                ("A-7D", "aged", "55%"), ("G-5H", "ok", "80%"),
                                ("G-7D", "reset", None), ("F-7D", "?", None),
                                ("X-5H", "n/a", None), ("X-7D", "ok", "89%")):
        row = _row(screen, name)
        assert state in row.split()
        assert "%" not in row if value is None else value in row
    positions = [screen.index(_row(screen, name)) for name in ("3P-5H", "3P-7D", "A-7D", "G-5H", "G-7D")]
    assert positions == sorted(positions)  # peer, family, then window (F10')
    assert "ok enough | low >0-10% | fast >10% + threat (2+ samples) | out 0% | aged stale" in screen
    assert "G flash A argon" not in screen
    assert "X-5H n/a (plan prolite)" in screen
    assert "deepthink 0% (3P-7D)" in screen
    assert "unmapped" in _row(screen, "F-7D")
    for text in ("facts ok 2026-11-08", "sessions >=100 retained (sampled)",
                 "delivery (sampled): terminal 166 / started 15 / not started 2",
                 "telemetry 56/60", "details: --expand"):
        assert text in screen
    for old in ("EXECUTION [", "SESSIONS [", "USAGE [", "EXHAUST-IN", "Columns omitted", "latest session:"):
        assert old not in screen


@pytest.mark.parametrize("width", [80, 100])
@pytest.mark.parametrize("view,unicode_mode", [("plain", False), ("rich", True)])
@pytest.mark.parametrize("pace,expected", [(46.4, "+46"), (-12.4, "-12"), (0, "0"), (-.1, "0")])
@pytest.mark.parametrize("aged", [False, True])
def test_compact_pace_cells_keep_signed_measurement_for_aged_rows(
        compact_frame, width, view, unicode_mode, pace, expected, aged):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(projection={"state": "stale" if aged else "on_pace", "pace_pt": pace},
               state="STALE" if aged else "MEASURED", age_seconds=540 if aged else 60)
    screen = render_grouped_frame(compact_frame, width=width, view=view, unicode=unicode_mode)
    header = next(line for line in screen.splitlines() if line.startswith("PEER "))
    assert header.index("RESETS") < header.index("PACE") < header.index("BAR") < header.index("AGE")
    glyph = ("▲" if unicode_mode else "^") if expected.startswith("+") else ("▼" if unicode_mode else "v") if expected.startswith("-") else ""
    assert _row(screen, "G-5H")[header.index("PACE"):header.index("BAR")].strip() == glyph + expected
    assert "80%" in _row(screen, "G-5H")


@pytest.mark.parametrize("width", [80, 100])
@pytest.mark.parametrize("unusable", ["n/a", "?", "reset", "missing", "nan", "inf", "bool", "invalid"])
def test_compact_pace_is_dash_without_usable_evidence(compact_frame, width, unusable):
    rows = compact_frame["groups"]["capacity"]["data"]["observations"]
    row = rows[6] if unusable == "n/a" else rows[3]
    row["projection"] = {"pace_pt": 46}
    if unusable == "?": row.update(state="UNKNOWN")
    if unusable == "reset": row.update(resets_at=NOW - 1)
    if unusable == "missing": row["projection"] = {}
    if unusable == "nan": row["projection"]["pace_pt"] = float("nan")
    if unusable == "inf": row["projection"]["pace_pt"] = float("inf")
    if unusable == "bool": row["projection"]["pace_pt"] = True
    if unusable == "invalid": row.update(last_measurements={"remaining_fraction": 1.1})
    screen = render_grouped_frame(compact_frame, width=width, view="plain")
    header = next(line for line in screen.splitlines() if line.startswith("PEER "))
    pool = row["pool_ref"].split(":")[-1]
    assert _row(screen, pool)[header.index("PACE"):header.index("BAR")].strip() == "-"


@pytest.mark.parametrize("width", [80, 100])
@pytest.mark.parametrize("elapsed", [0, 60])
@pytest.mark.parametrize("reset_field", ["resets_at", "reset_in_seconds"])
def test_exact_reset_expires_state_pace_and_suggestion(compact_frame, width, elapsed, reset_field):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(projection={"state": "on_pace", "pace_pt": 46})
    row.pop("resets_at")
    row[reset_field] = NOW + elapsed if reset_field == "resets_at" else elapsed
    compact_frame["groups"]["capacity"]["data"]["observations"] = [row]
    compact_frame["configured_profiles"] = [
        {"profile": "ag.effort", "fields": {"pools": {"value": ["G-5H"]}}}]
    compact_frame["display_elapsed_seconds"] = elapsed
    screen = render_grouped_frame(compact_frame, width=width, view="plain")
    header = next(line for line in screen.splitlines() if line.startswith("PEER "))
    pool = _row(screen, "G-5H")
    assert "reset" in pool.split() and "80%" not in pool
    assert pool[header.index("PACE"):header.index("BAR")].strip() == "-"
    assert "suggest: no eligible profiles; next: peerhub observation refresh" in screen


def test_suggestions_reject_zero_reset_seconds():
    from peerhub.cli.monitor_view import _compact_suggestions

    profiles = [{"profile": "ag.effort", "fields": {"pools": {"value": ["G-5H"]}}}]
    rows = [{"peer": "ag", "pool": "G-5H", "remaining": .8, "reset_seconds": 0,
             "aged": False, "state": "ok"}]
    assert _compact_suggestions(profiles, rows) == "suggest: no eligible profiles; next: peerhub observation refresh"


@pytest.mark.parametrize("width", [60, 70])
def test_narrow_pool_table_keeps_pace_and_quota_facts(compact_frame, width):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row["projection"] = {"pace_pt": 46}
    screen = render_grouped_frame(compact_frame, width=width, view="plain", unicode=False)
    pool = _row(screen, "G-5H")
    assert "80%" in pool and "ok" in pool.split()
    assert "1h00m" in pool.split() and "^+46" in pool.split()
    assert all(_width(line) <= width for line in screen.splitlines())


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
    row.update(projection={"state": "threat"}, projection_samples=samples)
    assert expected in _row(render_grouped_frame(compact_frame, width=100), "G-5H").split()


@pytest.mark.parametrize("remaining,projection,samples,age,reset,expected", [
    (.10, "safe", 0, 60, 3600, "low"),
    (.10, "threat", 2, 301, 3600, "low"),
    (.100001, "threat", 2, 60, 3600, "fast"),
    (.8, "threat", 2, 301, 3600, "fast"),
    (.8, "threat", 1, 301, 3600, "aged"),
    (.8, "safe", 2, 60, 3600, "ok"),
    (0, "threat", 2, 301, 3600, "out"),
    (.05, "threat", 2, 301, -1, "reset"),
    (None, "threat", 2, 60, -1, "?"),
])
def test_low_fast_and_higher_priority_states(compact_frame, remaining, projection, samples, age, reset, expected):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(last_measurements={"remaining_fraction": remaining}, projection={"state": projection},
               projection_samples=samples, age_seconds=age, resets_at=NOW + reset)
    original = deepcopy(compact_frame)
    screen = render_grouped_frame(compact_frame, view="plain", width=100)
    assert expected in _row(screen, "G-5H").split()
    assert compact_frame == original


@pytest.mark.parametrize("stored_state,expected", [("ABSENT", "aged"), ("UNKNOWN", "?"), ("ERROR", "?")])
def test_stale_row_without_number_is_aged_only_for_absent(compact_frame, stored_state, expected):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(last_measurements={"remaining_fraction": None}, stored_state=stored_state, age_seconds=99999,
               resets_at=NOW + 3600)
    assert expected in _row(render_grouped_frame(compact_frame, view="plain", width=100), "G-5H").split()


@pytest.mark.parametrize("view,unicode_mode", [("plain", False), ("rich", True)])
@pytest.mark.parametrize("width", [48, 80, 100])
def test_compact_legend_explains_states_and_fits_width(compact_frame, view, unicode_mode, width):
    legend = "ok enough | low >0-10% | fast >10% + threat (2+ samples) | out 0% | aged stale | reset passed | n/a plan omits | ? unknown"
    screen = render_grouped_frame(compact_frame, view=view, unicode=unicode_mode, width=width)
    legend_lines = screen.split("ok enough", 1)[1].split("PEERS", 1)[0]
    assert legend in " ".join(("ok enough" + legend_lines).split())
    assert "PACE=used%-elapsed% pp, + ahead of even burn, - behind; '-' cell = unavailable" in " ".join(legend_lines.split())
    assert "precedence:" not in screen
    assert all(_width(line) <= min(99, width - 1) for line in screen.splitlines())


def test_fast_is_magenta_on_state_and_bar_only_in_rich_output(compact_frame, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(projection={"state": "threat"}, projection_samples=2)
    screen = render_grouped_frame(compact_frame, view="rich", color=True, width=100)
    line = next(line for line in screen.splitlines() if "G-5H" in line and "fast" in line)
    assert not line.startswith("\x1b") and "\x1b[35mfast\x1b[0m" in line
    assert line.count("\x1b[35m") == 2 and re.search(r"\x1b\[35m\[[^\]]*\]\x1b\[0m", line)


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
    assert all(_width(line) <= width for line in screen.splitlines())
    assert "55%" in screen and "89%" in screen
    assert "--expand" in screen
    if width < 48:
        assert "PEER  POOL" not in screen  # labelled vertical pool rows on narrow terminals


def test_unknown_and_all_absent_are_never_zero_or_unlimited(compact_frame):
    rows = compact_frame["groups"]["capacity"]["data"]["observations"]
    compact_frame["groups"]["capacity"]["data"]["observations"] = [rows[6]]
    screen = render_grouped_frame(compact_frame, width=100)
    assert "? (not reported)" in screen and "? (never measured)" in screen
    assert "100%" not in screen and "%" not in _row(screen, "X-5H")


def test_human_render_does_not_mutate_json_and_expand_keeps_details(compact_frame):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(projection={"state": "threat"}, projection_samples=2)
    compact_frame["configured_profiles"].extend([
        {"profile": name, "fields": {"pools": {"value": pools}}} for name, pools in (
            ("ag.effort", ["G-5H"]), ("cx.standard", ["X-7D"]))])
    before = json.dumps(compact_frame, ensure_ascii=True, separators=(",", ":")).encode()
    original = deepcopy(compact_frame)
    screen = render_grouped_frame(compact_frame, width=100)
    assert "fast" in _row(screen, "G-5H").split()
    assert "normal=cx.effort|ag.effort" in screen
    expanded = render_grouped_frame(compact_frame, width=100, expand=True)
    assert "EXHAUST-IN" in expanded or "Columns omitted" in expanded
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
    from peerhub.cli.monitor_preferences import GROUP_IDS
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
    screen = render_grouped_frame(compact_frame, width=100)
    assert "?" in _row(screen, "G-5H").split()
    assert "80%" not in _row(screen, "G-5H")


@pytest.mark.parametrize("remaining", [None, float("nan"), float("inf"), False, -0.1, 1.1])
def test_invalid_measurements_are_unknown(compact_frame, remaining):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(last_measurements={"remaining_fraction": remaining}, measurements={"remaining_fraction": remaining})
    assert "?" in _row(render_grouped_frame(compact_frame), "G-5H").split()


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


def test_absent_and_reset_windows_on_one_peer_do_not_hide_each_other(compact_frame):
    rows = compact_frame["groups"]["capacity"]["data"]["observations"]
    absent, reset = rows[6], rows[7]
    reset.update(resets_at=NOW - 1, age_seconds=540, state="STALE")
    compact_frame["groups"]["capacity"]["data"]["observations"] = [absent, reset]
    screen = render_grouped_frame(compact_frame, view="plain", width=100)
    assert "n/a" in _row(screen, "X-5H").split()
    assert "reset" in _row(screen, "X-7D").split()
    assert "%" not in _row(screen, "X-5H") and "%" not in _row(screen, "X-7D")
    assert "89%" not in screen


def test_poolless_probe_does_not_poison_quota_age_or_refresh(compact_frame):
    rows = compact_frame["groups"]["capacity"]["data"]["observations"]
    compact_frame["configured_profiles"] = [{"profile": "ag.standard", "fields": {"pools": {"value": ["G-5H"]}}}]
    rows[:] = [rows[3], {"peer_id": "ag", "pool_ref": None, "state": "ERROR",
                         "age_seconds": 86400 * 8}]
    screen = render_grouped_frame(compact_frame, view="plain")
    assert "quota age 1m" in screen and "next:" not in screen
    assert "probe error" not in screen  # later successful pool evidence supersedes this failed attempt
    assert len([line for line in screen.splitlines() if line.startswith("ag ")]) == 2
    rows[1]["age_seconds"] = 30
    screen = render_grouped_frame(compact_frame, view="plain")
    assert screen.count("probe error (30s)") == 1
    assert "quota age 1m" in screen and "next:" not in screen


def test_missing_configured_pool_requests_refresh(compact_frame):
    compact_frame["groups"]["capacity"]["data"]["observations"] = []
    assert "no quota evidence; next: peerhub observation refresh" in render_grouped_frame(compact_frame)
    compact_frame["selected_groups"] = ["peers"]
    assert "next: peerhub observation refresh" in render_grouped_frame(compact_frame)


def test_prefix_free_profiles_and_unknown_family(compact_frame):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][7]
    row["pool_ref"] = "quota:cx:X-UNK"
    screen = render_grouped_frame(compact_frame, view="plain")
    assert "ag.standard" not in screen
    assert "?" in _row(screen, "X-UNK").split()


def test_expired_absent_is_aged_not_na(compact_frame):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][6]
    row.update(state="STALE", age_seconds=301)
    screen = render_grouped_frame(compact_frame, view="plain")
    assert "n/a" not in _row(screen, "X-5H").split()
    assert "aged" in _row(screen, "X-5H").split()


@pytest.mark.parametrize("bad", ["aged", "reset", "unknown", "low", "missing", "na-only"])
def test_suggestions_exclude_unusable_profiles(compact_frame, bad):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    compact_frame["configured_profiles"] = [{"profile": "ag.effort", "fields": {"pools": {"value": ["G-5H"]}}}]
    if bad == "aged": row.update(state="STALE", age_seconds=301)
    if bad == "reset": row.update(resets_at=NOW - 1)
    if bad == "unknown": row.update(state="UNKNOWN")
    if bad == "low": row.update(last_measurements={"remaining_fraction": .049})
    if bad == "missing": row.update(pool_ref="quota:ag:G-7D")
    if bad == "na-only": row.update(state="ABSENT", reason="not reported by provider")
    screen = render_grouped_frame(compact_frame)
    assert "suggest: no eligible profiles; next: peerhub observation refresh" in screen.splitlines()
    assert "eco=- normal=- quality=- top=-" not in screen


@pytest.mark.parametrize("aged", [True, False])
def test_peers_age_suffix_once_at_end(compact_frame, aged):
    rows = compact_frame["groups"]["capacity"]["data"]["observations"]
    if not aged:
        for row in rows:
            row.update(age_seconds=60, state=row["stored_state"])
    screen = render_grouped_frame(compact_frame, view="plain", width=100)
    peers = screen.split("PEERS\n", 1)[1].split("suggest:", 1)[0].splitlines()
    ag = next(line for line in peers if line.startswith("ag "))
    assert ag.count("(aged)") == int(aged)
    assert ag.endswith("(aged)") == aged


def test_peers_age_suffix_follows_plan_notes(compact_frame):
    compact_frame["groups"]["capacity"]["data"]["observations"][7].update(age_seconds=301, state="STALE")
    screen = render_grouped_frame(compact_frame, view="plain", width=100)
    cx = screen.split("PEERS\n", 1)[1].split("suggest:", 1)[0]
    cx = next(line for line in cx.splitlines() if line.startswith("cx "))
    assert cx.endswith("X-5H n/a (plan prolite) (aged)")


def test_suggestions_min_score_na_skip_and_fable_own_pool(compact_frame):
    rows = compact_frame["groups"]["capacity"]["data"]["observations"]
    rows[5].update(last_measurements={"remaining_fraction": .7})
    compact_frame["configured_profiles"] = [
        {"profile": name, "fields": {"pools": {"value": pools}}} for name, pools in (
            ("ag.standard", ["G-5H"]), ("ag.effort", ["G-5H", "A-7D"]),
            ("cx.effort", ["X-5H", "X-7D"]), ("cc.fable", ["F-7D"]))]
    screen = render_grouped_frame(compact_frame)
    assert "suggest: eco=ag.standard normal=cx.effort quality=- top=cc.fable" in screen
    assert "fable 70% (F-7D)" in screen


def test_suggestions_use_the_worst_pool_score_and_keep_five_percent(compact_frame):
    rows = compact_frame["groups"]["capacity"]["data"]["observations"]
    rows[2].update(state="MEASURED", age_seconds=60, last_measurements={"remaining_fraction": .05},
                   resets_at=NOW + 86400)
    rows[7].update(last_measurements={"remaining_fraction": .5}, resets_at=NOW + 86400)
    compact_frame["configured_profiles"] = [
        {"profile": name, "fields": {"pools": {"value": pools}}} for name, pools in (
            ("ag.effort", ["A-7D", "G-5H"]), ("cx.effort", ["X-7D"]))]
    assert "normal=cx.effort|ag.effort" in render_grouped_frame(compact_frame)
    rows[7].update(state="UNKNOWN")
    assert "normal=ag.effort" in render_grouped_frame(compact_frame)


@pytest.mark.parametrize("all_fast", [False, True])
def test_suggestions_prefer_non_fast_but_still_pick_when_all_fast(compact_frame, all_fast):
    rows = compact_frame["groups"]["capacity"]["data"]["observations"]
    # ag has the better H score; its second applicable pool makes it fast.
    rows[2].update(state="MEASURED", age_seconds=60, resets_at=NOW + 86400,
                   last_measurements={"remaining_fraction": .8})
    rows[3].update(projection={"state": "threat"}, projection_samples=2)
    rows[7].update(resets_at=NOW + 86400, last_measurements={"remaining_fraction": .5},
                   projection={"state": "threat" if all_fast else "on_pace"}, projection_samples=2)
    compact_frame["configured_profiles"] = [
        {"profile": name, "fields": {"pools": {"value": pools}}} for name, pools in (
            ("ag.effort", ["A-7D", "G-5H"]), ("cx.effort", ["X-5H", "X-7D"]))]
    screen = render_grouped_frame(compact_frame, width=100)
    pick = "ag.effort|cx.effort" if all_fast else "cx.effort|ag.effort"
    assert f"normal={pick}" in screen
    assert "no eligible profiles" not in screen


@pytest.mark.parametrize("remaining,eligible", [(.049, False), (.05, True), (.075, True), (.10, True)])
def test_suggestions_keep_low_pools_at_or_above_five_percent(compact_frame, remaining, eligible):
    row = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    row.update(last_measurements={"remaining_fraction": remaining})
    compact_frame["configured_profiles"] = [
        {"profile": "ag.effort", "fields": {"pools": {"value": ["G-5H"]}}}]
    screen = render_grouped_frame(compact_frame)
    assert ("normal=ag.effort" in screen) == eligible
    assert ("no eligible profiles; next: peerhub observation refresh" in screen) != eligible


@pytest.mark.parametrize("tier,suffix", [("eco", "standard"), ("normal", "effort"),
                                        ("quality", "deepthink"), ("top", "custom")])
def test_suggestions_alternative_uses_same_ranking_with_name_ties(tier, suffix):
    from peerhub.cli.monitor_view import _compact_suggestions

    profiles = [{"profile": f"{peer}.{suffix}", "fields": {"pools": {"value": ["P-7D"]}}}
                for peer in ("cx", "cc", "ag")]
    rows = [{"peer": peer, "pool": "P-7D", "remaining": remaining, "reset_seconds": 86400,
             "aged": False, "state": "ok"} for peer, remaining in (("cx", .5), ("cc", .8), ("ag", .5))]
    result = _compact_suggestions(profiles, rows)
    assert f"{tier}=cc.{suffix}|ag.{suffix}" in result
    assert result == _compact_suggestions(list(reversed(profiles)), list(reversed(rows)))


@pytest.mark.parametrize("alternative", ["eligible", "absent", "same-peer", "ineligible"])
def test_suggestions_only_append_eligible_different_peer(alternative):
    from peerhub.cli.monitor_view import _compact_suggestions

    profiles = [{"profile": "cc.fable", "fields": {"pools": {"value": ["F-7D"]}}},
                {"profile": "cc.other", "fields": {"pools": {"value": ["F-7D"]}}}]
    rows = [{"peer": "cc", "pool": "F-7D", "remaining": .8, "reset_seconds": 86400,
             "aged": False, "state": "ok"}]
    if alternative != "same-peer":
        profiles.append({"profile": "ag.argon", "fields": {"pools": {"value": ["A-7D"]}}})
    if alternative in {"eligible", "ineligible"}:
        rows.append({"peer": "ag", "pool": "A-7D", "remaining": .5, "reset_seconds": 86400,
                     "aged": alternative == "ineligible", "state": "ok"})
    expected = "suggest: eco=- normal=- quality=- top=cc.fable"
    if alternative == "eligible":
        expected += "|ag.argon"
    assert _compact_suggestions(profiles, rows) == expected


def test_peers_only_uses_snapshot_capacity_support(compact_frame):
    standard = compact_frame["configured_profiles"][0]
    compact_frame["configured_profiles"].append({**standard, "profile": "ag.effort"})
    compact_frame["selected_groups"] = ["peers"]
    compact_frame["_human_observations"] = compact_frame["groups"].pop("capacity")["data"]["observations"]
    screen = render_grouped_frame(compact_frame, view="plain")
    assert "POOLS" not in screen
    assert "argon 55% (A-7D)" in screen
    assert "effort/standard 80% (G-5H)" in screen


def test_profile_cache_reuses_parse_and_invalidates_overlay(tmp_path, monkeypatch):
    from peerhub.cli.monitor import _render_human_frame
    calls = []
    monkeypatch.setattr("peerhub.extensions.adapters.profiles.list_profiles", lambda root: calls.append(root) or [])
    frame = {"store_selection": {"path": str(tmp_path / ".peerhub/core.db")}, "groups": {}}
    for _ in range(2):
        _render_human_frame(frame, view="plain", width=100, height=None, color=False)
    assert len(calls) == 1
    overlay = tmp_path / ".peerhub/config/models.toml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("schema_version=1\n", encoding="utf-8")
    _render_human_frame(frame, view="plain", width=100, height=None, color=False)
    assert len(calls) == 2


def test_footer_never_claims_active_or_bare_unknown_counts(compact_frame):
    screen = render_grouped_frame(compact_frame)
    assert "active" not in screen
    assert "sessions >=100 retained (sampled)" in screen
    assert "delivery (sampled): terminal 166" in screen
    compact_frame["groups"].pop("sessions")
    compact_frame["groups"].pop("execution")
    compact_frame["groups"].pop("usage")
    screen = render_grouped_frame(compact_frame)
    assert "sessions ?" not in screen and "delivery ?" not in screen and "telemetry ?" not in screen


def _pool_order(screen):
    order = []
    for line in screen.splitlines():
        parts = line.split()
        if len(parts) > 1 and parts[0] in {"ag", "cc", "cx"} and parts[1][0] in "3AGFX" and parts[1][1:2] in "P-" \
                and f"{parts[0]} {parts[1]}" not in order:
            order.append(f"{parts[0]} {parts[1]}")
    return order


def test_expand_quota_lists_pools_in_the_compact_order(compact_frame):
    compact = _pool_order(render_grouped_frame(compact_frame, view="plain", unicode=False, width=100))
    expanded = _pool_order(render_grouped_frame(compact_frame, view="plain", unicode=False, width=120, expand=True))
    assert compact[:5] == ["ag 3P-5H", "ag 3P-7D", "ag A-7D", "ag G-5H", "ag G-7D"]
    assert expanded == compact


COMPACT_COLUMNS = ["PEER", "POOL", "STATE", "LEFT", "RESETS", "PACE", "BAR", "MEANING", "AGE", "PROFILES"]


@pytest.mark.parametrize("width", [20, 40, 52, 60])
def test_narrow_pools_table_drops_whole_columns_from_the_right(compact_frame, width):
    screen = render_grouped_frame(compact_frame, view="plain", unicode=False, width=width)
    assert "~" not in screen
    header = next(line for line in screen.splitlines() if line.startswith("PEER "))
    names = COMPACT_COLUMNS
    shown = header.split()
    assert "PROFILES" not in shown and shown == names[:len(shown)] and len(shown) >= 2
    omitted = screen.split("Columns omitted:")[1].split("ok enough")[0].split()
    assert omitted == [name.lower() for name in names[len(shown):]]
    for pool in ("3P-5H", "G-5H", "X-7D"):  # every row shows the same columns
        assert len(_row(screen, pool).split()) == len(shown)


@pytest.mark.parametrize("width,dropped", [
    (40, ["bar", "meaning", "age", "profiles"]), (60, ["age", "profiles"]), (80, [])])
def test_pools_columns_drop_by_priority_and_state_survives(compact_frame, width, dropped):
    screen = render_grouped_frame(compact_frame, view="plain", unicode=False, width=width)
    header = next(line for line in screen.splitlines() if line.startswith("PEER ")).split()
    assert header == [name for name in COMPACT_COLUMNS if name.lower() not in dropped]
    assert "STATE" in header and "LEFT" in header
    if dropped:
        assert screen.split("Columns omitted:")[1].split("ok enough")[0].split() == dropped
    else:
        assert "Columns omitted" not in screen


@pytest.mark.parametrize("unicode_mode,full,empty", [(True, "█", "░"), (False, "#", ".")])
def test_pool_bar_shows_remaining_and_even_burn_tick(compact_frame, unicode_mode, full, empty):
    screen = render_grouped_frame(compact_frame, view="plain", unicode=unicode_mode, width=100)
    # G-5H: 80% left, 1h of a 5h window left -> even burn leaves 20%, so the tick sits in cell 1
    assert f"[{full}|{full * 4}{empty * 2}]" in _row(screen, "G-5H")
    assert "[--------]" in _row(screen, "X-5H")  # n/a
    assert "[--------]" in _row(screen, "F-7D")  # unknown
    assert "[--------]" in _row(screen, "G-7D")  # reset window passed


def test_bar_even_burn_tick_positions_and_unknown():
    from peerhub.cli.monitor_view import _bar_quota

    assert _bar_quota(.5, 8, True, .25) == "[██|█░░░░]"
    assert _bar_quota(.5, 8, False, .25) == "[##|#....]"
    assert _bar_quota(.25, 8, True, .75) == "[██░░░░|░]"
    assert _bar_quota(.5, 8, True, .5) == "[████|░░░]"
    assert _bar_quota(.5, 8, True, .45) == "[████░░░░]"  # the tick would sit on the filled edge
    assert _bar_quota(1.0, 8, False, 1.0) == "[########]"
    assert _bar_quota(0.0, 8, False, 0.9) == "[.......|]"
    assert _bar_quota(.5, 8, False, None) == "[####....]"
    assert _bar_quota(None, 8, True, .5) == _bar_quota(None, 8, False) == "[--------]"


def test_pace_glyph_marks_direction_and_keeps_the_number():
    from peerhub.cli.monitor_view import _pace_glyph

    assert [_pace_glyph(text, True) for text in ("+26", "-89", "0", "-", "+1.2pp", "-28.7pp", "+0.0pp", "-0.0pp")] == [
        "▲+26", "▼-89", "0", "-", "▲+1.2pp", "▼-28.7pp", "+0.0pp", "-0.0pp"]
    assert [_pace_glyph(text, False) for text in ("+26", "-89", "0", "-")] == ["^+26", "v-89", "0", "-"]


STATE_COLORS = {"ok": "32", "low": "33", "fast": "35", "out": "1;31", "aged": "2;33", "reset": "36", "n/a": "2"}


def test_compact_state_and_bar_carry_the_state_colour_only_when_colour_is_on(compact_frame, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    fast = compact_frame["groups"]["capacity"]["data"]["observations"][3]
    fast.update(projection={"state": "threat"}, projection_samples=2)
    colored = render_grouped_frame(compact_frame, view="rich", color=True, width=100)
    plain = render_grouped_frame(compact_frame, view="rich", color=False, width=100)
    assert "\x1b" not in plain
    assert re.sub(r"\x1b\[[0-9;]*m", "", colored) == plain  # colour never changes the text or the widths
    for pool, state in (("3P-5H", "low"), ("3P-7D", "out"), ("A-7D", "aged"), ("G-5H", "fast"),
                        ("G-7D", "reset"), ("X-5H", "n/a"), ("X-7D", "ok")):
        line = _row(colored, pool)
        code = STATE_COLORS[state]
        assert f"\x1b[{code}m{state}\x1b[0m" in line
        assert re.search(rf"\x1b\[{re.escape(code)}m\[[^\]]*\]\x1b\[0m", line)
        assert line.count("\x1b[0m") == 2 and not line.startswith("\x1b")  # only the state word and the bar
        assert _width(line) <= 99
    assert "\x1b" not in render_grouped_frame(compact_frame, view="plain", color=True, width=100)
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\x1b" not in render_grouped_frame(compact_frame, view="rich", color=True, width=100)


def test_expand_and_compact_share_the_column_priority_order(compact_frame):
    expanded = render_grouped_frame(compact_frame, view="plain", unicode=False, width=140, expand=True)
    header = next(line for line in expanded.splitlines() if line.startswith("PEER "))
    # expand column -> the compact column in the same priority slot
    slots = {"PEER": "PEER", "POOL": "POOL", "STATE": "STATE", "REM": "LEFT", "RESET-IN": "RESETS",
             "PACE": "PACE", "[BAR]": "BAR", "EXHAUST-IN": "MEANING", "AGE": "AGE", "PROFILES (configured)": "PROFILES"}
    positions = [header.index(name) for name in slots]
    assert positions == sorted(positions) and len(set(positions)) == len(slots)
    assert list(slots.values()) == COMPACT_COLUMNS


def test_wide_pools_table_keeps_every_column_and_prints_no_omission(compact_frame):
    screen = render_grouped_frame(compact_frame, view="plain", unicode=False, width=100)
    assert "Columns omitted" not in screen and "PROFILES" in screen
