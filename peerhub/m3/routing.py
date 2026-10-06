"""M3.3 Routing Engine (RTG-001..012).

Freeze Invariants:
1. Core & M2 unchanged; M3 removable.
7. Routing selects; it does not execute. Exact evidence replayable.
8. UNKNOWN stays unknown per policy.
Zero dev-dependency violation: Python standard library only.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class RoutingError(Exception):
    """Base exception for routing module."""


class NoEligibleRouteCandidateError(RoutingError):
    """Raised when no candidate meets requirements or policy constraints."""


class RoutingExecutionAttemptForbiddenError(RoutingError):
    """Raised when an operation attempts to execute work from the routing layer (Invariant 7)."""


class RoutingPolicyViolationError(RoutingError):
    """Raised on invalid routing policy configuration."""


class RoutingUnknownEvidencePolicyError(RoutingError):
    """Raised when UNKNOWN evidence is handled illegally against policy (Invariant 8)."""


@dataclass
class CandidatePeer:
    peer_id: str
    capabilities: list[str]
    health_status: str = "HEALTHY"
    quota_remaining: int | None = None
    eval_score: float = 1.0


@dataclass
class RouteRequest:
    request_id: str
    required_capabilities: list[str]
    cost_sensitivity: float = 0.5
    timeout_ms: int = 5000
    context: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass
class RoutePolicy:
    policy_id: str = "default"
    revision: int = 1
    unknown_evidence_mode: str = "REJECT"
    weights: dict[str, float] = field(default_factory=lambda: {"eval_score": 0.5, "quota_headroom": 0.5})


@dataclass
class CandidateEvaluation:
    peer_id: str
    eligible: bool
    rejection_reason: str | None = None
    composite_score: float = 0.0
    rank: int = 1


@dataclass
class RouteDecision:
    decision_id: str
    request_id: str
    selected_peer_id: str | None
    policy_revision: int
    evaluations: list[CandidateEvaluation]
    decision_digest: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _compute_decision_digest(
    request_id: str,
    selected_peer_id: str | None,
    policy_revision: int,
    evaluations: list[CandidateEvaluation],
) -> str:
    """Compute deterministic SHA-256 digest of route decision."""
    payload = {
        "request_id": request_id,
        "selected_peer_id": selected_peer_id,
        "policy_revision": policy_revision,
        "evaluations": [
            {
                "peer_id": e.peer_id,
                "eligible": e.eligible,
                "rejection_reason": e.rejection_reason,
                "score": round(e.composite_score, 4),
                "rank": e.rank,
            }
            for e in evaluations
        ],
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Router:
    """Deterministic policy-based candidate selector (selects, never executes)."""

    def execute_decision(self, decision: RouteDecision) -> None:
        """Enforces Invariant 7: Routing selects, it does not execute."""
        raise RoutingExecutionAttemptForbiddenError(
            f"Invariant 7 violation: Router cannot execute decision {decision.decision_id}. "
            "Routing is strictly a selection policy adapter."
        )

    def evaluate_candidates(
        self,
        request: RouteRequest,
        candidates: list[CandidatePeer],
        policy: RoutePolicy,
    ) -> list[CandidateEvaluation]:
        """Evaluate and rank candidates deterministically."""
        required_caps = set(request.required_capabilities)
        w_eval = policy.weights.get("eval_score", 0.5)
        w_quota = policy.weights.get("quota_headroom", 0.5)

        evals: list[CandidateEvaluation] = []
        for cand in candidates:
            # 1. Check capability coverage
            cand_caps = set(cand.capabilities)
            if not required_caps.issubset(cand_caps):
                evals.append(
                    CandidateEvaluation(
                        peer_id=cand.peer_id,
                        eligible=False,
                        rejection_reason="MISSING_CAPABILITIES",
                        composite_score=0.0,
                    )
                )
                continue

            # 2. Check health status
            if cand.health_status == "UNHEALTHY":
                evals.append(
                    CandidateEvaluation(
                        peer_id=cand.peer_id,
                        eligible=False,
                        rejection_reason="UNHEALTHY",
                        composite_score=0.0,
                    )
                )
                continue
            elif cand.health_status == "UNKNOWN":
                if policy.unknown_evidence_mode == "REJECT":
                    evals.append(
                        CandidateEvaluation(
                            peer_id=cand.peer_id,
                            eligible=False,
                            rejection_reason="UNKNOWN_HEALTH_REJECTED",
                            composite_score=0.0,
                        )
                    )
                    continue

            # 3. Calculate quota headroom (Invariant 8: UNKNOWN quota is never unlimited)
            if cand.quota_remaining is None:
                norm_quota = 0.0  # Zero bonus for unknown quota
            else:
                norm_quota = min(1.0, max(0.0, cand.quota_remaining / 1000.0))

            # 4. Composite score calculation
            score = (w_eval * cand.eval_score) + (w_quota * norm_quota)
            evals.append(
                CandidateEvaluation(
                    peer_id=cand.peer_id,
                    eligible=True,
                    rejection_reason=None,
                    composite_score=round(score, 4),
                )
            )

        # 5. Deterministic sorting: eligible first, score desc, tie-break by peer_id asc
        evals.sort(
            key=lambda e: (
                1 if e.eligible else 0,
                e.composite_score,
                # Lexicographical inverse for ascending peer_id when reverse=True
                "".join(chr(255 - ord(c)) for c in e.peer_id),
            ),
            reverse=True,
        )

        # Assign ranks
        for idx, e in enumerate(evals, start=1):
            e.rank = idx

        return evals

    def route(
        self,
        request: RouteRequest,
        candidates: list[CandidatePeer],
        policy: RoutePolicy | None = None,
    ) -> RouteDecision:
        """Perform deterministic candidate selection and return RouteDecision."""
        if not candidates:
            raise NoEligibleRouteCandidateError(f"No candidates provided for request {request.request_id}.")

        effective_policy = policy if policy is not None else RoutePolicy()
        evaluations = self.evaluate_candidates(request, candidates, effective_policy)

        eligible = [e for e in evaluations if e.eligible]
        if not eligible:
            raise NoEligibleRouteCandidateError(
                f"No eligible candidates found for request {request.request_id}."
            )

        selected_peer_id = eligible[0].peer_id
        decision_id = f"dec-{uuid.uuid4().hex[:12]}"
        digest = _compute_decision_digest(
            request_id=request.request_id,
            selected_peer_id=selected_peer_id,
            policy_revision=effective_policy.revision,
            evaluations=evaluations,
        )

        return RouteDecision(
            decision_id=decision_id,
            request_id=request.request_id,
            selected_peer_id=selected_peer_id,
            policy_revision=effective_policy.revision,
            evaluations=evaluations,
            decision_digest=digest,
        )
