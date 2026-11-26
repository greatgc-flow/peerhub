"""Small helpers for CI suite selection, JUnit reports, and lane results."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

CI_WORKFLOW = ".github/workflows/ci.yml"
MAIN_REF = "refs/heads/main"


def git_output(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          encoding="utf-8", timeout=30).stdout


def version_changed(base: str, head: str, *, repo: Path) -> bool:
    from tools.release_evidence import VERSION_FILE, parse_source_version

    if not base or set(base) == {"0"}:
        return True
    current = parse_source_version(git_output(repo, "show", f"{head}:{VERSION_FILE}"))
    try:
        previous = parse_source_version(git_output(repo, "show", f"{base}:{VERSION_FILE}"))
    except (ValueError, subprocess.CalledProcessError):
        return True  # a new or rewritten branch can lack the previous version
    return previous != current


def windows_workflow_settings(source: str) -> tuple:
    import yaml

    try:
        workflow = yaml.load(source, Loader=yaml.BaseLoader)
    except yaml.YAMLError as exc:
        raise ValueError("invalid CI workflow YAML") from exc
    jobs = workflow["jobs"]
    return (workflow.get("env"), workflow.get("defaults"), workflow.get("permissions"), workflow["on"].get("pull_request"),
            jobs["windows-platform"], jobs.get("plan"))


def windows_suite(event: str, base: str, head: str, force_full: bool = False, *, ref: str = "", repo: Path | None = None) -> str:
    if force_full or event == "schedule" or (event == "push" and ref in {MAIN_REF, "main"}):
        return "full"
    if not base or set(base) == {"0"}:
        return "full"
    root = repo or Path.cwd()
    try:
        paths = set(git_output(root, "diff", "--no-renames", "--name-only", "-z", base, head).split("\0")) - {""}
        if paths & {"tools/ci_platform.py", "tests/conftest.py", "ci/platform_sensitive_tests.txt"}:
            return "full"
        if CI_WORKFLOW in paths and windows_workflow_settings(
            git_output(root, "show", f"{base}:{CI_WORKFLOW}")
        ) != windows_workflow_settings(git_output(root, "show", f"{head}:{CI_WORKFLOW}")):
            return "full"
        if "pyproject.toml" in paths:
            before, after = (tomllib.loads(git_output(root, "show", f"{rev}:pyproject.toml")) for rev in (base, head))
            if before.get("tool", {}).get("pytest") != after.get("tool", {}).get("pytest"):
                return "full"
    except (OSError, subprocess.SubprocessError, ImportError, ValueError, TypeError, KeyError, AttributeError):
        return "full"  # uncertainty escalates to the broader Windows suite
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


def plan(event: str, ref: str, base: str, head: str, dispatch_matrix: bool, *, repo: Path) -> dict[str, str]:
    candidate = ((event == "workflow_dispatch" and dispatch_matrix)
                 or (event == "push" and ref == MAIN_REF and version_changed(base, head, repo=repo)))
    full_matrix = event == "schedule" or candidate
    suite = "skip" if full_matrix else windows_suite(event, base, head, ref=ref, repo=repo)
    return {"candidate": str(candidate).lower(), "full_matrix": str(full_matrix).lower(), "windows_suite": suite}


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
    expected = {"pytest-oracle-compat"} | ({"full-matrix"} if event == "schedule" or full_matrix else {
        "ubuntu-full", "compatibility-smoke", "windows-platform"
    })
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


def merge_junit(parallel: ET.Element, serial: ET.Element) -> None:
    """Append pytest's serial suites, preserving their outcomes and counts."""
    suites = serial.findall("testsuite")
    if parallel.tag != "testsuites" or serial.tag != "testsuites" or not parallel.findall("testsuite"):
        raise ValueError("expected pytest JUnit <testsuites> reports")
    if not serial.findall("testsuite/testcase"):
        raise ValueError("serial JUnit contains no test cases")
    parallel_cases = {(case.get("classname"), case.get("name")) for case in parallel.iter("testcase")}
    if any((case.get("classname"), case.get("name")) in parallel_cases for case in serial.iter("testcase")):
        raise ValueError("serial JUnit contains test cases already reported by the parallel suite")
    parallel.extend(suites)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    detect = sub.add_parser("detect-windows-suite")
    detect.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    detect.add_argument("--base", default="")
    detect.add_argument("--head", default="HEAD")
    detect.add_argument("--force-full", action="store_true")
    detect.add_argument("--ref", default="")
    route = sub.add_parser("plan")
    route.add_argument("--event", required=True)
    route.add_argument("--ref", required=True)
    route.add_argument("--base", default="")
    route.add_argument("--head", required=True)
    route.add_argument("--dispatch-matrix", choices=("true", "false"), default="false")
    verify = sub.add_parser("verify-junit")
    verify.add_argument("junit", type=Path)
    verify.add_argument("--inventory", type=Path, default=Path("ci/platform_sensitive_tests.txt"))
    merge = sub.add_parser("merge-junit")
    merge.add_argument("parallel", type=Path)
    merge.add_argument("serial", type=Path)
    marker = sub.add_parser("verify-marker-inventory")
    marker.add_argument("--inventory", type=Path, default=Path("ci/platform_sensitive_tests.txt"))
    aggregate = sub.add_parser("verify-ci-results")
    aggregate.add_argument("--event", required=True)
    aggregate.add_argument("--run-full-matrix", choices=("true", "false"), default="false")
    aggregate.add_argument("--windows-suite", required=True)
    for lane in ("ubuntu-full", "compatibility-smoke", "windows-platform", "full-matrix", "pytest-oracle-compat"):
        aggregate.add_argument(f"--{lane}-result", default="")
    args = parser.parse_args(argv)
    if args.command == "detect-windows-suite":
        print(f"windows_suite={windows_suite(args.event, args.base, args.head, args.force_full, ref=args.ref)}")
        return 0
    try:
        if args.command == "plan":
            for name, value in plan(args.event, args.ref, args.base, args.head,
                                    args.dispatch_matrix == "true", repo=Path.cwd()).items():
                print(f"{name}={value}")
        elif args.command == "verify-junit":
            verify_junit(args.junit, args.inventory)
        elif args.command == "merge-junit":
            tree = ET.parse(args.parallel)
            merge_junit(tree.getroot(), ET.parse(args.serial).getroot())
            pending = args.parallel.with_suffix(".xml.tmp")
            tree.write(pending, encoding="utf-8", xml_declaration=True)
            pending.replace(args.parallel)
        elif args.command == "verify-marker-inventory":
            verify_marker_inventory(args.inventory)
        else:
            results = {lane: getattr(args, lane.replace("-", "_") + "_result") for lane in
                       ("ubuntu-full", "compatibility-smoke", "windows-platform", "full-matrix", "pytest-oracle-compat")}
            verify_ci_results(args.event, args.run_full_matrix == "true", args.windows_suite,
                              {name: value for name, value in results.items() if value})
    except (OSError, subprocess.SubprocessError, ET.ParseError, ValueError) as exc:
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
