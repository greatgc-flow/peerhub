import ast
import subprocess
import sys
from pathlib import Path

import pytest

from peerhub.core.models import Peer
from tests.communication.spec import ROOT

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
    for p in (PKG / "core").rglob("*.py"):
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


@pytest.mark.catalog_id("ARCH-001")
def test_arch_001_core_import_graph_forbids_extension_dependency():
    core = _core_modules()
    assert "peerhub.core.store" in core and "peerhub.core.models" in core
    reach = _reachable(core)
    bad = {m: via for m, via in reach.items() if m.startswith(FORBIDDEN_PREFIXES)}
    assert not bad, f"Core reaches extensions: {bad}"


@pytest.mark.catalog_id("ARCH-002")
def test_arch_002_core_boots_with_extension_tables_absent(harness):
    for pid in ("a", "b"):
        harness.create_peer({"peer_id": pid})
    harness.create_stream({"stream_id": "s", "members": ["a", "b"]})
    rec = harness.append_record(dict(created_at="2026-10-01T00:00:00Z", stream_id="s", author_peer_id="a", kind="message", body="hi", idempotency_key="k1"))
    assert [r.record_id for r in harness.read_records("s")] == [rec.record_id]
    assert set(harness.table_names()) == {"peers", "streams", "stream_members", "records", "offsets"}
    code = ("import sys, peerhub.core.store, peerhub.core.models;"
            "bad=[m for m in sys.modules if m.startswith('peerhub.extensions')];"
            "sys.exit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT).returncode == 0


@pytest.mark.catalog_id("ARCH-003")
def test_arch_003_no_vendor_peer_cardinality_ceiling(harness):
    ids = [f"vendor-peer-{i:02d}" for i in range(32)]
    for i in ids:
        harness.create_peer({"peer_id": i, "adapter_ref": "one-adapter-kind"})
    harness.create_stream({"stream_id": "wide", "members": ids})
    assert all(harness.get_peer(i) is not None for i in ids)
    assert harness.get_stream("wide").members == sorted(ids)
    harness.create_stream({"stream_id": "wide2", "members": ids[:16]})
    assert len(harness.get_stream("wide2").members) == 16


DIAG_MOD = "peerhub.extensions.diag"
DIAG_ALLOWED_INTERNAL = {"peerhub.extensions.observation_model", "peerhub.core.models", "peerhub.core.schema_version"}  # pure models + freshness + pure schema-version contract (no store, no writers; W7 MIG-003)
DIAG_BANNED_STDLIB = {"subprocess", "socket", "shutil", "multiprocessing", "threading", "os", "tempfile", "ctypes", "http", "urllib",
                      "asyncio", "signal", "pty", "webbrowser", "smtplib", "ftplib", "pickle", "shelve"}
DIAG_BANNED_NAMES = {"CoreStore", "ObservationStore", "SessionBridge", "Bridge", "ClaimStore", "register_peer", "create_stream", "append_record",
                     "advance_offset_cas", "cas_stream", "record_observation", "capture", "persist", "register_resource_pool", "run_migrations",
                     "subprocess", "Popen", "executescript", "executemany", "system", "remove", "unlink", "rename", "chmod", "write_text",
                     "write_bytes", "rmtree", "commit"}
WRITE_SQL = {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE", "VACUUM", "REINDEX", "ATTACH", "DETACH", "SAVEPOINT", "RELEASE"}


def diag_module_violations(text: str, mod: str = DIAG_MOD) -> list[str]:
    """Module-level ARCH-004 rule (D-W0-4): the WHOLE Diag module, not one class, may only read."""
    import sys

    tree = ast.parse(text)
    bad: list[str] = []
    for n in ast.walk(tree):
        names: list[str] = []
        if isinstance(n, ast.Import):
            names = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            root = n.module or ""
            if n.level:
                bad.append(f"relative import {root!r}")
                continue
            names = [root] + [f"{root}.{a.name}" for a in n.names if root.startswith("peerhub")]
        for name in names:
            top = name.split(".")[0]
            if top == "peerhub":
                if not any(name == a or name.startswith(a + ".") for a in DIAG_ALLOWED_INTERNAL):
                    bad.append(f"internal import {name}")
            elif top in DIAG_BANNED_STDLIB or top not in sys.stdlib_module_names and top not in ("pydantic",):
                bad.append(f"import {name}")
    ids = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    bad += [f"name {x}" for x in sorted(ids & DIAG_BANNED_NAMES)]
    for s in (n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)):
        words = s.upper().replace("(", " ").split()
        if len(words) > 1 and words[0] in WRITE_SQL:
            bad.append(f"write SQL {s[:40]!r}")
        if words[:2] == ["BEGIN", "IMMEDIATE"] or words[:2] == ["BEGIN", "EXCLUSIVE"]:
            bad.append(f"write transaction {s[:40]!r}")
    for n in ast.walk(tree):  # dynamic SQL construction can hide any statement from the literal scan: only literal first args
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("execute", "executescript", "executemany"):
            if not n.args or not (isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                bad.append(f"non-literal SQL passed to {n.func.attr}")
    for n in ast.walk(tree):  # every open() must be read-only; every sqlite3.connect must be mode=ro
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open":
            modes = [a.value for a in n.args[1:2] if isinstance(a, ast.Constant)] + [k.value.value for k in n.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
            if any(set(str(m)) & set("wax+") for m in modes):
                bad.append("writable open()")
    if text.count("sqlite3.connect(") != text.count("mode=ro") or text.count("sqlite3.connect(") < 1:
        bad.append("sqlite3.connect without mode=ro (or no connect)")
    return bad


@pytest.mark.catalog_id("ARCH-004")
def test_arch_004_diag_dependency_surface_is_read_only():
    path = ROOT / "peerhub/extensions/diag.py"
    assert path.exists() and not (ROOT / "peerhub/extensions/observation_and_diag.py").exists()  # ReadonlyDiag owns its module (D-W0-4)
    text = path.read_text(encoding="utf-8")
    assert diag_module_violations(text) == []
    assert "class ReadonlyDiag" in text and "class ObservationStore" not in text
    # the checker itself rejects each kind of violation (a checker that accepts everything would make this test vacuous)
    mutants = {"internal import": "from peerhub.extensions.observation import ObservationStore\n",
               "stdlib import": "import subprocess\n",
               "store import": "from peerhub.core.store import CoreStore\n",
               "write sql": "X = 'INSERT INTO t VALUES (1)'\n",
               "begin immediate": "X = 'BEGIN IMMEDIATE'\n",
               "writable open": "f = open('x', 'w')\n",
               "rw connect": "import sqlite3\nc = sqlite3.connect('x')\n",
               "chr smuggling": "def g(c):\n    c.execute(chr(73) + 'NSERT INTO t VALUES (1)')\n",
               "concat smuggling": "def g(c, t):\n    c.execute('SELECT * FROM ' + t)\n",
               "fstring smuggling": "def g(c, t):\n    c.execute(f'SELECT * FROM {t}')\n",
               "format smuggling": "def g(c, t):\n    c.execute('SELECT * FROM {}'.format(t))\n",
               "variable smuggling": "def g(c, q):\n    c.execute(q)\n",
               "script smuggling": "def g(c):\n    c.executescript('SELECT 1')\n",
               "many smuggling": "def g(c, q):\n    c.executemany(q, [])\n",
               "banned call": "def f(s):\n    s.append_record()\n"}
    for label, extra in mutants.items():
        assert diag_module_violations(text + chr(10) + extra), label
    # positive control for the checker: literal SQL (incl. implicit concatenation) is accepted
    assert diag_module_violations(text + chr(10) + "def g(c):" + chr(10) + "    c.execute('SELECT 1 ' 'FROM t')" + chr(10)) == []
    # positive control for the checker: harmless additions are still accepted
    assert diag_module_violations(text + chr(10) + "import hashlib" + chr(10) + "Y = 'SELECT 1'" + chr(10)) == []
    # every non-dunder public method of ReadonlyDiag is a reader
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ReadonlyDiag")
    assert {n.name for n in cls.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")} == {"render", "inspect_stream_health"}


@pytest.mark.catalog_id("ARCH-005")
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


# ----------------------------------------------------------------------------- extension independence (composition root = peerhub/cli)
_OBSERVATION_DIAG = ("observation.py", "observation_model.py", "quota_capture.py", "quota_probes.py", "quota_types.py",
                     "diag.py", "diag_quota.py", "diag_watch.py", "schema_guard.py")
_ALLOWED_EXTENSION_IMPORTS = {"peerhub.extensions.observation", "peerhub.extensions.observation_model", "peerhub.extensions.quota_types",
                              "peerhub.extensions.schema_guard", "peerhub.extensions.diag", "peerhub.extensions.peer_kinds", "peerhub.extensions.quota_probes",
                              "peerhub.extensions.binary_resolution", "peerhub.extensions.process_tree"}  # shared, dependency-free helpers inside the M1 trio


def _extension_imports(path):
    import ast

    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("peerhub.extensions"):
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names if a.name.startswith("peerhub.extensions")}
    return out


def test_arch_observation_and_diag_do_not_import_the_bridge_or_m2_m3_extensions():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "peerhub" / "extensions"
    bad = {name: sorted(m for m in _extension_imports(root / name) if m not in _ALLOWED_EXTENSION_IMPORTS)
           for name in _OBSERVATION_DIAG}
    assert {k: v for k, v in bad.items() if v} == {}  # e.g. quota_capture importing ask.ALIASES coupled Observation to the Bridge entry
