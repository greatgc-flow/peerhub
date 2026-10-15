"""Read-only, bounded aggregate queries over canonical monitor projections."""
from __future__ import annotations

import sqlite3
import json
import math
import re
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, cast

from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message


def _filters(peer_id: str | None, stream_id: str | None, alias: str = "s") -> tuple[str, list[Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    if peer_id is not None:
        clauses.append(f"{alias}.peer_id=?")
        args.append(peer_id)
    if stream_id is not None:
        clauses.append(f"{alias}.stream_id=?")
        args.append(stream_id)
    return (" AND ".join(clauses) if clauses else "1=1"), args


def usage_coverage(conn: sqlite3.Connection, *, peer_id: str | None = None,
                   stream_id: str | None = None, through_day: str | None = None) -> dict[str, int | bool]:
    """Read 30 UTC days of coverage from the canonical scope/day projections."""
    end = date.fromisoformat(through_day) if through_day else datetime.now(timezone.utc).date()
    start = (end - timedelta(days=29)).isoformat()
    where, scope_args = _filters(peer_id, stream_id)
    row = conn.execute(
        "WITH selected AS (SELECT * FROM bridge_monitor_delivery_scope s WHERE " + where + "), "
        "daily AS (SELECT COALESCE(SUM(d.terminal_count),0) eligible,COALESCE(SUM(d.measured_count),0) measured,"
        "COALESCE(SUM(d.absent_count),0) absent,COALESCE(SUM(d.unsupported_count),0) unsupported,"
        "COALESCE(SUM(d.unknown_count),0) unknown,COALESCE(SUM(d.missing_count),0) missing "
        "FROM bridge_monitor_delivery_day d JOIN selected s ON s.stream_id=d.stream_id AND s.peer_id=d.peer_id "
        "WHERE d.terminal_day_utc>=? AND d.terminal_day_utc<=?) "
        "SELECT daily.*,COALESCE((SELECT MIN(telemetry_coverage_start_seq) FROM selected),0),"
        "(SELECT COUNT(*) FROM selected),(SELECT COUNT(*) FROM selected WHERE delivery_complete=1 AND telemetry_complete=1) "
        "FROM daily", [*scope_args, start, end.isoformat()],
    ).fetchone()
    return {
        "eligible": int(row[0] or 0), "measured": int(row[1] or 0), "absent": int(row[2] or 0),
        "unsupported": int(row[3] or 0), "unknown": int(row[4] or 0), "missing": int(row[5] or 0),
        "coverage_start_delivery_seq": int(row[6] or 0),
        "complete": bool(row[7] and row[7] == row[8]),
    }


def session_latency(conn: sqlite3.Connection, *, peer_id: str | None = None,
                    stream_id: str | None = None, through_day: str) -> dict[str, int]:
    """Read the 30-day UTC timing rollup; never recount deliveries or evidence."""
    end = date.fromisoformat(through_day)
    start = (end - timedelta(days=29)).isoformat()
    where, scope_args = _filters(peer_id, stream_id)
    row = conn.execute(
        "SELECT COALESCE(SUM(d.latency_sum_ns),0),COALESCE(SUM(d.latency_count),0),"
        "COALESCE(SUM(d.latency_missing_start_count),0),COALESCE(SUM(d.latency_domain_mismatch_count),0),"
        "COALESCE(SUM(d.latency_invalid_count),0),COALESCE(SUM(d.latency_terminal_before_start_count),0) "
        "FROM bridge_monitor_delivery_day d JOIN bridge_monitor_delivery_scope s "
        "ON s.stream_id=d.stream_id AND s.peer_id=d.peer_id "
        f"WHERE {where} AND d.terminal_day_utc>=? AND d.terminal_day_utc<=?",
        [*scope_args, start, end.isoformat()],
    ).fetchone()
    return dict(zip(("latency_sum_ns", "latency_count", "missing_start_count", "domain_mismatch_count",
                     "invalid_ticks_count", "terminal_before_start_count"), (int(value or 0) for value in row)))


def _safe_object(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str):
        return {}
    try:
        parsed: object = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}


_SAFE_MODEL = re.compile(r"[A-Za-z0-9._+-]{1,128}\Z")
_SECRET_LIKE_BINDING = re.compile(
    r"(?i)(?:\b(?:sk|pk|rk|ghp|gho|ghs|xox[abp])[-_][A-Za-z0-9_-]{8,}|"
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}|"
    r"\b(?:api[_-]?key|token|secret|password|credential)[_-][A-Za-z0-9_-]{8,}\b)"
)


def _public_binding_value(value: object) -> str | None:
    if isinstance(value, str) and _SAFE_MODEL.fullmatch(value) and not _SECRET_LIKE_BINDING.search(value):
        return value
    return None


def _public_telemetry(raw: object, *, handle: str, generation: int, provider: str) -> dict[str, Any]:
    detail = _safe_object(raw)
    if (detail.get("schema_version") != 3 or detail.get("handle") != handle or
            detail.get("generation") != generation or detail.get("provider_kind") != provider):
        return {"configured_model": None, "resolved_model": None, "observed_at": None,
                "usage": None, "context": {"occupancy_tokens": None, "window_tokens": None,
                                             "percent": None, "source": None, "observed_at": None}}
    def model(value: object) -> str | None:
        return value if isinstance(value, str) and _SAFE_MODEL.fullmatch(value) else None
    usage_raw: object = detail.get("usage")
    public_usage: dict[str, Any] | None = None
    if isinstance(usage_raw, dict):
        from peerhub.extensions.session_receipts import validate_provider_usage
        usage = cast(dict[str, Any], usage_raw)
        candidate = {key: usage.get(key) for key in ("schema_version", "scope", "source", "reported", "normalized", "coverage")}
        checked = validate_provider_usage(candidate, provider)
        if checked == usage:
            public_usage = {key: usage.get(key) for key in ("schema_version", "scope", "attribution", "reported", "normalized", "coverage")}
    occupancy, window, pct = detail.get("last_input_tokens"), detail.get("context_window"), detail.get("context_pct")
    sample_at, window_at = detail.get("observed_at"), detail.get("window_reported_at")
    valid_context = (
        isinstance(occupancy, int) and not isinstance(occupancy, bool) and occupancy >= 0 and
        isinstance(window, int) and not isinstance(window, bool) and window > 0 and
        isinstance(pct, (int, float)) and not isinstance(pct, bool) and math.isfinite(float(pct)) and 0 <= pct <= 100 and
        isinstance(sample_at, (int, float)) and not isinstance(sample_at, bool) and
        isinstance(window_at, (int, float)) and not isinstance(window_at, bool)
    )
    context = {"occupancy_tokens": occupancy if valid_context else None,
               "window_tokens": window if valid_context else None,
               "percent": pct if valid_context else None,
               "source": model(detail.get("source")),
               "observed_at": sample_at if valid_context else None,
               "window_reported_at": window_at if valid_context else None}
    return {"configured_model": model(detail.get("configured_model")), "resolved_model": model(detail.get("resolved_model")),
            "observed_at": sample_at if isinstance(sample_at, (int, float)) and not isinstance(sample_at, bool) else None,
            "usage": public_usage, "context": context}


def render_monitor_frame(db_path: str, groups: list[str], *, read_at: float | None = None,
                         trace_callback: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Read grouped monitor facts from one query-only SQLite snapshot (five bounded query families)."""
    import time
    now = time.time() if read_at is None else read_at
    end_day = datetime.fromtimestamp(now, timezone.utc).date()
    start_day = (end_day - timedelta(days=29)).isoformat()
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    output: dict[str, Any] = {}
    errors: dict[str, str] = {}
    select_count = 0
    try:
        conn.execute("PRAGMA query_only=ON")
        if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise sqlite3.OperationalError("read-only monitor connection unavailable")
        conn.execute("BEGIN")
        schema = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if schema > SUPPORTED_SCHEMA_VERSION:
            raise SchemaVersionError(future_schema_message(schema, SUPPORTED_SCHEMA_VERSION))
        def trace(statement: str) -> None:
            nonlocal select_count
            if statement.lstrip().upper().startswith(("SELECT", "WITH")):
                select_count += 1
            if trace_callback is not None:
                trace_callback(statement)
        conn.set_trace_callback(trace)
        peers: list[dict[str, Any]] = []
        try:
            rows = (conn.execute("SELECT peer_id,display_name,adapter_ref FROM peers ORDER BY peer_id LIMIT 4").fetchall()
                    if "overview" in groups else [])
            peers = [{"peer_id": str(row[0]), "display_name": row[1], "adapter_ref": row[2]} for row in rows]
            if "overview" in groups:
                output["overview"] = {"status": "OK", "data": {"peers": peers[:3], "sample_size": min(len(peers), 3),
                                                                     "truncated": len(peers) > 3}}
        except sqlite3.Error as exc:
            errors["overview"] = type(exc).__name__
            if "overview" in groups:
                output["overview"] = {"status": "ERROR", "data": None}
        sessions: list[dict[str, Any]] = []
        truncated = False
        try:
            needs_sessions = bool(set(groups) & {"overview", "sessions", "usage", "context", "runtime"})
            rows = conn.execute(
                "SELECT h.session_handle,h.stream_id,h.peer_id,h.session_generation,h.runtime_kind,h.state,h.resumable,"
                "h.last_seen,h.created_at,h.tombstoned_at,s.binding_json,s.latest_telemetry_seq,e.detail "
                "FROM (SELECT rowid,session_handle,stream_id,peer_id,session_generation,runtime_kind,state,resumable,last_seen,created_at,tombstoned_at "
                "FROM bridge_session_history ORDER BY rowid DESC LIMIT 101) h "
                "LEFT JOIN bridge_session_monitor_stats s ON s.session_handle=h.session_handle "
                "LEFT JOIN bridge_evidence e ON e.seq=s.latest_telemetry_seq AND e.kind='session_telemetry' "
                "ORDER BY h.rowid DESC"
            ).fetchall() if needs_sessions else []
            truncated = len(rows) > 100
            for row in rows[:100]:
                binding = _safe_object(row["binding_json"])
                telemetry = _public_telemetry(row["detail"], handle=str(row["session_handle"]),
                                               generation=int(row["session_generation"]), provider=str(row["runtime_kind"]))
                sessions.append({"handle": str(row["session_handle"]), "stream_id": str(row["stream_id"]),
                                 "peer_id": str(row["peer_id"]), "generation": int(row["session_generation"]),
                                 "provider": str(row["runtime_kind"]), "state": str(row["state"]),
                                 "resumable": bool(row["resumable"]), "last_seen": float(row["last_seen"]),
                                 "created_at": float(row["created_at"]), "tombstoned_at": row["tombstoned_at"],
                                 "configured_binding": {key: _public_binding_value(binding.get(key)) for key in ("profile", "model", "effort")},
                                 "telemetry": telemetry})
        except sqlite3.Error as exc:
            errors["sessions"] = type(exc).__name__
        if "sessions" in groups:
            output["sessions"] = {"status": "ERROR" if "sessions" in errors else "OK",
                                  "data": {"items": sessions, "sample_size": len(sessions), "truncated": truncated}}
        if "usage" in groups:
            output["usage"] = {"status": "ERROR" if "sessions" in errors else "OK", "data": {"sessions": [{"handle": item["handle"], "peer_id": item["peer_id"],
                                                                            "generation": item["generation"], "telemetry": item["telemetry"]["usage"]}
                                                                   for item in sessions]}}
        if "context" in groups:
            output["context"] = {"status": "ERROR" if "sessions" in errors else "OK", "data": {"sessions": [{"handle": item["handle"], "peer_id": item["peer_id"],
                                                                              "generation": item["generation"], "context": item["telemetry"]["context"]}
                                                                     for item in sessions]}}
        if "runtime" in groups:
            output["runtime"] = {"status": "ERROR" if "sessions" in errors else "OK", "data": {"sessions": [{"handle": item["handle"], "peer_id": item["peer_id"],
                                                                              "provider": item["provider"], "configured_binding": item["configured_binding"],
                                                                              "resolved_model": item["telemetry"]["resolved_model"]}
                                                                     for item in sessions]}}
        scopes: list[dict[str, Any]] = []
        scope_rows: list[sqlite3.Row] = []
        scope_truncated_peers: set[str] = set()
        try:
            needs_scopes = bool(set(groups) & {"execution", "coverage", "usage", "sessions"})
            scope_rows = conn.execute(
                "WITH peer_ranks AS (SELECT peer_id,ROW_NUMBER() OVER (ORDER BY peer_id) peer_rank "
                "FROM (SELECT DISTINCT peer_id FROM bridge_monitor_delivery_scope)), "
                "ranked AS (SELECT s.*,r.retained_session_count,r.retained_complete,"
                "ROW_NUMBER() OVER (PARTITION BY s.peer_id ORDER BY s.stream_id) scope_rank,p.peer_rank "
                "FROM bridge_monitor_delivery_scope s JOIN peer_ranks p ON p.peer_id=s.peer_id "
                "LEFT JOIN bridge_session_monitor_scope r ON r.stream_id=s.stream_id AND r.peer_id=s.peer_id) "
                "SELECT s.stream_id,s.peer_id,s.not_started_count,s.may_have_started_count,s.started_count,s.terminal_count,"
                "s.measured_count,s.absent_count,s.unsupported_count,s.unknown_count,s.missing_count,s.delivery_complete,"
                "s.telemetry_complete,s.delivery_coverage_start_seq,s.telemetry_coverage_start_seq,"
                "COALESCE(SUM(d.latency_sum_ns),0),COALESCE(SUM(d.latency_count),0),"
                "COALESCE(SUM(d.latency_missing_start_count),0),COALESCE(SUM(d.latency_domain_mismatch_count),0),"
                "COALESCE(SUM(d.latency_invalid_count),0),COALESCE(SUM(d.latency_terminal_before_start_count),0),"
                "s.scope_rank,s.peer_rank,s.retained_session_count,s.retained_complete "
                "FROM ranked s LEFT JOIN bridge_monitor_delivery_day d "
                "ON d.stream_id=s.stream_id AND d.peer_id=s.peer_id AND d.terminal_day_utc>=? AND d.terminal_day_utc<=? "
                "WHERE s.peer_rank<=4 AND s.scope_rank<=21 GROUP BY s.stream_id,s.peer_id "
                "ORDER BY s.peer_rank,s.scope_rank", (start_day, end_day.isoformat()),
            ).fetchall() if needs_scopes else []
            selected_scope_rows = [row for row in scope_rows if int(row[22]) <= 3 and int(row[21]) <= 20]
            scope_truncated_peers = {str(row[1]) for row in scope_rows if int(row[22]) <= 3 and int(row[21]) == 21}
            scope_truncated = bool(scope_truncated_peers)
            for row in selected_scope_rows:
                scopes.append({"stream_id": str(row[0]), "peer_id": str(row[1]),
                               "certainty": {"not_started": int(row[2]), "may_have_started": int(row[3]),
                                             "started": int(row[4]), "terminal": int(row[5])},
                               "usage_coverage": {"terminal": int(row[5]), "measured": int(row[6]), "absent": int(row[7]),
                                                  "unsupported": int(row[8]), "unknown": int(row[9]), "missing": int(row[10]),
                                                  "complete": bool(row[12]), "coverage_start_seq": int(row[14])},
                               "delivery_complete": bool(row[11]),
                               "latency_30d": {"sum_ns": int(row[15]), "count": int(row[16]),
                                               "missing_start": int(row[17]), "domain_mismatch": int(row[18]),
                                               "invalid_ticks": int(row[19]), "terminal_before_start": int(row[20])},
                               "retained_session_count": row[23], "retained_complete": bool(row[24])})
            if scope_truncated:
                errors["scope_cap"] = "truncated"
        except sqlite3.Error as exc:
            errors["execution"] = f"{type(exc).__name__}: {exc}"
        if "execution" in groups:
            output["execution"] = {"status": "ERROR" if "execution" in errors and errors["execution"] != "truncated" else "OK",
                                   "data": {"scopes": scopes, "sample_size": len(scopes), "truncated": "scope_cap" in errors}}
        if "coverage" in groups:
            output["coverage"] = {"status": "ERROR" if "execution" in errors and errors["execution"] != "truncated" else "OK",
                                  "data": {"scopes": [{"stream_id": item["stream_id"], "peer_id": item["peer_id"],
                                                        "delivery_complete": item["delivery_complete"],
                                                        "usage_coverage": item["usage_coverage"]} for item in scopes],
                                           "truncated": "scope_cap" in errors}}
        if "execution" in errors and errors["execution"] != "truncated":
            for dependent in ("overview", "sessions", "usage"):
                if dependent in groups and isinstance(output.get(dependent), dict):
                    cast(dict[str, Any], output[dependent])["status"] = "ERROR"
        if "usage" in groups and isinstance(output.get("usage"), dict):
            usage_group = cast(dict[str, Any], output["usage"])
            usage_data = cast(dict[str, Any], usage_group["data"])
            usage_data["coverage_by_scope"] = [{"stream_id": item["stream_id"], "peer_id": item["peer_id"],
                                                **item["usage_coverage"]} for item in scopes]
        if "sessions" in groups and isinstance(output.get("sessions"), dict):
            sessions_group = cast(dict[str, Any], output["sessions"])
            sessions_data = cast(dict[str, Any], sessions_group["data"])
            retained: dict[str, dict[str, Any]] = {}
            for item in scopes:
                peer = str(item["peer_id"])
                entry = retained.setdefault(peer, {"value": 0, "complete": True, "reason": None, "scope_count": 0})
                entry["scope_count"] += 1
                if item["retained_session_count"] is None:
                    entry.update(value=None, complete=False, reason="projection_missing")
                elif not item["retained_complete"] and entry["complete"]:
                    entry.update(value=None, complete=False, reason="legacy_incomplete")
                elif entry["complete"]:
                    entry["value"] += int(item["retained_session_count"])
            for peer, entry in retained.items():
                if peer in scope_truncated_peers and entry["complete"]:
                    entry.update(value=None, complete=False, reason="scope_cap")
            sessions_data["retained_sessions_by_peer"] = {
                peer: {"value": item["value"], "complete": item["complete"], "reason": item["reason"]}
                for peer, item in retained.items()
            }
            if len(scope_rows) > 0 and max((int(row[22]) for row in scope_rows), default=0) > 3:
                sessions_data["peer_sample_truncated"] = True
        if "capacity" in groups:
            try:
                rows = conn.execute(
                    "SELECT o.subject_ref,o.kind,o.resource_pool_ref,o.source,o.state,o.effective_at_us "
                    "FROM observation_rollups r JOIN observations o ON o.capture_seq=r.latest_capture_seq "
                    "ORDER BY o.subject_ref,o.kind,r.pool_key LIMIT 101"
                ).fetchall()
                output["capacity"] = {"status": "OK", "data": {"observations": [
                    {"peer_id": str(row[0]), "kind": str(row[1]), "pool_ref": row[2], "source": str(row[3]),
                     "state": str(row[4]), "effective_at_us": int(row[5])}
                    for row in rows[:100]], "sample_size": min(len(rows), 100), "truncated": len(rows) > 100}}
            except sqlite3.Error as exc:
                errors["capacity"] = type(exc).__name__
                output["capacity"] = {"status": "ERROR", "data": None}
        if "store" in groups:
            try:
                output["store"] = {"status": "OK", "data": {"schema_version": schema,
                                                                  "database_size_bytes": Path(db_path).stat().st_size}}
            except OSError as exc:
                errors["store"] = type(exc).__name__
                output["store"] = {"status": "ERROR", "data": None}
        if "overview" in groups and isinstance(output.get("overview"), dict):
            overview = cast(dict[str, Any], output["overview"])
            overview_data = cast(dict[str, Any], overview["data"])
            overview_data["session_sample_size"] = len(sessions)
            overview_data["session_sample_truncated"] = truncated
        if "sessions" in errors or ("execution" in errors and errors["execution"] != "truncated"):
            for dependent in ("overview", "sessions", "usage", "context", "runtime"):
                if dependent in groups and isinstance(output.get(dependent), dict):
                    cast(dict[str, Any], output[dependent])["status"] = "ERROR"
        for _group_id, raw_group in output.items():
            group = cast(dict[str, Any], raw_group)
            data_obj: object = group.get("data")
            data = cast(dict[str, Any], data_obj) if isinstance(data_obj, dict) else {}
            is_truncated = bool(data.get("truncated", False) or data.get("session_sample_truncated", False)
                                or data.get("peer_sample_truncated", False))
            group_status = str(group.get("status", "ERROR"))
            sample_size = data.get("sample_size")
            if sample_size is None:
                sample_size = next((len(cast(list[Any], value)) for value in data.values() if isinstance(value, list)), None)
            group["coverage"] = {
                "read": "unavailable" if group_status == "ERROR" else "partial" if is_truncated else "complete",
                "sample_size": sample_size, "truncated": is_truncated,
                "reason": "query_error" if group_status == "ERROR" else "sample_cap" if is_truncated else None,
            }
        conn.execute("ROLLBACK")
        return {"schema_version": "2.0", "status": "PARTIAL" if errors else "OK", "read_at": now,
                "groups": output, "errors": errors, "query_families": select_count}
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.close()
