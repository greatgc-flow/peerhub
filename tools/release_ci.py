"""Select the exact candidate's successful CI matrix dispatch for publishing."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

CELLS = [(os_name, version) for os_name in ("ubuntu-latest", "windows-latest")
         for version in ("3.11", "3.12", "3.13", "3.14", "3.15")]
REQUIRED_JOBS = {"ci-pass", *(f"full-matrix ({os_name}, {version})" for os_name, version in CELLS)}
REPORT_NAMES = [f"g2-rc-matrix-{os_name}-py{version}" for os_name, version in CELLS]


def matrix_junit_paths(root: Path) -> list[Path]:
    return [root / f"junit-{name}" / f"{name}.xml" for name in REPORT_NAMES]


def github_rows(endpoint: str, key: str) -> list[dict[str, Any]]:
    result = subprocess.run(["gh", "api", "--paginate", "--slurp", endpoint],
                            check=True, stdout=subprocess.PIPE, encoding="utf-8")
    return [row for page in json.loads(result.stdout) for row in page[key]]


def find_candidate_run(repo: str, commit: str) -> int:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("The candidate commit must be a full Git SHA")
    actions = f"repos/{repo}/actions"
    runs = github_rows(f"{actions}/workflows/ci.yml/runs?event=workflow_dispatch&head_sha={commit}", "workflow_runs")
    for run in runs:
        if (run["head_sha"] != commit or run["event"] != "workflow_dispatch"
                or run["status"] != "completed" or run["conclusion"] != "success"
                or any((run.get(key) or {}).get("full_name") != repo for key in ("repository", "head_repository"))):
            continue
        run_id = run["id"]
        latest = {}
        # A failed-only rerun leaves successful jobs in earlier attempts.
        for attempt in range(run["run_attempt"], 0, -1):
            for job in github_rows(f"{actions}/runs/{run_id}/attempts/{attempt}/jobs", "jobs"):
                if job["name"] in REQUIRED_JOBS:
                    latest.setdefault(job["name"], job)
            if latest.keys() == REQUIRED_JOBS:
                break
        if latest.keys() == REQUIRED_JOBS and all(
            job["head_sha"] == commit and job["run_id"] == run_id
            and job["status"] == "completed" and job["conclusion"] == "success"
            for job in latest.values()
        ):
            return run_id
    raise ValueError(f"No successful ci.yml dispatch for {commit} with ci-pass and all ten full-matrix cells; dispatch with full_matrix=true on the candidate ref")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    try:
        run_id = find_candidate_run(args.repo, args.commit)
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"run-id={run_id}")
    print(f"run-url=https://github.com/{args.repo}/actions/runs/{run_id}")


if __name__ == "__main__":
    main()
