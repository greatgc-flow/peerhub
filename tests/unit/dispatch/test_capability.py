"""Unit tests for capability authority types and pure invariants."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, fields, replace

import pytest

from peerhub.core.protocol import CommandID
from peerhub.dispatch.capability import (
    CapabilityGrantDecision,
    CapabilityLease,
    CapabilityLeaseViolation,
    CapabilityTier,
    EnforcementLevel,
    ValidatedCapabilityBinding,
    mandatory_enforcement_floor,
    validate_capability_binding,
)
from peerhub.dispatch.contract import (
    AdmissionReceipt,
    CompletionContract,
    CompletionContractKind,
    LeaseFenceTuple,
    LeaseSnapshot,
    LeaseState,
    RequestSnapshot,
    RequestState,
)


_COMMAND_ID = CommandID("command-01")
_ROUTE_DIGEST = "1" * 64
_POLICY_REVISION = 7


@dataclass(frozen=True)
class _BindingRecords:
    request: RequestSnapshot
    receipt: AdmissionReceipt
    session_lease: LeaseSnapshot
    capability_lease: CapabilityLease


def _binding_records() -> _BindingRecords:
    completion_contract = CompletionContract(
        contract_id="contract-01",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=False,
    )
    request = RequestSnapshot(
        command_id=_COMMAND_ID,
        client_id="client-01",
        client_request_id="client-request-01",
        correlation_id="correlation-01",
        authenticated_principal="principal-01",
        command_type="peer.ask",
        idempotency_key="idempotency-01",
        payload_digest="0" * 64,
        scope={},
        params={},
        expected_policy_revision=_POLICY_REVISION,
        expected_configuration_revision=1,
        policy_revision=_POLICY_REVISION,
        configuration_revision=1,
        completion_contract=completion_contract,
        selected_peer_instance_id="cx-instance-01",
        selected_profile_id="cx-profile-01",
        route_decision_digest=_ROUTE_DIGEST,
        lease_id="session-lease-01",
        state=RequestState.ADMITTED,
        revision=1,
        created_at=10,
        updated_at=10,
    )
    receipt = AdmissionReceipt(
        admission_receipt_id="admission-receipt-01",
        command_id=_COMMAND_ID,
        client_id="client-01",
        client_request_id="client-request-01",
        command_type="peer.ask",
        idempotency_key="idempotency-01",
        payload_digest="0" * 64,
        completion_contract_id="contract-01",
        lease_id="session-lease-01",
        policy_revision=_POLICY_REVISION,
        configuration_revision=1,
        admitted_at=10,
    )
    fence = LeaseFenceTuple(
        session_id="session-01",
        lease_id="session-lease-01",
        fencing_token=1,
        revision=1,
        owner_principal_id="principal-01",
        owner_instance_id="owner-instance-01",
        owner_process_birth_identity=None,
        command_id=_COMMAND_ID,
        authority_epoch=1,
    )
    session_lease = LeaseSnapshot(
        lease_id="session-lease-01",
        session_id="session-01",
        fence=fence,
        state=LeaseState.RESERVED,
        heartbeat_expires_at=100,
        created_at=10,
        updated_at=10,
    )
    capability_lease = CapabilityLease(
        capability_lease_id="capability-lease-01",
        command_id=_COMMAND_ID,
        admission_receipt_id="admission-receipt-01",
        session_lease_id="session-lease-01",
        subject_principal_id="principal-01",
        selected_peer_kind="cx",
        required_tier=CapabilityTier.WORKTREE_WRITE,
        authorized_tier=CapabilityTier.WORKTREE_WRITE,
        minimum_enforcement=EnforcementLevel.ENFORCED,
        selected_peer_instance_id="cx-instance-01",
        selected_profile_id="cx-profile-01",
        route_decision_digest=_ROUTE_DIGEST,
        policy_revision=_POLICY_REVISION,
        issuer_id="capability-policy-01",
        issued_at=10,
        expires_at=100,
    )
    return _BindingRecords(
        request=request,
        receipt=receipt,
        session_lease=session_lease,
        capability_lease=capability_lease,
    )


def _validate(records: _BindingRecords) -> ValidatedCapabilityBinding:
    return validate_capability_binding(
        records.request,
        records.receipt,
        records.session_lease,
        records.capability_lease,
        expected_peer_kind="cx",
    )


def _assert_binding_violation(records: _BindingRecords) -> None:
    with pytest.raises(CapabilityLeaseViolation):
        _validate(records)


def test_capability_types_construct_the_positive_path() -> None:
    records = _binding_records()
    decision = CapabilityGrantDecision(
        granted=True,
        subject_principal_id="principal-01",
        selected_peer_kind="cx",
        selected_peer_instance_id="cx-instance-01",
        selected_profile_id="cx-profile-01",
        required_tier=CapabilityTier.WORKTREE_WRITE,
        authorized_tier=CapabilityTier.GIT_MUTATE,
        minimum_enforcement=EnforcementLevel.ENFORCED,
        policy_revision=_POLICY_REVISION,
        issuer_id="capability-policy-01",
    )

    binding = _validate(records)

    assert decision.granted is True
    assert decision.authorized_tier is CapabilityTier.GIT_MUTATE
    assert binding.capability_lease is records.capability_lease
    assert CapabilityTier.REMOTE_MUTATE > CapabilityTier.GIT_MUTATE
    assert EnforcementLevel.CONFINED > EnforcementLevel.ENFORCED


def test_capability_lease_has_the_exact_ratified_field_set() -> None:
    assert tuple(field.name for field in fields(CapabilityLease)) == (
        "capability_lease_id",
        "command_id",
        "admission_receipt_id",
        "session_lease_id",
        "subject_principal_id",
        "selected_peer_kind",
        "required_tier",
        "authorized_tier",
        "minimum_enforcement",
        "selected_peer_instance_id",
        "selected_profile_id",
        "route_decision_digest",
        "policy_revision",
        "issuer_id",
        "issued_at",
        "expires_at",
    )


def test_capability_lease_is_frozen() -> None:
    capability_lease = _binding_records().capability_lease
    with pytest.raises(FrozenInstanceError):
        capability_lease.issuer_id = "different-issuer"  # type: ignore[misc]


def test_denied_grant_decision_requires_a_reason_and_no_authority() -> None:
    decision = CapabilityGrantDecision(
        granted=False,
        subject_principal_id="principal-01",
        selected_peer_kind="cx",
        selected_peer_instance_id="cx-instance-01",
        selected_profile_id="cx-profile-01",
        required_tier=CapabilityTier.GIT_MUTATE,
        authorized_tier=None,
        minimum_enforcement=None,
        policy_revision=_POLICY_REVISION,
        issuer_id="capability-policy-01",
        denial_reason="policy denied git mutation",
    )

    assert decision.granted is False
    assert decision.authorized_tier is None
    assert decision.denial_reason == "policy denied git mutation"


def test_grant_decision_rejects_authority_below_required_tier() -> None:
    with pytest.raises(ValueError, match="below required_tier"):
        CapabilityGrantDecision(
            granted=True,
            subject_principal_id="principal-01",
            selected_peer_kind="cx",
            selected_peer_instance_id="cx-instance-01",
            selected_profile_id="cx-profile-01",
            required_tier=CapabilityTier.GIT_MUTATE,
            authorized_tier=CapabilityTier.WORKTREE_WRITE,
            minimum_enforcement=EnforcementLevel.ENFORCED,
            policy_revision=_POLICY_REVISION,
            issuer_id="capability-policy-01",
        )


def test_rejects_request_command_mismatch() -> None:
    records = _binding_records()
    _assert_binding_violation(
        replace(
            records,
            request=replace(
                records.request,
                command_id=CommandID("different-command"),
            ),
        )
    )


def test_rejects_receipt_command_mismatch() -> None:
    records = _binding_records()
    _assert_binding_violation(
        replace(
            records,
            receipt=replace(
                records.receipt,
                command_id=CommandID("different-command"),
            ),
        )
    )


def test_rejects_session_lease_command_mismatch() -> None:
    records = _binding_records()
    mismatched_fence = replace(
        records.session_lease.fence,
        command_id=CommandID("different-command"),
    )
    _assert_binding_violation(
        replace(
            records,
            session_lease=replace(
                records.session_lease,
                fence=mismatched_fence,
            ),
        )
    )


def test_rejects_admission_receipt_id_mismatch() -> None:
    records = _binding_records()
    _assert_binding_violation(
        replace(
            records,
            capability_lease=replace(
                records.capability_lease,
                admission_receipt_id="different-receipt",
            ),
        )
    )


def test_rejects_request_session_lease_id_mismatch() -> None:
    records = _binding_records()
    _assert_binding_violation(
        replace(
            records,
            request=replace(
                records.request,
                lease_id="different-lease",
            ),
        )
    )


def test_rejects_receipt_session_lease_id_mismatch() -> None:
    records = _binding_records()
    _assert_binding_violation(
        replace(
            records,
            receipt=replace(
                records.receipt,
                lease_id="different-lease",
            ),
        )
    )


def test_rejects_session_lease_id_mismatch() -> None:
    records = _binding_records()
    mismatched_fence = replace(
        records.session_lease.fence,
        lease_id="different-lease",
    )
    _assert_binding_violation(
        replace(
            records,
            session_lease=replace(
                records.session_lease,
                lease_id="different-lease",
                fence=mismatched_fence,
            ),
        )
    )


def test_rejects_subject_principal_mismatch() -> None:
    records = _binding_records()
    _assert_binding_violation(
        replace(
            records,
            request=replace(
                records.request,
                authenticated_principal="different-principal",
            ),
        )
    )


def test_rejects_peer_instance_mismatch() -> None:
    records = _binding_records()
    _assert_binding_violation(
        replace(
            records,
            request=replace(
                records.request,
                selected_peer_instance_id="different-instance",
            ),
        )
    )


def test_rejects_profile_mismatch() -> None:
    records = _binding_records()
    _assert_binding_violation(
        replace(
            records,
            request=replace(
                records.request,
                selected_profile_id="different-profile",
            ),
        )
    )


def test_rejects_route_digest_mismatch() -> None:
    records = _binding_records()
    _assert_binding_violation(
        replace(
            records,
            request=replace(
                records.request,
                route_decision_digest="2" * 64,
            ),
        )
    )


def test_rejects_machine_owned_peer_kind_mismatch() -> None:
    records = _binding_records()
    with pytest.raises(CapabilityLeaseViolation):
        validate_capability_binding(
            records.request,
            records.receipt,
            records.session_lease,
            records.capability_lease,
            expected_peer_kind="ag",
        )


def test_rejects_request_policy_revision_mismatch() -> None:
    records = _binding_records()
    _assert_binding_violation(
        replace(
            records,
            request=replace(records.request, policy_revision=8),
        )
    )


def test_rejects_receipt_policy_revision_mismatch() -> None:
    records = _binding_records()
    _assert_binding_violation(
        replace(
            records,
            receipt=replace(records.receipt, policy_revision=8),
        )
    )


def test_rejects_authorized_tier_different_from_required_tier() -> None:
    records = _binding_records()
    _assert_binding_violation(
        replace(
            records,
            capability_lease=replace(
                records.capability_lease,
                authorized_tier=CapabilityTier.GIT_MUTATE,
            ),
        )
    )


@pytest.mark.parametrize(
    "tier",
    (
        CapabilityTier.WORKTREE_WRITE,
        CapabilityTier.GIT_MUTATE,
        CapabilityTier.REMOTE_MUTATE,
    ),
)
def test_ag_mutating_requires_confined_enforcement(
    tier: CapabilityTier,
) -> None:
    floor = mandatory_enforcement_floor("ag", tier)

    assert floor is EnforcementLevel.CONFINED
    assert EnforcementLevel.ADVISORY < floor
    assert EnforcementLevel.ENFORCED < floor


def test_read_only_and_non_ag_floor_matrix() -> None:
    assert (
        mandatory_enforcement_floor("ag", CapabilityTier.READ_ONLY)
        is EnforcementLevel.ADVISORY
    )
    assert (
        mandatory_enforcement_floor(
            "cx",
            CapabilityTier.WORKTREE_WRITE,
        )
        is EnforcementLevel.ENFORCED
    )
