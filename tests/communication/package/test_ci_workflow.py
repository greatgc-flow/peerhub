"""CI fast-path/full-matrix contracts and Windows inventory guard."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.communication.diag import test_diag_monitor_read_model, test_monitor_activity
from tests.conftest import pytest_collection_modifyitems
from tools import ci_platform

pytestmark = [pytest.mark.package, pytest.mark.ci]
ROOT = Path(__file__).resolve().parents[3]
SERIAL_TIMING_REPORT = "junit/monitor-timing.xml"
SERIAL_TIMING_RUN = (
    f"python -u -m pytest -q -m serial_timing --junitxml={SERIAL_TIMING_REPORT} "
    "-o junit_suite_name=monitor-timing "
    "tests/communication/diag/test_diag_monitor_read_model.py tests/communication/diag/test_monitor_activity.py"
)


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
    targeted = next(step for step in windows_steps if "-m platform_sensitive" in step.get("run", ""))
    assert "-n" not in targeted["run"].split()
    assert targeted.get("env", {}).get("PEERHUB_CI_SERIAL_TIMING") is None
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
        steps = jobs[job_name]["steps"]
        checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"].get("fetch-depth") == 0
        parallel = next(step for step in steps if "pytest -q -n auto" in step.get("run", ""))
        serial = next(step for step in steps if step.get("run") == SERIAL_TIMING_RUN)
        assert parallel["env"]["PEERHUB_CI_SERIAL_TIMING"] == "1"
        assert serial.get("if") == parallel.get("if")
        assert "PEERHUB_CI_SERIAL_TIMING" not in serial.get("env", {})
        assert not serial.get("continue-on-error")
        assert steps.index(serial) == steps.index(parallel) + 1
        uploads = [step for step in steps
                   if step.get("uses", "").startswith("actions/upload-artifact@") and step.get("if") == "failure()"]
        assert len(uploads) == 1
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


def test_release_workflow_requires_exact_candidate_ci_before_live_validation():
    release_text = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(release_text)
    jobs = wf["jobs"]
    assert "release-matrix" not in jobs
    assert "release-evidence" in jobs["publish"]["needs"]
    assert "matrix" in jobs["release-evidence"]["needs"]
    assert "candidate-ci" in jobs["live-validation"]["needs"]
    assert "candidate-ci" in jobs["release-evidence"]["needs"]
    for job_name in ("verify", "build", "matrix", "release-evidence", "candidate-ci"):
        checkout_refs = [step.get("with", {}).get("ref") for step in jobs[job_name]["steps"]
                         if step.get("uses", "").startswith("actions/checkout@")]
        if checkout_refs:
            assert all(ref == "${{ github.sha }}" for ref in checkout_refs)
    candidate = jobs["candidate-ci"]
    assert candidate["permissions"] == {"contents": "read", "actions": "read"}
    checkout = candidate["steps"][0]
    assert checkout["with"]["fetch-depth"] == 0
    select = next(step for step in candidate["steps"] if step.get("id") == "candidate-ci")
    assert 'git rev-parse HEAD' in select["run"]
    assert 'git rev-parse "refs/tags/$RELEASE_TAG^{commit}"' in select["run"]
    assert '"$GITHUB_SHA"' in select["run"]
    assert 'tools.release_ci --repo "$GITHUB_REPOSITORY" --commit "$candidate_sha"' in select["run"]
    assert select["env"]["RELEASE_TAG"] == "${{ github.event.release.tag_name }}"
    download = next(step for step in candidate["steps"] if "run-id" in step.get("with", {}))
    assert download["uses"].startswith("actions/download-artifact@")
    assert download["with"] == {
        "github-token": "${{ github.token }}",
        "run-id": "${{ steps.candidate-ci.outputs.run-id }}",
        "pattern": "junit-g2-rc-matrix-*",
        "path": "ci-junit/",
    }
    verify = next(step for step in candidate["steps"] if "--verify-ci-junit ci-junit" in step.get("run", ""))
    assert '--commit "$GITHUB_SHA"' in verify["run"]
    upload = next(step for step in candidate["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["name"] == "candidate-ci-junit" and upload["with"]["path"] == "ci-junit/"
    assert candidate["steps"].index(select) < candidate["steps"].index(download) < candidate["steps"].index(verify) < candidate["steps"].index(upload)
    assert all(not step.get("if") and not step.get("continue-on-error") for step in (select, download, verify, upload))
    assert candidate["outputs"]["run-url"] == "${{ steps.candidate-ci.outputs.run-url }}"
    evidence = jobs["release-evidence"]
    archived = next(step for step in evidence["steps"] if step.get("with", {}).get("name") == "candidate-ci-junit")
    assert archived["with"]["path"] == "ci-junit/"
    generate = next(step for step in evidence["steps"] if "tools.release_evidence" in step.get("run", ""))
    assert '--ci-junit-dir ci-junit --ci-run-url "$CI_RUN_URL"' in generate["run"]
    assert generate["env"]["CI_RUN_URL"] == "${{ needs.candidate-ci.outputs.run-url }}"
    assert evidence["steps"].index(archived) < evidence["steps"].index(generate)


def test_ci_dispatch_stamps_and_uploads_source_reports_for_every_candidate_cell():
    wf = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    matrix = wf["jobs"]["full-matrix"]
    assert matrix["name"] == "full-matrix (${{ matrix.os }}, ${{ matrix.python-version }})"
    steps = matrix["steps"]
    name = "g2-rc-matrix-${{ matrix.os }}-py${{ matrix.python-version }}"
    report = f"junit/{name}.xml"
    suite = next(step for step in steps if "pytest -q" in step.get("run", ""))
    assert f'--junitxml="{report}"' in suite["run"]
    stamp = next(step for step in steps if "--stamp-junit" in step.get("run", ""))
    dispatch = "github.event_name == 'workflow_dispatch' && inputs.full_matrix"
    assert stamp["if"] == dispatch
    assert f'--stamp-junit "{report}" --gate G2 --commit "$(git rev-parse HEAD)"' in stamp["run"]
    upload = next(step for step in steps if step.get("with", {}).get("name") == f"junit-{name}")
    assert upload["if"] == dispatch
    assert upload["with"]["path"] == report
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["overwrite"] is True
    timing = next(step for step in steps if step.get("run") == SERIAL_TIMING_RUN)
    assert steps.index(suite) < steps.index(timing) < steps.index(stamp) < steps.index(upload)


@pytest.mark.parametrize("workflow, job, report", [
    ("ci.yml", "ubuntu-full", "junit/ubuntu-full.xml"),
    ("ci.yml", "windows-platform", "junit/windows-full.xml"),
    ("ci.yml", "full-matrix", '"junit/g2-rc-matrix-${{ matrix.os }}-py${{ matrix.python-version }}.xml"'),
    ("publish.yml", "verify", "junit/g2.xml"),
])
def test_monitor_timing_junit_is_merged_before_stamping_or_upload_on_failure(workflow, job, report):
    wf = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))
    steps = wf["jobs"][job]["steps"]
    parallel = next(i for i, step in enumerate(steps) if f"--junitxml={report}" in step.get("run", ""))
    serial = steps[parallel + 1]
    assert serial["id"] == "monitor-timing"
    assert serial["run"] == SERIAL_TIMING_RUN
    assert not serial.get("continue-on-error")
    merge = steps[parallel + 2]
    assert merge["run"] == f"python -m tools.ci_platform merge-junit {report} {SERIAL_TIMING_REPORT}"
    assert merge["if"] == (
        "${{ !cancelled() && (steps.monitor-timing.outcome == 'success' || steps.monitor-timing.outcome == 'failure') }}"
    )
    evidence_steps = [
        i for i, step in enumerate(steps)
        if f"--stamp-junit {report}" in step.get("run", "")
        or step.get("with", {}).get("path") in (report.strip('"'), "junit/windows-*.xml")
    ]
    assert evidence_steps and min(evidence_steps) > parallel + 2


def test_merge_junit_preserves_serial_failure_and_suite_counts():
    parallel = ET.fromstring(
        '<testsuites><testsuite name="pytest" tests="1" failures="0">'
        '<testcase classname="monitor" name="test_work_bound"/></testsuite></testsuites>'
    )
    serial = ET.fromstring(
        '<testsuites><testsuite name="monitor-timing" tests="2" failures="1">'
        '<testcase classname="monitor" name="test_p95"><failure>260 ms</failure></testcase>'
        '<testcase classname="monitor" name="test_activity_wall_clock"/></testsuite></testsuites>'
    )
    ci_platform.merge_junit(parallel, serial)
    structural, timing = parallel.findall("testsuite")
    assert structural.get("tests") == "1" and structural.get("failures") == "0"
    assert timing.get("tests") == "2" and timing.get("failures") == "1"
    assert timing.findtext("testcase[@name='test_p95']/failure") == "260 ms"
    with pytest.raises(ValueError, match="already reported"):
        ci_platform.merge_junit(parallel, serial)


@pytest.mark.parametrize("worker, ci", [(True, True), (False, True), (True, False)])
def test_monitor_timing_cases_deferred_only_in_ci_workers(monkeypatch, worker, ci):
    if ci:
        monkeypatch.setenv("PEERHUB_CI_SERIAL_TIMING", "1")
    else:
        monkeypatch.delenv("PEERHUB_CI_SERIAL_TIMING", raising=False)
    deselected = []
    collected = SimpleNamespace(config=SimpleNamespace(
        hook=SimpleNamespace(pytest_deselected=lambda *, items: deselected.extend(items)),
    ))
    if worker:
        collected.config.workerinput = {}

    class Case:
        def __init__(self, test):
            self.markers = {marker.name: marker for marker in getattr(test, "pytestmark", ())}

        def get_closest_marker(self, name):
            return self.markers.get(name)

    structural = [
        Case(test_diag_monitor_read_model.test_monitor_frame_scale_smoke_v1_bounded_queries_indexes),
        Case(test_monitor_activity.test_scale_one_scope_with_a_long_delivery_history),
    ]
    timing = [
        Case(test_diag_monitor_read_model.test_monitor_frame_scale_p95),
        Case(test_monitor_activity.test_activity_long_delivery_history_wall_clock),
    ]
    items = structural + timing
    pytest_collection_modifyitems(collected, items)
    assert items == (structural if worker and ci else structural + timing)
    assert deselected == (timing if worker and ci else [])
