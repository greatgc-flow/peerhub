# Runtime Health Recovery Addendum R3 — 2026-07-28

## Purpose and precedence

This addendum resolves the concrete AG/CX deepthink red-team findings against
`RUNTIME-HEALTH-RECOVERY-DECISIONS-2026-07-28.md`.  Where they differ, this
addendum governs.  It is Phase 0 design only.

## 1. Separate facts, health, and admission

An observation has one *evidence subject* (`environment`, `root`, `profile`,
or `quota_family`), but multiple independently-derived decisions may use it.
Generic provider 500 evidence opens only the failing profile circuit.  A
quota-family gate requires explicit quota-family evidence, such as a correlated
429/RESOURCE_EXHAUSTED response or authoritative quota telemetry.  It is never
inferred from a generic 500.  Thus GPT-OSS's observed 500 does not close AG's
Gemini or Opus profiles.

Health and per-dispatch admission are separate:

- `health`: current evidence and circuit/quarantine state;
- `admission`: current quota reserve, pacing, task policy, and routing choice.

A healthy profile can be admission-deferred without losing its positive health
receipt.  Pacing/reserve denial neither opens a health circuit nor requires a
recovery action.

## 2. Trust, ordering, and mutation authority

SHA-256 identifies canonical content but does not authenticate its producer.
Only the host probe runner may mint a candidate receipt, after issuing a
single-use nonce and validating adapter/process provenance.  A sandboxed peer
may return raw probe evidence but can neither issue a trusted receipt nor clear
its own gate.  The host-side mutation broker validates and append-only journals
the receipt, then atomically commits a compare-and-swap transition.

Each gate has `gate_generation` and each failure opens an `incident_id`.
Recovery must name both, use a receipt whose `ended_at` is later than the
incident's `closed_at`, match subject and configuration revision, and CAS the
expected generation.  An older success cannot replay over a newer failure.

Every quarantine also has `kind`, `opened_by`, and `required_clearer`.
Receipt-based recovery may clear only an automatically opened health circuit of
the same scope and reason.  Manual, security, policy, administrative, and
consensus-owned quarantines require their designated authority; liveness does
not override them.

## 3. Recovery probing without a safety bypass

`CIRCUIT_OPEN -> RECOVERY_PROBING -> HEALTHY|CIRCUIT_OPEN` is host controlled,
single-flight, and contains no ordinary task work.  It uses the exact production
adapter, argv, model/effort, sandbox, and stable environment revision.  Its
receipt records all attempts, including retries owned by the provider.

Recovery probing is quota-aware but cannot deadlock permanently:

1. run a zero-cost preflight where the CLI supports one;
2. otherwise wait for `next_probe_at` and use one bounded canary only when a
   dedicated recovery allowance or explicit human-authorized allowance exists;
3. the allowance is distinct from terminal and premium reserves and cannot be
   silently borrowed from them; if unavailable, remain `CIRCUIT_OPEN` with an
   observable deferred reason until reset or authority action.

Hub, adapter, and provider retry ownership is explicit in the receipt:
`adapter_attempt`, `provider_attempt_observed`, `retry_owner`,
`next_probe_at`, and `backoff_generation`.  The Hub never immediately duplicates
a failed uncertain task dispatch.

## 4. Stable environment and model identity

The sanitized environment revision includes stable execution-boundary facts:
executable path/hash/version, effective sandbox/elevation mode, selected config
hash, proxy-policy presence/hash, adapter type, and PTY-required class.  It
excludes PID, handles, terminal dimensions, timestamps, and other ephemeral
PTY data.  This detects the CX sandbox regression without continually expiring
AG PTY receipts.

Receipts store `requested_model`/`requested_effort` separately from
`observed_model`/`observed_effort`.  A sentinel proves a successful configured
dispatch; absent correlated provider evidence leaves observed selection
`UNKNOWN`.  Unknown observed selection may establish transport health but may
not claim stronger model-identity validation.

## 5. Additional mandatory tests

1. A generic 500 opens only a profile circuit; a verified 429 family condition
   gates all and only profiles mapped to that family.
2. A receipt predating an incident, or targeting a different generation,
   fails recovery CAS.
3. A liveness receipt cannot clear a manual/security/policy/consensus
   quarantine.
4. Exactly one recovery probe is in flight; ordinary routing remains blocked
   while it runs, and a deferred allowance produces a visible non-routable
   state without mutating health facts.
5. Changing PID or PTY size does not expire a receipt; changing sandbox mode,
   effective config, executable, model, or effort does.
6. An unknown observed provider model is displayed as unknown, never promoted
   to model-identity proof.
