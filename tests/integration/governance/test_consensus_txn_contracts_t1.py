"""T1: authority validated inside the committing txn; public-command replay survives restart."""
from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.governance.authority_fence import StaleAuthorityError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus_shell import BrokerAuthorityVersionStore, ConsensusShell
from peerhub.governance.contract import (
    EffectIntent,
    MutationDisposition,
    build_mutation_request,
)
from peerhub.persistence.sqlite import SqliteStateStore

CFG = {"formula": "max(2, N)", "required_votes": 2, "final_call_rule": "always",
       "deadlines": {}, "escalation_paths": []}


class _Health:
    def check_health_gate(self, actor_id: str, evaluated_at: int) -> bool:
        return True


def _env(db: Path, tag: str):
    store = SqliteStateStore(db, workspace_home_id="t1")
    if not db.exists():
        store.initialize()
    ids = FakeIdSource([f"{tag}-{i}" for i in range(1, 3000)])
    broker = GovernanceBroker(store, clock=FakeClock(range(1, 10_000)), ids=ids)
    auth = BrokerAuthorityVersionStore(broker)
    shell = ConsensusShell(broker, clock=FakeClock(range(1, 10_000)), ids=ids,
                           verifier=None, health_port=_Health(), authority_store=auth)
    return shell, broker, auth


def _propose(shell, rid="r1"):
    shell.propose_v2(rid, "T", "Q", "B", "p1", ["p1", "p2"], ["p1", "p2"], "normal", "sha256:x", CFG)


def test_broker_precondition_runs_inside_the_txn_and_blocks_the_write(tmp_path):
    _, broker, _ = _env(tmp_path / "a.sqlite3", "a")

    def veto(unit):
        raise StaleAuthorityError("vetoed in txn")

    req = build_mutation_request(
        broker.ids, id_prefix="t", client_id="t1", target_id="x", expected_revision=0,
        actor_id="a", operation="op", desired_state={"kind": "task"},
        effect_intent=EffectIntent(kind="consensus.noop", payload={}),
    )
    with pytest.raises(StaleAuthorityError):
        broker.submit(req, precondition=veto)
    assert broker.get_target("x") is None


def test_broker_precondition_is_skipped_for_an_idempotent_replay(tmp_path):
    _, broker, _ = _env(tmp_path / "b.sqlite3", "b")
    req = build_mutation_request(
        broker.ids, id_prefix="t", client_id="t1", target_id="y", expected_revision=0,
        actor_id="a", operation="op", desired_state={"kind": "task"},
        effect_intent=EffectIntent(kind="consensus.noop", payload={}),
    )
    broker.submit(req)

    def veto(unit):
        raise AssertionError("must not run on replay")

    assert broker.submit(req, precondition=veto).disposition is MutationDisposition.IDEMPOTENCY_HIT


def test_authority_bump_between_read_and_commit_is_rejected_by_the_committing_txn(tmp_path):
    shell, broker, auth = _env(tmp_path / "c.sqlite3", "c")
    _propose(shell)
    shell.cast_vote("r1", "p1", "agree")
    real_submit = broker.submit

    bumped = {"done": False}

    def bump_then_submit(req, **kw):
        if not bumped["done"]:
            bumped["done"] = True
            broker.submit = real_submit  # type: ignore[method-assign]
            auth.increment()  # authority changes AFTER every pre-check passed
            broker.submit = bump_then_submit  # type: ignore[method-assign]
        return real_submit(req, **kw)

    broker.submit = bump_then_submit  # type: ignore[method-assign]
    before = broker.get_target("r1").revision
    with pytest.raises(StaleAuthorityError):
        shell.cast_vote("r1", "p2", "agree")
    assert broker.get_target("r1").revision == before


def test_public_command_replay_survives_restart_and_writes_nothing_twice(tmp_path):
    db = tmp_path / "d.sqlite3"
    shell, broker, _ = _env(db, "first")
    _propose(shell)
    first = shell.cast_vote("r1", "p1", "agree", idempotency_key="client-cmd-1")
    rev = broker.get_target("r1").revision

    shell2, broker2, _ = _env(db, "second")  # simulated restart, fresh id source
    again = shell2.cast_vote("r1", "p1", "agree", idempotency_key="client-cmd-1")
    assert again.disposition is MutationDisposition.IDEMPOTENCY_HIT
    assert again.receipt.receipt_id == first.receipt.receipt_id
    assert broker2.get_target("r1").revision == rev


def test_idempotency_key_is_scoped_per_actor_and_round(tmp_path):
    shell, broker, _ = _env(tmp_path / "e.sqlite3", "e")
    _propose(shell)
    a = shell.cast_vote("r1", "p1", "agree", idempotency_key="k")
    b = shell.cast_vote("r1", "p2", "agree", idempotency_key="k")  # other actor, same client key
    assert b.disposition is MutationDisposition.COMMITTED
    assert a.receipt.receipt_id != b.receipt.receipt_id
