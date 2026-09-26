"""C2: after activation only the V2 consensus worker may claim consensus effects."""
import sqlite3
from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus import ConsensusService
from peerhub.governance.consensus_shell import BrokerAuthorityVersionStore, ConsensusShell
from peerhub.governance.contract import EffectIntent, EffectOutcome, build_mutation_request
from peerhub.governance.proposal_policy import EffectRoutingError
from peerhub.governance.invariant_requests import RATIFIED_INVARIANT_EFFECT_KIND
from peerhub.persistence.consensus_activation import activate_consensus_v2
from peerhub.persistence.sqlite import SqliteStateStore


class _Health:
    def check_health_gate(self, actor_id: str, evaluated_at: int) -> bool:
        return True


class Env:
    """One process view of the workspace DB (fresh store handle per view)."""

    def __init__(self, db: Path, tag: str) -> None:
        self.store = SqliteStateStore(db, workspace_home_id="c2")
        self.clock = FakeClock(range(1, 10_000))
        self.ids = FakeIdSource([f"{tag}-id-{i}" for i in range(1, 5000)])
        self.broker = GovernanceBroker(self.store, clock=self.clock, ids=self.ids)
        self.legacy = ConsensusService(
            self.broker, clock=self.clock, ids=FakeIdSource([f"{tag}-d-{i}" for i in range(1, 5000)])
        )
        self.shell = ConsensusShell(
            self.broker, clock=self.clock, ids=self.ids, verifier=None,
            health_port=_Health(), authority_store=BrokerAuthorityVersionStore(self.broker),
        )

    def emit(self, target_id: str, kind: str, payload: dict, state_kind: str = "task") -> None:
        self.broker.submit(build_mutation_request(
            self.ids, id_prefix="c2", client_id="c2-test", target_id=target_id,
            expected_revision=0, actor_id="tester", operation="emit",
            desired_state={"kind": state_kind, "round_id": target_id},
            effect_intent=EffectIntent(kind=kind, payload=payload),
        ))


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "c2.sqlite3"
    store = SqliteStateStore(path, workspace_home_id="c2")
    store.initialize()
    return path


def _activate(db: Path) -> None:
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        activate_consensus_v2(conn, now=5000)
    finally:
        conn.close()


def _unclaimed(env: Env, round_id: str) -> bool:
    return all(
        p.event.claimed_by is None
        for p in env.broker.recover_pending_effects()
        if p.event.payload.get("target_id") == round_id
    )


def test_legacy_worker_cannot_claim_consensus_effect_after_activation(db):
    pre = Env(db, "pre")
    pre.emit("r-legacy", "consensus.resolved", {"round_id": "r-legacy"})
    _activate(db)
    post = Env(db, "post")
    with pytest.raises(sqlite3.IntegrityError):
        post.legacy.process_consensus_effects("r-legacy")
    assert _unclaimed(post, "r-legacy")


def test_v2_worker_claims_records_receipt_and_is_idempotent(db):
    pre = Env(db, "pre")
    pre.emit("r-v2", "consensus.resolved", {"round_id": "r-v2"})
    _activate(db)
    post = Env(db, "post")
    receipts = post.shell.process_consensus_effects("r-v2")
    assert len(receipts) == 1
    assert receipts[0].outcome is EffectOutcome.EFFECT_SUCCEEDED
    assert receipts[0].owner_id.startswith("consensus-v2:")
    assert post.shell.process_consensus_effects("r-v2") == ()


def test_effect_preclaimed_by_legacy_before_activation_can_still_complete(db):
    pre = Env(db, "pre")
    pre.emit("r-pre", "consensus.resolved", {"round_id": "r-pre"})
    claimed = pre.broker.claim_effect(
        pre.broker.recover_pending_effects()[0].event.event_id,
        owner_id="consensus-worker:r-pre", attempt_id="att-1",
    )
    _activate(db)
    post = Env(db, "post")
    receipt = post.broker.record_effect_result(
        claimed.event_id, owner_id="consensus-worker:r-pre", attempt_id="att-1",
        outcome=EffectOutcome.EFFECT_SUCCEEDED,
    )
    assert receipt.owner_id == "consensus-worker:r-pre"


def test_unknown_consensus_effect_kind_holds_and_errors(db):
    pre = Env(db, "pre")
    pre.emit("r-weird", "consensus.weird", {"round_id": "r-weird"})
    _activate(db)
    post = Env(db, "post")
    with pytest.raises(EffectRoutingError) as info:
        post.shell.process_consensus_effects("r-weird")
    assert info.value.hold is True
    assert _unclaimed(post, "r-weird")


def test_ratified_invariant_effect_is_left_for_the_exclusive_materializer(db):
    pre = Env(db, "pre")
    pre.emit("r-inv", RATIFIED_INVARIANT_EFFECT_KIND, {"round_id": "r-inv"})
    _activate(db)
    post = Env(db, "post")
    assert post.shell.process_consensus_effects("r-inv") == ()
    assert _unclaimed(post, "r-inv")


def test_non_consensus_effects_keep_working_for_any_owner_after_activation(db):
    pre = Env(db, "pre")
    pre.emit("t-1", "task.created", {"task_id": "t-1"})
    _activate(db)
    post = Env(db, "post")
    event_id = post.broker.recover_pending_effects()[0].event.event_id
    claimed = post.broker.claim_effect(event_id, owner_id="task-worker", attempt_id="a1")
    assert claimed.claimed_by == "task-worker"
