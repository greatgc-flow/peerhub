"""Wave 7 package tier: REL-001, REL-002, REL-003, REL-008, REL-009 (built wheel + sdist, fresh venv, no repo on the path)."""
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import textwrap
import zipfile
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

from tests.m1.harness import pkg_env
from tests.m1.harness.pkg_env import REPO

pytestmark = [pytest.mark.package, pytest.mark.release, pytest.mark.timeout(900)]

# Packaging allowlist (literal): runtime data that MUST ship, and development junk that MUST NOT.
M1_SCHEMAS = {"offset", "peer", "record", "stream"}
EXT_SCHEMAS = {"peer-observation", "resource-pool"}
PROHIBITED_PARTS = {"tests", "docs", ".github", "__pycache__", ".pytest_cache", ".hypothesis", "build", ".git", ".peerhub", "tools"}
PROHIBITED_SUFFIXES = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log", ".orig", ".rej", ".swp")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _repo_py_files() -> set[str]:
    return {p.relative_to(REPO).as_posix() for p in (REPO / "peerhub").rglob("*.py") if "__pycache__" not in p.parts}


def _meta(text: str, key: str) -> list[str]:
    return re.findall(rf"^{key}: (.*)$", text, re.M)


# ---------------------------------------------------------------------- REL-001
def test_rel_001_build_wheel_and_sdist_from_clean_checkout_with_consistent_metadata(dist):
    ver = pkg_env.source_version()
    assert dist.wheel.name == f"peerhub-{ver}-py3-none-any.whl"
    assert dist.sdist.name == f"peerhub-{ver}.tar.gz"
    wmeta = pkg_env.wheel_text(dist.wheel, ".dist-info/METADATA")
    with tarfile.open(dist.sdist) as t:
        smeta = t.extractfile(f"peerhub-{ver}/PKG-INFO").read().decode("utf-8").replace("\r\n", "\n")
    for meta in (wmeta, smeta):
        assert _meta(meta, "Name") == ["peerhub"] and _meta(meta, "Version") == [ver]
        assert [SpecifierSet(x) for x in _meta(meta, "Requires-Python")] == [SpecifierSet(pkg_env.pyproject()["project"]["requires-python"])]
        assert {re.split(r"[<>=!~ ;]", d)[0] for d in _meta(meta, "Requires-Dist") if "extra ==" not in d} == {
            "jsonschema", "psutil", "pydantic", "typing_extensions"}
    ep = pkg_env.wheel_text(dist.wheel, ".dist-info/entry_points.txt")
    assert "peerhub = peerhub.cli:main" in ep and "peerhub-m1 = peerhub.m1_cli:main" in ep
    # the built wheel and the source tree agree on the version actually importable from the wheel
    assert pkg_env.wheel_text(dist.wheel, "peerhub/_version.py").strip() == f'__version__ = "{ver}"'


# ---------------------------------------------------------------------- REL-002
def test_rel_002_fresh_environment_install_and_import_cli_smoke(installed, outside):
    probe = installed.run(["-I", "-c", "import peerhub, peerhub.m1, sys; print(peerhub.__file__); print(peerhub.m1.__file__)"],
                          cwd=outside)
    assert probe.returncode == 0, probe.stderr
    for line in probe.stdout.split("\n")[:2]:
        p = Path(line.strip()).resolve()
        assert str(installed.venv.resolve()) in str(p) and REPO not in p.parents  # installed artifact, not the checkout
    ver = pkg_env.source_version()
    v = pkg_env.run([installed.script("peerhub"), "--version"], cwd=outside)
    assert v.returncode == 0 and ver in v.stdout
    for exe in ("peerhub", "peerhub-m1"):  # both entrypoints start and print help
        h = pkg_env.run([installed.script(exe), "--help"], cwd=outside)
        assert h.returncode == 0 and "usage:" in h.stdout.lower(), (exe, h.stderr)
    core = installed.run(["-I", "-c", "from peerhub.m1 import Peer, Stream, Record, Offset; "
                          "from peerhub.m1.store import CoreStore; print('core-import-ok')"], cwd=outside)
    assert core.stdout.strip() == "core-import-ok", core.stderr
    # negative control for the probe itself: a module that is not in the wheel really fails to import
    bad = installed.run(["-I", "-c", "import peerhub.definitely_not_shipped"], cwd=outside)
    assert bad.returncode != 0 and "ModuleNotFoundError" in bad.stderr


# ---------------------------------------------------------------------- REL-003
CRASH_SNIPPET = textwrap.dedent("""
    import os, sys
    from peerhub.m1.store import CoreStore
    def hook(point):
        if point == "append.before_commit":
            os._exit(17)
    CoreStore(sys.argv[1], fault_hook=hook).append_record(
        stream_id="s", author_peer_id="a", kind="message", body="third", idempotency_key="k3",
        created_at="2026-10-04T00:00:00Z")
""")


def _cli(installed, outside, db, *args):
    return pkg_env.run([installed.script("peerhub-m1"), "--db", str(db), *args], cwd=outside)


def test_rel_003_fresh_workspace_smoke_with_restart_and_crash(installed, outside, tmp_path):
    ws = tmp_path / "fresh workspace"
    ws.mkdir()
    db = ws / "core.db"
    assert not db.exists()
    for peer in ("a", "b"):
        assert _cli(installed, outside, db, "peer", "register", "--id", peer).returncode == 0  # each call = a new process
    assert _cli(installed, outside, db, "stream", "create", "--id", "s", "--members", "a", "b").returncode == 0
    for i in (1, 2):
        r = _cli(installed, outside, db, "record", "append", "--stream", "s", "--author", "a", "--kind", "message",
                 "--body", json.dumps(f"m{i}"), "--idemp-key", f"k{i}", "--created-at", "2026-10-04T00:00:00Z")
        assert r.returncode == 0, r.stderr
    recs = json.loads(_cli(installed, outside, db, "record", "read", "--stream", "s").stdout)
    assert [(r["position"], r["body"]) for r in recs] == [(1, "m1"), (2, "m2")]
    adv = _cli(installed, outside, db, "offset", "advance", "--peer", "b", "--stream", "s", "--position", "1", "--revision", "1")
    assert adv.returncode == 0 and json.loads(adv.stdout)["revision"] == 2
    stale = _cli(installed, outside, db, "offset", "advance", "--peer", "b", "--stream", "s", "--position", "2", "--revision", "1")
    assert stale.returncode == 3  # CAS lost: explicit error, nothing changed
    off = json.loads(_cli(installed, outside, db, "offset", "get", "--peer", "b", "--stream", "s").stdout)
    assert (off["read_through_position"], off["revision"]) == (1, 2)
    # hard crash mid-append (real process death), then restart: old state intact, retry lands exactly once
    crash = installed.run(["-I", "-c", CRASH_SNIPPET, str(db)], cwd=outside)
    assert crash.returncode == 17
    assert len(json.loads(_cli(installed, outside, db, "record", "read", "--stream", "s").stdout)) == 2
    for _ in range(2):  # the retry is idempotent
        r = _cli(installed, outside, db, "record", "append", "--stream", "s", "--author", "a", "--kind", "message",
                 "--body", json.dumps("third"), "--idemp-key", "k3", "--created-at", "2026-10-04T00:00:00Z")
        assert r.returncode == 0, r.stderr
    final = json.loads(_cli(installed, outside, db, "record", "read", "--stream", "s").stdout)
    assert [(r["position"], r["body"]) for r in final] == [(1, "m1"), (2, "m2"), (3, "third")]


# ---------------------------------------------------------------------- REL-008
def _artifact_names(dist):
    with zipfile.ZipFile(dist.wheel) as z:
        wheel = z.namelist()
    with tarfile.open(dist.sdist) as t:
        top = f"peerhub-{pkg_env.source_version()}/"
        sdist = [m.name[len(top):] for m in t.getmembers() if m.isfile()]
    return wheel, sdist


def test_rel_008_wheel_and_sdist_contain_all_runtime_data_and_every_module_and_no_dev_junk(dist, tmp_path):
    wheel, sdist = _artifact_names(dist)
    expected_data = ({f"peerhub/m1/schemas/{n}.schema.json" for n in M1_SCHEMAS}
                     | {f"peerhub/extensions/schemas/{n}.schema.json" for n in EXT_SCHEMAS}
                     | {"peerhub/config_data/ask-defaults.toml"}
                     | {p.relative_to(REPO).as_posix() for p in (REPO / "peerhub/persistence/migrations").glob("*.sql")})
    assert len([p for p in expected_data if p.endswith(".sql")]) >= 28  # oracle sanity: the migration set is not empty
    for label, names in (("wheel", wheel), ("sdist", sdist)):
        missing = expected_data - set(names)
        assert not missing, f"{label} lacks runtime data: {sorted(missing)}"
    assert _repo_py_files() <= set(wheel), f"wheel lacks modules: {sorted(_repo_py_files() - set(wheel))}"
    assert _repo_py_files() <= set(sdist), f"sdist lacks modules: {sorted(_repo_py_files() - set(sdist))}"
    for label, names in (("wheel", wheel), ("sdist", sdist)):
        junk = [n for n in names if set(Path(n).parts) & PROHIBITED_PARTS or n.endswith(PROHIBITED_SUFFIXES) or Path(n).name == ".env"]
        assert not junk, f"{label} ships development junk: {junk[:10]}"
    assert {n.split("/")[0] for n in wheel} == {"peerhub", f"peerhub-{pkg_env.source_version()}.dist-info"}
    assert {n.split("/")[0] for n in sdist} <= {"peerhub", "pyproject.toml", "README.md", "LICENSE", "PKG-INFO", "setup.cfg",
                                                "peerhub.egg-info", "CONTRIBUTING.md", "CONVENTION.md"}
    # the shipped schema bytes are the frozen spec schemas (independent hashes of the spec files)
    spec = REPO / "docs/m1_spec/04_SCHEMAS"
    with zipfile.ZipFile(dist.wheel) as z:
        for n in sorted(M1_SCHEMAS):
            assert _sha(z.read(f"peerhub/m1/schemas/{n}.schema.json")) == _sha((spec / f"{n}.schema.json").read_bytes()), n
        for n in sorted(EXT_SCHEMAS):
            assert _sha(z.read(f"peerhub/extensions/schemas/{n}.schema.json")) == _sha((spec / f"{n}.schema.json").read_bytes()), n


def test_rel_008_both_artifacts_install_and_the_shipped_data_is_loadable(dist, installed, tmp_path, outside):
    # (a) wheel: the session venv; (b) sdist: built and installed into an isolated target directory
    target = tmp_path / "from_sdist"
    r = pkg_env.run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(dist.sdist)], cwd=outside)
    assert r.returncode == 0, r.stderr[-2000:]
    loader = textwrap.dedent("""
        import importlib.resources as r, json, sys
        out = {}
        for pkg, names in (("peerhub.m1", %r), ("peerhub.extensions", %r)):
            for n in names:
                data = json.loads(r.files(pkg).joinpath("schemas/" + n + ".schema.json").read_text(encoding="utf-8"))
                assert data["type"] == "object" and set(data["required"]) <= set(data["properties"]), n
                out[pkg + ":" + n] = sorted(data["required"])
        import peerhub, os
        out["file"] = os.path.dirname(peerhub.__file__)
        print(json.dumps(out))
    """) % (sorted(M1_SCHEMAS), sorted(EXT_SCHEMAS))
    wheel_run = installed.run(["-I", "-c", loader], cwd=outside)
    assert wheel_run.returncode == 0, wheel_run.stderr
    sdist_run = pkg_env.run([sys.executable, "-c", loader], cwd=outside,
                            env=pkg_env.clean_env({"PYTHONPATH": str(target)}))
    assert sdist_run.returncode == 0, sdist_run.stderr
    w, s = json.loads(wheel_run.stdout), json.loads(sdist_run.stdout)
    assert str(target) in s.pop("file") and "site-packages" in w.pop("file")
    assert w == s and len(w) == len(M1_SCHEMAS) + len(EXT_SCHEMAS)


# ---------------------------------------------------------------------- REL-009
def test_rel_009_installed_artifact_runs_without_dev_dependencies(installed, outside, tmp_path):
    # precondition: this venv really lacks every dev-only tool (so a pass proves independence from them)
    dev = ["pytest", "pyright", "hypothesis", "alembic", "build", "yaml", "coverage", "setuptools_scm"]
    absent = installed.run(["-I", "-c", "import importlib.util as u; print([m for m in %r if u.find_spec(m)])" % dev], cwd=outside)
    assert absent.stdout.strip() == "[]", absent.stdout
    # every module of the artifact imports (no hidden dev-only import), including the Core-only CLI and the extensions
    walk = textwrap.dedent("""
        import importlib, pkgutil, peerhub
        bad = []
        for m in pkgutil.walk_packages(peerhub.__path__, "peerhub."):
            if m.name.endswith("__main__"):
                continue
            try:
                importlib.import_module(m.name)
            except Exception as e:
                bad.append(m.name + ": " + type(e).__name__ + ": " + str(e)[:100])
        print(bad)
    """)
    res = installed.run(["-I", "-c", walk], cwd=outside)
    assert res.returncode == 0 and res.stdout.strip() == "[]", res.stdout + res.stderr
    # initialize a workspace and validate the packaged schemas against what the runtime actually emits
    db = tmp_path / "ws" / "core.db"
    db.parent.mkdir()
    assert _cli(installed, outside, db, "peer", "register", "--id", "a").returncode == 0
    assert _cli(installed, outside, db, "stream", "create", "--id", "s", "--members", "a").returncode == 0
    rec = _cli(installed, outside, db, "record", "append", "--stream", "s", "--author", "a", "--kind", "message", "--body", '"x"',
               "--idemp-key", "k", "--created-at", "2026-10-04T00:00:00Z")
    assert rec.returncode == 0, rec.stderr
    emitted = json.loads(rec.stdout)
    req = installed.run(["-I", "-c", "import importlib.resources as r, json; print(json.dumps(json.loads(r.files('peerhub.m1')"
                         ".joinpath('schemas/record.schema.json').read_text(encoding='utf-8'))['required']))"], cwd=outside)
    required = json.loads(req.stdout)
    assert required and set(required) <= set(emitted), sorted(set(required) - set(emitted))
    assert emitted["stream_id"] == "s" and emitted["position"] == 1

