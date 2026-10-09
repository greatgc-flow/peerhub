"""The M2/M3 catalogs and the real tests must not drift apart (positive control + negative controls on a synthetic tree)."""
from __future__ import annotations

import json
from pathlib import Path

from tools import traceability as tr


def test_the_real_m2_m3_catalogs_are_fully_covered_and_cross_referenced():
    assert tr.check_m2_m3() == {}  # every catalog test has a real test function; no dangling requirement/test references


def _tree(tmp_path: Path, tests, reqs, test_source: str) -> Path:
    for test in tests:
        test.setdefault("dimensions", ["positive"])
    for req in reqs:
        req.setdefault("required_dimensions", ["positive"])
    for rel, key, payload in (("docs/m2/test-catalog.m2.json", "tests", tests), ("docs/m2/requirements.m2.json", "requirements", reqs),
                              ("docs/m3/test-catalog.m3.json", "tests", []), ("docs/m3/requirements.m3.json", "requirements", [])):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({key: payload}), encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(test_source, encoding="utf-8")
    return tmp_path


def test_a_catalog_test_without_a_real_test_function_is_reported(tmp_path):
    root = _tree(tmp_path, [{"id": "AAA-001", "requirements": ["REQ-A"]}, {"id": "AAA-002", "requirements": ["REQ-A"]}],
                 [{"id": "REQ-A", "priority": "P0"}], "def test_aaa_001_something():\n    pass\n")
    assert tr.check_m2_m3(root) == {"uncovered_tests": ["AAA-002"]}


def test_range_and_pair_names_and_markers_count_as_coverage(tmp_path):
    src = ("def test_aaa_001_and_002_pair():\n    pass\n\ndef test_aaa_003_to_005_range():\n    pass\n\n"
           "@pytest.mark.catalog_id(\"AAA-006\")\ndef test_something_else():\n    pass\n")
    tests = [{"id": f"AAA-00{n}", "requirements": ["REQ-A"]} for n in range(1, 7)]
    root = _tree(tmp_path, tests, [{"id": "REQ-A", "priority": "P0"}], src)
    assert tr.check_m2_m3(root) == {}


def test_dangling_references_and_p0_without_tests_are_reported(tmp_path):
    root = _tree(tmp_path, [{"id": "AAA-001", "requirements": ["REQ-MISSING"]}],
                 [{"id": "REQ-A", "priority": "P0", "required_dimensions": []}],
                 "def test_aaa_001_x():\n    pass\n")
    assert tr.check_m2_m3(root) == {"unknown_requirements_in_tests": ["AAA-001:REQ-MISSING"],
                              "p0_requirement_without_tests": ["REQ-A"]}


def test_requirement_tests_are_derived_and_duplicate_fields_are_rejected(tmp_path):
    tests = [{"id": "AAA-001", "requirements": ["REQ-A"]}]
    reqs = [{"id": "REQ-A", "priority": "P0"}]
    root = _tree(tmp_path, tests, reqs, "def test_aaa_001_x():\n    pass\n")
    assert tr.check_m2_m3(root) == {}
    assert [t["id"] for t in tr.requirement_tests(tests)["REQ-A"]] == ["AAA-001"]
    reqs[0]["tests"] = ["AAA-001"]
    (root / "docs/m2/requirements.m2.json").write_text(json.dumps({"requirements": reqs}), encoding="utf-8")
    assert tr.check_m2_m3(root) == {"duplicated_requirement_tests": ["REQ-A"]}


def test_required_dimensions_union_all_linked_tests_in_both_milestones(tmp_path):
    tests = [{"id": "AAA-001", "requirements": ["REQ-A"], "dimensions": ["positive"]},
             {"id": "AAA-002", "requirements": ["REQ-A"], "dimensions": ["recovery"]}]
    reqs = [{"id": "REQ-A", "priority": "P0", "required_dimensions": ["positive", "recovery", "fault"]}]
    root = _tree(tmp_path, tests, reqs, "def test_aaa_001_and_002_x():\n    pass\n")
    expected = {"required_dimension_uncovered": ["REQ-A:fault"]}
    assert tr.check_m2_m3(root) == expected
    # Move the same data to M3 to prove both catalog paths use the shared check.
    for name, key, payload in (("test-catalog", "tests", tests), ("requirements", "requirements", reqs)):
        (root / f"docs/m2/{name}.m2.json").write_text(json.dumps({key: []}), encoding="utf-8")
        (root / f"docs/m3/{name}.m3.json").write_text(json.dumps({key: payload}), encoding="utf-8")
    assert tr.check_m2_m3(root) == expected
    reqs[0]["required_dimensions"].remove("fault")
    (root / "docs/m3/requirements.m3.json").write_text(json.dumps({"requirements": reqs}), encoding="utf-8")
    assert tr.check_m2_m3(root) == {}
