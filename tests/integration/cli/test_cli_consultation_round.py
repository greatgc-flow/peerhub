"""REVIEW / QUORUM / UNANIMOUS consultation before ask dispatch, on an ACTIVATED V2 workspace.

Peers vote through the real `peerhub consensus vote` CLI while `ask` waits on the durable round.
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import peerhub.cli as cli_mod
from peerhub.cli import main
from peerhub.core.context import PathLayout, RuntimeContext
from peerhub.persistence.consensus_activation import activate_consensus_v2
from peerhub.runtime import create_read_runtime, create_runtime
from tests.integration.application.test_proposals import FixedClock, _seed_runtime_health
from tests.fakes import SequentialIdSource


DISPATCHED: list = []


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
    paths = PathLayout.for_workspace(tmp_path)
    home = cli_mod._detect_workspace_home_id(paths.database_path, tmp_path.name)
    ts = int(time.time())
    with create_runtime(RuntimeContext(home, paths, FixedClock(ts), SequentialIdSource()),
                        proposal_voters=("cc", "cx")) as rt:
        _seed_runtime_health(rt.state_store, rt.health_service.policy, timestamp=ts)
    conn = sqlite3.connect(paths.database_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    activate_consensus_v2(conn, now=ts)
    conn.close()
    (tmp_path / ".peerhub" / "proposals.json").write_text(
        json.dumps({"schema_version": 1, "voters": ["cc", "cx"]}), encoding="utf-8")
    DISPATCHED.clear()
    monkeypatch.setattr(cli_mod, "execute_direct_ask", lambda request, **kw: DISPATCHED.append(request) or _Ok())
    monkeypatch.setattr(cli_mod, "_ask_exit_code", lambda result: 0)
    return tmp_path


def _policy(ws: Path, depth: str, timeout: int = 30):
    cfg = ws / ".peerhub" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "dispatch-policy.toml").write_text(
        f'[consultation.overrides]\n"ask" = "{depth}"\n[consensus]\n'
        f'quorum_formula = "all_required"\nfinal_call_rule = "never"\n'
        f'timeout_seconds = {timeout}\nescalation_target = "human-tier-0"\n', encoding="utf-8")


def _round_id(ws: Path, wait: float = 20.0):
    paths = PathLayout.for_workspace(ws)
    home = cli_mod._detect_workspace_home_id(paths.database_path, ws.name)
    end = time.time() + wait
    while time.time() < end:
        with create_read_runtime(RuntimeContext(home, paths, cli_mod.SystemClock(), cli_mod.UuidSource()),
                                 adapter_peer_kind="fake") as rt:
            rounds = [t for t in rt.governance_broker.list_targets("consensus-round", None)
                      if t.target_id.startswith("consultation-")]
        if rounds:
            return rounds[0].target_id
        time.sleep(0.1)
    raise AssertionError("consultation round never appeared")


def _voters(ws: Path, votes: dict):
    def run():
        rid = _round_id(ws)
        for actor, choice in votes.items():
            main(["consensus", "vote", "--workspace", str(ws), "--round-id", rid,
                  "--actor", actor, "--choice", choice])
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _ask(ws: Path):
    return main(["ask", "cx", "the confidential question", "--workspace", str(ws)])


def test_review_approved_by_the_reviewer_dispatches_and_keeps_only_a_digest(ws, capsys):
    _policy(ws, "review")
    t = _voters(ws, {"cc": "agree"})
    assert _ask(ws) == 0
    t.join(5)
    assert len(DISPATCHED) == 1
    err = capsys.readouterr().err
    assert "consultation approved" in err
    paths = PathLayout.for_workspace(ws)
    home = cli_mod._detect_workspace_home_id(paths.database_path, ws.name)
    with create_read_runtime(RuntimeContext(home, paths, cli_mod.SystemClock(), cli_mod.UuidSource()),
                             adapter_peer_kind="fake") as rt:
        (round_,) = [t for t in rt.governance_broker.list_targets("consensus-round", None)
                     if t.target_id.startswith("consultation-")]
    state = json.dumps(round_.state["proposal"], default=str)
    assert "the confidential question" not in state and "sha256:" in state
    assert not list((ws / "prompt-staging").glob("*")) if (ws / "prompt-staging").exists() else True


def test_review_dissent_holds_the_dispatch(ws, capsys):
    _policy(ws, "review")
    t = _voters(ws, {"cc": "disagree"})
    assert _ask(ws) == 2
    t.join(5)
    assert DISPATCHED == []
    assert "dispatch held" in capsys.readouterr().err


def test_review_timeout_is_non_blocking(ws, capsys):
    _policy(ws, "review", timeout=1)
    assert _ask(ws) == 0
    assert len(DISPATCHED) == 1
    assert "non-blocking" in capsys.readouterr().err


def test_quorum_timeout_escalates_and_holds(ws, capsys):
    _policy(ws, "quorum", timeout=1)
    assert _ask(ws) == 2
    assert DISPATCHED == []
    assert "escalated" in capsys.readouterr().err


def test_quorum_approved_when_every_required_voter_agrees(ws):
    _policy(ws, "quorum")
    t = _voters(ws, {"cc": "agree", "cx": "agree"})
    assert _ask(ws) == 0
    t.join(5)
    assert len(DISPATCHED) == 1


def test_no_eligible_reviewer_holds_immediately(ws, capsys):
    _policy(ws, "review")
    (ws / ".peerhub" / "proposals.json").write_text(
        json.dumps({"schema_version": 1, "voters": []}), encoding="utf-8")
    assert _ask(ws) == 2
    assert "no eligible reviewer" in capsys.readouterr().err
    assert DISPATCHED == []


def test_review_before_v2_activation_fails_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "no-global"))
    assert main(["workspace", "init", "--workspace", str(tmp_path)]) == 0
    (tmp_path / ".peerhub" / "proposals.json").write_text(
        json.dumps({"schema_version": 1, "voters": ["cc", "cx"]}), encoding="utf-8")
    _policy(tmp_path, "review")
    DISPATCHED.clear()
    monkeypatch.setattr(cli_mod, "execute_direct_ask", lambda request, **kw: DISPATCHED.append(request) or _Ok())
    assert main(["ask", "cx", "hi", "--workspace", str(tmp_path)]) == 2
    assert DISPATCHED == []
    assert "V2" in capsys.readouterr().err
