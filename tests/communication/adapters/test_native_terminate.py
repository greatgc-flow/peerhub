"""Native CLI cancellation: real process trees and honest failure evidence."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from peerhub.extensions.adapters import CliRuntimeTarget, process
from peerhub.extensions.bridge import RuntimeTargetError

pytestmark = pytest.mark.unit


def alive(pid):
    if sys.platform == "win32":
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True)
        return f" {pid} ".encode() in result.stdout
    try:
        os.kill(pid, 0)
        # An orphan zombie has exited, even if the host's init has not reaped it yet.
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists() and stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
            return False
        return True
    except (OSError, ProcessLookupError):
        return False


def eventually(predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        assert time.monotonic() < deadline, "process observation deadline exceeded"
        time.sleep(0.02)


def test_terminate_kills_real_child_and_grandchild_and_aborts_delivery(tmp_path):
    ready = tmp_path / "tree.json"
    child = (
        "import json,os,subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
        f"Path({str(ready)!r}).write_text(json.dumps([os.getpid(),p.pid])); "
        "time.sleep(120)"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
        # Valid vendor success output must still never become a response after cancellation.
        "print('{\"type\":\"result\",\"is_error\":false,\"result\":\"OK\"}',flush=True); "
        "time.sleep(120)"
    )
    adapter = CliRuntimeTarget("cc", tmp_path, command=[sys.executable, "-c", parent], timeout_s=30)
    delivery = adapter.deliver("session", SimpleNamespace(body="question"), [])
    events = [next(delivery)]
    root = int(events[0][1].split(":")[1])

    def consume():
        for event in delivery:
            events.append(event)

    try:
        eventually(lambda: ready.exists() and bool(ready.read_text()))
        descendants = json.loads(ready.read_text())
        assert all(alive(pid) for pid in [root, *descendants])
        with ThreadPoolExecutor(max_workers=1) as worker:
            result = worker.submit(consume)
            adapter.terminate("session")
            assert not alive(root)  # terminate waits for the root's observed exit
            with pytest.raises(RuntimeTargetError, match="aborted"):
                result.result(timeout=20)
        eventually(lambda: all(not alive(pid) for pid in descendants))
        assert [event[0] for event in events] == ["started"]
        assert not adapter._active
        with pytest.raises(RuntimeTargetError, match="^nothing running$"):
            adapter.terminate("session")
    finally:
        delivery.close()


def test_terminate_missing_session_does_not_kill_another_delivery(tmp_path):
    adapter = CliRuntimeTarget("cx", tmp_path,
                               command=[sys.executable, "-c", "import time; time.sleep(120)"], timeout_s=30)
    delivery = adapter.deliver("live", SimpleNamespace(body="question"), [])
    started = next(delivery)
    pid = int(started[1].split(":")[1])
    try:
        assert adapter.supports_terminate
        assert not adapter.supports_interrupt and not adapter.supports_steer
        with pytest.raises(RuntimeTargetError, match="^nothing running$"):
            adapter.terminate("missing")
        assert alive(pid)
    finally:
        delivery.close()
    assert not alive(pid) and not adapter._active


@pytest.mark.parametrize("failure", ["kill", "exit"])
def test_terminate_unconfirmed_death_raises_runtime_error(tmp_path, monkeypatch, failure):
    adapter = CliRuntimeTarget("cc", tmp_path, command=[sys.executable])
    runner = process.BoundedProcess([sys.executable], stdin=None, cwd=str(tmp_path), env=os.environ)

    def wait(timeout):
        if failure == "exit":
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    runner._proc = SimpleNamespace(pid=123, poll=lambda: None, wait=wait)
    adapter._active["session"] = runner
    monkeypatch.setattr(process, "_kill_tree", lambda proc: failure != "kill")
    with pytest.raises(RuntimeTargetError, match="could not be confirmed"):
        adapter.terminate("session")
    assert runner._aborted.is_set()
    assert not any(event["event"] == "terminated" for event in adapter.evidence)


def test_aborted_zero_exit_never_yields_terminal(tmp_path, monkeypatch):
    from peerhub.extensions.adapters import base

    result = process.ProcessResult(123, 0, b'{"response":"OK"}', b"", False, False, 0.1, aborted=True)
    runner = SimpleNamespace(pid=123, start=lambda: None, wait=lambda: result)
    monkeypatch.setattr(base, "BoundedProcess", lambda *args, **kwargs: runner)
    adapter = CliRuntimeTarget("ag", tmp_path, command=["fake.exe"])
    delivery = adapter.deliver("session", SimpleNamespace(body="question"), [])
    assert next(delivery)[0] == "started"
    with pytest.raises(RuntimeTargetError, match="aborted"):
        next(delivery)
    assert not adapter._active
