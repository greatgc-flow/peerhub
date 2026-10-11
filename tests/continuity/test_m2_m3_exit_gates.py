"""M2/M3 exit-gate evidence (ToGo 09_ROADMAP):

M2 Exit: with every extension disabled M1 works; deleting every derived projection and rebuilding from the authoritative Records alone
         yields logically identical state.
M3 Exit: with the whole M3 layer disabled M1 AND M2 work; Search rebuild, Memory supersession/revocation replay, deterministic Routing,
         bounded Orchestration crash recovery and exact-effect Approval are proven by their own suites (named in the closure record).
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from peerhub.core.models import Peer, Stream, StreamState
from peerhub.core.store import CoreStore
from peerhub.extensions.artifact import ArtifactStore
from peerhub.extensions.memory import MemoryStore
from peerhub.extensions.search import SearchIndex
from peerhub.extensions.skills import SkillCatalogEngine
from peerhub.extensions.work import WorkProjection

REPO = Path(__file__).resolve().parents[2]
M3_MODULES = ("search", "memory", "a2a", "a2a_http", "a2a_journal", "routing", "orchestration", "approval", "runtime_port", "source_records")
M2_MODULES = ("work", "artifact", "skills", "mcp", "backup", "eval", "host", "manifest", "sqlite_tx", "boundary", "authoritative")
M2_M3_MODULES = M2_MODULES + M3_MODULES

PRELUDE = textwrap.dedent("""
    import importlib.abc, json, sys
    BLOCKED = set(json.loads(sys.argv[1]))
    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            parts = name.split(".")
            if len(parts) >= 3 and parts[:2] == ["peerhub", "extensions"] and parts[2] in BLOCKED:
                raise ImportError("extension disabled: " + name)
    sys.meta_path.insert(0, Block())
    def control(module):
        try:
            __import__(module)
            return "NOT BLOCKED"
        except ImportError:
            return "blocked"
    def loaded():
        return sorted(m for m in sys.modules if m.startswith("peerhub.extensions.") and m.split(".")[2] in BLOCKED)
""")

M1_SCRIPT = PRELUDE + textwrap.dedent("""
    from peerhub.cli.app import main
    db = sys.argv[2]
    codes = [
        main(["--db", db, "peer", "register", "--peer", "a"]),
        main(["--db", db, "peer", "register", "--peer", "b"]),
        main(["--db", db, "stream", "create", "--stream", "s", "--members", "a", "b"]),
        main(["--db", db, "record", "append", "--stream", "s", "--author-peer", "a", "--kind", "message", "--body", '"hi"',
              "--idempotency-key", "k1", "--created-at", "2026-10-01T00:00:00Z"]),
        main(["--db", db, "offset", "advance", "--peer", "b", "--stream", "s", "--position", "1", "--revision", "1"]),
    ]
    # Session Bridge + Observation through the real `ask` path with a scripted runtime (no provider is called)
    from peerhub.extensions.ask import ask
    from tests.communication.fakes import FakeRuntimeTarget
    runtime = FakeRuntimeTarget()
    runtime.script_deliver(("started", "exec-1"), ("terminal", {"response": "answer"}))
    first = ask(db, "cx", "question", request_id="req", runtime=runtime)
    replay = ask(db, "cx", "question", request_id="req", runtime=FakeRuntimeTarget())
    # Diag reads it all back (the dashboard shows the per-peer ask history built from Observation evidence)
    import io, contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        codes.append(main(["--db", db, "diag", "--view", "rich"]))
        codes.append(main(["--db", db, "diag", "health"]))
    print("RESULT" + json.dumps({"control": control("peerhub.extensions.work"), "codes": codes, "loaded": loaded(),
                                 "ask": [first["status"], first["response"], replay["status"], replay["response"]],
                                 "dashboard_has_asks": "ASKS" in out.getvalue() and "cx" in out.getvalue()}))
""")

M2_SCRIPT = PRELUDE + textwrap.dedent("""
    from pathlib import Path
    from peerhub.core.models import Peer, Stream
    from peerhub.core.store import CoreStore
    root = Path(sys.argv[2])
    store = CoreStore(root / "core.db")
    store.register_peer(Peer(peer_id="p1"))
    store.create_stream(Stream(stream_id="s1", members=["p1"]))
    from peerhub.extensions.work import WorkProjection
    work = WorkProjection(root / "work.db", store=store)
    work.create_work(stream_id="s1", work_id="w1", title="T")
    work_state = work.transition_work("w1", 1, "ACTIVE").state
    skill_dir = root / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\\nname: demo\\ndescription: d\\nversion: 1.0.0\\n---\\n\\nbody\\n", encoding="utf-8")
    from peerhub.extensions.skills import SkillCatalogEngine
    skills = SkillCatalogEngine(root / "skills.db", store=store)
    skills.index_skill(stream_id="s1", skill_dir=skill_dir)
    skill_state = skills.transition_skill("demo", 1, "VALIDATED").state
    from peerhub.extensions.artifact import ArtifactStore
    artifacts = ArtifactStore(root / "artifacts")
    digest = artifacts.commit_staged(artifacts.stage_bytes(b"payload"))
    exported = artifacts.export_verified(digest, root / "out.bin")
    from peerhub.extensions.eval import ExactMatchEvaluator, capture_trace, register_dataset, run_eval, TraceSpan
    ds = register_dataset("d", [{"expected": "x"}], artifacts)
    trace = capture_trace("t", [TraceSpan("s0", None, "n", 0, 0, {"output": "x"})])
    verdict = run_eval(ExactMatchEvaluator(), trace, ds, store=artifacts).verdict
    from peerhub.extensions.backup import create_backup, restore_authoritative
    create_backup(root / "core.db", root / "artifacts", root / "bundle", skill_dir=root / "skills")
    restore_authoritative(root / "bundle", root / "restored")
    restored = CoreStore(root / "restored" / "core.db").get_peer("p1") is not None
    from peerhub.extensions.host import ExtensionHost
    host_report = ExtensionHost(root / "host.db", extensions_dir=root / "no-extensions").boot()
    from peerhub.extensions.mcp import MCPServer
    mcp = MCPServer(store, work_projection=work, artifact_store=artifacts, skill_catalog=skills)
    answer = json.loads(mcp.handle_message(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})) or "{}")
    print("RESULT" + json.dumps({"control": control("peerhub.extensions.search"), "loaded": loaded(), "work": work_state, "skill": skill_state,
                                 "exported": exported, "eval": verdict, "restored": restored,
                                 "boot": host_report["failed"], "mcp": "result" in answer}))
""")


def run_script(script: str, blocked: tuple[str, ...], arg: Path) -> dict:
    import os
    env = dict(os.environ)
    env.pop("PEERHUB_DB", None)
    run = subprocess.run([sys.executable, "-c", script, json.dumps(list(blocked)), str(arg)], cwd=REPO, capture_output=True, text=True, timeout=240, env=env)
    assert run.returncode == 0, run.stderr[-2000:]
    return json.loads(next(line for line in run.stdout.splitlines() if line.startswith("RESULT"))[len("RESULT"):])


def test_m1_works_with_every_m2_and_m3_extension_disabled(tmp_path):
    result = run_script(M1_SCRIPT, M2_M3_MODULES, tmp_path / "core.db")
    assert result["control"] == "blocked"  # the blocker really blocks (control for the assertions below)
    assert result["codes"] == [0, 0, 0, 0, 0, 0, 0] and result["loaded"] == []  # Core CRUD, the dashboard and health all succeed
    assert result["ask"] == ["delivered", "answer", "recovered_terminal", "answer"]  # Bridge + Observation work, the retry is idempotent
    assert result["dashboard_has_asks"] is True  # Diag shows what Observation recorded


def test_m2_works_with_the_whole_m3_layer_disabled(tmp_path):
    result = run_script(M2_SCRIPT, M3_MODULES, tmp_path)
    assert result["control"] == "blocked" and result["loaded"] == []  # no M3 module was imported by any M2 operation
    assert (result["work"], result["skill"], result["eval"]) == ("ACTIVE", "VALIDATED", "PASSED")
    assert result["exported"] == len(b"payload") and result["restored"] is True
    assert result["boot"] == {} and result["mcp"] is True


# --------------------------------------------------------------------------- rebuild equivalence over every projection
def test_deleting_every_derived_projection_and_rebuilding_from_records_is_logically_identical(tmp_path):
    store = CoreStore(tmp_path / "core.db")
    for peer in ("p1", "memory"):
        store.register_peer(Peer(peer_id=peer))
    store.create_stream(Stream(stream_id="s1", members=["p1"], state=StreamState.OPEN))
    store.create_stream(Stream(stream_id="mem", members=["memory"], state=StreamState.OPEN))
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: d\nversion: 1.0.0\ntags: [a, b]\n---\n\nbody\n", encoding="utf-8")

    work_db, skill_db, mem_db, search_db = tmp_path / "work.db", tmp_path / "skills.db", tmp_path / "memory.db", tmp_path / "search.db"
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

    artifacts = ArtifactStore(tmp_path / "artifacts")
    digest = artifacts.commit_staged(artifacts.stage_bytes(b"benchmark evaluation notes"))

    memory = MemoryStore(mem_db, core_store=store, stream_id="mem", author_peer_id="memory")
    a = memory.propose_memory("k:a", "alpha", "fact:1", confidence=0.8)
    memory.accept_memory(a.memory_id)
    b = memory.propose_memory("k:b", "beta", "fact:2", memory_type="semantic", confidence=0.6)
    memory.accept_memory(b.memory_id)
    memory.supersede_memory(a.memory_id, b.memory_id)
    c = memory.propose_memory("k:c", "gamma", "fact:3")
    memory.reject_memory(c.memory_id)
    memory_ids = [a.memory_id, b.memory_id, c.memory_id]

    search = SearchIndex(search_db)
    search.index_artifact(digest=digest, metadata={"name": "notes.txt", "kind": "dataset"}, snippet="benchmark evaluation notes")
    search.rebuild_from_sources(store, artifacts, skills)

    def full(obj):
        return json.dumps(obj.__dict__ if hasattr(obj, "__dict__") else obj, sort_keys=True, default=str)

    def logical():
        return {
            "work": sorted(full(w) for w in work.list_work()),
            "skills": sorted(full(s) for s in skills.list_skills()),
            "cap": full(skills.get_capability("cap")),
            "memory": [full(memory.get_memory(i)) for i in memory_ids],  # key, content, source_ref, confidence, type, lifecycle: ALL fields
            "search": sorted((r.doc_id, r.doc_type, r.source_ref, json.dumps(r.metadata, sort_keys=True)) for q in ("demo", "benchmark")
                             for r in search.search(q)),
        }

    before = logical()
    assert before["search"], "the search projection must hold something for the equivalence check to mean anything"
    for db in (work_db, skill_db, mem_db, search_db):  # delete EVERY derived projection (with its WAL/SHM)
        for suffix in ("", "-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
    work2 = WorkProjection(work_db, store=store)
    skills2 = SkillCatalogEngine(skill_db, store=store)
    memory2 = MemoryStore(mem_db, core_store=store, stream_id="mem", author_peer_id="memory")
    search2 = SearchIndex(search_db)
    work2.rebuild_projection(store.read_records("s1"))
    skills2.rebuild_index(store.read_records("s1"))
    memory2.rebuild_from_records(store)
    search2.index_artifact(digest=digest, metadata={"name": "notes.txt", "kind": "dataset"}, snippet="benchmark evaluation notes")
    search2.rebuild_from_sources(store, artifacts, skills2)
    work, skills, memory, search = work2, skills2, memory2, search2
    assert logical() == before  # authoritative Records (and the immutable blobs) alone reproduce the state exactly


def test_the_gate_module_lists_name_only_real_extension_modules_and_never_the_m1_modules():
    ext = REPO / "peerhub" / "extensions"
    assert all((ext / f"{name}.py").is_file() for name in M2_M3_MODULES)  # a renamed module must not silently escape the gate
    m1 = {"ask", "bridge", "bridge_claims", "catchup", "observation", "observation_model", "quota_capture", "quota_probes",
          "quota_types", "binary_resolution", "process_tree", "diag", "diag_quota", "diag_watch", "peer_kinds", "schema_guard",
          "quota_projection"}
    assert not (m1 & set(M2_M3_MODULES))
    # every extension module is classified: nothing can quietly sit outside both the M1 set and the gated sets
    classified = m1 | set(M2_M3_MODULES) | {"direction"}
    unclassified = {p.stem for p in ext.glob("*.py") if p.stem != "__init__"} - classified
    assert unclassified == set(), f"classify new extension modules as M1, M2 or M3 in this gate: {sorted(unclassified)}"
