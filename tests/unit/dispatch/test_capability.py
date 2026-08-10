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
    PeerEnforcementEvidence,
    ValidatedCapabilityBinding,
    mandatory_enforcement_floor,
    require_enforcement_floor,
    validate_capability_binding,
)
from peerhub.dispatch.capability_policy import (
    StaticCapabilityPolicy,
    StaticPeerEnforcementEvidenceProvider,
    default_capability_policy,
    default_enforcement_evidence_provider,
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
        required_capability_tier=CapabilityTier.WORKTREE_WRITE,
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


def test_rejects_required_tier_different_from_request() -> None:
    records = _binding_records()
    _assert_binding_violation(
        replace(
            records,
            request=replace(
                records.request,
                required_capability_tier=CapabilityTier.GIT_MUTATE,
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


# --- The shipped defaults (peerhub.dispatch.capability_policy) -------------
#
# capability_policy.py is what a production DispatchService/AdmissionCoordinator
# falls back to when nothing is injected, so its behavior IS the deployed
# security posture.  These tests pin the two claims its module docstring makes:
# no real peer carries a measured enforcement ceiling, and the policy grants
# least privilege only.

_MUTATING_TIERS = (
    CapabilityTier.WORKTREE_WRITE,
    CapabilityTier.GIT_MUTATE,
    CapabilityTier.REMOTE_MUTATE,
)

_BUILTIN_PEERS = ("ag", "cc", "cx")


@pytest.mark.parametrize("peer", _BUILTIN_PEERS)
def test_shipped_evidence_provider_claims_no_measured_ceiling(
    peer: str,
) -> None:
    """Every built-in peer resolves to an absent, DIR-004-tagged ceiling."""

    evidence = default_enforcement_evidence_provider().resolve(
        peer_instance_id=peer,
        profile_id=f"{peer}.standard",
    )

    assert evidence.peer_kind == peer
    assert evidence.peer_instance_id == peer
    assert evidence.enforcement_ceiling is None
    assert evidence.source_tag == "absent"


def test_shipped_evidence_provider_treats_unknown_instances_as_unmeasured() -> None:
    """An unmapped instance is unknown, never optimistically ADVISORY."""

    evidence = default_enforcement_evidence_provider().resolve(
        peer_instance_id="some-instance-nobody-measured",
        profile_id="whatever.profile",
    )

    assert evidence.peer_kind == "some-instance-nobody-measured"
    assert evidence.enforcement_ceiling is None
    assert evidence.source_tag == "absent"


@pytest.mark.parametrize("peer", _BUILTIN_PEERS)
@pytest.mark.parametrize("tier", _MUTATING_TIERS)
def test_shipped_evidence_cannot_satisfy_any_mutating_floor(
    peer: str,
    tier: CapabilityTier,
) -> None:
    """No built-in peer can be granted a mutating tier as shipped.

    This is the by-construction claim in ``capability_policy``'s docstring:
    the denial is not a peer-specific carve-out, it holds for the whole
    built-in cross product.
    """

    evidence = default_enforcement_evidence_provider().resolve(
        peer_instance_id=peer,
        profile_id=f"{peer}.standard",
    )

    with pytest.raises(CapabilityLeaseViolation) as exc_info:
        require_enforcement_floor(evidence.peer_kind, tier, evidence)

    assert exc_info.value.invariant == (
        "selected adapter has no measured enforcement evidence for the "
        "mandatory enforcement floor"
    )


@pytest.mark.parametrize("peer", _BUILTIN_PEERS)
def test_shipped_evidence_still_satisfies_the_read_only_floor(
    peer: str,
) -> None:
    """READ_ONLY's ADVISORY floor needs no measurement -- fail closed, not always."""

    evidence = default_enforcement_evidence_provider().resolve(
        peer_instance_id=peer,
        profile_id=f"{peer}.standard",
    )

    assert (
        require_enforcement_floor(
            evidence.peer_kind,
            CapabilityTier.READ_ONLY,
            evidence,
        )
        is EnforcementLevel.ADVISORY
    )


def test_require_enforcement_floor_rejects_a_ceiling_below_the_floor() -> None:
    """A measured but too-weak ceiling is denied, not rounded up."""

    weak = PeerEnforcementEvidence(
        peer_instance_id="ag",
        peer_kind="ag",
        enforcement_ceiling=EnforcementLevel.ENFORCED,
        source_tag="controlled_fake",
    )

    # ag + mutating demands CONFINED; ENFORCED is one level short.
    with pytest.raises(CapabilityLeaseViolation) as exc_info:
        require_enforcement_floor(
            "ag",
            CapabilityTier.WORKTREE_WRITE,
            weak,
        )

    assert exc_info.value.invariant == (
        "selected adapter cannot meet the mandatory enforcement floor"
    )


@pytest.mark.parametrize("tier", (CapabilityTier.READ_ONLY,) + _MUTATING_TIERS)
def test_shipped_policy_grants_least_privilege_only(
    tier: CapabilityTier,
) -> None:
    """``authorized_tier`` equals ``required_tier`` -- never a wider grant."""

    decision = default_capability_policy().decide(
        subject_principal_id="principal-01",
        selected_peer_kind="cx",
        selected_peer_instance_id="cx-instance-01",
        selected_profile_id="cx-profile-01",
        policy_revision=_POLICY_REVISION,
        required_tier=tier,
        minimum_enforcement=EnforcementLevel.CONFINED,
    )

    assert decision.granted is True
    assert decision.authorized_tier is tier
    assert decision.required_tier is tier
    # The code-owned floor handed in is carried through, never lowered.
    assert decision.minimum_enforcement is EnforcementLevel.CONFINED
    assert decision.denial_reason is None


def test_static_policy_denies_configured_tiers_without_authority() -> None:
    """A denied decision carries a reason and no tier or enforcement level."""

    policy = StaticCapabilityPolicy(
        denied_tiers=frozenset({CapabilityTier.REMOTE_MUTATE}),
    )

    decision = policy.decide(
        subject_principal_id="principal-01",
        selected_peer_kind="cx",
        selected_peer_instance_id="cx-instance-01",
        selected_profile_id="cx-profile-01",
        policy_revision=_POLICY_REVISION,
        required_tier=CapabilityTier.REMOTE_MUTATE,
        minimum_enforcement=EnforcementLevel.ENFORCED,
    )

    assert decision.granted is False
    assert decision.authorized_tier is None
    assert decision.minimum_enforcement is None
    assert decision.denial_reason == "tier denied by policy"


def test_shipped_policy_revalidation_fails_closed_on_a_revision_change() -> None:
    """A rotated policy revision revokes an already-issued lease.

    This is the revocation window the pre-merge review flagged: the lease was
    minted under revision 7 and is presented while the live policy has moved
    on.  ``revalidate`` must reject it rather than honor the stale grant.
    """

    binding = _validate(_binding_records())

    with pytest.raises(CapabilityLeaseViolation) as exc_info:
        default_capability_policy().revalidate(
            binding,
            current_policy_revision=_POLICY_REVISION + 1,
            now=binding.capability_lease.issued_at,
        )

    assert exc_info.value.invariant == (
        "capability lease policy revision differs from the current "
        "policy revision"
    )


def test_shipped_policy_revalidation_fails_closed_after_expiry() -> None:
    """An expired lease is refused at the boundary, not at the boundary + 1."""

    binding = _validate(_binding_records())
    expires_at = binding.capability_lease.expires_at
    assert expires_at is not None

    # Exactly at expiry is still valid; one tick past it is not.
    default_capability_policy().revalidate(
        binding,
        current_policy_revision=_POLICY_REVISION,
        now=expires_at,
    )

    with pytest.raises(CapabilityLeaseViolation) as exc_info:
        default_capability_policy().revalidate(
            binding,
            current_policy_revision=_POLICY_REVISION,
            now=expires_at + 1,
        )

    assert exc_info.value.invariant == "capability lease has expired"


def test_shipped_policy_issues_leases_without_an_expiry_by_default() -> None:
    """The shipped TTL is absent; a configured TTL is added to issuance time."""

    assert default_capability_policy().expires_at(1_000) is None
    assert StaticCapabilityPolicy(lease_ttl_seconds=60).expires_at(1_000) == 1_060


@pytest.mark.parametrize("bad_ttl", [0, -1, 1.5, "60", True])
def test_static_policy_rejects_a_non_positive_integer_ttl(
    bad_ttl: object,
) -> None:
    """A malformed TTL is a construction error, not a silently ignored one."""

    with pytest.raises(ValueError):
        StaticCapabilityPolicy(lease_ttl_seconds=bad_ttl)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("blank", ["", "   "])
def test_static_evidence_provider_rejects_blank_selection_identifiers(
    blank: str,
) -> None:
    """Neither identifier may be blank -- an empty instance is not a peer."""

    provider = StaticPeerEnforcementEvidenceProvider()

    with pytest.raises(ValueError):
        provider.resolve(peer_instance_id=blank, profile_id="cx.standard")
    with pytest.raises(ValueError):
        provider.resolve(peer_instance_id="cx", profile_id=blank)


def test_unmeasured_evidence_must_carry_the_absent_source_tag() -> None:
    """A ``None`` ceiling can never be dressed up as a real measurement."""

    with pytest.raises(ValueError):
        PeerEnforcementEvidence(
            peer_instance_id="ag",
            peer_kind="ag",
            enforcement_ceiling=None,
            source_tag="empirical_probe",
        )
