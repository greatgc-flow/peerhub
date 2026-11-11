"""Authoritative cross-stream Record scanning contract (#335)."""
from __future__ import annotations

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore
from peerhub.extensions.source_records import source_records


pytestmark = [pytest.mark.architecture, pytest.mark.sqlite, pytest.mark.unit, pytest.mark.platform_sensitive]


def _seed(store: CoreStore) -> None:
    store.register_peer(Peer(peer_id="writer"))
    # Intentionally register streams out of lexical order.
    store.create_stream(Stream(stream_id="z", members=["writer"]))
    store.create_stream(Stream(stream_id="a", members=["writer"]))


def _append(store: CoreStore, stream_id: str, key: str) -> None:
    store.append_record(
        stream_id=stream_id,
        author_peer_id="writer",
        kind="message",
        body={"key": key},
        idempotency_key=key,
        created_at="2026-11-11T00:00:00Z",
    )


def test_source_records_and_core_iterator_order_and_pagination(tmp_path) -> None:
    store = CoreStore(tmp_path / "core.db")
    _seed(store)
    _append(store, "z", "z1")
    _append(store, "a", "a1")
    _append(store, "a", "a2")

    expected = [("a", 1, "a1"), ("a", 2, "a2"), ("z", 1, "z1")]
    for page_size in (1, 2, 500):
        direct = list(store.iter_records_snapshot(page_size=page_size))
        compat = list(source_records(store, page_size=page_size))
        assert direct == compat
        assert [(r.stream_id, r.position, r.body["key"]) for r in direct] == expected


def test_reader_holds_one_committed_snapshot_across_pages(tmp_path) -> None:
    store = CoreStore(tmp_path / "core.db")
    _seed(store)
    _append(store, "a", "a1")
    _append(store, "a", "a2")

    reader = source_records(store, page_size=1)
    first = next(reader)
    assert first.body == {"key": "a1"}

    # The iterator's initial read UoW remains open; a concurrent writer is
    # allowed in WAL mode, but the pinned reader must NOT see its later commit.
    _append(store, "z", "z-after-snapshot")
    remaining = list(reader)
    assert [(r.stream_id, r.position) for r in remaining] == [("a", 2)]
    assert [(r.stream_id, r.position) for r in store.iter_records_snapshot(1)] == [
        ("a", 1), ("a", 2), ("z", 1)
    ]


@pytest.mark.parametrize("page_size", [0, -1, True, 1.5, "5"])
def test_invalid_page_size_does_not_start_scan(tmp_path, page_size) -> None:
    store = CoreStore(tmp_path / "core.db")
    with pytest.raises(ValueError, match="page size"):
        list(source_records(store, page_size=page_size))
