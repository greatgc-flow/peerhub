# peerhub — Target Architecture (v1, pre-TDD)

> Status: converged design, **not yet implemented**. This is the canonical
> deliverable of the 2026-07-27 `ag`/`cx`/`cc` architecture debate — Round
> 1-3 (main architecture, full convergence), a Round 4-5 meta-review pass
> (5-Whys/MECE/purpose-fit-generalization/efficiency/feedback-loop lenses,
> fully converged, found and fixed a real completeness gap — missing
> `coordination` ownership — plus 7 smaller organizational issues), a
> Round 6-7 coupling/anti-spaghetti cross-check (fully converged, found and
> fixed a real package-cycle risk around `core.api` plus 15 smaller
> coupling/interface issues), and a Round 8-9 SSOT cross-check (fully
> converged, triggered by a real same-session incident where a single live
> config fact had 4 independently-writable copies that drifted — found and
> fixed the analogous gap in this design: the configured peer/model pin had
> no single named owner, closed with a new `PeerProfileBinding` type plus a
> full SSOT ownership matrix, §4.1). **`ag`'s "0 findings" verdict was wrong
> and independently re-verified against `cx`'s evidence, not accepted at
> face value, in every one of the last three rounds** (Round 6: a leftover
> `peer_id` reference, confirmed directly against the live text; Round 8:
> `ag` cited specific fields of a type, `ProfileDescriptor`, that a direct
> `grep` proved were never actually defined anywhere in this document).
> Full process record in `docs/design/peerhub-architecture-debate.md`. No
> code, tests, or scaffolding exist yet. A future, separately-authorized
> round starts TDD implementation against this document, beginning with
> Phase 0 below.

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

> **Round 4-5 meta-review note:** fixed 3 real gaps the converged Round 1-3
> design still had: duplicate `RuntimeContext` ownership, duplicate
> protocol/error ownership, and — most significantly — **no owner at all
> for `hub.py`'s real room/mailbox/presence/handoff coordination
> mechanisms** (`hub.py:1363,1388,1460,1531,1573,1658,3532,3568,8690,8752,
> 8793,8844,8929,11135` — `action_init_session`, `action_end_session`,
> `action_send`, `action_broadcast`, `action_check`, `action_mark_read`,
> `action_update_status`, terminal-duty handoff), despite §1 claiming full
> communication/coordination replacement.
>
> **Round 6-7 coupling cross-check note:** `ag`'s first coupling pass found
> 0 issues; `cx`'s independent pass found 16, including a **real package
> cycle**: `core/api.py` (as it stood after Round 4-5) depended on every
> feature service, while every feature service depended back on
> `core.protocol`/`core.evidence`/`core.context` — all living in the same
> `core` package, i.e. `core <-> features`. `ag` re-verified all 16 findings
> concretely against the live text (rather than deferring on volume alone,
> after one — a leftover `peer_id` reference in the old §7.3 — was
> confirmed real by direct inspection) and accepted all of them. The tree
> below is the result: `core.api` moved out to a new `application/`
> package so `core` is a true dependency leaf; `core/execution.py` added so
> `adapters` and `dispatch` share process-boundary types without importing
> each other; `state/contract.py` (the port) separated from
> `persistence/sqlite.py` (the implementation) so no feature needs to
> import a concrete backend; narrow `contract.py` files added per feature
> so cross-feature access goes through a stable published DTO, never a
> sibling's private `model.py`.

```text
peerhub/
  __init__.py                 # public Client, typed public values, version only
  client.py                   # thin in-process/remote client; calls application.api, never storage directly
  runtime.py                  # configuration resolution & composition-root construction ONLY

  core/                        # pure leaf package — imports NOTHING feature-specific
    context.py                 # owns RuntimeContext & PathLayout (immutable value types only)
    protocol.py                 # owns ALL wire schemas: command/event/envelope/version/ErrorCode/EventEnvelope
    errors.py                   # internal exception types + their mapping to protocol.ErrorCode
    evidence.py                  # shared EvidenceValue/EvidenceRef algebra (MEASURED/ABSENT/UNAVAILABLE/ERROR/STALE)
    execution.py                  # NEW (Round 6-7): shared TransportKind/TransportLimits/ProcessTerminalEvidence/
                                   # ExecutionCertainty/RetryDisposition/Deadline — adapters AND dispatch import
                                   # from here; neither imports the other for these types
    ports.py                       # NEW (Round 6-7): CommandSubmitter — submit(CommandEnvelope) -> CommandReceipt.
                                    # Internal workers depend on this narrow port, not on application.api,
                                    # so the dependency arrow never points back up

  application/                   # NEW (Round 6-7), split out of core/api.py — the only mutating EXTERNAL entrance
    api.py                        # canonical application facade (§2.1 rule 3); implements core.ports.CommandSubmitter
    workflows.py                   # cross-feature sagas (the ONLY place sibling features get coordinated together)

  state/
    contract.py                 # StateStore / UnitOfWork PORT ONLY — domain depends on THIS, not sqlite3.
                                 # (Round 6-7: sqlite.py moved OUT — see persistence/ — so this stays a pure
                                 # interface package with zero feature-model imports.)

  persistence/                   # NEW (Round 6-7), split out of state/
    sqlite.py                     # SQLite v1 transactional backend (local filesystem, WAL mode, atomic outbox).
                                   # May import state.contract + every feature's public contract.py. NO feature
                                   # may import persistence — only runtime.py wires it in.
    migrations/

  adapters/
    contract.py                  # PeerAdapter + optional capability protocols (UsageProvider, SessionCapability)
    instance.py                   # PeerInstanceConfig — see §6.1a
    registry.py                    # DISPOSABLE derived index over PeerDescriptor + PeerInstanceConfig (§4.1) -- never an independent store
    readiness.py                    # readiness probe contract & receipts (§6.5)
    builtins/
      claude.py                   # ClaudeAdapter (cc)
      codex.py                     # CodexAdapter (cx)
      antigravity.py                # AgyAdapter (ag)
    # Round 6-7: adapters imports core.execution for process-boundary types; adapters NEVER imports dispatch.

  dispatch/
    model.py                       # PURE request/attempt/session/lease transition reducers
    service.py                      # orchestration across adapter, runner, store
    contract.py                      # NEW (Round 6-7): published DTOs other features may import — AskResult, DispatchRequest
    process.py                       # process-supervisor port/types (pipe/PTY) — built on core.execution
    pipe.py                           # concrete pipe runner
    pty.py                             # concrete PTY runner
    artifacts.py                       # staged input/output ArtifactMaterializer
    completion.py                       # CompletionAssessor — see §9

  coordination/                          # rooms, mailbox, presence, handoff — closes the Round 4-5 gap
    model.py                              # PURE room/membership/presence/message/handoff/checkpoint reducers
    service.py                             # coordination orchestration (send/broadcast/read/handoff)
    contract.py                             # NEW (Round 6-7): RoomRef, ConversationScope, TerminalAssignmentSnapshot
    # Round 6-7: cross-feature effects (retiring a session on room close, updating a handoff projection on
    # proposal change) go through events consumed by application.workflows — coordination never imports
    # dispatch/governance/health/consensus directly. See §10.1-adjacent event note and Finding 15 in the ledger.

  routing/
    model.py                            # PURE RouteDecision reducer
    service.py                           # capacity/EXH-aware routing service
    contract.py                           # NEW (Round 6-7): RouteRequest, RouteDecision

  consensus/
    model.py                              # PURE round/electorate/vote/final-opinion reducer
    service.py                             # R:10 protocol orchestration; returns PeerInvocationIntent/
                                            # ArbiterInvocationIntent rather than calling dispatch directly — see §8
    contract.py                             # NEW (Round 6-7): RoundSpec, Decision, PeerInvocationIntent

  health/
    model.py                                # PURE availability/admission/quarantine reducers
    service.py                               # health/recovery orchestration (policy & transitions ONLY)
    contract.py                               # NEW (Round 6-7): AdmissionSnapshot

  telemetry/                                 # split out of health/ — feeds health AND routing
    collectors.py                             # dependency-declared fan-out (T87 fix)
    projections.py                             # outcome -> observation -> projection feedback loop — see §10.1.
                                                # Consumes the narrow AttemptTerminalObserved event, NEVER the
                                                # full dispatch.AskResult (structurally enforces §10.1's safety rule)
    contract.py                                 # NEW (Round 6-7): TelemetryProjectionReader, OperationalProjectionSnapshot

  ipc/
    jsonl.py                                     # framing, serialization, version negotiation ONLY
    cli.py                                        # CLI translation to the same command bus (calls application.api)

  governance/
    mutations.py                                  # MutationRequest / MutationPlan / TransitionReceipt
    broker.py                                      # governed mutation broker (CAS, journal, effect workers)
    proposals.py                                    # fingerprint-deduplicated proposal engine (T89 fix)
    contract.py                                      # NEW (Round 6-7): MutationRequest, TransitionReceipt, ProposalRef

  # NOT present in v1 (Round 4-5 deferral — see §16): a published `testing/`
  # conformance-kit package. Fixtures live in the repository's own test
  # suite until a real third-party adapter needs a public kit.
```

### 2.1 Ownership rules

1. **`core.protocol` owns compatibility, not behavior.** ALL schemas, correlation, version negotiation, stable error codes, and the `EventEnvelope` the outbox stores — the canonical, transport-neutral source. `ipc.jsonl` only frames/serializes what `core.protocol` defines; `state.contract` must never define a second event payload schema (Round 6-7, Finding 10). Cannot import `state`/`adapters`/feature internals.
2. **`*/model.py` modules are pure.** They accept values and return transition decisions/effect intents. No file I/O, no clock reads, no environment access, no vendor state.
3. **`application.api` is the only mutating *external* entrance.** (Round 6-7: moved out of `core` — see the module-structure note above; `core.api` no longer exists.) CLI, JSONL, and the embedded `Client` all submit the same typed commands through it. This does not forbid internal system-initiated mutation: recovery sweeps, lease heartbeats, outbox/effect workers, and broker reconciliation all mutate state too, but they do so through the narrow `core.ports.CommandSubmitter` port (which `application.api` implements) — never by calling repositories or `application.api` itself directly, which would point the dependency arrow the wrong way. Every mutation, external or internal, gets identical authorization/idempotency/audit treatment.
4. **`adapters` translates; it does not coordinate.** Adapters plan invocations and decode vendor output. They cannot spawn processes, select peers, modify health, persist sessions, acquire leases, append audit events, or decide task success. **`adapters` never imports `dispatch`** — shared process-boundary types live in `core.execution`, imported by both (Round 6-7, Finding 3).
5. **Infrastructure (runners, `persistence.sqlite`) executes plans, not policy.** A PTY runner can emit chunks and terminate a process tree, but cannot decide to retry, quarantine, or invoke an arbiter. **No feature package may import `persistence`** — only `runtime.py` wires the concrete backend to the `state.contract` port (Round 6-7, Finding 4).
6. **`RuntimeContext` is immutable dependency injection**, owned and constructed by `core.context`, and contains only low-level immutable values (paths, scope, policy revision, clock/ID ports) — **never feature-service instances**, which would turn it into a service locator and reintroduce the same cycle risk `core.api` had (Round 6-7, Finding 2). `runtime.py` composes the actual `Runtime` object containing feature-service instances; feature services receive narrow constructor dependencies, never the whole runtime container.
7. **Concrete peer IDs stay in `adapters.builtins` registration/config.** Core code selects capabilities and descriptors, never `if peer == "cx"`.
8. **Vendor installation is outside the engine.** A descriptor supplies a configured executable reference; readiness canonicalizes and probes it. `peerhub` does not install, update, authenticate, or bundle vendor CLIs (this boundary is permanent — see the portable-dev-env's own 2026-07-27 T82 re-scope, which drew the identical line from the other side).
9. **`coordination` owns room/mailbox/presence/handoff state — not `ipc`.** `ipc` is command/event *transport*; it is not a valid owner for the actual room/membership/message/checkpoint domain, which is a real, currently-implemented part of what §1 commits to replacing.
10. **A feature may import a sibling's `contract.py`, never its `model.py`.** (Round 6-7, Finding 5.) Only `application` (facade + workflows) calls sibling `service.py` methods to coordinate more than one feature; a feature's own `service.py` never calls another feature's `service.py` directly — see Finding 6 (`consensus` returning a `PeerInvocationIntent` instead of calling `dispatch.service`) and Finding 15 (`coordination` emitting events instead of importing `governance`/`health`/`dispatch`/`consensus`) for the two concrete cases this rule was written to prevent.
11. **`telemetry` and `health` do not duplicate each other.** (Round 6-7, Finding 14/16.) `telemetry` owns measured observations and empirical aggregates; `health` owns policy classifications (`HEALTHY`/`COOLDOWN`/`QUARANTINED`/`OPEN`). `coordination`'s presence tracking (membership, heartbeat, terminal-duty ownership) is a third, distinct concern from both — presence may be evidence supplied *to* health policy, but never itself equals `HEALTHY`/`OPEN` (Finding 16).

## 3. Hosting model

**Resolved (Round 2-3): no resident OS daemon in v1.**

Two deployment forms, same command/event schema as the compatibility surface in both:

- **Embedded**: `from peerhub import Client` — `Client` invokes `application.api` directly in the same Python process. This is the default, CLI-first mode.
- **One-shot CLI/JSONL**: `peerhub` as a subprocess handling one correlated command (or a short synchronous exchange), matching today's `hub.py` usage pattern exactly.

**Round 4-5 deferral:** a **persistent, multi-command `serve --stdio` connection** is explicitly **not in v1** — it was in the Round 1-3 design but has no named v1 consumer needing connection reuse; today's `hub.py` command surface is one-shot throughout. The versioned JSONL envelope (§5) is preserved so adding a persistent host later doesn't change command semantics, but v1 ships embedded + one-shot only. Add persistent hosting when a real client needs multi-command connection state, not speculatively.

Multiple independent CLI/embedded host processes coordinate safely through the transactional store (unique idempotency keys, owner-aware leases with process-birth identity, durable outbox events, startup recovery sweeps) — this covers concurrent dispatches without needing a singleton broker. A resident daemon is deliberately deferred: add one only when a measured requirement for continuous cross-client event subscription (not just concurrent dispatch) actually appears — symmetric-deferral rule, same as the shelved blueprint's own process discipline.

MCP is not a foundation; if added later, it is a translation adapter over this same command/event service.

## 4. Authoritative state

**Resolved (Round 2-3, backend location fixed Round 6-7): `StateStore`/`UnitOfWork` interface in `state/contract.py`; SQLite (`persistence/sqlite.py`) is the supported v1 backend.** Domain code depends on the interface, never on `sqlite3` directly. (Round 6-7, Finding 4: the interface and the implementation must live in different packages — `state/contract.py` stays a pure port with zero feature-model imports, while `persistence/sqlite.py` is the only place allowed to import feature `contract.py` modules to persist/hydrate their records. No feature imports `persistence`; only `runtime.py` wires the two together.)

**Round 8-9 SSOT fix: these SQLite records are the single *operational* source of truth for peer/model configuration — not a file.** This closes the exact failure shape a live incident hit during this same debate: the portable-dev-env's real `cc.deepthink` model ID had 4 independently-writable locations (an orchestration file, a capability-declarations file, routing-target strings, and a model-registry file), none derived from the others, so one legitimate model bump required a coordinated 4-file edit and drifted silently until hardcoded tests caught it. External configuration (YAML/JSON, however it's authored) may **bootstrap or submit a governed import** of peer/profile/model configuration, but once imported it is not a second live source — dispatch, routing, readiness, and the adapter registry (§6.1) read only the SQLite record. See the SSOT Ownership Matrix (§4.1) for the complete per-fact ownership table.

One SQLite database per configured `PeerHubHome`:

- Request, request-attempt, process-lease, and session-binding records.
- Configured peer instances and their profile-to-model bindings (`PeerInstanceConfig` + `PeerProfileBinding` — §6.1a; §4.1).
- Room, membership/presence, message, and handoff/checkpoint records (`coordination` — new in Round 4-5, closes the gap noted in §2).
- Health observations, telemetry projections, and current health/admission projections.
- Routing decisions.
- Consensus rounds, electorate snapshots, votes, and arbiter opinions.
- Proposal finding sets, trigger cursors, and proposal lifecycles.
- Mutation requests, receipts, effect intents, and an event outbox.
- **Not in v1** (Round 4-5 deferral, §16): budget reservations. No reservation table ships until Phase 0 identifies a measured oversubscription problem and an authoritative reservable unit — see §11.

Large transcripts, staged prompts, and output artifacts remain files referenced by content digest and length — not embedded in the database.

Required store properties: short `BEGIN IMMEDIATE` write transactions with revision/CAS checks; unique constraints for command idempotency, one vote per voter/round, one active proposal per dedup identity, one live session binding per scope; state transition + outbox event committed atomically; immutable evidence rows, mutable projections always carrying a revision; no transaction held while waiting on a peer/provider/filesystem/network call; recovery derived from authoritative records and effect intents, never inferred from log text alone.

This is intentionally a local, not distributed, design. SQLite removes the bespoke JSON-file locking that caused T83, and directly supplies the atomicity/uniqueness the observed defect classes (T83, T89) need. **The DB must be enforced on a local filesystem** (not SMB/NFS) — v1 fails startup if its lock/transaction probe fails rather than claim unmeasured safety on network filesystems (`TEST NEEDED` if that support matrix is ever wanted).

A future non-SQLite backend is acceptable only if it passes the identical multi-process/crash-boundary/uniqueness/state-plus-outbox test suite as SQLite — v1 ships exactly one backend, not two.

### 4.1 SSOT ownership matrix (Round 8-9)

Every fact category below has exactly one canonical owner. Everything in the third column is a reference (by ID/revision/digest) or a mechanically-derived/immutable-freeze projection — never an independently-writable second copy.

| Fact category | Canonical owner | Everyone else references or derives it |
|---|---|---|
| Adapter implementation capabilities (transports, session/stream support) | `PeerDescriptor` (adapter code, §6.1) | `adapters.registry` indexes it; nothing else declares capabilities |
| Configured instance identity, model pin, reasoning effort | `PeerInstanceConfig` + `PeerProfileBinding` (SQLite, §6.1a) | `dispatch`, `routing`, `health`, `coordination` all key on `instance_id`; `InvocationPlan.argv` is generated from the binding, never independently specified |
| Observed executable/model/version/context capability | Immutable readiness `EvidenceValue` (§6.5) | Compared against the configured binding by policy; never copied back into it |
| Routing weights, cost classification, terminal exclusions | Versioned `RoutingPolicy` (§11) | `RouteDecision` records which policy revision it used, never redefines the policy |
| Measured quota/headroom | `UsageEvidence` (§6.4) | Routing reads it as evidence; never cached as a second mutable figure |
| Health availability + admission policy classification | The live projection owned by `health.service` (§10) | `AdmissionSnapshot` is an immutable revisioned freeze of it; `RouteDecision`/consensus reference the snapshot ID and never independently write `HEALTHY`/`OPEN`/quarantine state |
| Raw/aggregated telemetry observations | `telemetry.projections`, fed only by the narrow `AttemptTerminalObserved`/`ReadinessObserved`/`UsageObserved` events (§10.1) | `health.service` reads via `TelemetryProjectionReader`; telemetry itself never computes admission states |
| Consensus round rules (electorate, decision rule, risk classification) | The frozen `RoundContract` created with the round (§8) | Global policy supplies the input at creation time only; later policy changes never retroactively alter an in-flight or decided round — the round records `source_policy_revision` as provenance, not a re-derivation path |
| Consensus outcome | `ConsensusDecision` (base) + optional `ArbiterOpinion` (override), both immutable (§8) | "Effective decision" is a pure derived read (arbiter override if authorized and present, else base) — never a third persisted field |
| Votes | One immutable `(round_id, voter_id)` row (§8) | Nothing else stores or recomputes a vote |
| Mutation lifecycle | `MutationRequest` (intent) → `MutationPlan` (authorized) → target record (current state) → `TransitionReceipt` (commit result) (§12) | Each stage references the prior stage's ID/digest; `TransitionReceipt` never duplicates the full target record, only transition identity/revisions/evidence refs |
| Proposal identity/deduplication | The canonical normalized `FindingSet` and its server-derived fingerprint, computed only by `governance.proposals` (§13.3) | Caller-supplied fingerprints are rejected; handoff/dashboard entries are read-only projections of proposal/outbox events, never independently appended |
| Wire schemas (commands/events/errors) | `core.protocol` (§2.1 rule 1) | `ipc` only frames/serializes what protocol defines |
| Shared evidence/execution vocabulary | `core.evidence.EvidenceValue`, `core.execution` (§2.1 rules) | `UsageEvidence` composes `EvidenceValue` rather than redefining it; protocol errors and `ExecutionOutcome` share one `ExecutionCertainty` enum |

`adapters.registry` itself is a **disposable, mechanically rebuilt derived index** over `PeerDescriptor` declarations and `PeerInstanceConfig`/`PeerProfileBinding` records — if cached, it carries the source revisions/digests it was built from and is discarded on mismatch. It never persists an independently editable copy of a model ID, capability, cost figure, or context-window value.

## 5. Public command, event, and error contract

All commands carry: `protocol_version`, `command_id` (caller-generated idempotency key), `correlation_id`, `client_id`, `scope`, `expected_policy_revision?`, `method`, `params`.

**Round 4-5 fix:** `scope` is `WorkspaceScope | GlobalScope`, not a bare `workspace_scope` — coordinator-level operations (node listing, broker status, global health, configuration inspection) aren't inherently workspace-scoped, and forcing a fake workspace value on them was a real modeling gap. Each command schema declares which scope kind(s) it accepts.

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


@dataclass(frozen=True)
class ProfileDescriptor:
    """Round 8-9 SSOT fix: this type was referenced 4 times in earlier rounds
    but never actually defined — the gap that let Round 8's coupling check
    initially (wrongly) assume it owned the model pin. It does NOT: this is
    adapter-level, generic profile-KIND metadata, shared by every instance
    of this adapter. The configured model pin is instance-level and lives
    in PeerProfileBinding (§6.1a) instead — see the §4.1 ownership matrix."""
    profile_id: str                          # "standard" / "effort" / "deepthink" / a specialty name
    profile_class: str                       # "tier" | "specialty"
    supports_reasoning_effort: bool
```

Profile, account, and quota-pool identifiers are data, never inferred from peer names. Registry loading validates uniqueness and referential integrity before a runtime is admitted. A descriptor declaring a capability it doesn't actually implement is a load-time error, not a runtime surprise.

### 6.1a `PeerInstanceConfig` (Round 4-5 fix — adapter type vs. configured instance)

The Round 1-3 design left `peer_id` ambiguous: session/routing/health records referenced it, but the document never said whether it meant the adapter kind (`claude`), a configured executable, an account, or a running instance — ambiguous the moment a fourth peer reuses an existing adapter, or two accounts of one vendor / two executables need distinct readiness bindings.

```python
@dataclass(frozen=True)
class PeerInstanceConfig:
    instance_id: str
    adapter_id: str                    # which PeerDescriptor/adapter implementation
    executable_reference: str
    profile_bindings: tuple[PeerProfileBinding, ...]
    account_id: str | None
    quota_pool_id: str | None


@dataclass(frozen=True)
class PeerProfileBinding:
    """Round 8-9 SSOT fix (replaces the earlier bare `enabled_profile_ids:
    tuple[str, ...]`, which had no field for the model pin at all): the
    SINGLE owner of "which model does this instance's this profile actually
    run." Stored in SQLite as part of PeerInstanceConfig (§4.1) — never
    duplicated into ProfileDescriptor, InvocationPlan literals, or a
    separate registry file. This is exactly the fact that had 4
    independently-writable copies in the real incident that motivated this
    round; here it has exactly one."""
    binding_id: str
    profile_id: str                    # references ProfileDescriptor.profile_id (§6.1)
    model_id: str                       # e.g. "claude-opus-5", "gpt-5.6-sol" — the actual configured pin
    reasoning_effort: str | None
    revision: str
```

**Health, routing, leases, and sessions key on `instance_id`.** Adapter conformance (§6.5) keys on `adapter_id` + adapter version. `InvocationPlan.argv` (§6.2) is mechanically generated from the resolved `PeerProfileBinding`, never independently specified. A request attempt freezes the `binding_id` + `revision` it used; readiness evidence (§6.5) records the *observed* model and whether it matched the binding, and never rewrites the configured pin itself. This closes a real, named gap (multi-account/multi-executable configurations) without inventing a speculative plugin ecosystem.

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
        limits: TransportLimits,          # core.execution.TransportLimits — Round 6-7, Finding 3
    ) -> InvocationPlan: ...

    def new_decoder(self, plan: InvocationPlan) -> OutputDecoder: ...

    def interpret_output(
        self,
        plan: InvocationPlan,
        process: ProcessTerminalEvidence,  # core.execution.ProcessTerminalEvidence — Round 6-7, Finding 3
        raw_chunks: Sequence[bytes],
    ) -> ProtocolAssessment:
        """Vendor-protocol evidence ONLY (malformed/truncated framing, empty
        response, vendor error, progress-without-terminal marker, suspicious
        delegation marker). MUST NOT decide task fulfillment — see §9."""
        ...
```

**(Round 6-7, Finding 3):** `TransportLimits` and `ProcessTerminalEvidence` live in `core.execution`, not in `dispatch` or `adapters` — both packages import them from that shared leaf module. Without this, `adapters` would need to import `dispatch.process` for these types while `dispatch.service` imports `adapters.contract` for `PeerAdapter`, a real cycle. `adapters` never imports `dispatch`.

**`AdapterRequest`**: the already-authorized request ID, prompt content/reference, workspace scope, profile, requested session policy, and a `CompletionContract` — the adapter never evaluates it, only carries it. **(Round 4-5 fix: admission always freezes a contract, never a bare optional.)** If the caller didn't supply one, admission freezes a canonical implicit contract whose only claim is delivery — producing `DELIVERED_UNVERIFIED` on valid protocol output, never an unevaluated gap. This replaces an inconsistency in the Round 1-3 text (§6.2 said "optional," §13.2 said "every dispatch freezes one").

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

`UsageEvidence` is never `{}`. **(Round 6-7, Finding 8: `UsageEvidence = EvidenceValue[UsageMeasurement]`** — composition over `core.evidence.EvidenceValue`, not a second independent copy of its state algebra.) It carries the shared `EvidenceValue` state (`MEASURED | ABSENT | UNAVAILABLE | ERROR | STALE`), source tag, provider version, observed/captured timestamps and freshness, and a raw evidence reference — all owned exclusively by `core.evidence`, never redefined here. `UsageMeasurement` (the type parameter) owns only usage-specific numeric values and quota-pool scope. No-provider is valid and explicit. A provider failure can never change a peer invocation's result. Providers capable of blocking I/O run in a supervised worker process (a timed-out Python thread is not real containment). Readiness and telemetry-collector evidence (§13.1) should reuse this same `EvidenceValue` composition rather than inventing their own state enums.

### 6.5 Readiness

Separate probe contract, because adapter conformance and vendor-executable readiness are different evidence: adapter conformance proves the Python adapter against fixtures; dependency readiness proves the configured executable's identity/version/capabilities right now; a mutable readiness binding references the current immutable receipts. The engine canonicalizes and probes the configured executable — it neither installs nor updates it.

**Model-pin readiness (LL-20260731-001, real incident, portable-dev-env `_sys/core/hub.py`/`orchestration.json`):** a vendor CLI's own model-enumeration surface (e.g. a `models` subcommand printing a catalog) is not evidence that the same identifier is honored by that CLI's `--model`-equivalent invocation flag. A real production incident had `_sys/core/hub.py`'s `ag.deepthink` profile pass a model identifier that the vendor CLI's enumeration command listed as valid, while the CLI's actual runtime resolver silently substituted a different, lower-tier model on every invocation — zero non-zero exit code, zero stderr, a "successful invocation" indistinguishable from a correct one. It was caught only by grepping that CLI's own live per-invocation log for the line recording which model label it actually propagated to the backend, and comparing it against the requested identifier. Consequently: an adapter's readiness probe (this section) that only proves "the executable is present, at this version, and enumerates this model" is **not sufficient** to certify a `PeerProfileBinding.model_id` (§6.1a) as `verified_local`. The probe must additionally capture and compare the vendor's own per-invocation resolution evidence (whatever form that takes for that adapter — log line, structured event, response metadata) against the requested `model_id`, and record a mismatch as a readiness failure, not a pass. This is why §6.1a already requires readiness evidence to record "the *observed* model and whether it matched the binding" rather than trusting the invocation's exit code alone — this incident is the motivating case for that requirement, and any future adapter implementation must not weaken it back down to enumeration-only or exit-code-only verification.

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

**Binding key is `(WorkspaceScopeId, instance_id, profile_id, conversation_scope)`** — not `peer_id` (Round 6-7, Finding 11: this was a leftover reference to the identity model §6.1a already superseded; `cc` independently verified the stale text before this fix, `instance_id` is always the configured `PeerInstanceConfig`, `adapter_id` is never used as runtime identity, and global-scope commands cannot create/resume sessions). Only `ACTIVE` can be reused; resume requires adapter fingerprint + readiness binding + profile + session generation to match; an interrupted/start-uncertain request moves the session to `SUSPECT`/`UNKNOWN`, never straight back to active; only correlated vendor evidence verifies it; session state updates only after request assessment by `application.api`, never by the adapter.

## 8. Consensus state machine

```text
DRAFT -> VOTING -> DECIDING -> APPROVED | REJECTED | ESCALATED
                             -> ARBITRATION_PENDING -> OVERRIDDEN_APPROVE | OVERRIDDEN_REJECT | ESCALATED
                  -> EXPIRED -> ESCALATED
```

Creating a round atomically freezes an immutable **`RoundContract`** (Round 8-9 SSOT naming — see §4.1): full electorate, policy revision + collaboration rate, decision rule + minimum participation, risk classification + whether arbiter override is permitted, health/readiness evidence for every voter (**without deleting any voter** — the `required_voters` reduction bug in today's `hub.py:7619-7701`, where an unavailable voter can silently shrink the unanimity denominator, must not recur), deadline, proposer + subject digest, round revision.

**Round 8-9 SSOT fix — precedence rule:** the frozen `RoundContract` is authoritative for its round, full stop. Global policy owns the *defaults that fed the contract at creation time* — it is never re-consulted to reconstruct or reinterpret a round after the fact. If policy evolves mid-round or after a round closes, the round's own frozen fields still govern it; `policy_revision` is retained as provenance (which policy generation the contract was built from), not a live pointer a consumer could dereference to get a different, "more current" answer for the same round.

`(round_id, voter_id)` is unique; identical resubmission is idempotent, a different second vote is rejected; votes are immutable evidence; the decision reducer is pure over the frozen contract + votes; the base result and any later override are both retained, never overwritten. **The "effective" decision a caller sees is a pure derived read** — `arbiter_opinion if (authorized and present) else base_decision` — never a third independently-persisted field that could itself drift from the two source facts.

**Arbiter invocation ownership (Round 6-7, Finding 6):** `consensus.service` owns round policy and transitions — it does not call `dispatch.service` directly (that would be a sideways cross-feature service call, and would force consensus's own tests to require process orchestration). Instead, when DIR-005 final-arbiter selection is required, `consensus.service` returns an immutable `PeerInvocationIntent`/`ArbiterInvocationIntent` (published via `consensus/contract.py`). `application.workflows` executes that intent through `dispatch.service` and submits the resulting vote/opinion back to consensus as a new command — a separate application transition after the base result commits, never inside the consensus transaction itself.

## 9. Outcome model (T88 structural fix)

**Resolved (Round 2-3).** Three distinct outcome layers, never conflated:

```python
@dataclass(frozen=True)
class AskResult:
    execution: ExecutionOutcome
    protocol: ProtocolAssessment
    completion: CompletionAssessment
    policy_revision: str

    @property
    def effective_status(self) -> AskStatus:
        """Round 4-5 fix: DERIVED, not independently stored/persisted —
        a persisted 4th field could contradict the 3 evidence layers it's
        supposed to summarize. Pure computed property (or an equivalent
        central reducer output) over (execution, protocol, completion)."""
        ...

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

**Round 8-9 SSOT fix — temporal ownership:** `health.service` owns the single *live* availability/admission projection described above — it is the only writer. `AdmissionSnapshot` is an **immutable, revisioned freeze** of that projection at the moment `routing.service` or a consensus round needed one, identified by projection ID/revision/digest — never a second place that independently re-decides "is this instance routable." `RouteDecision` (§11) and a consensus round's frozen contract (§8) both *reference* the snapshot they used; neither ever writes `HEALTHY`/`OPEN`/quarantine state itself. This keeps "healthy" (health.service's call), "admitted" (the frozen snapshot a decision was made against), and "selected" (that decision's own outcome) as three distinct facts with three distinct owners, never three places that could each separately claim the same one.

### 10.1 Evidence feedback loop (Round 4-5 addition)

**Gap found:** the Round 1-3 design's outbox/evidence store (§4) is not *wholly* write-only — routing already consumes current usage/admission evidence — but the path from a terminal `AskResult` (§9) back into future health/routing decisions was never explicit. As written, a terminal outcome could land in the outbox and stay audit-only forever, missing the same category of gap the shelved blueprint's own phase2-arch document found in itself (§13.16, Evidence Feedback Loop).

**Smallest fix that closes it**, owned by `telemetry.projections`:

1. The terminal transition commits its normalized observation alongside request state and the outbox, in the same transaction — as a **narrow, dedicated event, not the full `AskResult`** (Round 6-7, Finding 12 — see below).
2. `telemetry.projections` maps classified attempt/probe/provider outcomes into immutable observations and revisioned per-instance/profile projections — tracking only operationally meaningful facts: infrastructure failure category, verified process integrity, latency, freshness, failure streak, measured quota state.
3. `health.service` consumes these projections — through `telemetry.contract.TelemetryProjectionReader.get(instance_id, profile_id) -> OperationalProjectionSnapshot` (Round 6-7, Finding 13), never raw `telemetry.projections` internals — to derive `AdmissionSnapshot` (§10).
4. `routing.service` (§11) may consume the same frozen, policy-declared reliability/latency snapshot under minimum-sample and freshness rules, recording the supporting evidence references in its own `RouteDecision`.
5. Every new decision emits evidence again — the loop closes and stays rebuildable: projections are idempotently reconstructable from immutable observations, never authoritative in their own right.

**Why `telemetry` must never receive the full `dispatch.AskResult` (Round 6-7, Finding 12):** `AskResult` carries `CompletionAssessment` — exactly the task-semantic judgment the safety caveat below forbids from influencing health. If telemetry held the full `AskResult`, that forbidden field would sit right there, one accidental read away from violating the caveat by convention alone. Instead, `dispatch` commits and emits a narrow canonical `AttemptTerminalObserved` event (defined in `core.protocol`): `instance_id`, `profile_id`, `transport`, `operational_failure_category?`, `execution_certainty`, `process_integrity`, `started_at`, `terminal_at`, `latency`, `evidence_refs` — no completion state at all. `telemetry.projections` consumes only this event from the outbox; it never imports `dispatch`, reads request tables, or receives `CompletionAssessment`. Probe/provider paths similarly emit typed `ReadinessObserved`/`UsageObserved` events. This makes the safety caveat structural (the field literally isn't there to misuse) rather than conventional — the same design principle §13's T87/T88/T89 prevention already uses elsewhere in this document.

**Safety caveat (non-negotiable):** a semantic `INCOMPLETE`/`UNVERIFIED` `CompletionAssessment` must **never** automatically degrade peer health unless a versioned policy explicitly classifies the specific failure as peer-caused (vs. task-difficulty-caused). Otherwise a peer would be penalized for correctly reporting honest uncertainty on a hard task — exactly the dishonesty §9 was built to prevent, reintroduced one layer up.

Consensus/arbiter policy (§8) is explicitly **excluded** from this loop: electorate, unanimity, risk classification, and arbiter authority are governed policy, not optimization weights, and must never self-tune from historical outcomes. Evidence may inform a human/R:10 policy revision; it must never silently rewrite constitutional behavior.

## 11. Routing and budget

Pure decision over requested capabilities/profile constraints, readiness binding, admission snapshot, measured usage/headroom evidence, in-flight reservations, terminal exclusion/cost policy, task-size/context requirements, and a deterministic request ID. Returns a durable `RouteDecision`: all candidates, exclusions with reason+evidence, representative profiles, effective weights, seed/draw if stochastic, selected target, policy revision — matching the audit shape `hub.py`'s current routing already demonstrates is achievable. Missing usage is `ABSENT`/`UNAVAILABLE`, never zero.

**Round 4-5 deferral: no `BudgetReservation` state machine in v1.** The Round 1-3 design specified one before deciding whether v1 even includes budget authority, whether providers report request-level cost vs. account/pool headroom only, or what the authoritative reservable unit would be — a state machine can't be correctly specified before those facts exist. v1 ships usage evidence + no-budget routing only, in explicit no-budget mode (never a fake/zero provider). The shape below is preserved as a **conditional design note**, activated only if Phase 0 characterization finds a measured oversubscription problem and names an authoritative reservable unit:

```text
RESERVED -> DISPATCH_INTENT -> LAUNCHED -> CONSUMED | CONSUMED_UNKNOWN
RESERVED -> RELEASED   # only if definitively pre-dispatch
```

The usage provider would report evidence only; it would never itself reserve/release budget. A crash after dispatch intent would conservatively become `CONSUMED_UNKNOWN`.

## 12. Governed mutations and brokered effects

```text
MutationRequest -> authorized + domain validated + expected-revision checked
                 -> MutationPlan
                 -> atomic state transition + receipt + outbox/effect intents
                 -> TransitionReceipt(COMMITTED_ENFORCEMENT_PENDING)
                 -> effect worker
                 -> TransitionReceipt(COMPLETED | EFFECT_FAILED)
```

`MutationRequest` carries operation/command/correlation IDs, actor/client attribution, policy revision, target record ID, expected record revision, typed desired transition. Authorization + domain-invariant validation happen before plan commit; the transaction commits the new authoritative record, immutable receipt, and outbox/effect intent together; filesystem/process effects happen after commit and reconcile idempotently; cross-record/cross-workspace operations are explicit sagas, not claimed distributed transactions. Sandboxed clients may submit a create-only immutable request file (a bridge for restricted adapter/tool sandboxes — cx's Round 1 draft hit exactly this limitation trying to write files directly during this very debate; the broker-inbox pattern generalizes that real constraint); the privileged broker validates and imports it by request ID, with database uniqueness making repeated imports harmless. Once imported, the pending file is spent transport, not a second live copy — the database `MutationRequest` is authoritative, and an archived copy of the file is provenance only, never re-read as current state. `AuditedOperation` remains available for attempts that produce evidence without mutating authoritative state.

**Round 8-9 SSOT fix — non-overlapping roles, not a chain of copies:** `MutationRequest` (caller intent), `MutationPlan` (authorized/normalized execution), the target record (current domain state), and `TransitionReceipt` (commit result) each reference the prior stage by ID/digest rather than duplicating its content. In particular, `TransitionReceipt` records transition identity, revisions, outcome, and evidence references — it must never become a second full copy of the target record. A consumer reconstructing "what happened to this mutation" reads the receipt for the outcome and the target record for current state; it does not have two different valid paths to the same answer.

## 13. Structural prevention of T87, T88, T89

### 13.1 T87 — one missing source suppressing independent collectors

**Observed in `hub.py`:** `gather_peer()` returns immediately when both status and health dicts are empty (`snapshot.py:878-927`), before the independent Codex SQLite/rollout/app-server collectors (`snapshot.py:1094-1146`) ever run.

**Prevention:** telemetry collection is a declared fan-out plan, not one sequential function. Every collector declares only its actual dependencies (a Codex rate-limit provider depends on its own endpoint/session capability, not a status file). The executor schedules every dependency-satisfied collector independently, under a per-collector deadline. Each collector returns a typed `EvidenceValue` (`MEASURED`/`ABSENT`/`UNAVAILABLE`/`ERROR`/`STALE`). Aggregation is total over collector results — a missing source can suppress only the derived fields that actually require it, never unrelated collectors. Overall state is `COMPLETE`/`PARTIAL`/`UNAVAILABLE` with a source matrix. There is no global "empty, return now" branch anywhere in this design.

### 13.2 T88 — exit 0 masquerading as task success

Structurally prevented by §9's three-layer `AskResult` — `ProcessExit(0)` can never directly produce `SUCCEEDED_VERIFIED`; every dispatch freezes a `CompletionContract`; adapters cannot declare semantic success (only `ProtocolAssessment`); missing required artifacts/failed validators produce `INCOMPLETE` even with exit 0 and text; unverified/incomplete work is not auto-retried after a potentially-effectful dispatch.

### 13.3 T89 — missing trigger input plus no dedup flooding proposals

**Observed in `hub.py`/portable-dev-env:** every session end launches self-care unconditionally (`ctx_end.py:472-480`); missing `commit_count` silently defaults to `0` (`saturation_scan.py:219-229`); `0 % 10 == 0` makes the "every-10th-commit" scan run every single time (`saturation_scan.py:279-285`); any nonempty stdout triggers `proposal-add` (`self_care.py:244-264`); proposal creation only increments a filename sequence, no content dedup (`hub.py:10438-10472`) — 60+ near-duplicate proposal files accumulated in one day as a direct result.

**Prevention:** trigger evaluation returns `DUE | NOT_DUE | INDETERMINATE` — a missing or malformed counter is `INDETERMINATE`, never silently coerced to a numeric default. A persisted trigger cursor compares real source revisions, not modulo arithmetic alone. A scan emits a normalized `FindingSet` with a fingerprint over sorted stable finding identities. **(Round 8-9 SSOT fix: the fingerprint is computed exclusively, server-side, by `governance.proposals` from that canonical `FindingSet` — a caller-supplied fingerprint is rejected outright, never accepted as an alternative source, so mismatched or differently-normalized client fingerprints can't create duplicate proposals the uniqueness index fails to catch. A changed normalization algorithm gets an explicit new `fingerprint_algorithm_version`, never a silent reinterpretation of old proposals under the same version.)** Proposal identity is `(proposal_kind, workspace_scope, finding_fingerprint, lifecycle_generation)`; a database unique partial index permits at most one active proposal per identity, with creation as one transaction (insert-on-conflict/read-existing) so concurrent session-end processes still converge on the same proposal. A genuinely changed finding set gets a different fingerprint and may create a new proposal; rediscovering an identical closed set follows an explicit reopen/cooldown policy, never a bare filename counter. Pending handoff/dashboard entries are projections of the proposal/outbox event, never independently appended.

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
- **Phase 3 — adapter/provider conformance.** Build the conformance fixtures **inside the repository's own test suite** (Round 4-5 deferral: do NOT publish a `peerhub.testing` package in v1 — that creates a supported public API and compatibility burden with no external adapter consumer yet; publish a kit only when the first third-party adapter is actually accepted, consistent with §16's third-party-adapter deferral). Migrate built-ins incrementally (simplest pipe adapter first, then PTY/staging, then the richest JSONL/session adapter). Every adapter passes create/resume, decoder, artifact, cancellation, error, fingerprint fixtures. Test the valid `usage_provider=None` case. Live probes are opt-in empirical tests, never routine CI blockers.
- **Phase 3.5 — coordination slice.** Room/membership/presence/message/handoff/checkpoint reducers and service (`coordination/` — §2), covering the mechanisms `hub.py` currently implements at the cited line ranges. Golden transcripts for send/broadcast/read/handoff, matching Phase 0's characterization set.
- **Phase 4 — telemetry, health, routing.** Dependency-declared collector fan-out + typed evidence first, including T87 differential tests. Availability/admission projections + probe-based recovery. Routing as pure policy with golden decisions. Provider-process isolation + failure-amplification tests before enabling live usage routing.
- **Phase 5 — consensus, proposals, governed mutations.** Frozen electorate/rule, immutable votes, timeouts, arbiter separation, atomic decision outbox. Finding fingerprints, trigger cursors, active-proposal uniqueness, concurrent T89 tests. `MutationRequest`/`Plan`/`Receipt`, broker-inbox import, effect worker, saga reconciliation.
- **Phase 6 — versioned surfaces and strangler integration.** Expose the JSONL service + CLI through the same `application.api`. Run current `hub.py` in shadow/dual-read mode against `peerhub` decisions. Move one mechanism at a time behind the facade with rollback. Never maintain two write authorities for the same record. Retire old paths only after behavior/recovery/evidence parity is demonstrated.
- **Phase 7 — hardening and release.** Concurrent-client, crash-at-every-transition, malformed/truncated-protocol, provider-exhaustion, process-tree-leak, long-run proposal-dedup tests. Golden fixtures for current + previous major protocol. Local-filesystem transaction/locking probes on supported platforms. Normal PyPI package release with a console entry point. No vendor CLI bundling, no self-updater, no package-manager orchestration, no host-lifecycle framework — that boundary is permanent (§2.1 rule 8).

## 16. Remaining questions (reclassified, Round 4-5)

Storage, layering, outcome-model, service-model, coordination ownership,
and the completion-contract/status inconsistencies are all resolved
(§§2-4, 6.1a, 9, 10.1). The old flat "13 open questions" list conflated
four genuinely different categories (cx's Round 4 finding) and contained
two live inconsistencies with resolved sections (network-filesystem
support already resolved local-only in §4; "current + previous major"
in Phase 7 assumed a policy §16.13 called undecided, but v1 has no
previous major to support). Reclassified below; no item here blocks
Phase 0 from starting.

### 16.1 Phase 0 decisions (must be resolved before/during Phase 0)

1. State scope — one `PeerHubHome` DB per user vs. per-workspace, and how one service coordinates multiple workspaces if per-user.
2. `UsageProvider` granularity — per-request cost estimates, or pool/account quota percentage only?
3. Concrete health failure categories/thresholds/cooldowns — policy data, not hardcoded in reducers.
4. Whether v1 includes any budget authority at all (§11) — a Phase 0 decision, not an implementation detail.
5. Default UI wording for `DELIVERED_UNVERIFIED` — honest without making ordinary asks look failed.

### 16.2 Implementation/empirical spikes (resolved by building and measuring, not by debate)

6. Windows PTY emulation approach for `AgyAdapter` (POSIX `pty`/`termios` on Linux/macOS; native ConPTY via `ctypes` or a `winpty` binding on Windows — cross-platform from the start, not a Windows-only assumption).
7. SQLite lock/transaction probe behavior on the actual target filesystem matrix — `TEST NEEDED`, not assumed; v1 stays local-filesystem-only per §4 regardless of outcome.
8. Whether a scoped, empirically-verified vendor-state locator can ever qualify as a `SessionCapability` (modification-time selection alone must not — §6.3).
9. Provider isolation cost boundary — which telemetry collectors need a supervised worker process vs. safe in-process execution.
10. Proposal-dedup tombstone TTL — how long a closed/rejected fingerprint stays in the index before a re-proposal is allowed.

### 16.3 Explicitly deferred until a real trigger fires (do not design speculatively)

11. Public Python surface beyond the stable `Client` — wait for a real second consumer's concrete need before stabilizing more internals.
12. Third-party adapter discovery/signing, and publishing the `peerhub.testing` conformance kit (§15 Phase 3) — wait for a real third-party adapter.
13. Protocol support window beyond v1 (e.g. "current + previous major") — v1 has no previous major; decide when the first breaking major is actually proposed, test protocol v1 only until then.
14. Persistent `serve --stdio` hosting (§3) — wait for a real client needing multi-command connection reuse.
15. `BudgetReservation` state machine (§11) — wait for Phase 0 to find a measured oversubscription problem and name an authoritative reservable unit.

---

*Process record, full evidence citations, and the 3-round convergence debate
that produced this document: `docs/design/peerhub-architecture-debate.md`
in this same directory.*
