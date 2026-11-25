"""Schema, transactional maintenance for delivery monitoring projections."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, cast

from peerhub.core.peer_kinds import BUILTIN_PEER_KINDS
from peerhub.extensions.session_receipts import dumps, snapshot, valid_tick
from peerhub.extensions.usage_normalization import validate_provider_usage


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


CERTAINTY_COLUMNS = {
    "NOT_STARTED": "not_started_count", "MAY_HAVE_STARTED": "may_have_started_count",
    "STARTED": "started_count", "TERMINAL": "terminal_count",
}


def _ensure_delivery_scope(conn: sqlite3.Connection, delivery: Mapping[str, Any] | sqlite3.Row,
                           certainty: str) -> None:
    stream, peer, seq = str(delivery["stream_id"]), str(delivery["peer_id"]), int(delivery["seq"])
    column = CERTAINTY_COLUMNS[certainty]
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
    old_col, new_col = CERTAINTY_COLUMNS[old], CERTAINTY_COLUMNS[new]
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
    if reason is None and not valid_tick(elapsed):
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
    provider_kind = identity[0] if identity and identity[0] in BUILTIN_PEER_KINDS else "unknown"
    usage_raw = detail.get("usage")
    if not isinstance(usage_raw, dict):
        return "UNKNOWN"
    usage = cast("dict[str, Any]", usage_raw)
    candidate = {key: usage.get(key) for key in ("schema_version", "scope", "source", "reported", "normalized", "coverage")}
    normalized = validate_provider_usage(candidate, provider_kind)
    if normalized != usage:
        return "UNKNOWN"
    return str(usage["coverage"]["state"])


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
