import ctypes
import os
import subprocess
import sys
from queue import Queue
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from peerhub.extensions import quota_capture, quota_probes
from peerhub.extensions.observation_model import EvidenceState

COMMAND = ["fake-provider"]
PROCESS_HANDLE = 101
JOB_HANDLE = 102
WINERROR = 5
RESUME_FAILURE = -1
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CALLER_FLAGS = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
WAIT_SECONDS = 10
CHILD_SLEEP_SECONDS = 6 * WAIT_SECONDS


@pytest.fixture
def windows(monkeypatch):
    events = []
    options = {}

    def api(name, result):
        def call(*args, **kwargs):
            events.append(name)
            return result
        return Mock(side_effect=call)

    kernel = SimpleNamespace(
        CreateJobObjectW=api("create", JOB_HANDLE),
        SetInformationJobObject=api("set", True),
        AssignProcessToJobObject=api("assign", True),
        TerminateJobObject=api("terminate", True),
        CloseHandle=api("close", True),
    )
    resume = api("resume", 0)

    class Ntdll:
        @property
        def NtResumeProcess(self):
            events.append("resolve resume")
            return resume

    def win_dll(name, **kwargs):
        return {"kernel32": kernel, "ntdll": Ntdll()}[name]

    proc = Mock(
        _handle=PROCESS_HANDLE,
        returncode=None,
        poll=lambda: proc.returncode,
        kill=api("kill", None),
        wait=api("wait", 0),
        communicate=api("communicate", ("", "")),
        stdin=None,
        stdout=None,
        stderr=None,
    )

    def popen(command, **kwargs):
        events.append("spawn")
        options.update(kwargs)
        return proc

    def forbidden(*args, **kwargs):
        pytest.fail("Windows probe cleanup tried to look up a process by PID")

    monkeypatch.setattr(quota_probes, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setattr(quota_probes, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", NO_WINDOW, raising=False)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(psutil, "process_iter", forbidden)
    monkeypatch.setattr(quota_probes, "kill_process_tree", forbidden)
    monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: WINERROR, raising=False)
    return SimpleNamespace(events=events, proc=proc, options=options, kernel=kernel, resume=resume)


def test_windows_probe_assigns_before_resume_and_preserves_flags(windows):
    cancellation = quota_probes.ProbeCancellation()
    proc = cancellation.spawn(COMMAND, creationflags=CALLER_FLAGS)
    cancellation.finish(proc)

    assert windows.events == ["resolve resume", "spawn", "create", "set", "assign", "resume", "terminate", "close", "wait"]
    from peerhub.extensions import win_job

    assert windows.options["creationflags"] == CALLER_FLAGS | NO_WINDOW | win_job.CREATE_SUSPENDED
    assert not windows.options["start_new_session"]
    windows.kernel.CreateJobObjectW.assert_called_once_with(None, None)
    job, info_class, limits, length = windows.kernel.SetInformationJobObject.call_args.args
    assert job == JOB_HANDLE
    assert info_class == win_job.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION
    assert limits._obj.BasicLimitInformation.LimitFlags == win_job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert length == ctypes.sizeof(limits._obj)
    windows.kernel.AssignProcessToJobObject.assert_called_once_with(JOB_HANDLE, PROCESS_HANDLE)
    windows.resume.assert_called_once_with(PROCESS_HANDLE)


@pytest.mark.parametrize("api_name,attempted", [
    ("CreateJobObjectW", ["create"]),
    ("SetInformationJobObject", ["create", "set", "close"]),
    ("AssignProcessToJobObject", ["create", "set", "assign", "close"]),
])
def test_windows_probe_job_failure_resumes_with_handle_only_cleanup(windows, api_name, attempted):
    failed = getattr(windows.kernel, api_name)
    original = failed.side_effect
    def fail(*args):
        original(*args)
        return 0

    failed.side_effect = fail
    cancellation = quota_probes.ProbeCancellation()
    proc = cancellation.spawn(COMMAND)
    cancellation.stop()
    cancellation.finish(proc)

    assert windows.events == ["resolve resume", "spawn", *attempted, "resume", "kill", "wait"]
    warning, = cancellation.take_warnings()
    assert api_name in warning and str(WINERROR) in warning
    assert cancellation.take_warnings() == []


def test_windows_probe_containment_warning_reaches_the_refresh_result(windows, monkeypatch):
    windows.kernel.AssignProcessToJobObject.side_effect = None
    windows.kernel.AssignProcessToJobObject.return_value = False
    monkeypatch.setattr(quota_capture, "ObservationStore", lambda *a, **kw: Mock())
    monkeypatch.setattr(quota_capture, "_read_latest_facts_report", lambda store: None)
    cancellation = quota_probes.ProbeCancellation()

    def version_checker(kind):
        quota_probes.run_text_probe(COMMAND, timeout=WAIT_SECONDS, cancellation=cancellation)
        return None

    result = quota_capture.refresh_quota(__file__, ["cc"], pollers={}, version_checker=version_checker,
                                         cancellation=cancellation)
    warning, = result["warnings"]
    assert "AssignProcessToJobObject" in warning and str(WINERROR) in warning


@pytest.mark.parametrize("missing_handle", [False, True])
def test_windows_probe_resume_failure_kills_the_suspended_process(windows, monkeypatch, missing_handle):
    if missing_handle:
        del windows.proc._handle
    else:
        windows.resume.side_effect = None
        windows.resume.return_value = RESUME_FAILURE
    monkeypatch.setattr(quota_probes, "_real_command", lambda *a: COMMAND)
    cancellation = quota_probes.ProbeCancellation()
    result = quota_probes.poll_claude_account(cancellation=cancellation)
    cancellation.close()

    assert result.state == EvidenceState.ERROR
    assert "communicate" not in windows.events
    windows.proc.kill.assert_called_once_with()
    assert windows.kernel.CloseHandle.call_count == (0 if missing_handle else 1)
    warning, = cancellation.take_warnings()
    assert "NtResumeProcess" in warning


@pytest.mark.parametrize("termination_raises", [False, True])
def test_windows_probe_finishes_exited_wrapper_and_closes_the_job_once(windows, termination_raises):
    cancellation = quota_probes.ProbeCancellation()
    proc = cancellation.spawn(COMMAND)
    proc.returncode = 0
    if termination_raises:
        windows.kernel.TerminateJobObject.side_effect = OSError("termination failed")
    cancellation.finish(proc)
    cancellation.finish(proc)
    cancellation.stop()

    assert windows.kernel.TerminateJobObject.call_count == 1
    windows.kernel.CloseHandle.assert_called_once_with(JOB_HANDLE)
    windows.proc.kill.assert_not_called()


def test_windows_text_probe_timeout_terminates_and_closes_job(windows):
    windows.proc.communicate.side_effect = subprocess.TimeoutExpired(COMMAND, WAIT_SECONDS)
    cancellation = quota_probes.ProbeCancellation()
    with pytest.raises(subprocess.TimeoutExpired):
        quota_probes.run_text_probe(COMMAND, timeout=WAIT_SECONDS, cancellation=cancellation)
    cancellation.close()

    assert windows.events[-3:] == ["terminate", "close", "wait"]
    windows.kernel.CloseHandle.assert_called_once_with(JOB_HANDLE)


@pytest.mark.platform_sensitive
@pytest.mark.skipif(os.name != "nt", reason="requires real Windows Job Objects")
def test_windows_probe_cancel_kills_child_after_wrapper_exit_and_reaches_eof():
    child_code = f"import os, time; print(os.getpid(), flush=True); time.sleep({CHILD_SLEEP_SECONDS})"
    wrapper_code = (
        "import subprocess, sys; sys.stdin.readline(); "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}], stdin=subprocess.DEVNULL, stdout=sys.stdout)"
    )
    cancellation = quota_probes.ProbeCancellation()
    child = None
    proc = cancellation.spawn([sys.executable, "-c", wrapper_code], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    assert proc.stdin is not None and proc.stdout is not None
    lines = Queue()

    def read_pipe():
        for line in proc.stdout:
            lines.put(line)
        lines.put("")

    reader = Thread(target=read_pipe, daemon=True)
    try:
        warnings = cancellation.take_warnings()
        if any("AssignProcessToJobObject" in warning for warning in warnings):
            pytest.skip("; ".join(warnings))
        assert not warnings
        reader.start()
        proc.stdin.write("\n")
        proc.stdin.close()
        child = psutil.Process(int(lines.get(timeout=WAIT_SECONDS)))
        assert proc.wait(timeout=WAIT_SECONDS) == 0
        assert child.is_running() and lines.empty()

        cancellation.stop()

        child.wait(timeout=WAIT_SECONDS)
        assert not child.is_running()
        assert lines.get(timeout=WAIT_SECONDS) == ""
    finally:
        cancellation.stop()
        if child is not None and child.is_running():
            child.kill()
            child.wait(timeout=WAIT_SECONDS)
        if reader.ident is not None:
            reader.join(WAIT_SECONDS)
        proc.stdin.close()
        if not reader.is_alive():
            proc.stdout.close()


def test_a_cancel_during_job_setup_never_resumes_the_probe(windows):
    cancellation = quota_probes.ProbeCancellation()

    def stop_while_setting_up(*args, **kwargs):
        windows.events.append("set")
        cancellation._stopped.set()  # stop() sets the event before it waits for the lock this spawn holds
        return True

    windows.kernel.SetInformationJobObject.side_effect = stop_while_setting_up
    with pytest.raises(OSError, match="cancelled"):
        cancellation.spawn(COMMAND)

    assert "resume" not in windows.events
    assert "terminate" in windows.events and windows.events.count("close") == 1
    windows.proc.kill.assert_called()

