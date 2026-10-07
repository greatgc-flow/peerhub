"""M2/M3 traceability: the requirement and test catalogs (docs/m2, docs/m3) against the real test functions.

A catalog test id is covered when a pytest function is named for it (`test_ext_022_...`, `test_art_016_and_017_...`,
`test_art_020_to_023_...`) or carries `@pytest.mark.catalog_id("EXT-022")`. Checks, all fail-closed:
  * every catalog test is covered by a real test function,
  * every requirement lists tests that exist in its catalog, and every catalog test names requirements that exist,
  * every P0 requirement has at least one test.
Usage: python -m tools.traceability_m2m3 [-v]    (exit 1 on any gap)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGS = (("docs/m2/test-catalog.m2.json", "docs/m2/requirements.m2.json"), ("docs/m3/test-catalog.m3.json", "docs/m3/requirements.m3.json"))
_NAMED = re.compile(r"def (test_[a-z0-9]{2,4}_\d{3}(?:_(?:and|to)_\d{3}|_\d{3})*)")
_MARK = re.compile(r"""catalog_id\(\s*["']([A-Z0-9]{2,4}-\d{3})["']""")


def covered_ids(tests_dir: Path = ROOT / "tests") -> dict[str, list[str]]:
    cov: dict[str, list[str]] = {}
    for path in tests_dir.rglob("test_*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(tests_dir.parent).as_posix()
        for m in _NAMED.finditer(text):
            name = m.group(1)
            prefix = re.match(r"test_([a-z0-9]{2,4})_", name).group(1).upper()  # type: ignore[union-attr]
            nums = re.findall(r"_(\d{3})", name)
            if "_to_" in name and len(nums) >= 2:
                nums = [f"{n:03d}" for n in range(int(nums[0]), int(nums[1]) + 1)]
            for n in nums:
                cov.setdefault(f"{prefix}-{n}", []).append(rel)
        for tid in _MARK.findall(text):
            cov.setdefault(tid, []).append(rel)
    return cov


def check(root: Path = ROOT) -> dict[str, list[str]]:
    cov = covered_ids(root / "tests")
    problems: dict[str, list[str]] = {"uncovered_tests": [], "unknown_tests_in_requirements": [], "unknown_requirements_in_tests": [],
                                      "p0_requirement_without_tests": []}
    for cat_rel, req_rel in CATALOGS:
        tests = {t["id"]: t for t in json.loads((root / cat_rel).read_text(encoding="utf-8"))["tests"]}
        reqs = {r["id"]: r for r in json.loads((root / req_rel).read_text(encoding="utf-8"))["requirements"]}
        problems["uncovered_tests"] += sorted(i for i in tests if i not in cov)
        for rid, r in reqs.items():
            problems["unknown_tests_in_requirements"] += [f"{rid}:{t}" for t in r.get("tests", []) if t not in tests]
            if r.get("priority") == "P0" and not r.get("tests"):
                problems["p0_requirement_without_tests"].append(rid)
        for tid, t in tests.items():
            problems["unknown_requirements_in_tests"] += [f"{tid}:{q}" for q in t.get("requirements", []) if q not in reqs]
    return {k: v for k, v in problems.items() if v}


def main(argv: list[str] | None = None) -> int:
    problems = check()
    if "-v" in (argv or sys.argv[1:]) or problems:
        print(json.dumps(problems or {"status": "OK"}, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
