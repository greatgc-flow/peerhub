"""Published immutable governance DTOs."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from peerhub.core.protocol import CommandID, JsonValue


class MutationDisposition(str, Enum):
    """The result of submitting a mutation request."""

    COMMITTED = "COMMITTED"
    IDEMPOTENCY_HIT = "IDEMPOTENCY_HIT"


class TransitionStatus(str, Enum):
    """The durable state of an initial transition receipt."""

    COMMITTED_ENFORCEMENT_PENDING = (
        "COMMITTED_ENFORCEMENT_PENDING"
    )


class OutboxState(str, Enum):
    """The recoverable lifecycle of one outbox event."""

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    CONSUMED = "CONSUMED"


class EffectOutcome(str, Enum):
    """The immutable terminal outcome of an effect attempt."""

    EFFECT_SUCCEEDED = "EFFECT_SUCCEEDED"
    EFFECT_FAILED = "EFFECT_FAILED"


class RecoveryDisposition(str, Enum):
    """How recovery may handle one unconsumed effect intent."""

    READY_TO_CLAIM = "READY_TO_CLAIM"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return unicodedata.normalize("NFC", value)


def _optional_text(
    value: str | None,
    name: str,
) -> str | None:
    if value is None:
        return None
    return _text(value, name)


def _nonnegative(value: int, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _positive(value: int, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _freeze_json(value: object) -> JsonValue:
    if value is None or isinstance(value, bool):
        return value
    if type(value) is int:
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("JSON object keys must be strings")
            key = unicodedata.normalize("NFC", raw_key)
            if key in frozen:
                raise ValueError(
                    f"duplicate JSON key after normalization: {key}"
                )
            frozen[key] = _freeze_json(raw_value)
        return MappingProxyType(frozen)
    raise ValueError(
        f"unsupported JSON value type: {type(value).__name__}"
    )


def _freeze_mapping(
    value: Mapping[str, object],
) -> Mapping[str, JsonValue]:
    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise ValueError("value must be a JSON object")
    return frozen


def _freeze_references(
    values: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(_text(value, "evidence_ref") for value in values)


@dataclass(frozen=True)
class EffectIntent:
    """A declarative effect to execute only after transition commit."""

    kind: str
    payload: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _text(self.kind, "kind"))
        object.__setattr__(
            self,
            "payload",
            _freeze_mapping(self.payload),
        )


@dataclass(frozen=True)
class MutationRequest:
    """Caller intent for one governed target transition."""

    request_id: str
    command_id: CommandID
    correlation_id: str
    client_id: str
    command_type: str
    idempotency_key: str
    actor_id: str
    policy_revision: str
    target_id: str
    expected_revision: int
    operation: str
    desired_state: Mapping[str, JsonValue]
    effect_intent: EffectIntent

    def __post_init__(self) -> None:
        for name in (
            "request_id",
            "correlation_id",
            "client_id",
            "command_type",
            "idempotency_key",
            "actor_id",
            "policy_revision",
            "target_id",
            "operation",
        ):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "command_id",
            CommandID(
                _text(str(self.command_id), "command_id")
            ),
        )
        object.__setattr__(
            self,
            "expected_revision",
            _nonnegative(
                self.expected_revision,
                "expected_revision",
            ),
        )
        object.__setattr__(
            self,
            "desired_state",
            _freeze_mapping(self.desired_state),
        )


@dataclass(frozen=True)
class TargetState:
    """One authoritative revisioned governance target."""

    target_id: str
    revision: int
    state: Mapping[str, JsonValue]
    updated_at: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "target_id",
            _text(self.target_id, "target_id"),
        )
        object.__setattr__(
            self,
            "revision",
            _positive(self.revision, "revision"),
        )
        object.__setattr__(
            self,
            "state",
            _freeze_mapping(self.state),
        )
        object.__setattr__(
            self,
            "updated_at",
            _nonnegative(self.updated_at, "updated_at"),
        )


@dataclass(frozen=True)
class MutationPlan:
    """An authorized normalized transition ready for atomic commit."""

    plan_id: str
    request_id: str
    request_digest: str
    target_id: str
    previous_revision: int
    next_revision: int
    next_state: Mapping[str, JsonValue]
    effect_intent: EffectIntent
    planned_at: int

    def __post_init__(self) -> None:
        for name in (
            "plan_id",
            "request_id",
            "request_digest",
            "target_id",
        ):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "previous_revision",
            _nonnegative(
                self.previous_revision,
                "previous_revision",
            ),
        )
        object.__setattr__(
            self,
            "next_revision",
            _positive(self.next_revision, "next_revision"),
        )
        if self.next_revision != self.previous_revision + 1:
            raise ValueError(
                "next_revision must equal previous_revision + 1"
            )
        object.__setattr__(
            self,
            "next_state",
            _freeze_mapping(self.next_state),
        )
        object.__setattr__(
            self,
            "planned_at",
            _nonnegative(self.planned_at, "planned_at"),
        )


@dataclass(frozen=True)
class CommandBinding:
    """A durable command-idempotency identity and stored receipt."""

    client_id: str
    command_type: str
    idempotency_key: str
    payload_digest: str
    request_id: str
    receipt_id: str
    created_at: int

    def __post_init__(self) -> None:
        for name in (
            "client_id",
            "command_type",
            "idempotency_key",
            "payload_digest",
            "request_id",
            "receipt_id",
        ):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "created_at",
            _nonnegative(self.created_at, "created_at"),
        )


@dataclass(frozen=True)
class TransitionReceipt:
    """Immutable evidence that a target transition committed."""

    receipt_id: str
    request_id: str
    plan_id: str
    target_id: str
    previous_revision: int
    next_revision: int
    status: TransitionStatus
    committed_at: int
    outbox_event_id: str
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in (
            "receipt_id",
            "request_id",
            "plan_id",
            "target_id",
            "outbox_event_id",
        ):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "previous_revision",
            _nonnegative(
                self.previous_revision,
                "previous_revision",
            ),
        )
        object.__setattr__(
            self,
            "next_revision",
            _positive(self.next_revision, "next_revision"),
        )
        if self.next_revision != self.previous_revision + 1:
            raise ValueError(
                "next_revision must equal previous_revision + 1"
            )
        if not isinstance(self.status, TransitionStatus):
            raise ValueError("status must be a TransitionStatus")
        object.__setattr__(
            self,
            "committed_at",
            _nonnegative(self.committed_at, "committed_at"),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _freeze_references(self.evidence_refs),
        )


@dataclass(frozen=True)
class OutboxEvent:
    """One canonical protocol-wide durable outbox event."""

    event_id: str
    protocol_major: int
    protocol_minor: int
    schema_version: str
    correlation_id: str
    occurred_at: int
    event_kind: str
    payload: Mapping[str, JsonValue]
    state: OutboxState
    created_at: int
    request_id: str | None = None
    transition_receipt_id: str | None = None
    topic: str | None = None
    outbox_position: int | None = None
    round_id: str | None = None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    predecessor_digest: str | None = None
    recovery_context: Mapping[str, JsonValue] | None = None
    claimed_by: str | None = None
    claim_attempt_id: str | None = None
    claimed_at: int | None = None
    consumed_at: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "schema_version",
            "correlation_id",
            "event_kind",
        ):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "protocol_major",
            _nonnegative(self.protocol_major, "protocol_major"),
        )
        object.__setattr__(
            self,
            "protocol_minor",
            _nonnegative(self.protocol_minor, "protocol_minor"),
        )
        object.__setattr__(
            self,
            "occurred_at",
            _nonnegative(self.occurred_at, "occurred_at"),
        )
        object.__setattr__(
            self,
            "created_at",
            _nonnegative(self.created_at, "created_at"),
        )
        if self.created_at < self.occurred_at:
            raise ValueError(
                "created_at cannot precede occurred_at"
            )
        if not isinstance(self.state, OutboxState):
            raise ValueError("state must be an OutboxState")
        object.__setattr__(
            self,
            "payload",
            _freeze_mapping(self.payload),
        )

        for name in (
            "request_id",
            "transition_receipt_id",
            "topic",
            "round_id",
            "predecessor_digest",
            "claimed_by",
            "claim_attempt_id",
        ):
            object.__setattr__(
                self,
                name,
                _optional_text(getattr(self, name), name),
            )

        if self.outbox_position is not None:
            object.__setattr__(
                self,
                "outbox_position",
                _positive(
                    self.outbox_position,
                    "outbox_position",
                ),
            )
        object.__setattr__(
            self,
            "evidence_refs",
            _freeze_references(self.evidence_refs),
        )
        if self.recovery_context is not None:
            object.__setattr__(
                self,
                "recovery_context",
                _freeze_mapping(self.recovery_context),
            )
        for name in ("claimed_at", "consumed_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    _nonnegative(value, name),
                )

        claim_fields = (
            self.claimed_by,
            self.claim_attempt_id,
            self.claimed_at,
        )
        claim_is_absent = all(
            value is None for value in claim_fields
        )
        claim_is_complete = all(
            value is not None for value in claim_fields
        )

        if self.state is OutboxState.PENDING:
            if not claim_is_absent or self.consumed_at is not None:
                raise ValueError(
                    "pending outbox event cannot carry claim data"
                )
        elif self.state is OutboxState.CLAIMED:
            if not claim_is_complete or self.consumed_at is not None:
                raise ValueError(
                    "claimed outbox event requires complete claim data"
                )
        elif (
            not claim_is_complete
            or self.consumed_at is None
        ):
            raise ValueError(
                "consumed outbox event requires claim and consume data"
            )


@dataclass(frozen=True)
class EffectReceipt:
    """One immutable terminal receipt for an outbox effect."""

    effect_receipt_id: str
    request_id: str
    outbox_event_id: str
    attempt_id: str
    owner_id: str
    outcome: EffectOutcome
    completed_at: int
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in (
            "effect_receipt_id",
            "request_id",
            "outbox_event_id",
            "attempt_id",
            "owner_id",
        ):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), name),
            )
        if not isinstance(self.outcome, EffectOutcome):
            raise ValueError("outcome must be an EffectOutcome")
        object.__setattr__(
            self,
            "completed_at",
            _nonnegative(self.completed_at, "completed_at"),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _freeze_references(self.evidence_refs),
        )


@dataclass(frozen=True)
class MutationSubmission:
    """The receipt returned by a committed or idempotent submission."""

    disposition: MutationDisposition
    receipt: TransitionReceipt

    def __post_init__(self) -> None:
        if not isinstance(
            self.disposition,
            MutationDisposition,
        ):
            raise ValueError(
                "disposition must be a MutationDisposition"
            )


@dataclass(frozen=True)
class PendingEffect:
    """A committed transition and its unconsumed effect intent."""

    event: OutboxEvent
    transition_receipt: TransitionReceipt
    disposition: RecoveryDisposition

    def __post_init__(self) -> None:
        if not isinstance(
            self.disposition,
            RecoveryDisposition,
        ):
            raise ValueError(
                "disposition must be a RecoveryDisposition"
            )
        if self.event.transition_receipt_id is None:
            raise ValueError(
                "pending governance effect needs a transition receipt"
            )
        if self.event.request_id is None:
            raise ValueError(
                "pending governance effect needs a request ID"
            )
        if (
            self.event.transition_receipt_id
            != self.transition_receipt.receipt_id
        ):
            raise ValueError(
                "event and transition receipt do not match"
            )
        if (
            self.event.request_id
            != self.transition_receipt.request_id
        ):
            raise ValueError(
                "event and transition request IDs do not match"
            )
