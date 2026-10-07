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
import math
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, cast


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


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _finite_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass
class CandidatePeer:
    peer_id: str
    capabilities: list[str]
    health_status: str = "UNKNOWN"
    quota_remaining: int | None = None
    eval_score: float = 1.0
    source_refs: dict[str, str | None] = field(default_factory=lambda: {
        "capability": None, "health": None, "quota": None, "eval": None,
    })


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


@dataclass(frozen=True)
class CandidateEvaluation:
    peer_id: str
    eligible: bool
    rejection_reason: str | None = None
    composite_score: float = 0.0
    rank: int = 1


@dataclass(frozen=True)
class RouteDecision:
    decision_id: str
    request_id: str
    selected_peer_id: str | None
    policy_revision: int
    evaluations: tuple[CandidateEvaluation, ...]
    decision_digest: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    input_digest: str = ""
    input_snapshot_json: str = ""


def _compute_decision_digest(
    request_id: str,
    selected_peer_id: str | None,
    policy_revision: int,
    evaluations: list[CandidateEvaluation],
    input_digest: str = "",
) -> str:
    """Compute deterministic SHA-256 digest of route decision."""
    payload = {
        "request_id": request_id,
        "selected_peer_id": selected_peer_id,
        "policy_revision": policy_revision,
        "input_digest": input_digest,
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
        if policy.unknown_evidence_mode not in ("REJECT", "DEGRADE"):
            raise RoutingUnknownEvidencePolicyError("Unknown evidence policy must be REJECT or DEGRADE.")
        if not _positive_int(policy.revision):
            raise RoutingPolicyViolationError("Policy revision must be a positive integer.")
        if any(key not in ("eval_score", "quota_headroom") for key in policy.weights):
            raise RoutingPolicyViolationError("Unsupported routing weight.")
        if not _positive_int(request.timeout_ms):
            raise RoutingPolicyViolationError("timeout_ms must be a positive integer.")
        if not _finite_number(request.cost_sensitivity) or not 0 <= request.cost_sensitivity <= 1:
            raise RoutingPolicyViolationError("cost_sensitivity must be finite and within [0, 1].")
        required_caps = set(request.required_capabilities)
        w_eval = policy.weights.get("eval_score", 0.5)
        w_quota = policy.weights.get("quota_headroom", 0.5)
        if any(not _finite_number(w) or w < 0 for w in (w_eval, w_quota)) or w_eval + w_quota <= 0:
            raise RoutingPolicyViolationError("Weights must be finite, nonnegative, and have a positive sum.")
        if len({cand.peer_id for cand in candidates}) != len(candidates):
            raise RoutingPolicyViolationError("Candidate peer IDs must be unique.")

        evals: list[CandidateEvaluation] = []
        for cand in candidates:
            if set(cand.source_refs) - {"capability", "health", "quota", "eval"} or any(
                value is not None and (not isinstance(cast(object, value), str) or not value) for value in cand.source_refs.values()
            ):
                raise RoutingPolicyViolationError("Evidence references must be nonempty source refs or UNKNOWN.")
            if not cand.peer_id or cand.health_status not in ("HEALTHY", "DEGRADED", "UNKNOWN", "UNHEALTHY"):
                raise RoutingPolicyViolationError("Candidate requires a peer ID and a declared health state.")
            if not _finite_number(cand.eval_score) or not 0 <= cand.eval_score <= 1:
                raise RoutingPolicyViolationError("Evaluation score must be finite and within [0, 1].")
            if cand.quota_remaining is not None and (
                not _nonnegative_int(cand.quota_remaining)
            ):
                raise RoutingPolicyViolationError("Quota must be a nonnegative integer or UNKNOWN.")
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
                if policy.unknown_evidence_mode == "REJECT":
                    evals.append(CandidateEvaluation(cand.peer_id, False, "UNKNOWN_QUOTA_REJECTED", 0.0))
                    continue
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
                0 if e.eligible else 1,
                -e.composite_score,
                e.peer_id,
            ),
        )

        # Assign ranks
        return [replace(e, rank=idx) for idx, e in enumerate(evals, start=1)]

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
        try:
            snapshot_json = json.dumps({
                "schema_version": 1,
                "request": asdict(request),
                "policy": asdict(effective_policy),
                "candidates": [{**asdict(c), "source_refs": {
                    key: c.source_refs.get(key) for key in ("capability", "health", "quota", "eval")
                }} for c in sorted(candidates, key=lambda c: c.peer_id)],
            }, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise RoutingPolicyViolationError("Routing inputs must be finite JSON evidence.") from exc
        input_digest = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()

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
            input_digest=input_digest,
        )

        return RouteDecision(
            decision_id=decision_id,
            request_id=request.request_id,
            selected_peer_id=selected_peer_id,
            policy_revision=effective_policy.revision,
            evaluations=tuple(evaluations),
            decision_digest=digest,
            input_digest=input_digest,
            input_snapshot_json=snapshot_json,
        )

    def replay_decision(self, decision: RouteDecision) -> RouteDecision:
        """Replay immutable exact evidence without consulting current provider state."""
        if not decision.input_snapshot_json or hashlib.sha256(decision.input_snapshot_json.encode("utf-8")).hexdigest() != decision.input_digest:
            raise RoutingPolicyViolationError("Routing input evidence digest mismatch.")
        try:
            data: dict[str, Any] = json.loads(decision.input_snapshot_json)
            if data.get("schema_version") != 1:
                raise ValueError("Unsupported routing evidence version.")
            replayed = self.route(RouteRequest(**data["request"]),
                                  [CandidatePeer(**item) for item in data["candidates"]],
                                  RoutePolicy(**data["policy"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RoutingPolicyViolationError("Invalid routing replay evidence.") from exc
        if replayed.decision_digest != decision.decision_digest or replayed.selected_peer_id != decision.selected_peer_id or replayed.evaluations != decision.evaluations:
            raise RoutingPolicyViolationError("Routing decision does not match its input evidence.")
        return replayed
