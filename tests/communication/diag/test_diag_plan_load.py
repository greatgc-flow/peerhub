"""`peerhub diag --plan` load line: what an AI needs beside the pools (active asks, per-peer slots, free RAM).

Vote pqv (3 peers, unanimous): additive, read-only, constants in one core module; slots_free is against the default limit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.core import load_limits as L
from tests.communication.diag.test_diag_plan_339 import plan_store  # noqa: F401  (fixture)

pytestmark = [pytest.mark.unit]

GB = 1024 ** 3
REPO = Path(__file__).resolve().parents[3]


def test_constants_are_the_documented_limits():
    assert (L.ACTIVE_ASKS_DEFAULT, L.ACTIVE_ASKS_CEILING, L.MIN_FREE_RAM_GB) == (4, 6, 6)
    assert L.PER_PEER_LIMITS["cc"] == 3 and L.PER_PEER_LIMITS["cx"] == 3 and L.PER_PEER_LIMITS["ag"] == 1


def test_snapshot_counts_slots_against_the_default_and_never_goes_negative():
    load = L.load_snapshot({"cx": 1}, 12 * GB)
    assert load["active"] == 1 and load["limit_default"] == 4 and load["limit_ceiling"] == 6
    assert load["slots_free"] == 3
    assert load["peers"]["cx"] == {"active": 1, "limit": 3, "free": 2}
    assert load["peers"]["cc"] == {"active": 0, "limit": 3, "free": 3}
    assert load["ram_free_gb"] == 12.0 and load["ram_min_gb"] == 6 and load["ram_ok"] is True
    over = L.load_snapshot({"ag": 2, "cc": 3, "cx": 3}, 12 * GB)
    assert over["slots_free"] == 0 and over["peers"]["ag"]["free"] == 0


def test_snapshot_reconciles_peers_outside_the_three_cli_peers():
    load = L.load_snapshot({"ca": 1, "zz": 2}, None)
    assert load["active"] == 3
    assert load["peers"]["ca"] == {"active": 1, "limit": 3, "free": 2}
    assert load["peers"]["zz"] == {"active": 2, "limit": None, "free": None}


def test_ram_unknown_and_low():
    unknown = L.load_snapshot({}, None)
    assert unknown["ram_free_gb"] is None and unknown["ram_ok"] is None
    low = L.load_snapshot({}, 5 * GB)
    assert low["ram_free_gb"] == 5.0 and low["ram_ok"] is False


def test_format_line_is_one_short_line():
    line = L.format_load(L.load_snapshot({"cx": 1}, 12 * GB))
    assert line == "load: active 1/4 (ceil 6) | cc 0/3 cx 1/3 ag 0/1 | free RAM 12.0 GB (min 6) | sampled now"
    other = L.format_load(L.load_snapshot({"ca": 1, "zz": 2}, None))
    assert "ca 1/3" in other and "zz 2" in other and "free RAM ? GB (min 6)" in other
    assert "\n" not in line


def test_plan_json_and_text_carry_load(plan_store, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr("peerhub.extensions.diag_running.free_ram_bytes", lambda: 8 * GB)
    assert main(["--db", str(plan_store), "diag", "--plan", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["load"]["ram_free_gb"] == 8.0 and data["load"]["active"] == 0 and data["pools"]
    assert main(["--db", str(plan_store), "diag", "--plan"]) == 0
    out = capsys.readouterr().out
    assert "load: active 0/4 (ceil 6)" in out and "free RAM 8.0 GB (min 6)" in out

