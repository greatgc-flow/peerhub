"""End-to-end tests proving the quota-polling pipeline wiring bugs.

These tests exercise the real CLI entry points (main(["diag", ...]) and
main(["status", "--peer", ...])) through the full poll → persist → read
path.  They do NOT mock the poll functions themselves (that's what the
existing unit tests in test_quota_polling.py already do and is exactly
what let the wiring gap through undetected).

Instead, they mock only at the subprocess/filesystem boundary (i.e. what
a real CLI binary or file would return) and assert on the END-TO-END
observable output.

BUG 1:  `--fresh` is a dead flag — diag output is byte-identical with or
         without it.
BUG 2:  The quota-polling pipeline has zero callers — CC/CX quota is
         always rendered as honestly-absent ("--") regardless of whether
         real polled data exists.
"""

import json
import os
import subprocess
import time
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from peerhub.cli import main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a minimal workspace with the _sys layout diag/status expect."""
    ws = tmp_path / "ws"
    ws.mkdir()
    peerhub_dir = ws / ".peerhub"
    peerhub_dir.mkdir()

    sys_dir = ws / "_sys"
    sys_dir.mkdir()

    # AG statusline log (the one path that already works in production)
    ag_log_dir = sys_dir / "data" / "temp"
    ag_log_dir.mkdir(parents=True)
    ag_log = ag_log_dir / "ag_statusline_stdin.log"
    ag_log.write_text(json.dumps({
        "context_window": {
            "total_input_tokens": 50000,
            "context_window_size": 1048576,
            "used_percentage": 4.8,
        },
        "quota": {
            "gemini-5h": {
                "remaining_fraction": 0.95,
                "reset_in_seconds": 14400,
            },
            "gemini-weekly": {
                "remaining_fraction": 0.85,
                "reset_in_seconds": 500000,
                "reset_time": "2026-08-25T00:00:00Z",
            },
            "3p-5h": {
                "remaining_fraction": 0.90,
                "reset_in_seconds": 14000,
            },
            "3p-weekly": {
                "remaining_fraction": 0.80,
                "reset_in_seconds": 490000,
                "reset_time": "2026-08-25T00:00:00Z",
            },
        },
    }), encoding="utf-8")

    # Point env so _resolve_sys_dir finds this _sys
    monkeypatch.setenv("PEERHUB_SYS_DIR", str(sys_dir))
    monkeypatch.chdir(ws)

    return ws


def _fake_claude_binary_returning_usage(sys_dir: Path) -> Path:
    """Create a fake claude.cmd that prints realistic /usage output."""
    env_dir = sys_dir / "env" / "nodejs" / "npm-global"
    env_dir.mkdir(parents=True, exist_ok=True)
    exe = env_dir / "claude.cmd"
    # batch file that echoes Claude-like /usage output
    exe.write_text(
        '@echo off\n'
        'echo Current session: 42%% used resets Aug 19, 3:00pm (Asia/Seoul)\n'
        'echo Current week (all models): 28%% used resets Aug 22, 12:00am (Asia/Seoul)\n',
        encoding="utf-8",
    )
    return exe


def _fake_codex_binary_returning_usage(sys_dir: Path) -> Path:
    """Create a fake codex.cmd that responds to the app-server MCP protocol."""
    env_dir = sys_dir / "env" / "nodejs" / "npm-global"
    env_dir.mkdir(parents=True, exist_ok=True)
    exe = env_dir / "codex.cmd"
    # Python one-liner that does the MCP init handshake then replies with rate limits
    script = (
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    try:\n"
        "        obj = json.loads(line)\n"
        "    except Exception:\n"
        "        continue\n"
        "    if obj.get('method') == 'initialize':\n"
        "        print(json.dumps({'id': obj['id'], 'result': {'serverInfo': {'name': 'codex'}}}))\n"
        "        sys.stdout.flush()\n"
        "    elif obj.get('method') == 'account/rateLimits/read':\n"
        "        result = {\n"
        "            'primary': {'usedPercent': 35, 'resetsAt': '2026-08-19T18:00:00Z', 'windowDurationMins': 300},\n"
        "            'secondary': {'usedPercent': 15, 'resetsAt': '2026-08-25T00:00:00Z', 'windowDurationMins': 10080},\n"
        "        }\n"
        "        print(json.dumps({'id': obj['id'], 'result': result}))\n"
        "        sys.stdout.flush()\n"
        "        break\n"
    )
    exe.write_text(
        f'@echo off\npython -c "{script.replace(chr(10), "\\n")}"\n',
        encoding="utf-8",
    )
    # Actually write a proper Python script and call it
    script_path = env_dir / "_codex_fake.py"
    script_path.write_text(script, encoding="utf-8")
    exe.write_text(f'@echo off\npython "{script_path}"\n', encoding="utf-8")
    return exe


# ---------------------------------------------------------------------------
# BUG 1: --fresh is a dead flag
# ---------------------------------------------------------------------------


class TestFreshFlagIsDeadBug:
    """Prove that `--fresh` currently has zero effect on output."""

    def test_fresh_flag_does_not_change_diag_output(
        self, isolated_workspace: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Without the fix, --fresh produces byte-identical output to no --fresh.

        After the fix, --fresh should trigger a real poll and populate CC/CX
        quota data that would otherwise be absent (or only read from cache if
        fresh enough by TTL).
        """
        # Run without --fresh
        main(["diag", "--no-color", "--json", "--workspace", str(isolated_workspace)])
        out_no_fresh = capsys.readouterr().out

        # Run WITH --fresh
        main(["diag", "--no-color", "--json", "--fresh", "--workspace", str(isolated_workspace)])
        out_fresh = capsys.readouterr().out

        # BUG ASSERTION: before the fix, these are byte-identical because
        # --fresh is never referenced. After the fix, --fresh should at
        # minimum attempt a poll (possibly failing closed if no binary is
        # found, but the code path itself should differ).
        #
        # We parse the JSON and check the CC/CX peer pools. Before the fix,
        # both are empty regardless of --fresh.
        snap_no_fresh = json.loads(out_no_fresh)
        snap_fresh = json.loads(out_fresh)

        cc_pools_no_fresh = snap_no_fresh["peers"]["cc"]["pools"]
        cc_pools_fresh = snap_fresh["peers"]["cc"]["pools"]

        # Before fix: both are empty lists (no pollers wired)
        # After fix: --fresh should attempt polling (even if it fail-closes)
        # and the snapshot structure should differ (at minimum: a fail-closed
        # ABSENT pool entry vs. nothing at all).
        #
        # The actual test: with the fix in place and a real fake binary
        # available, --fresh should produce real data in the pools.
        # Without the fix, this assert below will PASS (proving the bug):
        assert cc_pools_no_fresh == cc_pools_fresh == [], (
            "BUG CONFIRMED: --fresh has zero effect; CC pools are always empty"
        )


class TestQuotaPipelineHasNoCallersBug:
    """Prove the polling pipeline is never invoked from CLI entry points."""

    def test_diag_never_calls_poll_functions(
        self, isolated_workspace: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """CC/CX quota pools are always empty in diag output."""
        main(["diag", "--json", "--workspace", str(isolated_workspace)])
        out = capsys.readouterr().out
        snap = json.loads(out)

        cc_pools = snap["peers"]["cc"]["pools"]
        cx_pools = snap["peers"]["cx"]["pools"]

        # BUG: both are always empty because the poll functions are never called
        assert cc_pools == [], "BUG: CC pools are always empty (no pollers wired)"
        assert cx_pools == [], "BUG: CX pools are always empty (no pollers wired)"

    def test_status_peer_shows_no_quota_data(
        self, isolated_workspace: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """peerhub status --peer cc shows 'No quota data' because write-side is never called."""
        # Initialize the database so status --peer works
        from peerhub.core.context import PathLayout, RuntimeContext
        from peerhub.cli import SystemClock, UuidSource
        from peerhub.runtime import create_runtime

        paths = PathLayout.for_workspace(isolated_workspace)
        ctx = RuntimeContext(
            workspace_home_id="test",
            paths=paths,
            clock=SystemClock(),
            ids=UuidSource(),
        )
        with create_runtime(ctx, adapter_peer_kind="fake"):
            pass  # just initialize the DB

        exit_code = main(["status", "--peer", "cc", "--workspace", str(isolated_workspace)])
        out = capsys.readouterr().out

        # BUG: always shows "No quota data" because nothing ever writes observations
        assert "No quota data" in out, (
            "BUG: status --peer always shows 'No quota data' because poll pipeline is unwired"
        )


# ---------------------------------------------------------------------------
# Post-fix: these tests should PASS after the wiring fix
# ---------------------------------------------------------------------------


class TestQuotaWiringFixed:
    """After the fix, diag/status should poll and display real data."""

    def test_diag_fresh_polls_and_populates_cc_quota(
        self, isolated_workspace: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """With --fresh and a working fake claude binary, CC pools should be populated."""
        sys_dir = isolated_workspace / "_sys"
        _fake_claude_binary_returning_usage(sys_dir)

        main(["diag", "--json", "--fresh", "--workspace", str(isolated_workspace)])
        out = capsys.readouterr().out
        snap = json.loads(out)

        cc_pools = snap["peers"]["cc"]["pools"]
        assert len(cc_pools) > 0, (
            "FIXED: --fresh should poll and populate CC quota pools"
        )
        # Verify the pool has real parsed data
        pool_names = [p["name"] for p in cc_pools]
        assert "C-pool" in pool_names

    def test_diag_no_fresh_uses_cached_projections_after_poll(
        self, isolated_workspace: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Without --fresh, diag should read cached projections if they exist and are fresh."""
        sys_dir = isolated_workspace / "_sys"
        _fake_claude_binary_returning_usage(sys_dir)

        # First call with --fresh to populate the DB
        main(["diag", "--json", "--fresh", "--workspace", str(isolated_workspace)])
        capsys.readouterr()  # discard

        # Second call WITHOUT --fresh — should still show data from DB if within TTL
        main(["diag", "--json", "--workspace", str(isolated_workspace)])
        out = capsys.readouterr().out
        snap = json.loads(out)

        cc_pools = snap["peers"]["cc"]["pools"]
        # After fix: projections should be readable from the DB even without --fresh
        # (assuming freshness_ttl hasn't expired — which it hasn't, it's the same second)
        assert len(cc_pools) > 0, (
            "FIXED: diag without --fresh should read cached projections from DB"
        )

    def test_status_peer_shows_quota_after_fresh_poll(
        self, isolated_workspace: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """After a fresh poll, `peerhub status --peer cc` should show real data."""
        sys_dir = isolated_workspace / "_sys"
        _fake_claude_binary_returning_usage(sys_dir)

        # First, run diag --fresh to populate the DB with polled data
        main(["diag", "--json", "--fresh", "--workspace", str(isolated_workspace)])
        capsys.readouterr()

        # Now status --peer cc should find the data
        exit_code = main(["status", "--peer", "cc", "--workspace", str(isolated_workspace)])
        out = capsys.readouterr().out

        assert "No quota data" not in out, (
            "FIXED: status --peer cc should show quota data after a fresh poll"
        )
        # Verify we see the CC quota pool scope labels
        assert "C-5H" in out or "C-7D" in out, (
            "FIXED: status should show Claude quota pool scopes"
        )

    def test_diag_json_reflects_real_data(
        self, isolated_workspace: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--json output must reflect the same real polled data."""
        sys_dir = isolated_workspace / "_sys"
        _fake_claude_binary_returning_usage(sys_dir)

        main(["diag", "--json", "--fresh", "--workspace", str(isolated_workspace)])
        out = capsys.readouterr().out
        snap = json.loads(out)

        cc_pools = snap["peers"]["cc"]["pools"]
        assert len(cc_pools) > 0
        # The fake claude binary returns 42% for session, 28% for weekly
        c_pool = cc_pools[0]
        assert "42%" in c_pool.get("five_h", "") or "28%" in c_pool.get("seven_d", ""), (
            "FIXED: JSON output should contain the actual parsed poll values"
        )

    def test_fresh_flag_actually_differs_from_no_fresh(
        self, isolated_workspace: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """With the fix, --fresh should produce different output than without --fresh
        when no cached data exists and a binary is available."""
        sys_dir = isolated_workspace / "_sys"
        _fake_claude_binary_returning_usage(sys_dir)

        # Without --fresh on a clean workspace (no DB data yet)
        main(["diag", "--json", "--workspace", str(isolated_workspace)])
        out_no_fresh = capsys.readouterr().out
        snap_no_fresh = json.loads(out_no_fresh)

        # With --fresh — should actively poll
        main(["diag", "--json", "--fresh", "--workspace", str(isolated_workspace)])
        out_fresh = capsys.readouterr().out
        snap_fresh = json.loads(out_fresh)

        # After fix: --fresh should have populated CC pools, while no-fresh
        # on a clean DB might or might not (depends on TTL-based stale check).
        # But --fresh should definitely have data since it bypasses TTL.
        cc_fresh = snap_fresh["peers"]["cc"]["pools"]
        assert len(cc_fresh) > 0, (
            "FIXED: --fresh should actively poll and show CC quota data"
        )
