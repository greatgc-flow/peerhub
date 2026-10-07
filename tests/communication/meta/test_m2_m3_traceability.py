"""The M2/M3 catalogs and the real tests must not drift apart (positive control + negative controls on a synthetic tree)."""
from __future__ import annotations

import json
from pathlib import Path

from tools import traceability_m2m3 as tr


def test_the_real_m2_m3_catalogs_are_fully_covered_and_cross_referenced():
    assert tr.check() == {}  # every catalog test has a real test function; no dangling requirement/test references


def _tree(tmp_path: Path, tests, reqs, test_source: str) -> Path:
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
                 [{"id": "REQ-A", "priority": "P0", "tests": ["AAA-001", "AAA-002"]}], "def test_aaa_001_something():\n    pass\n")
    assert tr.check(root) == {"uncovered_tests": ["AAA-002"]}


def test_range_and_pair_names_and_markers_count_as_coverage(tmp_path):
    src = ("def test_aaa_001_and_002_pair():\n    pass\n\ndef test_aaa_003_to_005_range():\n    pass\n\n"
           "@pytest.mark.catalog_id(\"AAA-006\")\ndef test_something_else():\n    pass\n")
    tests = [{"id": f"AAA-00{n}", "requirements": ["REQ-A"]} for n in range(1, 7)]
    root = _tree(tmp_path, tests, [{"id": "REQ-A", "priority": "P0", "tests": [t["id"] for t in tests]}], src)
    assert tr.check(root) == {}


def test_dangling_references_and_p0_without_tests_are_reported(tmp_path):
    root = _tree(tmp_path, [{"id": "AAA-001", "requirements": ["REQ-MISSING"]}],
                 [{"id": "REQ-A", "priority": "P0", "tests": []}, {"id": "REQ-B", "priority": "P1", "tests": ["AAA-404"]}],
                 "def test_aaa_001_x():\n    pass\n")
    assert tr.check(root) == {"unknown_tests_in_requirements": ["REQ-B:AAA-404"], "unknown_requirements_in_tests": ["AAA-001:REQ-MISSING"],
                              "p0_requirement_without_tests": ["REQ-A"]}
