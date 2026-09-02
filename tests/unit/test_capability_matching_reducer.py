from __future__ import annotations

from dataclasses import replace

import pytest

from peerhub.core.evidence import EvidenceState
from peerhub.health.contract import AdmissionState, AvailabilityState
from peerhub.routing.capability_matching import (
    CapabilityCandidateFacts,
    CapabilityCandidateStatus,
    CapabilityEvidenceProvenance,
    CapabilityMatchingPolicy,
    ConfiguredCapability,
    DefaultProposerPolicy,
    QuotaRankingFact,
    ScoreComponentName,
    ScoreComponentState,
    rank_capability_candidates,
    resolve_default_proposer,
    score_capability_candidate,
)


def _provenance(
    fact: str = "health-grade",
    state: EvidenceState = EvidenceState.MEASURED,
) -> CapabilityEvidenceProvenance:
    return CapabilityEvidenceProvenance(
        fact=fact,
        evidence_state=state,
        source_kind="test",
        source_id=f"test:{fact}",
        source_revision=1,
        source_digest=None,
        evidence_refs=(),
        observed_at=100,
    )


def _policy() -> CapabilityMatchingPolicy:
    return CapabilityMatchingPolicy(
        formula_id="native-v1",
        policy_id="capability-native-v1",
        policy_revision=1,
        target_id="routing-policy:capability-native-v1:1",
        target_revision=1,
        target_digest="a" * 64,
        empty_capability_points=1,
        exact_capability_points=10,
        substring_capability_points=7,
        health_points=(
            (AvailabilityState.HEALTHY, 3),
            (AvailabilityState.DEGRADED, 1),
            (AvailabilityState.UNKNOWN, 0),
            (AvailabilityState.PROBING, 0),
            (AvailabilityState.STALE, -5),
        ),
        continuity_bonus=2,
        quota_bands=(
            (0.90, 3),
            (0.75, 2),
            (0.50, 1),
            (0.10, -1),
            (0.0, -3),
        ),
        recent_history_window=2,
        recent_use_penalty=2,
        default_proposer=DefaultProposerPolicy(
            mode="ROTATING",
            fixed_node_id=None,
            rotation_order=("cc", "ag", "cx"),
        ),
        evaluated_at=100,
    )


def _quota(
    remaining: float,
    *,
    state: EvidenceState = EvidenceState.MEASURED,
) -> QuotaRankingFact:
    provenance = _provenance("quota-margin", state)
    return QuotaRankingFact(
        projection_id="quota-1",
        instance_id="cc",
        profile_id="cc.standard",
        quota_pool_scope="C",
        remaining_fraction=remaining,
        resets_at=200,
        revision=1,
        updated_at=90,
        evidence_state=state,
        provenance=provenance,
    )


def _candidate(
    node_id: str = "cc",
    *,
    enabled: bool = True,
    aliases: tuple[str, ...] = ("claude",),
    capabilities: tuple[ConfiguredCapability, ...] = (
        ConfiguredCapability("architecture", ("test",)),
    ),
    availability: AvailabilityState | None = AvailabilityState.HEALTHY,
    admission: AdmissionState | None = AdmissionState.OPEN,
    backed_off: bool | None = False,
    quotas: tuple[QuotaRankingFact, ...] = (),
    current: bool = False,
    history: tuple[str, ...] = (),
) -> CapabilityCandidateFacts:
    return CapabilityCandidateFacts(
        node_id=node_id,
        peer_kind=node_id,
        profile_id=f"{node_id}.standard",
        peer_node_target_id=f"peer-node:{node_id}",
        peer_node_revision=1,
        enabled=enabled,
        aliases=aliases,
        capabilities=capabilities,
        capability_config_target_id=f"peer-capability-config:{node_id}",
        capability_config_revision=1,
        availability_status=availability,
        admission_status=admission,
        profile_gate_backed_off=backed_off,
        health_provenance=_provenance(
            state=(
                EvidenceState.ABSENT
                if availability is None
                else EvidenceState.MEASURED
            )
        ),
        quota_projections=quotas,
        is_current_leader=current,
        recent_leader_node_ids=history,
    )


@pytest.mark.parametrize(
    ("needs", "points"),
    [
        ("", 1),
        ("CC", 10),
        ("CLAUDE", 10),
        ("Architecture", 10),
        ("architect", 7),
        ("enterprise-architecture", 7),
    ],
)
def test_native_v1_capability_match_tiers(needs: str, points: int) -> None:
    match = score_capability_candidate(
        _candidate(), needs=needs, requested_effort="mid", policy=_policy()
    )
    component = match.components[0]
    assert component.name is ScoreComponentName.CAPABILITY_MATCH
    assert component.state is ScoreComponentState.APPLIED
    assert component.points == points


def test_nonempty_capability_mismatch_is_hard_excluded() -> None:
    match = score_capability_candidate(
        _candidate(), needs="legal", requested_effort="mid", policy=_policy()
    )
    assert match.candidate_status is CapabilityCandidateStatus.HARD_EXCLUDED
    assert match.ranking_score is None
    assert match.components[0].state is ScoreComponentState.HARD_EXCLUDED


def test_health_grading_keeps_stale_eligible_and_real_zero_applied() -> None:
    stale = score_capability_candidate(
        _candidate(availability=AvailabilityState.STALE),
        needs="architecture",
        requested_effort="mid",
        policy=_policy(),
    )
    assert stale.candidate_status is CapabilityCandidateStatus.ELIGIBLE
    health = stale.components[1]
    assert health.state is ScoreComponentState.APPLIED
    assert health.points == -5
    assert stale.ranking_score == 5

    unknown = score_capability_candidate(
        _candidate(availability=AvailabilityState.UNKNOWN),
        needs="architecture",
        requested_effort="mid",
        policy=_policy(),
    )
    assert unknown.components[1].state is ScoreComponentState.APPLIED
    assert unknown.components[1].points == 0


@pytest.mark.parametrize(
    "candidate",
    [
        _candidate(availability=AvailabilityState.UNAVAILABLE),
        _candidate(admission=AdmissionState.PROBE_AUTHORIZED),
        _candidate(backed_off=True),
    ],
)
def test_health_admission_and_backoff_hard_exclusions(
    candidate: CapabilityCandidateFacts,
) -> None:
    match = score_capability_candidate(
        candidate,
        needs="architecture",
        requested_effort="mid",
        policy=_policy(),
    )
    assert match.candidate_status is CapabilityCandidateStatus.HARD_EXCLUDED
    assert match.components[1].state is ScoreComponentState.HARD_EXCLUDED


def test_quota_zero_is_hard_exclusion_and_expired_only_is_absent() -> None:
    exhausted = score_capability_candidate(
        _candidate(quotas=(_quota(0.0),)),
        needs="architecture",
        requested_effort="mid",
        policy=_policy(),
    )
    assert exhausted.components[3].state is ScoreComponentState.HARD_EXCLUDED
    assert exhausted.ranking_score is None

    expired = score_capability_candidate(
        _candidate(quotas=(_quota(0.8, state=EvidenceState.STALE),)),
        needs="architecture",
        requested_effort="mid",
        policy=_policy(),
    )
    assert expired.components[3].state is ScoreComponentState.ABSENT
    assert expired.components[3].points is None
    assert expired.candidate_status is CapabilityCandidateStatus.ELIGIBLE


def test_formula_applies_continuity_quota_and_recent_use_exactly() -> None:
    match = score_capability_candidate(
        _candidate(
            quotas=(_quota(0.8),),
            current=True,
            history=("cc", "cc"),
        ),
        needs="architecture",
        requested_effort="medium",
        policy=_policy(),
    )
    points = {component.name: component.points for component in match.components}
    assert points[ScoreComponentName.CAPABILITY_MATCH] == 10
    assert points[ScoreComponentName.HEALTH_GRADE] == 3
    assert points[ScoreComponentName.CONTINUITY] == 2
    assert points[ScoreComponentName.QUOTA_MARGIN] == 2
    assert points[ScoreComponentName.RECENT_USE] == 2
    assert match.ranking_score == 15


def test_unsupported_components_are_absent_never_synthetic_zeroes() -> None:
    match = score_capability_candidate(
        _candidate(availability=None, admission=None, backed_off=None),
        needs="architecture",
        requested_effort="high",
        policy=_policy(),
    )
    components = {component.name: component for component in match.components}
    for name in (
        ScoreComponentName.COST,
        ScoreComponentName.CONSOLE_FIT,
        ScoreComponentName.COLD_START,
        ScoreComponentName.EFFORT_QUALITY,
        ScoreComponentName.MODEL_TIER,
    ):
        assert components[name].state is ScoreComponentState.ABSENT
        assert components[name].points is None
    assert match.cost_tier is None
    assert match.cost_tier_state is ScoreComponentState.ABSENT
    assert match.model_tier is None
    assert match.model_tier_state is ScoreComponentState.ABSENT


def test_tie_break_is_score_then_healthy_then_case_sensitive_node_id() -> None:
    candidates = (
        _candidate("z", availability=AvailabilityState.HEALTHY),
        _candidate(
            "y",
            aliases=(),
            availability=AvailabilityState.UNKNOWN,
            quotas=(_quota(0.95),),
        ),
        _candidate("b", availability=AvailabilityState.UNKNOWN),
        _candidate("A", availability=AvailabilityState.UNKNOWN),
    )
    ranking = rank_capability_candidates(
        candidates,
        needs="architecture",
        requested_effort="low",
        policy=_policy(),
    )
    assert tuple(match.node_id for match in ranking.ordered_matches) == (
        "z",
        "y",
        "A",
        "b",
    )


def test_rotating_default_uses_order_history_and_all_safety_gates() -> None:
    ranking = rank_capability_candidates(
        (
            _candidate("cc", aliases=()),
            _candidate("ag", enabled=False, aliases=()),
            _candidate(
                "cx",
                aliases=(),
                availability=AvailabilityState.UNAVAILABLE,
            ),
        ),
        needs="no-capability-matches-this",
        requested_effort="mid",
        policy=_policy(),
    )
    assert not ranking.ordered_matches
    resolution = resolve_default_proposer(
        ranking,
        policy=DefaultProposerPolicy(
            mode="ROTATING",
            fixed_node_id=None,
            rotation_order=("cc", "ag", "cx"),
        ),
        coordinator_history=("cc",),
    )
    assert resolution.considered_node_ids == ("ag", "cx", "cc")
    assert resolution.node_id == "cc"
    assert resolution.basis == "ROTATING_DEFAULT"


def test_unknown_effort_is_rejected() -> None:
    with pytest.raises(ValueError, match="requested_effort"):
        score_capability_candidate(
            replace(_candidate(), node_id="cc"),
            needs="architecture",
            requested_effort="ultra",
            policy=_policy(),
        )
