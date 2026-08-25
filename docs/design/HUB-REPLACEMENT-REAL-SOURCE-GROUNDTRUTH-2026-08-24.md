# Real peerhub Source — Ground Truth for Reconciling Gaps 1-7 (2026-08-24)

Status: terminal-authored, direct file reads of `P:\workspace\peerhub\peerhub\`
(the terminal has direct filesystem access to this repo; `cx`'s sandbox
repeatedly could NOT reliably access it this session — sometimes read
`_sys/docs-v2/` fine, never confirmed reading `peerhub/` source, per every
gap draft's own "evidence boundary" section). This doc exists so future
dialectical rounds can be grounded in real code pasted into the prompt,
the same way gap-2's consensus rules were resolved against real
`protocol.md` text, instead of `cx` guessing at peerhub internals.

**Headline finding: peerhub's real architecture is substantially more
developed and more specific than gaps 1-7 assumed. The gap drafts should
be RECONCILED against this real machinery, not treated as parallel
designs to build from scratch.** This is good news for the "does peerhub
sufficiently cover hub.py" question — much of the low-level substrate
already exists — but it also means several gap-draft proposals need
correcting to reuse real names/shapes instead of inventing new ones.

## Real module map (`peerhub/`)

`adapters/`, `application/`, `builtins/`, `core/` (protocol envelope,
identity, errors, evidence, execution, ports, context), `dispatch/` (18
files — admission, artifact_coordination, artifacts, attempt_lifecycle,
capability, capability_policy, completion, heartbeat, materializer,
process, retry_authorization, session_lease, tree_controller,
unit_of_work), `events/` (contract only — thin), `governance/` (broker,
contract, mutations), `health/` (contract, model, service), `persistence/`,
`routing/` (contract, model, service), `state/` (contract — StateStore/
UnitOfWork abstraction), `telemetry/`.

Also `docs/compatibility/peer-cli-contracts.toml` +
`peer-cli-observations.md` — **NOT related to hub.py compatibility**;
these document empirically-verified facts about the EXTERNAL vendor CLIs
(agy.exe/claude.cmd/codex.cmd) that peerhub's own adapters call out to.
Do not confuse this with a hub.py-facing compat layer.

## `peerhub/core/protocol.py` — CONFIRMS gap-1's envelope proposal exactly

Real constants: `PROTOCOL_MAJOR = 1`, `PROTOCOL_MINOR = 0`,
`SCHEMA_VERSION = "1.0.0"`. Real `ErrorCode` enum already has ~20+ stable
codes (`PROTOCOL_VERSION_MISMATCH`, `SCHEMA_VERSION_UNSUPPORTED`,
`MALFORMED_ENVELOPE`, `PEER_UNAVAILABLE`, `PROFILE_UNAVAILABLE`,
`ROUTE_EXHAUSTED`, `ADMISSION_CLOSED`, `IDEMPOTENCY_HIT`,
`MISSING_IDEMPOTENCY_KEY`, etc.). `OperationalFailureCategory` enum
(`EXECUTABLE_UNAVAILABLE`, `ENVIRONMENT_UNAVAILABLE`, `AUTH_UNAVAILABLE`,
`NETWORK_UNAVAILABLE`, `PROVIDER_UNAVAILABLE`, `QUOTA_EXHAUSTED`,
`RATE_LIMITED`). **Gap-1's proposed envelope needs no redesign — reuse
these real names verbatim, don't invent parallel ones.**

## `peerhub/health/model.py` + `contract.py` — far more developed than gap-4's sketch

Real `HealthStage` pipeline (6 explicit stages, in order):
`RESOLVE_EXECUTABLE → VALIDATE_ENVIRONMENT → AUTHENTICATE →
CONNECT_NETWORK → CALL_PROVIDER → CHECK_USAGE_ADMISSION`, each mapped to
an `OperationalFailureCategory`. Real `AdmissionState` with explicit
precedence: `OPEN(0) → PROBE_AUTHORIZED(1) → RECOVERY_REQUIRED(2) →
COOLDOWN(3) → QUARANTINED(4)`. Real types: `CircuitState`,
`QuarantineAuthorityClass`, `RecoveryProbeAuthorization/Grant/Receipt`,
`ReadinessEvaluation/State/GateState`, `PolicyAction`, `PolicyReceipt`,
`CooldownEvaluation`, `AutomaticClearanceResult`.

**Gap-4's proposed `NodeRegistry`/`PeerHealth` split with a generic
`quarantine: NONE|AUTO_QUARANTINED|OPERATOR_QUARANTINED|DISABLED` state
machine is a plausible-sounding but DIFFERENT shape than the real 5-state
`AdmissionState` + 6-stage `HealthStage` pipeline + explicit
`QuarantineAuthorityClass` concept.** This needs a dedicated reconciliation
round: does gap-4's design map cleanly onto the real `AdmissionState`
precedence order, or does it need to be rewritten around it? The real
`CHECK_USAGE_ADMISSION` stage likely already covers what gap-4 called
"quota/EXH tracking."

## `peerhub/dispatch/session_lease.py` — SessionLeaseCoordinator already IS a fenced-lease substrate

Real `SessionLeaseCoordinator` with `create_lease`, `renew_lease`,
`close_lease`, `expire_and_recover_lease`, `validate_lease_fence` — and
real request/snapshot types `LeaseCreateRequest`, `LeaseRenewRequest`,
`LeaseCloseRequest`, `LeaseFenceCheckRequest`, `LeaseSnapshot`,
`SessionBindingKey`, `SessionBindingSnapshot`, `RecoveryReceipt`,
`RecoveryTrigger`.

**This is almost exactly gap-3's proposed "fenced terminal-duty lease"
concept, already built — for sessions specifically.** Gap-3/gap-4's open
question #1 ("does peerhub already have a concrete schema/event naming
convention for this area") is answered: yes, largely, via
`SessionLeaseCoordinator`. Open question for reconciliation: is
"terminal duty" a distinct lease TYPE using this same coordinator
(scoped differently, e.g. by room instead of by session), or does it need
its own coordinator? Gap-4's `DutyAssignment` (leadership/roles) likely
needs the same question answered.

## `peerhub/events/contract.py` — confirms append-only event log pattern, but it's thin

Real `EventLogRecord{envelope: EventEnvelope, appended_at, outbox_position}`
and `ConsumerOffset{consumer_id, outbox_position, event_id, revision}`.
Confirms gaps 2-6's "append-only events + materialized projections"
architectural assumption is correct in direction. But this contract file
is thin (just these 2 DTOs) — the actual event TYPES gap-2/3/4/5/6 each
invented (`ConsensusRoundProposed`, `LeaseAcquired`, `LessonActivated`,
etc.) do not appear to exist yet as real code; only the generic envelope
mechanism they'd ride on does.

## `peerhub/governance/broker.py` — a REAL generic mutation broker, more general than gap-6 assumed

Real `MutationRequest/Plan/Submission/Disposition`, `EffectOutcome/Receipt`,
`OutboxEvent/State`, `TargetState`, `TransitionReceipt`,
`RecoveryDisposition`, with idempotency-payload-mismatch and
exclusive-claim-conflict error types already defined. This is a
domain-agnostic "propose a mutation, get idempotent outbox-backed
execution with recovery" broker — NOT specific to consensus or
governance-artifacts. **Gap-2's consensus-round mutations and gap-6's
directive/lesson/proposal mutations should likely be expressed as USES of
this existing broker, not parallel event-sourcing machinery invented
per-category.**

## `peerhub/routing/model.py` — explicitly, honestly scoped-down today

Real module docstring: *"This slice supports only boolean eligibility,
fixed unit weights, and deterministic equal-weight selection. Cost,
latency, terminal-tier weighting, admission-snapshot drift detection, and
stale-decision repair are deliberately outside this module's current
scope."* This is a disclosed, current real limitation — relevant to
gap-4's "registration and discovery" section, which speculated about
richer routing/candidate-selection logic that does not yet exist.

## What this changes about the 7-category effort

1. **Priority order gets a new #0** (before gap-1's revised #1 "native
   replacement completeness"): **reconcile every gap draft against this
   real source**, file by file, before resolving each category's own
   open-questions list — several of those questions ("does peerhub
   already have X") are now partially answerable from real code, and
   several gap proposals need renaming/restructuring to reuse real types
   instead of inventing parallel ones.
2. This is NOT a "start over" finding — the *directional* architecture
   (append-only events, versioned envelope, fenced leases, generic
   mutation broker) that `cx` converged on independently matches the real
   code well. The gap is in the SPECIFIC shapes/names within each
   category, not the overall approach.
3. Given `cx`'s sandbox cannot reliably read `peerhub/` source directly,
   future reconciliation rounds must have the relevant real code pasted
   into the dispatch prompt (as this doc does), the same discipline
   already used for gap-2's `protocol.md` grounding.
