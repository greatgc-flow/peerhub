"""Tests for candidate binding and authority fence."""

import pytest

from peerhub.governance.authority_fence import (
    AuthorityFence,
    StaleAuthorityError,
)
from peerhub.governance.candidate import (
    AckLedger,
    Candidate,
    apply_retraction,
)


class FakeAuthorityVersionStore:
    def __init__(self) -> None:
        self.version = 1

    def read_version(self) -> int:
        return self.version

    def increment(self) -> int:
        self.version += 1
        return self.version


def test_ack_ledger_bind_ack_stores_participant() -> None:
    ledger = AckLedger()
    candidate = Candidate(round_id="r1", expected_revision=1, target_state_hash="h1")
    ledger.bind_ack(candidate, "p1")
    assert "p1" in ledger.acks_for(candidate)


def test_ack_ledger_bind_ack_different_candidate_does_not_count() -> None:
    ledger = AckLedger()
    c1 = Candidate(round_id="r1", expected_revision=1, target_state_hash="h1")
    c2 = Candidate(round_id="r1", expected_revision=2, target_state_hash="h2")
    ledger.bind_ack(c1, "p1")
    assert "p1" not in ledger.acks_for(c2)


def test_ack_ledger_invalidate_drops_all_acks() -> None:
    ledger = AckLedger()
    c1 = Candidate(round_id="r1", expected_revision=1, target_state_hash="h1")
    ledger.bind_ack(c1, "p1")
    ledger.invalidate("test")
    assert "p1" not in ledger.acks_for(c1)


def test_ack_ledger_is_complete_true_when_all_required_acked() -> None:
    ledger = AckLedger()
    c1 = Candidate(round_id="r1", expected_revision=1, target_state_hash="h1")
    ledger.bind_ack(c1, "p1")
    ledger.bind_ack(c1, "p2")
    assert ledger.is_complete(c1, ["p1", "p2"]) is True


def test_ack_ledger_is_complete_false_when_missing_ack() -> None:
    ledger = AckLedger()
    c1 = Candidate(round_id="r1", expected_revision=1, target_state_hash="h1")
    ledger.bind_ack(c1, "p1")
    assert ledger.is_complete(c1, ["p1", "p2"]) is False


def test_ack_ledger_is_complete_false_when_ack_for_different_candidate() -> None:
    ledger = AckLedger()
    c1 = Candidate(round_id="r1", expected_revision=1, target_state_hash="h1")
    c2 = Candidate(round_id="r1", expected_revision=2, target_state_hash="h2")
    ledger.bind_ack(c1, "p1")
    ledger.bind_ack(c2, "p2")
    assert ledger.is_complete(c1, ["p1", "p2"]) is False


def test_apply_retraction_pre_authorization() -> None:
    ledger = AckLedger()
    c1 = Candidate(round_id="r1", expected_revision=1, target_state_hash="h1")
    ledger.bind_ack(c1, "p1")
    outcome = apply_retraction(ledger, c1, authorized=False)
    assert outcome.state == "voting"
    assert "p1" not in ledger.acks_for(c1)


def test_apply_retraction_post_authorization() -> None:
    ledger = AckLedger()
    c1 = Candidate(round_id="r1", expected_revision=1, target_state_hash="h1")
    ledger.bind_ack(c1, "p1")
    outcome = apply_retraction(ledger, c1, authorized=True)
    assert outcome.state == "RevocationRecorded"
    assert "p1" in ledger.acks_for(c1)


def test_authority_fence_check_passes_when_match() -> None:
    store = FakeAuthorityVersionStore()
    fence = AuthorityFence(store)
    fence.check(1)


def test_authority_fence_check_raises_when_mismatch() -> None:
    store = FakeAuthorityVersionStore()
    fence = AuthorityFence(store)
    with pytest.raises(StaleAuthorityError):
        fence.check(2)


def test_authority_fence_bump_for_increments_version() -> None:
    store = FakeAuthorityVersionStore()
    fence = AuthorityFence(store)
    fence.bump_for("test")
    assert store.read_version() == 2


def test_authority_fence_claim_returns_applied_first_time() -> None:
    store = FakeAuthorityVersionStore()
    fence = AuthorityFence(store)
    c1 = Candidate(round_id="r1", expected_revision=1, target_state_hash="h1")
    assert fence.claim(c1, "e1", 1) == "applied"


def test_authority_fence_claim_returns_already_applied_for_identical() -> None:
    store = FakeAuthorityVersionStore()
    fence = AuthorityFence(store)
    c1 = Candidate(round_id="r1", expected_revision=1, target_state_hash="h1")
    fence.claim(c1, "e1", 1)
    assert fence.claim(c1, "e1", 1) == "already_applied"


def test_authority_fence_claim_raises_for_stale_authority() -> None:
    store = FakeAuthorityVersionStore()
    fence = AuthorityFence(store)
    c1 = Candidate(round_id="r1", expected_revision=1, target_state_hash="h1")
    store.increment()
    with pytest.raises(StaleAuthorityError):
        fence.claim(c1, "e1", 1)
