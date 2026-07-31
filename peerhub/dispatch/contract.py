"""Published DTOs for the scoped Slice 2 session/lease kernel.

This increment deliberately defers the RESERVED pre-spawn lifecycle,
request/attempt/coordinator-epoch linkage, and issuing a new lease during
SL-02 compatible resume. Those require a future separately-authorized
increment; the types below must not be read as implementing those scopes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from peerhub.core.execution import ExecutionCertainty


class LeaseState(str, Enum):
    """Authoritative lifecycle states for a session lease."""

    RESERVED = "RESERVED"
    ACTIVE = "ACTIVE"
    RENEWED = "RENEWED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    FENCING = "FENCING"
    FENCED = "FENCED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    OWNERSHIP_LOST = "OWNERSHIP_LOST"
    ABANDONED_PRE_SPAWN = "ABANDONED_PRE_SPAWN"


class SessionBindingState(str, Enum):
    """Authoritative lifecycle states for a session binding."""

    ABSENT = "ABSENT"
    CREATING = "CREATING"
    ACTIVE = "ACTIVE"
    SUSPECT = "SUSPECT"
    UNKNOWN = "UNKNOWN"
    IN_USE = "IN_USE"
    STALE = "STALE"
    RETIRED = "RETIRED"
    VERIFYING = "VERIFYING"


class LeaseAuthorityCertainty(str, Enum):
    """Authority certainty level regarding lease fencing boundaries."""

    PRIOR_HOLDER_UNVERIFIED = "PRIOR_HOLDER_UNVERIFIED"
    FENCED_FOR_FUTURE_WRITES = "FENCED_FOR_FUTURE_WRITES"


class RecoveryTrigger(str, Enum):
    """Triggers causing an automated lease fence or recovery receipt."""

    PROCESS_BIRTH_MISMATCH = "PROCESS_BIRTH_MISMATCH"
    HEARTBEAT_TIMEOUT = "HEARTBEAT_TIMEOUT"
    FENCE_REPLAY_STALE = "FENCE_REPLAY_STALE"
    EXPLICIT_RECOVERY_REQUEST = "EXPLICIT_RECOVERY_REQUEST"


class RecoveryDecision(str, Enum):
    """Authoritative decision rendered upon lease recovery."""

    FENCE_AND_CLOSE = "FENCE_AND_CLOSE"
    REJECT_AND_QUARANTINE = "REJECT_AND_QUARANTINE"
    MARK_SUSPECT = "MARK_SUSPECT"
    MARK_INTERRUPTED = "MARK_INTERRUPTED"


@dataclass(frozen=True)
class ProcessBirthIdentity:
    """Immutable dual-key OS process birth identity."""

    pid: int
    process_creation_time: int

    def __post_init__(self) -> None:
        if type(self.pid) is not int or self.pid <= 0:
            raise ValueError("pid must be a positive integer")
        if (
            type(self.process_creation_time) is not int
            or self.process_creation_time < 0
        ):
            raise ValueError("process_creation_time must be a nonnegative integer")


@dataclass(frozen=True)
class SessionBindingKey:
    """Canonical four-part binding key for session instances."""

    workspace_scope_id: str
    instance_id: str
    profile_id: str
    conversation_scope: str

    def __post_init__(self) -> None:
        if not self.workspace_scope_id.strip():
            raise ValueError("workspace_scope_id must be non-empty")
        if not self.instance_id.strip():
            raise ValueError("instance_id must be non-empty")
        if not self.profile_id.strip():
            raise ValueError("profile_id must be non-empty")
        if not self.conversation_scope.strip():
            raise ValueError("conversation_scope must be non-empty")


@dataclass(frozen=True)
class LeaseFenceTuple:
    """Security-authoritative lease fence plus descriptive peer metadata."""

    session_id: str
    lease_id: str
    fencing_token: int
    revision: int
    owner_principal_id: str
    owner_instance_id: str
    owner_process_birth_identity: ProcessBirthIdentity
    owner_peer_id: str = ""

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("session_id must be non-empty")
        if not self.lease_id.strip():
            raise ValueError("lease_id must be non-empty")
        if type(self.fencing_token) is not int or self.fencing_token < 0:
            raise ValueError("fencing_token must be a nonnegative int")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative int")
        if not self.owner_principal_id.strip():
            raise ValueError("owner_principal_id must be non-empty")
        if not self.owner_instance_id.strip():
            raise ValueError("owner_instance_id must be non-empty")


@dataclass(frozen=True)
class SessionBindingSnapshot:
    """Authoritative revisioned state snapshot of a session binding."""

    key: SessionBindingKey
    session_id: str
    current_lease_id: str | None
    adapter_fingerprint: str
    readiness_binding: str
    session_generation: int
    revision: int
    state: SessionBindingState
    updated_at: int

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("revision must be a positive integer")


@dataclass(frozen=True)
class LeaseSnapshot:
    """Authoritative state snapshot of a session lease."""

    lease_id: str
    session_id: str
    fence: LeaseFenceTuple
    state: LeaseState
    heartbeat_expires_at: int
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class RecoveryReceipt:
    """Immutable recovery receipt recorded upon lease recovery/fencing."""

    recovery_receipt_id: str
    session_id: str
    lease_id: str
    detected_at: int
    recovery_actor_principal_id: str
    trigger: RecoveryTrigger
    mismatch_dimensions: tuple[str, ...]
    evidence_digest: str
    policy_id: str
    policy_revision: int
    decision: RecoveryDecision
    certainty_before_policy: LeaseAuthorityCertainty
    certainty_after_policy: LeaseAuthorityCertainty
    external_effect_certainty: ExecutionCertainty | None
    pre_lifecycle_state: LeaseState
    pre_revision: int
    pre_fencing_token: int
    post_lifecycle_state: LeaseState
    post_revision: int
    post_fencing_token: int


@dataclass(frozen=True)
class SessionResumeRequest:
    """Request to validate compatibility with an existing session binding."""

    key: SessionBindingKey
    requested_session_id: str
    adapter_fingerprint: str
    readiness_binding: str
    session_generation: int


@dataclass(frozen=True)
class LeaseCreateRequest:
    """Request to create a new session lease."""

    session_id: str
    owner_principal_id: str
    owner_instance_id: str
    owner_process_birth_identity: ProcessBirthIdentity
    heartbeat_timeout_ms: int
    owner_peer_id: str = ""


@dataclass(frozen=True)
class LeaseRenewRequest:
    """Request to renew an active session lease."""

    lease_id: str
    fence: LeaseFenceTuple


@dataclass(frozen=True)
class LeaseCloseRequest:
    """Request to close an active session lease."""

    lease_id: str
    fence: LeaseFenceTuple


@dataclass(frozen=True)
class LeaseFenceCheckRequest:
    """Request to validate a requester's asserted fence tuple."""

    requester_fence: LeaseFenceTuple
