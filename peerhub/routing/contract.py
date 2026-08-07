"""Immutable routing request, candidate, audit, and decision contracts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from peerhub.core.evidence import EvidenceRef
from peerhub.core.protocol import (
    ErrorCode,
    JsonValue,
    canonical_json_bytes,
    freeze_json_mapping,
    require_text,
)
from peerhub.health.contract import AdmissionSnapshot
from peerhub.telemetry.contract import UsageEvidence


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


def _normalize_text_tuple(
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


class RouteEligibility(str, Enum):
    """Slice 4's deliberately boolean eligibility vocabulary."""

    ELIGIBLE = "ELIGIBLE"
    EXCLUDED = "EXCLUDED"


@dataclass(frozen=True)
class ConfigurationSnapshot:
    """Injected immutable configuration identity."""

    revision: int
    digest: str

    def __post_init__(self) -> None:
        _require_nonnegative(self.revision, "revision")
        object.__setattr__(
            self,
            "digest",
            _require_sha256_hex(self.digest, "digest"),
        )


@dataclass(frozen=True)
class RouteCandidateInput:
    """Already-observed facts supplied to pure candidate evaluation.

    A missing usage provider must be represented by ABSENT or
    UNAVAILABLE UsageEvidence, never by None or a synthetic zero.
    """

    candidate_id: str
    instance_id: str
    representative_profile_id: str
    eligible: bool
    exclusion_reason: str | None
    usage_evidence: UsageEvidence
    in_flight_reservations: int
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "instance_id",
            "representative_profile_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )

        if type(self.eligible) is not bool:
            raise ValueError("eligible must be a boolean")

        if self.eligible:
            if self.exclusion_reason is not None:
                raise ValueError(
                    "eligible candidate cannot have "
                    "an exclusion reason"
                )
        elif self.exclusion_reason is None:
            raise ValueError(
                "excluded candidate requires an exclusion reason"
            )
        else:
            object.__setattr__(
                self,
                "exclusion_reason",
                require_text(
                    self.exclusion_reason,
                    "exclusion_reason",
                ),
            )

        _require_nonnegative(
            self.in_flight_reservations,
            "in_flight_reservations",
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _normalize_refs(self.evidence_refs),
        )


@dataclass(frozen=True)
class RouteRequest:
    """Complete immutable input to one routing decision."""

    client_request_id: str
    configuration: ConfigurationSnapshot
    admission_snapshot: AdmissionSnapshot
    requested_capabilities: tuple[str, ...]
    profile_constraints: Mapping[str, JsonValue]
    required_readiness_binding: str | None
    candidates: tuple[RouteCandidateInput, ...]
    routing_policy_id: str
    routing_policy_revision: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "client_request_id",
            require_text(
                self.client_request_id,
                "client_request_id",
            ),
        )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.configuration,
            ConfigurationSnapshot,
        ):
            raise ValueError(
                "configuration must be ConfigurationSnapshot"
            )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.admission_snapshot,
            AdmissionSnapshot,
        ):
            raise ValueError(
                "admission_snapshot must be AdmissionSnapshot"
            )
        if (
            self.admission_snapshot.configuration_revision
            != self.configuration.revision
        ):
            raise ValueError(
                "admission_snapshot configuration revision "
                "must match configuration revision"
            )
        if (
            self.admission_snapshot.configuration_digest
            != self.configuration.digest
        ):
            raise ValueError(
                "admission_snapshot configuration digest "
                "must match configuration digest"
            )

        object.__setattr__(
            self,
            "requested_capabilities",
            _normalize_text_tuple(
                self.requested_capabilities,
                "requested_capability",
            ),
        )
        object.__setattr__(
            self,
            "profile_constraints",
            freeze_json_mapping(self.profile_constraints),
        )

        if self.required_readiness_binding is not None:
            object.__setattr__(
                self,
                "required_readiness_binding",
                require_text(
                    self.required_readiness_binding,
                    "required_readiness_binding",
                ),
            )

        candidates = tuple(self.candidates)
        candidate_ids = [
            candidate.candidate_id
            for candidate in candidates
        ]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(
                "route request contains duplicate candidates"
            )
        object.__setattr__(self, "candidates", candidates)

        object.__setattr__(
            self,
            "routing_policy_id",
            require_text(
                self.routing_policy_id,
                "routing_policy_id",
            ),
        )
        _require_positive(
            self.routing_policy_revision,
            "routing_policy_revision",
        )


@dataclass(frozen=True)
class RouteCandidateDecision:
    """Audited eligibility and equal-weight decision for one candidate."""

    candidate_id: str
    instance_id: str
    representative_profile_id: str
    eligibility: RouteEligibility
    effective_weight: int
    exclusion_reason: str | None
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "instance_id",
            "representative_profile_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.eligibility,
            RouteEligibility,
        ):
            raise ValueError(
                "eligibility must be RouteEligibility"
            )
        if self.effective_weight not in {0, 1}:
            raise ValueError(
                "Slice 4 effective_weight must be zero or one"
            )

        if self.eligibility is RouteEligibility.ELIGIBLE:
            if self.effective_weight != 1:
                raise ValueError(
                    "eligible candidate must have unit weight"
                )
            if self.exclusion_reason is not None:
                raise ValueError(
                    "eligible candidate cannot have "
                    "an exclusion reason"
                )
        else:
            if self.effective_weight != 0:
                raise ValueError(
                    "excluded candidate must have zero weight"
                )
            if self.exclusion_reason is None:
                raise ValueError(
                    "excluded candidate requires a reason"
                )
            object.__setattr__(
                self,
                "exclusion_reason",
                require_text(
                    self.exclusion_reason,
                    "exclusion_reason",
                ),
            )

        object.__setattr__(
            self,
            "evidence_refs",
            _normalize_refs(self.evidence_refs),
        )


@dataclass(frozen=True)
class RouteSelection:
    """Deterministic equal-weight selection audit."""

    audit_seed: str
    selection_index: int
    ordered_candidates: tuple[str, ...]
    selected_candidate: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "audit_seed",
            _require_sha256_hex(
                self.audit_seed,
                "audit_seed",
            ),
        )
        _require_nonnegative(
            self.selection_index,
            "selection_index",
        )
        object.__setattr__(
            self,
            "ordered_candidates",
            _normalize_text_tuple(
                self.ordered_candidates,
                "ordered_candidate",
            ),
        )
        if not self.ordered_candidates:
            raise ValueError(
                "ordered_candidates must not be empty"
            )
        if self.selection_index >= len(
            self.ordered_candidates
        ):
            raise ValueError(
                "selection_index is outside ordered_candidates"
            )
        object.__setattr__(
            self,
            "selected_candidate",
            require_text(
                self.selected_candidate,
                "selected_candidate",
            ),
        )
        if (
            self.ordered_candidates[self.selection_index]
            != self.selected_candidate
        ):
            raise ValueError(
                "selected_candidate does not match selection_index"
            )


@dataclass(frozen=True)
class RouteDecision:
    """Durable audit of one routing decision."""

    decision_id: str
    client_request_id: str
    configuration: ConfigurationSnapshot
    admission_snapshot_id: str
    admission_snapshot_revision: int
    admission_snapshot_digest: str
    routing_policy_id: str
    routing_policy_revision: int
    candidates: tuple[RouteCandidateDecision, ...]
    audit_seed: str | None
    selection_index: int | None
    selected_candidate_id: str | None
    created_at: int

    def __post_init__(self) -> None:
        for name in (
            "decision_id",
            "client_request_id",
            "admission_snapshot_id",
            "routing_policy_id",
        ):
            object.__setattr__(
                self,
                name,
                require_text(getattr(self, name), name),
            )

        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.configuration,
            ConfigurationSnapshot,
        ):
            raise ValueError(
                "configuration must be ConfigurationSnapshot"
            )
        _require_positive(
            self.admission_snapshot_revision,
            "admission_snapshot_revision",
        )
        object.__setattr__(
            self,
            "admission_snapshot_digest",
            _require_sha256_hex(
                self.admission_snapshot_digest,
                "admission_snapshot_digest",
            ),
        )
        _require_positive(
            self.routing_policy_revision,
            "routing_policy_revision",
        )

        candidates = tuple(self.candidates)
        ids = [
            candidate.candidate_id
            for candidate in candidates
        ]
        if len(ids) != len(set(ids)):
            raise ValueError(
                "route decision contains duplicate candidates"
            )
        object.__setattr__(self, "candidates", candidates)

        selection_values = (
            self.audit_seed,
            self.selection_index,
            self.selected_candidate_id,
        )
        if all(value is None for value in selection_values):
            pass
        elif any(value is None for value in selection_values):
            raise ValueError(
                "selection audit fields must be supplied together"
            )
        else:
            object.__setattr__(
                self,
                "audit_seed",
                _require_sha256_hex(
                    self.audit_seed,  # pyright: ignore[reportArgumentType]
                    "audit_seed",
                ),
            )
            _require_nonnegative(
                self.selection_index,  # pyright: ignore[reportArgumentType]
                "selection_index",
            )
            object.__setattr__(
                self,
                "selected_candidate_id",
                require_text(
                    self.selected_candidate_id,  # pyright: ignore[reportArgumentType]
                    "selected_candidate_id",
                ),
            )
            selected = [
                candidate
                for candidate in candidates
                if (
                    candidate.candidate_id
                    == self.selected_candidate_id
                )
            ]
            if (
                len(selected) != 1
                or selected[0].eligibility
                is not RouteEligibility.ELIGIBLE
            ):
                raise ValueError(
                    "selected candidate must be exactly one "
                    "eligible audited candidate"
                )

        _require_nonnegative(self.created_at, "created_at")


def canonical_route_decision_digest(
    decision: RouteDecision,
) -> str:
    """Hash the complete immutable route-decision audit."""

    if not isinstance(decision, RouteDecision):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("decision must be RouteDecision")

    ordered_candidates = tuple(
        sorted(
            decision.candidates,
            key=lambda candidate: candidate.candidate_id,
        )
    )
    projection = {
        "decision_id": decision.decision_id,
        "client_request_id": decision.client_request_id,
        "configuration": {
            "revision": decision.configuration.revision,
            "digest": decision.configuration.digest,
        },
        "admission_snapshot": {
            "snapshot_id": decision.admission_snapshot_id,
            "revision": decision.admission_snapshot_revision,
            "digest": decision.admission_snapshot_digest,
        },
        "routing_policy": {
            "policy_id": decision.routing_policy_id,
            "revision": decision.routing_policy_revision,
        },
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "instance_id": candidate.instance_id,
                "representative_profile_id": (
                    candidate.representative_profile_id
                ),
                "eligibility": candidate.eligibility.value,
                "effective_weight": candidate.effective_weight,
                "exclusion_reason": candidate.exclusion_reason,
                "evidence_refs": [
                    str(reference)
                    for reference in candidate.evidence_refs
                ],
            }
            for candidate in ordered_candidates
        ],
        "selection": {
            "audit_seed": decision.audit_seed,
            "selection_index": decision.selection_index,
            "selected_candidate_id": (
                decision.selected_candidate_id
            ),
        },
        "created_at": decision.created_at,
    }
    return hashlib.sha256(
        canonical_json_bytes(projection)
    ).hexdigest()


@dataclass(frozen=True)
class RoutePlanResult:
    """Typed result of producing one durable route decision."""

    decision: RouteDecision
    error_code: ErrorCode | None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, RouteDecision):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError(
                "decision must be RouteDecision"
            )

        if self.error_code is None:
            if self.decision.selected_candidate_id is None:
                raise ValueError(
                    "successful route plan requires "
                    "a selected candidate"
                )
            return

        if self.error_code is not ErrorCode.ROUTE_EXHAUSTED:
            raise ValueError(
                "route plan may return only ROUTE_EXHAUSTED"
            )
        if self.decision.selected_candidate_id is not None:
            raise ValueError(
                "exhausted route plan cannot select a candidate"
            )
        if any(
            candidate.eligibility is RouteEligibility.ELIGIBLE
            for candidate in self.decision.candidates
        ):
            raise ValueError(
                "ROUTE_EXHAUSTED requires zero eligible candidates"
            )


@dataclass(frozen=True)
class RouteDispatchValidation:
    """Configuration-only RT-06 pre-dispatch result."""

    dispatch_permitted: bool
    error_code: ErrorCode | None
    dispatch_count: int
    replanning_input_revision: int | None

    def __post_init__(self) -> None:
        if type(self.dispatch_permitted) is not bool:
            raise ValueError(
                "dispatch_permitted must be a boolean"
            )
        _require_nonnegative(
            self.dispatch_count,
            "dispatch_count",
        )

        if self.error_code is not None and (
            self.error_code is not ErrorCode.CONFIGURATION_STALE
        ):
            raise ValueError(
                "RT-06 may return only CONFIGURATION_STALE"
            )

        if self.replanning_input_revision is not None:
            _require_nonnegative(
                self.replanning_input_revision,
                "replanning_input_revision",
            )

        if self.dispatch_permitted:
            if self.error_code is not None:
                raise ValueError(
                    "permitted dispatch cannot carry an error"
                )
            if self.dispatch_count != 1:
                raise ValueError(
                    "permitted dispatch_count must equal one"
                )
            if self.replanning_input_revision is not None:
                raise ValueError(
                    "permitted dispatch cannot request replanning"
                )
        else:
            if self.error_code is not ErrorCode.CONFIGURATION_STALE:
                raise ValueError(
                    "denied RT-06 dispatch must be "
                    "CONFIGURATION_STALE"
                )
            if self.dispatch_count != 0:
                raise ValueError(
                    "denied dispatch_count must equal zero"
                )
            if self.replanning_input_revision is None:
                raise ValueError(
                    "stale decision requires replanning revision"
                )


@dataclass(frozen=True)
class RoutePreDispatchResult:
    """Pre-dispatch validation and optional replacement plan."""

    validation: RouteDispatchValidation
    replanned_route: RoutePlanResult | None

    def __post_init__(self) -> None:
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.validation,
            RouteDispatchValidation,
        ):
            raise ValueError(
                "validation must be RouteDispatchValidation"
            )

        if self.validation.dispatch_permitted:
            if self.replanned_route is not None:
                raise ValueError(
                    "permitted dispatch cannot carry a replan"
                )
        elif self.replanned_route is None:
            raise ValueError(
                "stale dispatch validation requires a replan"
            )
        elif not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.replanned_route,
            RoutePlanResult,
        ):
            raise ValueError(
                "replanned_route must be RoutePlanResult"
            )
