"""Offline fixtures for the release workflow's Actions API handoff."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from tools import release_ci

pytestmark = [pytest.mark.package, pytest.mark.ci]
REPO = "greatgc-flow/peerhub"
SHA = "a" * 40
ROOT = Path(__file__).resolve().parents[3]
RUNS = f"repos/{REPO}/actions/workflows/ci.yml/runs?head_sha={SHA}"
JOBS = f"repos/{REPO}/actions/runs/42/attempts"
CELLS = [(os_name, py) for os_name in ("ubuntu-latest", "windows-latest")
         for py in ("3.11", "3.12", "3.13", "3.14", "3.15")]


def run(**fields):
    return {"id": 42, "run_attempt": 1, "status": "completed", "conclusion": "success",
            "head_sha": SHA, "event": "workflow_dispatch", "head_repository": {"full_name": REPO},
            "repository": {"full_name": REPO}, **fields}


def job(name: str, **fields):
    return {"name": name, "run_id": 42, "head_sha": SHA, "status": "completed", "conclusion": "success", **fields}


@pytest.fixture
def actions_api(monkeypatch):
    results = {RUNS: [run()], f"{JOBS}/1/jobs": [job("ci-pass"), *[job(f"full-matrix ({os_name}, {py})") for os_name, py in CELLS]]}

    def rows(endpoint: str, key: str):
        assert key == ("workflow_runs" if endpoint == RUNS else "jobs")
        return results[endpoint]

    monkeypatch.setattr(release_ci, "github_rows", rows)
    return results


def test_successful_candidate_dispatch_and_exact_reports(actions_api):
    assert release_ci.find_candidate_run(REPO, SHA) == 42
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    matrix = ci["jobs"]["full-matrix"]["strategy"]["matrix"]
    cells = [(os_name, py) for os_name in matrix["os"] for py in matrix["python-version"]]
    assert cells == CELLS
    assert release_ci.matrix_junit_paths(Path("ci-junit")) == [
        Path("ci-junit") / f"junit-g2-rc-matrix-{os_name}-py{py}" / f"g2-rc-matrix-{os_name}-py{py}.xml"
        for os_name, py in cells
    ]


def test_successful_main_push_can_supply_candidate_reports(actions_api):
    actions_api[RUNS] = [run(event="push", head_branch="main")]
    assert release_ci.find_candidate_run(REPO, SHA) == 42


@pytest.mark.parametrize("fields", [
    {"head_sha": "b" * 40}, {"event": "push", "head_branch": "other"}, {"event": "schedule"}, {"status": "in_progress"},
    {"conclusion": "failure"}, {"head_repository": {"full_name": "fork/peerhub"}},
    {"repository": {"full_name": "fork/peerhub"}},
])
def test_candidate_run_identity_is_required(actions_api, fields):
    actions_api[RUNS] = [run(**fields)]
    with pytest.raises(ValueError, match="No successful ci.yml candidate run"):
        release_ci.find_candidate_run(REPO, SHA)


@pytest.mark.parametrize("status", ["failure", "skipped", "cancelled"])
@pytest.mark.parametrize("index", [0, -1], ids=["ci-pass", "matrix-cell"])
def test_candidate_jobs_must_pass(actions_api, status, index):
    actions_api[f"{JOBS}/1/jobs"][index]["conclusion"] = status
    with pytest.raises(ValueError, match="No successful ci.yml candidate run"):
        release_ci.find_candidate_run(REPO, SHA)


def test_ordinary_dispatch_with_green_ci_pass_has_no_candidate_matrix(actions_api):
    actions_api[f"{JOBS}/1/jobs"] = [job("ci-pass"), job("full-matrix", conclusion="skipped")]
    with pytest.raises(ValueError, match="No successful ci.yml candidate run"):
        release_ci.find_candidate_run(REPO, SHA)


def test_failed_only_rerun_uses_latest_result_per_job(actions_api):
    actions_api[RUNS] = [run(run_attempt=2)]
    first_attempt = actions_api[f"{JOBS}/1/jobs"]
    cell = first_attempt[-1]["name"]
    first_attempt[-1]["conclusion"] = "failure"
    first_attempt[0]["conclusion"] = "failure"
    actions_api[f"{JOBS}/2/jobs"] = [job(cell), job("ci-pass")]
    assert release_ci.find_candidate_run(REPO, SHA) == 42

    first_attempt[-1]["conclusion"] = "success"
    actions_api[f"{JOBS}/2/jobs"][0]["conclusion"] = "skipped"
    with pytest.raises(ValueError, match="No successful ci.yml candidate run"):
        release_ci.find_candidate_run(REPO, SHA)


def test_actions_api_paginates_and_propagates_failure(monkeypatch):
    def fetch(args, **kwargs):
        assert args == ["gh", "api", "--paginate", "--slurp", RUNS]
        assert kwargs["check"] is True
        return subprocess.CompletedProcess(args, 0, '[{"workflow_runs": [{"id": 1}]}, {"workflow_runs": [{"id": 2}]}]')

    monkeypatch.setattr(release_ci.subprocess, "run", fetch)
    assert release_ci.github_rows(RUNS, "workflow_runs") == [{"id": 1}, {"id": 2}]

    def failed_fetch(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "gh api")

    monkeypatch.setattr(release_ci.subprocess, "run", failed_fetch)
    with pytest.raises(subprocess.CalledProcessError):
        release_ci.find_candidate_run(REPO, SHA)
