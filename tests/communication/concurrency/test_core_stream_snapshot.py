"""A Stream's revision, metadata and members belong to one committed snapshot."""
from __future__ import annotations

import threading

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore


@pytest.mark.concurrency
@pytest.mark.platform_sensitive
@pytest.mark.parametrize("readonly", [False, True])
def test_get_stream_keeps_members_in_the_row_snapshot(tmp_path, monkeypatch, readonly):
    db = tmp_path / "core.db"
    writer = CoreStore(db)
    for peer_id in ("before", "after"):
        writer.register_peer(Peer(peer_id=peer_id))
    original = writer.create_stream(Stream(stream_id="shared", title="before", members=["before"]))
    reader = CoreStore(db, readonly=readonly)
    row_read, committed = threading.Event(), threading.Event()
    failures = []

    def update_stream():
        try:
            assert row_read.wait(30), "reader did not reach the row/member boundary"
            writer.cas_stream("shared", original.revision, {"title": "after", "members": ["after"]})
        except BaseException as exc:
            failures.append(exc)
        finally:
            committed.set()

    load_members = reader._load_stream

    def pause_before_members(conn, row):
        row_read.set()
        assert committed.wait(30), "writer could not commit while the Stream was being read"
        return load_members(conn, row)

    thread = threading.Thread(target=update_stream, daemon=True)
    with monkeypatch.context() as patch:
        patch.setattr(reader, "_load_stream", pause_before_members)
        thread.start()
        try:
            observed = reader.get_stream("shared")
        finally:
            row_read.set()
            thread.join(30)

    assert not thread.is_alive() and not failures
    assert observed == original  # never old revision/title paired with new members
    current = reader.get_stream("shared")
    assert current.revision == original.revision + 1
    assert current.title == "after" and current.members == ["after"]
