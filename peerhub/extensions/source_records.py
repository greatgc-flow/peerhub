"""Bounded-page iteration of immutable Records from one committed Core snapshot."""
from __future__ import annotations

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
                    yield CoreStore._row_to_record(row)  # pyright: ignore[reportPrivateUsage]  # single row->Record mapping, read inside this snapshot
                position = rows[-1]["position"]
