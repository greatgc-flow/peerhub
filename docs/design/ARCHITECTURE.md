# peerhub — Target Architecture (v1, pre-TDD)

> Status: converged design, **not yet implemented**. This is the canonical
> deliverable of the 2026-07-27 `ag`/`cx`/`cc` architecture debate
> (3 rounds, full convergence — process record in
> `docs/design/peerhub-architecture-debate.md`). No code, tests, or
> scaffolding exist yet. A future, separately-authorized round starts TDD
> implementation against this document, beginning with Phase 0 below.

## 1. Mission

`peerhub` is a standalone, installable Python package (`pip install
peerhub`) that coordinates multiple AI CLI agents (Claude Code, Codex,
Antigravity, and future peers) as collaborating peers: dispatch, routing,
consensus, health/quarantine, session/lease lifecycle, IPC, and brokered
mutations. It is designed to eventually fully replace the
communication/coordination responsibilities currently implemented inside
`_sys/core/hub.py` in the portable-dev-env ("Engram") project.

It is grounded in two sources: `hub.py`'s real, battle-tested behavior
(what to preserve) and the shelved 2026-07-20 Engram-refactor-blueprint's
clean-room target design (what to aspire to) — reconciled against three
concrete defects already tracked in the portable-dev-env backlog (T87, T88,
T89), which this architecture is designed to make structurally impossible,
not just "fixed this once."

## 2. Module structure

```text
peerhub/
  __init__.py                 # public Client, typed public values, version only
  client.py                   # thin in-process/remote client; never direct storage
  runtime.py                  # immutable RuntimeContext and composition root

  core/
    api.py                    # canonical application facade (the ONE mutating entrance)
    context.py                 # PathLayout & RuntimeContext
    protocol.py                 # wire protocol & command/event schemas
    errors.py                   # stable machine error codes

  state/
    contract.py                 # StateStore / UnitOfWork interfaces — domain depends on THIS, not sqlite3
    sqlite.py                    # SQLite v1 transactional backend (local filesystem, WAL mode, atomic outbox)
    migrations/

  adapters/
    contract.py                  # PeerAdapter + optional capability protocols (UsageProvider, SessionCapability)
    registry.py                   # descriptor/config resolution, adapter-conformance/v1 validation
    builtins/
      claude.py                   # ClaudeAdapter (cc)
      codex.py                     # CodexAdapter (cx)
      antigravity.py                # AgyAdapter (ag)

  dispatch/
    model.py                       # PURE request/attempt/session/lease transition reducers
    service.py                      # orchestration across adapter, runner, store
    process.py                       # process-supervisor port/types (pipe/PTY)
    pipe.py                           # concrete pipe runner
    pty.py                             # concrete PTY runner
    artifacts.py                       # staged input/output ArtifactMaterializer

  routing/
    model.py                            # PURE RouteDecision reducer
    service.py                           # capacity/EXH-aware routing service

  consensus/
    model.py                              # PURE round/electorate/vote/final-opinion reducer
    service.py                             # R:10 protocol orchestration, arbiter invocation

  health/
    model.py                                # PURE availability/admission/quarantine reducers
    collectors.py                            # dependency-declared telemetry fan-out (T87 fix)
    service.py                                # health/recovery orchestration

  ipc/
    commands.py                                # versioned command envelopes
    events.py                                   # versioned event/evidence envelopes
    jsonl.py                                     # framed JSONL transport + version negotiation
    cli.py                                        # CLI translation to the same command bus

  governance/
    mutations.py                                  # MutationRequest / MutationPlan / TransitionReceipt
    broker.py                                      # governed mutation broker (CAS, journal, effect workers)
    proposals.py                                    # fingerprint-deduplicated proposal engine (T89 fix)

  testing/                                          # published adapter-conformance kit — not runtime policy
```

### 2.1 Ownership rules

1. **`core.protocol` owns compatibility, not behavior.** Schemas, correlation, version negotiation, stable error codes. Cannot import `state`/`adapters`/infrastructure internals.
2. **`*/model.py` modules are pure.** They accept values and return transition decisions/effect intents. No file I/O, no clock reads, no environment access, no vendor state.
3. **`core.api` is the only mutating entrance.** CLI, JSONL, and the embedded `Client` all submit the same typed commands, so every authorization, idempotency, state-transition, and audit rule is transport-independent.
4. **`adapters` translates; it does not coordinate.** Adapters plan invocations and decode vendor output. They cannot spawn processes, select peers, modify health, persist sessions, acquire leases, append audit events, or decide task success.
5. **Infrastructure (runners, `state.sqlite`) executes plans, not policy.** A PTY runner can emit chunks and terminate a process tree, but cannot decide to retry, quarantine, or invoke an arbiter.
6. **`RuntimeContext` is immutable dependency injection**, constructed once per process — not a mutable global.
7. **Concrete peer IDs stay in `adapters.builtins` registration/config.** Core code selects capabilities and descriptors, never `if peer == "cx"`.
8. **Vendor installation is outside the engine.** A descriptor supplies a configured executable reference; readiness canonicalizes and probes it. `peerhub` does not install, update, authenticate, or bundle vendor CLIs (this boundary is permanent — see the portable-dev-env's own 2026-07-27 T82 re-scope, which drew the identical line from the other side).

## 3. Hosting model

**Resolved (Round 2-3): no resident OS daemon in v1.**

Two deployment forms, same command/event schema as the compatibility surface in both:

- **Embedded**: `from peerhub import Client` — `Client` invokes `core.api` directly in the same Python process. This is the default, CLI-first mode.
- **Foreground service**: `peerhub serve --stdio` hosts the same runtime for a longer JSONL session (multiple correlated commands over one connection), for the lifetime of that connection only. Not installed or managed as an OS service.

Multiple independent CLI/stdio host processes coordinate safely through the transactional store (unique idempotency keys, owner-aware leases with process-birth identity, durable outbox events, startup recovery sweeps) — this covers concurrent dispatches without needing a singleton broker. A resident daemon is deliberately deferred: add one only when a measured requirement for continuous cross-client event subscription (not just concurrent dispatch) actually appears — symmetric-deferral rule, same as the shelved blueprint's own process discipline.

MCP is not a foundation; if added later, it is a translation adapter over this same command/event service.

## 4. Authoritative state

**Resolved (Round 2-3): `StateStore`/`UnitOfWork` interface in `state/contract.py`; SQLite (`state/sqlite.py`) is the supported v1 backend.** Domain code depends on the interface, never on `sqlite3` directly.

One SQLite database per configured `PeerHubHome`:

- Request, request-attempt, process-lease, and session-binding records.
- Health observations and current health/admission projections.
- Routing decisions.
- Consensus rounds, electorate snapshots, votes, and arbiter opinions.
- Proposal finding sets, trigger cursors, and proposal lifecycles.
- Mutation requests, receipts, effect intents, and an event outbox.
- Optional budget reservations.

Large transcripts, staged prompts, and output artifacts remain files referenced by content digest and length — not embedded in the database.

Required store properties: short `BEGIN IMMEDIATE` write transactions with revision/CAS checks; unique constraints for command idempotency, one vote per voter/round, one active proposal per dedup identity, one live session binding per scope; state transition + outbox event committed atomically; immutable evidence rows, mutable projections always carrying a revision; no transaction held while waiting on a peer/provider/filesystem/network call; recovery derived from authoritative records and effect intents, never inferred from log text alone.

This is intentionally a local, not distributed, design. SQLite removes the bespoke JSON-file locking that caused T83, and directly supplies the atomicity/uniqueness the observed defect classes (T83, T89) need. **The DB must be enforced on a local filesystem** (not SMB/NFS) — v1 fails startup if its lock/transaction probe fails rather than claim unmeasured safety on network filesystems (`TEST NEEDED` if that support matrix is ever wanted).

A future non-SQLite backend is acceptable only if it passes the identical multi-process/crash-boundary/uniqueness/state-plus-outbox test suite as SQLite — v1 ships exactly one backend, not two.

## 5. Public command, event, and error contract

All commands carry: `protocol_version`, `command_id` (caller-generated idempotency key), `correlation_id`, `client_id`, `workspace_scope`, `expected_policy_revision?`, `method`, `params`.

All events carry: `protocol_version`, `event_id`, `correlation_id`, `request_id?`, `round_id?`, `sequence` (monotonic within the correlated stream), `occurred_at`, `kind`, `payload`, `evidence_refs[]`.

A client negotiates `initialize` before effects. Major versions may break; minor versions are additive. Unknown required fields, unsupported versions, truncated frames, duplicate IDs with different content, and invalid transitions all return stable typed errors before dispatch.

Error envelope:

```text
code                     # e.g. PEER_UNAVAILABLE, REVISION_CONFLICT
phase                    # validation/admission/pre_spawn/post_spawn/assessment/effect
execution_certainty      # NOT_STARTED/MAY_HAVE_STARTED/STARTED/TERMINAL
retry_disposition        # SAFE/UNSAFE/CONDITIONAL/NEVER
message
details                  # versioned, machine-readable
correlation_id
```

Exception class names and vendor prose are diagnostic details, never control flow. The core converts infrastructure exceptions exactly once, at the application boundary.

## 6. Core peer contract

### 6.1 Descriptor

```python
@dataclass(frozen=True)
class PeerDescriptor:
    adapter_id: str
    adapter_version: str
    peer_kind: str
    profiles: tuple[ProfileDescriptor, ...]
    transports: frozenset[TransportKind]     # PIPE, PTY
    capabilities: frozenset[Capability]      # SESSION, STREAM, GRACEFUL_CANCEL...
    usage_provider_id: str | None
    readiness_probe_id: str
```

Profile, account, and quota-pool identifiers are data, never inferred from peer names. Registry loading validates uniqueness and referential integrity before a runtime is admitted. A descriptor declaring a capability it doesn't actually implement is a load-time error, not a runtime surprise.

### 6.2 `PeerAdapter`

```python
class PeerAdapter(Protocol):
    descriptor: PeerDescriptor

    def prompt_policy(self, profile: ProfileDescriptor) -> PromptPolicy: ...

    def plan_invocation(
        self,
        request: AdapterRequest,
        profile: ProfileDescriptor,
        session: SessionHint | None,
        limits: TransportLimits,
    ) -> InvocationPlan: ...

    def new_decoder(self, plan: InvocationPlan) -> OutputDecoder: ...

    def interpret_output(
        self,
        plan: InvocationPlan,
        process: ProcessTerminalEvidence,
        raw_chunks: Sequence[bytes],
    ) -> ProtocolAssessment:
        """Vendor-protocol evidence ONLY (malformed/truncated framing, empty
        response, vendor error, progress-without-terminal marker, suspicious
        delegation marker). MUST NOT decide task fulfillment — see §9."""
        ...
```

**`AdapterRequest`**: the already-authorized request ID, prompt content/reference, workspace scope, profile, requested session policy, and an optional caller-supplied `CompletionContract` — the adapter never evaluates that contract itself.

**`InvocationPlan`**: immutable argv tokens, cwd policy, environment delta, transport kind, stdin payload, timeout/silence policy, redacted display form; declarative `ArtifactSpec` values (content bytes/reference, SHA-256, expected length, access mode, lifecycle) with artifact-path placeholders rather than adapter-created files; optional session action `NONE | CREATE | RESUME`; optional graceful-cancel recipe (the process supervisor still owns escalation/tree termination); no shell command string, no ambient environment capture.

A central `ArtifactMaterializer` replaces placeholders, creates files with create-new semantics, verifies digest/length round trips, records ownership, and deletes only after the supervised process tree is terminal — preserving the real Antigravity oversized-prompt staging need without adapters owning unmanaged filesystem effects.

**`OutputDecoder`**: per-invocation mutable parsing state (not a singleton), consumes ordered stdout/stderr/PTY chunks, emits typed progress/assistant-text/session-identity/usage-hint/vendor-error/completion-marker evidence, never writes state or calls another service, bounded-memory with transcript spill to core artifact infrastructure.

### 6.3 Optional `SessionCapability`

```python
class SessionCapability(Protocol):
    def fingerprint(self, profile: ProfileDescriptor) -> str: ...
    def validate_resume_hint(self, hint: SessionHint) -> ResumeDisposition: ...
```

Session persistence is core-owned. If a vendor doesn't emit a strongly correlated session identity, the result is `UNKNOWN` and automatic reuse stays disabled — time-based/modification-time discovery (today's Antigravity fallback) must never count as a verified binding under concurrency.

### 6.4 Separate `UsageProvider`

```python
class UsageProvider(Protocol):
    descriptor: UsageProviderDescriptor
    def collect(self, query: UsageQuery, deadline: Deadline) -> UsageEvidence: ...
```

`UsageEvidence` is never `{}`. It carries an explicit state (`MEASURED | ABSENT | UNAVAILABLE | ERROR | STALE`), source tag, provider version, observed/captured timestamps and freshness, scope, numeric values only when measured, error category/retry hint when not measured, and a raw evidence reference. No-provider is valid and explicit. A provider failure can never change a peer invocation's result. Providers capable of blocking I/O run in a supervised worker process (a timed-out Python thread is not real containment).

### 6.5 Readiness

Separate probe contract, because adapter conformance and vendor-executable readiness are different evidence: adapter conformance proves the Python adapter against fixtures; dependency readiness proves the configured executable's identity/version/capabilities right now; a mutable readiness binding references the current immutable receipts. The engine canonicalizes and probes the configured executable — it neither installs nor updates it.

## 7. State machines

### 7.1 Request lifecycle

```text
RECEIVED
  -> REJECTED_VALIDATION
  -> ADMITTED
       -> REJECTED_POLICY
       -> PREPARED
            -> FAILED_PRE_DISPATCH
            -> DISPATCH_INTENT
                 -> START_UNCERTAIN
                 -> RUNNING
                      -> CANCELLING
                      -> ASSESSING
                           -> SUCCEEDED_VERIFIED
                           -> DELIVERED_UNVERIFIED
                           -> INCOMPLETE
                           -> FAILED
                      -> INTERRUPTED
                      -> CANCELLED
                 -> INTERRUPTED
```

`command_id` creates or returns one request (idempotency conflict on different content). Admission freezes peer/profile, route decision, policy revision, completion contract, optional budget reservation. `PREPARED` means invocation+artifacts validated, no peer has run. **`DISPATCH_INTENT` and lease ownership commit before spawn — this is the replay-safety boundary.** After spawn, process-birth identity commits in `RUNNING`; a crash between spawn and that commit is `START_UNCERTAIN`, never auto-retried. Stream/progress events never change the authoritative outcome by themselves. A retry is a new `Attempt` under the same request, its own lease, allowed only when the prior attempt is proven pre-dispatch or the request's replay policy explicitly permits it. Every terminal transition emits an outbox event in the same transaction.

### 7.2 Lease lifecycle

```text
RESERVED -> ACTIVE -> RENEWED (self-transition, revision++) -> RELEASED
                    -> EXPIRED -> FENCING -> FENCED | IDENTITY_MISMATCH
                    -> OWNERSHIP_LOST
         -> ABANDONED_PRE_SPAWN
```

Keyed by a cryptographically strong lease ID, linked to request/attempt ID, peer/profile, coordinator epoch, owner instance, heartbeat/expiry, **and process identity (PID + process-creation time, not PID alone — a T83-adjacent gap in today's `hub.py`, where sweep can kill a live PID after a reused-PID expiry)**. `RESERVED` created atomically with `DISPATCH_INTENT`. Renew/close require lease ID + owner instance + coordinator epoch + expected revision; process actions additionally require birth-identity match. Expiry is an ownership fact, not proof the child is dead — recovery verifies identity first: a verified-live expired child is fenced from publishing success then terminated; a dead matching child becomes `INTERRUPTED`; an identity mismatch is quarantined for review and never killed.

### 7.3 Session-binding lifecycle

```text
ABSENT -> CREATING -> ACTIVE | UNKNOWN
ACTIVE -> IN_USE -> ACTIVE | SUSPECT | RETIRED
ACTIVE -> STALE | RETIRED
SUSPECT -> VERIFYING -> ACTIVE | RETIRED
UNKNOWN -> VERIFYING -> ACTIVE | RETIRED
```

Binding key is `(workspace_scope, peer_id, profile_id, conversation_scope)`, not merely peer ID. Only `ACTIVE` can be reused; resume requires adapter fingerprint + readiness binding + profile + session generation to match; an interrupted/start-uncertain request moves the session to `SUSPECT`/`UNKNOWN`, never straight back to active; only correlated vendor evidence verifies it; session state updates only after request assessment by `core.api`, never by the adapter.

## 8. Consensus state machine

```text
DRAFT -> VOTING -> DECIDING -> APPROVED | REJECTED | ESCALATED
                             -> ARBITRATION_PENDING -> OVERRIDDEN_APPROVE | OVERRIDDEN_REJECT | ESCALATED
                  -> EXPIRED -> ESCALATED
```

Creating a round atomically freezes: full electorate, policy revision + collaboration rate, decision rule + minimum participation, risk classification + whether arbiter override is permitted, health/readiness evidence for every voter (**without deleting any voter** — the `required_voters` reduction bug in today's `hub.py:7619-7701`, where an unavailable voter can silently shrink the unanimity denominator, must not recur), deadline, proposer + subject digest, round revision.

`(round_id, voter_id)` is unique; identical resubmission is idempotent, a different second vote is rejected; votes are immutable evidence; the decision reducer is pure over the frozen contract + votes; arbiter invocation is a separate application transition after the base result commits (never inside the consensus transaction); the base result and any later override are both retained, never overwritten.

## 9. Outcome model (T88 structural fix)

**Resolved (Round 2-3).** Three distinct outcome layers, never conflated:

```python
@dataclass(frozen=True)
class AskResult:
    execution: ExecutionOutcome
    protocol: ProtocolAssessment
    completion: CompletionAssessment
    effective_status: AskStatus

class ExecutionOutcome:
    started: bool
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    execution_certainty: ExecutionCertainty   # NOT_STARTED/MAY_HAVE_STARTED/STARTED/TERMINAL

class ProtocolAssessment:                     # produced by PeerAdapter.interpret_output — §6.2
    parsed: bool
    response_present: bool
    vendor_completion_marker: bool | None
    suspected_truncation: bool
    protocol_failure: ProtocolFailure | None

class CompletionAssessment:                   # produced centrally, NEVER by an adapter
    state: VERIFIED | INCOMPLETE | UNVERIFIED | NOT_APPLICABLE
    failed_requirements: tuple[RequirementFailure, ...]
    evidence_refs: tuple[EvidenceRef, ...]
```

1. Execution: did the process start, time out, crash, exit with code N?
2. Peer-protocol: did the adapter parse a valid response/completion marker/session result? (adapter's job, via `interpret_output`)
3. Task: did the response satisfy the caller's `CompletionContract`? (core's job, via a central `CompletionAssessor` — artifact existence + digest, schema validation, required fields, an optional caller verifier, or an explicitly-accepted vendor-native completion receipt)

Exit 0 + nonempty text establishes at most `DELIVERED_UNVERIFIED` — never `SUCCEEDED_VERIFIED` by itself. For unconstrained prose with no verifiable criterion, `DELIVERED_UNVERIFIED` is the honest terminal state, not a failure. A short-response/delegation-marker heuristic is `SUSPICIOUS` evidence only, never the success/failure rule. Automatic retry of an incomplete/unverified attempt stays unsafe unless the caller declared the task replay-safe (mirrors `hub.py`'s own existing post-dispatch no-auto-retry rule).

This directly closes the adapter no longer being asked to do something it structurally cannot know (whether unrelated task semantics were fulfilled) while still capturing everything an adapter legitimately CAN observe (vendor protocol/framing evidence).

## 10. Health and quarantine

Two orthogonal projections over immutable observations, replacing today's overlapping `context_health.status`/`gate_open`/`quarantined`/cooldown fields that can currently contradict each other:

**Availability** ("what do current evidence sources establish"): `UNKNOWN -> PROBING -> HEALTHY | DEGRADED | UNAVAILABLE`, any measured state ages to `STALE` on freshness expiry, `STALE -> PROBING`.

**Admission/quarantine** ("may new work be routed here", policy derived from availability + rate limits + failure category + integrity evidence + administrative action): `OPEN -> COOLDOWN -> RECOVERY_REQUIRED | OPEN`; `OPEN -> QUARANTINED -> RECOVERY_REQUIRED -> PROBE_AUTHORIZED -> OPEN | QUARANTINED`.

A rate-limit reset ends `COOLDOWN` only, not general health. **Administrative "recover" authorizes a probe — it does not write HEALTHY/GREEN directly** (today's `hub.py:3501-3524` does the latter, erasing the distinction between "attempt recovery" and "measured proof of readiness"). Reopening requires a fresh successful probe tied to the current executable/readiness/adapter fingerprint. Routing consumes one frozen `AdmissionSnapshot`; it never mutates health during selection. Stale/unavailable evidence is never fabricated as healthy.

## 11. Routing and budget

Pure decision over requested capabilities/profile constraints, readiness binding, admission snapshot, measured usage/headroom evidence, in-flight reservations, terminal exclusion/cost policy, task-size/context requirements, and a deterministic request ID. Returns a durable `RouteDecision`: all candidates, exclusions with reason+evidence, representative profiles, effective weights, seed/draw if stochastic, selected target, policy revision — matching the audit shape `hub.py`'s current routing already demonstrates is achievable. Missing usage is `ABSENT`/`UNAVAILABLE`, never zero.

Optional `BudgetReservation` authority (only if enabled — `peerhub` must support an explicit no-budget mode, never a fake provider): `RESERVED -> DISPATCH_INTENT -> LAUNCHED -> CONSUMED | CONSUMED_UNKNOWN`; `RESERVED -> RELEASED` only if definitively pre-dispatch. The usage provider reports evidence; it cannot itself reserve/release budget. A crash after dispatch intent conservatively becomes `CONSUMED_UNKNOWN`.

## 12. Governed mutations and brokered effects

```text
MutationRequest -> authorized + domain validated + expected-revision checked
                 -> MutationPlan
                 -> atomic state transition + receipt + outbox/effect intents
                 -> TransitionReceipt(COMMITTED_ENFORCEMENT_PENDING)
                 -> effect worker
                 -> TransitionReceipt(COMPLETED | EFFECT_FAILED)
```

`MutationRequest` carries operation/command/correlation IDs, actor/client attribution, policy revision, target record ID, expected record revision, typed desired transition. Authorization + domain-invariant validation happen before plan commit; the transaction commits the new authoritative record, immutable receipt, and outbox/effect intent together; filesystem/process effects happen after commit and reconcile idempotently; cross-record/cross-workspace operations are explicit sagas, not claimed distributed transactions. Sandboxed clients may submit a create-only immutable request file (a bridge for restricted adapter/tool sandboxes — cx's Round 1 draft hit exactly this limitation trying to write files directly during this very debate; the broker-inbox pattern generalizes that real constraint); the privileged broker validates and imports it by request ID, with database uniqueness making repeated imports harmless. `AuditedOperation` remains available for attempts that produce evidence without mutating authoritative state.

## 13. Structural prevention of T87, T88, T89

### 13.1 T87 — one missing source suppressing independent collectors

**Observed in `hub.py`:** `gather_peer()` returns immediately when both status and health dicts are empty (`snapshot.py:878-927`), before the independent Codex SQLite/rollout/app-server collectors (`snapshot.py:1094-1146`) ever run.

**Prevention:** telemetry collection is a declared fan-out plan, not one sequential function. Every collector declares only its actual dependencies (a Codex rate-limit provider depends on its own endpoint/session capability, not a status file). The executor schedules every dependency-satisfied collector independently, under a per-collector deadline. Each collector returns a typed `EvidenceValue` (`MEASURED`/`ABSENT`/`UNAVAILABLE`/`ERROR`/`STALE`). Aggregation is total over collector results — a missing source can suppress only the derived fields that actually require it, never unrelated collectors. Overall state is `COMPLETE`/`PARTIAL`/`UNAVAILABLE` with a source matrix. There is no global "empty, return now" branch anywhere in this design.

### 13.2 T88 — exit 0 masquerading as task success

Structurally prevented by §9's three-layer `AskResult` — `ProcessExit(0)` can never directly produce `SUCCEEDED_VERIFIED`; every dispatch freezes a `CompletionContract`; adapters cannot declare semantic success (only `ProtocolAssessment`); missing required artifacts/failed validators produce `INCOMPLETE` even with exit 0 and text; unverified/incomplete work is not auto-retried after a potentially-effectful dispatch.

### 13.3 T89 — missing trigger input plus no dedup flooding proposals

**Observed in `hub.py`/portable-dev-env:** every session end launches self-care unconditionally (`ctx_end.py:472-480`); missing `commit_count` silently defaults to `0` (`saturation_scan.py:219-229`); `0 % 10 == 0` makes the "every-10th-commit" scan run every single time (`saturation_scan.py:279-285`); any nonempty stdout triggers `proposal-add` (`self_care.py:244-264`); proposal creation only increments a filename sequence, no content dedup (`hub.py:10438-10472`) — 60+ near-duplicate proposal files accumulated in one day as a direct result.

**Prevention:** trigger evaluation returns `DUE | NOT_DUE | INDETERMINATE` — a missing or malformed counter is `INDETERMINATE`, never silently coerced to a numeric default. A persisted trigger cursor compares real source revisions, not modulo arithmetic alone. A scan emits a normalized `FindingSet` with a fingerprint over sorted stable finding identities. Proposal identity is `(proposal_kind, workspace_scope, finding_fingerprint, lifecycle_generation)`; a database unique partial index permits at most one active proposal per identity, with creation as one transaction (insert-on-conflict/read-existing) so concurrent session-end processes still converge on the same proposal. A genuinely changed finding set gets a different fingerprint and may create a new proposal; rediscovering an identical closed set follows an explicit reopen/cooldown policy, never a bare filename counter. Pending handoff/dashboard entries are projections of the proposal/outbox event, never independently appended.

## 14. Other failure semantics

- **Spawn ambiguity/replay:** pre-spawn failures are `NOT_STARTED` (safely retryable per policy); post-`DISPATCH_INTENT` crash is `MAY_HAVE_STARTED`; post-identity-commit is `STARTED`. No automatic replay at either uncertain boundary.
- **Timeout/cancellation:** hard deadline, silence deadline, and caller cancellation are different causes; timeout initiates graceful cancellation only if declared, then bounded tree termination; cleanup failure attaches to the primary outcome, never masks it; a lease isn't released until the process tree is terminal or state is explicitly `IDENTITY_MISMATCH`/`UNKNOWN`.
- **Provider/observer failure:** telemetry can never change an already-completed peer result; provider circuit breaking is scoped to provider/profile/account; stale cached evidence stays tagged stale with its observation time; provider unavailability is never reported as zero usage, healthy, or unlimited quota.
- **State/event publication:** state transition + outbox event are atomic; consumers checkpoint event IDs and are idempotent; `COMMITTED_ENFORCEMENT_PENDING` is distinct from `COMPLETED`; no history/handoff/proposal side effect runs inside a consensus or request-state lock.
- **Configuration drift:** runtime configuration resolves to an immutable revision; a dispatch freezes its relevant revision + executable/readiness binding; session resume checks adapter/executable/profile fingerprints; declared configuration is never treated as measured readiness.

## 15. Future TDD implementation order (sequencing only — this document remains pre-TDD)

- **Phase 0 — behavioral inventory and contract freeze.** Enumerate current `hub.py` commands, error codes, state files, adapter profiles, session scopes, guard effects. Capture golden transcripts (pipe, PTY, session create/resume, timeout, broker vote merge, consensus close, health close/recover, routing audit). Write decision records for storage scope and process model. Specify protocol v1 commands/events/errors/SQLite invariants. **No package code until this compatibility set is agreed.**
- **Phase 1 — pure domain and store kernel.** TDD every reducer (request/attempt/lease/session/health/admission/consensus/proposal/mutation/budget). TDD the SQLite repositories, CAS, uniqueness, command idempotency, atomic outbox. Inject clock/IDs/process-identity. Fault-inject every transaction boundary. No real peer adapter yet.
- **Phase 2 — fake-peer vertical dispatch slice.** One fake pipe + one fake PTY executable. Invocation/artifact materialization, incremental events, deadlines, cancellation, process-birth identity, lease heartbeats, crash recovery. TDD every transition around `DISPATCH_INTENT`/spawn. Establish the three-layer outcome model. Prove no model call ever occurs under a store transaction.
- **Phase 3 — adapter/provider conformance.** Publish the conformance kit. Migrate built-ins incrementally (simplest pipe adapter first, then PTY/staging, then the richest JSONL/session adapter). Every adapter passes create/resume, decoder, artifact, cancellation, error, fingerprint fixtures. Test the valid `usage_provider=None` case. Live probes are opt-in empirical tests, never routine CI blockers.
- **Phase 4 — telemetry, health, routing.** Dependency-declared collector fan-out + typed evidence first, including T87 differential tests. Availability/admission projections + probe-based recovery. Routing as pure policy with golden decisions. Provider-process isolation + failure-amplification tests before enabling live usage routing.
- **Phase 5 — consensus, proposals, governed mutations.** Frozen electorate/rule, immutable votes, timeouts, arbiter separation, atomic decision outbox. Finding fingerprints, trigger cursors, active-proposal uniqueness, concurrent T89 tests. `MutationRequest`/`Plan`/`Receipt`, broker-inbox import, effect worker, saga reconciliation.
- **Phase 6 — versioned surfaces and strangler integration.** Expose the JSONL service + CLI through the same `core.api`. Run current `hub.py` in shadow/dual-read mode against `peerhub` decisions. Move one mechanism at a time behind the facade with rollback. Never maintain two write authorities for the same record. Retire old paths only after behavior/recovery/evidence parity is demonstrated.
- **Phase 7 — hardening and release.** Concurrent-client, crash-at-every-transition, malformed/truncated-protocol, provider-exhaustion, process-tree-leak, long-run proposal-dedup tests. Golden fixtures for current + previous major protocol. Local-filesystem transaction/locking probes on supported platforms. Normal PyPI package release with a console entry point. No vendor CLI bundling, no self-updater, no package-manager orchestration, no host-lifecycle framework — that boundary is permanent (§2.1 rule 8).

## 16. Open questions carried forward (not blocking — resolve during Phase 0 characterization)

Storage, layering, outcome-model, and service-model are resolved (§§3-4, 9). Remaining, from the Round 1 drafts, not yet contested but not yet decided either:

1. `UsageProvider` granularity — per-request cost estimates, or pool/account quota percentage only?
2. Windows PTY emulation approach for `AgyAdapter` (vendor a thin WinPTY wrapper vs. rely on standard-library bindings).
3. Proposal-dedup tombstone TTL — how long a closed/rejected fingerprint stays in the index before a re-proposal is allowed.
4. State scope — one `PeerHubHome` DB per user vs. per-workspace, and how one service coordinates multiple workspaces if per-user.
5. SQLite behavior on network/unusual filesystems — `TEST NEEDED`, not assumed.
6. Default UI wording for `DELIVERED_UNVERIFIED` — honest without making ordinary asks look failed.
7. Whether a scoped, empirically-verified vendor-state locator can ever qualify as a `SessionCapability` (modification-time selection alone must not).
8. Provider isolation cost boundary — which collectors need a supervised worker process vs. safe in-process execution.
9. Concrete health failure categories/thresholds/cooldowns — policy data, not hardcoded in reducers.
10. Public Python surface beyond the stable `Client` — wait for a real second consumer's concrete need before stabilizing more internals.
11. Third-party adapter discovery/signing — wait for a real third-party adapter before designing this.
12. Budget-authority v1 inclusion — optional authority with an explicit no-budget mode; exact inclusion point needs product-scope confirmation.
13. Protocol support window (current + previous major, per the blueprint) — final call depends on release cadence and consumer upgrade tolerance.

---

*Process record, full evidence citations, and the 3-round convergence debate
that produced this document: `docs/design/peerhub-architecture-debate.md`
in this same directory.*
