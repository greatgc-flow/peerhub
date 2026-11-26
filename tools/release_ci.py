"""Select the exact candidate's successful CI matrix for publishing."""
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
REPORT_PATTERN = "junit-g2-rc-matrix-*"
WORKFLOW = "ci.yml"
BRANCH = "main"
DISPATCH = "workflow_dispatch"


def matrix_junit_paths(root: Path) -> list[Path]:
    return [root / f"junit-{name}" / f"{name}.xml" for name in REPORT_NAMES]


def github_rows(endpoint: str, key: str) -> list[dict[str, Any]]:
    result = subprocess.run(["gh", "api", "--paginate", "--slurp", endpoint],
                            check=True, stdout=subprocess.PIPE, encoding="utf-8")
    return [row for page in json.loads(result.stdout) for row in page[key]]


def candidate_runs_endpoint(commit: str) -> str:
    return f"actions/workflows/{WORKFLOW}/runs?head_sha={commit}"


def is_candidate_run(run: dict[str, Any], repo: str, commit: str) -> bool:
    return (run.get("head_sha") == commit
            and (run.get("event") == DISPATCH or (run.get("event") == "push" and run.get("head_branch") == BRANCH))
            and all((run.get(key) or {}).get("full_name", "").casefold() == repo.casefold()
                    for key in ("repository", "head_repository")))


def find_candidate_run(repo: str, commit: str) -> int:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("The candidate commit must be a full Git SHA")
    actions = f"repos/{repo}/actions"
    runs = github_rows(f"repos/{repo}/{candidate_runs_endpoint(commit)}", "workflow_runs")
    for run in runs:
        if not is_candidate_run(run, repo, commit) or run["status"] != "completed" or run["conclusion"] != "success":
            continue
        run_id = run["id"]
        latest = {}
        # A failed-only rerun leaves successful jobs in earlier attempts.
        for attempt in range(run["run_attempt"], 0, -1):
            seen = set()
            for job in github_rows(f"{actions}/runs/{run_id}/attempts/{attempt}/jobs", "jobs"):
                name = job["name"]
                if name in REQUIRED_JOBS:
                    if name in seen:
                        raise ValueError(f"CI run {run_id} has duplicate job {name} in attempt {attempt}")
                    seen.add(name)
                    latest.setdefault(name, job)
            if latest.keys() == REQUIRED_JOBS:
                break
        if latest.keys() == REQUIRED_JOBS and all(
            job["head_sha"] == commit and job["run_id"] == run_id
            and job["status"] == "completed" and job["conclusion"] == "success"
            for job in latest.values()
        ):
            return run_id
    raise ValueError(f"No successful {WORKFLOW} candidate run for {commit} with ci-pass and all ten full-matrix cells; "
                     "dispatch with full_matrix=true on the candidate ref")


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
