"""Small CI helpers for selecting and verifying the Windows platform suite."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def windows_suite(event: str, base: str, head: str, force_full: bool = False, *, repo: Path | None = None) -> str:
    if force_full or event == "schedule":
        return "full"
    if not base or set(base) == {"0"}:
        return "full"
    root = repo or Path.cwd()
    try:
        proc = subprocess.run(["git", "diff", "--name-only", base, head], cwd=root,
                              check=True, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "full"  # uncertainty escalates to the broader Windows suite
    paths = [line.replace("\\", "/") for line in proc.stdout.splitlines() if line]
    # Keep the short Windows inventory only for documentation-only changes.
    # An allowlist of platform-sensitive source paths ages into false greens.
    docs_only = bool(paths) and all(
        path.lower().endswith(".md") and ("/" not in path or path.startswith("docs/")
                                         or path.startswith("docs\\"))
        for path in paths
    )
    return "targeted" if docs_only else "full"


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
    verify = sub.add_parser("verify-junit")
    verify.add_argument("junit", type=Path)
    verify.add_argument("--inventory", type=Path, default=Path("ci/platform_sensitive_tests.txt"))
    args = parser.parse_args(argv)
    if args.command == "detect-windows-suite":
        print(f"windows_suite={windows_suite(args.event, args.base, args.head, args.force_full)}")
        return 0
    try:
        verify_junit(args.junit, args.inventory)
    except (OSError, ET.ParseError, ValueError) as exc:
        print(f"platform-sensitive JUnit verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
