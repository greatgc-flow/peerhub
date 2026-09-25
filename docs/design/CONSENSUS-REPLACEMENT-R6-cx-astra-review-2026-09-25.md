# Round 6 re-review: consensus-engine replacement

Author: cx.astra. Date: 2026-09-25. **Verdict: REJECT.** Round 5 closes several specific gaps, including the credential cases, but its proposer adapter produces incorrect results, its legacy origin mapping cannot distinguish the two creation paths, and its authority fence remains an unbound requirement rather than a transaction contract.

This is a design review against document text and actual source. Missing implementation alone is not a blocker. The blockers below are missing or contradictory design choices that affect approval, authorization, provenance, and recovery. No implementation/configuration changes, commits, activation, or peer dispatch were performed.

**Review basis and measured checks**

- Read all 164 lines of the [Round 5 proposal](CONSENSUS-REPLACEMENT-R1-ag-deepthink-2026-09-25.md), SHA-256 `0b0071bc923ee846888c8669a4aa2806d8361456925f4c78af2f422d4d8cf05a`, and the [Round 4 review](CONSENSUS-REPLACEMENT-R4-cx-astra-review-2026-09-25.md). Reviewed the actual service, orchestrator, policy resolver, proposal coordinator, mutation contracts, broker, SQLite transaction/repository, invariant projector, handlers, and relevant ratified contract/tests.
- Source baseline: HEAD `490ce480bfad66092429a86a13b1b7e1550423fe`; tracked source was unchanged. Line spans in prose refer to this inspected baseline and the Round 5 draft, not deployed behavior.
- **[empirical_probe]** `python -B -m pytest -q -p no:cacheprovider tests/unit/dispatch/test_orchestrator_consultation.py tests/unit/governance/test_consensus_policy_integration.py`: **42 passed in 0.35s**. These tests call existing methods directly; they do not implement or validate the proposed adapter.
- **[empirical_probe]** Called the real `process_quorum_round` for all CD-Q/CD-U unit-test inputs, then with identity-consistent inputs computed using the proposed adapter. Results are below. The proposed correction-event entrance remains unimplemented; the current layer's changed-vote exception is not proof of reopening.
- **[empirical_probe]** Ran actual direct `propose()` and `ProposalCoordinator.add_proposal()` through `_submit`, `GovernanceBroker`, production migrations, units of work, and SQLite repositories using shared in-memory SQLite connections and fixed time/IDs. Only connection transport and proposal availability discovery were substituted. Both paths produced equal state envelopes and the same stored operation/command type/client. Explicitly injected `origin`/`action` fields survived submission, a fresh service/broker, a vote, and SQL readback. This measures serialization and transaction behavior, not disk/crash durability.
- A disk-backed probe stopped at `PermissionError` opening the scratch `maintenance.lock`; it did not validate disk persistence. Concurrency, crash recovery, V2 implementation, activation fencing, and deployed authorization remain **TEST NEEDED**.

**Disposition by blocker**

| Blocker | Accepted change | Round 6 disposition |
|---|---|---|
| B1 | Event table, named frozen context, timeout phase choice, first-valid arbiter evidence. | Open: NACK contradicts the ratified concern rule; corrections contradict the listed ingress phases; normal resolution/escalation/reopening coverage remains incomplete. |
| B2 | All present/absent credential cases; missing/malformed health identity handling; named frozen eligible set; separate exceptional authority. | Round 4's listed credential/health gaps are closed at design level. Commit/claim ordering remains B5. |
| B3 | An explicit proposer/count adapter; three-person unanimous example now works when the proposer agrees. | Open: CD-Q-01/CD-Q-07 and proposer-absent-agreement variants of CD-U-02/CD-U-03 fail; the general binding floor is false. |
| B4 | Versioned snapshot intent, decode rejection, retained dissent obligation, action discriminator. | Open: exact policy selection, risk floor, mandatory Final Call rule and serialized contract remain incomplete. |
| B5 | Named authority version, candidate invalidation, public ACK/NACK/retraction intent, receipt-first replay intent. | Open: no storage/increment/transaction/claim contract for the fence; command binding and serial orders still incomplete. |
| B6 | Ordered proposal dispositions, intentional high-risk Final Call, final snapshot effect factory, exclusive materializer routing. | Mostly improved; open: too-few-voters count conflicts with B3, proposal unanimity is conditional without a selected policy, atomic effect submission is no longer explicit. |
| B7 | Explicit V2 origin/action intent, ambiguity hold, authoritative legacy count, broad read adaptation. | Open: neither creation path is assigned mandatory persisted values; the proposed V1 operation-key mapping is non-discriminating. |
| B8 | Writer-generation gate intent, legacy eligible-set completion, phase table and coupled examples. | Open: gate enforcement and full effect recovery remain unspecified; immutable approval identity still has no recovery validation rule. |

**B3 — The adapter fixes one formula branch and breaks others. HIGH.**

Draft lines 50-53 specify `T = len(eligible - {proposer})`, `P = proposer has agreed`, and an agreement count including the proposer. But the [orchestrator](../../peerhub/dispatch/orchestrator.py), lines 48-77, does not use a uniform denominator contract:

| Formula | Actual predicate |
|---|---|
| majority | `A > T / 2` |
| supermajority | `A >= ceil(2*T/3)` |
| all_required | `A == T` |
| unanimous | `R = T + int(P)`; `R >= 2 and A == R` |

Only unanimous adds the agreeing proposer. Its required count also changes when the proposer stops agreeing. Globally subtracting the proposer cannot preserve these semantics.

The following **[empirical_probe]** table covers every CD-Q/CD-U case, using an included proposer unless specified. `E` is the complete eligible count; `A` counts agreements; `check` means `check_final_call`, not authorization. Oracles come from the [test file](../../tests/unit/dispatch/test_orchestrator_consultation.py), lines 71-166, and [ratified case table](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), lines 435-455. Identity-based probes supply membership absent from those scalar tests.

| Case | Identity-consistent adapter input | Expected | Actual |
|---|---|---|---|
| CD-Q-01 | E=3, A=3, proposer agrees, all_required | check | **pending** (`3 != 2`) |
| CD-Q-02 | E=3, A=2 including proposer, other abstains | check | check |
| CD-Q-03 | E=3, A=2 including proposer, other ordinary dissent | check, then remaining obligations | check; later obligations untested |
| CD-Q-04 | E=3, A=1 non-proposer, timed out | escalation | escalation |
| CD-Q-05 | E=3, A=2 including proposer, other silent | check | check |
| CD-Q-06 | E=3, A=2 including proposer, majority | check | check |
| CD-Q-07 | E=4, A=2 including proposer, majority | pending | **check** (`2 > 3/2`) |
| CD-Q-08 | E=3, A=2 including proposer, supermajority | check | check |
| CD-Q-09 | E=6, A=3 including proposer, supermajority | pending | pending |
| CD-Q-10 | E=3, A=2 including proposer, majority | check | check |
| CD-Q-11 | Only proposer agrees | blocked_non_proposer_required | blocked_non_proposer_required |
| CD-U-01 | E=3, A=3 including proposer | check | check |
| CD-U-02 | E=3, A=2; proposer agrees / proposer abstains | pending in both | pending / **check** |
| CD-U-03 | E=3, A=2, timed out; proposer agrees / proposer unreachable | escalation in both | escalation / **check** |
| CD-U-04 | Pass changed_vote to current layer | InvalidMutationError; correction entrance must reopen separately | Exception confirmed; proposed reopening **TEST NEEDED** |
| CD-U-05 | E=2 = proposer + one other; both / only other / only proposer agree | Both required | check / pending / blocked_non_proposer_required |

CD-U-05's existing scalar unit test sets both agreement booleans true while passing `A=1`. That combination is not realizable under Round 5's agreement-count definition. Its raw call returns `pending`; do not confuse that fixture with the one-person-total example, or treat it as adapter validation.

Additional **[empirical_probe]** counterexamples:

- E=3, all_required: proposer plus one other agreeing passes at A=2, but the third agreement makes it fail at A=3. This is a nonmonotonic approval predicate.
- E={proposer, other}, only other agrees: majority, supermajority and all_required all return `check_final_call` with one agreement. E={other}, proposer outside and not agreeing, does likewise. Draft line 55's universal minimum-two claim is therefore false. The independence check only blocks when `proposer_voted` is true; the minimum-two arithmetic exists only in unanimous.
- The proposer-only unanimous example does block, but returns `blocked_non_proposer_required` at orchestrator lines 52-53 before reaching the `required_agrees >= 2` check. Draft lines 61-64 misdescribe the actual control flow.

Draft lines 65-68 also count an agreeing proposer outside `eligible`. Current [cast_vote](../../peerhub/governance/consensus.py), lines 544-547, rejects that actor. The design must explicitly define the authorized voting universe if it intends an outside proposer to contribute. It cannot both use eligible membership as the credential gate and silently count an ineligible vote. Different actor IDs alone also do not establish distinct logical voters/failure domains.

**Required:** Freeze complete counted identities, required identities, proposer membership and the denominator independently of votes. Use formula-specific adaptation or change the pure evaluator's contract; enforce minimum two independent agreeing identities separately on every binding path. Preserve required membership, including a silent/abstaining proposer when required. Define the outside-proposer case and the legacy fixed-count branch. Repeat the table with both proposer placements and negative boundary cases.

**B7 — State fields can survive persistence; their creation and the V1 mapping are still unspecified. HIGH.**

Draft line 129 adds the right kind of fields. The current path will preserve them if they are actually put into `desired_state`: [consensus.py](../../peerhub/governance/consensus.py), lines 766-791; [contract.py](../../peerhub/governance/contract.py), lines 303-350; [mutations.py](../../peerhub/governance/mutations.py), lines 96-104; [SQLite target storage](../../peerhub/persistence/sqlite_governance.py), lines 98-153. The metadata-survival probe confirmed this. The broker does **not** strip arbitrary state fields.

However, the draft does not assign required values to both creation entrances. Its `proposals/create` is an example, not a contract for `ProposalCoordinator.add_proposal()` and direct `ConsensusService.propose()`/native creation. Line 83's `action_name` examples also have no specified mapping from stored `origin`/`action` to a resolver key. Define that before selecting policy or effects.

More decisively, line 133 promises a V1 mapping from the original stored operation key. The real proposal path calls the same `propose()` as the direct path: [proposals.py](../../peerhub/application/proposals.py), lines 280-294. Both pass `operation="consensus.propose"`: [consensus.py](../../peerhub/governance/consensus.py), lines 128-134. `build_mutation_request` copies this to `command_type`, uses `client_id="peerhub.consensus"`, and generates an opaque request ID as the idempotency key. The repository persists those same values: [contract.py](../../peerhub/governance/contract.py), lines 331-350; [sqlite_governance.py](../../peerhub/persistence/sqlite_governance.py), lines 222-276.

**[empirical_probe]** With identical public inputs and fixed time/IDs, both creation paths produced equal complete state envelopes, no origin field, and `(operation, command_type, client_id) = (consensus.propose, consensus.propose, peerhub.consensus)` in SQLite. The operation key therefore cannot distinguish these records. The ambiguity hold is a valid safe fallback, but does not make the claimed mapping work.

**Required:** Specify validated immutable origin/action values assigned by each trusted creation adapter and included in the initial committed state. Preserve them on every update; define their mapping to policy/effect selection. For V1, provide an explicit independently evidenced per-round migration mapping, or hold genuinely indistinguishable records. Do not claim the shared consensus operation is provenance. Retain the accepted authoritative `quorum.required_votes` rule and add its fixed-count/identity validation contract.

**B5 — `authority_version` names the intended invariant, not its implementation boundary. HIGH.**

Draft lines 91-93 name a global counter and require the broker to check it; line 98 says pending claims re-authorize. Missing are the persisted record/field, type/initialization, expected value carried by the mutation, atomic increment owner, prevention of lost increments/reset/ABA, covered authority-changing writers, and the exact check inside commit and claim. A copied value in round state is not a precondition on live authority.

The real [MutationRequest](../../peerhub/governance/contract.py), lines 243-262, has `expected_revision` but no expected authority dependency. [Broker submit](../../peerhub/governance/broker.py), lines 319-420, enters a unit of work, validates the round revision at 347-348, CAS-writes the round at 389, then commits the request/receipt/outbox together. [SQLite CAS](../../peerhub/persistence/sqlite_governance.py), lines 136-153, predicates only on `target_id` and `revision`. Its [transaction](../../peerhub/persistence/sqlite.py), lines 957-973, uses `BEGIN IMMEDIATE`; that provides a usable serialization boundary **if all relevant authority writes and validation join it**. The draft does not assign that protocol. The existing workspace activation-generation check at sqlite.py lines 396-408 is not the proposed revocation/rebinding counter.

**[empirical_probe]** In the real broker/SQLite transaction path with in-memory connections, an authority target advanced from version 1 to 2 while the round revision stayed fixed. Submitting that round at its valid revision with `authority_version=1` in desired state still committed. This is a baseline integration counterexample, not an observed deployed race or a test of an implemented V2 fence.

[claim_effect](../../peerhub/governance/broker.py), lines 513-552, checks delivery state and ownership, with an early return for an already-held identical claim. It has no live authorization dependency check. Merely saying pending claims re-authorize does not address a claim that wins just before revocation. The [ratified rule](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), line 126, specifies both orders, a pre-invocation recheck and cancellation semantics.

The receipt-first replay rule at draft line 102 is accepted as a direction. It still needs a durable lookup key: current `_submit` generates new request IDs, while broker idempotency is keyed by client/type/idempotency key and validates a digest including revision, desired state and effect payload. See [contract.py](../../peerhub/governance/contract.py), lines 331-349; [mutations.py](../../peerhub/governance/mutations.py), lines 33-59. A tuple containing `mutation_id` does not by itself connect the public semantic command to that ledger.

**Required:** Bind an expected authority version to the request and receipt/effect, specify a durable monotonic record updated atomically with every relevant revocation/rebinding/authority change, and locate the comparison in the same transaction before the round CAS and before effect claim. Cover live expiry/health validation as applicable, stale identical claims, revocation-first and claim-first orders. Specify the stable public-command lookup and preserve approval plus effect in one commit.

**B1 — The event table introduces a ratified-behavior conflict. HIGH.**

Draft line 18 makes every NACK terminal `rejected`. The [ratified contract](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), lines 94-100 and 513-517, distinguishes ordinary disagreement, qualifying integrity/safety concern, and cosmetic feedback; a qualifying Final Call NACK holds the barrier. The current [SM-20 test](../../tests/unit/governance/test_consensus_policy_integration.py), lines 45-49, and [state machine](../../peerhub/governance/consensus.py), lines 851-853, remain in `final_call`. This change needs an explicit replacement decision, not a claim that the old gap is closed.

The table permits Vote/Dissent only in `proposed`/`voting` (line 16), while line 71 requires a correction to invalidate an active `final_call` candidate. No correction row admits that event there. It also supplies only a boolean vote payload, with no disposition for `abstain`, `need_more_info` or typed BLOCK. The source accepts all four ordinary choices at consensus.py lines 571-574; the ratified table distinguishes BLOCK at lines 500-507.

Timeout now explicitly preserves phase, and first-valid arbiter attachment preserves the decision: accepted. But timeout omits currently accepted `proposed` and `quorum_reached` phases ([mark_timeout](../../peerhub/governance/consensus.py), lines 236-265), and the table still lacks explicit normal resolution, escalation/reopening with deadlines, and the complete quorum-to-Final-Call transition. It is not yet the full replacement event contract.

**Required:** Reconcile NACK/concern behavior with the ratified contract, supply correction ingress and guards, and cover the missing operations or explicitly incorporate their exact preserved predicates. State intentional changes, including narrowed timeout phases.

**B2 — The Round 4 credential/health amendments are accepted.**

Draft lines 28-40 explicitly cover optional absent proof, required absent proof, presented proof with unavailable/failed verifier, missing registry/projection, malformed identity, the live health predicate and a named frozen `participants.eligible` set. These answer the particular Round 4 objections against [consensus.py](../../peerhub/governance/consensus.py), lines 166-177 and 544-569, and [proposals.py](../../peerhub/application/proposals.py), lines 172-205. Returning false for malformed identity is a specified fail-closed change from the source's exception. This accepts the design predicate; it does not validate implementation or close B5's ordering issue.

**B4 — Depth floor and Final Call obligation are still conflated. HIGH.**

Draft line 80 assigns the mandatory `tier-0 OR high-risk OR unresolved dissent` union to a `QUORUM or UNANIMOUS` depth floor. The ratified contract assigns that union to **mandatory Final Call**, even when `final_call_rule="never"`: [ratified rules](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), lines 99-100 and 508-512. Section 6 explicitly covers high-risk proposals, but does not establish the complete native/direct/proposal Final Call union. The exact risk-to-depth mapping also remains unspecified.

Line 83 names `action_name` but gives no per-action consensus policy or precedence. The real [resolver](../../peerhub/dispatch/policy_resolver.py), lines 119-147, applies action overrides to consultation depth only and constructs a shared consensus policy. Naming a discriminator does not choose the proposal's unanimous/reject-on-dissent behavior versus general majority behavior.

Line 78 specifies version/hash/effective-depth intent and rejection on inconsistent decode, which is useful. It still lacks a validated saved representation for formula/fixed count, Final Call rule, deadlines/escalation, action/origin, risk derivation and snapshot identity. The source's `source_hash="000"` at policy_resolver.py line 227 is not that identity. The previously accepted explicit unreadable-config failure rule has also disappeared; rejecting a bad saved snapshot does not fix `_load_toml` returning `{}` on `OSError` at lines 21-27.

**Required:** Specify the exact per-action policy and risk floor, mandatory Final Call union, authoritative snapshot fields/validation/hash, and update rules for later concern obligations without re-resolving configuration. Retain the explicit unreadable-config failure rule.

**B6 — Effect routing is improved, but two proposal predicates remain wrong or undecided.**

The ordered list, intentional high-risk Final Call, final-snapshot effect factory and generic-worker exclusion in draft lines 109-122 are accepted improvements. However, line 112 calls too few eligible voters `total_voters < 2`. B3 defines that identifier to exclude the proposer. A valid two-member eligible set containing the proposer therefore has `total_voters=1` and is escalated. The actual [proposal rule](../../peerhub/application/proposals.py), lines 606-615, tests `len(eligible) < 2`. Use a distinct complete-electorate count.

Line 114 rejects dissent only “if unanimity applies,” but neither B4 nor B6 binds proposal creation to unanimity. Current proposal reconciliation rejects any eligible dissent and requires every required voter to agree: proposals.py lines 631-650. Specify the preserved proposal policy or the intentional change; conditional wording does not preserve it.

The previous accepted guarantee that the final approval and invariant effect share a broker submission is no longer explicit. Passing a final snapshot to an effect factory is necessary but does not specify when its result joins the approval mutation. Preserve that guarantee on immediate, exceptional and last-ACK approval, including after restart; the source [broker](../../peerhub/governance/broker.py), lines 374-413, supports one state/receipt/outbox commit.

**B8 — The phase table does not yet settle activation or recovery.**

Draft line 143 chooses a writer-generation gate, but specifies no stored generation, trusted writer token, enforcement point, activation order or stale-process rejection path. Current broker CAS does not enforce such a gate. It must be distinct from merely calling the new evaluator from new callers.

Line 144 improves the legacy ACK rule by naming eligible-set completion and a legacy-equivalent entrance. It still does not define the frozen content/policy/candidate boundary or how pre-existing unbound ACKs interact with new mandatory floors. Retaining the old ACK map and matching its completion count alone does not prove equivalent authorization.

The table at lines 147-153 omits timeout/escalation evidence and separate pending, claimed-without-result, materialized-before-result, and completed-effect dispositions. Legacy generic resolution stores `phase="resolved"`, not `approved`: [consensus.py](../../peerhub/governance/consensus.py), lines 323-379. Give the explicit phase/status/outcome mapping and recovery rule.

Most concretely, “preserve approval identity” does not change [proposal recovery](../../peerhub/application/proposals.py), lines 448-485, which compares immutable `approved_revision` to the current round revision and compares participants/votes to the current state. [Arbiter attachment](../../peerhub/governance/consensus.py), lines 714-759, increments that revision. Draft line 163's expected one-effect result still lacks the immutable approval snapshot/reference and revised validation rule that make it possible. Likewise, the invariant projector already distinguishes claimed and consumed events at [invariant_requests.py](../../peerhub/governance/invariant_requests.py), lines 128-165; those states need explicit preservation through cutover.

The seven coupled rows are useful examples, but last-ACK/retraction and authority-change/authorization cover only one order, and no row defines lost-response/claimed/materialized recovery records. “All callers are mapped” at line 144 is not the requested call-path map: identify direct/native handlers, proposal create/vote/reconcile/list, arbiter attachment and effect recovery, and their adapters/completion entrances. See [native handlers](../../peerhub/application/handlers/consensus.py), lines 100-174 and 195-293; [runtime construction](../../peerhub/runtime.py), lines 221-227 and 364-376.

**Final vote: REJECT as written.** B2 and the specific improvements above should be retained. Correcting B3's observable arithmetic failures, B7's non-discriminating provenance mapping, B5's transaction/claim contract, and the remaining event/policy/cutover choices requires substantive amendments, not editorial ratification.
