"""Projection rebuilds replace state atomically: a failure leaves the previous projection untouched."""
from __future__ import annotations

from pathlib import Path

import pytest

from peerhub.core.models import Peer, Stream, StreamState
from peerhub.core.store import CoreStore
from peerhub.extensions.skills import SkillCatalogEngine
from peerhub.extensions.work import WorkProjection


@pytest.fixture
def store(tmp_path: Path) -> CoreStore:
    s = CoreStore(tmp_path / "core.db")
    s.register_peer(Peer(peer_id="p1", display_name="Peer 1"))
    s.create_stream(Stream(stream_id="s1", members=["p1"], state=StreamState.OPEN))
    return s


def _exploding(records):
    yield from records[:1]
    raise RuntimeError("record source failed")


@pytest.mark.catalog_id("WRK-015")
def test_work_rebuild_failing_record_source_keeps_existing_projection(tmp_path, store):
    proj = WorkProjection(tmp_path / "w.db", store=store)
    proj.create_work(stream_id="s1", work_id="w-1", title="T", spec={})
    records = list(store.read_records(stream_id="s1"))
    with pytest.raises(RuntimeError):
        proj.rebuild_projection(_exploding(records))
    assert [w.work_id for w in proj.list_work()] == ["w-1"]  # not wiped by the failed rebuild


@pytest.mark.catalog_id("WRK-015")
def test_work_rebuild_failing_write_rolls_back_the_delete(tmp_path, store, monkeypatch):
    proj = WorkProjection(tmp_path / "w.db", store=store)
    proj.create_work(stream_id="s1", work_id="w-1", title="T", spec={})
    records = list(store.read_records(stream_id="s1"))

    def boom(self, work, conn=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(WorkProjection, "_save_work_projection", boom)
    with pytest.raises(RuntimeError):
        proj.rebuild_projection(records)
    monkeypatch.undo()
    assert [w.work_id for w in proj.list_work()] == ["w-1"]


@pytest.mark.catalog_id("SKL-017")
def test_skill_rebuild_failing_record_source_keeps_existing_projection(tmp_path, store):
    cat = SkillCatalogEngine(tmp_path / "c.db", store=store)
    cat.declare_capability(stream_id="s1", capability_id="cap-1", spec={"k": "v"})
    records = list(store.read_records(stream_id="s1"))
    with pytest.raises(RuntimeError):
        cat.rebuild_index(_exploding(records))
    assert cat.get_capability("cap-1").capability_id == "cap-1"


def test_rebuild_still_restores_state(tmp_path, store):
    proj = WorkProjection(tmp_path / "w.db", store=store)
    proj.create_work(stream_id="s1", work_id="w-1", title="T", spec={})
    assert proj.rebuild_projection(store.read_records(stream_id="s1")) >= 1
    assert [w.work_id for w in proj.list_work()] == ["w-1"]
