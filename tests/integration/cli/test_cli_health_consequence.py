"""A definitive ask failure requests a quarantine review; outcome/exit code are unaffected."""
from pathlib import Path

import pytest

import peerhub.cli as cli_mod
from peerhub.cli import main
from peerhub.core.execution import ExecutionCertainty
from peerhub.dispatch.contract import RequestState


class _Failed:
    command_id = "c"
    attempt_id = "a"
    peer_kind = "cx"
    profile_id = "cx.standard"
    response_text = None
    request_state = RequestState.FAILED
    error_code = None
    execution_certainty = ExecutionCertainty.TERMINAL


@pytest.fixture
def ws(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "no-global"))
    assert main(["workspace", "init", "--workspace", str(tmp_path)]) == 0
    monkeypatch.setattr(cli_mod, "execute_direct_ask", lambda request, **kw: _Failed())
    return tmp_path


def _review_targets(ws: Path):
    from peerhub.core.context import PathLayout, RuntimeContext
    from peerhub.runtime import create_read_runtime
    paths = PathLayout.for_workspace(ws)
    ctx = RuntimeContext(cli_mod._detect_workspace_home_id(paths.database_path, ws.name), paths,
                         cli_mod.SystemClock(), cli_mod.UuidSource())
    with create_read_runtime(ctx, adapter_peer_kind="fake") as rt:
        return list(rt.governance_broker.list_targets("quarantine-review", None))


def test_definitive_failure_requests_a_review_and_keeps_the_exit_code(ws, capsys):
    code = main(["ask", "cx", "hello", "--workspace", str(ws)])
    assert code != 0  # unchanged failure exit contract
    assert "quarantine-review requested" in capsys.readouterr().err
    reviews = _review_targets(ws)
    assert len(reviews) == 1 and reviews[0].state["peer_key"] == "cx"


def test_each_failure_is_its_own_review_hc07(ws):
    main(["ask", "cx", "one", "--workspace", str(ws)])
    main(["ask", "cx", "two", "--workspace", str(ws)])
    assert len(_review_targets(ws)) == 2
