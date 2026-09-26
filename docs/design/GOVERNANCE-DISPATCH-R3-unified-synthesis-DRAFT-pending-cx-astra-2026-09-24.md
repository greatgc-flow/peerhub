# GOVERNANCE-DISPATCH-R3-unified-synthesis-DRAFT-pending-cx-astra-2026-09-24

**Status**: DRAFT (Pending cx.astra Ratification)

This document is the unified synthesis of the peerhub governance/dispatch redesign. It consolidates the proposals from Round 1 (`ag.opus`, `cx.astra`) and the cross-reviews from Round 2 and 2b.

## 1. Changelog & Provenance

- **From ag.opus Round 1 (Unchanged)**: The `DispatchPolicy` dataclass, the `DispatchOrchestrator` thin coordinator, the `dispatch-policy.toml` config format, the 5-value `ConsultationDepth` Enum + bijective `collab_rate` mapping, advisory-only effort routing, and the `PolicyResolver` resolution order.
- **From cx.astra Round 1 (Grafted via Round 2b)**: Final Call ACK retraction state machine, execution certainty / crash-recovery fencing (NOT_STARTED to COMPLETED), explicit opt-in privacy default for staged prompts (`retention_mode`), and epoch/scope-gated restriction semantics for `HealthService`.
- **Dropped (Scope Creep)**: cx.astra's D13 full-legacy-action ledger and its 20+ policy-namespace model were rejected to preserve radical simplicity.

---

## 2. Final Architecture

The architecture uses a thin coordinator (`DispatchOrchestrator`) that reads a frozen, immutable configuration record (`DispatchPolicy`) resolved from `dispatch-policy.toml`, CLI overrides, and per-command defaults.

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
    # max_inline_bytes, staging_config, cleanup_rule, query_file_path, retention_mode

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


### 2.2 Execution Certainty and Crash-Recovery Fencing (Grafted)
The orchestrator maintains rigorous state boundaries for attempt execution: `NOT_STARTED`, `MAY_HAVE_STARTED`, `STARTED`, and `COMPLETED`. 
- **Cancellation**: Cancellation after a claim records the true observed outcome. It never fabricates a "cancelled" state over a possibly completed effect, preventing unsafe non-idempotent replays.

### 2.3 Privacy vs. Crash-Replay (Grafted)
`transport.retention_mode` defaults to `ephemeral`. An ephemeral prompt is inherently disposable and is NOT retained on disk upon an uncertain outcome or crash, prioritizing privacy. A crash during an ephemeral prompt legitimately results in `INPUT_REQUIRED` on recovery. Durable replay across crashes requires explicit caller opt-in (`retention_mode = "retained"`).

### 2.4 Final Call ACK Retraction (Grafted)
Final Call is a rigorous CAS-backed integrity barrier.
- **Pre-authorization Retraction**: Removes the actor's ACK and holds the authorization barrier.
- **Post-authorization Retraction**: Authorization has already committed. The retraction becomes a revocation request that fences future work but cannot undo completed side effects.

### 2.5 Epoch and Scope-Gated Restrictions (Grafted)
Minimal, foundational extension to the existing `HealthService`:
- **Scopes**: Restrictions are categorized as shared-account/provider (applying to all member bindings) or profile-specific.
- **Epochs**: Restrictions maintain an epoch counter. A stale recovery probe from a prior epoch cannot reopen a newer restriction circuit.

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

# Retention mode for staged prompts: "ephemeral" (deleted on crash/uncertainty) or "retained" (preserved for durable replay)
retention_mode = "ephemeral" 

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


---

## 4. Scope Exclusions

The following concepts from Round 1 are explicitly **excluded**:
- **cx.astra's D13 Ledger**: Porting every legacy `action_*` function into formal receipts is scope creep. We are porting only the 6 known gaps and IPC hygiene.
- **cx.astra's 20+ Namespace Model**: Rejected in favor of `ag.opus`'s single `DispatchPolicy` config file.

---

## 5. FULL MECE Exception-Case Tables

## 1. DispatchPolicy Resolution State Machine

### 1.1 States

| State | Description |
|---|---|
| `UNRESOLVED` | No policy loaded yet |
| `LOADING` | Reading config files |
| `VALIDATING` | Checking resolved values |
| `RESOLVED` | Frozen, immutable policy ready |
| `ERROR` | Validation or I/O failure |

### 1.2 Transitions

| From | Event | To | Side effects |
|---|---|---|---|
| UNRESOLVED | `resolve()` called | LOADING | Read config chain |
| LOADING | All files read successfully | VALIDATING | Merge layers |
| LOADING | File I/O error (missing ok) | VALIDATING | Use defaults for missing files |
| LOADING | File I/O error (permission denied) | ERROR | `ConfigurationError` |
| LOADING | Malformed TOML | ERROR | `ConfigurationError` with parse position |
| VALIDATING | All values valid | RESOLVED | Freeze `DispatchPolicy` |
| VALIDATING | Invalid value | ERROR | `ConfigurationError` with field + reason |

### 1.3 Resolution edge cases (MECE)

| # | Scenario | Expected outcome |
|---|---|---|
| R-01 | No config files at any layer | RESOLVED with all built-in defaults |
| R-02 | Global exists, workspace absent | RESOLVED with global overrides |
| R-03 | Workspace exists, global absent | RESOLVED with workspace overrides |
| R-04 | Both exist, workspace overrides global | RESOLVED with workspace winning |
| R-05 | CLI override trumps both files | RESOLVED with CLI winning |
| R-06 | Config file has unknown keys | RESOLVED (unknown keys ignored, not errored — forward compat) |
| R-07 | Config file has wrong type for known key (e.g., `timeout_seconds = "abc"`) | ERROR: type mismatch |
| R-08 | `consultation.overrides` maps to invalid depth | ERROR: invalid enum value |
| R-09 | `session.soft_pressure_threshold` = 90, `hard` = 75 (inverted) | ERROR: soft must be < hard |
| R-10 | `session.soft_pressure_threshold` = 75, `hard` = 75 (equal) | ERROR: soft must be strictly < hard |
| R-11 | `transport.staging_dir` = `"../../etc"` | ERROR: traversal rejected |
| R-12 | `transport.staging_dir` = `"/absolute/path"` | ERROR: must be relative |
| R-13 | `transport.max_inline_bytes` = 0 | ERROR: must be > 0 |
| R-14 | `transport.max_inline_bytes` = -1 | ERROR: must be > 0 |
| R-15 | `consensus.quorum_formula` = "unknown_formula" | ERROR: invalid formula |
| R-16 | `consensus.final_call_rule` = "invalid" | ERROR: invalid rule |
| R-17 | Per-command minimum overrides user config (e.g., `consensus propose` forced to >= QUORUM) | RESOLVED: user's lower value silently raised to minimum |
| R-18 | Config file is valid TOML but empty `{}` | RESOLVED with all defaults (empty overrides nothing) |
| R-19 | Config file encoded in UTF-16 | ERROR: TOML spec requires UTF-8 |
| R-20 | Concurrent resolution calls | Each independently resolves (no shared mutable state in resolver) |

---

## 2. ConsultationDepth Decision Tree

### 2.1 Orchestrator decision points (per dispatch)

```
Input: resolved DispatchPolicy.consultation_depth
       resolved DispatchPolicy.consensus (if applicable)

NONE ──────────────────────────────────────→ proceed to dispatch
NOTIFY ────→ create notification target ──→ proceed to dispatch (non-blocking)
REVIEW ────→ create round(quorum=1) ──────→ wait up to timeout
             ├── ACK received ────────────→ proceed to dispatch
             └── timeout ─────────────────→ proceed to dispatch (non-blocking)
QUORUM ────→ create round(quorum=formula(N,risk)) → wait
             ├── quorum reached ──────────→ check final_call_rule
             │   ├── final_call needed ───→ enter Final Call phase
             │   └── no final_call ───────→ resolve → proceed
             ├── timeout ─────────────────→ escalation
             └── explicit disagree ───────→ blocked (escalation or abandon)
UNANIMOUS ──→ create round(quorum=N) ─────→ same as QUORUM but N=all required
```

### 2.2 MECE edge cases for each depth

#### NONE

| # | Case | Outcome |
|---|---|---|
| CD-N-01 | Simple dispatch with `depth=NONE` | Direct dispatch, no governance targets created |
| CD-N-02 | `depth=NONE` overridden by per-command minimum to QUORUM | Round created (minimum enforced) |

#### NOTIFY

| # | Case | Outcome |
|---|---|---|
| CD-NF-01 | Notification target created, all peers read it | Target state: delivered |
| CD-NF-02 | Notification target created, some peers unreachable | Target state: partially-delivered (not an error) |
| CD-NF-03 | Notification target created, zero peers reachable | Target state: undelivered (dispatch still proceeds — NOTIFY is non-blocking) |

#### REVIEW

| # | Case | Outcome |
|---|---|---|
| CD-R-01 | Single reviewer ACKs within timeout | Dispatch proceeds |
| CD-R-02 | Single reviewer NACKs | Dispatch blocked; escalation |
| CD-R-03 | Reviewer doesn't respond before timeout | Dispatch proceeds (REVIEW is non-blocking on timeout) |
| CD-R-04 | Reviewer is the same as the proposer | `InvalidMutationError` — self-review prohibited |
| CD-R-05 | No eligible reviewers (all quarantined) | Escalation immediately |

#### QUORUM

| # | Case | Outcome |
|---|---|---|
| CD-Q-01 | All required agree, no disagree | Quorum reached → check final_call |
| CD-Q-02 | Majority agrees, minority abstains | Quorum reached if formula satisfied |
| CD-Q-03 | Majority agrees, one disagrees | Blocked — explicit disagree blocks |
| CD-Q-04 | Timeout with insufficient votes | Escalation |
| CD-Q-05 | Exactly `quorum_required` agree, rest silent | Quorum reached (silence ≠ disagree at QUORUM depth) |
| CD-Q-06 | `quorum_formula=majority`, 3 voters, 2 agree | Quorum reached (2 > 1.5) |
| CD-Q-07 | `quorum_formula=majority`, 4 voters, 2 agree | Quorum NOT reached (2 = 2, need > 2) |
| CD-Q-08 | `quorum_formula=supermajority`, 3 voters, 2 agree | Quorum reached (2 ≥ 2) |
| CD-Q-09 | `quorum_formula=supermajority`, 6 voters, 3 agree | Quorum NOT reached (3 < 4) |
| CD-Q-10 | Proposer votes (not the sole voter) | Vote counted normally |
| CD-Q-11 | Only proposer votes, no non-proposer | Blocked: `non_proposer_required` check |

#### UNANIMOUS

| # | Case | Outcome |
|---|---|---|
| CD-U-01 | All required explicitly agree | Quorum reached |
| CD-U-02 | All but one agree, one abstains | Quorum NOT reached (abstain ≠ agree at UNANIMOUS) |
| CD-U-03 | All but one agree, one unreachable | Blocked until timeout, then escalation |
| CD-U-04 | All agree but one then disagrees after initial agree | Vote is immutable — disagreement after agreement is a new superseding event that reopens the question |
| CD-U-05 | Required voter set is 1 person (+ proposer = 2 total) | Both must agree (quorum min floor = 2) |

---

## 3. Consensus Round Extended State Machine

### 3.1 Phase transitions with Final Call integration

```
proposed ──→ voting (snapshot created)
         ──→ abandoned (validation failure)

voting ────→ quorum_reached (quorum satisfied)
         ──→ timeout (deadline passed)
         ──→ forced_escalation (authorized)
         ──→ abandoned (voter set invalidated)

quorum_reached ──→ final_call (final_call_rule requires it)
               ──→ resolved (final_call_rule = NEVER, or no dissent with DISSENT_ONLY)

final_call ──→ resolved (all ACK)
           ──→ timeout → forced_escalation
           ──→ (stays in final_call if NACKs received — needs proposer action)

timeout ───→ forced_escalation (policy says escalate)
         ──→ resolved (authorized acceptance of timeout)

forced_escalation ──→ resolved (human/coordinator decides)
                  ──→ voting (escalation rejected, round reopened)

resolved ──→ (terminal)
abandoned ──→ (terminal)
```

### 3.2 Transition edge cases (MECE)

| # | From | Event | To | Detail |
|---|---|---|---|---|
| SM-01 | proposed | Valid voter set snapshot | voting | Normal |
| SM-02 | proposed | Empty voter set after health filter | abandoned | "insufficient eligible voters" |
| SM-03 | proposed | Duplicate round_id | Error | `StaleRevisionError` (CAS) |
| SM-04 | voting | Agree + quorum met | quorum_reached | Normal |
| SM-05 | voting | Agree + quorum NOT met | voting | Stay, await more votes |
| SM-06 | voting | Disagree (first) | voting | Blocks quorum at UNANIMOUS; flags dissent |
| SM-07 | voting | Disagree blocks quorum at QUORUM depth | depends | If remaining possible votes can't reach quorum, escalation |
| SM-08 | voting | `need_more_info` vote | voting | Counts as neither agree nor disagree |
| SM-09 | voting | `abstain` vote | voting | Doesn't count toward quorum |
| SM-10 | voting | Timeout fires | timeout | `timeout_evidence` recorded |
| SM-11 | voting | Force escalation by authorized actor | forced_escalation | Manual override |
| SM-12 | voting | Same voter votes twice | `StaleRevisionError` | Votes are immutable per voter |
| SM-13 | quorum_reached | `final_call_rule=ALWAYS` | final_call | Always enter |
| SM-14 | quorum_reached | `final_call_rule=NEVER` | resolved | Skip Final Call |
| SM-15 | quorum_reached | `final_call_rule=TIER_0_ONLY` + risk=tier-0 | final_call | Enter |
| SM-16 | quorum_reached | `final_call_rule=TIER_0_ONLY` + risk=low | resolved | Skip |
| SM-17 | quorum_reached | `final_call_rule=DISSENT_ONLY` + no dissent | resolved | Skip |
| SM-18 | quorum_reached | `final_call_rule=DISSENT_ONLY` + dissent | final_call | Enter |
| SM-19 | final_call | All eligible ACK | resolved | `outcome=approved` |
| SM-20 | final_call | One NACK | final_call | Stays, proposer must address |
| SM-21 | final_call | Timeout | forced_escalation | Same timeout mechanism |
| SM-22 | final_call | ACK from non-eligible actor | `InvalidMutationError` | Authorization check |
| SM-29 | final_call | Pre-authorization ACK retraction | final_call | Actor's ACK is removed, barrier remains held. |
| SM-30 | resolved | Post-authorization ACK retraction | resolved | Round cannot un-resolve; creates separate revocation-request adjacent record (fences future work, cannot undo completed effect). |
| SM-31 | final_call | Concurrent ACK and retraction race | depends | Serialized via CAS. Loser gets `StaleRevisionError` and must retry. |
| SM-23 | timeout | Escalation accepted | resolved | Human override |
| SM-24 | timeout | Escalation → round reopened | voting | Rare but valid |
| SM-25 | forced_escalation | Human resolves | resolved | Terminal |
| SM-26 | forced_escalation | Human reopens | voting | Back to voting |
| SM-27 | resolved | Any mutation | `InvalidMutationError` | Terminal state |
| SM-28 | abandoned | Any mutation | `InvalidMutationError` | Terminal state |

---

## 4. Session Rotation State Machine

### 4.1 States (from SessionRotationSaga)

Already defined in the saga; this section adds the orchestrator's response to each:

| Saga result | Orchestrator action | CLI output |
|---|---|---|
| PROCEED_WITH_REUSE | Use existing session. No rotation. | (silent) |
| CHECKPOINT_REQUIRED | Issue checkpoint command before dispatch. | stderr: "Session pressure detected; checkpoint issued" |
| ROTATION_PENDING_PROCEED | Proceed but warn. | stderr: "Session pressure detected but rotation not safe; proceeding with current session" |
| ROTATION_CLAIMED | New session. Use new conversation_id. | stderr: "Session rotated (new conversation)" |
| ROTATION_IN_PROGRESS_RETRY | Back off. Retry after delay. | stderr: "Session rotation in progress; retrying..." |

### 4.2 Edge cases

| # | Case | Expected |
|---|---|---|
| SS-01 | First-ever dispatch (no session exists) | `fresh` creates one; `auto`/`reuse` create one implicitly via dispatch |
| SS-02 | `auto` + exact-attribution evidence + pressure above soft, below hard | PROCEED_WITH_REUSE (pressure not "reached" until hard threshold) |
| SS-03 | `auto` + exact-attribution evidence + pressure above hard + safe=true | ROTATION_CLAIMED |
| SS-04 | `auto` + exact-attribution evidence + pressure above hard + safe=false | ROTATION_PENDING_PROCEED |
| SS-05 | `auto` + estimate-only evidence (not exact-attribution) | PROCEED_WITH_REUSE (estimate not trusted) |
| SS-06 | `auto` + stale evidence (age > max_observation_age_ms) | PROCEED_WITH_REUSE (stale not trusted) |
| SS-07 | `fresh` regardless of any evidence | ROTATION_CLAIMED (or ROTATION_IN_PROGRESS_RETRY) |
| SS-08 | `reuse` + pressure above hard | CHECKPOINT_REQUIRED (reuse never rotates, just checkpoints) |
| SS-09 | Concurrent `ROTATION_CLAIMED` from two dispatches | Second gets ROTATION_IN_PROGRESS_RETRY (CAS conflict) |
| SS-10 | ROTATION_IN_PROGRESS_RETRY + max retries exhausted | Dispatch fails with clear error |

---

## 5. Quarantine Review Resolution

### 5.1 Decision tree

```
review target exists + status=REQUESTED
    ├── operator chooses DISMISS
    │   └── status → DISMISSED, no health action
    └── operator chooses ESCALATE
        ├── peer_key valid?
        │   ├── yes → open_manual_quarantine()
        │   │   ├── peer already quarantined → idempotent no-op
        │   │   └── peer not quarantined → circuit opens
        │   └── no → InvalidMutationError
        └── peer deregistered → RecordNotFoundError
```

### 5.2 Edge cases

| # | Case | Expected |
|---|---|---|
| QR-E-01 | DISMISS valid review | Status → DISMISSED |
| QR-E-02 | ESCALATE valid review, peer healthy | Peer quarantined |
| QR-E-03 | ESCALATE valid review, peer already quarantined | Idempotent success |
| QR-E-04 | ESCALATE, peer_key missing from review target | InvalidMutationError |
| QR-E-05 | ESCALATE, peer_key present but node deregistered | RecordNotFoundError |
| QR-E-06 | ESCALATE, peer_kind malformed | InvalidMutationError |
| QR-E-07 | Resolve a review that's already resolved (DISMISSED or ESCALATED) | InvalidMutationError (not REQUESTED) |
| QR-E-08 | Resolve a nonexistent review_id | RecordNotFoundError |
| QR-E-09 | ESCALATE with invalid actor (no AuthenticatedSubject) | Authorization failure |
| QR-E-10 | Rapid ESCALATE → RECOVER sequence | Both succeed; quarantine opens then closes |

---


## 5.5 Execution Certainty & Crash Fences (Grafted in R2b)

| # | Case | Expected |
|---|---|---|
| EX-01 | Cancellation requested *before* execution claim | Effect is `NOT_STARTED`. Cleanly cancelled. |
| EX-02 | Cancellation requested *after* execution claim (uncertain crash) | Effect marked `MAY_HAVE_STARTED` or `STARTED`. Replay requires idempotency validation. True outcome recorded. |
| EX-03 | Cancellation requested on `COMPLETED` effect | Rejected. Cannot overwrite completed history with cancelled fiction. |

## 6. Transport / IPC State Machine

### 6.1 Prompt staging lifecycle

```
Prompt received (inline text or --query-file)
    ├── size ≤ max_inline_bytes → pass inline to adapter
    └── size > max_inline_bytes → stage_prompt()
        ├── staging succeeds → reference on AdapterRequest.prompt_reference
        │   ├── dispatch outcome DEFINITE (success/failure/cancel)
        │   │   ├── cleanup_rule = on_completion → remove_staged_prompt()
        │   │   ├── cleanup_rule = on_success_only
        │   │   │   ├── success → remove_staged_prompt()
        │   │   │   └── failure → preserve for debugging
        │   │   └── cleanup_rule = sweep_only → no action
        │   └── dispatch outcome UNCERTAIN → preserve always
        └── staging fails (disk error) → dispatch fails with clear error
```

### 6.2 Sweep lifecycle

```
At dispatch start:
    sweep_stale_staged_prompts(max_age=config.sweep_max_age_seconds)
    └── removes files older than max_age from staging dir
```

### 6.3 Edge cases

| # | Case | Expected |
|---|---|---|
| TP-E-01 | `--query-file` with file exactly at max_inline_bytes | Inline (not staged) |
| TP-E-02 | `--query-file` with file at max_inline_bytes + 1 | Staged |
| TP-E-03 | Staging dir doesn't exist | Created by `stage_prompt()` (existing behavior) |
| TP-E-04 | Staging dir is read-only | `stage_prompt()` raises OSError → dispatch fails |
| TP-E-05 | Disk full during staging | `stage_prompt()` raises OSError → staged file cleaned up (existing behavior) |
| TP-E-06 | Same prompt staged twice (retry) | Reuses existing file (idempotent, existing behavior) |
| TP-E-07 | Staged file modified externally between staging and read | Peer reads modified content. No mitigation (filesystem trust boundary). |
| TP-E-08 | Sweep runs during active dispatch | Safe if dispatch started > max_age ago (shouldn't happen with reasonable max_age). **Design note**: sweep should skip files newer than max_age, which it already does. |
| TP-E-09 | `--query-file` path is a symlink | Followed (Python's `Path.read_text()` follows symlinks). No special handling — symlink is the user's responsibility. |
| TP-E-10 | `--query-file` path is a directory | `IsADirectoryError` → exit 2 |
| TP-E-11 | `--query-file` path is binary (non-UTF-8) | `UnicodeDecodeError` → exit 2 with "query file must be UTF-8" |
| TP-E-12 | Crash/uncertain outcome with `retention_mode="ephemeral"` | Staged file is cleaned up per normal lifecycle; recovery requires `INPUT_REQUIRED` (see sec 2.3). |
| TP-E-13 | Crash/uncertain outcome with `retention_mode="retained"` | Staged file is preserved past the crash; safe for automated replay on recovery. |

---

## 7. Health Consequence Decision Tree

### 7.1 After dispatch failure

```
Dispatch fails definitively
    ├── health_consequence.default_consequence
    │   ├── report_only → create quarantine-review target (existing behavior)
    │   │                  no automatic quarantine
    │   ├── review_then_quarantine → create quarantine-review target
    │   │                           (manual ESCALATE → quarantine, via Gap 3 fix)
    │   └── evidence_circuit_only → if typed evidence available:
    │                                 auto-circuit-open (existing HealthService)
    │                               else:
    │                                 create quarantine-review target (no fabrication)
    └── health_consequence.recovery_authority
        ├── MANUAL → only `authorize_administrative_recovery()` can recover
        ├── EVIDENCE_BASED → `authorize_recovery()` with typed evidence
        └── NONE → no recovery authority configured (quarantine is permanent until human intervenes)
```

### 7.2 Edge cases

| # | Case | Expected |
|---|---|---|
| HC-01 | Dispatch fails, `consequence=report_only` | Quarantine-review created, no auto-quarantine |
| HC-02 | Dispatch fails, `consequence=review_then_quarantine`, operator ESCALATEs | Peer quarantined |
| HC-03 | Dispatch fails, `consequence=evidence_circuit_only`, typed evidence present | Auto-circuit-open |
| HC-04 | Dispatch fails, `consequence=evidence_circuit_only`, no typed evidence | Falls back to review-request (no fabrication) |
| HC-05 | Dispatch succeeds | No health consequence regardless of config |
| HC-06 | Dispatch outcome uncertain | No health consequence (cannot judge failure from uncertainty) |
| HC-07 | Multiple consecutive failures for same peer | Each creates a separate review target; quarantine is idempotent |
| HC-08 | Recovery attempted while circuit still closing | `authorize_recovery()` checks circuit state; may reject if evidence insufficient |
| HC-09 | Recovery probe from Epoch 1 arrives, but restriction is now at Epoch 2 | Probe is rejected as stale (`StaleAuthorityEpochError`). Circuit remains closed. |
| HC-10 | Shared-account restriction activated | Applies to all peer bindings under that account, not just the single profiled identity. |
| HC-11 | Profile-specific restriction activated | Sibling profiles on the same account remain active and routable. |

---

## 8. Telemetry Headroom Surface

### 8.1 Display tiers

| Surface | Content |
|---|---|
| `none` | Raw quota numbers only (existing behavior) |
| `basic` | Per-peer: used%, remaining%, window, freshness |
| `full` | basic + 24h fail count/rate, trend direction, headroom forecast |

### 8.2 Data sources

| Datum | Source | Freshness requirement |
|---|---|---|
| Quota used% | `quota_polling.py` → vendor API | Real-time (polled) |
| Quota remaining% | Computed: 100% - used% | Real-time |
| Window duration | Vendor API response | Real-time |
| 24h fail count | New: dispatch failure counter in telemetry SQLite | Aggregated |
| 24h fail rate | Computed: failures / total dispatches | Aggregated |
| Trend | Computed: current window used% vs previous | Requires ≥ 2 data points |
| Headroom forecast | Computed: at current rate, when will limit be hit | Requires trend |

### 8.3 Edge cases

| # | Case | Expected |
|---|---|---|
| TH-01 | No telemetry data at all | "No telemetry data" (not "0%") |
| TH-02 | Data for one peer only | Show that peer; "No data" for others |
| TH-03 | Stale data (age > max) | Show with `[stale: Xm ago]` tag |
| TH-04 | Vendor API returns error | Show `[error]` with last-known if available |
| TH-05 | 24h fail rate with zero total dispatches | "fail rate: no dispatches" (not "0%" or "NaN") |
| TH-06 | Trend with only 1 data point | "trend: insufficient data" |
| TH-07 | Headroom = "full" but fail tracking not populated yet | "fail rate: no data" |
| TH-08 | Rate limit window changed between polls | Detect via `windowDurationMins`; display both windows |

---

## 9. Cross-Cutting Concerns

### 9.1 Concurrency

| Scenario | Mechanism | Expectation |
|---|---|---|
| Two dispatches resolve policy simultaneously | PolicyResolver is stateless; each resolves independently | No conflict |
| Two dispatches create consensus rounds simultaneously | GovernanceBroker CAS (expected_revision) prevents conflicts | Second gets `StaleRevisionError` if target_id collides |
| Two ESCALATEs for same peer simultaneously | `open_manual_quarantine()` is idempotent | Both succeed |
| Session rotation and dispatch interleave | CAS-based claim in `SessionRotationSaga` | Only one claimant succeeds |
| Config file changes during dispatch | No effect — policy already snapshotted | Correct by design |

### 9.2 Backward compatibility

| Concern | Mitigation |
|---|---|
| Existing `peerhub ask` without `--session-policy` | Default from config (which defaults to `"auto"`, which without telemetry = PROCEED_WITH_REUSE = current behavior) |
| Existing `peerhub consensus propose` without depth | Default from config (which defaults to `"quorum"` + `all_required` formula = current behavior `max(2, N)`) |
| Existing `dispatch-policy.toml` doesn't exist | All defaults = current behavior |
| `ConsensusService._quorum_required()` callers | New `formula` parameter has default `"all_required"` = current behavior |

### 9.3 Observability

Every policy resolution should log (at DEBUG level):
- The resolved `DispatchPolicy` (JSON-serialized)
- Which config layers contributed
- Which CLI overrides were applied
- Which per-command minimums were enforced

Every orchestrator decision should log:
- The consultation depth chosen
- Whether session rotation was triggered
- Whether a consensus round was created
- The final_call_rule applied

---

## 10. File Summary

This spec contains **133 enumerated test cases** across 9 categories:
- Policy resolution: 20 cases (R-01 to R-20)
- Consultation depth: ~16 cases (CD-N/NF/R/Q/U)
- Consensus state machine: 31 cases (SM-01 to SM-31)
- Session rotation: 10 cases (SS-01 to SS-10)
- Quarantine review: 10 cases (QR-E-01 to QR-E-10)
- Transport/IPC: 13 cases (TP-E-01 to TP-E-13)
- Health consequence: 8 cases (HC-01 to HC-08)
- Telemetry headroom: 8 cases (TH-01 to TH-08)
- Cross-cutting: concurrency + compatibility (5 + 4 scenarios)

Each case is specified to the level of "given X, expect Y" — ready for direct translation to `pytest` parametrized test cases in the TDD stage.


---

## 6. Open Items for cx.astra

This document is pending your review. Please confirm or contest the following resolutions to your Round 1 flagged uncertainties:

1. **O1 (Quarantine Interpretation)**: We synthesized this using `ag.opus`'s `health_consequence` mapping. Instead of spoofing a health check, a failure triggers a review which *then* explicitly calls `open_manual_quarantine()`. This preserves operator authority without fabricating evidence. Do you ratify this approach?
2. **O2 (Routing-before-evidence)**: We synthesized this by adopting `ag.opus`'s `--effort-hint` as an **advisory-only** output. It honors declared preferences truthfully without enforcing a strict evidence-backed dispatch block. Do you ratify this?
3. **O3 (Host-authority-boundary)**: Both rounds agree that preventing peer impersonation of a human grant requires an OS-level or cryptographic boundary, which cannot be solved in policy prose. Should we officially **defer this to the deployment phase**, or do you require a specific cryptographic receipt schema added to `DispatchPolicy` now?
