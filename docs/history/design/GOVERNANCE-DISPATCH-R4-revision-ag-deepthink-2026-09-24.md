# GOVERNANCE-DISPATCH-R6-ratified-revision-ag-deepthink-2026-09-24

**Status**: **RATIFIED** (2026-09-24, by cx.astra, unanimous with ag.opus/ag.deepthink Round 1-10 contributions). See [`GOVERNANCE-DISPATCH-R10-cx-astra-FINAL-ratification-2026-09-24.md`](GOVERNANCE-DISPATCH-R10-cx-astra-FINAL-ratification-2026-09-24.md) for the final ratification record (SHA-256 `4f17bea62b350aef1bab80eef849e8b3f20a34e5ad10734006aabf9423088b51`). This document (despite its filename/title still saying "R6") is the ratified design: 788 lines, 135 MECE exception cases, closing all of Rounds 1-10's B1-B10 objections and section-6 corrections with zero open design amendments. Implementation, acceptance tests, and deployment qualification remain **TEST NEEDED** -- this ratifies the design only, not runtime behavior.

This document resolves all B1-B10 blocking objections and Section 6 specification corrections from cx.astra's Round 5 ratification review.

This document is the unified synthesis of the peerhub governance/dispatch redesign. It consolidates the proposals from Round 1 (`ag.opus`, `cx.astra`) and the cross-reviews from Round 2 and 2b.

## 1. Changelog & Provenance

*(This file has been updated in-place with Round 6 specification amendments resolving cx.astra's Round 5 findings.)*

- **From ag.opus Round 1 (Unchanged)**: The `DispatchPolicy` dataclass, the `DispatchOrchestrator` thin coordinator, the `dispatch-policy.toml` config format, the 5-value `ConsultationDepth` Enum + many-to-one mapping from legacy `collab_rate` (not bijective), advisory-only effort routing, and the `PolicyResolver` resolution order.
- **From cx.astra Round 1 (Grafted via Round 2b)**: Final Call ACK retraction state machine, execution certainty / crash-recovery fencing (NOT_STARTED to COMPLETED), explicit opt-in privacy default for staged prompts (`retention_mode`), and epoch/scope-gated restriction semantics for `HealthService`.
- **Dropped (Scope Creep)**: cx.astra's D13 full-legacy-action ledger and its 20+ policy-namespace model were rejected to preserve radical simplicity.

---

## 2. Final Architecture

The architecture uses a thin coordinator (`DispatchOrchestrator`) that reads a frozen, immutable configuration record (`DispatchPolicy`) resolved from `dispatch-policy.toml`, CLI overrides, and per-command defaults.


### 2.1 The Ingress Gate and Plan Authorization (B1)
The orchestrator is not an optional CLI wrapper; it is the **unavoidable application admission point**.
- **Enforcement Point**: Native ask, broadcast children, proposal/consensus transitions, and review effects in this round all reach the same evaluator before the existing runner. No alternate proposal auto-resolution path bypasses it.
- **Plan Altitude vs New Direction**: A new direction evaluates standing policies from scratch. A child uses an existing plan approval ONLY when its operation, resource scope, generation, and relevant bounds are strictly contained within that plan. Expired, revoked, or expanded work requires a new review.
- **Policy Precedence**: Merged preferences must enforce protected standing obligations. Editing a lower-trust TOML file cannot authorize its own downgrade (e.g., disabling DIR-006 or mandatory Final Call).
- **Ingress Semantics**: Ingress strictly accepts exactly one input source. Original queries are preserved (query fidelity) and borrowed file ownership is not usurped by cleanup. There is no ingress size rejection at this layer (`max_inline_bytes` only determines staging vs inline). Both `ask` and `broadcast` share this identical ingress contract.

| Source Condition | Outcome Before Dispatch |
|---|---|
| Exactly one valid source (inline or `--query-file`) | Reaches canonical input contract |
| Zero sources provided | Exit 2 (missing input) |
| Multiple sources provided | Exit 2 (ambiguous input) |
| Empty text or empty file | Exit 2 (empty input) |
| Invalid source (directory, non-UTF-8) | Exit 2 (invalid input) |
| Unsupported selectors (e.g., `stdin`) | Exit 2 (unsupported selector) |
- **Durable Gate Contract**: The effective dispatch policy, the runner authorization binding/reference, and the atomic decision/receipt/pending-effect boundary are durably persisted in the existing store.
- **Standing Policy**: Activating any standing policy requires prior authorization.
- **Recursion Exemption**: Bounded consultation, vote and ACK collection does not trigger additional consultation. Actor, disclosure, approved-scope and effect/admission checks still apply. This exemption authorizes collecting the review or decision evidence only; it cannot authorize implementing the proposal under review.

| Purpose | Default | Minimum | Description |
|---|---|---|---|
| New direction | `quorum` | Protected floor | Evaluates standing policies from scratch |
| Contained child | `none` | `none` | Reuses existing plan approval within identical bounds |
| Ordinary ask/broadcast | Configured | `none` | Resolves from TOML or CLI overrides |
| Consultation/Vote/ACK | `none` | `none` | Authorizes collecting evidence only; no additional consultation triggered |

### 2.2 What DispatchPolicy IS

A `DispatchPolicy` is an immutable, resolved record that answers every question the dispatch/governance orchestrator needs:

```python
@dataclass(frozen=True)
class DispatchPolicy:
    """Resolved once, immutable for the action's lifetime. All nested values are immutable."""

    # --- Consultation axis ---
    consultation_depth: ConsultationDepth # Enum: NONE, NOTIFY, REVIEW, QUORUM, UNANIMOUS

    # --- Consensus sub-policy (provides timeout for REVIEW, quorum/rules for QUORUM+) ---
    consensus: ConsensusPolicy
    # quorum_formula: str, final_call_rule: str, timeout_seconds: int, escalation_target: str

    # --- Session management ---
    session: SessionPolicy
    # mode: str, soft_pressure_threshold: int, hard_pressure_threshold: int, max_observation_age_ms: int

    # --- Profile/routing ---
    routing: RoutingPolicy
    # effort_routing: str (off/advisory/opt-in)

    # --- Transport/IPC ---
    transport: TransportPolicy
    # max_inline_bytes: int, staging_dir: str, cleanup_rule: str, retention_mode: str

    # --- Health consequences ---
    health_consequence: HealthConsequencePolicy
    # default_consequence: str, recovery_authority: str

    # --- Telemetry ---
    telemetry: TelemetryPolicy
    # refresh_before_dispatch: bool, headroom_surface: str

    # --- Provenance ---
    resolved_from: str
    resolved_at: int
    source_hash: str
```


### 2.3 Consensus Semantics, Vote Correction, and Dissent (B3 & B2)
- **Quorum vs Dissent**: Ordinary disagreement can coexist with a majority threshold (it does not inherently veto). However, a typed integrity/safety concern (BLOCK) is a universal veto regardless of majority.
- **Vote Correction**: Same-key duplicate -> original receipt/count once; unauthorized changed vote -> conflict; explicit correction while voting -> invalidate old contribution and recompute before a fresh valid vote can restore it.
- **Electorate Rules**: Freeze logical identities, required membership, independent non-proposer condition, denominator and absences; high-risk missing required voters hold and health cannot shrink an open electorate.
- **REVIEW/Reopen**: Give advisory REVIEW a timeout source distinct from binding minimum-two consensus, and make reopened/exceptional resolutions retain explicit proof, outcomes and deadlines.

- **Final Call Triggers**: The mandatory trigger union is: `tier-0 OR high-risk OR unresolved dissent`. The `never` rule may only disable *optional* additional Final Calls, never the mandatory floors.
- **Final Call Integrity**: The barrier freezes candidate identity (subject, bounds, policy, active vote set). A changed candidate clears all prior ACKs (material decision changes require fresh votes). Bind ACK/retraction to actor and exact candidate, including authority/disposition references. Last ACK, live required authority, candidate validity, absence of qualifying concerns and authorization must commit together. Cosmetic feedback is not a veto. Reopening gets a defined deadline and invalidates obsolete ACKs. Preserve dissent-origin obligation after arbiter disposition.

### 2.4 Execution Certainty and Crash-Recovery Fencing (Grafted)
Execution state lives in the existing runner. The orchestrator invokes it subject to:

| Observed certainty | Recovery rule |
|---|---|
| Before claim | No effect |
| After durable claim but uncertain handoff | Reconcile uncertainty |
| External effect before result commit | No unsafe replay |
| Committed result before response/cleanup | Return existing receipt and repair bookkeeping |
| Mixed broadcast results | Do not repeat successful children when recovering a partial broadcast |

**Execution Retry Rules**:

| Observed certainty | Recovery rule |
|---|---|
| NOT_STARTED, proven | A new attempt may run only if current authorization, admission, input, deadline and retry bounds permit. |
| MAY_HAVE_STARTED | Reconcile using stable process/provider request identity. No automatic non-idempotent replay. Independently proven safe/idempotent work may retry within bounds. |
| STARTED, no definitive terminal outcome | Same replay restriction; a cancellation request is not proof of death or non-effect. |
| COMPLETED success | Return/settle that result. Retry cleanup/accounting/notification only, never the model/effect. |
| COMPLETED failure | Retry only when the operation-specific effect semantics establish safety. A nonzero exit alone does not prove zero side effects. |

**Execution Recovery Rules**: Preserve unique retry attempt identity. Lease expiry is not process death.
**Acceptance**: The four boundaries without a cancellation request, lease expiry with a live old process, lost response after completion, and mixed broadcast results all have a defined surviving record and effect count.

Claim atomically checks the relevant live authorization/revocation version, restriction epoch, target/session binding, and current attempt ownership. Concurrent claim and revocation share a serializable guard. If revocation wins, no new claim. If claim wins, recheck before adapter invocation and apply best-effort cancellation.

### 2.5 Privacy vs. Crash-Replay (Grafted)
- **Ephemeral (Default)**: No durable canonical prompt retention in request/audit/WAL/backup state. Temporary transport copies survive until trusted consumption or confirmed consumer death makes cleanup safe. Age alone is not proof for deletion. On crash recovery, missing ephemeral input yields `INPUT_REQUIRED` (an input blocker, not an execution-certainty reset).
- **Retained**: Opt-in protected canonical input with explicit TTL and deletion policy, separate from scratch copies. Safe for replay ONLY IF the certainty table (B4) permits it.
- **Ownership**: Every retry and broadcast child owns a distinct exclusively created transport copy. Expected content identity is verified before consumption; modified bytes reject before execution.

### 2.6 Final Call ACK Retraction (Grafted)
Final Call is a rigorous CAS-backed integrity barrier.
- **Pre-authorization Retraction**: Removes the actor's ACK and holds the authorization barrier.
- **Post-authorization Retraction**: Authorization has already committed. The retraction becomes a revocation request that fences future work but cannot undo completed side effects.

### 2.7 Epoch and Scope-Gated Restrictions (Grafted)
Minimal, foundational extension to the existing `HealthService`:
- **Scopes**: Restrictions use existing scopes (`ROOT`, `PROFILE`, `QUOTA_FAMILY`, `ENVIRONMENT`).
- **Epochs**: Restrictions maintain an epoch counter. A stale recovery probe from a prior epoch is rejected.
- **Pending-Effect Identity**: Bind the pending effect to a stable effect identity, exact restriction target/authority class/current revision, and a validated review revision/operator grant before committing the pending effect.
- **Reconciliation**: Pending/applied reconciliation and concurrent dismissal guards are defined; identical delivery alone is a no-op.
- **Release/Recovery**: Release removes only the named current restriction under the appropriate authority; circuit recovery leaves independent manual/security holds.
- **Scope Membership**: Registered scope membership governs shared effects.
- **Uncertainty Limits**: Uncertainty forbids invented failure, not independently trusted evidence or an authorized operator hold.


---


### 2.8 Automatic Profile Routing (B7)
Routing relies purely on explicit capability matching and user preference, governed by this compact input contract:

| Input | Concrete contract |
|---|---|
| Declared request hint | A caller supplies `--effort-hint` (or the `AdapterRequest.effort_hint` field) carrying the intended work shape key. This is a declaration, not measured quality. |
| Ordered preference mapping | The config field `routing.preference_map` maps each supported hint string to an ordered list of registered profile-binding IDs. In automatic mode, apply the accepted hard filters and choose the first eligible binding in that list. |
| Routing activation | An explicit caller/config setting of `effort_routing = "opt-in"` enables automatic selection. To prevent silent automatic activation, the schema's built-in default is `advisory`. |
| Missing and unknown hints | A missing hint leaves the profile selection unchanged. An unknown hint key causes a validation error. Do not infer an ordering from a model name or tier string. |

| Mode | Behavior |
|---|---|
| `off` / `advisory` | Leaves selection unchanged (advisory may log suggestions). |
| `opt-in` | Applies health, permission, verified binding/capability and approved-scope filters *before* declared preference ordering. |
| `pin` | Selects exactly the eligible target or rejects; never silently substitutes. No eligible target is an explicit blocker. |

**Quality Rule**: Quality remains ABSENT unless evidenced, distinct from a declared preference. Missing quality never becomes a score.

### 2.9 Telemetry Normalization (B8)
- **Normalization**: Normalize both `rateLimits` and every `rateLimitsByLimitId` entry before selection; preserve source/pool/limit/window/reset identity; dedup identical observations; retain conflicts; localize malformed-bucket errors.
- **Snapshot & Applicability**: Preserve applicability/freshness and unknown applicability through selection; use the same normalized snapshot for display/admission.
- **Reliability Calculation**: Definitive started attempts (deduplicated by attempt ID) completed within `(as_of - 24h, as_of]`. Rate = `failed / (succeeded + failed)`. Show the counts, excluded cancelled/unknown/pre-admission categories, and partial coverage.
- **Trend & Refresh**: Restrict historical deltas to comparable pool/window observations; a reset breaks continuity. Bounded source-specific refresh without model calls or suppression of independent valid sources.

### 2.10 Session Policy Saga Wiring (B9)
`SessionRotationSaga` requires a signature change to accept policy values (replacing hardcoded 0.90/0.75 peer-name branches).
- **Semantics**: Below soft = reuse; soft to hard = checkpoint/mark pending; >= hard = request safe rotation for auto. Retry after successful rotation reuses that prepared generation.

### 2.11 Host Authority Contract (B10)
- **Proof Binding**: Bind proof to exact subject/candidate or validated contained standing scope, effect/exception, trusted issuer/designated principal, expiry, replay identity, and revocation version.
- **Persistence**: Persist its reference with the decision.
- **Validation**: Invalid, expired, revoked, wrong-subject, or currently unverifiable required proof waits or rejects without actor-string/coordinator fallback. Valid scoped proof is reused.
- **Deployment Boundary**: Concrete isolation is deferred, but explicitly requires peer-origin issuance/replay/store-modification qualification before claiming malicious-same-user resistance; until then, state the cooperative-process assumption.

## 3. Configuration Surface

### 3.1 File: `dispatch-policy.toml`

Located in the standard config resolution chain (workspace `.peerhub/` → global `~/.peerhub/` → built-in defaults). Uses TOML because peerhub already uses TOML for `ask-defaults.toml` and `model-defaults.toml`.

```toml
# dispatch-policy.toml — Governance/dispatch policy configuration
# Every value has a built-in default; this file overrides selectively.

[consultation]
# Default consultation depth for governance actions.
# Values: "none", "notify", "review", "quorum", "unanimous"
# See Section 2.1 Purpose Table for minimum bounds and default overrides (e.g. contained child defaults to none).
default_depth = "quorum"

# Per-action overrides (action name → depth)
[consultation.overrides]
"consensus.propose" = "quorum"
"task.create" = "quorum" # Aligned with actual intended defaults
"lesson.inject" = "notify"
# Actions not listed here use default_depth.

[consensus]
# Quorum formula: "majority" (>50%), "supermajority" (≥2/3), "unanimous", or "all_required"
quorum_formula = "all_required"

# Final Call rule: "always", "tier_0_or_high_risk_or_dissent", "never"
final_call_rule = "tier_0_or_high_risk_or_dissent" # Explicit mandatory trigger union

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
effort_routing = "advisory" # off | advisory | opt-in

[routing.preference_map]
# Maps hint strings to an ordered list of profile binding IDs
"deep" = ["ag.deepthink", "cc.deepthink", "cx.deepthink"]
"fast" = ["cx.flash", "cc.flash"]



[transport]
# Maximum inline prompt size (bytes) before staging to file
max_inline_bytes = 65536

# Staging directory (resolved under the private temp base)
staging_dir = "prompt-staging"

# Copies live until consumed or consumer death is confirmed.
# Age alone is never sufficient for deletion.
cleanup_rule = "on_consumption_or_death"

# Retention mode for staged prompts: "ephemeral" (deleted when safe/consumer dead) or "retained" (durable replay opt-in)
retention_mode = "ephemeral"

# Time-To-Live for separately retained canonical input (days)
retained_input_ttl_days = 30



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

### 3.2 Schema Field Contracts
| Section | Field | Default | Owner | Bound Inputs / Contracts |
|---|---|---|---|---|
| `consultation` | `default_depth` | `"quorum"` | Orchestrator | `"none"`, `"notify"`, `"review"`, `"quorum"`, `"unanimous"` |
| `consultation` | `overrides` | `{}` | Orchestrator | Map of action to depth |
| `consensus` | `quorum_formula` | `"all_required"` | Consensus | `"majority"`, `"supermajority"`, `"unanimous"`, `"all_required"` |
| `consensus` | `final_call_rule` | `"always"` | Consensus | `"always"`, `"tier_0_or_high_risk_or_dissent"`, `"never"` |
| `consensus` | `timeout_seconds` | `300` | Orchestrator | Applies to REVIEW and binding consensus |
| `consensus` | `escalation_target` | `"coordinator"` | Orchestrator | |
| `session` | `mode` | `"auto"` | Session Saga | Resolved from config `default_mode`. `"fresh"`, `"reuse"`, `"auto"` |
| `session` | `soft_pressure_threshold` | `75` | Session Saga | `0-100` |
| `session` | `hard_pressure_threshold` | `90` | Session Saga | `0-100` |
| `session` | `max_observation_age_ms` | `300000` | Session Saga | Max ms |
| `routing` | `effort_routing` | `"advisory"` | Orchestrator | `"off"`, `"advisory"`, `"opt-in"`. Binds routing-preference inputs. |
| `routing` | `preference_map` | `{}` | Orchestrator | Maps hint strings to an ordered list of profile binding IDs. |
| `transport` | `max_inline_bytes` | `8192` | Transport | > 0 |
| `transport` | `staging_dir` | `"prompt-staging"` | Transport | Resolved under the private temp base. |
| `transport` | `cleanup_rule` | `"on_consumption_or_death"` | Transport | |
| `transport` | `retention_mode` | `"ephemeral"` | Transport | Binds retained TTL to own policy |
| `transport` | `retained_input_ttl_days` | `30` | Transport | > 0 |
| `health_consequence` | `default_consequence`| `"review_then_quarantine"`| Health | |
| `health_consequence` | `recovery_authority` | `"MANUAL"` | Health | Binds recovery authority inputs |
| `telemetry` | `refresh_before_dispatch`| `true` | Telemetry | |
| `telemetry` | `headroom_surface` | `"full"` | Telemetry | |



---

## 4. Scope Exclusions

The following concepts from Round 1 are explicitly **excluded**:
- **cx.astra's D13 Ledger**: Porting every legacy `action_*` function into formal receipts is scope creep. We are porting only the 6 known gaps and IPC hygiene.
- **cx.astra's 20+ Namespace Model**: Rejected in favor of `ag.opus`'s single `DispatchPolicy` config file.

---

## 5. Closure Matrix for this Round
| Scope | Design Status | Implementation Status | Native Entrypoint / Owner / Evidence Trace |
|---|---|---|---|
| Consultation and plan authority | Complete (Sec 2.1, 2.3, 6.5) | TEST NEEDED | `action_ask()` / Orchestrator / (Pending Test) |
| Final Call | Complete (Sec 2.3, 3.1) | TEST NEEDED | `ConsensusService` / Consensus Broker / (Pending Test) |
| Review quarantine | Complete (Sec 5.1, 5.2) | TEST NEEDED | `resolve_quarantine_review()` / Health Service / (Pending Test) |
| Session rotation | Complete (Sec 4.1, 4.2) | TEST NEEDED | `SessionRotationSaga` / Session Manager / (Pending Test) |
| Profile selection | Complete (Sec 2.8) | TEST NEEDED | Routing Module / Orchestrator / (Pending Test) |
| Telemetry | Complete (Sec 2.9) | TEST NEEDED | `quota_polling.py` / Telemetry Service / (Pending Test) |
| IPC and execution recovery | Complete (Sec 2.4, 6.1, 6.2) | TEST NEEDED | `TransportService` / Adapter / (Pending Test) |

## 6. FULL MECE Exception-Case Tables (135 Cases)

*(Note: 135 cases total)*

## 7. DispatchPolicy Resolution State Machine

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
| R-06 | Config file has unknown keys | ERROR: `ConfigValidationError`. Reject unknown core keys; inert extensions must use an explicit namespace. Enforce enum/duration/bound/combination checks. |
| R-07 | Config file has wrong type for known key (e.g., `timeout_seconds = "abc"`) | ERROR: type mismatch |
| R-08 | `consultation.overrides` maps to invalid depth | ERROR: invalid enum value |
| R-09 | `session.soft_pressure_threshold` = 90, `hard` = 75 (inverted) | ERROR: soft must be < hard |
| R-10 | `session.soft_pressure_threshold` = 75, `hard` = 75 (equal) | ERROR: soft must be strictly < hard |
| R-11 | `transport.staging_dir` = `"../../etc"` | ERROR: Path traversal rejected. Must resolve cleanly under `PRIVATE_VOLATILE_ROOT`. |
| R-12 | `transport.staging_dir` = `"/absolute/path"` | ERROR: Absolute paths rejected. Must be a relative segment applied strictly to `PRIVATE_VOLATILE_ROOT`. |
| R-13 | `transport.max_inline_bytes` = 0 | ERROR: must be > 0 |
| R-14 | `transport.max_inline_bytes` = -1 | ERROR: must be > 0 |
| R-15 | `consensus.quorum_formula` = "unknown_formula" | ERROR: invalid formula |
| R-16 | `consensus.final_call_rule` = "invalid" | ERROR: invalid rule |
| R-17 | Per-command minimum overrides user config (e.g., `consensus propose` forced to >= QUORUM) | RESOLVED: user's lower value silently raised to minimum |
| R-18 | Config file is valid TOML but empty `{}` | RESOLVED with all defaults (empty overrides nothing) |
| R-19 | Config file encoded in UTF-16 | ERROR: TOML spec requires UTF-8 |
| R-20 | Concurrent resolution calls | Each independently resolves (no shared mutable state in resolver) |

---

## 8. ConsultationDepth Decision Tree

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
             ├── typed BLOCK ──────────────→ blocked (escalation or abandon)
             └── ordinary disagree ────────→ continue (does not veto if threshold met)
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
| CD-Q-03 | Majority agrees, one disagrees (ordinary) | Approved — ordinary dissent does not veto |
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

## 9. Consensus Round Extended State Machine

### 3.1 Phase transitions with Final Call integration

```
proposed ──→ voting (snapshot created)
         ──→ abandoned (validation failure)

voting ────→ quorum_reached (quorum satisfied)
         ──→ timeout (deadline passed)
         ──→ forced_escalation (authorized)
         ──→ forced_escalation (typed BLOCK received)
         ──→ forced_escalation (quorum mathematically impossible)
         ──→ abandoned (voter set invalidated)

quorum_reached ──→ final_call (mandatory floors hit OR final_call_rule = always)
               ──→ resolved (skip authorized AND mandatory floors absent)

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
| SM-06 | voting | Disagree (Ordinary) | voting | Does not veto at majority; counts for participation |
| SM-07 | voting | Disagree blocks quorum at QUORUM depth | forced_escalation | Votes can no longer mathematically reach quorum. |
| SM-08 | voting | `need_more_info` vote | voting | Counts as neither agree nor disagree |
| SM-08b| voting | BLOCK (Typed Integrity Concern) | forced_escalation | Universal veto regardless of majority |
| SM-09 | voting | `abstain` vote | voting | Doesn't count toward quorum |
| SM-10 | voting | Timeout fires | timeout | `timeout_evidence` recorded |
| SM-11 | voting | Force escalation by authorized actor | forced_escalation | Manual override |
| SM-12 | voting | Same voter votes twice | voting | Idempotent duplicate counts once; unauthorized change = conflict; explicit correction invalidates old vote. |
| SM-13 | quorum_reached | `final_call_rule="always"` | final_call | Always enter |
| SM-14 | quorum_reached | `final_call_rule="never"` + no mandatory floors hit | resolved | Skip optional Final Call |
| SM-15 | quorum_reached | `final_call_rule="never"` + mandatory floor hit (e.g. high-risk) | final_call | Cannot bypass mandatory floors |
| SM-16 | quorum_reached | `final_call_rule="tier_0_or_high_risk_or_dissent"` + mandatory floor hit | final_call | Enter (mandatory trigger union) |
| SM-17 | quorum_reached | `final_call_rule="tier_0_or_high_risk_or_dissent"` + no mandatory floors hit | resolved | Skip (mandatory floors absent) |
| SM-19 | final_call | Last ACK commits with live required authority, valid candidate, no concerns | resolved | `outcome=approved` |
| SM-20 | final_call | NACK with valid qualifying concern | final_call | Cosmetic feedback cannot veto |
| SM-21 | final_call | Timeout | forced_escalation | Same timeout mechanism |
| SM-22 | final_call | ACK from non-eligible actor | `InvalidMutationError` | Authorization check |
| SM-29 | final_call | Pre-authorization ACK retraction | final_call | Actor's ACK is removed, barrier remains held. |
| SM-30 | resolved | Post-authorization ACK retraction | resolved | Round cannot un-resolve; creates separate revocation-request adjacent record (fences future work, cannot undo completed effect). |
| SM-31 | final_call | Concurrent ACK and retraction race | serialized | Serialized via CAS. Loser gets `StaleRevisionError` and must re-read state. |
| SM-23 | timeout | Escalation accepted | resolved | Explicit outcome (approved/denied/exceptional). Only approving + valid proof permits execution. |
| SM-24 | timeout | Escalation → round reopened | voting | Back to voting; bound to a new stored deadline. |
| SM-25 | forced_escalation | Human/Arbiter resolves | resolved | Explicit outcome (approved/denied/exceptional). Only approving + valid proof permits execution. |
| SM-26 | forced_escalation | Human/Arbiter reopens | voting | Back to voting; bound to a new stored deadline. |
| SM-27 | resolved | Any mutation | `InvalidMutationError` | Terminal state |
| SM-28 | abandoned | Any mutation | `InvalidMutationError` | Terminal state |

---

## 10. Session Rotation State Machine

### 4.1 States (from SessionRotationSaga)

Already defined in the saga; this section adds the orchestrator's response to each:

| Saga result | Orchestrator action | CLI output |
|---|---|---|
| PROCEED_WITH_REUSE | Use existing session. No rotation. | (silent) |
| CHECKPOINT_PENDING | Issue checkpoint command, mark pending; proceed. | stderr: "Session pressure detected; checkpoint pending" |
| CHECKPOINT_REQUIRED | Issue checkpoint command before dispatch. All required checkpoint failures hold. | stderr: "Session pressure detected; checkpoint issued" |
| ROTATION_PENDING_PROCEED | Proceed but warn. | stderr: "Session pressure detected but rotation not safe; proceeding with current session" |
| ROTATION_CLAIMED | New session. Use new conversation_id. | stderr: "Session rotated (new conversation)" |
| ROTATION_IN_PROGRESS_RETRY | Back off. Retry after delay. | stderr: "Session rotation in progress; retrying..." |

**Session Guards**: Fresh and reuse cannot override verified capacity, fingerprint compatibility, ownership lease or required continuity; retain prepared-generation retry and stale-generation fencing.

### 4.2 Edge cases

| # | Case | Expected |
|---|---|---|
| SS-01 | First-ever dispatch (no session exists) | `fresh` creates one; `auto`/`reuse` create one implicitly via dispatch |
| SS-02 | `auto` + exact-attribution evidence + `soft <= p < hard` | CHECKPOINT_PENDING (checkpoint/mark pending at the soft-to-hard band) |
| SS-03 | `auto` + exact-attribution evidence + `p >= hard` + safe=true | ROTATION_CLAIMED |
| SS-04 | `auto` + exact-attribution evidence + `p >= hard` + safe=false | ROTATION_PENDING_PROCEED |
| SS-05 | `auto` + estimate-only evidence (unknown pressure) | PROCEED_WITH_REUSE (visibly unknown, follows permissive default) |
| SS-06 | `auto` + stale evidence (age > max_observation_age_ms) | PROCEED_WITH_REUSE (stale not trusted) |
| SS-07 | `fresh` regardless of any evidence | ROTATION_CLAIMED (or ROTATION_IN_PROGRESS_RETRY) |
| SS-08 | `reuse` + pressure above hard | CHECKPOINT_REQUIRED (checkpoint failure holds attempt) |
| SS-09 | Concurrent `ROTATION_CLAIMED` from two dispatches | Second gets ROTATION_IN_PROGRESS_RETRY (CAS conflict) |
| SS-10 | ROTATION_IN_PROGRESS_RETRY + max retries exhausted | Dispatch fails with clear error |

---

## 11. Quarantine Review Resolution

### 5.1 Pending-Effect Transitions

The resolution follows a strict pending-commit → idempotent-apply → reconcile-from-receipt sequence:
1. **Validate & Commit Pending**: Validate REQUESTED revision, target, and operator grant. Atomically commit the pending-effect identity and the review's pending disposition BEFORE applying the restriction.
2. **CAS Contention**: DISMISS and ESCALATE contend on the same review revision. If dismissal wins first, no pending effect commits. If pending-commit wins first, dismissal cannot later succeed as if still REQUESTED; any release must go through the separate authorized release path.
3. **Idempotent Application & Reconciliation**: Apply the effect idempotently, get its receipt, and reconcile the review from that receipt to final disposition.
4. **Crash Recovery**: Restart after pending-commit retries/reconciles that identity. Restart after application uses the receipt rather than inventing a new effect.

```
review target exists + status=REQUESTED
    ├── operator chooses DISMISS
    │   └── atomically validate revision → status = DISMISSED, no health action
    └── operator chooses ESCALATE
        ├── validate revision, target, and operator grant
        ├── atomically commit pending-effect identity + status = PENDING_ESCALATE
        └── apply effect (open_manual_quarantine)
            ├── get receipt
            └── reconcile from receipt → status = ESCALATED
```

### 5.2 Edge cases

| # | Case | Expected |
|---|---|---|
| QR-E-01 | DISMISS valid review | Status → DISMISSED |
| QR-E-02 | ESCALATE valid review, peer healthy | Peer quarantined via pending-effect transitions |
| QR-E-03 | ESCALATE valid review, peer already restricted | Identical delivery alone is a no-op; genuinely new security hold is never swallowed. |
| QR-E-04 | ESCALATE, peer_key missing from review target | InvalidMutationError |
| QR-E-05 | ESCALATE, peer_key present but node deregistered | RecordNotFoundError |
| QR-E-06 | ESCALATE, peer_kind malformed | InvalidMutationError |
| QR-E-07 | Resolve a review that's already resolved (DISMISSED or ESCALATED) | InvalidMutationError (not REQUESTED) |
| QR-E-08 | Resolve a nonexistent review_id | RecordNotFoundError |
| QR-E-09 | ESCALATE with invalid actor (no AuthenticatedSubject) | Authorization failure |
| QR-E-10 | Rapid ESCALATE → RECOVER sequence | Operational recovery does NOT release administrative quarantine; exact authorized restriction release is required to close. |
| QR-E-11 | Crash after PENDING_ESCALATE commit | Restart retries/reconciles that exact identity. |
| QR-E-12 | Crash after application, before review reconciliation | Restart uses the existing application receipt to reconcile review. |

---


## 12. Execution Certainty & Crash Fences (Grafted in R2b)

| # | Case | Expected |
|---|---|---|
| EX-01 | Cancellation requested *before* execution claim | Effect is `NOT_STARTED`. Cleanly cancelled. |
| EX-02 | Cancellation requested *after* execution claim (uncertain crash) | Effect marked `MAY_HAVE_STARTED` or `STARTED`. Replay requires idempotency validation. True outcome recorded. |
| EX-03 | Cancellation requested on `COMPLETED` effect | Rejected. Cannot overwrite completed history with cancelled fiction. |

## 13. Transport / IPC State Machine

### 6.1 Prompt staging lifecycle

```
Prompt received (inline text or --query-file)
    ├── size ≤ max_inline_bytes → pass inline to adapter
    └── size > max_inline_bytes → stage_prompt()
        ├── staging succeeds → scratch reference on AdapterRequest.prompt_reference
        │   ├── dispatch outcome DEFINITE (success/failure/cancel)
        │   │   └── remove_staged_prompt()
        │   └── dispatch outcome UNCERTAIN
        │       ├── in either retention_mode → clean up scratch copy when safe (consumed or consumer death confirmed)
        │       └── if retention_mode = retained → preserve separately-retained canonical input durably according to its TTL
        └── staging fails (disk error) → dispatch fails with clear error
```

### 6.2 Sweep lifecycle and Ownership
- **Managed Ownership**: Sweep uses managed-ownership plus no-further-consumer-use guards; age can select candidates, not authorize deletion.
- **Scratch vs Canonical**: Apply consumption/death cleanup to scratch copies in either retention mode; retained canonical bytes use their own protection/TTL/deletion policy.
- **Borrowed/Inline parity**: Borrowed-file non-deletion applies (cleanup cannot delete a borrowed source). Inline retention parity is maintained.
- **Cleanup Debt**: Cleanup debt is an explicit pending record/action maintained until the scratch file is removed.
- **Root Resolution**: `PRIVATE_VOLATILE_ROOT` is resolved relative to the OS temporary directory (or XDG_RUNTIME_DIR), guaranteeing a volatile, private path outside durable state.

**Acceptance**: Old-but-live or unknown-consumer scratch survives a sweep; safe consumption/death permits owned-copy cleanup; retained canonical input does not make scratch immortal; borrowed files survive. Missing input and uncertain earlier execution can coexist; existing exact input does not spuriously require re-entry; cleanup failure never reruns the effect.

### 6.3 Edge cases

| # | Case | Expected |
|---|---|---|
| TP-E-01 | `--query-file` with file exactly at max_inline_bytes | Inline (not staged) |
| TP-E-02 | `--query-file` with file at max_inline_bytes + 1 | Staged |
| TP-E-03 | Staging dir doesn't exist | Created by `stage_prompt()` (existing behavior) |
| TP-E-04 | Staging dir is read-only | `stage_prompt()` raises OSError → dispatch fails |
| TP-E-05 | Disk full during staging | `stage_prompt()` raises OSError → staged file cleaned up (existing behavior) |
| TP-E-06 | Same prompt staged twice (retry/broadcast child) | Distinct exclusively-created copy per retry/child. |
| TP-E-07 | Staged file modified externally between staging and read | Verify content identity before consumption; reject on mismatch. |
| TP-E-08 | Sweep runs during active dispatch | Sweeping by age alone is insufficient; file lives until consumed or consumer death confirmed. |
| TP-E-09 | `--query-file` path is a symlink | Followed (Python's `Path.read_text()` follows symlinks). No special handling — symlink is the user's responsibility. |
| TP-E-10 | `--query-file` path is a directory | `IsADirectoryError` → exit 2 |
| TP-E-11 | `--query-file` path is binary (non-UTF-8) | `UnicodeDecodeError` → exit 2 with "query file must be UTF-8" |
| TP-E-12 | Crash/uncertain outcome with `retention_mode="ephemeral"` | Preserve `INPUT_REQUIRED` only when exact authorized input is unavailable, independently of prior execution certainty. Staged file cleaned up only when safe (consumer death confirmed). |
| TP-E-13 | Crash/uncertain outcome with `retention_mode="retained"` | Separately-retained canonical input preserved until its TTL expires; scratch copy cleaned up when safe. Replay safety is dictated strictly by the certainty table (B4), not assumed. |

---

## 14. Health Consequence Decision Tree

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
| HC-06 | Dispatch outcome uncertain | Uncertainty forbids invented failure, but independently trusted evidence or authorized operator holds can still apply. |
| HC-07 | Multiple consecutive failures for same peer | Each creates a separate review target; quarantine is idempotent |
| HC-08 | Recovery attempted while circuit still closing | `authorize_recovery()` checks circuit state; may reject if evidence insufficient |
| HC-09 | Recovery probe from Epoch 1 arrives, but restriction is now at Epoch 2 | Probe is rejected as stale (`StaleAuthorityEpochError`). Circuit preserves its prior blocked state. |
| HC-10 | Shared-account restriction activated | Applies to all peer bindings under that account, not just the single profiled identity. |
| HC-11 | Profile-specific restriction activated | Sibling profiles remain active UNLESS blocked by a separate shared restriction. |

---

## 15. Telemetry Headroom Surface

### 8.1 Display tiers

| Surface | Content |
|---|---|
| `none` | Raw quota numbers only (existing behavior) |
| `basic` | Per-peer: used%, remaining%, window, freshness |
| `full` | basic + 24h fail count/rate, trend direction |

### 8.2 Data sources

| Datum | Source | Freshness requirement |
|---|---|---|
| Quota used% | `quota_polling.py` → vendor API | Real-time (polled) |
| Quota remaining% | Computed: 100% - used% | Real-time |
| Window duration | Vendor API response | Real-time |
| 24h fail count | New: dispatch failure counter in telemetry SQLite | Aggregated |
| 24h fail rate | Computed: failed / (succeeded + failed) | Aggregated |
| Trend | Computed: current window used% vs previous | Requires ≥ 2 data points |

### 8.3 Edge cases

| # | Case | Expected |
|---|---|---|
| TH-01 | No telemetry data at all | "No telemetry data" (not "0%") |
| TH-02 | Data for one peer only | Show that peer; "No data" for others |
| TH-03 | Stale data (age > max) | Show with `[stale: Xm ago]` tag |
| TH-04 | Vendor API returns error | Show `[error]` with last-known if available |
| TH-05 | 24h fail rate with zero total dispatches | "fail rate: no dispatches" (not "0%" or "NaN") |
| TH-06 | Trend with only 1 data point | "trend: insufficient data" |
| TH-07 | Telemetry tracking not populated yet | "fail rate: no data" |
| TH-08 | Rate limit window changed between polls | Detect via `windowDurationMins`; display both windows |
| TH-09 | ByLimitId-only or mixed malformed input | Valid/unknown-family input retains information; no-completion != 0%; reset != consumption trend |

---

## 16. Cross-Cutting Concerns

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
| Existing `dispatch-policy.toml` doesn't exist | Defaults map safely to current behavior or explicit purpose-aware floors (e.g. bounded children reuse approval). |
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

## 17. File Summary

This spec contains **135 enumerated test cases** across 9 categories:
- Policy resolution: 20 cases
- Consultation depth: 26 cases
- Consensus state machine: 31 cases
- Session rotation: 10 cases
- Quarantine review: 12 cases
- Execution certainty: 3 cases
- Transport/IPC: 13 cases
- Health consequence: 11 cases
- Telemetry headroom: 9 cases
Plus unnumbered cross-cutting examples.


---

