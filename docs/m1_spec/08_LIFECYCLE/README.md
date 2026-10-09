# Closed Feedback Loop After Development and Testing

This directory **does not treat development completion as the endpoint.**
In M1, a change is closed when evidence closes the loop below.

```text
Requirement / Evidence
→ RED
→ Minimum Implementation
→ Deterministic Verification
→ Package / Clean Install
→ Real Runtime Canary
→ Release
→ Stabilization Observation
→ Feedback / Incident / Drift
→ Triage + Reproduce
→ Root Cause / Decision
→ Requirement + Regression + Evidence Update
→ Re-verify / Promote / Rollback
→ Closure + Recurrence Watch
↺
```

## SSOT

- Machine-readable lifecycle: `closed-loop.json`
- invariant catalog: `INVARIANT_CATALOG.md` (View; SSOT is `closed-loop.json`)
- lifecycle shape: `closed-loop.schema.json`
- release gate: `release-gates.json`
- test→gate traceability: `../06_GUIDES/TEST_SET/TEST_RELEASE_GATE_MAP.json`
- release gate shape: `release-gates.schema.json`
- Operational signal classification: `signal-routing.json`
- Operational signal shape: `signal-routing.schema.json`

These documents are human-readable guides to the SSOTs above.

## Reading order

1. `CLOSED_LOOP_OPERATING_MODEL.md`
2. `RELEASE_PROMOTION_ROLLBACK.md`
3. `OPERATIONS_OBSERVABILITY_SLO.md`
4. `INCIDENT_PROBLEM_CHANGE.md`
5. `FEEDBACK_TRIAGE_AND_LEARNING.md`
6. `EVIDENCE_RETENTION_AND_AUDIT.md`
7. `DEPRECATION_EXTENSION_LIFECYCLE.md`
8. `POST_RELEASE_REVIEW.md`
9. `RUNBOOKS/` for specific situations
10. `TEMPLATES/` for creating actual records

## Four completion states

| State | Meaning |
|---|---|
| **Done** | Requirements and implementation pass tests |
| **Released** | Released after passing package/real-environment gates |
| **Operated** | Evidence of actual use collected during the stabilization observation window without key invariant breaches |
| **Closed** | Feedback, risks, regressions, and docs are all classified, and recurrence watch is complete |

`Done != Closed`.

## Gate evidence policy

`gate-evidence-policy.json` defines canonical release gate result states, candidate identity binding, freshness triggers, and stale/timeout HOLD rules.
