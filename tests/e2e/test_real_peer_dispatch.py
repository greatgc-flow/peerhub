import subprocess
import sys
import json
import pytest

@pytest.mark.e2e
def test_bug1_direct_ask_admission(tmp_path):
    """
    Regression test for Bug 1: admission rejection on fresh workspace.
    Runs a real direct ask.
    """
    cmd = [
        sys.executable, "-m", "peerhub.cli",
        "ask", "cc",
        "Reply with FIX VERIFIED",
        "--capability-tier", "READ_ONLY",
        "--workspace", str(tmp_path)
    ]
    # We don't assert exit code 0 because network might fail.
    # We just assert it doesn't fail with "request was not admitted".
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    assert "request was not admitted" not in result.stderr
    assert "request was not admitted" not in result.stdout

@pytest.mark.e2e
def test_bug2_diag_consistency(tmp_path):
    """
    Regression test for Bug 2: diag output consistency.
    """
    cmd = [
        sys.executable, "-m", "peerhub.cli",
        "diag", "--workspace", str(tmp_path), "--fresh", "--json"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0
    
    data = json.loads(result.stdout)
    badges = data.get("alert_badges", [])
    routing = data.get("routing_rows", [])
    
    # If healthy (no alerts or unknown), there shouldn't be fake 0% limits.
    for row in routing:
        if row["profile"] == "cc.effort":
            assert "(Limit)" not in row["quota"], "Fake limit detected!"

@pytest.mark.e2e
def test_bug3_room_thread_message_propagation(tmp_path):
    """
    Regression test for Bug 3: room/thread message counts are visible via
    `room status --json` without mutating the room/thread's own governed
    state.

    An earlier attempt at this fix mutated the parent room/thread's own
    state on every child create/append, which broke the deliberate
    immutability invariant asserted by tests/integration/governance/
    test_rooms_threads.py::test_append_message_creates_separate_immutable_target
    (thread.revision stays 1 forever). The real fix is read-time: `room
    status` composes `thread_ids`/`message_count`/`last_message_at` by
    querying child message/thread targets by scope (RoomsService.list_threads
    / list_messages), rather than storing a rollup on the parent.
    """
    def run_cmd(*args):
        cmd = [sys.executable, "-m", "peerhub.cli"] + list(args) + ["--workspace", str(tmp_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"Command failed: {result.stderr}"
        return result.stdout

    run_cmd(
        "room", "create",
        "--room-id", "test-room",
        "--topic-id", "test-topic",
        "--title", "Test Topic",
        "--creator", "cc",
        "--participants", "cc,ag"
    )
    run_cmd(
        "room", "create-thread",
        "--room-id", "test-room",
        "--thread-id", "test-thread",
        "--subject", "Test Thread",
        "--creator", "cc"
    )
    run_cmd(
        "room", "append-message",
        "--message-id", "test-msg-1",
        "--room-id", "test-room",
        "--thread-id", "test-thread",
        "--body", "Hello World",
        "--author", "cc"
    )

    out = run_cmd("room", "status", "--room-id", "test-room", "--json")
    data = json.loads(out)
    assert "test-thread" in data["thread_ids"]
    assert data["message_count"] == 1
    assert data["last_message_at"] is not None
