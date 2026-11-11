"""Compatibility iterator: Core owns the authoritative, snapshot-consistent Record scan."""
from __future__ import annotations

from typing import Iterator

from peerhub.core.models import Record
from peerhub.core.store import CoreStore


def source_records(store: CoreStore, page_size: int = 500) -> Iterator[Record]:
    """Preserve the extension entry point while delegating one snapshot to Core."""
    yield from store.iter_records_snapshot(page_size=page_size)
