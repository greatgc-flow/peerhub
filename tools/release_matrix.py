"""Single support-matrix authority for GitHub release jobs and evidence admission.

Usage:
    python -m tools.release_matrix emit
    python -m tools.release_matrix validate --junit-dir junit

This checks *identities and presence* of all cells, not test success: the
existing release_evidence evaluator verifies stamped JUnit, commit and gates.
No PyYAML, GitHub API, network or provider CLI is needed.
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
from pathlib import Path

DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "ci" / "release-matrix.json"


def read_matrix(path: Path = DEFAULT_MANIFEST) -> dict[str, list[str]]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or set(manifest) != {"os", "python-version"}:
        raise ValueError("matrix needs exactly os and python-version arrays")
    for key, pattern in (
        ("os", r"[a-z][a-z0-9-]*"),
        ("python-version", r"3\.[0-9]{1,2}"),
    ):
        items = manifest[key]
        if (not isinstance(items, list) or not 1 <= len(items) <= 20
                or any(not isinstance(v, str) or not re.fullmatch(pattern, v) for v in items)
                or len(set(items)) != len(items)):
            raise ValueError(f"invalid or duplicate {key} matrix items")
    return manifest


def expected_names(matrix: dict[str, list[str]], prefix: str) -> set[str]:
    return {
        f"{prefix}-{os}-py{py}.xml"
        for os, py in itertools.product(matrix["os"], matrix["python-version"])
    }


def verify_evidence_names(directory: Path, matrix: dict[str, list[str]]) -> None:
    for prefix in ("g4-matrix", "g2-rc-matrix"):
        expected = expected_names(matrix, prefix)
        found = {p.name for p in Path(directory).glob(f"{prefix}-*.xml") if p.is_file()}
        missing = sorted(expected - found)
        unexpected = sorted(found - expected)
        if missing or unexpected:
            raise ValueError(
                f"{prefix}: missing={missing}, unexpected={unexpected} "
                f"(expected {len(expected)} exact cells)"
            )
        if any((Path(directory) / name).stat().st_size == 0 for name in expected):
            raise ValueError(f"{prefix}: empty JUnit file")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["emit", "validate"])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--junit-dir", type=Path, default=Path("junit"))
    args = parser.parse_args()
    try:
        matrix = read_matrix(args.manifest)
        if args.operation == "emit":
            print(json.dumps(matrix, separators=(",", ":"), sort_keys=True))
        else:
            verify_evidence_names(args.junit_dir, matrix)
            print("Release matrix cell identity: complete")
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"release matrix validation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
