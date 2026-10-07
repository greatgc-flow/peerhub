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
