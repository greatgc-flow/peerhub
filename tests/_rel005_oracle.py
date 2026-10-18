"""Private pytest plugin and fail-closed validator for the REL-005 suite gate."""
from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

_APPROVED_PTY_SKIPS: dict[str, str] = {
    "tests/communication/e2e/test_cli_monitor.py::test_posix_pty_snapshot_obeys_physical_viewport":
        "Skipped: POSIX PTY smoke; Windows TTY behavior is covered by ConPTY/manual tests",
    "tests/communication/e2e/test_cli_monitor.py::test_monitor_reset_countdown_posix_pty_plain_parity":
        "Skipped: POSIX PTY parity; simulated TTY parity runs on Windows",
}


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("rel005-oracle")
    group.addoption("--rel005-report", dest="rel005_report", required=True)
    group.addoption("--rel005-nonce", dest="rel005_nonce", required=True)


def pytest_configure(config: Any) -> None:
    global _ACTIVE_STATE
    config._rel005_state = {
        "report_path": config.getoption("rel005_report"),
        "run_nonce": config.getoption("rel005_nonce"),
        "item_nodeids": [],
        "collect_reports": [],
        "deselected_nodeids": [],
        "test_reports": [],
    }
    _ACTIVE_STATE = config._rel005_state


def _state(config: Any) -> dict[str, Any]:
    state = getattr(config, "_rel005_state", None)
    if not isinstance(state, dict):
        raise RuntimeError("REL-005 oracle plugin state was not initialized")
    return state


def pytest_collection_finish(session: Any) -> None:
    _state(session.config)["item_nodeids"] = [str(item.nodeid) for item in session.items]


def pytest_collectreport(report: Any) -> None:
    result = getattr(report, "result", None)
    try:
        result_count = len(result) if result is not None else 0
    except TypeError:
        result_count = -1
    state = _ACTIVE_STATE
    if state is None:
        raise RuntimeError("REL-005 oracle received collector report before configuration")
    state["collect_reports"].append({
        "nodeid": str(report.nodeid),
        "report_type": type(report).__qualname__,
        "outcome": str(report.outcome),
        "result_count": result_count,
    })


def pytest_deselected(items: list[Any]) -> None:
    # Pytest calls this hook once for each deselection batch; preserve order and duplicates for validation.
    state = _ACTIVE_STATE
    if state is None:
        raise RuntimeError("REL-005 oracle received deselection before configuration")
    state["deselected_nodeids"].extend(str(item.nodeid) for item in items)


def pytest_runtest_logreport(report: Any) -> None:
    row: dict[str, Any] = {
        "nodeid": str(report.nodeid),
        "when": str(report.when),
        "outcome": str(report.outcome),
        "wasxfail": getattr(report, "wasxfail", None),
        "skip_reason": None,
    }
    if row["outcome"] == "skipped":
        row["skip_reason"] = extract_skip_reason(getattr(report, "longrepr", None))
    state = _ACTIVE_STATE
    if state is None:
        raise RuntimeError("REL-005 oracle received test report before configuration")
    state["test_reports"].append(row)


_ACTIVE_STATE: dict[str, Any] | None = None


def extract_skip_reason(longrepr: object) -> str:
    """Return only pytest's structured (path, line, reason) skip representation; fail closed otherwise."""
    if (isinstance(longrepr, tuple) and len(longrepr) == 3
            and isinstance(longrepr[0], (str, Path))
            and isinstance(longrepr[1], int) and not isinstance(longrepr[1], bool)
            and isinstance(longrepr[2], str)):
        return longrepr[2]
    raise ValueError("unrecognized pytest skip reason representation")


def _write_report(path_value: object, payload: dict[str, Any]) -> None:
    path = Path(str(path_value))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    state = _state(session.config)
    report = {
        "schema_version": 1,
        "pytest_version": str(pytest.__version__),
        "run_nonce": state["run_nonce"],
        "exit_status": int(exitstatus),
        "item_nodeids": state["item_nodeids"],
        "collect_reports": state["collect_reports"],
        "deselected_nodeids": state["deselected_nodeids"],
        "test_reports": state["test_reports"],
    }
    _write_report(state["report_path"], report)


def _reject(message: str) -> None:
    raise ValueError(message)


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _reject(f"{label} must be a list of strings")
    return value


def _validate_report(report: object, *, nonce: str, label: str, returncode: int) -> dict[str, Any]:
    if returncode != 0:
        _reject(f"{label} pytest process exited {returncode}")
    if not isinstance(report, dict):
        _reject(f"{label} evidence is not an object")
    if type(report.get("schema_version")) is not int or report["schema_version"] != 1:
        _reject(f"{label} evidence schema mismatch")
    if report.get("run_nonce") != nonce:
        _reject(f"{label} evidence nonce mismatch (stale or wrong report)")
    if not isinstance(report.get("pytest_version"), str) or not report["pytest_version"]:
        _reject(f"{label} pytest version missing")
    if type(report.get("exit_status")) is not int or report["exit_status"] != 0:
        _reject(f"{label} pytest session exit status is not zero")
    items = _string_list(report.get("item_nodeids"), f"{label} item nodeids")
    if not items or any(not nodeid for nodeid in items):
        _reject(f"{label} collection is empty or has an empty item nodeid")
    if len(items) != len(set(items)):
        _reject(f"{label} collection contains duplicate item nodeids")
    return report


def _collector_rows(value: object, label: str) -> list[tuple[str, str, str, int]]:
    if not isinstance(value, list):
        _reject(f"{label} collector reports must be a list")
    rows: list[tuple[str, str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            _reject(f"{label} collector report is malformed")
        nodeid, report_type, outcome, count = (item.get("nodeid"), item.get("report_type"),
                                                item.get("outcome"), item.get("result_count"))
        if not all(isinstance(field, str) for field in (nodeid, report_type, outcome)):
            _reject(f"{label} collector report identity is malformed")
        key = (nodeid, report_type)
        if key in seen:
            _reject(f"{label} has duplicate collector report {key!r}")
        seen.add(key)
        if outcome != "passed":
            _reject(f"{label} collector {nodeid!r} outcome is {outcome!r}")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            _reject(f"{label} collector {nodeid!r} result count is malformed")
        rows.append((nodeid, report_type, outcome, count))
    return rows


def _phase_rows(value: object, *, nodeids: set[str], label: str) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, list):
        _reject(f"{label} test reports must be a list")
    phases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in value:
        if not isinstance(item, dict):
            _reject(f"{label} test report is malformed")
        nodeid, when, outcome = item.get("nodeid"), item.get("when"), item.get("outcome")
        if not isinstance(nodeid, str) or nodeid not in nodeids:
            _reject(f"{label} test report has unknown nodeid {nodeid!r}")
        if when not in ("setup", "call", "teardown") or not isinstance(outcome, str):
            _reject(f"{label} test report phase or outcome is malformed")
        phases[nodeid].append(item)
    return phases


def validate_pair(collection: object, execution: object, *, expected_nonce: tuple[str, str],
                  platform: str, collect_returncode: int, execution_returncode: int) -> None:
    """Validate exact collection parity and the terminal pytest phase outcome of every item."""
    if platform not in ("posix", "windows"):
        _reject(f"unsupported platform {platform!r}")
    if (not isinstance(expected_nonce, tuple) or len(expected_nonce) != 2
            or any(not isinstance(value, str) or not value for value in expected_nonce)):
        _reject("expected run nonces must be two non-empty strings")
    collected = _validate_report(collection, nonce=expected_nonce[0], label="collection",
                                 returncode=collect_returncode)
    executed = _validate_report(execution, nonce=expected_nonce[1], label="execution",
                                returncode=execution_returncode)
    if collected["pytest_version"] != executed["pytest_version"]:
        _reject("pytest versions differ between collection and execution")
    collected_items = collected["item_nodeids"]
    executed_items = executed["item_nodeids"]
    if set(collected_items) != set(executed_items):
        _reject("developer collection and sandbox execution item nodeid sets differ")
    collect_nodes = _collector_rows(collected.get("collect_reports"), "collection")
    execution_nodes = _collector_rows(executed.get("collect_reports"), "execution")
    if collect_nodes != execution_nodes:
        _reject("collector reports differ between collection and execution")
    deselected_collect = _string_list(collected.get("deselected_nodeids"), "collection deselected nodeids")
    deselected_execution = _string_list(executed.get("deselected_nodeids"), "execution deselected nodeids")
    if len(deselected_collect) != len(set(deselected_collect)) or len(deselected_execution) != len(set(deselected_execution)):
        _reject("deselected nodeids contain duplicates")
    if deselected_collect != deselected_execution:
        _reject("deselected nodeids differ between collection and execution")
    if collected.get("test_reports") != []:
        _reject("collect-only run unexpectedly has test phase reports")
    phases = _phase_rows(executed.get("test_reports"), nodeids=set(executed_items), label="execution")
    if set(phases) != set(executed_items):
        _reject("execution evidence is missing test phase reports")
    observed_skips: set[str] = set()
    for nodeid in executed_items:
        rows = phases[nodeid]
        lifecycle = [(item["when"], item["outcome"]) for item in rows]
        if nodeid in _APPROVED_PTY_SKIPS and platform == "windows":
            expected = [("setup", "skipped"), ("teardown", "passed")]
            if lifecycle != expected:
                _reject("expected Windows PTY test skip lifecycle is missing or invalid")
            if rows[0].get("skip_reason") != _APPROVED_PTY_SKIPS[nodeid]:
                _reject("Windows PTY skip reason does not exactly match the approved reason")
            observed_skips.add(nodeid)
        else:
            if lifecycle != [("setup", "passed"), ("call", "passed"), ("teardown", "passed")]:
                _reject(f"test {nodeid!r} did not complete setup/pass, call/pass, teardown/pass")
            if any(item.get("skip_reason") is not None for item in rows):
                _reject(f"test {nodeid!r} has an unexpected skip reason")
        if any(item.get("wasxfail") for item in rows):
            _reject(f"test {nodeid!r} was xfailed or xpassed")
    expected_skips = {nodeid for nodeid in executed_items if nodeid in _APPROVED_PTY_SKIPS} if platform == "windows" else set()
    if observed_skips != expected_skips:
        _reject(f"observed skip nodeids {sorted(observed_skips)!r} do not equal {sorted(expected_skips)!r}")
