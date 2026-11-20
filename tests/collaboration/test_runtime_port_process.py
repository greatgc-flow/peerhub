"""M3.6: a hard-cancellable runtime binding. Real subprocesses, real process-tree cancellation, no remote shell."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
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


def gone(target: int | psutil.Process, timeout: float = 10.0) -> bool:
    """True once the process is dead. A `psutil.Process` taken while it ran is checked by identity (pid and creation time): on a
    loaded machine Windows can hand a freed pid to a new process within seconds, and a bare pid check then reports the dead
    process as still running."""
    alive = target.is_running if isinstance(target, psutil.Process) else (lambda: psutil.pid_exists(target))
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not alive():
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


def test_default_spool_stays_in_system_temp():
    a = ProcessRuntimeAdapter()
    a.register_command(cap("quick"), [PY, "-c", "print('{}')"])
    job = a.submit("quick", {})
    spool = Path(a._procs[job.job_id][1])
    assert spool.parent == Path(tempfile.gettempdir())
    assert wait_done(a, job.job_id).status == "COMPLETED"
    assert not spool.exists()


def test_explicit_scratch_is_lazy_and_independent_until_settle_and_cancel(tmp_path):
    scratch = tmp_path / "nested scratch" / "tmp"
    a = ProcessRuntimeAdapter(scratch_dir=scratch)
    a.register_command(cap("quick"), [PY, "-c", "print('{}')"])
    a.register_command(cap("sleepy"), [PY, "-c", "import time; time.sleep(60)"])
    assert not scratch.exists()
    sleepy = a.submit("sleepy", {})
    try:
        (sleepy_spool,) = scratch.iterdir()
        quick = a.submit("quick", {})
        assert len(list(scratch.iterdir())) == 2
        assert wait_done(a, quick.job_id).status == "COMPLETED"
        assert list(scratch.iterdir()) == [sleepy_spool]
    finally:
        assert a.cancel(sleepy.job_id).status == "CANCELLED"
    assert list(scratch.iterdir()) == []


def test_spawn_failure_removes_spool(tmp_path):
    scratch = tmp_path / "scratch"
    a = ProcessRuntimeAdapter(scratch_dir=str(scratch))
    a.register_command(cap("missing"), [str(tmp_path / "missing-executable")])
    job = a.submit("missing", {})
    assert job.status == "FAILED" and job.error == "start failed: FileNotFoundError"
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("running,interrupt", [(False, False), (True, False), (True, True)])
def test_orderly_exit_cleans_uncollected_job(running, interrupt):
    owner = textwrap.dedent("""
        import json, sys
        from peerhub.extensions.runtime_port import ProcessRuntimeAdapter, RuntimeCapability

        # This fixture launches one process with no descendants.
        class LeafRuntime(ProcessRuntimeAdapter):
            @staticmethod
            def _kill_tree(proc):
                proc.kill()

        adapter = LeafRuntime()
        adapter.register_command(RuntimeCapability("sleepy", "sleepy", "test"),
                                 [sys.executable, "-c", sys.argv[1]])
        job = adapter.submit("sleepy", {})
        proc, spool = adapter._procs[job.job_id]
        if sys.argv[1] == "print('{}')":
            proc.wait(timeout=10)
        print(json.dumps([proc.pid, spool]), flush=True)
        if sys.argv[2] == "True":
            raise KeyboardInterrupt
    """)
    code = "import time; time.sleep(60)" if running else "print('{}')"
    result = subprocess.run([PY, "-c", owner, code, str(interrupt)], capture_output=True, timeout=30)
    assert (result.returncode != 0) == interrupt, result.stderr.decode()
    pid, spool = json.loads(result.stdout)
    assert gone(pid)
    assert not Path(spool).exists()


def test_close_retries_owned_unlink_failure(monkeypatch):
    a = ProcessRuntimeAdapter()
    a.register_command(cap("quick"), [PY, "-c", "print('{}')"])
    job = a.submit("quick", {})
    spool = Path(a._procs[job.job_id][1])
    with monkeypatch.context() as patch:
        def locked(path):
            assert Path(path) == spool
            raise PermissionError("injected sharing violation")

        from peerhub.extensions import runtime_port
        patch.setattr(runtime_port.os, "unlink", locked)
        assert wait_done(a, job.job_id).status == "COMPLETED"
        assert spool.is_file()
    a.close()
    assert not spool.exists()


def test_registration_refuses_unsafe_command_shapes_and_no_raw_shell_exists():
    a = ProcessRuntimeAdapter()
    for argv in ([], ["{prog}", "x"], [PY, "prefix-{word}"], [PY, "{undeclared}"], [PY, ""], ["definitely-not-an-installed-binary-xyz"]):
        with pytest.raises(ValueError):
            a.register_command(cap("c", word="string"), argv)
    with pytest.raises(GeneralRemoteShellForbiddenError):
        a.invoke_raw_shell("whoami")
    with pytest.raises(Exception):
        a.submit("never-registered", {})


# The pid file is published whole with a rename: `open(path, "w")` creates it empty before the write, and on a loaded machine
# the test could see the file in that gap and fail on int("").
TREE = textwrap.dedent("""
    import os, subprocess, sys, time
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    with open(sys.argv[1] + ".tmp", "w") as f:
        f.write(str(child.pid))
    os.replace(sys.argv[1] + ".tmp", sys.argv[1])
    time.sleep(120)
""")


@pytest.mark.catalog_id("RUN-013")
def test_cancel_kills_the_whole_process_tree_and_reports_cancelled(tmp_path):
    pidfile = tmp_path / "child.pid"
    a = ProcessRuntimeAdapter()
    a.register_command(cap("tree", pidfile="string"), [PY, "-c", TREE, "{pidfile}"])
    job = a.submit("tree", {"pidfile": str(pidfile)})
    end = time.monotonic() + 15
    while not pidfile.exists() and time.monotonic() < end:
        time.sleep(0.05)
    child = psutil.Process(int(pidfile.read_text()))
    parent = psutil.Process(a._procs[job.job_id][0].pid)  # noqa: SLF001 - test-only peek at the spawned parent
    assert child.is_running() and parent.is_running()
    cancelled = a.cancel(job.job_id)
    assert cancelled.status == "CANCELLED"
    assert gone(parent) and gone(child)  # no orphaned grandchild keeps running after a "hard" cancel
    assert a.cancel(job.job_id).status == "CANCELLED"  # idempotent


@pytest.mark.catalog_id("RUN-013")
def test_an_unconfirmed_kill_is_never_reported_as_cancelled(monkeypatch):
    a = ProcessRuntimeAdapter(cancel_wait_seconds=0.3)
    a.register_command(cap("sleepy"), [PY, "-c", "import time; time.sleep(60)"])
    job = a.submit("sleepy", {})
    spool = Path(a._procs[job.job_id][1])
    monkeypatch.setattr(ProcessRuntimeAdapter, "_kill_tree", staticmethod(lambda proc: None))  # the OS "fails" to kill
    with pytest.raises(RuntimeCancellationError):
        a.cancel(job.job_id)
    assert a.get(job.job_id).status == "RUNNING"  # truthful: its effects may still be happening
    assert spool.is_file()
    monkeypatch.undo()
    assert a.cancel(job.job_id).status == "CANCELLED"  # a real kill later still works
    assert not spool.exists()


@pytest.mark.catalog_id("RUN-013")
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


def test_a_failed_taskkill_is_reported_not_swallowed_so_cancel_cannot_claim_a_dead_tree(monkeypatch):
    from types import SimpleNamespace
    from peerhub.extensions import runtime_port

    monkeypatch.setattr(runtime_port.os, "name", "nt")
    monkeypatch.setattr(runtime_port.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(OSError):
        runtime_port.ProcessRuntimeAdapter._kill_tree(SimpleNamespace(pid=1))
