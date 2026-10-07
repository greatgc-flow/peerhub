"""Bounded-page iteration of immutable Records from one committed Core snapshot."""
from __future__ import annotations

import json
from typing import Iterator

from peerhub.core.models import Record
from peerhub.core.store import CoreStore


def source_records(store: CoreStore, page_size: int = 500) -> Iterator[Record]:
    if page_size <= 0:
        raise ValueError("source page size must be positive")
    with store.read_uow() as conn:
        for stream in conn.execute("SELECT stream_id FROM streams ORDER BY stream_id").fetchall():
            position = 0
            while True:
                rows = conn.execute("SELECT * FROM records WHERE stream_id=? AND position>? ORDER BY position LIMIT ?",
                                    (stream["stream_id"], position, page_size)).fetchall()
                if not rows:
                    break
                for row in rows:
                    yield Record(record_id=row["record_id"], stream_id=row["stream_id"], position=row["position"],
                                 author_peer_id=row["author_peer_id"], kind=row["kind"],
                                 body=json.loads(row["body_json"]) if row["body_json"] is not None else None,
                                 targets=json.loads(row["targets_json"]), reply_to=row["reply_to"], refs=json.loads(row["refs_json"]),
                                 metadata=json.loads(row["metadata_json"]), idempotency_key=row["idempotency_key"],
                                 payload_digest=row["payload_digest"], created_at=row["created_at"], appended_at=row["appended_at"])
                position = rows[-1]["position"]
