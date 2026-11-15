"""Alerts of the monitor read model (#374 section 3): a pure function, a fixed order, one window constant, and their sources."""
from __future__ import annotations

import json
from typing import Any

import pytest

from peerhub.extensions import monitor_read_model as mrm
from tests.communication.harness.core import CoreHarness

pytestmark = [pytest.mark.unit, pytest.mark.diag]

NOW = 1_790_812_800.0


def ask(state: str, name: str = "s", **kw: Any) -> dict[str, Any]:
    row = {"state": state, "superseded": False, "alive": None, "alive_reason": None, "peer": "cx", "profile": "cx.standard",
           "name": name, "elapsed_s": 41.0, "ended_at": None, "ended_exact": None, "age_s": None, "lease_left_s": None, "note": None}
    row.update(kw)
    return row


def alerts(**kw: Any) -> list[dict[str, Any]]:
    args: dict[str, Any] = {"asks": [], "pools": [], "accounts": [], "facts_report": None, "failures": [], "now": NOW}
    args.update(kw)
    return mrm.build_alerts(**args)


def test_window_constant():
    assert mrm.ALERT_WINDOW_S == 1800


def test_alert_shape():
    (alert,) = alerts(asks=[ask("run", "gone-one", alive=False)])
    assert alert == {"code": "a2", "kind": "gone", "subject": "cx.standard gone-one", "age_s": 41.0, "severity": "high"}
    assert list(alert) == ["code", "kind", "subject", "age_s", "severity"]


def test_every_rule_in_order():
    rows = [
        ask("empty", "bad-empty", ended_at=NOW - 60.0, age_s=60.0),
        ask("overdue", "late", lease_left_s=-120.0),
        ask("run", "fine", alive=True),
        ask("run", "unknown", alive=None),
        ask("run", "gone", alive=False),
        ask("ok", "good", ended_at=NOW - 10.0, age_s=10.0),
    ]
    pools = [{"pool": "ag 3P-7D", "left_pct": 0, "state": "out", "window": "7D"},
             {"pool": "cc C-7D", "left_pct": 5, "state": "low", "window": "7D"}]
    accounts = [{"peer_id": "cx", "logged_in": False, "login_token_expires_at": None},
                {"peer_id": "cc", "logged_in": True, "login_token_expires_at": NOW - 5.0},
                {"peer_id": "ag", "logged_in": True, "login_token_expires_at": NOW + 3600.0},
                {"peer_id": "cc-work", "logged_in": True, "login_token_expires_at": NOW + 49 * 3600.0}]
    got = alerts(asks=rows, pools=pools, accounts=accounts, failures=[("capacity", "OperationalError")],
                 facts_report={"status": "needs_recheck", "reasons": ["ag: version_drift"]})
    assert [(a["code"], a["kind"], a["subject"], a["severity"]) for a in got] == [
        ("a6", "store", "capacity: OperationalError", "high"),
        ("a2", "gone", "cx.standard gone", "high"),
        ("a2", "overdue", "cx.standard late", "high"),
        ("a1", "empty", "cx.standard bad-empty", "high"),
        ("a5", "login", "cx", "high"),
        ("a5", "token_expired", "cc", "high"),
        ("a3", "pool_out", "ag 3P-7D", "high"),
        ("a5", "token_expiring", "ag", "low"),
        ("a6", "facts", "ag: version_drift", "low"),
    ]
    assert got[2]["age_s"] == 120.0 and got[3]["age_s"] == 60.0 and got[5]["age_s"] == 5.0


@pytest.mark.parametrize("state", ["empty", "error", "unsure"])
def test_a1_window_boundary(state):
    inside = ask(state, "in", ended_at=NOW - mrm.ALERT_WINDOW_S, age_s=float(mrm.ALERT_WINDOW_S))
    outside = ask(state, "out", ended_at=NOW - mrm.ALERT_WINDOW_S - 1, age_s=float(mrm.ALERT_WINDOW_S + 1))
    assert [a["subject"] for a in alerts(asks=[inside, outside])] == ["cx.standard in"]


def test_a1_with_an_inexact_end_uses_the_bound_and_has_no_age():
    got = alerts(asks=[ask("error", "bound", ended_at=NOW - 100.0, ended_exact=False, age_s=None)])
    assert [(a["code"], a["age_s"]) for a in got] == [("a1", None)]


def test_overdue_window_boundary():
    inside = ask("overdue", "in", lease_left_s=-float(mrm.ALERT_WINDOW_S))
    outside = ask("overdue", "out", lease_left_s=-float(mrm.ALERT_WINDOW_S) - 1)
    assert [a["subject"] for a in alerts(asks=[inside, outside])] == ["cx.standard in"]


def test_superseded_and_unended_rows_never_alert():
    rows = [ask("unsure", "sup", superseded=True, ended_at=NOW - 10.0, age_s=10.0, note="superseded"),
            ask("unsure", "restored", ended_at=None, note="other generation"),
            ask("run", "unknown", alive=None)]
    assert alerts(asks=rows) == []


def test_low_and_fast_pools_are_not_alerts():
    pools = [{"pool": "ag 3P-7D", "left_pct": 1, "state": "low", "window": "7D"},
             {"pool": "cx X-7D", "left_pct": 40, "state": "fast", "window": "7D"},
             {"pool": "cc C-5H", "left_pct": 0, "state": "out", "window": "5H"}]
    assert [(a["code"], a["subject"]) for a in alerts(pools=pools)] == [("a3", "cc C-5H")]


def test_a1_rows_keep_the_given_order():
    rows = [ask("error", "newest", ended_at=NOW - 10.0, age_s=10.0), ask("unsure", "older", ended_at=NOW - 20.0, age_s=20.0)]
    assert [a["subject"] for a in alerts(asks=rows)] == ["cx.standard newest", "cx.standard older"]


def test_not_read_sources_give_no_alerts():
    assert alerts(asks=None, pools=None, accounts=None) == []


def test_pool_states_and_min_quota():
    def obs(peer, pool, remaining, state="MEASURED", projection=None):
        return {"peer_id": peer, "kind": "quota", "pool_ref": f"{peer}:{pool}", "state": state,
                "measurements": {"remaining_fraction": remaining} if remaining is not None else None,
                "projection": projection or {}, "reset_in_seconds": 3600.0}
    observations = [obs("ag", "3P-7D", 0.01), obs("ag", "A-7D", 0.79), obs("cc", "C-5H", 0.0),
                    obs("cx", "X-7D", 0.42, projection={"state": "threat"}), obs("cc", "F-7D", None, state="UNKNOWN"),
                    obs("cx", "X-5H", 0.42, projection={"state": "threat"})]
    observations[3]["projection_samples"] = 2  # the view's rule: `fast` needs two samples (monitor_view `_compact_pool`)
    pools = mrm.pool_states(observations)
    by_name = {p["pool"]: p for p in pools}
    assert by_name["ag 3P-7D"] == {"pool": "ag 3P-7D", "left_pct": 1, "state": "low", "shown": "low", "window": "7D",
                                   "evidence": "MEASURED", "fresh": True, "na": False}
    assert by_name["cc C-5H"]["state"] == "out" and by_name["cx X-7D"]["state"] == by_name["cx X-7D"]["shown"] == "fast"
    assert by_name["cx X-5H"]["state"] == "ok"  # a threat projection from fewer than two samples is not `fast`
    assert by_name["cc F-7D"]["state"] == "?" and by_name["cc F-7D"]["left_pct"] is None
    assert mrm.min_quota(pools) == {"pool": "ag 3P-7D", "left_pct": 1, "state": "low", "aged": False}
    assert mrm.min_quota([]) is None


def test_two_sample_rule_reads_the_counts_of_the_human_path():
    item = {"observation_id": "x", "peer_id": "cx", "kind": "quota", "pool_ref": "cx:X-7D", "state": "MEASURED",
            "measurements": {"remaining_fraction": 0.42}, "projection": {"state": "threat"}, "reset_in_seconds": 100.0}
    assert mrm.pool_states([item])[0]["state"] == "ok"
    assert mrm.pool_states([item], samples={"x": 2})[0]["state"] == "fast"


def stale(peer, pool, oid):
    return {"observation_id": oid, "peer_id": peer, "kind": "quota", "pool_ref": f"{peer}:{pool}", "state": "STALE",
            "measurements": None, "projection": {"state": "threat"}, "reset_in_seconds": None}


def test_stale_pools_are_aged_with_their_last_known_value_and_never_out_low_or_fast():
    observations = [stale("ag", "3P-7D", "o1"), stale("cc", "C-7D", "o2"), stale("cx", "X-7D", "o3"), stale("cx", "X-5H", "o4")]
    last_known = {"o1": (0.0, None), "o2": (0.05, NOW + 100.0), "o3": (0.5, NOW - 1.0)}  # o4: no valid last value
    pools = {p["pool"]: p for p in mrm.pool_states(observations, last_known, NOW)}
    assert (pools["ag 3P-7D"]["state"], pools["ag 3P-7D"]["shown"], pools["ag 3P-7D"]["left_pct"]) == ("aged", "out", 0)
    assert (pools["cc C-7D"]["state"], pools["cc C-7D"]["shown"], pools["cc C-7D"]["left_pct"]) == ("aged", "low", 5)
    assert (pools["cx X-7D"]["state"], pools["cx X-7D"]["shown"], pools["cx X-7D"]["left_pct"]) == ("reset", "reset", None)
    assert (pools["cx X-5H"]["state"], pools["cx X-5H"]["left_pct"]) == ("?", None)
    assert alerts(pools=list(pools.values())) == []  # a3 is fresh-only: `state`, never `shown`
    assert mrm.min_quota(list(pools.values())) == {"pool": "ag 3P-7D", "left_pct": 0, "state": "out", "aged": True}


@pytest.mark.parametrize("fraction,projection,samples,shown", [
    (0.01, {}, 0, "low"), (0.60, {}, 0, "aged"), (0.0, {}, 0, "out"), (0.60, {"state": "threat"}, 2, "fast"),
    (0.60, {"state": "threat"}, 1, "aged")])
def test_min_quota_of_a_stale_pool_shows_the_view_precedence(fraction, projection, samples, shown):
    item = {**stale("ag", "3P-7D", "o1"), "projection": projection, "projection_samples": samples}
    pools = mrm.pool_states([item], {"o1": (fraction, None)}, NOW)
    assert mrm.min_quota(pools) == {"pool": "ag 3P-7D", "left_pct": round(fraction * 100), "state": shown, "aged": True}
    assert pools[0]["state"] == "aged" and alerts(pools=pools) == []


def test_capacity_source_is_stale_only_without_a_fresh_seven_day_pool():
    fresh = {"observation_id": "f", "peer_id": "cc", "kind": "quota", "pool_ref": "cc:F-7D", "state": "MEASURED",
             "measurements": {"remaining_fraction": 0.4}, "projection": {}, "reset_in_seconds": 100.0}
    all_stale = mrm.pool_states([stale("ag", "3P-7D", "o1")], {"o1": (0.2, None)}, NOW)
    assert mrm.capacity_source(all_stale) == "stale"
    assert mrm.capacity_source(mrm.pool_states([stale("ag", "3P-7D", "o1"), fresh], {"o1": (0.2, None)}, NOW)) == "read"
    assert mrm.capacity_source(mrm.pool_states([stale("ag", "3P-5H", "o1")], {}, NOW)) == "read"  # no 7-day row is stale
    assert mrm.capacity_source([]) == "read"  # providers that never report quota


def unread(peer, pool, state="UNKNOWN", **kw):
    return {"observation_id": f"{peer}-{pool}", "peer_id": peer, "kind": "quota", "pool_ref": f"{peer}:{pool}", "state": state,
            "measurements": None, "projection": {}, "reset_in_seconds": None, **kw}


def test_capacity_source_is_unknown_when_no_seven_day_pool_has_a_reading():
    """#376 P14: every 7-day pool is unknown or in error and none is STALE; a window the provider says it has not is no pool."""
    def source(*observations):
        return mrm.capacity_source(mrm.pool_states(list(observations), {"o1": (0.2, None)}, NOW))

    fresh = {"observation_id": "f", "peer_id": "cc", "kind": "quota", "pool_ref": "cc:F-7D", "state": "MEASURED",
             "measurements": {"remaining_fraction": 0.4}, "projection": {}, "reset_in_seconds": 100.0}
    not_reported = unread("cc", "C-7D", "ABSENT", reason="not reported by provider; plan=pro")
    assert source(unread("cc", "C-7D"), unread("cx", "X-7D", "ERROR"), unread("ag", "A-7D", "UNAVAILABLE")) == "unknown"
    assert source(unread("cc", "C-7D", "ABSENT", reason="no usage block")) == "unknown"  # absent, but not a provider fact
    assert source(unread("cc", "C-7D"), fresh) == "read"
    assert source(unread("cc", "C-7D"), stale("ag", "3P-7D", "o1")) == "stale"
    assert source(unread("cc", "C-5H")) == "read"  # no 7-day row at all
    assert source(not_reported) == "read"
    assert source(unread("cc", "C-7D", "STALE", stored_state="ABSENT", reason="disabled by provider: weekly limit")) == "read"
    assert source(not_reported, unread("cc", "F-7D", "ERROR")) == "unknown"
    assert [p["na"] for p in mrm.pool_states([not_reported, unread("cc", "F-7D")])] == [True, False]


def test_a_frame_whose_only_seven_day_pool_is_unknown_reports_unknown_capacity(tmp_path):
    from tests.communication.fakes.observation import FakeObservationSource, unavailable
    from tests.communication.harness.observation import T0, ObservationHarness
    h = ObservationHarness(tmp_path / "unknown")
    h.register_pool({"schema_version": "1.0", "resource_pool_id": "cx:X-7D", "provider": "openai", "kind": "QUOTA", "metadata": {}})
    h.capture("cx", "quota", FakeObservationSource(unavailable("probe_failed")), resource_pool_ref="cx:X-7D")
    out = mrm.render_monitor_frame(h.db_path, ["capacity"], read_at=T0 + 10, preference_source="command")
    assert out["alert_sources"] == {"activity": "not_selected", "capacity": "unknown", "accounts": "read", "facts": "read"}
    assert out["min_quota"] is None and out["alerts"] == [] and out["errors"] == {}
    (pool,) = out["groups"]["capacity"]["data"]["observations"]  # the pool's own error stays visible
    assert (pool["state"], pool["reason"], pool["measurements"]) == ("UNAVAILABLE", "probe_failed", None)


def _quota_store(tmp_path, fraction):
    from tests.communication.fakes.observation import FakeObservationSource, measured
    from tests.communication.harness.observation import ObservationHarness
    h = ObservationHarness(tmp_path / "quota")
    h.register_pool({"schema_version": "1.0", "resource_pool_id": "cx:X-7D", "provider": "openai", "kind": "QUOTA", "metadata": {}})
    h.capture("cx", "quota", FakeObservationSource(measured({"remaining_fraction": fraction})), resource_pool_ref="cx:X-7D")
    return h


def test_all_stale_frame_reports_stale_capacity_and_an_aged_min_quota_without_a3(tmp_path):
    from tests.communication.harness.observation import T0
    h = _quota_store(tmp_path, 0.0)
    out = mrm.render_monitor_frame(h.db_path, ["capacity"], read_at=T0 + 300, preference_source="command")
    assert out["groups"]["capacity"]["data"]["observations"][0]["state"] == "STALE"
    assert out["alert_sources"]["capacity"] == "stale"
    assert out["min_quota"] == {"pool": "cx X-7D", "left_pct": 0, "state": "out", "aged": True}  # A6: the shown state
    assert out["alerts"] == []  # still no a3 for a stale 0%
    assert out["groups"]["capacity"]["data"]["observations"][0]["measurements"] is None  # the public value stays hidden


def test_stale_frame_whose_reset_has_passed_has_no_min_quota(tmp_path):
    from tests.communication.fakes.observation import FakeObservationSource, measured
    from tests.communication.harness.observation import T0, ObservationHarness
    h = ObservationHarness(tmp_path / "reset")
    h.register_pool({"schema_version": "1.0", "resource_pool_id": "cx:X-7D", "provider": "openai", "kind": "QUOTA", "metadata": {}})
    h.capture("cx", "quota", FakeObservationSource(measured({"remaining_fraction": 0.3, "resets_at": T0 + 100})),
              resource_pool_ref="cx:X-7D")
    out = mrm.render_monitor_frame(h.db_path, ["capacity"], read_at=T0 + 300, preference_source="command")
    assert out["groups"]["capacity"]["data"]["observations"][0]["state"] == "STALE"
    assert out["alert_sources"]["capacity"] == "stale" and out["min_quota"] is None and out["alerts"] == []


@pytest.mark.parametrize("fraction", [0.145, 0.855, 0.285, 0.575, 0.355])
def test_stale_value_is_the_checked_fraction_not_one_minus_used(tmp_path, fraction):
    from tests.communication.harness.observation import T0
    out = mrm.render_monitor_frame(_quota_store(tmp_path, fraction).db_path, ["capacity"], read_at=T0 + 300,
                                   preference_source="command")
    fresh = mrm.render_monitor_frame(_quota_store(tmp_path / "f", fraction).db_path, ["capacity"], read_at=T0 + 10,
                                     preference_source="command")
    assert out["min_quota"]["left_pct"] == fresh["min_quota"]["left_pct"] == round(fraction * 100)


@pytest.mark.parametrize("captures,shown", [(2, "fast"), (1, "ok")])
@pytest.mark.parametrize("human", [False, True])
def test_fast_needs_two_samples_on_every_path_without_another_statement(tmp_path, monkeypatch, captures, shown, human):
    """`fast` (two MEASURED samples of one window and a threat projection) is the same on the JSON, expand and compact paths."""
    from tests.communication.fakes.observation import FakeObservationSource, measured
    from tests.communication.harness.observation import T0, ObservationHarness
    monkeypatch.setattr(mrm, "project_exhaustion", lambda *a, **k: {"state": "threat", "pace_pt": 30.0})
    h = ObservationHarness(tmp_path / "fast")
    h.register_pool({"schema_version": "1.0", "resource_pool_id": "cx:X-7D", "provider": "openai", "kind": "QUOTA", "metadata": {}})
    for n in range(captures):
        if n:
            h.clock.advance(5)
        h.capture("cx", "quota", FakeObservationSource(measured({"remaining_fraction": 0.42, "resets_at": T0 + 86_400})),
                  resource_pool_ref="cx:X-7D")
    statements: list[str] = []
    out = mrm.render_monitor_frame(h.db_path, ["capacity"], read_at=T0 + 20, preference_source="command", human=human,
                                   trace_callback=statements.append)
    assert out["min_quota"] == {"pool": "cx X-7D", "left_pct": 42, "state": shown, "aged": False}
    assert out["query_families"] == (2 if human else 1)  # the count rides in the capacity statement; human adds its export only


def test_a_malformed_older_sample_does_not_abort_the_human_export(tmp_path):
    """#376 P8: the export's sample count guards its JSON functions, exactly as the capacity statement does."""
    import sqlite3
    from contextlib import closing
    from tests.communication.harness.observation import T0
    h = _quota_store(tmp_path, 0.42)
    with closing(sqlite3.connect(h.db_path)) as c:  # an older MEASURED row of the same pool whose payload is not JSON
        c.execute("INSERT INTO observations (observation_id, subject_ref, resource_pool_ref, kind, source, state, payload_json, "
                  "observed_at, captured_at, effective_at_us) VALUES ('bad-json','cx','cx:X-7D','quota','s','MEASURED','{not json',"
                  "'2026-10-01T00:00:00Z', NULL, 0)")
        c.commit()
    plain = mrm.render_monitor_frame(h.db_path, ["capacity"], read_at=T0 + 10, preference_source="command")
    out = mrm.render_monitor_frame(h.db_path, ["capacity"], read_at=T0 + 10, preference_source="command", human=True)
    (item,) = out["_human_observations"]
    assert item["last_measurements"] == {"remaining_fraction": 0.42} and item["projection_samples"] == 2
    assert out["errors"] == {} and out["min_quota"] == plain["min_quota"] == {
        "pool": "cx X-7D", "left_pct": 42, "state": "ok", "aged": False}


def test_fresh_frame_reads_capacity_and_alerts_on_an_out_pool(tmp_path):
    from tests.communication.harness.observation import T0
    out = mrm.render_monitor_frame(_quota_store(tmp_path, 0.0).db_path, ["capacity"], read_at=T0 + 10, preference_source="command")
    assert out["alert_sources"]["capacity"] == "read"
    assert out["min_quota"] == {"pool": "cx X-7D", "left_pct": 0, "state": "out", "aged": False}
    assert [(a["code"], a["subject"]) for a in out["alerts"]] == [("a3", "cx X-7D")]


def test_a_store_without_quota_reports_reads_capacity(tmp_path):
    from tests.communication.fakes.observation import FakeObservationSource, absent
    from tests.communication.harness.observation import T0, ObservationHarness
    h = ObservationHarness(tmp_path / "noquota")
    h.register_pool({"schema_version": "1.0", "resource_pool_id": "cx:X-7D", "provider": "openai", "kind": "QUOTA", "metadata": {}})
    h.capture("cx", "quota", FakeObservationSource(absent({"reason": "not reported by provider"})), resource_pool_ref="cx:X-7D")
    out = mrm.render_monitor_frame(h.db_path, ["capacity"], read_at=T0 + 300, preference_source="command")
    assert out["alert_sources"]["capacity"] == "read" and out["min_quota"] is None and out["alerts"] == []


# ---- through the frame --------------------------------------------------------------------------------

def test_alerts_and_sources_are_always_present(tmp_path):
    h = CoreHarness(tmp_path / "ws")
    out = mrm.render_monitor_frame(h.db_path, ["store"], read_at=NOW)
    assert out["alerts"] == []
    assert out["alert_sources"] == {"activity": "not_selected", "capacity": "not_selected", "accounts": "not_selected",
                                    "facts": "not_selected"}
    assert out["min_quota"] is None
    assert list(out).index("alert_sources") == list(out).index("errors") + 1


@pytest.mark.parametrize("damage", ["drop_rollups", "bad_observations"])
def test_a_failing_group_is_an_a6_alert_and_an_error_source(tmp_path, damage):
    import sqlite3
    from contextlib import closing
    from peerhub.extensions.observation import ObservationStore
    h = CoreHarness(tmp_path / "ws")
    ObservationStore(h.db_path)
    with closing(sqlite3.connect(h.db_path)) as c:  # the observation tables exist, but the capacity statement cannot run
        if damage == "drop_rollups":
            c.execute("DROP TABLE observation_rollups")
        else:
            c.execute("DROP TABLE observations")
            c.execute("CREATE TABLE observations (capture_seq INTEGER)")
        c.commit()
    out = mrm.render_monitor_frame(h.db_path, ["capacity", "activity"], read_at=NOW, preference_source="command")
    assert out["groups"]["capacity"]["status"] == "ERROR" and out["errors"] == {"capacity": "OperationalError"}
    assert out["alert_sources"] == {"activity": "read", "capacity": "error", "accounts": "error", "facts": "error"}
    assert [(a["code"], a["kind"], a["subject"]) for a in out["alerts"]] == [("a6", "store", "capacity: OperationalError")]


@pytest.mark.parametrize("groups", [["capacity", "activity"], ["peers", "activity"]])
def test_a_store_that_never_had_a_quota_refresh_is_not_a_store_failure(tmp_path, groups):
    from tests.communication.diag.test_monitor_activity import _bridge_store  # pyright: ignore[reportPrivateUsage]
    h = _bridge_store(tmp_path)  # a real store with an ask, but no observation tables yet (never a quota refresh)
    out = mrm.render_monitor_frame(h.db_path, groups, read_at=2000.0, preference_source="command", liveness=lambda *a, **k: True)
    assert out["alerts"] == []
    assert out["alert_sources"] == {"activity": "read", "capacity": "no_evidence", "accounts": "no_evidence",
                                    "facts": "no_evidence", "reasons": {"capacity": "no quota evidence"}}
    assert out["errors"] == {"capacity": "OperationalError"}  # JSON errors and the group status are unchanged
    if "capacity" in groups:
        assert out["groups"]["capacity"] == {"status": "ERROR", "data": None, "coverage": out["groups"]["capacity"]["coverage"]}
    assert out["groups"]["activity"]["status"] == "OK" and len(out["groups"]["activity"]["data"]["asks"]) == 1
    assert out["min_quota"] is None


@pytest.mark.parametrize("source,hint", [("workspace", True), ("global", True), ("command", False), ("packaged", False)])
def test_hint_when_saved_defaults_omit_activity(tmp_path, source, hint):
    h = CoreHarness(tmp_path / "ws")
    out = mrm.render_monitor_frame(h.db_path, ["store"], read_at=NOW, preference_source=source)
    assert ("hint" in out["alert_sources"]) is hint
    if hint:
        assert out["alert_sources"]["hint"] == "peerhub monitor defaults reset"


def test_hint_is_inferred_from_saved_defaults_when_no_source_is_given(tmp_path, monkeypatch):
    h = CoreHarness(tmp_path / "ws")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "global"))
    config = tmp_path / ".peerhub" / "config" / "monitor.toml"
    config.parent.mkdir(parents=True)
    config.write_text('schema_version = 1\n[monitor]\ndefault_groups = ["store"]\n', encoding="utf-8")
    out = mrm.render_monitor_frame(h.db_path, ["store"], read_at=NOW)
    assert out["alert_sources"]["hint"] == "peerhub monitor defaults reset"
    other = mrm.render_monitor_frame(h.db_path, ["capacity", "store"], read_at=NOW)
    assert "hint" not in other["alert_sources"]


def test_frame_alerts_come_from_activity_rows(tmp_path):
    from tests.communication.diag.test_monitor_activity import caller, claim, delivery, iso
    from peerhub.extensions import bridge, bridge_claims
    import sqlite3
    from contextlib import closing
    h = CoreHarness(tmp_path / "ws")
    with closing(sqlite3.connect(h.db_path)) as c:
        for stmt in bridge._DDL:  # pyright: ignore[reportPrivateUsage]
            c.execute(stmt)
        c.execute(bridge_claims._DDL)  # pyright: ignore[reportPrivateUsage]
        c.commit()
    claim(h.db_path, "gone-ask")
    delivery(h.db_path, "gone-ask", caller_detail=caller())
    claim(h.db_path, "bad-ask", expires=NOW - 30.0)
    delivery(h.db_path, "bad-ask", certainty="TERMINAL", terminal_at=iso(NOW - 30.0), result={"response": "", "error": None})
    out = mrm.render_monitor_frame(h.db_path, ["activity"], read_at=NOW, liveness=lambda *a, **k: False)
    assert [(a["code"], a["kind"], a["subject"]) for a in out["alerts"]] == [
        ("a2", "gone", "cx.standard gone-ask"), ("a1", "empty", "cx.standard bad-ask")]
    assert out["alert_sources"]["activity"] == "read"
    json.dumps(out)
