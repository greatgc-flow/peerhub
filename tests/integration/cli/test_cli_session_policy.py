"""`ask --session-policy/--session-id/--room-id` map onto the direct-ask session request."""
from pathlib import Path

import pytest

import peerhub.cli as cli_mod
from peerhub.adapters.contract import SessionAction
from peerhub.cli import main


class _Ok:
    response_text = "ok"
    error_code = None
    request_state = "SUCCEEDED_VERIFIED"
    execution_certainty = None
    peer_kind = "cx"
    profile_id = "cx.standard"


@pytest.fixture
def ws(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "no-global"))
    assert main(["workspace", "init", "--workspace", str(tmp_path)]) == 0
    seen = {}

    def fake(request, **kw):
        seen["req"] = request
        return _Ok()

    monkeypatch.setattr(cli_mod, "execute_direct_ask", fake)
    monkeypatch.setattr(cli_mod, "_ask_exit_code", lambda r: 0)
    return tmp_path, seen


def _ask(path, *extra):
    return main(["ask", "cx", "hi", "--workspace", str(path), *extra])


def test_default_policy_is_auto_and_changes_nothing(ws):
    path, seen = ws
    assert _ask(path) == 0
    r = seen["req"]
    assert (r.session_id, r.room_id, r.resume, r.session_action) == (None, None, False, None)


def test_reuse_resumes_and_fresh_creates_the_next_generation(ws):
    path, seen = ws
    assert _ask(path, "--session-id", "c1", "--session-policy", "reuse") == 0
    assert seen["req"].resume is True and seen["req"].session_id == "c1"
    assert _ask(path, "--room-id", "room-1", "--session-policy", "fresh") == 0
    r = seen["req"]
    assert r.session_action is SessionAction.CREATE and r.resume is False and r.room_id == "room-1"


def test_workspace_policy_default_mode_applies_when_no_flag_is_given(ws):
    path, seen = ws
    cfg = path / ".peerhub" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "dispatch-policy.toml").write_text('[session]\ndefault_mode = "fresh"\n', encoding="utf-8")
    assert _ask(path, "--session-id", "c2") == 0
    assert seen["req"].session_action is SessionAction.CREATE
