"""M2/M3 exit-gate evidence (ToGo MILESTONE_GATES):

1. With EVERY M2/M3 extension disabled, M1 (Core + Session Bridge/Observation/Diag modules) works unchanged.
2. Deleting every derived projection and rebuilding from the authoritative Records alone yields logically identical state.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from peerhub.core.models import Peer, Stream, StreamState
from peerhub.core.store import CoreStore
from peerhub.extensions.memory import MemoryStore
from peerhub.extensions.skills import SkillCatalogEngine
from peerhub.extensions.work import WorkProjection

REPO = Path(__file__).resolve().parents[2]
M2_M3_MODULES = ("work", "artifact", "skills", "mcp", "backup", "eval", "host", "manifest", "search", "memory", "a2a", "a2a_http",
                 "a2a_journal", "routing", "orchestration", "approval", "runtime_port", "source_records")

SCRIPT = textwrap.dedent("""
    import importlib.abc, json, sys
    BLOCKED = set(json.loads(sys.argv[1]))
    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            parts = name.split(".")
            if len(parts) >= 3 and parts[:2] == ["peerhub", "extensions"] and parts[2] in BLOCKED:
                raise ImportError("extension disabled: " + name)
    sys.meta_path.insert(0, Block())
    try:
        import peerhub.extensions.work
        control = "NOT BLOCKED"
    except ImportError:
        control = "blocked"
    from peerhub.cli.app import main
    db = sys.argv[2]
    codes = [
        main(["--db", db, "peer", "register", "--peer", "a"]),
        main(["--db", db, "peer", "register", "--peer", "b"]),
        main(["--db", db, "stream", "create", "--stream", "s", "--members", "a", "b"]),
        main(["--db", db, "record", "append", "--stream", "s", "--author-peer", "a", "--kind", "message", "--body", '"hi"',
              "--idempotency-key", "k1", "--created-at", "2026-10-01T00:00:00Z"]),
        main(["--db", db, "offset", "advance", "--peer", "b", "--stream", "s", "--position", "1", "--revision", "1"]),
        main(["--db", db, "diag", "health"]),
        main(["--db", db, "diag", "quota", "--json"]),
        main(["--db", db, "diag", "--view", "rich"]),
    ]
    import peerhub.extensions.ask, peerhub.extensions.bridge, peerhub.extensions.observation, peerhub.extensions.diag
    import peerhub.extensions.quota_capture, peerhub.extensions.diag_quota
    loaded = sorted(m for m in sys.modules if m.startswith("peerhub.extensions.") and m.split(".")[2] in BLOCKED)
    print("RESULT" + json.dumps({"control": control, "codes": codes, "loaded": loaded}))
""")


def test_m1_works_with_every_m2_m3_extension_disabled(tmp_path):
    run = subprocess.run([sys.executable, "-c", SCRIPT, json.dumps(list(M2_M3_MODULES)), str(tmp_path / "core.db")],
                         cwd=REPO, capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stderr[-1500:]
    result = json.loads(next(line for line in run.stdout.splitlines() if line.startswith("RESULT"))[len("RESULT"):])
    assert result["control"] == "blocked"  # the blocker really blocks (control for the assertions below)
    # `diag quota` exits 5 and the dashboard 4 (PARTIAL) on a store with no observation tables yet: the M1 contract for a
    # never-refreshed workspace (read-only, nothing is created or refreshed). Everything else is 0.
    assert result["codes"] == [0, 0, 0, 0, 0, 0, 5, 4] and result["loaded"] == []  # M1 never imported an M2/M3 module


# --------------------------------------------------------------------------- rebuild equivalence over all projections
def test_deleting_every_derived_projection_and_rebuilding_from_records_is_logically_identical(tmp_path):
    store = CoreStore(tmp_path / "core.db")
    for peer in ("p1", "memory"):
        store.register_peer(Peer(peer_id=peer))
    store.create_stream(Stream(stream_id="s1", members=["p1"], state=StreamState.OPEN))
    store.create_stream(Stream(stream_id="mem", members=["memory"], state=StreamState.OPEN))
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: d\nversion: 1.0.0\ntags: [a, b]\n---\n\nbody\n", encoding="utf-8")

    work_db, skill_db, mem_db = tmp_path / "work.db", tmp_path / "skills.db", tmp_path / "memory.db"
    work = WorkProjection(work_db, store=store)
    work.create_work(stream_id="s1", work_id="w1", title="T", spec={"k": 1})
    work.transition_work("w1", 1, "ACTIVE")
    work.checkpoint_work("w1", 2, {"step": 3})
    work.create_work(stream_id="s1", work_id="w2", title="Other")
    work.transition_work("w2", 1, "CANCELLED")

    skills = SkillCatalogEngine(skill_db, store=store)
    skills.index_skill(stream_id="s1", skill_dir=skill_dir)
    skills.transition_skill("demo", 1, "VALIDATED")
    skills.transition_skill("demo", 2, "ACTIVE")
    skills.declare_capability(stream_id="s1", capability_id="cap", spec={"image": "py"})
    skills.update_capability("cap", 1, {"image": "py312"})

    memory = MemoryStore(mem_db, core_store=store, stream_id="mem", author_peer_id="memory")
    a = memory.propose_memory("k:a", "alpha", "fact:1")
    memory.accept_memory(a.memory_id)
    b = memory.propose_memory("k:b", "beta", "fact:2")
    memory.accept_memory(b.memory_id)
    memory.supersede_memory(a.memory_id, b.memory_id)
    c = memory.propose_memory("k:c", "gamma", "fact:3")
    memory.reject_memory(c.memory_id)
    memory_ids = [a.memory_id, b.memory_id, c.memory_id]

    def logical():
        return {
            "work": sorted((w.work_id, w.state, w.revision, w.title, json.dumps(w.spec, sort_keys=True), json.dumps(w.checkpoint, sort_keys=True))
                           for w in work.list_work()),
            "skills": [(s.skill_id, s.state, s.revision, s.tree_digest, s.file_count, tuple(s.tags)) for s in skills.list_skills()],
            "cap": (skills.get_capability("cap").revision, json.dumps(skills.get_capability("cap").spec, sort_keys=True)),
            "memory": [(m.memory_id, m.state, m.revision, m.superseded_by) for m in (memory.get_memory(i) for i in memory_ids)],
        }

    before = logical()
    for db in (work_db, skill_db, mem_db):  # delete EVERY derived projection (with its WAL/SHM)
        for suffix in ("", "-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
    work2 = WorkProjection(work_db, store=store)
    skills2 = SkillCatalogEngine(skill_db, store=store)
    memory2 = MemoryStore(mem_db, core_store=store, stream_id="mem", author_peer_id="memory")
    work2.rebuild_projection(store.read_records("s1"))
    skills2.rebuild_index(store.read_records("s1"))
    memory2.rebuild_from_records(store)
    work, skills, memory = work2, skills2, memory2
    assert logical() == before  # authoritative Records alone reproduce the state exactly


def test_the_gate_module_list_names_only_real_extension_modules():
    ext = REPO / "peerhub" / "extensions"
    assert all((ext / f"{name}.py").is_file() for name in M2_M3_MODULES)  # a renamed module must not silently escape the gate
    m1_trio = {"ask", "bridge", "bridge_claims", "catchup", "observation", "observation_model", "quota_capture", "quota_probes",
               "quota_types", "diag", "diag_quota", "diag_watch", "peer_kinds", "schema_guard"}
    assert not (m1_trio & set(M2_M3_MODULES))
