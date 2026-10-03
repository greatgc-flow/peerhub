"""Legacy v0.x fixture builder: the REAL legacy schema (SqliteStateStore migrations), populated with raw SQL.

Expected values in tests are literals; nothing here computes an oracle from the importer."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

# Default fixture: two correlation groups, two consumers (see test_imp_*.py for the literal expectations).
DEFAULT_EVENTS: list[dict[str, Any]] = [
    {"event_id": "e1", "correlation_id": "c1", "occurred_at": 1700000000, "event_kind": "dispatch.admitted",
     "payload": {"note": "café 한글", "n": 1.5, "nested": {"a": [1, 2, None]}}, "request_id": "r1"},
    {"event_id": "e2", "correlation_id": "c1", "occurred_at": 1700000005, "event_kind": "dispatch.completed",
     "payload": {"ok": True}, "evidence_refs": ["ev-1", "ev-2"]},
    {"event_id": "e3", "correlation_id": "c2", "occurred_at": 1700000010, "event_kind": "dispatch.admitted",
     "payload": {}, "round_id": "round-9", "predecessor_digest": "d" * 8},
]
DEFAULT_CONSUMERS = {"dash": 2, "audit": 3}  # consumer_id -> outbox_position


def make_legacy(path: Path, events: list[dict[str, Any]] | None = None, consumers: dict[str, int] | None = None,
                *, governed_targets: int = 1) -> Path:
    from peerhub.persistence.sqlite import SqliteStateStore

    events = DEFAULT_EVENTS if events is None else events
    consumers = DEFAULT_CONSUMERS if consumers is None else consumers
    path = Path(path)
    SqliteStateStore(path, workspace_home_id="legacy-ws").initialize()
    with closing(sqlite3.connect(path)) as c:
        for i, ev in enumerate(events, start=1):
            raw = lambda k, default: ev[k] if k in ev else default  # noqa: E731
            payload_json = ev["payload_json"] if "payload_json" in ev else json.dumps(ev.get("payload", {}), ensure_ascii=False)
            c.execute(
                "INSERT INTO event_log (event_id, protocol_major, protocol_minor, schema_version, correlation_id, occurred_at,"
                " event_kind, payload_json, request_id, round_id, evidence_refs_json, predecessor_digest, recovery_context_json,"
                " appended_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ev["event_id"], 0, 1, "v0", ev["correlation_id"], raw("occurred_at", 1700000000), ev.get("event_kind", "k"),
                 payload_json, ev.get("request_id"), ev.get("round_id"),
                 ev["evidence_refs_json"] if "evidence_refs_json" in ev else json.dumps(ev.get("evidence_refs", [])),
                 ev.get("predecessor_digest"), ev.get("recovery_context_json"),
                 raw("appended_at", raw("occurred_at", 1700000000))))
        for cid, pos in consumers.items():
            eid = c.execute("SELECT event_id FROM event_log WHERE outbox_position = ?", (pos,)).fetchone()[0]
            c.execute("INSERT INTO consumer_offsets (consumer_id, outbox_position, event_id, revision) VALUES (?,?,?,1)",
                      (cid, pos, eid))
        for i in range(governed_targets):
            c.execute("INSERT INTO governed_targets (target_id, revision, state_json, updated_at) VALUES (?,?,?,?)",
                      (f"t{i}", 1, "{}", 1700000000))
        c.commit()
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("PRAGMA journal_mode = DELETE")
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    return path


def tree_fingerprint(directory: Path) -> dict[str, str]:
    """sha256 of every file under `directory` (relative posix path -> hash): detects any byte or file-set change."""
    out = {}
    for p in sorted(Path(directory).rglob("*")):
        if p.is_file():
            out[p.relative_to(directory).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def raw_dump(db: Path, table: str, order: str, cols: str = "*") -> list[tuple]:
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)) as c:
        return [tuple(r) for r in c.execute(f"SELECT {cols} FROM {table} ORDER BY {order}")]


def normalized_state(db: Path) -> dict[str, Any]:
    """Target state minus generated ids/times: used to prove interrupted+resumed == uninterrupted."""
    return {
        "peers": raw_dump(db, "peers", "peer_id", "peer_id, display_name, adapter_ref, metadata_json"),
        "streams": raw_dump(db, "streams", "stream_id", "stream_id, title, state, revision, metadata_json, created_at"),
        "members": raw_dump(db, "stream_members", "stream_id, rowid", "stream_id, peer_id"),
        "records": raw_dump(db, "records", "stream_id, position",
                            "stream_id, position, author_peer_id, kind, body_json, idempotency_key, payload_digest, created_at"),
        "offsets": raw_dump(db, "offsets", "peer_id, stream_id"),
    }

