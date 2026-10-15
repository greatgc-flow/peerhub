"""Session call/terminal evidence and writer-maintained monitoring projections.

All functions use the caller's transaction. Readers never invoke these writers.
Billing usage is deliberately not interpreted as final-request occupancy.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Mapping, cast

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
    detail["started_at"] = now
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
    updated = conn.execute("UPDATE bridge_session_monitor_stats SET latest_telemetry_seq=? WHERE session_handle=?",
                           (seq, accepted["handle"])).rowcount
    if updated != 1:
        raise sqlite3.IntegrityError("session telemetry projection missing")
