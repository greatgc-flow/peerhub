# Runtime Health Recovery Decisions — 2026-07-28

## Evidence and classification

The live sweep in `RUNTIME-PROFILE-VERIFICATION-2026-07-28.md` proves that
AG standard/effort/deepthink/opus and CX standard/effort/deepthink completed
fresh Hub dispatches.  AG GPT-OSS reached its provider but received retried
`INTERNAL 500` / `model unreachable`; it is a transient, profile-scoped
provider failure, not authentication, quota exhaustion, or an AG-root fault.
The earlier CX TLS issue was an `unelevated` Windows sandbox execution-boundary
condition; a current elevated-sandbox host probe and all three CX dispatches
prove recovery.

## Decisions

1. Health evidence has exactly one scope: `root`, `profile`, `quota_family`, or
   `environment`.  A result may only close the gate at the scope it proves.
2. `GREEN` is an effective derived state, not an independent label.  It requires
   enabled configuration, a current positive receipt, no effective quarantine,
   and current quota/pacing admission.  Status display and routing use the same
   reducer.
3. Every probe creates an immutable canonical JSON receipt with a deterministic
   SHA-256 identity, subject, timestamps/expiry, executable/config revision,
   exact model/effort, sanitized environment revision, result/error class, and
   transcript hash.  Do not claim cryptographic signatures before multi-host
   key management exists.
4. Recovery names matching, current positive receipt ids and is atomic.  It
   rejects stale, failed, mismatched profile, or config-revision-mismatched
   receipts.  PeerHub has no blind `peer-recover` operation.
5. A provider 500 opens a profile circuit with backoff and jitter.  It blocks
   automatic routing for that profile only and never causes an immediate
   duplicate retry.  A root gate requires root adapter/auth/transport evidence.
6. Fresh dispatch success supersedes `UNMEASURED` or stale catalogue metadata
   for current availability only.  Metadata freshness remains a separate signal.
7. Probing is quota-aware: zero-cost CLI/auth/catalog preflight first; a minimal
   model canary only for configuration drift, route-critical staleness, or an
   allowed schedule.  Canary admission honors quota reserves and pacing.

## Mandatory conformance cases

- `GREEN` with an effective quarantine is rejected and never routed.
- Recovery rejects expired, failed, mismatched-subject, and mismatched-config
  receipts; a matching receipt clears only its matching gate.
- An `ag.gptoss`-style 500 leaves AG's Gemini and Opus profiles routable.
- A root adapter/auth failure blocks descendants until a root-scoped positive
  receipt is observed.
- Model, effort, executable, sandbox, or sanitized environment changes expire
  old positive receipts.
- A canary is denied if it would violate a quota reserve or pacing gate.

## Legacy gap retained as evidence

On 2026-07-28, legacy `health-update GREEN` did not clear
`availability.quarantined`, so routing was RED while status showed GREEN.  A
single GPT-OSS provider error also quarantined the AG root.  The evidence-backed
manual recovery used that day restored operations, but is not the PeerHub
contract and must not be replicated by future automation.
