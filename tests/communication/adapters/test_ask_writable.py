"""Peers are read-only by default; write access is an explicit per-ask choice."""
import pytest

from peerhub.extensions.adapters.base import SPECS
from peerhub.extensions.ask import ask


def test_every_peer_is_read_only_by_default():
    assert SPECS["cx"].argv(None, None, "p")[SPECS["cx"].argv(None, None, "p").index("-s") + 1] == "read-only"
    assert "--permission-mode" not in SPECS["cc"].argv(None, None, "p")
    assert "--mode" not in SPECS["ag"].argv(None, None, "p")


def test_writable_opens_write_access_for_each_peer():
    cx = SPECS["cx"].argv(None, None, "p", True)
    assert cx[cx.index("-s") + 1] == "workspace-write"
    cc = SPECS["cc"].argv(None, None, "p", True)
    assert cc[cc.index("--permission-mode") + 1] == "acceptEdits"
    ag = SPECS["ag"].argv(None, None, "p", True)
    assert ag[ag.index("--mode") + 1] == "accept-edits"


def test_writable_cannot_be_combined_with_an_explicit_runtime(tmp_path):
    with pytest.raises(ValueError, match="writable"):
        ask(tmp_path / "core.db", "cx", "hi", runtime=object(), writable=True)  # type: ignore[arg-type]


@pytest.mark.parametrize("writable", [False, True])
def test_resume_preserves_writable_mode_per_peer(writable):
    cx = SPECS["cx"].argv(None, None, "p", writable, vendor_session_id="thread-123")
    assert "-s" not in cx
    assert cx[cx.index("-c") + 1] == f'sandbox_mode="{"workspace-write" if writable else "read-only"}"'

    cc = SPECS["cc"].argv(None, None, "p", writable, vendor_session_id="session-123")
    if writable:
        assert cc[cc.index("--permission-mode") + 1] == "acceptEdits"
    else:
        # Note: asserting absent --permission-mode only checks argv construction;
        # see test_read_only_live_canary_sentinel for real enforcement behavior.
        assert "--permission-mode" not in cc

    ag = SPECS["ag"].argv(None, None, "p", writable, vendor_session_id="conv-123")
    if writable:
        assert ag[ag.index("--mode") + 1] == "accept-edits"
    else:
        # Note: asserting absent --mode only checks argv construction;
        # see test_read_only_live_canary_sentinel for real enforcement behavior.
        assert "--mode" not in ag


def test_ag_without_writable_fails_closed_before_store_creation(tmp_path):
    """#140: native ag without writable=True fails closed pre-spawn without creating CoreStore or appending records."""
    from peerhub.extensions.adapters.base import PrespawnError

    db = tmp_path / "uncreated" / "core.db"
    with pytest.raises(PrespawnError, match="ag execution requires --writable"):
        ask(db, "ag", "hello", writable=False)
    assert not db.exists()


@pytest.mark.canary
@pytest.mark.parametrize("peer", ["cc", "ag"])
def test_read_only_live_canary_sentinel(peer):
    """Opt-in live canary verifying whether fresh and resumed read-only asks prevent file writes.

    cx guarantees read-only sandbox via -s read-only / -c sandbox_mode="read-only".
    cc enforces read-only in headless stream-json mode by withholding --permission-mode acceptEdits.
    ag fails closed pre-spawn without --writable (#140).
    """
    import json
    import os
    import shutil
    import subprocess
    import sys
    import uuid
    from pathlib import Path

    if os.environ.get("CI") or os.environ.get("PEERHUB_LIVE") != "1":
        pytest.skip("opt-in live test requires PEERHUB_LIVE=1 and non-CI environment")

    from peerhub.extensions.adapters.base import resolve_binary
    if resolve_binary(SPECS[peer]) is None:
        pytest.skip(f"provider CLI for {peer} is not installed")

    root = Path(__file__).resolve().parents[3]
    workspace = root / "build" / "test_work" / f"canary-ro-{peer}-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True, exist_ok=True)
    db = workspace / "core.db"
    try:
        sentinel1 = workspace / "sentinel_fresh.txt"
        sentinel2 = workspace / "sentinel_resumed.txt"
        env = {**os.environ, "PYTHONPATH": str(root)}

        # Fresh read-only ask
        cmd1 = [sys.executable, "-m", "peerhub", "--db", str(db), "ask", peer,
                f"Create sentinel_fresh.txt containing {uuid.uuid4().hex}. Do not edit other files. Reply OK.",
                "--workspace", str(workspace), "--stream", f"ro-canary-{peer}", "--request-id", f"ro-fresh-{uuid.uuid4().hex}",
                "--profile", f"{peer}.standard", "--timeout-seconds", "120", "--json"]
        run1 = subprocess.run(cmd1, cwd=workspace, env=env, capture_output=True, text=True, timeout=180)
        if peer == "ag":
            assert run1.returncode != 0
            res1 = json.loads(run1.stdout)
            assert res1["status"] == "error"
            assert not sentinel1.exists()
            return
        assert run1.returncode == 0, run1.stdout + run1.stderr
        res1 = json.loads(run1.stdout)
        assert res1["status"] == "delivered" and res1["certainty"] == "TERMINAL"

        # Resumed read-only ask
        cmd2 = [sys.executable, "-m", "peerhub", "--db", str(db), "ask", peer,
                f"Create sentinel_resumed.txt containing {uuid.uuid4().hex}. Do not edit other files. Reply OK.",
                "--workspace", str(workspace), "--stream", f"ro-canary-{peer}", "--request-id", f"ro-resumed-{uuid.uuid4().hex}",
                "--profile", f"{peer}.standard", "--timeout-seconds", "120", "--json"]
        run2 = subprocess.run(cmd2, cwd=workspace, env=env, capture_output=True, text=True, timeout=180)
        assert run2.returncode == 0, run2.stdout + run2.stderr
        res2 = json.loads(run2.stdout)
        assert res2["status"] == "delivered" and res2["certainty"] == "TERMINAL"
        assert res2.get("effective_mode") == "resumed"

        assert not sentinel1.exists(), f"fresh read-only ask to {peer} created sentinel file"
        assert not sentinel2.exists(), f"resumed read-only ask to {peer} created sentinel file"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)



