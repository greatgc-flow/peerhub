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

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from peerhub.extensions.adapters import CliRuntimeTarget
from peerhub.extensions.adapters.process import BoundedProcess, ProcessResult
from peerhub.extensions.bridge import RuntimeTargetError
from peerhub.extensions.process_tree import kill_process_tree
from peerhub.extensions.runtime_port import ProcessRuntimeAdapter, RuntimeCancellationError, RuntimeCapability
from tests.communication.bridge_helpers import bseed, delivery_rows, kinds
from tests.communication.harness.bridge import BridgeHarness

PY = sys.executable


def _record(body: str = "test prompt", rid: str = "rec-1"):
    return SimpleNamespace(record_id=rid, body=body, author_peer_id="a")


def _drain(gen):
    return list(gen)


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

    # Inject failure into kill_process_tree
    monkeypatch.setattr(proc_module, "kill_process_tree", lambda p: False)

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
