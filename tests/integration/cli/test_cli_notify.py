"""NOTIFY: dispatch proceeds and a durable notification target (digest only) is recorded."""
import hashlib
import json
from pathlib import Path

import pytest

import peerhub.cli as cli_mod
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
    cfg = tmp_path / ".peerhub" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "dispatch-policy.toml").write_text('[consultation.overrides]\n"ask" = "notify"\n', encoding="utf-8")
    (tmp_path / ".peerhub" / "proposals.json").write_text(
        json.dumps({"schema_version": 1, "voters": ["cc", "cx"]}), encoding="utf-8")
    monkeypatch.setattr(cli_mod, "execute_direct_ask", lambda request, **kw: _Ok())
    monkeypatch.setattr(cli_mod, "_ask_exit_code", lambda result: 0)
    return tmp_path


def _targets(ws: Path):
    from peerhub.core.context import PathLayout, RuntimeContext
    from peerhub.runtime import create_read_runtime
    paths = PathLayout.for_workspace(ws)
    ctx = RuntimeContext(cli_mod._detect_workspace_home_id(paths.database_path, ws.name), paths,
                         cli_mod.SystemClock(), cli_mod.UuidSource())
    with create_read_runtime(ctx, adapter_peer_kind="fake") as rt:
        return list(rt.governance_broker.list_targets("dispatch-notification", None))


def test_notify_dispatches_and_records_an_undelivered_target_with_a_digest_only(ws, capsys):
    secret = "the confidential question"
    assert main(["ask", "cx", secret, "--workspace", str(ws)]) == 0
    err = capsys.readouterr().err
    assert "undelivered" in err
    (target,) = _targets(ws)
    state = dict(target.state)
    assert state["delivery_state"] == "undelivered"
    assert state["prompt_digest"] == "sha256:" + hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in json.dumps(state)
    assert list(state["recipients"]) == ["cc", "cx"]
