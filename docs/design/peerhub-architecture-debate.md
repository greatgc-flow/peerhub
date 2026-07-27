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

*(pending)*
