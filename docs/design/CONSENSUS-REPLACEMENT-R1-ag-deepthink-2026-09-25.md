# CONSENSUS-REPLACEMENT-R13-ag-deepthink-2026-09-25

**Status**: **RATIFIED** (2026-09-25, by cx.astra, Round 14 -- see docs/design/CONSENSUS-REPLACEMENT-R14-cx-astra-review-2026-09-25.md)

This document provides a concrete architectural design for fully replacing the legacy `ConsensusService` with the new policy-driven machinery. This Round 13 revision addresses activation atomicity, crash recovery, fixed-count type validation, required-set behavioral scoping, and coverage table edge cases from the Round 12 review.

---

## 1. Core Interface and Event Contracts (B1)

The pure `ConsensusStateMachine` evaluates exactly one event at a time, deterministically returning a `TransitionResult`. To ensure determinism, the `EvalContext` must freeze `current_timestamp`, `caller_identity`, and `frozen_authority_set` prior to evaluation.

### 1.1 Per-Event Contract
| Event Type | Source Phases | Payload / Evidence | Resulting Transition and Behavior |
|---|---|---|---|
| **Vote/Dissent** | `proposed`, `voting` | Actor, choice (`agree`, `abstain`, `need_more_info`, `block`, `disagree`), credential | Validates credential (Sec 2). Updates vote map. A change to `block` or `need_more_info` adds an unresolved dissent obligation (Sec 4). Ordinary `disagree` is processed natively without blocking auto-resolution. |
| **Correction** | `final_call` | Actor, choice (`block`) | Invalidates the active candidate, drops bound ACKs, transitions back to `voting` and requires fresh votes. |
| **Timeout** | `proposed`, `voting`, `quorum_reached`, `final_call` | `requester`, `deadline` | **Preserves** current phase, but records evidence and triggers policy re-evaluation. |
| **Escalation/Reopen** | Any non-terminal | `source_phases`, `replacement_deadline` | Authorized escalation/reopening event. Records new replacement deadline and triggers re-evaluation for timeout boundaries. |
| **Quorum Met** | `voting` | Agreement count | Internal auto-transition: -> `quorum_reached`. If mandatory Final Call union applies, -> `final_call`. |
| **ACK/NACK** | `final_call` | Candidate ID, Actor, Proof, NACK type | If ACK completes required set, -> `approved`. If NACK is a qualifying integrity/safety concern (`block`), **holds the barrier** (remains in `final_call`, preserving ratified SM-20 behavior). Cosmetic NACKs are logged. Terminal rejection -> `rejected`. |
| **Retraction** | `final_call` | Candidate ID, Actor, Proof | Pre-authorization: Removes ACK, invalidates candidate, -> `voting`. |
| **Arbiter Attachment** | `approved`, `rejected` | Verdict, profile, dispatch | Validates verified dispatch and matching profile. **First-valid-reference-wins.** Attaches evidence but strictly preserves the terminal decision phase. |
| **Resolution** | `quorum_reached`, `escalated` | Generic outcome | Normal auto-resolution -> `approved` or `rejected` based on configured rules. |
| **Exceptional Resolution**| Any non-terminal | Admin proof, bypass reason | Validates admin proof -> `approved` or `rejected`. |
| **Abandon** | Any non-terminal | Reason, requesting actor | -> `abandoned`. Retains preceding phase state and any accumulated effect data. |

---

## 2. Credentials and Health Boundaries (B2)

The shell injects a health/identity port from the composition root. Electorate checking uses a strictly named frozen authority set: `participants.eligible`.

### 2.1 Four-Way Credential Contract
- **Present:** Must always verify against the claimed actor. If it fails, or if no verifier is available to check it, **reject** the event.
- **Absent + Optional:** Proceed (asserted path).
- **Absent + Required:** **Reject** the event immediately, regardless of whether a verifier exists.

### 2.2 Electorate Gate and Frozen Membership
To evaluate "entire required electorate" (for reconciliation or Final Call), the system checks every member of the frozen `participants.eligible` set:
1. A missing registry node or missing health projection returns `False`.
2. Malformed identity returns `False`.
3. The live predicate requires `AvailabilityState != UNAVAILABLE|STALE`, `AdmissionState == OPEN`, and no backoff.
Exceptional authority bypasses this via a separately validated administrator proof.

---

## 3. Unified Quorum and Proposer Adapter (B3)

A single pure quorum evaluator handles both direct and proposal-based rounds. To safely interface with `process_quorum_round` without double-counting or breaking its internal logic, the adapter conditionally branches based on the active formula.

### 3.1 Proposer-Sensitive Counting and Binding Floor

The effective unanimous required set is kept independent of whether the proposer actually voted, enforcing the frozen required membership.

**Authorized Voting Universe:** An actor outside the `participants.eligible` set is STRICTLY REJECTED and cannot cast a vote. A proposer who is not eligible cannot agree or contribute to `agree_count`.

**Binding Direct-Path Floor:** The direct path must also enforce a universal minimum floor independent of formula arithmetic: at least two independent logical agreeing voters are required on every binding path (e.g. if the proposer is outside `eligible`, 2 eligible non-proposer votes are required).

### 3.1.1 B3 Production-Code Correction (Unanimous Semantic)

To align the internal inconsistent test suite and `process_quorum_round` logic to the design intent, `unanimous` now adopts the explicit CD-U-05 convention: `total_voters` ALWAYS strictly excludes the proposer, and the proposer's agreement is unconditionally required.

**Required `process_quorum_round` change:**
For `unanimous`, `required_agrees = total_voters + 1` unconditionally (it is no longer `total_voters + int(proposer_voted)`). This guarantees that a proposer abstaining, going unreachable, or timing out does not incorrectly decrease the requirement from 3 to 2, which previously caused a false `check_final_call` pass.

**Required test updates (`tests/unit/dispatch/test_orchestrator_consultation.py`):**
The legacy tests `CD-U-01` through `CD-U-04` must be updated to match the corrected `unanimous` convention without changing their expected behavioral outcomes:
- `test_cd_u_01_unanimous_all_agree`: currently passes `total_voters=3` without `proposer_voted=True`. Must be updated to pass `total_voters=2` and `proposer_voted=True`. Outcome: `check_final_call`.
- `test_cd_u_02_...`: updated to pass `total_voters=2`, `proposer_voted=False` (abstention). Outcome: `pending` (was previously failing or silently expecting quorum).
- `test_cd_u_03_...`/`04`: same pattern, always supplying explicit `proposer_voted` and subtracting 1 from `total_voters`.

**Worked Examples Verified Against Corrected `process_quorum_round`:**
1. **Formula `unanimous` (3 eligible total, proposer inside):** 
   - *Proposer agrees:* Adapter passes `total_voters = 2`, `proposer_voted = True`, `agree_count = 3`. Formula requires `2 + 1 = 3`. Condition `(3 >= 3)` passes. Quorum reached.
   - *Proposer abstains:* Adapter passes `total_voters = 2`, `proposer_voted = False`, `agree_count = 2`. Formula requires `2 + 1 = 3`. Condition `(2 >= 3)` fails. Remains pending.
2. **Formula `all_required` (3 eligible total, proposer inside):**
   - *Proposer agrees:* Adapter passes `total_voters = 3`, `proposer_voted = True`, `agree_count = 3`. Formula requires `agree_count == 3`. Quorum reached.
   - *Proposer abstains:* Adapter passes `total_voters = 3`, `proposer_voted = False`, `agree_count = 2`. Formula requires `agree_count == 3`. Quorum not reached.
3. **Formula `majority` (3 eligible total, proposer inside):**
   - *Proposer agrees:* Adapter passes `total_voters = 3`, `proposer_voted = True`, `agree_count = 2` (1 other agrees). Formula requires `2 > 3 / 2` (1.5). Quorum reached.
   - *Proposer abstains:* Adapter passes `total_voters = 3`, `proposer_voted = False`, `agree_count = 2` (2 others agree). Formula requires `2 > 3 / 2`. Quorum reached.
4. **Formula `supermajority` (4 eligible total, proposer inside):**
   - *Proposer agrees:* Adapter passes `total_voters = 4`, `proposer_voted = True`, `agree_count = 3` (2 others agree). Formula requires `3 >= ceil(2*4/3)` (3). Quorum reached.
   - *Proposer abstains:* Adapter passes `total_voters = 4`, `proposer_voted = False`, `agree_count = 2` (2 others agree). Formula requires `2 >= 3`. Quorum not reached.

### 3.2 Duplicate and Correction Dispositions
Duplicate identical votes are no-ops. Changed votes from `True` to `False` (dissent) are processed explicitly as `correction` events: the old contribution is invalidated, and any active `final_call` candidate is immediately invalidated, returning the round to `voting` and requiring fresh votes.

---

## 4. Risk, Policy, and Snapshot Execution (B4)

### 4.1 Policy Freezing and Saved Snapshot Identity
Configuration is evaluated exactly once at round creation. The authoritative snapshot identity must store and validate:
- The active formula and fixed-count (`quorum.required_votes`)
- Explicit Final Call rule (`never`, `always`, etc.)
- Deadlines, escalation paths, and risk derivation
- Complete `action` and `origin`

This is saved as a versioned `policy_snapshot`. Any subsequent restart decoding that finds inconsistent snapshots or missing versions fails closed (`ConfigurationError`). Explicitly, if the underlying TOML configuration is unreadable (e.g., `OSError`), the system must fail closed immediately, rejecting the read rather than returning an empty configuration `{}`.

### 4.2 Exact Risk-to-Depth Mapping and Mandatory Final Call
Risk inputs strictly dictate the depth floor and execution path:
- **Risk-to-Depth**: Action overrides and risk apply to consultation depth. The effective required depth is `max(configured_depth, risk_floor)`. `risk_floor` is explicitly sourced from the authoritative configuration block `governance.policy.risk_floors` (mapping risk strings like `high` to depth floors like `QUORUM`). Any unknown or missing risk input safely defaults to `QUORUM`. This ensures both restart and creation paths select the identical minimum depth.
- **Mandatory Final Call Union**: The condition `tier-0 OR high-risk OR unresolved dissent` enforces a **mandatory Final Call**, explicitly overriding `final_call_rule="never"`. This applies universally across all creation paths (native, direct, and proposal). This obligation is durably retained in the snapshot; new concern obligations update this state without re-resolving external configuration.

### 4.3 Per-Action Selection
The orchestrator selects consensus policy using the explicit `action` override. This action override adjusts consultation depth and constructs a shared consensus policy, distinguishing a proposal's strict unanimous/reject-on-dissent requirements from general majority behavior. Proposal overrides (`proposals.create`) are evaluated first, falling back to specific action keys, and finally the global default.

---

## 5. Authority Fencing and Final Call (B5)

### 5.1 Shared Transaction/Version Fence
The shell's revalidation must share a transactional boundary with authority changes to prevent race conditions.
- **The Record**: A durable, monotonic `global_authority_version` (integer) is stored in the system repository. It is initialized to `1` and updated atomically (incremented) by any authority-changing writer (revocations, rebindings, live health transitions). ABA and lost increments are prevented by strict monotonic DB increment within `BEGIN IMMEDIATE` transactions.
- **The Check**: The mutation request, approval snapshot, and effect claim must all carry the `expected_authority_version`. Within the single `BEGIN IMMEDIATE` broker transaction, strictly before the round CAS and before the effect claim, the broker must read the live `global_authority_version`.
- If the live version `!= expected_authority_version`, the transaction fails, rejecting the CAS or effect claim and forcing a re-authorization.

### 5.2 Candidate Invalidation and Serial Orders
- A candidate is uniquely identified by `(round_id, expected_revision, target_state_hash)`.
- **Pre-authorization retraction (Revocation-first)**: Drops the ACK and returns to `voting`.
- **Post-authorization retraction (Revocation-first)**: Authorization has already committed. The retraction creates a separate revocation request that fences future work but does not undo completed side effects. The event is successfully recorded as a revocation, rather than being rejected.
- **Claim Execution (Pre-invocation recheck)**: A claim is not proof that an effect ran. The broker/effect materializer must perform a live recheck of authorization strictly before invoking the adapter. If the recheck fails or a revocation is detected, it applies best-effort cancellation.
- Any new dissent (`correction`), live health/expiry validation failure, authority revocation, or `participants.eligible` membership change invalidates the candidate and all bound ACKs.
- Time-dependent credential/health validty checks are evaluated at commit/claim time; clock expiry does not cause a version-changing write, but freshness is evaluated at read time.
- Stale identical claims (same candidate and same effect already applied) exit early without error, ensuring idempotency. An early return branch for a claim submitted against an already advanced authority target is explicitly handled and rejected.

### 5.3 Public Events and Stable Binding
ACK, NACK, and Retraction are exposed as distinct public event entrances, explicitly avoiding generic vote overloading. Replay uses a durable semantic command-to-receipt binding: a stable public command lookup keyed by `client_id`, `command_type`, and `idempotency_key` (rather than just an opaque `mutation_id`). The broker ensures that the approval state, receipt, and effect outbox are preserved together in a single atomic commit.

---

## 6. Proposal Semantics and Effect Recovery (B6)

### 6.1 Explicit Policy Dispositions and Precedence
The core implements the exact proposal precedence:
1. Resolved recovery
2. Existing escalation (from prior timeouts)
3. Too few eligible voters (`len(participants.eligible) < 2` using the complete electorate count, distinct from the adapter's `total_voters`)
4. Any eligible gate closure (missing/stale health)
5. Eligible dissent rejection (**unconditionally** rejects any eligible dissent, preserving the current proposal reconciliation policy)
6. All required agree + independent agreement (triggers `final_call` or approval; requires every required voter to agree)
7. Proposer-only self-finalization escalation

### 6.2 Exclusive Effect Routing and Atomic Submission
High-risk proposal Final Call explicitly replaces auto-resolution and maps its ingress to the native public ACK event.
- **Atomic Submission Guarantee**: The final approval state transition and the generated invariant effect must share a single broker submission (one state/receipt/outbox commit). This is guaranteed on immediate, exceptional, and last-ACK approval, including evaluation after restart.
- **Factory Routing**: The `RATIFIED_INVARIANT_EFFECT_KIND` factory is supplied the final approved snapshot directly via the core's `effect_intent`, joining its result to the approval mutation.
- **Materializer Exclusivity**: The generic `process_consensus_effects()` worker is strictly forbidden from claiming `RATIFIED_INVARIANT_EFFECT_KIND`. It is routed exclusively to the invariant materializer.
- Unknown effect kinds hold and error. `CONSENSUS_OK`, `NACK`, `ESCALATED`, and request ID are preserved to ensure exactly-one-target recovery.

---

## 7. Schema Provenance and V2 Adapter (B7)

### 7.1 Creation Provenance and Action Classification
V1/V2 schema tags indicate structural format, not origin. The V2 schema introduces explicit, durable `origin` and `action` fields that must be populated on creation. Since `ProposalCoordinator.add_proposal()` internally calls `ConsensusService.propose()` (a nested call), provenance must be preserved:
- **Proposal Path (`ProposalCoordinator.add_proposal()`)**: The outer caller mandatorily sets trusted origin (`origin: "proposals"`) and exact action (`action: "governance.proposal.create"`). The inner `propose()` call receives and strictly preserves this explicitly passed origin/action without overwriting it.
- **Direct Path (`ConsensusService.propose()` as entrypoint)**: When called directly without an injected origin, it defaults to `origin: "direct"` and exact action `action: "consensus.round.propose"`.

The combination of `origin` and `action` directly constructs the `action_name` policy resolver key. Origin is never inferred from shared V1 keys. Ambiguous records hold for manual administrator disposition.

### 7.2 Read-Time Adapter and V1 Migration
The V2 adapter intercepts all reads, mutations, and recoveries (including broker-backed proposal list enumerations):
- **V1 Mapping**: V1 rounds lack an origin field and share the same `operation` key. The migration mapping must NOT guess origin based on shared keys. Genuinely indistinguishable V1 records are placed in an ambiguity hold state requiring explicit administrator mapping. Identifiable V1 records use an explicit, independently evidenced per-round migration mapping.
- **Fixed Count Validation**: The `quorum.required_votes` threshold must be strictly validated at snapshot creation. The system must explicitly reject non-integer values (including booleans, which subclass `int` in Python, fractional, and string values), as well as zero or negative thresholds. It must enforce the independent 2-agreement minimum floor.
- **Fixed Count Contract (V1 vs V2 Scope)**: 
  - **NEW (V2-Native) Rounds**: Fixed-count identity evaluation explicitly allows agreement from *any* eligible voter in `participants.eligible`, bypassing required-voter assertions. The counted `agree_count` must be `>= required_votes`.
  - **V1-Migrated/Upcast Rounds**: To prevent silent behavioral changes to in-flight or historical rounds, legacy rounds MUST retain their original required-set semantics. The adapter must strictly count only voters explicitly defined in the legacy required set; a non-required eligible voter cannot substitute for a required one.
  - For both, if `required_votes` exceeds the available counting pool (`len(participants.eligible)` or `len(required_set)` respectively), the state is unattainable and the round is rejected immediately (`ConfigurationError`). Dissent does not automatically reject the round unless `block` is chosen, preserving standard resolution unless `required_votes` becomes mathematically impossible.
- Historical hashes, receipts, and original approval identities are explicitly preserved. Pure reads perform in-memory upcasting without broker writes, maintaining the original revision.
- Unknown or inconsistent schema versions fail closed (`SchemaError`).

---

## 8. Cutover Protocol and Coverage Matrix (B8)

### 8.1 Fencing and Legacy Equivalent Contract
An enforced **writer-generation gate** handles cutover.
- **Generation Token & Exclusive Boundary**: Because legacy V1 brokers bypass generation checks directly to SQLite, a metadata marker alone cannot fence running V1 code, nor can a new workspace epoch alone prevent a freshly restarted V1 service from writing to a V2-marked target using the old CAS.
- **Atomic Activation & Storage Restriction**: To guarantee old V1 code is definitively fenced, the V2 activation must be a SINGLE atomic transaction/commit containing three operations together: the schema rename (e.g., from `governed_targets` to `governed_targets_v1`), the epoch increment, AND the activation metadata update. If the process crashes mid-activation, standard SQLite transaction rollback guarantees that upon restart, the system will detect either a fully completed activation (V2 schema present, epoch bumped, metadata written) or a fully rolled-back state (V1 schema intact), allowing it to safely re-attempt the activation. A freshly reopened V1 runtime that attempts to write after a committed activation will encounter hard SQL `OperationalError`s (table not found) and definitively crash, preventing any bypass.
- **Legacy Contract Boundary**: For legacy rounds in `final_call`, unbound legacy ACKs are retained under a **frozen legacy contract evaluator**. The candidate boundary is frozen to the legacy eligible set. New mandatory floors do not retroactively apply to these frozen candidates. Completion requires a legacy-equivalent ACK event.

### 8.2 Phase-to-Disposition Table and Recovery
Legacy generic resolution stored `phase="resolved"`, not `approved`. The exact mapping:
| Phase / State at Cutover | Migration Disposition & Recovery |
|---|---|
| `proposed` / `voting` | Upcast to V2 `voting`; evaluate via new core. |
| `quorum_reached` | Upcast; evaluate floor. If floor requires Final Call, -> `final_call`. |
| `final_call` (partial) | Retain unbound ACKs; evaluate via frozen legacy contract until completion. |
| `resolved` / timeout | Maps to V2 `approved`, `rejected`, or `escalated` based on generic outcome and recorded timeout evidence. |
| Pending / Target Unmaterialized | Preserve the exact `approved_revision`, immutable approval snapshot, and `participants` to satisfy proposal recovery. This isolates recovery from the live state, which arbiter attachments would drift. Re-route to exclusive materializer. |
| Claimed with no target / Target Materialized without result | Differentiated from lost-responses. Evaluated explicitly using exact immutable target/result evidence from `project_event`. Never forces execution after intervening revocation. |
| Materialized / Consumed | Preserved as terminal `completed-effect` in the invariant projector; no re-execution. A surviving approval receipt is never treated as a completed effect unless corroborated by the result log. |

### 8.3 Complete Call-Path Map
All callers are explicitly mapped to the V2 evaluator and its adapters:
- **Direct/Native Handlers**: Handlers (`propose`, `cast_vote`, `mark_timeout`) map to pure V2 events.
- **Proposals**: `create`/`vote`/`reconcile`/`list` use the unified adapter, preserving their precedence (Sec 6.1).
- **Arbiter Attachment**: Native adapter attaches verdict and increments revision. The immutable approval snapshot isolates effect recovery from this increment.
- **Effect Recovery**: Adapter reads the preserved `approved_revision` and public command lookup.

### 8.4 Concrete Coupled Coverage Rows
| Coupled Case | Expected Durable Record | Applied Effects | Outbox Intents |
|---|---|---|---|
| Missing credential with installed verifier | `InvalidMutationError` / Rejection | 0 | 0 |
| **Revocation-first**: Rejection before approval | Rejection before intent creation; no approval snapshot | 0 | 0 |
| Authority change before claim | Claim expected version fails; forces re-authorize but intent survives | 0 | 1 |
| Approval, then revocation before invocation | Pre-invocation recheck fails; execution cancelled | 0 | 1 |
| Approval, execution completes, then revocation | Effect completed normally; revocation recorded later | 1 | 1 |
| **Retraction wins**, then Last ACK | Retraction drops ACK, candidate invalid; ACK rejected | 0 | 0 |
| Claimed with no target (Recovery) | Claim revalidates authorization before target creation | 0 | 1 |
| Target present, no result (Recovery) | Validates immutable target; settles bookkeeping | UNCERTAIN | 1 |
| Uncertain adapter execution | Target preserved; state marked UNCERTAIN | UNCERTAIN (0-1) | 1 |
| Terminal approval + arbiter attachment (pending execution) | `approved`, revision+1, isolated approval snapshot | 0 | 1 |
| Terminal approval + arbiter attachment (completed execution) | Explicit completed-effect evidence; arbiter attached | 1 | 1 |
| Old-writer cutover attempt (reopened) | Hard SQL schema error (`OperationalError`) -> crashed | 0 | 0 |
