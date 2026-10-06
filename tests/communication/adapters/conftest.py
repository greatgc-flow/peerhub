import json
import sys
from pathlib import Path

import pytest

FAKE = str(Path(__file__).resolve().parents[1] / "harness" / "fake_cli.py")


@pytest.fixture
def mk(tmp_path):
    """Factory: adapter over the python fake CLI. Returns (adapter, log_path, workspace)."""
    from peerhub.extensions.adapters import CliRuntimeTarget

    ws = tmp_path / "isolated_ws"
    ws.mkdir()
    log = tmp_path / "calls.jsonl"

    def make(kind="cc", mode="ok", **kw):
        env = {"FAKE_MODE": mode, "FAKE_KIND": kind, "FAKE_LOG": str(log), **kw.pop("env_extra", {})}
        kw.setdefault("timeout_s", 20)
        return CliRuntimeTarget(kind, ws, command=kw.pop("command", [sys.executable, FAKE]), env_extra=env, **kw)

    make.log, make.ws = log, ws
    return make


def calls(log: Path) -> list[dict]:
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
