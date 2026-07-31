"""Canonical command, event, JSON, and error protocol types."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import NewType, TypeAlias


PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0
SCHEMA_VERSION = "1.0.0"

CommandID = NewType("CommandID", str)

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = (
    JsonScalar
    | tuple["JsonValue", ...]
    | Mapping[str, "JsonValue"]
)
RevisionValue: TypeAlias = str | int


class ErrorCode(str, Enum):
    """Stable Protocol v1 error codes."""

    PROTOCOL_VERSION_MISMATCH = "PROTOCOL_VERSION_MISMATCH"
    SCHEMA_VERSION_UNSUPPORTED = "SCHEMA_VERSION_UNSUPPORTED"
    MALFORMED_ENVELOPE = "MALFORMED_ENVELOPE"
    TRUNCATED_FRAME = "TRUNCATED_FRAME"
    DUPLICATE_ID_CONTENT_MISMATCH = (
        "DUPLICATE_ID_CONTENT_MISMATCH"
    )

    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    INVALID_PARAMS = "INVALID_PARAMS"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    MISSING_IDEMPOTENCY_KEY = "MISSING_IDEMPOTENCY_KEY"

    CLIENT_UNKNOWN = "CLIENT_UNKNOWN"
    ACTOR_UNAUTHORIZED = "ACTOR_UNAUTHORIZED"
    SCOPE_UNAUTHORIZED = "SCOPE_UNAUTHORIZED"

    PEER_UNAVAILABLE = "PEER_UNAVAILABLE"
    PROFILE_UNAVAILABLE = "PROFILE_UNAVAILABLE"
    ROUTE_EXHAUSTED = "ROUTE_EXHAUSTED"
    ADMISSION_CLOSED = "ADMISSION_CLOSED"
    CONFIGURATION_STALE = "CONFIGURATION_STALE"
    POLICY_STALE = "POLICY_STALE"

    IDEMPOTENCY_HIT = "IDEMPOTENCY_HIT"
    IDEMPOTENCY_PAYLOAD_MISMATCH = (
        "IDEMPOTENCY_PAYLOAD_MISMATCH"
    )

    SPAWN_FAILED = "SPAWN_FAILED"
    START_UNCERTAIN = "START_UNCERTAIN"
    PROCESS_TIMEOUT = "PROCESS_TIMEOUT"
    SILENCE_TIMEOUT = "SILENCE_TIMEOUT"
    PROCESS_KILLED = "PROCESS_KILLED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_OWNERSHIP_LOST = "LEASE_OWNERSHIP_LOST"
    CANCELLATION_CLEANUP_FAILED = (
        "CANCELLATION_CLEANUP_FAILED"
    )
    ARTIFACT_IDENTITY_UNPROVABLE = (
        "ARTIFACT_IDENTITY_UNPROVABLE"
    )

    REVISION_CONFLICT = "REVISION_CONFLICT"
    RECORD_NOT_FOUND = "RECORD_NOT_FOUND"
    UNIQUE_CONSTRAINT_VIOLATED = (
        "UNIQUE_CONSTRAINT_VIOLATED"
    )
    EPOCH_STALE = "EPOCH_STALE"
    CUTOVER_INPUT_DRIFT = "CUTOVER_INPUT_DRIFT"
    CUTOVER_EPOCH_CONTENDED = "CUTOVER_EPOCH_CONTENDED"
    MIGRATION_LOCK_LOST = "MIGRATION_LOCK_LOST"
    WRITE_SCOPE_NOT_QUIESCED = "WRITE_SCOPE_NOT_QUIESCED"
    PEERHUB_ERA_WRITES_PRESENT = "PEERHUB_ERA_WRITES_PRESENT"
    WORKSPACE_IDENTITY_MISMATCH = (
        "WORKSPACE_IDENTITY_MISMATCH"
    )
    FILESYSTEM_UNSUPPORTED = "FILESYSTEM_UNSUPPORTED"

    PROTOCOL_ASSESSMENT_FAILED = (
        "PROTOCOL_ASSESSMENT_FAILED"
    )
    COMPLETION_INCOMPLETE = "COMPLETION_INCOMPLETE"
    COMPLETION_UNVERIFIED = "COMPLETION_UNVERIFIED"


def require_text(value: str, name: str) -> str:
    """Validate and NFC-normalize a required protocol string."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return unicodedata.normalize("NFC", value)


def _freeze_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool):
        return value
    if type(value) is int:
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
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
            frozen[key] = _freeze_json_value(raw_value)
        return MappingProxyType(frozen)
    raise ValueError(
        f"unsupported JSON value type: {type(value).__name__}"
    )


def freeze_json_mapping(
    value: Mapping[str, object],
) -> Mapping[str, JsonValue]:
    """Return an immutable, NFC-normalized JSON object."""

    frozen = _freeze_json_value(value)
    if not isinstance(frozen, Mapping):
        raise ValueError("JSON value must be an object")
    return frozen


def _normalize_json_value(value: object) -> object:
    if value is None or isinstance(value, bool):
        return value
    if type(value) is int:
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("JSON object keys must be strings")
            key = unicodedata.normalize("NFC", raw_key)
            if key in normalized:
                raise ValueError(
                    f"duplicate JSON key after normalization: {key}"
                )
            normalized[key] = _normalize_json_value(raw_value)
        return normalized
    raise ValueError(
        f"unsupported JSON value type: {type(value).__name__}"
    )


def canonical_json_bytes(value: object) -> bytes:
    """Encode supported values as deterministic canonical JSON bytes.

    Protocol v1 deliberately admits only integer JSON numbers in this
    kernel. Floating-point values, including non-finite values, are
    rejected before encoding rather than being serialized ambiguously.
    """

    normalized = _normalize_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validate_protocol_component(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _validate_revision(
    value: RevisionValue | None,
    name: str,
) -> None:
    if value is None:
        return
    if type(value) is int:
        if value < 0:
            raise ValueError(f"{name} must be nonnegative")
        return
    if isinstance(value, str):
        require_text(value, name)
        return
    raise ValueError(f"{name} must be a string, integer, or null")


@dataclass(frozen=True)
class CommandEnvelope:
    """Caller submission for a Protocol v1 command.

    The caller supplies ``client_request_id`` and ``correlation_id``.
    It never supplies ``command_id``; that identifier is minted by the
    server only after successful authorization and admission.
    """

    protocol_major: int
    protocol_minor: int
    schema_version: str
    client_request_id: str
    correlation_id: str
    client_id: str
    actor_id: str | None
    scope: Mapping[str, JsonValue]
    method: str
    params: Mapping[str, JsonValue]
    idempotency_key: str | None
    expected_policy_revision: RevisionValue | None
    expected_configuration_revision: RevisionValue | None
    client_timestamp: int

    def __post_init__(self) -> None:
        _validate_protocol_component(
            self.protocol_major,
            "protocol_major",
        )
        _validate_protocol_component(
            self.protocol_minor,
            "protocol_minor",
        )

        for name in (
            "schema_version",
            "client_request_id",
            "correlation_id",
            "client_id",
            "method",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )

        if self.actor_id is not None:
            object.__setattr__(
                self,
                "actor_id",
                require_text(self.actor_id, "actor_id"),
            )

        if self.idempotency_key is not None:
            object.__setattr__(
                self,
                "idempotency_key",
                require_text(
                    self.idempotency_key,
                    "idempotency_key",
                ),
            )

        _validate_revision(
            self.expected_policy_revision,
            "expected_policy_revision",
        )
        _validate_revision(
            self.expected_configuration_revision,
            "expected_configuration_revision",
        )

        if (
            type(self.client_timestamp) is not int
            or self.client_timestamp < 0
        ):
            raise ValueError(
                "client_timestamp must be a nonnegative integer"
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

    @property
    def protocol_version(self) -> str:
        """Return the negotiated protocol version as ``major.minor``."""

        return f"{self.protocol_major}.{self.protocol_minor}"


@dataclass(frozen=True)
class EventEnvelope:
    """Transport-neutral Protocol v1 outbox or stream event."""

    protocol_major: int
    protocol_minor: int
    schema_version: str
    event_id: str
    correlation_id: str
    occurred_at: int
    kind: str
    payload: Mapping[str, JsonValue]
    outbox_position: int | None = None
    request_id: str | None = None
    round_id: str | None = None
    stream_id: str | None = None
    stream_sequence: int | None = None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    predecessor_digest: str | None = None
    recovery_context: Mapping[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        _validate_protocol_component(
            self.protocol_major,
            "protocol_major",
        )
        _validate_protocol_component(
            self.protocol_minor,
            "protocol_minor",
        )

        for name in (
            "schema_version",
            "event_id",
            "correlation_id",
            "kind",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )

        if (
            type(self.occurred_at) is not int
            or self.occurred_at < 0
        ):
            raise ValueError(
                "occurred_at must be a nonnegative integer"
            )

        if self.outbox_position is not None and (
            type(self.outbox_position) is not int
            or self.outbox_position < 1
        ):
            raise ValueError(
                "outbox_position must be a positive integer or null"
            )

        for name in ("request_id", "round_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    require_text(value, name),
                )

        if (self.stream_id is None) != (
            self.stream_sequence is None
        ):
            raise ValueError(
                "stream_id and stream_sequence must be supplied together"
            )
        if self.stream_id is not None:
            object.__setattr__(
                self,
                "stream_id",
                require_text(self.stream_id, "stream_id"),
            )
            if (
                type(self.stream_sequence) is not int
                or self.stream_sequence < 0
            ):
                raise ValueError(
                    "stream_sequence must be a nonnegative integer"
                )

        if self.predecessor_digest is not None:
            object.__setattr__(
                self,
                "predecessor_digest",
                require_text(
                    self.predecessor_digest,
                    "predecessor_digest",
                ),
            )

        object.__setattr__(
            self,
            "payload",
            freeze_json_mapping(self.payload),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(
                require_text(reference, "evidence_ref")
                for reference in self.evidence_refs
            ),
        )

        if self.recovery_context is not None:
            object.__setattr__(
                self,
                "recovery_context",
                freeze_json_mapping(self.recovery_context),
            )

    @property
    def protocol_version(self) -> str:
        """Return the event protocol version as ``major.minor``."""

        return f"{self.protocol_major}.{self.protocol_minor}"
