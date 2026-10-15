"""Small CI helpers for selecting and verifying the Windows platform suite."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def windows_suite(event: str, base: str, head: str, force_full: bool = False, *, ref: str = "", repo: Path | None = None) -> str:
    if force_full or event == "schedule" or (event == "push" and ref in {"refs/heads/main", "main"}):
        return "full"
    if not base or set(base) == {"0"}:
        return "full"
    root = repo or Path.cwd()
    try:
        proc = subprocess.run(["git", "diff", "--name-only", "-z", base, head], cwd=root,
                              check=True, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "full"  # uncertainty escalates to the broader Windows suite
    paths = [line.replace("\\", "/") for line in proc.stdout.split("\0") if line]
    if not paths:
        return "targeted"
    def prose_doc(path: str) -> bool:
        lowered = path.lower()
        if lowered in {"readme.md", "contributing.md", "changelog.md"}:
            return True
        return lowered.startswith("docs/") and lowered.endswith(".md") and not any(
            part in {"m1_spec", "m1_impl", "schemas", "generated"}
            for part in lowered.split("/")
        )
    return "skip" if all(prose_doc(path) for path in paths) else "targeted"


def verify_marker_inventory(inventory: Path, *, repo: Path | None = None) -> int:
    root = repo or Path.cwd()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "platform_sensitive", "-o", "addopts="],
        cwd=root, check=False, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode:
        raise ValueError(f"pytest collection failed: {proc.stderr.strip() or proc.stdout.strip()}")
    expected = {line.strip() for line in inventory.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")}
    observed: set[str] = set()
    for line in proc.stdout.splitlines():
        node = line.strip().split("::", 1)[0].replace("\\", "/").removeprefix("./")
        if node.endswith(".py"):
            observed.add(node[:-3].replace("/", "."))
    if observed != expected:
        raise ValueError(f"platform-sensitive inventory mismatch: missing={sorted(observed - expected)}, stale={sorted(expected - observed)}")
    return len(observed)


def verify_ci_results(event: str, full_matrix: bool, windows_suite: str, results: dict[str, str]) -> None:
    expected = {"full-matrix"} if event == "schedule" or full_matrix else {
        "ubuntu-full", "compatibility-smoke", "windows-platform"
    }
    missing = sorted(expected - results.keys())
    bad = {name: results.get(name, "missing") for name in expected if results.get(name) != "success"}
    if windows_suite == "skip" and event != "pull_request" and not (event == "schedule" or full_matrix):
        bad["windows-platform-selection"] = "unexpected Windows skip outside pull requests"
    if windows_suite not in {"full", "targeted", "skip"}:
        bad["windows-platform-selection"] = f"invalid selection: {windows_suite}"
    if missing or bad:
        raise ValueError(f"CI results incomplete or unsuccessful: missing={missing}, results={bad}")


def verify_junit(path: Path, inventory: Path) -> None:
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    if not cases:
        raise ValueError("platform-sensitive JUnit contains no test cases")
    skipped = [case for case in cases if case.find("skipped") is not None]
    if skipped:
        raise ValueError(f"platform-sensitive JUnit contains {len(skipped)} skipped/xfail tests")
    expected = {line.strip() for line in inventory.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")}
    observed = {case.get("classname", "") for case in cases}
    missing = sorted(module for module in expected if not any(name.endswith(module) for name in observed))
    if missing:
        raise ValueError(f"platform-sensitive test inventory was not collected: {missing}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    detect = sub.add_parser("detect-windows-suite")
    detect.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    detect.add_argument("--base", default="")
    detect.add_argument("--head", default="HEAD")
    detect.add_argument("--force-full", action="store_true")
    detect.add_argument("--ref", default="")
    verify = sub.add_parser("verify-junit")
    verify.add_argument("junit", type=Path)
    verify.add_argument("--inventory", type=Path, default=Path("ci/platform_sensitive_tests.txt"))
    marker = sub.add_parser("verify-marker-inventory")
    marker.add_argument("--inventory", type=Path, default=Path("ci/platform_sensitive_tests.txt"))
    aggregate = sub.add_parser("verify-ci-results")
    aggregate.add_argument("--event", required=True)
    aggregate.add_argument("--run-full-matrix", choices=("true", "false"), default="false")
    aggregate.add_argument("--windows-suite", required=True)
    for lane in ("ubuntu-full", "compatibility-smoke", "windows-platform", "full-matrix"):
        aggregate.add_argument(f"--{lane}-result", default="")
    args = parser.parse_args(argv)
    if args.command == "detect-windows-suite":
        print(f"windows_suite={windows_suite(args.event, args.base, args.head, args.force_full, ref=args.ref)}")
        return 0
    try:
        if args.command == "verify-junit":
            verify_junit(args.junit, args.inventory)
        elif args.command == "verify-marker-inventory":
            verify_marker_inventory(args.inventory)
        else:
            results = {lane: getattr(args, lane.replace("-", "_") + "_result") for lane in
                       ("ubuntu-full", "compatibility-smoke", "windows-platform", "full-matrix")}
            verify_ci_results(args.event, args.run_full_matrix == "true", args.windows_suite,
                              {name: value for name, value in results.items() if value})
    except (OSError, ET.ParseError, ValueError) as exc:
        print(f"platform-sensitive JUnit verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
