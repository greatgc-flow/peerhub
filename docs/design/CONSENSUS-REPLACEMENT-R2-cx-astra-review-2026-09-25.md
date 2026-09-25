# Round 2 cross-review: full consensus-engine replacement

Author: cx.astra. Date: 2026-09-25. **Verdict: REJECT. The structural direction is acceptable; the replacement contract is incomplete and contains concrete regressions.**

A persistence-aware shell around deterministic consensus logic is achievable. Keeping the public `ConsensusService` facade is also compatible with full replacement. Round 1 does not yet specify that replacement: it describes an interface the current state machine does not implement, omits much of the service's behavior, weakens one credential rule, and cannot reliably distinguish the persisted round types it proposes migrating. These require design decisions, not just implementation detail.

This is a design review. Only this review document was added. No implementation/configuration change, commit, activation, or peer dispatch was performed.

## 1. Review basis

I read the [Round 1 proposal](CONSENSUS-REPLACEMENT-R1-ag-deepthink-2026-09-25.md) first, then all six requested source files, the [Round 3 review](GOVERNANCE-DISPATCH-R3-cx-astra-ratification-2026-09-24.md), the [Round 10 final ratification](GOVERNANCE-DISPATCH-R10-cx-astra-FINAL-ratification-2026-09-24.md), and the relevant final specification, broker, gateway, effect projector, production callers, and tests.

- **R1** means the 138-line proposal, SHA-256 `857067545dc32120e2988c860825378140ab3e1a8182e5200006fd75047a842d`.
- **Source baseline:** HEAD `490ce480bfad66092429a86a13b1b7e1550423fe`; the proposal was untracked, and tracked source had no working-tree changes at inspection.
- Source citations below give file links and exact line spans in prose. They describe the inspected implementation, not inferred runtime deployment.
- **Measured checks [empirical_probe]:** 42 tests passed in the existing orchestrator-consultation and consensus-policy test files. Direct in-memory probes confirmed the quorum examples, invalid optional credential rejection, unreadable-TOML fallback, unsupported `custom` formula, absent consensus health helper, and ACK phase-guard gap described below. A broader run including resolver/quarantine tests encountered `PermissionError [WinError 5]` in pytest's sandbox temp directory and did not produce a clean verification result. No concurrency, crash-recovery, or deployed authorization guarantee is claimed verified by these probes.

## 2. Explicit votes on the requested sections

| R1 section | Vote | Finding |
|---|---|---|
| 1: persistence boundary | Accept the pattern; reject completeness claim | The broker supports atomic state/receipt/outbox writes. The state machine does not accept/return round dictionaries, and most service operations have no assigned replacement. B1/B5. |
| 2: authorization | Reject as specified | The named helper is in `ProposalCoordinator`, not `ConsensusService`. Optional-but-present credentials must still verify. Checking the caller alone does not preserve the proposal path's whole-electorate gate. B2. |
| 3: quorum/risk | Partial numerical agreement; reject the proposed contract | The first three thresholds match for valid counts. `unanimous`, proposer inclusion, equality predicates, floors, and actual risk/policy wiring are unresolved. B3/B4. |
| 4: Final Call | Reject as sufficient | Authorization may live in a shell, but ACK membership, candidate identity, all-required live authority, atomic effects, and revocation fencing remain unspecified. Round CAS alone does not fence external authority changes. B5. |
| 5: legacy unification | Reject equivalence claim | `never` only addresses the optional barrier; it does not replace independence, escalation priority, dissent handling, or invariant-request materialization. B6. |
| 6: JIT upcasting | Reject proposed classifier | Both creation paths use the same constructor and include `verified_required`. Content/phase shapes cannot establish provenance. `custom` is not a supported formula. B7. |
| 7: MECE cases | Reject sufficiency claim | Ten mixed-axis examples are not a partition and omit rolling cutover, existing ACKs, pending effects, authorization races, and response-loss recovery. B8. |
| Full replacement | Not demonstrated | Keeping a facade is fine; retaining its old decision branches or a second proposal resolver is not closure. B1/B8 supply the bounded inventory needed. |

## 3. Blocking objections and required amendments

### B1. Specify the actual core interface and assign every existing responsibility

**Location:** R1 lines 11-17, 21, 74-86, 101-102.

`ConsensusStateMachine` is free of broker I/O, but it is not stateless: it mutates `self.state`, `deadline_extended`, and `revocation_record_created`. Its constructor accepts a phase string. `process_ack()` accepts booleans, returns `"approved"` or `None`, and does not receive an actor, decision, round dictionary, votes, ACK map, timestamp, revision, or effect payload. There is no `propose` or `cast_vote` transition. R1's “persist the exact dictionary returned” interface therefore requires a substantial new implementation, not straightforward delegation. [consensus.py](../../peerhub/governance/consensus.py), lines 830-874.

The quarantine analogy establishes only the usefulness of a shell. Its actual path performs validation, commits `PENDING_ESCALATE`, applies/reuses a health restriction, then commits a receipt-bearing `ESCALATED` state, with a separate restart reconciler. It is not calling a pure round reducer. The corrected pending/apply/reconcile sequence is precisely why “compute then persist” is an insufficient description of cross-domain work. [quarantine_review.py](../../peerhub/application/quarantine_review.py), lines 101-162, 173-238. This observation does not reopen the old bug as if its earlier code were still present.

**Required amendment:** Define an operation/event contract, a validated immutable round snapshot, injected time/IDs/evidence, and an explicit transition result containing next state, outcome/receipt data, and effect intent. Name which component owns each predicate; “state machine or orchestrator” cannot leave two evaluators authoritative. A per-operation object that performs no I/O can work, but do not reuse mutated instances across rounds or failed CAS attempts.

Include this entire existing surface in the replacement disposition:

| Existing responsibility | Source | Required owner/decision |
|---|---|---|
| Formation, electorate and initial envelope | `consensus.py` 64-134 | Resolver/shell gather and freeze inputs; core validates formation. |
| Votes, tally, dissent, vote replacement | `consensus.py` 527-639 | One authoritative core/orchestrator path; specify replacement/correction semantics. |
| Final Call and approval | `consensus.py` 154-234 | Candidate/ACK reducer plus atomic authorization/effect commit. |
| Timeout evidence and escalation | `consensus.py` 236-294 | Preserve evidence, requester, authority and deadline, or explicitly ratify changed semantics. |
| General resolution and dissent rejection | `consensus.py` 323-445 | Define approved/denied/exceptional outcomes and mandatory gates for every entrance. |
| Abandonment | `consensus.py` 447-484 | Preserve reason, preceding phase, terminal status and abandonment effect. |
| Effect claim/result/recovery | `consensus.py` 486-525 | Explicit effect-kind ownership and restart behavior; see B6. |
| Canonical arbiter evidence | `consensus.py` 641-764 | Preserve identity/profile/verified-dispatch/verdict validation, resolved-only attachment and first-valid-reference wins. |
| Reads, audit, hashes and mutation envelope | `consensus.py` 52-55, 136-152, 296-321, 766-792 | One schema boundary and durable receipt contract, including terminal reads/evidence updates. |

For example, today's `mark_timeout()` records evidence/escalation without changing phase; the new stub's `process_timeout()` changes phase to `forced_escalation`. Today's arbiter method attaches evidence only after resolution and never changes the decision. Neither equivalence may be assumed from similarly named methods.

**Acceptance:** Every row names its replacement owner, reachable entrypoint, behavior preserved or deliberately changed, and boundary test. No old approval/tally branch survives outside the designated evaluator. Keeping generic broker infrastructure and a compatibility facade is permitted.

### B2. Preserve the real credential rule and distinguish membership, health and authority

**Location:** R1 lines 33-46; INT-02/03/10.

Today both `cast_vote()` and `final_call_ack()` enforce eligibility and this credential predicate: if a credential is supplied, it must verify for the claimed actor, even when `verified_required=False`; otherwise, missing credentials reject only when required. R1 line 44 describes verification only when the round requires it. Implemented literally, that permits invalid supplied credentials on optional rounds. The gateway follows the same no-fallback rule, but the direct service check remains necessary. [consensus.py](../../peerhub/governance/consensus.py), lines 166-177, 544-569; [governance_authorizer.py](../../peerhub/application/governance_authorizer.py), lines 10-18, 66-73. The existing regression test explicitly covers the optional-round invalid-credential case. [test_consensus.py](../../tests/integration/governance/test_consensus.py), lines 434-450.

There is **no** `_voter_gate_is_open()` on `ConsensusService`, and its constructor has no registry/health dependencies. That helper belongs to `ProposalCoordinator`. It requires an existing well-formed node, a nonmissing projection, availability neither `UNAVAILABLE` nor `STALE`, admission `OPEN`, and no profile backoff. [consensus.py](../../peerhub/governance/consensus.py), lines 46-50; [proposals.py](../../peerhub/application/proposals.py), lines 172-205. R1 is proposing a new direct-service health guarantee, not preserving one already there.

The proposal coordinator also rechecks **every eligible voter**, not just the current caller, and escalates on any closed gate. Merely rejecting the unavailable caller lets another healthy caller finish under a weaker condition. [proposals.py](../../peerhub/application/proposals.py), lines 604-630.

**Required amendment:** Preserve all four credential combinations (optional/required times absent/present), with invalid presented proof always rejected and missing verifier failing closed. Inject a health/identity port from the composition root; do not create an upward dependency from governance into application coordinators. State the exact gate predicate and its new scope. Never remove a closed voter from the frozen electorate. Preserve the proposal escalation outcome and revalidate the required authority set at authorization, not only the last actor. Registry identity plus an open health gate is not itself proof that the caller controls that identity; retain the asserted/verified distinction and separately validated exceptional authority.

**Acceptance:** Direct-service and gateway calls both reject invalid optional credentials; unavailable/stale/missing/backed-off voters do not count as live; another voter's gate closure cannot be bypassed by a healthy final actor. Check these without requiring the pure function to perform I/O.

### B3. Use one quorum contract; do not omit or accidentally double-count unanimity

**Location:** R1 lines 52-68, 116; INT-09.

The first three formulas are **not numerically wrong** for valid integer tallies. The actual implementation is:

| Formula | Current `process_quorum_round()` predicate |
|---|---|
| majority | `agree_count > total_voters / 2`, equivalent to `agree_count >= total_voters // 2 + 1` |
| supermajority | `agree_count >= ceil(2 * total_voters / 3)` |
| all_required | `agree_count == total_voters` |
| unanimous | Let `k = total_voters + (1 if proposer_voted else 0)`; require `k >= 2` **and** `agree_count == k`. |

There is also an earlier `proposer_voted and not non_proposer_voted` block, and explicit changed-vote rejection. [orchestrator.py](../../peerhub/dispatch/orchestrator.py), lines 48-77.

R1 omits the fourth formula completely. Its proposed `(participant_count, formula)` signature cannot express the current proposer-sensitive rule. Moreover, the current service's `required_participants` can already include the proposer: blindly mapping `len(required)` into `total_voters` and also setting `proposer_voted=True` double-counts that person. With `total_voters=3`, proposer true, and three agreements, the current orchestrator waits for four. With one required voter and proposer true, it requires two; with one and proposer false, unanimity cannot succeed. These were reproduced [empirical_probe]; see also [test_orchestrator_consultation.py](../../tests/unit/dispatch/test_orchestrator_consultation.py), lines 137-166.

An integer alone also loses the exact-equality predicates if the existing `>= required_votes` check is retained. More significantly, today's `cast_vote()` vetoes on any dissent and silently overwrites an existing vote; the ratified majority semantics allow ordinary dissent, and correction has a separate contract. Changing only the initial quorum integer leaves those old decisions authoritative. [consensus.py](../../peerhub/governance/consensus.py), lines 577-616; [ratified revision](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), lines 93-100.

**Required amendment:** Extract/reuse one quorum evaluator for formation, vote transitions and migration. Define frozen logical identities, proposer inclusion, required versus eligible sets, count domain, independence, equality versus threshold, dissent/concern handling and the binding minimum. Preserve the current tested math through an explicit input adapter, or clearly identify and ratify any correction to it; do not add a second approximation. Counts outside the valid electorate must reject. Define N=0 and N=1 behavior separately rather than treating all formulas as interchangeable thresholds.

**Acceptance:** Table tests cover all four formulas, proposer inside/outside the required set, zero/one/two voters, threshold neighbors, duplicate logical identities, ordinary dissent versus qualifying concern, and explicit correction. Assert both the integer/contract and the actual persisted transition. For the first three formulas, my bounded comparison over N=1..8 and agreement counts 0..N found zero discrepancies [empirical_probe].

### B4. Make risk, per-action policy and the frozen snapshot executable contracts

**Location:** R1 lines 55-60, 99-101, 115-117; INT-04/05/07.

`PolicyResolver.resolve()` accepts action name, CLI overrides and command minimums, but no risk. Action names select **consultation depth only**; the consensus section is shared. There is no existing per-action final-call override for `legacy.proposal`. `DispatchOrchestrator.evaluate()` does not consult its `policy`, `action_name`, or `risk` arguments. Thus “high risk forces QUORUM (or UNANIMOUS)” and “configure legacy.proposal to never” are missing policy/API decisions. [policy_resolver.py](../../peerhub/dispatch/policy_resolver.py), lines 61-76, 112-148; [orchestrator.py](../../peerhub/dispatch/orchestrator.py), lines 9-25.

Forcing QUORUM could also lower an existing UNANIMOUS baseline unless the rule is explicitly monotone. Changing the final-call rule string does not set `mandatory_floors_hit`; the state machine only checks that boolean or the literal `"always"`. Tier-0 and dissent ancestry need real inputs too. [consensus.py](../../peerhub/governance/consensus.py), lines 831-841.

INT-04 is not today's resolver behavior: `_load_toml()` returns `{}` on read `OSError`, permitting fallback; malformed UTF-8/TOML raises `ConfigurationError`. The injected unreadable-file probe returned `{}` [empirical_probe]. The resolver also returns `source_hash="000"`; deep copying isolates policies from one another but `RoutingPolicy.preference_map` remains mutable inside each frozen dataclass. A freeze/provenance guarantee needs an explicit serialized snapshot contract. [policy_resolver.py](../../peerhub/dispatch/policy_resolver.py), lines 21-37, 78-85, 217-228; [policy.py](../../peerhub/dispatch/policy.py), lines 28-30, 46-73.

**Required amendment:** Specify exact risk inputs and monotone depth floors, protected trigger union, action-to-consensus-policy resolution, validated snapshot encoding/decoding/version/identity, and restart behavior. Freeze the effective values and applicable obligations, not just a hash or mutable in-memory object. Distinguish absent optional configuration from unreadable configured policy and identify the deliberate loader change required by INT-04. No mid-round re-resolution from current TOML.

**Acceptance:** High risk cannot downgrade unanimity; all mandatory triggers reach Final Call even with `never`; per-action selection changes the intended consensus policy; restarts use identical stored values; unreadable and malformed config have the specified distinct outcomes; mutating a returned container cannot alter a persisted round's policy.

### B5. Complete Final Call integrity and preserve the broker's atomic boundary

**Location:** R1 lines 17, 77-86; INT-01/08.

External authorization is a legitimate shell responsibility. It is not sufficient to call today's `process_ack()` after checking one actor: that method neither checks the current phase nor derives `is_last`, candidate validity, required ACK membership or concerns. A direct call on an `"abandoned"` state with `is_last=True` returned `"approved"` and changed it to `"resolved"` [empirical_probe]. There is no candidate/ACK history inside this class to be authoritative over. [consensus.py](../../peerhub/governance/consensus.py), lines 831-849.

R1 **does** specify reread/recompute after `StaleRevisionError`. That can prevent a stale round snapshot from winning if every attempt submits exactly the revision it read. The remaining gap is cross-record authority: a health restriction, registry rebinding or credential revocation can change after checking it without changing the round revision. Round-only CAS does not detect that. Last ACK requires the live required authority set, not merely X's health. The ratified design explicitly requires candidate-bound ACK/retraction and a shared authorization/revocation/claim ordering. [ratified revision](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), lines 96-100, 123-126.

There is also a concrete commit-order regression in R1's numbered workflow: persist resolved state, **then** generate the effect. Today `final_call_ack()` builds the `EffectIntent` before `_submit()`. `GovernanceBroker.submit()` atomically commits the target, mutation plan, transition receipt, idempotency binding and outbox event. Splitting intent creation into a later write creates a crash window with approval but no recoverable effect. [consensus.py](../../peerhub/governance/consensus.py), lines 209-234, 766-792; [broker.py](../../peerhub/governance/broker.py), lines 311-420.

**Required amendment:** Define a persisted candidate ID, frozen ACK set, per-actor candidate-bound attestations/retractions, qualifying concerns, explicit outcome and authorization reference. The reducer computes valid transitions from those records; caller booleans cannot stand in for absent data. Each retry reloads/revalidates all inputs and creates a fresh reducer evaluation. Specify a transaction or version-fenced authorization interface that validates relevant external authority versions at the commit/claim boundaries; merely adding another pre-submit read is not atomicity. Build state, outcome and effect intent together and submit them in the existing single broker transaction. Apply external effects afterward through their recoverable consumers.

Distinguish an internally detected CAS conflict from an explicitly supplied `expected_revision`: do not silently rebase a caller's stale precondition. Define stable semantic operation/candidate identity for response-loss retries; `_submit()` currently constructs fresh request IDs, which is not by itself same-command idempotency. Explicitly assign the public ACK/NACK/retraction and exceptional-resolution entrances as well as the reducer.

**Acceptance:** Actual concurrent last-ACK/retraction and authority-change tests establish both serial orders; stale candidate/phase events reject; explicit revision conflicts remain conflicts; crash before commit produces no approval/effect, crash after commit recovers the same receipt/outbox item, and no path executes from a mere `phase="resolved"` without an approved/authorized outcome.

### B6. Preserve proposal semantics and its specific effect, with explicit intentional changes

**Location:** R1 lines 92-102; INT-07.

The proposal path already calls `ConsensusService.propose()` and `cast_vote()`; what it adds is a decision/recovery coordinator. Its reconciliation priority is: return/recover resolved outcomes; preserve existing escalation; too few eligible voters -> Tier-0 escalation; any eligible gate closure -> Tier-0 escalation; eligible dissent -> rejection; every required voter agrees **and** at least one agreeing identity differs from the proposer -> approval; proposer-only agreement after all votes -> self-finalization escalation. [proposals.py](../../peerhub/application/proposals.py), lines 280-294, 555-691, 710-735.

`final_call_rule="never"` implements none of those predicates. The orchestrator's `non_proposer_voted=True` default is not evidence of the actual `any(voter_id != proposer_id for voter_id in agreed)` condition. High-risk proposal Final Call under INT-07 is a deliberate change from this current auto-resolution behavior, even if it is necessary to enforce the already-ratified floor. State that change and provide an ACK/recovery entrance; a coordinator that merely waits cannot finish a newly mandatory barrier by itself.

Approval also has a non-generic effect: `_approval_effect()` captures the approved revision, decision hash, proposal content, electorate and votes into `RATIFIED_INVARIANT_EFFECT_KIND`. `resolve(..., effect_intent)` commits it atomically; the projector materializes and validates an immutable invariant-write-request target. Merely generating `consensus.resolved` cannot substitute for it; the current recovery path errors if that exact approval effect/target is absent. [proposals.py](../../peerhub/application/proposals.py), lines 344-446, 448-490, 638-665; [invariant_requests.py](../../peerhub/governance/invariant_requests.py), lines 128-165.

The real `process_consensus_effects()` is also less sophisticated than the question's “multiple effect types” wording may imply: it skips `consensus.noop`, broadly matches round-associated events, then claims and marks them successful without a kind-specific materializer. Copying that behavior into a generic replacement worker risks consuming the invariant-request event without creating its target. This is a source-level hazard, not a claim that a production collision was observed. [consensus.py](../../peerhub/governance/consensus.py), lines 486-525.

**Required amendment:** Move the proposal's decision predicates into the common evaluator with explicit policy dispositions for dissent and floors. Retain proposal-specific formatting, effect construction/projection and recovery in a bounded adapter/consumer. Preserve all three named escalation reasons and their priority or explicitly ratify replacements. Define effect routing by kind and receipt identity; only the responsible materializer may claim completion. The final approval transaction must contain the invariant-request intent whether approval follows quorum immediately or follows Final Call later.

**Acceptance:** Preserve native proposal outputs (`CONSENSUS_OK`, `NACK`, `ESCALATED`, invariant target ID), non-proposer agreement, all-electorate health checks, reason ordering, denied/abandoned no-write behavior, and exactly one matching invariant request after restart. A high-risk proposal's last ACK must reach that same materializer.

### B7. Replace heuristic upcasting with validated provenance and a complete schema adapter

**Location:** R1 lines 108-119; INT-06.

The stated discriminator is factually wrong: `ProposalCoordinator.add_proposal()` passes `verified_required` to the same `ConsensusService.propose()` used by direct rounds, and that constructor always writes the field. Both share schema, kind, phase and proposer structure. Even `_is_proposal_round()` only recognizes a question/body convention which a direct caller can reproduce; it is not durable origin evidence. [proposals.py](../../peerhub/application/proposals.py), lines 236-245, 280-294, 493-506; [consensus.py](../../peerhub/governance/consensus.py), lines 82-127.

“All legacy rounds implicitly required Final Call” is also not an exact description: generic `resolve()` permits approval at `quorum_reached` without Final Call, and permits escalation-based resolution. A proposed compatibility policy of `always` would tighten those historical entrances. That may be a sound choice, but it is not automatic equivalence. [consensus.py](../../peerhub/governance/consensus.py), lines 337-354.

The synthetic policy is currently unrepresentable through the normal resolver: `custom` is rejected, `ConsensusPolicy` has no fixed-count field, and `process_quorum_round(..., "custom", ...)` returns pending. Adding a policy block alone also cannot manufacture the absent candidate identity, bound ACKs, deadline history or authority evidence. [policy.py](../../peerhub/dispatch/policy.py), lines 14-18; [policy_resolver.py](../../peerhub/dispatch/policy_resolver.py), lines 134-148; [orchestrator.py](../../peerhub/dispatch/orchestrator.py), lines 57-77.

Finally, `_open_state()` is not the common read entrance: `get_target`, `cast_vote`, `resolve` and `record_arbiter_opinion` read the broker directly. Putting the adapter only there leaves several operations unnormalized. [consensus.py](../../peerhub/governance/consensus.py), lines 52-55, 136-146, 332-335, 537-540, 714-719.

**Required amendment:** Use an explicit version and origin/action discriminator for new rounds. Classify old rounds only with demonstrable durable provenance or an explicit migration mapping; where the existing data cannot distinguish origins, hold for a documented disposition rather than infer authority from prose or missing fields. Define validation for inconsistent schema/policy combinations and unknown versions. Preserve the stored required count/electorate and historical receipt identities; define a validated frozen quorum contract understood by the single evaluator instead of an unsupported string. Normalize every applicable read/mutation/recovery entrance, including terminal evidence updates. Pure reads must not silently advance persisted revisions.

**Acceptance:** Direct and proposal rounds with identical content are not silently assigned different governance based on guesses. Verified/unverified variants work on both paths. Missing or inconsistent fields reject with an accurate corruption/schema error. Repeated adaptation is deterministic, losing CAS attempts do not partially migrate, and historical hashes/effect references remain valid.

### B8. Specify in-flight cutover and replace the ten examples with a coverage matrix

**Location:** R1 lines 108, 123-138.

The hardest compatibility case is absent: an old round already in `final_call`, with a partial ACK map, while old and new workers may race. The legacy ACK records contain actor, boolean, time and mutation ID; they have no candidate binding. Its completion check uses `eligible` rather than deriving completion from a candidate-bound barrier. Injecting policy cannot retroactively prove those ACKs reviewed the new candidate. [consensus.py](../../peerhub/governance/consensus.py), lines 179-205.

Also cover already-resolved rounds with unconsumed effects, claimed effects without results, committed effects whose caller lost the response, abandoned rounds, and later arbiter attachments. Rewriting a terminal round solely to mark V2 can invalidate consumers that compare `approved_revision` to the round revision. [proposals.py](../../peerhub/application/proposals.py), lines 463-482. An old writer can still apply legacy transition predicates to a V2 round because these operations do not check schema compatibility; round CAS does not fence writer versions. [consensus.py](../../peerhub/governance/consensus.py), lines 136-146, 537-547, 616-638.

The ten INT rows mix configuration, operation, authority and migration axes. They overlap and omit entire categories; they are examples, not MECE coverage. INT-01 is especially weak as the only concurrency case: casting votes and ACKing normally have disjoint phase preconditions, while two votes, two ACKs, and last ACK/retraction are the decisive races. INT-04 describes an unimplemented loader change, and INT-05 calls `propose()` “mid-round” in the adjacent row. These need corrected event boundaries, not more reassuring counts.

The claimed “135 existing unit-level state machine cases” is inaccurate. The prior final ratification counted **135 design case IDs across nine areas**, not 135 implemented consensus-state-machine tests. The inspected consensus-policy file contains 16 tests. Its retraction race test merely passes `simulate_concurrent_modification=True`; it does not race broker writes. The quarantine crash tests likewise call reconcile on a nonexistent review or resume an empty pending set, rather than inject the stated crashes. [Round 10](GOVERNANCE-DISPATCH-R10-cx-astra-FINAL-ratification-2026-09-24.md), section 3; [test_consensus_policy_integration.py](../../tests/unit/governance/test_consensus_policy_integration.py), lines 104-108; [test_quarantine_review_policy.py](../../tests/unit/application/test_quarantine_review_policy.py), lines 257-267.

**Required amendment:** Choose an explicit cutover protocol: fence/drain old writers before activation, or specify compatible versioned writes. Define an old partial ACK's disposition: preserve under a demonstrably equivalent frozen legacy contract, or reopen/recollect under a documented migration decision. Never silently relabel unbound historical ACKs as new attestations. Keep historical decision/receipt/effect identity intact and define restart handling at every durable boundary.

Use at least this bounded matrix, expanded into concrete test rows with preconditions, event order, expected revision/receipt and effect count:

| Axis | Required partitions/couplings |
|---|---|
| Entry/operation | Native propose/vote/read/sweep; proposal create/vote/reconcile; ACK/NACK/retraction; resolution/rejection/escalation/abandon; arbiter attachment; effect recovery. |
| Schema/origin | New V2; supported V1 origins; ambiguous origin; corrupt/inconsistent schema; unsupported version. |
| Phase at cutover | Proposed/voting; quorum reached; partial Final Call; escalation/timeout evidence; resolved with pending/claimed/completed effects; abandoned. |
| Authority | Eligible/ineligible; valid/invalid/missing credential under both requirements; missing/stale/closed/backed-off health; other required voter closes; authority changes between check and commit. |
| Quorum/policy | All four formulas; proposer inclusion and independence; minimum/zero electorate; duplicates/corrections; ordinary dissent/qualifying concern; each mandatory trigger; config change/unreadable config. |
| Ordering/recovery | Vote/vote; ACK/ACK; last ACK/retraction; authority change/authorization; upcast/old writer; before commit; after commit before response; before claim; after apply before receipt; resume/replay. |
| Effect/outcome | Approved generic decision; approved invariant request; rejected; abandoned; exceptional approval with proof; already-completed delivery; unknown effect kind. |

**Acceptance:** Real broker/SQLite fault and concurrency tests, plus native entrypoint tests, establish these guarantees. Unit booleans are supporting tests only. A small closure matrix must demonstrate that all production consensus/proposal paths reach the new evaluator; the initial search still finds `PolicyResolver`, `DispatchOrchestrator`, and `ConsensusStateMachine` only at their definitions under `peerhub/`, while runtime constructs the legacy service. [runtime.py](../../peerhub/runtime.py), lines 215-227; [handlers/consensus.py](../../peerhub/application/handlers/consensus.py), lines 100-174, 261-311; [arbiter_review.py](../../peerhub/application/arbiter_review.py), line 753.

## 4. Next-round ratification gate

Retain the policy/resolver/orchestrator architecture and a persistence facade. Revise the proposal in place to supply: the complete operation/transition ownership table; one exact quorum and electorate contract; the credential/authority and commit predicates; per-action risk/policy freezing; proposal-specific effect/recovery ownership; and a provenance-safe, phase-specific cutover protocol. Identify each intentional change from the legacy behavior instead of promising universal equivalence.

This does not require a new workflow framework, broad legacy capability ledger, unrelated hub migration, or another live peer dispatch. It does require answers to B1-B8 before implementation can faithfully realize “full replacement.”

**Final vote: REJECT as written.** The abstraction is viable. The draft's current interfaces, persistence sequence, eligibility claims and migration assumptions are not sufficient to retire the legacy engine safely or to claim preservation of the ratified behavior.
