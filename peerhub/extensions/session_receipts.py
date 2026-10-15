"""Session call/terminal evidence and writer-maintained monitoring projections.

All functions use the caller's transaction. Readers never invoke these writers.
Billing usage is deliberately not interpreted as final-request occupancy.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, TypeGuard, cast

from peerhub.extensions.usage_normalization import NORMALIZED_FIELDS, REPORTED_FIELDS, normalize_provider_usage


DDL = (
    """CREATE TABLE IF NOT EXISTS bridge_monitor_delivery_scope (
        stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
        not_started_count INTEGER NOT NULL CHECK(not_started_count>=0),
        may_have_started_count INTEGER NOT NULL CHECK(may_have_started_count>=0),
        started_count INTEGER NOT NULL CHECK(started_count>=0), terminal_count INTEGER NOT NULL CHECK(terminal_count>=0),
        measured_count INTEGER NOT NULL CHECK(measured_count>=0), absent_count INTEGER NOT NULL CHECK(absent_count>=0),
        unsupported_count INTEGER NOT NULL CHECK(unsupported_count>=0), unknown_count INTEGER NOT NULL CHECK(unknown_count>=0),
        missing_count INTEGER NOT NULL CHECK(missing_count>=0), delivery_coverage_start_seq INTEGER NOT NULL,
        delivery_high_water_seq INTEGER NOT NULL, delivery_complete INTEGER NOT NULL CHECK(delivery_complete IN (0,1)),
        telemetry_coverage_start_seq INTEGER NOT NULL, telemetry_high_water_seq INTEGER NOT NULL,
        telemetry_complete INTEGER NOT NULL CHECK(telemetry_complete IN (0,1)),
        latency_sum_ns INTEGER NOT NULL CHECK(latency_sum_ns>=0),
        latency_count INTEGER NOT NULL CHECK(latency_count>=0),
        latency_missing_start_count INTEGER NOT NULL CHECK(latency_missing_start_count>=0),
        latency_domain_mismatch_count INTEGER NOT NULL CHECK(latency_domain_mismatch_count>=0),
        latency_invalid_count INTEGER NOT NULL CHECK(latency_invalid_count>=0),
        latency_terminal_before_start_count INTEGER NOT NULL CHECK(latency_terminal_before_start_count>=0),
        semantics_version INTEGER NOT NULL CHECK(semantics_version>=1),
        PRIMARY KEY(stream_id,peer_id))""",
    "CREATE INDEX IF NOT EXISTS idx_monitor_delivery_peer ON bridge_monitor_delivery_scope(peer_id,stream_id)",
    """CREATE TABLE IF NOT EXISTS bridge_monitor_delivery_day (
        stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, terminal_day_utc TEXT NOT NULL,
        terminal_count INTEGER NOT NULL CHECK(terminal_count>=0), measured_count INTEGER NOT NULL CHECK(measured_count>=0),
        absent_count INTEGER NOT NULL CHECK(absent_count>=0), unsupported_count INTEGER NOT NULL CHECK(unsupported_count>=0),
        unknown_count INTEGER NOT NULL CHECK(unknown_count>=0), missing_count INTEGER NOT NULL CHECK(missing_count>=0),
        latency_sum_ns INTEGER NOT NULL CHECK(latency_sum_ns>=0), latency_count INTEGER NOT NULL CHECK(latency_count>=0),
        latency_missing_start_count INTEGER NOT NULL CHECK(latency_missing_start_count>=0),
        latency_domain_mismatch_count INTEGER NOT NULL CHECK(latency_domain_mismatch_count>=0),
        latency_invalid_count INTEGER NOT NULL CHECK(latency_invalid_count>=0),
        latency_terminal_before_start_count INTEGER NOT NULL CHECK(latency_terminal_before_start_count>=0),
        source_high_water_seq INTEGER NOT NULL CHECK(source_high_water_seq>=0),
        complete INTEGER NOT NULL CHECK(complete IN (0,1)), semantics_version INTEGER NOT NULL CHECK(semantics_version>=1),
        PRIMARY KEY(stream_id,peer_id,terminal_day_utc))""",
    "CREATE INDEX IF NOT EXISTS idx_monitor_delivery_day ON bridge_monitor_delivery_day(peer_id,terminal_day_utc DESC)",
    """CREATE TABLE IF NOT EXISTS bridge_session_monitor_stats (
        session_handle TEXT PRIMARY KEY REFERENCES bridge_session_history(session_handle),
        reuse_count INTEGER CHECK(reuse_count IS NULL OR reuse_count>=0),
        fresh_count INTEGER CHECK(fresh_count IS NULL OR fresh_count>=0),
        counts_complete INTEGER NOT NULL CHECK(counts_complete IN (0,1)), binding_json TEXT NOT NULL,
        latest_call_seq INTEGER REFERENCES bridge_evidence(seq), latest_telemetry_seq INTEGER REFERENCES bridge_evidence(seq))""",
    """CREATE TABLE IF NOT EXISTS bridge_session_monitor_scope (
        stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
        reuse_count INTEGER CHECK(reuse_count IS NULL OR reuse_count>=0),
        fresh_count INTEGER CHECK(fresh_count IS NULL OR fresh_count>=0),
        counts_complete INTEGER NOT NULL CHECK(counts_complete IN (0,1)), coverage_start_seq INTEGER NOT NULL,
        retained_session_count INTEGER CHECK(retained_session_count IS NULL OR retained_session_count>=0),
        retained_complete INTEGER NOT NULL DEFAULT 0 CHECK(retained_complete IN (0,1)),
        PRIMARY KEY(stream_id,peer_id))""",
    """CREATE TABLE IF NOT EXISTS bridge_session_latency_daily (
        stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, day_utc TEXT NOT NULL,
        latency_sum_ns INTEGER NOT NULL CHECK(latency_sum_ns BETWEEN 0 AND 9223372036854775807),
        latency_count INTEGER NOT NULL CHECK(latency_count BETWEEN 0 AND 9223372036854775807),
        missing_start_count INTEGER NOT NULL CHECK(missing_start_count>=0),
        domain_mismatch_count INTEGER NOT NULL CHECK(domain_mismatch_count>=0),
        invalid_ticks_count INTEGER NOT NULL CHECK(invalid_ticks_count>=0),
        terminal_before_start_count INTEGER NOT NULL CHECK(terminal_before_start_count>=0),
        PRIMARY KEY(stream_id,peer_id,day_utc))""",
    "CREATE INDEX IF NOT EXISTS idx_session_latency_peer_day ON bridge_session_latency_daily(peer_id,day_utc DESC)",
    "CREATE INDEX IF NOT EXISTS idx_session_latency_day ON bridge_session_latency_daily(day_utc)",
    """CREATE TABLE IF NOT EXISTS bridge_session_usage_coverage_daily (
        stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, day_utc TEXT NOT NULL,
        eligible_count INTEGER NOT NULL CHECK(eligible_count>=0), measured_count INTEGER NOT NULL CHECK(measured_count>=0),
        absent_count INTEGER NOT NULL CHECK(absent_count>=0), unsupported_count INTEGER NOT NULL CHECK(unsupported_count>=0),
        unknown_count INTEGER NOT NULL CHECK(unknown_count>=0), missing_count INTEGER NOT NULL CHECK(missing_count>=0),
        CHECK(eligible_count=measured_count+absent_count+unsupported_count+unknown_count+missing_count),
        PRIMARY KEY(stream_id,peer_id,day_utc))""",
    "CREATE INDEX IF NOT EXISTS idx_session_usage_coverage_day ON bridge_session_usage_coverage_daily(day_utc)",
    """CREATE TABLE IF NOT EXISTS bridge_session_usage_coverage_scope (
        stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, coverage_start_delivery_seq INTEGER NOT NULL,
        coverage_start_day TEXT NOT NULL, PRIMARY KEY(stream_id,peer_id))""",
    "CREATE TABLE IF NOT EXISTS bridge_session_monitor_meta (name TEXT PRIMARY KEY, version INTEGER NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_be_session_call_once ON bridge_evidence(delivery_id) WHERE kind='session_call'",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_be_session_telemetry_once ON bridge_evidence(delivery_id) WHERE kind='session_telemetry'",
    "CREATE INDEX IF NOT EXISTS idx_be_peer_call ON bridge_evidence(peer_id,seq DESC) WHERE kind='session_call'",
    "CREATE INDEX IF NOT EXISTS idx_be_scope_call ON bridge_evidence(stream_id,peer_id,seq DESC) WHERE kind='session_call'",
)


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


_SAFE_BINDING_VALUE = re.compile(r"[A-Za-z0-9._+-]{1,128}\Z")
_SECRET_LIKE_BINDING = re.compile(
    r"(?i)(?:\b(?:sk|pk|rk|ghp|gho|ghs|xox[abp])[-_][A-Za-z0-9_-]{8,}|"
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}|"
    r"\b(?:api[_-]?key|token|secret|password|credential)[_-][A-Za-z0-9_-]{8,}\b)"
)
_ADAPTER_VERSION = re.compile(r"adapter:(cc|cx|ag):v([0-9]{1,3})\Z")
PROCESS_CLOCK_DOMAIN = str(uuid.uuid4())
monotonic_ns = time.monotonic_ns
_MAX_TICK = 2**63 - 1


def _valid_observed_at(value: object) -> TypeGuard[int | float]:
    return type(value) in (int, float) and math.isfinite(cast(float, value))


def _valid_tick(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_TICK


_CERTAINTY_COLUMNS = {
    "NOT_STARTED": "not_started_count", "MAY_HAVE_STARTED": "may_have_started_count",
    "STARTED": "started_count", "TERMINAL": "terminal_count",
}


def _ensure_delivery_scope(conn: sqlite3.Connection, delivery: Mapping[str, Any] | sqlite3.Row,
                           certainty: str) -> None:
    stream, peer, seq = str(delivery["stream_id"]), str(delivery["peer_id"]), int(delivery["seq"])
    column = _CERTAINTY_COLUMNS[certainty]
    prior = int(conn.execute("SELECT COUNT(*) FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND seq<?",
                             (stream, peer, seq)).fetchone()[0])
    conn.execute(
        "INSERT OR IGNORE INTO bridge_monitor_delivery_scope(stream_id,peer_id,not_started_count,may_have_started_count,"
        "started_count,terminal_count,measured_count,absent_count,unsupported_count,unknown_count,missing_count,"
        "delivery_coverage_start_seq,delivery_high_water_seq,delivery_complete,telemetry_coverage_start_seq,"
        "telemetry_high_water_seq,telemetry_complete,latency_sum_ns,latency_count,latency_missing_start_count,"
        "latency_domain_mismatch_count,latency_invalid_count,latency_terminal_before_start_count,semantics_version) "
        f"VALUES (?,?,{','.join('?' for _ in range(22))})",
        (stream, peer, int(column == "not_started_count"), int(column == "may_have_started_count"),
         int(column == "started_count"), int(column == "terminal_count"), 0, 0, 0, 0, 0,
         seq, seq, int(prior == 0), 0, 0, int(prior == 0), 0, 0, 0, 0, 0, 0, 1),
    )


def record_delivery_created(conn: sqlite3.Connection, delivery: Mapping[str, Any] | sqlite3.Row) -> None:
    """Start the scope projection at the immutable delivery insertion boundary."""
    _ensure_delivery_scope(conn, delivery, "NOT_STARTED")
    conn.execute("UPDATE bridge_monitor_delivery_scope SET delivery_high_water_seq=MAX(delivery_high_water_seq,?) "
                 "WHERE stream_id=? AND peer_id=?", (delivery["seq"], delivery["stream_id"], delivery["peer_id"]))


def record_delivery_transition(conn: sqlite3.Connection, delivery: Mapping[str, Any] | sqlite3.Row,
                              old: str, new: str, *, observed_at: float) -> None:
    if old == new:
        return
    _ensure_delivery_scope(conn, delivery, old)
    old_col, new_col = _CERTAINTY_COLUMNS[old], _CERTAINTY_COLUMNS[new]
    changed = conn.execute(
        f"UPDATE bridge_monitor_delivery_scope SET {old_col}=MAX(0,{old_col}-1),{new_col}={new_col}+1,"
        "delivery_high_water_seq=MAX(delivery_high_water_seq,?) WHERE stream_id=? AND peer_id=?",
        (delivery["seq"], delivery["stream_id"], delivery["peer_id"]),
    )
    if changed.rowcount != 1:
        raise sqlite3.IntegrityError("delivery scope projection missing during certainty transition")
    if new == "TERMINAL":
        epoch = _terminal_epoch(delivery, observed_at)
        day = datetime.fromtimestamp(epoch, timezone.utc).date().isoformat()
        scope = conn.execute("SELECT delivery_complete FROM bridge_monitor_delivery_scope WHERE stream_id=? AND peer_id=?",
                             (delivery["stream_id"], delivery["peer_id"])).fetchone()
        complete = int(bool(scope[0])) if scope else 0
        conn.execute(
            "INSERT INTO bridge_monitor_delivery_day(stream_id,peer_id,terminal_day_utc,terminal_count,measured_count,"
            "absent_count,unsupported_count,unknown_count,missing_count,latency_sum_ns,latency_count,"
            "latency_missing_start_count,latency_domain_mismatch_count,latency_invalid_count,"
            "latency_terminal_before_start_count,source_high_water_seq,complete,semantics_version) "
            "VALUES (?,?,?,1,0,0,0,0,0,0,0,0,0,0,0,?,?,1) ON CONFLICT(stream_id,peer_id,terminal_day_utc) "
            "DO UPDATE SET terminal_count=terminal_count+1,source_high_water_seq=MAX(source_high_water_seq,excluded.source_high_water_seq),"
            "complete=MIN(complete,excluded.complete)",
            (delivery["stream_id"], delivery["peer_id"], day, delivery["seq"], complete),
        )
        retention_cutoff = (datetime.fromtimestamp(observed_at, timezone.utc).date() - timedelta(days=29)).isoformat()
        conn.execute("DELETE FROM bridge_monitor_delivery_day WHERE terminal_day_utc<?", (retention_cutoff,))


def terminal_timing(conn: sqlite3.Connection, delivery: Mapping[str, Any] | sqlite3.Row) -> dict[str, Any]:
    """Create an append-only terminal timing pair; never infer across process clocks."""
    end_tick = monotonic_ns()
    end_domain = PROCESS_CLOCK_DOMAIN
    row = conn.execute("SELECT detail FROM bridge_evidence WHERE delivery_id=? AND stream_id=? AND peer_id=? "
                       "AND kind='session_call' AND certainty='STARTED' ORDER BY seq DESC LIMIT 1",
                       (delivery["delivery_id"], delivery["stream_id"], delivery["peer_id"])).fetchone()
    start: dict[str, Any] | None = None
    if row is not None:
        try:
            detail: object = json.loads(row[0])
            detail_dict = cast("dict[str, Any]", detail) if isinstance(detail, dict) else {}
            raw_start = detail_dict.get("peerhub_timing")
            start = cast(dict[str, Any], raw_start) if isinstance(raw_start, dict) else {}
            if (type(detail_dict.get("schema_version")) is not int or detail_dict["schema_version"] != 2
                    or type(start.get("schema_version")) is not int or start["schema_version"] != 1):
                start = {}
        except (TypeError, ValueError):
            start = {}
    reason: str | None = None
    elapsed: int | None = None
    if start is None:
        reason = "missing_start"
    else:
        start_domain: object = start.get("clock_domain")
        start_tick: object = start.get("started_monotonic_ns")
        if not _valid_tick(start_tick) or not _valid_tick(end_tick) or not isinstance(start_domain, str) or not start_domain:
            reason = "invalid_ticks"
        elif start_domain != end_domain:
            reason = "domain_mismatch"
        elif end_tick < start_tick:
            reason = "terminal_before_start"
        else:
            elapsed = end_tick - start_tick
    return {"schema_version": 1, "clock_domain": end_domain, "terminal_monotonic_ns": end_tick,
            "elapsed_ns": elapsed, "latency_reason": reason}


def _terminal_epoch(delivery: Mapping[str, Any] | sqlite3.Row, fallback: float) -> float:
    """Use immutable terminal_at for daily attribution, with legacy fallback only."""
    value = delivery["terminal_at"]
    if isinstance(value, str):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            pass
    return fallback


def record_latency(conn: sqlite3.Connection, delivery: Mapping[str, Any] | sqlite3.Row, observed_at: float,
                   timing: Mapping[str, Any]) -> None:
    """Maintain a bounded 30-day UTC scope projection inside the terminal transaction."""
    started = conn.execute(
        "SELECT 1 FROM bridge_evidence WHERE delivery_id=? AND stream_id=? AND peer_id=? "
        "AND kind IN ('started','session_call') AND certainty='STARTED' LIMIT 1",
        (delivery["delivery_id"], delivery["stream_id"], delivery["peer_id"]),
    ).fetchone()
    if started is None:
        return
    stream_id, peer_id = str(delivery["stream_id"]), str(delivery["peer_id"])
    retention_day = datetime.fromtimestamp(observed_at, timezone.utc).date()
    terminal_day = datetime.fromtimestamp(_terminal_epoch(delivery, observed_at), timezone.utc).date()
    cutoff = (retention_day - timedelta(days=29)).isoformat()
    day_text = terminal_day.isoformat()
    elapsed, reason = timing.get("elapsed_ns"), timing.get("latency_reason")
    if reason is None and not _valid_tick(elapsed):
        reason = "invalid_ticks"
        elapsed = None
    reason_columns = {
        "missing_start": "missing_start_count", "domain_mismatch": "domain_mismatch_count",
        "invalid_ticks": "invalid_ticks_count", "terminal_before_start": "terminal_before_start_count",
    }
    if reason is not None and reason not in reason_columns:
        reason = "invalid_ticks"
    conn.execute(
        "INSERT INTO bridge_session_latency_daily(stream_id,peer_id,day_utc,latency_sum_ns,latency_count,"
        "missing_start_count,domain_mismatch_count,invalid_ticks_count,terminal_before_start_count) "
        "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(stream_id,peer_id,day_utc) DO UPDATE SET "
        "latency_sum_ns=latency_sum_ns+excluded.latency_sum_ns,latency_count=latency_count+excluded.latency_count,"
        "missing_start_count=missing_start_count+excluded.missing_start_count,"
        "domain_mismatch_count=domain_mismatch_count+excluded.domain_mismatch_count,"
        "invalid_ticks_count=invalid_ticks_count+excluded.invalid_ticks_count,"
        "terminal_before_start_count=terminal_before_start_count+excluded.terminal_before_start_count",
        (stream_id, peer_id, day_text, elapsed if reason is None else 0, int(reason is None),
         int(reason == "missing_start"), int(reason == "domain_mismatch"),
         int(reason == "invalid_ticks"), int(reason == "terminal_before_start")),
    )
    latency_sum = elapsed if reason is None else 0
    latency_count = int(reason is None)
    reason_counter = {"missing_start": "latency_missing_start_count",
                      "domain_mismatch": "latency_domain_mismatch_count",
                      "invalid_ticks": "latency_invalid_count",
                      "terminal_before_start": "latency_terminal_before_start_count"}
    reason_values = {name: int(reason == key) for key, name in reason_counter.items()}
    scope_update = conn.execute(
        "UPDATE bridge_monitor_delivery_scope SET latency_sum_ns=latency_sum_ns+?,latency_count=latency_count+?,"
        "latency_missing_start_count=latency_missing_start_count+?,"
        "latency_domain_mismatch_count=latency_domain_mismatch_count+?,latency_invalid_count=latency_invalid_count+?,"
        "latency_terminal_before_start_count=latency_terminal_before_start_count+? WHERE stream_id=? AND peer_id=?",
        (latency_sum, latency_count, reason_values["latency_missing_start_count"],
         reason_values["latency_domain_mismatch_count"], reason_values["latency_invalid_count"],
         reason_values["latency_terminal_before_start_count"], stream_id, peer_id),
    )
    day_update = conn.execute(
        "UPDATE bridge_monitor_delivery_day SET latency_sum_ns=latency_sum_ns+?,latency_count=latency_count+?,"
        "latency_missing_start_count=latency_missing_start_count+?,"
        "latency_domain_mismatch_count=latency_domain_mismatch_count+?,latency_invalid_count=latency_invalid_count+?,"
        "latency_terminal_before_start_count=latency_terminal_before_start_count+? WHERE stream_id=? AND peer_id=? AND terminal_day_utc=?",
        (latency_sum, latency_count, reason_values["latency_missing_start_count"],
         reason_values["latency_domain_mismatch_count"], reason_values["latency_invalid_count"],
         reason_values["latency_terminal_before_start_count"], stream_id, peer_id, day_text),
    )
    if scope_update.rowcount != 1 or day_update.rowcount != 1:
        raise sqlite3.IntegrityError("latency projection missing during terminal receipt")
    conn.execute("DELETE FROM bridge_session_latency_daily WHERE day_utc<?", (cutoff,))


def telemetry_coverage_state(conn: sqlite3.Connection, delivery: Mapping[str, Any] | sqlite3.Row) -> str:
    rows = conn.execute("SELECT * FROM bridge_evidence WHERE delivery_id=? AND kind='session_telemetry'",
                        (delivery["delivery_id"],)).fetchall()
    if not rows:
        return "MISSING"
    if (len(rows) != 1 or delivery["certainty"] != "TERMINAL" or rows[0]["certainty"] != "TERMINAL"
            or rows[0]["stream_id"] != delivery["stream_id"] or rows[0]["peer_id"] != delivery["peer_id"]):
        return "UNKNOWN"
    accepted = snapshot(delivery)
    if accepted is None:
        return "UNKNOWN"
    try:
        detail_raw: object = json.loads(rows[0]["detail"])
    except (ValueError, TypeError):
        return "UNKNOWN"
    if not isinstance(detail_raw, dict):
        return "UNKNOWN"
    detail = cast("dict[str, Any]", detail_raw)
    expected_keys = {"handle", "generation", "observed_at", "last_input_tokens", "context_window", "context_pct",
                     "source", "window_reported_at", "configured_model", "resolved_model", "schema_version",
                     "provider_kind", "captured_at", "adapter_version", "provider_cli_version", "configured_binding",
                     "usage", "steps", "step_coverage"}
    if (set(detail) != expected_keys or type(detail.get("schema_version")) is not int or detail["schema_version"] != 3
            or detail.get("handle") != accepted.get("handle")
            or type(detail.get("generation")) is not int or detail["generation"] != accepted.get("generation")):
        return "UNKNOWN"
    identity = conn.execute("SELECT runtime_kind FROM bridge_session_history WHERE session_handle=?",
                            (accepted["handle"],)).fetchone()
    provider_kind = identity[0] if identity and identity[0] in ("cc", "cx", "ag") else "unknown"
    usage_raw = detail.get("usage")
    if not isinstance(usage_raw, dict):
        return "UNKNOWN"
    usage = cast("dict[str, Any]", usage_raw)
    candidate = {key: usage.get(key) for key in ("schema_version", "scope", "source", "reported", "normalized", "coverage")}
    normalized = _provider_usage(candidate, provider_kind)
    if normalized != usage:
        return "UNKNOWN"
    return str(usage["coverage"]["state"])


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
    if not _valid_tick(start_tick) or not _valid_tick(end_tick) or not isinstance(start_domain, str) or not isinstance(end_domain, str):
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
                state_column = _CERTAINTY_COLUMNS.get(str(delivery["certainty"]))
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


def record_usage_coverage(conn: sqlite3.Connection, delivery: Mapping[str, Any] | sqlite3.Row, observed_at: float) -> None:
    state = telemetry_coverage_state(conn, delivery)
    column = {"MEASURED": "measured_count", "ABSENT": "absent_count", "UNSUPPORTED": "unsupported_count",
              "UNKNOWN": "unknown_count", "MISSING": "missing_count"}[state]
    retention_day = datetime.fromtimestamp(observed_at, timezone.utc).date()
    day = datetime.fromtimestamp(_terminal_epoch(delivery, observed_at), timezone.utc).date()
    conn.execute("INSERT OR IGNORE INTO bridge_session_usage_coverage_scope VALUES (?,?,?,?)",
                 (delivery["stream_id"], delivery["peer_id"], delivery["seq"], day.isoformat()))
    values = {name: int(name == column) for name in
              ("measured_count", "absent_count", "unsupported_count", "unknown_count", "missing_count")}
    delivery_seq = int(delivery["seq"])
    terminal_seq = int(conn.execute("SELECT COALESCE(MIN(seq),0) FROM bridge_evidence WHERE delivery_id=? AND kind='terminal'",
                                    (delivery["delivery_id"],)).fetchone()[0])
    source_seq = int(conn.execute("SELECT COALESCE(MAX(seq),0) FROM bridge_evidence WHERE delivery_id=? AND kind IN ('terminal','session_telemetry')",
                                  (delivery["delivery_id"],)).fetchone()[0])
    if terminal_seq == 0 or source_seq == 0:
        raise sqlite3.IntegrityError("terminal evidence missing during usage coverage projection")
    for name in values:
        scope_update = conn.execute(f"UPDATE bridge_monitor_delivery_scope SET {name}={name}+? WHERE stream_id=? AND peer_id=?",
                                    (values[name], delivery["stream_id"], delivery["peer_id"]))
        day_update = conn.execute(f"UPDATE bridge_monitor_delivery_day SET {name}={name}+? WHERE stream_id=? AND peer_id=? AND terminal_day_utc=?",
                                  (values[name], delivery["stream_id"], delivery["peer_id"], day.isoformat()))
        if scope_update.rowcount != 1 or day_update.rowcount != 1:
            raise sqlite3.IntegrityError("usage coverage projection missing during terminal receipt")
    scope_update = conn.execute("UPDATE bridge_monitor_delivery_scope SET telemetry_coverage_start_seq="
                                "CASE WHEN telemetry_coverage_start_seq=0 THEN ? ELSE MIN(telemetry_coverage_start_seq,?) END,"
                                "telemetry_high_water_seq=MAX(telemetry_high_water_seq,?) WHERE stream_id=? AND peer_id=?",
                                (terminal_seq, terminal_seq, source_seq, delivery["stream_id"], delivery["peer_id"]))
    if scope_update.rowcount != 1:
        raise sqlite3.IntegrityError("usage coverage scope missing during terminal receipt")
    conn.execute("UPDATE bridge_monitor_delivery_day SET source_high_water_seq=MAX(source_high_water_seq,?),"
                 "complete=MIN(complete,(SELECT telemetry_complete FROM bridge_monitor_delivery_scope WHERE stream_id=? AND peer_id=?)) "
                 "WHERE stream_id=? AND peer_id=? AND terminal_day_utc=?",
                 (delivery_seq, delivery["stream_id"], delivery["peer_id"], delivery["stream_id"], delivery["peer_id"], day.isoformat()))
    conn.execute(
        "INSERT INTO bridge_session_usage_coverage_daily(stream_id,peer_id,day_utc,eligible_count,"
        "measured_count,absent_count,unsupported_count,unknown_count,missing_count) VALUES (?,?,?,1,?,?,?,?,?) "
        "ON CONFLICT(stream_id,peer_id,day_utc) DO UPDATE SET eligible_count=eligible_count+1,"
        "measured_count=measured_count+excluded.measured_count,absent_count=absent_count+excluded.absent_count,"
        "unsupported_count=unsupported_count+excluded.unsupported_count,unknown_count=unknown_count+excluded.unknown_count,"
        "missing_count=missing_count+excluded.missing_count",
        (delivery["stream_id"], delivery["peer_id"], day.isoformat(), values["measured_count"],
         values["absent_count"], values["unsupported_count"], values["unknown_count"], values["missing_count"]),
    )
    cutoff = (retention_day - timedelta(days=29)).isoformat()
    conn.execute("DELETE FROM bridge_session_usage_coverage_daily WHERE day_utc<?", (cutoff,))


def repair_latest_telemetry_pointers(conn: sqlite3.Connection) -> None:
    """One-time projection repair; only identity-matched terminal v3 receipts qualify."""
    name = "latest_terminal_telemetry_v1"
    conn.execute("INSERT OR IGNORE INTO bridge_session_monitor_meta(name,version) VALUES (?,0)", (name,))
    if conn.execute("SELECT version FROM bridge_session_monitor_meta WHERE name=?", (name,)).fetchone()[0] >= 1:
        return
    candidates = conn.execute(
        "SELECT e.seq,d.session_handle,CAST(CASE WHEN json_valid(e.detail) "
        "THEN json_extract(e.detail,'$.observed_at') END AS REAL) observed_at "
        "FROM bridge_evidence e JOIN bridge_deliveries d ON d.delivery_id=e.delivery_id "
        "JOIN bridge_session_history h ON h.session_handle=d.session_handle "
        "WHERE e.kind='session_telemetry' AND e.certainty='TERMINAL' AND d.certainty='TERMINAL' "
        "AND e.stream_id=d.stream_id AND e.peer_id=d.peer_id "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail) END='object' "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.schema_version') END='integer' "
        "AND CASE WHEN json_valid(e.detail) THEN json_extract(e.detail,'$.schema_version') END=3 "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.handle') END='text' "
        "AND CASE WHEN json_valid(e.detail) THEN json_extract(e.detail,'$.handle') END=d.session_handle "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.generation') END='integer' "
        "AND CASE WHEN json_valid(e.detail) THEN json_extract(e.detail,'$.generation') END=d.session_generation "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.observed_at') END IN ('integer','real') "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.provider_kind') END='text' "
        "AND CASE WHEN json_valid(e.detail) THEN json_extract(e.detail,'$.provider_kind') END="
        "CASE WHEN h.runtime_kind IN ('cc','cx','ag') THEN h.runtime_kind ELSE 'unknown' END "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.configured_binding') END='object' "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.steps') END='array' "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.step_coverage') END='object' "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.usage') END='object' "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.usage.schema_version') END='integer' "
        "AND CASE WHEN json_valid(e.detail) THEN json_extract(e.detail,'$.usage.schema_version') END=1 "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.usage.reported') END='object' "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.usage.normalized') END='object' "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.usage.field_evidence') END='object' "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.usage.scope') END IN ('text','null') "
        "AND (CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.usage.scope') END='null' OR "
        "CASE WHEN json_valid(e.detail) THEN json_extract(e.detail,'$.usage.scope') END IN ('session_cumulative','delivery')) "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.usage.source') END IN ('text','null') "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.usage.attribution') END='text' "
        "AND CASE WHEN json_valid(e.detail) THEN json_extract(e.detail,'$.usage.attribution') END IN ('delivery','unattributed') "
        "AND CASE WHEN json_valid(e.detail) THEN json_extract(e.detail,'$.usage.attribution') END="
        "CASE WHEN CASE WHEN json_valid(e.detail) THEN json_extract(e.detail,'$.usage.scope') END='delivery' "
        "THEN 'delivery' ELSE 'unattributed' END "
        "AND CASE WHEN json_valid(e.detail) THEN json_extract(e.detail,'$.usage.source') END IS "
        "CASE h.runtime_kind WHEN 'cc' THEN 'cc.result.usage' WHEN 'cx' THEN 'cx.turn.completed.usage' "
        "WHEN 'ag' THEN 'ag.result.usage' ELSE NULL END "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.usage.coverage.state') END='text' "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.usage.coverage.reason') END IN ('text','null') "
        "AND CASE WHEN json_valid(e.detail) THEN json_extract(e.detail,'$.usage.coverage.state') END IN ('MEASURED','ABSENT','UNSUPPORTED','UNKNOWN')")
    latest: dict[str, tuple[float, int]] = {}
    while batch := candidates.fetchmany(500):
        for row in batch:
            candidate = (row["observed_at"], row["seq"])
            if row["session_handle"] not in latest or candidate > latest[row["session_handle"]]:
                latest[row["session_handle"]] = candidate
    last_handle = ""
    while batch := conn.execute("SELECT session_handle,latest_telemetry_seq FROM bridge_session_monitor_stats "
                                "WHERE session_handle>? ORDER BY session_handle LIMIT 500", (last_handle,)).fetchall():
        for row in batch:
            candidate = latest.get(row["session_handle"])
            pointer = candidate[1] if candidate else None
            if row["latest_telemetry_seq"] != pointer:
                conn.execute("UPDATE bridge_session_monitor_stats SET latest_telemetry_seq=? WHERE session_handle=?",
                             (pointer, row["session_handle"]))
        last_handle = batch[-1]["session_handle"]
    conn.execute("UPDATE bridge_session_monitor_meta SET version=1 WHERE name=?", (name,))


def _safe_binding(value: object) -> str | None:
    if (isinstance(value, str) and _SAFE_BINDING_VALUE.fullmatch(value)
            and not _SECRET_LIKE_BINDING.search(value)):
        return value
    return None


def unknown_telemetry() -> dict[str, Any]:
    return dict(last_input_tokens=None, context_window=None, context_pct=None, source="unknown", quota=None)


_PROVIDER_USAGE_CONTRACT = {
    "cc": ("session_cumulative", "cc.result.usage"),
    "cx": ("session_cumulative", "cx.turn.completed.usage"),
    "ag": ("session_cumulative", "ag.result.usage"),
}
_SAFE_COVERAGE_REASON = re.compile(r"[a-z_]{1,64}\Z")


def _provider_usage(candidate: object, provider_kind: str) -> dict[str, Any]:
    """Validate the adapter candidate again at the fenced persistence boundary."""
    def unknown(reason: str) -> dict[str, Any]:
        return {"schema_version": 1, "scope": None, "source": None, "attribution": "unattributed",
                "reported": {}, "normalized": {}, "field_evidence": {},
                "coverage": {"state": "UNKNOWN", "reason": reason}}

    if candidate is None:
        return unknown("not_captured")
    if not isinstance(candidate, dict):
        return unknown("invalid_candidate_shape")
    candidate = cast("dict[str, Any]", candidate)
    if set(candidate) != {"schema_version", "scope", "source", "reported", "normalized", "coverage"}:
        return unknown("invalid_candidate_shape")
    schema_version = candidate.get("schema_version")
    if (provider_kind not in _PROVIDER_USAGE_CONTRACT or isinstance(schema_version, bool)
            or schema_version != 1):
        return unknown("unsupported_provider_schema")
    expected_scope, expected_source = _PROVIDER_USAGE_CONTRACT[provider_kind]
    scope, source = candidate.get("scope"), candidate.get("source")
    reported, normalized, coverage = candidate.get("reported"), candidate.get("normalized"), candidate.get("coverage")
    if scope is None and source is None:
        if reported != {} or normalized != {} or not isinstance(coverage, dict):
            return unknown("invalid_unknown_candidate")
        coverage = cast("dict[str, Any]", coverage)
        if coverage.get("state") != "UNKNOWN":
            return unknown("invalid_unknown_candidate")
        reason = coverage.get("reason")
        if not isinstance(reason, str) or _SAFE_COVERAGE_REASON.fullmatch(reason) is None:
            return unknown("invalid_coverage_reason")
        return unknown(reason)
    if scope != expected_scope or source != expected_source:
        return unknown("provider_scope_mismatch")
    if not isinstance(reported, dict) or not isinstance(normalized, dict) or not isinstance(coverage, dict):
        return unknown("invalid_candidate_shape")
    reported = cast("dict[str, Any]", reported)
    normalized = cast("dict[str, Any]", normalized)
    coverage = cast("dict[str, Any]", coverage)
    if set(reported) - REPORTED_FIELDS[provider_kind] or set(normalized) - NORMALIZED_FIELDS:
        return unknown("unsupported_usage_field")
    for values in (reported, normalized):
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > 2**63 - 1 for v in values.values()):
            return unknown("invalid_usage_counter")
    expected_normalized = normalize_provider_usage(provider_kind, reported)
    if normalized != expected_normalized:
        return unknown("normalization_mismatch")
    state, reason = coverage.get("state"), coverage.get("reason")
    if state not in ("MEASURED", "ABSENT", "UNSUPPORTED", "UNKNOWN"):
        return unknown("invalid_coverage_state")
    if reason is not None and (not isinstance(reason, str) or _SAFE_COVERAGE_REASON.fullmatch(reason) is None):
        return unknown("invalid_coverage_reason")
    if state == "MEASURED" and (not reported or reason is not None):
        return unknown("incomplete_measured_candidate")
    if state != "MEASURED" and (reported or normalized):
        return unknown("non_measured_candidate_has_counters")
    attribution = "delivery" if scope == "delivery" else "unattributed"
    return {"schema_version": 1, "scope": scope, "source": source, "attribution": attribution,
            "reported": dict(reported), "normalized": dict(normalized), "field_evidence": {},
            "coverage": {"state": state, "reason": reason}}


def validate_provider_usage(candidate: object, provider_kind: str) -> dict[str, Any]:
    """Shared versioned receipt validator for writers and read-only monitor projections."""
    return _provider_usage(candidate, provider_kind)


def binding_attributes(runtime: object) -> dict[str, Any]:
    method = getattr(runtime, "session_binding", None)
    raw: Any = method() if callable(method) else {}
    values = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    return {key: _safe_binding(values.get(key)) for key in ("profile", "model", "effort")} | {
        "writable": values.get("writable") if isinstance(values.get("writable"), bool) else None}


def historical_counts(conn: sqlite3.Connection, stream: str, peer: str, generation: int | None = None) -> tuple[int | None, int | None, int]:
    """Only recorded mode and exact terminal attribution can prove old counts."""
    query = "SELECT * FROM bridge_deliveries WHERE stream_id=? AND peer_id=?"
    args: tuple[Any, ...] = (stream, peer)
    if generation is not None:
        query += " AND session_generation=?"
        args += (generation,)
    deliveries = conn.execute(query, args).fetchall()
    if not deliveries:
        return None, None, 0  # an old mapping is not evidence of zero historical calls
    reuse = fresh = 0
    for d in deliveries:
        confirmed = d["certainty"] in ("STARTED", "TERMINAL") or conn.execute(
            "SELECT 1 FROM bridge_evidence WHERE delivery_id=? AND kind='started' LIMIT 1", (d["delivery_id"],)).fetchone()
        if not confirmed:
            continue
        receipts = conn.execute("SELECT detail FROM bridge_evidence WHERE delivery_id=? AND kind='terminal'", (d["delivery_id"],)).fetchall()
        if len(receipts) != 1 or d["session_generation"] is None or d["external_session_id"] is None:
            return None, None, 0
        try:
            receipt = json.loads(receipts[0][0])
            mode = receipt.get("effective_mode")
        except (ValueError, TypeError, AttributeError):
            return None, None, 0
        if mode not in ("fresh", "resumed"):
            return None, None, 0
        reuse += int(mode == "resumed")
        fresh += int(mode == "fresh")
    return reuse, fresh, 1


def historical_counts_by_generation(conn: sqlite3.Connection, stream: str, peer: str) -> tuple[dict[int, tuple[int | None, int | None, int]], tuple[int | None, int | None, int]]:
    """Rebuild one scope from a bounded indexed scan, not per-session queries."""
    rows = conn.execute(
        "SELECT d.session_generation,d.external_session_id,d.certainty,d.delivery_id,"
        "SUM(CASE WHEN e.kind='terminal' THEN 1 ELSE 0 END) terminal_count,"
        "MAX(CASE WHEN e.kind='started' THEN 1 ELSE 0 END) has_started,"
        "MAX(CASE WHEN e.kind='terminal' AND json_valid(e.detail) THEN json_extract(e.detail,'$.effective_mode') END) mode "
        "FROM bridge_deliveries d LEFT JOIN bridge_evidence e ON e.delivery_id=d.delivery_id "
        "WHERE d.stream_id=? AND d.peer_id=? GROUP BY d.delivery_id",
        (stream, peer),
    ).fetchall()
    if not rows:
        return {}, (None, None, 0)
    per_generation: dict[int, list[int | bool]] = {}
    scope = [0, 0]
    incomplete_generations: set[int] = set()
    scope_incomplete = False
    for row in rows:
        if row[0] is not None:
            per_generation.setdefault(row[0], [0, 0])
        if row[2] not in ("STARTED", "TERMINAL") and not row[5]:
            continue
        generation = row[0]
        if row[4] != 1 or generation is None or row[1] is None or row[6] not in ("fresh", "resumed"):
            scope_incomplete = True
            if generation is not None:
                incomplete_generations.add(generation)
            continue
        counts = per_generation.setdefault(generation, [0, 0])
        index = 0 if row[6] == "resumed" else 1
        counts[index] = int(counts[index]) + 1
        scope[index] += 1
    result: dict[int, tuple[int | None, int | None, int]] = {
        generation: (None, None, 0) if generation in incomplete_generations else (int(counts[0]), int(counts[1]), 1)
        for generation, counts in per_generation.items()
    }
    for generation in incomplete_generations:
        result[generation] = (None, None, 0)
    return result, (None, None, 0) if scope_incomplete else (int(scope[0]), int(scope[1]), 1)


def initialize_stats(conn: sqlite3.Connection, mapping: Mapping[str, Any], binding: dict[str, Any], *, legacy: bool = False,
                     historical: tuple[int | None, int | None, int] | None = None,
                     historical_scope: tuple[int | None, int | None, int] | None = None,
                     history_created: bool = False) -> None:
    stream, peer, handle = mapping["stream_id"], mapping["peer_id"], mapping["session_handle"]
    stats = historical if historical is not None else historical_counts(conn, stream, peer, mapping["session_generation"]) if legacy else (0, 0, 1)
    conn.execute("INSERT INTO bridge_session_monitor_stats(session_handle,reuse_count,fresh_count,counts_complete,binding_json) VALUES (?,?,?,?,?)",
                 (handle, *stats, dumps(binding)))
    scope = historical_scope if historical_scope is not None else historical_counts(conn, stream, peer) if legacy else (0, 0, 1)
    coverage = conn.execute("SELECT COALESCE(MAX(seq),0) FROM bridge_evidence").fetchone()[0] if legacy else 0
    retained = 1 if history_created else None
    conn.execute("INSERT OR IGNORE INTO bridge_session_monitor_scope(stream_id,peer_id,reuse_count,fresh_count,counts_complete,"
                 "coverage_start_seq,retained_session_count,retained_complete) VALUES (?,?,?,?,?,?,?,?)",
                 (stream, peer, *scope, coverage, retained, int(history_created and not legacy)))
    if history_created:
        if conn.execute("SELECT changes()").fetchone()[0] == 0:
            conn.execute("UPDATE bridge_session_monitor_scope SET retained_session_count=retained_session_count+1 "
                         "WHERE stream_id=? AND peer_id=? AND retained_session_count IS NOT NULL", (stream, peer))


def snapshot(delivery: Mapping[str, Any] | sqlite3.Row) -> dict[str, Any] | None:
    raw = delivery["session_snapshot_json"]
    if not raw:
        return None
    parsed: object = json.loads(raw)
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else None


def call(conn: sqlite3.Connection, delivery: Mapping[str, Any] | sqlite3.Row, now: float) -> None:
    """First confirmed STARTED only; old started calls never receive new ordering."""
    if delivery["certainty"] in ("STARTED", "TERMINAL"):
        return
    accepted = snapshot(delivery)
    if accepted is None:
        # Late confirmation of an unattributable old call must invalidate totals,
        # rather than manufacture a mode or silently keep a known-zero count.
        high = conn.execute("SELECT COALESCE(MAX(seq),0) FROM bridge_evidence").fetchone()[0]
        conn.execute("INSERT INTO bridge_session_monitor_scope(stream_id,peer_id,reuse_count,fresh_count,counts_complete,coverage_start_seq) "
                     "VALUES (?,?,NULL,NULL,0,?) ON CONFLICT(stream_id,peer_id) "
                     "DO UPDATE SET reuse_count=NULL,fresh_count=NULL,counts_complete=0",
                     (delivery["stream_id"], delivery["peer_id"], high))
        conn.execute("UPDATE bridge_session_monitor_stats SET reuse_count=NULL,fresh_count=NULL,counts_complete=0 WHERE session_handle IN "
                     "(SELECT session_handle FROM bridge_session_history WHERE stream_id=? AND peer_id=? AND (? IS NULL OR session_generation=?))",
                     (delivery["stream_id"], delivery["peer_id"], delivery["session_generation"], delivery["session_generation"]))
        return
    if conn.execute("SELECT 1 FROM bridge_evidence WHERE delivery_id=? AND kind='session_call'", (delivery["delivery_id"],)).fetchone():
        return
    detail = {key: accepted.get(key) for key in ("handle", "generation", "mode", "reason", "prior_handle")}
    detail["schema_version"] = 2
    detail["started_at"] = now
    detail["peerhub_timing"] = {"schema_version": 1, "clock_domain": PROCESS_CLOCK_DOMAIN,
                                "started_monotonic_ns": monotonic_ns()}
    seq = conn.execute("INSERT INTO bridge_evidence(delivery_id,stream_id,peer_id,kind,certainty,detail) VALUES (?,?,?,'session_call','STARTED',?)",
                       (delivery["delivery_id"], delivery["stream_id"], delivery["peer_id"], dumps(detail))).lastrowid
    reuse, fresh = int(accepted["mode"] == "resumed"), int(accepted["mode"] == "fresh")
    conn.execute("UPDATE bridge_session_monitor_stats SET reuse_count=reuse_count+?,fresh_count=fresh_count+?,latest_call_seq=? WHERE session_handle=?",
                 (reuse, fresh, seq, accepted["handle"]))
    conn.execute("UPDATE bridge_session_monitor_scope SET reuse_count=reuse_count+?,fresh_count=fresh_count+? WHERE stream_id=? AND peer_id=?",
                 (reuse, fresh, delivery["stream_id"], delivery["peer_id"]))


def telemetry(conn: sqlite3.Connection, delivery: Mapping[str, Any] | sqlite3.Row, now: float,
              provider_candidate: object = None) -> None:
    accepted = snapshot(delivery)
    if accepted is None or conn.execute("SELECT 1 FROM bridge_evidence WHERE delivery_id=? AND kind='session_telemetry'", (delivery["delivery_id"],)).fetchone():
        return
    binding = conn.execute("SELECT binding_json FROM bridge_session_monitor_stats WHERE session_handle=?", (accepted["handle"],)).fetchone()
    identity = conn.execute("SELECT runtime_kind,adapter_fingerprint FROM bridge_session_history WHERE session_handle=?",
                            (accepted["handle"],)).fetchone()
    raw_binding: object = json.loads(binding[0]) if binding else {}
    if not isinstance(raw_binding, dict):
        raw_binding = {}
    raw_binding = cast("dict[str, Any]", raw_binding)
    binding_detail = {key: _safe_binding(raw_binding.get(key)) for key in ("profile", "model", "effort")}
    provider_kind = identity["runtime_kind"] if identity and identity["runtime_kind"] in ("cc", "cx", "ag") else "unknown"
    fingerprint = identity["adapter_fingerprint"] if identity else None
    version_match = _ADAPTER_VERSION.fullmatch(fingerprint) if isinstance(fingerprint, str) else None
    adapter_version = version_match.group(2) if version_match and version_match.group(1) == provider_kind else None
    detail = dict(handle=accepted["handle"], generation=accepted["generation"], observed_at=now,
                  last_input_tokens=None, context_window=None, context_pct=None, source="unknown",
                  window_reported_at=None, configured_model=binding_detail["model"], resolved_model=None, schema_version=3,
                  provider_kind=provider_kind, captured_at=now, adapter_version=adapter_version,
                  provider_cli_version=None, configured_binding=binding_detail,
                  usage=_provider_usage(provider_candidate, provider_kind),
                  steps=[], step_coverage={"state": "UNKNOWN", "measured": 0, "dropped": 0})
    seq = conn.execute("INSERT INTO bridge_evidence(delivery_id,stream_id,peer_id,kind,certainty,detail) VALUES (?,?,?,'session_telemetry',?,?)",
                       (delivery["delivery_id"], delivery["stream_id"], delivery["peer_id"], delivery["certainty"], dumps(detail))).lastrowid
    current = conn.execute("SELECT latest_telemetry_seq FROM bridge_session_monitor_stats WHERE session_handle=?",
                           (accepted["handle"],)).fetchone()
    if current is None:
        raise sqlite3.IntegrityError("session telemetry projection missing")
    pointer = current[0]
    should_advance = pointer is None
    if pointer is not None:
        old = conn.execute("SELECT e.kind,e.certainty,e.stream_id,e.peer_id,e.detail,d.certainty,d.stream_id,d.peer_id,"
                           "d.session_handle,d.session_generation FROM bridge_evidence e JOIN bridge_deliveries d "
                           "ON d.delivery_id=e.delivery_id WHERE e.seq=?", (pointer,)).fetchone()
        valid_old = False
        old_time: object = None
        if (old is not None and old["kind"] == "session_telemetry" and old["certainty"] == "TERMINAL"
                and old["certainty"] == old[5] and old["stream_id"] == old[6] == delivery["stream_id"]
                and old["peer_id"] == old[7] == delivery["peer_id"] and old["session_handle"] == accepted["handle"]
                and old["session_generation"] == accepted["generation"]):
            try:
                parsed: object = json.loads(old["detail"])
                old_detail = cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {}
                if (old_detail.get("schema_version") == 3
                        and not isinstance(old_detail.get("schema_version"), bool)
                        and old_detail.get("handle") == accepted["handle"]
                        and old_detail.get("generation") == accepted["generation"]
                        and isinstance(old_detail.get("usage"), dict)
                        and old_detail["usage"].get("schema_version") == 1):
                    old_time = old_detail.get("observed_at")
                    valid_old = _valid_observed_at(old_time)
            except (TypeError, ValueError):
                pass
        should_advance = not valid_old or (float(now), seq) > (float(cast(int | float, old_time)), pointer)
    if should_advance:
        conn.execute("UPDATE bridge_session_monitor_stats SET latest_telemetry_seq=? WHERE session_handle=?",
                     (seq, accepted["handle"]))
