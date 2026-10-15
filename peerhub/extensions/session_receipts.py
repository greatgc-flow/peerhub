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
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, TypeGuard, cast

from peerhub.extensions.usage_normalization import NORMALIZED_FIELDS, REPORTED_FIELDS, normalize_provider_usage


DDL = (
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


def terminal_timing(conn: sqlite3.Connection, delivery: Mapping[str, Any] | sqlite3.Row) -> dict[str, Any]:
    """Create an append-only terminal timing pair; never infer across process clocks."""
    end_tick = monotonic_ns()
    end_domain = PROCESS_CLOCK_DOMAIN
    row = conn.execute("SELECT detail FROM bridge_evidence WHERE delivery_id=? AND kind='session_call'",
                       (delivery["delivery_id"],)).fetchone()
    start: dict[str, Any] | None = None
    if row is not None:
        try:
            detail: object = json.loads(row[0])
            detail_dict = cast("dict[str, Any]", detail) if isinstance(detail, dict) else {}
            raw_start = detail_dict.get("peerhub_timing")
            start = cast("dict[str, Any]", raw_start) if isinstance(raw_start, dict) else None
        except (TypeError, ValueError):
            start = None
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


def record_latency(conn: sqlite3.Connection, stream_id: str, peer_id: str, observed_at: float,
                   timing: Mapping[str, Any]) -> None:
    """Maintain a bounded 30-day UTC scope projection inside the terminal transaction."""
    day = datetime.fromtimestamp(observed_at, timezone.utc).date()
    cutoff = (day - timedelta(days=29)).isoformat()
    day_text = day.isoformat()
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
    conn.execute("DELETE FROM bridge_session_latency_daily WHERE day_utc<?", (cutoff,))


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
    stats = conn.execute("SELECT session_handle,latest_telemetry_seq FROM bridge_session_monitor_stats")
    while batch := stats.fetchmany(500):
        for row in batch:
            candidate = latest.get(row["session_handle"])
            pointer = candidate[1] if candidate else None
            if row["latest_telemetry_seq"] != pointer:
                conn.execute("UPDATE bridge_session_monitor_stats SET latest_telemetry_seq=? WHERE session_handle=?",
                             (pointer, row["session_handle"]))
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
                     historical_scope: tuple[int | None, int | None, int] | None = None) -> None:
    stream, peer, handle = mapping["stream_id"], mapping["peer_id"], mapping["session_handle"]
    stats = historical if historical is not None else historical_counts(conn, stream, peer, mapping["session_generation"]) if legacy else (0, 0, 1)
    conn.execute("INSERT INTO bridge_session_monitor_stats(session_handle,reuse_count,fresh_count,counts_complete,binding_json) VALUES (?,?,?,?,?)",
                 (handle, *stats, dumps(binding)))
    scope = historical_scope if historical_scope is not None else historical_counts(conn, stream, peer) if legacy else (0, 0, 1)
    coverage = conn.execute("SELECT COALESCE(MAX(seq),0) FROM bridge_evidence").fetchone()[0] if legacy else 0
    conn.execute("INSERT OR IGNORE INTO bridge_session_monitor_scope VALUES (?,?,?,?,?,?)", (stream, peer, *scope, coverage))


def snapshot(delivery: Mapping[str, Any] | sqlite3.Row) -> dict[str, Any] | None:
    raw = delivery["session_snapshot_json"]
    return json.loads(raw) if raw else None


def call(conn: sqlite3.Connection, delivery: Mapping[str, Any] | sqlite3.Row, now: float) -> None:
    """First confirmed STARTED only; old started calls never receive new ordering."""
    if delivery["certainty"] in ("STARTED", "TERMINAL"):
        return
    accepted = snapshot(delivery)
    if accepted is None:
        # Late confirmation of an unattributable old call must invalidate totals,
        # rather than manufacture a mode or silently keep a known-zero count.
        high = conn.execute("SELECT COALESCE(MAX(seq),0) FROM bridge_evidence").fetchone()[0]
        conn.execute("INSERT INTO bridge_session_monitor_scope VALUES (?,?,NULL,NULL,0,?) ON CONFLICT(stream_id,peer_id) "
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
