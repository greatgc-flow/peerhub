"""Pure capability matching and default-proposer reducers.

Every input is an immutable fact supplied by the application coordinator.
This module never opens persistence, reads configuration files, or calls a
sibling service.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from peerhub.core.evidence import EvidenceRef, EvidenceState
from peerhub.core.protocol import JsonValue, require_text
from peerhub.health.contract import AdmissionState, AvailabilityState


class ScoreComponentName(str, Enum):
    CAPABILITY_MATCH = "CAPABILITY_MATCH"
    HEALTH_GRADE = "HEALTH_GRADE"
    CONTINUITY = "CONTINUITY"
    QUOTA_MARGIN = "QUOTA_MARGIN"
    RECENT_USE = "RECENT_USE"
    COST = "COST"
    CONSOLE_FIT = "CONSOLE_FIT"
    COLD_START = "COLD_START"
    EFFORT_QUALITY = "EFFORT_QUALITY"
    MODEL_TIER = "MODEL_TIER"


class ScoreComponentState(str, Enum):
    APPLIED = "APPLIED"
    ABSENT = "ABSENT"
    HARD_EXCLUDED = "HARD_EXCLUDED"


class CapabilityCandidateStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    HARD_EXCLUDED = "HARD_EXCLUDED"


@dataclass(frozen=True, slots=True)
class ConfiguredCapability:
    name: str
    sources: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_text(self.name, "name"))
        normalized_sources = tuple(
            require_text(source, "source") for source in self.sources
        )
        if not normalized_sources:
            raise ValueError("configured capability needs at least one source")
        if len(normalized_sources) != len(set(normalized_sources)):
            raise ValueError("configured capability sources must be unique")
        object.__setattr__(self, "sources", normalized_sources)


@dataclass(frozen=True, slots=True)
class CapabilityScoreComponent:
    name: ScoreComponentName
    state: ScoreComponentState
    raw_value: JsonValue | None
    points: int | None
    reason: str | None
    provenance_indexes: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.state is ScoreComponentState.ABSENT and self.points is not None:
            raise ValueError("ABSENT score components cannot carry points")
        if self.state is ScoreComponentState.APPLIED and self.points is None:
            raise ValueError("APPLIED score components require points")
        if self.state is ScoreComponentState.HARD_EXCLUDED and self.points is not None:
            raise ValueError("HARD_EXCLUDED score components cannot carry points")
        if any(index < 0 for index in self.provenance_indexes):
            raise ValueError("provenance indexes must be nonnegative")


@dataclass(frozen=True, slots=True)
class CapabilityEvidenceProvenance:
    fact: str
    evidence_state: EvidenceState
    source_kind: str
    source_id: str
    source_revision: int | None
    source_digest: str | None
    evidence_refs: tuple[EvidenceRef, ...]
    observed_at: int | None


@dataclass(frozen=True, slots=True)
class QuotaRankingFact:
    projection_id: str
    instance_id: str
    profile_id: str
    quota_pool_scope: str
    remaining_fraction: float
    resets_at: int
    revision: int
    updated_at: int
    evidence_state: EvidenceState
    provenance: CapabilityEvidenceProvenance


@dataclass(frozen=True, slots=True)
class DefaultProposerPolicy:
    mode: Literal["FIXED", "ROTATING"]
    fixed_node_id: str | None
    rotation_order: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.mode not in {"FIXED", "ROTATING"}:
            raise ValueError("default proposer mode must be FIXED or ROTATING")
        if self.mode == "FIXED":
            if self.fixed_node_id is None:
                raise ValueError("FIXED default proposer requires fixed_node_id")
            require_text(self.fixed_node_id, "fixed_node_id")
        elif self.fixed_node_id is not None:
            raise ValueError("ROTATING default proposer cannot have fixed_node_id")
        if self.mode == "ROTATING" and not self.rotation_order:
            raise ValueError("ROTATING default proposer requires rotation_order")
        if len(self.rotation_order) != len(set(self.rotation_order)):
            raise ValueError("rotation_order must contain unique node IDs")
        for node_id in self.rotation_order:
            require_text(node_id, "rotation_order node_id")


@dataclass(frozen=True, slots=True)
class FallbackResolution:
    node_id: str | None
    basis: Literal[
        "FIXED_DEFAULT", "ROTATING_DEFAULT", "NO_ELIGIBLE_DEFAULT"
    ]
    considered_node_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapabilityMatchingPolicy:
    formula_id: str
    policy_id: str
    policy_revision: int
    target_id: str
    target_revision: int
    target_digest: str
    empty_capability_points: int
    exact_capability_points: int
    substring_capability_points: int
    health_points: tuple[tuple[AvailabilityState, int], ...]
    continuity_bonus: int
    quota_bands: tuple[tuple[float, int], ...]
    recent_history_window: int
    recent_use_penalty: int
    default_proposer: DefaultProposerPolicy
    evaluated_at: int = 0

    def __post_init__(self) -> None:
        if self.formula_id != "native-v1":
            raise ValueError("unsupported capability matching formula")
        if self.policy_revision < 1 or self.target_revision < 1:
            raise ValueError("policy revisions must be positive")
        if self.recent_history_window < 1:
            raise ValueError("recent_history_window must be positive")
        if self.evaluated_at < 0:
            raise ValueError("evaluated_at must be nonnegative")


@dataclass(frozen=True, slots=True)
class CapabilityCandidateFacts:
    node_id: str
    peer_kind: str
    profile_id: str
    peer_node_target_id: str
    peer_node_revision: int
    enabled: bool
    aliases: tuple[str, ...]
    capabilities: tuple[ConfiguredCapability, ...]
    capability_config_target_id: str
    capability_config_revision: int
    availability_status: AvailabilityState | None
    admission_status: AdmissionState | None
    profile_gate_backed_off: bool | None
    health_provenance: CapabilityEvidenceProvenance
    quota_projections: tuple[QuotaRankingFact, ...]
    is_current_leader: bool
    recent_leader_node_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapabilityMatch:
    node_id: str
    peer_kind: str
    profile_id: str
    candidate_status: CapabilityCandidateStatus
    exclusion_reason: str | None
    availability_status: AvailabilityState | None
    admission_status: AdmissionState | None
    cost_tier: str | None
    cost_tier_state: ScoreComponentState
    model_tier: str | None
    model_tier_state: ScoreComponentState
    ordered_capabilities: tuple[str, ...]
    ranking_score: int | None
    components: tuple[CapabilityScoreComponent, ...]
    provenance: tuple[CapabilityEvidenceProvenance, ...]


@dataclass(frozen=True, slots=True)
class CapabilityRankingResult:
    formula_id: str
    policy_id: str
    policy_revision: int
    needs: str
    requested_effort: str
    ordered_matches: tuple[CapabilityMatch, ...]
    excluded_candidates: tuple[CapabilityMatch, ...]
    fallback: FallbackResolution | None
    evaluated_at: int


def _absent(
    name: ScoreComponentName,
    reason: str,
) -> CapabilityScoreComponent:
    return CapabilityScoreComponent(
        name=name,
        state=ScoreComponentState.ABSENT,
        raw_value=None,
        points=None,
        reason=reason,
        provenance_indexes=(),
    )


def _applied(
    name: ScoreComponentName,
    raw_value: JsonValue,
    points: int,
    *provenance_indexes: int,
    reason: str | None = None,
) -> CapabilityScoreComponent:
    return CapabilityScoreComponent(
        name=name,
        state=ScoreComponentState.APPLIED,
        raw_value=raw_value,
        points=points,
        reason=reason,
        provenance_indexes=tuple(provenance_indexes),
    )


def _excluded(
    name: ScoreComponentName,
    raw_value: JsonValue | None,
    reason: str,
    *provenance_indexes: int,
) -> CapabilityScoreComponent:
    return CapabilityScoreComponent(
        name=name,
        state=ScoreComponentState.HARD_EXCLUDED,
        raw_value=raw_value,
        points=None,
        reason=reason,
        provenance_indexes=tuple(provenance_indexes),
    )


def _target_provenance(
    *,
    fact: str,
    source_id: str,
    revision: int,
    digest: str | None = None,
) -> CapabilityEvidenceProvenance:
    return CapabilityEvidenceProvenance(
        fact=fact,
        evidence_state=EvidenceState.MEASURED,
        source_kind="governance-target",
        source_id=source_id,
        source_revision=revision,
        source_digest=digest,
        evidence_refs=(),
        observed_at=None,
    )


def _capability_component(
    candidate: CapabilityCandidateFacts,
    needs: str,
    policy: CapabilityMatchingPolicy,
) -> CapabilityScoreComponent:
    if not candidate.enabled:
        return _excluded(
            ScoreComponentName.CAPABILITY_MATCH,
            False,
            "capability configuration disabled",
            1,
        )
    folded_needs = needs.casefold()
    if not folded_needs:
        return _applied(
            ScoreComponentName.CAPABILITY_MATCH,
            "",
            policy.empty_capability_points,
            0,
            1,
            2,
            reason="empty need accepts every configured candidate",
        )
    if folded_needs == candidate.node_id.casefold() or any(
        folded_needs == alias.casefold() for alias in candidate.aliases
    ):
        return _applied(
            ScoreComponentName.CAPABILITY_MATCH,
            needs,
            policy.exact_capability_points,
            0,
            1,
            2,
            reason="exact node or alias match",
        )
    capability_names = tuple(capability.name for capability in candidate.capabilities)
    if any(folded_needs == name.casefold() for name in capability_names):
        return _applied(
            ScoreComponentName.CAPABILITY_MATCH,
            needs,
            policy.exact_capability_points,
            1,
            2,
            reason="exact capability match",
        )
    if any(
        folded_needs in name.casefold() or name.casefold() in folded_needs
        for name in capability_names
    ):
        return _applied(
            ScoreComponentName.CAPABILITY_MATCH,
            needs,
            policy.substring_capability_points,
            1,
            2,
            reason="symmetric capability substring match",
        )
    return _excluded(
        ScoreComponentName.CAPABILITY_MATCH,
        needs,
        "capability mismatch",
        0,
        1,
        2,
    )


def _health_component(
    candidate: CapabilityCandidateFacts,
    policy: CapabilityMatchingPolicy,
    health_index: int,
) -> CapabilityScoreComponent:
    availability = candidate.availability_status
    admission = candidate.admission_status
    raw: JsonValue = {
        "availability": None if availability is None else availability.value,
        "admission": None if admission is None else admission.value,
        "profile_gate_backed_off": candidate.profile_gate_backed_off,
    }
    if availability is AvailabilityState.UNAVAILABLE:
        return _excluded(
            ScoreComponentName.HEALTH_GRADE,
            raw,
            "availability is UNAVAILABLE",
            health_index,
        )
    if admission is not None and admission is not AdmissionState.OPEN:
        return _excluded(
            ScoreComponentName.HEALTH_GRADE,
            raw,
            f"admission is {admission.value}, not OPEN",
            health_index,
        )
    if candidate.profile_gate_backed_off is True:
        return _excluded(
            ScoreComponentName.HEALTH_GRADE,
            raw,
            "profile gate is backed off",
            health_index,
        )
    if availability is None:
        return CapabilityScoreComponent(
            name=ScoreComponentName.HEALTH_GRADE,
            state=ScoreComponentState.ABSENT,
            raw_value=None,
            points=None,
            reason="no health projection",
            provenance_indexes=(health_index,),
        )
    health_points = dict(policy.health_points)
    return _applied(
        ScoreComponentName.HEALTH_GRADE,
        raw,
        health_points[availability],
        health_index,
        reason=f"observed {availability.value} health grade",
    )


def _quota_component(
    candidate: CapabilityCandidateFacts,
    policy: CapabilityMatchingPolicy,
    first_quota_index: int,
) -> CapabilityScoreComponent:
    indexes = tuple(
        range(first_quota_index, first_quota_index + len(candidate.quota_projections))
    )
    active = tuple(
        projection
        for projection in candidate.quota_projections
        if projection.evidence_state is EvidenceState.MEASURED
    )
    if not active:
        stale_only = bool(candidate.quota_projections) and all(
            projection.evidence_state is EvidenceState.STALE
            for projection in candidate.quota_projections
        )
        return CapabilityScoreComponent(
            name=ScoreComponentName.QUOTA_MARGIN,
            state=ScoreComponentState.ABSENT,
            raw_value=None,
            points=None,
            reason=(
                "only expired quota projections"
                if stale_only
                else "no quota projections"
            ),
            provenance_indexes=indexes,
        )
    remaining = min(projection.remaining_fraction for projection in active)
    if remaining <= 0.0:
        return _excluded(
            ScoreComponentName.QUOTA_MARGIN,
            remaining,
            "current-window quota margin is exhausted",
            *indexes,
        )
    points = next(
        points
        for threshold, points in policy.quota_bands
        if remaining >= threshold
    )
    return _applied(
        ScoreComponentName.QUOTA_MARGIN,
        remaining,
        points,
        *indexes,
        reason="minimum current-window remaining quota",
    )


def score_capability_candidate(
    candidate: CapabilityCandidateFacts,
    *,
    needs: str,
    requested_effort: str,
    policy: CapabilityMatchingPolicy,
) -> CapabilityMatch:
    """Apply the exact native-v1 scoring formula to one candidate."""

    normalized_needs = needs.strip()
    normalized_effort = requested_effort.strip().lower()
    if normalized_effort == "medium":
        normalized_effort = "mid"
    if normalized_effort not in {"low", "mid", "high"}:
        raise ValueError("requested_effort must be low, mid, medium, or high")

    provenance = [
        _target_provenance(
            fact="peer-node",
            source_id=candidate.peer_node_target_id,
            revision=candidate.peer_node_revision,
        ),
        _target_provenance(
            fact="capability-configuration",
            source_id=candidate.capability_config_target_id,
            revision=candidate.capability_config_revision,
        ),
        _target_provenance(
            fact="capability-matching-policy",
            source_id=policy.target_id,
            revision=policy.target_revision,
            digest=policy.target_digest,
        ),
        candidate.health_provenance,
    ]
    provenance.extend(projection.provenance for projection in candidate.quota_projections)

    capability = _capability_component(candidate, normalized_needs, policy)
    health = _health_component(candidate, policy, 3)
    continuity_points = (
        policy.continuity_bonus
        if candidate.is_current_leader
        and candidate.availability_status
        not in {AvailabilityState.STALE, AvailabilityState.UNAVAILABLE}
        else 0
    )
    continuity = _applied(
        ScoreComponentName.CONTINUITY,
        candidate.is_current_leader,
        continuity_points,
        reason="current leadership snapshot",
    )
    quota = _quota_component(candidate, policy, 4)
    history = candidate.recent_leader_node_ids[-policy.recent_history_window :]
    recently_repeated = (
        len(history) == policy.recent_history_window
        and all(node_id == candidate.node_id for node_id in history)
    )
    recent_use = _applied(
        ScoreComponentName.RECENT_USE,
        history,
        policy.recent_use_penalty if recently_repeated else 0,
        reason="persisted coordinator history window",
    )
    components = (
        capability,
        health,
        continuity,
        quota,
        recent_use,
        _absent(ScoreComponentName.COST, "no authoritative cost classification"),
        _absent(ScoreComponentName.CONSOLE_FIT, "no native recommended-console policy"),
        _absent(ScoreComponentName.COLD_START, "no authoritative cold-start evidence"),
        _absent(
            ScoreComponentName.EFFORT_QUALITY,
            "no authoritative effort-quality evidence",
        ),
        _absent(ScoreComponentName.MODEL_TIER, "no authoritative model-tier evidence"),
    )
    hard_exclusions = tuple(
        component
        for component in components
        if component.state is ScoreComponentState.HARD_EXCLUDED
    )
    ranking_score = None
    if not hard_exclusions:
        applied = {component.name: component.points for component in components}
        ranking_score = (
            int(applied[ScoreComponentName.CAPABILITY_MATCH] or 0)
            + int(applied[ScoreComponentName.HEALTH_GRADE] or 0)
            + int(applied[ScoreComponentName.CONTINUITY] or 0)
            + int(applied[ScoreComponentName.QUOTA_MARGIN] or 0)
            - int(applied[ScoreComponentName.RECENT_USE] or 0)
        )
    ordered_capabilities = tuple(
        sorted(capability.name for capability in candidate.capabilities)
    )
    return CapabilityMatch(
        node_id=candidate.node_id,
        peer_kind=candidate.peer_kind,
        profile_id=candidate.profile_id,
        candidate_status=(
            CapabilityCandidateStatus.HARD_EXCLUDED
            if hard_exclusions
            else CapabilityCandidateStatus.ELIGIBLE
        ),
        exclusion_reason=(
            hard_exclusions[0].reason if hard_exclusions else None
        ),
        availability_status=candidate.availability_status,
        admission_status=candidate.admission_status,
        cost_tier=None,
        cost_tier_state=ScoreComponentState.ABSENT,
        model_tier=None,
        model_tier_state=ScoreComponentState.ABSENT,
        ordered_capabilities=ordered_capabilities,
        ranking_score=ranking_score,
        components=components,
        provenance=tuple(provenance),
    )


def rank_capability_candidates(
    candidates: tuple[CapabilityCandidateFacts, ...],
    *,
    needs: str,
    requested_effort: str,
    policy: CapabilityMatchingPolicy,
) -> CapabilityRankingResult:
    """Score and deterministically order an immutable candidate snapshot."""

    normalized_needs = needs.strip()
    normalized_effort = requested_effort.strip().lower()
    if normalized_effort == "medium":
        normalized_effort = "mid"
    scored = tuple(
        score_capability_candidate(
            candidate,
            needs=normalized_needs,
            requested_effort=normalized_effort,
            policy=policy,
        )
        for candidate in candidates
    )
    eligible = tuple(
        sorted(
            (
                match
                for match in scored
                if match.candidate_status is CapabilityCandidateStatus.ELIGIBLE
            ),
            key=lambda match: (
                -int(match.ranking_score),  # type: ignore[arg-type]
                0 if match.availability_status is AvailabilityState.HEALTHY else 1,
                match.node_id,
            ),
        )
    )
    excluded = tuple(
        sorted(
            (
                match
                for match in scored
                if match.candidate_status is CapabilityCandidateStatus.HARD_EXCLUDED
            ),
            key=lambda match: match.node_id,
        )
    )
    return CapabilityRankingResult(
        formula_id=policy.formula_id,
        policy_id=policy.policy_id,
        policy_revision=policy.policy_revision,
        needs=normalized_needs,
        requested_effort=normalized_effort,
        ordered_matches=eligible,
        excluded_candidates=excluded,
        fallback=None,
        evaluated_at=policy.evaluated_at,
    )


def _fallback_safe(match: CapabilityMatch) -> bool:
    hard = tuple(
        component
        for component in match.components
        if component.state is ScoreComponentState.HARD_EXCLUDED
    )
    return not hard or all(
        component.name is ScoreComponentName.CAPABILITY_MATCH
        and component.reason == "capability mismatch"
        for component in hard
    )


def resolve_default_proposer(
    ranking: CapabilityRankingResult,
    *,
    policy: DefaultProposerPolicy,
    coordinator_history: tuple[str, ...],
) -> FallbackResolution:
    """Resolve a fixed/rotating token to one currently safe node ID."""

    matches = {
        match.node_id: match
        for match in (*ranking.ordered_matches, *ranking.excluded_candidates)
    }
    safe_ids = {
        node_id for node_id, match in matches.items() if _fallback_safe(match)
    }
    if policy.mode == "FIXED":
        assert policy.fixed_node_id is not None
        considered = (policy.fixed_node_id,)
        if policy.fixed_node_id in safe_ids:
            return FallbackResolution(
                node_id=policy.fixed_node_id,
                basis="FIXED_DEFAULT",
                considered_node_ids=considered,
            )
        return FallbackResolution(
            node_id=None,
            basis="NO_ELIGIBLE_DEFAULT",
            considered_node_ids=considered,
        )

    order = policy.rotation_order
    most_recent = next(
        (node_id for node_id in reversed(coordinator_history) if node_id in order),
        None,
    )
    start = 0 if most_recent is None else (order.index(most_recent) + 1) % len(order)
    considered = order[start:] + order[:start]
    selected = next((node_id for node_id in considered if node_id in safe_ids), None)
    return FallbackResolution(
        node_id=selected,
        basis=(
            "ROTATING_DEFAULT"
            if selected is not None
            else "NO_ELIGIBLE_DEFAULT"
        ),
        considered_node_ids=considered,
    )
