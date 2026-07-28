# Quota Period Scaling Policy R1

**Status:** Proposed Phase 0 design addition — design-only. Authorizes no
provider call, quota reservation, routing change, or configuration mutation.

**Scope:** A deliberate front-loaded quota-consumption policy: compress the
effective pacing period below the real reset window, drive the `TERMINAL`
pool to full (100%) consumption ahead of the real reset, and hold
`NON_TERMINAL` pools to a partial (80%) ceiling, also reached ahead of the
real reset rather than spread flat across it.

## 1. Ownership and SSOT boundary

- `UsageProvider` stays policy-blind: it reports only measured
  `UsageEvidence` (`quota_pool_id`, `usage_window_key`, measured
  used/remaining fraction, window start/reset, freshness, source). It
  computes no target, ceiling, or pacing state.
- Pacing policy facts live in a new value object, `QuotaPacingRule`, owned by
  the existing versioned `RoutingPolicy` — not a second ad hoc config file,
  and not cached inside `UsageProvider`.
- Evaluation is a pure function in `routing.pacing`: given `UsageEvidence` +
  `QuotaPacingRule` + `now`, it returns a `PacingAssessment`. It writes
  nothing back to measured evidence.
- `pool_role` (`TERMINAL` | `NON_TERMINAL` | `CUSTOM`) is explicit
  configuration on `QuotaPacingRule`, never inferred from peer name, model,
  or current terminal duty. All instances sharing one physical
  `quota_pool_id` must resolve to one compatible role; a conflicting
  assignment is a validation error, not two independent budgets.

## 2. Policy and evidence model

```text
QuotaPacingRule
  quota_pool_id
  usage_window_key
  pool_role: TERMINAL | NON_TERMINAL | CUSTOM
  target_fraction: Decimal        # terminal default 1.0; non-terminal default 0.8
  effective_period_days: Decimal  # e.g. 6.25 against a measured ~7-day window
  curve: LINEAR_TO_TARGET         # v1 only; a different curve needs a new schema
  post_target: HOLD_AUTOMATIC
```

`QuotaPacingRule` is a typed subresource of `RoutingPolicy`, not an
independent aggregate: it carries no revision counter of its own. A write
to any `QuotaPacingRule` CASes against `RoutingPolicy.revision`, so pacing
rules and every other routing-policy fact share exactly one authority and
one version history (see `UNIFIED-SETTINGS-SURFACE-R1.md` §1, added after
`cx.deepthink`'s Final Call review flagged the original draft's
`revision` field as an implicit second CAS surface).

## 3. Evaluation and routing semantics

For a measured window `[window_start, reset_at]`:

```text
target_at             = min(window_start + effective_period_days, reset_at)
progress              = clamp((now - window_start) / (target_at - window_start), 0, 1)
planned_used_fraction = target_fraction * progress
delta                 = planned_used_fraction - measured_used_fraction
```

`PacingAssessment` ∈ `{BEHIND_TARGET, ON_TRACK, AHEAD_OF_TARGET,
TARGET_HELD, PACING_EVIDENCE_UNAVAILABLE}`, carried with policy revision and
evidence references. Routing reads the assessment to adjust
eligibility/weight and records the exact assessment in `RouteDecision`; it
never writes quota measurement or health state.

**Terminal-unescape safety valve:** if every pool in a family is
simultaneously `AHEAD_OF_TARGET`/`TARGET_HELD` (all throttled) and a request
still arrives, routing MAY bypass the `TERMINAL` pool's pacing constraint —
never a `NON_TERMINAL` pool's — provided its raw `measured_used_fraction <
1.0`, and MUST emit an explicit `PACING_UNESCAPE_EMERGENCY` record. This
exists so a compressed effective period cannot manufacture a full routing
blackout for the remainder of the real reset window; it does not relax the
80%/100% targets themselves.

Missing, stale, or incomplete window evidence produces
`PACING_EVIDENCE_UNAVAILABLE`, never assumed zero usage or unlimited quota.
A manual (non-automatic) dispatch may bypass pacing but must be auditable
and must never be recorded as if it complied with the pacing policy.

## 4. Limits and authority boundaries

This is a pacing target, not a reservation or a guarantee: without
per-request cost evidence and a separately justified, account-scoped
`BudgetReservation` authority, actual consumption can over- or undershoot
100%/80% under concurrent load or delayed observation. Two further limits
are explicitly out of v1 scope:

- **Cross-home oversubscription:** multiple PeerHub homes drawing on one
  provider account can each independently believe they are within pacing;
  no local policy can serialize an account-wide quota without a new,
  separately justified authority.
- **Physical-pool ambiguity:** assigning different ceilings (terminal 100%
  vs non-terminal 80%) to profiles that in fact share one physical provider
  quota pool is invalid configuration, not two independent budgets —
  validation must reject it (§1).

## 5. Required pre-TDD fixtures

Terminal 100%/6.25-day; non-terminal 80%/6.25-day; stale evidence; changed
reset boundary mid-window; multiple concurrent windows; shared-pool role
conflict (validation rejection); policy-revision drift mid-window;
concurrent-home oversubscription (documented as unresolved, not silently
passing); all-pool-throttled safety-valve trigger. Passing a design fixture
must not be represented as live quota evidence.

## 6. Ratification gate

This document, the relevant `ARCHITECTURE.md` revision, the evidence/schema
shapes in §2, and the fixture list in §5 must be bound together by hash in a
new unanimous Hub round before any of this becomes buildable. That round
explicitly excludes source implementation and live policy activation,
consistent with `TDD-READINESS-GATE-R1.md`.

## 7. Provenance

Drafted independently by `ag.deepthink` and `cx.deepthink` from a shared
brief (2026-07-29), reconciled in
`QUOTA-SETTINGS-DESIGN-RECONCILIATION-R1.md`. The pacing formula and
evidence-ownership split were convergent between both drafts; the safety
valve in §3 originates from the `ag` draft and was folded in unchanged.
