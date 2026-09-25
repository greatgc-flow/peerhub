# Round 10 re-review: consensus-engine replacement

Author: cx.astra. Date: 2026-09-25. **Verdict: REJECT as written.** The requested B3 correction passes: the Round 8 proposer-abstention/unreachability counterexamples are repaired, and CD-U-01 through CD-U-05 retain their expected outcomes with corrected inputs. B5's pre-invocation recheck and post-authorization revocation rules, and B7's exact creation keys and nested-call preservation, are accepted at design level. Remaining blockers concern the old-writer cutover fence and contradictory effect-recovery acceptance rows. The fixed-count and risk-floor contracts also remain incomplete.

This is a design review. Missing V2 implementation alone is not a blocker. Only this report was added; no production source, tests, configuration, activation, commit, or peer dispatch was changed or performed.

**Review basis and measured checks**

- Read all 212 lines of the [Round 9 proposal](CONSENSUS-REPLACEMENT-R1-ag-deepthink-2026-09-25.md), SHA-256 `33ba6c42a62af94e9f80c77fc8db6dbb831acf9bdeddec56c7a3d9b998a3f90f`, and the [Round 8 review](CONSENSUS-REPLACEMENT-R8-cx-astra-review-2026-09-25.md). Draft line references below identify that exact revision.
- Source HEAD remains `c0f3eefad05510204e8fd51581c23487ea1ccbdc`. The reviewed production files and consultation tests have no working-tree changes. The proposed production correction has not yet been applied to the checked-in function.
- **[empirical_probe]** Baseline command: `python -B -m pytest -q -p no:cacheprovider tests/unit/dispatch/test_orchestrator_consultation.py tests/unit/governance/test_consensus_policy_integration.py`: **42 passed in 0.36s**.
- **[empirical_probe]** Compiled the actual orchestrator source in memory with exactly one replacement: `required_agrees = total_voters + (1 if proposer_voted else 0)` became `required_agrees = total_voters + 1`. All other branches, including equality, the independent-voter guard, changed-vote rejection, and timeout ordering, were retained. **12 directed probes passed.**
- **[empirical_probe]** Parsed the real consultation test module and changed only CD-U-01..04 call arguments: `total_voters=3` became `2`; explicit `proposer_voted` was true for 01/04 and false for 02/03. No assertion or expected exception changed. With the corrected function installed only in memory, **all 26 consultation tests passed**, including the untouched CD-U-05.
- **[empirical_probe]** Exercised production migrations, the epoch check, broker, units of work, repositories, and `ConsensusService.cast_vote()` using shared in-memory SQLite connections. An existing epoch-pinned V1 store was rejected after an epoch change; a fresh V1 store adopted the new epoch and committed a vote over a target marked `engine_generation=2`. Injected proposal origin/action survived that broker round trip and vote. Connection transport replaced filesystem guards; this does not measure disk durability, OS-lock concurrency, or a completed V2 implementation.

**Disposition by blocker**

| Blocker | Round 10 disposition |
|---|---|
| B1 | Ordinary `disagree` and an authorized escalation/reopen event are now explicit. The requested Round 8 additions are accepted. |
| B2 | Credential and frozen-electorate requirements retained; no new finding. |
| B3 | **Requested arithmetic correction accepted; all requested negative probes and adjusted legacy test expectations pass.** The direct-path two-agreement floor is now explicit. Fixed-count migration remains a separate B7 gap below. |
| B4 | Mandatory Final Call union and snapshot rules retained. Risk-floor source is still described rather than concretely defined. |
| B5 | **Pre-invocation recheck and successful post-authorization revocation accepted.** Section 8.4 still contradicts these rules by guaranteeing an applied effect in insufficiently specified schedules. |
| B6 | Proposal precedence, complete-electorate floor, unconditional dissent rejection, and atomic/exclusive effect routing retained. |
| B7 | **Exact native action keys and preservation through the nested `propose()` call accepted.** Fixed-count identity/range evaluation remains undefined. |
| B8 | Recovery prose now distinguishes important states. **HIGH:** the named epoch mechanism does not prevent reopened V1 writers; coverage rows still conflate intent receipts and applied effects. |

**B3 — The production correction and legacy input migration work.**

Draft lines 62-71 explicitly require all three necessary changes: the unanimous denominator always excludes the proposer; `R = T + 1` regardless of proposer agreement; and CD-U-01..04 input updates without changing their expected behavior. This is a production-semantic correction, not an attempt to make the old conditional formula work by fabricating a proposer vote.

The actual [orchestrator](../../peerhub/dispatch/orchestrator.py), lines 48-77, still contains the old conditional expression. The experiment applied precisely the required replacement in memory. `T` below counts eligible non-proposers, `A` counts agreeing eligible identities, and `P` records whether the proposer agreed. `check_final_call` means quorum reached, not completed authorization.

| Reproduced Round 8 probe | Inputs | Independent arithmetic | Corrected measured result |
|---|---|---|---|
| Required proposer abstains; both others agree | T=2, A=2, P=false | R=2+1=3; 2 != 3 | **PASS: pending** |
| Required proposer unreachable; before timeout | T=2, A=2, P=false | R=3; 2 != 3 | **PASS: pending** |
| Same unreachable proposer; after timeout | Same, timed_out=true | R=3; insufficient quorum reaches timeout branch | **PASS: escalation** |

The unchanged current function returned `check_final_call` for all three inputs. The corrected function repairs every one of those counterexamples.

The independent CD-U arithmetic is:

| Test | Corrected input convention | Arithmetic/control flow | Unchanged expected outcome |
|---|---|---|---|
| CD-U-01 | T=2, A=3, P=true | R=3; A == R | **check_final_call — PASS** |
| CD-U-02 | T=2, A=2, P=false; one abstention | R=3; A < R; no timeout | **pending — PASS** |
| CD-U-03 | T=2, A=2, P=false; timed_out=true | R=3; A < R; timeout | **escalation — PASS** |
| CD-U-04 | T=2, A=3, P=true; changed_vote=true | R would be 3, but the changed-vote guard raises before counting | **InvalidMutationError — PASS** |
| CD-U-05 | T=1, A=1, P=true; existing non-proposer flag retained | R=1+1=2; A < R | **pending — PASS** |

CD-U-05's positive counterpart also passes: `T=1, A=2, P=true` yields `R=2` and `check_final_call`. Its missing-proposer counterpart, `T=1, A=1, P=false`, remains pending. A non-proposer abstention and a non-proposer timeout also preserve pending/escalation respectively. Thus the fix removes the previous proposer asymmetry.

CD-U-04's real test asserts an exception, not a completed superseding/reopen event. Its unchanged assertion remains achievable; the proposed correction-event adapter in draft line 88 still needs its own implementation test. Likewise, the two-independent-agreement floor at line 58 is an accepted requirement, not functionality proven by these scalar tests. Identity deduplication and eligibility filtering remain prerequisites for interpreting `A` correctly.

**B5 — Requested authority-ordering corrections accepted; the coverage matrix still conflicts.**

Draft line 125 now requires a live authorization recheck strictly before adapter invocation and best-effort cancellation after failed authorization/revocation. Line 124 explicitly records a post-authorization retraction as a separate revocation request instead of rejecting it. Lines 127-128 retain read-time expiry validation and distinguish already-applied idempotency from stale-authority claims. These address the two specific Round 8 objections and match the [ratified contract](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), lines 126 and 133-136. The event table's line 22 is expressly a pre-authorization row; section 5.2 supplies the post-authorization disposition.

The real insertion points confirm why these requirements matter. [Broker claim](../../peerhub/governance/broker.py), lines 504-552, commits independently and currently returns early for the same owner/attempt at lines 522-527. The [invariant projector](../../peerhub/governance/invariant_requests.py), lines 153-165, separately claims, creates the immutable target, and records the result. Claim, authorization, target creation, and completion are distinct boundaries. Their existing absence of V2 checks is not a design-review failure.

**Remaining B5/B8 contradiction: HIGH.** Draft line 207 says "Last ACK wins, then retraction" always has effect count **1**. A permitted ordering is instead: last ACK commits approval/outbox, retraction commits revocation, then the materializer checks authority. Under lines 124-125/205, the applied-effect count is **0**. If the effect completed before revocation, the count is **1**. Winning the last-ACK race alone does not select between these schedules.

Line 211 also still combines "Lost response / Claimed without result" and assigns effect count **1** solely by finding the original public-command receipt. This directly conflicts with the improved lines 191-192: a claimed effect may have no target, and an approval receipt is not completion evidence. It also reproduces the specific recovery objection from Round 8.

**Required:** Split the rows by execution evidence and ordering. Approval followed by revocation before invocation must permit zero applied effects. Already materialized/completed work preserves one effect without repeating it. Claimed/no-target recovery must revalidate authorization; target-present/no-result recovery must validate the exact immutable target and settle bookkeeping; uncertain adapter execution must preserve uncertainty. State whether counts refer to outbox intents or applied effects, and keep those counts separate. Do not retain tests that require one applied effect merely because approval or claim won first.

**B7 — Exact action names and nested provenance are now correct.**

Draft lines 159-163 explicitly specify:

| Creation entrance | Durable origin | Exact action |
|---|---|---|
| `ProposalCoordinator.add_proposal()` | `proposals` | `governance.proposal.create` |
| Direct `ConsensusService.propose()` | `direct` | `consensus.round.propose` |

These action strings match the real [handler registrations](../../peerhub/application/handlers/consensus.py), lines 100-117 and 261-274. The real [proposal coordinator](../../peerhub/application/proposals.py), lines 280-294, calls the same [service builder](../../peerhub/governance/consensus.py), lines 64-134. Line 160 now unambiguously requires the inner call to preserve the trusted outer origin/action; direct defaults only apply at the uninjected entrance. This closes the requested B7 handoff objection. Current `propose()` still lacks those parameters, so this is an accepted implementation contract, not a claim that the checked-in creation path already persists provenance.

**[empirical_probe]** Explicitly injected `origin="proposals"` and `action="governance.proposal.create"` survived the real broker and a fresh service's subsequent vote. Serialization supports the proposed contract. The resolver currently matches `action_name` exactly at [policy_resolver.py](../../peerhub/dispatch/policy_resolver.py), lines 119-120; implementation must carry these exact keys into that lookup and implement section 4.3's proposal override precedence explicitly.

**Remaining fixed-count gap: MEDIUM.** Line 168 chooses authoritative `quorum.required_votes`, but says the adapter "must explicitly define" identity/range evaluation without actually defining it. The current [vote evaluator](../../peerhub/governance/consensus.py), lines 588-616, counts agreements among `participants.required` and compares `counted >= required_votes`; this is not necessarily agreement among all eligible voters. Specify which identities count, required/eligible consistency, allowed threshold types/ranges, dissent behavior, and malformed/unattainable-state disposition. The four-formula scalar helper has no fixed-count branch. Preserve the accepted authoritative-field choice while completing this contract.

**B8 — Activation epoch is a useful component, not an old-code admission fence. HIGH.**

Draft lines 178-179 now name workspace activation epoch plus draining/restricting legacy paths. This improves on a generation marker alone, but the asserted exclusion still lacks the mechanism that prevents old code from reopening after activation. The real [epoch checker](../../peerhub/persistence/sqlite.py), lines 396-408, rejects a changed epoch only when that store already has a pinned generation. A newly constructed store starts with `_generation=None` at line 151 and adopts the current epoch. The existing [workspace maintenance guard](../../peerhub/persistence/maintenance.py) can exclude open connections during maintenance; it does not distinguish V1 from V2 after the exclusive lock is released.

**[empirical_probe]** With production epoch/broker/vote logic and in-memory connection transport:

1. Created a V1 round and committed `engine_generation=2` into its state, reaching revision 2.
2. Called the real `mint_new_epoch()` through another store.
3. A vote through the previously pinned V1 store raised `WorkspaceMaintenanceError`, as intended.
4. Constructed a fresh V1 store/service and voted on the same target. The vote **committed**, advancing revision **2 -> 3**, while preserving `engine_generation=2`.

The old [SQL CAS](../../peerhub/persistence/sqlite_governance.py), lines 136-153, still checks target ID/revision only. Consequently, draft line 212's promised `1 < 2` rejection describes a new broker's behavior, not the behavior of the reopened old writer. This is an ordinary restart/reopen scenario; it does not require a malicious writer or bypass of a held OS lock.

**Required:** Specify the concrete exclusive activation sequence and persistent admission/storage restriction that prevents fresh or restarted V1 code from writing after the maintenance lock is released. If selecting a staged upgrade/drain, identify how old entrypoints are disabled and remain disabled before V2 writes become available. Include a reopened-old-runtime test alongside the existing-instance epoch test. A required-generation check implemented only in V2 cannot enforce this against V1.

**Other retained item — B4 risk-floor mapping: MEDIUM.** Draft line 105 now names a durable mapping, but supplies only an example, `high -> QUORUM`, while calling it an action-to-depth mapping. It does not identify a concrete configuration field/table or complete risk/default handling. The real resolver has action-depth overrides and externally supplied per-command minimums, not that mapping. Specify the authoritative mapping and unknown/missing-input behavior so restart/creation paths choose the same floor. The mandatory Final Call union at line 106 is accepted and should remain independent of this clarification.

**Final vote: REJECT as written.** Retain the verified B3 correction and input-only test migration, B5 recheck/revocation rules, B7 exact keys and provenance preservation, and the improved recovery prose. Close the old-runtime admission gap and reconcile the effect-count acceptance rows before ratification; finish the fixed-count and risk-floor definitions. The previously failing proposer-unanimity probes are no longer grounds for rejection.
