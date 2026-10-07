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
    if missing_gate:
        print(f"FAIL: missing for waves <= {a.upto}: " + ", ".join(missing_gate))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
