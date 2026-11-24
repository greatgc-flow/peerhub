"""#394 on the store side: the read model lists every peer, says how big the sample is, and the diag readers apply the
same cc F-7D limit (effective left = min(F-7D, C-7D), the nearer reset when the parent is lower)."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing

import pytest

from peerhub.extensions import diag_quota
from peerhub.extensions.diag_readiness import compose
from peerhub.extensions.monitor_read_model import PEER_CAP, mapped_quota, render_monitor_frame
from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
from tests.communication.bridge_helpers import bseed
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.harness.observation import ObservationHarness

pytestmark = [pytest.mark.unit, pytest.mark.diag, pytest.mark.platform_sensitive]


# ---- 2: every configured peer --------------------------------------------------------------------------------------
@pytest.mark.parametrize("names,truncated", [(("a", "ag", "ca", "cc", "cx", "ga", "oa"), False),
                                             (("a", *(f"p{n}" for n in range(PEER_CAP + 1))), True)])
def test_peers_lists_every_builtin_peer_and_says_when_it_is_capped(tmp_path, names, truncated):
    harness = BridgeHarness(tmp_path / "workspace")  # bseed's author is the peer "a"
    bseed(harness, peers=names, stream="s")
    data = render_monitor_frame(harness.db_path, ["peers", "overview"], workspace=harness.workspace)["groups"]
    peers = data["peers"]["data"]
    assert peers["peer_sample_truncated"] is truncated
    assert [p["peer_id"] for p in peers["peers"]] == sorted(names)[:PEER_CAP]
    assert [p["peer_id"] for p in data["overview"]["data"]["peers"]] == sorted(names)[:PEER_CAP]
    if truncated:
        assert peers["peers_total"] == len(names)
    else:
        assert "peers_total" not in peers


# ---- 1 and 3: sample sizes -----------------------------------------------------------------------------------------
def test_sessions_usage_and_context_share_one_sample_and_one_coverage(tmp_path):
    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness, peers=("a",), stream="s")
    with sqlite3.connect(harness.db_path) as conn:
        conn.executemany(
            "INSERT INTO bridge_session_history(session_handle,stream_id,peer_id,runtime_kind,external_session_id,"
            "session_generation,adapter_fingerprint,binding,resumable,state,last_seen,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(f"sess-{i:03d}", "s", "a", "codex", f"ext-{i}", i + 1, "fp", '{"profile":"a.default"}', 1, "ACTIVE",
              100.0 + i, 100.0 + i) for i in range(105)])
    frame = render_monitor_frame(harness.db_path, ["sessions", "usage", "context"], workspace=harness.workspace)
    for name in ("sessions", "usage", "context"):
        group = frame["groups"][name]
        assert group["data"]["truncated"] is True and group["data"]["sample_total"] == 105, name
        assert group["coverage"]["read"] == "partial" and group["coverage"]["reason"] == "sample_cap", name
    assert frame["groups"]["usage"]["data"]["sample_size"] == frame["groups"]["context"]["data"]["sample_size"] == 100


def test_a_complete_session_read_has_no_sample_total(tmp_path):
    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness, peers=("a",), stream="s")
    frame = render_monitor_frame(harness.db_path, ["sessions", "usage", "context"], workspace=harness.workspace)
    for name in ("sessions", "usage", "context"):
        assert frame["groups"][name]["coverage"]["read"] == "complete"
        assert "sample_total" not in frame["groups"][name]["data"]


def test_a_capped_execution_sample_reports_its_size_and_no_scope_total(tmp_path):
    """The total would need a scan of every scope (test_monitor_scope_scale forbids it): `sample N`, without `/M`."""
    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness)
    harness.delivery_cycle("b", "s", _ProviderTelemetryRuntime())
    with harness.cs.transaction() as conn:
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(bridge_monitor_delivery_scope)")]
        template = list(conn.execute("SELECT * FROM bridge_monitor_delivery_scope WHERE peer_id='b'").fetchone())
        for n in range(30):
            values = template.copy()
            values[0] = f"extra-{n:03d}"
            conn.execute(f"INSERT INTO bridge_monitor_delivery_scope ({','.join(columns)}) "
                         f"VALUES ({','.join('?' for _ in columns)})", values)
    data = render_monitor_frame(harness.db_path, ["execution"], workspace=harness.workspace)["groups"]["execution"]["data"]
    assert data["truncated"] is True and data["sample_size"] == 20 and "sample_total" not in data


# ---- 7: PEERS quota choice -----------------------------------------------------------------------------------------
def observation(pool: str, remaining: float, at: int = 1) -> dict:
    return {"pool_ref": f"quota:cc:{pool}", "state": "MEASURED", "measurements": {"remaining_fraction": remaining},
            "effective_at_us": at, "source": "probe", "age_seconds": 10}


@pytest.mark.parametrize("parent,child,chosen,left", [(.08, .26, "C-7D", .08), (.80, .26, "F-7D", .26)])
def test_the_peer_quota_choice_is_the_lower_of_the_scoped_window_and_its_parent(parent, child, chosen, left):
    quota = mapped_quota(["C-7D", "F-7D"], [observation("C-7D", parent), observation("F-7D", child)])
    assert quota is not None and quota["pool_ref"] == f"quota:cc:{chosen}" and quota["remaining_fraction"] == left


def test_the_peer_quota_choice_does_not_invent_a_parent_for_a_profile_that_lists_only_the_scoped_window():
    quota = mapped_quota(["F-7D"], [observation("C-7D", .08), observation("F-7D", .26)])  # as POOLS: no consumer proof
    assert quota is not None and quota["pool_ref"] == "quota:cc:F-7D" and quota["remaining_fraction"] == .26


# ---- 7: diag readiness ---------------------------------------------------------------------------------------------
NOW = 1_794_700_000.0


def readiness_observation(pool: str, remaining: float, reset_in: float) -> dict:
    return {"resource_pool_ref": f"quota:cc:{pool}", "kind": "quota", "state": "MEASURED", "age_seconds": 10,
            "captured_at": "2026-11-14T23:46:00Z", "observed_at": "2026-11-14T23:46:00Z", "capture_seq": 1,
            "observation_id": f"obs-{pool}", "payload": {"remaining_fraction": remaining, "resets_at": NOW + reset_in}}


def readiness(parent: float, child: float, parent_reset: float = 3600, child_reset: float = 86400) -> dict:
    profiles = [{"id": "cc.fable", "pools": ["quota:cc:C-7D", "quota:cc:F-7D"], "mapping": "verified",
                 "binding": "cc.fable/m/e", "policy": None},
                {"id": "cc.effort", "pools": ["quota:cc:C-7D"], "mapping": "verified", "binding": "cc.effort/m/e",
                 "policy": None}]  # the shipped membership: F-7D's profiles are a strict subset of C-7D's
    return compose(profiles, [readiness_observation("C-7D", parent, parent_reset),
                              readiness_observation("F-7D", child, child_reset)], [], NOW, complete=True)


def test_readiness_parent_lower_the_child_pool_has_the_parent_left_and_the_nearer_reset():
    pool = readiness(.08, .26)["pools"]["quota:cc:F-7D"]
    assert pool["remaining"] == .08 and pool["reset_in"] == 3600 and pool["limited_by"] == "quota:cc:C-7D"
    assert pool["own_remaining"] == .26


def test_readiness_parent_out_exhausts_the_child_pool_and_its_profile():
    report = readiness(0.0, .26)
    assert report["pools"]["quota:cc:F-7D"]["remaining"] == 0
    fable = next(p for p in report["profiles"] if p["id"] == "cc.fable")
    assert fable["state"] == "EXHAUSTED" and fable["action"] == "wait_reset"


def test_readiness_parent_higher_changes_nothing():
    pool = readiness(.80, .26, parent_reset=3600, child_reset=86400)["pools"]["quota:cc:F-7D"]
    assert pool["remaining"] == .26 and pool["reset_in"] == 86400 and "limited_by" not in pool


@pytest.mark.parametrize("remaining", [0, .08])
@pytest.mark.parametrize("reset", [0, -1])
def test_readiness_reset_parent_cannot_limit_the_child(remaining, reset):
    report = readiness(remaining, .26, parent_reset=reset)
    parent, child = (report["pools"][f"quota:cc:{name}"] for name in ("C-7D", "F-7D"))
    assert parent["state"] == "MEASURED" and parent["remaining"] is None and parent["reset_in"] == 0
    assert child["remaining"] == .26 and child["reset_in"] == 86400 and "limited_by" not in child
    assert all(p["state"] == "UNKNOWN" and p["action"] == "inspect_facts" for p in report["profiles"])


# ---- 7: diag quota -------------------------------------------------------------------------------------------------
def quota_store(tmp_path, parent: float, child: float, parent_reset: float = 3600, child_reset: float = 86400):
    harness = ObservationHarness(tmp_path / "ws")
    now = time.time()
    with closing(sqlite3.connect(harness.db_path)) as conn:
        for ref in ("quota:cc:C-7D", "quota:cc:F-7D"):
            conn.execute("INSERT INTO resource_pools (resource_pool_id, provider, kind, metadata_json) "
                         "VALUES (?, 'cc', 'QUOTA', '{}')", (ref,))
        for ref, left, reset in (("quota:cc:C-7D", parent, parent_reset), ("quota:cc:F-7D", child, child_reset)):
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 10))
            conn.execute(
                "INSERT INTO observations (observation_id, subject_ref, resource_pool_ref, kind, source, state, "
                "payload_json, observed_at, captured_at, effective_at_us) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"o-{ref}", "cc", ref, "quota", "probe", "MEASURED",
                 json.dumps({"remaining_fraction": left, "resets_at": int(now + reset)}), stamp, stamp,
                 int((now - 10) * 1e6)))
        conn.commit()
    return harness


def f7d(report: dict) -> dict:
    return next(i for g in report["pools"] for i in g["items"] if i["resource_pool_ref"] == "quota:cc:F-7D")


def test_diag_quota_parent_lower_adds_the_effective_values_without_changing_the_measurements(tmp_path):
    report = diag_quota.quota_report(quota_store(tmp_path, .08, .26).db_path)
    item = f7d(report)
    assert item["measurements"]["remaining_fraction"] == .26  # the pool's own evidence is untouched
    assert item["effective"]["remaining_fraction"] == .08 and item["effective"]["limited_by"] == "quota:cc:C-7D"
    assert item["effective"]["resets_at"] == item["window"]["resets_at"] - 86400 + 3600
    assert "effective=8%" in diag_quota.format_quota_table(report)
    assert set(report) == {"schema_version", "status", "source_db", "read_at", "filters", "overall", "pools",
                           "reset_credits", "error"}


def test_diag_quota_parent_higher_adds_nothing(tmp_path):
    report = diag_quota.quota_report(quota_store(tmp_path, .80, .26).db_path)
    assert "effective" not in f7d(report) and "effective=" not in diag_quota.format_quota_table(report)
