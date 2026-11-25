"""Tests for process-tree termination confirmation propagation (PR 1).

Covers:
- Failed taskkill (injected) -> termination_confirmed is False
- Dead parent with surviving child -> termination_confirmed is False (UNCONFIRMED)
- Normal exit -> termination_confirmed is None (not attempted)
- Timeout with confirmed kill -> termination_confirmed is True
- Abort with confirmed kill -> termination_confirmed is True
- Durable bridge evidence carries termination_confirmed flag in detail JSON
- runtime_port does not swallow returncode 128 (dead parent) as confirmed cancellation
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import SimpleNamespace
from unittest.mock import Mock, call

import psutil
import pytest

pytestmark = pytest.mark.platform_sensitive

from peerhub.extensions.adapters import CliRuntimeTarget, cli_runtime, process
from peerhub.extensions.adapters.process import BoundedProcess, ProcessResult
from peerhub.extensions.runtime_contract import PrespawnError, RuntimeTargetError
from peerhub.extensions.process_tree import kill_process_tree
from peerhub.extensions.runtime_port import ProcessRuntimeAdapter, RuntimeCancellationError, RuntimeCapability


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX launch gate")
@pytest.mark.parametrize("approved", [False, True])
def test_supervised_process_only_execs_after_approval(approved):
    # Close the gate's writer by exiting the wrapper, exactly as a wrapper crash does.
    wrapper = textwrap.dedent("""
        import os
        import sys
        from peerhub.extensions.adapters.process import BoundedProcess

        provider = [sys.executable, "-c",
                    "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('executed')",
                    sys.argv[2]]
        process = BoundedProcess(provider, stdin=None, cwd=os.getcwd(), env=os.environ,
                                 timeout_s=10, max_bytes=1024)
        process.start(suspended=True)
        print(process.pid, flush=True)
        if sys.argv[1] == "True":
            process.release_launch()
            assert process.wait().returncode == 0
        else:
            os._exit(17)
    """)
    with NamedTemporaryFile() as file:
        result = subprocess.run([sys.executable, "-c", wrapper, str(approved), file.name],
                                capture_output=True, timeout=15)
        assert result.returncode == (0 if approved else 17), result.stderr
        if not approved:
            pid = int(result.stdout)
            deadline = time.monotonic() + 5
            while True:
                try:
                    if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                        break
                except psutil.NoSuchProcess:
                    break
                assert time.monotonic() < deadline, "launch process did not exit after losing its gate"
                time.sleep(0.01)
        assert Path(file.name).read_text() == ("executed" if approved else "")
from tests.communication.bridge_helpers import bseed, delivery_rows, kinds
from tests.communication.harness.bridge import BridgeHarness

PY = sys.executable


def _record(body: str = "test prompt", rid: str = "rec-1"):
    return SimpleNamespace(record_id=rid, body=body, author_peer_id="a")


def _drain(gen):
    return list(gen)


@pytest.mark.parametrize("fail_at", [1, 3])
def test_worker_start_failure_kills_and_reaps_child(monkeypatch, fail_at):
    calls = Mock()
    child = SimpleNamespace(pid=4242, poll=lambda: None, wait=calls.wait,
                            stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO())
    calls.kill_tree.return_value = True
    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(process, "kill_process_tree", calls.kill_tree)
    original_start = threading.Thread.start
    starts = 0

    def start(thread):
        nonlocal starts
        starts += 1
        if starts == fail_at:
            raise RuntimeError("thread start failed")
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    target = CliRuntimeTarget("cc", ".", command=["fake-cc"])
    session = target.create_session()
    with pytest.raises(RuntimeTargetError, match="thread start failed") as failure:
        next(iter(target.deliver(session, _record(), [])))
    assert not isinstance(failure.value, PrespawnError)
    assert failure.value.termination_confirmed is True
    assert calls.mock_calls == [call.kill_tree(child), call.wait(timeout=10)]
    assert session not in target._active


def test_popen_failure_stays_prespawn(monkeypatch):
    def popen(*args, **kwargs):
        raise FileNotFoundError("fake executable missing")

    monkeypatch.setattr(process.subprocess, "Popen", popen)
    target = CliRuntimeTarget("cc", ".", command=["fake-cc"])
    with pytest.raises(PrespawnError, match="fake executable missing"):
        _drain(target.deliver(target.create_session(), _record(), []))


def test_unverified_supervised_process_is_uncertain_and_stays_behind_the_gate(monkeypatch):
    runner = Mock(pid=4242)
    publish = Mock()
    monkeypatch.setattr(cli_runtime, "BoundedProcess", lambda *a, **kw: runner)
    monkeypatch.setattr(cli_runtime, "process_created", lambda pid: None)
    monkeypatch.setattr(cli_runtime, "capture_process_identity", lambda pid: None)
    target = CliRuntimeTarget("cc", ".", command=["fake-cc"])
    session = target.create_session()

    with pytest.raises(RuntimeTargetError, match="could not verify the supervised launch process") as failure:
        _drain(target.deliver(session, _record(), [], publish_launch=publish))
    assert not isinstance(failure.value, PrespawnError)
    runner.start.assert_called_once_with(suspended=True)
    publish.assert_not_called()
    runner.release_launch.assert_not_called()
    runner.cancel_launch.assert_called_once_with()
    runner.abort.assert_called_once_with()
    assert session not in target._active


def test_broken_launch_gate_does_not_prove_exec_was_rejected(monkeypatch):
    close = Mock()
    monkeypatch.setattr(process, "os", SimpleNamespace(write=Mock(side_effect=BrokenPipeError), close=close))
    runner = BoundedProcess(["fake-cc"], stdin=None, cwd=".", env={})
    runner._launch_gate, runner._launch_status = 101, 102
    with pytest.raises(BrokenPipeError):
        runner.release_launch()
    assert close.call_args_list == [call(101), call(102)]


@pytest.mark.parametrize("kind,stdout", [
    ("cc", b'{"type":"result","subtype":"success","is_error":false,"result":"OK"}\n'),
    ("cx", b'{"type":"item.completed","item":{"type":"agent_message","text":"OK"}}\n'
           b'{"type":"turn.completed"}\n'),
])
@pytest.mark.parametrize("state", ["complete", "short", "broken"])
def test_stdin_must_be_complete_before_zero_exit_can_deliver(monkeypatch, kind, stdout, state):
    child = SimpleNamespace(pid=4242, poll=lambda: 0, wait=lambda **kwargs: 0, returncode=0,
                            stdin=io.BytesIO(), stdout=io.BytesIO(stdout), stderr=io.BytesIO())
    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(cli_runtime, "process_created", lambda pid: None)
    writes: list[bytes] = []

    def write_some(pipe, view):
        writes.append(bytes(view))
        if len(writes) == 1:
            return 4
        if state == "broken":
            raise BrokenPipeError
        return len(view) if state == "complete" else 0

    monkeypatch.setattr(process, "_write_some", write_some)
    target = CliRuntimeTarget(kind, ".", command=["fake-cli"])
    events = iter(target.deliver(target.create_session(), _record(), []))
    assert next(events)[0] == "started"
    if state == "complete":
        terminal, = events
        assert terminal[0] == "terminal"
        assert terminal[1]["response"] == "OK"
    else:
        with pytest.raises(RuntimeTargetError, match=rf"stdin write incomplete \({state}, 4/11\)") as failure:
            next(events)
        assert not isinstance(failure.value, PrespawnError)
    assert writes == [b"test prompt", b" prompt"]


def test_normal_exit_termination_confirmed_is_none(tmp_path):
    """Normal exit within deadline: kill never attempted, termination_confirmed is None."""
    proc = BoundedProcess(
        [PY, "-c", "import sys; sys.stdout.write('hello'); sys.exit(0)"],
        stdin=None,
        cwd=str(tmp_path),
        env={},
        timeout_s=10.0,
    ).start()
    res = proc.wait()
    assert res.returncode == 0
    assert not res.timed_out
    assert not res.aborted
    assert res.termination_confirmed is None


def test_timeout_with_confirmed_kill(tmp_path, bridge_h):
    """Timeout with successful whole-tree kill sets termination_confirmed=True in ProcessResult and bridge evidence."""
    # 1. BoundedProcess direct assertion
    proc = BoundedProcess(
        [PY, "-c", "import time; time.sleep(30)"],
        stdin=None,
        cwd=str(tmp_path),
        env={},
        timeout_s=0.2,
    ).start()
    res = proc.wait()
    assert res.timed_out is True
    assert res.termination_confirmed is True

    # 2. Bridge cycle assertion
    ws = tmp_path / "ws_target"
    ws.mkdir()
    target = CliRuntimeTarget(
        "cc",
        ws,
        command=[PY, "-c", "import time; time.sleep(30)"],
        timeout_s=0.2,
    )
    (rec,) = bseed(bridge_h, stream="s_to")
    result = bridge_h.delivery_cycle("b", "s_to", target)
    assert result.status == "uncertain"
    assert result.certainty == "STARTED"

    ev = bridge_h.execution_evidence(rec.record_id, "b")
    events = ev["events"]
    timeout_evs = [e for e in events if e["kind"] == "runtime_timeout"]
    assert len(timeout_evs) == 1
    assert timeout_evs[0]["detail"].get("termination_confirmed") is True

    no_term_evs = [e for e in events if e["kind"] == "no_terminal"]
    assert len(no_term_evs) == 1
    assert no_term_evs[0]["detail"].get("termination_confirmed") is True


def test_failed_taskkill_injected_termination_unconfirmed(tmp_path, bridge_h, monkeypatch):
    """When taskkill / kill_process_tree fails, termination_confirmed is False in ProcessResult and bridge evidence."""
    import peerhub.extensions.adapters.process as proc_module

    # Inject a tree kill that reports failure. It still kills and reaps the child first: a live child would block
    # BoundedProcess.wait in stdout.close() (the pump thread holds the pipe in read1) until sleep(30) ends.
    # A failed kill result is never confirmed, so the asserted False does not depend on timing.
    kills = []

    def failed_kill(p):
        kills.append(p.pid)
        p.kill()
        p.wait(timeout=10)
        return False

    monkeypatch.setattr(proc_module, "kill_process_tree", failed_kill)

    proc = None
    target = None
    try:
        # 1. BoundedProcess direct assertion
        proc = BoundedProcess(
            [PY, "-c", "import time; time.sleep(30)"],
            stdin=None,
            cwd=str(tmp_path),
            env={},
            timeout_s=0.2,
        ).start()
        res = proc.wait()
        assert res.timed_out is True
        assert res.termination_confirmed is False
        assert kills == [res.pid]

        # 2. Bridge cycle assertion
        ws = tmp_path / "ws_fail"
        ws.mkdir()
        target = CliRuntimeTarget(
            "cc",
            ws,
            command=[PY, "-c", "import time; time.sleep(30)"],
            timeout_s=0.2,
        )
        (rec,) = bseed(bridge_h, stream="s_fail")
        result = bridge_h.delivery_cycle("b", "s_fail", target)
        assert result.status == "uncertain"
        assert result.certainty == "STARTED"

        ev = bridge_h.execution_evidence(rec.record_id, "b")
        events = ev["events"]
        timeout_evs = [e for e in events if e["kind"] == "runtime_timeout"]
        assert len(timeout_evs) == 1
        assert timeout_evs[0]["detail"].get("termination_confirmed") is False

        no_term_evs = [e for e in events if e["kind"] == "no_terminal"]
        assert len(no_term_evs) == 1
        assert no_term_evs[0]["detail"].get("termination_confirmed") is False
        assert len(kills) == 2
    finally:
        for p_to_kill in (getattr(proc, "_proc", None),):
            if p_to_kill is not None and p_to_kill.poll() is None:
                p_to_kill.kill()
                p_to_kill.wait(timeout=1)
        if target is not None:
            for act in list(target._active.values()):
                if act._proc is not None and act._proc.poll() is None:
                    act._proc.kill()
                    act._proc.wait(timeout=1)


def test_dead_parent_with_surviving_child_unconfirmed(tmp_path, bridge_h):
    """When a parent process exits while a child survives, termination is UNCONFIRMED (False)."""
    pid_file = tmp_path / "child.pid"
    script = textwrap.dedent(f"""
        import subprocess, sys, time
        # Spawn child that will survive parent exit
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open({repr(str(pid_file))}, "w") as f:
            f.write(str(child.pid))
        # Parent exits immediately
        sys.exit(0)
    """)

    child_pid = None
    try:
        proc = BoundedProcess(
            [PY, "-c", script],
            stdin=None,
            cwd=str(tmp_path),
            env={},
            timeout_s=10.0,
        ).start()

        # Wait for pid_file to be written by the parent
        for _ in range(50):
            if pid_file.exists():
                break
            time.sleep(0.05)
        assert pid_file.exists(), "child.pid was not created"
        child_pid = int(pid_file.read_text().strip())

        # Give the parent process a moment to exit
        time.sleep(0.2)
        assert proc._proc is not None and proc._proc.poll() is not None, "parent should have exited"

        # Now abort the process; since parent exited while child survived, termination must be UNCONFIRMED
        proc.abort()
        res = proc.wait()
        assert res.termination_confirmed is False
    finally:
        # Clean up child process if still alive
        if child_pid is not None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(child_pid)], capture_output=True, check=False)
            else:
                try:
                    os.kill(child_pid, 9)
                except OSError:
                    pass


def test_abort_with_confirmed_kill(tmp_path, bridge_h):
    """When aborting a running process tree and kill succeeds, termination_confirmed is True."""
    # 1. BoundedProcess direct assertion
    proc = BoundedProcess(
        [PY, "-c", "import time; time.sleep(30)"],
        stdin=None,
        cwd=str(tmp_path),
        env={},
        timeout_s=30.0,
    ).start()
    time.sleep(0.1)
    proc.abort()
    res = proc.wait()
    assert res.aborted is True
    assert res.termination_confirmed is True

    # 2. CliRuntimeTarget abort on generator close
    ws = tmp_path / "ws_abort"
    ws.mkdir()
    target = CliRuntimeTarget(
        "cc",
        ws,
        command=[PY, "-c", "import time; time.sleep(30)"],
        timeout_s=30.0,
    )
    sess = target.create_session()
    gen = target.deliver(sess, _record(), [])
    ev0 = next(gen)
    assert ev0[0] == "started"
    # Closing generator triggers finally block -> proc.abort()
    gen.close()
    aborted_note = next(e for e in target.evidence if e["event"] == "aborted")
    assert aborted_note.get("termination_confirmed") is True

    # 3. CliRuntimeTarget abort via target.terminate() while waiting in deliver
    ws2 = tmp_path / "ws_term"
    ws2.mkdir()
    target2 = CliRuntimeTarget(
        "cc",
        ws2,
        command=[PY, "-c", "import time; time.sleep(30)"],
        timeout_s=30.0,
    )
    sess2 = target2.create_session()
    gen2 = target2.deliver(sess2, _record(), [])
    ev0 = next(gen2)
    assert ev0[0] == "started"
    target2.terminate(sess2)
    with pytest.raises(RuntimeTargetError) as exc_info:
        next(gen2)
    assert exc_info.value.termination_confirmed is True
    exited_note = next(e for e in target2.evidence if e["event"] == "exited")
    assert exited_note.get("termination_confirmed") is True
    term_note = next(e for e in target2.evidence if e["event"] == "terminated")
    assert term_note.get("termination_confirmed") is True


class _BlockedPipe:
    """read1 and raw.write block until released; close() during a blocked call is a #367 violation."""

    def __init__(self, name: str, release: threading.Event, violations: list[str]) -> None:
        self.name, self.release, self.violations = name, release, violations
        self.busy = False
        self.raw = self

    def _block(self) -> None:
        self.busy = True
        try:
            self.release.wait()
        finally:
            self.busy = False

    def read1(self, n: int) -> bytes:
        self._block()
        return b""

    def write(self, view) -> int:
        self._block()
        raise BrokenPipeError("released")

    def close(self) -> None:
        if self.busy:
            self.violations.append(self.name)


class _StuckPopen:
    def __init__(self, returncode, release: threading.Event, violations: list[str]) -> None:
        self.pid, self.returncode = 4242, returncode
        self.stdin, self.stdout, self.stderr = (_BlockedPipe(n, release, violations) for n in ("stdin", "stdout", "stderr"))

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("stuck", timeout)
        return self.returncode


@pytest.mark.parametrize("returncode,after_kill", [(None, None), (None, 0), (0, None)],
                         ids=["never-exits", "exits-0-after-failed-kill", "exits-0-on-its-own"])
def test_failed_kill_with_blocked_pipes_is_bounded_and_never_closes_a_blocked_pipe(tmp_path, bridge_h, monkeypatch,
                                                                                    returncode, after_kill):
    """#366 B1 test 16 / #367: wait() is bounded by timeout + reap (10 s) + 5 s and never closes a pipe in use.

    The kill always reports failure; `after_kill` is the exit code the main process then has (None: still running).
    """
    import peerhub.extensions.adapters.process as proc_module

    def failed_kill(p):
        p.returncode = after_kill
        return False

    release, violations = threading.Event(), []
    monkeypatch.setattr(proc_module.subprocess, "Popen", lambda *a, **k: _StuckPopen(returncode, release, violations))
    monkeypatch.setattr(proc_module, "kill_process_tree", failed_kill)
    # Popen is a fake, so there is no launcher PID or child reading the approval pipe.
    monkeypatch.setattr(cli_runtime, "process_created", lambda pid: 123.0)
    monkeypatch.setattr(cli_runtime, "capture_process_identity",
                        lambda pid: "linux:fake-boot:1" if sys.platform.startswith("linux") else None)
    approve = Mock()
    monkeypatch.setattr(BoundedProcess, "release_launch", approve)
    timeout_s = 0.5
    bound = timeout_s + 10 + 5 + 2
    ws = tmp_path / "ws_stuck"
    ws.mkdir()
    try:
        t0 = time.monotonic()
        res = BoundedProcess([PY, "-c", "pass"], stdin=b"prompt bytes", cwd=str(ws), env={}, timeout_s=timeout_s).start().wait()
        assert time.monotonic() - t0 <= bound
        assert res.drain_timed_out is True
        assert res.timed_out is (returncode is None)
        # A kill was attempted and failed: never confirmed, whether or not the main process then exited.
        assert res.termination_confirmed is (False if returncode is None else None)
        assert (res.stdin_state, res.stdin_bytes_written) == ("pending", 0)

        target = CliRuntimeTarget("cc", ws, command=[PY, "-c", "pass"], timeout_s=timeout_s)
        (rec,) = bseed(bridge_h)
        t0 = time.monotonic()
        result = bridge_h.delivery_cycle("b", "s", target)
        assert time.monotonic() - t0 <= bound
        assert (result.status, result.certainty) == ("uncertain", "STARTED")
        assert approve.call_count == (0 if sys.platform == "win32" else 1)
        details = [e["detail"] for e in bridge_h.execution_evidence(rec.record_id, "b")["events"]
                   if e["kind"] in ("runtime_timeout", "runtime_error")]
        assert details and all(d.get("termination_confirmed") is not True for d in details)
        if returncode is None:
            assert details[0]["termination_confirmed"] is False
        assert violations == []
    finally:
        release.set()


def test_runtime_port_dead_parent_taskkill_128_not_swallowed(monkeypatch):
    """ProcessRuntimeAdapter._kill_tree must not swallow taskkill 128 as confirmed cancellation."""
    from peerhub.extensions import runtime_port

    # 1. Direct _kill_tree test: dead parent (proc.poll() is not None) + taskkill 128
    # Replace this module's OS binding without changing the host OS for launchers or pytest.
    monkeypatch.setattr(runtime_port, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        runtime_port.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=128),
    )
    dead_proc = SimpleNamespace(pid=123, poll=lambda: 0, wait=lambda timeout=None: 0)
    with pytest.raises(OSError):
        runtime_port.ProcessRuntimeAdapter._kill_tree(dead_proc)



def test_runtime_port_failed_kill_not_confirmed(monkeypatch):
    """A failed injected tree kill cannot produce confirmed cancellation on any OS."""
    adapter = ProcessRuntimeAdapter(cancel_wait_seconds=0.3)
    cap = RuntimeCapability("c1", "c1", "test")
    adapter.register_command(cap, [PY, "-c", "import time; time.sleep(60)"])
    proc = SimpleNamespace(poll=lambda: None, wait=lambda timeout=None: 0)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: proc)
    job = adapter.submit("c1", {})
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                ProcessRuntimeAdapter,
                "_kill_tree",
                staticmethod(lambda proc: (_ for _ in ()).throw(OSError("taskkill exited 128"))),
            )
            with pytest.raises(RuntimeCancellationError):
                adapter.cancel(job.job_id)
            assert adapter.get(job.job_id).status == "RUNNING"
    finally:
        # Settle the fake process and remove its output file even if an assertion fails.
        monkeypatch.setattr(ProcessRuntimeAdapter, "_kill_tree", staticmethod(lambda proc: None))
        adapter.cancel(job.job_id)


@pytest.mark.parametrize("platform, os_name", [("linux", "posix"), ("win32", "nt")])
def test_runtime_port_launch_without_windows_process_group_constant(monkeypatch, platform, os_name):
    """Linux needs a private session; a missing Windows constant must not raise AttributeError."""
    from peerhub.extensions import runtime_port

    adapter = ProcessRuntimeAdapter()
    adapter.register_command(RuntimeCapability("c1", "c1", "test"), [PY, "-c", "pass"])
    launched = {}
    proc = SimpleNamespace(poll=lambda: None, wait=lambda timeout=None: 0)

    def launch(argv, **kwargs):
        launched.update(kwargs)
        return proc

    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", platform)
        patch.delattr(subprocess, "CREATE_NEW_PROCESS_GROUP", raising=False)
        patch.setattr(runtime_port, "os", SimpleNamespace(name=os_name, close=os.close, unlink=os.unlink))
        patch.setattr(subprocess, "Popen", launch)
        patch.setattr(ProcessRuntimeAdapter, "_kill_tree", staticmethod(lambda proc: None))
        job = adapter.submit("c1", {})
        try:
            assert job.status == "RUNNING"
            if os_name == "posix":
                assert launched["start_new_session"] is True
                assert "creationflags" not in launched
            else:
                assert launched["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
                assert "start_new_session" not in launched
        finally:
            adapter.cancel(job.job_id)
