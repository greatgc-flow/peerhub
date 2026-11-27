"""Read-only, versioned activity export. Consumers own their checkpoints."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from time import monotonic
from typing import Any, Iterator

from peerhub.core.connection import create_connection
from peerhub.core.models import json_digest
from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION
from peerhub.extensions.activity import cursor, events
from peerhub.extensions.activity.cursor import ActivityError

TABLES = ("streams", "records", "bridge_evidence", "bridge_deliveries", "bridge_ask_outcomes")
MAX_BYTES = 1048576

_RECORD = """
 SELECT r.position AS seq,r.position,r.record_id,r.stream_id,r.reply_to,r.kind,r.author_peer_id,r.created_at,
 CASE WHEN length(CAST(r.idempotency_key AS BLOB))<=260 AND r.kind='prompt' AND length(CAST(r.metadata_json AS BLOB))<=16384 AND
   json_type(r.metadata_json,'$.binding')='text' AND json_type(r.metadata_json,'$.runtime_kind')='text'
   THEN r.idempotency_key END request_key,
 CASE WHEN length(CAST(r.targets_json AS BLOB))<=1024 AND json_array_length(r.targets_json)=1
   AND json_type(r.targets_json,'$[0]')='text' THEN json_extract(r.targets_json,'$[0]') END target_peer,
 {delivery} AS delivery_id,
 CASE WHEN r.kind='response' AND length(CAST(r.metadata_json AS BLOB))<=16384
 THEN json_type(r.metadata_json,'$.output_truncated') END truncation
 FROM records r WHERE r.stream_id=:partition AND r.position>:lo AND r.position<=:hi
"""
_RESPONSE_DELIVERY = """CASE WHEN r.kind='response' AND substr(r.idempotency_key,1,16)='bridge-response:'
 THEN (SELECT d.delivery_id FROM bridge_deliveries d WHERE d.delivery_id=substr(r.idempotency_key,17)
 AND d.stream_id=r.stream_id AND d.peer_id=r.author_peer_id AND d.record_id=r.reply_to) END"""
_EVIDENCE = """
 SELECT e.seq,e.kind AS event,e.certainty,e.stream_id,e.peer_id,e.delivery_id,d.record_id,d.attempt,
 d.session_handle,d.session_generation,d.seq AS delivery_seq,
 CASE WHEN e.kind='runtime_error' AND length(CAST(p.metadata_json AS BLOB))<=16384
 THEN json_extract(p.metadata_json,'$.runtime_kind') END prompt_provider,
 CASE WHEN e.kind IN ('attempt_created','about_to_invoke','started','session_call','session_telemetry',
 'runtime_error','response_appended','reconcile_registered','prespawn_failure','invocation_unresolved_recovered')
 AND length(CAST(e.detail AS BLOB))<=16384 THEN e.detail END detail,
 CASE WHEN e.kind='session_telemetry' AND length(CAST(d.session_snapshot_json AS BLOB))<=16384
 THEN json_extract(d.session_snapshot_json,'$.mode') END session_mode,
 CASE WHEN e.kind='offset_acked' AND length(CAST(d.result_json AS BLOB))<=65536
 THEN json_extract(d.result_json,'$.reason') END settled_reason,
 CASE WHEN e.kind='offset_acked' AND length(CAST(d.result_json AS BLOB))<=65536
 THEN json_extract(d.result_json,'$.task_outcome')='NOT_EXECUTED' END not_executed,
 CASE WHEN e.kind='offset_acked' AND d.certainty='TERMINAL' AND length(CAST(d.result_json AS BLOB))<=65536
 THEN json_type(d.result_json,'$.empty_response')='true' AND json_array_length(d.result_json,'$.denied_actions')>0 END denied_empty
 FROM bridge_evidence e LEFT JOIN bridge_deliveries d ON d.delivery_id=e.delivery_id
 AND d.peer_id=e.peer_id AND d.stream_id=e.stream_id
 LEFT JOIN records p ON p.record_id=d.record_id AND p.stream_id=e.stream_id WHERE e.seq>:lo AND e.seq<=:hi
"""
_OUTCOME = """
 SELECT o.seq,o.record_id,o.outcome,o.marked_at,r.stream_id FROM bridge_ask_outcomes o
 JOIN records r ON r.record_id=o.record_id WHERE o.seq>:lo AND o.seq<=:hi
"""


def _heads(conn: sqlite3.Connection, stream: str | None, tables: set[str], frozen: dict[str, int] | None) -> dict[str, int]:
    if stream is not None:
        streams = [stream]
    elif frozen is not None:
        streams = [key[2:] for key in frozen if key.startswith("r:")]
    else:
        streams = [r[0] for r in conn.execute("SELECT stream_id FROM streams ORDER BY stream_id LIMIT ?",
                                              (cursor.MAX_STREAMS + 1,))]
    if len(streams) > cursor.MAX_STREAMS:
        raise ActivityError("PARTITION_LIMIT")
    heads: dict[str, int] = {}
    for sid in streams:
        if not isinstance(sid, str) or len(sid.encode()) > cursor.MAX_ID_BYTES:
            raise ActivityError("PARTITION_LIMIT")
        heads["r:" + sid] = conn.execute("SELECT coalesce(max(position),0) FROM records WHERE stream_id=?", (sid,)).fetchone()[0]
    if {"bridge_evidence", "bridge_deliveries"} <= tables:
        heads["evidence"] = conn.execute("SELECT coalesce(max(seq),0) FROM bridge_evidence").fetchone()[0]
    if "bridge_ask_outcomes" in tables:
        heads["outcome"] = conn.execute("SELECT coalesce(max(seq),0) FROM bridge_ask_outcomes").fetchone()[0]
    return heads


def _rows(conn: sqlite3.Connection, key: str, lo: int, hi: int, stream: str | None,
          ask: str | None, limit: int, link_attempts: bool) -> Iterator[tuple[int, dict[str, Any]]]:
    params = {"partition": key[2:], "lo": lo, "hi": hi, "stream": stream, "ask": ask, "limit": limit}
    if key.startswith("r:"):
        query = _RECORD.format(delivery=_RESPONSE_DELIVERY if link_attempts else "NULL") + (" AND (r.record_id=:ask OR r.reply_to=:ask)" if ask else "") + " ORDER BY r.position LIMIT :limit"
    elif key == "evidence":
        query = _EVIDENCE + (" AND e.stream_id=:stream" if stream is not None else "")
        query += (" AND d.record_id=:ask" if ask else "") + " ORDER BY e.seq LIMIT :limit"
    else:
        query = _OUTCOME + (" AND r.stream_id=:stream" if stream is not None else "")
        query += (" AND o.record_id=:ask" if ask else "") + " ORDER BY o.seq LIMIT :limit"
    for row in conn.execute(query, params):
        if key.startswith("r:"):
            item = events.record(row)
        elif key == "evidence":
            item = events.evidence(conn, row)
        else:
            item = events.event("outcome", row["seq"], "peer.outcome", row,
                                outcome=row["outcome"] if row["outcome"] in ("accepted", "rejected", "retried") else None,
                                occurred_at=events.time_text(row["marked_at"]))
        if len(cursor.wire(item)) > 16384:
            item = events.event("omission", item["source_event_id"], "coverage.gap", row, reason="TOO_LARGE")
        yield row["seq"], item


def _page(conn: sqlite3.Connection, store: str, lineage: str | None, stream: str | None, ask: str | None,
          token: str | None, limit: int, max_bytes: int) -> dict[str, Any]:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SUPPORTED_SCHEMA_VERSION:
        raise ActivityError("UNSUPPORTED_SCHEMA", "schema_version_error", 6)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?,?)", TABLES)}
    if not {"records", "streams"} <= tables:
        raise ActivityError("UNSUPPORTED_SCHEMA", "schema_version_error", 6)
    scope = json_digest(["activity/1.0", stream, ask, "metadata"])
    after, frozen = cursor.decode(token, store, lineage, scope) if token is not None else ({}, None)
    if ask is not None:
        row = conn.execute("SELECT stream_id FROM records WHERE record_id=? AND kind='prompt' "
                           "AND (? IS NULL OR stream_id=?)", (ask, stream, stream)).fetchone()
        if row is None:
            raise ActivityError("ASK_NOT_FOUND", "ask_not_found", 1)
        stream = row[0]
    current = _heads(conn, stream, tables, frozen)
    heads = current if frozen is None else frozen
    if (any(k not in current or n > current[k] for k, n in heads.items())
            or any(k not in heads or n > heads[k] for k, n in after.items())):
        raise ActivityError("RESNAPSHOT_REQUIRED")
    positions = {k: after.get(k, 0) for k in heads}
    items: list[dict[str, Any]] = []
    result: dict[str, Any] = dict(schema_version="1.0", source="peerhub", store_ref=store, lineage=lineage,
        coverage={"state": "PARTIAL", "reason": "metadata_only", "content": "OMITTED", "verification": "UNKNOWN",
                  "incremental": "SUPPORTED" if lineage else "NO_LINEAGE"},
        channels={"record": "AVAILABLE", "delivery_evidence": "AVAILABLE" if "evidence" in heads else "UNAVAILABLE",
                  "outcome": "AVAILABLE" if "outcome" in heads else "UNAVAILABLE"}, items=items)
    def finish(more: bool) -> dict[str, Any]:
        result["has_more"] = more
        result["next_cursor"] = cursor.encode(store, lineage, scope, positions, heads if more else None) if lineage else None
        return result

    # Reserve the current envelope and continuation on every candidate. Count encoded bytes, including stdout newline.
    used = 0
    for key in sorted(heads, key=lambda k: (not k.startswith("r:"), k)):
        if positions[key] == heads[key]:
            continue
        for seq, item in _rows(conn, key, positions[key], heads[key], stream, ask, limit - len(items) + 1, "bridge_deliveries" in tables):
            if len(items) == limit:
                return finish(True)
            prior = positions[key]
            positions[key] = seq
            finish(True)
            # Size the envelope without serializing all previously accepted items.
            overhead = len(cursor.wire({**result, "items": []})) + 2
            size = len(cursor.wire(item)) + int(bool(items))
            if overhead + used + size > max_bytes:
                positions[key] = prior
                if not items:
                    raise ActivityError("LIMIT_EXCEEDED")
                return finish(True)
            items.append(item)
            used += size
        positions[key] = heads[key]
    return finish(False)


def export_page(db: Path, *, stream: str | None = None, ask: str | None = None, token: str | None = None,
                limit: int = 100, max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    if (not 1 <= limit <= 200 or not 512 <= max_bytes <= MAX_BYTES
            or any(s is not None and (not s or len(s.encode()) > cursor.MAX_ID_BYTES) for s in (ask, stream))):
        raise ActivityError("INVALID_ARGUMENT")
    if not db.is_file():
        raise ActivityError("STORE_NOT_FOUND", "store_missing", 5)
    origin = cursor.identity(db)
    with closing(create_connection(db, readonly=True, timeout_ms=1000)) as conn:
        conn.row_factory = sqlite3.Row
        # This limit also protects reads of oversized legacy identifiers or malformed SQLite rows.
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8 * MAX_BYTES)
        conn.execute("PRAGMA trusted_schema=OFF")
        deadline = monotonic() + 2
        conn.set_progress_handler(lambda: int(monotonic() > deadline), 2000)
        conn.execute("BEGIN")
        try:
            result = _page(conn, *origin, stream, ask, token, limit, max_bytes)
        except sqlite3.OperationalError as exc:
            if "no such column" in str(exc) or "no such table" in str(exc):
                raise ActivityError("UNSUPPORTED_SCHEMA", "schema_version_error", 6) from None
            raise
        except sqlite3.DataError:
            raise ActivityError("SOURCE_ROW_LIMIT", "diag_unavailable", 5) from None
    if cursor.identity(db) != origin:
        raise ActivityError("RESNAPSHOT_REQUIRED")
    if len(cursor.wire(result)) + 2 > max_bytes:
        raise ActivityError("LIMIT_EXCEEDED")
    return result
