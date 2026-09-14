# Runtime health drift: freshness is not readiness

> Status: open design issue; requires peer review before Phase 0 closure.
> Scope: legacy-host characterization and PeerHub v1 health semantics only.

## Observed facts

1. AG's legacy `health.json` reported `GREEN`, `authenticated: true`,
   `gate_open: true`, and `entrypoint_ok: true` with `checked_at` at
   `20260728T081129`.
2. The legacy policy marks health stale after 120 minutes. The stored record
   was consequently marked `STALE` at `2026-07-28T10:41:42`, without a new
   provider or authentication observation.
3. A zero-token local CLI check passed: `agy.exe --help` returned exit 0.
4. A zero-token catalog/authentication check failed: `agy.exe models` returned
   exit 1 with `Please sign in to view available models.` Therefore the stored
   authentication fact was stale and must not authorize an AG dispatch.
5. Legacy `action_peer_recover()` writes `GREEN`, opens the gate, clears
   failures, and writes `last_success_at` without first running an
   authentication or provider-readiness probe. This conflicts with the
   existing PeerHub Phase 0 requirement that recovery authorizes a probe and
   never manufactures healthy evidence.

## Problem statement

The old single status conflates at least two distinct conditions:

- **freshness expiration**: a past observation aged beyond its policy window;
- **runtime unavailability**: an executable, authentication, authorization,
  or provider-readiness failure observed now.

Treating every freshness expiry as an automatic routing exclusion is too
coarse. Treating an administrative recovery as proof of health is unsafe.
The AG incident exhibits both mistakes: the initial `STALE` label lacked a
current failure reason, while an actual no-spend authentication probe then
proved that the old `authenticated` fact could not be trusted.

## Proposed PeerHub v1 rule

Persist independently timestamped evidence for `executable`, `authenticated`,
`admitted`, `provider_ready`, and `usage` rather than deriving a single
mutable health flag. Every evidence item records its probe method, scope,
configuration revision, observed time, expiry, and redacted failure class.

`READINESS_STALE` means only that a required observation has expired. It is
not equivalent to `UNHEALTHY` and does not erase the last evidence. A policy
may admit a zero-cost revalidation attempt before routing, but it must not
dispatch a paid/provider effect until the required current evidence is
present. A verified negative probe (such as AG's sign-in failure) produces
`AUTH_UNAVAILABLE`, closes admission, and retains the probe receipt.

`recover` becomes `authorize_recovery_probe`: it may create a recovery attempt
and its fencing/idempotency identity, but cannot write `HEALTHY` or reopen
admission. Only a successful current probe under the same runtime/configuration
revision may produce a new open/readiness receipt.

## Phase 0 implications

- Preserve the legacy stale and blind-recover behaviors as characterization
  evidence; do not carry either behavior into PeerHub v1.
- Add a controlled fake-adapter fixture for expiry, negative authentication
  probe, authorized recovery probe, and successful recovery receipt. This
  refines HR-02 through HR-06 without weakening their required outcomes.
- Until AG is interactively signed in, it cannot provide a peer verdict. The
  missing AG review is an external runtime blocker, not an implied approval.
- CX is independently unavailable after its transport failure, so no peer or
  CC ratification may be claimed from this incident.

## Review questions

1. Which evidence facets are mandatory for a routing admission versus a
   consensus voter snapshot?
2. Is `models` an acceptable zero-cost authentication/readiness probe for AG,
   or must the host expose a narrower declared probe?
3. What expiry policy is appropriate for each facet, and when may a revalidation
   attempt be automatic?
4. Should a failed authentication probe quarantine only the affected profiles
   or the root peer?
