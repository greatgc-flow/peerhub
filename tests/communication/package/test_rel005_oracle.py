"""REL-005 exact pytest collection and execution oracle contracts."""
from __future__ import annotations

import copy
from importlib import import_module
from typing import Any

import pytest

_PTY_NODEID = "tests/communication/e2e/test_cli_monitor.py::test_posix_pty_snapshot_obeys_physical_viewport"
_SKIP_REASON = "POSIX PTY smoke; Windows TTY behavior is covered by ConPTY/manual tests"
_SKIP_REASON_RAW = "Skipped: " + _SKIP_REASON


def _reports(platform: str = "posix") -> tuple[dict[str, Any], dict[str, Any]]:
    item_ids = [_PTY_NODEID, "tests/sample/test_other.py::test_pass"]
    collector = [
        {"nodeid": "", "report_type": "CollectReport", "outcome": "passed", "result_count": 1},
        {"nodeid": "tests/sample/test_other.py", "report_type": "CollectReport", "outcome": "passed", "result_count": 2},
    ]
    passed = [
        {"nodeid": nodeid, "when": phase, "outcome": "passed", "wasxfail": None, "skip_reason": None}
        for nodeid in item_ids for phase in ("setup", "call", "teardown")
    ]
    if platform == "windows":
        passed = [r for r in passed if not (r["nodeid"] == _PTY_NODEID and r["when"] == "call")]
        passed[0] = {**passed[0], "outcome": "skipped", "skip_reason": _SKIP_REASON_RAW}
    collection = {
        "schema_version": 1, "pytest_version": "9.1.1", "run_nonce": "collect-nonce",
        "exit_status": 0, "item_nodeids": item_ids, "collect_reports": collector,
        "deselected_nodeids": [], "test_reports": [],
    }
    execution = {
        "schema_version": 1, "pytest_version": "9.1.1", "run_nonce": "execute-nonce",
        "exit_status": 0, "item_nodeids": item_ids, "collect_reports": copy.deepcopy(collector),
        "deselected_nodeids": [], "test_reports": passed,
    }
    return collection, execution


def _validate(collection: dict[str, Any], execution: dict[str, Any], *, platform: str = "posix",
              collect_returncode: int = 0, execution_returncode: int = 0) -> None:
    oracle = import_module("_rel005_oracle")
    oracle.validate_pair(collection, execution, expected_nonce=("collect-nonce", "execute-nonce"),
                         platform=platform, collect_returncode=collect_returncode,
                         execution_returncode=execution_returncode)


def test_rel005_oracle_accepts_complete_posix_and_exact_windows_skip_lifecycles():
    collection, execution = _reports()
    _validate(collection, execution)
    collection, execution = _reports("windows")
    _validate(collection, execution, platform="windows")


@pytest.mark.parametrize("mutation", [
    "missing_item", "extra_item", "duplicate_item", "collection_reports_differ", "duplicate_collector",
    "collector_failed", "collector_skipped", "deselected_differ", "wrong_nonce", "wrong_version",
    "nonzero_exit", "process_failed", "missing_phase", "duplicate_phase", "phase_order",
    "unexpected_skip", "wrong_skip_reason", "double_skip_prefix", "wrong_skip_node", "xfail", "teardown_failed",
    "missing_expected_windows_skip", "unexpected_posix_skip",
])
def test_rel005_oracle_rejects_inconsistent_or_nonterminal_evidence(mutation: str):
    collection, execution = _reports("windows" if mutation == "missing_expected_windows_skip" else "posix")
    platform = "windows" if mutation == "missing_expected_windows_skip" else "posix"
    collect_rc = execute_rc = 0
    if mutation == "missing_item":
        collection["item_nodeids"].pop()
    elif mutation == "extra_item":
        execution["item_nodeids"].append("tests/extra.py::test_extra")
    elif mutation == "duplicate_item":
        execution["item_nodeids"].append(execution["item_nodeids"][0])
    elif mutation == "collection_reports_differ":
        execution["collect_reports"].pop()
    elif mutation == "duplicate_collector":
        execution["collect_reports"].append(copy.deepcopy(execution["collect_reports"][0]))
    elif mutation in {"collector_failed", "collector_skipped"}:
        execution["collect_reports"][0]["outcome"] = "failed" if mutation == "collector_failed" else "skipped"
    elif mutation == "deselected_differ":
        execution["deselected_nodeids"] = ["tests/sample/test_unselected.py::test_not_run"]
    elif mutation == "wrong_nonce":
        execution["run_nonce"] = "stale-nonce"
    elif mutation == "wrong_version":
        execution["pytest_version"] = "8.0.0"
    elif mutation == "nonzero_exit":
        execution["exit_status"] = 1
    elif mutation == "process_failed":
        execute_rc = 1
    elif mutation == "missing_phase":
        execution["test_reports"].pop(0)
    elif mutation == "duplicate_phase":
        execution["test_reports"].append(copy.deepcopy(execution["test_reports"][0]))
    elif mutation == "phase_order":
        execution["test_reports"][0], execution["test_reports"][1] = execution["test_reports"][1], execution["test_reports"][0]
    elif mutation in {"unexpected_skip", "wrong_skip_reason", "double_skip_prefix", "wrong_skip_node", "unexpected_posix_skip"}:
        row = execution["test_reports"][0]
        row["outcome"] = "skipped"
        row["skip_reason"] = "unexpected skip reason"
        if mutation == "wrong_skip_reason":
            platform = "windows"
            collection, execution = _reports("windows")
            execution["test_reports"][0]["skip_reason"] = _SKIP_REASON
        elif mutation == "double_skip_prefix":
            platform = "windows"
            collection, execution = _reports("windows")
            execution["test_reports"][0]["skip_reason"] = "Skipped: " + _SKIP_REASON_RAW
        elif mutation == "wrong_skip_node":
            platform = "windows"
            collection, execution = _reports("windows")
            execution["test_reports"][0]["nodeid"] = "tests/other.py::test_wrong"
        elif mutation == "unexpected_posix_skip":
            execution["test_reports"][0]["skip_reason"] = "platform-specific skip"
    elif mutation == "xfail":
        execution["test_reports"][1]["wasxfail"] = "expected failure"
    elif mutation == "teardown_failed":
        execution["test_reports"][2]["outcome"] = "failed"
    elif mutation == "missing_expected_windows_skip":
        execution["test_reports"][0] = {**execution["test_reports"][0], "outcome": "passed", "skip_reason": None}
        execution["test_reports"].insert(1, {
            "nodeid": _PTY_NODEID, "when": "call", "outcome": "passed", "wasxfail": None, "skip_reason": None,
        })
    with pytest.raises((ValueError, TypeError)):
        _validate(collection, execution, platform=platform, collect_returncode=collect_rc,
                  execution_returncode=execute_rc)


def test_rel005_oracle_rejects_unknown_skip_reason_representation():
    oracle = import_module("_rel005_oracle")
    for raw in ("Skipped: opaque representation", ("path", "line", "reason"), ("path", 1),
                (None, 1, "reason"), ("path", True, "reason"), ("path", 1, None)):
        with pytest.raises(ValueError, match="skip"):
            oracle.extract_skip_reason(raw)


def test_rel005_report_write_is_atomic_when_replace_fails(tmp_path, monkeypatch):
    oracle = import_module("_rel005_oracle")
    destination = tmp_path / "report.json"
    destination.write_text("previous complete report\n", encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("simulated interrupted publish")

    monkeypatch.setattr(oracle.os, "replace", fail_replace)
    with pytest.raises(OSError, match="interrupted"):
        oracle._write_report(destination, {"schema_version": 1})
    assert destination.read_text(encoding="utf-8") == "previous complete report\n"
    assert list(tmp_path.iterdir()) == [destination]


def test_rel005_oracle_plugin_captures_supported_skip_lifecycle(tmp_path):
    """Exercise actual pytest hooks on each compatibility-matrix version."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    sample = tmp_path / "tests/communication/e2e"
    sample.mkdir(parents=True)
    test_file = sample / "test_cli_monitor.py"
    deselected_file = sample / "test_deselected.py"
    test_file.write_text(
        "import os, pytest\n"
        "@pytest.mark.skipif(os.name == 'nt', reason=" + repr(_SKIP_REASON) + ")\n"
        "def test_posix_pty_snapshot_obeys_physical_viewport():\n    assert True\n",
        encoding="utf-8",
        newline="\n",
    )
    deselected_file.write_text(
        "import pytest\n"
        "@pytest.mark.live\n"
        "def test_deselected_by_default_gate():\n    assert False\n",
        encoding="utf-8",
        newline="\n",
    )
    base_env = os.environ.copy()
    base_env.pop("PYTEST_ADDOPTS", None)
    base_env["PYTHONPATH"] = os.pathsep.join((str(repo / "tests"), str(repo)))
    common = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
              "-p", "_rel005_oracle", "-o", "addopts=", "-o", "markers=live",
              "-m", "not live", "--rootdir", str(tmp_path), str(sample)]
    records: list[dict[str, Any]] = []
    for label, extra in (("collect", ["--collect-only"]), ("execute", [])):
        nonce = f"{label}-nonce"
        report = tmp_path / label / "evidence.json"
        report.parent.mkdir()
        command = [*common, "--rel005-report", str(report), "--rel005-nonce", nonce, *extra]
        result = subprocess.run(command, cwd=repo, env=base_env, text=True, capture_output=True, timeout=60, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        records.append(json.loads(report.read_text(encoding="utf-8")))
    _validate(records[0], records[1], platform="windows" if os.name == "nt" else "posix")
    assert records[0]["deselected_nodeids"] == [
        "tests/communication/e2e/test_deselected.py::test_deselected_by_default_gate"
    ]
