"""Canonical, cardinality-accurate scale fixture for the monitor read frame.

The CI test deliberately asserts structure and bounds, not host-dependent time.
Set PEERHUB_MONITOR_BENCHMARK=1 to run the local 20+20 latency gate.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
import os
import platform
import sqlite3
from time import perf_counter

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.diag, pytest.mark.platform_sensitive]

PEERS = ("ag", "cc", "cx")
SCOPE_COUNT = 60
RECENT_SESSIONS = 1_200
TOMBSTONES = 3_800
TERMINAL_DELIVERIES = 50_000
UNRELATED_DELIVERIES = 40_000
UNRELATED_EVIDENCE = 40_000
UNRELATED_OBSERVATIONS = 20_000
QUOTA_POOLS = 24
END_DAY = date(2026, 10, 15)
READ_AT = 1_792_022_400.0  # 2026-10-15 UTC


def _populate_scale_v1(tmp_path):
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness
    from peerhub.extensions.observation import ObservationStore

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness)
    ObservationStore(harness.db_path, clock=lambda: READ_AT)
    db = sqlite3.connect(harness.db_path)
    db.execute("PRAGMA foreign_keys=ON")
    scopes = [(f"scale-{peer}-{number:02d}", peer) for peer in PEERS for number in range(20)]
    recent_at = READ_AT - 30
    old_at = READ_AT - 86400 * 90
    with db:
        db.executemany("INSERT OR IGNORE INTO peers(peer_id,display_name,adapter_ref,metadata_json,created_at) VALUES (?,?,?,'{}',?)",
                       [(peer, peer, "test", "2026-10-15T00:00:00Z") for peer in PEERS])

        # Retained history: tombstones first, then recent rows, so the reader's
        # newest-100 sample is demonstrably drawn from the retained population.
        history = []
        for i in range(TOMBSTONES):
            stream, peer = scopes[i % SCOPE_COUNT]
            history.append((f"tomb-{i:05d}", stream, peer, "cc", f"external-t-{i}", i // SCOPE_COUNT + 1,
                            "fp", "{}", 0, "TOMBSTONED", old_at, "scale", None, None, None,
                            None, 1, old_at, old_at))
        db.executemany("INSERT INTO bridge_session_history(session_handle,stream_id,peer_id,runtime_kind,external_session_id,session_generation,adapter_fingerprint,binding,resumable,state,last_seen,workspace_generation,vendor_session_id,context_watermark,bootstrap_truncated,usage_json,context_watermark_version,created_at,tombstoned_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", history)
        recent = []
        for i in range(RECENT_SESSIONS):
            stream, peer = scopes[i % SCOPE_COUNT]
            recent.append((f"recent-{i:05d}", stream, peer, "cc", f"external-r-{i}", 1000 + i // SCOPE_COUNT,
                           "fp", '{"profile":"standard"}', 1, "ACTIVE", recent_at, "scale", None, None,
                           None, None, 1, recent_at, None))
        db.executemany("INSERT INTO bridge_session_history(session_handle,stream_id,peer_id,runtime_kind,external_session_id,session_generation,adapter_fingerprint,binding,resumable,state,last_seen,workspace_generation,vendor_session_id,context_watermark,bootstrap_truncated,usage_json,context_watermark_version,created_at,tombstoned_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", recent)
        db.executemany("INSERT INTO bridge_session_monitor_stats(session_handle,reuse_count,fresh_count,counts_complete,binding_json,latest_call_seq,latest_telemetry_seq) VALUES (?,0,1,1,'{}',NULL,NULL)",
                       [(r[0],) for r in history + recent])
        # Per-scope delivery projection and 30 UTC daily buckets. Distribution
        # of terminal deliveries differs by at most one across all 1,800 cells.
        per_cell, remainder = divmod(TERMINAL_DELIVERIES, SCOPE_COUNT * 30)
        scope_counts = {scope: 0 for scope in scopes}
        days = [ (END_DAY - timedelta(days=29 - i)).isoformat() for i in range(30)]
        daily_rows = []
        cell = 0
        for stream, peer in scopes:
            terminal_count = 0
            for day in days:
                count = per_cell + (1 if cell < remainder else 0)
                cell += 1
                terminal_count += count
                daily_rows.append((stream, peer, day, count, 0, 0, 0, 0, count,
                                   0, 0, 0, 0, 0, 0, TERMINAL_DELIVERIES, 1, 1))
            scope_counts[(stream, peer)] = terminal_count
        scope_rows = []
        for (stream, peer), count in scope_counts.items():
            scope_rows.append((stream, peer, 0, 0, 0, count, 0, 0, 0, 0, count,
                               1, TERMINAL_DELIVERIES, 1, 0, 0, 1,
                               0, 0, 0, 0, 0, 0, 1))
        db.executemany("INSERT INTO bridge_monitor_delivery_scope VALUES (" + ",".join("?" for _ in range(24)) + ")", scope_rows)
        db.executemany("INSERT INTO bridge_monitor_delivery_day VALUES (" + ",".join("?" for _ in range(18)) + ")", daily_rows)
        retained_per_scope = [5000 // SCOPE_COUNT + (1 if i < 5000 % SCOPE_COUNT else 0)
                              for i in range(SCOPE_COUNT)]
        db.executemany("INSERT INTO bridge_session_monitor_scope(stream_id,peer_id,reuse_count,fresh_count,counts_complete,coverage_start_seq,retained_session_count,retained_complete) VALUES (?,?,0,20,1,0,?,1)",
                       [(stream, peer, retained_per_scope[i]) for i, (stream, peer) in enumerate(scopes)])

        # Terminal facts have one matching terminal evidence row each. An
        # independent 40k unrelated delivery/evidence population stresses the
        # same canonical source tables without entering the selected scopes.
        delivery_sql = "INSERT INTO bridge_deliveries(delivery_id,seq,stream_id,peer_id,record_id,record_position,attempt,certainty,claim_generation,terminal_at,acked) VALUES (?,?,?,?,?,0,1,?,1,?,0)"
        evidence_sql = "INSERT INTO bridge_evidence(seq,delivery_id,stream_id,peer_id,kind,certainty,detail) VALUES (?,?,?,?,?,?,?)"
        terminal_deliveries = []
        terminal_evidence = []
        for i in range(TERMINAL_DELIVERIES):
            stream, peer = scopes[i % SCOPE_COUNT]
            terminal_at = f"{days[(i // SCOPE_COUNT) % 30]}T12:00:00Z"
            delivery_id = f"scale-terminal-{i:05d}"
            terminal_deliveries.append((delivery_id, i + 1, stream, peer, f"record-terminal-{i}", "NOT_STARTED", terminal_at))
            terminal_evidence.append((i + 1, delivery_id, stream, peer, "terminal", "TERMINAL", "{}"))
        db.executemany(delivery_sql, terminal_deliveries)
        # Follow the real monotone delivery lifecycle (NOT_STARTED -> STARTED
        # -> TERMINAL) while avoiding tens of thousands of per-row API calls.
        db.execute("UPDATE bridge_deliveries SET certainty='STARTED' WHERE seq<=?", (TERMINAL_DELIVERIES,))
        db.execute("UPDATE bridge_deliveries SET certainty='TERMINAL',result_json='{}',terminal_at=terminal_at WHERE seq<=?",
                   (TERMINAL_DELIVERIES,))
        db.executemany(evidence_sql, terminal_evidence)
        unrelated_deliveries = ((f"unrelated-d-{i:05d}", TERMINAL_DELIVERIES + i + 1,
                                 f"unrelated-stream-{i % 200:03d}", "unrelated", f"record-unrelated-{i}",
                                 "NOT_STARTED", None) for i in range(UNRELATED_DELIVERIES))
        db.executemany(delivery_sql, unrelated_deliveries)
        unrelated_evidence = ((TERMINAL_DELIVERIES + i + 1, f"unrelated-d-{i:05d}",
                               f"unrelated-stream-{i % 200:03d}", "unrelated", "diagnostic",
                               "NOT_STARTED", "{}") for i in range(UNRELATED_EVIDENCE))
        db.executemany(evidence_sql, unrelated_evidence)

        # Register 24 independent quota pools and latest quota observations.
        db.executemany("INSERT INTO resource_pools(resource_pool_id,provider,kind,metadata_json) VALUES (?,?,'account','{}')",
                       [(f"pool-{i:02d}", "cc" if i < 8 else ("cx" if i < 16 else "ag")) for i in range(QUOTA_POOLS)])
        db.executemany("INSERT INTO observations(observation_id,subject_ref,resource_pool_ref,kind,source,state,payload_json,observed_at,captured_at,effective_at_us) VALUES (?,? ,?,'quota','scale-fixture','MEASURED',?,?,?,?)",
                       [(f"quota-{i:02d}", f"peer:{PEERS[i // 8]}", f"pool-{i:02d}",
                         json.dumps({"remaining_fraction": 0.5}), "2026-10-15T00:00:00Z", "2026-10-15T00:00:00Z", i)
                        for i in range(QUOTA_POOLS)])
        # These observations are unrelated activity records; append trigger
        # maintains the real observation_rollups projection during bulk load.
        db.executemany("INSERT INTO observations(observation_id,subject_ref,resource_pool_ref,kind,source,state,payload_json,observed_at,captured_at,effective_at_us) VALUES (?,? ,NULL,'activity','scale-fixture','UNKNOWN','{}',?,?,?)",
                       ((f"unrelated-o-{i:05d}", f"unrelated-subject-{i % 500:03d}",
                         "2026-10-15T00:00:00Z", "2026-10-15T00:00:00Z", QUOTA_POOLS + i)
                        for i in range(UNRELATED_OBSERVATIONS)))
    db.close()
    return harness


@pytest.fixture(scope="module")
def scale_harness(tmp_path_factory):
    return _populate_scale_v1(tmp_path_factory.mktemp("monitor-frame-scale-v1"))


def _assert_scale_cardinalities(db_path: str) -> None:
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM bridge_session_history").fetchone()[0] == 5000
        assert db.execute("SELECT COUNT(*) FROM bridge_session_history WHERE state='TOMBSTONED'").fetchone()[0] == 3800
        assert db.execute("SELECT COUNT(*) FROM bridge_session_history WHERE state='ACTIVE'").fetchone()[0] == 1200
        assert db.execute("SELECT COUNT(*) FROM bridge_monitor_delivery_scope").fetchone()[0] == 60
        assert db.execute("SELECT COUNT(*) FROM bridge_monitor_delivery_day").fetchone()[0] == 1800
        assert db.execute("SELECT SUM(terminal_count) FROM bridge_monitor_delivery_day").fetchone()[0] == TERMINAL_DELIVERIES
        assert db.execute("SELECT COUNT(*) FROM bridge_deliveries WHERE certainty='TERMINAL'").fetchone()[0] == TERMINAL_DELIVERIES
        assert db.execute("SELECT COUNT(*) FROM bridge_deliveries WHERE peer_id='unrelated'").fetchone()[0] == UNRELATED_DELIVERIES
        assert db.execute("SELECT COUNT(*) FROM bridge_evidence WHERE kind='terminal'").fetchone()[0] == TERMINAL_DELIVERIES
        assert db.execute("SELECT COUNT(*) FROM bridge_evidence WHERE kind='diagnostic'").fetchone()[0] == UNRELATED_EVIDENCE
        assert db.execute("SELECT COUNT(*) FROM observations WHERE kind='activity'").fetchone()[0] == UNRELATED_OBSERVATIONS
        assert db.execute("SELECT COUNT(*) FROM resource_pools").fetchone()[0] == QUOTA_POOLS
        assert UNRELATED_DELIVERIES + UNRELATED_EVIDENCE + UNRELATED_OBSERVATIONS == 100_000
        assert (UNRELATED_DELIVERIES + UNRELATED_EVIDENCE + UNRELATED_OBSERVATIONS) >= 100_000
        assert db.execute("SELECT SUM(retained_session_count) FROM bridge_session_monitor_scope").fetchone()[0] == 5000


def _frame(harness, trace=None):
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    return render_monitor_frame(harness.db_path,
        ["overview", "execution", "sessions", "usage", "context", "capacity", "runtime", "coverage", "store"],
        read_at=READ_AT, trace_callback=trace)


def test_monitor_frame_scale_v1_full_fixture_has_bounded_output_and_select_count(scale_harness):
    harness = scale_harness
    _assert_scale_cardinalities(harness.db_path)
    statements: list[str] = []
    frame = _frame(harness, statements.append)
    selects = [s for s in statements if s.lstrip().upper().startswith(("SELECT", "WITH"))]
    assert frame["query_families"] == len(selects) <= 6
    execution = frame["groups"]["execution"]["data"]
    assert execution["sample_size"] == 60
    assert len(execution["scopes"]) == 60
    assert all(len([scope for scope in execution["scopes"] if scope["peer_id"] == peer]) <= 20 for peer in PEERS)
    assert frame["groups"]["sessions"]["data"]["sample_size"] == 100
    assert frame["groups"]["sessions"]["data"]["truncated"] is True
    assert frame["groups"]["capacity"]["data"]["sample_size"] == QUOTA_POOLS
    with sqlite3.connect(harness.db_path) as db:
        plans = {
            "scope": " ".join(str(row[3]) for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT stream_id FROM bridge_monitor_delivery_scope WHERE peer_id='cc' ORDER BY stream_id LIMIT 21")),
            "days": " ".join(str(row[3]) for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT terminal_count FROM bridge_monitor_delivery_day WHERE peer_id='cc' AND terminal_day_utc BETWEEN '2026-09-16' AND '2026-10-15'")),
            "sessions": " ".join(str(row[3]) for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT session_handle FROM bridge_session_history ORDER BY rowid DESC LIMIT 101")),
            "observations": " ".join(str(row[3]) for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT latest_capture_seq FROM observation_rollups WHERE kind='quota'")),
        }
    assert "idx_monitor_delivery_peer" in plans["scope"]
    assert "idx_monitor_delivery_day" in plans["days"]
    assert "idx_bridge_session_history" in plans["sessions"] or "SCAN bridge_session_history" in plans["sessions"]
    assert "observation_rollups" in plans["observations"]


@pytest.mark.skipif(os.environ.get("PEERHUB_MONITOR_BENCHMARK") != "1", reason="local scale latency gate is opt-in")
def test_monitor_frame_scale_v1_local_benchmark_p95(scale_harness):
    import math
    harness = scale_harness
    _assert_scale_cardinalities(harness.db_path)
    for _ in range(20):
        _frame(harness)
    timings = []
    for _ in range(20):
        started = perf_counter()
        _frame(harness)
        timings.append((perf_counter() - started) * 1000)
    p95 = sorted(timings)[math.ceil(0.95 * len(timings)) - 1]
    report = {"fixture": "monitor_frame_scale_v1", "machine": platform.platform(),
              "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
              "warm_frames": 20, "measured_frames": 20, "p95_ms": round(p95, 3)}
    print(json.dumps(report, sort_keys=True))
    assert p95 <= 150, report
