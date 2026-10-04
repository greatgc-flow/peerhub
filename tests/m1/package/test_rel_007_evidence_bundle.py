"""Wave 7: REL-007 - release evidence bundle has commit/version, suite result, live canaries, package hashes (checksums) and a
requirement coverage summary; a P0 requirement without passing evidence blocks readiness (fail closed)."""
import hashlib
import json
import shutil
import subprocess
from xml.sax.saxutils import quoteattr

import pytest

from tests.m1.harness import pkg_env
from tests.m1.harness.pkg_env import REPO
from tools import m1_release_evidence as ev

pytestmark = [pytest.mark.package, pytest.mark.release, pytest.mark.evidence, pytest.mark.timeout(900)]

TS = REPO / "docs/m1_spec/06_GUIDES/TEST_SET"
CATALOG = json.loads((TS / "test-catalog.json").read_text(encoding="utf-8"))["tests"]
REQS = {r["id"]: r for r in json.loads((TS / "requirements.json").read_text(encoding="utf-8"))["requirements"]}
LIVE_IDS = ["LIVE-CC-001", "LIVE-CX-001", "LIVE-AG-001", "LIVE-004", "LIVE-005", "LIVE-006"]  # literal
SOAK_IDS = ["SOAK-001", "SOAK-002"]
# literal oracle (derived by hand from requirements.json): requirements that link a live-provider test
REQS_WITH_LIVE = {"REQ-BRIDGE-001", "REQ-BRIDGE-003", "REQ-OBS-001", "REQ-REL-002", "REQ-REL-003"}
P0_WITH_LIVE = ["REQ-BRIDGE-001", "REQ-OBS-001", "REQ-REL-002", "REQ-REL-003"]


def junit(path, outcomes):
    """outcomes: {catalog_id: 'passed'|'failed'|'skipped'|'error'}; testcase names follow the repo convention test_<id>_...."""
    cases = []
    for tid, out in outcomes.items():
        name = "test_" + tid.lower().replace("-", "_") + "_synthetic"
        body = {"passed": "", "failed": "<failure message='boom'/>", "skipped": "<skipped message='no'/>", "error": "<error message='e'/>"}[out]
        cases.append(f"<testcase classname='tests.x' name={quoteattr(name)}>{body}</testcase>")
    path.write_text("<?xml version='1.0'?><testsuites><testsuite name='pytest'>" + "".join(cases) + "</testsuite></testsuites>",
                    encoding="utf-8")
    return path


def all_ids(*, live, soak=False):
    ids = [t["id"] for t in CATALOG]
    return [i for i in ids if (live or i not in LIVE_IDS) and (soak or i not in SOAK_IDS)]


@pytest.fixture
def dist_dir(dist):
    return dist.wheel.parent


def manifest(tmp_path, dist_dir, outcomes, **kw):
    j = junit(tmp_path / "junit.xml", outcomes)
    return ev.build_manifest(repo_root=REPO, junit_paths=[j], dist_dir=dist_dir, commit=kw.get("commit"))


def test_rel_007_manifest_has_commit_version_suite_live_hashes_and_requirement_coverage(tmp_path, dist_dir, dist):
    m = manifest(tmp_path, dist_dir, {i: "passed" for i in all_ids(live=True)}, commit="0123456789abcdef0123456789abcdef01234567")
    assert m["commit"] == "0123456789abcdef0123456789abcdef01234567"
    assert m["version"] == pkg_env.source_version()
    assert m["deterministic_suite"]["passed"] == len(all_ids(live=False)) and m["deterministic_suite"]["failed"] == 0
    assert m["live_canaries"] == {i: "passed" for i in LIVE_IDS}
    want = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (dist.wheel, dist.sdist)}  # independent hashlib oracle
    assert {p["file"]: p["sha256"] for p in m["packages"]} == want
    assert {p["file"]: p["bytes"] for p in m["packages"]} == {p.name: p.stat().st_size for p in (dist.wheel, dist.sdist)}
    cov = m["requirement_coverage"]
    assert cov["total"] == 88 and cov["uncovered_p0"] == [] and cov["uncovered"] == [] and cov["covered"] == 88
    assert m["blockers"] == [] and m["release_ready"] is True


def test_rel_007_missing_live_canaries_and_uncovered_p0_requirements_block_release(tmp_path, dist_dir):
    m = manifest(tmp_path, dist_dir, {i: "passed" for i in all_ids(live=False)})  # deterministic suite only
    assert m["live_canaries"] == {i: "missing" for i in LIVE_IDS}
    assert m["requirement_coverage"]["uncovered_p0"] == P0_WITH_LIVE
    assert set(m["requirement_coverage"]["uncovered"]) == REQS_WITH_LIVE
    assert m["release_ready"] is False
    assert any("live canary" in b and "LIVE-CC-001" in b for b in m["blockers"]), m["blockers"]
    assert any("uncovered P0 requirement" in b for b in m["blockers"])


@pytest.mark.parametrize("bad_status", ["failed", "skipped", "error"])
def test_rel_007_a_non_passing_test_uncovers_exactly_its_requirements(tmp_path, dist_dir, bad_status):
    outcomes = {i: "passed" for i in all_ids(live=True)}
    outcomes["REL-012"] = bad_status  # REL-012 is the only test of REQ-LEGACY-001 (requirements.json)
    m = manifest(tmp_path, dist_dir, outcomes)
    assert m["requirement_coverage"]["uncovered_p0"] == ["REQ-LEGACY-001"] and m["release_ready"] is False
    outcomes["REL-012"] = "passed"
    outcomes["REL-013"] = bad_status  # REQ-REL-007 needs BOTH REL-013 and REL-014
    assert manifest(tmp_path, dist_dir, outcomes)["requirement_coverage"]["uncovered_p0"] == ["REQ-REL-007"]
    outcomes.pop("REL-014")  # absent evidence is not a pass either
    outcomes["REL-013"] = "passed"
    assert manifest(tmp_path, dist_dir, outcomes)["requirement_coverage"]["uncovered_p0"] == ["REQ-REL-007"]


def test_rel_007_soak_is_non_blocking_and_a_failed_live_canary_blocks(tmp_path, dist_dir):
    outcomes = {i: "passed" for i in all_ids(live=True)}
    outcomes.update({"SOAK-001": "failed", "SOAK-002": "failed"})
    assert manifest(tmp_path, dist_dir, outcomes)["release_ready"] is True  # G6 never blocks publish
    outcomes["LIVE-CX-001"] = "failed"
    m = manifest(tmp_path, dist_dir, outcomes)
    assert m["live_canaries"]["LIVE-CX-001"] == "failed" and m["release_ready"] is False
    assert any("LIVE-CX-001" in b for b in m["blockers"])


def test_rel_007_no_packages_or_version_mismatch_blocks_release(tmp_path, dist):
    j = junit(tmp_path / "j.xml", {i: "passed" for i in all_ids(live=True)})
    empty = tmp_path / "empty"
    empty.mkdir()
    m = ev.build_manifest(repo_root=REPO, junit_paths=[j], dist_dir=empty, commit="a" * 40)
    assert m["packages"] == [] and m["release_ready"] is False and any("package" in b for b in m["blockers"])
    stale = tmp_path / "stale"
    stale.mkdir()
    shutil.copy(dist.wheel, stale / "peerhub-0.0.1-py3-none-any.whl")  # artifact whose version is not the source version
    m2 = ev.build_manifest(repo_root=REPO, junit_paths=[j], dist_dir=stale, commit="a" * 40)
    assert m2["release_ready"] is False and any("version" in b for b in m2["blockers"])


def test_rel_007_bundle_checksums_detect_tampering_and_are_never_rewritten(tmp_path, dist_dir):
    j = junit(tmp_path / "junit.xml", {i: "passed" for i in all_ids(live=True)})
    out = tmp_path / "bundle"
    m = ev.build_manifest(repo_root=REPO, junit_paths=[j], dist_dir=dist_dir, commit="b" * 40)
    ev.write_bundle(out, m, [j], dist_dir)
    assert ev.verify_bundle(out) == []  # positive control
    sums = (out / "SHA256SUMS").read_bytes()
    names = {ln.split("  ", 1)[1] for ln in sums.decode().splitlines()}
    assert {"evidence.json", "junit/junit.xml"} <= names and any(n.startswith("packages/") and n.endswith(".whl") for n in names)
    wheel = next(p for p in (out / "packages").iterdir() if p.suffix == ".whl")
    raw = bytearray(wheel.read_bytes())
    raw[len(raw) // 2] ^= 0x01
    wheel.write_bytes(bytes(raw))
    errs = ev.verify_bundle(out)
    assert errs and any(f"packages/{wheel.name}" in e for e in errs)
    assert (out / "SHA256SUMS").read_bytes() == sums  # baseline not rewritten
    # editing the manifest or adding an uncovered file is also caught
    wheel.write_bytes(bytes(raw[:len(raw) // 2]) + bytes([raw[len(raw) // 2] ^ 0x01]) + bytes(raw[len(raw) // 2 + 1:]))
    assert ev.verify_bundle(out) == []
    (out / "evidence.json").write_text((out / "evidence.json").read_text(encoding="utf-8").replace('"release_ready": true', '"release_ready": false'),
                                       encoding="utf-8")
    assert any("evidence.json" in e for e in ev.verify_bundle(out))
    (out / "extra.txt").write_text("x", encoding="utf-8")
    assert any("extra.txt" in e for e in ev.verify_bundle(out))


def test_rel_007_cli_writes_the_bundle_and_exits_nonzero_when_not_release_ready(tmp_path, dist_dir):
    j = junit(tmp_path / "junit.xml", {i: "passed" for i in all_ids(live=False)})
    out = tmp_path / "b"
    cp = subprocess.run([pkg_env.sys.executable, "-m", "tools.m1_release_evidence", "--junit", str(j), "--dist", str(dist_dir), "--out", str(out),
                         "--commit", "c" * 40], cwd=REPO, capture_output=True, text=True)
    assert cp.returncode == 1 and (out / "evidence.json").is_file() and ev.verify_bundle(out) == []  # evidence is written even when blocked
    assert json.loads((out / "evidence.json").read_text(encoding="utf-8"))["release_ready"] is False


# ---------------------------------------------------------------- W9: readiness is fail-closed for every blocking-gate test
def _with_message(path, tid, tag, message=None):
    """Rewrite one synthetic testcase to <skipped message=...> / failure / error (independent XML oracle)."""
    text = path.read_text(encoding="utf-8")
    name = "test_" + tid.lower().replace("-", "_") + "_synthetic"
    attr = f" message={quoteattr(message)}" if message is not None else ""
    old = f"<testcase classname='tests.x' name={quoteattr(name)}></testcase>"
    assert old in text
    path.write_text(text.replace(old, f"<testcase classname='tests.x' name={quoteattr(name)}><{tag}{attr}/></testcase>"), encoding="utf-8")


@pytest.mark.parametrize("tid", ["IMP-003", "ARCH-001", "REL-012"])  # IMP-003 is the reported repro (its only requirement REQ-IMP-002)
@pytest.mark.parametrize("bad", ["failed", "error", "skipped", "missing"])
def test_rel_007_w9_any_blocking_test_not_passed_flips_readiness(tmp_path, dist_dir, tid, bad):
    base = {i: "passed" for i in all_ids(live=True)}
    assert manifest(tmp_path, dist_dir, base)["release_ready"] is True  # positive control
    if bad == "missing":
        base.pop(tid)
    else:
        base[tid] = bad
    m = manifest(tmp_path, dist_dir, base)
    assert m["release_ready"] is False
    assert any(f"blocking test {tid}" in b for b in m["blockers"]), m["blockers"]
    assert m["unverified_blocking_tests"][tid] == ("missing" if bad == "missing" else "failed" if bad == "failed" else bad)
    if tid == "IMP-003":
        assert "REQ-IMP-002" in m["requirement_coverage"]["uncovered"] and any("uncovered requirement" in b for b in m["blockers"])


@pytest.mark.parametrize("message,accepted", [("LIVE-OPT-IN[env=PEERHUB_M1_LIVE;required=1;marker=live]: off", True),
                                              ("LIVE-PROVIDER-UNAVAILABLE[provider=cc;reason=x]", True), ("CI-ONLY[job=x]", True),
                                              ("flaky, skipping", False), ("", False), ("LIVE-OPT-IN no brackets", False)])
def test_rel_007_w9_skip_is_accepted_only_with_machine_readable_reason_and_is_never_a_pass(tmp_path, dist_dir, message, accepted):
    base = {i: "passed" for i in all_ids(live=True)}
    j = junit(tmp_path / "junit.xml", base)
    _with_message(j, "IMP-003", "skipped", message)
    m = ev.build_manifest(repo_root=REPO, junit_paths=[j], dist_dir=dist_dir)
    assert m["unverified_blocking_tests"]["IMP-003"] == ("not_verified" if accepted else "skipped")
    assert m["deterministic_suite"]["not_verified"] == (1 if accepted else 0) and m["deterministic_suite"]["passed"] == len(all_ids(live=False)) - 1
    assert m["release_ready"] is False  # an accepted skip stays explicit and unverified, never ready
