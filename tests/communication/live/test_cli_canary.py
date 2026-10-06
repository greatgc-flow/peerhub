"""Explicit, paid-provider public CLI gate; disabled by the default test command."""
import json
import os
import subprocess
import sys

import pytest

from tests.communication.live.live_support import ROOT, lowest_profile

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("provider", ["cx", "cc", "ag"])
def test_public_ask_live_returns_a_durable_response(tmp_path, provider):
    profile = lowest_profile(provider)
    db = tmp_path / "core.db"
    args = [sys.executable, "-m", "peerhub", "--db", str(db), "ask", provider, "Reply with OK",
            "--workspace", str(tmp_path), "--model", profile["model"], "--request-id", "live-cli",
            "--timeout-seconds", "240", "--json"]
    if profile["effort"]:
        args += ["--effort", profile["effort"]]
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PEERHUB_CLI": "core"}
    first = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    assert first.returncode == 0, first.stdout + first.stderr
    result = json.loads(first.stdout)
    assert result["certainty"] == "TERMINAL" and result["response_record_id"]
    retry = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert retry.returncode == 0 and json.loads(retry.stdout)["status"] == "recovered_terminal"
