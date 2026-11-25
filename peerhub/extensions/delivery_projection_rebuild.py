"""Rebuild delivery monitoring projections atomically from historical evidence."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, cast

from peerhub.extensions.delivery_projections import CERTAINTY_COLUMNS, telemetry_coverage_state
from peerhub.extensions.session_receipts import valid_tick


def _historical_latency(conn: sqlite3.Connection, delivery: Mapping[str, Any] | sqlite3.Row) -> tuple[int | None, str | None] | None:
    started = conn.execute("SELECT 1 FROM bridge_evidence WHERE delivery_id=? AND stream_id=? AND peer_id=? "
                           "AND kind IN ('started','session_call') AND certainty='STARTED' LIMIT 1",
                           (delivery["delivery_id"], delivery["stream_id"], delivery["peer_id"])).fetchone()
    if started is None:
        return None
    call = conn.execute("SELECT detail FROM bridge_evidence WHERE delivery_id=? AND stream_id=? AND peer_id=? "
                        "AND kind='session_call' AND certainty='STARTED' ORDER BY seq DESC LIMIT 1",
                        (delivery["delivery_id"], delivery["stream_id"], delivery["peer_id"])).fetchone()
    terminal = conn.execute("SELECT detail FROM bridge_evidence WHERE delivery_id=? AND stream_id=? AND peer_id=? "
                            "AND kind='terminal' AND certainty='TERMINAL' ORDER BY seq DESC LIMIT 1",
                            (delivery["delivery_id"], delivery["stream_id"], delivery["peer_id"])).fetchone()
    if terminal is None:
        return None
    if call is None:
        return None, "missing_start"
    try:
        call_obj: object = json.loads(call[0])
        terminal_obj: object = json.loads(terminal[0]) if terminal else None
        call_detail = cast(dict[str, object], call_obj) if isinstance(call_obj, dict) else {}
        terminal_detail = cast(dict[str, object], terminal_obj) if isinstance(terminal_obj, dict) else {}
        start_raw: object = call_detail.get("peerhub_timing")
        end_raw: object = terminal_detail.get("peerhub_timing")
    except (TypeError, ValueError):
        return None, "invalid_ticks"
    start_timing = cast(dict[str, object], start_raw) if isinstance(start_raw, dict) else {}
    end_timing = cast(dict[str, object], end_raw) if isinstance(end_raw, dict) else {}
    if (type(call_detail.get("schema_version")) is not int or call_detail["schema_version"] != 2
            or not isinstance(start_raw, dict) or type(start_timing.get("schema_version")) is not int
            or start_timing["schema_version"] != 1
            or not isinstance(end_raw, dict) or type(end_timing.get("schema_version")) is not int
            or end_timing["schema_version"] != 1):
        return None, "invalid_ticks"
    start = cast(dict[str, object], start_raw)
    end = cast(dict[str, object], end_raw)
    start_tick, end_tick = start.get("started_monotonic_ns"), end.get("terminal_monotonic_ns")
    start_domain, end_domain = start.get("clock_domain"), end.get("clock_domain")
    if not valid_tick(start_tick) or not valid_tick(end_tick) or not isinstance(start_domain, str) or not isinstance(end_domain, str):
        return None, "invalid_ticks"
    if start_domain != end_domain:
        return None, "domain_mismatch"
    if end_tick < start_tick:
        return None, "terminal_before_start"
    return end_tick - start_tick, None


def rebuild_delivery_projections(db_path: str, *, chunk_size: int = 500, through_day: str | None = None) -> dict[str, Any]:
    """Explicit, atomic reconstruction from immutable delivery/evidence sources."""
    if not 1 <= chunk_size <= 5000:
        raise ValueError("chunk_size must be between 1 and 5000")
    import os
    if not os.path.isfile(db_path):
        raise FileNotFoundError(db_path)
    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("BEGIN IMMEDIATE")
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"bridge_monitor_delivery_scope", "bridge_monitor_delivery_day", "bridge_deliveries", "bridge_evidence"}
        if not required.issubset(tables):
            raise sqlite3.OperationalError("monitor projection schema is unavailable; open the Bridge workspace first")
        delivery_source = conn.execute("SELECT COUNT(*),COUNT(DISTINCT seq),COALESCE(MIN(seq),0),COALESCE(MAX(seq),0) "
                                       "FROM bridge_deliveries").fetchone()
        if int(delivery_source[0]) != int(delivery_source[1]):
            raise ValueError("delivery source sequence is not unique; projection rebuild aborted")
        end_day = date.fromisoformat(through_day) if through_day else datetime.now(timezone.utc).date()
        cutoff_day = (end_day - timedelta(days=29)).isoformat()
        scopes: dict[tuple[str, str], dict[str, int]] = {}
        days: dict[tuple[str, str, str], dict[str, int]] = {}
        first_seq: dict[tuple[str, str], int] = {}
        telemetry_first_seq: dict[tuple[str, str], int] = {}
        delivery_count = 0
        cursor = 0
        while True:
            rows = conn.execute("SELECT * FROM bridge_deliveries WHERE seq>? ORDER BY seq LIMIT ?", (cursor, chunk_size)).fetchall()
            if not rows:
                break
            for delivery in rows:
                delivery_count += 1
                cursor = int(delivery["seq"])
                scope_key = (str(delivery["stream_id"]), str(delivery["peer_id"]))
                first_seq.setdefault(scope_key, cursor)
                scope = scopes.setdefault(scope_key, {name: 0 for name in (
                    "not_started_count", "may_have_started_count", "started_count", "terminal_count",
                    "measured_count", "absent_count", "unsupported_count", "unknown_count", "missing_count",
                    "delivery_high_water_seq", "telemetry_high_water_seq", "latency_sum_ns", "latency_count",
                    "latency_missing_start_count", "latency_domain_mismatch_count", "latency_invalid_count",
                    "latency_terminal_before_start_count")})
                state_column = CERTAINTY_COLUMNS.get(str(delivery["certainty"]))
                if state_column is None:
                    raise ValueError("unsupported delivery certainty in source history")
                scope[state_column] += 1
                scope["delivery_high_water_seq"] = cursor
                if delivery["certainty"] != "TERMINAL":
                    continue
                state = telemetry_coverage_state(conn, delivery)
                scope[f"{state.lower()}_count"] += 1
                evidence_rows = conn.execute(
                    "SELECT seq,stream_id,peer_id,kind,certainty FROM bridge_evidence "
                    "WHERE delivery_id=? AND kind IN ('terminal','session_telemetry') ORDER BY seq",
                    (delivery["delivery_id"],),
                ).fetchall()
                terminal_rows = [row for row in evidence_rows if row["kind"] == "terminal"]
                if len(terminal_rows) != 1:
                    raise ValueError("terminal evidence cardinality invalid; projection rebuild aborted")
                if not terminal_rows or any(row["stream_id"] != delivery["stream_id"] or row["peer_id"] != delivery["peer_id"]
                                            or row["certainty"] != "TERMINAL" for row in terminal_rows):
                    raise ValueError("terminal evidence identity/certainty invalid; projection rebuild aborted")
                terminal_evidence_seq = min(int(row["seq"]) for row in terminal_rows)
                telemetry_seq = max((int(row["seq"]) for row in evidence_rows), default=terminal_evidence_seq)
                telemetry_first_seq[scope_key] = min(telemetry_first_seq.get(scope_key, terminal_evidence_seq), terminal_evidence_seq)
                scope["telemetry_high_water_seq"] = max(scope["telemetry_high_water_seq"], telemetry_seq)
                raw_terminal_at = delivery["terminal_at"]
                if not isinstance(raw_terminal_at, str):
                    raise ValueError("terminal delivery lacks terminal_at; projection rebuild aborted")
                try:
                    terminal_epoch = datetime.strptime(raw_terminal_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
                except ValueError as exc:
                    raise ValueError("terminal delivery has invalid terminal_at; projection rebuild aborted") from exc
                day_text = datetime.fromtimestamp(terminal_epoch, timezone.utc).date().isoformat()
                day: dict[str, int] | None = None
                if day_text >= cutoff_day:
                    day_key = (*scope_key, day_text)
                    day = days.setdefault(day_key, {name: 0 for name in (
                        "terminal_count", "measured_count", "absent_count", "unsupported_count", "unknown_count", "missing_count",
                        "latency_sum_ns", "latency_count", "latency_missing_start_count", "latency_domain_mismatch_count",
                        "latency_invalid_count", "latency_terminal_before_start_count", "source_high_water_seq")})
                    day["terminal_count"] += 1
                    day[f"{state.lower()}_count"] += 1
                    day["source_high_water_seq"] = max(day["source_high_water_seq"], cursor)
                latency = _historical_latency(conn, delivery)
                if latency is not None:
                    elapsed, reason = latency
                    if reason is None and elapsed is not None:
                        scope["latency_sum_ns"] += elapsed
                        scope["latency_count"] += 1
                        if day is not None:
                            day["latency_sum_ns"] += elapsed
                            day["latency_count"] += 1
                    elif reason is not None:
                        reason_name = {"missing_start": "latency_missing_start_count",
                                       "domain_mismatch": "latency_domain_mismatch_count",
                                       "invalid_ticks": "latency_invalid_count",
                                       "terminal_before_start": "latency_terminal_before_start_count"}[reason]
                        scope[reason_name] += 1
                        if day is not None:
                            day[reason_name] += 1
        total, distinct_seq, min_seq, max_seq = map(int, delivery_source)
        delivery_complete = int(total == 0 or (distinct_seq == total and min_seq == 1 and max_seq == total))
        evidence_source = conn.execute("SELECT COUNT(*),COALESCE(MIN(seq),0),COALESCE(MAX(seq),0) FROM bridge_evidence").fetchone()
        evidence_total, evidence_min, evidence_max = map(int, evidence_source)
        telemetry_complete = int(evidence_total == 0 or (evidence_min == 1 and evidence_max == evidence_total))
        complete = int(delivery_complete and telemetry_complete)
        conn.execute("DELETE FROM bridge_monitor_delivery_scope")
        conn.execute("DELETE FROM bridge_monitor_delivery_day")
        conn.execute("DELETE FROM bridge_session_latency_daily")
        conn.execute("DELETE FROM bridge_session_usage_coverage_daily")
        conn.execute("DELETE FROM bridge_session_usage_coverage_scope")
        for (stream, peer), scope in scopes.items():
            first = first_seq[(stream, peer)]
            conn.execute(
                "INSERT INTO bridge_monitor_delivery_scope VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (stream, peer, scope["not_started_count"], scope["may_have_started_count"], scope["started_count"], scope["terminal_count"],
                 scope["measured_count"], scope["absent_count"], scope["unsupported_count"], scope["unknown_count"], scope["missing_count"],
                 first, scope["delivery_high_water_seq"], delivery_complete,
                 telemetry_first_seq.get((stream, peer), 0), scope["telemetry_high_water_seq"], telemetry_complete,
                 scope["latency_sum_ns"], scope["latency_count"], scope["latency_missing_start_count"],
                 scope["latency_domain_mismatch_count"], scope["latency_invalid_count"], scope["latency_terminal_before_start_count"], 1),
            )
        for (stream, peer, day_text), day in days.items():
            conn.execute(
                "INSERT INTO bridge_monitor_delivery_day VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (stream, peer, day_text, day["terminal_count"], day["measured_count"], day["absent_count"], day["unsupported_count"],
                 day["unknown_count"], day["missing_count"], day["latency_sum_ns"], day["latency_count"],
                 day["latency_missing_start_count"], day["latency_domain_mismatch_count"], day["latency_invalid_count"],
                 day["latency_terminal_before_start_count"], day["source_high_water_seq"], complete, 1),
            )
            conn.execute("INSERT INTO bridge_session_latency_daily VALUES (?,?,?,?,?,?,?,?,?)",
                         (stream, peer, day_text, day["latency_sum_ns"], day["latency_count"], day["latency_missing_start_count"],
                          day["latency_domain_mismatch_count"], day["latency_invalid_count"], day["latency_terminal_before_start_count"]))
            conn.execute("INSERT INTO bridge_session_usage_coverage_daily VALUES (?,?,?,?,?,?,?,?,?)",
                         (stream, peer, day_text, day["terminal_count"], day["measured_count"], day["absent_count"],
                          day["unsupported_count"], day["unknown_count"], day["missing_count"]))
        for (stream, peer), first in first_seq.items():
            first_day_text = min((key[2] for key in days if key[:2] == (stream, peer)), default="1970-01-01")
            conn.execute("INSERT INTO bridge_session_usage_coverage_scope VALUES (?,?,?,?)", (stream, peer, first, first_day_text))
        conn.execute("COMMIT")
        return {"status": "OK", "delivery_count": delivery_count, "scope_count": len(scopes),
                "day_count": len(days), "complete": bool(complete), "chunk_size": chunk_size}
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
