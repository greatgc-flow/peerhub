"""Canonical command, event, JSON, and error protocol types."""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Generic, Literal, NewType, TypeAlias, TypeVar
from uuid import RFC_4122, UUID

from .execution import ExecutionCertainty

R = TypeVar("R")


PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0
SCHEMA_VERSION = "1.0.0"
ATTEMPT_TERMINAL_OBSERVED_EVENT_KIND = (
    "AttemptTerminalObserved"
)

CommandID = NewType("CommandID", str)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = (
    JsonScalar
    | tuple["JsonValue", ...]
    | Mapping[str, "JsonValue"]
)
RevisionValue: TypeAlias = str | int


class OperationalFailureCategory(str, Enum):
    """Measured operational categories frozen by HR-03."""

    EXECUTABLE_UNAVAILABLE = "EXECUTABLE_UNAVAILABLE"
    ENVIRONMENT_UNAVAILABLE = "ENVIRONMENT_UNAVAILABLE"
    AUTH_UNAVAILABLE = "AUTH_UNAVAILABLE"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    RATE_LIMITED = "RATE_LIMITED"


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
    COMMAND_NOT_BACKED = "COMMAND_NOT_BACKED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
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
    ARTIFACT_RESERVATION_FAILED = (
        "ARTIFACT_RESERVATION_FAILED"
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


def require_uuid4(value: str, name: str) -> str:
    """Validate and return one canonical RFC 4122 UUIDv4 string."""

    normalized = require_text(value, name)
    try:
        parsed = UUID(normalized)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be an RFC 4122 UUIDv4"
        ) from exc
    if parsed.version != 4 or parsed.variant != RFC_4122:
        raise ValueError(f"{name} must be an RFC 4122 UUIDv4")
    return str(parsed)


def _normalize_json_string(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(
            "JSON strings cannot contain unpaired surrogates"
        ) from exc
    return normalized


def _freeze_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool):
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, str):
        return _normalize_json_string(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("JSON object keys must be strings")
            key = _normalize_json_string(raw_key)
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
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, str):
        return _normalize_json_string(value)
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("JSON object keys must be strings")
            key = _normalize_json_string(raw_key)
            if key in normalized:
                raise ValueError(
                    f"duplicate JSON key after normalization: {key}"
                )
            normalized[key] = _normalize_json_value(raw_value)
        return normalized
    raise ValueError(
        f"unsupported JSON value type: {type(value).__name__}"
    )


def _expand_exponential_number(raw: str) -> str:
    sign = ""
    unsigned = raw
    if unsigned.startswith("-"):
        sign = "-"
        unsigned = unsigned[1:]

    mantissa, raw_exponent = unsigned.split("e", 1)
    exponent = int(raw_exponent)
    integer, _, fraction = mantissa.partition(".")
    digits = integer + fraction
    decimal_position = len(integer) + exponent

    if decimal_position <= 0:
        expanded = "0." + ("0" * -decimal_position) + digits
    elif decimal_position >= len(digits):
        expanded = digits + ("0" * (decimal_position - len(digits)))
    else:
        expanded = (
            digits[:decimal_position]
            + "."
            + digits[decimal_position:]
        )
    return sign + expanded


def _fixed_to_scientific(raw: str) -> str:
    sign = ""
    unsigned = raw
    if unsigned.startswith("-"):
        sign = "-"
        unsigned = unsigned[1:]

    integer, _, fraction = unsigned.partition(".")
    digits = integer + fraction
    first_nonzero = next(
        index
        for index, digit in enumerate(digits)
        if digit != "0"
    )
    significant = digits[first_nonzero:].rstrip("0")
    exponent = len(integer) - first_nonzero - 1
    coefficient = significant[0]
    if len(significant) > 1:
        coefficient += "." + significant[1:]
    exponent_text = (
        f"+{exponent}" if exponent >= 0 else str(exponent)
    )
    return f"{sign}{coefficient}e{exponent_text}"


def _canonical_float(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if value == 0.0:
        return "0"

    raw = repr(value).lower()
    absolute = abs(value)

    # ECMAScript Number::toString, as required by RFC 8785, uses
    # fixed notation in this interval and scientific notation outside it.
    if 1e-6 <= absolute < 1e21:
        if "e" in raw:
            return _expand_exponential_number(raw)
        if raw.endswith(".0"):
            return raw[:-2]
        return raw

    if "e" in raw:
        mantissa, raw_exponent = raw.split("e", 1)
        if mantissa.endswith(".0"):
            mantissa = mantissa[:-2]
        exponent = int(raw_exponent)
        exponent_text = (
            f"+{exponent}" if exponent >= 0 else str(exponent)
        )
        return f"{mantissa}e{exponent_text}"
    return _fixed_to_scientific(raw)


def _canonical_json_text(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        return _canonical_float(value)
    if isinstance(value, str):
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    if isinstance(value, list):
        return "[" + ",".join(
            _canonical_json_text(item) for item in value
        ) + "]"
    if isinstance(value, Mapping):
        # RFC 8785 sorts object member names as UTF-16 code units.
        keys = sorted(
            value,
            key=lambda item: item.encode("utf-16-be"),
        )
        return "{" + ",".join(
            _canonical_json_text(key)
            + ":"
            + _canonical_json_text(value[key])
            for key in keys
        ) + "}"
    raise ValueError(
        f"unsupported JSON value type: {type(value).__name__}"
    )


def canonical_json_bytes(value: object) -> bytes:
    """Encode an NFC-normalized value using RFC 8785 JCS."""

    normalized = _normalize_json_value(value)
    return _canonical_json_text(normalized).encode("utf-8")


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
class AttemptTerminalObserved:
    """Operational-only terminal observation consumed by telemetry.

    CompletionAssessment is structurally absent: semantic task
    completion must not feed health automatically.
    """

    instance_id: str
    profile_id: str
    transport: str
    operational_failure_category: (
        OperationalFailureCategory | None
    )
    execution_certainty: ExecutionCertainty
    process_integrity: bool
    started_at: int | None
    terminal_at: int
    latency: int | None
    evidence_refs: tuple[str, ...] = field(
        default_factory=tuple
    )

    def __post_init__(self) -> None:
        for name in (
            "instance_id",
            "profile_id",
            "transport",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )

        if (
            self.operational_failure_category is not None
            and not isinstance(
                self.operational_failure_category,
                OperationalFailureCategory,
            )
        ):
            raise ValueError(
                "operational_failure_category must be "
                "OperationalFailureCategory or null"
            )

        if not isinstance(
            self.execution_certainty,
            ExecutionCertainty,
        ):
            raise ValueError(
                "execution_certainty must be ExecutionCertainty"
            )
        if type(self.process_integrity) is not bool:
            raise ValueError(
                "process_integrity must be a boolean"
            )

        if self.started_at is not None and (
            type(self.started_at) is not int
            or self.started_at < 0
        ):
            raise ValueError(
                "started_at must be a nonnegative integer or null"
            )
        if (
            type(self.terminal_at) is not int
            or self.terminal_at < 0
        ):
            raise ValueError(
                "terminal_at must be a nonnegative integer"
            )
        if (
            self.started_at is not None
            and self.terminal_at < self.started_at
        ):
            raise ValueError(
                "terminal_at cannot precede started_at"
            )

        if self.latency is not None and (
            type(self.latency) is not int
            or self.latency < 0
        ):
            raise ValueError(
                "latency must be a nonnegative integer or null"
            )
        if self.started_at is None and self.latency is not None:
            raise ValueError(
                "latency requires a started_at observation"
            )

        object.__setattr__(
            self,
            "evidence_refs",
            tuple(
                require_text(reference, "evidence_ref")
                for reference in self.evidence_refs
            ),
        )


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

        object.__setattr__(
            self,
            "event_id",
            require_uuid4(self.event_id, "event_id"),
        )

        for name in (
            "schema_version",
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


class IdempotencyDisposition(str, Enum):
    CREATED = "CREATED"
    HIT = "HIT"


class ErrorPhase(str, Enum):
    VALIDATION = "VALIDATION"
    ADMISSION = "ADMISSION"
    PRE_SPAWN = "PRE_SPAWN"
    POST_SPAWN = "POST_SPAWN"
    ASSESSMENT = "ASSESSMENT"
    EFFECT = "EFFECT"


class RetryDisposition(str, Enum):
    SAFE = "SAFE"
    UNSAFE = "UNSAFE"
    CONDITIONAL = "CONDITIONAL"
    NEVER = "NEVER"


@dataclass(frozen=True)
class ErrorDetail:
    code: ErrorCode
    phase: ErrorPhase
    execution_certainty: ExecutionCertainty
    retry_disposition: RetryDisposition
    message: str
    details: Mapping[str, JsonValue]


@dataclass(frozen=True)
class CommandSuccess(Generic[R]):
    ok: Literal[True]
    protocol_major: int
    protocol_minor: int
    schema_version: str

    diagnostic_id: str
    correlation_id: str
    command_id: str | None

    state: str
    receipt_ref: str | None
    policy_revision: RevisionValue | None
    configuration_revision: RevisionValue | None
    idempotency: IdempotencyDisposition

    result: R


@dataclass(frozen=True)
class CommandFailure:
    ok: Literal[False]
    protocol_major: int
    protocol_minor: int
    schema_version: str

    diagnostic_id: str
    correlation_id: str | None
    command_id: str | None
    error: ErrorDetail


CommandOutcome: TypeAlias = CommandSuccess[R] | CommandFailure


def cli_exit_code(outcome: CommandOutcome[object]) -> int:
    if outcome.ok:
        return 0

    code = outcome.error.code

    if code == ErrorCode.INTERNAL_ERROR:
        return 1

    if code in {
        ErrorCode.PROTOCOL_VERSION_MISMATCH,
        ErrorCode.SCHEMA_VERSION_UNSUPPORTED,
        ErrorCode.MALFORMED_ENVELOPE,
        ErrorCode.TRUNCATED_FRAME,
        ErrorCode.UNKNOWN_COMMAND,
        ErrorCode.COMMAND_NOT_BACKED,
        ErrorCode.INVALID_PARAMS,
        ErrorCode.MISSING_IDEMPOTENCY_KEY,
        ErrorCode.RECORD_NOT_FOUND,
    } or outcome.error.phase == ErrorPhase.VALIDATION:
        return 2

    if code in {
        ErrorCode.CLIENT_UNKNOWN,
        ErrorCode.ACTOR_UNAUTHORIZED,
        ErrorCode.SCOPE_UNAUTHORIZED,
    }:
        return 3

    if code in {
        ErrorCode.PEER_UNAVAILABLE,
        ErrorCode.PROFILE_UNAVAILABLE,
        ErrorCode.ROUTE_EXHAUSTED,
        ErrorCode.ADMISSION_CLOSED,
        ErrorCode.CONFIGURATION_STALE,
        ErrorCode.POLICY_STALE,
    } or outcome.error.phase == ErrorPhase.ADMISSION:
        return 4

    if code in {
        ErrorCode.DUPLICATE_ID_CONTENT_MISMATCH,
        ErrorCode.IDEMPOTENCY_PAYLOAD_MISMATCH,
        ErrorCode.REVISION_CONFLICT,
        ErrorCode.UNIQUE_CONSTRAINT_VIOLATED,
        ErrorCode.EPOCH_STALE,
        ErrorCode.CUTOVER_INPUT_DRIFT,
        ErrorCode.CUTOVER_EPOCH_CONTENDED,
        ErrorCode.MIGRATION_LOCK_LOST,
        ErrorCode.WRITE_SCOPE_NOT_QUIESCED,
        ErrorCode.PEERHUB_ERA_WRITES_PRESENT,
    }:
        return 7

    if (
        outcome.error.execution_certainty == ExecutionCertainty.NOT_STARTED
        or outcome.error.phase == ErrorPhase.PRE_SPAWN
    ):
        return 5

    return 6
