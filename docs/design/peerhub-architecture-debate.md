# peerhub Architecture Debate — Running Ledger

> Status: living, pre-TDD design phase. No code/tests are written from this
> document until a future, separate round explicitly authorizes moving to
> implementation. This ledger accumulates rounds in place; do not fork a new
> file per round.

## Charter (Round 0)

**Mission:** `peerhub` is a standalone, installable Python package (`pip
install peerhub`) that coordinates multiple AI CLI agents (Claude Code,
Codex, Antigravity, and future peers) as collaborating peers: dispatch,
routing, consensus, health/quarantine, session/lease lifecycle, IPC, and
brokered mutations. It is designed to eventually fully replace the
communication/coordination responsibilities currently implemented inside
`_sys/core/hub.py` in the portable-dev-env ("Engram") project.

**Why this exists now, and why it's different from the last attempt:** on
2026-07-20, a 10-round debate designed almost this exact architecture, then
unanimously reversed itself because there was no validated second consumer
— the design would have been speculative infrastructure. `peerhub` **is**
that second consumer: a real, separate, user-created repository
(`github.com/greatgc-flow/peerhub`), explicitly intended to replace
`hub.py`. The objection that shelved the prior debate is resolved. The
2026-07-20 design work is not wasted — it is this debate's starting
substrate, not its conclusion.

**Two source materials to reconcile — ground everything in both, not just one:**

1. **`_sys/core/hub.py`** (portable-dev-env repo, ~11,000 lines) — the
   real, currently-running, battle-tested implementation. Treat it as the
   empirical baseline of "what already works and has survived real
   operational failures": the dispatch/ask lifecycle (PTY + subprocess
   paths), session/lease management (note: `_lease_open`/`_lease_renew`/
   `_lease_close` are uuid4-keyed and pid-checked — this is the T83 fix,
   a real concurrency bug found and corrected; don't reintroduce the
   peer_id-keyed clobbering bug it replaced), health/quarantine, routing
   (token load balancer, final arbiter), consensus (R:10 voting protocol),
   the IPC query-file protocol, brokered mutations (governed-mutation
   guard / hash-watch), and proposal governance. Read the actual code for
   these mechanisms — do not describe them from memory or assumption.
2. **`_sys/docs-v2/ops/engram-refactor-blueprint-2026-07-20.md`** — the
   shelved clean-room target design: `PeerAdapter`/`UsageProvider` split
   (§3), the concurrency/crash-recovery/budget-reservation lifecycle
   (§4), the protocol versioning contract (§5), governance model (§6),
   testing pyramid (§7), and client trust/blast-radius model (§8). The
   doc ends at §10 — it does NOT contain "§13/§14"; that numbering
   belongs to a DIFFERENT document, `phase2-arch-general-specific-
   2026-07-22.md`, which has its own §13.15 (Governed Mutation Protocol)
   and §13.16 (Evidence Feedback Loop), and whose §14 (Host Distribution,
   Packaging & User Lifecycle) targeted a different, heavier product (a
   Windows desktop app with a signed native installer and embedded
   Python runtime) — re-evaluate that material for fit against
   `peerhub`'s actual, much lighter distribution need (a normal PyPI
   package with a CLI entry point), don't copy-paste it uncritically.
   **(Correction 2026-07-27: the original charter conflated these two
   documents' section numbers — caught and fixed by `cx.deepthink` in its
   Round 1 draft, provenance-corrected, not a design disagreement.)**

**Also ground in the real defects already tracked against `hub.py` in the
portable-dev-env backlog** (`_sys/ai/backlog.json`) — `peerhub`'s design
should structurally prevent these categories of bug, not just carry them
forward:
- **T87**: `gather_peer()`-style early-return control flow that
  conflates "status metadata unavailable" with "skip all peer-specific
  collection," silently losing a live data source.
- **T88**: a peer can report success (`exit 0`) while silently
  under-delivering (a truncated reply on a task that clearly needed
  substantial output) — no distinguishing signal from a legitimately
  short correct answer.
- **T89**: a governance/proposal pipeline with no fingerprint-based
  deduplication and a trigger condition that silently always fires,
  causing unbounded duplicate accumulation.

**Scope boundary for this debate — pre-TDD design only:**
Deliverable is a converged architecture document: module boundaries,
protocol/contract shapes, state machines, failure semantics, and phasing —
ready to hand to a future, separately-authorized TDD implementation round.
**No code, no tests, no scaffolding in this phase.**

**Process (mirrors the 2026-07-20 precedent, which is the proven format
for this kind of debate in this project):**
- Unlimited rounds — continue for as many rounds as needed. No artificial
  cap. Stop only on genuine convergence (both `ag` and `cx` independently
  return an explicit **CONVERGED** verdict with no new HIGH/MEDIUM finding
  for 2 consecutive rounds), or if `cc` flags the round count has grown
  large enough (~15-20+) to warrant a user check-in before continuing
  further — a budget-awareness safeguard, not a hard stop.
  Alternating structure: one side drafts/revises, the other critiques
  with concrete findings (not vague concerns) or proposes a reject-with-
  alternative; roles can swap round to round.
- Every finding must be evidence-based: cite real `hub.py` line ranges,
  real backlog items, or a concrete failure scenario — not speculation
  (DIR-004).
- **Process amendment carried over from the 2026-07-20 debate (adopt
  again here):** deferral must be symmetric with addition — scope removed
  in one round needs the same explicit justification to add back as it
  did to remove. The default between any two rounds is the smallest
  surviving plan.
- `cc` (terminal) relays between `ag` and `cx`, keeps this ledger current,
  and synthesizes tersely between rounds rather than re-deriving analysis
  independently — this round is `ag`/`cx`-resourced by explicit user
  direction; `cc` conserves its own tokens.
- Every round appends to this ledger (below), never forks a new file.

---

## Round 1

### ag.deepthink draft (Round 1)

#### 1. Module & Package Boundary Proposal

`peerhub` is structured as a modular, standalone Python package (`peerhub`) with zero direct dependencies on `_sys/core/hub.py` or portable environment paths. All path discovery and runtime configuration are injected via `RuntimeContext` and `PathLayout`.

```
peerhub/
├── __init__.py
├── core/
│   ├── context.py          # PathLayout & RuntimeContext (immutable core, shared config, workspace .ai)
│   └── api.py              # PeerHub facade (canonical public API for dispatch, routing, consensus, state)
├── adapters/
│   ├── contract.py         # PeerAdapter, UsageProvider, PeerCapabilities, InvocationKind, PreparedInvocation
│   ├── registry.py         # Catalog-based adapter registration & conformance validation (adapter-conformance/v1)
│   └── builtins/           # Concrete peer implementations
│       ├── claude.py       # ClaudeAdapter (cc)
│       ├── codex.py        # CodexAdapter (cx)
│       └── agy.py          # AgyAdapter (ag)
├── dispatch/
│   ├── engine.py           # Process launcher, PTY/subprocess streaming, timeout & stream parsing
│   └── lease.py            # Transactional UUID-keyed request leases, PID + birthtime verification
├── routing/
│   ├── balancer.py         # Capacity-aware peer & profile routing, EXH quota fallback
│   └── arbiter.py          # Smartest-Model Final Arbiter (DIR-005) for unresolved dissent / high-risk tasks
├── consensus/
│   └── protocol.py         # R:10 voting protocol engine, single-shot proposal collection, vote immutability
├── health/
│   ├── quarantine.py       # Health state machine (GREEN/YELLOW/RED/QUARANTINED/RECOVERING), live re-check
│   └── diagnostics.py      # Decoupled telemetry collector (T87 fix)
├── ipc/
│   └── query_file.py       # Query-file IPC frame parser, result framing, status reporting
└── governance/
    ├── broker.py           # Governed mutation broker (hash-watch, transactional CAS file updates)
    └── proposal.py         # Deduplicated proposal engine (T89 fix)
```

---

#### 2. Core Peer-Adapter Contract Shape

Building on the blueprint's `PeerAdapter` / `UsageProvider` split, validated against `hub.py`'s actual dispatch needs (`_build_session_cmd`, PTY execution, environment construction, and output validation):

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Mapping, Sequence

class InvocationKind(Enum):
    HELP_OR_VERSION = "help_or_version"
    LOCAL_AGENT = "local_agent"
    REMOTE_AGENT = "remote_agent"
    ADMIN_OR_SERVICE = "admin_or_service"

@dataclass(frozen=True)
class PeerCapabilities:
    requires_pty: bool
    supports_streaming: bool
    supported_invocation_kinds: set[InvocationKind]
    default_timeout_sec: int

@dataclass(frozen=True)
class PreparedInvocation:
    peer_id: str
    argv: list[str]
    env: dict[str, str]
    cwd: str
    requires_pty: bool
    effective_model: str | None
    effective_profile: str
    banner_message: str | None

@dataclass(frozen=True)
class OutputValidationResult:
    is_valid: bool
    is_truncated: bool
    failure_reason: str | None

class PeerAdapter(ABC):
    @property
    @abstractmethod
    def peer_id(self) -> str: ...

    @property
    @abstractmethod
    def capabilities(self) -> PeerCapabilities: ...

    @abstractmethod
    def prepare_invocation(
        self,
        query: str,
        profile_name: str,
        extra_args: Sequence[str],
        cwd: str,
        env_overlay: Mapping[str, str]
    ) -> PreparedInvocation:
        """Constructs final argv, environment, cwd, root-scope flag placement, and banner message."""
        ...

    @abstractmethod
    def parse_stream_chunk(self, chunk: bytes) -> Iterator[str]:
        """Translates raw stdout/stderr bytes into normalized output text or events."""
        ...

    @abstractmethod
    def validate_output(
        self,
        query: str,
        raw_output: str,
        exit_code: int
    ) -> OutputValidationResult:
        """Validates output completeness against query intent (structural defense against T88)."""
        ...


class UsageProvider(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @abstractmethod
    def get_quota_status(self) -> dict:
        """Returns structured quota metrics. Never returns empty/null as a false failure."""
        ...
```

**Key Refinements over Blueprint**:
1. **`validate_output` method**: Added to `PeerAdapter` to enforce response completeness validation (length, structural refusal detection) before declaring process success.
2. **`PreparedInvocation` dataclass**: Encapsulates root-scope flag insertion (from `peer_console.py` C8-B), PTY requirements, environment overrides, and truthful banner messages.
3. **Explicit Optional `UsageProvider`**: `UsageProvider` is strictly `Optional[UsageProvider]`. Peers without external quota APIs (e.g. `ag`) return `None` honestly without creating dummy quota stubs.

---

#### 3. Core State Machines

##### A. Session / Request / Lease Lifecycle State Machine
```
[CREATED] ──(open_lease)──> [LEASED] ──(prepare)──> [DISPATCH_INTENT]
                                                         │
                                                  (process spawn)
                                                         ▼
[COMPLETED] <──(exit 0 & valid)── [STREAMING] <────── [LAUNCHED]
     │                               │                    │
 (failure)                       (timeout)             (crash)
     ▼                               ▼                    ▼
 [FAILED]                        [FENCED]            [INTERRUPTED]
```
- **Lease Keying**: Keyed by unique `lease_id` (`uuid.uuid4().hex`), NOT `peer_id` or `ask_id`.
- **Ownership Validation**: `_lease_renew` and `_lease_close` require matching `(lease_id, pid, process_birth_time)`. Mismatches raise typed ownership errors (`LeaseOwnershipError`).
- **Recovery & Fencing**: On timeout, `FENCED` status invalidates the lease. PID + process birth-time verification ensures only the exact child process is terminated before lease cleanup.

##### B. Consensus Round Lifecycle State Machine (R:10 Protocol)
```
[PROPOSED] ──(init_round)──> [COLLECTING_VOTES] ──(all_voted)──> [VOTES_EVALUATED]
                                                                        │
                   ┌────────────────────────────────────────────────────┴──────────────────────────────────┐
                   ▼                                                    ▼                                  ▼
           [UNANIMOUS_AGREE]                                    [UNANIMOUS_REJECT]                 [DISSENT_DETECTED]
                   │                                                    │                                  │
                   ▼                                                    ▼                         (check DIR-005 arbiter)
            (apply mutation)                                   (abort proposal)                            │
                                                                                ┌──────────────────────────┴──────────────────────────┐
                                                                                ▼                                                     ▼
                                                                        [ARBITER_REVIEW]                                    [ESCALATED_TO_USER]
                                                                                │
                                                                                ▼
                                                                       [ARBITER_FINALIZED]
```
- **Vote Immutability**: Once recorded for a `round_id`, a peer's vote is immutable and cannot be overwritten.
- **Live Re-Check Integration**: If a voter peer is flagged as `QUARANTINED`, `peerhub` executes a live health probe before excluding the voter from the round quorum.

##### C. Health & Quarantine State Machine
```
[HEALTHY (GREEN)] ──(failure threshold)──> [DEGRADED (YELLOW)] ──(consecutive failures)──> [FAILED (RED)]
       ▲                                                                                     │
       │                                                                               (quarantine)
       │                                                                                     ▼
  (probe ok) <── [PROBING] <──(live recheck / scheduled)── [RECOVERING] <─────────── [QUARANTINED]
```
- **Live Recheck Guarantee**: `QUARANTINED` status does not permanently block dispatch; every routing request triggers an asynchronous live re-check probe if cooldown has elapsed.
- **Decoupled Evaluation**: Health state evaluates process execution & exit codes, never diagnostic metadata availability.

---

#### 4. Failure-Semantics: Structural Prevention of Backlog Defects

##### T87 Prevention (Decoupled Telemetry Pipeline vs. Early-Return Short Circuit)
- **Defect in `hub.py`**: `snapshot.py`'s `gather_peer()` contained `if not data and not health_data: return info`, aborting peer-specific data collection when top-level health metadata was missing.
- **`peerhub` Structural Fix**: Decouple diagnostic data collection into a **Pipeline-Filter Pattern** in `peerhub.health.diagnostics`. Every collector step (`gather_peer_health`, `gather_sqlite_metrics`, `gather_rollout_state`) is executed independently as an isolated step returning `CollectorResult(status=OK|UNAVAILABLE, data=...)`. Missing metadata in step A never aborts step B.

##### T88 Prevention (Output Completeness Validation Stage)
- **Defect in `hub.py`**: `hub.py ask` relied strictly on process exit code `0` to infer ask success, missing cases where `ag.opus`/`ag.gptoss` exited `0` but returned a truncated 171-character reply announcing internal delegation without delivering content.
- **`peerhub` Structural Fix**: Mandatory `validate_output()` phase in `PeerAdapter`. For tasks categorized as requiring substantial output (`workload=code_generation|deep_review`), if the returned text is below minimum character threshold or contains unexecuted delegation markers, `peerhub` overrides exit code `0` and sets `status = OUTPUT_VALIDATION_FAILED` (triggering targeted retry or fallback).

##### T89 Prevention (Fingerprint-Based Deduplication & Schema Safety)
- **Defect in `hub.py`**: `saturation_scan.py` evaluated `commit_count % 10 != 0` on `commit_count` which was missing from `.ai/state.json`, evaluating `0 % 10 != 0` (False) on every session end. `action_proposal_add()` lacked content fingerprint deduplication, causing unbounded file accumulation.
- **`peerhub` Structural Fix**:
  1. **Typed Schema Accessors**: Missing state keys return explicit `None` rather than coercing to `0`. `None` skips trigger evaluation safely.
  2. **Mandatory SHA-256 Content Deduplication**: `peerhub.governance.proposal` computes a canonical SHA-256 fingerprint over normalized proposal title + finding set. `action_proposal_add()` checks existing open proposals by fingerprint; duplicate proposals return `DUPLICATE_PROPOSAL_IGNORED` without creating new proposal files.

---

#### 5. Phasing Sketch (Pre-TDD Implementation Order)

1. **Phase 1: Foundation & Contracts (`peerhub.core`, `peerhub.adapters.contract`)**
   - Implement `RuntimeContext`, `PathLayout`, `PeerAdapter`, `UsageProvider` abstract classes, and `adapter-conformance/v1` test harness.
2. **Phase 2: Dispatch & Lease Engine (`peerhub.dispatch`)**
   - Implement PTY / Subprocess runner, UUID4-keyed lease management (`_lease_open`/`renew`/`close`), atomic session state mutators.
3. **Phase 3: Health, Telemetry & Failure Guarding (`peerhub.health`, `peerhub.ipc`)**
   - Implement Health state machine, T87 pipeline-decoupled diagnostic gatherer, T88 output completeness validator, query-file IPC protocol.
4. **Phase 4: Consensus, Governance & Routing (`peerhub.consensus`, `peerhub.routing`, `peerhub.governance`)**
   - Implement R:10 voting engine, DIR-005 final arbiter, governed mutation broker, T89 deduplicated proposal engine.
5. **Phase 5: Strangler Adapters & Delegation Facade (`peerhub.adapters.builtins`)**
   - Implement `ClaudeAdapter`, `CodexAdapter`, `AgyAdapter`, and the `hub.py` delegation facade shim.

---

#### 6. Open Questions & Uncertainties for `cx` Critique

1. **`UsageProvider` Granularity**: Should `UsageProvider` report per-request cost estimates, or remain strictly pool/account quota percentage?
2. **PTY Emulation on Windows**: `AgyAdapter` requires WinPTY in `hub.py` today. Should `peerhub` vendor a thin WinPTY wrapper module or rely on Python 3.14+ standard `winpty`/`conpty` bindings?
3. **Proposal Deduplication Tombstone TTL**: When a proposal is closed or rejected, how long should its SHA-256 fingerprint stay in the deduplication tombstone index to prevent re-proposals?

---

### cx.deepthink independent draft (Round 1)

*(Written to the ledger by `cc` on cx's behalf — cx's sandbox rejected a direct file write to this path; cx returned the full draft as reply text instead. This is verbatim.)*

# peerhub Round 1 — cx.deepthink independent draft

Status: independent Round 1 design, pre-TDD; no implementation or scaffolding is proposed here.

Provenance note: by the time I read the charter in full, its file already contained another peer's appended Round 1 text. I did not use that text as design input. This draft is derived from the charter's Round 0 requirements, targeted reads of the live implementation, and the architecture sources named below. This caveat preserves the intended independence constraint explicitly.

## 1. Position in one paragraph

`peerhub` should be a single-machine coordination engine with one canonical application boundary, pure domain state machines, stateless peer adapters, and one transactional local state authority. The stable compatibility surface should be versioned commands, results, and events; the Python client and CLI should be thin transports over the same command handlers. Peer processes, PTYs, vendor session formats, and vendor telemetry remain replaceable infrastructure. No adapter may own routing, health, leases, persistence, or the definition of success. The engine should persist dispatch intent before spawning, use process-birth identity rather than PID alone, treat every command as idempotent, and emit state plus an outbox event in the same local transaction. SQLite is my proposed first authoritative store because this is a concurrent local coordinator and its hardest existing failures are precisely cross-file read/modify/write, uniqueness, and partial-side-effect failures. Locks and database transactions must never span a model call.

The important boundary is not "generic versus peer-specific" in the abstract. It is:

- Core owns policy, state, admission, orchestration, and evidence semantics.
- Adapters translate between a typed invocation/result contract and one peer CLI.
- Infrastructure performs effects under core-issued plans.
- Transports can request actions but cannot bypass the application boundary.
- Observations are immutable evidence, while routing, health, and consensus records are mutable, revisioned decisions derived from that evidence.

## 2. Grounding evidence and corrections

I used these source facts as constraints, not as instructions to copy the existing shape.

### 2.1 Live behavior worth preserving

- The current adapter contract proves that peer-specific command construction, session create/resume, prompt transport, response parsing, session-ID extraction, and usage extraction differ materially (`_sys/core/hub_peer.py:483-545`). `PreparedInvocation` also proves that the core needs argv, stdin bytes, staged-artifact ownership, and cleanup metadata rather than merely a shell command (`hub_peer.py:64-85`).
- The oversized Antigravity prompt path is a real example of why invocation planning must represent staged payloads, byte counts, digests, and cleanup (`hub_peer.py:972-1045`). That behavior belongs behind an adapter contract, but actual filesystem materialization and deletion should be core-supervised.
- The pipe path has real incremental-output and silence semantics: independent stdout/stderr drains, activity timestamps, a hard deadline, zombie timeout, process-tree termination, and lease heartbeats (`_sys/core/hub.py:4562-4677`). PTY and pipe are different runners, not two different dispatch domains.
- T83 fixed two actual concurrency defects. Session state now performs read-modify-write under one lock (`hub.py:4081-4096`), and leases are keyed by UUID with ownership checks rather than by peer ID (`hub.py:10707-10773`). Those are invariants to preserve, although a transactional store can express them more directly.
- Routing already demonstrates the right audit shape: pure selection over an input snapshot, deterministic seed, complete candidates, exclusions, weights/probabilities, and a reason (`_sys/core/snapshot.py:2261-2273`, `2410-2444`, `2550-2590`). The formulas may remain policy while this evidence shape becomes contractual.
- Consensus correctly snapshots its rule and electorate observations at round creation and keeps vote writes immutable/idempotent (`hub.py:7619-7653`, `7664-7702`, `7781-7830`). The broker's vote-merge path also reads current state under the same per-round lock rather than replacing a stale whole file (`hub.py:7715-7757`).
- Brokered mutations already contain the right seeds: target validation, authorization, a common resource lock, expected-revision CAS, journal intent, atomic replacement, immutable queued requests, and deterministic draining (`hub.py:819-859`, `942-1059`).
- The current code explicitly suppresses automatic retry once a process may have executed because replay can duplicate side effects (`hub.py:1951-1978`). That is a core semantic rule, not merely an error-message choice.

### 2.2 Live behavior that should not become the new contract

- A lease is currently created only after `_spawn_process` returns (`hub.py:7046-7064`). That leaves no durable pre-spawn `dispatch_intent`. The blueprint's stronger order—intent before spawn, process identity after spawn—is the target (`_sys/docs-v2/ops/engram-refactor-blueprint-2026-07-20.md:96-104`).
- Current leases retain PID but no process-birth identity (`hub.py:10722-10732`), while sweep kills a live PID after expiry (`hub.py:10883-10904`). A reused PID can identify an unrelated process. `peerhub` must persist a platform process identity, such as PID plus creation time and, where available, a process-handle-derived identity.
- Current session state is mostly "active or retired," persisted after a successful-looking result (`hub.py:4099-4163`). It does not model creating, in-use, uncertain after crash, or verified recovery. These need explicit states.
- Manual recovery writes GREEN and opens the gate directly (`hub.py:3501-3524`). That erases the distinction between an administrative instruction to attempt recovery and measured proof that the peer is ready.
- Success is currently "exit code 0, nonempty parsed output, and successful optional output-file write" on both PTY and pipe paths (`hub.py:6839-6870`, `6970-6987`, `7235-7335`). T88 shows that this is transport success, not task completion.
- `PeerAdapter.extract_usage` returns an untyped dictionary, and implementations commonly return `{}` for absence, parse failure, mismatch, and I/O failure (`hub_peer.py:699-729`, `844-890`, `1078-1105`). Those conditions must not remain indistinguishable.
- The current `PeerAdapter` includes session-persistence methods (`hub_peer.py:537-545`). An adapter should identify or describe a vendor session; the core repository must own authoritative session state.
- The current consensus snapshot stores all voters but also a reduced `required_voters` list based on health (`hub.py:7619-7651`), while `_decide_consensus` calculates unanimity only over that reduced list (`hub.py:7673-7701`). Whatever the intended surrounding policy, the new model must never silently turn "unavailable voter" into an affirmative unanimous vote. Electorate, eligibility evidence, and decision rule should be separate frozen fields.
- Current health combines observation, routing admission, rate-limit cooldown, manual quarantine, and profile/root aggregation into overlapping JSON fields (`hub.py:2123-2310`, `2537-2601`, `3487-3524`). A product state with orthogonal availability and admission axes is safer.

### 2.3 Source-location correction

The named blueprint file currently ends at section 10, not section 13. Its relevant decisions are D1-D5, the concurrency contract, protocol, governance, and testing (`engram-refactor-blueprint-2026-07-20.md:76-156`). The `MutationRequest -> MutationPlan -> TransitionReceipt` and Evidence/Recommendation material attributed in the charter to "§13.15/§13.16" actually appears in `_sys/docs-v2/ops/phase2-arch-general-specific-2026-07-22.md:586-626`. I used the actual locations. This is a documentation-provenance correction, not a design disagreement.

## 3. Package and module boundaries

Proposed source layout:

```text
peerhub/
  __init__.py                 # public Client, typed public values, version only
  client.py                   # thin in-process/remote client; never direct storage
  runtime.py                  # immutable RuntimeContext and composition root

  protocol/
    commands.py               # versioned command/result envelopes
    events.py                 # versioned event/evidence envelopes
    errors.py                 # stable machine error codes
    jsonl.py                  # framed JSONL transport and negotiation
    cli.py                    # CLI translation to the same command bus

  domain/
    dispatch.py               # request/outcome/session/lease FSMs
    consensus.py               # round/electorate/vote/final-opinion FSM
    health.py                  # observation + admission/quarantine FSMs
    routing.py                 # pure candidate eligibility and RouteDecision
    proposals.py                # trigger cursor, finding set, dedup identity
    mutations.py                # MutationRequest/Plan/Receipt and effect intents
    budgets.py                  # optional quota reservation authority
    evidence.py                 # measured/absent/unavailable/error value algebra

  application/
    service.py                 # sole command dispatch/enforcement boundary
    dispatch.py                 # orchestration across adapter, runner, store
    consensus.py
    health.py
    routing.py
    proposals.py
    recovery.py
    mutations.py

  peers/
    contract.py                 # PeerAdapter + optional capability protocols
    usage.py                    # UsageProvider contract
    registry.py                 # descriptor/config resolution, no routing decisions
    builtins/
      claude.py
      codex.py
      antigravity.py

  ports/
    state.py                    # UnitOfWork/repositories/outbox interfaces
    process.py                  # pipe/PTY supervision interface
    artifacts.py                 # staged input/output artifact materializer
    policy.py                    # authorization and decision policy interfaces
    clock.py                     # monotonic + wall-clock injection
    identity.py                  # IDs and process-birth identity
    telemetry.py                 # collector execution interface

  infrastructure/
    sqlite/
      store.py                  # authoritative records + atomic outbox
      migrations/
    process/
      pipe.py
      pty.py
      tree.py
    filesystem/
      artifacts.py
      broker_inbox.py            # optional create-only bridge for sandboxed clients
    telemetry/
      executor.py                # bounded fan-out, provider isolation, cache

  testing/                       # published adapter conformance kit, not runtime policy
```

### 3.1 Ownership rules

1. **`protocol` owns compatibility, not behavior.** It defines schemas, correlation, version negotiation, and stable error codes. It cannot import infrastructure or adapters.
2. **`domain` is pure.** It accepts values and returns transition decisions/effect intents. It cannot read files, clocks, environment variables, or vendor state.
3. **`application.service` is the only mutating entrance.** CLI, JSONL, and `peerhub.Client` all submit the same typed commands. Every authorization, idempotency, state transition, and audit rule is therefore transport-independent.
4. **`peers` translates; it does not coordinate.** Adapters may plan invocations and decode vendor output. They cannot spawn processes, select peers, modify health, persist sessions, acquire leases, append audit events, or decide task success.
5. **`infrastructure` executes plans, not policy.** A PTY runner can emit chunks and terminate a process tree, but cannot decide to retry, quarantine, or invoke an arbiter.
6. **`RuntimeContext` is immutable dependency injection.** It carries the state directory, workspace scope, registry snapshot, policy revision, clock, store, runners, and sinks. It is constructed once per service process; it is not a mutable global.
7. **Concrete peer IDs stay in built-in adapter registration/config.** Core code selects capabilities and descriptors, never `if peer == "cx"`. This retains the blueprint's D4 constraint (`engram-refactor-blueprint-2026-07-20.md:85`).
8. **Vendor installation is outside the engine.** A descriptor supplies a configured executable reference. Readiness canonicalizes and probes it; `peerhub` does not install, update, authenticate, or bundle vendor CLIs.

### 3.2 Runtime topology

The architecture should support two deployment forms without changing semantics:

- An embedded `peerhub.Client` connected to an in-process `ApplicationService`.
- A client connected to a long-running local service through versioned JSONL.

The command/event schema is the compatibility surface in both cases. For the first implementation I would choose a foreground local service or explicit `serve --stdio` process, not yet commit to a resident OS daemon. Multiple clients are safe because authoritative transitions are transactional and commands carry idempotency keys.

MCP is not a foundation. If added later, it is a translation adapter over the same command/event service, consistent with the blueprint's D1 reasoning (`engram-refactor-blueprint-2026-07-20.md:82`).

### 3.3 Authoritative state

I propose one SQLite database per configured `PeerHubHome` as the v1 authoritative store:

- Request, request-attempt, process-lease, and session-binding records.
- Health observations and current health/admission projections.
- Routing decisions.
- Consensus rounds, electorate snapshots, votes, and arbiter opinions.
- Proposal finding sets, trigger cursors, and proposal lifecycles.
- Mutation requests, receipts, effect intents, and an event outbox.
- Optional budget reservations.

Large transcripts, staged prompts, and output artifacts remain files referenced by content digest and length. They are not embedded blindly in the database.

Required store properties:

- Short `BEGIN IMMEDIATE` write transactions with revision/CAS checks.
- Unique constraints for command idempotency, one vote per voter/round, one active proposal per dedup identity, and one live session binding per scope.
- State transition plus outbox event committed atomically.
- Immutable evidence rows; mutable projections always carry revision.
- No database transaction held while waiting on a peer, provider, filesystem effect, or network call.
- Recovery derived from authoritative records and effect intents, never inferred only from log text.

This is intentionally local, not a distributed-system design. SQLite removes bespoke JSON locking and supplies the exact atomicity and uniqueness needed by the observed defect classes. Support for a state directory on network filesystems is `TEST NEEDED`; v1 should document local-filesystem storage as a requirement and fail startup if its lock/transaction probe fails rather than claim unmeasured safety.

## 4. Public command, event, and error contract

All commands carry:

```text
protocol_version
command_id            # caller-generated idempotency key
correlation_id
client_id
workspace_scope
expected_policy_revision?
method
params
```

All events carry:

```text
protocol_version
event_id
correlation_id
request_id?
round_id?
sequence               # monotonic within the correlated stream
occurred_at
kind
payload
evidence_refs[]
```

A client must negotiate `initialize` before effects. Major versions may break; minor versions are additive. Unknown required fields, unsupported versions, truncated frames, duplicate IDs with different content, and invalid transitions return stable typed errors before dispatch. This follows the blueprint's version-negotiation and golden-transcript direction (`engram-refactor-blueprint-2026-07-20.md:110-128`).

Errors use a stable envelope:

```text
code                     # e.g. PEER_UNAVAILABLE, REVISION_CONFLICT
phase                    # validation/admission/pre_spawn/post_spawn/assessment/effect
execution_certainty      # NOT_STARTED/MAY_HAVE_STARTED/STARTED/TERMINAL
retry_disposition        # SAFE/UNSAFE/CONDITIONAL/NEVER
message
details                  # versioned, machine-readable
correlation_id
```

Exception class names and vendor prose are diagnostic details, never control flow. The core converts infrastructure exceptions exactly once at the application boundary.

## 5. Core peer contract

### 5.1 Why one large interface is the wrong shape

The live contract confirms several real needs, but it also mixes responsibilities. Command planning, staged-input I/O, context shaping, session persistence, output parsing, and usage collection are all present today (`hub_peer.py:483-545`). The blueprint correctly split `PeerAdapter` from `UsageProvider`; I would go one step further and make session support and graceful cancellation explicit capabilities rather than mandatory stub methods.

The adapter remains one registered object, but its descriptor declares which optional capability protocols it implements. Runtime `Protocol` shape checks are only a fast screen; each capability needs behavioral conformance fixtures. A descriptor that says `sessions=True` but lacks the session capability is a load-time error.

### 5.2 Descriptor

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

Profile, account, and quota-pool identifiers are data. They are not inferred from peer names. Registry loading validates uniqueness and referential integrity before a runtime is admitted.

### 5.3 Required `PeerAdapter`

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

    def new_decoder(
        self,
        plan: InvocationPlan,
    ) -> OutputDecoder: ...

    def interpret_exit(
        self,
        plan: InvocationPlan,
        process: ProcessTerminalEvidence,
        decoded: DecodedPeerEvidence,
    ) -> PeerProtocolResult: ...
```

The types do the boundary work.

**`AdapterRequest`**

- Contains the already-authorized request ID, prompt content or content reference, workspace scope, profile, and requested session policy.
- Contains no mutable repository, logger, or global path.
- Carries an optional caller-supplied `CompletionContract`, but the adapter does not evaluate task-level acceptance.

**`InvocationPlan`**

- Immutable argv tokens, cwd policy, environment delta, transport kind, stdin payload, timeout/silence policy, and redacted display form.
- Declarative `ArtifactSpec` values for staged payloads, including content bytes/reference, SHA-256, expected length, access mode, and lifecycle.
- Artifact-path placeholders in argv/prompt rather than adapter-created files.
- Optional session action `NONE | CREATE | RESUME` and opaque external-session hint.
- Optional graceful-cancel recipe; the process supervisor still owns escalation and tree termination.
- No shell command string and no ambient environment capture.

The central `ArtifactMaterializer` replaces placeholders, creates files with create-new semantics, verifies digest/length round trips, records ownership, and deletes only after the supervised process tree is terminal. This preserves the real Antigravity staging need without letting adapters own unmanaged filesystem effects.

**`OutputDecoder`**

- Consumes ordered stdout, stderr, or PTY chunks and emits typed progress, assistant-text, session-identity, usage-hint, vendor-error, and completion-marker evidence.
- Is per-invocation mutable parsing state, not a singleton.
- Never writes state or calls another service.
- Has a bounded-memory policy, with transcript spill handled by core artifact infrastructure.

**`PeerProtocolResult`**

- Says whether the vendor protocol was parsed, whether the vendor emitted a positive completion marker, the assistant messages, external-session observation, and classified vendor failure.
- Does not say whether the user's task was fulfilled.
- Retains raw evidence references and parser version so an assessment is reproducible.

### 5.4 Optional session capability

```python
class SessionCapability(Protocol):
    def fingerprint(self, profile: ProfileDescriptor) -> str: ...
    def validate_resume_hint(self, hint: SessionHint) -> ResumeDisposition: ...
```

Session persistence is core-owned. An adapter may extract an external session ID from correlated output. If a vendor does not emit a strongly correlated session identity, the result is `UNKNOWN`, and automatic reuse stays disabled. The current Antigravity fallback picks the newest filesystem directory by modification time (`hub_peer.py:1060-1074`); that is useful evidence of why time-based discovery must not count as a verified binding under concurrency.

### 5.5 Separate `UsageProvider`

```python
class UsageProvider(Protocol):
    descriptor: UsageProviderDescriptor

    def collect(
        self,
        query: UsageQuery,
        deadline: Deadline,
    ) -> UsageEvidence: ...
```

`UsageEvidence` is never `{}`. It contains:

- State: `MEASURED | ABSENT | UNAVAILABLE | ERROR | STALE`.
- Source tag and provider version.
- Observed/captured timestamps and freshness.
- Peer/profile/account/quota-pool/session scope.
- Numeric values only when measured.
- Error category and retry hint when unavailable/error.
- Raw evidence reference/digest.

No-provider is valid and explicit. A provider failure cannot change a peer invocation result. Collectors run under independent deadlines and circuit breakers. Any provider capable of blocking in external I/O should run in a supervised worker process so a timeout can actually terminate it; a timed-out Python thread is not containment. This directly addresses the blueprint's unresolved provider-failure-amplification item (`engram-refactor-blueprint-2026-07-20.md:152-156`).

### 5.6 Readiness

Readiness is a separate probe contract because adapter conformance and vendor-executable readiness are different evidence:

- Adapter conformance proves the Python adapter against fixtures.
- Dependency readiness proves the configured executable identity, version, and capabilities now.
- A mutable readiness binding references the current immutable receipts.

The engine canonicalizes the configured executable, records its identity, and probes declared capabilities. It neither installs nor updates that executable.

## 6. Dispatch, session, and lease state machines

One overloaded lifecycle would recreate ambiguity. I propose three related state machines plus explicit attempt records.

### 6.1 Request lifecycle

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

Rules:

1. `command_id` creates or returns one request; the same ID with different canonical content is `IDEMPOTENCY_CONFLICT`.
2. Admission freezes the peer/profile, route decision, policy revision, completion contract, and optional budget reservation.
3. `PREPARED` means the invocation and artifacts validate, but no peer can have run.
4. `DISPATCH_INTENT` and lease ownership are committed before spawn. This is the replay-safety boundary.
5. After spawn, process-birth identity is committed in `RUNNING`. A crash between spawn and that commit produces `START_UNCERTAIN`, never an automatic retry.
6. Stream/progress events do not change the authoritative outcome by themselves.
7. Process terminal evidence moves to `ASSESSING`; assessment writes one of the completion outcomes.
8. A retry is a new `Attempt` under the same request, allowed only when the prior attempt is proven pre-dispatch or the request's side-effect/retry policy explicitly permits it. Each attempt has its own lease.
9. Every terminal transition emits an outbox event in the same transaction, so clients do not wait forever after recovery.

### 6.2 Lease lifecycle

```text
RESERVED
  -> ACTIVE
       -> RENEWED (self-transition, revision increments)
       -> RELEASED
       -> EXPIRED
            -> FENCING
                 -> FENCED
                 -> IDENTITY_MISMATCH
       -> OWNERSHIP_LOST
  -> ABANDONED_PRE_SPAWN
```

A lease is keyed by a cryptographically strong lease ID and linked to request ID, attempt ID, peer/profile, coordinator epoch, owner instance, heartbeat/expiry, and process identity.

Rules:

- `RESERVED` is created atomically with `DISPATCH_INTENT`.
- `ACTIVE` requires recorded PID plus process-creation identity. PID alone is insufficient for renew, close, or kill.
- Renew and close require lease ID, owner instance, coordinator epoch, and expected revision; process actions also require a birth-identity match.
- Expiry is an ownership fact, not proof that the child is dead. Recovery first verifies identity.
- A verified-live expired child is fenced from publishing success, then terminated; a dead matching child becomes `INTERRUPTED`; an identity mismatch is quarantined for human or recovery review and never killed.
- Closing the lease and recording the request's terminal state occur in one transaction where possible. Cleanup effects can follow through durable effect intents.
- Coordinator leadership, if needed, is a separate epoch lease. A new coordinator can recover old request leases only after proving its epoch and using CAS.

This keeps the T83 uniqueness/ownership improvement while adding the blueprint's missing birth identity and pre-spawn intent.

### 6.3 Session-binding lifecycle

```text
ABSENT
  -> CREATING
       -> ACTIVE
       -> UNKNOWN
  ACTIVE
       -> IN_USE
            -> ACTIVE
            -> SUSPECT
            -> RETIRED
       -> STALE
       -> RETIRED
  SUSPECT
       -> VERIFYING
            -> ACTIVE
            -> RETIRED
  UNKNOWN
       -> VERIFYING
            -> ACTIVE
            -> RETIRED
```

A binding key is `(workspace_scope, peer_id, profile_id, conversation_scope)`, not merely peer ID. It stores an external opaque ID, adapter fingerprint, generation, last verified evidence, and concurrency policy.

Rules:

- Only `ACTIVE` can be reused.
- Resume requires the adapter fingerprint, configured executable readiness binding, profile, and session generation to match.
- A session becomes `IN_USE` under CAS before dispatch if the vendor cannot safely accept concurrent turns.
- An interrupted or start-uncertain request moves the session to `SUSPECT/UNKNOWN`, never directly back to active.
- Only correlated vendor evidence may verify it.
- Permanent resume rejection retires the generation; a fresh session is a new generation.
- Session state is updated only after request assessment by the application service, not by the adapter.

### 6.4 Three distinct outcome layers

The engine must preserve:

1. **Execution outcome:** did the process start, time out, crash, or exit with code N?
2. **Peer-protocol outcome:** did the adapter parse a valid response, completion marker, or session result?
3. **Task outcome:** did the response satisfy the caller's completion contract?

Exit 0 plus nonempty text establishes at most `DELIVERED_UNVERIFIED`. `SUCCEEDED_VERIFIED` requires a positive, reproducible acceptance result:

- A required artifact exists under the expected scope and digest rules.
- A structured result validates against a schema.
- Required named sections or fields are present.
- A caller-provided verifier passes.
- A vendor-native completion receipt exists and the caller's policy explicitly accepts it.

For unconstrained prose, semantic completeness is generally not decidable from output length. The honest state is `DELIVERED_UNVERIFIED`, not fabricated success. A short-response heuristic can be an evidence signal, never the success rule. Automatic retry of an incomplete or unverified attempt remains unsafe unless the caller declared the task replay-safe.

## 7. Consensus state machine

### 7.1 Round states

```text
DRAFT
  -> VOTING
       -> DECIDING
            -> APPROVED
            -> REJECTED
            -> ESCALATED
            -> ARBITRATION_PENDING
                 -> OVERRIDDEN_APPROVE
                 -> OVERRIDDEN_REJECT
                 -> ESCALATED
       -> EXPIRED -> ESCALATED
```

`APPROVED`, `REJECTED`, `ESCALATED`, and override outcomes are terminal.

### 7.2 Frozen round contract

Creating a round atomically freezes:

- Full electorate.
- Policy revision and collaboration rate.
- Decision rule and minimum participation.
- Risk classification and whether arbiter override is permitted.
- Health/readiness evidence for every voter, without deleting any voter.
- Deadline.
- Proposer and subject digest.
- Round revision.

Electorate membership and current eligibility are different concepts. An unavailable member may make the result `ESCALATED`, but must never silently reduce an R:10 unanimity denominator. A voter exclusion, if policy genuinely allows it, must be a recorded policy decision with a reason; it is not a side effect of a health read.

### 7.3 Votes and decision

- `(round_id, voter_id)` is unique.
- An identical resubmission is idempotent; a different second vote is rejected.
- Votes are immutable evidence.
- The decision reducer is pure and uses only the frozen round contract plus votes.
- A disagreement may close cheap-peer voting, but arbiter invocation is a separate application transition after the base result commits.
- Arbiter selection uses its own policy and budget, records its candidate set and reason, and cannot run inside the consensus transaction.
- The base result and later final opinion are both retained; an override never rewrites history.
- Round state and its outbox decision event commit atomically. Decision capsules and handoff projections consume that event idempotently instead of being separately appended in the voting transaction.

This retains the current frozen-snapshot and immutable-vote strengths while preventing health-driven pseudo-unanimity and duplicate finalization effects.

## 8. Health and quarantine state model

"Health" currently means too many things. I propose immutable observations plus two orthogonal projections.

### 8.1 Availability projection

```text
UNKNOWN
  -> PROBING
       -> HEALTHY
       -> DEGRADED
       -> UNAVAILABLE
HEALTHY -> DEGRADED -> UNAVAILABLE
UNAVAILABLE -> PROBING
DEGRADED -> PROBING | HEALTHY | UNAVAILABLE
any measured state -> STALE (freshness expiry)
STALE -> PROBING
```

Availability answers: "What do current evidence sources establish?" Observations include source, scope (root, profile, or account), category, freshness, and correlation to an invocation or probe.

### 8.2 Admission/quarantine projection

```text
OPEN
  -> COOLDOWN
       -> RECOVERY_REQUIRED
       -> OPEN
  -> QUARANTINED
       -> RECOVERY_REQUIRED
            -> PROBE_AUTHORIZED
                 -> OPEN
                 -> QUARANTINED
```

Admission answers: "May new work be routed here?" It is policy derived from availability, rate limits, failure category, integrity evidence, and administrative action.

Rules:

- A rate-limit reset may end `COOLDOWN`; it does not prove general health.
- A transient profile failure closes only that profile when evidence is profile-scoped.
- Auth, fatal, or integrity conditions may close root admission under policy.
- Administrative "recover" authorizes a probe; it does not write HEALTHY/GREEN.
- Reopening requires a fresh successful probe tied to the current executable, readiness, and adapter fingerprint.
- Automatic thresholds use typed failure categories, not raw stderr text.
- Manual quarantine and automatic cooldown have separate reasons and histories.
- Routing consumes one frozen `AdmissionSnapshot`; it never mutates health during selection.
- Stale or unavailable evidence is not fabricated as healthy. Policy chooses fail-closed or explicit degraded routing and records that choice.

This separates the current `context_health.status`, `gate_open`, `quarantined`, and cooldown fields into states that cannot contradict one another.

## 9. Routing and budget semantics

Routing is a pure decision over:

- Requested capabilities and profile constraints.
- Readiness binding.
- Admission snapshot.
- Measured usage and headroom evidence.
- In-flight reservations.
- Terminal exclusion and cost policy.
- Task-size/context requirements.
- Deterministic request ID.

It returns a durable `RouteDecision` containing all candidates, exclusions with reason and evidence, representative profiles, effective weights, seed/draw if stochastic, selected target, and policy revision. The current snapshot output demonstrates that this audit shape is feasible (`snapshot.py:2261-2273`, `2570-2590`).

Missing usage is `ABSENT/UNAVAILABLE`, not zero. Policy may reject, use an explicit conservative fallback, or route without quota balancing; the decision records which. Selection bias remains visible because the complete considered set and reasons are logged.

If budget enforcement is enabled, `BudgetReservation` is its own authority:

```text
RESERVED -> DISPATCH_INTENT -> LAUNCHED -> CONSUMED
                                  \-> CONSUMED_UNKNOWN
RESERVED -> RELEASED   # only if definitely pre-dispatch
```

The usage provider reports evidence; it cannot reserve or release budget. A crash after dispatch intent conservatively becomes `CONSUMED_UNKNOWN`, matching the blueprint (`engram-refactor-blueprint-2026-07-20.md:101-104`).

## 10. Governed mutations and brokered effects

All authoritative record changes pass through:

```text
MutationRequest
  -> authorized + domain validated + expected revision checked
  -> MutationPlan
  -> atomic state transition + receipt + outbox/effect intents
  -> TransitionReceipt(COMMITTED_ENFORCEMENT_PENDING)
  -> effect worker
  -> TransitionReceipt(COMPLETED | EFFECT_FAILED)
```

The contract follows the actual §13.15 source (`phase2-arch-general-specific-2026-07-22.md:586-610`) and the current broker's useful CAS/journal behavior (`hub.py:819-859`).

Specifics:

- `MutationRequest` has operation, command, and correlation IDs; actor/client attribution; policy revision; target record ID; expected record revision; and a typed desired transition.
- Authorization and domain-invariant validation happen before plan commit.
- The transaction commits the new authoritative record, immutable receipt, and outbox/effect intent together.
- Filesystem or process effects happen after commit and are reconciled idempotently.
- Cross-record or cross-workspace operations are explicit sagas, not claimed distributed transactions.
- Sandboxed clients may submit a create-only immutable request file. The privileged broker validates and imports it by request ID; database uniqueness makes repeated imports harmless.
- Hashes are integrity and CAS evidence, not permission.
- Every effect has a stable idempotency key and result receipt.
- Command and observation planes stay separate. A vendor-side change is evidence; the resulting readiness-binding update is governed state.

`AuditedOperation` remains available for attempts that produce evidence but do not mutate authoritative state.

## 11. Structural prevention of T87, T88, and T89

### 11.1 T87 — one missing source suppresses independent collectors

**Observed class:** `gather_peer()` returns immediately when both status and health dictionaries are empty (`_sys/core/snapshot.py:878-927`), before the independent Codex SQLite, rollout, and app-server collectors at `snapshot.py:1094-1146`.

**Structural prevention:**

1. Telemetry collection is a declared fan-out plan, not one sequential peer-specific function.
2. Every collector declares only its actual dependencies. `CodexRateLimitProvider` depends on its endpoint/session capability, not on a status file.
3. The executor schedules every dependency-satisfied collector independently under a per-collector deadline.
4. Each collector returns a typed `EvidenceValue`: `MEASURED`, `ABSENT`, `UNAVAILABLE`, `ERROR`, or `STALE`.
5. Aggregation is total over collector results. Missing metadata may suppress only derived fields that require it, never unrelated collectors.
6. Overall collection state is `COMPLETE`, `PARTIAL`, or `UNAVAILABLE` with a source matrix. There is no global "empty, return now" branch.
7. Provider timeouts are isolated and cannot block other results; the aggregator emits partial evidence at the overall deadline.

Thus, the control-flow shape that created T87 is unavailable by construction.

### 11.2 T88 — exit 0 and nonempty output masquerade as task success

**Observed class:** the live paths classify exit 0 plus nonempty parsed output as success (`hub.py:6839-6870`, `6970-6987`, `7235-7335`), but a heavy Antigravity task returned only an intent-to-delegate message, created no requested artifact, and was recorded as successful (`_sys/ai/backlog.json:2300-2312`).

**Structural prevention:**

1. Execution, peer-protocol, and task outcomes are separate immutable fields.
2. `ProcessExit(0)` can never directly produce `SUCCEEDED_VERIFIED`.
3. Every dispatch freezes a `CompletionContract`. If it has no verifiable criterion, the best terminal state is `DELIVERED_UNVERIFIED`.
4. Artifact, schema, and field validators run centrally against evidence; adapters cannot declare semantic success.
5. A vendor-native completion marker is evidence, not a substitute for caller acceptance unless policy says so explicitly.
6. Missing required artifacts or failed validators produce `INCOMPLETE`, even with exit 0 and text.
7. Short-output or task-complexity heuristics may raise `SUSPICIOUS` evidence but never decide correctness.
8. Unverified or incomplete work is not automatically retried after a potentially effectful dispatch.

This cannot prove arbitrary prose semantically complete, but it structurally prevents the false claim that process success equals task success. Honest uncertainty is a first-class outcome.

### 11.3 T89 — missing trigger input plus no dedup floods proposals

**Observed class:** every session end launches self-care (`_sys/hooks/ctx_end.py:472-480`); missing `commit_count` defaults to `0` (`_sys/checks/saturation_scan.py:219-229`); `0 % 10` makes the scan run (`saturation_scan.py:279-285`); any stdout causes `proposal-add` (`_sys/checks/self_care.py:244-264`); and proposal creation only increments a filename sequence before appending another handoff entry (`hub.py:10438-10472`).

**Structural prevention:**

1. Trigger evaluation returns `DUE | NOT_DUE | INDETERMINATE`; a missing or malformed counter is `INDETERMINATE`, never a numeric default.
2. A persisted trigger cursor records the last evaluated source revision and last successful firing. "Every N commits" compares real source revisions; modulo alone is not the gate.
3. A scan emits a normalized `FindingSet` with a canonical schema and version and a fingerprint over sorted stable finding identities, not arbitrary stdout.
4. Proposal identity is `(proposal_kind, workspace_scope, finding_fingerprint, lifecycle_generation)`.
5. A database unique partial index permits at most one active proposal for that identity. Creation is one transaction using insert-on-conflict/read-existing, so concurrent session-end processes still return the same proposal.
6. A genuinely changed finding set has a different fingerprint and may create a new proposal. Closing and later rediscovering the identical set follows an explicit reopen or cooldown policy, not a filename counter.
7. Pending handoff/dashboard entries are projections of the proposal/outbox event. They are not independently appended, so one proposal produces at most one active projection entry.
8. Scheduler delivery may be at least once; command idempotency and proposal uniqueness make that safe.

The exact T89 acceptance criteria become database and transition invariants rather than best-effort checks.

## 12. Other failure semantics

### 12.1 Spawn ambiguity and replay

- Pre-spawn validation or admission failures are `NOT_STARTED`, safely retryable subject to policy.
- After `DISPATCH_INTENT`, a crash is `MAY_HAVE_STARTED`.
- After process-identity commit, it is `STARTED`.
- No automatic replay occurs at either uncertain boundary.
- Recovery emits a terminal `INTERRUPTED/UNKNOWN` event or resumes observation of a verified live child.

### 12.2 Timeout and cancellation

- Hard deadline, silence deadline, and caller cancellation are different causes.
- Timeout initiates graceful cancellation only if declared, then bounded tree termination.
- Child-tree cleanup is supervised and receipt-backed.
- Cleanup failure is attached to the primary outcome and never replaces or masks it.
- A lease is not released until the process tree is terminal or state is explicitly `IDENTITY_MISMATCH/UNKNOWN`.

### 12.3 Provider and observer failure

- Telemetry cannot change an already-completed peer result.
- Provider circuit breaking is scoped to the provider, profile, and account.
- Stale cached evidence remains tagged stale with its observation time.
- Provider unavailability is not zero usage, healthy, or unlimited quota.
- Provider processes have resource and deadline limits and cannot hold store transactions.

### 12.4 State and event publication

- State transition and outbox event are atomic.
- Consumers checkpoint event IDs and are idempotent.
- Projection failure leaves an outbox item for retry.
- `COMMITTED_ENFORCEMENT_PENDING` is distinct from `COMPLETED`, as in the §13.15 design.
- No history, handoff, or proposal side effect runs inside a consensus or request-state lock.

### 12.5 Configuration drift

- Runtime configuration resolves to an immutable revision.
- A dispatch freezes its relevant revision and executable/readiness binding.
- Session resume checks adapter, executable, and profile fingerprints.
- Policy changes before an effectful transition follow an explicit staleness rule: reauthorize, abort, or proceed only if the operation type permits.
- Declared configuration is not measured readiness.

## 13. Future TDD implementation order

This is sequencing only; Round 1 remains pre-TDD.

### Phase 0 — behavioral inventory and contract freeze

- Enumerate current commands, error codes, state files, adapter profiles, session scopes, and guard effects.
- Capture golden transcripts for pipe, PTY, session create/resume, timeout, broker vote merge, consensus close, health close/recover, and routing audit.
- Write decision records for storage scope and runtime process model.
- Specify protocol v1 commands, events, errors, and SQLite invariants.
- Build no package code until the externally observable compatibility set is agreed.

### Phase 1 — pure domain and store kernel

- TDD request/attempt, lease, session, health/admission, consensus, proposal, mutation, and optional budget reducers.
- TDD SQLite repositories, CAS, uniqueness, command idempotency, and atomic outbox.
- Inject clock, IDs, and process identity.
- Fault-inject every transaction boundary.
- Use no real peer adapter yet.

### Phase 2 — fake-peer vertical dispatch slice

- One fake pipe executable and one fake PTY executable.
- Invocation and artifact materialization, incremental events, deadlines, cancellation, process-birth identity, lease heartbeats, and crash recovery.
- TDD every transition around `DISPATCH_INTENT` and spawn.
- Establish the three-layer outcome model and completion-contract validators.
- Prove that no model call occurs under a store transaction.

### Phase 3 — adapter/provider conformance

- Publish the conformance kit.
- Migrate built-ins incrementally, not all at once: begin with the simplest pipe adapter, then the PTY/staging adapter, then the richest JSONL/session adapter.
- Require every adapter to pass create/resume, output-decoder, artifact, cancellation, error, and fingerprint fixtures.
- Test the valid `usage_provider=None` case.
- Run live probes as opt-in empirical tests, never routine CI blockers.
- Prohibit time-only session discovery from becoming a verified binding.

### Phase 4 — telemetry, health, and routing

- Implement dependency-declared collector fan-out and typed evidence first, including T87 differential tests.
- Implement availability/admission projections and probe-based recovery.
- Port routing as pure policy with golden decisions and full candidate evidence.
- Add provider-process isolation and failure-amplification tests before enabling live usage routing.

### Phase 5 — consensus, proposals, and governed mutations

- Port frozen electorate/rule, immutable votes, timeouts, arbiter separation, and atomic decision outbox.
- Implement finding fingerprints, trigger cursors, active-proposal uniqueness, and concurrent T89 tests.
- Implement `MutationRequest/Plan/Receipt`, broker-inbox import, effect worker, and saga reconciliation.
- Verify that projections cannot duplicate handoff or history entries.

### Phase 6 — versioned surfaces and strangler integration

- Expose the JSONL service and CLI through the same application service.
- Make the Python `Client` submit the same command envelopes.
- Run current `hub.py` in shadow or dual-read mode against `peerhub` decisions.
- Move one mechanism at a time behind the facade with rollback.
- Never maintain two write authorities for the same record.
- Retire old paths only after behavior, recovery, and evidence parity are demonstrated.

### Phase 7 — hardening and release

- Concurrent-client, crash-at-every-transition, malformed/truncated protocol, provider exhaustion, process-tree leak, and long-run proposal-dedup tests.
- Current and previous major protocol golden fixtures.
- Local-filesystem transaction and locking probes on supported platforms.
- Package release with normal PyPI metadata and console entry point.
- No vendor CLI bundling, self-updater, package-manager orchestration, or host-lifecycle framework.

## 14. Uncertainties and critique requests

These are open questions, not hidden assumptions.

1. **Service process model.** A resident local daemon simplifies multi-client streaming and recovery, while an explicit foreground/stdio service is operationally smaller. The domain/store design supports either, but v1 must pick one. I lean toward foreground `serve` first. I want `ag` to challenge whether that creates unacceptable client-lifecycle friction.
2. **State scope.** "One SQLite DB per `PeerHubHome`" needs a firm rule for per-user versus per-workspace ownership and how one service coordinates multiple workspaces. I lean toward one home DB with explicit workspace scope, but that increases blast radius. Separate workspace DBs reduce it but complicate machine-scope quota and coordinator state.
3. **SQLite on portable or unusual filesystems.** Local SQLite is the simplest real transactional authority; network or removable-filesystem locking behavior is not established here. This requires empirical support-matrix tests, not assurance from documentation.
4. **Task completion for free-form work.** T88 cannot be solved generally without a caller contract or vendor completion receipt. `DELIVERED_UNVERIFIED` is honest but may be ergonomically noisy. I want critique on which default UI wording preserves honesty without making ordinary asks look failed.
5. **Adapter session discovery.** Disabling automatic reuse when no strongly correlated session ID exists is safer, but may reduce functionality for Antigravity. A scoped, empirically verified vendor-state locator could be an optional capability; modification-time selection alone should not qualify.
6. **Provider isolation cost.** A worker process gives real timeout and resource containment but adds startup cost and complexity for cheap local collectors. The likely boundary is in-process only for deterministic, bounded local reads and a supervised process for vendor, network, or live RPC. That classification needs explicit conformance tests.
7. **Health thresholds.** The state model is firm; actual failure categories, thresholds, cooldowns, and profile/root propagation are policy and need characterization from current behavior. They should not be hardcoded in reducers.
8. **Consensus absent-voter policy.** I believe R:10 must retain the full electorate and escalate when unanimity cannot be established. If lower collaboration rates permit exclusions, the rule must state and freeze them. The live comment and implementation around `required_voters` deserve direct adversarial review.
9. **Public Python surface.** I propose a stable `Client` only, with command/event schemas as the canonical compatibility contract. If the new second consumer requires rich in-process extension, its concrete use cases should be known before more Python internals are stabilized.
10. **Third-party adapters.** Built-ins plus a published conformance kit are enough for the first slice. Python entry-point discovery, signature/integrity policy, and third-party capability bundles should wait for a real third-party adapter rather than be guessed now.
11. **Budget authority.** The blueprint includes machine-scope budget reservation, but `peerhub` may be deployed where no enforceable quota exists. I propose an optional authority with an explicit no-budget mode, never a fake provider. The exact v1 inclusion point needs product-scope confirmation.
12. **Protocol support window.** Current plus previous major is a reasonable starting contract from the blueprint, but release cadence and the new consumer's upgrade tolerance should determine the final support policy.

## 15. Round 1 decision summary

- Use one canonical application service and versioned command/event compatibility surface.
- Use pure domain reducers and one local transactional store with an atomic outbox.
- Keep adapters stateless and split optional usage, session, and readiness capabilities explicitly.
- Persist dispatch intent before spawn; identify processes by PID plus birth identity; never replay ambiguous dispatches.
- Model request, lease, session, consensus, and health/admission as separate state machines.
- Separate execution, peer-protocol, and task completion; exit 0 is not task success.
- Make telemetry a dependency-declared fan-out with typed partial evidence.
- Make proposal dedup and command/vote idempotency database-uniqueness invariants.
- Preserve brokered mutation through revisioned requests, receipts, and durable effect intents.
- Migrate through characterization and vertical slices, with one write authority at every stage.

---

## Round 2

### ag.deepthink critique of cx's draft (Round 2)

*(Originally written by ag to `round2-ag-critique-of-cx.md`; inlined here by cc per the charter's single-ledger rule.)*

**Verdict: CONVERGED** — ag accepts cx's Round 1 positions on all 4 divergence points (SQLite authoritative storage, pure-domain hexagonal layering, 3-layer task outcome model, foreground/stdio service model with no resident daemon), with one packaging refinement (co-locate `ports` protocols with their functional module instead of a separate top-level `ports/` tree, to avoid excess directory depth) and one added operational constraint (SQLite DB must be enforced on local filesystem, not SMB/NFS, for WAL lock safety). Full reasoning, evidence citations, and the consensus table are preserved in `round2-ag-critique-of-cx.md` in this same directory.

Consensus table ag produced:

| Area | Decision |
|---|---|
| Authoritative Store | Single local SQLite DB per `PeerHubHome` (local filesystem enforced, WAL mode, atomic outbox). |
| Architecture / Layering | Hexagonal with 100% pure domain state reducers + single `ApplicationService` entry point. |
| Outcome Model | 3-layer: execution outcome, peer-protocol outcome, task outcome (`DELIVERED_UNVERIFIED` default for prose). |
| Service Model | In-process `Client` + foreground `peerhub serve --stdio`. No background OS daemon. |
| Adapter Contract | `PeerAdapter` + optional `UsageProvider` split; adapters translate protocol, never orchestrate. |
| Defect Safeguards | Pipeline fan-out (T87); 3-layer outcome model (T88); DB unique index + SHA-256 fingerprinting (T89). |

---

### cx.deepthink critique of ag's draft (Round 2)

*(Written to the ledger by cc on cx's behalf — cx's sandbox again rejected a direct file write; returned as reply text, verbatim below.)*

# peerhub Round 2 — cx.deepthink critique of ag's draft

## Overall assessment

The drafts are closer than their directory trees suggest. We agree on the critical behavioral invariants: UUID-keyed leases, process-birth identity, dispatch intent before spawn, immutable votes, independent telemetry collectors, fingerprint-based proposal deduplication, adapter/provider separation, and incremental strangler migration.

The real disagreements are narrower:

- What persistence mechanism makes those invariants authoritative under concurrent CLI processes and crashes?
- What result model prevents T88 without pretending arbitrary task completeness is machine-decidable?

I am withdrawing two unnecessarily strong implications from my Round 1 draft:

- The logical dependency rules do not require five mirrored top-level layer trees.
- A resident daemon is not required for v1.

## 1. Authoritative storage

**Position now: SYNTHESIS, with SQLite as the recommended/default v1 backend.**

Ag's flatter package layout does not inherently require SQLite. Package layering and persistence technology are independent questions. A correct file-backed implementation is possible, but "per-record lock files plus atomic rename" is not sufficient by itself for all the invariants both drafts require.

### What a lighter file store can do correctly

The current implementation proves that a file-backed design can repair individual races:

- Session read-modify-write is now performed under one lock (`_sys/core/hub.py:4081-4096`).
- Leases are UUID-keyed and ownership-checked under a lease lock (`hub.py:10707-10773`).
- Consensus votes use a per-round lock, fresh reads, and immutable/idempotent merge semantics (`hub.py:7715-7757`, `7781-7830`).
- Broker mutations use expected-revision hashes, a shared resource lock, and atomic replacement (`hub.py:819-859`).

A deliberately small system could therefore store each aggregate in one JSON file and serialize its updates under one lock.

### Where per-record locks stop being enough

The target architecture also requires:

- A request transition and its client-visible event to commit together.
- One active proposal per normalized finding fingerprint under concurrent session-end processes.
- One immutable vote per voter/round.
- Command idempotency across separate CLI processes.
- One live session binding per scope.
- Lease state, request state, and recovery evidence that cannot contradict one another.
- Durable effect intents that survive a crash between authoritative commit and filesystem/process enforcement.
- Idempotent handoff/history projections rather than separately appended side effects.

Per-record lock files do not provide atomic state-plus-outbox publication when those are different files. There are only three ways around that:

1. Put the complete aggregate and its outbox in one increasingly large file under a global lock.
2. Design a write-ahead journal, transaction IDs, commit markers, recovery rules, uniqueness indexes, and compaction for the file store.
3. Use an existing local transactional engine.

Option 2 is effectively a bespoke database. Option 1 can be correct, but it serializes unrelated writes and makes append-only evidence, indexing, pruning, and concurrent streaming progressively awkward. SQLite already supplies local transactions, uniqueness constraints, revision checks, crash recovery, and indexing through the Python standard library. It is not a distributed-system dependency or external service.

T89 makes this particularly concrete. Ag proposes checking existing open proposals by fingerprint. Under two concurrent session-end processes, "check, then create a file" still races unless the fingerprint index and proposal creation share one lock/transaction. A SQLite partial unique index makes the acceptance criterion structural; a file implementation needs an equivalent global index and transaction protocol.

### Recommendation

Adopt ag's simpler module organization, but add an explicit authoritative-state boundary:

```text
peerhub/
  core/
    api.py
    context.py
  state/
    contract.py
    sqlite.py
    migrations/
  adapters/
  dispatch/
  routing/
  consensus/
  health/
  ipc/
  governance/
```

The domain should depend on a `StateStore`/`UnitOfWork` contract, not on `sqlite3`. SQLite should be the supported v1 backend. JSON and Markdown remain suitable for:

- User-owned configuration.
- Imported legacy state.
- Human-readable exports and projections.
- Create-only broker inbox requests from restricted clients.
- Transcripts and large artifacts referenced by digest.

A future file backend is acceptable only if it passes the same multi-process, crash-boundary, uniqueness, and state-plus-outbox tests. I would not make two persistence implementations part of v1.

### What remains unresolved

Ag did not specify an authoritative store or atomic publication model. This point remains open until one of these is accepted:

- SQLite as the v1 authority behind a store interface; or
- A concrete lighter transaction design that covers atomic state plus outbox, concurrent deduplication, command idempotency, and recovery — not only per-record locked replacement.

## 2. Layering depth

**Position now: SYNTHESIS, physically closer to ag's layout.**

My Round 1 tree overexpressed logical boundaries as physical package depth. A mirrored set of `domain/dispatch.py`, `application/dispatch.py`, `ports/process.py`, and `infrastructure/process/pipe.py` is defensible, but it adds navigation cost and duplicate naming for a local coordinator with a small number of bounded mechanisms.

Ag's feature-first packages already identify the correct cohesive areas: `dispatch`, `routing`, `consensus`, `health`, `ipc`, `governance`, `adapters`, `core`.

The important requirement is dependency direction, not the number of directory levels.

### Recommended reconciliation

Use a feature-first modular monolith, with purity enforced inside each feature:

```text
peerhub/
  core/
    api.py
    context.py
    protocol.py
    errors.py

  state/
    contract.py
    sqlite.py

  adapters/
    contract.py
    registry.py
    builtins/

  dispatch/
    model.py          # pure request/session/lease transitions
    service.py         # orchestration
    process.py          # process-supervisor port/types
    pipe.py               # concrete runner
    pty.py                # concrete runner
    artifacts.py

  routing/
    model.py          # pure RouteDecision
    service.py

  consensus/
    model.py          # pure round/vote reducer
    service.py

  health/
    model.py          # pure availability/admission reducers
    collectors.py
    service.py

  ipc/
    commands.py
    events.py
    jsonl.py
    cli.py

  governance/
    mutations.py
    broker.py
    proposals.py
```

Rules still matter:

- `model.py` modules are pure and cannot read files, environment, clocks, or vendor state.
- `service.py` modules coordinate effects through typed boundaries.
- Only the composition root selects concrete storage, runners, clocks, and adapters.
- Adapters cannot persist sessions, route, update health, acquire leases, or decide task completion.
- CLI and JSONL paths invoke the same canonical API.
- Import-boundary tests enforce these rules.

This keeps the useful part of my design — pure transition reducers and explicit effect boundaries — without requiring a five-layer directory hierarchy.

One specific adjustment is needed to ag's tree: `dispatch/engine.py` should not combine policy/orchestration, raw process effects, stream parsing, and state transition rules in one module. It can remain under the flat `dispatch` package, but those responsibilities should be separated internally.

### Resolution

I no longer consider physical layering a contested architecture point. Ag's flatter feature layout is preferable if the dependency and ownership rules above are made normative.

## 3. Task-completion outcome model

**Position now: MY SEMANTIC SPLIT, with a simpler combined result type.**

Ag's `OutputValidationResult(is_valid, is_truncated, failure_reason)` leaves a real T88 gap. It is not merely a less ceremonial spelling of the three-layer model.

### Concrete gaps

**A. `is_valid` conflates different questions** — a result may be a valid process execution, validly parsed vendor output, a valid assistant message, or incomplete relative to the user's requested deliverable. Those are different facts. One `is_valid` Boolean cannot preserve which layer failed.

**B. `is_truncated` describes only one symptom** — the observed T88 response was not necessarily transport-truncated. It was a complete-looking sentence announcing intended delegation while failing to perform the work. Other under-delivery shapes: a long answer omitting a required artifact; a report file never created; a structured answer missing required fields; an assistant refusal wrapped in valid vendor JSON; a response with progress but no terminal result; a correct short answer to a genuinely short task. Text length and delegation markers are useful signals, not a completeness proof.

**C. Ag's method lacks the evidence needed to verify the actual T88 request** — its signature is `validate_output(query, raw_output, exit_code)`. The real heavy test required a report file that was never created (`_sys/ai/backlog.json:2300-2312`). The proposed method receives no artifact manifest, workspace effect evidence, structured completion contract, or session/process evidence. It cannot reliably detect that failure.

**D. Task semantics do not belong to the peer adapter** — Claude, Codex, and Antigravity differ in invocation grammar, terminal stream, vendor events, and session identity. Whether "create report X" was fulfilled is peer-independent. Putting this decision in each adapter guarantees three divergent definitions of completion.

**E. Targeted retry may be unsafe** — ag's T88 section says validation failure can trigger targeted retry or fallback. Once the first peer may have executed tools or mutations, replay can duplicate side effects. The current hub already suppresses automatic retry after possible dispatch for this reason (`hub.py:1951-1978`). Output suspicion cannot override replay safety.

### Recommended synthesis

Keep one public `AskResult`, but make its internal evidence explicit:

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
    execution_certainty: ExecutionCertainty

class ProtocolAssessment:
    parsed: bool
    response_present: bool
    vendor_completion_marker: bool | None
    suspected_truncation: bool
    protocol_failure: ProtocolFailure | None

class CompletionAssessment:
    state: VERIFIED | INCOMPLETE | UNVERIFIED | NOT_APPLICABLE
    failed_requirements: tuple[RequirementFailure, ...]
    evidence_refs: tuple[EvidenceRef, ...]
```

Ag's method can survive after being narrowed and renamed: `PeerAdapter.interpret_output(...) -> ProtocolAssessment`. It may report malformed/truncated vendor framing, empty response, vendor error, progress-without-terminal marker, or a suspicious delegation marker. **It must not decide task fulfillment.**

A central `CompletionAssessor` evaluates a caller-supplied `CompletionContract` against the parsed peer result, required output fields/schema, required artifact/effect receipts, expected files under an authorized scope, and an optional caller verifier. If no verifiable completion criterion exists, exit 0 plus a valid response becomes `DELIVERED_UNVERIFIED`, not `SUCCEEDED_VERIFIED` — an honest delivery state, not a failure. A UI can still show a compact status (`verified`/`delivered`/`incomplete`/`failed`/`interrupted`); the internal evidence stays separate so health, retries, billing, and callers don't infer the wrong thing.

### Resolution

This remains a substantive open point. Ag's current three-field `OutputValidationResult` does not structurally prevent T88. It becomes sufficient only if it is re-scoped to peer-protocol validation and paired with a separate task-level completion assessment.

## 4. Service/process model

**Position now: SYNTHESIS, leaning toward ag's embedded/CLI-first implementation. A resident daemon is not required for v1.**

My Round 1 draft used "application service" and "local service" too closely, making a logical enforcement boundary sound like an OS-resident daemon requirement. Those are different things.

**Mandatory: application service** — `PeerHub` needs one canonical application API in code so embedded callers, the CLI, JSONL transport, and a possible future MCP adapter all invoke the same admission, idempotency, lease, routing, consensus, and mutation rules. This is a logical application service or facade. It does not imply a background daemon.

**Not mandatory: resident daemon** — ag's strangler/delegation-facade model can satisfy v1 if each CLI call hosts a `PeerHubRuntime` for the lifetime of the operation. A `peerhub serve --stdio` process can host the same runtime for a longer JSONL session; it is a service for the connection's lifetime but need not be installed or managed as a resident OS service.

**Multi-client behavior without a daemon** — multiple independent CLI/stdio host processes can coordinate through the transactional store: commands use unique idempotency keys, leases identify their owner runtime and process birth identity, outbox events are durable, a client can stream its own request's live events and later query persisted events, and recovery sweeps run at startup and before affected operations. This is sufficient for concurrent dispatches; it does not provide instantaneous push of unrelated events from one client's operation to every other client. That stronger cross-client subscription model would justify a singleton local broker/daemon, but the charter establishes multiple AI peers, not yet multiple external clients requiring continuous cross-client subscriptions. Under the debate's symmetric-deferral rule, the resident daemon should remain out until a real consumer requires one.

### Resolution

The reconciled v1 position: embedded client and CLI first; canonical in-process application service mandatory; optional `serve --stdio` JSONL host over the same API; no resident daemon or OS service in v1; durable store and owner-aware leases make concurrent host processes safe; add a singleton daemon only when a measured external-client requirement justifies it. I no longer consider this point contested.

## Resulting synthesis

The smallest surviving architecture is therefore: ag's feature-first module organization; pure transition models inside feature packages; one canonical application API; an explicit authoritative-state module, with SQLite as the proposed v1 backend behind a `StateStore` interface (not a hard `sqlite3` dependency in domain code); stateless peer adapters plus optional usage/session/readiness capabilities; embedded/CLI-first hosting with optional foreground JSONL stdio; three distinct result-evidence layers inside one compact `AskResult`; no resident daemon; no bespoke packaging or vendor-CLI lifecycle system.

## Verdict: OPEN

Two points remain contested:

1. **Authoritative persistence.** Ag must either accept a transactional state boundary with SQLite as the v1 default (behind a `StateStore` interface) or provide a concrete lighter design that guarantees atomic state-plus-outbox publication, multi-process idempotency, proposal uniqueness, vote uniqueness, session-binding uniqueness, and crash recovery. "CAS file updates" or per-record lock files alone do not cover that complete set.
2. **T88 completion semantics.** Ag must either accept the execution/protocol/completion distinction — packaged in one `AskResult` — or demonstrate how its current `validate_output(query, raw_output, exit_code)` can detect missing required artifacts, distinguish valid vendor framing from task fulfillment, preserve honest uncertainty, and prevent unsafe retries.

The layering and process-model differences are reconciled from my side. If ag accepts the two corrections above, the Round 1 drafts are structurally compatible and the next round can focus on narrower contract details rather than competing architectures.

---

## Round 3

### ag.deepthink final reconciliation (Round 3)

*(Originally written by ag to `round3-ag-final.md`; inlined here by cc.)*

**Verdict: CONVERGED.** ag accepts both of cx's Round 2 refined positions in full: the `StateStore`/`UnitOfWork` interface framing (SQLite as the default v1 backend, not a hardcoded domain dependency), and the `AskResult` (`ExecutionOutcome`/`ProtocolAssessment`/`CompletionAssessment`) completion model with `PeerAdapter.interpret_output(...) -> ProtocolAssessment` narrowed to protocol-only evidence. Full reasoning preserved in `round3-ag-final.md`. ag explicitly adopts cx's Round 2 "Resulting synthesis" paragraph as the authoritative Round 1-3 target architecture.

### cx.deepthink closing confirmation (Round 3)

Asked to confirm whether ag's acceptance (above) satisfies the two conditions cx's Round 2 verdict was contingent on. cx's full reply:

> I'll verify the short acceptance document against the two conditions from Round 2, then return only the requested verdict.
>
> **CONVERGED**

---

## Outcome

**Full convergence reached after 3 rounds** (2 independent Round 1 drafts, 1 cross-critique round resolving 4 major divergences with 2 conceded immediately by ag and 2 refined-then-accepted after ag's evidence-based pushback, 1 closing confirmation). Genuine adversarial process, not rubber-stamping — both peers moved off their Round 1 positions where the other's evidence was stronger (ag conceded on storage/layering/outcome-model/service-model in Round 2; cx conceded on layering and service-model in its own Round 2, then held and sharpened its storage and outcome-model positions with concrete evidence until ag explicitly accepted the refined versions in Round 3).

`cc` will now consolidate this ledger into a canonical `ARCHITECTURE.md` design document. This ledger remains the process record; `ARCHITECTURE.md` is the clean deliverable.

---

# Meta-Review (Round 4+) — user-requested, 2026-07-27

The Round 1-3 debate asked "is each mechanism correct." This pass asks a
different question, mirroring the 2026-07-20 blueprint's own successful
meta-review pass (5-Whys + MECE + feedback-loop, 3 further rounds after its
main debate): **is `ARCHITECTURE.md` organized at the right level, and is
it actually the smallest design that fully serves the stated purpose?**
Still pre-TDD — this pass details/hardens the design, it does not start
implementation. Same process rules as Round 1-3 (unlimited rounds, no
artificial cap, alternating draft/critique, evidence over preference,
symmetric deferral, stop only on genuine 2-consecutive-round convergence).

**Five lenses, applied to `docs/design/ARCHITECTURE.md`:**

1. **5-Whys.** For each major decision in ARCHITECTURE.md (SQLite-behind-
   `StateStore`, the 3-layer `AskResult`, no resident daemon, feature-first
   modules, the specific §16 open-questions list) — trace it back one more
   "why" than the debate already did. Does the root justification still
   hold, or does some decision rest on an assumption that was never
   actually re-examined after Round 1's initial framing?
2. **MECE.** Are the module/layer boundaries (§2, §2.1) actually mutually
   exclusive and collectively exhaustive? Look specifically for: any
   responsibility that could plausibly live in two different modules as
   written; any real `hub.py` mechanism that doesn't map cleanly to
   anything in the new structure (a gap); any two sections of
   ARCHITECTURE.md that assert overlapping or mutually inconsistent rules.
3. **Purpose-fit generalization.** The charter's mission is a multi-peer
   AI CLI collaboration engine — not limited to exactly `cc`/`ag`/`cx`, not
   Windows-only. Find spots where the current design is more specific than
   the stated purpose actually requires (hardcoded assumptions that would
   break for a 4th peer, a non-Windows host, or a peer with a materially
   different invocation model) — but do NOT reintroduce the
   over-generalization mistake the 2026-07-20 debate's own red-team pass
   corrected (speculative flexibility for consumers/scenarios that don't
   exist yet). Every generalization proposed here must cite what it's
   generalizing FOR (a real, named gap), not "future-proofing" in the
   abstract.
4. **Token/feature efficiency.** Is there a mechanism in ARCHITECTURE.md
   that costs more implementation/runtime complexity than the value it
   returns for THIS purpose? Where could a simpler mechanism deliver the
   same safety property (e.g., does every one of the 3 `AskResult` layers
   need to be a full typed sub-object, or would a flatter shape lose
   nothing real)? Flag over-engineering as concretely as the earlier
   rounds flagged under-engineering (T87/T88/T89) — same evidentiary bar.
5. **Virtuous-cycle feedback loop.** The shelved blueprint's own phase2-arch
   document (§13.16) found a real gap in its OWN design: accumulated
   Evidence was write-only, never improving future decisions. Does
   ARCHITECTURE.md have the same gap? Concretely: do routing decisions
   (§11), health thresholds (§10), or consensus/arbiter behavior (§8) ever
   get informed by the outcomes recorded in the outbox/evidence store
   (§4), or does evidence just accumulate without feeding back into future
   policy? If it's a real gap, propose the smallest addition that closes
   it — not a full recommendation-engine rebuild.

**Deliverable:** a findings list per lens (each finding: cite the exact
ARCHITECTURE.md section, state the concrete problem, propose a concrete
fix or explicitly say "no fix warranted, documented as accepted"), leading
to either direct edits to ARCHITECTURE.md (small, surgical) or a clearly
justified "no change" verdict per section. `cc` conserves tokens this pass
— `ag`/`cx` do the lens analysis; `cc` relays/synthesizes/applies edits.

## Round 4

### ag.deepthink meta-review (Round 4)

*(Originally written by ag to `round4-ag-metareview.md`; inlined here by cc.)*

8 findings, all LOW severity, 0 HIGH. Summary: SQLite WAL startup-probe mechanism needed explicit definition (§4); `CompletionAssessor` should only run on terminal exit-0 (§9); "action proposals" vs "consensus rounds" need disambiguation since both used the word "proposal" (§2/§8); lease-sweep ownership needed an explicit home in `dispatch/service.py` (§7.2); `dispatch/pty.py` needs explicit cross-platform framing (POSIX pty vs Windows ConPTY) (§6.2); `AskResult`'s 3 sub-objects are worth keeping as-is (no change); `BudgetReservation` should default to `DISABLED`/no-op (§11); and a feedback-loop clause should connect terminal outcomes → health availability → admission → routing (§10/§11). Full findings in `round4-ag-metareview.md`.

### cx.deepthink meta-review (Round 4)

*(Written to the ledger by cc on cx's behalf — cx's sandbox again rejected a direct file write; returned as reply text, verbatim below.)*

# peerhub Round 4 — cx.deepthink meta-review

The design is substantively sound, but this pass found several real organization/minimality gaps. Surgical edits are warranted; no architectural reset is needed.

## 1. 5-Whys

### SQLite behind `StateStore` — no fix warranted

**Sections:** §§3-4. The root justification still holds: independent processes require atomic ownership, uniqueness, CAS, and state-plus-outbox commits; T83/T89 demonstrate these are concrete correctness requirements; `StateStore` isolates domain logic for deterministic testing, not to speculate about multiple production backends; SQLite is explicitly limited to local filesystems and fails closed when its transaction probe fails. This remains the smallest credible authoritative store for the stated concurrency model.

### Three-layer `AskResult` — no structural simplification warranted

**Sections:** §§6.2, 7.1, 9, 13.2. The layers describe facts from different authorities (process supervisor: execution; adapter: vendor protocol; central assessor: task completion). Flattening them would not reduce the number of facts or producers; it would weaken their ownership boundary and make another T88-style conflation easier. One small correction under Lens 4: `effective_status` should be derived, not independently stored.

### No resident daemon — no fix warranted

**Section:** §3. The root reason remains absence of a measured continuous-subscription requirement. Transactional coordination already covers concurrent commands. The separate long-lived `serve --stdio` surface does not have equally strong justification — addressed under Lens 4.

### Feature-first modules — root holds, boundary cleanup needed

**Sections:** §§2-2.1. Feature-local models and services fit independently evolving state machines better than a generic five-layer directory tree. The remaining problems are duplicate or missing owners inside that structure, not a reason to abandon it.

### The §16 "open questions" bucket is incorrectly organized

**Sections:** §§4, 15-16. It mixes four different categories: actual Phase 0 decisions, implementation spikes, empirical validation, and trigger-gated future work that must not be decided yet. Inconsistencies: §16.5 calls unusual/network-filesystem support open, while §4 already resolves v1 as local-filesystem-only with fail-closed probing; §16.10/11/13 require future consumers/releases so Phase 0 cannot legitimately resolve them; Phase 7 promises current-plus-previous-major fixtures while §16.13 says that policy is undecided, but v1 has no previous major.

**Fix:** Replace §16 with three lists — (1) Phase 0 decisions: state scope, provider granularity, health policy data, budget inclusion, default outcome wording; (2) Implementation/empirical spikes: PTY implementation, filesystem probe matrix, vendor session locator, provider-isolation boundary; (3) Explicitly deferred until trigger: broader public Python API, third-party adapter discovery, breaking-release support window. For v1, test protocol v1 only; decide previous-major support when the first breaking major is proposed.

## 2. MECE

### Duplicate `RuntimeContext` ownership

**Section:** §2. Both `runtime.py` and `core/context.py` claim `RuntimeContext`. **Fix:** `core/context.py` owns the immutable `RuntimeContext`/`PathLayout` value types; `runtime.py` owns only configuration resolution and composition-root construction.

### Duplicate protocol/error ownership

**Sections:** §§2, 2.1, 5. `core.protocol` owns command/event schemas, negotiation, and stable error codes, while `ipc.commands`, `ipc.events`, and `core.errors` claim overlapping pieces. **Fix:** `core.protocol` = canonical transport-neutral command/event/envelope/version/`ErrorCode` types; `core.errors` = internal exception types + their mapping to protocol errors; `ipc.jsonl` = framing/serialization only; remove or rename `ipc.commands.py`/`ipc.events.py` to codecs if transport-specific encoding genuinely remains.

### Current collaboration mechanisms have no feature owner

**Sections:** §§1-2, 4. The architecture intends to replace `hub.py`'s communication and coordination, but its module map lacks ownership for several real mechanisms: sessions/room lifecycle (`hub.py:1363,1388,3532,3568`), durable send/broadcast/read state (`hub.py:1460,1531,1573`), node registration/presence (`hub.py:1658`), terminal duty and handoff (`hub.py:8690,8752,8793`), checkpoint/context/handoff state (`hub.py:8844,8929,11135`). `ipc/` is defined as command/event transport only, so it is not a valid owner for the mailbox/room domain.

**Fix:** Add a feature package:

```text
coordination/
  model.py       # Room, membership/presence, message, handoff/checkpoint,
                 # terminal-assignment transitions
  service.py
```

Add the corresponding authoritative records to §4. If any mechanism is intentionally excluded, §1 must say so explicitly instead of claiming full communication/coordination replacement.

### Several important abstractions are described but have no module owner

**Sections:** §§2, 6.4-6.5, 9, 10, 13.1. Missing/ambiguous owners: `CompletionAssessor`; shared `EvidenceValue`/`EvidenceRef` types; readiness-probe contract and receipts; cross-feature telemetry fan-out (currently under `health.collectors` even though usage evidence also serves routing). **Fix:** add explicit homes — `core/evidence.py`, `dispatch/completion.py`, `adapters/readiness.py`, `telemetry/collectors.py`, `telemetry/projections.py`. `health` should own health policy/transitions, not all telemetry collection.

### "One mutating entrance" conflicts with internal workers

**Sections:** §2.1 rule 3, §§3, 12, 14. Recovery sweeps, lease heartbeats, outbox/effect workers, and broker reconciliation must mutate state, yet the document literally says `core.api` is the only mutating entrance. **Fix:** clarify `core.api` is the sole **external** facade; every mutation (external or system-initiated) passes through the same application command handlers and `UnitOfWork`; internal workers submit typed system commands rather than invoking the public client facade or repositories directly.

### Completion-contract optionality is inconsistent

**Sections:** §§6.2, 7.1, 13.2. `AdapterRequest` permits an optional caller contract, but §13.2 says every dispatch freezes one. **Fix:** admission always freezes a contract — either caller-supplied or a canonical implicit contract whose only claim is delivery, producing `DELIVERED_UNVERIFIED` when valid protocol output exists.

## 3. Purpose-fit generalization

### Adapter type and configured peer instance are not clearly separated

**Sections:** §§2.1 rule 7, 6.1, 7.3, 11. `PeerAdapter` describes a vendor integration, while session/routing/health records refer to `peer_id` — the document never defines whether `peer_id` means adapter kind, configured executable, account, or active instance. Ambiguous for a fourth peer using an existing adapter, two accounts of one vendor, or two executables with different readiness bindings.

**Fix:** add a small configured-instance value:

```text
PeerInstanceConfig:
  instance_id
  adapter_id
  executable_reference
  enabled_profile_ids
  account_id?
  quota_pool_id?
```

Health, routing, leases, and sessions key on `instance_id`; adapter conformance keys on `adapter_id` + adapter version. This generalizes for a named real gap without inventing a plugin ecosystem.

### Commands incorrectly require a workspace scope universally

**Section:** §5. Coordinator operations (node listing, broker status, global health, config inspection) are not inherently workspace-scoped; requiring `workspace_scope` forces fake values. **Fix:** `scope: WorkspaceScope | GlobalScope`, each command schema declaring which scope kinds it accepts.

### No additional host or transport generalization is warranted

**Sections:** §§2.1, 3, 6.1-6.5, 16. No fix warranted — built-in `cc`/`cx`/`ag` modules don't hardcode identities into core policy; PIPE/PTY cover the three actual invocation models; process supervision is described through ports, not Windows APIs; the Windows PTY choice remains a localized implementation spike; HTTP peers/remote brokers/plugin signing/arbitrary transports lack a named current requirement.

## 4. Token/feature efficiency

### `effective_status` should be derived

**Section:** §9. `AskResult` contains 3 authoritative subobjects plus a fourth status summarizing them — if independently persisted/constructed, permits contradictory combinations. **Fix:** make `effective_status` a pure computed property/central reducer output; persist the three evidence layers + the policy revision used to derive status. The three subobjects stay — they're the T88 safety boundary.

### Budget reservation is premature v1 machinery

**Sections:** §§4, 11, 16.1, 16.12. The document defines a reservation state machine before deciding whether v1 includes budget authority, whether providers report request-level cost vs. account/pool headroom only, and what reservable unit would be authoritative — a state machine can't be correctly specified before those facts exist. **Fix:** keep usage evidence + no-budget routing in v1; move `BudgetReservation` to a conditional design note activated only if Phase 0 identifies a measured oversubscription problem + an authoritative reservable unit.

### Long-lived `serve --stdio` lacks a named v1 consumer

**Sections:** §§3, 5, 15 Phase 6. The design correctly rejects a daemon (continuous subscription unmeasured) but then includes a multi-command foreground service without citing a consumer needing connection reuse — today's `hub.py` command surface is one-shot. **Fix:** preserve the versioned JSONL envelope, but v1 surfaces = embedded API + one-shot CLI/JSONL only. Defer persistent `serve --stdio` until a real client needs multi-command connection state.

### Publishing a conformance kit is ahead of the third-party-adapter decision

**Sections:** §§2, 15 Phase 3, 16.11. Built-in adapters need shared conformance tests, but publishing `peerhub.testing` creates a supported public API + compatibility burden with no external adapter consumer yet (§16.11 already correctly defers discovery). **Fix:** keep conformance fixtures in repository tests for v1; publish a supported kit only when the first external adapter is accepted.

### SQLite abstraction and the outbox are not over-engineering

**Sections:** §§4, 12, 14. No fix warranted — the store interface enables pure tests and preserves ownership; the atomic outbox closes a real crash boundary between state and external effects. Replacing either with direct repository writes or best-effort logging would lose identified safety properties.

## 5. Virtuous-cycle feedback loop

### Evidence is not wholly write-only, but the outcome-to-policy path is underspecified

**Sections:** §§4, 7, 10-11, 14. Routing already consumes current usage/admission evidence, so this is not as empty as the shelved blueprint's loop. However, the architecture never explicitly maps completed attempts, probe results, provider failures, latency, or process-integrity failures into the immutable observations and mutable projections that later health/routing decisions consume — terminal outcomes can enter the outbox and remain audit-only as written.

**Smallest fix:** add one deterministic feedback paragraph and owner — `telemetry.projections` maps classified attempt/probe/provider outcomes into immutable observations and revisioned per-instance/profile projections; the terminal transition commits its normalized observation alongside request state and the outbox; projections track only operationally meaningful facts (infrastructure failure category, verified process integrity, latency, freshness, failure streak, measured quota state); health consumes these projections to derive admission; routing may consume a frozen, policy-declared reliability/latency projection with minimum-sample/freshness rules, recording supporting evidence references in its decision; every new decision emits evidence again, completing the loop; projections are rebuildable idempotently from immutable observations. **Important safety caveat: semantic `INCOMPLETE`/`UNVERIFIED` results must not automatically damage peer health unless a versioned policy explicitly classifies the failure as peer-caused — otherwise task difficulty would contaminate reliability.**

### Consensus and arbiter rules should not self-tune

**Sections:** §§8, 14. No automatic feedback fix warranted for electorate, unanimity, risk classification, or arbiter authority — those are governed policy, not optimization weights. Historical consensus evidence may support a human/R:10 policy revision, but must not silently rewrite constitutional behavior.

## Recommended surgical edit set

1. Clean up duplicate context/protocol/error ownership in §2.
2. Add explicit `coordination` and `telemetry` feature ownership.
3. Clarify internal mutation handling under §2.1.
4. Define configured peer instances and global/workspace command scopes.
5. Make the implicit completion contract and derived `effective_status` explicit.
6. Defer budget reservations, persistent stdio hosting, and a published conformance API until their named triggers exist.
7. Add the deterministic outcome-observation-projection loop for health and routing.
8. Reclassify §16 into Phase 0 decisions, empirical spikes, and trigger-gated deferrals.

Overall verdict: **targeted revision warranted**. The core architecture remains valid; the necessary changes primarily remove ambiguity, omit three premature v1 surfaces, restore missing coordination ownership, and close the evidence feedback loop.

---

## Round 5

### ag.deepthink reaction to cx's meta-review (Round 5)

*(Originally written by ag to `round5-ag-final.md`; inlined here by cc.)*

**Verdict: CONVERGED.** ag fully endorses all 8 items in cx's Round 4 surgical edit set, and independently verified the `coordination/` gap against real `hub.py` code (`action_init_session`, `action_end_session`, `action_send`, `action_broadcast`, `action_check`, `action_mark_read`, `action_update_status` — 400+ lines of active room/mailbox/presence/handoff logic with no owner in ARCHITECTURE.md §2, despite §1's claim of full communication/coordination replacement). Explicitly confirmed as a genuine Round 1-3 completeness miss, not cosmetic polish. Full reasoning, including agreement rationale for each of the 8 items, preserved in `round5-ag-final.md`.

---

## Outcome (Meta-Review)

**Full convergence reached after 2 rounds** (1 independent 5-lens pass by both ag and cx in parallel, 1 reaction round where ag verified and accepted cx's more extensive findings in full). cx's pass found substantially more (13 findings, including a real MECE completeness gap) than ag's (8 low-severity polish items) — ag did not simply defer to cx's greater volume; it independently re-verified the `hub.py` line citations before agreeing.

**Applied to `ARCHITECTURE.md`** (cc, mechanical synthesis, no further peer dispatch needed): duplicate `RuntimeContext`/protocol/error ownership resolved (§2, §2.1); new `coordination/` package added for room/mailbox/presence/handoff, closing the real completeness gap (§2, §4, new Phase 3.5); new `telemetry/` package split out of `health/` (§2); `core.api`'s "only mutating entrance" clarified as external-only, with internal workers using typed system commands (§2.1 rule 3); `PeerInstanceConfig` + `GlobalScope` added to separate adapter-kind from configured-instance and stop forcing fake workspace scopes (§5, new §6.1a); completion-contract optionality inconsistency fixed — always frozen, implicit-or-explicit (§6.2); `effective_status` made a derived property, not a persisted 4th field (§9); evidence feedback loop added as new §10.1 with an explicit safety caveat (incomplete/unverified results must not auto-damage health unless policy-classified as peer-caused); budget reservation, persistent `serve --stdio`, and the published conformance kit all deferred out of v1 with the shape preserved as a conditional note (§3, §11, §15 Phase 3); §16 reclassified from one flat list into Phase-0-decisions / implementation-spikes / trigger-gated-deferrals, fixing 2 live inconsistencies with already-resolved sections.

**Outcome: the design is now believed complete for its stated purpose** (multi-peer AI CLI coordination, replacing hub.py's dispatch/routing/consensus/health/coordination responsibilities in full — the one real scope gap this pass found is now closed) and is the smallest version of itself both peers could defend under 5 different adversarial lenses. Still pre-TDD; Phase 0 of §15 is the next authorized step, in a future round.

---

# Coupling / Anti-Spaghetti Cross-Check (Round 6+) — user-requested, 2026-07-27

User endorsed the direct-verification pattern (ag re-checking cx's citations
rather than deferring on volume) and asked for a **few more, bounded**
rounds — not unlimited this time — with one specific new lens: is
`ARCHITECTURE.md`'s feature-first module structure (§2, as revised in
Round 4-5) actually free of spaghetti coupling, or did adding `coordination/`
and `telemetry/` introduce hidden cross-feature entanglement? Still pre-TDD
— this checks the design document, not real code (none exists yet).

**The lens, precisely:**

1. **Dependency graph.** Draw the actual allowed dependency direction
   between every feature package (`core`, `state`, `adapters`, `dispatch`,
   `coordination`, `routing`, `consensus`, `health`, `telemetry`, `ipc`,
   `governance`). Is it acyclic? Any package that both depends on and is
   depended on by another (a cycle) is a spaghetti finding by definition.
2. **Interface minimality.** For each feature, what EXACTLY does another
   feature import from it? Is that surface small, explicit, and stable —
   or does anything reach past a `service.py` into another feature's
   `model.py` internals, private state, or a specific dataclass field
   that was never declared as part of a public contract?
3. **Shared-concern leakage.** Do `core/evidence.py`, `state/contract.py`,
   and `core/protocol.py` actually stay the single source for their
   concerns, or did Round 4-5's edits accidentally leave a duplicate or
   near-duplicate definition inside a feature package (the same mistake
   that caused the original `RuntimeContext`/protocol duplication cx
   found last round — check specifically whether that class of mistake
   recurred anywhere else)?
4. **New-module fit check.** `coordination/` and `telemetry/` are the two
   newest packages. Do they cleanly depend on `core`/`state`/`adapters`
   only, or do they reach sideways into `dispatch`/`health`/`routing`
   internals instead of going through those features' own `service.py`
   entrances? Concretely: how does `telemetry.projections` (§10.1) get
   the terminal `AskResult` it needs — through a clean event/hook
   `dispatch.service` exposes, or by reading `dispatch`'s internal state
   directly?
5. **Concrete refactor proposal, only if something real is found.** Any
   finding must come with either a specific interface fix (rename/move/
   narrow a specific import or field) or an explicit "checked, no
   spaghetti here" verdict — no restructuring for its own sake, no
   speculative interface layers for hypothetical future coupling that
   doesn't exist yet (same discipline as the Round 4-5 generalization
   lens).

**Process:** same as before (evidence over preference, `cc` conserves
tokens, `ag` writes to a file / `cx` returns text since its sandbox
still can't write) but **bounded** — target 2-3 rounds, not unlimited;
`cc` will check in with the user if it looks like more are needed.

## Round 6

### ag.deepthink coupling cross-check (Round 6)

*(Originally written by ag to `round6-ag-coupling.md`; inlined here by cc.)*

**Verdict: CONVERGED, 0 findings.** ag's dependency-graph analysis places `core.api` at a separate "Level 3" above the feature services (functionally agreeing it sits at the top of the stack), and reports 0 cycles, 0 shared-concern leakages, 0 sideways coupling from `coordination`/`telemetry`. Full analysis in `round6-ag-coupling.md`.

### cx.deepthink coupling cross-check (Round 6)

*(Written to the ledger by cc on cx's behalf — cx's sandbox again rejected a direct file write; returned as reply text, verbatim below.)*

# peerhub Round 6 — cx.deepthink coupling cross-check

## Overall verdict

The revised architecture is cohesive at the domain level, but its dependency graph is not yet demonstrably acyclic. One package cycle is definite, one boundary implies a second cycle, and three cross-feature paths remain underspecified enough that implementation could easily become spaghetti. The fixes are localized: move application orchestration out of `core`, separate the SQLite implementation from the state contract package, formalize cross-feature contracts, and use typed events/intents for telemetry and coordination integration.

## 1. Dependency graph

**Finding 1 — `core` has a definite package cycle** (§§2, 2.1, 5, 10.1). As specified: `core.api -> dispatch/coordination/routing/consensus/health/telemetry/governance`, and those features `-> core.protocol/core.evidence/core.context`. A canonical application facade cannot dispatch commands without depending on feature services; those services necessarily depend on core types. Therefore `core <-> feature packages` — a package cycle under the charter's definition. **Fix:** move `core/api.py` to a top-level `application/` package (`api.py`, `workflows.py`). `application` may depend on every feature service; `core` remains a leaf containing only shared values, protocol schemas, evidence algebra, execution primitives, narrow ports. `client.py` and `ipc.cli/jsonl` call `application.api`, not `core.api`.

**Finding 2 — `RuntimeContext` could recreate the same cycle** (§§2, 2.1 rule 6). If `RuntimeContext` contains feature services, repositories, runners, or adapters, `core.context` must import those packages, and features importing `core.context` create another cycle, turning the context into a service locator. **Fix:** `core.context` owns only low-level immutable values (`PathLayout`, command scope, policy revision, clock/ID ports, execution metadata); `runtime.py` owns the composed `Runtime` object containing feature-service instances; feature services receive narrow constructor dependencies, never the whole runtime container.

**Finding 3 — adapter/process boundary implies `adapters <-> dispatch`** (§§2, 6.1-6.2). `dispatch.process` is assigned process-supervisor port/types, while `PeerAdapter.interpret_output()` accepts `ProcessTerminalEvidence` and invocation planning uses `TransportLimits`; `dispatch.service` necessarily imports `PeerAdapter`. As written this implies `dispatch -> adapters.contract` AND `adapters.contract -> dispatch.process`. **Fix:** move neutral process-boundary values (`TransportKind`, `TransportLimits`, `ProcessTerminalEvidence`, `ExecutionCertainty`, `RetryDisposition`, `Deadline`) into `core/execution.py`. Both adapters and dispatch import these from `core.execution`. `ProtocolAssessment`/`InvocationPlan`/decoder contracts stay adapter-owned. Adapters must never import `dispatch`.

**Finding 4 — `state.contract` and `state.sqlite` in one package leave a latent feature cycle** (§§2, 4). Feature services import `state.contract`; a typed SQLite implementation must persist/hydrate feature records — if `state.sqlite` imports feature contracts/models, the package graph becomes `feature -> state.contract`, `state.sqlite -> feature`, and because both live in `state`, that's `state <-> feature`. Avoiding it only via untyped dict exchange would replace the cycle with persistence leakage. **Fix:** separate port from implementation — `state/contract.py` (generic `UnitOfWork`/transaction/outbox contracts only) vs. a new `persistence/sqlite.py` + `migrations/` (may depend on `state.contract` + public feature contracts; no feature imports `persistence`; `runtime.py` wires implementations to ports).

**Corrected acyclic direction:** `core` (nothing feature-specific) ← `state.contract`/adapter contracts/feature contracts+models ← feature services ← `application.api`/`workflows` ← `client`/`ipc`; separately `persistence.sqlite -> state.contract` + public feature contracts, `runtime -> application + persistence + concrete adapters/runners`. Within the feature-service level, sibling services should not call one another directly — cross-feature workflows belong in `application.workflows`; stable snapshots flow through public contracts.

## 2. Interface minimality

**Finding 5 — public feature DTOs currently live implicitly in `model.py`** (§§2, 7-12). `AskResult`, `RouteDecision`, `AdmissionSnapshot`, telemetry projections, room/conversation references, consensus decisions/invocation intents, mutation receipts all cross feature boundaries, but the tree provides only `model.py`/`service.py` for most features — consumers would need to import reducer internals, or services would expose undocumented internal dataclasses. **Fix:** add a narrow `contract.py` per feature that genuinely has cross-feature values (`dispatch/contract.py`, `coordination/contract.py`, `routing/contract.py`, `consensus/contract.py`, `health/contract.py`, `telemetry/contract.py`, `governance/contract.py`). Rules: another feature may import a sibling's `contract.py`, never its `model.py`; only `application` calls sibling `service.py` methods; `model.py` reducers/state stay feature-private; public contracts expose immutable values/IDs/revisions/evidence references, never repository objects or mutable projections.

**Finding 6 — consensus invocation ownership is ambiguous** (§§2, 8). `consensus.service` owns "arbiter invocation," but invocation is a dispatch concern — directly calling `dispatch.service` creates a sideways service dependency and makes consensus tests require process orchestration. **Fix:** consensus returns an immutable `PeerInvocationIntent`/`ArbiterInvocationIntent`; `application.workflows` executes it through dispatch and submits the resulting vote/opinion back to consensus. Consensus owns round policy/transitions; dispatch owns execution.

**Finding 7 — internal workers need a non-cyclic command submission port** (§2.1 rule 3, §§12, 14). Broker workers, recovery sweeps, and effect workers must submit typed system commands. Once `core.api` moves to `application`, those workers must NOT import `application.api` (application already imports their feature services — that would be circular). **Fix:** a narrow lower-level port, `core.ports.CommandSubmitter: submit(CommandEnvelope) -> CommandReceipt`; `application` implements it, workers receive it via constructor injection, knowing only the envelope/receipt, not the application facade.

## 3. Shared-concern leakage

**Finding 8 — `UsageEvidence` repeats the `EvidenceValue` state algebra** (§§2, 6.4, 13.1). `core.evidence.EvidenceValue` already owns `MEASURED|ABSENT|UNAVAILABLE|ERROR|STALE`; `UsageEvidence` independently carries the identical state set plus the same freshness/source/error concepts — near-duplicate ownership. **Fix:** `UsageEvidence = EvidenceValue[UsageMeasurement]`, where `UsageMeasurement` owns only usage-specific numeric fields + quota scope; source/freshness/error-state/timestamps/raw-evidence-reference stay exclusively in `core.evidence`. Readiness/collector evidence should reuse the same algebra rather than re-inventing state enums.

**Finding 9 — execution certainty needs one owner** (§§5, 7, 9, 14). `execution_certainty` appears in both the public error envelope and `ExecutionOutcome` with no explicit shared owner — protocol and dispatch could define incompatible enums. **Fix:** `core.execution.ExecutionCertainty` is canonical; `core.protocol` references it in errors, dispatch references it in `ExecutionOutcome`. Same for `RetryDisposition`.

**Finding 10 — outbox events must use the protocol event envelope directly** (§§4-5, 10.1, 14). The document names both an event outbox and canonical protocol events but never explicitly prohibits `state.contract` from defining a separate `OutboxEvent`. **Fix:** state explicitly that the outbox stores `core.protocol.EventEnvelope` plus delivery/checkpoint metadata; `state.contract` must not define another event payload schema.

**Finding 11 — legacy peer/scope identity remains in the dispatch model** (§§5, 6.1a, 7.3). §6.1a says sessions key on `instance_id`, but §7.3 still specifies `(workspace_scope, peer_id, profile_id, conversation_scope)` — this leaks the superseded identity model into a feature boundary. **[cc independently verified this against the live file — confirmed real, not a stale reading: ARCHITECTURE.md line 338 still reads `peer_id` verbatim.]** **Fix:** `(WorkspaceScopeId, instance_id, profile_id, conversation_scope)`. Global-scope commands cannot create/resume sessions. `instance_id` is always the configured peer instance; `adapter_id` is never used as runtime identity.

**Checked, no finding:** protocol/error duplication from Round 4 is confirmed resolved — `core.protocol` owns wire schemas/error codes, `ipc` is limited to framing/serialization, `core.errors` maps internal failures into canonical codes without a second wire envelope.

## 4. New-module fit

**Finding 12 — telemetry should not receive the full `AskResult`** (§§9, 10.1). `telemetry.projections` needs operational facts, not dispatch's complete internal result — importing `AskResult` would couple telemetry to execution field layout, adapter protocol assessment, and completion semantics, AND risks telemetry using semantic completion evidence despite §10.1's own safety prohibition (the safety caveat becomes much easier to accidentally violate if the forbidden field is sitting right there in the object telemetry already holds). **Fix:** dispatch commits and emits a narrow canonical event instead (`AttemptTerminalObserved`: `instance_id`, `profile_id`, `transport`, `operational_failure_category?`, `execution_certainty`, `process_integrity`, `started_at`, `terminal_at`, `latency`, `evidence_refs` — defined in `core.protocol`). `telemetry.projections` consumes it idempotently from the outbox; it never imports `dispatch`, reads request tables, or receives raw `CompletionAssessment`. Probe/provider paths similarly emit typed `ReadinessObserved`/`UsageObserved` events.

**Finding 13 — telemetry consumers need snapshots, not projection internals** (§§10-11). The text says `health.service`/`routing.service` consume "these projections" but names no public boundary — direct access to `telemetry.projections` fields or tables would couple policy code to aggregation internals. **Fix:** `telemetry.contract` exposes an immutable, revisioned reader: `TelemetryProjectionReader.get(instance_id, profile_id) -> OperationalProjectionSnapshot`, containing only measurements/freshness/sample-count/evidence-references — no admission/routing policy decision.

**Finding 14 — telemetry and health ownership needs one explicit dividing rule** (§§4, 10-10.1). The semantic split is good but should be normative, not implied: telemetry owns measured observations/empirical aggregates; health owns policy classifications (`HEALTHY`/`COOLDOWN`/`QUARANTINED`/`OPEN`). **Fix:** add an ownership rule forbidding telemetry from emitting admission states and health from recomputing raw collector aggregates — `health.service` maps one frozen telemetry snapshot plus readiness/admin evidence into an `AdmissionSnapshot`.

**Finding 15 — coordination must use events for cross-feature effects** (§§2, 4, 7.3, 12-14). Several real coordination transitions imply other-feature effects: new topic/room closure should retire old dispatch session bindings; proposal lifecycle changes should update handoff/dashboard projections; terminal-duty selection may consider health eligibility; consensus/dispatch completion may append room history/checkpoints. If `coordination.service` directly imports dispatch/governance/health/consensus internals to do this, the new package becomes a central coupling hub. **Fix:** `RoomSuperseded`/`ConversationScopeRetired` event → an application handler issues a dispatch `RetireSessionScope` system command; `ProposalLifecycleChanged` event → coordination updates its handoff projection without importing governance models; terminal assignment receives a prepared `TerminalCandidate` list with evidence references, coordination doesn't query health/routing tables directly; request/consensus history updates consume canonical terminal/decision events. Coordination owns its own state transitions; `application` owns cross-feature sagas.

**Finding 16 — presence and health must not duplicate one another** (§§2, 4, 10). `coordination` owns presence while `health` owns availability/admission — without a boundary, both could accumulate `online`/`last_seen`/`available`/gate fields independently. **Fix:** coordination presence = registered membership, heartbeat/contact timestamp, terminal-duty ownership; health = executable readiness, provider state, operational failures, cooldown/quarantine, admission. Presence may be evidence supplied TO health policy; it never directly equals `HEALTHY`/`OPEN`.

## 5. Concrete refactor set

1. Move `core.api` to `application.api`/`workflows`.
2. Keep `core.context` free of feature-service references; compose services in `runtime.py`.
3. Move shared execution-boundary types into `core.execution`.
4. Split `state.contract` from `persistence.sqlite`.
5. Add narrow public `contract.py` surfaces for cross-feature values; forbid sibling `model.py` imports.
6. Have application workflows, not feature services, coordinate sibling service calls.
7. Feed telemetry through canonical terminal/probe/usage events — not `AskResult` or dispatch tables.
8. Integrate coordination through events and typed effect intents.
9. Reuse the core evidence/execution/event algebras instead of redefining usage states, execution certainty, or outbox envelopes.
10. Replace the remaining session `peer_id` key with `instance_id`.

**Current verdict: real coupling findings exist; targeted refactor required.** The domain decomposition itself is good — especially the new coordination/telemetry split — but the application facade, persistence backend, process boundary, and cross-feature event paths must be repositioned before implementation to prevent those good feature boundaries from collapsing into cyclic imports.

---

## Round 7

*(pending — reconciling ag's 0-finding verdict against cx's 16 findings; cc independently verified Finding 11 as real against the live file before dispatching this round)*
