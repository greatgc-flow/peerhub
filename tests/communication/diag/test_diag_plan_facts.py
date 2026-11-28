"""Extra JSON evidence for planning a wave from stored quota readings."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.cli.view import PLAN_EARLY_TAG, PLAN_FAST_TAG, PLAN_TAG_LEGEND, render_plan, render_view
from peerhub.extensions.diag_read_model import PLAN_GATE_TAG, dashboard_data, plan_data
from peerhub.extensions.quota_windows import FAMILIES
from peerhub.core.load_limits import PLAN_NA_POOLS_LABEL, PLAN_NA_POOLS_LIMIT, ResourceSample, load_snapshot
from peerhub.core.quota_policy import PLAN_SPEND_PACE_PT, QUOTA_TTL_SECONDS, format_pace
from peerhub.extensions.diag import ReadonlyDiag
from peerhub.core.quota_policy import LOW_QUOTA_FRACTION
from peerhub.extensions.observation_model import epoch_to_iso
from tests.communication.diag.test_diag_plan_339 import DAY, HOUR, NOW, _item, _report

FRESH_AGE = QUOTA_TTL_SECONDS / 2
AGED = QUOTA_TTL_SECONDS * 2
SHORT = 5 * HOUR
WEEK = 7 * DAY
FACT_FIELDS = {"age_seconds", "ttl_seconds", "fast", "family", "parent", "gates"}
HEALTH_FIELDS = {"even_left_pct", "exhaust_in_seconds", "exhaust_before_reset", "stale", "limited_by", "own_left_pct"}
WEEKLY_PROFILES = {"C-7D": ["cc.standard", "cc.fable"], "F-7D": ["cc.fable"]}


def _rows(report, profiles=None):
    return {r["resource_pool_ref"]: r for r in plan_data(report, {}, profiles_by_pool=profiles)["pools"]}


def test_dashboard_ignores_a_weekly_cap_after_a_relative_reset():
    parent = _item("cc", "C-7D", 0, span=WEEK, elapsed=WEEK - 30, age=60)
    parent["payload"].pop("resets_at")
    parent["payload"]["reset_in_seconds"] = 30
    child = _item("cc", "F-7D", .8, span=WEEK, elapsed=WEEK / 2)
    report = _report(parent, child)
    row = _rows(report, WEEKLY_PROFILES)[child["resource_pool_ref"]]
    assert row["limited_by"] is None and row["left_pct"] == 80
    rendered = render_view(report, width=140, profiles_by_pool=WEEKLY_PROFILES)
    line = next(line for line in rendered.splitlines() if line.startswith("cc   F-7D"))
    assert "80%" in line and "exhausted" not in line and "(cap C-7D)" not in line
    assert dashboard_data(report, profiles_by_pool=WEEKLY_PROFILES)["threats"]["exhausted_pools"] == []


def test_dashboard_uses_the_effective_reset_for_a_weekly_cap_forecast():
    parent = _item("cc", "C-7D", .11, span=WEEK, elapsed=.8 * WEEK, age=0)
    child = _item("cc", "F-7D", .8, span=WEEK, elapsed=WEEK - 3 * HOUR, age=0)
    report = _report(parent, child)
    row = _rows(report, WEEKLY_PROFILES)[child["resource_pool_ref"]]
    assert row["limited_by"] == parent["resource_pool_ref"]
    assert row["health"]["effective_reset_in_seconds"] == 3 * HOUR
    assert row["exhaust_in_seconds"] > 10 * HOUR and not row["exhaust_before_reset"]
    rendered = render_view(report, width=140, profiles_by_pool=WEEKLY_PROFILES)
    line = next(line for line in rendered.splitlines() if line.startswith("cc   F-7D"))
    assert "11%" in line and "3h" in line and "= on pace (cap C-7D)" in line
    assert dashboard_data(report, profiles_by_pool=WEEKLY_PROFILES)["threats"]["threatened_pools"] == [parent["resource_pool_ref"]]


def _flash_windows():
    return (_item("ag", "G-5H", .3, span=SHORT, elapsed=SHORT - FRESH_AGE / 2, age=FRESH_AGE),
            _item("ag", "G-7D", .5, span=WEEK, elapsed=WEEK / 2, age=FRESH_AGE),
            _item("ag", "A-5H", 0, span=SHORT, elapsed=HOUR, age=FRESH_AGE))


@pytest.mark.parametrize("with_profiles", [False, True])
def test_reset_short_window_holds_without_gating_and_keeps_a_placeholder_row_in_both_views(with_profiles):
    from peerhub.cli.monitor_view import render_grouped_frame
    from tests.communication.diag.test_monitor_activity_view import pool
    from tests.communication.diag.test_monitor_plain_golden import _payload

    report = _report(*_flash_windows())
    profiles = {"G-5H": ["ag.effort"], "G-7D": ["ag.effort"]} if with_profiles else None
    rows = _rows(report, profiles)
    weekly, short, argon = (rows[f"quota:ag:{pool}"] for pool in ("G-7D", "G-5H", "A-5H"))
    assert short["action"] == "hold" and short["reason"] == "window reset; refresh"
    assert short["left_pct"] == 30 and short["pace_pt"] is None and short["resets_in_seconds"] == -FRESH_AGE / 2
    assert weekly["action"] == "hold" and weekly["binding"] == weekly["pool"] and weekly["gates"] == []
    assert weekly["parent"] is None and short["parent"] == weekly["pool"]
    assert weekly["family"] == short["family"] == FAMILIES["G"]
    assert argon["family"] == FAMILIES["A"] and argon["parent"] is None
    assert short["gates"] == argon["gates"] == []
    assert weekly["age_seconds"] == FRESH_AGE and weekly["ttl_seconds"] == QUOTA_TTL_SECONDS

    out = render_plan(report, width=100, profiles_by_pool=profiles)
    lines = {line.split()[1]: line for line in out.splitlines() if line.startswith("ag ")}
    assert lines["G-5H"].split()[2] == "—" and lines["G-5H"].split()[-4:] == ["hold", "window", "reset;", "refresh"]
    assert lines["G-7D"].endswith(str(weekly["reason"])) and "G-5H" not in lines["G-7D"]
    assert any(line.endswith(PLAN_TAG_LEGEND) for line in out.splitlines())

    payload = _payload([], [pool("ag", "G-5H", .3, reset=-FRESH_AGE / 2), pool("ag", "G-7D", .5)],
                       selected_groups=["capacity"])
    options = dict(view="plain", width=120)
    for expand in (False, True):
        lines = render_grouped_frame(payload, expand=expand, **options).splitlines()
        parent = next(i for i, line in enumerate(lines) if " G-7D " in line)
        assert re.fullmatch(r"\s+└─ G-5H reset · refresh pending", lines[parent + 1])
    assert "ag G-5H n/a (quota window expired)" not in render_grouped_frame(payload, expand=True, **options)


def test_weekly_gates_use_each_siblings_own_action_and_skip_na_reset_and_other_accounts():
    _, weekly, _ = _flash_windows()
    rested = _item("ag", "G-5H", .3, span=SHORT, elapsed=HOUR, age=FRESH_AGE)
    usable = _item("ag", "G-1H", .9, span=HOUR, elapsed=HOUR / 2, age=FRESH_AGE)
    absent = _item("ag", "G-1D", None, span=DAY, elapsed=HOUR, state="ABSENT",
                   reason="not reported by provider", age=FRESH_AGE)
    other_account = {**rested, "resource_pool_ref": "quota:other:G-2H"}
    reset = _item("ag", "G-3H", .3, span=3 * HOUR, elapsed=3 * HOUR - FRESH_AGE / 2, age=FRESH_AGE)
    report = _report(weekly, rested, usable, absent, other_account, reset)
    rows = _rows(report)
    assert rows[usable["resource_pool_ref"]]["action"] == "rest"  # inherits the 5H action
    assert rows[absent["resource_pool_ref"]]["action"] == "n/a"
    gates = rows[weekly["resource_pool_ref"]]["gates"]
    rested_row = _rows(_report(rested))[rested["resource_pool_ref"]]
    assert gates == [
        {"pool": rested_row["pool"], "action": rested_row["action"], "reason": rested_row["reason"]},
    ]
    assert rows[reset["resource_pool_ref"]]["action"] == "hold"
    line = next(line for line in render_plan(report, width=120).splitlines()
                if line.split()[:2] == [weekly["subject_ref"], rows[weekly["resource_pool_ref"]]["pool"]])
    assert line.endswith(" ".join(f"({PLAN_GATE_TAG}: {gate['pool']})" for gate in gates))
    assert rows[other_account["resource_pool_ref"]]["parent"] is None


def test_registry_only_window_has_unknown_age_and_the_policy_ttl():
    report = _report()
    pools = report.sections["resource_pools"]
    ref = "quota:cc:C-7D"
    report.sections["resource_pools"] = replace(pools, data={"pools": [
        {"resource_pool_id": ref, "provider": "cc", "kind": "QUOTA"}
    ]})
    row = _rows(report)[ref]
    assert row["age_seconds"] is None and row["ttl_seconds"] == QUOTA_TTL_SECONDS
    assert row["family"] == FAMILIES["C"] and row["fast"] is False and row["gates"] == []


def test_scoped_weekly_parent_follows_configured_consumers():
    common = _item("cc", "C-7D", .5, span=WEEK, elapsed=WEEK / 2)
    fable = _item("cc", "F-7D", .5, span=WEEK, elapsed=WEEK / 2)
    ref = fable["resource_pool_ref"]
    report = _report(common, fable)
    profiles = {"C-7D": ["cc.standard", "cc.fable"], "F-7D": ["cc.fable"]}
    assert _rows(report, profiles)[ref]["parent"] == "C-7D"
    assert _rows(report, {**profiles, "C-7D": ["cc.fable"]})[ref]["parent"] is None
    assert _rows(report, {**profiles, "F-7D": ["cc.eff"],
                          "C-7D": ["cc.effort", "cc.fable", "cc.standard"]})[ref]["parent"] is None


def _observation_connection():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE observations (capture_seq INTEGER PRIMARY KEY, observation_id TEXT, subject_ref TEXT, "
                 "resource_pool_ref TEXT, kind TEXT, source TEXT, state TEXT, payload_json TEXT, observed_at TEXT, "
                 "captured_at TEXT, effective_at_us INTEGER)")
    return conn


@pytest.mark.parametrize("history,remaining,age,fast", [
    ("distinct", .4, FRESH_AGE, True),
    ("duplicate", .4, FRESH_AGE, False),
    ("other window", .4, FRESH_AGE, False),
    ("distinct", LOW_QUOTA_FRACTION, FRESH_AGE, False),
    ("distinct", .4, AGED, False),
])
def test_fast_matches_monitor_state_from_stored_samples(history, remaining, age, fast):
    conn = _observation_connection()
    ref = "quota:ag:G-5H"
    latest = NOW - age
    for seq, at in enumerate((latest if history == "duplicate" else latest - 1, latest), 1):
        start = latest - HOUR - (SHORT if seq == 1 and history == "other window" else 0)
        payload = {"remaining_fraction": remaining, "window_started_at": start, "resets_at": start + SHORT}
        stamp = epoch_to_iso(at)
        conn.execute("INSERT INTO observations VALUES (?, ?, 'ag', ?, 'quota', 'test', 'MEASURED', ?, ?, ?, ?)",
                     (seq, str(seq), ref, json.dumps(payload), stamp, stamp, round(at * 1e6)))
    diag = ReadonlyDiag(":memory:", connection_factory=lambda _: conn)
    report = diag.render(["observations"], NOW, include_quota_samples=True)
    row = _rows(report)[ref]
    assert row["forecast"] == ("stale" if age == AGED else "threat")
    assert row["fast"] is fast
    line = next(line for line in render_plan(report, width=100).splitlines()
                if line.split()[:2] == [row["peer"], row["pool"]])
    tags = [PLAN_EARLY_TAG] if age != AGED else []
    if fast:
        tags.append(f"({PLAN_FAST_TAG})")
    assert line.endswith(" ".join([row["reason"], *tags]))


def test_cli_plan_json_facts_are_compact(capsys, monkeypatch):
    absent = _item("ag", "G-1D", None, span=DAY, elapsed=HOUR, state="ABSENT",
                   reason="not reported by provider", age=FRESH_AGE)
    running_profile = "ag.standard"
    profile_pools = {running_profile: ("G-7D", "G-5H", "G-1D", "G-2D"), "ag.fast": ("G-7D",)}
    profile_reads = []
    running = [{"peer_id": "ag", "profile": running_profile, "relation": relation} for relation in ("mine", "other")]
    running.append({"peer_id": "ag", "profile": None, "relation": "unknown"})

    def render(self, sections, *, include_quota_samples):
        assert include_quota_samples
        return _report(absent, *_flash_windows())

    def list_profiles(workspace):
        profile_reads.append(workspace)
        return [{"profile": name, "fields": {"pools": {"value": pools}}}
                for name, pools in profile_pools.items()]

    monkeypatch.setattr(ReadonlyDiag, "render", render)
    monkeypatch.setattr("peerhub.extensions.adapters.profiles.list_profiles", list_profiles)
    monkeypatch.setattr("peerhub.extensions.diag_running.running_report",
                        lambda *args, **kwargs: {"status": "OK", "rows": running, "truncated": False})
    argv = ["--db", str(Path(__file__).resolve()), "diag", "--plan"]
    assert main([*argv, "--json"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["pools"] and all(FACT_FIELDS | HEALTH_FIELDS | {"health"} <= row.keys() for row in data["pools"])
    assert data["gaps"]["baseline"] is None
    assert out == json.dumps(data, separators=(",", ":"), ensure_ascii=True) + "\n"
    assert profile_reads == [Path.cwd()]
    for row in data["pools"]:
        assert row["profiles"] == sorted(name for name, pools in profile_pools.items() if row["pool"] in pools)

    by_pool = dict.fromkeys(profile_pools[running_profile], len(running) - 1)
    assert data["load"]["by_pool"] == by_pool
    assert data["load"]["unmapped"] == 1
    assert data["load"]["active_by_relation"] == {"mine": 1, "other": 1, "unknown": 1}

    monkeypatch.setenv("COLUMNS", "180")
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert all(field not in out for field in FACT_FIELDS - {"fast"})
    weekly = next(row for row in data["pools"] if row["pool"] == "G-7D")
    assert weekly["gates"] == [] and f"({PLAN_GATE_TAG}: G-5H)" not in out
    assert profile_reads == [Path.cwd(), Path.cwd()]
    groups = [f"{row['pool']} {','.join(row['profiles'])}" for row in data["pools"]
              if row["action"] != "n/a" and row["profiles"]]
    lines = out.splitlines()
    load_start = next(index for index, line in enumerate(lines) if line.startswith("load:"))
    legend, footer, gaps = lines[load_start - 3:load_start]
    load = " ".join(lines[load_start:])
    assert legend.endswith(PLAN_TAG_LEGEND)
    assert footer == "profiles: " + " | ".join(groups)
    assert gaps.startswith("gap: ")
    assert load.startswith("load: ") and "(mine 1, other 1, unknown 1)" in load
    table_pools = {row["pool"] for row in data["pools"] if row["action"] != "n/a"}
    named_pools = ", ".join(f"{pool} {by_pool[pool]}" for pool in sorted(table_pools & by_pool.keys()))
    hidden_pools = by_pool.keys() - table_pools
    assert named_pools and len(hidden_pools) == 2
    assert f"in flight {named_pools} unmapped {data['load']['unmapped']}" in load
    assert load.endswith(f" | {PLAN_NA_POOLS_LABEL} {', '.join(sorted(hidden_pools))}")


@pytest.mark.parametrize("overflow", [None, 0, 2])
def test_load_names_only_mapped_in_flight_na_pools_and_caps_the_list(overflow):
    cap = PLAN_NA_POOLS_LIMIT
    count = 1 if overflow is None else cap + overflow
    pools = sorted(f"X-{i}H" for i in range(1, count + 1))
    idle_pool = "X-7D"
    report = _report(*(_item("cx", pool, None, span=SHORT, elapsed=HOUR, state="ABSENT",
                             reason="not reported by provider", age=FRESH_AGE) for pool in [*pools, idle_pool]))
    load = load_snapshot({"cx": 1}, ResourceSample(), dict.fromkeys(reversed(pools), 1))
    names = ", ".join(pools[:cap])
    suffix = f" +{overflow}" if overflow else ""
    load_line = render_plan(report, load=load, width=180).splitlines()[-1]
    assert load_line.startswith("load: ")
    assert load_line.endswith(f" | {PLAN_NA_POOLS_LABEL} {names}{suffix}")
    assert idle_pool not in load_line
    assert plan_data(report, {}, load=load)["load"] == load


def test_explain_parallel_quota_links_to_the_json_row_facts(capsys):
    assert main(["explain", "parallel-quota"]) == 0
    assert "docs/batch.md#parallel-work-over-the-least-quota" in capsys.readouterr().out
    guide = Path("docs/batch.md").read_text(encoding="utf-8").split("### Parallel work over the least quota", 1)[1]
    assert FACT_FIELDS <= set(re.findall(r"\w+", guide))


def test_a_stale_window_keeps_its_own_stale_flag_when_a_sibling_decides_the_reason():
    from tests.communication.diag.test_diag_plan_339 import HOUR, DAY, _by_pool, _item, _report
    rows = _by_pool(_report(_item("ag", "G-5H", .84, span=5 * HOUR, elapsed=2 * HOUR, state="STALE", age=2000.0),
                            _item("ag", "G-7D", .60, span=7 * DAY, elapsed=3.5 * DAY)))
    five, seven = rows[("ag", "G-5H")], rows[("ag", "G-7D")]
    assert "stale" in five["reason"], five["reason"]
    assert "stale" not in seven["reason"]


def test_the_stale_tag_leads_the_reason_so_a_narrow_terminal_cannot_cut_it_off():
    from tests.communication.diag.test_diag_plan_339 import DAY, HOUR, _item, _report
    report = _report(_item("ag", "G-5H", .84, span=5 * HOUR, elapsed=2 * HOUR, state="STALE", age=2000.0),
                     _item("ag", "G-7D", .60, span=7 * DAY, elapsed=3.5 * DAY))
    lines = render_plan(report, width=80, store_path="x", store_source="explicit").splitlines()
    row = next(line for line in lines if " G-5H " in line)
    assert len(row) <= 80 and "(stale)" in row
    assert row.split(maxsplit=8)[-1].startswith("(stale)")  # LEFT also carries its age badge


@pytest.mark.parametrize("age,remaining_time,expected_state,expected_reason", [
    (FRESH_AGE, HOUR, "out", "out"),
    (QUOTA_TTL_SECONDS, HOUR, "aged", "stale zero; refresh"),
    (FRESH_AGE, -FRESH_AGE / 2, "reset", "window reset; refresh"),
])
def test_zero_reason_uses_shared_state(age, remaining_time, expected_state, expected_reason):
    item = _item("cc", "C-7D", 0, span=WEEK, elapsed=WEEK - age - remaining_time, age=age)
    row = _rows(_report(item))[item["resource_pool_ref"]]
    assert row["reason"] == expected_reason and row["action"] == ("hold" if expected_state == "reset" else "blocked")
    assert row["health"]["state"] == expected_state


def test_limited_child_agrees_with_health_and_keeps_own_pace_and_reset():
    from peerhub.extensions.diag_quota import projection
    from peerhub.extensions.pool_health import pool_health
    from tests.communication.diag.test_pool_health import CHILD_LEFT

    parent = _item("cc", "C-7D", 0, span=WEEK, elapsed=WEEK * 3 / 4)
    child = _item("cc", "F-7D", CHILD_LEFT, span=WEEK, elapsed=WEEK / 2)
    report = _report(parent, child)
    profiles = {"C-7D": ["cc.standard", "cc.fable"], "F-7D": ["cc.fable"]}
    row = _rows(report, profiles)[child["resource_pool_ref"]]
    facts = pool_health(child, parent, report.read_at)
    assert row["pace_pt"] == projection(child, NOW)["pace_pt"] != facts["pace_pt"]
    assert row["resets_in_seconds"] == child["payload"]["resets_at"] - NOW != facts["reset_in_seconds"]
    assert {field: row[field] for field in HEALTH_FIELDS} == {field: facts[field] for field in HEALTH_FIELDS}
    assert row["health"] == {
        "state": "blocked", "pace_band": facts["pace_band"], "reserve": "out",
        "effective_pace_pt": facts["pace_pt"], "effective_reset_in_seconds": facts["reset_in_seconds"],
    }
    text_row = next(line.split() for line in render_plan(report, profiles_by_pool=profiles).splitlines()
                    if line.startswith("cc   F-7D"))
    assert text_row[2:4] == ["0%", format_pace(facts['pace_pt']) + "!"]
    assert text_row[4].startswith("OUT/")
    assert text_row[5:7] == ["0.00", "blocked"]
    assert row["action"] == "blocked" and row["reason"] == f"gate: {parent['resource_pool_ref'].rsplit(':', 1)[1]} out"


def test_limited_child_budget_uses_effective_left_and_nearer_reset():
    parent_pool, child_pool = "C-7D", "F-7D"
    parent = _item("cc", parent_pool, .07, span=WEEK, elapsed=WEEK / 2)
    child = _item("cc", child_pool, .26, span=WEEK, elapsed=WEEK * .75)
    profiles = {parent_pool: ["cc.standard", "cc.fable"], child_pool: ["cc.fable"]}
    report = _report(parent, child)
    rows = _rows(report, profiles)
    own = rows[child["resource_pool_ref"]]
    effective = rows[parent["resource_pool_ref"]]
    text = render_plan(report, width=120, profiles_by_pool=profiles)
    cells = next(line.split(maxsplit=7) for line in text.splitlines() if line.startswith(f"cc   {child_pool}"))
    hrs = own["health"]["effective_reset_in_seconds"] / HOUR
    assert cells[5] == f"{effective['left_pct'] / hrs:.2f}"
    assert own["target_pct_per_hour"] == round(own["left_pct"] / hrs, 2)
    assert cells[6] == "rest" and cells[7].startswith(f"{parent_pool}: ")


def test_plan_formats_pace_exhaustion_and_gaps_from_health():
    from peerhub.core.quota_policy import PLAN_REST_PACE_PT
    from tests.communication.diag.test_pool_health import HALF, OVER_BOUNDARY

    def item(peer, pool, pace):
        return _item(peer, pool, HALF, span=WEEK, elapsed=WEEK * (HALF - pace / 100), age=0)

    absent = _item("ag", "A-7D", None, span=WEEK, elapsed=HOUR, state="ABSENT",
                   reason="not reported by provider")
    unknown = _item("ag", "3P-7D", .5, span=WEEK, elapsed=HOUR)
    del unknown["payload"]["window_started_at"]
    report = _report(item("ag", "G-7D", PLAN_REST_PACE_PT + OVER_BOUNDARY),
                     item("cx", "X-7D", -OVER_BOUNDARY),
                     item("cc", "C-7D", PLAN_REST_PACE_PT), absent, unknown)
    gaps = plan_data(report, {})["gaps"]
    assert gaps["baseline"] == "X-7D"
    assert gaps["pools"]["A-7D"] is None and gaps["pools"]["3P-7D"] is None
    lines = render_plan(report, width=100).splitlines()
    assert "gap: vs X-7D (pp): C-7D +20.00, G-7D +20.00" in lines
    table = {line.split()[1]: line.split() for line in lines if line[:2] in {"ag", "cc", "cx"}}
    assert table["A-7D"][2:7] == ["-"] * 5
    assert table["3P-7D"][2:4] == ["50%", "—"]
    assert table["G-7D"][3:5] == ["+20.00!", "2d02h/4d21h!"]
    assert table["C-7D"][3] == "+20.00"
    assert table["X-7D"][3] == format_pace(0)
    assert PLAN_EARLY_TAG in table["G-7D"] and PLAN_EARLY_TAG not in table["X-7D"]


def test_exhaustion_and_reset_cells_keep_independent_fallbacks():
    item = _item("cc", "C-7D", .9, span=WEEK, elapsed=HOUR)
    row = next(line.split() for line in render_plan(_report(item)).splitlines() if line.startswith("cc "))
    assert row[4].startswith("—/") and not row[4].endswith("!")
    unavailable = {**item, "payload": {"remaining_fraction": .9}}
    row = next(line.split() for line in render_plan(_report(unavailable)).splitlines() if line.startswith("cc "))
    assert row[4] == "—/—"


def test_exhaustion_cell_formats_whole_hours_and_tags_early_forecasts():
    from tests.communication.diag.test_pool_health import HALF

    hours_to_exhaust, hours_to_reset = 20, 34
    item = _item("cc", "C-7D", HALF, span=(hours_to_exhaust + hours_to_reset) * HOUR,
                 elapsed=hours_to_exhaust * HOUR, age=0)
    row = next(line.split() for line in render_plan(_report(item)).splitlines() if line.startswith("cc "))
    assert row[4] == "20h/1d10h!" and PLAN_EARLY_TAG in row


def test_early_fact_keeps_existing_hold_advice_below_projection_threat_threshold():
    from tests.communication.diag.test_pool_health import HALF

    item = _item("cc", "C-7D", HALF, span=WEEK, elapsed=WEEK * (HALF - .01))
    row = _rows(_report(item))[item["resource_pool_ref"]]
    assert row["action"] == "hold" and row["forecast"] == "on_pace"
    assert row["exhaust_before_reset"]


def test_exhausted_profile_window_gates_all_its_pools_in_its_account_only():
    gate, parent, child, sibling, unrelated = "C-5H", "C-7D", "F-7D", "F-5H", "C-1H"
    profile, standard = "cc.fable", "cc.standard"
    profiles = {gate: [profile], parent: [profile, standard], child: [profile], sibling: [profile],
                unrelated: [standard]}
    empty = _item("cc", gate, 0, span=SHORT, elapsed=2 * HOUR)
    pairs = [(parent, WEEK), (child, WEEK), (sibling, SHORT), (unrelated, HOUR)]
    items = [_item("cc", pool, .8, span=span, elapsed=span / 2) for pool, span in pairs]
    other_account = {**items[0], "resource_pool_ref": f"quota:another-account:{parent}"}
    other_peer = _item("ag", parent, .8, span=WEEK, elapsed=WEEK / 2)
    unmapped_gate = _item("ag", "G-5H", 0, span=SHORT, elapsed=2 * HOUR)
    unmapped_week = _item("ag", "G-7D", .8, span=WEEK, elapsed=WEEK / 2)
    report = _report(empty, *items, other_account, other_peer, unmapped_gate, unmapped_week)
    rows = _rows(report, profiles)
    alone = _rows(_report(*items), profiles)
    text = render_plan(report, profiles_by_pool=profiles, width=140)
    reason = f"{PLAN_GATE_TAG}: {gate} out"
    for item in items[:3]:
        ref = item["resource_pool_ref"]
        row = rows[ref]
        assert (row["action"], row["binding"], row["reason"]) == ("blocked", gate, reason)
        assert any(line.split()[:2] == [row["peer"], row["pool"]] and reason in line for line in text.splitlines())
        for field in ("left_pct", "resets_in_seconds", "pace_pt", "target_pct_per_hour"):
            assert row[field] == alone[ref][field]
    unblocked = _rows(_report(*items, other_account, other_peer, unmapped_week), profiles)
    for item in (items[-1], other_account, other_peer, unmapped_week):
        row = rows[item["resource_pool_ref"]]
        assert row["action"] == "spend" and row["binding"] == unblocked[item["resource_pool_ref"]]["binding"]
        assert gate not in row["reason"]


@pytest.mark.parametrize("parent_left,gate_left,gate_action,expected_action", [
    (.7, .8, "spend", "spend"),
    (.2, .8, "spend", "rest"),
    (.7, .5, "hold", "hold"),
    (.7, .2, "rest", "rest"),
    (.7, 0, "blocked", "blocked"),
])
def test_limited_weekly_pool_combines_scoped_gates(parent_left, gate_left, gate_action, expected_action):
    parent, child, gate, sibling = "C-7D", "F-7D", "F-5H", "F-3H"
    other_profile, other_account_pool = "F-2H", "F-1H"
    fable, standard = "cc.fable", "cc.standard"
    profiles = {parent: [fable, standard], child: [fable], gate: [fable],
                sibling: [fable], other_profile: [standard], other_account_pool: [fable]}
    common = _item("cc", parent, parent_left, span=WEEK, elapsed=WEEK / 2)
    limited = _item("cc", child, .8, span=WEEK, elapsed=WEEK / 2)
    short = _item("cc", gate, gate_left, span=SHORT, elapsed=SHORT / 2)
    spare = _item("cc", sibling, .8, span=3 * HOUR, elapsed=1.5 * HOUR)
    unrelated = _item("cc", other_profile, 0, span=2 * HOUR, elapsed=HOUR)
    other_account = {**short, "resource_pool_ref": f"quota:another-account:{other_account_pool}",
                     "payload": {**short["payload"], "remaining_fraction": 0}}
    report = _report(common, limited, short, spare, unrelated, other_account)

    row = _rows(report, profiles)[limited["resource_pool_ref"]]
    assert row["limited_by"] == common["resource_pool_ref"]
    assert row["action"] == expected_action
    winning_pool = parent if gate_action == "spend" else gate
    assert row["binding"] == winning_pool and winning_pool in row["reason"]
    gates = {entry["pool"]: entry for entry in row["gates"]}
    assert gates[gate]["action"] == gate_action
    assert gates[sibling]["action"] == "spend"
    if winning_pool == gate:
        assert row["reason"] == f"{PLAN_GATE_TAG}: {gate} {gates[gate]['reason']}"
    assert {other_profile, other_account_pool}.isdisjoint(gates)
    line = next(line.split(maxsplit=7) for line in render_plan(report, profiles_by_pool=profiles, width=140).splitlines()
                if line.split()[:2] == [row["peer"], child])
    assert line[6] == expected_action and line[7].startswith(row["reason"])


@pytest.mark.parametrize("parent_left,parent_elapsed,child_left,child_elapsed,child_age,gate_left,winner,expected", [
    (.7, WEEK / 2, .75, WEEK / 5, FRESH_AGE, .8, "child", "rest"),
    (.2, WEEK / 2, .75, WEEK / 2, FRESH_AGE, .8, "parent", "rest"),
    (.4, WEEK * .7, .5, WEEK / 2, FRESH_AGE, .2, "gate", "rest"),
    (.7, WEEK / 2, 0, WEEK / 2, AGED, 0, "child", "blocked"),
], ids=["child-rest", "parent-rest", "gate-rest", "stale-zero"])
def test_child_action_ranks_its_own_parent_and_gate_actions(
    parent_left, parent_elapsed, child_left, child_elapsed, child_age, gate_left, winner, expected,
):
    parent, child, gate = "C-7D", "F-7D", "F-5H"
    profile = "cc.fable"
    profiles = {parent: ["cc.standard", profile], child: [profile], gate: [profile]}
    items = {
        "parent": _item("cc", parent, parent_left, span=WEEK, elapsed=parent_elapsed, age=FRESH_AGE),
        "child": _item("cc", child, child_left, span=WEEK, elapsed=child_elapsed, age=child_age),
        "gate": _item("cc", gate, gate_left, span=SHORT, elapsed=SHORT / 2, age=FRESH_AGE),
    }
    report = _report(*items.values())
    rows = _rows(report, profiles)
    child_ref = items["child"]["resource_pool_ref"]
    parent_ref = items["parent"]["resource_pool_ref"]
    winner_ref = items[winner]["resource_pool_ref"]
    own_winner = _rows(_report(items[winner]))[winner_ref]
    row = rows[child_ref]
    assert row["limited_by"] == (None if child_age == AGED else parent_ref)
    assert row["action"] == own_winner["action"] == expected
    assert row["binding"] == own_winner["pool"]
    if winner == "child":
        assert row["reason"] == own_winner["reason"]
        if child_age == AGED:
            assert row["stale"] and "refresh" in row["reason"]
            assert any(entry["pool"] == gate and entry["action"] == "blocked" for entry in row["gates"])
        else:
            assert {"pool": child, "action": row["action"], "reason": row["reason"]} in rows[parent_ref]["gates"]
    else:
        binding_gate = next(entry for entry in row["gates"] if entry["pool"] == row["binding"])
        assert binding_gate["action"] == row["action"]
        assert binding_gate["reason"] == own_winner["reason"]
        prefix = f"{parent}:" if winner == "parent" else f"{PLAN_GATE_TAG}: {gate}"
        assert row["reason"] == f"{prefix} {binding_gate['reason']}"
    line = next(line for line in render_plan(report, profiles_by_pool=profiles, width=140).splitlines()
                if line.split()[:2] == [row["peer"], child])
    cells, reason, _ = line.partition(row["reason"])
    assert reason and cells.split()[-1] == expected


def test_family_gate_binding_agrees_with_the_action_even_when_another_pool_has_less_left():
    weekly = _item("ag", "G-7D", .7, span=WEEK, elapsed=WEEK / 2)
    short = _item("ag", "G-5H", .75, span=SHORT, elapsed=SHORT / 5)
    rows = _rows(_report(weekly, short))
    row = rows[weekly["resource_pool_ref"]]
    own_gate = _rows(_report(short))[short["resource_pool_ref"]]
    assert row["action"] == own_gate["action"] == "rest"
    assert row["binding"] == own_gate["pool"]
    assert f"{row['binding']}: {own_gate['reason']}" == row["reason"]
    assert {"pool": row["binding"], "action": row["action"], "reason": own_gate["reason"]} in row["gates"]


def test_parent_forecast_votes_when_only_its_state_limits_the_child():
    parent, child = "C-7D", "F-7D"
    common = _item("cc", parent, .6, span=WEEK, elapsed=WEEK / 4)
    common["projection_samples"] = 2
    limited = _item("cc", child, .5, span=WEEK, elapsed=WEEK * .7)
    profiles = {parent: ["cc.standard", "cc.fable"], child: ["cc.fable"]}
    row = _rows(_report(common, limited), profiles)[limited["resource_pool_ref"]]
    assert row["health"]["state"] == "fast" and row["limited_by"] == common["resource_pool_ref"]
    assert row["health"]["effective_pace_pt"] == row["pace_pt"]
    assert row["action"] == "rest" and row["binding"] == parent
    (gate,) = row["gates"]
    assert gate["action"] == row["action"] and gate["pool"] == row["binding"]
    assert row["reason"] == f"{PLAN_GATE_TAG}: {parent} {gate['reason']}"


@pytest.mark.parametrize("age,elapsed,action", [(AGED, HOUR, "blocked"), (FRESH_AGE, SHORT - FRESH_AGE / 2, "hold")])
def test_stale_or_reset_zero_does_not_gate_the_profile(age, elapsed, action):
    gate, pool = "G-5H", "G-7D"
    empty = _item("ag", gate, 0, span=SHORT, elapsed=elapsed, age=age)
    available = _item("ag", pool, .8, span=WEEK, elapsed=WEEK / 2)
    profiles = dict.fromkeys((gate, pool), ["ag.neon"])
    rows = _rows(_report(empty, available), profiles)
    assert rows[empty["resource_pool_ref"]]["action"] == action
    weekly = rows[available["resource_pool_ref"]]
    assert weekly["action"] == "spend" and weekly["binding"] == pool


REPORTER_TIME_GAP = 5
SPARE_WEEKLY_LEFT = .8


@pytest.mark.parametrize("reporter_offset,own_left,shared_left", [
    (-REPORTER_TIME_GAP, 0, 0),
    (REPORTER_TIME_GAP, 0, 0),
    (-REPORTER_TIME_GAP, SPARE_WEEKLY_LEFT, 0),
    (REPORTER_TIME_GAP, 0, SPARE_WEEKLY_LEFT),
], ids=["older-reporter", "newer-reporter", "only-other-out", "newer-nonzero"])
def test_shared_pool_reporters_keep_a_fresh_profile_gate(reporter_offset, own_left, shared_left):
    gate, pool = "C-5H", "C-7D"
    profiles = dict.fromkeys((gate, pool), ["cc.standard"])
    own = _item("cc", gate, own_left, span=SHORT, elapsed=HOUR, age=FRESH_AGE)
    shared = _item("other", gate, shared_left, span=SHORT, elapsed=HOUR, age=FRESH_AGE - reporter_offset)
    shared["resource_pool_ref"] = own["resource_pool_ref"]
    weekly = _item("cc", pool, SPARE_WEEKLY_LEFT, span=WEEK, elapsed=WEEK / 2)
    report = _report(own, shared, weekly)

    row = _rows(report, profiles)[weekly["resource_pool_ref"]]
    assert (row["action"], row["binding"], row["reason"]) == ("blocked", gate, f"{PLAN_GATE_TAG}: {gate} out")
    assert row["gates"] == [{"pool": gate, "action": "blocked", "reason": "out"}]
    line = next(line.split(maxsplit=7) for line in render_plan(report, profiles_by_pool=profiles, width=120).splitlines()
                if line.split()[:2] == [row["peer"], pool])
    assert line[6] == row["action"] and line[7].startswith(row["reason"])


@pytest.mark.parametrize("window_offset,earlier_age,expected_state", [
    (0, 1, "fast"),
    (0, 1.125, "fast"),
    (SHORT, SHORT, "ok"),
], ids=["same-window", "fractional-observation-clock", "next-window"])
def test_relative_reset_samples_only_count_within_one_window(window_offset, earlier_age, expected_state):
    from peerhub.cli.monitor_view import render_grouped_frame
    from tests.communication.diag.test_monitor_pool_health import frame, pool_cells

    peer, pool = "ag", "G-5H"
    latest = _item(peer, pool, .4, span=SHORT, elapsed=HOUR, age=0)
    earlier = _item(peer, pool, .4, span=SHORT, elapsed=HOUR + window_offset - earlier_age, age=earlier_age)
    conn = _observation_connection()
    for seq, item in enumerate((earlier, latest), 1):
        payload = item["payload"]
        at = NOW - item["age_seconds"]
        payload["reset_in_seconds"] = payload.pop("resets_at") - at
        observed, captured_at = epoch_to_iso(at), at
        if item is earlier and not float(earlier_age).is_integer():
            observed = observed.replace("T", "t").replace("Z", "z")
            captured_at += .25
        conn.execute("INSERT INTO observations VALUES (?, ?, ?, ?, 'quota', 'test', 'MEASURED', ?, ?, ?, ?)",
                     (seq, str(seq), peer, item["resource_pool_ref"], json.dumps(payload), observed,
                      epoch_to_iso(captured_at), round(captured_at * 1e6)))

    diag = ReadonlyDiag(":memory:", connection_factory=lambda _: conn)
    report = diag.render(["observations"], NOW, include_quota_samples=True)
    row = _rows(report)[latest["resource_pool_ref"]]
    assert row["forecast"] == "threat"
    assert row["health"]["state"] == expected_state
    assert row["fast"] is (expected_state == "fast")
    plan_line = next(line for line in render_plan(report, width=120).splitlines()
                     if line.split()[:2] == [peer, pool])
    assert (f"({PLAN_FAST_TAG})" in plan_line) is row["fast"]
    monitor_frame = frame(*report.sections["observations"].data["items"])
    for expand in (False, True):
        cells = pool_cells(render_grouped_frame(monitor_frame, view="plain", width=120, expand=expand), pool)
        assert cells and all(cell[0] == expected_state for cell in cells)


def test_plan_and_health_agree_at_spend_boundary_with_float_noise():
    pool = "C-7D"
    left = .45
    elapsed = WEEK * (1 - left + PLAN_SPEND_PACE_PT / 100)
    item = _item("cc", pool, left, span=WEEK, elapsed=elapsed, age=0)
    report = _report(item)
    row = _rows(report)[item["resource_pool_ref"]]
    assert row["pace_pt"] == pytest.approx(-PLAN_SPEND_PACE_PT)
    assert row["health"]["pace_band"] == row["action"] == "spend"
    cells = next(line.split() for line in render_plan(report, width=120).splitlines()
                 if line.split()[:2] == [row["peer"], pool])
    assert cells[3] == format_pace(-PLAN_SPEND_PACE_PT) and cells[6] == row["action"]


@pytest.mark.parametrize("state, reason, age, action", [
    ("UNKNOWN", "probe incomplete", FRESH_AGE, "blocked"),
    ("ABSENT", "not reported by provider; plan=unknown", AGED, "blocked"),
    ("ABSENT", "not reported by provider; plan=prolite", FRESH_AGE, "n/a"),
    ("STALE", None, AGED, "blocked"),
])
@pytest.mark.parametrize("peer, family", [("cc", "C"), ("cx", "X")])
def test_unknown_short_window_never_appears_as_a_weekly_gate(state, reason, age, action, peer, family):
    short_name, weekly_name = f"{family}-5H", f"{family}-7D"
    short = _item(peer, short_name, 0.0 if state == "STALE" else None, span=SHORT, elapsed=HOUR,
                  state=state, age=age, reason=reason)
    weekly = _item(peer, weekly_name, .5, span=WEEK, elapsed=WEEK / 2)
    profiles = dict.fromkeys((short_name, weekly_name), [f"{peer}.standard"])
    report = _report(short, weekly)
    rows = _rows(report, profiles)

    assert rows[short["resource_pool_ref"]]["action"] == action
    row = rows[weekly["resource_pool_ref"]]
    assert (row["action"], row["binding"], row["gates"]) == ("hold", weekly_name, [])
    rendered = render_plan(report, width=140, profiles_by_pool=profiles)
    line = next(line for line in rendered.splitlines() if line.split()[:2] == [peer, weekly_name])
    assert "gate:" not in line


@pytest.mark.parametrize("same_pool", [True, False])
def test_diag_shared_parent_keeps_consumers_when_alias_is_the_displayed_report(same_pool):
    common = _item("cc", "C-7D", .8, span=WEEK, elapsed=WEEK / 2, age=FRESH_AGE)
    child = _item("cc", "F-7D", .9, span=WEEK, elapsed=WEEK / 2, age=FRESH_AGE)
    alias = _item("cc2", "C-7D", .25, span=WEEK, elapsed=WEEK / 2, age=FRESH_AGE - 1)
    if same_pool:
        alias["resource_pool_ref"] = common["resource_pool_ref"]
    report = _report(common, child, alias)

    rows = _rows(report, WEEKLY_PROFILES)
    parent = rows[common["resource_pool_ref"]]
    assert parent["peer"] == ("cc2" if same_pool else "cc")
    assert parent["profiles"] == WEEKLY_PROFILES["C-7D"]
    assert rows[child["resource_pool_ref"]]["limited_by"] == common["resource_pool_ref"]
    rendered = render_view(report, width=140, profiles_by_pool=WEEKLY_PROFILES)
    line = next(line for line in rendered.splitlines() if line.startswith("cc   F-7D"))
    assert ("25%" if same_pool else "80%") in line
