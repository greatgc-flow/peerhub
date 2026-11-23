"""Extra JSON evidence for planning a wave from stored quota readings."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.cli.monitor_view import FAMILIES
from peerhub.cli.view import PLAN_EARLY_TAG, PLAN_FAST_TAG, PLAN_GATE_TAG, PLAN_TAG_LEGEND, plan_data, render_plan
from peerhub.core.load_limits import PLAN_NA_POOLS_LABEL, PLAN_NA_POOLS_LIMIT, load_snapshot
from peerhub.core.quota_policy import QUOTA_TTL_SECONDS
from peerhub.extensions.diag import ReadonlyDiag
from peerhub.extensions.observation_model import LOW_QUOTA_FRACTION, epoch_to_iso
from tests.communication.diag.test_diag_plan_339 import DAY, HOUR, NOW, _item, _report

FRESH_AGE = QUOTA_TTL_SECONDS / 2
AGED = QUOTA_TTL_SECONDS * 2
SHORT = 5 * HOUR
WEEK = 7 * DAY
FACT_FIELDS = {"age_seconds", "ttl_seconds", "fast", "family", "parent", "gates"}
HEALTH_FIELDS = {"even_left_pct", "exhaust_in_seconds", "exhaust_before_reset", "stale", "limited_by", "own_left_pct"}


def _rows(report, profiles=None):
    return {r["resource_pool_ref"]: r for r in plan_data(report, {}, profiles_by_pool=profiles)["pools"]}


def _flash_windows():
    return (_item("ag", "G-5H", .3, span=SHORT, elapsed=SHORT - FRESH_AGE / 2, age=FRESH_AGE),
            _item("ag", "G-7D", .5, span=WEEK, elapsed=WEEK / 2, age=FRESH_AGE),
            _item("ag", "A-5H", 0, span=SHORT, elapsed=HOUR, age=FRESH_AGE))


def test_reset_gate_is_visible_alongside_a_weekly_hold():
    rows = _rows(_report(*_flash_windows()))
    weekly, short, argon = (rows[f"quota:ag:{pool}"] for pool in ("G-7D", "G-5H", "A-5H"))
    assert weekly["action"] == "hold" and short["action"] == "blocked"
    assert weekly["gates"] == [{"pool": short["pool"], "action": short["action"], "reason": "reset"}]
    assert weekly["parent"] is None and short["parent"] == weekly["pool"]
    assert weekly["family"] == short["family"] == FAMILIES["G"]
    assert argon["family"] == FAMILIES["A"] and argon["parent"] is None
    assert short["gates"] == argon["gates"] == []
    assert weekly["age_seconds"] == FRESH_AGE and weekly["ttl_seconds"] == QUOTA_TTL_SECONDS

    out = render_plan(_report(*_flash_windows()), width=100)
    line = next(line for line in out.splitlines() if line.split()[:2] == [weekly["peer"], weekly["pool"]])
    assert line.endswith(f"{weekly['reason']} ({PLAN_GATE_TAG}: {short['pool']})")
    assert any(line.endswith(PLAN_TAG_LEGEND) for line in out.splitlines())


def test_weekly_gates_use_each_siblings_own_action_and_skip_na_and_other_accounts():
    _, weekly, _ = _flash_windows()
    rested = _item("ag", "G-5H", .3, span=SHORT, elapsed=HOUR, age=FRESH_AGE)
    usable = _item("ag", "G-1H", .9, span=HOUR, elapsed=HOUR / 2, age=FRESH_AGE)
    absent = _item("ag", "G-1D", None, span=DAY, elapsed=HOUR, state="ABSENT",
                   reason="not reported by provider", age=FRESH_AGE)
    other_account = {**rested, "resource_pool_ref": "quota:other:G-2H"}
    blocked = _item("ag", "G-3H", .3, span=3 * HOUR, elapsed=3 * HOUR - FRESH_AGE / 2, age=FRESH_AGE)
    report = _report(weekly, rested, usable, absent, other_account, blocked)
    rows = _rows(report)
    assert rows[usable["resource_pool_ref"]]["action"] == "rest"  # inherits the 5H action
    assert rows[absent["resource_pool_ref"]]["action"] == "n/a"
    gates = rows[weekly["resource_pool_ref"]]["gates"]
    rested_row = _rows(_report(rested))[rested["resource_pool_ref"]]
    assert gates == [
        {"pool": rows[blocked["resource_pool_ref"]]["pool"], "action": "blocked", "reason": "reset"},
        {"pool": rested_row["pool"], "action": rested_row["action"], "reason": rested_row["reason"]},
    ]
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


@pytest.mark.parametrize("history,remaining,age,fast", [
    ("distinct", .4, FRESH_AGE, True),
    ("duplicate", .4, FRESH_AGE, False),
    ("other window", .4, FRESH_AGE, False),
    ("distinct", LOW_QUOTA_FRACTION, FRESH_AGE, False),
    ("distinct", .4, AGED, False),
])
def test_fast_matches_monitor_state_from_stored_samples(history, remaining, age, fast):
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE observations (capture_seq INTEGER PRIMARY KEY, observation_id TEXT, subject_ref TEXT, "
                 "resource_pool_ref TEXT, kind TEXT, source TEXT, state TEXT, payload_json TEXT, observed_at TEXT, "
                 "captured_at TEXT, effective_at_us INTEGER)")
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
    assert weekly["gates"]
    assert all(f"({PLAN_GATE_TAG}: {gate['pool']})" in out for gate in weekly["gates"])
    assert profile_reads == [Path.cwd(), Path.cwd()]
    groups = [f"{row['pool']} {','.join(row['profiles'])}" for row in data["pools"]
              if row["action"] != "n/a" and row["profiles"]]
    legend, footer, gaps, load = out.splitlines()[-4:]
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
    load = load_snapshot({"cx": 1}, None, dict.fromkeys(reversed(pools), 1))
    names = ", ".join(pools[:cap])
    suffix = f" +{overflow}" if overflow else ""
    load_line = render_plan(report, load=load, width=180).splitlines()[-1]
    assert load_line.startswith("load: ")
    assert load_line.endswith(f" | {PLAN_NA_POOLS_LABEL} {names}{suffix}")
    assert idle_pool not in load_line
    assert plan_data(report, {}, load=load)["load"] == load


def test_explain_parallel_quota_names_the_json_row_facts(capsys):
    assert main(["explain", "parallel-quota"]) == 0
    assert FACT_FIELDS <= set(re.findall(r"\w+", capsys.readouterr().out))


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
    assert row["reason"] == expected_reason and row["action"] == "blocked"
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
    assert text_row[2:4] == ["0%", f"{facts['pace_pt']:+.1f}!"]
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
    assert own["target_pct_per_hour"] == pytest.approx(own["left_pct"] / hrs)
    assert cells[6] == "rest" and cells[7].startswith(f"{parent_pool}: ")


def test_plan_formats_pace_exhaustion_and_gaps_from_health():
    from peerhub.cli.view import PLAN_REST_PACE_PT
    from tests.communication.diag.test_pool_health import HALF, OVER_BOUNDARY

    def item(peer, pool, pace):
        return _item(peer, pool, HALF, span=WEEK, elapsed=WEEK * (HALF - pace / 100), age=0)

    report = _report(item("ag", "G-7D", PLAN_REST_PACE_PT + OVER_BOUNDARY),
                     item("cx", "X-7D", -OVER_BOUNDARY),
                     item("cc", "C-7D", PLAN_REST_PACE_PT))
    gaps = plan_data(report, {})["gaps"]
    assert gaps["baseline"] == "X-7D"
    lines = render_plan(report, width=100).splitlines()
    assert "gap: vs X-7D (pp): C-7D +20.0, G-7D +20.0, A-7D —, 3P-7D —" in lines
    table = {line.split()[1]: line.split() for line in lines if line[:2] in {"ag", "cc", "cx"}}
    assert table["G-7D"][3:5] == ["+20.0!", "2d02h/4d21h!"]
    assert table["C-7D"][3] == "+20.0"
    assert table["X-7D"][3] == "0.0"
    assert PLAN_EARLY_TAG in table["G-7D"] and PLAN_EARLY_TAG not in table["X-7D"]


def test_exhaustion_and_reset_cells_keep_independent_fallbacks():
    item = _item("cc", "C-7D", .5, span=WEEK, elapsed=HOUR)
    row = next(line.split() for line in render_plan(_report(item)).splitlines() if line.startswith("cc "))
    assert row[4].startswith("—/") and not row[4].endswith("!")
    unavailable = {**item, "payload": {"remaining_fraction": .5}}
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


@pytest.mark.parametrize("age,elapsed", [(AGED, HOUR), (FRESH_AGE, SHORT - FRESH_AGE / 2)])
def test_stale_or_reset_zero_does_not_gate_the_profile(age, elapsed):
    gate, pool = "G-5H", "G-7D"
    empty = _item("ag", gate, 0, span=SHORT, elapsed=elapsed, age=age)
    available = _item("ag", pool, .8, span=WEEK, elapsed=WEEK / 2)
    profiles = dict.fromkeys((gate, pool), ["ag.neon"])
    rows = _rows(_report(empty, available), profiles)
    assert rows[empty["resource_pool_ref"]]["action"] == "blocked"
    weekly = rows[available["resource_pool_ref"]]
    assert weekly["action"] == "spend" and weekly["binding"] == pool
