# Routing Contract (M3.3)

## 1. Authority Separation & Core Invariants
- **Core Invariant 7: `Routing selects; it does not execute. Exact evidence replayable`:**
  - The Router is strictly a deterministic policy selector. It evaluates candidates against requirements, observations, and policies to yield an immutable `RouteDecision`.
  - The Router NEVER triggers execution, dispatches tasks, or mutates stream state.
  - Replaying `route()` with the exact same inputs and policy revision produces the exact same `decision_digest`.
- **Core Invariant 8: `UNKNOWN stays unknown per policy`:**
  - UNKNOWN health or quota observations MUST NEVER be treated as healthy, unlimited, or supported by default.
  - Under `REJECT` mode (default P0 fail-safe), candidates with UNKNOWN status are disqualified from critical routing paths.
- **Decision Digest & Provenance:**
  - Every `RouteDecision` includes candidate breakdown scores, rejection reasons, input digests, and cryptographic SHA-256 decision digest.
- **Zero Dev-Dependency Violation (REL-009):**
  - Pure Python standard library implementation.

## 2. Public Interfaces & Protocols
The Routing engine (`peerhub.m3.routing`) provides:
- `CandidatePeer`:
  - `peer_id: str`
  - `capabilities: list[str]`
  - `health_status: str` ("HEALTHY" | "DEGRADED" | "UNKNOWN" | "UNHEALTHY")
  - `quota_remaining: int | None` (None indicates UNKNOWN)
  - `eval_score: float` (0.0 to 1.0)
- `RouteRequest`:
  - `request_id: str`
  - `required_capabilities: list[str]`
  - `cost_sensitivity: float`
  - `timeout_ms: int`
  - `context: dict[str, Any]`
- `RoutePolicy`:
  - `policy_id: str`
  - `revision: int`
  - `unknown_evidence_mode: str` ("REJECT" | "DEGRADE")
  - `weights: dict[str, float]`
- `CandidateEvaluation`:
  - `peer_id: str`
  - `eligible: bool`
  - `rejection_reason: str | None`
  - `composite_score: float`
  - `rank: int`
- `RouteDecision`:
  - `decision_id: str`
  - `request_id: str`
  - `selected_peer_id: str | None`
  - `policy_revision: int`
  - `evaluations: list[CandidateEvaluation]`
  - `decision_digest: str`
  - `created_at: str`
- `Router`:
  - `route(request: RouteRequest, candidates: list[CandidatePeer], policy: RoutePolicy | None = None) -> RouteDecision`
  - `evaluate_candidates(request: RouteRequest, candidates: list[CandidatePeer], policy: RoutePolicy) -> list[CandidateEvaluation]`

## 3. Route Decision Lifecycle State Machine
```text
[Route Decision Lifecycle]
  EVALUATING -> DECIDED -> EXPIRED
```
- Attempting execution from the routing layer raises `RoutingExecutionAttemptForbiddenError`.
- No eligible candidates raise `NoEligibleRouteCandidateError`.
