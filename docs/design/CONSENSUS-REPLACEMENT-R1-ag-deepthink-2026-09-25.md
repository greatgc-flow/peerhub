# CONSENSUS-REPLACEMENT-R1-ag-deepthink-2026-09-25

**Status**: **PROPOSED** (Round 7)

This document provides a concrete architectural design for fully replacing the legacy `ConsensusService` with the new policy-driven machinery. This Round 7 revision addresses the remaining arithmetic and authority gaps from the Round 6 review.

---

## 1. Core Interface and Event Contracts (B1)

The pure `ConsensusStateMachine` evaluates exactly one event at a time, deterministically returning a `TransitionResult`. To ensure determinism, the `EvalContext` must freeze `current_timestamp`, `caller_identity`, and `frozen_authority_set` prior to evaluation.

### 1.1 Per-Event Contract
| Event Type | Source Phases | Payload / Evidence | Resulting Transition and Behavior |
|---|---|---|---|
| **Vote/Dissent** | `proposed`, `voting` | Actor, choice (`agree`, `abstain`, `need_more_info`, `block`), credential | Validates credential (Sec 2). Updates vote map. A change to `block` or `need_more_info` adds an unresolved dissent obligation (Sec 4). |
| **Correction** | `final_call` | Actor, choice (`block`) | Invalidates the active candidate, drops bound ACKs, transitions back to `voting` and requires fresh votes. |
| **Timeout** | `proposed`, `voting`, `quorum_reached`, `final_call` | `requester`, `deadline` | **Preserves** current phase (narrowed timeout semantics), but records evidence and triggers policy re-evaluation (escalation/reopening with deadlines). |
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
The adapter defines the inputs to the orchestrator to match its inconsistent internal counting contract:
- **`total_voters`**: For `majority`, `supermajority`, and `all_required`, this is the count of the `participants.eligible` set **including** the proposer. For `unanimous` ONLY, this is the count of the `participants.eligible` set **excluding** the proposer (because the function adds the proposer back itself).
- **`proposer_voted`**: `True` if the proposer has cast an agreeing vote.
- **`non_proposer_voted`**: `True` if at least one eligible voter *other* than the proposer has agreed (enforces independence).
- **`agree_count`**: The total sum of non-retracted `True` votes from eligible voters (always includes the proposer's vote if cast).

**Authorized Voting Universe:** An actor outside the `participants.eligible` set is STRICTLY REJECTED and cannot cast a vote. A proposer who is not eligible cannot agree or contribute to `agree_count`.

**Worked Examples Verified Against `process_quorum_round`:**
1. **Formula `unanimous` (3 eligible total, proposer inside):** 
   - *Proposer agrees:* Adapter passes `total_voters = 2`, `proposer_voted = True`, `agree_count = 3`. Formula requires `2 + 1 = 3`. Condition `(3 >= 2)` passes. Quorum reached.
   - *Proposer abstains:* Adapter passes `total_voters = 2`, `proposer_voted = False`, `agree_count = 2`. Formula requires `2 + 0 = 2`. Condition `(2 >= 2)` passes. Quorum reached.
2. **Formula `all_required` (3 eligible total, proposer inside):**
   - *Proposer agrees:* Adapter passes `total_voters = 3`, `proposer_voted = True`, `agree_count = 3`. Formula requires `agree_count == 3`. Quorum reached. (Fixes CD-Q-01 pending bug).
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
- **Risk-to-Depth**: Action overrides and risk apply to consultation depth. The effective required depth is `max(configured_depth, risk_floor)`.
- **Mandatory Final Call Union**: The condition `tier-0 OR high-risk OR unresolved dissent` enforces a **mandatory Final Call**, explicitly overriding `final_call_rule="never"`. This applies universally across all creation paths (native, direct, and proposal). This obligation is durably retained in the snapshot; new concern obligations update this state without re-resolving external configuration.

### 4.3 Per-Action Selection
The orchestrator selects consensus policy using the explicit `action` override. This action override adjusts consultation depth and constructs a shared consensus policy, distinguishing a proposal's strict unanimous/reject-on-dissent requirements from general majority behavior based on established precedence.

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
- **Post-authorization revocation (Claim-first)**: Increments `global_authority_version`. When the effect claim attempts to commit, its expected version check fails against the live version, forcing a re-authorization.
- Any new dissent (`correction`), live health/expiry validation failure, authority revocation, or `participants.eligible` membership change invalidates the candidate and all bound ACKs.
- Stale identical claims (same candidate and same effect already applied) exit early without error, ensuring idempotency.

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
V1/V2 schema tags indicate structural format, not origin. The V2 schema introduces explicit, durable `origin` and `action` fields that must be populated on creation:
- **Proposal Path (`ProposalCoordinator.add_proposal()`)**: Must mandatorily set `origin: "proposals"`, `action: "<proposal_type_action>"`.
- **Direct Path (`ConsensusService.propose()`)**: Must mandatorily set `origin: "direct"`, `action: "<direct_handler_action>"`.

The combination of `origin` and `action` directly constructs the `action_name` policy resolver key (e.g., `"proposals.create"`). Origin is never inferred. Ambiguous records hold for manual administrator disposition.

### 7.2 Read-Time Adapter and V1 Migration
The V2 adapter intercepts all reads, mutations, and recoveries (including broker-backed proposal list enumerations):
- **V1 Mapping**: V1 rounds lack an origin field and share the same `operation` key. The migration mapping must NOT guess origin based on shared keys. Genuinely indistinguishable V1 records are placed in an ambiguity hold state requiring explicit administrator mapping. Identifiable V1 records use an explicit, independently evidenced per-round migration mapping.
- **Fixed Count Contract**: The legacy fixed-count contract uses `quorum.required_votes` as authoritative when it disagrees with `participants.quorum.required`. The adapter must apply its fixed-count and identity validation contract.
- Historical hashes, receipts, and original approval identities are explicitly preserved. Pure reads perform in-memory upcasting without broker writes, maintaining the original revision.
- Unknown or inconsistent schema versions fail closed (`SchemaError`).

---

## 8. Cutover Protocol and Coverage Matrix (B8)

### 8.1 Fencing and Legacy Equivalent Contract
An enforced **writer-generation gate** handles cutover.
- **Generation Token**: A monotonically increasing `engine_generation` field is added to the system repository and broker CAS target. V1 processes carry `engine_generation=1`, V2 carries `2`.
- **Enforcement**: The broker CAS enforces that `request.generation >= target.generation`. An old V1 writer attempting to commit over a V2-claimed round will fail CAS immediately (stale-process rejection). The activation order upgrades the schema and generation token first, fencing out all running V1 mutations.
- **Legacy Contract Boundary**: For legacy rounds in `final_call`, unbound legacy ACKs are retained under a **frozen legacy contract evaluator**. The candidate boundary is frozen to the legacy eligible set. New mandatory floors do not retroactively apply to these frozen candidates. Completion requires a legacy-equivalent ACK event.

### 8.2 Phase-to-Disposition Table and Recovery
Legacy generic resolution stored `phase="resolved"`, not `approved`. The exact mapping:
| Phase / State at Cutover | Migration Disposition & Recovery |
|---|---|
| `proposed` / `voting` | Upcast to V2 `voting`; evaluate via new core. |
| `quorum_reached` | Upcast; evaluate floor. If floor requires Final Call, -> `final_call`. |
| `final_call` (partial) | Retain unbound ACKs; evaluate via frozen legacy contract until completion. |
| `resolved` / timeout | Maps to V2 `approved`, `rejected`, or `escalated` based on generic outcome and recorded timeout evidence. |
| Pending Effect / Claimed | Preserve the exact `approved_revision`, immutable approval snapshot, and `participants` to satisfy proposal recovery. This isolates recovery from the live state, which arbiter attachments would drift. Re-route to exclusive materializer. |
| Materialized / Consumed | Preserved as terminal `completed-effect` in the invariant projector; no re-execution. |

### 8.3 Complete Call-Path Map
All callers are explicitly mapped to the V2 evaluator and its adapters:
- **Direct/Native Handlers**: Handlers (`propose`, `cast_vote`, `mark_timeout`) map to pure V2 events.
- **Proposals**: `create`/`vote`/`reconcile`/`list` use the unified adapter, preserving their precedence (Sec 6.1).
- **Arbiter Attachment**: Native adapter attaches verdict and increments revision. The immutable approval snapshot isolates effect recovery from this increment.
- **Effect Recovery**: Adapter reads the preserved `approved_revision` and public command lookup.

### 8.4 Concrete Coupled Coverage Rows
| Coupled Case | Expected Durable Record | Effect Count |
|---|---|---|
| Missing credential with installed verifier | `InvalidMutationError` / Rejection | 0 |
| **Revocation-first**: Authority change before claim | Claim expected version fails; forces re-authorize | 0 |
| **Claim-first**: Claim wins, then authority change | Effect applied, increment subsequent version | 1 |
| **Last ACK wins**, then retraction | Retraction rejected (candidate already approved) | 1 |
| **Retraction wins**, then Last ACK | Retraction drops ACK, candidate invalid; ACK rejected | 0 |
| Origin ambiguity (V1 missing mapping) | Hold status; admin alert | 0 |
| Terminal approval + arbiter attachment | `approved`, revision+1, isolated approval snapshot | 1 |
| Lost response / Claimed without result | Find original receipt via public command idemp key | 1 |
| Old-writer cutover attempt | Broker generation mismatch (`1 < 2`) -> rejected | 0 |
