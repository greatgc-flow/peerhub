"""Wave 7: REL-010 (declared Python matrix) and REL-011 (declared OS matrix + Windows path/newline smoke).

Truthfulness rule (TD-18): everything the package metadata declares must be exercised by a CI job in the workflow matrix.
Locally only the cell this machine can run is EXECUTED; every other declared cell is skipped with a machine-readable reason
`CI-ONLY[python=<v>;os=<name>]: ...` and the marker `ci_only(python=, os=)` (so a CI report can assert they ran there)."""
import json
import platform
import re
import sys

import pytest
import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from tests.communication.harness import pkg_env
from tests.communication.harness.pkg_env import REPO

pytestmark = [pytest.mark.package, pytest.mark.release, pytest.mark.matrix, pytest.mark.timeout(900)]

OS_CLASSIFIERS = {"ubuntu-latest": "Operating System :: POSIX :: Linux", "windows-latest": "Operating System :: Microsoft :: Windows",
                  "macos-latest": "Operating System :: MacOS"}


def _ci():
    wf = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = {k: v for k, v in wf["jobs"].items() if "matrix" in (v.get("strategy") or {})}
    return wf, jobs


def _ci_matrix():
    _, jobs = _ci()
    if not jobs:
        return set()
    cells = set()
    for job in jobs.values():
        m = job["strategy"]["matrix"]
        assert not m.get("exclude") and not m.get("include"), "keep the declared matrix a plain cross product"
        for py in m["python-version"]:
            for os_ in m["os"]:
                cells.add((str(py), os_))
    return cells


def _declared_pythons():
    proj = pkg_env.pyproject()["project"]
    classified = {c.rsplit("::", 1)[1].strip() for c in proj["classifiers"]
                  if c.startswith("Programming Language :: Python :: 3.")}
    spec = SpecifierSet(proj["requires-python"])
    admitted = {f"3.{m}" for m in range(0, 40) if spec.contains(Version(f"3.{m}"))}
    return classified, admitted, proj["requires-python"]


def _declared_oses():
    classifiers = set(pkg_env.pyproject()["project"]["classifiers"])
    return {os_ for os_, c in OS_CLASSIFIERS.items() if c in classifiers}


EVIDENCE = REPO / "docs/m1_impl/matrix_evidence/windows-py3.11-3.13.json"
EVIDENCE_REL = "docs/m1_impl/matrix_evidence/windows-py3.11-3.13.json"


def _verified_local():
    """Cells with recorded local execution evidence (VERIFIED-LOCAL). A cell counts only if its evidence says every directory ran with 0 failures."""
    if not EVIDENCE.exists():
        return set()
    ev = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    return {(c["python"], c["os"]) for c in ev["cells"] if c["failed"] == 0 and c["dirs"]}


CI_EVIDENCE_DIR = REPO / "docs/m1_impl/matrix_evidence"


def _ci_evidence_files():
    return sorted(CI_EVIDENCE_DIR.glob("ci-run-*.json"))


def _verified_ci():
    """{(python, os): run_url} for cells whose CI conclusion is recorded as success in a ci-run-<id>.json evidence file."""
    out = {}
    for f in _ci_evidence_files():
        ev = json.loads(f.read_text(encoding="utf-8"))
        for c in ev["cells"]:
            if c["conclusion"] == "success":
                out[(c["python"], c["os"])] = ev["run_url"]
    return out


def _cell_params():
    local_py = f"{sys.version_info.major}.{sys.version_info.minor}"
    local_os = pkg_env.local_os_name()
    params = []
    if not _ci_matrix():  # no declared matrix: a visible FAILING cell (never an empty parameter set, which pytest would skip)
        return [pytest.param("none", "none", id="no-ci-matrix")]
    for py, os_ in sorted(_ci_matrix()):
        runnable = py == local_py and os_ == local_os  # a cell is executed only if BOTH axes match this machine
        marks = [pytest.mark.ci_only(python=py, os=os_)] if not runnable else []
        if not runnable:
            marks.append(pytest.mark.skip(reason=f"CI-ONLY[python={py};os={os_}]: UNVERIFIED current candidate (TD-18); "
                                                 f"not runnable on local python {local_py} / {local_os}; "
                                                 "requires the exact testcase pass in candidate-bound matrix evidence"))
        params.append(pytest.param(py, os_, id=f"py{py}-{os_}", marks=marks))
    return params


def test_rel_010_foreign_cell_skip_never_claims_historical_verification():
    reasons = [mark.kwargs["reason"] for param in _cell_params()
               for mark in param.marks if mark.name == "skip"]
    assert reasons, "multi-cell matrix must contain foreign-cell skips on one interpreter"
    assert all("UNVERIFIED current candidate" in reason for reason in reasons)
    assert all("VERIFIED-CI" not in reason and "VERIFIED-LOCAL" not in reason for reason in reasons)


def _smoke(installed, workdir):
    """Minimal install/workspace/append/restart smoke through the installed wheel (separate processes = restart)."""
    db = workdir / "core.db"
    def cli(*a):
        return pkg_env.run([installed.script("peerhub"), "--db", str(db), *a], cwd=workdir)
    assert cli("peer", "register", "--peer", "a").returncode == 0
    assert cli("stream", "create", "--stream", "s", "--members", "a").returncode == 0
    r = cli("record", "append", "--stream", "s", "--author-peer", "a", "--kind", "message", "--body", '"hi"', "--idempotency-key", "k",
            "--created-at", "2026-10-04T00:00:00Z")
    assert r.returncode == 0, r.stderr
    got = cli("record", "read", "--stream", "s")
    return json.loads(got.stdout)


# ------------------------------------------------------------------------------------------- REL-010
def test_rel_010_declared_python_support_equals_what_the_ci_matrix_exercises():
    classified, admitted, spec = _declared_pythons()
    ci_pythons = {py for py, _ in _ci_matrix()}
    assert ci_pythons, "ci.yml has no matrix job"
    assert classified, "no Python classifiers declared"
    # requires-python must not silently promise more than the classifiers/CI (open-ended '>=3.11' admits 3.99)
    assert admitted == classified, f"requires-python {spec!r} admits {sorted(admitted)} but classifiers say {sorted(classified)}"
    assert classified == ci_pythons, f"declared {sorted(classified)} vs CI matrix {sorted(ci_pythons)}"
    # positive control for the comparator: an open-ended specifier is NOT equal to the classifier set
    assert {f"3.{m}" for m in range(40) if SpecifierSet(">=3.11").contains(Version(f"3.{m}"))} != classified


def test_rel_010_local_interpreter_is_inside_the_declared_matrix(record_property):
    classified, _, _ = _declared_pythons()
    local = f"{sys.version_info.major}.{sys.version_info.minor}"
    record_property("matrix_local_python", local)
    record_property("matrix_local_os", pkg_env.local_os_name())
    record_property("matrix_local_impl", platform.python_implementation())
    assert local in classified, f"local python {local} is not a declared version: local results would not be matrix evidence"


@pytest.mark.parametrize(("py", "os_"), _cell_params())
def test_rel_010_python_matrix_cell(py, os_, installed, tmp_path, record_property):
    assert py != "none", "ci.yml declares no matrix job"
    record_property("matrix_cell_executed", f"python={py};os={os_}")
    out = installed.run(["-I", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], cwd=tmp_path)
    assert out.stdout.strip() == py  # the venv under test really is the declared interpreter
    assert [r["body"] for r in _smoke(installed, tmp_path)] == ["hi"]


def test_rel_010_verified_local_claims_are_backed_by_evidence():
    """A VERIFIED-LOCAL claim without evidence fails; evidence must cover every test directory with 0 failures and match a declared cell."""
    ci_cells = _ci_matrix()
    text = {"README.md": (REPO / "README.md").read_text(encoding="utf-8"), "ci.yml": (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")}
    claims = {name: t for name, t in text.items() if "VERIFIED-LOCAL" in t}
    if not EVIDENCE.exists():
        assert not claims, f"VERIFIED-LOCAL claimed in {sorted(claims)} but {EVIDENCE_REL} is missing"
        return
    ev = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    required = {"m1/architecture", "m1/meta", "m1/schema", "m1/property", "m1/migration", "m1/core", "m1/integration", "m1/observation", "m1/diag",
                "m1/bridge", "m1/control", "m1/security", "m1/concurrency", "m1/fault", "m1/e2e", "m1/adapters", "unit/m1", "m1/package", "m1/soak"}
    assert ev["cells"], "evidence file has no cells"
    for c in ev["cells"]:
        assert (c["python"], c["os"]) in ci_cells, f"evidence cell {c['python']}/{c['os']} is not a declared matrix cell"
        assert set(c["dirs"]) == required, f"evidence for python {c['python']} misses {sorted(required - set(c['dirs']))}"
        assert c["failed"] == 0 and all(not d.get("failed") and not d.get("error") and d.get("passed", 0) > 0 for d in c["dirs"].values())
        assert c["sqlite_version"] and c["python_full"].startswith(c["python"] + ".")
    assert claims, "evidence exists but no doc claims it"
    for name, t in claims.items():
        assert EVIDENCE_REL in t, f"{name} claims VERIFIED-LOCAL without citing {EVIDENCE_REL}"
    # negative control: a cell without evidence is not VERIFIED-LOCAL
    assert ("3.11", "ubuntu-latest") not in _verified_local()


def test_rel_010_verified_ci_claims_are_backed_by_run_evidence():
    """A VERIFIED-CI claim requires a ci-run-<id>.json evidence file: real run id/url, a head sha, and a green conclusion for the
    claimed cell; evidence may only name declared matrix cells. Without evidence no doc may claim VERIFIED-CI."""
    ci_cells = _ci_matrix()
    text = {"README.md": (REPO / "README.md").read_text(encoding="utf-8"), "ci.yml": (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")}
    claims = {name: t for name, t in text.items() if "VERIFIED-CI" in t}
    files = _ci_evidence_files()
    if not files:
        assert not claims, f"VERIFIED-CI claimed in {sorted(claims)} but no {CI_EVIDENCE_DIR.name}/ci-run-<id>.json evidence exists"
        return
    for f in files:
        ev = json.loads(f.read_text(encoding="utf-8"))
        run_id = f.stem.removeprefix("ci-run-")
        assert run_id.isdigit() and ev["run_id"] == run_id, f"{f.name}: run_id must equal the file name id"
        assert ev["run_url"] == f"https://github.com/greatgc-flow/peerhub/actions/runs/{run_id}", f"{f.name}: run_url must point at the run"
        assert re.fullmatch(r"[0-9a-f]{40}", ev["head_sha"]), f"{f.name}: head_sha must be a full commit sha"
        seen = set()
        for c in ev["cells"]:
            cell = (c["python"], c["os"])
            assert cell in ci_cells, f"{f.name}: evidence cell {cell} is not a declared matrix cell"
            assert cell not in seen, f"{f.name}: duplicate cell {cell}"
            seen.add(cell)
            assert c["conclusion"] in {"success", "failure", "cancelled", "skipped"}
            assert c["job_url"].startswith(ev["run_url"] + "/job/"), f"{f.name}: job_url of {cell} does not belong to the run"
    verified = _verified_ci()
    assert claims, "CI evidence exists but no doc claims it"
    for name, t in claims.items():
        assert any(f.name in t for f in files), f"{name} claims VERIFIED-CI without citing a ci-run-<id>.json evidence file"
    # a doc may claim the whole matrix only if every declared cell has green evidence
    if any("all 8 cells VERIFIED-CI" in t for t in claims.values()):
        assert set(verified) == ci_cells, f"claimed all cells VERIFIED-CI but evidence is green only for {sorted(verified)}"
    # negative control: a cell without evidence is not VERIFIED-CI
    assert ("3.99", "ubuntu-latest") not in verified


# ------------------------------------------------------------------------------------------- REL-011
def test_rel_011_declared_os_support_equals_ci_matrix_and_includes_windows():
    declared = _declared_oses()
    ci_oses = {os_ for _, os_ in _ci_matrix()}
    assert ci_oses, "ci.yml has no matrix job"
    assert "windows-latest" in declared, "Windows must be declared (OS classifier)"
    assert declared == ci_oses, f"declared {sorted(declared)} vs CI {sorted(ci_oses)}"
    assert all(OS_CLASSIFIERS[o] in pkg_env.pyproject()["project"]["classifiers"] for o in ci_oses)


BODIES = ["line1\r\nline2\nline3\rline4", "\n", "\r\n\r\n", "tab\tand trailing space ", "café 한글 ✓ \U0001f642", "a b c"]


@pytest.mark.parametrize(("py", "os_"), _cell_params())
def test_rel_011_os_matrix_cell_path_and_newline_smoke(py, os_, installed, tmp_path, record_property):
    assert py != "none", "ci.yml declares no matrix job"
    record_property("matrix_cell_executed", f"python={py};os={os_}")
    work = tmp_path / "dir with spaces 한글 üñî"  # spaces + non-ASCII, as on a real Windows profile path
    work.mkdir()
    db = work / "core é.db"

    def cli(*a):
        return pkg_env.run([installed.script("peerhub"), "--db", str(db), *a], cwd=work)  # NO PYTHONUTF8: default console encoding

    assert cli("peer", "register", "--peer", "a").returncode == 0
    assert cli("stream", "create", "--stream", "s", "--members", "a").returncode == 0
    for i, body in enumerate(BODIES):
        r = cli("record", "append", "--stream", "s", "--author-peer", "a", "--kind", "message", "--body", json.dumps(body, ensure_ascii=False),
                "--idempotency-key", f"k{i}", "--created-at", "2026-10-04T00:00:00Z")
        assert r.returncode == 0, (i, r.stderr)
        assert json.loads(r.stdout)["body"] == body  # lossless in the command's own output
    read = cli("record", "read", "--stream", "s")  # after restart (new process)
    assert read.returncode == 0, read.stderr
    assert [r["body"] for r in json.loads(read.stdout)] == BODIES  # byte-exact: CR/LF/CRLF, tabs, non-BMP, LS/PS
    assert db.exists() and not (work / "core.db").exists()
