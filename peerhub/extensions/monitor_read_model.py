"""Assemble a grouped monitor frame from one read-only SQLite snapshot."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, cast

from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
from peerhub.core.peer_kinds import API_PEER_KINDS, peer_kind
from peerhub.extensions import monitor_activity, monitor_alerts, monitor_quota
from peerhub.extensions.billing_port import billing_consumer
from peerhub.extensions.monitor_queries import PEER_CAP, delivery_scope_rows, peer_rows, recent_quota_payloads
from peerhub.extensions.monitor_sessions import current_profile, monitor_workspace, read_session_groups


def _decode_text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def _read_peer_group(conn: sqlite3.Connection, groups: list[str], output: dict[str, Any],
                     errors: dict[str, str]) -> tuple[list[dict[str, Any]], bool, int]:
    peers: list[dict[str, Any]] = []
    peer_sample_truncated = False
    peers_total = 0
    try:
        rows = peer_rows(conn) if bool(set(groups) & {"overview", "peers"}) else []
        peer_sample_truncated = len(rows) > PEER_CAP
        peers = [{"peer_id": str(row[0]), "display_name": row[1], "adapter_ref": row[2]} for row in rows[:PEER_CAP]]
        if peer_sample_truncated:  # what "showing N of M peers" needs
            peers_total = int(rows[0][3])
        if "overview" in groups:
            output["overview"] = {"status": "OK", "data": {"peers": peers, "sample_size": len(peers),
                                                                 "truncated": peer_sample_truncated,
                                                                 **({"peers_total": peers_total}
                                                                    if peer_sample_truncated else {})}}
    except sqlite3.Error as exc:
        errors["overview"] = type(exc).__name__
        if "overview" in groups:
            output["overview"] = {"status": "ERROR", "data": None}
    return peers, peer_sample_truncated, peers_total


def _read_scope_groups(conn: sqlite3.Connection, groups: list[str], output: dict[str, Any], errors: dict[str, str],
                       start_day: str, end_day: date) -> None:
    scopes: list[dict[str, Any]] = []
    scope_rows: list[sqlite3.Row] = []
    scope_truncated_peers: set[str] = set()
    try:
        needs_scopes = bool(set(groups) & {"execution", "coverage", "usage", "sessions"})
        if needs_scopes:
            scope_rows = delivery_scope_rows(conn, start_day, end_day.isoformat())
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


def _assemble_peers(conn: sqlite3.Connection, db_path: str, workspace: str | Path | None,
                    peers: list[dict[str, Any]], peer_sample_truncated: bool, peers_total: int,
                    sessions: list[dict[str, Any]], truncated: bool, quota: monitor_quota.QuotaSnapshot,
                    output: dict[str, Any], errors: dict[str, str], read_at: float) -> dict[str, list[str]]:
    latest_facts_report = quota.facts_report
    config_workspace = monitor_workspace(db_path, workspace)
    peer_records: list[dict[str, Any]] = []
    mapped_pools: dict[str, list[str]] = {}
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
                cp = current_profile(prof_name, config_workspace)
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

        peer_observations = [o for o in quota.observations if o["peer_id"] == pid]
        if is_mapped:
            mapped_pools[pid] = configured_pools
        q_data = monitor_quota.peer_quota(peer_observations, mapped_pools.get(pid), has_session=s_item is not None)
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
                    "window_tokens": tel_ctx.get("window_tokens"),
                    "observed_at": tel_ctx.get("observed_at"),  # the end of the ask the estimate belongs to
                    "state": tel_ctx.get("state"),
                }
        peer_record = {
            "peer_id": pid,
            "quota": q_data,
            "profile": prof_data,
            "session": sess_data,
            "context": ctx_data,
        }
        kind = peer_kind(p["adapter_ref"], pid)
        if kind in API_PEER_KINDS and (billing := billing_consumer()) is not None:
            peer_record["billing"] = billing.peer_billing(conn, pid, kind, read_at)
        peer_records.append(peer_record)
    from peerhub.extensions.adapters.profiles import packaged_defaults_meta
    defaults_meta = packaged_defaults_meta()
    if latest_facts_report is None:
        try:
            f_rows = recent_quota_payloads(conn)
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
            **({"peers_total": peers_total} if peer_sample_truncated else {}),
            "defaults_revision": defaults_meta.get("defaults_revision"),
            "observed_at": defaults_meta.get("observed_at"),
            "facts_report": latest_facts_report,
        },
    }
    quota.facts_report = latest_facts_report
    return mapped_pools


def render_monitor_frame(db_path: str, groups: list[str], *, read_at: float | None = None,
                         trace_callback: Callable[[str], None] | None = None,
                         workspace: str | Path | None = None, human: bool = False,
                         liveness: Callable[..., object] | None = None,
                         process_probe: Callable[[int], object] | None = None,
                         preference_source: str | None = None,
                         lead_reader: monitor_activity.LeadReader | None = None) -> dict[str, Any]:
    """Read grouped monitor facts from one query-only SQLite snapshot (five bounded query families).

    `lead_reader` (composed in peerhub/cli) gives the lead sessions; it runs only when `activity` was read, after the
    read transaction ends. Without it no file is read, every lead has `session: None` and `leads_read` is None."""
    import time
    now = time.time() if read_at is None else read_at
    end_day = datetime.fromtimestamp(now, timezone.utc).date()
    start_day = (end_day - timedelta(days=29)).isoformat()
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # A stored "\ud800" escape makes json_extract return invalid UTF-8; decode it with U+FFFD instead of failing the fetch.
    conn.text_factory = _decode_text
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
        peers, peer_sample_truncated, peers_total = _read_peer_group(conn, groups, output, errors)
        sessions, truncated = read_session_groups(conn, groups, output, errors, db_path, workspace)
        _read_scope_groups(conn, groups, output, errors, start_day, end_day)
        quota = monitor_quota.read_quota_groups(conn, groups, output, errors, now)
        mapped_pools = (_assemble_peers(conn, db_path, workspace, peers, peer_sample_truncated, peers_total,
                                      sessions, truncated, quota, output, errors, now) if "peers" in groups else {})
        text_on = os.environ.get("PEERHUB_MONITOR_TEXT", "").strip().lower() != "off"
        activity_data = (monitor_activity.read_activity_group(conn, db_path, now, human=human, text_on=text_on,
                                                              liveness=liveness, process_probe=process_probe,
                                                              output=output, errors=errors)
                         if "activity" in groups else None)
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
        human_observations, quota_age = (monitor_quota.read_human_quota(conn, groups, quota.observations, now)
                                         if human else ([], {}))
        conn.execute("ROLLBACK")
        if activity_data is not None:  # only when activity was read, after the read transaction
            monitor_activity.merge_activity_leads(activity_data, lead_reader, now=now, text_on=text_on,
                                                  process_probe=process_probe)
        capacity_ok = bool(set(groups) & {"capacity", "peers"}) and "capacity" not in errors
        pools = (monitor_quota.pool_states(quota.observations, quota.last_known, now, quota.sample_counts)
                 if capacity_ok else None)
        alert_sources = monitor_alerts.alert_source_status(groups, errors, activity_data, quota, pools, preference_source)
        alerts = monitor_alerts.build_alerts(asks=activity_data["asks"] if activity_data else None, pools=pools,
                                             accounts=quota.accounts if capacity_ok else None,
                                             facts_report=quota.facts_report if capacity_ok else None,
                                             failures=[(key, str(value).split(":")[0]) for key, value in errors.items()
                                                       if key != "scope_cap"],
                                             now=now)
        return {"schema_version": "2.0", "status": "PARTIAL" if errors else "OK", "read_at": now,
                "groups": output, "errors": errors, "alert_sources": alert_sources, "alerts": alerts,
                "min_quota": monitor_quota.min_quota(pools) if pools is not None else None, "query_families": select_count,
                **({"_human_observations": human_observations, "_human_peer_pools": mapped_pools,
                    "_human_quota_accounts": quota.accounts, **quota_age} if human else {})}
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.close()
