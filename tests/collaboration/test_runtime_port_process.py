"""M3.6: a hard-cancellable runtime binding. Real subprocesses, real process-tree cancellation, no remote shell."""
from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

from peerhub.extensions.orchestration import (
    HardDeadlineStepRunner,
    Plan,
    PlanBoundExceededError,
    PlanBounds,
    PlanExecutor,
    PlanStep,
    UnsupportedExecutionBoundError,
)
from peerhub.extensions.runtime_port import (
    GeneralRemoteShellForbiddenError,
    LoopbackRuntimeAdapter,
    ProcessRuntimeAdapter,
    RuntimeCancellationError,
    RuntimeCapability,
    RuntimeJobFailedError,
)

psutil = pytest.importorskip("psutil")
PY = sys.executable


def cap(cid: str, **schema) -> RuntimeCapability:
    return RuntimeCapability(cid, cid, "test capability", dict(schema))


def wait_done(adapter: ProcessRuntimeAdapter, job_id: str, timeout: float = 20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = adapter.get(job_id)
        if job.status in ("COMPLETED", "FAILED", "CANCELLED"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def gone(pid: int, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.05)
    return False


def test_a_command_capability_runs_collects_json_and_validates_parameters_first():
    a = ProcessRuntimeAdapter()
    a.register_command(cap("echo", word="string"), [PY, "-c", "import json,sys;print(json.dumps({'word': sys.argv[1], 'cost': 2}))", "{word}"])
    assert a.probe("echo")["available"] is True
    job = wait_done(a, a.submit("echo", {"word": "hi; rm -rf /"}).job_id)  # a metacharacter string is just one argv element
    assert job.status == "COMPLETED" and job.output == {"word": "hi; rm -rf /", "cost": 2}
    assert a.collect(job.job_id) == job.output
    with pytest.raises(ValueError):
        a.submit("echo", {"word": 5})  # rejected before any process starts
    with pytest.raises(ValueError):
        a.submit("echo", {"word": "x", "extra": 1})


@pytest.mark.parametrize("code,error", [
    ("import sys; sys.exit(3)", "exit code 3"),
    ("print('not json')", "not valid JSON"),
    ("print('[1, 2]')", "not a JSON object"),
])
def test_bad_exit_or_bad_output_fails_the_job_and_cannot_be_collected(code, error):
    a = ProcessRuntimeAdapter()
    a.register_command(cap("bad"), [PY, "-c", code])
    job = wait_done(a, a.submit("bad", {}).job_id)
    assert job.status == "FAILED" and error in (job.error or "")
    with pytest.raises(RuntimeJobFailedError):
        a.collect(job.job_id)


def test_oversized_output_is_bounded():
    a = ProcessRuntimeAdapter(max_output_bytes=50)
    a.register_command(cap("big"), [PY, "-c", "import json;print(json.dumps({'x': 'y' * 500}))"])
    job = wait_done(a, a.submit("big", {}).job_id)
    assert job.status == "FAILED" and "max_output_bytes" in (job.error or "")


def test_registration_refuses_unsafe_command_shapes_and_no_raw_shell_exists():
    a = ProcessRuntimeAdapter()
    for argv in ([], ["{prog}", "x"], [PY, "prefix-{word}"], [PY, "{undeclared}"], [PY, ""], ["definitely-not-an-installed-binary-xyz"]):
        with pytest.raises(ValueError):
            a.register_command(cap("c", word="string"), argv)
    with pytest.raises(GeneralRemoteShellForbiddenError):
        a.invoke_raw_shell("whoami")
    with pytest.raises(Exception):
        a.submit("never-registered", {})


TREE = textwrap.dedent("""
    import subprocess, sys, time
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    open(sys.argv[1], "w").write(str(child.pid))
    time.sleep(120)
""")


def test_cancel_kills_the_whole_process_tree_and_reports_cancelled(tmp_path):
    pidfile = tmp_path / "child.pid"
    a = ProcessRuntimeAdapter()
    a.register_command(cap("tree", pidfile="string"), [PY, "-c", TREE, "{pidfile}"])
    job = a.submit("tree", {"pidfile": str(pidfile)})
    end = time.monotonic() + 15
    while not pidfile.exists() and time.monotonic() < end:
        time.sleep(0.05)
    child_pid = int(pidfile.read_text())
    parent_pid = a._procs[job.job_id][0].pid  # noqa: SLF001 - test-only peek at the spawned parent
    assert psutil.pid_exists(child_pid) and psutil.pid_exists(parent_pid)
    cancelled = a.cancel(job.job_id)
    assert cancelled.status == "CANCELLED"
    assert gone(parent_pid) and gone(child_pid)  # no orphaned grandchild keeps running after a "hard" cancel
    assert a.cancel(job.job_id).status == "CANCELLED"  # idempotent


def test_an_unconfirmed_kill_is_never_reported_as_cancelled(monkeypatch):
    a = ProcessRuntimeAdapter(cancel_wait_seconds=0.3)
    a.register_command(cap("sleepy"), [PY, "-c", "import time; time.sleep(60)"])
    job = a.submit("sleepy", {})
    monkeypatch.setattr(ProcessRuntimeAdapter, "_kill_tree", staticmethod(lambda proc: None))  # the OS "fails" to kill
    with pytest.raises(RuntimeCancellationError):
        a.cancel(job.job_id)
    assert a.get(job.job_id).status == "RUNNING"  # truthful: its effects may still be happening
    monkeypatch.undo()
    assert a.cancel(job.job_id).status == "CANCELLED"  # a real kill later still works


def test_strict_orchestration_kills_overrunning_work_at_the_deadline(tmp_path):
    pidfile = tmp_path / "child.pid"
    a = ProcessRuntimeAdapter()
    a.register_command(cap("tree", pidfile="string"), [PY, "-c", TREE, "{pidfile}"])
    runner = HardDeadlineStepRunner(a)
    plan = Plan("hard", "t", [PlanStep("slow", "tree", {"pidfile": str(pidfile)}, max_attempts=1)], bounds=PlanBounds(timeout_seconds=1.5))
    started = time.monotonic()
    with pytest.raises(PlanBoundExceededError):
        PlanExecutor(require_hard_deadline=True).execute_plan(plan, runner)
    assert time.monotonic() - started < 20  # it did not wait for the 120 s sleeps
    assert pidfile.exists() and gone(int(pidfile.read_text()))  # and the work itself is dead, not just noticed


def test_strict_orchestration_runs_normal_steps_and_records_reported_cost():
    a = ProcessRuntimeAdapter()
    a.register_command(cap("quick", n="integer"), [PY, "-c", "import json,sys;print(json.dumps({'n': int(sys.argv[1]), 'cost': 1.5}))", "{n}"])
    plan = Plan("ok", "t", [PlanStep("a", "quick", {"n": 1}), PlanStep("b", "quick", {"n": 2}, depends_on=["a"])],
                bounds=PlanBounds(timeout_seconds=60.0, cost_budget=10.0))
    summary = PlanExecutor(require_hard_deadline=True).execute_plan(plan, HardDeadlineStepRunner(a))
    assert summary.status == "COMPLETED" and summary.total_cost == 3.0
    assert summary.step_results["b"].output == {"n": 2, "cost": 1.5}


def test_the_runner_reports_unconfirmed_cancellation_as_may_have_started(monkeypatch):
    a = ProcessRuntimeAdapter(cancel_wait_seconds=0.2)
    a.register_command(cap("sleepy"), [PY, "-c", "import time; time.sleep(60)"])
    monkeypatch.setattr(ProcessRuntimeAdapter, "_kill_tree", staticmethod(lambda proc: None))
    runner = HardDeadlineStepRunner(a, poll_seconds=0.01)
    runner.set_deadline(time.monotonic() + 0.2)
    result = runner(PlanStep("s", "sleepy", {}), 1)
    assert result.status == "MAY_HAVE_STARTED"  # never blindly replayed
    monkeypatch.undo()
    for proc, _ in list(a._procs.values()):  # noqa: SLF001 - cleanup
        proc.kill()


def test_a_rejected_submission_is_a_certain_failure_and_the_runner_needs_the_process_adapter():
    a = ProcessRuntimeAdapter()
    result = HardDeadlineStepRunner(a)(PlanStep("s", "unknown-capability", {}), 1)
    assert result.status == "FAILED" and "not started" in (result.error or "")
    with pytest.raises(TypeError):
        HardDeadlineStepRunner(object())  # a port that does not declare confirmed hard cancellation is refused
    with pytest.raises(TypeError):
        HardDeadlineStepRunner(LoopbackRuntimeAdapter())  # the in-process loopback cannot interrupt a handler
    with pytest.raises(UnsupportedExecutionBoundError):
        PlanExecutor(require_hard_deadline=True).execute_plan(Plan("p", "t", [PlanStep("s", "x", {})]), lambda s, a: None)  # type: ignore[arg-type,return-value]
