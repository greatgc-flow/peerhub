from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.unit


def test_usage_coverage_reads_bounded_daily_projection():
    from peerhub.extensions.monitor_read_model import usage_coverage

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE bridge_session_usage_coverage_daily(stream_id,peer_id,day_utc,eligible_count,measured_count,"
                 "absent_count,unsupported_count,unknown_count,missing_count)")
    conn.execute("CREATE TABLE bridge_session_usage_coverage_scope(stream_id,peer_id,coverage_start_delivery_seq,coverage_start_day)")
    conn.executemany("INSERT INTO bridge_session_usage_coverage_daily VALUES (?,?,?,?,?,?,?,?,?)", [
        ("s", "cx", "2026-10-15", 5, 1, 1, 1, 1, 1),
        ("s", "cx", "2026-10-01", 2, 0, 0, 0, 1, 1),
        ("other", "cx", "2026-10-15", 100, 100, 0, 0, 0, 0),
        ("s", "cc", "2026-10-15", 100, 100, 0, 0, 0, 0),
        ("s", "cx", "2026-09-01", 100, 100, 0, 0, 0, 0),
    ])

    assert usage_coverage(conn, peer_id="cx", stream_id="s", through_day="2026-10-15") == {
        "eligible": 7, "measured": 1, "absent": 1, "unsupported": 1, "unknown": 2, "missing": 2,
        "coverage_start_delivery_seq": 0, "complete": 0,
    }
    conn.execute("INSERT INTO bridge_session_usage_coverage_scope VALUES ('s','cx',12,'2026-10-01')")
    result = usage_coverage(conn, peer_id="cx", stream_id="s", through_day="2026-10-15")
    assert result["coverage_start_delivery_seq"] == 12 and result["complete"] == 0
    conn.execute("UPDATE bridge_session_usage_coverage_scope SET coverage_start_day='2026-09-01'")
    assert usage_coverage(conn, peer_id="cx", stream_id="s", through_day="2026-10-15")["complete"] == 1


def test_session_latency_reads_only_named_scope_and_thirty_utc_days():
    from peerhub.extensions.monitor_read_model import session_latency

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE bridge_session_latency_daily(stream_id,peer_id,day_utc,latency_sum_ns,latency_count,"
                 "missing_start_count,domain_mismatch_count,invalid_ticks_count,terminal_before_start_count)")
    conn.executemany("INSERT INTO bridge_session_latency_daily VALUES (?,?,?,?,?,?,?,?,?)", [
        ("s", "cc", "2026-10-15", 90, 2, 1, 0, 0, 0),
        ("s", "cc", "2026-10-01", 30, 1, 0, 1, 0, 0),
        ("other", "cc", "2026-10-15", 500, 1, 0, 0, 0, 0),
        ("s", "cx", "2026-10-15", 700, 1, 0, 0, 0, 0),
        ("s", "cc", "2026-09-01", 999, 1, 0, 0, 0, 0),
    ])

    result = session_latency(conn, peer_id="cc", stream_id="s", through_day="2026-10-15")

    assert result == {
        "latency_sum_ns": 120, "latency_count": 3,
        "missing_start_count": 1, "domain_mismatch_count": 1,
        "invalid_ticks_count": 0, "terminal_before_start_count": 0,
    }
