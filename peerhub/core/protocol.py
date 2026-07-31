"""Canonical command, event, JSON, and error protocol types."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import NewType, TypeAlias


CommandID = NewType("CommandID", str)

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = (
    JsonScalar
    | tuple["JsonValue", ...]
    | Mapping[str, "JsonValue"]
)


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


def _require_text(value: str, name: str) -> str:
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


def _freeze_json_mapping(
    value: Mapping[str, object],
) -> Mapping[str, JsonValue]:
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
    """Encode a supported value as deterministic canonical JSON bytes."""

    normalized = _normalize_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class CommandEnvelope:
    """A transport-neutral Protocol v1 command envelope."""

    protocol_version: str
    command_id: CommandID
    correlation_id: str
    client_id: str
    scope: Mapping[str, JsonValue]
    method: str
    params: Mapping[str, JsonValue]
    expected_policy_revision: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "protocol_version",
            _require_text(
                self.protocol_version,
                "protocol_version",
            ),
        )
        object.__setattr__(
            self,
            "command_id",
            CommandID(
                _require_text(
                    str(self.command_id),
                    "command_id",
                )
            ),
        )
        for name in (
            "correlation_id",
            "client_id",
            "method",
        ):
            object.__setattr__(
                self,
                name,
                _require_text(getattr(self, name), name),
            )
        if self.expected_policy_revision is not None:
            object.__setattr__(
                self,
                "expected_policy_revision",
                _require_text(
                    self.expected_policy_revision,
                    "expected_policy_revision",
                ),
            )
        object.__setattr__(
            self,
            "scope",
            _freeze_json_mapping(self.scope),
        )
        object.__setattr__(
            self,
            "params",
            _freeze_json_mapping(self.params),
        )


@dataclass(frozen=True)
class EventEnvelope:
    """A transport-neutral Protocol v1 outbox event."""

    protocol_version: str
    event_id: str
    correlation_id: str
    sequence: int
    occurred_at: int
    kind: str
    payload: Mapping[str, JsonValue]
    request_id: str | None = None
    round_id: str | None = None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in (
            "protocol_version",
            "event_id",
            "correlation_id",
            "kind",
        ):
            object.__setattr__(
                self,
                name,
                _require_text(getattr(self, name), name),
            )
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("sequence must be a nonnegative integer")
        if type(self.occurred_at) is not int or self.occurred_at < 0:
            raise ValueError(
                "occurred_at must be a nonnegative integer"
            )
        for name in ("request_id", "round_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    _require_text(value, name),
                )
        object.__setattr__(
            self,
            "payload",
            _freeze_json_mapping(self.payload),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(
                _require_text(reference, "evidence_ref")
                for reference in self.evidence_refs
            ),
        )
