"""Read-only diag plan reports current lease counts and machine resources."""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from peerhub.cli.app import main
from peerhub.core import load_limits as L
from tests.communication.diag.test_diag_plan_339 import plan_store  # noqa: F401  (fixture)

pytestmark = [pytest.mark.unit]

RAM = L.MIN_FREE_RAM_GB + 2
CORES = 12
BUSY = L.CPU_BUSY_WARN_PCT - 10
RESOURCES = L.ResourceSample(RAM * L._GB, BUSY, CORES)


def test_snapshot_exposes_counts_and_resources():
    load = L.load_snapshot({"cx": 1, "ag": CORES}, RESOURCES)
    assert load["active"] == CORES + 1
    assert load["peers"] == {"cx": {"active": 1}, "cc": {"active": 0}, "ag": {"active": CORES}}
    assert {"limit_default", "limit_ceiling", "slots_free"}.isdisjoint(load)
    assert load["ram_free_gb"] == RAM and load["ram_min_gb"] == L.MIN_FREE_RAM_GB and load["ram_ok"] is True
    assert load["cpu_busy_pct"] == BUSY and load["cpu_busy_warn_pct"] == L.CPU_BUSY_WARN_PCT
    assert load["cpu_logical_cores"] == CORES


def test_snapshot_reconciles_peers_outside_the_three_cli_peers():
    load = L.load_snapshot({"ca": 1, "zz": 2}, L.ResourceSample())
    assert load["active"] == 3
    assert load["peers"]["ca"] == {"active": 1}
    assert load["peers"]["zz"] == {"active": 2}


def test_ram_unknown_and_low_and_checks_before_display_rounding():
    unknown = L.load_snapshot({}, L.ResourceSample())
    assert unknown["ram_free_gb"] is None and unknown["ram_ok"] is None
    low = L.load_snapshot({}, L.ResourceSample(L.MIN_FREE_RAM_GB * L._GB - 1))
    assert low["ram_free_gb"] == L.MIN_FREE_RAM_GB and low["ram_ok"] is False


def test_format_line_is_one_short_line():
    line = L.format_load(L.load_snapshot({"cx": 1}, RESOURCES))
    assert line == (f"load: active 1 | cc 0 cx 1 ag 0 | free RAM {RAM:.1f} GB (min {L.MIN_FREE_RAM_GB}) | "
                    f"CPU {BUSY}% of {CORES} (warn {L.CPU_BUSY_WARN_PCT}) | sampled now")
    other = L.format_load(L.load_snapshot({"ca": 1, "zz": 2}, L.ResourceSample()))
    assert "ca 1" in other and "zz 2" in other and "free RAM ? GB" in other and "CPU ? of ?" in other


def test_plan_json_and_text_carry_load(plan_store, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr("peerhub.core.load_limits.read_resources", lambda: RESOURCES)
    assert main(["--db", str(plan_store), "diag", "--plan", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["load"]["active"] == 0 and data["pools"]
    assert data["load"]["unmapped"] == 0
    assert all(data["load"][key] == value for key, value in RESOURCES.facts().items())
    assert main(["--db", str(plan_store), "diag", "--plan"]) == 0
    out = capsys.readouterr().out
    assert L.format_load(data["load"]) in out
    assert "unmapped" not in out


def test_unknown_leases_show_a_question_mark_never_zero():
    load = L.load_snapshot(None, RESOURCES)
    assert load["active"] is None and load["unmapped"] is None
    assert load["peers"]["cx"] == {"active": None}
    line = L.format_load(load)
    assert line.startswith("load: active ? | cc ? cx ? ag ? |") and f"free RAM {RAM:.1f} GB" in line


@pytest.mark.parametrize("psutil_cores", [CORES, None])
@pytest.mark.real_resources
def test_read_resources_samples_cpu_with_psutil_and_falls_back_for_core_count(monkeypatch, psutil_cores):
    cpu_percent = Mock(return_value=BUSY)
    monkeypatch.setattr(L.psutil, "cpu_percent", cpu_percent)
    monkeypatch.setattr(L.psutil, "cpu_count", lambda: psutil_cores)
    monkeypatch.setattr(L.os, "cpu_count", lambda: CORES + 1)
    monkeypatch.setattr(L, "free_ram_bytes", lambda: RESOURCES.ram_available_bytes)

    assert L.read_resources() == L.ResourceSample(RESOURCES.ram_available_bytes, BUSY, psutil_cores or CORES + 1)
    cpu_percent.assert_called_once_with(interval=L.CPU_SAMPLE_SECONDS)


@pytest.mark.real_resources
def test_failed_cpu_reads_stay_unknown_when_no_core_count_is_available(monkeypatch):
    monkeypatch.setattr(L.psutil, "cpu_percent", Mock(side_effect=OSError))
    monkeypatch.setattr(L.psutil, "cpu_count", Mock(side_effect=OSError))
    monkeypatch.setattr(L.os, "cpu_count", lambda: None)
    monkeypatch.setattr(L, "free_ram_bytes", lambda: RESOURCES.ram_available_bytes)

    assert L.read_resources() == L.ResourceSample(RESOURCES.ram_available_bytes)


def test_sample_load_counts_every_active_ask_and_fails_to_unknown(monkeypatch):
    from peerhub.extensions import diag_running
    seen = {}

    count = diag_running.MAX_ROWS ** 3

    def fake(db, *, read_at=None, all_rows=False, connection_factory=None):
        seen["all_rows"] = all_rows
        return {"status": "OK", "rows": [{"peer_id": "cx"}] * count, "truncated": False}

    monkeypatch.setattr(diag_running, "running_report", fake)
    monkeypatch.setattr(L, "read_resources", lambda: RESOURCES)
    load = diag_running.sample_load("x.db")
    assert load["active"] == count and seen["all_rows"]
    monkeypatch.setattr(diag_running, "running_report", lambda db, **kw: {"status": "FAILED", "rows": [], "truncated": False})
    assert diag_running.sample_load("x.db")["active"] is None
    monkeypatch.setattr(diag_running, "running_report", lambda db, **kw: {"status": "OK", "rows": [], "truncated": True})
    cutoff = diag_running.sample_load("x.db")
    assert cutoff["active"] is None, "a cut-off list is not a count"
    assert cutoff["active_by_relation"] == dict.fromkeys(("mine", "other", "unknown"))
    assert "(mine" not in L.format_load(cutoff)


@pytest.mark.parametrize("relations", [("mine",), ("mine", "other", "unknown")])
def test_load_counts_each_relation_and_formats_only_a_non_mine_split(monkeypatch, relations):
    from peerhub.extensions import diag_running

    monkeypatch.setattr(L, "read_resources", lambda: RESOURCES)
    rows = [{"peer_id": "cx", "relation": relation} for relation in relations]
    monkeypatch.setattr(diag_running, "running_report",
                        lambda *args, **kwargs: {"status": "OK", "rows": rows, "truncated": False})
    load = diag_running.sample_load("x.db")
    counts = {relation: relations.count(relation) for relation in ("mine", "other", "unknown")}
    assert load["active"] == sum(counts.values()) and load["active_by_relation"] == counts
    split = " (" + ", ".join(f"{relation} {count}" for relation, count in counts.items()) + ")"
    assert (split in L.format_load(load)) == (len(relations) > 1)


def test_plan_json_rows_name_the_profiles_that_use_each_pool(plan_store, capsys, monkeypatch):  # noqa: F811
    monkeypatch.chdir(plan_store.parent)
    assert main(["--db", str(plan_store), "diag", "--plan", "--json"]) == 0
    rows = {p["pool"]: p for p in json.loads(capsys.readouterr().out)["pools"]}
    assert all(isinstance(row["profiles"], list) for row in rows.values())
    assert any(name.startswith("cc.") for name in rows["C-7D"]["profiles"])
    assert rows["C-7D"]["profiles"] == sorted(rows["C-7D"]["profiles"])


def test_in_flight_asks_are_counted_per_pool_as_a_fact():
    load = L.load_snapshot({"cx": 2, "cc": 1}, RESOURCES, {"X-7D": 2, "C-7D": 1})
    assert load["by_pool"] == {"X-7D": 2, "C-7D": 1}
    assert load["unmapped"] == 0
    assert "unmapped" not in L.format_load(load)
    line = L.format_load(load)
    assert "in flight C-7D 1, X-7D 2" in line and line.index("in flight") < line.index("free RAM")
    assert "in flight" not in L.format_load(L.load_snapshot({"cx": 0}, RESOURCES, {}))
    assert L.load_snapshot(None, L.ResourceSample())["by_pool"] is None


def test_sample_load_maps_running_profiles_to_pools(monkeypatch):
    from peerhub.extensions import diag_running
    rows = [{"peer_id": "cx", "profile": "cx.effort"}, {"peer_id": "cx", "profile": "cx.effort"},
            {"peer_id": "cc", "profile": "cc.standard"}, {"peer_id": "ag", "profile": None},
            {"peer_id": "ag", "profile": "ag.retired"}, {"peer_id": "oa", "profile": "oa.standard"}]
    monkeypatch.setattr(diag_running, "running_report", lambda db, **kw: {"status": "OK", "rows": rows, "truncated": False})
    monkeypatch.setattr(L, "read_resources", lambda: RESOURCES)
    mapping = {"cx.effort": ["X-7D"], "cc.standard": ["C-7D", "F-7D"], "oa.standard": []}
    load = diag_running.sample_load("x.db", pools_of_profile=mapping)
    assert load["by_pool"] == {"X-7D": 2, "C-7D": 1, "F-7D": 1}
    assert load["unmapped"] == len(rows) - 3
    assert f"in flight C-7D 1, F-7D 1, X-7D 2 unmapped {load['unmapped']} | free RAM" in L.format_load(load)

    load = diag_running.sample_load("x.db", pools_of_profile={})
    assert load["by_pool"] == {} and load["unmapped"] == len(rows)
    assert f"in flight unmapped {len(rows)} | free RAM" in L.format_load(load)
