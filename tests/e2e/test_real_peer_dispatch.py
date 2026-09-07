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
def test_bug_codex_trusted_directory_check_on_fresh_non_git_workspace(tmp_path):
    """
    Regression test: codex.cmd refuses to run outside a directory it
    considers a trusted git repo ("Not inside a trusted directory and
    --skip-git-repo-check was not specified"), which peerhub's codex
    adapter never passed. Invisible to the rest of the suite because the
    slow, real `RealCodexAdapter` integration test always runs with
    workspace_scope="." from inside the peerhub git repo itself -- this
    only reproduces on a genuinely fresh, non-git workspace, found via a
    real install in a brand-new directory outside any git repo.
    """
    cmd = [
        sys.executable, "-m", "peerhub.cli",
        "ask", "cx",
        "Reply with FIX VERIFIED",
        "--capability-tier", "READ_ONLY",
        "--workspace", str(tmp_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    assert "trusted directory" not in result.stderr
    assert "trusted directory" not in result.stdout

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


@pytest.mark.e2e
def test_mailbox_send_and_check_inbox_real_delivery(tmp_path):
    """
    Regression coverage for the private mailbox primitive (room send /
    room check-inbox), a core coordination path distinct from public
    thread messaging. Runs through the real CLI end-to-end.
    """
    def run_cmd(*args):
        cmd = [sys.executable, "-m", "peerhub.cli"] + list(args) + ["--workspace", str(tmp_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"Command failed: {result.stderr}"
        return result.stdout

    run_cmd(
        "room", "create",
        "--room-id", "mb-room",
        "--topic-id", "mb-topic",
        "--title", "Mailbox Test",
        "--creator", "cc",
        "--participants", "cc,ag",
    )
    run_cmd(
        "room", "send",
        "--room-id", "mb-room",
        "--sender-instance-id", "cc",
        "--sender-profile-id", "cc",
        "--recipient-instance-id", "ag",
        "--recipient-profile-id", "ag",
        "--body", "hello ag",
    )
    out = run_cmd(
        "room", "check-inbox",
        "--room-id", "mb-room",
        "--caller-instance-id", "ag",
        "--caller-profile-id", "ag",
        "--json",
    )
    data = json.loads(out)
    messages = data["messages"]
    assert len(messages) == 1
    assert messages[0]["state"]["body"] == "hello ag"
    assert messages[0]["state"]["recipient"]["instance_id"] == "ag"


@pytest.mark.e2e
def test_lesson_propose_approve_activate_sweep_real_lifecycle(tmp_path):
    """
    Regression coverage for the lesson expiry/sweep gap identified in the
    legacy-parity audit: LessonService.propose() previously hardcoded
    expires_at=None with no way to set it, and no sweep existed to retire
    expired lessons. Runs the full lifecycle through the real CLI.
    """
    def run_cmd(*args):
        cmd = [sys.executable, "-m", "peerhub.cli"] + list(args) + ["--workspace", str(tmp_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"Command failed: {result.stderr}"
        return result.stdout

    run_cmd(
        "lesson", "propose",
        "--lesson-id", "e2e-lesson",
        "--title", "Test lesson",
        "--rule", "Always verify empirically",
        "--category", "runtime-reality",
        "--severity", "LOW",
        "--proposer", "cc",
        "--affected", "cc,ag",
        "--expires-at", "1",
    )
    run_cmd("lesson", "approve", "--lesson-id", "e2e-lesson", "--approved-by", "human:tester")
    run_cmd("lesson", "activate", "--lesson-id", "e2e-lesson", "--actor", "cc")

    out = run_cmd("lesson", "sweep", "--json")
    data = json.loads(out)
    assert "lesson:e2e-lesson" in data["retired"]

    status_out = run_cmd("lesson", "status", "--lesson-id", "e2e-lesson", "--json")
    status_data = json.loads(status_out)
    assert status_data["lifecycle"] == "RETIRED"
