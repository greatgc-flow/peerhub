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
from peerhub.cli.view import PLAN_FAST_TAG, PLAN_GATE_TAG, PLAN_TAG_LEGEND, plan_data, render_plan
from peerhub.core.quota_policy import QUOTA_TTL_SECONDS
from peerhub.extensions.diag import ReadonlyDiag
from peerhub.extensions.observation_model import LOW_QUOTA_FRACTION, epoch_to_iso
from tests.communication.diag.test_diag_plan_339 import DAY, HOUR, NOW, _item, _report

FRESH_AGE = QUOTA_TTL_SECONDS / 2
AGED = QUOTA_TTL_SECONDS * 2
SHORT = 5 * HOUR
WEEK = 7 * DAY
FACT_FIELDS = {"age_seconds", "ttl_seconds", "fast", "family", "parent", "gates"}


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
    assert PLAN_TAG_LEGEND in out.splitlines()[-1]


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
    assert line.endswith(row["reason"] + (f" ({PLAN_FAST_TAG})" if fast else ""))


def test_cli_plan_json_facts_are_compact(capsys, monkeypatch):
    absent = _item("ag", "G-1D", None, span=DAY, elapsed=HOUR, state="ABSENT",
                   reason="not reported by provider", age=FRESH_AGE)
    running_profile = "ag.standard"
    profile_pools = {running_profile: ("G-7D", "G-5H", "G-1D", "G-2D"), "ag.fast": ("G-7D",)}
    profile_reads = []
    running = [{"peer_id": "ag", "profile": running_profile}] * 2 + [{"peer_id": "ag", "profile": None}]

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
    assert data["pools"] and all(FACT_FIELDS <= row.keys() for row in data["pools"])
    assert out == json.dumps(data, separators=(",", ":"), ensure_ascii=True) + "\n"
    assert profile_reads == [Path.cwd()]
    for row in data["pools"]:
        assert row["profiles"] == sorted(name for name, pools in profile_pools.items() if row["pool"] in pools)

    by_pool = dict.fromkeys(profile_pools[running_profile], len(running) - 1)
    assert data["load"]["by_pool"] == by_pool
    assert data["load"]["unmapped"] == 1

    monkeypatch.setenv("COLUMNS", "180")
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert all(field not in out for field in FACT_FIELDS - {"fast"})
    weekly = next(row for row in data["pools"] if row["gates"])
    assert all(f"({PLAN_GATE_TAG}: {gate['pool']})" in out for gate in weekly["gates"])
    assert profile_reads == [Path.cwd(), Path.cwd()]
    groups = [f"{row['pool']} {','.join(row['profiles'])}" for row in data["pools"]
              if row["action"] != "n/a" and row["profiles"]]
    legend, footer, load = out.splitlines()[-3:]
    assert legend.endswith(PLAN_TAG_LEGEND)
    assert footer == "profiles: " + " | ".join(groups)
    assert load.startswith("load: ")
    table_pools = {row["pool"] for row in data["pools"] if row["action"] != "n/a"}
    named_pools = ", ".join(f"{pool} {by_pool[pool]}" for pool in sorted(table_pools & by_pool.keys()))
    hidden_pools = by_pool.keys() - table_pools
    assert named_pools and len(hidden_pools) == 2
    assert f"in flight {named_pools} unmapped {data['load']['unmapped']}" in load
    assert all(pool not in load for pool in hidden_pools)
    assert load.count(f"n/a pools {len(hidden_pools)}") == 1


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
