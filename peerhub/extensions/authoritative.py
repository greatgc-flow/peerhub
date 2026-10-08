"""Shared mechanics of the M2 authoritative-CAS pattern (Work, Skill/Capability): stream paging and projection repair."""
from __future__ import annotations

from typing import Any, Callable, Iterator

from peerhub.core.models import Record
from peerhub.core.store import CoreStore


def stream_records(store: CoreStore, stream_id: str, page_size: int = 500) -> Iterator[Record]:
    """Every Record of one stream in position order, in bounded pages."""
    position = 0
    while batch := store.read_records(stream_id, after_position=position, limit=page_size):
        yield from batch
        position = batch[-1].position


def authoritative_item(*, get_cached: Callable[[], Any], locate: Callable[[], str | None], replay: Callable[[str], Any],
                       build: Callable[[Any], Any], save: Callable[[Any], None], not_found: type[Exception], what: str, identity: str) -> Any:
    """The item as the ordered stream Records define it, repairing a stale projection (crash or another writer).

    CAS decisions must use this, never the cached projection row: the Record is the authority. `replay(stream_id)` returns the
    reduced state of `identity` (or None)."""
    try:
        cached = get_cached()
        stream_id = cached.stream_id
    except not_found:  # projection row missing (crash after the creation Record): locate it from the Records
        cached = None
        found = locate()
        if found is None:
            raise
        stream_id = found
    state = replay(stream_id)
    if state is None:
        raise not_found(f"{what} {identity!r} has no authoritative Records in stream {stream_id!r}")
    item = build(state)
    if item != cached:
        save(item)
    return item
