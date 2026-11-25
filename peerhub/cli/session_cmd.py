"""Read-only retained session commands."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from time import time
from typing import TYPE_CHECKING, Any, cast

from peerhub.cli.support import error_line

if TYPE_CHECKING:
    from peerhub.cli.arguments import Subparsers


_SESSION_CONTEXT_FIELDS = ("ctx_est_pct", "ctx_est_tokens", "ctx_age_seconds")


def register_session_parser(subparsers: Subparsers) -> None:
    session_parser = subparsers.add_parser("session", help="Inspect retained sessions (read-only)")
    session_sub = session_parser.add_subparsers(dest="action", required=True)
    session_list = session_sub.add_parser("list", help="List retained sessions")
    session_list.add_argument("--stream", help="Only sessions of this stream")
    session_list.add_argument("--peer", help="Only sessions of this peer")
    session_list.add_argument("--limit", type=_session_page_limit, default=20, help="Maximum sessions per page (default %(default)s)")
    session_list.add_argument("--cursor", help="Continue after the next_cursor of the previous page")
    session_list.add_argument("--format", choices=["auto", "table", "json"], default="auto", help=argparse.SUPPRESS)
    session_show = session_sub.add_parser("show", help="Show a retained session by PeerHub handle")
    session_show.add_argument("handle", help="PeerHub session handle from an ask receipt or session list")
    session_show.add_argument("--format", choices=["auto", "table", "json"], default="auto", help=argparse.SUPPRESS)


def _session_page_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer from 1 to 100") from exc
    if not 1 <= limit <= 100:
        raise argparse.ArgumentTypeError("limit must be from 1 to 100")
    return limit


def _encode_session_cursor(cursor: dict[str, Any]) -> str:
    raw = json.dumps(cursor, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_session_cursor(raw_cursor: str, *, store_id: str, lineage: str, filters: dict[str, str | None]) -> dict[str, Any]:
    try:
        decoded: object = json.loads(base64.urlsafe_b64decode(raw_cursor + "=" * (-len(raw_cursor) % 4)))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid session cursor") from exc
    if not isinstance(decoded, dict):
        raise ValueError("invalid session cursor")
    data = cast(dict[str, Any], decoded)
    if (data.get("v") != 1 or data.get("store") != store_id or data.get("lineage") != lineage
            or data.get("filters") != filters or data.get("order") != "scope-generation-handle-v1"
            or not isinstance(data.get("high_water"), int) or not isinstance(data.get("after"), list)
            or len(data["after"]) != 4):
        raise ValueError("session cursor does not match store, lineage, filters, or ordering")
    return data


def _session_context_text(session: dict[str, Any]) -> str:
    percent, age = session["ctx_est_pct"], session["ctx_age_seconds"]
    if percent is None or age is None:
        return ""
    from peerhub.cli.monitor_layout import compact_age

    return f"ctx {percent}% ({compact_age(age)} ago)"


def run_session(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.is_file():
        from peerhub.cli.store_select import report_missing_store
        db_source = getattr(args, "db_source", None)
        report_missing_store(args.db, json_output=args.format == "json", selected=db_source in ("explicit", "env"), source=db_source)
        return 5
    from contextlib import closing
    from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
    from peerhub.extensions.observation_model import read_only_authorizer

    stat = db_path.stat()
    identity = f"{db_path.resolve()}|{stat.st_dev}|{stat.st_ino}".casefold()
    store_id = hashlib.sha256(identity.encode()).hexdigest()
    generation_path = db_path.parent / "workspace.generation"
    lineage = generation_path.read_text(encoding="utf-8").strip() if generation_path.is_file() else ""
    filters = {"stream": getattr(args, "stream", None), "peer": getattr(args, "peer", None)}
    use_json = args.format == "json"

    def emit(payload: dict[str, Any]) -> int:
        if use_json:
            print(json.dumps(payload, ensure_ascii=True, indent=2))
        if payload.get("status") == "NOT_FOUND":
            error_line("session_not_found", f"Session not found: {args.handle}", "peerhub session list")
        elif not use_json and args.action == "show":
            session = payload["session"]
            for key, value in session.items():
                if key not in _SESSION_CONTEXT_FIELDS:
                    print(f"{key}: {value}")
            context = _session_context_text(session)
            if context:
                print(context)
        elif not use_json:
            rows = payload.get("items", [])
            if not rows:
                print("(no sessions)")
            else:
                keys = ("handle", "stream_id", "peer_id", "generation", "state", "profile", "model", "effort", "resumable", "last_seen")
                def cell(row: dict[str, Any], key: str) -> str:
                    if key in ("profile", "model", "effort"):
                        value = row.get("configured_binding", {}).get(key)
                    else:
                        value = row.get(key)
                    if key == "last_seen" and isinstance(value, (int, float)):
                        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
                    return "-" if value is None or value == "" else str(value)
                widths = [max(len(key), *(len(cell(row, key)) for row in rows)) for key in keys]
                print("  ".join(key.upper().ljust(widths[i]) for i, key in enumerate(keys)))
                print("  ".join("-" * width for width in widths))
                for row in rows:
                    line = "  ".join(cell(row, key).ljust(widths[i]) for i, key in enumerate(keys))
                    context = _session_context_text(row)
                    print(f"{line}  {context}" if context else line)
                if payload.get("next_cursor"):
                    print(f"next_cursor: {payload['next_cursor']}")
        status = payload.get("status")
        return 1 if status == "NOT_FOUND" else 4 if status == "ERROR" else 0

    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
                raise sqlite3.OperationalError("session inspection connection is not read-only")
            conn.set_authorizer(read_only_authorizer(allow_transactions=True))
            conn.execute("BEGIN")
            conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if schema_version > SUPPORTED_SCHEMA_VERSION:
                raise SchemaVersionError(future_schema_message(schema_version, SUPPORTED_SCHEMA_VERSION))
            has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bridge_session_history'").fetchone()
            has_stats = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bridge_session_monitor_stats'").fetchone()
            now = time()
            if args.action == "show":
                extra_select = (",s.binding_json,s.fresh_count,s.reuse_count,s.counts_complete,e.detail AS telemetry_detail "
                                "FROM bridge_session_history h LEFT JOIN bridge_session_monitor_stats s ON s.session_handle=h.session_handle "
                                "LEFT JOIN bridge_evidence e ON e.seq=s.latest_telemetry_seq AND e.kind='session_telemetry'") if has_stats else (
                                ",NULL AS binding_json,NULL AS fresh_count,NULL AS reuse_count,NULL AS counts_complete,NULL AS telemetry_detail "
                                "FROM bridge_session_history h")
                row = None if not has_table else conn.execute(
                    "SELECT h.session_handle,h.stream_id,h.peer_id,h.session_generation,h.runtime_kind,h.state,h.resumable,h.last_seen,"
                    "h.context_watermark,h.bootstrap_truncated,h.context_watermark_version,h.created_at,h.tombstoned_at" +
                    extra_select + " WHERE h.session_handle=?", (args.handle,)).fetchone()
                if row is None:
                    return emit({"schema_version": "1.0", "status": "NOT_FOUND", "handle": args.handle})
                return emit({"schema_version": "1.0", "status": "OK", "session": _session_public_row(row, now=now)})
            if not has_table:
                return emit({"schema_version": "1.0", "store_selection": {"path": str(db_path), "source": getattr(args, "db_source", "unknown")},
                             "items": [], "next_cursor": None})
            cursor = None if not args.cursor else _decode_session_cursor(args.cursor, store_id=store_id, lineage=lineage, filters=filters)
            high_water = int(cursor["high_water"]) if cursor else int(
                conn.execute("SELECT COALESCE(MAX(rowid),0) FROM bridge_session_history").fetchone()[0])
            where = ["h.rowid <= ?"]
            params: list[Any] = [high_water]
            if filters["stream"] is not None:
                where.append("h.stream_id = ?")
                params.append(filters["stream"])
            if filters["peer"] is not None:
                where.append("h.peer_id = ?")
                params.append(filters["peer"])
            if cursor:
                stream, peer, generation, handle = cursor["after"]
                where.append("(h.stream_id > ? OR (h.stream_id = ? AND h.peer_id > ?) OR "
                             "(h.stream_id = ? AND h.peer_id = ? AND h.session_generation < ?) OR "
                             "(h.stream_id = ? AND h.peer_id = ? AND h.session_generation = ? AND h.session_handle < ?))")
                params.extend([stream, stream, peer, stream, peer, generation, stream, peer, generation, handle])
            params.append(args.limit + 1)
            extra_select = (",s.binding_json,s.fresh_count,s.reuse_count,s.counts_complete,e.detail AS telemetry_detail "
                            "FROM bridge_session_history h LEFT JOIN bridge_session_monitor_stats s ON s.session_handle=h.session_handle "
                            "LEFT JOIN bridge_evidence e ON e.seq=s.latest_telemetry_seq AND e.kind='session_telemetry'") if has_stats else (
                            ",NULL AS binding_json,NULL AS fresh_count,NULL AS reuse_count,NULL AS counts_complete,NULL AS telemetry_detail "
                            "FROM bridge_session_history h")
            rows = conn.execute(
                "SELECT h.rowid,h.session_handle,h.stream_id,h.peer_id,h.session_generation,h.runtime_kind,h.state,h.resumable,h.last_seen,"
                "h.context_watermark,h.bootstrap_truncated,h.context_watermark_version,h.created_at,h.tombstoned_at" +
                extra_select + " WHERE " + " AND ".join(where) +
                " ORDER BY h.stream_id,h.peer_id,h.session_generation DESC,h.session_handle DESC LIMIT ?", params).fetchall()
            page_rows = rows[:args.limit]
            next_cursor = None
            if len(rows) > args.limit and page_rows:
                last = page_rows[-1]
                next_cursor = _encode_session_cursor({"v": 1, "store": store_id, "lineage": lineage, "filters": filters,
                    "order": "scope-generation-handle-v1", "high_water": high_water,
                    "after": [last["stream_id"], last["peer_id"], last["session_generation"], last["session_handle"]]})
            return emit({"schema_version": "1.0", "store_selection": {"path": str(db_path), "source": getattr(args, "db_source", "unknown")},
                         "items": [_session_public_row(row, now=now) for row in page_rows], "next_cursor": next_cursor})
    except SchemaVersionError as exc:
        error = {"schema_version": "1.0", "status": "SCHEMA_VERSION_ERROR", "error": str(exc)}
        if use_json:
            print(json.dumps(error, ensure_ascii=True, indent=2))
        error_line("schema_version_error", str(exc), "peerhub guide")
        return 6
    except ValueError as exc:
        error = {"schema_version": "1.0", "status": "ERROR", "error": str(exc)}
        if use_json:
            print(json.dumps(error, ensure_ascii=True, indent=2))
        error_line("invalid_arguments", str(exc))
        return 1
    except sqlite3.Error as exc:
        error = {"schema_version": "1.0", "status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        if use_json:
            print(json.dumps(error, ensure_ascii=True, indent=2))
        error_line("invalid_arguments", str(error["error"]))
        return 4


def _session_public_row(row: sqlite3.Row, *, now: float) -> dict[str, Any]:
    from peerhub.extensions.monitor_read_model import context_estimate_age, public_telemetry

    binding: dict[str, Any] = {"profile": None, "model": None, "effort": None, "writable": None}
    binding_json = row["binding_json"] if "binding_json" in row.keys() else None
    if isinstance(binding_json, str):
        try:
            parsed = json.loads(binding_json)
            if isinstance(parsed, dict):
                parsed = cast("dict[str, Any]", parsed)
                for key in binding:
                    value = parsed.get(key)
                    if value is None or isinstance(value, (str, bool)):
                        binding[key] = value
        except (ValueError, TypeError):
            pass
    calls = None
    if "counts_complete" in row.keys():
        complete = bool(row["counts_complete"])
        calls = {"fresh": row["fresh_count"], "reuse": row["reuse_count"], "complete": complete}
    latest: dict[str, Any] | None = None
    raw_telemetry = row["telemetry_detail"] if "telemetry_detail" in row.keys() else None
    if isinstance(raw_telemetry, str):
        latest = {"schema_version": 3, **public_telemetry(
            raw_telemetry, handle=str(row["session_handle"]),
            generation=int(row["session_generation"]), provider=str(row["runtime_kind"]))}
    context: dict[str, Any] = latest["context"] if latest is not None else {}
    result = {
        "handle": str(row["session_handle"]), "stream_id": str(row["stream_id"]),
        "peer_id": str(row["peer_id"]), "generation": int(row["session_generation"]),
        "state": str(row["state"]), "resumable": bool(row["resumable"]),
        "last_seen": float(row["last_seen"]), "context_watermark": row["context_watermark"],
        "bootstrap_truncated": None if row["bootstrap_truncated"] is None else bool(row["bootstrap_truncated"]),
        "context_watermark_version": int(row["context_watermark_version"]),
        "created_at": float(row["created_at"]), "tombstoned_at": row["tombstoned_at"],
        "configured_binding": binding,
        "ctx_est_pct": context.get("percent"),
        "ctx_est_tokens": context.get("occupancy_tokens"),
        "ctx_age_seconds": context_estimate_age(context.get("observed_at"), now),
    }
    if calls is not None:
        result["call_counts"] = calls
    if latest is not None:
        result["latest_telemetry"] = latest
    return result
