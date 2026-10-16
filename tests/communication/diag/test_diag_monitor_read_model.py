from __future__ import annotations

import sqlite3
import json

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.diag, pytest.mark.platform_sensitive]


def test_usage_coverage_reads_bounded_daily_projection():
    from peerhub.extensions.monitor_read_model import usage_coverage

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE bridge_monitor_delivery_scope(stream_id,peer_id,delivery_coverage_start_seq,"
                 "telemetry_coverage_start_seq,delivery_complete,telemetry_complete)")
    conn.execute("CREATE TABLE bridge_monitor_delivery_day(stream_id,peer_id,terminal_day_utc,terminal_count,measured_count,"
                 "absent_count,unsupported_count,unknown_count,missing_count,latency_sum_ns,latency_count,"
                 "latency_missing_start_count,latency_domain_mismatch_count,latency_invalid_count,"
                 "latency_terminal_before_start_count,source_high_water_seq,complete,semantics_version)")
    conn.executemany("INSERT INTO bridge_monitor_delivery_day VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("s", "cx", "2026-10-15", 5, 1, 1, 1, 1, 1, 90, 2, 1, 0, 0, 0, 500, 1, 1),
        ("s", "cx", "2026-10-01", 2, 0, 0, 0, 1, 1, 30, 1, 0, 1, 0, 0, 400, 1, 1),
        ("other", "cx", "2026-10-15", 100, 100, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 800, 1, 1),
        ("s", "cc", "2026-10-15", 100, 100, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 900, 1, 1),
        ("s", "cx", "2026-09-01", 100, 100, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1000, 1, 1),
    ])
    conn.executemany("INSERT INTO bridge_monitor_delivery_scope VALUES (?,?,?,?,?,?)", [
        ("s", "cx", 12, 12, 1, 0), ("other", "cx", 1, 1, 1, 1), ("s", "cc", 1, 1, 1, 1)
    ])

    assert usage_coverage(conn, peer_id="cx", stream_id="s", through_day="2026-10-15") == {
        "eligible": 7, "measured": 1, "absent": 1, "unsupported": 1, "unknown": 2, "missing": 2,
        "coverage_start_delivery_seq": 12, "complete": 0,
    }
    result = usage_coverage(conn, peer_id="cx", stream_id="s", through_day="2026-10-15")
    assert result["coverage_start_delivery_seq"] == 12 and result["complete"] == 0
    conn.execute("UPDATE bridge_monitor_delivery_scope SET telemetry_complete=1 WHERE peer_id='cx' AND stream_id='s'")
    assert usage_coverage(conn, peer_id="cx", stream_id="s", through_day="2026-10-15")["complete"] == 1


def test_scope_sample_cap_is_partial_coverage_not_query_error(tmp_path):
    from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
    from tests.communication.harness.bridge import BridgeHarness
    from tests.communication.bridge_helpers import bseed
    from peerhub.extensions.monitor_read_model import render_monitor_frame

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness)
    harness.delivery_cycle("b", "s", _ProviderTelemetryRuntime())
    with harness.cs.transaction() as conn:
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(bridge_monitor_delivery_scope)")]
        values = list(conn.execute("SELECT * FROM bridge_monitor_delivery_scope WHERE stream_id='s' AND peer_id='b'").fetchone())
        placeholders = ",".join("?" for _ in columns)
        for index in range(1, 21):
            values[0] = f"s-{index:02d}"
            conn.execute(f"INSERT INTO bridge_monitor_delivery_scope ({','.join(columns)}) VALUES ({placeholders})", values)
        assert conn.execute("SELECT COUNT(*) FROM bridge_monitor_delivery_scope WHERE peer_id='b'").fetchone()[0] == 21
    frame = render_monitor_frame(harness.db_path, ["execution", "coverage", "sessions"])
    assert frame["groups"]["execution"]["status"] == "OK"
    assert frame["groups"]["execution"]["coverage"]["read"] == "partial", (frame["groups"]["execution"], frame["errors"])
    assert frame["groups"]["execution"]["data"]["truncated"] is True


def test_fourth_peer_is_detected_as_partial_in_execution_and_coverage(tmp_path):
    from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
    from tests.communication.harness.bridge import BridgeHarness
    from tests.communication.bridge_helpers import bseed
    from peerhub.extensions.monitor_read_model import render_monitor_frame

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness)
    harness.delivery_cycle("b", "s", _ProviderTelemetryRuntime())
    with harness.cs.transaction() as conn:
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(bridge_monitor_delivery_scope)")]
        base = list(conn.execute("SELECT * FROM bridge_monitor_delivery_scope WHERE stream_id='s' AND peer_id='b'").fetchone())
        conn.execute("DELETE FROM bridge_monitor_delivery_scope")
        placeholders = ",".join("?" for _ in columns)
        for peer in ("a", "b", "c", "d"):
            values = list(base)
            values[0], values[1] = f"scope-{peer}", peer
            conn.execute(f"INSERT INTO bridge_monitor_delivery_scope ({','.join(columns)}) VALUES ({placeholders})", values)

    frame = render_monitor_frame(harness.db_path, ["execution", "coverage"])
    for group in ("execution", "coverage"):
        assert frame["groups"][group]["coverage"]["read"] == "partial"
        assert frame["groups"][group]["data"]["truncated"] is True
    assert {scope["peer_id"] for scope in frame["groups"]["execution"]["data"]["scopes"]} == {"a", "b", "c"}


def test_required_scope_failure_propagates_only_to_dependent_selected_groups(tmp_path):
    from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
    from tests.communication.harness.bridge import BridgeHarness
    from tests.communication.bridge_helpers import bseed
    from peerhub.extensions.monitor_read_model import render_monitor_frame

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness)
    harness.delivery_cycle("b", "s", _ProviderTelemetryRuntime())
    groups = ["overview", "execution", "sessions", "usage", "context", "runtime", "coverage", "capacity", "store"]
    baseline = render_monitor_frame(harness.db_path, groups)
    with harness.cs.transaction() as conn:
        conn.execute("DROP TABLE bridge_monitor_delivery_scope")
    frame = render_monitor_frame(harness.db_path, groups)
    for group in ("overview", "execution", "sessions", "usage", "context", "runtime", "coverage"):
        assert frame["groups"][group]["status"] == "ERROR"
    assert frame["groups"]["capacity"]["status"] == baseline["groups"]["capacity"]["status"]
    assert frame["groups"]["store"]["status"] == "OK"


def test_monitor_frame_scale_smoke_v1_bounded_queries_indexes_and_p95(tmp_path):
    from math import ceil
    from time import perf_counter
    from datetime import date, timedelta
    from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
    from tests.communication.harness.bridge import BridgeHarness
    from tests.communication.bridge_helpers import bseed
    from peerhub.extensions.monitor_read_model import render_monitor_frame

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness)
    harness.delivery_cycle("b", "s", _ProviderTelemetryRuntime())
    with harness.cs.transaction() as conn:
        scope_columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(bridge_monitor_delivery_scope)")]
        base_scope = list(conn.execute("SELECT * FROM bridge_monitor_delivery_scope WHERE stream_id='s' AND peer_id='b'").fetchone())
        marks = ",".join("?" for _ in scope_columns)
        for peer in ("a", "b", "c"):
            for index in range(21):
                if peer == "b" and index == 0:
                    continue
                values = list(base_scope)
                values[0], values[1] = f"scale-{peer}-{index:02d}", peer
                conn.execute(f"INSERT INTO bridge_monitor_delivery_scope ({','.join(scope_columns)}) VALUES ({marks})", values)
        day_columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(bridge_monitor_delivery_day)")]
        base_day = list(conn.execute("SELECT * FROM bridge_monitor_delivery_day WHERE stream_id='s' AND peer_id='b'").fetchone())
        day_marks = ",".join("?" for _ in day_columns)
        for peer in ("a", "b", "c"):
            for index in range(21):
                if peer == "b" and index == 0:
                    continue
                for age in range(30):
                    values = list(base_day)
                    values[0], values[1] = f"scale-{peer}-{index:02d}", peer
                    values[2] = (date(2026, 10, 15) - timedelta(days=age)).isoformat()
                    conn.execute(f"INSERT INTO bridge_monitor_delivery_day ({','.join(day_columns)}) VALUES ({day_marks})", values)

        scope_plan = " ".join(str(row[3]) for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT stream_id FROM bridge_monitor_delivery_scope WHERE peer_id='b' ORDER BY stream_id LIMIT 21"))
        day_plan = " ".join(str(row[3]) for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT terminal_count FROM bridge_monitor_delivery_day "
            "WHERE peer_id='b' AND terminal_day_utc BETWEEN '2026-09-16' AND '2026-10-15'"))
    assert "idx_monitor_delivery_peer" in scope_plan
    assert "idx_monitor_delivery_day" in day_plan

    groups = ["execution", "coverage", "sessions", "usage"]
    samples = []
    frame = None
    for _ in range(10):
        started = perf_counter()
        frame = render_monitor_frame(harness.db_path, groups, read_at=1792022400.0)
        samples.append(perf_counter() - started)
    assert frame is not None and frame["query_families"] <= 6
    assert frame["groups"]["execution"]["data"]["sample_size"] == 60
    p95_ms = sorted(samples)[ceil(len(samples) * 0.95) - 1] * 1000
    assert p95_ms < 250


def test_session_latency_reads_only_named_scope_and_thirty_utc_days():
    from peerhub.extensions.monitor_read_model import session_latency

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE bridge_monitor_delivery_day(stream_id,peer_id,terminal_day_utc,terminal_count,measured_count,"
                 "absent_count,unsupported_count,unknown_count,missing_count,latency_sum_ns,latency_count,"
                 "latency_missing_start_count,latency_domain_mismatch_count,latency_invalid_count,"
                 "latency_terminal_before_start_count,source_high_water_seq,complete,semantics_version)")
    conn.execute("CREATE TABLE bridge_monitor_delivery_scope(stream_id,peer_id,delivery_coverage_start_seq,"
                 "telemetry_coverage_start_seq,delivery_complete,telemetry_complete)")
    conn.execute("INSERT INTO bridge_monitor_delivery_scope VALUES ('s','cc',1,1,1,1)")
    conn.executemany("INSERT INTO bridge_monitor_delivery_day VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("s", "cc", "2026-10-15", 2, 0, 0, 0, 0, 2, 90, 2, 1, 0, 0, 0, 2, 1, 1),
        ("s", "cc", "2026-10-01", 1, 0, 0, 0, 0, 1, 30, 1, 0, 1, 0, 0, 1, 1, 1),
        ("other", "cc", "2026-10-15", 1, 0, 0, 0, 0, 1, 500, 1, 0, 0, 0, 0, 3, 1, 1),
        ("s", "cx", "2026-10-15", 1, 0, 0, 0, 0, 1, 700, 1, 0, 0, 0, 0, 4, 1, 1),
        ("s", "cc", "2026-09-01", 1, 0, 0, 0, 0, 1, 999, 1, 0, 0, 0, 0, 5, 1, 1),
    ])

    result = session_latency(conn, peer_id="cc", stream_id="s", through_day="2026-10-15")

    assert result == {
        "latency_sum_ns": 120, "latency_count": 3,
        "missing_start_count": 1, "domain_mismatch_count": 1,
        "invalid_ticks_count": 0, "terminal_before_start_count": 0,
    }


def test_monitor_telemetry_projection_is_schema_bound_and_allowlisted():
    from peerhub.extensions.monitor_read_model import _public_telemetry
    from peerhub.extensions.session_receipts import validate_provider_usage

    usage = validate_provider_usage({
        "schema_version": 1, "scope": "session_cumulative", "source": "cc.result.usage",
        "reported": {"input_tokens": 4, "output_tokens": 2},
        "normalized": {"uncached_input_tokens": 4, "output_tokens": 2},
        "coverage": {"state": "MEASURED", "reason": None},
    }, "cc")
    detail = {"schema_version": 3, "handle": "peerhub-handle", "generation": 2, "provider_kind": "cc",
              "configured_model": "claude-sonnet", "resolved_model": None, "observed_at": 1000.0,
              "usage": usage, "last_input_tokens": 10, "context_window": 100, "context_pct": 10.0,
              "source": "cc.context", "window_reported_at": 1000.0,
              "response": "prompt/secret must never be exposed", "vendor_session_id": "vendor-secret"}
    public = _public_telemetry(json.dumps(detail), handle="peerhub-handle", generation=2, provider="cc")
    assert public["usage"]["reported"] == {"input_tokens": 4, "output_tokens": 2}
    assert public["context"]["occupancy_tokens"] is None
    assert public["context"]["window_tokens"] is None
    assert public["context"]["percent"] is None
    assert public["context"]["state"] == "UNKNOWN"
    assert public["context"]["reason"] == "unvalidated_context_source"
    assert "response" not in public and "vendor_session_id" not in public
    mismatched = _public_telemetry(json.dumps(detail), handle="another-handle", generation=2, provider="cc")
    assert mismatched["usage"] is None and mismatched["context"]["occupancy_tokens"] is None


def test_grouped_monitor_frame_uses_one_read_only_snapshot_and_at_most_six_selects(tmp_path):
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness)
    harness.delivery_cycle("b", "s", _ProviderTelemetryRuntime())
    statements: list[str] = []
    frame = render_monitor_frame(harness.db_path,
                                 ["overview", "execution", "sessions", "usage", "context", "capacity", "runtime", "coverage", "store"],
                                 read_at=2000.0, trace_callback=statements.append)
    select_statements = [statement for statement in statements if statement.lstrip().upper().startswith(("SELECT", "WITH"))]
    assert len(select_statements) == frame["query_families"] <= 6
    assert frame["status"] == "PARTIAL"
    assert frame["groups"]["capacity"]["status"] == "ERROR"
    assert set(frame["groups"]) == {"overview", "execution", "sessions", "usage", "context", "capacity", "runtime", "coverage", "store"}
    assert frame["groups"]["sessions"]["data"]["retained_sessions_by_peer"]["b"] == {
        "value": 1, "complete": True, "reason": None
    }
    assert len(frame["groups"]["execution"]["data"]["scopes"]) == 1, frame["errors"]
    assert frame["groups"]["execution"]["data"]["scopes"][0]["certainty"]["terminal"] == 1
    assert "vendor_session_id" not in str(frame)


def test_monitor_frame_queries_only_requested_group_families(tmp_path):
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness)
    statements: list[str] = []
    frame = render_monitor_frame(harness.db_path, ["store"], read_at=2000.0, trace_callback=statements.append)
    selects = [statement for statement in statements if statement.lstrip().upper().startswith(("SELECT", "WITH"))]
    assert selects == []
    assert frame["query_families"] == 0
    assert set(frame["groups"]) == {"store"}


def test_monitor_read_boundary_revalidates_persisted_session_binding(tmp_path):
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness)
    harness.delivery_cycle("b", "s", _ProviderTelemetryRuntime())
    with harness.cs.transaction() as conn:
        conn.execute("UPDATE bridge_session_monitor_stats SET binding_json=?", (
            json.dumps({"profile": "api_key=do-not-leak", "model": "safe-model", "effort": "x" * 500}),
        ))
    frame = render_monitor_frame(harness.db_path, ["sessions", "runtime"], read_at=2000.0)
    item = frame["groups"]["sessions"]["data"]["items"][0]
    assert item["configured_binding"] == {"profile": None, "model": "safe-model", "effort": None}
    assert "api_key=do-not-leak" not in json.dumps(frame)
