"""Read-only, bounded aggregate queries for structured monitor views."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any


def usage_coverage(conn: sqlite3.Connection, *, peer_id: str | None = None,
                   stream_id: str | None = None, through_day: str | None = None) -> dict[str, int | bool]:
    """Read the writer-maintained 30-day coverage projection; never scan delivery history."""
    end = date.fromisoformat(through_day) if through_day else datetime.now(timezone.utc).date()
    start = (end - timedelta(days=29)).isoformat()
    filters = ["day_utc>=?", "day_utc<=?"]
    args: list[Any] = [start, end.isoformat()]
    if peer_id is not None:
        filters.append("peer_id=?")
        args.append(peer_id)
    if stream_id is not None:
        filters.append("stream_id=?")
        args.append(stream_id)
    row = conn.execute(
        "SELECT COALESCE(SUM(eligible_count),0),COALESCE(SUM(measured_count),0),"
        "COALESCE(SUM(absent_count),0),COALESCE(SUM(unsupported_count),0),"
        "COALESCE(SUM(unknown_count),0),COALESCE(SUM(missing_count),0) "
        f"FROM bridge_session_usage_coverage_daily WHERE {' AND '.join(filters)}", args,
    ).fetchone()
    scope = conn.execute(
        "SELECT MIN(c.coverage_start_delivery_seq),COUNT(c.peer_id),COUNT(a.peer_id),"
        "COALESCE(SUM(CASE WHEN c.coverage_start_day<=? THEN 1 ELSE 0 END),0) "
        "FROM (SELECT DISTINCT stream_id,peer_id FROM bridge_session_usage_coverage_daily "
        f"WHERE {' AND '.join(filters)}) a LEFT JOIN bridge_session_usage_coverage_scope c "
        "ON c.stream_id=a.stream_id AND c.peer_id=a.peer_id", [start, *args],
    ).fetchone()
    result: dict[str, int | bool] = dict(zip(("eligible", "measured", "absent", "unsupported", "unknown", "missing"),
                                             (int(value or 0) for value in row)))
    result["coverage_start_delivery_seq"] = int(scope[0] or 0)
    result["complete"] = int(scope[1] > 0 and scope[1] == scope[2] == scope[3])
    return result


def session_latency(conn: sqlite3.Connection, *, peer_id: str | None = None,
                    stream_id: str | None = None, through_day: str) -> dict[str, int]:
    """Read the bounded UTC daily timing projection; do not recount evidence history."""
    end = date.fromisoformat(through_day)
    start = (end - timedelta(days=29)).isoformat()
    filters = ["day_utc>=?", "day_utc<=?"]
    args: list[Any] = [start, end.isoformat()]
    if peer_id is not None:
        filters.append("peer_id=?")
        args.append(peer_id)
    if stream_id is not None:
        filters.append("stream_id=?")
        args.append(stream_id)
    row = conn.execute(
        "SELECT COALESCE(SUM(latency_sum_ns),0),COALESCE(SUM(latency_count),0),"
        "COALESCE(SUM(missing_start_count),0),COALESCE(SUM(domain_mismatch_count),0),"
        "COALESCE(SUM(invalid_ticks_count),0),COALESCE(SUM(terminal_before_start_count),0) "
        f"FROM bridge_session_latency_daily WHERE {' AND '.join(filters)}", args,
    ).fetchone()
    return dict(zip(("latency_sum_ns", "latency_count", "missing_start_count", "domain_mismatch_count",
                     "invalid_ticks_count", "terminal_before_start_count"),
                    (int(value or 0) for value in row)))
