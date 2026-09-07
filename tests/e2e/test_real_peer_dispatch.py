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
def test_bug3_room_thread_message_creation_flow(tmp_path):
    """
    Regression test for Bug 3: room/thread/message creation flow works end-to-end.

    NOTE: `message_projection`/`thread_ids` live-rollup onto the parent room
    and thread targets is a known, still-open gap (not fixed here): governed
    targets are CAS-versioned, so mutating a parent's stored state on every
    child append would bump its revision on every message -- which directly
    violates the deliberate immutability invariant asserted by
    tests/integration/governance/test_rooms_threads.py::
    test_append_message_creates_separate_immutable_target (thread.revision
    stays 1 forever). A correct fix needs a query-based projection (e.g.
    listing child message/thread targets by scope) rather than parent-state
    mutation, and is left as a follow-up design task.
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
        "--room", "test-room",
        "--thread", "test-thread",
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

    out4 = run_cmd("room", "status", "--room-id", "test-room")
    assert "test-room" in out4

    import sqlite3
    db_path = tmp_path / ".peerhub" / "peerhub.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.execute("SELECT state_json FROM governed_targets WHERE target_id = ?", ("message:test-msg-1",))
        row = c.fetchone()
        assert row is not None
        state = json.loads(row[0])
        assert state.get("body") == "Hello World"
        assert state.get("scope") == "test-room"
    finally:
        conn.close()
