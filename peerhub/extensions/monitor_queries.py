"""Bounded SQLite queries for monitor projections, executed on the caller's read snapshot."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from peerhub.extensions.delivery_reads import invocation_marker_join, invocation_process_fields
from peerhub.extensions.observation_model import QUOTA_MAX_AGE_SECONDS, QUOTA_PROJECTION_SAMPLE_COUNT_SQL


PEER_CAP = 8  # the six built-in peers (ag, ca, cc, cx, ga, oa) and two more; more is reported as "showing N of M peers"


_ROW_COLUMNS = ("o.capture_seq,o.observation_id,o.subject_ref,o.resource_pool_ref,o.kind,o.source,o.state,"
                "o.payload_json,o.observed_at,o.captured_at,o.effective_at_us,p.provider,p.kind AS pool_kind ")
_ROW_JOINS = ("FROM observation_rollups r JOIN observations o ON o.capture_seq=r.latest_capture_seq "
              "LEFT JOIN resource_pools p ON p.resource_pool_id=o.resource_pool_ref ")


def _guarded(json_column: str, path: str, value: str) -> str:
    """`value` (an expression over json_extract/json_type of `json_column`) only when the column is valid JSON; nested CASE so
    SQLite never evaluates a JSON function on malformed text (which would abort the whole statement)."""
    return f"CASE WHEN json_valid({json_column}) THEN {value.format(c=json_column, p=path)} END"


# The capacity statement includes sample counts for every quota row without another SELECT.
_SAMPLES_COLUMN = ",CASE WHEN o.kind='quota' THEN " + QUOTA_PROJECTION_SAMPLE_COUNT_SQL + " END AS projection_samples "
# Quota rows keep their 100-row cap; account rows ride in the same statement with a cap of their own, so neither starves the other.
_CAPACITY_SQL = ("SELECT * FROM (SELECT " + _ROW_COLUMNS + _SAMPLES_COLUMN + _ROW_JOINS +
                 "WHERE r.kind IN ('quota','rate_limit','reset_credit') "
                 "ORDER BY o.subject_ref,r.pool_key LIMIT 101) UNION ALL SELECT * FROM (SELECT " + _ROW_COLUMNS +
                 ",NULL AS projection_samples " + _ROW_JOINS +
                 "WHERE r.kind='account' ORDER BY o.subject_ref LIMIT 51)")  # 51 = one past ACCOUNT_ROW_CAP, to detect truncation
ACCOUNT_ROW_CAP = 50


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


# ---- activity (#374): running and recent asks from one claims-first statement --------------------------------------------
ALERT_WINDOW_S = 1800  # ONE window: the a1 rule, the a2 overdue rule, the order of ended rows and the text selection
ACTIVITY_SPAN_S = 86_400  # scopes whose claim expired (or was released) in the last 24 hours
ACTIVITY_SCOPE_CAP = 1000
SUPERSEDED_CAP = 50
TEXT_MAX_CHARS = 262_144  # a longer text is never fetched: "(long text)", so nothing is cut before redaction
TEXT_NEWEST_ROWS = 20


# Evidence kinds (bridge.py) -> state, for a delivery without a stored result. The newest kind that is not bookkeeping decides.
START_KINDS = ("about_to_invoke", "launch_reserved", "started", "session_call", "output")  # the provider may be running
PRESTART_KINDS = ("attempt_created",)  # nothing invoked yet


_BOOKKEEPING_KINDS = ("caller", "offset_acked", "response_appended", "session_telemetry", "execution_metadata", "duplicate_terminal",
                      "conflicting_terminal", "reconcile_registered", "control_applied")


def _sql_list(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)  # constants of this module only


_R = "tx.result_json"
_LATEST_INDEX = "idx_bd_scope_seq"
_SUPERSEDED_INDEX = "idx_bd_scope_unresolved"
_ACTIVITY_SQL = (
    "WITH s AS (SELECT c.stream_id,c.peer_id,c.generation AS c_generation,c.expires_at,c.lease_sec,c.workspace_generation AS c_wsgen,"
    "ROW_NUMBER() OVER (ORDER BY c.expires_at DESC,c.stream_id,c.peer_id) AS scope_rank,COUNT(*) OVER () AS scope_total "
    "FROM bridge_claims c WHERE c.expires_at>=:since), "
    # One seek per scope, plus a range of unresolved deliveries. Legacy stores
    # can execute the same statement before the Bridge installs the indexes.
    f"latest AS (SELECT s.*,s.scope_rank<=:scope_cap AS is_detail,(SELECT d.delivery_id FROM bridge_deliveries d INDEXED BY {_LATEST_INDEX} "
    "WHERE d.stream_id=s.stream_id AND d.peer_id=s.peer_id ORDER BY d.seq DESC LIMIT 1) AS latest_id "
    "FROM s), "
    # Beyond the detail cap, only the latest deliveries without a result can count as running.
    "pick AS (SELECT l.*,d.delivery_id,d.seq,d.certainty,0 AS sup FROM latest l "
    "CROSS JOIN bridge_deliveries d ON d.delivery_id=l.latest_id WHERE l.is_detail OR d.result_json IS NULL UNION ALL "
    f"SELECT l.*,d.delivery_id,d.seq,d.certainty,1 AS sup FROM latest l CROSS JOIN bridge_deliveries d INDEXED BY {_SUPERSEDED_INDEX} "
    "ON d.stream_id=l.stream_id AND d.peer_id=l.peer_id "
    "WHERE l.is_detail AND d.certainty IN ('STARTED','MAY_HAVE_STARTED') AND d.result_json IS NULL AND d.delivery_id<>l.latest_id), "
    "ranked AS (SELECT pick.*,ROW_NUMBER() OVER (PARTITION BY pick.sup ORDER BY pick.seq DESC) AS sup_rank,"
    "SUM(pick.sup) OVER () AS sup_total FROM pick), "
    # Keep the ranked rows on the outside so SQLite seeks each kept delivery by ID.
    "kept AS (SELECT k.*,d.claim_generation,d.record_id,d.result_json,d.terminal_at FROM ranked k "
    "CROSS JOIN bridge_deliveries d ON d.delivery_id=k.delivery_id WHERE k.sup=0 OR k.sup_rank<=:sup_cap), "
    "ev AS (SELECT kept.*,"
    "COALESCE(json_extract(CASE WHEN json_valid(m.detail) THEN m.detail END,'$.claim_generation'),"
    "kept.claim_generation) AS invocation_generation,"
    "(SELECT e.kind FROM bridge_evidence e WHERE e.delivery_id=kept.delivery_id AND e.kind NOT IN (" + _sql_list(_BOOKKEEPING_KINDS) + ") "
    "ORDER BY e.seq DESC LIMIT 1) AS last_kind,"
    "CASE WHEN kept.is_detail THEN (SELECT e.detail FROM bridge_evidence e WHERE e.delivery_id=kept.delivery_id "
    "AND e.kind='session_call' AND e.certainty='STARTED' ORDER BY e.seq DESC LIMIT 1) END AS start_detail,"
    + invocation_process_fields("kept.delivery_id") + ","
    "(SELECT e.detail FROM bridge_evidence e WHERE e.delivery_id=kept.delivery_id AND e.kind='processes_dead' "
    "ORDER BY e.seq DESC LIMIT 1) AS abandonment,"
    "COALESCE(CAST(strftime('%s',kept.terminal_at) AS REAL),kept.expires_at) AS end_bound,"
    "CASE WHEN kept.result_json IS NULL THEN NULL WHEN json_valid(kept.result_json) THEN 1 ELSE 0 END AS result_ok FROM kept "
    + invocation_marker_join("kept.delivery_id") + "), "
    # Open = may be `run` or `overdue`; its text is always read. Ended rows are ranked by their (bounded) end time.
    "op AS (SELECT ev.*,CASE WHEN ev.sup=0 AND ev.result_json IS NULL AND ev.certainty IN ('NOT_STARTED','STARTED') "
    "AND ev.expires_at>=:window_start "  # an overdue row is open only for ALERT_WINDOW_S after its lease expired
    "AND (ev.last_kind IS NULL OR ev.last_kind IN (" + _sql_list(START_KINDS + PRESTART_KINDS) + ")) THEN 1 ELSE 0 END AS is_open FROM ev), "
    # Superseded rows rank apart, so they never take the "20 newest ended" text ranks of real ended rows.
    "rk AS (SELECT op.*,ROW_NUMBER() OVER (PARTITION BY op.is_detail,op.is_open,op.sup ORDER BY op.end_bound DESC,op.seq DESC) AS end_rank FROM op), "
    "tx AS (SELECT rk.*,CASE WHEN rk.is_detail AND :text_on=1 AND "
    "(rk.is_open=1 OR (rk.sup=0 AND rk.end_bound>=:window_start) OR rk.end_rank<=:newest) "
    "THEN 1 ELSE 0 END AS want_text,"
    # RECENT's rows (the newest ended rows, as for the text) are joined to the newest session of their stream (#412).
    "CASE WHEN rk.is_detail AND rk.is_open=0 AND rk.sup=0 AND rk.end_rank<=:newest THEN 1 ELSE 0 END AS want_join FROM rk), "
    "se AS (SELECT h.stream_id,h.peer_id,h.session_handle,h.session_generation,h.runtime_kind,h.state,h.tombstoned_at,"
    "ROW_NUMBER() OVER (PARTITION BY h.stream_id,h.peer_id ORDER BY h.session_generation DESC) AS srn "
    "FROM bridge_session_history h WHERE EXISTS (SELECT 1 FROM tx WHERE tx.want_join=1 AND tx.stream_id=h.stream_id "
    "AND tx.peer_id=h.peer_id)) "
    "SELECT tx.stream_id,tx.peer_id,tx.c_generation,tx.expires_at,tx.lease_sec,"
    "CASE WHEN :wsgen IS NULL THEN 1 WHEN tx.c_wsgen=:wsgen THEN 1 ELSE 0 END AS wsgen_match,"
    "scope.scope_total,tx.sup_total,tx.is_detail,tx.seq,tx.certainty,tx.invocation_generation AS claim_generation,"
    "tx.terminal_at,tx.sup,tx.last_kind,tx.start_detail,"
    "tx.caller_detail,tx.provider_detail,tx.abandonment,tx.result_ok,tx.want_text,r.record_id,"
    "CASE WHEN tx.result_ok=1 THEN json_type(" + _R + ",'$.response') END AS response_type,"
    "CASE WHEN tx.result_ok=1 THEN CASE WHEN json_type(" + _R + ",'$.response')='text' "
    "THEN length(json_extract(" + _R + ",'$.response')) END END AS reply_len,"
    "CASE WHEN tx.result_ok=1 THEN CASE WHEN json_type(" + _R + ",'$.response')='text' "
    # Byte length of the trimmed reply: length() of text stops at a NUL, so a reply starting with NUL would look empty.
    "THEN length(CAST(trim(json_extract(" + _R + ",'$.response'),char(32,9,10,11,12,13)) AS BLOB)) END END AS reply_nonblank,"
    "CASE WHEN tx.result_ok=1 THEN json_type(" + _R + ",'$.empty_response') END AS empty_type,"
    "CASE WHEN tx.result_ok=1 THEN json_type(" + _R + ",'$.error') END AS error_type,"
    "CASE WHEN tx.result_ok=1 THEN CASE WHEN json_type(" + _R + ",'$.error.code')='text' "
    "THEN substr(json_extract(" + _R + ",'$.error.code'),1,200) END END AS error_code,"
    "CASE WHEN tx.result_ok=1 THEN CASE WHEN json_type(" + _R + ",'$.denied_actions')='array' THEN CASE WHEN "
    "length(json_extract(" + _R + ",'$.denied_actions'))<=8192 THEN json_extract(" + _R + ",'$.denied_actions') END END END AS denied_json,"
    "CASE WHEN tx.result_ok=1 THEN CASE WHEN json_type(" + _R + ",'$.usage.input_tokens')='integer' "
    "THEN json_extract(" + _R + ",'$.usage.input_tokens') END END AS usage_in,"
    "CASE WHEN tx.result_ok=1 THEN CASE WHEN json_type(" + _R + ",'$.usage.output_tokens')='integer' "
    "THEN json_extract(" + _R + ",'$.usage.output_tokens') END END AS usage_out,"
    "CASE WHEN tx.result_ok=1 THEN CASE WHEN json_type(" + _R + ",'$.usage.cached_input_tokens')='integer' "
    "THEN json_extract(" + _R + ",'$.usage.cached_input_tokens') END END AS usage_cache_read,"
    "CASE WHEN tx.result_ok=1 THEN CASE WHEN json_type(" + _R + ",'$.usage.cache_write_tokens')='integer' "
    "THEN json_extract(" + _R + ",'$.usage.cache_write_tokens') END END AS usage_cache_write,"
    "se.session_generation AS ses_generation,se.runtime_kind AS ses_provider,se.session_handle AS ses_handle,"
    "se.state AS ses_state,se.tombstoned_at AS ses_tombstoned_at,ms.reuse_count AS ses_reuse,ms.fresh_count AS ses_fresh,"
    "te.detail AS ses_detail,"
    "r.record_id IS NOT NULL AS has_record,"
    + _guarded("r.metadata_json", "$.binding",
               "CASE WHEN json_type({c},'{p}')='text' THEN substr(json_extract({c},'{p}'),1,512) END") + " AS binding,"
    + _guarded("r.body_json", "$", "CASE WHEN json_type({c})='text' THEN length(json_extract({c},'{p}')) END") + " AS ask_len,"
    "CASE WHEN tx.want_text=1 THEN " + _guarded(
        "r.body_json", "$", "CASE WHEN json_type({c})='text' THEN CASE WHEN length(json_extract({c},'{p}'))<=:max_text "
        "THEN json_extract({c},'{p}') END END") + " END AS ask_text,"
    "CASE WHEN tx.want_text=1 AND tx.result_ok=1 THEN CASE WHEN json_type(" + _R + ",'$.response')='text' THEN CASE WHEN "
    "length(json_extract(" + _R + ",'$.response'))<=:max_text THEN json_extract(" + _R + ",'$.response') END END END AS reply_text "
    # Carry scope coverage even when no sampled claim has a delivery.
    "FROM (SELECT scope_total FROM s LIMIT 1) scope LEFT JOIN tx ON 1 "
    "LEFT JOIN records r ON tx.is_detail AND r.record_id=tx.record_id "
    "LEFT JOIN se ON tx.want_join=1 AND se.stream_id=tx.stream_id AND se.peer_id=tx.peer_id AND se.srn=1 "
    "LEFT JOIN bridge_session_monitor_stats ms ON ms.session_handle=se.session_handle "
    "LEFT JOIN bridge_evidence te ON te.seq=ms.latest_telemetry_seq AND te.kind='session_telemetry' "
    "ORDER BY tx.sup,tx.seq DESC"
)
# The same statement for a store without the session tables: no join, every session column NULL.
_ACTIVITY_SQL_NO_SESSIONS = (
    _ACTIVITY_SQL.replace(_ACTIVITY_SQL[_ACTIVITY_SQL.index("se AS (SELECT h.stream_id"):_ACTIVITY_SQL.index("SELECT tx.stream_id")],
                          "se AS (SELECT NULL AS stream_id,NULL AS peer_id,NULL AS session_handle,NULL AS session_generation,"
                          "NULL AS runtime_kind,NULL AS state,NULL AS tombstoned_at,NULL AS srn WHERE 0) ")
    .replace("LEFT JOIN bridge_session_monitor_stats ms ON ms.session_handle=se.session_handle ",
             "LEFT JOIN (SELECT NULL AS session_handle,NULL AS reuse_count,NULL AS fresh_count,NULL AS latest_telemetry_seq "
             "WHERE 0) ms ON ms.session_handle=se.session_handle ")
)


def _activity_params(now: float, workspace_generation: str | None, text_on: bool) -> dict[str, Any]:
    return {"since": now - ACTIVITY_SPAN_S, "scope_cap": ACTIVITY_SCOPE_CAP,
            "sup_cap": SUPERSEDED_CAP, "window_start": now - ALERT_WINDOW_S, "newest": TEXT_NEWEST_ROWS,
            "text_on": 1 if text_on else 0, "wsgen": workspace_generation, "max_text": TEXT_MAX_CHARS}


_OBSERVATION_TABLES = ("observations", "observation_rollups", "resource_pools")


def no_observation_tables(conn: sqlite3.Connection, exc: sqlite3.Error) -> bool:
    """True only when the capacity read failed because NONE of the observation tables exists (a store that never had a quota
    refresh). A missing table among existing ones, an unreadable table, a malformed row or a locked or corrupt store is False.
    PRAGMA table_info reads the schema without a SELECT, so the pinned statement caps are unchanged."""
    if not isinstance(exc, sqlite3.OperationalError) or not str(exc).startswith("no such table:"):
        return False
    try:
        return all(not conn.execute(f"PRAGMA table_info({name})").fetchall() for name in _OBSERVATION_TABLES)
    except sqlite3.Error:
        return False


def last_measured_account_rows(conn: sqlite3.Connection, failed: list[str]) -> sqlite3.Cursor:
    marks = ",".join("?" for _ in failed)
    return conn.execute(
        "SELECT * FROM (SELECT " + _ROW_COLUMNS + ",ROW_NUMBER() OVER (PARTITION BY o.subject_ref ORDER BY o.effective_at_us DESC,"
        "o.capture_seq DESC) AS rn FROM observations o LEFT JOIN resource_pools p ON p.resource_pool_id=o.resource_pool_ref "
        f"WHERE o.kind='account' AND o.state='MEASURED' AND o.subject_ref IN ({marks})) WHERE rn=1", failed)


def latest_account_quota_row(conn: sqlite3.Connection, peer_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT " + _ROW_COLUMNS + "FROM observations o LEFT JOIN resource_pools p "
                        "ON p.resource_pool_id=o.resource_pool_ref WHERE o.subject_ref=? AND o.kind='quota' "
                        "ORDER BY o.effective_at_us DESC,o.capture_seq DESC LIMIT 1", (peer_id,)).fetchone()


def quota_age_rows(conn: sqlite3.Connection, read_us: int) -> sqlite3.Cursor:
    return conn.execute("SELECT " + _ROW_COLUMNS + _ROW_JOINS +
                        "WHERE r.kind IN ('quota','account') AND o.effective_at_us<=?", (read_us,))


def peer_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT peer_id,display_name,adapter_ref,(SELECT COUNT(*) FROM peers) FROM peers "
                        "ORDER BY peer_id LIMIT ?", (PEER_CAP + 1,)).fetchall()


def session_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT h.session_handle,h.stream_id,h.peer_id,h.session_generation,h.runtime_kind,h.state,h.resumable,"
        "h.last_seen,h.created_at,h.tombstoned_at,COALESCE(s.binding_json, h.binding) AS binding_json,"
        "s.latest_telemetry_seq,s.reuse_count,s.fresh_count,e.detail,"
        "(SELECT COUNT(*) FROM bridge_session_history) AS total_sessions "
        "FROM (SELECT rowid,session_handle,stream_id,peer_id,session_generation,runtime_kind,binding,state,resumable,last_seen,created_at,tombstoned_at "
        "FROM bridge_session_history ORDER BY rowid DESC LIMIT 101) h "
        "LEFT JOIN bridge_session_monitor_stats s ON s.session_handle=h.session_handle "
        "LEFT JOIN bridge_evidence e ON e.seq=s.latest_telemetry_seq AND e.kind='session_telemetry' "
        "ORDER BY h.rowid DESC"
    ).fetchall()


def delivery_scope_rows(conn: sqlite3.Connection, start_day: str, end_day: str) -> list[sqlite3.Row]:
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
    return conn.execute(
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
        (*scope_args, start_day, end_day),
    ).fetchall() if branches else []


def recent_quota_payloads(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT payload_json FROM observations "
        "WHERE kind='quota' "
        "ORDER BY capture_seq DESC LIMIT 20"
    ).fetchall()


def activity_rows(conn: sqlite3.Connection, now: float, generation: str | None, text_on: bool) -> list[sqlite3.Row]:
    params = _activity_params(now, generation, text_on)

    def read(sql: str) -> list[sqlite3.Row]:
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            if str(exc) not in (f"no such index: {_LATEST_INDEX}", f"no such index: {_SUPERSEDED_INDEX}"):
                raise
            legacy_sql = sql.replace(f" INDEXED BY {_LATEST_INDEX}", "").replace(f" INDEXED BY {_SUPERSEDED_INDEX}", "")
            return conn.execute(legacy_sql, params).fetchall()

    try:
        try:
            return read(_ACTIVITY_SQL)
        except sqlite3.OperationalError as exc:
            if not str(exc).startswith("no such table: bridge_session"):
                raise  # a store without the session tables (#412) reads the asks without the join
            return read(_ACTIVITY_SQL_NO_SESSIONS)
    except sqlite3.OperationalError as exc:
        if "no such table: bridge_claims" not in str(exc):
            raise
        return []  # no Bridge activity was ever recorded in this store


def quota_evidence_rows(conn: sqlite3.Connection, ids: list[str], now: float) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT o.observation_id, o.capture_seq, o.payload_json, m.payload_json AS prior_payload, "
        "m.effective_at_us AS prior_at_us, m.observed_at AS prior_observed_at, " + QUOTA_PROJECTION_SAMPLE_COUNT_SQL + " AS samples "
        "FROM observations o LEFT JOIN observations m ON m.capture_seq=(SELECT s.capture_seq FROM observations s "
        "WHERE o.state='ERROR' AND o.kind='quota' AND o.resource_pool_ref IS NOT NULL "
        "AND s.subject_ref=o.subject_ref AND s.resource_pool_ref=o.resource_pool_ref AND s.kind='quota' "
        "AND s.state='MEASURED' AND json_valid(s.payload_json) "
        "AND s.effective_at_us>? "
        "AND (s.effective_at_us,s.capture_seq)<(o.effective_at_us,o.capture_seq) "
        "ORDER BY s.effective_at_us DESC,s.capture_seq DESC LIMIT 1) "
        "WHERE o.observation_id IN (" + ",".join("?" for _ in ids) + ")",
        [round((now - QUOTA_MAX_AGE_SECONDS) * 1_000_000), *ids],
    ).fetchall()


def capacity_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(_CAPACITY_SQL).fetchall()
