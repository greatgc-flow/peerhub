# Unified Governance/Dispatch Layer — Round 1 Independent Design Proposal

> **Author**: ag.opus (Round 1, independent — no visibility into the parallel reviewer's proposal)
> **Date**: 2026-09-24
> **Status**: DESIGN PROPOSAL (pre-ratification, pre-TDD)
> **Scope**: Addresses 6 verified gaps + IPC hygiene point as a unified architecture, not piecemeal patches

---

## 0. Executive Summary

The 6 gaps and 1 hygiene point share a single underlying cause: peerhub has mature *domain services* (consensus, health, routing, telemetry, session) but lacks the *policy orchestration layer* that connects them into decision-aware dispatch. Hub.py's `protocol.json` was that orchestration layer (via `collab_rate`, `risk`, `auto_quarantine`, profile routing tables). Peerhub correctly rejected hub.py's implementation (brittle, hardcoded, evidence-fabricating) but hasn't yet built the replacement orchestrator.

**The unifying concept**: a single `DispatchPolicy` object, resolved once per dispatch (or once per governance action), that encodes every decision axis — consultation depth, session management, profile selection, telemetry refresh, IPC transport, and health consequence — as a declarative policy record. The policy is:
1. Resolved from configuration (not hardcoded)
2. Snapshotted at action-start (immutable for the action's lifetime)
3. Interpreted by a thin orchestrator that calls existing domain services
4. Auditable (the resolved policy is persisted with the action's record)

This replaces hub.py's `protocol.json` + scattered `collab_rate` + `auto_quarantine` logic with **one concept, one configuration surface, one snapshot, one orchestrator**. The domain services remain unchanged.

---

## 1. The Unifying Primitive: DispatchPolicy

### 1.1 5-Whys: Why does peerhub need this?

1. **Why are 6 gaps open?** Each gap is a missing connection between an existing domain service and the dispatch/governance path.
2. **Why are they disconnected?** There's no orchestration layer that knows *when* to invoke each service.
3. **Why is there no orchestration layer?** hub.py's `protocol.json` served that role; peerhub rejected its implementation but hasn't replaced the concept.
4. **Why was it rejected?** Because hub.py hardcoded policy decisions (auto-quarantine, fixed quorum, static profile mapping) instead of making them configurable and evidence-based.
5. **Why does the replacement need to be unified?** Because the decisions interact — `collab_rate` affects whether a dispatch needs consensus, which affects whether Final Call applies, which affects timeout handling, which affects health consequences. Separate patches would recreate hub.py's fragmentation.

### 1.2 What DispatchPolicy IS

A `DispatchPolicy` is an immutable, resolved record that answers every question the dispatch/governance orchestrator needs:

```python
@dataclass(frozen=True)
class DispatchPolicy:
    """Resolved once, immutable for the action's lifetime."""

    # --- Consultation axis (replaces collab_rate) ---
    consultation_depth: ConsultationDepth
    # Enum: NONE, NOTIFY, REVIEW, QUORUM, UNANIMOUS

    # --- Consensus sub-policy (only meaningful when depth >= QUORUM) ---
    consensus: ConsensusPolicy | None
    # quorum_formula, final_call_rule, timeout, escalation_target

    # --- Session management ---
    session: SessionPolicy
    # reuse/auto/fresh, pressure_thresholds, max_observation_age

    # --- Profile/routing ---
    routing: RoutingPolicy
    # explicit_profile, capability_requirements, effort_hint, fallback_chain

    # --- Transport/IPC ---
    transport: TransportPolicy
    # max_inline_bytes, staging_config, cleanup_rule, query_file_path

    # --- Health consequences ---
    health_consequence: HealthConsequencePolicy
    # on_failure: REPORT_ONLY | REVIEW_REQUEST | AUTO_CIRCUIT_OPEN
    # recovery_authority: MANUAL | EVIDENCE_BASED | NONE

    # --- Telemetry ---
    telemetry: TelemetryPolicy
    # refresh_before_dispatch: bool, normalization_mode, headroom_surface

    # --- Provenance ---
    resolved_from: str          # config source that produced this policy
    resolved_at: int            # timestamp
    source_hash: str            # hash of the config at resolution time
```

### 1.3 What DispatchPolicy is NOT

- **Not a God object**: it's a resolved configuration record, not a service. It has no methods. It makes no decisions. It's read by the orchestrator.
- **Not hub.py's `protocol.json`**: `protocol.json` mixed policy with implementation. `DispatchPolicy` is pure data. The orchestrator interprets it.
- **Not a new persistence layer**: it's snapshotted as part of the existing dispatch request's state, using the existing `GovernanceBroker` CAS mechanism.

### 1.4 Generalization check

Could any sub-policy be a more general concept?

| Sub-policy | More general option? | Decision |
|---|---|---|
| `ConsultationDepth` | Could be a numeric scale (0-10 like collab_rate) | **No.** 5-whys: hub.py's 0-10 scale was never actually used at most levels. Real behavior clustered into 5 modes. The numeric scale created the illusion of fine-grained control that didn't exist. Enum is more honest. **However**: the enum values map bijectively to collab_rate ranges for backward compat: `NONE`=0, `NOTIFY`=1-3, `REVIEW`=4-5, `QUORUM`=6-8, `UNANIMOUS`=9-10. |
| `SessionPolicy` | Could be part of `RoutingPolicy` | **No.** Session rotation is orthogonal to profile selection. A "fresh" session policy applies regardless of which profile was selected. |
| `HealthConsequencePolicy` | Could be derived from `ConsultationDepth` | **Tempting but wrong.** A high-consultation-depth action (e.g., consensus vote) that fails should NOT auto-quarantine the voter. Health consequences depend on the *failure mode*, not the consultation depth. |
| `TelemetryPolicy` | Could be part of `HealthConsequencePolicy` | **No.** Telemetry refresh is a pre-dispatch concern; health consequences are post-dispatch. |

### 1.5 External packages considered

| Package | Considered for | Decision |
|---|---|---|
| `pydantic` | Policy schema validation | **Reject.** peerhub already has `dataclass(frozen=True)` + manual validation everywhere. Adding pydantic for one schema would be inconsistent. The existing pattern works. |
| `transitions` (pytransitions) | Consensus state machine | **Reject.** The consensus state machine is already implemented in `consensus.py` and works. Adding a generic FSM library would add a dependency for ~60 lines of state transition logic that's already correct and tested. |
| `tenacity` | Retry/backoff in dispatch | **Reject.** peerhub already has `RetryLoopStopReason`, `AttemptDispatchPlan`. The retry logic is dispatch-specific (lease-aware, health-aware), not generic exponential backoff. |
| `jsonschema` | Policy config validation | **Consider.** The policy config file (section 3) could benefit from schema validation. But peerhub's existing config validation (`config_validate.py`) uses manual checks, and a JSON schema would need to be kept in sync. **Defer** — revisit if the config surface grows beyond what manual validation handles cleanly. |

---

## 2. Architecture: How DispatchPolicy Connects the 6 Gaps

### 2.1 Architecture diagram

```
┌──────────────────────────────────────────────────────────────┐
│  CLI / Client                                                │
│  peerhub ask / broadcast / consensus propose / ...           │
└─────────────────────────┬────────────────────────────────────┘
                          │
                    ┌─────▼──────┐
                    │  PolicyResolver  │ ← reads dispatch-policy.toml
                    │  (resolves config │    + per-command defaults
                    │   into frozen     │    + CLI overrides
                    │   DispatchPolicy) │
                    └─────┬──────┘
                          │ frozen DispatchPolicy
                    ┌─────▼──────┐
                    │  DispatchOrchestrator  │
                    │  (thin coordinator,    │
                    │   calls domain services│
                    │   per policy fields)   │
                    └─────┬──────┘
                          │
          ┌───────────────┼───────────────────────┐
          │               │                       │
    ┌─────▼─────┐   ┌────▼─────┐          ┌──────▼──────┐
    │ Consensus  │   │ Session  │          │ Routing     │
    │ Service    │   │ Rotation │          │ Service     │
    │ (existing) │   │ Saga     │          │ (existing)  │
    │            │   │ (existing│          │             │
    │ + final_call│  │  unwired)│          │ + effort    │
    │   wiring   │   │          │          │   hint      │
    └────────────┘   └──────────┘          └─────────────┘
          │               │                       │
    ┌─────▼─────┐   ┌────▼─────┐          ┌──────▼──────┐
    │ Health    │   │Transport │          │ Telemetry   │
    │ Service   │   │ (prompt  │          │ Service     │
    │ (existing)│   │  staging │          │ (existing   │
    │           │   │  + IPC)  │          │  + fix)     │
    │ + quarantine  │          │          │             │
    │   from review │          │          │             │
    └────────────┘   └──────────┘          └─────────────┘
```

### 2.2 How each gap maps to the architecture

| Gap | DispatchPolicy field | Orchestrator action |
|---|---|---|
| 1. `collab_rate` | `consultation_depth` | Orchestrator checks depth before dispatch; if ≥ QUORUM, creates consensus round with `consensus.quorum_formula` from `policy.consensus` |
| 2. Final Call | `consensus.final_call_rule` | Orchestrator, after quorum is reached, checks rule: `ALWAYS`, `TIER_0_ONLY`, `DISSENT_ONLY`, `NEVER`. If applicable, transitions round to `final_call` phase and issues CLI command. |
| 3. Quarantine from review | `health_consequence` | Orchestrator, on ESCALATE decision, calls `health.open_manual_quarantine()` (not just `authorize_administrative_recovery()`). The missing connection. |
| 4. Session rotation | `session` | Orchestrator, before dispatch, calls `SessionRotationSaga.evaluate_and_claim()` with policy's `session.mode`, `session.pressure_thresholds`. |
| 5. Profile routing | `routing.effort_hint` | Orchestrator, if `effort_hint` is present and no explicit `--profile`, queries `CapabilityMatchingCoordinator` with the hint as a soft preference. No automatic dispatch — surfaces a recommendation. |
| 6. Telemetry | `telemetry` | Orchestrator, before dispatch, if `telemetry.refresh_before_dispatch`, triggers quota polling. Normalization fix is a separate bugfix (section 6.6). |
| IPC hygiene | `transport` | Orchestrator uses `transport.cleanup_rule` to determine when `remove_staged_prompt()` is called. Also exposes `--query-file` on CLI. |

---

## 3. Configuration Surface

### 3.1 File: `dispatch-policy.toml`

Located in the standard config resolution chain (workspace `.peerhub/` → global `~/.peerhub/` → built-in defaults). Uses TOML because peerhub already uses TOML for `ask-defaults.toml` and `model-defaults.toml`.

```toml
# dispatch-policy.toml — Governance/dispatch policy configuration
# Every value has a built-in default; this file overrides selectively.

[consultation]
# Default consultation depth for governance actions.
# Values: "none", "notify", "review", "quorum", "unanimous"
default_depth = "quorum"

# Per-action overrides (action name → depth)
[consultation.overrides]
"consensus.propose" = "unanimous"
"task.create" = "review"
"lesson.inject" = "notify"
# Actions not listed here use default_depth.

[consensus]
# Quorum formula: "majority" (>50%), "supermajority" (≥2/3), "unanimous", or "all_required"
quorum_formula = "all_required"

# Final Call rule: "always", "tier_0_only", "dissent_only", "never"
final_call_rule = "tier_0_only"

# Timeout in seconds before escalation
timeout_seconds = 1800

# Escalation target when timeout fires
escalation_target = "human-tier-0"

[session]
# Default session policy: "reuse", "auto", "fresh"
default_mode = "auto"

# Pressure thresholds for "auto" mode (percentage, 0-100)
soft_pressure_threshold = 75
hard_pressure_threshold = 90

# Maximum age of telemetry observation to trust (milliseconds)
max_observation_age_ms = 30000

[routing]
# Whether to use effort-based profile routing when no --profile is given
effort_routing_enabled = false

# Effort hint source: "none", "prompt_length", "declared"
# "prompt_length" uses character count as a rough proxy (not a quality signal)
# "declared" requires the caller to pass --effort-hint explicitly
effort_hint_source = "none"

[transport]
# Maximum inline prompt size (bytes) before staging to file
max_inline_bytes = 65536

# Staging directory (relative to workspace .peerhub/)
staging_dir = "prompt-staging"

# Cleanup rule: "on_completion" (after definite outcome),
#   "on_success_only" (keep on failure for debugging),
#   "sweep_only" (rely on startup sweep, never per-dispatch cleanup)
cleanup_rule = "on_completion"

# Maximum age for sweep cleanup (seconds)
sweep_max_age_seconds = 3600

[health_consequence]
# Default consequence for dispatch failures
# "report_only": create quarantine-review target, no automatic action
# "review_then_quarantine": on ESCALATE, actually quarantine the peer
# "evidence_circuit_only": only auto-open circuit on typed evidence (existing behavior)
default_consequence = "review_then_quarantine"

[telemetry]
# Whether to refresh quota telemetry before dispatch
refresh_before_dispatch = false

# Headroom surface: "none", "basic" (used/remaining), "full" (+ fail rate, + 24h trend)
headroom_surface = "basic"
```

### 3.2 Design decision: TOML not JSON

5-whys:
1. Why not JSON? Hub.py used `protocol.json`. But JSON has no comments, and this config *needs* comments — every field has non-obvious semantics.
2. Why not YAML? peerhub has no YAML dependency. Adding one for a config file is unjustified.
3. Why TOML? peerhub already uses TOML (`ask-defaults.toml`, `model-defaults.toml`). TOML supports comments, is well-typed, and `tomllib` is stdlib since Python 3.11 (peerhub's minimum).

### 3.3 Design decision: Flat file not per-action config

5-whys: Could each action (ask, consensus propose, task create) have its own config file?
1. **No.** The whole point is ONE policy surface. Per-action files recreate hub.py's scattered `protocol.json` + inline hardcoding.
2. **But**: per-action *overrides within the single file* (section `[consultation.overrides]`) are necessary because different governance actions genuinely have different requirements.
3. **The override is one level deep**: action-name → consultation depth. Not action-name → full policy subtree. Deeper nesting would make the config unusable.

---

## 4. Per-Gap Detailed Design

### 4.1 Gap 1: ConsultationDepth (replaces collab_rate)

#### 4.1.1 The enum

```python
class ConsultationDepth(str, Enum):
    NONE = "none"            # No consensus required. Direct dispatch.
    NOTIFY = "notify"        # Post-hoc notification to peers. No blocking.
    REVIEW = "review"        # One peer must ACK before dispatch. Non-blocking if ACK times out.
    QUORUM = "quorum"        # Quorum of peers must agree. Blocking.
    UNANIMOUS = "unanimous"  # All required peers must explicitly agree. Blocking.
```

#### 4.1.2 Mapping to hub.py's collab_rate

| collab_rate | ConsultationDepth | Rationale |
|---|---|---|
| 0 | NONE | No consensus needed |
| 1-3 | NOTIFY | Informal notification, no blocking |
| 4-5 | REVIEW | One-peer review, non-blocking timeout |
| 6-8 | QUORUM | Majority/supermajority, configurable formula |
| 9-10 | UNANIMOUS | All required voters must explicitly agree |

This mapping is a strict superset: every `collab_rate` value maps to exactly one `ConsultationDepth`, and `ConsultationDepth` is implementable without the numeric scale's false precision.

#### 4.1.3 How it connects to the existing ConsensusService

The orchestrator does NOT modify `ConsensusService`. Instead:

- For `NONE`: skip consensus entirely.
- For `NOTIFY`: create a governance broker target with type `notification` (not a consensus round). No votes, no quorum. Peers are notified asynchronously.
- For `REVIEW`: create a consensus round with `quorum_required=1`, `final_call_rule=NEVER`. If the single reviewer times out, the action proceeds (non-blocking).
- For `QUORUM`: create a consensus round with the configured `quorum_formula`.
- For `UNANIMOUS`: create a consensus round with `quorum_required=len(required_participants)`.

The only change to `ConsensusService._quorum_required()`: make it actually use its `risk` parameter by looking up the quorum formula from the resolved `ConsensusPolicy`:

```python
# Current (ignores risk):
return max(2, participant_count)

# Proposed (uses policy):
def _quorum_required(self, participant_count: int, risk: str, *, formula: str = "all_required") -> int:
    if participant_count < 1:
        raise ValueError("at least one required participant is needed")
    if formula == "majority":
        return max(2, (participant_count // 2) + 1)
    if formula == "supermajority":
        return max(2, (participant_count * 2 + 2) // 3)
    # "all_required" or "unanimous"
    return max(2, participant_count)
```

#### 4.1.4 MECE exception cases

| Case | Behavior | Rationale |
|---|---|---|
| Zero required participants | `ValueError` at policy resolution time | Policy is invalid. Fail fast. |
| One required participant who is also the proposer | Round created but `non_proposer_required` check prevents self-ratification. Escalation fires at timeout. | Hub.py had this rule; it's correct (self-approval is not consensus). |
| Required voter unreachable at round start | Excluded from snapshot denominator. If this drops required count to < `quorum_required`, round is `abandoned` with reason "insufficient eligible voters". | Health filtering at snapshot time is already implemented. |
| Required voter unreachable AFTER voting starts | Their uncast vote blocks at `UNANIMOUS` depth. At `QUORUM` depth, quorum can still be reached without them. At timeout, escalation fires. | No denominator change mid-round (existing design, correct). |
| Risk classification ambiguous | Default to the higher `ConsultationDepth`. Configurable via `[consultation.overrides]` per action. | Fail-safe: over-consult rather than under-consult. |
| Concurrent proposals for same scope | Each proposal is an independent round with its own snapshot. The orchestrator does NOT prevent concurrent proposals — that's the proposer's problem. | Hub.py also allowed concurrent proposals. Locking would add complexity for a rare case. |
| Voter wants to retract a cast vote | **Not supported.** A correction is a new superseding event (already in consensus.py's design doc). The original vote remains in the audit trail. | Votes are immutable facts. This is already the correct design. |
| Network partition mid-vote | Timeout fires. Escalation to `escalation_target`. No automatic resolution of a partitioned round. | Correct: silence is not consent. |
| Configuration changes during an active round | **No effect on active round.** Policy is snapshotted at round creation. | Already designed this way in GAP-2. |

### 4.2 Gap 2: Final Call

#### 4.2.1 The mechanism

Final Call is already implemented in `ConsensusService.final_call_ack()` (line 154 of `consensus.py`). The gap is that nothing calls it. The fix is in the orchestrator:

```
After quorum_reached:
    if policy.consensus.final_call_rule == ALWAYS:
        transition to final_call phase
    elif policy.consensus.final_call_rule == TIER_0_ONLY:
        if risk == "tier-0" or risk == "high":
            transition to final_call phase
        else:
            resolve immediately
    elif policy.consensus.final_call_rule == DISSENT_ONLY:
        if any vote is "disagree" or "need_more_info":
            transition to final_call phase
        else:
            resolve immediately
    elif policy.consensus.final_call_rule == NEVER:
        resolve immediately
```

#### 4.2.2 CLI surface

New command: `peerhub consensus final-call-ack --round-id ROUND --actor ACTOR [--ack|--nack] [--credential-id CRED]`

This calls the existing `ConsensusService.final_call_ack()` method. No new domain logic needed.

#### 4.2.3 MECE exception cases

| Case | Behavior | Rationale |
|---|---|---|
| Final Call required but no eligible acknowledgers | Escalation fires immediately. | Cannot complete a required step with no one to do it. |
| Acknowledger ACKs then another NACKs | Round stays in `final_call` until all have responded. If any NACK, round is NOT auto-resolved — it stays in `final_call` for the proposer to address the concern or abandon. | NACKs are meaningful objections, not just "no" votes. |
| All acknowledgers ACK | Round resolves as `approved`. Effect intent fires. | Existing behavior in `final_call_ack()`. |
| Final Call timeout | Escalation fires (using the round's `timeout_seconds`). | Same timeout mechanism as the voting phase. |
| `final_call_rule` is `NEVER` but arbiter override demands Final Call | Arbiter override is a different mechanism (`ArbiterReviewCommand`). It can force a re-vote or escalation, but it doesn't change the `final_call_rule` — it operates *above* the policy. | Separation of concerns: policy is configuration, arbiter is exception handling. |
| Quorum reached but one vote was `need_more_info` and `final_call_rule` is `DISSENT_ONLY` | `need_more_info` counts as "unresolved concern" → Final Call required. | `need_more_info` is not agreement; it shouldn't silently auto-resolve. |

### 4.3 Gap 3: Quarantine from Review

#### 4.3.1 The missing connection

Current: `QuarantineReviewCoordinator.resolve_quarantine_review()` on ESCALATE calls `health.authorize_administrative_recovery()`. This is backwards — ESCALATE should quarantine, not recover.

The fix is straightforward:

```python
# In resolve_quarantine_review(), ESCALATE branch:
if normalized_decision == "ESCALATE":
    # Current (wrong): self._health.authorize_administrative_recovery(...)
    # Proposed (correct):
    self._health.open_manual_quarantine(
        scope=PolicyScope.PROFILE,
        subject=authenticated,
        authority_class=QuarantineAuthorityClass.REVIEW_ESCALATION,
        reason=normalized_reason,
        actor_id=authenticated.principal_id,
        requested_at=now,
    )
```

#### 4.3.2 Why this is NOT about DispatchPolicy

This gap is not a policy question — it's a wiring bug. The quarantine-review system correctly identifies that a peer should be quarantined. The ESCALATE action should quarantine the peer. It currently recovers the peer instead (calling `authorize_administrative_recovery`). This is a one-line fix in the orchestrator wiring, not a policy configuration.

**However**, the `health_consequence` policy field controls *whether* a dispatch failure creates a quarantine-review target at all (vs. just a report-error record). The quarantine-review → actual quarantine path is infrastructure that should always work correctly, regardless of policy.

#### 4.3.3 MECE exception cases

| Case | Behavior | Rationale |
|---|---|---|
| ESCALATE for a peer that's already quarantined | Idempotent — `open_manual_quarantine()` on an already-quarantined peer is a no-op (circuit is already open). | Existing health service behavior. |
| ESCALATE for a peer with no `peer_key` | `InvalidMutationError` (already handled at line 134). | Defensive validation. |
| ESCALATE for a deregistered peer | `RecordNotFoundError` from `peer_registry.get_node()`. | Cannot quarantine what doesn't exist. |
| DISMISS a review that should have been escalated | Review is dismissed. No quarantine. If the problem recurs, a new review target is created. | DISMISS is an explicit human decision. No second-guessing. |
| Multiple concurrent reviews for the same peer | Each is independent. Multiple ESCALATEs are idempotent (quarantine is already open after the first). | No deduplication needed — idempotency handles it. |
| ESCALATE + immediate RECOVER | Valid sequence. An operator escalates (quarantining the peer) then immediately recovers after a fix. Both operations go through proper authorization. | This is a legitimate workflow. |

### 4.4 Gap 4: Session Rotation

#### 4.4.1 Wiring SessionRotationSaga into the dispatch path

`SessionRotationSaga.evaluate_and_claim()` exists, is tested, and works. The gap is that `peerhub ask` never calls it.

The orchestrator calls it like this:

```
Before dispatch:
    saga_result = session_saga.evaluate_and_claim(
        policy=policy.session.default_mode,  # "reuse" | "auto" | "fresh"
        workspace_scope_id=...,
        instance_id=...,
        profile_id=...,
        conversation_scope=...,
        current_generation_id=...,
        rotation_safe=rotation_safe_signal,
        max_observation_age_ms=policy.session.max_observation_age_ms,
    )

    match saga_result.decision:
        case PROCEED_WITH_REUSE:
            # use existing session
        case CHECKPOINT_REQUIRED:
            # issue checkpoint, then proceed
        case ROTATION_PENDING_PROCEED:
            # warn but proceed (pressure detected, no safe signal)
        case ROTATION_CLAIMED:
            # new session claimed; start dispatch with new conversation_id
        case ROTATION_IN_PROGRESS_RETRY:
            # back off and retry
```

#### 4.4.2 CLI surface

New argument on `peerhub ask`: `--session-policy reuse|auto|fresh`

Default: whatever `dispatch-policy.toml`'s `[session].default_mode` says (which defaults to `"auto"`).

#### 4.4.3 MECE exception cases

| Case | Behavior | Rationale |
|---|---|---|
| No telemetry data available (first dispatch) | `evaluate_and_claim()` already handles this: no projection → "No Pressure / Absent" → PROCEED_WITH_REUSE. | Existing saga logic. |
| Stale telemetry (older than `max_observation_age_ms`) | Treated as "Absent" → PROCEED_WITH_REUSE. | Existing saga logic. Not trusting stale data is correct. |
| Concurrent rotation claims | `ROTATION_IN_PROGRESS_RETRY` → caller backs off. | CAS-based claim prevents double-rotation. |
| `fresh` policy but no safe signal | Force rotation anyway (`fresh` ignores safe signal). | `fresh` is explicit: "I want a new session regardless." |
| `auto` policy, pressure reached, safe signal = false | `ROTATION_PENDING_PROCEED` — warn but proceed. | Can't safely rotate, but can't ignore pressure. Warn the human. |
| Rotation claimed but dispatch fails | Session was rotated; failure is handled by the retry loop. Next retry uses the new session. | Session rotation is a pre-dispatch decision; dispatch failure is post-dispatch. Independent concerns. |

### 4.5 Gap 5: Profile Routing with Effort Hint

#### 4.5.1 Design decision: NO automatic profile routing

5-whys:
1. Why didn't peerhub wire effort-based routing? Because the backlog explicitly says "no effort score, no effort gate" pending "a real effort-quality data source."
2. Why is there no data source? Because prompt complexity is hard to measure. Character count is a terrible proxy. Model-reported complexity doesn't exist for any vendor CLI.
3. Should this design invent one? **No.** DIR-004 (Measured-Only Claims) explicitly prohibits this. An effort router without a real data source would be fabricating evidence.
4. What CAN this design do? Surface the routing recommendation as advisory, with an explicit opt-in flag.

#### 4.5.2 The design

- `routing.effort_routing_enabled`: defaults to `false`. When `true` AND `routing.effort_hint_source` is `"declared"`, the orchestrator calls `CapabilityMatchingCoordinator` with the declared hint.
- `--effort-hint low|medium|high` on `peerhub ask`: only accepted when `effort_routing_enabled=true` and `effort_hint_source="declared"`. Otherwise silently ignored (not an error — the flag is a suggestion, not a command).
- The routing result is **advisory**: printed to stderr as a recommendation, not applied automatically. The user or calling system can choose to follow it.
- `"prompt_length"` source: if `effort_hint_source="prompt_length"`, the orchestrator maps character count to a rough hint (e.g., <1000 → low, 1000-10000 → medium, >10000 → high). This is explicitly labeled `[estimated]` in output, per DIR-004.

#### 4.5.3 Why NOT automatic

Automatic profile selection based on a fabricated effort metric would violate DIR-004 and create a false sense of intelligence. The routing subsystem is real and useful — it just needs a real input. The design leaves the door open for a real effort-quality data source (model self-report, historical success rates, etc.) without pretending one exists today.

#### 4.5.4 MECE exception cases

| Case | Behavior | Rationale |
|---|---|---|
| `effort_routing_enabled=false` (default) | No routing advisory. Direct dispatch to `--profile` or default. | Opt-in only. |
| `effort_hint_source="declared"` but no `--effort-hint` | No routing advisory. Proceed with default profile. | Missing hint is not an error. |
| `effort_hint_source="prompt_length"` | Advisory with `[estimated]` tag. | DIR-004 compliance: estimates are labeled as such. |
| Routing recommends a profile that's quarantined | Advisory shows the recommendation AND the quarantine status. Human decides. | Advisory is information, not a command. |
| All eligible profiles quarantined | Routing returns no candidates. Dispatch proceeds with explicit `--profile` if given; otherwise fails with a clear error. | Cannot route to no one. |

### 4.6 Gap 6: Telemetry Normalization Fix + Headroom Surface

#### 4.6.1 The normalization bug

The bug (confirmed in `quota_polling.py:496-503`): `_codex_rate_limits` returns a response where the rate limit data is nested under `rateLimits` (confirmed by the code's own comment: "correct rate-limit data was present, just nested"). The code now correctly reads `rate_limits_envelope.get("rateLimits")`.

**Wait** — re-reading the code, the bug described in the task brief (`_codex_rate_limits returns rateLimitsByLimitId but the normalization path only consumes rl.get("rateLimits")`) appears to be partially fixed already (lines 496-503 have a comment about this exact issue being found and fixed). Let me be precise about what remains:

The task brief references `snapshot.py:255/:1134/:682`. But `snapshot.py` doesn't exist — the task brief may be referencing an older state. The current telemetry code is in `quota_polling.py`. The quota-efficiency ratified design doc says the bug is in the normalization path. The current code at `quota_polling.py:500` does `rate_limits_raw = rate_limits_envelope.get("rateLimits")`, which reads the correct key.

**I genuinely cannot verify whether this bug is still open from what I've read.** The code has a comment suggesting it was found and fixed. I will flag this as an uncertainty point.

#### 4.6.2 Headroom surface

The `telemetry.headroom_surface` config controls what `peerhub diag` displays:

- `"none"`: existing behavior (raw quota numbers)
- `"basic"`: used/remaining per window per peer
- `"full"`: + 24h fail rate, + trend direction (increasing/decreasing/stable), matching P:'s `diag.py --headroom`

The `"full"` surface requires:
- Fail rate tracking: count dispatch failures per peer per 24h window. This is a new telemetry aggregate stored in the existing SQLite telemetry tables.
- Trend: compare current window's used% to previous window's used%. Simple delta, no forecasting.

#### 4.6.3 MECE exception cases

| Case | Behavior | Rationale |
|---|---|---|
| No telemetry data (first run) | Display "No telemetry data available" for each peer. No estimates. | DIR-004. |
| Stale telemetry (older than configured max age) | Display with `[stale]` tag and age. | Information, not suppression. |
| Vendor API unreachable | Display `[error]` with last-known values if any. | Graceful degradation. |
| Rate limit response format changes | `ERROR` evidence state → `[error]` display. No crash. | Existing error handling in `_fail_closed()`. |
| Headroom surface = "full" but fail-rate tracking not yet populated | Show "fail rate: no data" rather than "fail rate: 0%". | Zero failures and no data are different. DIR-004. |

### 4.7 IPC Hygiene: Query-File + Prompt Cleanup

#### 4.7.1 CLI surface: `--query-file`

New argument on `peerhub ask` and `peerhub broadcast`:

```
peerhub ask ag --query-file /path/to/prompt.md
```

Reads the file's contents and uses them as the prompt. Mutually exclusive with inline prompt text. The file is read once, at CLI parse time, and its contents become the `DirectAskRequest.prompt`. The file is NOT the staged prompt — staging happens separately if the prompt exceeds `transport.max_inline_bytes`.

This matches hub.py's `--query-file` pattern exactly.

#### 4.7.2 Prompt cleanup lifecycle

The `transport.cleanup_rule` determines when `remove_staged_prompt()` is called:

| Rule | When cleanup happens | Use case |
|---|---|---|
| `on_completion` | After any definite outcome (success, definite failure, cancellation) | Default. Clean workspace. |
| `on_success_only` | After success only. Failures leave the staged file for debugging. | Debugging. |
| `sweep_only` | Never per-dispatch. Rely on `sweep_stale_staged_prompts()` at next dispatch start. | Minimal per-dispatch overhead. |

For all rules: `ExecutionCertainty.MAY_HAVE_STARTED` / `START_UNCERTAIN` outcomes NEVER trigger cleanup (the supervised process might still be reading the file). This is already the existing design in `prompt_transport.py:164`.

#### 4.7.3 MECE exception cases

| Case | Behavior | Rationale |
|---|---|---|
| `--query-file` with nonexistent file | CLI error, exit 2. Clear error message. | Fail fast. |
| `--query-file` with empty file | CLI error, exit 2. "Query file is empty." | Empty prompt is not useful. |
| `--query-file` AND inline prompt | CLI error, exit 2. "Cannot specify both --query-file and inline prompt." | Mutually exclusive. argparse enforces this. |
| Staged prompt file deleted before peer reads it | Peer gets a broken reference. Dispatch outcome: definite failure. Retry loop creates a new staged file. | Cannot prevent external deletion. Retry handles it. |
| Staged prompt file from uncertain outcome | Left on disk until sweep. Sweep removes files older than `sweep_max_age_seconds`. | Existing design in `prompt_transport.py:173-202`. |
| Concurrent dispatches staging to same directory | Safe — filenames are SHA-256 of (request_id, payload_digest), so no collisions between different dispatches. Same dispatch retrying same payload reuses the file (idempotent). | Existing design in `prompt_transport.py:92-97`. |
| Staged file contains sensitive data | **Known issue (from dotdir consolidation doc).** `transport.cleanup_rule = "on_completion"` is the default, which cleans up promptly. For maximum security, users should not use `sweep_only`. **Design gap I'm flagging**: there's no option to encrypt staged files at rest. This may be needed for production use with sensitive prompts. |

---

## 5. PolicyResolver: How Configuration Becomes Policy

### 5.1 Resolution order

1. Built-in defaults (hardcoded in `PolicyResolver`, matching the defaults in section 3.1)
2. Global config (`~/.peerhub/dispatch-policy.toml`)
3. Workspace config (`.peerhub/dispatch-policy.toml`)
4. CLI overrides (`--session-policy`, `--consultation-depth`, `--effort-hint`, `--query-file`)
5. Per-command defaults (the orchestrator knows that `consensus propose` at `UNANIMOUS` depth, regardless of config — some commands have non-overridable minimums)

Each layer overrides the previous. The result is frozen into a `DispatchPolicy` dataclass.

### 5.2 Per-command minimums

Some commands have a minimum `ConsultationDepth` that configuration cannot lower:

| Command | Minimum depth | Rationale |
|---|---|---|
| `consensus propose` | QUORUM | A proposal is inherently a consensus action |
| `consensus vote` | NONE | Voting IS the consultation |
| `task create` | NOTIFY | Task creation should at least notify peers |
| `lesson inject` | NONE | Lessons are informational |
| `ask` | NONE | Dispatch doesn't require consensus by default |
| `broadcast` | NONE | Fan-out doesn't require consensus |

### 5.3 Validation

`PolicyResolver` validates the resolved policy before freezing:

- `consultation_depth` is a valid enum value
- If `consultation_depth >= QUORUM`, `consensus` sub-policy must be present
- `session.soft_pressure_threshold < session.hard_pressure_threshold`
- `transport.max_inline_bytes > 0`
- `transport.staging_dir` passes `resolve_staging_dir()` validation (relative, no traversal)

Validation errors are `ConfigurationError` (exit code 2).

---

## 6. What This Design Does NOT Change

Explicitly listing what is NOT in scope, to prevent scope creep:

1. **GovernanceBroker**: unchanged. It's the persistence layer. Policy doesn't touch it.
2. **ConsensusService state machine**: unchanged (except making `_quorum_required` use its `risk` parameter and accepting a `formula` argument).
3. **HealthService**: unchanged. The quarantine-review fix is a wiring change in `QuarantineReviewCoordinator`, not a health service change.
4. **SessionRotationSaga**: unchanged. Already works. Just needs to be called.
5. **Routing/capability matching**: unchanged. Already works. Just needs an input (effort hint).
6. **Existing CLI commands**: unchanged. New arguments are additive.
7. **ApplicationAPI.submit() gateway**: unchanged. The orchestrator works above the gateway, not inside it.
8. **D-CTX credential verification**: unchanged. Orthogonal to policy.
9. **DutyLeaseCoordinator / LeadershipService**: unchanged. Orthogonal to dispatch policy.

---

## 7. MECE Test Case Specification

### 7.1 PolicyResolver tests

| Test ID | Description | Expected behavior |
|---|---|---|
| PR-01 | No config files exist | Built-in defaults used |
| PR-02 | Global config only | Global overrides built-in |
| PR-03 | Workspace config overrides global | Workspace wins |
| PR-04 | CLI override wins over config | CLI wins |
| PR-05 | Per-command minimum enforced | `consensus propose` with `consultation_depth=none` in config → resolved as QUORUM |
| PR-06 | Invalid config value | `ConfigurationError` with clear message |
| PR-07 | Malformed TOML | `ConfigurationError` with parse error |
| PR-08 | Config changes during running dispatch | No effect (policy already snapshotted) |
| PR-09 | `consultation.overrides` for unknown action | Ignored (not an error) |
| PR-10 | `consensus` sub-policy missing when depth >= QUORUM | `ConfigurationError` |
| PR-11 | Pressure thresholds inverted (soft > hard) | `ConfigurationError` |
| PR-12 | `staging_dir` with `..` traversal | `ConfigurationError` (existing validation) |

### 7.2 DispatchOrchestrator tests

| Test ID | Description | Expected behavior |
|---|---|---|
| DO-01 | `consultation_depth=NONE` → direct dispatch | No consensus round created |
| DO-02 | `consultation_depth=NOTIFY` → notification created | Governance target created, no votes |
| DO-03 | `consultation_depth=REVIEW` → single-reviewer round | Round with quorum=1, non-blocking timeout |
| DO-04 | `consultation_depth=QUORUM` → multi-voter round | Round with configured quorum formula |
| DO-05 | `consultation_depth=UNANIMOUS` → all-must-agree | Round with quorum=N |
| DO-06 | Session policy `auto` + no pressure | PROCEED_WITH_REUSE |
| DO-07 | Session policy `auto` + pressure + safe | ROTATION_CLAIMED |
| DO-08 | Session policy `fresh` | Always ROTATION_CLAIMED |
| DO-09 | Session policy `reuse` + pressure | CHECKPOINT_REQUIRED |
| DO-10 | `--query-file` staging | File read, prompt staged if > max_inline_bytes |
| DO-11 | Cleanup on success | Staged file removed |
| DO-12 | Cleanup on failure with `on_success_only` | Staged file preserved |
| DO-13 | No cleanup on uncertain outcome | Staged file preserved regardless of cleanup_rule |

### 7.3 ConsensusService extension tests

| Test ID | Description | Expected behavior |
|---|---|---|
| CS-01 | `quorum_formula="majority"` with 3 voters | quorum_required=2 |
| CS-02 | `quorum_formula="majority"` with 4 voters | quorum_required=3 |
| CS-03 | `quorum_formula="supermajority"` with 3 voters | quorum_required=2 |
| CS-04 | `quorum_formula="supermajority"` with 6 voters | quorum_required=4 |
| CS-05 | `quorum_formula="all_required"` with 3 voters | quorum_required=3 (but min 2) |
| CS-06 | `quorum_formula="all_required"` with 1 voter | quorum_required=2 (min floor) |

### 7.4 Final Call wiring tests

| Test ID | Description | Expected behavior |
|---|---|---|
| FC-01 | `final_call_rule=ALWAYS` → quorum reached → final_call phase | Phase transition to final_call |
| FC-02 | `final_call_rule=NEVER` → quorum reached → resolved | Direct resolution |
| FC-03 | `final_call_rule=TIER_0_ONLY` + risk=tier-0 → final_call | Phase transition |
| FC-04 | `final_call_rule=TIER_0_ONLY` + risk=low → resolved | Direct resolution |
| FC-05 | `final_call_rule=DISSENT_ONLY` + all agree → resolved | Direct resolution |
| FC-06 | `final_call_rule=DISSENT_ONLY` + one disagree → final_call | Phase transition |
| FC-07 | `final_call_rule=DISSENT_ONLY` + one need_more_info → final_call | Phase transition |
| FC-08 | Final Call timeout → escalation | Existing timeout mechanism |
| FC-09 | All ACK in final_call → resolved approved | Existing `final_call_ack()` logic |
| FC-10 | One NACK in final_call → stays in final_call | Proposer must address |

### 7.5 Quarantine-review wiring tests

| Test ID | Description | Expected behavior |
|---|---|---|
| QR-01 | ESCALATE → `open_manual_quarantine()` called | Peer quarantined |
| QR-02 | ESCALATE for already-quarantined peer | Idempotent success |
| QR-03 | DISMISS → no quarantine action | Review closed, peer unchanged |
| QR-04 | ESCALATE for peer with no peer_key | `InvalidMutationError` |
| QR-05 | ESCALATE for deregistered peer | `RecordNotFoundError` |

### 7.6 Session rotation wiring tests

| Test ID | Description | Expected behavior |
|---|---|---|
| SR-01 | `ask --session-policy auto` invokes saga | `evaluate_and_claim()` called |
| SR-02 | `ask --session-policy fresh` forces rotation | `ROTATION_CLAIMED` or `ROTATION_IN_PROGRESS_RETRY` |
| SR-03 | `ask` with no `--session-policy` uses config default | Config's `session.default_mode` used |
| SR-04 | `ROTATION_IN_PROGRESS_RETRY` → backoff and retry | Dispatch retried after backoff |

### 7.7 Effort routing tests

| Test ID | Description | Expected behavior |
|---|---|---|
| ER-01 | `effort_routing_enabled=false` → no advisory | No routing output |
| ER-02 | `effort_routing_enabled=true` + `effort_hint_source=declared` + `--effort-hint high` | Advisory printed to stderr |
| ER-03 | `effort_routing_enabled=true` + `effort_hint_source=declared` + no `--effort-hint` | No advisory (missing hint is not error) |
| ER-04 | `effort_routing_enabled=true` + `effort_hint_source=prompt_length` | Advisory with `[estimated]` tag |
| ER-05 | Advisory recommends quarantined profile | Advisory shows quarantine status |

### 7.8 Telemetry tests

| Test ID | Description | Expected behavior |
|---|---|---|
| TL-01 | `refresh_before_dispatch=true` → quota poll before ask | Quota refresh called |
| TL-02 | `headroom_surface="full"` → fail rate displayed | 24h fail rate in diag output |
| TL-03 | No telemetry data → "No data" (not "0%") | DIR-004 compliance |
| TL-04 | Stale data → `[stale]` tag | Age displayed |

### 7.9 Transport/IPC tests

| Test ID | Description | Expected behavior |
|---|---|---|
| TP-01 | `--query-file` reads file contents | Prompt = file contents |
| TP-02 | `--query-file` + inline prompt | argparse error, exit 2 |
| TP-03 | `--query-file` nonexistent file | Clear error, exit 2 |
| TP-04 | `--query-file` empty file | Clear error, exit 2 |
| TP-05 | Prompt > `max_inline_bytes` → staged | `stage_prompt()` called |
| TP-06 | Staged prompt cleanup on completion | `remove_staged_prompt()` called |
| TP-07 | No cleanup on uncertain outcome | File preserved |
| TP-08 | Sweep at dispatch start | `sweep_stale_staged_prompts()` called |

---

## 8. Implementation Sequence (Recommended)

If this design is ratified, the implementation order should be:

1. **Quarantine-review wiring fix** (Gap 3) — smallest change, biggest correctness impact. One function call change.
2. **`--query-file` CLI argument** (IPC hygiene) — simple, no new architecture. Just argparse + file read.
3. **`dispatch-policy.toml` schema + `PolicyResolver`** — the foundation. All subsequent work depends on this.
4. **`--session-policy` + SessionRotationSaga wiring** (Gap 4) — requires `PolicyResolver` for defaults.
5. **ConsultationDepth + ConsensusService quorum fix** (Gap 1) — requires `PolicyResolver`.
6. **Final Call wiring** (Gap 2) — requires ConsultationDepth to be wired first.
7. **Telemetry headroom surface** (Gap 6) — can be done in parallel with 5-6.
8. **Effort routing advisory** (Gap 5) — lowest priority, explicitly deferred pending real data source.

---

## 9. Points of Genuine Uncertainty

These are the areas where I see multiple defensible options and most want a second model's independent view:

### 9.1 Should ConsultationDepth be an enum or a numeric scale?

I chose an enum (5 values) over hub.py's 0-10 numeric scale. My reasoning: the numeric scale's intermediate values (1-3, 4-5, 6-8) were never meaningfully distinct in hub.py's actual behavior. The enum is more honest.

**But**: an enum makes it harder to add new consultation levels later. A numeric scale with named thresholds (like HTTP status codes) might be more extensible. I'm genuinely unsure.

### 9.2 Should the orchestrator be a new class or integrated into ApplicationAPI.submit()?

I designed it as a separate `DispatchOrchestrator` class that wraps `ApplicationAPI.submit()`. Alternative: integrate the policy logic directly into `ApplicationAPI.submit()`.

**For separate**: cleaner separation, easier to test, doesn't bloat the already-large `ApplicationAPI`.
**For integrated**: fewer moving parts, no additional indirection, the gateway is already the natural chokepoint.

I lean toward separate, but could be convinced otherwise.

### 9.3 Is the telemetry normalization bug (Gap 6) still open?

The code at `quota_polling.py:496-503` has a comment that appears to describe and fix the exact bug from the task brief. But the task brief says it's unresolved. I cannot verify without running the actual telemetry polling against a live Codex API. **I am explicitly flagging this as "cannot verify from source reading alone" per DIR-004.**

---

## 10. Hub.py Superset Verification

Every capability of hub.py's governance/dispatch system must be achievable in this design:

| Hub.py capability | This design | How |
|---|---|---|
| `collab_rate` 0-10 | ConsultationDepth enum | Bijective mapping (section 4.1.2) |
| `protocol.json` policy config | `dispatch-policy.toml` | Strictly more expressive (comments, typed, layered) |
| Auto-quarantine on repeated error | `health_consequence.default_consequence = "review_then_quarantine"` | Evidence-based, not blind threshold |
| `--query-file` IPC | `--query-file` on ask/broadcast | Same semantics |
| Session rotation | `--session-policy` + SessionRotationSaga | Same semantics, more modes |
| Profile routing by effort | Advisory effort routing | Advisory only (honest about data source quality) |
| `diag --headroom` | `telemetry.headroom_surface = "full"` | Same information + fail rate + trend |
| Final Call post-quorum | `consensus.final_call_rule` | Configurable (hub.py hardcoded it) |
| Consensus timeout + escalation | `consensus.timeout_seconds` + `consensus.escalation_target` | Configurable (hub.py hardcoded 30min) |
| Voter health filtering | Already shipped | No change needed |
| Non-proposer requirement | Already shipped | No change needed |

**Strict superset confirmed**: every hub.py capability maps to a configurable feature in this design. Several hub.py hardcoded values become configurable.

---

## Appendix A: Glossary

- **ConsultationDepth**: How much peer agreement is required before an action proceeds.
- **DispatchPolicy**: The resolved, immutable policy record for a single dispatch/governance action.
- **DispatchOrchestrator**: The thin coordinator that reads a `DispatchPolicy` and calls domain services accordingly.
- **PolicyResolver**: Reads configuration from TOML files and CLI arguments, validates, and produces a frozen `DispatchPolicy`.
- **Final Call**: A post-quorum ACK step where all eligible participants must explicitly confirm the result before it becomes effective.
- **Quarantine-review**: A governance target requesting human review of whether a peer should be quarantined.
- **SessionRotationSaga**: Existing (unwired) component that evaluates session pressure and claims a new session when appropriate.
