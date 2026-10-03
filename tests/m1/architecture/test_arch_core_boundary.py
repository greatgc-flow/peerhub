import ast
import subprocess
import sys
from pathlib import Path

import pytest

from peerhub.m1.models import Peer
from tests.m1.spec import ROOT

pytestmark = pytest.mark.architecture
PKG = ROOT / "peerhub"
FORBIDDEN_PREFIXES = ("peerhub.extensions",)


def _module_file(mod: str) -> Path | None:
    rel = Path(*mod.split("."))
    for cand in (ROOT / rel.with_suffix(".py"), ROOT / rel / "__init__.py"):
        if cand.exists():
            return cand
    return None


def _imports(path: Path, mod: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    is_pkg = path.name == "__init__.py"
    base = mod.split(".") if is_pkg else mod.split(".")[:-1]
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                parent = base[: len(base) - (n.level - 1)]
                root = ".".join(parent + ([n.module] if n.module else []))
            else:
                root = n.module or ""
            out.add(root)
            out.update(f"{root}.{a.name}" for a in n.names)
    return out


def _core_modules() -> dict[str, Path]:
    mods = {}
    for p in (PKG / "m1").rglob("*.py"):
        parts = list(p.relative_to(ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        mods[".".join(parts)] = p
    return mods


def _reachable(start: dict[str, Path]) -> dict[str, str]:
    """first-party module -> importer chain; transitive over peerhub.* modules."""
    seen: dict[str, str] = {}
    stack = [(m, m) for m in start]
    done: set[str] = set()
    while stack:
        mod, origin = stack.pop()
        if mod in done:
            continue
        done.add(mod)
        f = _module_file(mod)
        if f is None:
            continue
        for imp in _imports(f, mod):
            if imp.startswith("peerhub"):
                seen.setdefault(imp, f"{origin} -> {mod}")
                stack.append((imp, origin))
    return seen


@pytest.mark.m1_id("ARCH-001")
def test_arch_001_core_import_graph_forbids_extension_dependency():
    core = _core_modules()
    assert "peerhub.m1.store" in core and "peerhub.m1.models" in core
    reach = _reachable(core)
    bad = {m: via for m, via in reach.items() if m.startswith(FORBIDDEN_PREFIXES)}
    assert not bad, f"Core reaches extensions: {bad}"


@pytest.mark.m1_id("ARCH-002")
def test_arch_002_core_boots_with_extension_tables_absent(harness):
    for pid in ("a", "b"):
        harness.create_peer({"peer_id": pid})
    harness.create_stream({"stream_id": "s", "members": ["a", "b"]})
    rec = harness.append_record(dict(stream_id="s", author_peer_id="a", kind="message", body="hi", idempotency_key="k1"))
    assert [r.record_id for r in harness.read_records("s")] == [rec.record_id]
    assert set(harness.table_names()) == {"peers", "streams", "stream_members", "records", "offsets"}
    code = ("import sys, peerhub.m1.store, peerhub.m1.models;"
            "bad=[m for m in sys.modules if m.startswith('peerhub.extensions')];"
            "sys.exit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT).returncode == 0


@pytest.mark.m1_id("ARCH-003")
def test_arch_003_no_vendor_peer_cardinality_ceiling(harness):
    ids = [f"vendor-peer-{i:02d}" for i in range(32)]
    for i in ids:
        harness.create_peer({"peer_id": i, "adapter_ref": "one-adapter-kind"})
    harness.create_stream({"stream_id": "wide", "members": ids})
    assert all(harness.get_peer(i) is not None for i in ids)
    assert harness.get_stream("wide").members == sorted(ids)
    harness.create_stream({"stream_id": "wide2", "members": ids[:16]})
    assert len(harness.get_stream("wide2").members) == 16


@pytest.mark.m1_id("ARCH-004")
def test_arch_004_diag_dependency_surface_is_read_only():
    path = ROOT / "peerhub/extensions/observation_and_diag.py"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ReadonlyDiag")
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    ann = {a.arg: ast.unparse(a.annotation) for a in init.args.args if a.arg != "self" and a.annotation}
    assert ann and all(v in ("str | Path", "str", "Path") for v in ann.values()), ann
    names = {n.id for n in ast.walk(cls) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(cls) if isinstance(n, ast.Attribute)}
    banned = {"CoreStore", "ObservationStore", "SessionBridge", "register_peer", "create_stream", "append_record",
              "advance_offset_cas", "record_observation", "subprocess", "Popen", "executescript"}
    assert not (names & banned), names & banned
    for s in (n.value for n in ast.walk(cls) if isinstance(n, ast.Constant) and isinstance(n.value, str)):
        first = s.strip().split(None, 1)[0].upper() if s.strip() else ""
        assert first not in {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE"}, s
    src = ast.get_source_segment(text, cls)
    assert src.count("sqlite3.connect(") == src.count("mode=ro") >= 1
    imps = _imports(path, "peerhub.extensions.observation_and_diag")
    assert not [i for i in imps if i.startswith((
        "peerhub.extensions.session_bridge", "peerhub.adapters", "peerhub.dispatch", "peerhub.application",
        "peerhub.runtime", "peerhub.m1.store", "subprocess"))]


@pytest.mark.m1_id("ARCH-005")
def test_arch_005_core_identity_is_not_runtime_identity(harness):
    harness.create_peer({"peer_id": "peer-x"})
    before = harness.get_peer("peer-x")
    digest = harness.state_digest()
    bridge_mapping: dict[str, dict] = {}  # bridge-side only
    for desc in ({"external_session_id": "sess-1", "pid": 111, "profile": "a"},
                 {"external_session_id": "sess-2", "pid": 222, "profile": "b"}):
        bridge_mapping["peer-x"] = desc
        after = harness.get_peer("peer-x")
        assert after.peer_id == before.peer_id
        ser = after.model_dump(mode="json")
        assert not any(k in ser or k in str(ser.get("metadata")) for k in desc)
        assert not any(w in f for f in Peer.model_fields for w in ("session", "pid", "process", "profile"))
    assert harness.state_digest() == digest
