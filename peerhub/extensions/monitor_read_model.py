"""Read-only, bounded aggregate queries for structured monitor views."""
from __future__ import annotations

import sqlite3
from typing import Any


def usage_coverage(conn: sqlite3.Connection, *, peer_id: str | None = None,
                   stream_id: str | None = None) -> dict[str, int]:
    """Coverage among terminal deliveries; absent receipts remain explicitly missing."""
    filters = ["d.certainty='TERMINAL'"]
    args: list[Any] = []
    if peer_id is not None:
        filters.append("d.peer_id=?")
        args.append(peer_id)
    if stream_id is not None:
        filters.append("d.stream_id=?")
        args.append(stream_id)
    where = " AND ".join(filters)
    row = conn.execute(
        "WITH scoped AS (SELECT d.delivery_id,e.seq receipt_seq,CASE WHEN json_valid(e.detail) THEN json_extract(e.detail,'$.usage.coverage.state') END state "
        "FROM bridge_deliveries d LEFT JOIN bridge_evidence e ON e.delivery_id=d.delivery_id AND e.kind='session_telemetry' "
        f"WHERE {where}) SELECT COUNT(*),"
        "SUM(CASE WHEN state='MEASURED' THEN 1 ELSE 0 END),"
        "SUM(CASE WHEN state='ABSENT' THEN 1 ELSE 0 END),"
        "SUM(CASE WHEN state='UNSUPPORTED' THEN 1 ELSE 0 END),"
        "SUM(CASE WHEN receipt_seq IS NOT NULL AND (state IS NULL OR state NOT IN ('MEASURED','ABSENT','UNSUPPORTED')) THEN 1 ELSE 0 END),"
        "SUM(CASE WHEN receipt_seq IS NULL THEN 1 ELSE 0 END) FROM scoped", args,
    ).fetchone()
    return dict(zip(("eligible", "measured", "absent", "unsupported", "unknown", "missing"),
                    (int(value or 0) for value in row)))
