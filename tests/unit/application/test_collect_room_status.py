"""Unit coverage for collect_room_status (quality-audit-2026-09-19.md
item 2: had zero tests despite being the CLI status display's data
source)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.application.status import collect_room_status
from peerhub.core.errors import RecordNotFoundError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.rooms import RoomsService
from peerhub.persistence.sqlite import SqliteStateStore


def _service(tmp_path: Path) -> RoomsService:
    store = SqliteStateStore(
        tmp_path / "rooms.sqlite3",
        workspace_home_id="status-test",
    )
    store.initialize()
    broker = GovernanceBroker(
        store,
        clock=FakeClock(range(1, 200)),
        ids=FakeIdSource([f"id-{i}" for i in range(1, 200)]),
    )
    return RoomsService(
        broker,
        clock=FakeClock(range(1, 200)),
        ids=FakeIdSource([f"domain-{i}" for i in range(1, 200)]),
    )


def test_collect_room_status_on_room_with_no_activity(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.create_room(
        room_id="room-1",
        topic_id="topic-1",
        title="General",
        creator_id="cc",
        participants=("cc", "cx"),
    )

    result = collect_room_status(service, room_id="room-1")

    assert result["room_id"] == "room-1"
    assert result["unread_count"] == 0
    assert result["active_participants"] == ()
    assert result["thread_ids"] == ()
    assert result["threads"] == ()
    assert result["message_count"] == 0
    assert result["last_message_at"] is None
    # room_summary reflects update_room_summary(), not mere room existence
    # -- a freshly created room has no summary yet.
    assert result["room_summary"] is None


def test_collect_room_status_reports_threads_and_messages(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.create_room(
        room_id="room-1",
        topic_id="topic-1",
        title="General",
        creator_id="cc",
        participants=("cc", "cx"),
    )
    service.create_thread(
        thread_id="thread-1",
        room_id="room-1",
        subject="kickoff",
        creator_id="cc",
    )
    service.append_message(
        message_id="msg-1",
        room_id="room-1",
        thread_id="thread-1",
        author_id="cc",
        body="hello",
    )

    result = collect_room_status(service, room_id="room-1")

    assert result["thread_ids"] == ("thread-1",)
    threads = result["threads"]
    assert len(threads) == 1
    assert threads[0]["subject"] == "kickoff"
    assert result["message_count"] == 1
    assert result["last_message_at"] is not None


def test_collect_room_status_on_unknown_room_raises(tmp_path: Path) -> None:
    """collect_room_status has no not-found guard of its own -- it
    delegates straight to RoomsService.count_unread_messages(), which
    requires the room to exist. Confirmed real behavior, not assumed."""
    service = _service(tmp_path)

    with pytest.raises(RecordNotFoundError):
        collect_room_status(service, room_id="does-not-exist")
