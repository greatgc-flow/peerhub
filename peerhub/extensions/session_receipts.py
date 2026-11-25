"""Session snapshots and call/terminal evidence written in the caller's transaction.

Billing usage is deliberately not interpreted as final-request occupancy.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from typing import Any, Mapping, TypeGuard, cast

from peerhub.core.peer_kinds import BUILTIN_PEER_KINDS
from peerhub.extensions.usage_normalization import PROVIDER_USAGE_CONTRACT, validate_provider_usage


_SAFE_BINDING_VALUE = re.compile(r"[A-Za-z0-9._+-]{1,128}\Z")
_SECRET_LIKE_BINDING = re.compile(
    r"(?i)(?:\b(?:sk|pk|rk|ghp|gho|ghs|xox[abp])[-_][A-Za-z0-9_-]{8,}|"
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}|"
    r"\b(?:api[_-]?key|token|secret|password|credential)[_-][A-Za-z0-9_-]{8,}\b)"
)
_ADAPTER_VERSION = re.compile(rf"adapter:({'|'.join(map(re.escape, BUILTIN_PEER_KINDS))}):v([0-9]{{1,3}})\Z")
PROCESS_CLOCK_DOMAIN = str(uuid.uuid4())
monotonic_ns = time.monotonic_ns
_MAX_TICK = 2**63 - 1


def json_object(raw: str | None) -> dict[str, Any]:
    try:
        value: object = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return {}
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def valid_observed_at(value: object) -> TypeGuard[int | float]:
    return type(value) in (int, float) and math.isfinite(cast(float, value))


def valid_tick(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_TICK


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
        if not valid_tick(start_tick) or not valid_tick(end_tick) or not isinstance(start_domain, str) or not start_domain:
            reason = "invalid_ticks"
        elif start_domain != end_domain:
            reason = "domain_mismatch"
        elif end_tick < start_tick:
            reason = "terminal_before_start"
        else:
            elapsed = end_tick - start_tick
    return {"schema_version": 1, "clock_domain": end_domain, "terminal_monotonic_ns": end_tick,
            "elapsed_ns": elapsed, "latency_reason": reason}


def repair_latest_telemetry_pointers(conn: sqlite3.Connection) -> None:
    """One-time projection repair; only identity-matched terminal v3 receipts qualify."""
    name = "latest_terminal_telemetry_v1"
    conn.execute("INSERT OR IGNORE INTO bridge_session_monitor_meta(name,version) VALUES (?,0)", (name,))
    if conn.execute("SELECT version FROM bridge_session_monitor_meta WHERE name=?", (name,)).fetchone()[0] >= 1:
        return
    kind_marks = ",".join("?" for _ in BUILTIN_PEER_KINDS)
    source_cases = " ".join("WHEN ? THEN ?" for _ in PROVIDER_USAGE_CONTRACT)
    source_params = tuple(value for kind, (_, source) in PROVIDER_USAGE_CONTRACT.items() for value in (kind, source))
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
        f"CASE WHEN h.runtime_kind IN ({kind_marks}) THEN h.runtime_kind ELSE 'unknown' END "
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
        f"CASE h.runtime_kind {source_cases} ELSE NULL END "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.usage.coverage.state') END='text' "
        "AND CASE WHEN json_valid(e.detail) THEN json_type(e.detail,'$.usage.coverage.reason') END IN ('text','null') "
        "AND CASE WHEN json_valid(e.detail) THEN json_extract(e.detail,'$.usage.coverage.state') END IN ('MEASURED','ABSENT','UNSUPPORTED','UNKNOWN')",
        (*BUILTIN_PEER_KINDS, *source_params))
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


CONTEXT_SOURCE = "cc.stream.context"  # the receipt `source` of the one validated context reading (#376 P2)
CONTEXT_TOKENS_MAX = 100_000_000


def context_estimate(tokens: object, window: object) -> tuple[int, int, int] | None:
    """(tokens, window, percent) of a context reading, shared by the receipt writer and the monitor reader, else None.

    Both are ints (not bool) with 0 < tokens <= window <= CONTEXT_TOKENS_MAX; percent = tokens * 100 // window. More
    tokens than the window means the window is not the one the ask ran with, so the reading is unknown."""
    if (isinstance(tokens, bool) or not isinstance(tokens, int) or isinstance(window, bool) or not isinstance(window, int)
            or not 0 < tokens <= window <= CONTEXT_TOKENS_MAX):
        return None
    return tokens, window, tokens * 100 // window


def _context_fields(context: object, provider_kind: str, now: float) -> dict[str, Any]:
    """The receipt's context fields from the adapter's `context` candidate, validated again at the persistence boundary.

    Only cc has a validated source: an estimate of the occupancy at the end of the ask. Anything else stays unknown."""
    raw = cast("dict[str, Any]", context) if isinstance(context, dict) else {}
    estimate = (context_estimate(raw["tokens"], raw["window"])
                if provider_kind == "cc" and set(raw) == {"tokens", "window"} else None)
    if estimate is None:
        return dict(last_input_tokens=None, context_window=None, context_pct=None, source="unknown", window_reported_at=None)
    return dict(last_input_tokens=estimate[0], context_window=estimate[1], context_pct=estimate[2], source=CONTEXT_SOURCE,
                window_reported_at=now)


def binding_attributes(runtime: object) -> dict[str, Any]:
    method = getattr(runtime, "session_binding", None)
    raw: Any = method() if callable(method) else {}
    values = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    return {key: _safe_binding(values.get(key)) for key in ("profile", "model", "effort")} | {
        "writable": values.get("writable") if isinstance(values.get("writable"), bool) else None}


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
    provider_kind = identity["runtime_kind"] if identity and identity["runtime_kind"] in BUILTIN_PEER_KINDS else "unknown"
    fingerprint = identity["adapter_fingerprint"] if identity else None
    version_match = _ADAPTER_VERSION.fullmatch(fingerprint) if isinstance(fingerprint, str) else None
    adapter_version = version_match.group(2) if version_match and version_match.group(1) == provider_kind else None
    usage_candidate: object = provider_candidate
    context: object = None
    if isinstance(provider_candidate, dict) and "context" in provider_candidate:  # rides beside the usage keys, never in them
        usage_keys = dict(cast("dict[str, Any]", provider_candidate))
        context = usage_keys.pop("context")
        usage_candidate = usage_keys
    detail = dict(handle=accepted["handle"], generation=accepted["generation"], observed_at=now,
                  **_context_fields(context, provider_kind, now),
                  configured_model=binding_detail["model"], resolved_model=None, schema_version=3,
                  provider_kind=provider_kind, captured_at=now, adapter_version=adapter_version,
                  provider_cli_version=None, configured_binding=binding_detail,
                  usage=validate_provider_usage(usage_candidate, provider_kind),
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
                    valid_old = valid_observed_at(old_time)
            except (TypeError, ValueError):
                pass
        should_advance = not valid_old or (float(now), seq) > (float(cast(int | float, old_time)), pointer)
    if should_advance:
        conn.execute("UPDATE bridge_session_monitor_stats SET latest_telemetry_seq=? WHERE session_handle=?",
                     (seq, accepted["handle"]))
