"""M2/M3 exit gates reuse candidate-bound release evidence and fail closed."""
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from tests.communication.harness.pkg_env import REPO
from tests.communication.package.test_cut_candidate_evidence import Env
from tests.communication.package.test_rel_007_evidence_bundle import M23_CATALOG, all_ids

pytestmark = [pytest.mark.package, pytest.mark.evidence]
M23_IDS = {t["id"] for t in M23_CATALOG}


@pytest.fixture
def evidence(tmp_path):
    env = Env(tmp_path)
    m1 = env.evidence({i: "passed" for i in all_ids(live=True) if i not in M23_IDS})
    m23 = env.evidence({i: "passed" for i in M23_IDS}, gate="G2", dist=None)
    return env, m1, m23


def test_m23_all_catalog_tests_pass_in_candidate_evidence(evidence):
    env, m1, m23 = evidence
    manifest = env.manifest([m1, m23])
    assert manifest["release_ready"] and manifest["blockers"] == []
    assert manifest["unverified_blocking_tests"] == {}
    assert manifest["deterministic_suite"]["passed"] == len(all_ids(live=False))


@pytest.mark.parametrize("tid", ["EXT-001", "SRC-001"])
@pytest.mark.parametrize("bad", ["missing", "failed", "error", "skipped", "allowed_skip"])
def test_m23_every_nonpass_blocks_candidate(evidence, tid, bad):
    env, m1, m23 = evidence
    assert env.manifest([m1, m23])["release_ready"]
    tree = ET.parse(m23)
    suite = next(tree.getroot().iter("testsuite"))
    case = next(c for c in suite.findall("testcase") if c.get("name") == "test_" + tid.lower().replace("-", "_") + "_synthetic")
    if bad == "missing":
        suite.remove(case)
    else:
        tag = {"failed": "failure", "error": "error", "skipped": "skipped", "allowed_skip": "skipped"}[bad]
        ET.SubElement(case, tag, message="CI-ONLY[job=verify]: off" if bad == "allowed_skip" else "boom")
    tree.write(m23, encoding="utf-8", xml_declaration=True)
    manifest = env.manifest([m1, m23])
    expected = "skipped" if bad == "allowed_skip" else bad
    assert not manifest["release_ready"]
    assert manifest["unverified_blocking_tests"] == {tid: expected}
    assert f"blocking test {tid} (gate G2) is {expected}" in manifest["blockers"]
    assert any(b.startswith("HOLD: blocking gate G2 has no complete current PASS evidence") for b in manifest["hold_reasons"])


@pytest.mark.parametrize("bad", [None, "failure", "error", "skipped"])
def test_m23_property_only_ids_are_evidence_and_every_case_must_pass(evidence, bad):
    env, m1, m23 = evidence
    tree = ET.parse(m23)
    suite = next(tree.getroot().iter("testsuite"))
    for case in list(suite.findall("testcase")):
        if case.get("name") in {"test_ext_001_synthetic", "test_src_001_synthetic"}:
            suite.remove(case)
    case = ET.SubElement(suite, "testcase", name="test_marker_only_behavior[param]")
    props = ET.SubElement(case, "properties")
    for tid in ("EXT-001", "SRC-001"):
        ET.SubElement(props, "property", name="catalog_id", value=tid)
    tree.write(m23, encoding="utf-8", xml_declaration=True)
    assert env.manifest([m1, m23])["release_ready"]
    if bad:
        duplicate = ET.SubElement(suite, "testcase", name="test_marker_only_behavior[other]")
        duplicate.append(ET.fromstring(ET.tostring(props)))
        ET.SubElement(duplicate, bad, message="LIVE-OPT-IN[env=x]: off")
        tree.write(m23, encoding="utf-8", xml_declaration=True)
        manifest = env.manifest([m1, m23])
        assert not manifest["release_ready"]
        assert manifest["unverified_blocking_tests"] == {tid: {"failure": "failed", "error": "error", "skipped": "skipped"}[bad]
                                                         for tid in ("EXT-001", "SRC-001")}


@pytest.mark.parametrize("name,ids", [
    ("test_art_016_and_017_behavior[param]", {"ART-016", "ART-017"}),
    ("test_art_020_to_023_behavior", {"ART-020", "ART-021", "ART-022", "ART-023"}),
    ("test_art_016_017_behavior", {"ART-016", "ART-017"}),
])
def test_m23_function_name_groups_match_traceability(evidence, name, ids):
    assert ids <= M23_IDS
    env, m1, m23 = evidence
    tree = ET.parse(m23)
    suite = next(tree.getroot().iter("testsuite"))
    for case in list(suite.findall("testcase")):
        if case.get("name") in {"test_" + tid.lower().replace("-", "_") + "_synthetic" for tid in ids}:
            suite.remove(case)
    grouped = ET.SubElement(suite, "testcase", name=name)
    tree.write(m23, encoding="utf-8", xml_declaration=True)
    assert env.manifest([m1, m23])["release_ready"]
    ET.SubElement(grouped, "failure", message="boom")
    tree.write(m23, encoding="utf-8", xml_declaration=True)
    assert env.manifest([m1, m23])["unverified_blocking_tests"] == {tid: "failed" for tid in ids}


def test_catalog_markers_really_reach_junit_properties(tmp_path):
    shutil.copy2(REPO / "tests/conftest.py", tmp_path / "conftest.py")
    probe = tmp_path / "test_probe.py"
    probe.write_text("import pytest\n@pytest.mark.catalog_id('EXT-001')\n@pytest.mark.catalog_id('SRC-001')\ndef test_behavior():\n    pass\n", encoding="utf-8")
    junit = tmp_path / "probe.xml"
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", str(REPO / "pyproject.toml"),
                             "--confcutdir", str(tmp_path), str(probe), "--junitxml", str(junit)], cwd=REPO, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    case = next(ET.parse(junit).getroot().iter("testcase"))
    assert {p.get("value") for p in case.findall("properties/property") if p.get("name") == "catalog_id"} == {"EXT-001", "SRC-001"}
