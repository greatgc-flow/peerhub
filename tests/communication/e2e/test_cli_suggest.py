"""Offline recommendation cases using the shipped profiles and quota projection."""
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

import pytest

from peerhub._console import format_shell_command
from peerhub.cli.app import main
from peerhub.cli import suggest as suggest_mod
from peerhub.cli.suggest import MAX_5H_USAGE
from peerhub.extensions.adapters.profiles import _load_layer, list_profiles
from peerhub.extensions.diag_quota import projection
from peerhub.extensions.observation_model import QUOTA_MAX_AGE_SECONDS

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


@pytest.mark.parametrize("json_output", [False, True])
def test_default_output_matches_before_candidates(quota, capsys, monkeypatch, json_output):
    items, _, _ = quota
    for peer in WINDOWS:
        items.extend(readings(peer))
    db = Path(__file__)
    monkeypatch.chdir(db.parent)
    argv = ["peerhub", "--db", str(db), "ask", "ag", "--profile", "ag.standard", "--fresh",
            "--query-file", "review.txt", "--writable"]
    command = format_shell_command(argv)
    reason = "5H usage 40.0%, 7D PACE -10.0 pp"
    expected = json.dumps({"status": "ok", "profile": "ag.standard", "command": command,
                           "argv": argv, "reason": reason}) + "\n" if json_output else f"{command}\n{reason}\n"
    options = ["--json"] if json_output else []
    assert main(["--db", db.name, "suggest", "--tier", "standard", "--task", "review", *options]) == 0
    assert capsys.readouterr() == (expected, "")


def test_candidates_show_tier_facts_without_changing_the_recommendation(quota, capsys):
    items, _, calls = quota
    items.extend(readings("ag", used_5h=MAX_5H_USAGE) + readings("cc", used_7d=.65) + readings("cx"))
    options = ("--tier", "standard", "--task", "code")
    assert suggest(*options, "--json") == 0
    default = json.loads(capsys.readouterr().out)
    assert suggest(*options, "--candidates", "--json") == 0
    result = json.loads(capsys.readouterr().out)
    candidates = {row["profile"]: row for row in result.pop("candidates")}
    assert result == default
    assert set(candidates) == {row["profile"] for row in list_profiles(Path(__file__).parent)
                               if row["profile"].endswith(".standard")}
    assert candidates["ag.standard"] == {
        "profile": "ag.standard", "peer": "ag", "eligible": False, "worst_pool": "G-5H",
        "left_pct": pytest.approx((1 - MAX_5H_USAGE) * 100), "pace_pt": pytest.approx(-10),
        "fast": False, "reason": f"5H usage >= {MAX_5H_USAGE:.0%}",
    }
    cc = candidates["cc.standard"]
    assert cc["eligible"] is True and cc["fast"] is True and cc["reason"] == "fast pool"
    assert cc["worst_pool"] == "C-7D" and cc["left_pct"] == pytest.approx(35)
    assert cc["pace_pt"] == pytest.approx(15)
    assert candidates["oa.standard"]["reason"] == suggest_mod.REASON_PROFILE_NOT_ENABLED
    assert "profile setting" in suggest_mod.REASON_PROFILE_NOT_ENABLED
    assert not calls

    assert suggest(*options) == 0
    default_text, _ = capsys.readouterr()
    assert suggest(*options, "--candidates") == 0
    out, err = capsys.readouterr()
    assert out.startswith(default_text) and not err
    table = out.removeprefix(default_text)
    header, *rows = table.splitlines()
    assert header.split() == ["PROFILE", "PEER", "ELIGIBLE", "WORST", "POOL", "(LEFT", "%)", "PACE", "(7D", "pp)", "FAST", "REASON"]
    assert len(rows) == len(candidates)
    cc_text = next(row.split() for row in rows if row.startswith("cc.standard"))
    assert cc_text == ["cc.standard", "cc", "yes", "C-7D", "35.0%", "+15.0", "yes", "fast", "pool"]


@pytest.mark.parametrize("condition", ["stale", "reset", "unknown"])
def test_candidates_explain_eligible_quota_fallback(quota, capsys, condition):
    items, _, _ = quota
    if condition == "stale":
        items.extend(readings("cx", age=QUOTA_MAX_AGE_SECONDS + 1))
    elif condition == "reset":
        items.extend(readings("cx"))
        items[-1]["projection"]["state"] = "expired"
    else:
        items.extend(readings("cx", used_7d=None))
    assert suggest("--tier", "effort", "--candidates", "--json") == 0
    value = json.loads(capsys.readouterr().out)
    rows = {row["profile"]: row for row in value["candidates"]}
    cx = rows["cx.effort"]
    assert value["profile"] == cx["profile"]
    assert cx["eligible"] is True and cx["reason"] == condition and cx["pace_pt"] is None
    assert rows["ag.effort"]["reason"] == suggest_mod.REASON_PROFILE_NOT_ENABLED


def test_exhausted_candidates_are_visible_when_suggest_is_unavailable(quota, capsys):
    items, _, _ = quota
    items.extend(readings("ag", used_7d=1.0, deep=True))
    options = ("--tier", "deepthink", "--json")
    assert suggest(*options) == 1
    default_out, default_err = capsys.readouterr()
    assert suggest(*options, "--candidates") == 1
    out, err = capsys.readouterr()
    value = json.loads(out)
    rows = {row["profile"]: row for row in value.pop("candidates")}
    assert value == json.loads(default_out) and err == default_err
    ag = rows["ag.deepthink"]
    assert ag["eligible"] is False and ag["reason"] == "exhausted 7D pool"
    assert ag["worst_pool"] == "3P-7D" and ag["left_pct"] == 0


@pytest.mark.parametrize("age,refreshed", [(QUOTA_MAX_AGE_SECONDS, False), (QUOTA_MAX_AGE_SECONDS + 1, True), (None, True)])
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
        items.extend(readings(peer, used_7d=used_7d, used_5h=used_5h, age=QUOTA_MAX_AGE_SECONDS))
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
    items.extend(readings("cc", used_7d=.01, age=QUOTA_MAX_AGE_SECONDS + 1) + readings("cx", used_7d=.40))
    refreshed["cc"] = readings("cc", used_7d=.02, age=QUOTA_MAX_AGE_SECONDS + 1)
    refreshed["ag"] = readings("ag", used_7d=None)
    assert suggest("--tier", "standard") == 0
    out = capsys.readouterr()
    command, reason = out.out.splitlines()
    assert " ask cx " in command and "--profile cx.standard" in command
    assert "7D PACE" in reason and not out.err
    assert len(calls) == 1 and set(calls[0]) == {"ag", "cc"}


def test_refreshed_reading_can_win(quota, capsys):
    items, refreshed, calls = quota
    items.extend(readings("cc") + readings("cx") + readings("ag", age=QUOTA_MAX_AGE_SECONDS + 1))
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
    assert value["profile"] == "ag.standard" and value["reason"] == "quota unknown (5H unknown)"
    assert not calls


def test_quota_unknown_when_no_peer_has_measured_weekly_quota(quota, capsys):
    items, _, calls = quota
    items.extend(readings("cx", used_7d=None) + readings("cc", used_7d=None))
    assert suggest("--tier", "effort", "--json") == 0
    out = capsys.readouterr()
    value = json.loads(out.out)
    assert value["profile"] == "cx.effort" and value["reason"] == "quota unknown"
    assert not out.err and not calls


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
        elif option == "--candidates":
            default = e.j(db, "suggest", "--tier", "effort")
            expanded = e.j(db, "suggest", "--tier", "effort", option)
            rows = {row["profile"]: row for row in expanded.pop("candidates")}
            assert expanded == default
            assert rows["cx.effort"]["eligible"] is True
            assert rows["ag.effort"]["eligible"] is False
        else:
            assert e.j(db, "suggest", "--tier", "effort")["profile"] == "cx.effort"


def test_unexposed_5h_is_no_gate(quota, capsys):
    items, _, _ = quota
    absent, = readings("cx", used_7d=None, state="ABSENT")
    absent["measurements"] = {}
    items.extend([absent, *readings("cx", used_5h=None, used_7d=.10), *readings("cc", used_7d=.50)])
    assert suggest("--tier", "effort", "--json") == 0
    value = json.loads(capsys.readouterr().out)
    assert value["profile"] == "cx.effort" and "5H n/a" in value["reason"]


@pytest.mark.parametrize("short_state,age", [
    (None, 0), ("UNKNOWN", 0), ("MEASURED", QUOTA_MAX_AGE_SECONDS + 1), ("ABSENT", QUOTA_MAX_AGE_SECONDS + 1),
])
def test_unknown_5h_ranks_after_measured_and_can_be_a_fallback(quota, capsys, short_state, age):
    items, _, _ = quota
    if short_state is not None:
        items.extend(readings("cx", used_7d=None, state=short_state, age=age))
    items.extend(readings("cx", used_5h=None, used_7d=.10))
    weekly_only = items.copy()
    items.extend(readings("cc", used_7d=.50))
    assert suggest("--tier", "effort", "--json") == 0
    assert json.loads(capsys.readouterr().out)["profile"] == "cc.effort"

    items[:] = weekly_only + readings("cc", used_7d=1.0)
    assert suggest("--tier", "effort", "--json") == 0
    value = json.loads(capsys.readouterr().out)
    assert value["profile"] == "cx.effort" and value["reason"] == "quota unknown (5H unknown)"


def test_exhausted_weekly_pool_is_not_suggested(quota, capsys):
    items, _, _ = quota
    items.extend(readings("ag", used_7d=1.0, deep=True))
    assert suggest("--tier", "deepthink", "--json") != 0
    assert "exhausted" in capsys.readouterr().err
