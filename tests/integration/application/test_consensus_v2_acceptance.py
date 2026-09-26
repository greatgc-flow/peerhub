"""Acceptance: an ACTIVATED V2 high-risk proposal completes through real entry points.

Runtime -> API command (proposal add/vote) -> CLI (`consensus ack|nack|retract`)
-> reconcile -> ratified invariant request. Nothing here calls the shell directly.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from peerhub.application.legacy import ProposalAddCommand, ProposalVoteCommand
from peerhub.cli import _detect_workspace_home_id, main
from peerhub.client import Client
from peerhub.core.context import PathLayout, RuntimeContext
from peerhub.core.ports import RequestContext
from peerhub.core.protocol import CommandSuccess
from peerhub.persistence.consensus_activation import activate_consensus_v2
from peerhub.runtime import create_runtime
from peerhub.cli import UuidSource
from tests.integration.application.test_proposals import (
    FixedClock,
    _seed_runtime_health,
    _submission,
)


def _open_runtime(tmp_path: Path, timestamp: int):
    paths = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext(
        _detect_workspace_home_id(paths.database_path, tmp_path.name),
        paths,
        FixedClock(timestamp),
        UuidSource(),
    )
    return create_runtime(context, proposal_voters=("cc", "cx"))


@pytest.fixture
def activated_workspace(tmp_path: Path):
    timestamp = int(time.time())
    with _open_runtime(tmp_path, timestamp) as runtime:
        _seed_runtime_health(runtime.state_store, runtime.health_service.policy, timestamp=timestamp)
        db_path = runtime.context.paths.database_path
    connection = sqlite3.connect(db_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        activate_consensus_v2(connection, now=timestamp)
    finally:
        connection.close()
    return tmp_path, timestamp


def _propose_and_vote(client, tag: str = "a"):
    added = client.submit(ProposalAddCommand(
        submission=_submission(f"add-{tag}"), subject=f"Ratify {tag}", from_peer="cc",
        impact="high", rationale="because", text="change the invariant",
    ))
    assert isinstance(added, CommandSuccess)
    round_id = added.result["round_id"]
    for voter in ("cc", "cx"):
        outcome = client.submit(ProposalVoteCommand(
            submission=_submission(f"vote-{tag}-{voter}"), proposal_id=round_id,
            voter=voter, vote="agree", reason="yes",
        ))
        assert isinstance(outcome, CommandSuccess)
    return round_id


def _client(runtime):
    return Client(
        runtime.application_api,
        caller=RequestContext(principal="acceptance-user", client_id="proposal-client"),
    )


def test_high_risk_proposal_needs_final_call_acks_then_approves_and_projects_the_request(
    activated_workspace, capsys
):
    workspace, timestamp = activated_workspace
    with _open_runtime(workspace, timestamp) as runtime:
        assert runtime.state_store.is_consensus_v2_active()
        round_id = _propose_and_vote(_client(runtime))
        state = runtime.governance_broker.get_target(round_id).state
        assert state["schema"] == "peerhub.consensus-round.v2"
        assert state["phase"] == "final_call"
        assert (state["origin"], state["action"]) == ("proposals", "governance.proposal.create")

    for actor in ("cc", "cx"):
        assert main(["consensus", "ack", "--workspace", str(workspace),
                     "--round-id", round_id, "--actor", actor]) == 0
    out = capsys.readouterr().out
    assert "phase=approved" in out

    with _open_runtime(workspace, timestamp) as runtime:
        result = runtime.proposal_coordinator.reconcile_outcome(round_id)
        assert result.outcome == "CONSENSUS_OK"
        assert result.invariant_request_target_id is not None
        request = runtime.governance_broker.get_target(result.invariant_request_target_id)
        assert request is not None and request.state["status"] == "REQUESTED"


def test_blocking_nack_through_the_cli_holds_the_barrier(activated_workspace, capsys):
    workspace, timestamp = activated_workspace
    with _open_runtime(workspace, timestamp) as runtime:
        round_id = _propose_and_vote(_client(runtime), tag="n")
    assert main(["consensus", "ack", "--workspace", str(workspace),
                 "--round-id", round_id, "--actor", "cc"]) == 0
    assert main(["consensus", "nack", "--workspace", str(workspace), "--round-id", round_id,
                 "--actor", "cx", "--nack-type", "block"]) == 0
    capsys.readouterr()
    with _open_runtime(workspace, timestamp) as runtime:
        state = runtime.governance_broker.get_target(round_id).state
        assert state["phase"] == "final_call"
        assert tuple(state["barrier_holders"]) == ("cx",)


def test_retracting_an_approval_through_the_cli_fences_authority(activated_workspace, capsys):
    workspace, timestamp = activated_workspace
    with _open_runtime(workspace, timestamp) as runtime:
        round_id = _propose_and_vote(_client(runtime), tag="r")
    for actor in ("cc", "cx"):
        assert main(["consensus", "ack", "--workspace", str(workspace),
                     "--round-id", round_id, "--actor", actor]) == 0
    assert main(["consensus", "retract", "--workspace", str(workspace),
                 "--round-id", round_id, "--actor", "cc"]) == 0
    capsys.readouterr()
    with _open_runtime(workspace, timestamp) as runtime:
        state = runtime.governance_broker.get_target(round_id).state
        assert tuple(state["revocations"]) == ("RevocationRecorded",)
        from peerhub.governance.consensus_shell import BrokerAuthorityVersionStore
        assert BrokerAuthorityVersionStore(runtime.governance_broker).read_version() == 2
        revoked = runtime.proposal_coordinator.reconcile_outcome(round_id)
        assert revoked.outcome == "REVOKED" and revoked.invariant_request_target_id is None


def test_ack_by_a_stranger_is_refused_by_the_cli(activated_workspace, capsys):
    workspace, timestamp = activated_workspace
    with _open_runtime(workspace, timestamp) as runtime:
        round_id = _propose_and_vote(_client(runtime), tag="s")
    assert main(["consensus", "ack", "--workspace", str(workspace),
                 "--round-id", round_id, "--actor", "mallory"]) == 2
    assert "frozen authority set" in capsys.readouterr().err.lower()
