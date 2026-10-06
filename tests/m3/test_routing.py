"""M3.3 Routing Engine Test Suite (RTG-001..012).

Freeze Invariant 7: Routing selects; it does not execute. Exact evidence replayable.
Freeze Invariant 8: UNKNOWN stays unknown per policy.
Core imports M3 = 0.
"""
from __future__ import annotations

import pytest

from peerhub.m3.routing import (
    Router,
    RouteRequest,
    RoutePolicy,
    CandidatePeer,
    RouteDecision,
    NoEligibleRouteCandidateError,
    RoutingExecutionAttemptForbiddenError,
    RoutingPolicyViolationError,
    RoutingUnknownEvidencePolicyError,
)


@pytest.fixture
def default_router() -> Router:
    return Router()


@pytest.fixture
def default_policy() -> RoutePolicy:
    return RoutePolicy(
        policy_id="policy-default",
        revision=1,
        unknown_evidence_mode="REJECT",
        weights={"eval_score": 0.5, "quota_headroom": 0.5},
    )


def test_rtg_001_invariant_7_selects_without_execution(default_router: Router, default_policy: RoutePolicy):
    """RTG-001: Invariant 7 - Routing selects candidate peer without executing work or invoking external side effects."""
    req = RouteRequest(
        request_id="req-1",
        required_capabilities=["python", "review"],
        cost_sensitivity=0.5,
        timeout_ms=5000,
    )
    candidates = [
        CandidatePeer("peer:1", ["python", "review"], "HEALTHY", quota_remaining=100, eval_score=0.9),
        CandidatePeer("peer:2", ["python"], "HEALTHY", quota_remaining=50, eval_score=0.7),
    ]

    decision = default_router.route(req, candidates, default_policy)
    assert decision.selected_peer_id == "peer:1"
    assert len(decision.evaluations) == 2
    assert decision.evaluations[0].peer_id == "peer:1"
    assert decision.evaluations[0].eligible is True
    assert decision.evaluations[1].eligible is False  # Missing 'review'


def test_rtg_002_execution_attempt_forbidden(default_router: Router):
    """RTG-002: Attempting execution from Router raises RoutingExecutionAttemptForbiddenError."""
    req = RouteRequest("req-exec", ["python"])
    candidates = [CandidatePeer("peer:1", ["python"], "HEALTHY", quota_remaining=100, eval_score=0.9)]
    decision = default_router.route(req, candidates)

    with pytest.raises(RoutingExecutionAttemptForbiddenError):
        default_router.execute_decision(decision)


def test_rtg_003_deterministic_decision_replay(default_router: Router, default_policy: RoutePolicy):
    """RTG-003: Deterministic decision replay produces identical candidate ranking and decision digest."""
    req = RouteRequest("req-replay", ["calc"])
    candidates = [
        CandidatePeer("peer:a", ["calc"], "HEALTHY", quota_remaining=100, eval_score=0.8),
        CandidatePeer("peer:b", ["calc"], "HEALTHY", quota_remaining=80, eval_score=0.9),
    ]

    d1 = default_router.route(req, candidates, default_policy)
    d2 = default_router.route(req, candidates, default_policy)

    assert d1.selected_peer_id == d2.selected_peer_id
    assert d1.decision_digest == d2.decision_digest
    assert len(d1.evaluations) == len(d2.evaluations)


def test_rtg_004_policy_revision_affects_weights_and_digest(default_router: Router):
    """RTG-004: RoutePolicy revision change alters decision digest and reflects new weights deterministically."""
    req = RouteRequest("req-weights", ["calc"])
    # peer:a has high eval, low quota; peer:b has low eval, high quota
    candidates = [
        CandidatePeer("peer:a", ["calc"], "HEALTHY", quota_remaining=10, eval_score=0.95),
        CandidatePeer("peer:b", ["calc"], "HEALTHY", quota_remaining=1000, eval_score=0.7),
    ]

    p_eval = RoutePolicy("p1", revision=1, unknown_evidence_mode="REJECT", weights={"eval_score": 0.9, "quota_headroom": 0.1})
    p_quota = RoutePolicy("p2", revision=2, unknown_evidence_mode="REJECT", weights={"eval_score": 0.1, "quota_headroom": 0.9})

    d_eval = default_router.route(req, candidates, p_eval)
    d_quota = default_router.route(req, candidates, p_quota)

    assert d_eval.selected_peer_id == "peer:a"
    assert d_quota.selected_peer_id == "peer:b"
    assert d_eval.decision_digest != d_quota.decision_digest


def test_rtg_005_invariant_8_unknown_health_disqualified(default_router: Router, default_policy: RoutePolicy):
    """RTG-005: Invariant 8 - UNKNOWN health evidence causes disqualification in default fail-safe policy."""
    req = RouteRequest("req-unknown-h", ["calc"])
    candidates = [
        CandidatePeer("peer:unknown", ["calc"], "UNKNOWN", quota_remaining=100, eval_score=0.99),
        CandidatePeer("peer:healthy", ["calc"], "HEALTHY", quota_remaining=50, eval_score=0.70),
    ]

    decision = default_router.route(req, candidates, default_policy)
    assert decision.selected_peer_id == "peer:healthy"

    # peer:unknown was marked ineligible
    un_eval = next(e for e in decision.evaluations if e.peer_id == "peer:unknown")
    assert un_eval.eligible is False
    assert un_eval.rejection_reason == "UNKNOWN_HEALTH_REJECTED"


def test_rtg_006_invariant_8_unknown_quota_never_treated_as_unlimited(default_router: Router):
    """RTG-006: Invariant 8 - UNKNOWN quota evidence is never treated as unlimited quota."""
    req = RouteRequest("req-unknown-q", ["calc"])
    candidates = [
        CandidatePeer("peer:no-quota", ["calc"], "HEALTHY", quota_remaining=None, eval_score=0.8),
        CandidatePeer("peer:known-quota", ["calc"], "HEALTHY", quota_remaining=10, eval_score=0.8),
    ]
    # Quota heavy policy
    p = RoutePolicy("p-q", revision=1, unknown_evidence_mode="REJECT", weights={"eval_score": 0.1, "quota_headroom": 0.9})
    decision = default_router.route(req, candidates, p)

    # Known quota candidate selected over unknown quota candidate
    assert decision.selected_peer_id == "peer:known-quota"


def test_rtg_007_candidate_breakdown_ranking(default_router: Router, default_policy: RoutePolicy):
    """RTG-007: Candidate breakdown includes capability matching, score calculation, and rank order."""
    req = RouteRequest("req-eval", ["calc"])
    candidates = [
        CandidatePeer("p1", ["calc"], "HEALTHY", 100, 0.9),
        CandidatePeer("p2", ["calc"], "HEALTHY", 50, 0.5),
        CandidatePeer("p3", ["other"], "HEALTHY", 100, 0.9),
    ]
    decision = default_router.route(req, candidates, default_policy)
    assert len(decision.evaluations) == 3
    # Top rank should be p1
    assert decision.evaluations[0].peer_id == "p1"
    assert decision.evaluations[0].rank == 1
    # p3 is ineligible
    assert decision.evaluations[2].peer_id == "p3"
    assert decision.evaluations[2].eligible is False


def test_rtg_008_deterministic_tie_break(default_router: Router, default_policy: RoutePolicy):
    """RTG-008: Tie-break ordering is deterministic based on lexicographical peer_id."""
    req = RouteRequest("req-tie", ["calc"])
    candidates = [
        CandidatePeer("peer:zeta", ["calc"], "HEALTHY", 100, 0.8),
        CandidatePeer("peer:alpha", ["calc"], "HEALTHY", 100, 0.8),
    ]
    decision = default_router.route(req, candidates, default_policy)
    # peer:alpha comes first lexicographically
    assert decision.selected_peer_id == "peer:alpha"


def test_rtg_009_no_eligible_candidate_raises(default_router: Router, default_policy: RoutePolicy):
    """RTG-009: No eligible candidate raises NoEligibleRouteCandidateError."""
    req = RouteRequest("req-none", ["special-gpu"])
    candidates = [
        CandidatePeer("p1", ["cpu"], "HEALTHY", 100, 0.9),
        CandidatePeer("p2", ["special-gpu"], "UNHEALTHY", 100, 0.9),
    ]
    with pytest.raises(NoEligibleRouteCandidateError):
        default_router.route(req, candidates, default_policy)


def test_rtg_010_empty_candidates_raises(default_router: Router):
    """RTG-010: Empty candidates list raises NoEligibleRouteCandidateError cleanly."""
    req = RouteRequest("req-empty", ["calc"])
    with pytest.raises(NoEligibleRouteCandidateError):
        default_router.route(req, [])


def test_rtg_011_stateless_pure_functional_selector(default_router: Router):
    """RTG-011: Router operates as stateless pure functional selector without storage coupling."""
    req = RouteRequest("req-stateless", ["calc"])
    candidates = [CandidatePeer("p1", ["calc"], "HEALTHY", 100, 0.9)]
    d1 = default_router.route(req, candidates)
    d2 = default_router.route(req, candidates)
    assert d1.selected_peer_id == d2.selected_peer_id == "p1"


def test_rtg_012_zero_dev_dependency_violation():
    """RTG-012: Zero dev-dependency violation: routing engine runs on standard library (REL-009)."""
    import importlib
    rtg_mod = importlib.import_module("peerhub.m3.routing")
    for bad in ("pytest", "yaml", "opentelemetry", "mcp", "scipy", "numpy", "sklearn"):
        assert bad not in rtg_mod.__dict__
