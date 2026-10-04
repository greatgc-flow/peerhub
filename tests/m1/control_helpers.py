"""Wave-4 test helpers: raw-SQL oracles for control state and in-memory Records for pure projection tests."""
from __future__ import annotations

import json

from peerhub.m1.models import Record
from tests.m1.bridge_helpers import bseed, delivery_rows, evidence, kinds, offset_row, records, responses, sql  # noqa: F401
from tests.m1.fakes import FakeRuntimeTarget
from tests.m1.helpers import req


def ctl(h, kind, key, *, author="a", body=None, stream="s", **extra):
    return h.append_record({**req(body=body if body is not None else {}, key=key, author=author, stream=stream), "kind": kind, **extra})


def msg(h, key, *, author="a", stream="s", body=None):
    return h.append_record(req(body=key if body is None else body, key=key, author=author, stream=stream))


def control_rows(h, record_id=None):
    q = "SELECT record_id, stream_id, peer_id, kind, position, effect, outcome, error, delivery_id, attempts FROM bridge_controls"
    q += (" WHERE record_id=?" if record_id else "") + " ORDER BY position"
    return sql(h, q, (record_id,) if record_id else ())


def control_evidence(h, record_id):
    return [r for r in sql(h, "SELECT kind, certainty, detail FROM bridge_evidence ORDER BY seq")
            if json.loads(r[2]).get("control_record_id") == record_id]


def all_record_rows(h, stream="s"):
    return sql(h, "SELECT * FROM records WHERE stream_id=? ORDER BY position", (stream,))


def running(h, rt=None, stream="s"):
    """Leave a STARTED, non-terminal delivery behind (a runtime that is 'still running'); returns (rt, cycle result)."""
    rt = rt or FakeRuntimeTarget()
    rt.script_deliver(("started", "exec-1"), ("output", "partial"))
    res = h.delivery_cycle("b", stream, rt)
    assert res.status == "uncertain" and res.certainty == "STARTED"
    return rt, res


def mem_records(sizes, stream="s", author="a", kind="message"):
    """In-memory Records (no DB) with bodies of the given character counts; positions 1..n."""
    return [Record(record_id=f"r{i + 1}", stream_id=stream, position=i + 1, author_peer_id=author, kind=kind, body="x" * n,
                   idempotency_key=f"k{i + 1}", payload_digest="sha256:" + "0" * 64, created_at="2026-10-01T00:00:00Z",
                   appended_at="2026-10-01T00:00:00Z") for i, n in enumerate(sizes)]


def item_json(r) -> str:
    """Independent oracle for the serialized projection item (own json.dumps, not the implementation)."""
    return json.dumps({"author_peer_id": r.author_peer_id, "body": r.body, "kind": r.kind, "position": r.position,
                       "record_id": r.record_id}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_bytes(recs) -> int:
    return len(",".join(item_json(r) for r in recs).encode("utf-8"))
