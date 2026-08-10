# Capability/Mutation-Lease Design — Re-Ratification Errata (2026-08-08 → 2026-08-10)

**Status: errata/addendum to [CAPABILITY-LEASE-DESIGN-2026-08-08.md](file:///P:/peerhub/docs/design/CAPABILITY-LEASE-DESIGN-2026-08-08.md). Not a replacement — that document remains the canonical roadmap proposal; this one records verified/refuted findings, anchoring decisions, and concrete enforcement locations inside the codebase as of 2026-08-10. Section 7 is a second, normative addendum that supersedes Sections 2–5 wherever they conflict.**

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

> **Superseded by Section 7:** the execution chokepoint remains correct, but it accepts a durable `capability_lease_id` and loads the authoritative lease; it does not trust a caller-supplied lease value.

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

> **Corrected by Section 7:** `_load_admission()` covers replay only. Fresh issuance and validation belong inside `AdmissionCoordinator.admit_request()`'s existing transaction.

For dispatches that go through the full admission pipeline (not just `dispatch_and_execute` directly), the capability lease should also be validated during admission:

- **Location**: [`AdmissionCoordinator`](file:///P:/peerhub/peerhub/dispatch/admission.py#L35) (admission.py:35) — the "Phase 1 admission, validation, and idempotency" coordinator.
- **When**: During `_load_admission()` (admission.py:52–61), after the request and receipt are loaded but before the lease is returned. The loaded `LeaseSnapshot` (admission.py:70) should be cross-checked against the capability lease to ensure the session's generation-CAS lease and the capability lease are both valid.
- **Why here in addition to `dispatch_and_execute`**: Defense in depth. `dispatch_and_execute` is the enforcement chokepoint for the execution path; `AdmissionCoordinator` is the enforcement chokepoint for the state-machine path. Both must agree.

---

## Section 4 — The 3-level `EnforcementLevel` model

> **Refined by Section 7:** the lease records a minimum required level. Invocation-bound evidence records the realized level, and mandatory policy floors can reject `ADVISORY` or `ENFORCED` even when the lease was otherwise valid.

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

> **Corrected by Section 7:** application-handler mutability and downstream peer authority are different axes. The downstream tier is a separate structured admission field and is not inferred from `CommandDescriptor.mutability`.

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
- `direct_ask.py:243` is the first execution caller that must relay the authoritative `capability_lease_id` returned by admission. It must not construct a lease.
- All 3 real adapters (`RealAgyAdapter`, `RealClaudeAdapter`, `RealCodexAdapter`) define `plan_invocation` — their signatures will need an opaque `ValidatedCapabilityLease` parameter added when the lease is threaded through. Locations: [agy_adapter.py:121](file:///P:/peerhub/peerhub/adapters/agy_adapter.py#L121), [claude_adapter.py:128](file:///P:/peerhub/peerhub/adapters/claude_adapter.py#L128), [codex_adapter.py:139](file:///P:/peerhub/peerhub/adapters/codex_adapter.py#L139).

---

## Section 7 — Second errata addendum: capability-lease HOLD resolution

### Normative precedence

This section resolves the four blockers found in the final cross-check. Where it conflicts with an earlier statement in this errata, **this section controls**. In particular:

- `dispatch_and_execute()` must receive a durable `capability_lease_id`, not a caller-constructed `CapabilityLease` value.
- `direct_ask.py` requests a tier and relays the returned lease ID; it never issues a lease.
- `_load_admission()` is the replay validator, not the issuance location.
- `CapabilityLease.enforcement` is replaced by `minimum_enforcement`; an invocation-specific enforcement receipt records what was actually realized.
- `CommandDescriptor.mutability` is not the downstream peer-capability source of truth.

These corrections follow from the current code. `_load_admission()` loads already-persisted request/receipt/session-lease records ([admission.py:52–80](file:///P:/peerhub/peerhub/dispatch/admission.py#L52-L80)) and is reached from the idempotency path at [admission.py:150–154](file:///P:/peerhub/peerhub/dispatch/admission.py#L150-L154). Fresh admission instead creates IDs, reduces the request, reserves the generation lease, writes all records, and commits at [admission.py:293–359](file:///P:/peerhub/peerhub/dispatch/admission.py#L293-L359). Therefore the earlier proposal to validate only in `_load_admission()` covered replay but did not authorize or bind a fresh admission.

### 7.1 Authoritative issuance and durable binding

#### Issuer and transaction boundary

`AdmissionCoordinator.admit_request()` is the authoritative minting boundary. It already owns the fresh-admission transaction at [admission.py:265–357](file:///P:/peerhub/peerhub/dispatch/admission.py#L265-L357), while `DispatchService.admit_request()` is the public delegating facade at [service.py:223–259](file:///P:/peerhub/peerhub/dispatch/service.py#L223-L259). The caller may submit a required tier, but it may not submit a lease ID, granted tier, enforcement claim, issuer, or expiry.

The coordinator receives injected `CapabilityPolicy`/issuer and `PeerEnforcementEvidenceProvider` dependencies, analogous to its existing injected store, clock, and ID source at [admission.py:38–49](file:///P:/peerhub/peerhub/dispatch/admission.py#L38-L49). The evidence provider resolves the selected instance/profile to a machine-owned peer kind and measured enforcement ceiling; neither value is accepted from the command payload. For a fresh admission it must:

1. validate the caller's structured required tier;
2. ask the injected policy for a `CapabilityGrantDecision` for the authenticated subject, selected peer instance/profile/kind, policy revision, and requested tier;
3. apply the mandatory enforcement floor from Section 7.4;
4. reject admission if machine-owned enforcement evidence says the selected target cannot meet that floor (unknown/absent evidence is insufficient);
5. mint a new capability-lease ID with `IdSource`;
6. create a least-privilege lease whose `authorized_tier` equals the required tier, even if policy would permit a higher ceiling; and
7. persist the request, admission receipt, generation lease, and capability lease in the same transaction before the existing commit at admission.py:357.

The authenticated subject must also be authoritative. The application API already receives `RequestContext.principal`/`client_id` ([core/ports.py:6–11](file:///P:/peerhub/peerhub/core/ports.py#L6-L11)) and passes `caller.principal` into admission at [api.py:295–308](file:///P:/peerhub/peerhub/application/api.py#L295-L308). The direct CLI path bypasses that handler and currently supplies the literals `authenticated_principal="cli-user"` and `actor_authorized=True` at [direct_ask.py:198–210](file:///P:/peerhub/peerhub/application/direct_ask.py#L198-L210). That pair is not authority evidence. Replace the naked string/boolean issuance input with an opaque `AuthenticatedSubject` produced by the API authentication boundary or an injected, machine-owned local `CallerIdentityProvider` for direct CLI use. The CLI may not accept a principal override flag. If the direct path has no verified subject, capability admission fails closed; it does not issue a lease from those literals.

This is deliberately separate from the generation-CAS `LeaseSnapshot`: the current fresh path reserves that lease at [admission.py:317–331](file:///P:/peerhub/peerhub/dispatch/admission.py#L317-L331). A capability lease may bind to its ID without merging their types or lifecycles.

#### Proposed durable record

The implementation introduces `CapabilityTier`, `EnforcementLevel`, `CapabilityGrantDecision`, and `CapabilityLease` in a dispatch-owned capability module. The durable lease has no caller-writable fields after issuance:

```python
@dataclass(frozen=True)
class CapabilityLease:
    capability_lease_id: str
    command_id: CommandID
    admission_receipt_id: str
    session_lease_id: str
    subject_principal_id: str
    selected_peer_kind: str
    required_tier: CapabilityTier
    authorized_tier: CapabilityTier
    minimum_enforcement: EnforcementLevel
    selected_peer_instance_id: str
    selected_profile_id: str
    route_decision_digest: str
    policy_revision: RevisionValue
    issuer_id: str
    issued_at: int
    expires_at: int | None
```

The binding invariants are exact equality, not “at least roughly the same request”:

- `CapabilityLease.command_id == RequestSnapshot.command_id == AdmissionReceipt.command_id == LeaseSnapshot.fence.command_id`;
- `CapabilityLease.admission_receipt_id == AdmissionReceipt.admission_receipt_id`;
- `CapabilityLease.session_lease_id == RequestSnapshot.lease_id == AdmissionReceipt.lease_id == LeaseSnapshot.lease_id`;
- `CapabilityLease.subject_principal_id == RequestSnapshot.authenticated_principal`;
- its peer instance, profile, and route digest equal the request values; its peer kind equals the machine-owned instance lookup; its policy revision equals both request and receipt values; and
- `authorized_tier == required_tier`.

The existing records currently have no capability fields: see `AdmissionReceipt` at [contract.py:507–562](file:///P:/peerhub/peerhub/dispatch/contract.py#L507-L562) and `RequestSnapshot` at [contract.py:565–592](file:///P:/peerhub/peerhub/dispatch/contract.py#L565-L592). Add `required_capability_tier` to `RequestSnapshot`. A new bespoke migration, next in the current sequence after `0017_drop_legacy_outbox.sql`, creates `capability_leases` with UNIQUE foreign keys to `dispatch_requests(command_id)`, `admission_receipts(admission_receipt_id)`, and `leases(lease_id)`. Those are the current authoritative tables and relationships at [0003_command_request_attempt.sql:42–141](file:///P:/peerhub/peerhub/persistence/migrations/0003_command_request_attempt.sql#L42-L141) and [0003_command_request_attempt.sql:207–220](file:///P:/peerhub/peerhub/persistence/migrations/0003_command_request_attempt.sql#L207-L220). The parent request/receipt tables do not also foreign-key back to `capability_leases`; avoiding that redundant reverse pointer avoids a circular insertion dependency. The four-value `DispatchAdmission` return carries the new lease to callers.

`DispatchUnitOfWork` gains `add_capability_lease()`, `get_capability_lease()`, and unique lookup by command/admission receipt beside the existing admission-receipt operations at [unit_of_work.py:127–145](file:///P:/peerhub/peerhub/dispatch/unit_of_work.py#L127-L145). `AdmissionCoordinator` validates the four newly constructed records with the common validator described below before the first insert, writes all four, and commits once. Any fault before commit must leave none of them durable.

#### Replay behavior

Replay never mints or accepts a replacement lease. `_load_admission()` must load the uniquely bound lease by command/admission receipt, call the same binding validator used on the fresh objects, and return `(request, receipt, session_lease, capability_lease)`. The current internal-consistency check at [admission.py:62–80](file:///P:/peerhub/peerhub/dispatch/admission.py#L62-L80) is the exact place to extend. Both `peek_idempotent_admission()` ([admission.py:185–232](file:///P:/peerhub/peerhub/dispatch/admission.py#L185-L232)) and the replay branch inside `admit_request()` ([admission.py:266–291](file:///P:/peerhub/peerhub/dispatch/admission.py#L266-L291)) then return the same four-value admission, including the original durable lease ID.

Static binding corruption is fatal on replay. Expiry is not grounds to mint a different lease under the same idempotency key: replay returns the original admission identity, while pre-spawn revalidation denies a later dispatch if that lease is no longer valid.

### 7.2 One validation implementation, invoked at fresh admission, replay, and dispatch

The implementation must not copy a list of equality checks into three coordinators. Define one pure `validate_capability_binding(request, receipt, session_lease, capability_lease)` function. It verifies the immutable equalities in Section 7.1 and returns a typed `ValidatedCapabilityBinding`; it raises `CapabilityLeaseViolation` otherwise. Keep time-varying authorization in one separate `CapabilityPolicy.revalidate(binding, current_policy_revision, now)` operation: it checks expiry, revocation, and that the machine-owned current policy revision still equals the lease's issuance revision. A revision change fails closed; it never silently upgrades, downgrades, or renews the lease. A new authority decision requires a new command/admission identity.

Use that function at three boundaries:

1. **Fresh admission:** after all four values are constructed but before `unit.add_request()` currently begins the write sequence at [admission.py:338](file:///P:/peerhub/peerhub/dispatch/admission.py#L338). The issuer separately validates the grant decision and mandatory floor, then persists atomically.
2. **Replay:** from `_load_admission()` after all four durable records are loaded, extending the current consistency check at [admission.py:73–80](file:///P:/peerhub/peerhub/dispatch/admission.py#L73-L80). This proves that the replayed receipt still names the originally issued lease; it does not reissue one.
3. **Dispatch:** a new `DispatchService.require_dispatch_capability(command_id, capability_lease_id, peer_instance_id, adapter_descriptor, profile, enforcement_evidence, now)` loads the authoritative records through a read UoW, calls `validate_capability_binding()` and `CapabilityPolicy.revalidate()`, then adds only dispatch-time checks: ID supplied by the caller matches the durable ID, request state is dispatchable, selected target/profile match, `adapter_descriptor.peer_kind` matches the machine-resolved durable kind, and machine-owned evidence proves the mandatory floor can be met. `PeerDescriptor` currently exposes `peer_kind` at [adapters/contract.py:133–144](file:///P:/peerhub/peerhub/adapters/contract.py#L133-L144), so this is an equality check rather than prompt or argv inference. The method returns an opaque `ValidatedCapabilityLease`, not the raw persisted DTO. The opaque value includes the durable lease ID and the exact policy revision against which it was revalidated; it contains no independent grant authority.

`dispatch_and_execute()` therefore changes from the Section 2 proposal to this shape:

```python
def dispatch_and_execute(
    self,
    command_id: CommandID | str,
    *,
    capability_lease_id: str,       # required reference, no default
    peer_instance_id: str,          # required target identity, no default
    # existing parameters follow
) -> ExecutionWorkflowResult:
```

After adapter selection at [workflows.py:528–533](file:///P:/peerhub/peerhub/application/workflows.py#L528-L533), it calls `require_dispatch_capability()` before the current `plan_invocation()` call at [workflows.py:535–540](file:///P:/peerhub/peerhub/application/workflows.py#L535-L540). `PeerAdapter.plan_invocation()` receives the resulting `ValidatedCapabilityLease`; a raw `CapabilityLease` cannot be manufactured by a caller and passed through.

There is also a final state-machine check: `record_dispatch_intent()` and `record_dispatch_intent_and_reserve_artifacts()` must require the validated lease plus the invocation enforcement receipt, re-read the lease in their existing write transaction, and invoke the same `validate_capability_binding()` and `CapabilityPolicy.revalidate()` operations against the then-current policy revision. These are the last authoritative state transitions before spawn at [workflows.py:649–663](file:///P:/peerhub/peerhub/application/workflows.py#L649-L663); actual process creation begins later at [workflows.py:685–748](file:///P:/peerhub/peerhub/application/workflows.py#L685-L748). The write rejects a stale opaque token if its policy revision differs from either the durable lease or the current machine-owned policy revision. Reusing the two validators closes revocation, expiry, policy-change, and record-substitution windows without copying their logic.

### 7.3 Required-capability-tier threading

The current unstructured fields do not reach execution. `AdmitDispatchPayload.requested_capabilities` is a `list[str]` at [api.py:199–207](file:///P:/peerhub/peerhub/application/api.py#L199-L207), decoded into another string tuple at [api.py:266–275](file:///P:/peerhub/peerhub/application/api.py#L266-L275). `RouteRequest` likewise carries only `tuple[str, ...]` at [routing/contract.py:167–178](file:///P:/peerhub/peerhub/routing/contract.py#L167-L178). `direct_ask.py` hard-codes that tuple empty at [direct_ask.py:110–120](file:///P:/peerhub/peerhub/application/direct_ask.py#L110-L120). None is a security type.

Add one required enum field named `required_capability_tier` and thread the same value through these structures and calls:

1. `AdmitDispatchPayload` and typed `AdmitDispatch`. The latter currently exposes only `requested_capabilities: tuple[str, ...]` at [commands.py:55–73](file:///P:/peerhub/peerhub/application/commands.py#L55-L73). The wire payload must reject omission or an unknown enum value; there is no permissive default.
2. `DirectAskRequest` at [direct_ask.py:30–36](file:///P:/peerhub/peerhub/application/direct_ask.py#L30-L36). The CLI `ask` parser currently has peer/prompt/workspace/profile and execution limits but no authority input at [cli.py:178–216](file:///P:/peerhub/peerhub/cli.py#L178-L216); add required `--capability-tier {READ_ONLY,WORKTREE_WRITE,GIT_MUTATE,REMOTE_MUTATE}` and pass it when constructing `DirectAskRequest` at [cli.py:106–120](file:///P:/peerhub/peerhub/cli.py#L106-L120). No prompt-text inference is permitted. `execute_direct_ask()` also receives the opaque `AuthenticatedSubject` from the trusted CLI bootstrap; this is not a user-selectable request field.
3. `_DirectAskRouteRequestFactory` and `RouteRequest`, replacing the empty security meaning at direct_ask.py:114 while leaving any non-authoritative routing hints separately named.
4. `RouteDecision`, populated by `select_route()` where the request is reduced at [routing/model.py:164–225](file:///P:/peerhub/peerhub/routing/model.py#L164-L225). Add the tier to `canonical_route_decision_digest()`; its current canonical projection at [routing/contract.py:529–570](file:///P:/peerhub/peerhub/routing/contract.py#L529-L570) omits it. This binds routing and admission to the same authority request.
5. `ApplicationWorkflows.admit_request()` (current signature [workflows.py:292–308](file:///P:/peerhub/peerhub/application/workflows.py#L292-L308)). It checks that its required argument equals the route request/decision value and passes it at the existing dispatch admission call [workflows.py:363–387](file:///P:/peerhub/peerhub/application/workflows.py#L363-L387).
6. `DispatchService.admit_request()`, `AdmissionCoordinator.admit_request()`, and the pure `dispatch.model.admit_request()` reducer. Their current signatures have no tier at [service.py:223–241](file:///P:/peerhub/peerhub/dispatch/service.py#L223-L241), [admission.py:234–256](file:///P:/peerhub/peerhub/dispatch/admission.py#L234-L256), and [model.py:166–183](file:///P:/peerhub/peerhub/dispatch/model.py#L166-L183).
7. `RequestSnapshot.required_capability_tier` and the new durable `CapabilityLease`; `DispatchAdmission` expands from the current three values to `(request, admission_receipt, session_lease, capability_lease)`.
8. `DispatchAdmissionView.capability_lease_id`, alongside its current request/receipt/generation-lease identifiers at [commands.py:42–52](file:///P:/peerhub/peerhub/application/commands.py#L42-L52). The direct path obtains the ID from the fourth value returned after its current admission call at [direct_ask.py:198–215](file:///P:/peerhub/peerhub/application/direct_ask.py#L198-L215) and relays that ID at the current execution call [direct_ask.py:243–254](file:///P:/peerhub/peerhub/application/direct_ask.py#L243-L254).

`CommandDescriptor.mutability` must **not** be silently converted into the downstream peer tier. `CommandDescriptor` describes whether an application API handler mutates PeerHub state ([api.py:103–113](file:///P:/peerhub/peerhub/application/api.py#L103-L113)); for example, `dispatch.admit` is always registered as `MUTATING` because it writes admission state ([api.py:334–340](file:///P:/peerhub/peerhub/application/api.py#L334-L340)), even when the admitted peer task is `READ_ONLY`. Conflating those meanings would overgrant. The downstream tier comes from the typed `AdmitDispatch.required_capability_tier`, is policy-authorized during admission, and becomes durable. `Mutability` remains an API/idempotency invariant only.

`AdapterRequest` also does not carry another caller-controlled tier. Its current contract describes an already-authorized request at [adapters/contract.py:295–310](file:///P:/peerhub/peerhub/adapters/contract.py#L295-L310); the adapter receives the opaque `ValidatedCapabilityLease` as a separate `plan_invocation()` argument. This leaves exactly one authoritative durable tier.

One adjacent call site must be corrected when that signature changes: `resolve_peer_target()` currently creates a dummy `AdapterRequest` and calls `plan_invocation()` merely to discover `argv[0]` at [registry.py:137–155](file:///P:/peerhub/peerhub/adapters/registry.py#L137-L155). It must instead use a non-dispatch adapter metadata/probe method such as `executable_name()`; issuing a fake capability lease for discovery would create a bypass-shaped API.

### 7.4 Mandatory enforcement floor and the explicit ag deny

`ADVISORY` is an observation mode, not a security guarantee. A lease records `minimum_enforcement`; an `InvocationEnforcementReceipt` returned with the plan records the invocation-bound realized level, controls, evidence source tag, and plan digest. The receipt is accepted only when corroborated by the machine-owned launcher/evidence provider; an adapter's self-declaration is not proof. Status and audit output may say “advisory” but must never render it as “enforced” or “confined.”

The floor is code-owned and may be raised by policy but never lowered by config:

```python
def mandatory_enforcement_floor(
    peer_kind: str,
    tier: CapabilityTier,
) -> EnforcementLevel:
    if peer_kind == "ag" and tier is not CapabilityTier.READ_ONLY:
        return EnforcementLevel.CONFINED
    if tier is not CapabilityTier.READ_ONLY:
        return EnforcementLevel.ENFORCED
    return EnforcementLevel.ADVISORY
```

The explicit fail-closed check runs in `require_dispatch_capability()` at the first gate between adapter selection and `plan_invocation()` (current [workflows.py:528–535](file:///P:/peerhub/peerhub/application/workflows.py#L528-L535)):

```python
floor = mandatory_enforcement_floor(peer_kind, binding.required_tier)
if adapter_enforcement_ceiling < floor:
    raise CapabilityLeaseViolation(
        "selected adapter cannot meet the mandatory enforcement floor"
    )
```

Unknown or absent evidence sorts below every enforceable floor; it is never guessed upward. For ag specifically, the current adapter's plan is only `agy.exe -p ... --output-format json` and carries no confinement control at [agy_adapter.py:121–148](file:///P:/peerhub/peerhub/adapters/agy_adapter.py#L121-L148). The repository's `PeerDescriptor` has no enforcement field at all ([adapters/contract.py:133–144](file:///P:/peerhub/peerhub/adapters/contract.py#L133-L144)), and DIR-002's ag filesystem-confinement gap is backed by an `empirical_probe`. Therefore the current measured ceiling is not `CONFINED`; an ag request with `WORKTREE_WRITE`, `GIT_MUTATE`, or `REMOTE_MUTATE` is denied before planning and before attempt creation at [workflows.py:542–543](file:///P:/peerhub/peerhub/application/workflows.py#L542-L543). It cannot fall back to `ADVISORY` logging.

If the adapter registry supplies sufficient measured ceiling evidence, the second enforcement check runs immediately after `plan_invocation()` returns and before attempt creation. It verifies the invocation-bound receipt, corroborated by the launcher, actually meets both the lease minimum and mandatory floor. The same receipt is required by the atomic `record_dispatch_intent*()` call described in Section 7.2. Thus a stale descriptor, forged plan, missing receipt, or downgrade still fails before the state reaches dispatch intent and before `run_process()` at workflows.py:743.

For cx, cc, or any new peer, enforcement capability claims remain `TEST NEEDED` until supported by `cli_live`, `app_server`, `statusline`, or `empirical_probe` evidence under DIR-004. The implementation defaults unknown evidence to deny whenever the mandatory floor is `ENFORCED` or `CONFINED`.

### 7.5 Required implementation increments and acceptance checks

This design should land in independently reviewable increments:

1. capability enums, grant/lease DTOs, pure binding/floor validators, and negative unit tests;
2. schema migration plus repository/UoW support, including fresh-transaction rollback tests and replay returning the identical lease;
3. structured tier threading through API, direct ask, routing decision/digest, admission, and durable request;
4. pre-plan validation plus post-plan enforcement receipt, with `record_dispatch_intent*()` requiring both; and
5. adapter translation and empirical enforcement evidence, peer by peer.

Mandatory negative tests include: omitted/unknown tier; missing or caller-forged authenticated subject; caller-supplied or mismatched lease ID; missing durable lease; request/receipt/session-lease/principal cross-binding mismatch; tier or route-digest mismatch; expired or revoked lease; policy revision changing before pre-plan validation or between planning and dispatch-intent recording; target/profile/peer-kind substitution; replay attempting to mint a second lease; missing/unknown enforcement evidence; and ag mutating at `ADVISORY` or `ENFORCED`. The ag tests must assert `plan_invocation()` and `run_process()` were never called. A controlled fake may demonstrate the positive `CONFINED` path; it is not evidence that real ag confinement exists.

### 7.6 Remaining user decision

No security-critical ambiguity remains in these four corrections. The fail-closed choices are explicit: lease issuance is coordinator-owned, omission of the required tier is invalid, caller-provided lease values are untrusted, and ag mutation is denied until invocation-correlated `CONFINED` evidence exists. A future product decision may add a convenience CLI default of `READ_ONLY`, but that is intentionally outside this ratification because the current correction requires no default and no prompt-text inference.
