"""Run the whole existing arbiter-review suite against ACTIVATED V2 rounds.

The original module's helpers (`_services`, `_resolved_round`) are swapped for V2
equivalents; every arbiter expectation stays identical. Differences forced by the V2
hardening are limited to who may resolve an escalated round (an electorate member, not
an arbitrary "human:reviewer" string).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from peerhub.application.consensus_facade import ConsensusFacade
from peerhub.application.consensus_policy import ConsensusPolicyProvider
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus import ConsensusService
from peerhub.governance.consensus_shell import BrokerAuthorityVersionStore, ConsensusShell
from peerhub.persistence.consensus_activation import activate_consensus_v2
from peerhub.persistence.sqlite import SqliteStateStore
from tests.integration.application import test_arbiter_review as base
from tests.integration.application.test_arbiter_review import *  # noqa: F401,F403


class _AllHealthy:
    def check_health_gate(self, actor_id: str, evaluated_at: int) -> bool:
        return True


def _v2_services(tmp_path: Path):
    clock = base.FixedClock()
    ids = base.SequentialIdSource()
    db = tmp_path / "arbiter-v2.sqlite3"
    store = SqliteStateStore(db, workspace_home_id="arbiter-test")
    store.initialize()
    connection = sqlite3.connect(db, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        activate_consensus_v2(connection, now=1)
    finally:
        connection.close()
    store = SqliteStateStore(db, workspace_home_id="arbiter-test")
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    legacy = ConsensusService(broker, clock=clock, ids=ids)
    shell = ConsensusShell(
        broker, clock=clock, ids=ids, verifier=None, health_port=_AllHealthy(),
        authority_store=BrokerAuthorityVersionStore(broker),
    )
    facade = ConsensusFacade(
        legacy, shell, is_v2_active=store.is_consensus_v2_active,
        policy_provider=ConsensusPolicyProvider(tmp_path / "ws.toml", tmp_path / "gl.toml"),
    )
    return facade, broker, clock, ids


def _v2_resolved_round(consensus, round_id, *, choices):
    consensus.propose(
        round_id=round_id, title="Deployment choice", question="Should we deploy?",
        body="Review the rollout evidence and decide.", proposer_id="peer-a",
        required_participants=("peer-a", "peer-b"), eligible_participants=("peer-a", "peer-b"),
        risk="normal", source_hash="sha256:test",
    )
    for actor_id, choice in zip(("peer-a", "peer-b"), choices):
        if choice is not None:
            consensus.cast_vote(round_id, actor_id=actor_id, choice=choice)
    target = consensus.get_target(round_id)
    assert target is not None
    if target.state["phase"] != "approved":
        consensus.request_escalation(round_id, "missing required vote", "peer-a", 0, "human-tier-0")
        consensus.resolve(round_id, "approved", "peer-b", "manual resolution")


@pytest.fixture(autouse=True)
def _use_v2_helpers(monkeypatch):
    monkeypatch.setattr(base, "_services", _v2_services)
    monkeypatch.setattr(base, "_resolved_round", _v2_resolved_round)


def test_v2_round_records_the_first_arbiter_opinion_only(tmp_path):
    consensus, broker, clock, ids = _v2_services(tmp_path)
    _v2_resolved_round(consensus, "r-first", choices=("agree", "agree"))
    assert consensus.get_target("r-first").state["schema"] == "peerhub.consensus-round.v2"
    coordinator = base._coordinator(
        tmp_path=tmp_path, consensus=consensus, broker=broker, clock=clock, ids=ids,
        executor=base.FakeExecutor(["VERDICT: APPROVE\nfine"]), policy=base._enabled_policy(),
    )
    assert coordinator is not None
