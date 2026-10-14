"""Wave-3 test helpers: raw-SQL oracles (independent of the Bridge/ClaimStore read paths)."""
from __future__ import annotations

import sqlite3
from contextlib import closing

from tests.communication.helpers import req


def sql(h, query: str, args: tuple = ()) -> list[tuple]:
    with closing(sqlite3.connect(h.db_path)) as c:
        return c.execute(query, args).fetchall()


def claim_row(h, stream="s", peer="b"):
    r = sql(h, "SELECT owner_id, generation, expires_at FROM bridge_claims WHERE stream_id=? AND peer_id=?", (stream, peer))
    return r[0] if r else None


def delivery_rows(h, record_id=None):
    q = "SELECT * FROM bridge_deliveries" + (" WHERE record_id=?" if record_id else "") + " ORDER BY seq"
    return sql(h, q, (record_id,) if record_id else ())


def evidence(h, delivery_id=None):
    q = "SELECT seq, kind, certainty FROM bridge_evidence" + (" WHERE delivery_id=?" if delivery_id else "") + " ORDER BY seq"
    return sql(h, q, (delivery_id,) if delivery_id else ())


def kinds(h, delivery_id=None):
    return [r[1] for r in evidence(h, delivery_id)]


def records(h, stream="s"):
    return sql(h, "SELECT record_id, position, author_peer_id, kind, body_json, reply_to FROM records WHERE stream_id=? ORDER BY position",
               (stream,))


def responses(h, stream="s"):
    return [r for r in records(h, stream) if r[3] == "response"]


def offset_row(h, peer="b", stream="s"):
    r = sql(h, "SELECT read_through_position, revision FROM offsets WHERE peer_id=? AND stream_id=?", (peer, stream))
    return r[0] if r else (0, 1)


def bseed(h, peers=("a", "b"), stream="s", n=1):
    """Peer a authors; the bridged peer is b. Returns the appended Records."""
    for p in peers:
        h.create_peer({"peer_id": p})
    h.create_stream({"stream_id": stream, "members": list(peers)})
    return [h.append_record(req(body=f"m{i}", key=f"m{i}", author="a", stream=stream)) for i in range(n)]


def more_stream(h, stream, peers=("a", "b"), n=1):
    h.create_stream({"stream_id": stream, "members": list(peers)})
    return [h.append_record(req(body=f"m{i}", key=f"m{i}", author="a", stream=stream)) for i in range(n)]


def downgrade_registry(conn):
    """Fixture: recreate PR1's single-slot schema with its mapping facts."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='bridge_session_history'").fetchone():
        return
    read = conn.execute("SELECT h.* FROM bridge_sessions p JOIN bridge_session_history h ON h.session_handle=p.session_handle")
    columns = [c[0] for c in read.description]
    rows = [dict(zip(columns, row)) for row in read.fetchall()]
    for table in ("bridge_session_monitor_stats", "bridge_session_monitor_scope", "bridge_sessions", "bridge_session_counters", "bridge_session_history"):
        conn.execute(f"DROP TABLE {table}")
    conn.execute("CREATE TABLE bridge_sessions (stream_id TEXT NOT NULL,peer_id TEXT NOT NULL,runtime_kind TEXT NOT NULL,external_session_id TEXT NOT NULL,"
                 "session_generation INTEGER NOT NULL,adapter_fingerprint TEXT NOT NULL,binding TEXT NOT NULL,resumable INTEGER NOT NULL,state TEXT NOT NULL,last_seen REAL NOT NULL,"
                 "workspace_generation TEXT NOT NULL DEFAULT '',vendor_session_id TEXT,context_watermark INTEGER,bootstrap_truncated INTEGER,usage_json TEXT,context_watermark_version INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(stream_id,peer_id))")
    names = [r[1] for r in conn.execute("PRAGMA table_info(bridge_sessions)")]
    for row in rows:
        conn.execute(f"INSERT INTO bridge_sessions VALUES ({','.join('?' for _ in names)})", [row[n] for n in names])
