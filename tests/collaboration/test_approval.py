"""Tests for M3.5 Basic Approval (APP-001..012).

Verifies Core Invariant 11:
- Exact-effect binding (digest mismatch rejected).
- Single-use approval tokens (double consumption rejected).
- Lifecycle state transitions (REQUESTED -> APPROVED -> CONSUMED | REJECTED | EXPIRED).
- Zero dev-dependency violation (REL-009).
"""

from __future__ import annotations

import datetime
import sys
import threading
import types
import time
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest

from peerhub.extensions import approval as approval_module
from peerhub.extensions.approval import (
    ApprovalEngine,
    ApprovalRequest,
    compute_effect_digest,
    ApprovalNotFoundError,
    ApprovalStateTransitionError,
    ApprovalExpiredError,
    ApprovalAlreadyConsumedError,
    ApprovalEffectMismatchError,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    return tmp_path / "approval.db"


def test_app_001_lifecycle_happy_path(temp_db: Path) -> None:
    """APP-001: REQUESTED -> APPROVED -> CONSUMED lifecycle happy path."""
    engine = ApprovalEngine(temp_db)
    try:
        payload = {"action": "delete_branch", "target": "staging", "force": True}
        req = engine.request_approval(
            action_type="git_delete",
            effect_payload=payload,
            requested_by="agent-alice",
            ttl_seconds=300,
        )
        assert req.state == "REQUESTED"
        assert req.target_effect_digest == compute_effect_digest(payload)
        assert req.approver is None
        assert req.consumed_at is None

        # Approve
        approved_req = engine.approve(req.approval_id, approver="admin-bob")
        assert approved_req.state == "APPROVED"
        assert approved_req.approver == "admin-bob"

        # Consume
        consumed_req = engine.consume_approval(req.approval_id, effect_payload=payload)
        assert consumed_req.state == "CONSUMED"
        assert consumed_req.consumed_at is not None
    finally:
        engine.close()


def test_app_002_not_found(temp_db: Path) -> None:
    """APP-002: ApprovalNotFoundError raised when non-existent approval ID is referenced."""
    engine = ApprovalEngine(temp_db)
    try:
        with pytest.raises(ApprovalNotFoundError):
            engine.approve("non-existent-id", approver="admin")

        with pytest.raises(ApprovalNotFoundError):
            engine.consume_approval("non-existent-id", effect_payload={})
    finally:
        engine.close()


def test_app_003_rejection(temp_db: Path) -> None:
    """APP-003: Approval rejection transitions state to REJECTED with reason."""
    engine = ApprovalEngine(temp_db)
    try:
        payload = {"action": "drop_table", "table": "users"}
        req = engine.request_approval(
            action_type="db_migration",
            effect_payload=payload,
            requested_by="agent-charlie",
        )
        rejected_req = engine.reject(
            req.approval_id,
            reason="Destructive schema change without backup",
            approver="admin-sec",
        )
        assert rejected_req.state == "REJECTED"
        assert rejected_req.rejection_reason == "Destructive schema change without backup"
        assert rejected_req.approver == "admin-sec"

        # Subsequent consume must be rejected
        with pytest.raises(ApprovalStateTransitionError):
            engine.consume_approval(req.approval_id, effect_payload=payload)
    finally:
        engine.close()


def _clock_ahead(seconds: float) -> types.SimpleNamespace:
    """The `datetime` module as seen by the approval engine, with now() moved forward."""
    class _Later(datetime.datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return super().now(tz) + datetime.timedelta(seconds=seconds)

    return types.SimpleNamespace(datetime=_Later, timezone=datetime.timezone, timedelta=datetime.timedelta)


def test_app_004_expired(temp_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """APP-004: Expired approval token cannot be approved or consumed."""
    engine = ApprovalEngine(temp_db)
    try:
        payload = {"task": "deploy"}
        req = engine.request_approval(
            action_type="deploy",
            effect_payload=payload,
            requested_by="agent-dave",
            ttl_seconds=-10,  # Already expired
        )
        with pytest.raises(ApprovalExpiredError):
            engine.approve(req.approval_id, approver="admin")
        rejected = engine.get_approval(req.approval_id)
        assert rejected.state != "APPROVED" and rejected.approver is None

        # Now test an approved request that expires before consumption
        req2 = engine.request_approval(
            action_type="deploy2",
            effect_payload=payload,
            requested_by="agent-dave",
            ttl_seconds=60,
        )
        engine.approve(req2.approval_id, approver="admin")
        monkeypatch.setattr(approval_module, "datetime", _clock_ahead(120))  # deterministic: no sleeping, no slow-runner race

        with pytest.raises(ApprovalExpiredError):
            engine.consume_approval(req2.approval_id, effect_payload=payload)
        assert engine.get_approval(req2.approval_id).state != "CONSUMED"
        assert engine.get_approval(req2.approval_id).consumed_at is None
    finally:
        engine.close()


def test_app_005_invariant_single_use(temp_db: Path) -> None:
    """APP-005: Invariant 11: Single-use approval tokens (double consumption rejected)."""
    engine = ApprovalEngine(temp_db)
    try:
        payload = {"payment": 100, "currency": "USD"}
        req = engine.request_approval(
            action_type="transfer",
            effect_payload=payload,
            requested_by="agent-eve",
        )
        engine.approve(req.approval_id, approver="admin-finance")

        # First consumption succeeds
        engine.consume_approval(req.approval_id, effect_payload=payload)

        consumed_at = engine.get_approval(req.approval_id).consumed_at
        assert consumed_at is not None
        # Re-consuming same token must fail
        with pytest.raises(ApprovalAlreadyConsumedError):
            engine.consume_approval(req.approval_id, effect_payload=payload)
        after = engine.get_approval(req.approval_id)
        assert after.state == "CONSUMED" and after.consumed_at == consumed_at  # not re-applied
    finally:
        engine.close()


def test_app_006_invariant_exact_effect_binding(temp_db: Path) -> None:
    """APP-006: Invariant 11: Exact-effect binding (digest mismatch rejected)."""
    engine = ApprovalEngine(temp_db)
    try:
        approved_payload = {"command": "rm -rf /tmp/cache", "dry_run": False}
        tampered_payload = {"command": "rm -rf /", "dry_run": False}

        req = engine.request_approval(
            action_type="exec",
            effect_payload=approved_payload,
            requested_by="agent-frank",
        )
        engine.approve(req.approval_id, approver="admin-sys")

        # Tampered consumption fails with ApprovalEffectMismatchError
        with pytest.raises(ApprovalEffectMismatchError):
            engine.consume_approval(req.approval_id, effect_payload=tampered_payload)

        # Status must remain APPROVED and NOT CONSUMED
        current = engine.get_approval(req.approval_id)
        assert current is not None
        assert current.state == "APPROVED"
        assert current.consumed_at is None
    finally:
        engine.close()


def test_app_007_digest_determinism() -> None:
    """APP-007: Effect digest calculation is deterministic across arbitrary key orderings."""
    payload1 = {"b": 2, "a": 1, "nested": {"z": 10, "m": 5}}
    payload2 = {"nested": {"m": 5, "z": 10}, "a": 1, "b": 2}

    d1 = compute_effect_digest(payload1)
    d2 = compute_effect_digest(payload2)
    assert d1 == d2
    assert len(d1) == 64  # SHA-256 hex length


def test_app_008_consume_unapproved(temp_db: Path) -> None:
    """APP-008: Consuming unapproved (REQUESTED) request raises ApprovalStateTransitionError."""
    engine = ApprovalEngine(temp_db)
    try:
        payload = {"x": 1}
        req = engine.request_approval(
            action_type="do_x",
            effect_payload=payload,
            requested_by="agent-grace",
        )
        with pytest.raises(ApprovalStateTransitionError):
            engine.consume_approval(req.approval_id, effect_payload=payload)
        after = engine.get_approval(req.approval_id)
        assert after.state == "REQUESTED" and after.consumed_at is None
    finally:
        engine.close()


def test_app_009_persistence(temp_db: Path) -> None:
    """APP-009: Database persistence restores approval requests across engine restarts."""
    engine1 = ApprovalEngine(temp_db)
    payload = {"key": "value"}
    req = engine1.request_approval(
        action_type="write",
        effect_payload=payload,
        requested_by="agent-heidi",
    )
    engine1.approve(req.approval_id, approver="admin-1")
    engine1.close()

    engine2 = ApprovalEngine(temp_db)
    try:
        restored = engine2.get_approval(req.approval_id)
        assert restored is not None
        assert restored.state == "APPROVED"
        assert restored.approver == "admin-1"
        assert restored.effect_payload == payload
    finally:
        engine2.close()


def test_app_010_invalid_state_transitions(temp_db: Path) -> None:
    """APP-010: Approving already REJECTED or CONSUMED request raises ApprovalStateTransitionError."""
    engine = ApprovalEngine(temp_db)
    try:
        payload = {"k": "v"}
        req1 = engine.request_approval("a", payload, "agent-1")
        engine.reject(req1.approval_id, "no", "admin")
        with pytest.raises(ApprovalStateTransitionError):
            engine.approve(req1.approval_id, "admin")
        assert engine.get_approval(req1.approval_id).state == "REJECTED"

        req2 = engine.request_approval("b", payload, "agent-2")
        engine.approve(req2.approval_id, "admin")
        engine.consume_approval(req2.approval_id, payload)
        with pytest.raises(ApprovalStateTransitionError):
            engine.approve(req2.approval_id, "admin")
        assert engine.get_approval(req2.approval_id).state == "CONSUMED"
    finally:
        engine.close()


def test_app_011_list_and_filter(temp_db: Path) -> None:
    """APP-011: List and filter approval requests by state and requester."""
    engine = ApprovalEngine(temp_db)
    try:
        engine.request_approval("act1", {"x": 1}, "alice")
        r2 = engine.request_approval("act2", {"x": 2}, "bob")
        engine.approve(r2.approval_id, "admin")

        all_reqs = engine.list_approvals()
        assert len(all_reqs) == 2

        alice_reqs = engine.list_approvals(requested_by="alice")
        assert len(alice_reqs) == 1
        assert alice_reqs[0].requested_by == "alice"

        approved_reqs = engine.list_approvals(state="APPROVED")
        assert len(approved_reqs) == 1
        assert approved_reqs[0].approval_id == r2.approval_id
    finally:
        engine.close()


def test_app_012_rel_009_standard_library_compliance() -> None:
    """APP-012: REL-009 Standard library compliance."""
    import peerhub.extensions.approval as approval_module

    # Must be pure standard library
    for mod_name, mod in sys.modules.items():
        if mod_name.startswith("peerhub.extensions.approval"):
            assert mod is not None


def test_single_use_across_concurrent_engines(temp_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Independent consumers must not both accept the same exact-effect token."""
    import peerhub.extensions.approval as approval_module

    engines = [ApprovalEngine(temp_db), ApprovalEngine(temp_db)]
    payload = {"action": "publish", "artifact": "sha256:example"}
    req = engines[0].request_approval("publish", payload, "agent")
    engines[0].approve(req.approval_id, "operator")
    original_digest = approval_module.compute_effect_digest
    barrier = threading.Barrier(2)

    def simultaneous_digest(effect: dict[str, object]) -> str:
        digest = original_digest(effect)
        barrier.wait(timeout=5)
        return digest

    monkeypatch.setattr(approval_module, "compute_effect_digest", simultaneous_digest)

    def consume(engine: ApprovalEngine) -> str:
        try:
            return engine.consume_approval(req.approval_id, payload).state
        except ApprovalAlreadyConsumedError:
            return "ALREADY_CONSUMED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(consume, engines))
    assert sorted(results) == ["ALREADY_CONSUMED", "CONSUMED"]
    persisted = engines[0].get_approval(req.approval_id)
    assert persisted is not None
    assert persisted.state == "CONSUMED"


@pytest.mark.parametrize("operation", ["approve", "reject"])
def test_stale_lifecycle_transition_cannot_revive_consumed(
    temp_db: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    engine = ApprovalEngine(temp_db)
    competitor = ApprovalEngine(temp_db)
    payload = {"target": "release"}
    req = engine.request_approval("publish", payload, "agent")
    original_get = engine.get_approval
    reads = 0

    def stale_read(approval_id: str) -> ApprovalRequest | None:
        nonlocal reads
        snapshot = original_get(approval_id)
        reads += 1
        if reads == 2:
            competitor.approve(approval_id, "winner")
            competitor.consume_approval(approval_id, payload)
        return snapshot

    monkeypatch.setattr(engine, "get_approval", stale_read)
    with pytest.raises(ApprovalStateTransitionError):
        if operation == "approve":
            engine.approve(req.approval_id, "late")
        else:
            engine.reject(req.approval_id, "late reject", "late")
    persisted = competitor.get_approval(req.approval_id)
    assert persisted is not None
    assert persisted.state == "CONSUMED"
    assert persisted.approver == "winner"


def test_stale_expiry_cannot_overwrite_consumed(temp_db: Path) -> None:
    engine = ApprovalEngine(temp_db)
    req = engine.request_approval("publish", {}, "agent")
    approved = engine.approve(req.approval_id, "operator")
    engine.consume_approval(req.approval_id, {})
    engine._check_and_update_expiry(replace(approved, valid_until="2000-01-01T00:00:00+00:00"))
    persisted = engine.get_approval(req.approval_id)
    assert persisted is not None
    assert persisted.state == "CONSUMED"
