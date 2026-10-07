"""M2.3: Skill and Capability CAS decisions use the ordered stream Records, not the cached projection row."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from peerhub.core.models import Peer, Stream, StreamState
from peerhub.core.store import CoreStore
from peerhub.extensions.skills import CapabilityConflictError, SkillCatalogEngine, SkillRevisionConflictError

MANIFEST = "---\nname: demo\ndescription: demo skill\nversion: 1.0.0\n---\n\nbody\n"


@pytest.fixture
def store(tmp_path: Path) -> CoreStore:
    s = CoreStore(tmp_path / "core.db")
    s.register_peer(Peer(peer_id="p1"))
    s.create_stream(Stream(stream_id="s1", members=["p1"], state=StreamState.OPEN))
    return s


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    d = tmp_path / "skills" / "demo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(MANIFEST, encoding="utf-8")
    return d


def views(tmp_path: Path, store: CoreStore):
    return SkillCatalogEngine(tmp_path / "a.db", store=store), SkillCatalogEngine(tmp_path / "b.db", store=store)


def sync(engine: SkillCatalogEngine, store: CoreStore) -> None:
    engine.rebuild_index(store.read_records("s1"))


# ------------------------------------------------------------------------------------------------ skills
def test_a_stale_skill_projection_is_repaired_before_the_cas_decision(tmp_path, store, skill_dir):
    a, b = views(tmp_path, store)
    a.index_skill(stream_id="s1", skill_dir=skill_dir)
    sync(b, store)
    a.transition_skill("demo", 1, "VALIDATED")
    assert b.get_skill("demo").revision == 1  # b's cache is stale
    with pytest.raises(SkillRevisionConflictError):  # decided against the stream (revision 2), not b's stale row
        b.transition_skill("demo", 1, "ACTIVE")  # a legal transition, but at a stale expected revision
    assert b.get_skill("demo").revision == 2 and b.get_skill("demo").state == "VALIDATED"  # and the cache was repaired
    assert b.transition_skill("demo", 2, "ACTIVE").revision == 3


def test_a_skill_change_that_lost_the_race_is_not_acknowledged(tmp_path, store, skill_dir, monkeypatch):
    a, b = views(tmp_path, store)
    a.index_skill(stream_id="s1", skill_dir=skill_dir)
    sync(b, store)
    original, state = b._authoritative_skill, {"first": True}

    def racing(skill_id, accepted=None):
        item = original(skill_id, accepted)
        if state["first"]:
            state["first"] = False
            a.transition_skill("demo", 1, "VALIDATED")  # the other writer commits inside b's read-to-append window
        return item

    monkeypatch.setattr(b, "_authoritative_skill", racing)
    with pytest.raises(SkillRevisionConflictError):
        b.transition_skill("demo", 1, "VALIDATED")
    monkeypatch.undo()
    assert b.get_skill("demo").revision == 2  # exactly one writer took revision 2
    sync(b, store)
    assert b.get_skill("demo").revision == 2 and b.get_skill("demo").state == "VALIDATED"


def test_a_crash_between_skill_append_and_projection_save_does_not_wedge_the_skill(tmp_path, store, skill_dir, monkeypatch):
    a, _ = views(tmp_path, store)
    a.index_skill(stream_id="s1", skill_dir=skill_dir)
    monkeypatch.setattr(SkillCatalogEngine, "_save_skill_projection", lambda *args, **kw: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        a.transition_skill("demo", 1, "VALIDATED")
    monkeypatch.undo()
    assert a.get_skill("demo").revision == 1  # stale cache, durable Record
    assert a.transition_skill("demo", 2, "ACTIVE").state == "ACTIVE"


# --------------------------------------------------------------------------------------------- capabilities
def test_a_stale_capability_projection_is_repaired_and_a_lost_race_is_not_acknowledged(tmp_path, store):
    a, b = views(tmp_path, store)
    a.declare_capability(stream_id="s1", capability_id="cap", spec={"k": 1})
    sync(b, store)
    a.update_capability("cap", 1, {"k": 2})
    assert b.get_capability("cap").revision == 1
    with pytest.raises(CapabilityConflictError):
        b.update_capability("cap", 1, {"k": 3})
    assert b.get_capability("cap").spec == {"k": 2} and b.get_capability("cap").revision == 2


def test_concurrent_capability_updates_have_exactly_one_winner(tmp_path, store):
    a, b = views(tmp_path, store)
    a.declare_capability(stream_id="s1", capability_id="cap", spec={"k": 0})
    sync(b, store)
    barrier, outcomes = threading.Barrier(2), []

    def run(engine, value):
        barrier.wait()
        try:
            engine.update_capability("cap", 1, {"k": value})
            outcomes.append(("ok", value))
        except CapabilityConflictError:
            outcomes.append(("conflict", value))

    threads = [threading.Thread(target=run, args=(a, 1)), threading.Thread(target=run, args=(b, 2))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(o[0] for o in outcomes) == ["conflict", "ok"]
    winner = next(o[1] for o in outcomes if o[0] == "ok")
    sync(a, store)
    assert a.get_capability("cap").spec == {"k": winner} and a.get_capability("cap").revision == 2
