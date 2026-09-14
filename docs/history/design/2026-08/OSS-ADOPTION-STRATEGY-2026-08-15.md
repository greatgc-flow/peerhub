# peerhub Open-Source Adoption Strategy — Ratified Direction

Status: **RATIFIED (3-round dialectical process; converged, no unresolved
disagreement on the central question).**

Date: 2026-08-15

Origin: user request to review whether peerhub's architecture should adopt
open-source components more aggressively (including local LLMs), with the
explicit long-term ambition that peerhub become **"AI 간 협업을 위한 핵심
패키지"** — a core/foundational package for AI-to-AI collaboration, not
merely P:'s internal hub.py replacement. The user explicitly authorized
"keep all possibilities open" and unlimited-round dialectical research
among independent peers before converging on a direction.

---

## 1. Process

Three independent rounds, each with real web research and source citations
(not reasoning from training-data recall):

- **Round 1 (parallel, independent angles):**
  - `cx.deepthink` — local/self-hosted LLM runtime landscape (Ollama,
    llama.cpp, vLLM, SGLang, LocalAI, LM Studio, TextGen, MLX-LM, HF TGI)
    and technical fit against peerhub's actual `PeerAdapter`/
    `InvocationPlan`/`TransportKind` source.
  - `ag.deepthink` — multi-agent orchestration framework landscape
    (LangGraph, CrewAI, AutoGen, OpenHands, SWE-agent, MetaGPT) and
    vendor ToS restrictions on third-party harness access (Anthropic,
    OpenAI, Google).
- **Round 2 (independent third-voice verification):** `cc.deepthink`
  independently re-verified the specific disputed factual claims from
  both Round 1 positions against primary vendor documentation and direct
  reads of peerhub's own source, rather than adjudicating rhetorically.

Round 1 produced a genuine, substantive disagreement on the single most
consequential question — summarized as Position A and Position B below —
which Round 2 resolved with primary-source evidence.

---

## 2. The central disagreement (Round 1)

**Position A** (ag.deepthink): peerhub should stop building its own
dispatch/state-machine/retry/checkpointing logic and become "a thin,
highly-opinionated control plane built on top of LangGraph," which was
credited with "built-in conditional fallbacks (retries/failovers), and
health monitoring via checkpoints" and described as "the 2026 industry
standard for mission-critical orchestration." Also cited OpenHands as
having a dedicated "Agent Control Plane... directly mirroring peerhub's
ambitions," and recommended MCP/OpenAPI for peer communication and
LiteLLM for quota/routing.

**Position B** (cx.deepthink): "None should own peerhub's capability
leases or durable command authority." peerhub's existing dispatch
authority, attempt lifecycle, fencing, routing, and consensus kernel
should be preserved untouched. The only structural change needed is a
transport-neutral invocation abstraction
(`ProcessInvocation | HttpInvocation`, replacing today's process-only
`InvocationPlan`/`TransportKind{PIPE,PTY}`) so local/self-hosted HTTP
model endpoints can plug in as a new execution class alongside the
existing CLI adapters. Surveyed LiteLLM, Microsoft Agent Framework
(AutoGen's maintenance-only successor), LangGraph, CrewAI, Google ADK,
smolagents, OpenHands SDK, and mini-SWE-agent, and found no single
existing package combining peerhub's full authority/fencing/consensus
model — proposing each as an optional adjacent component, not a
replacement.

---

## 3. Round 2 findings (primary-source verification)

| Disputed claim | Verdict |
|---|---|
| LangGraph provides "health monitoring" | **No — category error.** LangChain's own persistence docs scope checkpointers to thread-local graph-state snapshots ("short-term, thread-scoped memory"), with no mention of provider health/quota/rate-limits/capability leases/fencing. Provider health monitoring is LiteLLM's actual domain — Position A credited one capability to two different tools. |
| LangGraph provides retries/failovers | **Partly right, overstated.** Real per-node `RetryPolicy` exists, but it's in-process node retry with no fallback routing, dead-letter queue, or notification system when exhausted. "Failover routing" is not supported. |
| LangGraph is safe for concurrent resume of the same unit of work | **Undocumented → treated as absent (DIR-004).** LangChain's own fault-tolerance docs are silent on concurrent execution of the same thread / exactly-once semantics. A motivated competing-vendor source (Diagrid) claims no built-in coordination exists; cross-checked against primary docs, which are silent rather than contradicting it. |
| LangGraph has anything resembling capability leases / fencing / dispatch-authority gating | **Absent.** Its "auth" is LangGraph Platform request-level AuthN/AuthZ (API access control) — a different layer entirely from minting attempt-scoped authority under a frozen policy revision. |
| OpenHands has a dedicated "Agent Control Plane" | **True — Position A was factually correct, Position B was outdated.** Real product, announced 2026-05-06, with fleet management, scheduling, sandboxed execution, cost attribution. |
| OpenHands's Control Plane is adoptable by peerhub | **No.** It is a closed **OpenHands Enterprise** product (the docs explicitly distinguish it from the open-source "core framework"), scoped to OpenHands' own agents, not heterogeneous third-party CLI peers. Read correctly, this is evidence *for* peerhub's niche, not a reason to defer to it: the best-funded implementation of peerhub's exact ambition is proprietary and vendor-locked, which is exactly the gap an open, adapter-neutral authority kernel fills. |
| `CapabilityTier` is a reasoning-quality scale (Position A's implicit premise) | **Factually wrong.** Direct source read: `READ_ONLY(0) → WORKTREE_WRITE(1) → GIT_MUTATE(2) → REMOTE_MUTATE(3)`, an authority scale. Position B's correction was right. |
| Is "thin control plane on top of LangGraph" compatible with keeping peerhub's already-shipped authority kernel (`retry_authorization.py`: 14-point precondition checklist, UNIQUE-constraint concurrency fencing, real two-thread SQLite race tests, the 9-dimension security-authoritative fence check that caught a real bug this session)? | **No.** Only two ways to stack them: keep both (two divergent state machines with no reconciliation) or move authority into LangGraph checkpoints (lose CAS revision fences, the concurrency backstop, capability leases, fencing tokens, and the race guarantee). Position A's own words describe the second option and never acknowledged it as a cost. The trade is worse than merely expensive: it swaps a **measured** guarantee (real race tests) for a **documented-absent** one. |

---

## 4. Ratified verdict

**Preserve peerhub's authority kernel as the outer boundary. Invert
Position A's stacking: adopt its recommended components, but underneath
the authority boundary, not above it.**

Reasoning:
1. The kernel's guarantees (capability leases, fencing, atomic retry
   authorization, typed concurrency outcomes) are load-bearing and
   independently verified this session — LangGraph provides none of them,
   confirmed against primary docs, not reputation.
2. It is peerhub's actual differentiator. For the "core package for
   AI-to-AI collaboration" ambition, the scarce asset is durable,
   fenced, capability-gated dispatch authority across heterogeneous
   peers. Rebuilding on LangGraph makes peerhub one more graph-
   orchestration wrapper competing on ergonomics against the framework
   it depends on.
3. OpenHands proves the category is real and valuable (a well-funded
   competitor built almost exactly this) and simultaneously proves the
   open-source niche is unfilled (their implementation is proprietary).

Where Position A was right, redirected to the correct layer:

```text
peerhub authority kernel  <- owns: capability leases, fencing, atomic retry
                             authorization, durable command state, consensus
        |
        +-- transport:  ProcessInvocation | HttpInvocation
        +-- interop:    MCP surface | A2A surface (see Section 10 -- added 2026-08-26)
        +-- provider health/quota/routing input:  LiteLLM
        +-- inner execution engines (optional, per-attempt):
              CLI adapters | LangGraph graphs | OpenHands SDK | ...
```

- **LiteLLM**: adopt for provider health checks, circuit-breaker
  ejection, usage-based routing, rate-limit-header accounting — genuinely
  commodity, genuinely hard to hand-roll correctly. peerhub's
  routing/health layer should consume these signals, not reimplement
  them. Composed with the new `HttpInvocation` execution class (below),
  a single execution class parameterized by base URL gets peerhub local
  models, hosted models, and provider failover simultaneously — not
  stated by either Round 1 position.
- **MCP**: adopt as a protocol/transport surface. Near-zero architectural
  cost (it doesn't touch the authority boundary), real interop upside.
- **LangGraph / OpenHands SDK**: adopt as *optional inner execution
  engines*. Once peerhub's kernel has authorized an attempt (capability
  lease, fence, route binding), what runs inside that authorized attempt
  can be a LangGraph graph or an OpenHands-SDK-driven harness. Good inner
  execution engine, not a viable outer authority.

### 4.1 Unresolved risk flagged during Round 2 (must be answered before
implementation, not deferred)

`EnforcementLevel` (`ADVISORY/ENFORCED/CONFINED`) and
`require_enforcement_floor()` are grounded in *process* sandboxing of CLI
agents. A raw HTTP model endpoint has no process to confine, and cannot
mutate a worktree or git at all. Naively reusing today's CLI enforcement
model for an `HttpInvocation` peer will either over-restrict or —
worse — silently under-enforce. This is structurally the same class of
bug as the `owner_instance_id` regression this session already found and
fixed in the failover path: an identity/semantic field reused across a
new execution path whose meaning didn't actually carry over. Working
recommendation (not yet ratified as an implementation decision): an
`HttpInvocation` peer is a distinct capability class, likely capped at
`READ_ONLY` unless a separately ratified tool-execution sandbox exists.

### 4.2 Bus-factor risk (raised by Round 2, orthogonal to the adopt/don't
decision)

The authority kernel (~900 lines in `retry_authorization.py` alone, plus
hand-built SQLite persistence/migrations) is exactly the code an outside
OSS contributor cannot safely touch without the verification discipline
this session has used — and this session's own real security-bug find in
that exact code cuts both ways. Mitigation is not to outsource the
kernel (nothing in the surveyed landscape implements it), but to treat
the kernel as a narrow, documented, stable public contract, keeping it
small and deliberately boring, so outside contribution concentrates on
the wide surface (adapters, transports, routing) instead. If this
strategy is executed, kernel documentation is a first-class deliverable,
not an afterthought.

---

## 5. Local LLM support

**Ratified direction: add local/self-hosted models as a new
`HttpInvocation` execution class, not a fourth hand-written CLI adapter
and not a bigger architectural pivot.**

Ollama, vLLM, SGLang, and LM Studio all expose OpenAI-compatible HTTP
APIs (change `base_url`/`api_key`, official OpenAI SDK works unmodified)
— meaning this is one parameterized execution class, not N separate
integrations. `PeerAdapter`'s `plan_invocation()` -> `InvocationPlan`
today hard-requires non-empty `argv`/process `cwd`/env/stdin
(`peerhub/adapters/contract.py`), and `TransportKind` is `{PIPE, PTY}`
only (`peerhub/core/execution.py`) — genuinely process-only. The correct
shape is a tagged union:

```python
DispatchInvocation = ProcessInvocation(argv, cwd, env, stdin, ...) \
                    | HttpInvocation(endpoint, headers_ref, request,
                                     stream_protocol, ...)
```

with transport-neutral terminal evidence (variants for process facts vs.
HTTP status/`Retry-After`/remote-request-id/SSE-termination/TLS-auth
facts) reducing into the same durable attempt outcome. Do not spawn
`curl`/a helper process and pretend it's a CLI — that misrepresents
cancellation, partial-completion, and remote usage/cost facts.

Model provider vs. agent harness is a real distinction: Ollama/vLLM
generate tokens; an agent loop with tools and workspace semantics is a
harness (OpenHands SDK, a future peerhub-native harness, etc.) built on
top. A raw local-model endpoint is not yet a coding peer without one.

Sessions should default to stateless HTTP requests — cache slots or
provider response IDs must not become `SessionHint` identity unless a
runtime-specific contract proves persistence, isolation, cancellation,
and restart behavior (most OpenAI-compatible chat endpoints are
stateless; the caller resends history).

Local models are a plausible low-marginal-cost, private,
quota-independent profile for bounded work (classification,
summarization, log triage, drafting, low-risk review, extra consensus
voices) — not automatically "high availability" (two peers sharing one
GPU share a failure domain; needs an explicit `failure_domain_id`
distinct from logical peer identity) and not automatically `standard`
tier by parameter count or leaderboard rank. Model choice and harness
choice both measurably affect coding results (one 2026 controlled
study found swings of 29.4 and 27.4 percentage points respectively) —
routing eligibility should be earned via a peerhub-specific evaluation
gate per model+harness+quantization combination, not inferred.

---

## 6. Vendor ToS constraint (confirmed, applies today)

Anthropic (Feb 2026 ToS revision), OpenAI, and Google all now prohibit
routing consumer subscription credentials (Claude Pro/Max, ChatGPT
Plus/Pro, Gemini Advanced) through third-party harnesses — API-key
billing is the only sanctioned path for programmatic/agentic use across
all three as of this research. This blocks routing peerhub's existing
`cc`/`cx`/`ag` adapters through any third-party aggregator (OpenCode
included) without an explicit, separately-evaluated cost-model change
(subscription -> pay-per-token). Not a blocker for the ratified direction
above (which does not route existing vendor peers through a third-party
harness), but must gate any future proposal that would.

---

## 7. Deliberately not decided here

- The exact `HttpInvocation`/`DispatchInvocation` DTO shape, transport-
  neutral terminal-evidence variants, and their integration into
  `ApplicationWorkflows`/`PipeRunnerConfig` — this needs its own
  design-and-ratify round with the same rigor as T1's increments
  (precondition checklist, scenario simulations) before implementation.
- The exact enforcement-tier mapping for `HttpInvocation` peers (Section
  4.1) — explicitly flagged as unresolved, not implicitly decided by
  omission.
- Whether/when to integrate LiteLLM and MCP concretely, and their exact
  points of contact with `routing`/`health` (this doc ratifies the
  *direction*, not an implementation plan).
- Kernel public-contract documentation scope (Section 4.2) — flagged as
  a first-class deliverable if this strategy proceeds, not yet scoped.
- Timing relative to T1 (the outer retry-loop track, in progress as of
  this document).

---

## 8. Review record

- Round 1: `cx.deepthink` (local-LLM/runtime landscape), `ag.deepthink`
  (multi-agent framework landscape + ToS verification). Independent,
  parallel, real web research with citations.
- Round 2: `cc.deepthink`, independent third voice. Verified every
  disputed factual claim against primary vendor documentation and direct
  reads of `peerhub/dispatch/retry_authorization.py`,
  `peerhub/dispatch/capability.py`, `peerhub/dispatch/model.py`,
  `peerhub/persistence/migrations/0022_retry_authority.sql`, and
  `tests/integration/dispatch/test_retry_authorization.py`. Cross-checked
  a motivated competing-vendor source against primary docs rather than
  citing it directly.
- No round left unresolved disagreement on the central question (whether
  to preserve or discard peerhub's authority kernel). Converged without
  needing a Round 3 confirmation pass from the original two researchers,
  given the specificity and primary-source grounding of Round 2's
  findings; a future async confirmation ping to ag/cx is optional, not
  required to act on this document.

---

## 9. Current-design compatibility audit (2026-08-15)

Follow-up per explicit user request: this direction is a documented future
possibility, not scheduled work. Before moving on, audit whether today's
actual shipped design can accommodate it later without a destructive
rewrite, and make small, safe, additive fixes now where a real gap is
found -- without implementing the OSS strategy itself. Direct source
reads (`peerhub/adapters/contract.py`, `peerhub/core/execution.py`,
`peerhub/adapters/registry.py`, `peerhub/dispatch/capability.py`), not
peer dispatch, since the terminal already held full context from Sections
1-8's research.

### 9.1 Transport layer (`InvocationPlan` / `TransportKind`) -- confirmed
extensible, no patch needed now

`InvocationPlan.__post_init__` hard-requires non-empty `argv` and process-
shaped fields (`cwd_reference`, `environment_delta`, `stdin_payload`,
`TransportLimits` with `process_timeout_ms`/`silence_timeout_ms`).
`TransportKind` is `{PIPE, PTY}` today. This confirms Section 5's premise:
the current shape is genuinely process-only, not something an `HttpInvocation`
variant slots into by adding one enum value.

The good news, found by reading rather than assumed: `PeerDescriptor.transports`
is already `frozenset[TransportKind]` -- a *set*, not a single value --
meaning the original design already anticipated one adapter supporting
multiple transport kinds. Widening `TransportKind` with a new member and
introducing a sibling `HttpInvocationPlan` behind a tagged union (exactly
Section 5's proposal) is additive at the `PeerDescriptor`/registry level:
existing adapters keep declaring `{PIPE}` or `{PTY}` and are structurally
unaffected. The real touch points are the two `PeerAdapter` Protocol
methods that currently pin to the process-specific types
(`new_decoder(plan: InvocationPlan)`, `interpret_output(plan, process:
ProcessTerminalEvidence, raw_chunks)`) -- widening their parameter types to
a union is a real signature change requiring the same design-and-ratify
rigor as any T1 increment (exhaustiveness checking via `match`/`assert_never`,
per the pattern already proven in `retry_authorization.py`'s
`FailoverRoute` branch). **No code change made here** -- this confirms the
extension is well-shaped when its own ratified round happens (Section 7),
not a currently-blocking gap.

### 9.2 `peer_kind` as a collaboration dimension -- confirmed open, no
patch needed

Grepped the whole `peerhub/` production tree for hardcoded enumerations of
the 3 current peer kinds (`cc`/`ag`/`cx`) as a closed set. Found exactly
one: `mandatory_enforcement_floor()` special-cases `peer_kind == "ag"` for
the `CONFINED` floor -- a legitimate *policy* choice (ag runs unsandboxed
in the current environment), not a structural enumeration. Everywhere
else `peer_kind` is compared for equality between two already-bound
values (e.g. `capability_lease.selected_peer_kind == machine_peer_kind`),
never enumerated against a closed list. No migration/schema `CHECK`
constraint in production code pins the peer-kind set either (the only
hits were Phase 0 prototype fixtures under `tools/phase0_fixture_runner/`,
not the shipped kernel). Adding a 4th, 5th, Nth peer kind does not require
touching a scattered enumeration across many files -- this is structurally
unlike the exact drift problem found in P:'s hub.py (Sections outside this
doc). No patch needed.

### 9.3 Enforcement floor -- structurally extensible, semantic gap
correctly deferred (not silently open)

`require_enforcement_floor()`/`PeerEnforcementEvidence` already route
through an injected `PeerEnforcementEvidenceProvider` Protocol producing an
abstract `EnforcementLevel` ceiling -- not something hardwired to "did the
process carry a sandbox flag." Adding a new peer_kind's floor policy is a
one-line addition to `mandatory_enforcement_floor()`, and a new evidence
provider can measure whatever confinement guarantee is meaningful for that
peer kind. The mechanism is sound.

The genuinely open question -- correctly identified already in Section 4.1
and re-confirmed here, not newly discovered -- is the *policy content*: what
does `ENFORCED`/`CONFINED` mean for a process-less HTTP peer, and what
floor should it default to. This is a real design decision, not a code
gap, and stays deferred to its own round per Section 7. Flagging again
here only to confirm the audit didn't find a reason to resolve it
prematurely.

### 9.4 Adapter registration (`peerhub/adapters/registry.py`) -- real
small gap found, patch proposed

`_ADAPTER_FACTORIES` and `_CLI_ALIASES` are module-level `MappingProxyType`
dict literals; adding any new adapter (a future OpenCode-routed peer, a
local-model `HttpInvocation` peer, etc.) means directly editing this one
file's dict literals. That's already far better than P:'s hub.py drift
pattern (one contained file, not several), but for a package whose stated
goal is collaboration -- more peers joining over time -- a closed
module-level dict is the one place a small, purely additive improvement is
worth making now: a public registration function
(`register_adapter_factory(peer_kind, cli_aliases, factory)`) alongside
the existing dict, so new adapters can self-register without editing this
file's literals directly. Zero risk to the existing kernel (registry.py
has no relationship to `retry_authorization.py`'s authority boundary) and
directly serves "collaboration is the goal" by lowering the cost of a new
peer joining. Proposed as a follow-up implementation (see task tracker),
not applied inline by the terminal, to keep the same TDD +
independent-verification discipline used for every other change this
session.

### 9.5 Summary

| Area | Finding | Action |
|---|---|---|
| Transport (`InvocationPlan`) | Process-only today, additive extension confirmed feasible (`PeerDescriptor.transports` already a set) | None now; ratify+implement in its own round per Section 7 |
| `peer_kind` | Already an open key, not a closed enumeration | None needed |
| Enforcement floor | Mechanism (evidence-provider Protocol) already extensible | None now; the *policy* content for HTTP peers stays deferred per Section 4.1 |
| Adapter registry | Real small gap: closed module-level dict | Small additive patch proposed (registration function), scheduled as its own small increment |

---

## 10. A2A (Agent2Agent) evaluation (2026-08-26, terminal + cx)

This section was never part of the original 2026-08-15 debate — it was
added later, pre-TDD, once the hub.py-replacement gap-1..7 design set
(see `HUB-REPLACEMENT-*` docs) had converged and the terminal noticed
this doc's own interop diagram (Section 4) only accounts for MCP, never
evaluating A2A at all (confirmed via full-repo grep — zero prior
mentions). Given gap-5's task lifecycle and gap-2's consensus/coordinator
design were *just* ratified as TDD-ready, and A2A's domain (agent-to-agent
task delegation) genuinely overlaps with both — unlike MCP, which only
concerns tool/resource exposure — this got a real evaluation with the
same Position-A/Position-B + primary-source-verification rigor as
Section 3 above, rather than being waved through by analogy to MCP.

**Facts (web-verified 2026-08-26, not training-data recall — this is a
fast-moving area)**: A2A is Linux Foundation-governed, reached v1.0 in
2026, 150+ supporting organizations (Google, Microsoft, AWS, Salesforce,
SAP, ServiceNow, Workday, IBM), ~23% enterprise adoption vs MCP's ~78%.
IBM's competing "Agent Communication Protocol" (ACP) merged INTO A2A in
August 2025 under LF AI & Data — ACP is deprecated/absorbed, so
evaluating A2A covers that lineage too. Core mechanics: Agent Cards
(capability/reachability metadata, optionally cryptographically signed),
JSON-RPC 2.0 / gRPC / HTTP+JSON transport, an 8-state Task lifecycle
(`submitted, working, input_required, auth_required, completed, failed,
canceled, rejected`). Verified directly against
[A2A's key-concepts doc](https://github.com/a2aproject/A2A/blob/main/docs/topics/key-concepts.md):
Agent Cards are "a JSON document that serves as a digital business card
for initial discovery and interaction setup," and A2A explicitly targets
agents that are "*opaque* (black-box)" to the client — internal workings,
memory, and tools not exposed. That opacity premise is the load-bearing
fact for the verdict below.

### Verdict: same shape as MCP — external interoperability layer, outside the authority kernel; TDD-ready verdict stands, no gap-5/gap-2 schema change required

```text
PeerHub authority kernel
  TargetState / CAS / leases / broker / consensus
          |
PeerHub task coordinator
          |
A2A adapter: Agent Card + auth + task projection + streaming/push
```

**Why Position A (adapter, not foundation) holds**, even though A2A's
domain overlaps peerhub's own more than MCP's did: peerhub's 3 real
adapters (`RealAgyAdapter`/`RealClaudeAdapter`/`RealCodexAdapter`) are
hand-built and fully known/controlled by one operator — not opaque
third-party agents from other organizations, which is A2A's entire
reason to exist. CAS, fencing, leases, mutation authorization, and
consensus rules are operator-local authority mechanisms A2A does not
define replacements for. A2A's task states are intentionally coarse and
externally observable by design; peerhub's internal checkpoints,
failover, lease binding, and consensus evidence should stay
peerhub-private regardless.

**Position B was taken seriously, not strawmanned**: if peerhub ever
becomes an inter-organizational agent gateway, or must delegate to an
arbitrary third-party A2A agent nobody hand-adapted, native A2A
ingress/egress would reduce integration friction. But a 4th opaque A2A
peer would need *an* adapter either way (the alternative to an A2A
adapter isn't "no adapter," it's "a different bespoke adapter") — which
settles the question in Position A's favor by itself: keep peerhub's own
protocol authoritative, add one A2A adapter when/if that use case
actually arrives, rather than replacing the kernel's own model with A2A's
now on spec.

**gap-5 (task) impact**: no schema change required. The existing state,
timestamps, and failure/approval references are sufficient for a later
projection function, entirely external to the schema:
`CREATED/READY/RUNNING/CHECKPOINTED/FAILOVER_PENDING -> working` (or
`submitted` pre-execution); `AWAITING_APPROVAL -> input_required` or
`auth_required` depending on which kind of gate it is;
`SUCCEEDED->completed`, `FAILED->failed`, `CANCELLED->canceled`. A2A's
own `Task` object already carries metadata/history/artifacts fields, so
peerhub IDs/revision/provenance can ride in A2A metadata without a
peerhub-side change. One real implementation requirement for whenever
this adapter is built (not a schema migration): the projection must be
revision-aware, reading one consistent `TargetState` snapshot and never
inferring an external terminal state from a stale one.

**gap-2 (consensus) impact**: not materially altered. Agent Cards overlap
peerhub's adapter registry only at the discovery/capability-description
boundary ("what does this remote endpoint claim to provide" vs. "which
controlled local adapter should receive this work") — A2A must never
determine active voters, quorum, coordinator authority, lease ownership,
fencing, proposal ratification, or human-override authority; those stay
peerhub governance decisions regardless of whether a future adapter
exposes a governed peerhub service as an Agent Card.

**Adjacent standards, evaluated and confirmed lower-priority** (surveyed
alongside A2A/MCP, none change the verdict above): **AGNTCY/OASF**
(Cisco/LangChain/LlamaIndex coalition; an agent capability/discovery
schema, the "DNS for agents" — overlaps Agent Cards conceptually, lower
adoption than A2A; revisit only if peerhub ever needs multi-registry
discovery). **ANP** (Agent Network Protocol; DID-based cross-org agent
identity/trust — no relevant trust boundary exists in peerhub's current
single-operator model). **Agent Skills** (Anthropic-originated, opened as
a cross-platform standard Dec 2025 — a *knowledge-packaging* format, "a
folder of instructions/scripts that teaches an agent how to approach a
category of work"; a genuinely different axis from runtime
delegation/authority/transport, not a competitor to MCP or A2A, and not
peerhub's coordination-kernel concern).

### Pre-TDD acceptance criteria added (design notes only, not schema changes)

1. gap-5 retains stable task IDs, timestamps, and approval/failure
   references, and observable revision/state transitions (already true
   of the ratified schema — nothing to change, just confirmed as a
   requirement going forward).
2. A future A2A adapter must be able to map every terminal and
   interrupted peerhub state without inventing new authority semantics.
3. A2A metadata/extension fields may carry peerhub correlation IDs and
   revision provenance when the adapter is eventually built.
4. Agent Cards are treated as discovery/capability documents only —
   never as peerhub authority or voter configuration, now or later.

**This does not reopen gap-5 or gap-2. The pre-TDD TDD-ready verdict
(`HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md`) stands, with
A2A now tracked as a documented future-interop item at the same status
as MCP** (Section 4 above): adopt later as an adapter, near-zero cost to
the kernel, real interop upside deferred until an actual opaque
third-party peer use case exists.

---

## 11. Extensibility stress test against 3 currently-popular external agent tools (2026-08-26, terminal + cx)

User request: rather than more standards-protocol theory, actually stress
the adapter-extensibility claim (Section 9: "`peer_kind` is already an
open key, not a closed enumeration") against real, currently-trendy
external agent tools. Three were picked to cover different shapes:
**OpenCode** (dominant open-source CLI coding agent, already peerhub's
own hypothetical example in Section 9), **Goose** (Block, Apache-2.0,
Linux Foundation Agentic AI governance), **OpenClaw** (renamed from a
Claude-adjacent name in Jan 2026 after an Anthropic trademark request;
self-hosted autonomous-agent gateway). Methodology: terminal drafted an
analysis grounded in direct reads of `peerhub/adapters/contract.py` and
`peerhub/core/execution.py`, cx cross-checked every factual claim against
each tool's own current docs (not training-data recall), terminal then
independently re-verified the one claim cx flagged as unconfirmed via a
live fetch. Two real errors were caught and corrected in this process —
recorded below, not silently fixed, per this session's citation-discipline
convention.

**Real peerhub constraint (grounds everything below)**: `TransportKind`
is closed to exactly `PIPE`/`PTY` today — both process-spawn transports.
`InvocationPlan` requires `argv` (non-empty), makes `stdin_payload`
optional, and the whole `PeerAdapter` contract (`plan_invocation` →
spawn → `new_decoder`/`interpret_output`) is synchronous: one bounded
invocation, wait for a terminal result. `HttpInvocation` exists only as
"ratified direction, not implemented" (Section 5).

### OpenCode — trivially adaptable, zero core changes

`opencode run <message>` is a one-shot CLI call. **Correction**: the
terminal's first pass claimed no stdin support; cx verified against
OpenCode's own CLI reference that stdin piping IS supported
(`echo "..." | opencode run`), alongside the positional-arg form — the
terminal's original claim was stale/wrong, not cx's. A direct
`RealOpenCodeAdapter` is straightforwardly possible today: `PIPE`/`PTY`
transport, `argv` or `stdin_payload` carrying the prompt, no peerhub core
change required. **Naming-collision note worth preserving**: OpenCode
also has a separate `opencode acp` server mode (stdin/stdout ndjson) —
this "ACP" is **Zed's Agent Client Protocol** (editor/client integration),
a third distinct meaning of the "ACP" acronym in this space, unrelated to
both IBM's (now A2A-absorbed) Agent Communication Protocol and to A2A
itself. Do not conflate the three in any future doc.

### Goose — trivially adaptable, AND double-validates the interop surface

`goose run --no-session -t "<task>"` — same argv-based headless shape,
same conclusion as OpenCode: a direct `RealGooseAdapter` needs no core
changes. Additionally, Goose ships MCP as its primary extension model
(70+ built-in extensions, 1700+ community MCP servers) and exposes the
same Zed ACP server mode; a third-party MCP↔A2A bridge exists for it
too. **cx's refinement (adopted)**: this validates the adapter-registry
extensibility claim, but does **not** prove a direct adapter and an
MCP/A2A-bridge path are interchangeable — a direct adapter gives
process-level control, local workspace binding, exact exit-status
semantics, and precise resource limits that a standards-bridge path
would not. Which to use depends on whether peerhub wants operator-grade
control (direct adapter) or is content treating Goose as an external
standards-speaking agent (bridge). Both paths are real and available;
neither is proven to be strictly better in general.

### OpenClaw — the real architectural seam, but not a current blocker

**Correction (important)**: the terminal's first draft cited a specific
`POST /workflow/start` + callback-URL REST contract as an established
fact. cx flagged this as unconfirmed against OpenClaw's own official
docs and downgraded it to `TEST NEEDED` rather than accepting it — the
terminal then independently re-verified via a live fetch of
`docs.openclaw.ai`: confirmed real are a persistent self-hosted Gateway
("single source of truth for sessions, routing, and channel
connections"), a Web Control UI, multi-agent routing, and webhooks
mentioned under "Capabilities" with no further endpoint detail on that
page; the specific `/workflow/start` shape is **not** confirmed by
primary docs and must not be treated as fact without a source that
actually shows it. This is exactly the "verify peer citations, don't
propagate an unverified claim" discipline working as intended — the
terminal's own first-pass research was the source of the unverified
claim here, not cx.

What IS confirmed: OpenClaw's real automation surface is
gateway/session/webhook-shaped, not spawn-one-process-and-wait. This is
materially different from peerhub's current `PIPE`/`PTY` + synchronous
`InvocationPlan` model.

**cx's architectural refinement (adopted over the terminal's original
"just build HttpInvocation" framing)**: even granting OpenClaw needs an
HTTP-capable transport, `HttpInvocation` alone is insufficient, because
there are two genuinely different patterns being conflated:

- **request/response HTTP** — `HttpInvocation` models this fine (send a
  request, get a response body back synchronously or via simple polling).
- **submit-now, complete-later** — a remote correlation ID comes back
  immediately, and the real completion arrives later via an independent
  callback/webhook. A synchronous `InvocationPlan` cannot represent this
  shape at all; it needs a separate pair of concepts:

```text
HttpSubmitPlan
  outbound request, remote correlation ID,
  callback/auth requirements, timeout/cancellation policy

WebhookReceiver / CompletionIngress
  authenticated callback, correlation lookup,
  idempotent completion event, task-state CAS transition
```

The callback must never mutate peerhub state directly — it should enter
through a brokered completion event with correlation, authentication,
replay protection, and CAS enforcement, with gap-5's task lifecycle (not
the adapter/transport layer) owning eventual completion. This is a clean
fit with gap-5's already-ratified schema: an externally-delivered
completion signal is structurally the same shape as any other task
transition, already CAS-gated.

**Is this worth building now?** No — for the current real use case
(3 known, operator-controlled adapters), building HTTP+webhook
infrastructure now would expand peerhub's authority and security surface
ahead of an actual deployment need. Same conclusion pattern as A2A's
"opaque third-party agent" case: a real, documented future requirement,
not current TDD scope.

### Verdict

peerhub's real architecture generalizes cleanly to OpenCode and Goose
today, through the existing adapter contract, with zero core changes —
directly confirming Section 9's "open key" extensibility claim, not just
in theory but against 2 of the most-used tools in this category right
now. It does **not** generalize to OpenClaw's gateway/webhook shape
without a genuine extension to the invocation/completion model — a real
architectural seam, correctly surfaced by this exercise, but **not a
TDD blocker**: `HttpInvocation` stays a documented future direction
(now justified by a concrete example instead of only a hypothetical
local-LLM one), not promoted to mandatory TDD scope.

**One new pre-TDD design constraint, added as a result of this
exercise**: any future externally-delivered task completion (OpenClaw-
style webhook, or anything else async) must be representable as an
authenticated, idempotent, CAS-checked task event — never a direct state
mutation from an inbound callback. This is a natural, zero-cost extension
of gap-5's already-ratified task schema (an external completion signal
is just another CAS-gated transition), not a new mechanism.

**Missing comparison, flagged by cx, not yet done**: an IDE-hosted agent
(e.g. Cline/Roo Code) would stress a third shape — long-lived
editor/session integration rather than CLI-once or gateway/webhook.
OpenCode's and Goose's own ACP (Zed protocol) modes give partial coverage
of this category already. Useful follow-up, not required before TDD.
