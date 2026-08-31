"""Immutable contracts for health, admission, and probe recovery."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from peerhub.core.evidence import EvidenceRef
from peerhub.core.protocol import (
    OperationalFailureCategory,
    require_text,
)


def _require_nonnegative(
    value: int,
    name: str,
) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _require_positive(
    value: int,
    name: str,
) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _require_sha256_hex(
    value: str,
    name: str,
) -> str:
    normalized = require_text(value, name)
    if (
        len(normalized) != 64
        or any(
            character not in "0123456789abcdef"
            for character in normalized
        )
    ):
        raise ValueError(
            f"{name} must be a lowercase SHA-256 digest"
        )
    return normalized


def _normalize_text_tuple(  # pyright: ignore[reportUnusedFunction]
    values: tuple[str, ...],
    name: str,
) -> tuple[str, ...]:
    normalized = tuple(
        require_text(value, name)
        for value in values
    )
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} cannot contain duplicates")
    return normalized


def _normalize_refs(
    values: tuple[EvidenceRef, ...],
) -> tuple[EvidenceRef, ...]:
    return tuple(
        EvidenceRef(require_text(value, "evidence_ref"))
        for value in values
    )


class AvailabilityState(str, Enum):
    """Live evidence-derived availability states."""

    UNKNOWN = "UNKNOWN"
    PROBING = "PROBING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"


class AdmissionState(str, Enum):
    """Live admission/quarantine states."""

    OPEN = "OPEN"
    COOLDOWN = "COOLDOWN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    QUARANTINED = "QUARANTINED"
    PROBE_AUTHORIZED = "PROBE_AUTHORIZED"


class ReadinessState(str, Enum):
    """Phase 0 readiness-evaluation compatibility vocabulary."""

    READY = "READY"
    PROBE_INCONCLUSIVE = "PROBE_INCONCLUSIVE"
    READINESS_STALE = "READINESS_STALE"


class ReadinessGateState(str, Enum):
    """Phase 0 readiness-gate compatibility vocabulary."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class AdmissionDecision(str, Enum):
    """Decision returned by one admission evaluation."""

    ADMITTED = "ADMITTED"
    REJECTED = "REJECTED"


class RevalidationAction(str, Enum):
    """The only Slice 4 action for expired readiness evidence."""

    REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED"


class HealthStage(str, Enum):
    """Canonical measured-failure boundary order."""

    RESOLVE_EXECUTABLE = "resolve_executable"
    VALIDATE_ENVIRONMENT = "validate_environment"
    AUTHENTICATE = "authenticate"
    CONNECT_NETWORK = "connect_network"
    CALL_PROVIDER = "call_provider"
    CHECK_USAGE_ADMISSION = "check_usage_admission"


class HealthStageStatus(str, Enum):
    """Observed outcome of one attempted health stage."""

    OK = "OK"
    FAILED = "FAILED"


class PolicyScope(str, Enum):
    """Scopes frozen by the HR-03 policy-action extension."""

    ROOT = "root"
    PROFILE = "profile"
    QUOTA_FAMILY = "quota_family"
    ENVIRONMENT = "environment"


class CircuitState(str, Enum):
    """Health-circuit state, distinct from admission state."""

    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    CIRCUIT_CLOSED = "CIRCUIT_CLOSED"


class QuarantineAuthorityClass(str, Enum):
    """Authority classes proven by HR-04."""

    AUTOMATIC = "AUTOMATIC"
    MANUAL = "MANUAL"
    SECURITY = "SECURITY"
    POLICY = "POLICY"


class RecoveryAuthorizationMode(str, Enum):
    """How a recovery-probe grant was authorized."""

    AUTOMATIC = "AUTOMATIC"
    ADMINISTRATIVE = "ADMINISTRATIVE"


class RecoveryGrantState(str, Enum):
    """Lifecycle state of a recovery-probe grant."""

    GRANTED = "GRANTED"
    CLAIMED = "CLAIMED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class ProbeResult(str, Enum):
    """Measured recovery-probe result."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class ProbeDisposition(str, Enum):
    """Disposition of an attempt to consume a probe grant."""

    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"


class ProbeTransition(str, Enum):
    """HR-06 recovery transition result."""

    FAILURE_BACKOFF_INCREMENTED = (
        "FAILURE_BACKOFF_INCREMENTED"
    )
    SUCCESS_CIRCUIT_CLOSED = "SUCCESS_CIRCUIT_CLOSED"
    STALE_PROBE_NO_OP = "STALE_PROBE_NO_OP"


@dataclass(frozen=True)
class HealthPolicy:
    """Injected versioned health-policy values.

    The jitter fraction is represented, but its deterministic derivation
    is intentionally outside this contract pending the ratified addendum
    required by Slice 4 decision 6.
    """

    policy_id: str
    revision: int
    readiness_freshness_seconds: int
    recovery_backoff_seconds: tuple[int, ...]
    recovery_jitter_fraction: float
    readiness_observation_threshold: int
    administrative_recovery_probe_limit: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_id",
            require_text(self.policy_id, "policy_id"),
        )
        _require_positive(self.revision, "revision")
        _require_positive(
            self.readiness_freshness_seconds,
            "readiness_freshness_seconds",
        )

        ladder = tuple(self.recovery_backoff_seconds)
        if not ladder:
            raise ValueError(
                "recovery_backoff_seconds must not be empty"
            )
        for delay in ladder:
            _require_positive(
                delay,
                "recovery_backoff_seconds entry",
            )
        if any(
            later < earlier
            for earlier, later in zip(
                ladder,
                ladder[1:],
            )
        ):
            raise ValueError(
                "recovery backoff must be nondecreasing"
            )
        object.__setattr__(
            self,
            "recovery_backoff_seconds",
            ladder,
        )

        if (
            type(self.recovery_jitter_fraction) is not float
            or not math.isfinite(
                self.recovery_jitter_fraction
            )
            or self.recovery_jitter_fraction < 0.0
            or self.recovery_jitter_fraction > 1.0
        ):
            raise ValueError(
                "recovery_jitter_fraction must be finite "
                "and between zero and one"
            )

        _require_positive(
            self.readiness_observation_threshold,
            "readiness_observation_threshold",
        )
        _require_positive(
            self.administrative_recovery_probe_limit,
            "administrative_recovery_probe_limit",
        )


@dataclass(frozen=True)
class AdministrativeRecoveryBudgetSnapshot:
    """Workspace-wide anchored administrative-recovery budget."""

    budget_id: str
    window_start: int
    count: int
    revision: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "budget_id",
            require_text(self.budget_id, "budget_id"),
        )
        _require_nonnegative(self.window_start, "window_start")
        _require_positive(self.count, "count")
        _require_positive(self.revision, "revision")


@dataclass(frozen=True)
class ReadinessEvaluation:
    """Fixture-domain readiness result plus explicit live-state mapping."""

    readiness_state: ReadinessState
    availability_state: AvailabilityState
    gate_state: ReadinessGateState
    admission_decision: AdmissionDecision
    provider_effect_permitted: bool
    reason_code: str | None
    revalidation_action: RevalidationAction | None
    zero_dispatch_calls: bool

    def __post_init__(self) -> None:
        for name, enum_type in (
            ("readiness_state", ReadinessState),
            ("availability_state", AvailabilityState),
            ("gate_state", ReadinessGateState),
            ("admission_decision", AdmissionDecision),
        ):
            if not isinstance(getattr(self, name), enum_type):
                raise ValueError(f"{name} has the wrong enum type")

        if type(self.provider_effect_permitted) is not bool:
            raise ValueError(
                "provider_effect_permitted must be a boolean"
            )
        if type(self.zero_dispatch_calls) is not bool:
            raise ValueError(
                "zero_dispatch_calls must be a boolean"
            )

        if self.reason_code is not None:
            object.__setattr__(
                self,
                "reason_code",
                require_text(
                    self.reason_code,
                    "reason_code",
                ),
            )
        if (
            self.revalidation_action is not None
            and not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
                self.revalidation_action,
                RevalidationAction,
            )
        ):
            raise ValueError(
                "revalidation_action has the wrong enum type"
            )

        if (
            self.admission_decision
            is AdmissionDecision.REJECTED
            and self.provider_effect_permitted
        ):
            raise ValueError(
                "rejected readiness cannot permit provider effects"
            )


@dataclass(frozen=True)
class HealthStageObservation:
    """One stage result in the canonical HR-03 trace."""

    stage: HealthStage
    status: HealthStageStatus

    def __post_init__(self) -> None:
        if not isinstance(self.stage, HealthStage):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("stage must be HealthStage")
        if not isinstance(self.status, HealthStageStatus):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("status must be HealthStageStatus")


@dataclass(frozen=True)
class HealthFailureClassification:
    """Derived failure boundary and its no-downstream-call audit."""

    category: OperationalFailureCategory
    attempted_trace: tuple[HealthStageObservation, ...]
    forbidden_downstream_stages: tuple[HealthStage, ...]
    forbidden_stages_present: tuple[HealthStage, ...]
    http_status: int | None = None
    verified_family_evidence: bool | None = None
    admission_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.category,
            OperationalFailureCategory,
        ):
            raise ValueError(
                "category must be OperationalFailureCategory"
            )

        object.__setattr__(
            self,
            "attempted_trace",
            tuple(self.attempted_trace),
        )
        object.__setattr__(
            self,
            "forbidden_downstream_stages",
            tuple(self.forbidden_downstream_stages),
        )
        object.__setattr__(
            self,
            "forbidden_stages_present",
            tuple(self.forbidden_stages_present),
        )

        if self.http_status is not None:
            _require_nonnegative(
                self.http_status,
                "http_status",
            )
        if (
            self.verified_family_evidence is not None
            and type(self.verified_family_evidence) is not bool
        ):
            raise ValueError(
                "verified_family_evidence must be boolean or null"
            )
        if type(self.admission_only) is not bool:
            raise ValueError("admission_only must be a boolean")


@dataclass(frozen=True)
class EvidenceSubject:
    """Scope and subject selected by measured evidence."""

    scope: PolicyScope
    subject: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, PolicyScope):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("scope must be PolicyScope")
        object.__setattr__(
            self,
            "subject",
            require_text(self.subject, "subject"),
        )


@dataclass(frozen=True)
class PolicyReceipt:
    """Identity which fences circuit clearance and probe results."""

    incident: str
    gate_generation: int
    timestamp: int
    fingerprint: str

    def __post_init__(self) -> None:
        for name in ("incident", "fingerprint"):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        _require_nonnegative(
            self.gate_generation,
            "gate_generation",
        )
        _require_nonnegative(self.timestamp, "timestamp")


@dataclass(frozen=True)
class PolicyAction:
    """Exact HR-03 health-circuit policy action."""

    scope: PolicyScope
    subject: str
    circuit_state: CircuitState
    quarantine_authority_class: QuarantineAuthorityClass
    receipt: PolicyReceipt

    def __post_init__(self) -> None:
        if not isinstance(self.scope, PolicyScope):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("scope must be PolicyScope")
        object.__setattr__(
            self,
            "subject",
            require_text(self.subject, "subject"),
        )
        if self.circuit_state is not CircuitState.CIRCUIT_OPEN:
            raise ValueError(
                "policy action must open a health circuit"
            )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.quarantine_authority_class,
            QuarantineAuthorityClass,
        ):
            raise ValueError(
                "quarantine_authority_class has wrong enum type"
            )
        if not isinstance(self.receipt, PolicyReceipt):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("receipt must be PolicyReceipt")


@dataclass(frozen=True)
class HealthCircuitSnapshot:
    """Revisioned circuit scoped independently from health projection."""

    circuit_id: str
    scope: PolicyScope
    subject: str
    state: CircuitState
    quarantine_authority_class: QuarantineAuthorityClass
    receipt: PolicyReceipt | None
    backoff_count: int
    cooldown_until: int | None
    revision: int
    created_at: int
    updated_at: int

    def __post_init__(self) -> None:
        for name in ("circuit_id", "subject"):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        if not isinstance(self.scope, PolicyScope):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("scope must be PolicyScope")
        if not isinstance(self.state, CircuitState):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("state must be CircuitState")
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.quarantine_authority_class,
            QuarantineAuthorityClass,
        ):
            raise ValueError(
                "quarantine_authority_class has wrong enum type"
            )
        if (
            self.receipt is not None
            and not isinstance(self.receipt, PolicyReceipt)  # pyright: ignore[reportUnnecessaryIsInstance]
        ):
            raise ValueError(
                "receipt must be PolicyReceipt or null"
            )

        _require_nonnegative(
            self.backoff_count,
            "backoff_count",
        )
        if self.cooldown_until is not None:
            _require_nonnegative(
                self.cooldown_until,
                "cooldown_until",
            )
        _require_positive(self.revision, "revision")
        _require_nonnegative(self.created_at, "created_at")
        _require_nonnegative(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at cannot precede created_at"
            )


@dataclass(frozen=True)
class AutomaticClearanceResult:
    """Result of applying one automatic clearance receipt."""

    circuit: HealthCircuitSnapshot
    clearance_applied: bool
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.circuit,
            HealthCircuitSnapshot,
        ):
            raise ValueError(
                "circuit must be HealthCircuitSnapshot"
            )
        if type(self.clearance_applied) is not bool:
            raise ValueError(
                "clearance_applied must be a boolean"
            )
        object.__setattr__(
            self,
            "reason",
            require_text(self.reason, "reason"),
        )


@dataclass(frozen=True)
class HealthProjectionSnapshot:
    """Current policy-owned availability/admission projection."""

    projection_id: str
    instance_id: str
    profile_id: str
    availability_state: AvailabilityState
    admission_state: AdmissionState
    readiness_observation_id: str | None
    operational_projection_id: str | None
    operational_projection_revision: int | None
    policy_id: str
    policy_revision: int
    cooldown_until: int | None
    evidence_refs: tuple[EvidenceRef, ...]
    revision: int
    created_at: int
    updated_at: int
    readiness_evaluation: ReadinessEvaluation | None = None
    sealed_runtime_revision: str | None = None
    adapter_declares_probe_safe: bool | None = None

    def __post_init__(self) -> None:
        for name in (
            "projection_id",
            "instance_id",
            "profile_id",
            "policy_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )

        for name in (
            "readiness_observation_id",
            "operational_projection_id",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    require_text(value, name),
                )

        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.availability_state,
            AvailabilityState,
        ):
            raise ValueError(
                "availability_state must be AvailabilityState"
            )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.admission_state,
            AdmissionState,
        ):
            raise ValueError(
                "admission_state must be AdmissionState"
            )

        if self.operational_projection_revision is not None:
            _require_positive(
                self.operational_projection_revision,
                "operational_projection_revision",
            )
        _require_positive(
            self.policy_revision,
            "policy_revision",
        )
        if self.cooldown_until is not None:
            _require_nonnegative(
                self.cooldown_until,
                "cooldown_until",
            )

        object.__setattr__(
            self,
            "evidence_refs",
            _normalize_refs(self.evidence_refs),
        )

        readiness_context = (
            self.readiness_evaluation,
            self.sealed_runtime_revision,
            self.adapter_declares_probe_safe,
        )
        if any(value is None for value in readiness_context):
            if not all(
                value is None for value in readiness_context
            ):
                raise ValueError(
                    "readiness evaluation context must be wholly "
                    "present or wholly absent"
                )
        else:
            if not isinstance(
                self.readiness_evaluation,
                ReadinessEvaluation,
            ):
                raise ValueError(
                    "readiness_evaluation must be "
                    "ReadinessEvaluation or null"
                )
            object.__setattr__(
                self,
                "sealed_runtime_revision",
                require_text(
                    self.sealed_runtime_revision,  # pyright: ignore[reportArgumentType]
                    "sealed_runtime_revision",
                ),
            )
            if type(self.adapter_declares_probe_safe) is not bool:
                raise ValueError(
                    "adapter_declares_probe_safe must be "
                    "a boolean or null"
                )

        _require_positive(self.revision, "revision")
        _require_nonnegative(self.created_at, "created_at")
        _require_nonnegative(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at cannot precede created_at"
            )


@dataclass(frozen=True)
class HealthProjectionRead:
    """Read-time computed view of a stored health projection.

    Evaluates freshness at an arbitrary ``evaluated_at`` timestamp,
    anchoring staleness on the referenced readiness observation's actual
    observation/validity time -- NOT on ``projection.updated_at``.
    Never mutates the stored projection.
    """

    projection: HealthProjectionSnapshot
    effective_availability_state: AvailabilityState
    effective_admission_state: AdmissionState
    stale_at_read: bool
    evaluated_at: int

    def __post_init__(self) -> None:
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.projection,
            HealthProjectionSnapshot,
        ):
            raise ValueError(
                "projection must be HealthProjectionSnapshot"
            )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.effective_availability_state,
            AvailabilityState,
        ):
            raise ValueError(
                "effective_availability_state must be "
                "AvailabilityState"
            )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.effective_admission_state,
            AdmissionState,
        ):
            raise ValueError(
                "effective_admission_state must be AdmissionState"
            )
        if type(self.stale_at_read) is not bool:
            raise ValueError(
                "stale_at_read must be a boolean"
            )
        _require_nonnegative(
            self.evaluated_at,
            "evaluated_at",
        )


@dataclass(frozen=True)
class RecoveryProbeGrant:
    """Single-use CAS-claimed authorization for one recovery probe."""

    grant_id: str
    circuit_id: str
    receipt: PolicyReceipt
    authorized_by: str
    authorized_at: int
    authorization_mode: RecoveryAuthorizationMode
    authorized_circuit_revision: int
    state: RecoveryGrantState
    expires_at: int
    consumed_at: int | None
    consumed_by_attempt_id: str | None
    revision: int

    def __post_init__(self) -> None:
        for name in (
            "grant_id",
            "circuit_id",
            "authorized_by",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        if not isinstance(self.receipt, PolicyReceipt):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("receipt must be PolicyReceipt")
        _require_nonnegative(
            self.authorized_at,
            "authorized_at",
        )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.authorization_mode,
            RecoveryAuthorizationMode,
        ):
            raise ValueError(
                "authorization_mode must be RecoveryAuthorizationMode"
            )
        _require_positive(
            self.authorized_circuit_revision,
            "authorized_circuit_revision",
        )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.state,
            RecoveryGrantState,
        ):
            raise ValueError("state must be RecoveryGrantState")
        _require_nonnegative(self.expires_at, "expires_at")
        if self.expires_at <= self.authorized_at:
            raise ValueError(
                "expires_at must be later than authorized_at"
            )

        if (self.consumed_at is None) != (
            self.consumed_by_attempt_id is None
        ):
            raise ValueError(
                "consumed_at and consumed_by_attempt_id "
                "must be supplied together"
            )
        if self.consumed_at is not None:
            _require_nonnegative(
                self.consumed_at,
                "consumed_at",
            )
            object.__setattr__(
                self,
                "consumed_by_attempt_id",
                require_text(
                    self.consumed_by_attempt_id,  # pyright: ignore[reportArgumentType]
                    "consumed_by_attempt_id",
                ),
            )
        if (
            self.state is RecoveryGrantState.GRANTED
            and self.consumed_at is not None
        ):
            raise ValueError(
                "a granted grant cannot record a claimant"
            )
        if (
            self.state
            in {
                RecoveryGrantState.CLAIMED,
                RecoveryGrantState.SUCCEEDED,
                RecoveryGrantState.FAILED,
            }
            and self.consumed_at is None
        ):
            raise ValueError(
                "a claimed or completed grant must record its claimant"
            )
        _require_positive(self.revision, "revision")


@dataclass(frozen=True)
class RecoveryProbeAuthorization:
    """Probe authorization without a direct healthy/open write."""

    projection: HealthProjectionSnapshot
    circuit: HealthCircuitSnapshot
    grant: RecoveryProbeGrant

    def __post_init__(self) -> None:
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.projection,
            HealthProjectionSnapshot,
        ):
            raise ValueError(
                "projection must be HealthProjectionSnapshot"
            )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.circuit,
            HealthCircuitSnapshot,
        ):
            raise ValueError(
                "circuit must be HealthCircuitSnapshot"
            )
        if not isinstance(self.grant, RecoveryProbeGrant):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError(
                "grant must be RecoveryProbeGrant"
            )


@dataclass(frozen=True)
class RecoveryProbeClaimResult:
    """Pure result of attempting to consume a probe grant."""

    grant: RecoveryProbeGrant
    attempt_id: str
    disposition: ProbeDisposition
    reason: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.grant, RecoveryProbeGrant):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError(
                "grant must be RecoveryProbeGrant"
            )
        object.__setattr__(
            self,
            "attempt_id",
            require_text(self.attempt_id, "attempt_id"),
        )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.disposition,
            ProbeDisposition,
        ):
            raise ValueError(
                "disposition must be ProbeDisposition"
            )
        if self.reason is not None:
            object.__setattr__(
                self,
                "reason",
                require_text(self.reason, "reason"),
            )


@dataclass(frozen=True)
class RecoveryProbeReceipt:
    """Measured probe result fenced to current circuit identity."""

    probe_receipt_id: str
    grant_id: str
    attempt_id: str
    reported_revision: int
    reported_receipt: PolicyReceipt
    result: ProbeResult
    observed_at: int
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        for name in (
            "probe_receipt_id",
            "grant_id",
            "attempt_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        _require_positive(
            self.reported_revision,
            "reported_revision",
        )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.reported_receipt,
            PolicyReceipt,
        ):
            raise ValueError(
                "reported_receipt must be PolicyReceipt"
            )
        if not isinstance(self.result, ProbeResult):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("result must be ProbeResult")
        _require_nonnegative(
            self.observed_at,
            "observed_at",
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _normalize_refs(self.evidence_refs),
        )


@dataclass(frozen=True)
class RecoveryProbeApplication:
    """Circuit result after identity-first probe processing."""

    circuit: HealthCircuitSnapshot
    reported_matches_current: bool
    transition: ProbeTransition

    def __post_init__(self) -> None:
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.circuit,
            HealthCircuitSnapshot,
        ):
            raise ValueError(
                "circuit must be HealthCircuitSnapshot"
            )
        if type(self.reported_matches_current) is not bool:
            raise ValueError(
                "reported_matches_current must be a boolean"
            )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.transition,
            ProbeTransition,
        ):
            raise ValueError(
                "transition must be ProbeTransition"
            )


@dataclass(frozen=True)
class CooldownEvaluation:
    """Pure evaluation of an authoritative cooldown boundary."""

    admission_state: AdmissionState
    retry_after: int | None
    cooldown_ended: bool

    def __post_init__(self) -> None:
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.admission_state,
            AdmissionState,
        ):
            raise ValueError(
                "admission_state must be AdmissionState"
            )
        if self.retry_after is not None:
            _require_nonnegative(
                self.retry_after,
                "retry_after",
            )
        if type(self.cooldown_ended) is not bool:
            raise ValueError(
                "cooldown_ended must be a boolean"
            )


@dataclass(frozen=True)
class HealthScopeBinding:
    """One explicit circuit-scope membership binding."""

    scope: PolicyScope
    subject: str
    members: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scope, PolicyScope):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("scope must be PolicyScope")
        object.__setattr__(
            self,
            "subject",
            require_text(self.subject, "subject"),
        )

        normalized_members = tuple(
            (
                require_text(
                    instance_id,
                    "member.instance_id",
                ),
                require_text(
                    profile_id,
                    "member.profile_id",
                ),
            )
            for instance_id, profile_id in self.members
        )
        if len(normalized_members) != len(
            set(normalized_members)
        ):
            raise ValueError(
                "health scope binding cannot contain "
                "duplicate members"
            )
        object.__setattr__(
            self,
            "members",
            tuple(sorted(normalized_members)),
        )


@dataclass(frozen=True)
class HealthScopeMembershipSnapshot:
    """Injected immutable circuit-scope membership facts."""

    configuration_revision: int
    configuration_digest: str
    configured_members: tuple[tuple[str, str], ...]
    bindings: tuple[HealthScopeBinding, ...]

    def __post_init__(self) -> None:
        _require_nonnegative(
            self.configuration_revision,
            "configuration_revision",
        )
        object.__setattr__(
            self,
            "configuration_digest",
            _require_sha256_hex(
                self.configuration_digest,
                "configuration_digest",
            ),
        )

        configured_members = tuple(
            (
                require_text(
                    instance_id,
                    "configured_member.instance_id",
                ),
                require_text(
                    profile_id,
                    "configured_member.profile_id",
                ),
            )
            for instance_id, profile_id in self.configured_members
        )
        if len(configured_members) != len(
            set(configured_members)
        ):
            raise ValueError(
                "configured_members cannot contain duplicates"
            )
        object.__setattr__(
            self,
            "configured_members",
            tuple(sorted(configured_members)),
        )

        bindings = tuple(self.bindings)
        if any(
            not isinstance(binding, HealthScopeBinding)  # pyright: ignore[reportUnnecessaryIsInstance]
            for binding in bindings
        ):
            raise ValueError(
                "bindings must contain only "
                "HealthScopeBinding values"
            )

        binding_keys = tuple(
            (binding.scope, binding.subject)
            for binding in bindings
        )
        if len(binding_keys) != len(set(binding_keys)):
            raise ValueError(
                "health scope membership snapshot contains "
                "duplicate scope/subject bindings"
            )

        configured_member_set = set(configured_members)
        for binding in bindings:
            if binding.scope is PolicyScope.PROFILE:
                raise ValueError(
                    "PROFILE membership is derived from "
                    "configured_members, not an explicit binding"
                )
            if any(
                member not in configured_member_set
                for member in binding.members
            ):
                raise ValueError(
                    "health scope binding contains a member "
                    "outside configured_members"
                )

        object.__setattr__(
            self,
            "bindings",
            tuple(
                sorted(
                    bindings,
                    key=lambda binding: (
                        binding.scope.value,
                        binding.subject,
                    ),
                )
            ),
        )


@dataclass(frozen=True)
class AdmissionSnapshotEntry:
    """Immutable freeze of one live health projection."""

    instance_id: str
    profile_id: str
    health_projection_id: str
    health_projection_revision: int
    availability_state: AvailabilityState
    admission_state: AdmissionState
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        for name in (
            "instance_id",
            "profile_id",
            "health_projection_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        _require_positive(
            self.health_projection_revision,
            "health_projection_revision",
        )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.availability_state,
            AvailabilityState,
        ):
            raise ValueError(
                "availability_state must be AvailabilityState"
            )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.admission_state,
            AdmissionState,
        ):
            raise ValueError(
                "admission_state must be AdmissionState"
            )
        object.__setattr__(
            self,
            "evidence_refs",
            _normalize_refs(self.evidence_refs),
        )


@dataclass(frozen=True)
class AdmissionSnapshot:
    """Immutable revisioned freeze consumed by one route decision."""

    snapshot_id: str
    revision: int
    digest: str
    configuration_revision: int
    configuration_digest: str
    policy_id: str
    policy_revision: int
    entries: tuple[AdmissionSnapshotEntry, ...]
    created_at: int

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "policy_id"):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        _require_positive(self.revision, "revision")
        object.__setattr__(
            self,
            "digest",
            _require_sha256_hex(self.digest, "digest"),
        )
        _require_nonnegative(
            self.configuration_revision,
            "configuration_revision",
        )
        object.__setattr__(
            self,
            "configuration_digest",
            _require_sha256_hex(self.configuration_digest, "configuration_digest"),
        )
        _require_positive(
            self.policy_revision,
            "policy_revision",
        )

        entries = tuple(self.entries)
        keys = [
            (entry.instance_id, entry.profile_id)
            for entry in entries
        ]
        if len(keys) != len(set(keys)):
            raise ValueError(
                "admission snapshot contains duplicate "
                "instance/profile entries"
            )
        object.__setattr__(self, "entries", entries)
        _require_nonnegative(self.created_at, "created_at")
