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
from peerhub.extensions.observation_model import (
    MEASUREMENT_KEYS,
    FreshnessPolicy,
    ObservationCorruptError,
    evaluate as evaluate_observation,
    row_to_observation,
)
from peerhub.extensions.quota_projection import project_exhaustion


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


def _current_profile(profile: object, workspace: Path) -> dict[str, Any]:
    if not isinstance(profile, str) or _public_binding_value(profile) is None:
        return {"basis": "current_config", "profile": None, "fields": None, "reason": "profile_unavailable"}
    from peerhub.extensions.adapters.profiles import inspect_profile
    try:
        current = inspect_profile(profile, workspace)
    except (OSError, ValueError):
        return {"basis": "current_config", "profile": profile, "fields": None, "reason": "profile_config_unavailable"}
    return {"basis": "current_config", **current}


def _monitor_workspace(db_path: str, workspace: str | Path | None) -> Path:
    if workspace is not None:
        return Path(workspace)
    database = Path(db_path).resolve()
    if (database.parent / "workspace.generation").is_file():
        return database.parent
    if database.parent.name == ".peerhub":
        return database.parent.parent
    return Path.cwd()


def _public_telemetry(raw: object, *, handle: str, generation: int, provider: str) -> dict[str, Any]:
    detail = _safe_object(raw)
    if (detail.get("schema_version") != 3 or detail.get("handle") != handle or
            detail.get("generation") != generation or detail.get("provider_kind") != provider):
        return {"configured_model": None, "resolved_model": None, "observed_at": None,
                "adapter_version": None, "provider_cli_version": None, "usage": None,
                "context": {"occupancy_tokens": None, "window_tokens": None, "percent": None,
                            "source": None, "observed_at": None, "window_reported_at": None,
                            "state": "UNKNOWN", "reason": "invalid_or_missing_receipt"}}
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
    has_context = any(detail.get(key) is not None for key in
                      ("last_input_tokens", "context_window", "context_pct", "context_sample"))
    # No adapter currently has a validated, versioned occupancy source. Keep
    # values unknown until a provider-specific validator is explicitly added.
    context = {"occupancy_tokens": None, "window_tokens": None, "percent": None,
               "source": model(detail.get("source")),
               "observed_at": sample_at if valid_context else None,
               "window_reported_at": window_at if valid_context else None,
               "state": "UNKNOWN",
               "reason": "unvalidated_context_source" if has_context else "no_context_measurement"}
    return {"configured_model": model(detail.get("configured_model")), "resolved_model": model(detail.get("resolved_model")),
            "adapter_version": _public_binding_value(detail.get("adapter_version")),
            "provider_cli_version": _public_binding_value(detail.get("provider_cli_version")),
            "observed_at": sample_at if isinstance(sample_at, (int, float)) and not isinstance(sample_at, bool) else None,
            "usage": public_usage, "context": context}


def render_monitor_frame(db_path: str, groups: list[str], *, read_at: float | None = None,
                         trace_callback: Callable[[str], None] | None = None,
                         workspace: str | Path | None = None) -> dict[str, Any]:
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
        peer_sample_truncated = False
        try:
            rows = (conn.execute("SELECT peer_id,display_name,adapter_ref FROM peers ORDER BY peer_id LIMIT 4").fetchall()
                    if bool(set(groups) & {"overview", "peers"}) else [])
            peer_sample_truncated = len(rows) > 3
            peers = [{"peer_id": str(row[0]), "display_name": row[1], "adapter_ref": row[2]} for row in rows[:3]]
            if "overview" in groups:
                output["overview"] = {"status": "OK", "data": {"peers": peers, "sample_size": len(peers),
                                                                     "truncated": peer_sample_truncated}}
        except sqlite3.Error as exc:
            errors["overview"] = type(exc).__name__
            if "overview" in groups:
                output["overview"] = {"status": "ERROR", "data": None}
        sessions: list[dict[str, Any]] = []
        truncated = False
        try:
            needs_sessions = bool(set(groups) & {"overview", "sessions", "usage", "context", "runtime", "peers"})
            rows = conn.execute(
                "SELECT h.session_handle,h.stream_id,h.peer_id,h.session_generation,h.runtime_kind,h.state,h.resumable,"
                "h.last_seen,h.created_at,h.tombstoned_at,COALESCE(s.binding_json, h.binding) AS binding_json,"
                "s.latest_telemetry_seq,s.reuse_count,s.fresh_count,e.detail "
                "FROM (SELECT rowid,session_handle,stream_id,peer_id,session_generation,runtime_kind,binding,state,resumable,last_seen,created_at,tombstoned_at "
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
                rc, fc = row["reuse_count"], row["fresh_count"]
                exec_mode = ("reused" if rc and rc > 0 else "fresh" if fc and fc > 0 else None)
                sessions.append({"handle": str(row["session_handle"]), "stream_id": str(row["stream_id"]),
                                 "peer_id": str(row["peer_id"]), "generation": int(row["session_generation"]),
                                 "provider": str(row["runtime_kind"]), "state": str(row["state"]),
                                 "resumable": bool(row["resumable"]), "execution_mode": exec_mode,
                                 "last_seen": float(row["last_seen"]),
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
            config_workspace = _monitor_workspace(db_path, workspace)
            current_profiles: dict[str, dict[str, Any]] = {}
            runtime_sessions: list[dict[str, Any]] = []
            for item in sessions:
                binding = item["configured_binding"]
                profile = binding.get("profile")
                if isinstance(profile, str):
                    if profile not in current_profiles:
                        current_profiles[profile] = _current_profile(profile, config_workspace)
                    current_profile = current_profiles[profile]
                else:
                    current_profile = _current_profile(profile, config_workspace)
                runtime_sessions.append({
                    "handle": item["handle"], "peer_id": item["peer_id"], "provider": item["provider"],
                    "configured_binding": binding, "resolved_model": item["telemetry"]["resolved_model"],
                    "adapter_version": item["telemetry"]["adapter_version"],
                    "provider_cli_version": item["telemetry"]["provider_cli_version"],
                    "configured_binding_provenance": {
                        "basis": "session_snapshot",
                        "field_sources": {"profile": None, "model": None, "effort": None}},
                    "current_profile": current_profile,
                })
            output["runtime"] = {"status": "ERROR" if "sessions" in errors else "OK",
                                  "data": {"sessions": runtime_sessions}}
        scopes: list[dict[str, Any]] = []
        scope_rows: list[sqlite3.Row] = []
        scope_truncated_peers: set[str] = set()
        try:
            needs_scopes = bool(set(groups) & {"execution", "coverage", "usage", "sessions"})
            if needs_scopes:
                peer_rows = conn.execute(
                    "WITH RECURSIVE peer_keys(peer_id,peer_rank) AS ("
                    "SELECT MIN(peer_id),1 FROM bridge_monitor_delivery_scope "
                    "UNION ALL SELECT (SELECT MIN(peer_id) FROM bridge_monitor_delivery_scope WHERE peer_id>p.peer_id),"
                    "p.peer_rank+1 FROM peer_keys p WHERE p.peer_id IS NOT NULL AND p.peer_rank<4) "
                    "SELECT peer_id,peer_rank FROM peer_keys WHERE peer_id IS NOT NULL ORDER BY peer_rank"
                ).fetchall()
                branches: list[str] = []
                scope_args: list[Any] = []
                for peer_row in peer_rows:
                    branches.append(
                        "SELECT * FROM (SELECT ? AS peer_rank,s.* FROM bridge_monitor_delivery_scope s "
                        "WHERE s.peer_id=? ORDER BY s.stream_id LIMIT 21)"
                    )
                    scope_args.extend((int(peer_row[1]), str(peer_row[0])))
                selected = " UNION ALL ".join(branches)
                scope_rows = conn.execute(
                    "WITH selected AS (" + selected + "), ranked AS ("
                    "SELECT s.*,r.retained_session_count,r.retained_complete,"
                    "ROW_NUMBER() OVER (PARTITION BY s.peer_id ORDER BY s.stream_id) scope_rank "
                    "FROM selected s LEFT JOIN bridge_session_monitor_scope r "
                    "ON r.stream_id=s.stream_id AND r.peer_id=s.peer_id) "
                    "SELECT s.stream_id,s.peer_id,s.not_started_count,s.may_have_started_count,s.started_count,s.terminal_count,"
                    "s.measured_count,s.absent_count,s.unsupported_count,s.unknown_count,s.missing_count,s.delivery_complete,"
                    "s.telemetry_complete,s.delivery_coverage_start_seq,s.telemetry_coverage_start_seq,"
                    "COALESCE(SUM(d.latency_sum_ns),0),COALESCE(SUM(d.latency_count),0),"
                    "COALESCE(SUM(d.latency_missing_start_count),0),COALESCE(SUM(d.latency_domain_mismatch_count),0),"
                    "COALESCE(SUM(d.latency_invalid_count),0),COALESCE(SUM(d.latency_terminal_before_start_count),0),"
                    "s.scope_rank,s.peer_rank,s.retained_session_count,s.retained_complete "
                    "FROM ranked s LEFT JOIN bridge_monitor_delivery_day d "
                    "ON d.stream_id=s.stream_id AND d.peer_id=s.peer_id AND d.terminal_day_utc>=? AND d.terminal_day_utc<=? "
                    "GROUP BY s.stream_id,s.peer_id ORDER BY s.peer_rank,s.scope_rank",
                    (*scope_args, start_day, end_day.isoformat()),
                ).fetchall() if branches else []
            else:
                scope_rows = []
            selected_scope_rows = [row for row in scope_rows if int(row[22]) <= 3 and int(row[21]) <= 20]
            scope_truncated_peers = {str(row[1]) for row in scope_rows
                                     if (int(row[22]) == 4 or (int(row[22]) <= 3 and int(row[21]) == 21))}
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
        observations: list[dict[str, Any]] = []
        latest_facts_report: dict[str, Any] | None = None
        if bool(set(groups) & {"capacity", "peers"}):
            try:
                rows = conn.execute(
                    "SELECT o.capture_seq,o.observation_id,o.subject_ref,o.resource_pool_ref,o.kind,o.source,o.state,"
                    "o.payload_json,o.observed_at,o.captured_at,o.effective_at_us,p.provider,p.kind AS pool_kind "
                    "FROM observation_rollups r JOIN observations o ON o.capture_seq=r.latest_capture_seq "
                    "LEFT JOIN resource_pools p ON p.resource_pool_id=o.resource_pool_ref "
                    "WHERE r.kind IN ('quota','rate_limit') ORDER BY o.subject_ref,r.pool_key LIMIT 101"
                ).fetchall()
                for row in rows[:100]:
                    try:
                        observation, capture_seq = row_to_observation(row)
                    except ObservationCorruptError as exc:
                        raise sqlite3.DataError(f"invalid quota observation at capture_seq={row['capture_seq']}") from exc
                    if latest_facts_report is None and "facts_report" in observation.payload:
                        raw_f: object = observation.payload.get("facts_report")
                        if isinstance(raw_f, dict):
                            latest_facts_report = cast(dict[str, Any], raw_f)
                    freshness = evaluate_observation(observation, capture_seq, round(now * 1_000_000), FreshnessPolicy())
                    observed_at = observation.observed_at
                    try:
                        measured_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).timestamp()
                    except (ValueError, OverflowError):
                        measured_at = None
                    remaining = observation.payload.get("remaining_fraction")
                    used_fraction = (1 - float(remaining)
                                     if isinstance(remaining, (int, float)) and not isinstance(remaining, bool)
                                     and math.isfinite(float(remaining)) and 0 <= remaining <= 1 else None)
                    projection = project_exhaustion(
                        used_fraction, observation.payload.get("window_started_at"), measured_at,
                        observation.payload.get("resets_at"), now,
                        evidence_state=freshness.state.value,
                        anchor_basis=observation.payload.get("anchor_basis"),
                    )
                    measurements = None
                    if freshness.state.value == "MEASURED":
                        measured: dict[str, int | float] = {}
                        for key in MEASUREMENT_KEYS.intersection(observation.payload):
                            value = observation.payload[key]
                            if (isinstance(value, (int, float)) and not isinstance(value, bool) and
                                    math.isfinite(float(value)) and value >= 0 and
                                    (key != "remaining_fraction" or value <= 1)):
                                measured[key] = value
                        measurements = measured
                    pool_ref = observation.resource_pool_ref
                    observations.append({
                        "observation_id": observation.observation_id,
                        "peer_id": observation.subject_ref,
                        "provider": row["provider"],
                        "kind": observation.kind,
                        "pool_ref": pool_ref,
                        "pool_kind": row["pool_kind"],
                        "attribution_scope": "peer_account_pool" if pool_ref is not None else "peer",
                        "source": observation.source,
                        "projection": projection,
                        "state": freshness.state.value,
                        "stored_state": freshness.stored_state.value,
                        "observed_at": observation.observed_at,
                        "captured_at": observation.captured_at,
                        "effective_at_us": int(row["effective_at_us"]),
                        "age_seconds": freshness.age_seconds,
                        "ttl_seconds": freshness.ttl_seconds,
                        "freshness_basis": freshness.basis,
                        "skew_seconds": freshness.skew_seconds,
                        "measurements": measurements,
                    })
                if "capacity" in groups:
                    output["capacity"] = {"status": "OK", "data": {"observations": observations,
                        "sample_size": min(len(rows), 100), "truncated": len(rows) > 100}}
            except sqlite3.Error as exc:
                errors["capacity"] = type(exc).__name__
                if "capacity" in groups:
                    output["capacity"] = {"status": "ERROR", "data": None}
        if "peers" in groups:
            config_workspace = _monitor_workspace(db_path, workspace)
            peer_records: list[dict[str, Any]] = []
            for p in peers:
                pid = p["peer_id"]
                peer_sessions = [s for s in sessions if s["peer_id"] == pid]
                s_item = max(
                    peer_sessions,
                    key=lambda s: (float(s.get("last_seen") or 0.0), int(s.get("generation") or 0)),
                    default=None,
                )
                prof_data: dict[str, Any] | None = None
                configured_model: object = None
                configured_pools: list[str] = []
                is_mapped = False
                if s_item is not None:
                    raw_binding: object = s_item.get("configured_binding")
                    binding: dict[str, Any] = cast(dict[str, Any], raw_binding) if isinstance(raw_binding, dict) else {}
                    prof_name = binding.get("profile")
                    recorded_model = binding.get("model")
                    prof_source = "unknown"
                    if isinstance(prof_name, str):
                        cp = _current_profile(prof_name, config_workspace)
                        raw_fields: object = cp.get("fields")
                        fields: dict[str, Any] = cast(dict[str, Any], raw_fields) if isinstance(raw_fields, dict) else {}
                        raw_model: object = fields.get("model")
                        model_field: dict[str, Any] = cast(dict[str, Any], raw_model) if isinstance(raw_model, dict) else {}
                        configured_model = model_field.get("value")
                        model_source: object = model_field.get("source")
                        raw_pools: object = fields.get("pools")
                        pools_field: dict[str, Any] = cast(dict[str, Any], raw_pools) if isinstance(raw_pools, dict) else {}
                        pools_val: object = pools_field.get("value")
                        if isinstance(pools_val, list):
                            configured_pools = [str(x) for x in cast(list[object], pools_val)]
                        if model_source and recorded_model is not None and recorded_model == configured_model:
                            prof_source = str(model_source)
                            if configured_pools:
                                is_mapped = True
                    prof_data = {
                        "profile": prof_name,
                        "model": binding.get("model"),
                        "source": prof_source,
                    }

                peer_observations = [o for o in observations if o["peer_id"] == pid]
                q_data: dict[str, Any] | None = None
                measured_candidates: list[tuple[float, dict[str, Any]]] = []
                stale_candidates: list[dict[str, Any]] = []
                for o in peer_observations:
                    raw_m: object = o.get("measurements")
                    q_m: dict[str, Any] = cast(dict[str, Any], raw_m) if isinstance(raw_m, dict) else {}
                    raw_rem: object = q_m.get("remaining_fraction")
                    is_valid_rem = (
                        isinstance(raw_rem, (int, float)) and not isinstance(raw_rem, bool)
                        and math.isfinite(float(raw_rem)) and 0.0 <= float(raw_rem) <= 1.0
                    )
                    state = o.get("state")
                    if state == "MEASURED" and is_valid_rem:
                        measured_candidates.append((float(cast(float, raw_rem)), o))
                    elif state == "STALE":
                        stale_candidates.append(o)

                if is_mapped:
                    mapped_measured: list[tuple[float, dict[str, Any]]] = []
                    is_incomplete = False
                    for p_req in configured_pools:
                        matching_obs = [
                            o for o in peer_observations
                            if (str(o.get("pool_ref") or o.get("pool_kind") or "").split(":")[-1]) == p_req
                        ]
                        if not matching_obs:
                            is_incomplete = True
                            break
                        best_matching = matching_obs[0]
                        raw_m_val = best_matching.get("measurements")
                        q_m_dict = cast(dict[str, Any], raw_m_val) if isinstance(raw_m_val, dict) else {}
                        rem_val = q_m_dict.get("remaining_fraction")
                        is_valid = (
                            isinstance(rem_val, (int, float)) and not isinstance(rem_val, bool)
                            and math.isfinite(float(rem_val)) and 0.0 <= float(rem_val) <= 1.0
                        )
                        if best_matching.get("state") != "MEASURED" or not is_valid:
                            is_incomplete = True
                            break
                        mapped_measured.append((float(cast(float, rem_val)), best_matching))

                    if is_incomplete:
                        q_data = {
                            "pool_ref": "incomplete",
                            "remaining_fraction": None,
                            "percent": None,
                            "state": "INCOMPLETE",
                            "source": "derived",
                            "age_seconds": None,
                            "scope": "mapped",
                        }
                    elif mapped_measured:
                        lowest_rem, best_obs = min(mapped_measured, key=lambda x: (x[0], str(x[1].get("pool_ref") or "")))
                        q_data = {
                            "pool_ref": best_obs.get("pool_ref") or best_obs.get("pool_kind"),
                            "remaining_fraction": lowest_rem,
                            "percent": round(lowest_rem * 100),
                            "state": "MEASURED",
                            "source": best_obs.get("source"),
                            "age_seconds": best_obs.get("age_seconds"),
                            "scope": "mapped",
                        }
                else:
                    unmapped_scope = "unmapped" if s_item is not None else None
                    if measured_candidates:
                        lowest_rem, best_obs = min(measured_candidates, key=lambda x: (x[0], str(x[1].get("pool_ref") or "")))
                        q_data = {
                            "pool_ref": best_obs.get("pool_ref") or best_obs.get("pool_kind"),
                            "remaining_fraction": lowest_rem,
                            "percent": round(lowest_rem * 100),
                            "state": "MEASURED",
                            "source": best_obs.get("source"),
                            "age_seconds": best_obs.get("age_seconds"),
                        }
                        if unmapped_scope is not None:
                            q_data["scope"] = unmapped_scope
                    elif stale_candidates:
                        stale_obs = stale_candidates[0]
                        q_data = {
                            "pool_ref": stale_obs.get("pool_ref") or stale_obs.get("pool_kind"),
                            "remaining_fraction": None,
                            "percent": None,
                            "state": "STALE",
                            "source": stale_obs.get("source"),
                            "age_seconds": stale_obs.get("age_seconds"),
                        }
                        if unmapped_scope is not None:
                            q_data["scope"] = unmapped_scope
                    elif peer_observations:
                        first_obs = peer_observations[0]
                        q_data = {
                            "pool_ref": first_obs.get("pool_ref") or first_obs.get("pool_kind"),
                            "remaining_fraction": None,
                            "percent": None,
                            "state": first_obs.get("state") or "UNKNOWN",
                            "source": first_obs.get("source"),
                            "age_seconds": first_obs.get("age_seconds"),
                        }
                        if unmapped_scope is not None:
                            q_data["scope"] = unmapped_scope
                sess_data: dict[str, Any] | None = None
                if s_item is not None:
                    sess_data = {
                        "generation": s_item.get("generation"),
                        "execution_mode": s_item.get("execution_mode"),
                        "resumable": s_item.get("resumable"),
                    }
                ctx_data: dict[str, Any] | None = None
                if s_item is not None:
                    raw_tel: object = s_item.get("telemetry")
                    tel: dict[str, Any] = cast(dict[str, Any], raw_tel) if isinstance(raw_tel, dict) else {}
                    raw_ctx: object = tel.get("context")
                    tel_ctx: dict[str, Any] = cast(dict[str, Any], raw_ctx) if isinstance(raw_ctx, dict) else {}
                    if isinstance(raw_ctx, dict):
                        ctx_data = {
                            "percent": tel_ctx.get("percent"),
                            "occupancy_tokens": tel_ctx.get("occupancy_tokens"),
                            "state": tel_ctx.get("state"),
                        }
                peer_records.append({
                    "peer_id": pid,
                    "quota": q_data,
                    "profile": prof_data,
                    "session": sess_data,
                    "context": ctx_data,
                })
            from peerhub.extensions.adapters.profiles import packaged_defaults_meta
            defaults_meta = packaged_defaults_meta()
            if latest_facts_report is None:
                try:
                    f_rows = conn.execute(
                        "SELECT payload_json FROM observations "
                        "WHERE kind='quota' "
                        "ORDER BY capture_seq DESC LIMIT 20"
                    ).fetchall()
                    for (f_str,) in f_rows:
                        if not f_str or "facts_report" not in f_str:
                            continue
                        try:
                            data_obj: object = json.loads(f_str)
                            if isinstance(data_obj, dict):
                                rep_val: object = cast(dict[str, Any], data_obj).get("facts_report")
                                if isinstance(rep_val, dict):
                                    latest_facts_report = cast(dict[str, Any], rep_val)
                                    break
                        except (ValueError, TypeError):
                            continue
                except (sqlite3.Error, ValueError, TypeError):
                    pass
            output["peers"] = {
                "status": "ERROR" if "sessions" in errors else "OK",
                "data": {
                    "peers": peer_records,
                    "sample_size": len(peer_records),
                    "truncated": truncated or peer_sample_truncated,
                    "session_sample_truncated": truncated,
                    "peer_sample_truncated": peer_sample_truncated,
                    "defaults_revision": defaults_meta.get("defaults_revision"),
                    "observed_at": defaults_meta.get("observed_at"),
                    "facts_report": latest_facts_report,
                },
            }
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
            for dependent in ("overview", "peers", "sessions", "usage", "context", "runtime"):
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
