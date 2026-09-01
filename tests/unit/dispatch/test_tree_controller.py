"""Unit tests for RealTreeController using trivial hermetic test subprocesses.

Covers the 5 required test cases:
1. bind_spawn + observe_tree on a freshly spawned trivial process reports RUNNING.
2. kill_tree on a running trivial process actually terminates process; observe_tree reports TERMINATED.
3. soft_cancel on a signal-ignoring process reports receipt / process stays RUNNING for ladder escalation.
4. observe_tree on a self-exited process reports TERMINATED without error.
5. Identity verification with mismatched creation time reports IDENTITY_UNCERTAIN; signal methods refuse action.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from peerhub.dispatch.contract import ProcessBirthIdentity
from peerhub.dispatch.process import ObservationState
from peerhub.dispatch.tree_controller import RealTreeController, _get_process_creation_time_ms


@pytest.fixture
def controller() -> RealTreeController:
    return RealTreeController()


def test_bind_spawn_and_observe_running_process(controller: RealTreeController) -> None:
    """bind_spawn + observe_tree on a freshly spawned trivial process reports RUNNING."""
    # Spawn a python process that sleeps for 10 seconds
    cmd = [sys.executable, "-c", "import time; time.sleep(10)"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        creation_time = _get_process_creation_time_ms(proc.pid)
        identity = ProcessBirthIdentity(pid=proc.pid, process_creation_time=creation_time)
        handle = controller.bind_spawn(process=proc, root=identity)

        observations = controller.observe_tree(handle)
        assert len(observations) >= 1
        assert observations[0].state is ObservationState.RUNNING
        assert observations[0].identity.pid == proc.pid
    finally:
        proc.kill()
        proc.wait()


def test_kill_tree_terminates_running_process(controller: RealTreeController) -> None:
    """kill_tree on a running process actually terminates it; observe_tree reports TERMINATED."""
    cmd = [sys.executable, "-c", "import time; time.sleep(10)"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        creation_time = _get_process_creation_time_ms(proc.pid)
        identity = ProcessBirthIdentity(pid=proc.pid, process_creation_time=creation_time)
        handle = controller.bind_spawn(process=proc, root=identity)

        # Verify running first
        assert controller.observe_tree(handle)[0].state is ObservationState.RUNNING

        receipt = controller.kill_tree(handle)
        assert receipt.dispatched is True

        proc.wait(timeout=5)
        observations = controller.observe_tree(handle)
        assert observations[0].state is ObservationState.TERMINATED
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_kill_by_identity_terminates_single_root_process(
    controller: RealTreeController,
) -> None:
    """Persisted identity cleanup kills one root without a live tree handle."""

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32"
            else 0
        ),
    )
    try:
        identity = ProcessBirthIdentity(
            pid=proc.pid,
            process_creation_time=_get_process_creation_time_ms(proc.pid),
        )

        receipt = controller.kill_by_identity(identity)

        assert receipt.dispatched is True
        assert receipt.target_identities == (identity,)
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_soft_cancel_on_signal_ignoring_process(controller: RealTreeController) -> None:
    """soft_cancel on a process ignoring signals leaves process RUNNING so ladder can escalate."""
    # Script that ignores SIGINT / CTRL_BREAK
    script = (
        "import signal, time, sys, ctypes; "
        "signal.signal(signal.SIGINT, signal.SIG_IGN) if hasattr(signal, 'SIGINT') else None; "
        "handler = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint32)(lambda ctrl: 1) if sys.platform == 'win32' else None; "
        "ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True) if sys.platform == 'win32' else None; "
        "print('READY'); sys.stdout.flush(); "
        "time.sleep(10)"
    )
    cmd = [sys.executable, "-c", script]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        import threading
        import queue
        
        q = queue.Queue()
        def _read_stdout():
            if proc.stdout:
                q.put(proc.stdout.readline())
                
        t = threading.Thread(target=_read_stdout, daemon=True)
        t.start()
        try:
            line = q.get(timeout=5.0)
            assert line.decode('utf-8', errors='ignore').strip() == 'READY'
        except queue.Empty:
            pytest.fail("Child process did not become READY within 5s")

        creation_time = _get_process_creation_time_ms(proc.pid)
        identity = ProcessBirthIdentity(pid=proc.pid, process_creation_time=creation_time)
        handle = controller.bind_spawn(process=proc, root=identity)

        receipt = controller.soft_cancel(handle)
        time.sleep(0.2)

        # Process is still running despite soft_cancel call
        observations = controller.observe_tree(handle)
        assert observations[0].state is ObservationState.RUNNING

        # Escalation to kill_tree succeeds
        kill_receipt = controller.kill_tree(handle)
        assert kill_receipt.dispatched is True
        proc.wait(timeout=5)
        assert controller.observe_tree(handle)[0].state is ObservationState.TERMINATED
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_observe_tree_on_self_exited_process(controller: RealTreeController) -> None:
    """observe_tree after process exits on its own reports TERMINATED and does not error."""
    cmd = [sys.executable, "-c", "import time; time.sleep(0.05)"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    creation_time = _get_process_creation_time_ms(proc.pid)
    identity = ProcessBirthIdentity(pid=proc.pid, process_creation_time=creation_time)
    handle = controller.bind_spawn(process=proc, root=identity)

    proc.wait(timeout=5)
    observations = controller.observe_tree(handle)
    assert len(observations) >= 1
    assert observations[0].state is ObservationState.TERMINATED


def test_identity_verification_mismatched_creation_time(controller: RealTreeController) -> None:
    """Mismatched creation time reports IDENTITY_UNCERTAIN and signal methods refuse action."""
    cmd = [sys.executable, "-c", "import time; time.sleep(10)"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        mismatched_identity = ProcessBirthIdentity(
            pid=proc.pid,
            process_creation_time=9999999999999,  # Mismatched creation time
        )
        handle = controller.bind_spawn(process=proc, root=mismatched_identity)

        # observe_tree returns IDENTITY_UNCERTAIN
        observations = controller.observe_tree(handle)
        assert observations[0].state is ObservationState.IDENTITY_UNCERTAIN

        # Signal methods refuse action due to failed identity verification
        soft_receipt = controller.soft_cancel(handle)
        assert soft_receipt.dispatched is False

        term_receipt = controller.terminate_tree(handle)
        assert term_receipt.dispatched is False

        kill_receipt = controller.kill_tree(handle)
        assert kill_receipt.dispatched is False

        identity_receipt = controller.kill_by_identity(
            mismatched_identity
        )
        assert identity_receipt.dispatched is False

        # Process is still running because signal methods refused to signal mismatched PID
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()


def test_windows_job_object_kill_on_close_orphaned_child() -> None:
    """Regression test for orphaned child processes on Windows abrupt termination."""
    if sys.platform != "win32":
        pytest.skip("Test requires Windows Job Objects")

    import textwrap
    import ctypes

    # We spawn a parent script. The parent script spawns a child script using RealTreeController.
    # The child script sleeps indefinitely. The parent script prints "READY <child_pid>" and waits.
    # The test kills the parent script abruptly using TerminateProcess.
    # We then verify the child process is dead.

    parent_script = textwrap.dedent("""
        import sys
        import time
        import subprocess
        from peerhub.dispatch.tree_controller import RealTreeController, _get_process_creation_time_ms
        from peerhub.dispatch.contract import ProcessBirthIdentity

        child_cmd = [sys.executable, "-c", "import time; time.sleep(100)"]
        proc = subprocess.Popen(child_cmd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        
        creation_time = _get_process_creation_time_ms(proc.pid)
        identity = ProcessBirthIdentity(pid=proc.pid, process_creation_time=creation_time)
        
        controller = RealTreeController()
        handle = controller.bind_spawn(process=proc, root=identity)
        
        # Let the test know we are ready and give it the child PID
        print(f"READY {proc.pid}")
        sys.stdout.flush()
        
        time.sleep(100)
    """)

    parent_proc = subprocess.Popen(
        [sys.executable, "-c", parent_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    
    try:
        child_pid = -1
        if parent_proc.stdout:
            line = parent_proc.stdout.readline().decode('utf-8').strip()
            assert line.startswith("READY"), f"Unexpected output: {line}"
            child_pid = int(line.split(" ")[1])
            
        assert child_pid > 0

        # Terminate parent abruptly using TerminateProcess (equivalent to taskkill /F)
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h_proc = kernel32.OpenProcess(0x0001, False, parent_proc.pid)
        if h_proc:
            kernel32.TerminateProcess(h_proc, 1)
            kernel32.CloseHandle(h_proc)
        
        parent_proc.wait(timeout=5)
        
        # Check if child process is still alive. It should be automatically terminated by Job Object.
        time.sleep(1.0) # Wait a moment for OS cleanup
        
        h_child = kernel32.OpenProcess(0x1000, False, child_pid) # PROCESS_QUERY_LIMITED_INFORMATION
        if not h_child:
            h_child = kernel32.OpenProcess(0x0400, False, child_pid) # PROCESS_QUERY_INFORMATION
            
        if h_child:
            exit_code = ctypes.c_uint32()
            res = kernel32.GetExitCodeProcess(h_child, ctypes.byref(exit_code))
            kernel32.CloseHandle(h_child)
            if res:
                # 259 is STILL_ACTIVE
                assert exit_code.value != 259, f"Child process {child_pid} is still running (orphaned)!"
        
    finally:
        if parent_proc.poll() is None:
            parent_proc.kill()
            parent_proc.wait()
