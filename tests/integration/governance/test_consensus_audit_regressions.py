"""cx.astra audit (683644a) findings 1,2,4,6,7 reproduced as regression tests."""
from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import IdempotencyPayloadMismatchError, InvalidMutationError
from peerhub.governance.authority_fence import StaleAuthorityError
from peerhub.governance.authorization import AuthorizationError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus_shell import BrokerAuthorityVersionStore, ConsensusShell
from peerhub.governance.contract import EffectOutcome, MutationDisposition
from peerhub.persistence.sqlite import SqliteStateStore


class Health:
    def __init__(self):
        self.down = set()

    def check_health_gate(self, actor_id, evaluated_at):
        return actor_id not in self.down


def verifier(*, credential_id, claimed_actor_id):
    return credential_id == f"cred-{claimed_actor_id}"


def cfg(rule="always", votes=2):
    return {"formula": "max(2, N)", "required_votes": votes, "final_call_rule": rule,
            "deadlines": {}, "escalation_paths": ["human-tier-0"]}


def env(tmp_path: Path, tag="a", db="w.sqlite3", **kw):
    path = tmp_path / db
    store = SqliteStateStore(path, workspace_home_id="audit")
    if not path.exists():
        store.initialize()
    ids = FakeIdSource([f"{tag}-{i}" for i in range(1, 9000)])
    broker = GovernanceBroker(store, clock=FakeClock(range(1, 90_000)), ids=ids)
    health = Health()
    shell = ConsensusShell(broker, clock=FakeClock(range(1, 90_000)), ids=ids, verifier=verifier,
                           health_port=health, authority_store=BrokerAuthorityVersionStore(broker),
                           system_principals=frozenset({"system:proposal-recovery"}), **kw)
    return shell, broker, health


def propose(shell, rid="r1", rule="always", verified=False, elig=("p1", "p2")):
    shell.propose_v2(rid, "T", "Q", "B", elig[0], list(elig), list(elig), "high", "sha256:x",
                     cfg(rule), verified_required=verified)


def state(broker, rid="r1"):
    return broker.get_target(rid).state


# ---- finding 1: untrusted system principals / terminal immutability ------------------
def test_arbitrary_system_string_cannot_escalate(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell)
    with pytest.raises(AuthorizationError):
        shell.request_escalation("r1", "x", "system:untrusted", 0, "human-tier-0")
    assert "escalation" not in state(broker)


def test_allowlisted_system_principal_may_escalate_but_not_resolve(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell)
    shell.request_escalation("r1", "too_few_voters", "system:proposal-recovery", 0, "human-tier-0")
    assert state(broker)["escalation"]["reason"] == "too_few_voters"
    with pytest.raises(AuthorizationError):
        shell.resolve("r1", "approved", "system:proposal-recovery", "no")
    assert state(broker)["phase"] == "voting"


def test_terminal_decisions_are_immutable(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell)
    shell.request_escalation("r1", "self_finalization", "p1", 0, "human-tier-0")
    shell.resolve("r1", "approved", "p2", "administrator decision")
    assert state(broker)["phase"] == "approved"
    for outcome in ("approved", "rejected"):
        with pytest.raises(InvalidMutationError):
            shell.resolve("r1", outcome, "p2", "again")
    assert state(broker)["phase"] == "approved"


def test_every_mutation_on_a_verified_round_accepts_and_requires_credentials(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell, verified=True)
    with pytest.raises(AuthorizationError):
        shell.request_escalation("r1", "x", "p1", 0, "human-tier-0")
    shell.request_escalation("r1", "x", "p1", 0, "human-tier-0", credential_id="cred-p1")
    with pytest.raises(AuthorizationError):
        shell.resolve("r1", "rejected", "p2", "b")
    shell.resolve("r1", "rejected", "p2", "b", credential_id="cred-p2")
    assert state(broker)["phase"] == "rejected"


# ---- finding 2: Final Call barrier / revalidation / dissent / correction ---------------
def _to_final_call(shell):
    propose(shell)
    shell.cast_vote("r1", "p1", "agree")
    shell.cast_vote("r1", "p2", "agree")


def test_blockers_own_later_ack_resolves_their_concern(tmp_path):
    shell, broker, _ = env(tmp_path)
    _to_final_call(shell)
    shell.final_call_ack("r1", "p1")
    shell.final_call_nack("r1", "p2", nack_type="block")
    shell.final_call_ack("r1", "p2")
    assert state(broker)["phase"] == "approved"


def test_barrier_persists_when_others_complete_the_acks(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell, elig=("p1", "p2", "p3"))
    shell.cast_vote("r1", "p1", "agree")
    shell.cast_vote("r1", "p2", "agree")
    shell.final_call_ack("r1", "p3")            # p3 acknowledges ...
    shell.final_call_nack("r1", "p3", nack_type="block")  # ... then raises a qualifying concern
    shell.final_call_ack("r1", "p1")
    shell.final_call_ack("r1", "p2")            # every required ACK is in, the barrier still holds
    assert state(broker)["phase"] == "final_call"
    assert not [e for e in broker.recover_pending_effects()
                if e.event.payload.get("effect_kind") == "consensus.resolved"]


def test_completing_ack_revalidates_previous_ack_holders(tmp_path):
    shell, broker, health = env(tmp_path)
    _to_final_call(shell)
    shell.final_call_ack("r1", "p1")
    health.down.add("p1")
    with pytest.raises(AuthorizationError):
        shell.final_call_ack("r1", "p2")
    assert state(broker)["phase"] == "final_call"


def test_unresolved_block_vote_forces_final_call_even_when_rule_is_never(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell, rule="never", elig=("p1", "p2", "p3"))
    shell.cast_vote("r1", "p3", "block")
    shell.cast_vote("r1", "p1", "agree")
    shell.cast_vote("r1", "p2", "agree")
    assert state(broker)["phase"] == "final_call"


def test_correction_requires_fresh_votes_and_clears_concerns(tmp_path):
    shell, broker, _ = env(tmp_path)
    _to_final_call(shell)
    shell.correction("r1", "p1")
    st = state(broker)
    assert st["phase"] == "voting"
    assert dict(st["votes"]) == {}
    assert dict(st["ack_ledger"]) == {}
    shell.cast_vote("r1", "p1", "agree")
    assert state(broker)["phase"] == "voting"


# ---- finding 4: revocation atomic with its fence; creation authority ------------------
def _approved(shell):
    _to_final_call(shell)
    shell.final_call_ack("r1", "p1")
    shell.final_call_ack("r1", "p2")


def test_revocation_and_authority_bump_commit_together(tmp_path):
    shell, broker, _ = env(tmp_path)
    _approved(shell)
    auth = BrokerAuthorityVersionStore(broker)
    before = auth.read_version()
    shell.retraction("r1", "p1")
    assert tuple(state(broker)["revocations"]) == ("RevocationRecorded",)
    assert auth.read_version() == before + 1


def test_failed_authority_bump_leaves_no_revocation_recorded(tmp_path):
    shell, broker, _ = env(tmp_path)
    _approved(shell)
    auth = BrokerAuthorityVersionStore(broker)
    before, rev = auth.read_version(), broker.get_target("r1").revision
    real = broker.submit_atomic

    def failing(build, **kw):
        def bad(unit):
            list(build(unit))
            raise RuntimeError("injected failure after building both mutations")
        return real(bad, **kw)

    broker.submit_atomic = failing
    with pytest.raises(RuntimeError):
        shell.retraction("r1", "p1")
    assert auth.read_version() == before
    assert broker.get_target("r1").revision == rev
    assert "revocations" not in state(broker)


def test_creation_validates_authority_inside_its_transaction(tmp_path):
    shell, broker, _ = env(tmp_path)
    auth = BrokerAuthorityVersionStore(broker)
    real_submit = broker.submit
    bumped = {"done": False}

    def bump_then_submit(req, **kw):
        if not bumped["done"]:
            bumped["done"] = True
            broker.submit = real_submit
            auth.increment()
            broker.submit = bump_then_submit
        return real_submit(req, **kw)

    broker.submit = bump_then_submit
    with pytest.raises(StaleAuthorityError):
        propose(shell)
    assert broker.get_target("r1") is None


# ---- finding 6: idempotency ------------------------------------------------------------
def test_changed_payload_under_same_key_is_rejected_not_replayed(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell, rule="never")
    shell.cast_vote("r1", "p1", "agree", idempotency_key="k")
    with pytest.raises(IdempotencyPayloadMismatchError):
        shell.cast_vote("r1", "p1", "disagree", idempotency_key="k")


def test_scope_collision_between_round_and_actor_names_is_impossible(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell, rid="r", rule="never", elig=("p1:p2", "p9"))
    propose(shell, rid="r:p1", rule="never", elig=("p2", "p9"))
    a = shell.cast_vote("r", "p1:p2", "agree", idempotency_key="k")
    b = shell.cast_vote("r:p1", "p2", "agree", idempotency_key="k")
    assert b.disposition is MutationDisposition.COMMITTED
    assert a.receipt.receipt_id != b.receipt.receipt_id


def test_replay_of_a_credentialed_command_needs_a_valid_credential(tmp_path):
    shell, broker, _ = env(tmp_path)
    propose(shell, rule="never", verified=True)
    shell.cast_vote("r1", "p1", "agree", credential_id="cred-p1", idempotency_key="k")
    with pytest.raises(AuthorizationError):
        shell.cast_vote("r1", "p1", "agree", credential_id="bogus", idempotency_key="k")
    with pytest.raises(AuthorizationError):
        shell.cast_vote("r1", "p1", "agree", idempotency_key="k")


def test_stale_expected_revision_is_rejected_for_nack(tmp_path):
    from peerhub.core.errors import StaleRevisionError
    shell, broker, _ = env(tmp_path)
    _to_final_call(shell)
    with pytest.raises(StaleRevisionError):
        shell.final_call_nack("r1", "p2", nack_type="block", expected_revision=1)


# ---- finding 7: effect recovery ---------------------------------------------------------
def test_crashed_claim_is_resumed_by_the_same_owner(tmp_path):
    shell, broker, _ = env(tmp_path)
    _approved(shell)
    real_record = broker.record_effect_result

    def crash(*a, **k):
        raise RuntimeError("crash after claim")

    broker.record_effect_result = crash
    with pytest.raises(RuntimeError):
        shell.process_consensus_effects("r1")
    broker.record_effect_result = real_record
    receipts = shell.process_consensus_effects("r1")
    assert receipts and all(r.outcome is EffectOutcome.EFFECT_SUCCEEDED for r in receipts)
    assert shell.process_consensus_effects("r1") == ()


def test_many_noop_effects_do_not_hide_later_approval_effects(tmp_path):
    shell, broker, _ = env(tmp_path)
    for i in range(120):
        propose(shell, rid=f"noise-{i}", rule="always")
    propose(shell, rid="r-many", rule="always")
    shell.cast_vote("r-many", "p1", "agree")
    shell.cast_vote("r-many", "p2", "agree")
    shell.final_call_ack("r-many", "p1")
    shell.final_call_ack("r-many", "p2")
    receipts = shell.process_consensus_effects("r-many")
    assert len(receipts) >= 1
    assert any(r.outcome is EffectOutcome.EFFECT_SUCCEEDED for r in receipts)
