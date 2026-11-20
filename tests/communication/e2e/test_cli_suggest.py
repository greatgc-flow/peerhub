"""Offline recommendation cases using the shipped profiles and quota projection."""
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.extensions.adapters.profiles import _load_layer
from peerhub.extensions.diag_quota import projection

NOW = 1_800_000_000
WINDOWS = {"ag": ("G", "3P"), "cx": ("X", "X"), "cc": ("C", "C")}
pytestmark = pytest.mark.integration


def readings(peer, *, used_5h=.40, used_7d=.40, age=0, deep=False, state="MEASURED"):
    family = WINDOWS[peer][int(deep)]
    measured_at = NOW - age
    result = []
    for window, duration, used in [("5H", 5 * 3600, used_5h), ("7D", 7 * 86400, used_7d)]:
        if used is None:
            continue
        payload = {"remaining_fraction": 1 - used, "window_started_at": measured_at - duration // 2,
                   "resets_at": measured_at + duration // 2}
        item = {"subject_ref": peer, "state": state, "age_seconds": age, "kind": "quota",
                "resource_pool_ref": f"quota:{peer}:{family}-{window}", "measurements": payload,
                "window": {"resets_at": payload["resets_at"]},
                "projection": projection({"payload": payload, "state": state,
                                          "observed_at": datetime.fromtimestamp(measured_at, timezone.utc).isoformat()}, NOW)}
        result.append(item)
    return result


def quota_world(monkeypatch):
    packaged = _load_layer(files("peerhub.config_data").joinpath("model-defaults.toml"))
    monkeypatch.setattr("peerhub.extensions.adapters.profiles._load_layers",
                        lambda workspace: [("packaged", packaged)])
    items = []
    refreshed = {}
    calls = []

    def report(*args, **kwargs):
        return {"status": "OK", "pools": [{"items": items.copy()}]}

    def refresh(db, peers, **kwargs):
        calls.append(list(peers))
        for peer in peers:
            if peer in refreshed:
                items[:] = [item for item in items if item["subject_ref"] != peer] + refreshed[peer]
        return {"status": "OK"}

    monkeypatch.setattr("peerhub.extensions.diag_quota.quota_report", report)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota", refresh)
    monkeypatch.setattr("peerhub.cli.support.quota_refresh_lock", lambda db: nullcontext(True))
    return items, refreshed, calls


@pytest.fixture
def quota(monkeypatch):
    return quota_world(monkeypatch)


def suggest(*options):
    return main(["--db", str(Path(__file__)), "suggest", *options])


@pytest.mark.parametrize("age,refreshed", [(900, False), (901, True), (None, True)])
def test_dispatch_only_refreshes_an_expired_or_missing_snapshot(quota, age, refreshed):
    from peerhub.cli.support import refresh_dispatch_quota
    items, _, calls = quota
    if age is not None:
        items.extend(readings("cx", age=age))
    items.extend(readings("ag"))
    refresh_dispatch_quota(Path(__file__), "cx")
    assert calls == ([["cx"]] if refreshed else [])


@pytest.mark.parametrize("weekly,short,expected", [
    ((.40, .40, .40), (.40, .40, .40), "ag.standard"),
    ((.60, .40, .40), (.40, .40, .40), "cx.standard"),
    ((.60, .40, .20), (.40, .40, .40), "cc.standard"),
    ((.20, .30, .40), (.90, .899, .40), "cx.standard"),
])
def test_rank_by_weekly_pace_and_gate_at_90_percent(quota, capsys, weekly, short, expected):
    items, _, calls = quota
    for peer, used_7d, used_5h in zip(WINDOWS, weekly, short):
        items.extend(readings(peer, used_7d=used_7d, used_5h=used_5h, age=900))
    assert suggest("--tier", "standard", "--task", "review", "--json") == 0
    out = capsys.readouterr()
    value = json.loads(out.out)
    assert value["profile"] == expected
    assert value["argv"][value["argv"].index("--profile") + 1] == expected
    assert "--fresh" in value["argv"]
    assert ("--writable" in value["argv"]) == expected.startswith("ag.")
    assert "7D PACE" in value["reason"]
    assert not out.err and not calls


def test_refresh_missing_and_expired_once_then_rank_unknown_last(quota, capsys):
    items, refreshed, calls = quota
    items.extend(readings("cc", used_7d=.01, age=901) + readings("cx", used_7d=.40))
    refreshed["cc"] = readings("cc", used_7d=.02, age=901)
    refreshed["ag"] = readings("ag", used_7d=None)
    assert suggest("--tier", "standard") == 0
    out = capsys.readouterr()
    command, reason = out.out.splitlines()
    assert " ask cx " in command and "--profile cx.standard" in command
    assert "7D PACE" in reason and not out.err
    assert len(calls) == 1 and set(calls[0]) == {"ag", "cc"}


def test_refreshed_reading_can_win(quota, capsys):
    items, refreshed, calls = quota
    items.extend(readings("cc") + readings("cx") + readings("ag", age=901))
    refreshed["ag"] = readings("ag", used_7d=.01)
    assert suggest("--tier", "standard", "--json") == 0
    assert json.loads(capsys.readouterr().out)["profile"] == "ag.standard"
    assert calls == [["ag"]]


def test_unknown_is_eligible_after_the_5h_gate(quota, capsys):
    items, _, calls = quota
    items.extend(readings("cx", used_5h=.95) + readings("cc", used_5h=.95))
    items.extend(readings("ag", state="ERROR"))
    assert suggest("--tier", "standard", "--json") == 0
    value = json.loads(capsys.readouterr().out)
    assert value["profile"] == "ag.standard" and value["reason"] == "quota unknown"
    assert not calls


@pytest.mark.parametrize("json_option", [[], ["--json"]])
def test_no_peer_has_both_usable_windows(quota, capsys, json_option):
    items, _, calls = quota
    items.extend(readings("cx", used_7d=None) + readings("cc", used_7d=None))  # 7D is required; an unexposed 5H is no gate
    assert suggest("--tier", "standard", *json_option) != 0
    out = capsys.readouterr()
    assert "no peer" in out.err and len(out.err.splitlines()) == 1
    if json_option:
        assert json.loads(out.out)["status"] == "unavailable"
    else:
        assert out.out == ""
    assert len(calls) == 1


@pytest.mark.parametrize("tier,expected", [("effort", "cx.effort"), ("deepthink", "ag.deepthink")])
def test_profiles_opted_out_of_suggestions(quota, capsys, tier, expected):
    items, _, calls = quota
    items.extend(readings("cc", used_7d=.01 if tier == "deepthink" else .55))
    items.extend(readings("cx", used_7d=.02 if tier == "deepthink" else .50))
    items.extend(readings("ag", used_7d=.01) + readings("ag", used_7d=.60, deep=True))
    assert suggest("--tier", tier, "--task", "code", "--json") == 0
    value = json.loads(capsys.readouterr().out)
    assert value["profile"] == expected and "--writable" in value["argv"]
    assert not calls


def suggest_effect(e, option):
    """Inventory effects use the same offline fixtures as the recommendation tests."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        items, _, _ = quota_world(monkeypatch)
        items.extend(readings("cc", used_7d=.55) + readings("cx") + readings("ag", used_7d=.01, deep=True))
        db = e.fresh()
        if option == "--tier":
            assert e.j(db, "suggest", "--tier", "deepthink")["profile"] == "ag.deepthink"
            assert e.j(db, "suggest", "--tier", "effort")["profile"] == "cx.effort"
        elif option == "--task":
            assert "--writable" in e.j(db, "suggest", "--tier", "effort", "--task", "code")["argv"]
            assert "--writable" not in e.j(db, "suggest", "--tier", "effort", "--task", "read")["argv"]
        else:
            assert e.j(db, "suggest", "--tier", "effort")["profile"] == "cx.effort"


def test_unexposed_5h_is_no_gate(quota, capsys):
    items, _, _ = quota
    items.extend(readings("cx", used_5h=None, used_7d=.10) + readings("cc", used_5h=None, used_7d=.50))
    assert suggest("--tier", "effort", "--json") == 0
    value = json.loads(capsys.readouterr().out)
    assert value["profile"] == "cx.effort" and "5H n/a" in value["reason"]


def test_exhausted_weekly_pool_is_not_suggested(quota, capsys):
    items, _, _ = quota
    items.extend(readings("ag", used_7d=1.0, deep=True))
    assert suggest("--tier", "deepthink", "--json") != 0
    assert "exhausted" in capsys.readouterr().err
