# Runtime health semantics R1

> Status: review draft. Resolves AG deepthink's 2026-07-28 critique of the
> initial freshness/readiness proposal. No package implementation is authorized.

## Evidence that motivated R1

AG's interactive CLI session was authenticated, while a child process inherited
`ALL_PROXY`, `HTTP_PROXY`, and `HTTPS_PROXY` as `http://127.0.0.1:9`. Its
catalog probe therefore failed at the local proxy boundary. Clearing those
variables only for the child process made `agy.exe models` succeed and list the
current catalog. This is neither an authentication failure nor an upstream
provider failure.

## R1 state and outcome model

Readiness is a projection of separately timestamped evidence for executable,
authentication, admission, provider readiness, and usage. It also has these
outcomes:

- `READINESS_STALE`: evidence expired; no current negative evidence inferred.
- `REVALIDATING`: a fenced, single-flight revalidation is in progress.
- `EXECUTABLE_UNAVAILABLE`: configured executable cannot be resolved or is
  incompatible; no network/auth/provider probe may be attempted.
- `ENVIRONMENT_UNAVAILABLE`: the declared child environment cannot establish
  a valid local process boundary, including invalid proxy inheritance, missing
  runtime libraries, or environment policy violation.
- `AUTH_UNAVAILABLE`, `NETWORK_UNAVAILABLE`, `PROVIDER_UNAVAILABLE`:
  distinct negative probe classes after their preceding boundaries were proved.
- `QUOTA_EXHAUSTED` and `RATE_LIMITED`: usage/admission outcomes distinct from
  authentication and provider availability.
- `READY`: all command-required evidence is current for the same sealed
  runtime revision.

There is no generic `REVALIDATION_FAILED` state: the terminal receipt retains
the probe phase and maps to one of the concrete negative outcomes above. An
unknown/unclassifiable failure is `PROBE_INCONCLUSIVE`, never `READY`.

## Sealed runtime revision

The command's `RuntimeRevision` binds canonical host configuration, executable
path plus binary fingerprint, adapter descriptor, and a **sanitized invocation
environment fingerprint**. The environment fingerprint is a canonical digest
of an explicit allowlist only: proxy-routing variables, PATH-resolution result,
working-directory policy, and declared sandbox mode. Raw environment values and
unrelated secrets are never persisted. A probe returning under a non-current
RuntimeRevision is recorded as stale evidence and cannot mutate readiness.

## Revalidation protocol

1. `READINESS_STALE` may authorize an automatic probe only when the adapter
   declares that exact probe as no-provider-effect and non-quota-consuming.
   Otherwise it returns `REVALIDATION_REQUIRED` without dispatching anything.
2. Authorization atomically acquires a per-peer/profile/revision probe lease
   with owner, fencing token, expiry, and idempotency key, then projects
   `REVALIDATING`.
3. Later callers observe that receipt and either wait on its bounded result or
   receive `REVALIDATION_IN_PROGRESS`; they never launch a duplicate probe.
4. A successful probe matching the active RuntimeRevision writes new evidence
   and may project `READY`. A failed probe writes its concrete outcome and
   closes the affected admission gate. A stale/fenced result is audit-only.
5. Administrative recovery can authorize step 1, but cannot clear evidence,
   open a gate, or write `READY` directly.

## Host boundary

PeerHub does not install, update, sign in to, or repair vendor CLIs. It returns
an actionable but non-mutating remediation class. A host repair changes the
runtime configuration or executable fingerprint, requiring a new
RuntimeRevision and fresh evidence before any effect.

## Required controlled fixtures

The HR fixture family must cover: expired evidence; missing executable with no
child/network dispatch; proxy/sandbox environment taint; authentication failure;
network failure after environment validation; quota/rate limit; probe
single-flight contention; fenced late result; revalidation unsupported by an
adapter; and successful matching-revision recovery.
