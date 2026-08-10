# Capability/Mutation-Lease Design — Re-Ratification Errata (2026-08-08 → 2026-08-10)

**Status: errata/addendum to [CAPABILITY-LEASE-DESIGN-2026-08-08.md](file:///P:/peerhub/docs/design/CAPABILITY-LEASE-DESIGN-2026-08-08.md). Not a replacement — that document remains the canonical roadmap proposal; this one records verified/refuted findings, anchoring decisions, and concrete enforcement locations inside the codebase as of 2026-08-10.**

---

## Why this errata exists

cx raised 5 drift points during capability-lease re-ratification (2026-08-10), asserting that the original design's anchor points and enforcement assumptions may have drifted since 2026-08-08 given the intervening Slice 5 work (real adapter conformance, `dispatch_and_execute` wiring, `direct_ask` pipeline). This document records each claim's verification status against the actual codebase, resolves the anchoring question (where does `CapabilityLease` attach?), and specifies the pre-spawn enforcement gate location.

---

## Section 1 — cx's 5 drift points: verified/refuted with file:line citations

### Drift Point 1: "CommandDescriptor.mutability is still binary (MUTATING / READ_ONLY), not the proposed 4-tier model"

**VERIFIED.** [`Mutability`](file:///P:/peerhub/peerhub/application/api.py#L88-L91) (api.py:88–91) is a 2-value enum:
```python
class Mutability(str, Enum):
    MUTATING = "MUTATING"
    READ_ONLY = "READ_ONLY"
```
[`CommandDescriptor`](file:///P:/peerhub/peerhub/application/api.py#L104-L113) (api.py:104) references it as a field (`mutability: Mutability`). This binary enum was always understood as an existing primitive the design would *extend*, not as the final capability model — the original doc explicitly says "this is the natural anchor point for a capability check" and proposes the 4-tier model as a separate concept. **No drift; the binary enum was never intended to be the 4-tier model.**

### Drift Point 2: "PeerAdapter.plan_invocation has no CapabilityLease parameter — the adapter boundary doesn't enforce capabilities yet"

**VERIFIED.** [`PeerAdapter.plan_invocation`](file:///P:/peerhub/peerhub/adapters/contract.py#L617-L623) (contract.py:617–623) accepts `(request, profile, session, limits)` — no capability or lease parameter:
```python
def plan_invocation(
    self,
    request: AdapterRequest,
    profile: ProfileDescriptor,
    session: SessionHint | None,
    limits: TransportLimits,
) -> InvocationPlan: ...
```
This is the expected state: the original design explicitly says "Targeted for Stage 3+, not a blocker for current Tier-1/Tier-2 work." The capability lease parameter was never scheduled to land before real adapter conformance work (Stage 3). **No drift — correctly deferred.**

### Drift Point 3: "SessionLeaseCoordinator is about session/generation CAS, not capability scoping — the naming collision warning from the original doc is still unresolved"

**VERIFIED.** [`SessionLeaseCoordinator`](file:///P:/peerhub/peerhub/dispatch/session_lease.py#L35-L36) (session_lease.py:35–36) docstring reads: *"Orchestrate Phase 1 session binding, lease lifecycle, and recovery."* Its [`create_session_and_lease`](file:///P:/peerhub/peerhub/dispatch/session_lease.py#L51-L58) method (session_lease.py:51–58) creates `SessionBindingSnapshot` + `LeaseSnapshot` — these are generation-CAS leases (which session generation is active), not capability-scoping leases (what mutations a dispatch is allowed to perform). The original doc's recommendation to keep these deliberately separate still applies. **No drift — the naming collision was already flagged; this errata re-confirms the recommendation: `CapabilityLease` must be a separate concept from `SessionLeaseCoordinator`'s generation lease.**

### Drift Point 4: "dispatch_and_execute now exists and calls plan_invocation — the enforcement anchor point has materialized since the original doc"

**VERIFIED.** [`dispatch_and_execute`](file:///P:/peerhub/peerhub/application/workflows.py#L510-L526) (workflows.py:510–526) is a real method, and it calls `plan_invocation` at [workflows.py:535](file:///P:/peerhub/peerhub/application/workflows.py#L535):
```python
invocation_plan = selected_peer_adapter.plan_invocation(
    request=adapter_request,
    profile=profile,
    session=None,
    limits=limits,
)
```
This is the concrete location where a `CapabilityLease` check must be injected — between the adapter selection (workflows.py:529–533) and the `plan_invocation` call (workflows.py:535). The caller in [`direct_ask.py`](file:///P:/peerhub/peerhub/application/direct_ask.py#L243-L254) (direct_ask.py:243–254) passes adapter, profile, and limits but no capability constraint. **This is the primary anchoring decision of this errata — see Section 2.**

### Drift Point 5: "The 4-coordinator split in DispatchService hasn't changed — AdmissionCoordinator is the natural pre-dispatch gate for capability enforcement"

**VERIFIED.** [`DispatchService.__init__`](file:///P:/peerhub/peerhub/dispatch/service.py#L140-L158) (service.py:140–158) still instantiates the same 4 coordinators:
```python
self._admission = AdmissionCoordinator(...)
self._artifacts = ArtifactCoordinator(...)
self._attempts = AttemptLifecycleCoordinator(...)
self._sessions = SessionLeaseCoordinator(...)
```
[`AdmissionCoordinator`](file:///P:/peerhub/peerhub/dispatch/admission.py#L35-L36) (admission.py:35) handles "Phase 1 admission, validation, and idempotency." **This is the correct pre-dispatch gate for capability enforcement — see Section 3.**

---

## Section 2 — Proposed `CapabilityLease` anchor: `dispatch_and_execute()`

### The decision

`CapabilityLease` should be anchored as a **required parameter on `dispatch_and_execute()`**, not as a field on `PeerAdapter.plan_invocation()` or a side-channel config lookup.

### Rationale

1. **`dispatch_and_execute()` is the single chokepoint.** Every real dispatch — whether from `direct_ask.py` (direct_ask.py:243), integration tests (test_vertical_dispatch.py, test_real_agy_adapter_via_pipe.py), or future multi-step orchestration — flows through this method. A capability lease parameter here cannot be bypassed by any entrypoint.

2. **`plan_invocation()` is the wrong layer.** The adapter boundary (`PeerAdapter`) is vendor-specific; different adapters translate capability constraints into different CLI flags (`--disable apps` for Codex, whatever ag/cc equivalents turn out to be). The adapter should *receive* the lease and translate it into invocation constraints — but the lease itself should be validated and threaded *before* the adapter is consulted, at the workflow level.

3. **`dispatch_and_execute()` already has the right shape.** Its signature (workflows.py:510–525) already accepts `peer_adapter`, `profile`, `limits`, and `completion_contract` — a `capability_lease: CapabilityLease` parameter fits naturally alongside these, and its absence would be a type error, not a silent default.

### Concrete signature change (proposed, not implemented)

```python
def dispatch_and_execute(
    self,
    command_id: CommandID | str,
    *,
    capability_lease: CapabilityLease,   # NEW — required, no default
    materializer: ArtifactMaterializer,
    adapter_request: AdapterRequest,
    peer_adapter: PeerAdapter | None = None,
    profile: ProfileDescriptor,
    limits: TransportLimits,
    workspace_roots: Mapping[str, Path],
    content_providers: Mapping[str, Callable[[], bytes]],
    completion_contract: CompletionContract,
    heartbeat_timeout_ms: int,
    transport: str = "pipe",
    service: DispatchService | None = None,
) -> ExecutionWorkflowResult:
```

### Pre-spawn enforcement gate

The capability lease must be checked **after** adapter selection but **before** `plan_invocation()` — i.e., between workflows.py:533 and workflows.py:535:

```python
# Current:
if selected_peer_adapter is None:
    raise ValueError("peer_adapter is required")
# >>> ENFORCEMENT GATE GOES HERE <<<
invocation_plan = selected_peer_adapter.plan_invocation(...)
```

If the lease does not authorize the requested mutation level (see Section 4), `dispatch_and_execute` raises a `CapabilityLeaseViolation` error and never reaches `plan_invocation()` or process spawn. The adapter's `plan_invocation` then receives the (validated) lease to translate capability constraints into adapter-specific CLI flags.

---

## Section 3 — Pre-spawn enforcement gate location in `AdmissionCoordinator`

For dispatches that go through the full admission pipeline (not just `dispatch_and_execute` directly), the capability lease should also be validated during admission:

- **Location**: [`AdmissionCoordinator`](file:///P:/peerhub/peerhub/dispatch/admission.py#L35) (admission.py:35) — the "Phase 1 admission, validation, and idempotency" coordinator.
- **When**: During `_load_admission()` (admission.py:52–61), after the request and receipt are loaded but before the lease is returned. The loaded `LeaseSnapshot` (admission.py:70) should be cross-checked against the capability lease to ensure the session's generation-CAS lease and the capability lease are both valid.
- **Why here in addition to `dispatch_and_execute`**: Defense in depth. `dispatch_and_execute` is the enforcement chokepoint for the execution path; `AdmissionCoordinator` is the enforcement chokepoint for the state-machine path. Both must agree.

---

## Section 4 — The 3-level `EnforcementLevel` model

Separate from *what capabilities a lease grants* (the 4-tier model, Section 5), the system needs to specify *how strictly* those capabilities are enforced. Three levels:

| Level | Name | Behavior | Use case |
|-------|------|----------|----------|
| 0 | `ADVISORY` | Log capability constraint violations; do not block dispatch. | Development, dry-run, audit-only mode. |
| 1 | `ENFORCED` | Block dispatch on violation; raise `CapabilityLeaseViolation`. Peer is launched with adapter-translated CLI constraints (e.g., `--disable apps`, sandbox flags). No OS-level confinement. | Production dispatches where the peer CLI's own sandbox is trusted. cx's `-s workspace-write`, Codex `--disable apps`. |
| 2 | `CONFINED` | `ENFORCED` + OS-level process confinement (restricted tokens, containers, filesystem ACLs). Peer process cannot mutate outside the lease's allowed surface even if the peer CLI's sandbox is bypassed or misconfigured. | High-assurance dispatches; unsandboxed peers (ag's known gap — see DIR-002 KNOWN GAP). |

### Design notes

- `ADVISORY` is not a no-op — it still records the lease, logs violations, and produces audit evidence. It just doesn't block.
- `CONFINED` is explicitly deferred (same as the original doc's "Full Windows-restricted-token process confinement" deferral) but modeled now so the type system is ready.
- The `EnforcementLevel` is a property of the `CapabilityLease`, not of the `CapabilityTier` — the same tier (e.g., `WORKTREE_WRITE`) can be enforced at different levels depending on the peer's trust posture.

---

## Section 5 — The retained 4-tier capability model

The original doc's 4-tier model is retained without modification:

| Tier | Name | What it permits | What it blocks |
|------|------|-----------------|----------------|
| 0 | `READ_ONLY` | Read any file in the workspace; run read-only CLI commands. | Any file write, any git operation, any remote operation. |
| 1 | `WORKTREE_WRITE` | Tier 0 + create/modify/delete files in the worktree. | `git add`, `git commit`, `git push`, any remote mutation. |
| 2 | `GIT_MUTATE` | Tier 1 + `git add`, `git commit`, branch operations, ref changes. | `git push`, any remote API call (GitHub, etc.). |
| 3 | `REMOTE_MUTATE` | Tier 2 + `git push`, remote API calls, deployment actions. | Nothing — full authority. |

### Relationship to existing `Mutability` enum

`Mutability.READ_ONLY` maps to `CapabilityTier.READ_ONLY` (tier 0). `Mutability.MUTATING` is an umbrella that spans tiers 1–3 — the capability lease *refines* the mutating case into three distinct scopes. The existing `Mutability` enum does not need to change; `CapabilityTier` is a separate, more granular concept that the lease carries.

### Relationship to `EnforcementLevel`

A `CapabilityLease` combines both:

```python
@dataclass(frozen=True)
class CapabilityLease:
    lease_id: str
    tier: CapabilityTier          # What the dispatch is allowed to do
    enforcement: EnforcementLevel  # How strictly that constraint is applied
    issued_at: int
    expires_at: int | None
    issuer: str                    # Who/what granted this lease
```

---

## Section 6 — Reconciliation with original doc

| Original doc section | Status in this errata |
|---|---|
| "Why this exists" | Unchanged. The preflight disconnect in hub.py remains the motivating incident. |
| "How this maps onto peerhub's existing architecture" | **Updated.** `dispatch_and_execute()` now exists (it was a future concept in the original doc). The anchor point is now concrete: workflows.py:510. |
| Design pass — ag's proposal | Unchanged (historical record). |
| Design pass — cx's critique | Unchanged (historical record). Refuted flags and PATH-shim bypasses still apply. |
| "What this means for peerhub's design" — point 1 (adapter boundary) | **Refined.** Lease validation happens at `dispatch_and_execute` *before* the adapter boundary; the adapter *translates* the lease into CLI constraints but does not validate it. |
| Point 2 (4-tier model) | Retained verbatim — see Section 5. |
| Point 3 (enforcement at subprocess creation) | **Concretized.** The pre-spawn gate is workflows.py:533→535 (between adapter selection and `plan_invocation`). |
| Point 4 (verified CLI flag surfaces) | Unchanged. DIR-004 (measured, not assumed) still governs. |
| Point 5 (no shell-level interception as security boundary) | Unchanged. |
| "Explicitly deferred" items | Unchanged, mapped to `EnforcementLevel.CONFINED` for future work. |

---

## Also recorded

- The `CapabilityLease` concept is **deliberately separate** from `SessionLeaseCoordinator`'s generation-CAS lease — re-confirmed from Drift Point 3. Different lifecycle, different semantics, should not share a coordinator or data model.
- `direct_ask.py:243` is the first caller that will need to construct and pass a `CapabilityLease` when this design is implemented.
- All 3 real adapters (`RealAgyAdapter`, `RealClaudeAdapter`, `RealCodexAdapter`) define `plan_invocation` — their signatures will need a `capability_lease` parameter added when the lease is threaded through. Locations: [agy_adapter.py:121](file:///P:/peerhub/peerhub/adapters/agy_adapter.py#L121), [claude_adapter.py:128](file:///P:/peerhub/peerhub/adapters/claude_adapter.py#L128), [codex_adapter.py:139](file:///P:/peerhub/peerhub/adapters/codex_adapter.py#L139).
