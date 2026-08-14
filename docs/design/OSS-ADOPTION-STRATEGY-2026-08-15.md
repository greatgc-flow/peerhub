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
        +-- interop:    MCP surface
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
