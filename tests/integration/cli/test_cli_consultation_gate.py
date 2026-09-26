"""The gate runs before anything is dispatched by ask/broadcast; broadcast routes per target."""
from pathlib import Path

import pytest

import peerhub.cli as cli_mod
from peerhub.cli import main


@pytest.fixture
def ws(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "no-global"))
    assert main(["workspace", "init", "--workspace", str(tmp_path)]) == 0
    called = {"ask": 0}

    def fake_execute(request, **kw):
        called["ask"] += 1
        raise AssertionError("must not dispatch")

    monkeypatch.setattr(cli_mod, "execute_direct_ask", fake_execute)
    return tmp_path, called


def _policy(ws: Path, body: str):
    cfg = ws / ".peerhub" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "dispatch-policy.toml").write_text(body, encoding="utf-8")


@pytest.mark.parametrize("cmd", [["ask", "cx", "hi"], ["broadcast", "hi", "--peers", "cx"]])
def test_configured_quorum_blocks_before_any_dispatch(ws, capsys, cmd):
    path, called = ws
    _policy(path, f'[consultation.overrides]\n"{cmd[0]}" = "quorum"\n')
    assert main(cmd + ["--workspace", str(path)]) == 2
    assert "refusing to bypass" in capsys.readouterr().err
    assert called["ask"] == 0


def test_broadcast_applies_the_hint_per_target_peer(ws, monkeypatch, capsys):
    path, _ = ws
    _policy(path, '[routing]\neffort_routing = "opt-in"\n[routing.preference_map]\n'
                  '"deep" = ["ag.deepthink", "cc.deepthink", "cx.deepthink"]\n')
    seen = {}

    class FakeCoordinator:
        def __init__(self, **kw):
            pass

        def fan_out(self, request):
            seen["targets"] = list(request.targets)

            class R:
                round_id = "r"
                disposition = "all_completed"
                legs = []
            return R()

    monkeypatch.setattr("peerhub.application.broadcast.BroadcastCoordinator", FakeCoordinator)
    assert main(["broadcast", "hi", "--peers", "cx,ag", "--effort-hint", "deep",
                 "--workspace", str(path)]) == 0
    assert seen["targets"] == [("cx", "cx.deepthink"), ("ag", "ag.deepthink")]
