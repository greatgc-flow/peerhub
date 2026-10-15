"""CI fast-path/full-matrix contracts and Windows inventory guard."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tools import ci_platform

pytestmark = [pytest.mark.package, pytest.mark.ci]
ROOT = Path(__file__).resolve().parents[3]


def test_ci_has_fast_gates_smoke_and_explicit_weekly_candidate_matrix():
    wf = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = wf["jobs"]
    assert {"ubuntu-full", "windows-platform", "compatibility-smoke", "full-matrix"} <= set(jobs)
    triggers = wf.get("on", wf.get(True, {}))
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    matrix = jobs["full-matrix"]["strategy"]["matrix"]
    assert matrix == {"os": ["ubuntu-latest", "windows-latest"],
                      "python-version": ["3.11", "3.12", "3.13", "3.14"]}
    assert "schedule" in jobs["full-matrix"]["if"] and "inputs.full_matrix" in jobs["full-matrix"]["if"]
    smoke = jobs["compatibility-smoke"]["strategy"]["matrix"]["include"]
    assert len(smoke) == 6
    assert {(cell["os"], cell["python-version"]) for cell in smoke} == {
        (os_name, py) for os_name in ("ubuntu-latest", "windows-latest") for py in ("3.12", "3.13", "3.14")
    }
    windows_run = "\n".join(step.get("run", "") for step in jobs["windows-platform"]["steps"])
    assert "-m platform_sensitive" in windows_run and "verify-junit" in windows_run
    assert wf["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' && !(inputs.full_matrix || false) }}"
    for job_name in ("ubuntu-full", "windows-platform", "full-matrix"):
        checkout = next(step for step in jobs[job_name]["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"].get("fetch-depth") == 0


def test_windows_suite_escalates_relevant_changes_and_unknown_diff(monkeypatch, tmp_path):
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="peerhub/extensions/adapters/base.py\n"))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "full"
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="README.md\n"))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "targeted"
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="peerhub/extensions/process_tree.py\n"))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "full"
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="tests/communication/fault/test_flt_storage.py\n"))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "full"
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="docs/m1_impl/catalog.json\n"))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "full"
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="peerhub/README.md\n"))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "full"
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("git unavailable")))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "full"


def test_windows_inventory_guard_rejects_skips_and_missing_modules(tmp_path):
    manifest = tmp_path / "inventory.txt"
    manifest.write_text("tests.module_a\ntests.module_b\n", encoding="utf-8")
    junit = tmp_path / "tests.xml"
    junit.write_text(
        '<testsuite><testcase classname="tests.module_a" name="a"/><testcase classname="tests.module_b" name="b"/></testsuite>',
        encoding="utf-8")
    ci_platform.verify_junit(junit, manifest)
    junit.write_text('<testsuite><testcase classname="tests.module_a" name="a"><skipped/></testcase></testsuite>',
                    encoding="utf-8")
    with pytest.raises(ValueError, match="skipped"):
        ci_platform.verify_junit(junit, manifest)


def test_release_workflow_runs_full_matrix_before_pyPI_publish():
    release_text = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(release_text)
    jobs = wf["jobs"]
    assert "release-matrix" in jobs
    assert "release-matrix" in jobs["release-evidence"]["needs"]
    assert "release-matrix" in jobs["publish"]["needs"]
    for job_name in ("verify", "build", "matrix", "release-matrix", "release-evidence"):
        checkout_refs = [step.get("with", {}).get("ref") for step in jobs[job_name]["steps"]
                         if step.get("uses", "").startswith("actions/checkout@")]
        if checkout_refs:
            assert all(ref == "${{ github.sha }}" for ref in checkout_refs)
    assert "${#rc_junits[@]} -ne 8" in release_text
    assert 'TAG_SHA="$(git rev-parse "refs/tags/$RELEASE_TAG^{commit}")"' in release_text
    for job_name in ("build", "release-matrix"):
        checkout = next(step for step in jobs[job_name]["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"].get("fetch-depth") == 0
