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
    assert {"ubuntu-full", "windows-platform", "compatibility-smoke", "pytest-oracle-compat", "full-matrix", "ci-pass"} <= set(jobs)
    triggers = wf.get("on", wf.get(True, {}))
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    matrix = jobs["full-matrix"]["strategy"]["matrix"]
    assert matrix == {"os": ["ubuntu-latest", "windows-latest"],
                      "python-version": ["3.11", "3.12", "3.13", "3.14", "3.15"]}
    assert "schedule" in jobs["full-matrix"]["if"] and "inputs.full_matrix" in jobs["full-matrix"]["if"]
    smoke = jobs["compatibility-smoke"]["strategy"]["matrix"]["include"]
    assert len(smoke) == 6
    assert {(cell["os"], cell["python-version"]) for cell in smoke} == {
        (os_name, py) for os_name in ("ubuntu-latest", "windows-latest") for py in ("3.12", "3.13", "3.14")
    }
    oracle = jobs["pytest-oracle-compat"]
    assert "if" not in oracle
    assert oracle["strategy"]["matrix"] == {"pytest-version": ["8.0.0", "9.1.1"]}
    oracle_setup = next(step for step in oracle["steps"]
                        if step.get("uses", "").startswith("actions/setup-python@"))
    assert oracle_setup["with"]["python-version"] == "3.11"
    oracle_runs = "\n".join(step.get("run", "") for step in oracle["steps"])
    assert "pytest==" in oracle_runs and "test_rel005_oracle.py" in oracle_runs
    windows_run = "\n".join(step.get("run", "") for step in jobs["windows-platform"]["steps"])
    assert "-m platform_sensitive" in windows_run and "verify-junit" in windows_run
    windows_steps = jobs["windows-platform"]["steps"]
    for step in windows_steps:
        if step.get("uses", "").startswith("actions/setup-python@") or step.get("name") == "Install dependencies":
            assert step.get("if") == "steps.suite.outputs.windows_suite != 'skip'"
    ubuntu_steps = jobs["ubuntu-full"]["steps"]
    reverse_check = next(i for i, step in enumerate(ubuntu_steps)
                         if "verify-marker-inventory" in step.get("run", ""))
    full_suite = next(i for i, step in enumerate(ubuntu_steps)
                      if "pytest -q" in step.get("run", ""))
    assert reverse_check < full_suite
    assert "refs/heads/main" in jobs["windows-platform"]["if"]
    assert "always()" in jobs["ci-pass"]["if"]
    assert set(jobs["ci-pass"]["needs"]) == {
        "ubuntu-full", "windows-platform", "compatibility-smoke", "pytest-oracle-compat", "full-matrix"
    }
    aggregate_run = "\n".join(step.get("run", "") for step in jobs["ci-pass"]["steps"])
    aggregate_env = next(step["env"] for step in jobs["ci-pass"]["steps"] if step.get("name") == "Verify required CI lanes")
    assert "pytest-oracle-compat" in aggregate_run and "PYTEST_ORACLE_RESULT" in aggregate_env
    assert "github.run_id" in wf["concurrency"]["group"]
    assert wf["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    for job_name in ("ubuntu-full", "windows-platform", "full-matrix"):
        checkout = next(step for step in jobs[job_name]["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"].get("fetch-depth") == 0
        uploads = [step for step in jobs[job_name]["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")]
        assert len(uploads) == 1
        assert uploads[0]["if"] == "failure()"
        assert uploads[0]["with"]["retention-days"] == 7
        assert uploads[0]["with"]["overwrite"] is True
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8").lower()
    assert "-m live" not in text and "tools.live_gate" not in text


def test_windows_suite_skips_only_prose_and_runs_platform_inventory_for_code(monkeypatch, tmp_path):
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="README.md\0docs/design/monitor.md\0"))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "skip"
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="peerhub/extensions/adapters/base.py\0"))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "targeted"
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="tests/communication/fault/test_flt_storage.py\0"))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "targeted"
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="docs/m1_impl/catalog.json\0"))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "targeted"
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="peerhub/README.md\0"))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "targeted"
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="docs/m1_spec/contract.md\0"))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "targeted"
    # CI workflow/selector edits require actual full-Windows coverage on that PR,
    # not just the platform-sensitive subset.
    for changed in (".github/workflows/ci.yml", "tools/ci_platform.py"):
        monkeypatch.setattr(
            ci_platform.subprocess, "run",
            lambda *a, changed=changed, **k: SimpleNamespace(stdout=changed + "\0"),
        )
        assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "full"
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("git unavailable")))
    assert ci_platform.windows_suite("pull_request", "base", "head", repo=tmp_path) == "full"


def test_windows_suite_escalates_main_and_scheduled_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="README.md\0"))
    assert ci_platform.windows_suite("push", "base", "head", ref="refs/heads/main", repo=tmp_path) == "full"
    assert ci_platform.windows_suite("schedule", "base", "head", repo=tmp_path) == "full"
    assert ci_platform.windows_suite("pull_request", "", "head", repo=tmp_path) == "full"


def test_platform_marker_inventory_is_exact_and_fails_closed(monkeypatch, tmp_path):
    inventory = tmp_path / "inventory.txt"
    inventory.write_text("tests.communication.test_one\ntests.communication.test_two\n", encoding="utf-8")
    listing = "tests/communication/test_one.py::test_a\ntests/communication/test_two.py::test_b\n2 tests collected\n"
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=listing, returncode=0))
    assert ci_platform.verify_marker_inventory(inventory, repo=tmp_path) == 2
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=listing + "tests/communication/test_new.py::test_c\n", returncode=0))
    with pytest.raises(ValueError, match="inventory mismatch"):
        ci_platform.verify_marker_inventory(inventory, repo=tmp_path)
    monkeypatch.setattr(ci_platform.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="", returncode=2, stderr="collection failed"))
    with pytest.raises(ValueError, match="collection failed"):
        ci_platform.verify_marker_inventory(inventory, repo=tmp_path)


def test_aggregate_ci_result_requires_expected_lanes_and_accepts_only_intentional_doc_skip():
    quick = {"ubuntu-full": "success", "windows-platform": "success",
             "compatibility-smoke": "success", "pytest-oracle-compat": "success", "full-matrix": "skipped"}
    ci_platform.verify_ci_results("pull_request", False, "targeted", quick)
    ci_platform.verify_ci_results("pull_request", False, "skip", quick)
    with pytest.raises(ValueError, match="unexpected Windows skip"):
        ci_platform.verify_ci_results("push", False, "skip", quick)
    for status in ("failure", "cancelled", "skipped", None):
        with pytest.raises(ValueError):
            ci_platform.verify_ci_results("pull_request", False, "targeted",
                                          {**quick, "windows-platform": status})
        with pytest.raises(ValueError):
            ci_platform.verify_ci_results("pull_request", False, "targeted",
                                          {**quick, "pytest-oracle-compat": status})
    ci_platform.verify_ci_results("schedule", False, "skip",
                                  {**quick, "full-matrix": "success"})
    with pytest.raises(ValueError, match="full-matrix"):
        ci_platform.verify_ci_results("workflow_dispatch", True, "skip", quick)
    with pytest.raises(ValueError, match="pytest-oracle-compat"):
        ci_platform.verify_ci_results("schedule", False, "skip",
                                      {**quick, "full-matrix": "success", "pytest-oracle-compat": "failure"})


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
    rc = jobs["release-matrix"]["strategy"]["matrix"]
    cells = len(rc["os"]) * len(rc["python-version"])  # the gate must count exactly the declared cells (v0.22.1: 8 vs 10, #254)
    assert f"${{#rc_junits[@]}} -ne {cells} ]]" in release_text
    assert 'TAG_SHA="$(git rev-parse "refs/tags/$RELEASE_TAG^{commit}")"' in release_text
    for job_name in ("build", "release-matrix"):
        checkout = next(step for step in jobs[job_name]["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"].get("fetch-depth") == 0
