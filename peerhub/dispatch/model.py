"""Pure reducers for the scoped Slice 2 session/lease kernel.

This increment deliberately does not implement the RESERVED pre-spawn
lifecycle, request/attempt/coordinator-epoch linkage, or issuing a new
lease during SL-02 compatible resume. Those remain explicit work for a
future separately-authorized increment.
"""

from __future__ import annotations

from peerhub.core.errors import InvalidMutationError, StaleRevisionError
from peerhub.core.execution import ExecutionCertainty

from .contract import (
    LeaseAuthorityCertainty,
    LeaseCloseRequest,
    LeaseCreateRequest,
    LeaseFenceTuple,
    LeaseRenewRequest,
    LeaseSnapshot,
    LeaseState,
    RecoveryDecision,
    RecoveryReceipt,
    RecoveryTrigger,
    SessionBindingKey,
    SessionBindingSnapshot,
    SessionBindingState,
    SessionResumeRequest,
)


def validate_lease_fence(
    persisted: LeaseFenceTuple,
    requester: LeaseFenceTuple,
) -> tuple[bool, tuple[str, ...]]:
    """Compare every security-authoritative requester fence dimension."""

    mismatches: list[str] = []

    if requester.session_id != persisted.session_id:
        mismatches.append("session_id")
    if requester.lease_id != persisted.lease_id:
        mismatches.append("lease_id")
    if requester.fencing_token != persisted.fencing_token:
        mismatches.append("fencing_token")
    if requester.revision != persisted.revision:
        mismatches.append("revision")
    if requester.owner_principal_id != persisted.owner_principal_id:
        mismatches.append("owner_principal_id")
    if requester.owner_instance_id != persisted.owner_instance_id:
        mismatches.append("owner_instance_id")
    if (
        requester.owner_process_birth_identity.pid
        != persisted.owner_process_birth_identity.pid
    ):
        mismatches.append("owner_process_birth_identity.pid")
    if (
        requester.owner_process_birth_identity.process_creation_time
        != persisted.owner_process_birth_identity.process_creation_time
    ):
        mismatches.append(
            "owner_process_birth_identity.process_creation_time"
        )

    # owner_peer_id is descriptive metadata; authenticated principal and
    # instance identity, not the superseded peer_id, define authority.
    return (len(mismatches) == 0, tuple(mismatches))


def create_lease(
    request: LeaseCreateRequest,
    *,
    lease_id: str,
    created_at: int,
) -> LeaseSnapshot:
    """Reduce a lease-creation request to this slice's ACTIVE lease."""

    fence = LeaseFenceTuple(
        session_id=request.session_id,
        lease_id=lease_id,
        fencing_token=1,
        revision=1,
        owner_principal_id=request.owner_principal_id,
        owner_instance_id=request.owner_instance_id,
        owner_process_birth_identity=request.owner_process_birth_identity,
        owner_peer_id=request.owner_peer_id,
    )
    expires_at = created_at + request.heartbeat_timeout_ms
    return LeaseSnapshot(
        lease_id=lease_id,
        session_id=request.session_id,
        fence=fence,
        state=LeaseState.ACTIVE,
        heartbeat_expires_at=expires_at,
        created_at=created_at,
        updated_at=created_at,
    )


def renew_lease(
    current: LeaseSnapshot,
    request: LeaseRenewRequest,
    *,
    heartbeat_timeout_ms: int,
    updated_at: int,
) -> LeaseSnapshot:
    """Renew an active lease after validating revision and fence identity."""

    if current.state not in (LeaseState.ACTIVE, LeaseState.RENEWED):
        raise InvalidMutationError(
            f"cannot renew lease in state {current.state.value}"
        )

    if request.fence.revision != current.fence.revision:
        raise StaleRevisionError(
            current.lease_id,
            request.fence.revision,
            current.fence.revision,
        )

    is_match, mismatches = validate_lease_fence(current.fence, request.fence)
    if not is_match:
        raise InvalidMutationError(
            f"lease fence mismatch on fields: {', '.join(mismatches)}"
        )

    next_fence = LeaseFenceTuple(
        session_id=current.fence.session_id,
        lease_id=current.fence.lease_id,
        fencing_token=current.fence.fencing_token + 1,
        revision=current.fence.revision + 1,
        owner_principal_id=current.fence.owner_principal_id,
        owner_instance_id=current.fence.owner_instance_id,
        owner_process_birth_identity=current.fence.owner_process_birth_identity,
        owner_peer_id=current.fence.owner_peer_id,
    )
    return LeaseSnapshot(
        lease_id=current.lease_id,
        session_id=current.session_id,
        fence=next_fence,
        state=LeaseState.RENEWED,
        heartbeat_expires_at=updated_at + heartbeat_timeout_ms,
        created_at=current.created_at,
        updated_at=updated_at,
    )


def close_lease(
    current: LeaseSnapshot,
    request: LeaseCloseRequest,
    *,
    updated_at: int,
) -> LeaseSnapshot:
    """Close a lease after validating revision and fence identity."""

    if current.state in (LeaseState.RELEASED, LeaseState.FENCED):
        raise InvalidMutationError(
            f"cannot close lease already in state {current.state.value}"
        )

    if request.fence.revision != current.fence.revision:
        raise StaleRevisionError(
            current.lease_id,
            request.fence.revision,
            current.fence.revision,
        )

    is_match, mismatches = validate_lease_fence(current.fence, request.fence)
    if not is_match:
        raise InvalidMutationError(
            f"lease fence mismatch on fields: {', '.join(mismatches)}"
        )

    next_fence = LeaseFenceTuple(
        session_id=current.fence.session_id,
        lease_id=current.fence.lease_id,
        fencing_token=current.fence.fencing_token + 1,
        revision=current.fence.revision + 1,
        owner_principal_id=current.fence.owner_principal_id,
        owner_instance_id=current.fence.owner_instance_id,
        owner_process_birth_identity=current.fence.owner_process_birth_identity,
        owner_peer_id=current.fence.owner_peer_id,
    )
    return LeaseSnapshot(
        lease_id=current.lease_id,
        session_id=current.session_id,
        fence=next_fence,
        state=LeaseState.RELEASED,
        heartbeat_expires_at=current.heartbeat_expires_at,
        created_at=current.created_at,
        updated_at=updated_at,
    )


def expire_and_recover_lease(
    current: LeaseSnapshot,
    *,
    recovery_receipt_id: str,
    recovery_actor_principal_id: str,
    trigger: RecoveryTrigger,
    evidence_digest: str,
    policy_id: str,
    policy_revision: int,
    detected_at: int,
    is_process_alive: bool,
    process_identity_matches: bool,
) -> tuple[LeaseSnapshot, RecoveryReceipt]:
    """Fence a lease and derive a recovery receipt from observed evidence."""

    pre_state = current.state
    pre_revision = current.fence.revision
    pre_fencing_token = current.fence.fencing_token

    mismatches: list[str] = []
    effect_certainty: ExecutionCertainty | None

    if not process_identity_matches:
        mismatches.append("owner_process_birth_identity")
        post_state = LeaseState.IDENTITY_MISMATCH
        decision = RecoveryDecision.REJECT_AND_QUARANTINE
        certainty_after = LeaseAuthorityCertainty.PRIOR_HOLDER_UNVERIFIED
        # Uncorrelated liveness cannot establish certainty about this lease's
        # process. The frozen enum has no UNKNOWN member, so absence is explicit.
        effect_certainty = None
    elif is_process_alive:
        post_state = LeaseState.FENCED
        decision = RecoveryDecision.FENCE_AND_CLOSE
        certainty_after = LeaseAuthorityCertainty.FENCED_FOR_FUTURE_WRITES
        effect_certainty = ExecutionCertainty.MAY_HAVE_STARTED
    else:
        post_state = LeaseState.FENCED
        decision = RecoveryDecision.MARK_INTERRUPTED
        certainty_after = LeaseAuthorityCertainty.FENCED_FOR_FUTURE_WRITES
        effect_certainty = ExecutionCertainty.TERMINAL

    post_revision = pre_revision + 1
    post_fencing_token = pre_fencing_token + 1

    next_fence = LeaseFenceTuple(
        session_id=current.fence.session_id,
        lease_id=current.fence.lease_id,
        fencing_token=post_fencing_token,
        revision=post_revision,
        owner_principal_id=current.fence.owner_principal_id,
        owner_instance_id=current.fence.owner_instance_id,
        owner_process_birth_identity=current.fence.owner_process_birth_identity,
        owner_peer_id=current.fence.owner_peer_id,
    )
    updated_lease = LeaseSnapshot(
        lease_id=current.lease_id,
        session_id=current.session_id,
        fence=next_fence,
        state=post_state,
        heartbeat_expires_at=current.heartbeat_expires_at,
        created_at=current.created_at,
        updated_at=detected_at,
    )

    receipt = RecoveryReceipt(
        recovery_receipt_id=recovery_receipt_id,
        session_id=current.session_id,
        lease_id=current.lease_id,
        detected_at=detected_at,
        recovery_actor_principal_id=recovery_actor_principal_id,
        trigger=trigger,
        mismatch_dimensions=tuple(mismatches),
        evidence_digest=evidence_digest,
        policy_id=policy_id,
        policy_revision=policy_revision,
        decision=decision,
        certainty_before_policy=LeaseAuthorityCertainty.PRIOR_HOLDER_UNVERIFIED,
        certainty_after_policy=certainty_after,
        external_effect_certainty=effect_certainty,
        pre_lifecycle_state=pre_state,
        pre_revision=pre_revision,
        pre_fencing_token=pre_fencing_token,
        post_lifecycle_state=post_state,
        post_revision=post_revision,
        post_fencing_token=post_fencing_token,
    )

    return (updated_lease, receipt)


def create_session_binding(
    key: SessionBindingKey,
    *,
    session_id: str,
    current_lease_id: str | None,
    adapter_fingerprint: str,
    readiness_binding: str,
    session_generation: int,
    created_at: int,
) -> SessionBindingSnapshot:
    """Create a new revision-one ACTIVE session binding."""

    return SessionBindingSnapshot(
        key=key,
        session_id=session_id,
        current_lease_id=current_lease_id,
        adapter_fingerprint=adapter_fingerprint,
        readiness_binding=readiness_binding,
        session_generation=session_generation,
        revision=1,
        state=SessionBindingState.ACTIVE,
        updated_at=created_at,
    )


def resume_session_binding(
    current: SessionBindingSnapshot,
    request: SessionResumeRequest,
    *,
    updated_at: int,
) -> tuple[bool, SessionBindingSnapshot]:
    """Validate compatible resume and mark an incompatible binding stale."""

    if current.key != request.key:
        raise InvalidMutationError("session binding key mismatch")

    is_compatible = (
        current.session_id == request.requested_session_id
        and current.adapter_fingerprint == request.adapter_fingerprint
        and current.readiness_binding == request.readiness_binding
        and current.session_generation == request.session_generation
        and current.state == SessionBindingState.ACTIVE
    )

    if is_compatible:
        return (True, current)

    updated = SessionBindingSnapshot(
        key=current.key,
        session_id=current.session_id,
        current_lease_id=current.current_lease_id,
        adapter_fingerprint=current.adapter_fingerprint,
        readiness_binding=current.readiness_binding,
        session_generation=current.session_generation,
        revision=current.revision + 1,
        state=SessionBindingState.STALE,
        updated_at=updated_at,
    )
    return (False, updated)


def mark_session_suspect(
    current: SessionBindingSnapshot,
    *,
    updated_at: int,
) -> SessionBindingSnapshot:
    """Transition a session binding to SUSPECT after interruption."""

    return SessionBindingSnapshot(
        key=current.key,
        session_id=current.session_id,
        current_lease_id=current.current_lease_id,
        adapter_fingerprint=current.adapter_fingerprint,
        readiness_binding=current.readiness_binding,
        session_generation=current.session_generation,
        revision=current.revision + 1,
        state=SessionBindingState.SUSPECT,
        updated_at=updated_at,
    )
