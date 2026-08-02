"""Pure Slice 4 routing evaluation and selection reducers.

This slice supports only boolean eligibility, fixed unit weights, and
deterministic equal-weight selection. Cost, latency, terminal-tier
weighting, admission-snapshot drift detection, and stale-decision repair
are deliberately outside this module's current scope.
"""

from __future__ import annotations

import hashlib

from peerhub.core.protocol import (
    ErrorCode,
    canonical_json_bytes,
    require_text,
)
from peerhub.routing.contract import (
    ConfigurationSnapshot,
    RouteCandidateDecision,
    RouteCandidateInput,
    RouteDecision,
    RouteDispatchValidation,
    RouteEligibility,
    RoutePlanResult,
    RouteRequest,
    RouteSelection,
)


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


def evaluate_route_candidates(
    candidates: tuple[RouteCandidateInput, ...],
) -> tuple[RouteCandidateDecision, ...]:
    """Evaluate candidates using Slice 4's boolean weight policy.

    RT-04's frozen audit order is lexicographic by candidate ID. This is
    not a stable eligible/excluded partition.
    """

    candidate_ids = tuple(
        candidate.candidate_id
        for candidate in candidates
    )
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError(
            "candidates cannot contain duplicate candidate IDs"
        )

    ordered = sorted(
        candidates,
        key=lambda candidate: candidate.candidate_id,
    )

    return tuple(
        RouteCandidateDecision(
            candidate_id=candidate.candidate_id,
            instance_id=candidate.instance_id,
            representative_profile_id=(
                candidate.representative_profile_id
            ),
            eligibility=(
                RouteEligibility.ELIGIBLE
                if candidate.eligible
                else RouteEligibility.EXCLUDED
            ),
            effective_weight=(
                1 if candidate.eligible else 0
            ),
            exclusion_reason=(
                None
                if candidate.eligible
                else candidate.exclusion_reason
            ),
            evidence_refs=candidate.evidence_refs,
        )
        for candidate in ordered
    )


def select_equal_weight_candidate(
    *,
    client_request_id: str,
    snapshot_digest: str,
    candidate_ids: tuple[str, ...],
) -> RouteSelection:
    """Select one candidate using the frozen RT-05 seed projection."""

    normalized_request_id = require_text(
        client_request_id,
        "client_request_id",
    )
    normalized_snapshot_digest = _require_sha256_hex(
        snapshot_digest,
        "snapshot_digest",
    )
    ordered_candidates = tuple(
        sorted(
            require_text(
                candidate_id,
                "candidate_id",
            )
            for candidate_id in candidate_ids
        )
    )

    if not ordered_candidates:
        raise ValueError(
            "candidate_ids must contain at least one candidate"
        )
    if len(ordered_candidates) != len(
        set(ordered_candidates)
    ):
        raise ValueError(
            "candidate_ids cannot contain duplicates"
        )

    seed_projection = {
        # The Phase 0 RT-05 canonical projection names this field
        # "request_id"; its value is the pre-admission
        # client_request_id.
        "request_id": normalized_request_id,
        "snapshot_digest": normalized_snapshot_digest,
    }
    audit_seed_bytes = hashlib.sha256(
        canonical_json_bytes(seed_projection)
    ).digest()
    selection_index = (
        int.from_bytes(
            audit_seed_bytes[:8],
            byteorder="big",
            signed=False,
        )
        % len(ordered_candidates)
    )

    return RouteSelection(
        audit_seed=audit_seed_bytes.hex(),
        selection_index=selection_index,
        ordered_candidates=ordered_candidates,
        selected_candidate=(
            ordered_candidates[selection_index]
        ),
    )


def select_route(
    request: RouteRequest,
    *,
    decision_id: str,
    created_at: int,
) -> RoutePlanResult:
    """Evaluate, select, and construct one immutable route audit."""

    candidates = evaluate_route_candidates(
        request.candidates
    )
    eligible_candidate_ids = tuple(
        candidate.candidate_id
        for candidate in candidates
        if candidate.eligibility is RouteEligibility.ELIGIBLE
    )

    selection = (
        select_equal_weight_candidate(
            client_request_id=request.client_request_id,
            snapshot_digest=request.admission_snapshot.digest,
            candidate_ids=eligible_candidate_ids,
        )
        if eligible_candidate_ids
        else None
    )

    decision = RouteDecision(
        decision_id=decision_id,
        client_request_id=request.client_request_id,
        configuration=request.configuration,
        admission_snapshot_id=(
            request.admission_snapshot.snapshot_id
        ),
        admission_snapshot_revision=(
            request.admission_snapshot.revision
        ),
        admission_snapshot_digest=(
            request.admission_snapshot.digest
        ),
        routing_policy_id=request.routing_policy_id,
        routing_policy_revision=(
            request.routing_policy_revision
        ),
        candidates=candidates,
        audit_seed=(
            None
            if selection is None
            else selection.audit_seed
        ),
        selection_index=(
            None
            if selection is None
            else selection.selection_index
        ),
        selected_candidate_id=(
            None
            if selection is None
            else selection.selected_candidate
        ),
        created_at=created_at,
    )

    return RoutePlanResult(
        decision=decision,
        error_code=(
            ErrorCode.ROUTE_EXHAUSTED
            if selection is None
            else None
        ),
    )


def validate_route_for_dispatch(
    decision: RouteDecision,
    *,
    current_configuration: ConfigurationSnapshot,
) -> RouteDispatchValidation:
    """Apply RT-06's configuration-revision-only drift check."""

    if decision.selected_candidate_id is None:
        raise ValueError(
            "route decision must select a candidate "
            "before dispatch validation"
        )

    if (
        decision.configuration.revision
        != current_configuration.revision
    ):
        return RouteDispatchValidation(
            dispatch_permitted=False,
            error_code=ErrorCode.CONFIGURATION_STALE,
            dispatch_count=0,
            replanning_input_revision=(
                current_configuration.revision
            ),
        )

    return RouteDispatchValidation(
        dispatch_permitted=True,
        error_code=None,
        dispatch_count=1,
        replanning_input_revision=None,
    )


def replan_route(
    stale_decision: RouteDecision,
    request: RouteRequest,
    *,
    decision_id: str,
    created_at: int,
) -> RoutePlanResult:
    """Select anew after drift without modifying the stale decision."""

    normalized_decision_id = require_text(
        decision_id,
        "decision_id",
    )
    if normalized_decision_id == stale_decision.decision_id:
        raise ValueError(
            "replanning requires a new decision_id"
        )
    if (
        request.client_request_id
        != stale_decision.client_request_id
    ):
        raise ValueError(
            "replanning request must preserve "
            "client_request_id"
        )

    validation = validate_route_for_dispatch(
        stale_decision,
        current_configuration=request.configuration,
    )
    if validation.dispatch_permitted:
        raise ValueError(
            "replan_route requires a configuration-stale "
            "decision"
        )

    return select_route(
        request,
        decision_id=normalized_decision_id,
        created_at=created_at,
    )
