"""cx.astra RE-audit (344dd1e) defects reproduced as regression tests (batch D)."""
import sqlite3

import pytest

from peerhub.core.errors import IdempotencyPayloadMismatchError, InvalidMutationError
from peerhub.governance.authority_fence import StaleAuthorityError
from peerhub.governance.authorization import AuthorizationError
from peerhub.governance.consensus import (
    AckNackEvent,
    ConsensusStateMachine,
    EvalContext,
)
from peerhub.governance.consensus_shell import BrokerAuthorityVersionStore
from peerhub.governance.contract import EffectIntent, EffectOutcome, MutationDisposition
from peerhub.governance.invariant_requests import RATIFIED_INVARIANT_EFFECT_KIND
from tests.integration.governance.test_consensus_audit_regressions import (
    cfg,
    env,
    propose,
    state,
    verifier,
)


# ---- P1-1a: escalation -> resolve may not approve past a held barrier/dissent ---------
def test_resolve_cannot_approve_while_a_blocking_nack_is_held(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell)
    shell.cast_vote("r1", "p1", "agree")
    shell.cast_vote("r1", "p2", "agree")
    shell.final_call_nack("r1", "p2", nack_type="block")
    shell.request_escalation("r1", "x", "p1", 0, "human-tier-0")
    with pytest.raises(InvalidMutationError):
        shell.resolve("r1", "approved", "p1", "override")
    assert state(broker)["phase"] == "final_call"
    shell.resolve("r1", "rejected", "p1", "reject is always allowed")
    assert state(broker)["phase"] == "rejected"


# ---- P1-1b: a revoked approval never materializes its invariant request ---------------
def test_revoked_approval_effect_is_not_materialized(tmp_path):
    from peerhub.governance.invariant_requests import RatifiedInvariantRequestProjector
    from fakes import FakeClock, FakeIdSource

    def factory(st, round_id, actor):
        return EffectIntent(kind=RATIFIED_INVARIANT_EFFECT_KIND, payload={
            "request_id": f"ratified-invariant-write-request:{round_id}:h", "round_id": round_id,
            "approved_revision": int(st["_rev"]) + 1, "decision_hash": "h", "proposer_id": "p1",
            "title": "t", "question": "q", "body": "b\n\nChanges:\nx", "source_hash": "sha256:x",
            "participants": st["participants"], "votes": st["votes"],
            "proposed_invariant_text": "x", "target_doc_hint": "10-invariants.md", "requested_at": 1,
        })

    shell, broker, _ = env(tmp_path, effect_factories={"consensus.round.propose": factory})
    propose(shell)
    shell.cast_vote("r1", "p1", "agree")
    shell.cast_vote("r1", "p2", "agree")
    shell.final_call_ack("r1", "p1")
    shell.final_call_ack("r1", "p2")
    shell.retraction("r1", "p1")
    projector = RatifiedInvariantRequestProjector(
        broker, clock=FakeClock(range(1, 90_000)), ids=FakeIdSource([f"pj-{i}" for i in range(500)])
    )
    event = next(p.event for p in broker.recover_pending_effects()
                 if p.event.payload.get("effect_kind") == RATIFIED_INVARIANT_EFFECT_KIND)
    with pytest.raises(InvalidMutationError):
        projector.project_event(event.event_id)
    assert broker.get_target("ratified-invariant-write-request:r1:h") is None
    assert broker.get_effect_receipt(event.event_id).outcome is EffectOutcome.EFFECT_FAILED


# ---- P1-1c: authority changes re-authorize open rounds instead of stranding them ------
def test_authority_bump_revalidates_the_electorate_and_lets_a_healthy_round_continue(tmp_path):
    shell, broker, health = env(tmp_path)
    propose(shell, rid="other", rule="never")
    shell.cast_vote("other", "p1", "agree")
    BrokerAuthorityVersionStore(broker).increment()  # e.g. an unrelated revocation elsewhere
    shell.cast_vote("other", "p2", "agree")  # healthy electorate: proceeds, version refreshed
    assert state(broker, "other")["expected_authority_version"] == 2


def test_authority_bump_with_an_unhealthy_electorate_member_still_fails_closed(tmp_path):
    shell, broker, health = env(tmp_path)
    propose(shell, rid="other", rule="never")
    BrokerAuthorityVersionStore(broker).increment()
    health.down.add("p2")
    with pytest.raises(AuthorizationError):
        shell.cast_vote("other", "p1", "agree")


# ---- P1-2: invalid NACK values never approve ---------------------------------------
def test_core_rejects_an_unknown_nack_type():
    ctx = EvalContext(current_timestamp=1.0, caller_identity="a", frozen_authority_set=frozenset({"a"}),
                      required_participants=frozenset({"a"}))
    with pytest.raises(InvalidMutationError):
        ConsensusStateMachine(state="final_call").evaluate(
            ctx, AckNackEvent(candidate_id="c", actor="a", proof="", nack_type="typo-block")
        )


def test_shell_rejects_an_unknown_nack_type_and_writes_nothing(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell)
    shell.cast_vote("r1", "p1", "agree")
    shell.cast_vote("r1", "p2", "agree")
    shell.final_call_ack("r1", "p1")
    rev = broker.get_target("r1").revision
    with pytest.raises(InvalidMutationError):
        shell.final_call_nack("r1", "p2", nack_type="typo-block")
    assert broker.get_target("r1").revision == rev and state(broker)["phase"] == "final_call"


# ---- P1-3: submit_atomic replay identity ---------------------------------------------
def test_atomic_replay_with_changed_payload_is_rejected(tmp_path):
    from peerhub.governance.contract import build_mutation_request
    shell, broker, _ = env(tmp_path)

    def build(value):
        def make(unit):
            return (build_mutation_request(
                broker.ids, id_prefix="t", client_id="t", target_id="z", expected_revision=0,
                actor_id="a", operation="op", desired_state={"kind": "task", "v": value},
                effect_intent=EffectIntent(kind="consensus.noop", payload={}),
            ),)
        return make

    import dataclasses
    holder = {}

    def keyed(value):
        def make(unit):
            (req,) = build(value)(unit)
            return (dataclasses.replace(req, idempotency_key="same-key"),)
        return make

    first = broker.submit_atomic(keyed(1))
    assert first[0].disposition is MutationDisposition.COMMITTED
    again = broker.submit_atomic(keyed(1))
    assert again[0].disposition is MutationDisposition.IDEMPOTENCY_HIT
    with pytest.raises(IdempotencyPayloadMismatchError):
        broker.submit_atomic(keyed(2))


# ---- P1-4: policy loader fails closed -------------------------------------------------
def test_unreadable_policy_layer_is_a_configuration_error(tmp_path, monkeypatch):
    from pathlib import Path
    from peerhub.application.consensus_policy import ConsensusPolicyProvider
    from peerhub.governance.policy_snapshot import ConfigurationError
    layer = tmp_path / "ws.toml"
    layer.write_text('[consensus]\nfinal_call_rule = "always"\n', encoding="utf-8")
    real = Path.read_bytes

    def boom(self):
        if self == layer:
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", boom)
    with pytest.raises(ConfigurationError):
        ConsensusPolicyProvider(layer, tmp_path / "gl.toml").round_config("consensus.round.propose", "normal", 2, 2)


# ---- P1-5: activation vs restore epochs; missing table; paged discovery ---------------
def test_activation_stays_consistent_when_the_epoch_advances_later(tmp_path):
    from peerhub.persistence.consensus_activation import activate_consensus_v2, read_activation_state
    from peerhub.persistence.sqlite import SqliteStateStore
    db = tmp_path / "e.sqlite3"
    SqliteStateStore(db, workspace_home_id="audit").initialize()
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    activate_consensus_v2(conn, now=5)
    conn.execute("UPDATE workspace_identity SET activation_epoch = activation_epoch + 1")  # restore/clone
    assert read_activation_state(conn) == "activated"
    conn.execute("UPDATE workspace_identity SET activation_epoch = activation_epoch - 5")  # rollback = wrong
    assert read_activation_state(conn) == "inconsistent"


def test_activation_probe_is_inconsistent_when_the_v2_table_is_missing(tmp_path):
    from peerhub.persistence.consensus_activation import activate_consensus_v2, read_activation_state
    from peerhub.persistence.sqlite import SqliteStateStore
    db = tmp_path / "m.sqlite3"
    SqliteStateStore(db, workspace_home_id="audit").initialize()
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    activate_consensus_v2(conn, now=5)
    conn.execute("DROP TABLE consensus_targets")
    assert read_activation_state(conn) == "inconsistent"


def test_broker_discovers_all_unfinished_effects_beyond_the_bounded_window(tmp_path):
    shell, broker, _ = env(tmp_path)
    for i in range(130):
        propose(shell, rid=f"n-{i}")
    assert len(broker.recover_all_pending_effects()) >= 130


# ---- P2-6: durable command identity ---------------------------------------------------
def test_replay_still_works_after_more_than_200_keyed_commands(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell, rule="never", elig=("p1", "p2"))
    first = shell.cast_vote("r1", "p1", "agree", idempotency_key="k0")
    for i in range(1, 230):
        shell.cast_vote("r1", "p2", "abstain" if i % 2 else "need_more_info", idempotency_key=f"k{i}")
    again = shell.cast_vote("r1", "p1", "agree", idempotency_key="k0")
    assert again.disposition is MutationDisposition.IDEMPOTENCY_HIT
    assert again.receipt.receipt_id == first.receipt.receipt_id


def test_identical_creation_retry_is_an_idempotent_replay(tmp_path):
    shell, broker, _ = env(tmp_path)
    args = ("r9", "T", "Q", "B", "p1", ["p1", "p2"], ["p1", "p2"], "high", "sha256:x", cfg())
    first = shell.propose_v2(*args, idempotency_key="create-1")
    second = shell.propose_v2(*args, idempotency_key="create-1")
    assert second.disposition is MutationDisposition.IDEMPOTENCY_HIT
    assert second.receipt.receipt_id == first.receipt.receipt_id
    other = ("r9", "DIFFERENT", "Q", "B", "p1", ["p1", "p2"], ["p1", "p2"], "high", "sha256:x", cfg())
    with pytest.raises(IdempotencyPayloadMismatchError):
        shell.propose_v2(*other, idempotency_key="create-1")


def test_fresh_v2_round_has_the_quorum_block_the_cli_prints(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell)
    q = state(broker)["quorum"]
    assert q["counted_votes"] == 0 and q["required_votes"] == 2 and q["reached"] is False


# ---- credentials of earlier ACK holders are re-verified at approval -------------------
def test_earlier_ack_holders_credentials_are_reverified_by_the_completing_ack(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell, verified=True)
    shell.cast_vote("r1", "p1", "agree", credential_id="cred-p1")
    shell.cast_vote("r1", "p2", "agree", credential_id="cred-p2")
    shell.final_call_ack("r1", "p1", credential_id="cred-p1")
    revoked = {"cred-p1"}
    shell._verifier = lambda *, credential_id, claimed_actor_id: (
        credential_id not in revoked and verifier(credential_id=credential_id, claimed_actor_id=claimed_actor_id)
    )
    with pytest.raises(AuthorizationError):
        shell.final_call_ack("r1", "p2", credential_id="cred-p2")
    assert state(broker)["phase"] == "final_call"
