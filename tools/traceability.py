"""Frozen contract traceability: catalog IDs vs tests. Usage: python tools/traceability.py [--upto N] [-v]"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs/m1_spec/06_GUIDES/TEST_SET/test-catalog.json"
WAVES = ROOT / "docs/m1_impl/waves.json"
TESTS = ROOT / "tests"
_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)", re.M)
_MARK = re.compile(r"""catalog_id\(\s*["']([A-Z0-9][A-Z0-9-]*)["']""")


def wave_of(test_id: str, waves: dict[str, list[str]]) -> int | None:
    best: tuple[int, int] | None = None  # (selector length, wave)
    for w, sels in waves.items():
        for s in sels:
            if test_id == s or test_id.startswith(s + "-"):
                if best is None or len(s) > best[0]:
                    best = (len(s), int(w))
    return None if best is None else best[1]


def found_ids(ids: list[str], tests_dir: Path = TESTS) -> set[str]:
    names: list[str] = []
    marked: set[str] = set()
    for p in tests_dir.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        names += _DEF.findall(text)
        marked.update(_MARK.findall(text))
    found = set(i for i in ids if i in marked)
    for i in ids:
        key = i.lower().replace("-", "_")
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(key) + r"(?![0-9])")
        if any(pat.search(n) for n in names):
            found.add(i)
    return found


# --- M2/M3: the catalogs in docs/m2 and docs/m3 against the real test functions. A catalog test id is covered when a pytest
# function is named for it (`test_ext_022_...`, `test_art_016_and_017_...`, `test_art_020_to_023_...`) or carries
# `@pytest.mark.catalog_id("EXT-022")`. Fail-closed: every catalog test is covered, every requirement/test link resolves, every
# P0 requirement has a test.
M23_CATALOGS = (("docs/m2/test-catalog.m2.json", "docs/m2/requirements.m2.json"), ("docs/m3/test-catalog.m3.json", "docs/m3/requirements.m3.json"))
_M23_NAMED = re.compile(r"def (test_[a-z0-9]{2,4}_\d{3}(?:_(?:and|to)_\d{3}|_\d{3})*)")
_M23_MARK = re.compile(r"""catalog_id\(\s*["']([A-Z0-9]{2,4}-\d{3})["']""")


def m23_covered_ids(tests_dir: Path = TESTS) -> dict[str, list[str]]:
    cov: dict[str, list[str]] = {}
    for path in tests_dir.rglob("test_*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(tests_dir.parent).as_posix()
        for m in _M23_NAMED.finditer(text):
            name = m.group(1)
            prefix = re.match(r"test_([a-z0-9]{2,4})_", name).group(1).upper()  # type: ignore[union-attr]
            nums = re.findall(r"_(\d{3})", name)
            if "_to_" in name and len(nums) >= 2:
                nums = [f"{n:03d}" for n in range(int(nums[0]), int(nums[1]) + 1)]
            for n in nums:
                cov.setdefault(f"{prefix}-{n}", []).append(rel)
        for tid in _M23_MARK.findall(text):
            cov.setdefault(tid, []).append(rel)
    return cov


def check_m2_m3(root: Path = ROOT) -> dict[str, list[str]]:
    cov = m23_covered_ids(root / "tests")
    problems: dict[str, list[str]] = {"uncovered_tests": [], "unknown_tests_in_requirements": [], "unknown_requirements_in_tests": [],
                                      "p0_requirement_without_tests": []}
    for cat_rel, req_rel in M23_CATALOGS:
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--upto", type=int, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    ids = [t["id"] for t in json.loads(CATALOG.read_text(encoding="utf-8"))["tests"]]
    waves = json.loads(WAVES.read_text(encoding="utf-8"))["waves"]
    present = found_ids(ids)
    per: dict[int, list[str]] = {}
    unmapped = []
    for i in ids:
        w = wave_of(i, waves)
        (unmapped.append(i) if w is None else per.setdefault(w, []).append(i))
    missing_gate: list[str] = []
    for w in sorted(per):
        miss = [i for i in per[w] if i not in present]
        print(f"wave {w}: present {len(per[w]) - len(miss)}/{len(per[w])} missing {len(miss)}")
        if a.verbose and miss:
            print("  missing: " + ", ".join(miss))
        if a.upto is not None and w <= a.upto:
            missing_gate += miss
    if unmapped:
        print("UNMAPPED ids: " + ", ".join(unmapped))
        return 2
    m23 = check_m2_m3()
    if m23:
        print("M2/M3 gaps: " + json.dumps(m23, indent=2))
        return 1
    if missing_gate:
        print(f"FAIL: missing for waves <= {a.upto}: " + ", ".join(missing_gate))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
