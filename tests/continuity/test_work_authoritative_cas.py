"""M2.2: CAS decisions use the ordered stream Records, not the cached projection row."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from peerhub.core.models import Peer, Stream, StreamState
from peerhub.core.store import CoreStore
from peerhub.extensions.work import WorkProjection, WorkRevisionConflictError


@pytest.fixture
def store(tmp_path: Path) -> CoreStore:
    s = CoreStore(tmp_path / "core.db")
    s.register_peer(Peer(peer_id="p1"))
    s.create_stream(Stream(stream_id="s1", members=["p1"], state=StreamState.OPEN))
    return s


def two_views(tmp_path: Path, store: CoreStore):
    return WorkProjection(tmp_path / "a.db", store=store), WorkProjection(tmp_path / "b.db", store=store)


def test_a_stale_projection_is_repaired_before_the_cas_decision(tmp_path, store):
    a, b = two_views(tmp_path, store)
    a.create_work(stream_id="s1", work_id="w", title="T")
    b.rebuild_projection(store.read_records("s1"))  # b has its own cache at revision 1
    a.transition_work("w", 1, "ACTIVE")  # advances the authoritative stream; b's row is now stale
    assert b.get_work("w").revision == 1
    with pytest.raises(WorkRevisionConflictError):  # decided against the stream (revision 2), not b's stale row
        b.transition_work("w", 1, "CANCELLED")
    assert b.get_work("w").revision == 2 and b.get_work("w").state == "ACTIVE"  # and the cache was repaired
    assert b.transition_work("w", 2, "BLOCKED").revision == 3


def test_a_crash_between_append_and_projection_save_does_not_wedge_the_work_item(tmp_path, store, monkeypatch):
    p = WorkProjection(tmp_path / "p.db", store=store)
    p.create_work(stream_id="s1", work_id="w", title="T")
    monkeypatch.setattr(WorkProjection, "_save_work_projection", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        p.transition_work("w", 1, "ACTIVE")  # the Record is durable, the projection was never updated
    monkeypatch.undo()
    assert p.get_work("w").revision == 1  # stale cache
    assert p.transition_work("w", 2, "BLOCKED").state == "BLOCKED"  # the next operation sees revision 2 and continues


def test_a_change_that_lost_the_race_is_not_acknowledged(tmp_path, store, monkeypatch):
    a, b = two_views(tmp_path, store)
    a.create_work(stream_id="s1", work_id="w", title="T")
    b.rebuild_projection(store.read_records("s1"))
    original = b._authoritative
    state = {"first": True}

    def racing(work_id, accepted=None):
        item = original(work_id, accepted)
        if state["first"]:
            state["first"] = False
            a.transition_work("w", 1, "ACTIVE")  # the other writer commits inside b's read-to-append window
        return item

    monkeypatch.setattr(b, "_authoritative", racing)
    with pytest.raises(WorkRevisionConflictError):
        b.transition_work("w", 1, "CANCELLED")
    monkeypatch.undo()
    assert b.get_work("w").state == "ACTIVE"  # exactly one writer won; a rebuild agrees
    b.rebuild_projection(store.read_records("s1"))
    assert b.get_work("w").state == "ACTIVE" and b.get_work("w").revision == 2


def test_concurrent_transitions_from_independent_views_have_exactly_one_winner(tmp_path, store):
    a, b = two_views(tmp_path, store)
    a.create_work(stream_id="s1", work_id="w", title="T")
    b.rebuild_projection(store.read_records("s1"))
    barrier, results = threading.Barrier(2), []

    def run(view, target):
        barrier.wait()
        try:
            results.append(("ok", view.transition_work("w", 1, target).state))
        except WorkRevisionConflictError:
            results.append(("conflict", target))

    threads = [threading.Thread(target=run, args=(a, "ACTIVE")), threading.Thread(target=run, args=(b, "CANCELLED"))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(r[0] for r in results) == ["conflict", "ok"]
    winner = next(r[1] for r in results if r[0] == "ok")
    a.rebuild_projection(store.read_records("s1"))
    assert a.get_work("w").state == winner and a.get_work("w").revision == 2


def test_a_crash_after_the_creation_record_does_not_wedge_the_new_work_item(tmp_path, store, monkeypatch):
    p = WorkProjection(tmp_path / "p.db", store=store)
    monkeypatch.setattr(WorkProjection, "_save_work_projection", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        p.create_work(stream_id="s1", work_id="w", title="T", spec={"k": 1})  # Record durable, no projection row
    monkeypatch.undo()
    assert [w.work_id for w in p.list_work()] == []
    assert p.transition_work("w", 1, "ACTIVE").state == "ACTIVE"  # located from the Records, projection repaired
    assert p.get_work("w").revision == 2


def test_recreating_after_a_creation_crash_is_idempotent_and_different_content_conflicts(tmp_path, store, monkeypatch):
    from peerhub.extensions.work import WorkAlreadyExistsError

    p = WorkProjection(tmp_path / "p.db", store=store)
    monkeypatch.setattr(WorkProjection, "_save_work_projection", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        p.create_work(stream_id="s1", work_id="w", title="T", spec={"k": 1})
    monkeypatch.undo()
    with pytest.raises(WorkAlreadyExistsError):
        p.create_work(stream_id="s1", work_id="w", title="Different", spec={"k": 1})
    again = p.create_work(stream_id="s1", work_id="w", title="T", spec={"k": 1})  # the retry a caller would make
    assert again.revision == 1 and p.get_work("w").title == "T"
    assert len([r for r in store.read_records("s1") if r.kind == "m2.work.created"]) == 1  # nothing was appended twice
