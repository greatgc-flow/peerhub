"""Published DTOs for the Phase 1 dispatch kernels.

Slice 3 adds pure request/attempt state, server-owned command
idempotency, completion-assessment DTOs, and command/attempt/epoch lease
fencing. Real spawning, process supervision, deadlines, output limits,
cancellation, and active orphan sweeps remain Phase 2 or later.

The original Slice 2 active-lease API remains process-bound. The additive
reservation API permits an absent process identity before RUNNING and an
absent attempt ID only while the lease is initially RESERVED.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from peerhub.adapters.contract import ProtocolAssessment
from peerhub.core.execution import ExecutionCertainty
from peerhub.dispatch.capability import CapabilityTier
from peerhub.core.protocol import (
    CommandEnvelope,
    CommandID,
    ErrorCode,
    ErrorPhase,
    OperationalFailureCategory,
    JsonValue,
    RevisionValue,
    freeze_json_mapping,
    require_text,
)


class TerminalClassification(str, Enum):
    """Currently ratified process classifications used by Slice 5 tests.

    This is intentionally not claimed to be the complete vocabulary. The
    Phase 0 contract still lists full terminal-classification closure as an
    open decision.
    """

    START_UNCERTAIN = "START_UNCERTAIN"
    SILENCE_TIMEOUT = "SILENCE_TIMEOUT"
    PROCESS_TIMEOUT = "PROCESS_TIMEOUT"
    EXIT_NON_ZERO = "EXIT_NON_ZERO"
    OUTPUT_LIMIT_EXCEEDED = "OUTPUT_LIMIT_EXCEEDED"


@dataclass(frozen=True)
class AttemptFailureClassification:
    code: ErrorCode
    phase: ErrorPhase
    operational_failure_category: OperationalFailureCategory | None


class RequestState(str, Enum):
    """Frozen shared request/attempt lifecycle vocabulary."""

    RECEIVED = "RECEIVED"
    REJECTED_VALIDATION = "REJECTED_VALIDATION"
    ADMITTED = "ADMITTED"
    REJECTED_POLICY = "REJECTED_POLICY"
    PREPARED = "PREPARED"
    FAILED_PRE_DISPATCH = "FAILED_PRE_DISPATCH"
    DISPATCH_INTENT = "DISPATCH_INTENT"
    START_UNCERTAIN = "START_UNCERTAIN"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    ASSESSING = "ASSESSING"
    SUCCEEDED_VERIFIED = "SUCCEEDED_VERIFIED"
    DELIVERED_UNVERIFIED = "DELIVERED_UNVERIFIED"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"


TERMINAL_REQUEST_STATES = frozenset(
    {
        RequestState.REJECTED_VALIDATION,
        RequestState.REJECTED_POLICY,
        RequestState.FAILED_PRE_DISPATCH,
        RequestState.SUCCEEDED_VERIFIED,
        RequestState.DELIVERED_UNVERIFIED,
        RequestState.INCOMPLETE,
        RequestState.FAILED,
        RequestState.INTERRUPTED,
        RequestState.CANCELLED,
    }
)


class CompletionContractKind(str, Enum):
    """Frozen completion-contract kinds."""

    DELIVERY_ONLY = "DELIVERY_ONLY"
    ARTIFACT_REQUIRED = "ARTIFACT_REQUIRED"
    SCHEMA_VALIDATED = "SCHEMA_VALIDATED"
    FIELD_REQUIRED = "FIELD_REQUIRED"
    CUSTOM_VERIFIER = "CUSTOM_VERIFIER"
    VENDOR_RECEIPT = "VENDOR_RECEIPT"


class CompletionAssessmentState(str, Enum):
    """Frozen central completion-assessment states."""

    VERIFIED = "VERIFIED"
    INCOMPLETE = "INCOMPLETE"
    UNVERIFIED = "UNVERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


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


class SessionRotationState(str, Enum):
    """Authoritative lifecycle states for session rotation generations."""

    ACTIVE = "ACTIVE"
    ROTATION_PENDING = "ROTATION_PENDING"
    DRAINING = "DRAINING"
    RETIRED = "RETIRED"
    SUSPECT = "SUSPECT"


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


def _require_nonnegative_int(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _require_positive_int(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _require_bool(value: bool, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")


def _require_sha256_hex(value: str, name: str) -> str:
    normalized = require_text(value, name).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef"
        for character in normalized
    ):
        raise ValueError(
            f"{name} must be a lowercase SHA-256 hex digest"
        )
    return normalized


def _validate_revision_value(
    value: RevisionValue | None,
    name: str,
) -> None:
    if value is None:
        return
    if type(value) is int:
        _require_nonnegative_int(value, name)
        return
    if isinstance(value, str):
        require_text(value, name)
        return
    raise ValueError(f"{name} must be a string, integer, or null")


@dataclass(frozen=True)
class CompletionContract:
    """Immutable completion requirements frozen at admission."""

    contract_id: str
    kind: CompletionContractKind
    requirements: tuple[Mapping[str, JsonValue], ...]
    replay_safe: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "contract_id",
            require_text(self.contract_id, "contract_id"),
        )
        if not isinstance(self.kind, CompletionContractKind):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError(
                "kind must be a CompletionContractKind"
            )
        _require_bool(self.replay_safe, "replay_safe")

        frozen_requirements = tuple(
            freeze_json_mapping(requirement)
            for requirement in self.requirements
        )
        if (
            self.kind is not CompletionContractKind.DELIVERY_ONLY
            and not frozen_requirements
        ):
            raise ValueError(
                "non-delivery completion contracts need requirements"
            )
        object.__setattr__(
            self,
            "requirements",
            frozen_requirements,
        )

    def canonical_projection(self) -> Mapping[str, object]:
        """Return the contract projection included in payload digests."""

        return {
            "contract_id": self.contract_id,
            "kind": self.kind.value,
            "requirements": self.requirements,
            "replay_safe": self.replay_safe,
        }


@dataclass(frozen=True)
class ExecutionOutcome:
    """Process-boundary facts; no semantic completion judgment."""

    started: bool
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    execution_certainty: ExecutionCertainty

    def __post_init__(self) -> None:
        _require_bool(self.started, "started")
        _require_bool(self.timed_out, "timed_out")
        _require_bool(self.cancelled, "cancelled")
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise ValueError("exit_code must be an integer or null")
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.execution_certainty,
            ExecutionCertainty,
        ):
            raise ValueError(
                "execution_certainty must be ExecutionCertainty"
            )
        if (
            not self.started
            and self.execution_certainty
            in {
                ExecutionCertainty.STARTED,
                ExecutionCertainty.TERMINAL,
            }
        ):
            raise ValueError(
                "an unstarted execution cannot be STARTED or TERMINAL"
            )


# ProtocolAssessment previously lived here (5-field dataclass).
# SLICE5-KICKOFF-R1.md item 5: moved verbatim to peerhub.adapters.contract
# and re-exported above (import at top of this file) so every existing
# `from peerhub.dispatch.contract import ProtocolAssessment` keeps
# resolving to the same class object -- not a parallel/drifting
# redefinition.


@dataclass(frozen=True)
class CompletionAssessment:
    """Central semantic assessment against a frozen contract.

    ``contract_kind`` is a required, non-nullable field recording which
    ``CompletionContractKind`` was evaluated.  This is the ratified
    structural enforcement mechanism (SLICE5-KICKOFF-R1.md
    "artifacts.py/completion.py contract RATIFIED"): downstream code
    must never compare ``state == VERIFIED`` without knowing the
    contract kind that produced it.
    """

    state: CompletionAssessmentState
    contract_kind: CompletionContractKind
    failed_requirements: tuple[str, ...] = field(
        default_factory=tuple
    )
    evidence_refs: tuple[str, ...] = field(
        default_factory=tuple
    )

    def __post_init__(self) -> None:
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.state,
            CompletionAssessmentState,
        ):
            raise ValueError(
                "state must be CompletionAssessmentState"
            )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.contract_kind,
            CompletionContractKind,
        ):
            raise ValueError(
                "contract_kind must be a CompletionContractKind"
            )
        object.__setattr__(
            self,
            "failed_requirements",
            tuple(
                require_text(value, "failed_requirement")
                for value in self.failed_requirements
            ),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(
                require_text(value, "evidence_ref")
                for value in self.evidence_refs
            ),
        )
        if (
            self.state is CompletionAssessmentState.VERIFIED
            and self.failed_requirements
        ):
            raise ValueError(
                "VERIFIED completion cannot have failed requirements"
            )


@dataclass(frozen=True)
class AskResult:
    """Three independent outcome layers with derived effective status."""

    execution: ExecutionOutcome
    protocol: ProtocolAssessment
    completion: CompletionAssessment
    policy_revision: RevisionValue
    terminal_classification: TerminalClassification | None = None
    failure_classification: AttemptFailureClassification | None = None

    def __post_init__(self) -> None:
        _validate_revision_value(
            self.policy_revision,
            "policy_revision",
        )

    @property
    def effective_status(self) -> RequestState:
        """Derive status; never persist a fourth independent truth."""

        if self.execution.cancelled:
            return RequestState.CANCELLED
        if self.execution.timed_out:
            return RequestState.INTERRUPTED
        if (
            not self.execution.started
            or self.execution.exit_code not in (None, 0)
            or not self.protocol.parsed
            or self.protocol.protocol_failure is not None
        ):
            return RequestState.FAILED
        if (
            self.completion.state
            is CompletionAssessmentState.VERIFIED
        ):
            return RequestState.SUCCEEDED_VERIFIED
        if (
            self.completion.state
            is CompletionAssessmentState.INCOMPLETE
        ):
            return RequestState.INCOMPLETE
        if (
            self.completion.state
            is CompletionAssessmentState.UNVERIFIED
        ):
            return RequestState.DELIVERED_UNVERIFIED
        return RequestState.FAILED


@dataclass(frozen=True)
class ValidatedSubmission:
    """Validated caller intent before server command-ID minting."""

    envelope: CommandEnvelope
    authenticated_principal: str
    payload_digest: str
    completion_contract: CompletionContract

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "authenticated_principal",
            require_text(
                self.authenticated_principal,
                "authenticated_principal",
            ),
        )
        object.__setattr__(
            self,
            "payload_digest",
            _require_sha256_hex(
                self.payload_digest,
                "payload_digest",
            ),
        )


@dataclass(frozen=True)
class ClientRequestBinding:
    """Unique binding for ``(client_id, client_request_id)``."""

    client_id: str
    client_request_id: str
    payload_digest: str
    command_id: CommandID
    admission_receipt_id: str
    created_at: int

    def __post_init__(self) -> None:
        for name in (
            "client_id",
            "client_request_id",
            "admission_receipt_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "command_id",
            CommandID(
                require_text(str(self.command_id), "command_id")
            ),
        )
        object.__setattr__(
            self,
            "payload_digest",
            _require_sha256_hex(
                self.payload_digest,
                "payload_digest",
            ),
        )
        _require_nonnegative_int(self.created_at, "created_at")


@dataclass(frozen=True)
class CommandIdempotencyBinding:
    """Unique command idempotency-key admission binding."""

    client_id: str
    command_type: str
    idempotency_key: str
    payload_digest: str
    command_id: CommandID
    admission_receipt_id: str
    created_at: int

    def __post_init__(self) -> None:
        for name in (
            "client_id",
            "command_type",
            "idempotency_key",
            "admission_receipt_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "command_id",
            CommandID(
                require_text(str(self.command_id), "command_id")
            ),
        )
        object.__setattr__(
            self,
            "payload_digest",
            _require_sha256_hex(
                self.payload_digest,
                "payload_digest",
            ),
        )
        _require_nonnegative_int(self.created_at, "created_at")


@dataclass(frozen=True)
class AdmissionReceipt:
    """Immutable receipt for an admitted server command."""

    admission_receipt_id: str
    command_id: CommandID
    client_id: str
    client_request_id: str
    command_type: str
    idempotency_key: str
    payload_digest: str
    completion_contract_id: str
    lease_id: str
    policy_revision: RevisionValue
    configuration_revision: RevisionValue
    admitted_at: int

    def __post_init__(self) -> None:
        for name in (
            "admission_receipt_id",
            "client_id",
            "client_request_id",
            "command_type",
            "idempotency_key",
            "completion_contract_id",
            "lease_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "command_id",
            CommandID(
                require_text(str(self.command_id), "command_id")
            ),
        )
        object.__setattr__(
            self,
            "payload_digest",
            _require_sha256_hex(
                self.payload_digest,
                "payload_digest",
            ),
        )
        _validate_revision_value(
            self.policy_revision,
            "policy_revision",
        )
        _validate_revision_value(
            self.configuration_revision,
            "configuration_revision",
        )
        _require_nonnegative_int(self.admitted_at, "admitted_at")


@dataclass(frozen=True)
class RequestSnapshot:
    """Authoritative revisioned request state after admission."""

    command_id: CommandID
    client_id: str
    client_request_id: str
    correlation_id: str
    authenticated_principal: str
    command_type: str
    idempotency_key: str
    payload_digest: str
    scope: Mapping[str, JsonValue]
    params: Mapping[str, JsonValue]
    expected_policy_revision: RevisionValue | None
    expected_configuration_revision: RevisionValue | None
    policy_revision: RevisionValue
    configuration_revision: RevisionValue
    completion_contract: CompletionContract
    required_capability_tier: CapabilityTier
    selected_peer_instance_id: str
    selected_profile_id: str
    route_decision_digest: str
    lease_id: str
    state: RequestState
    revision: int
    created_at: int
    updated_at: int
    terminal_error_code: ErrorCode | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "command_id",
            CommandID(
                require_text(str(self.command_id), "command_id")
            ),
        )
        for name in (
            "client_id",
            "client_request_id",
            "correlation_id",
            "authenticated_principal",
            "command_type",
            "idempotency_key",
            "selected_peer_instance_id",
            "selected_profile_id",
            "lease_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "payload_digest",
            _require_sha256_hex(
                self.payload_digest,
                "payload_digest",
            ),
        )
        object.__setattr__(
            self,
            "route_decision_digest",
            _require_sha256_hex(
                self.route_decision_digest,
                "route_decision_digest",
            ),
        )
        object.__setattr__(
            self,
            "scope",
            freeze_json_mapping(self.scope),
        )
        object.__setattr__(
            self,
            "params",
            freeze_json_mapping(self.params),
        )
        _validate_revision_value(
            self.expected_policy_revision,
            "expected_policy_revision",
        )
        _validate_revision_value(
            self.expected_configuration_revision,
            "expected_configuration_revision",
        )
        _validate_revision_value(
            self.policy_revision,
            "policy_revision",
        )
        _validate_revision_value(
            self.configuration_revision,
            "configuration_revision",
        )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.required_capability_tier,
            CapabilityTier,
        ):
            raise ValueError(
                "required_capability_tier must be CapabilityTier"
            )
        if not isinstance(self.state, RequestState):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("state must be RequestState")
        _require_positive_int(self.revision, "revision")
        _require_nonnegative_int(self.created_at, "created_at")
        _require_nonnegative_int(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at cannot precede created_at"
            )
        if (
            self.terminal_error_code is not None
            and not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
                self.terminal_error_code,
                ErrorCode,
            )
        ):
            raise ValueError(
                "terminal_error_code must be ErrorCode or null"
            )


@dataclass(frozen=True)
class AttemptSnapshot:
    """Authoritative revisioned execution attempt."""

    attempt_id: str
    command_id: CommandID
    attempt_number: int
    lease_id: str
    state: RequestState
    execution_certainty: ExecutionCertainty
    revision: int
    created_at: int
    updated_at: int
    reconciliation_complete: bool = False
    result: AskResult | None = None
    terminal_error_code: ErrorCode | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "attempt_id",
            require_text(self.attempt_id, "attempt_id"),
        )
        object.__setattr__(
            self,
            "command_id",
            CommandID(
                require_text(str(self.command_id), "command_id")
            ),
        )
        object.__setattr__(
            self,
            "lease_id",
            require_text(self.lease_id, "lease_id"),
        )
        _require_positive_int(
            self.attempt_number,
            "attempt_number",
        )
        if not isinstance(self.state, RequestState):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("state must be RequestState")
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.execution_certainty,
            ExecutionCertainty,
        ):
            raise ValueError(
                "execution_certainty must be ExecutionCertainty"
            )
        _require_positive_int(self.revision, "revision")
        _require_nonnegative_int(self.created_at, "created_at")
        _require_nonnegative_int(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at cannot precede created_at"
            )
        _require_bool(
            self.reconciliation_complete,
            "reconciliation_complete",
        )
        if (
            self.result is not None
            and self.state not in TERMINAL_REQUEST_STATES
        ):
            raise ValueError(
                "only terminal attempts may carry an AskResult"
            )
        if (
            self.terminal_error_code is not None
            and not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
                self.terminal_error_code,
                ErrorCode,
            )
        ):
            raise ValueError(
                "terminal_error_code must be ErrorCode or null"
            )


@dataclass(frozen=True)
class ProcessBirthIdentity:
    """Immutable dual-key OS process birth identity."""

    pid: int
    process_creation_time: int

    def __post_init__(self) -> None:
        _require_positive_int(self.pid, "pid")
        _require_nonnegative_int(
            self.process_creation_time,
            "process_creation_time",
        )


@dataclass(frozen=True)
class SessionBindingKey:
    """Canonical four-part binding key for session instances."""

    workspace_scope_id: str
    instance_id: str
    profile_id: str
    conversation_scope: str

    def __post_init__(self) -> None:
        for name in (
            "workspace_scope_id",
            "instance_id",
            "profile_id",
            "conversation_scope",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )


@dataclass(frozen=True)
class SessionRotationKey:
    """Canonical four-part binding key for a session rotation generation."""

    workspace_scope_id: str
    instance_id: str
    profile_id: str
    conversation_scope: str
    generation_id: int

    def __post_init__(self) -> None:
        for name in (
            "workspace_scope_id",
            "instance_id",
            "profile_id",
            "conversation_scope",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        if not isinstance(self.generation_id, int) or self.generation_id < 1:  # pyright: ignore[reportUnnecessaryIsInstance]  # defensive: type hints aren't runtime-enforced across construction boundaries
            raise ValueError(f"generation_id must be an integer >= 1, got {self.generation_id}")


@dataclass(frozen=True)
class SessionRotationGenerationSnapshot:
    """Authoritative revisioned state of a session rotation generation."""

    key: SessionRotationKey
    conversation_id: str
    state: SessionRotationState
    claim_token: str | None
    claim_expiry: int | None
    created_at: int
    updated_at: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "conversation_id",
            require_text(self.conversation_id, "conversation_id"),
        )
        if not isinstance(self.state, SessionRotationState):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("state must be SessionRotationState")
        if self.claim_token is not None:
            object.__setattr__(
                self,
                "claim_token",
                require_text(self.claim_token, "claim_token"),
            )
        if self.claim_expiry is not None:
            _require_nonnegative_int(self.claim_expiry, "claim_expiry")
        _require_nonnegative_int(self.created_at, "created_at")
        _require_nonnegative_int(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at cannot precede created_at"
            )


@dataclass(frozen=True)
class LeaseFenceTuple:
    """Security-authoritative full lease publication fence.

    ``owner_process_birth_identity`` is a required argument but may be
    null before RUNNING. ``attempt_id`` is absent only while the lease is
    initially RESERVED and becomes mandatory at DISPATCH_INTENT.
    """

    session_id: str
    lease_id: str
    fencing_token: int
    revision: int
    owner_principal_id: str
    owner_instance_id: str
    owner_process_birth_identity: ProcessBirthIdentity | None
    command_id: CommandID
    authority_epoch: int
    attempt_id: str | None = None
    owner_peer_id: str = ""

    def __post_init__(self) -> None:
        for name in (
            "session_id",
            "lease_id",
            "owner_principal_id",
            "owner_instance_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "command_id",
            CommandID(
                require_text(str(self.command_id), "command_id")
            ),
        )
        _require_nonnegative_int(
            self.fencing_token,
            "fencing_token",
        )
        _require_nonnegative_int(self.revision, "revision")
        _require_nonnegative_int(
            self.authority_epoch,
            "authority_epoch",
        )
        if self.attempt_id is not None:
            object.__setattr__(
                self,
                "attempt_id",
                require_text(self.attempt_id, "attempt_id"),
            )
        if self.owner_peer_id:
            object.__setattr__(
                self,
                "owner_peer_id",
                require_text(
                    self.owner_peer_id,
                    "owner_peer_id",
                ),
            )


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
        _require_positive_int(self.revision, "revision")


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

    def __post_init__(self) -> None:
        if self.lease_id != self.fence.lease_id:
            raise ValueError(
                "lease_id must match the fence lease_id"
            )
        if self.session_id != self.fence.session_id:
            raise ValueError(
                "session_id must match the fence session_id"
            )
        if not isinstance(self.state, LeaseState):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("state must be LeaseState")

        process_bound_states = {
            LeaseState.ACTIVE,
            LeaseState.RENEWED,
            LeaseState.RELEASED,
            LeaseState.EXPIRED,
            LeaseState.FENCING,
            LeaseState.FENCED,
            LeaseState.IDENTITY_MISMATCH,
            LeaseState.OWNERSHIP_LOST,
        }
        if self.state in process_bound_states:
            if self.fence.attempt_id is None:
                raise ValueError(
                    "process-bound leases require attempt_id"
                )
            if (
                self.fence.owner_process_birth_identity
                is None
            ):
                raise ValueError(
                    "process-bound leases require process identity"
                )

        _require_nonnegative_int(
            self.heartbeat_expires_at,
            "heartbeat_expires_at",
        )
        _require_nonnegative_int(self.created_at, "created_at")
        _require_nonnegative_int(self.updated_at, "updated_at")


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
    """Request to validate compatibility with a session binding."""

    key: SessionBindingKey
    requested_session_id: str
    adapter_fingerprint: str
    readiness_binding: str
    session_generation: int


@dataclass(frozen=True)
class LeaseCreateRequest:
    """Existing process-bound request for an immediately active lease."""

    session_id: str
    owner_principal_id: str
    owner_instance_id: str
    owner_process_birth_identity: ProcessBirthIdentity
    heartbeat_timeout_ms: int
    command_id: CommandID
    attempt_id: str
    authority_epoch: int
    owner_peer_id: str = ""


@dataclass(frozen=True)
class LeaseReservationRequest:
    """Additive pre-spawn request for a RESERVED lease."""

    session_id: str
    owner_principal_id: str
    owner_instance_id: str
    heartbeat_timeout_ms: int
    command_id: CommandID
    authority_epoch: int
    owner_peer_id: str = ""


@dataclass(frozen=True)
class LeaseAttemptBindRequest:
    """Bind a RESERVED lease to an attempt at DISPATCH_INTENT."""

    lease_id: str
    fence: LeaseFenceTuple
    attempt_id: str


@dataclass(frozen=True)
class LeaseProcessBindRequest:
    """Bind process-birth identity when entering RUNNING."""

    lease_id: str
    fence: LeaseFenceTuple
    owner_process_birth_identity: ProcessBirthIdentity


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


@dataclass(frozen=True)
class OutboxCheckpoint:
    """Revisioned consumer checkpoint for canonical outbox order."""

    consumer_id: str
    outbox_position: int
    event_id: str
    revision: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "consumer_id",
            require_text(self.consumer_id, "consumer_id"),
        )
        object.__setattr__(
            self,
            "event_id",
            require_text(self.event_id, "event_id"),
        )
        _require_nonnegative_int(
            self.outbox_position,
            "outbox_position",
        )
        _require_positive_int(self.revision, "revision")


class ArtifactState(str, Enum):
    """Authoritative lifecycle states for dispatch artifact items."""

    DECLARED = "DECLARED"
    STAGED = "STAGED"
    VERIFIED = "VERIFIED"
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"
    ORPHANED = "ORPHANED"
    CLEANED = "CLEANED"


ArtifactLifecycleState = ArtifactState


@dataclass(frozen=True)
class ArtifactManifestRecord:
    """Durable record of an artifact materialization manifest for an attempt."""

    attempt_id: str
    workspace_scope_id: str
    staging_root_ref: str
    manifest_digest: str
    item_count: int
    created_at: int
    revision: int
    intent_event_id: str | None = None
    consumed_at: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "attempt_id",
            require_text(self.attempt_id, "attempt_id"),
        )
        object.__setattr__(
            self,
            "workspace_scope_id",
            require_text(self.workspace_scope_id, "workspace_scope_id"),
        )
        object.__setattr__(
            self,
            "staging_root_ref",
            require_text(self.staging_root_ref, "staging_root_ref"),
        )
        object.__setattr__(
            self,
            "manifest_digest",
            require_text(self.manifest_digest, "manifest_digest"),
        )
        _require_nonnegative_int(self.item_count, "item_count")
        _require_nonnegative_int(self.created_at, "created_at")
        _require_positive_int(self.revision, "revision")
        if self.intent_event_id is not None:
            object.__setattr__(
                self,
                "intent_event_id",
                require_text(self.intent_event_id, "intent_event_id"),
            )
        if self.consumed_at is not None:
            _require_nonnegative_int(self.consumed_at, "consumed_at")


@dataclass(frozen=True)
class ArtifactMetadata:
    """Durable lifecycle/identity/digest metadata for a single artifact item."""

    attempt_id: str
    artifact_id: str
    placeholder: str
    workspace_scope_id: str
    staging_ref: str
    access_mode: str
    declared_lifecycle: str
    state: ArtifactState
    declared_at: int
    revision: int
    expected_sha256_hex: str | None = None
    expected_length: int | None = None
    verified_sha256_hex: str | None = None
    verified_length: int | None = None
    verified_object_identity_json: str | None = None
    failure_code: str | None = None
    staged_at: int | None = None
    verified_at: int | None = None
    reserved_at: int | None = None
    consumed_at: int | None = None
    cleaned_at: int | None = None
    orphaned_at: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "attempt_id",
            require_text(self.attempt_id, "attempt_id"),
        )
        object.__setattr__(
            self,
            "artifact_id",
            require_text(self.artifact_id, "artifact_id"),
        )
        object.__setattr__(
            self,
            "placeholder",
            require_text(self.placeholder, "placeholder"),
        )
        object.__setattr__(
            self,
            "workspace_scope_id",
            require_text(self.workspace_scope_id, "workspace_scope_id"),
        )
        object.__setattr__(
            self,
            "staging_ref",
            require_text(self.staging_ref, "staging_ref"),
        )
        object.__setattr__(
            self,
            "access_mode",
            require_text(self.access_mode, "access_mode"),
        )
        object.__setattr__(
            self,
            "declared_lifecycle",
            require_text(self.declared_lifecycle, "declared_lifecycle"),
        )
        if not isinstance(self.state, ArtifactState):  # pyright: ignore[reportUnnecessaryIsInstance]
            object.__setattr__(self, "state", ArtifactState(self.state))
        _require_nonnegative_int(self.declared_at, "declared_at")
        _require_positive_int(self.revision, "revision")

        if self.expected_sha256_hex is not None:
            object.__setattr__(
                self,
                "expected_sha256_hex",
                require_text(self.expected_sha256_hex, "expected_sha256_hex"),
            )
        if self.expected_length is not None:
            _require_nonnegative_int(self.expected_length, "expected_length")
        if self.verified_sha256_hex is not None:
            object.__setattr__(
                self,
                "verified_sha256_hex",
                require_text(self.verified_sha256_hex, "verified_sha256_hex"),
            )
        if self.verified_length is not None:
            _require_nonnegative_int(self.verified_length, "verified_length")
        if self.verified_object_identity_json is not None:
            object.__setattr__(
                self,
                "verified_object_identity_json",
                require_text(
                    self.verified_object_identity_json,
                    "verified_object_identity_json",
                ),
            )
        if self.failure_code is not None:
            object.__setattr__(
                self,
                "failure_code",
                require_text(self.failure_code, "failure_code"),
            )
        for ts_field in (
            "staged_at",
            "verified_at",
            "reserved_at",
            "consumed_at",
            "cleaned_at",
            "orphaned_at",
        ):
            val = getattr(self, ts_field)
            if val is not None:
                _require_nonnegative_int(val, ts_field)


@dataclass(frozen=True)
class ArtifactRecoveryDigest:
    """Recovery read model detailing attempt manifest, artifacts, and intent validation."""

    attempt_id: str
    workspace_scope_id: str
    manifest_digest: str
    item_count: int
    intent_event_id: str | None
    intent_event_verified: bool
    artifacts: tuple[ArtifactMetadata, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "attempt_id",
            require_text(self.attempt_id, "attempt_id"),
        )
        object.__setattr__(
            self,
            "workspace_scope_id",
            require_text(self.workspace_scope_id, "workspace_scope_id"),
        )
        object.__setattr__(
            self,
            "manifest_digest",
            require_text(self.manifest_digest, "manifest_digest"),
        )
        _require_nonnegative_int(self.item_count, "item_count")
        if self.intent_event_id is not None:
            object.__setattr__(
                self,
                "intent_event_id",
                require_text(self.intent_event_id, "intent_event_id"),
            )
        if type(self.intent_event_verified) is not bool:
            raise ValueError("intent_event_verified must be a boolean")
        arts = tuple(self.artifacts)
        for a in arts:
            if not isinstance(a, ArtifactMetadata):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ValueError(
                    "every artifact must be an ArtifactMetadata instance"
                )
        object.__setattr__(self, "artifacts", arts)
