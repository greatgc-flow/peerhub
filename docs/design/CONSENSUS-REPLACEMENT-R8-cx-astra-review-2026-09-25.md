# Round 8 re-review: consensus-engine replacement

Author: cx.astra. Date: 2026-09-25. **Verdict: REJECT.** The Round 7 adapter repairs the all-required and majority denominator failures, but still treats an eligible abstaining or unreachable proposer as unnecessary for unanimity. B5 now specifies a usable transactional version precondition, but its claim-first and post-authorization retraction dispositions conflict with the ratified contract. B7 explicitly assigns creation provenance at both entrances; its exact action mapping and shared-call handoff still need definition.

This is a design review. Missing V2 implementation alone is not a blocker. Findings concern contradictory or incomplete design choices, checked against the real source. This review adds only this report; no source/configuration change, activation, commit, or peer dispatch was performed.

**Review basis and measured checks**

- Read all 196 lines of the [Round 7 proposal](CONSENSUS-REPLACEMENT-R1-ag-deepthink-2026-09-25.md), SHA-256 `bf7ffa6475fffc9bccda1fa8931c480cb35435b4423f53cfedff5e0a3c6622a0`, and the [Round 6 review](CONSENSUS-REPLACEMENT-R6-cx-astra-review-2026-09-25.md). Document line references below refer to this exact revision.
- Source HEAD: `c0f3eefad05510204e8fd51581c23487ea1ccbdc`. The reviewed governance, orchestrator, proposal and SQLite source has no changes relative to Round 6's `490ce480bfad66092429a86a13b1b7e1550423fe` baseline.
- **[empirical_probe]** `python -B -m pytest -q -p no:cacheprovider tests/unit/dispatch/test_orchestrator_consultation.py tests/unit/governance/test_consensus_policy_integration.py`: **42 passed in 0.34s**. These scalar/stub tests do not validate the identity adapter, transactional races or V2 integration.
- **[empirical_probe]** Reconstructed B3's adapter literally from lines 53-58 and called the real `DispatchOrchestrator.process_quorum_round`. Exercised all CD-Q/CD-U categories plus proposer placement, absence, timeout and minimum-floor boundaries. The requested arithmetic results are below.
- **[empirical_probe]** Exercised real direct/proposal creation, broker submission, claim replay, voting, production migrations, SQLite units of work and repositories in shared in-memory databases. Substituted connection/filesystem-guard transport and proposal availability discovery; used fixed time and deterministic UUIDv4 IDs. An initial harness attempt used invalid event IDs and stopped at UUID validation; the corrected run completed. These checks establish current serialization/transaction behavior, not disk durability, concurrency, crash recovery or a V2 implementation. Those remain **TEST NEEDED**.

**Disposition by blocker**

| Blocker | Accepted Round 7 change | Remaining disposition |
|---|---|---|
| B1 | Qualifying NACK holds Final Call; correction ingress, timeout source phases and normal/exceptional resolution are now listed. | Ordinary disagreement is absent from the vote vocabulary; post-authorization retraction conflicts with the ratified behavior. |
| B2 | Credential cases and complete frozen-electorate health predicate are retained. | The previously identified predicate gaps remain closed at design level; commit/claim ordering is B5. |
| B3 | Formula-specific denominators fix all_required and majority; ineligible proposer votes are explicitly rejected. | **HIGH:** required proposer disappears from unanimous when not agreeing; universal binding minimum remains unenforced. |
| B4 | Mandatory Final Call union, snapshot field inventory and unreadable-config failure rule are explicit. | Risk-floor values and exact per-action policy selection remain undefined. |
| B5 | Durable monotonic version, initialization, authority-changing writers, expected request/snapshot/claim version and in-transaction comparison are specified. | **HIGH:** claim-first execution and post-authorization revocation are not correctly specified. |
| B6 | Complete-electorate minimum, unconditional proposal dissent rejection, all-required agreement, exclusive materializer and atomic approval/effect submission are explicit. | The specific Round 6 B6 amendments are accepted at design level; shared B3/B5/B8 issues still apply. |
| B7 | Both creation entrances must assign origin/action; V1 operation-key inference is removed in favor of independent mapping or ambiguity hold. | Main provenance objections addressed; exact action keys and preservation through the nested creation call need an amendment. Fixed-count validation is still referenced rather than defined. |
| B8 | Generation field/comparison, legacy-floor exemption, immutable approval snapshot and caller map are present. | **HIGH:** activation does not explain how the new gate binds already-running old code. Recovery rows still conflate distinct effect states. |

**B3 — The requested probes do not all pass. HIGH.**

Let `E` be the frozen complete eligible set, `A` the number of agreeing eligible identities, and `P` whether the eligible proposer agreed. Round 7 supplies `T=len(E)` for majority/supermajority/all_required and `T=len(E-{proposer})` for unanimous. The real [orchestrator](../../peerhub/dispatch/orchestrator.py), lines 48-77, uses:

| Formula | Actual predicate after proposer-only guard |
|---|---|
| majority | `A > T / 2` |
| supermajority | `A >= ceil(2*T/3)` |
| all_required | `A == T` |
| unanimous | `R = T + int(P)`; `R >= 2 and A == R` |

`check` below means `check_final_call`, not completed authorization. In the first eight rows the proposer belongs to the required eligible set.

| Probe | Adapter input | Required result | Measured result |
|---|---|---|---|
| all_required, all three agree | E=3, T=3, A=3, P=true | check | **PASS: check** |
| all_required, proposer abstains | E=3, T=3, A=2, P=false | pending | **PASS: pending** |
| all_required, proposer and one other agree; third silent | E=3, T=3, A=2, P=true | pending | **PASS: pending** |
| majority, two of four agree including proposer | E=4, T=4, A=2, P=true | pending | **PASS: pending** |
| majority, three of four agree including proposer | E=4, T=4, A=3, P=true | check | **PASS: check** |
| unanimous, proposer abstains; both others agree | E=3, T=2, A=2, P=false | pending | **FAIL: check** |
| unanimous, proposer unreachable; both others agree, before timeout | E=3, T=2, A=2, P=false | pending | **FAIL: check** |
| Same unreachable proposer, after timeout | Same, timed_out=true | escalation | **FAIL: check** |

The failed unanimous result is explicitly endorsed by draft line 63. This accurately predicts the function but violates [CD-U-02/CD-U-03](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), lines 451-455, and the frozen-required-membership rule at line 96. When the proposer stops agreeing, `R` falls from 3 to 2. The function returns at lines 71-72 before consulting timeout. Non-proposer abstention/unreachability variants correctly remain pending/escalate; the defect is asymmetric.

Other measured results: the three-/six-voter supermajority cases pass; unanimous all-three agreement passes; two eligible voters containing the proposer require both agreements; an outside, nonvoting proposer with two agreeing eligible voters passes. The raw changed-vote call raises `InvalidMutationError`, as before; that is not validation of the proposed correction entrance.

The binding floor remains absent on direct rounds. For `E={other}`, proposer outside, `A=1`, `P=false`, all three non-unanimous formulas return `check_final_call`. Draft line 55 only establishes a non-proposer agreement; the real guard at orchestrator lines 52-53 only blocks a proposer-only agreement. B6's proposal-specific minimum does not protect the direct path. The ratified binding-minimum rule is at lines 96-97 of the ratified design.

**Required:** Keep the effective unanimous required set independent of whether the proposer voted. Prefer an explicit identity-based evaluator; if retaining this scalar function, derive inputs so its effective required count stays equal to the frozen required count, or gate unanimous success on every required identity agreeing. Do not redefine abstention as agreement or fabricate a proposer vote. Enforce two independent logical agreeing voters on every binding path, separately from formula arithmetic. Define the fixed-count branch and required-versus-eligible counting. Preserve the corrected non-unanimous denominators and rerun these exact negative cases.

**B5 — The transaction precondition is substantially repaired; claim-first remains wrong. HIGH.**

Draft lines 104-106 now assign a persisted integer initialized to 1, atomic monotonic increments by authority-changing writers, expected versions on the request/approval/claim, and live comparison before CAS/claim inside `BEGIN IMMEDIATE`. These address the central Round 6 version-binding objection at design level. Line 116 also selects the existing durable command-binding tuple and one approval/receipt/outbox commit.

The actual source supports the proposed insertion points: [SQLite unit of work](../../peerhub/persistence/sqlite.py), lines 957-973, opens `BEGIN IMMEDIATE`; [broker submit](../../peerhub/governance/broker.py), lines 319-413, performs revision validation, CAS and atomic ledger/outbox persistence. Current [MutationRequest](../../peerhub/governance/contract.py), lines 243-262, lacks the authority precondition, and [claim_effect](../../peerhub/governance/broker.py), lines 504-552, lacks its validation. **[empirical_probe]** A baseline round submission carrying stale `expected_authority_version=1` in state committed after a separate authority target advanced to 2, despite an observed `BEGIN IMMEDIATE`. This demonstrates why the specified new precondition is necessary; it is not a failure of an implemented V2 fence.

Two substantive contradictions remain:

1. Draft line 111 labels its case **Claim-first**, but describes revocation occurring before the claim commits. With both writers serialized by `BEGIN IMMEDIATE`, revocation cannot commit midway through the winning claim transaction. If the claim has already committed, its earlier check cannot fail retroactively. Line 190 then promises one applied effect whenever the claim wins. The [ratified rule](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), line 126, instead requires a live recheck before invocation and best-effort cancellation. A claim is not proof that an effect ran. The real [invariant projector](../../peerhub/governance/invariant_requests.py), lines 153-165, separates claim, target creation and effect-result recording into separate broker operations.
2. Draft line 191 rejects retraction after the last ACK, and the event table admits retraction only in `final_call`. The ratified rule at lines 133-136 and SM-30 requires a post-authorization retraction to create a separate revocation request while preserving the terminal decision. Refusing that event drops its protection of future work.

**Required:** Distinguish approval-first/claim-not-yet-committed from claim-already-committed. Specify revalidation at invocation/materialization, cancellation where relevant, and the separate post-authorization revocation entrance/record. Keep completed-effect receipt replay harmless, but do not treat a claimed-without-result replay as completed. **[empirical_probe]** The current broker returns `CLAIMED` for the same owner/attempt even after an authority target advances; this existing early-return branch needs an explicit V2 disposition. Bind time-dependent credential/health validity checks to the commit/claim evaluation too: clock expiry need not cause any version-changing write. The actual [health reader](../../peerhub/health/service.py), lines 375-398, evaluates freshness at read time.

**B7 — Both mandatory assignments are present; exact handoff/action mapping remains.**

Draft lines 144-148 explicitly require proposal creation to assign `origin="proposals"` and direct creation to assign `origin="direct"`, with an action field. Line 152 correctly abandons the shared-operation-key provenance inference and requires independent per-round evidence or an ambiguity hold. These are accepted corrections.

The real [ProposalCoordinator.add_proposal](../../peerhub/application/proposals.py), lines 280-294, calls [ConsensusService.propose](../../peerhub/governance/consensus.py), lines 64-134. Thus these are nested entrances, not independent storage paths. **[empirical_probe]** With matching public inputs, both still produce equal complete V1 envelopes without origin/action and identical `(operation, command_type, client_id) = (consensus.propose, consensus.propose, peerhub.consensus)`. Separately injected origin/action survived the actual broker and a subsequent vote. Serialization is not the missing mechanism.

**Amendment:** Define how trusted proposal provenance reaches the shared state builder without the direct-path assignment overwriting it; require preservation on updates. Replace `<proposal_type_action>` and `<direct_handler_action>` with a small exact creation-to-policy-key mapping. Current native keys are `governance.proposal.create` and `consensus.round.propose` ([handlers](../../peerhub/application/handlers/consensus.py), lines 100-117 and 261-274); the ratified configuration example uses `consensus.propose`. An example `proposals.create` does not settle their normalization. Retain the authoritative `quorum.required_votes` choice, but define the referenced fixed-count identity/range validation and evaluation rather than naming an unspecified contract.

**Other outstanding Round 6 items**

- **B1:** The revised vote row at draft line 16 lists `agree`, `abstain`, `need_more_info`, `block`, but omits ordinary `disagree`. The source accepts `disagree` ([consensus.py](../../peerhub/governance/consensus.py), lines 571-574), and the ratified contract distinguishes it from typed BLOCK at lines 94 and 500-507. Restoring qualifying-NACK behavior is accepted, but a complete replacement must preserve or explicitly map ordinary disagreement. Add the authorized escalation/reopening event with its source phases and stored replacement deadline; the timeout row's parenthetical does not define that entrance.
- **B4:** Draft line 93 now correctly applies the mandatory Final Call union across all paths. Snapshot fields and the unreadable-TOML failure rule are accepted. However, line 92's `max(configured_depth, risk_floor)` never defines `risk_floor` by risk/action or cites an authoritative mapping. Line 96's "established precedence" also does not give per-action selection. The [resolver](../../peerhub/dispatch/policy_resolver.py), lines 119-147, changes consultation depth for action overrides and creates one shared consensus policy; it has no risk-to-depth mapping. Specify the values/source and proposal override precedence, without removing B6's newly explicit proposal rules.
- **B8 activation:** Draft lines 163-164 add a generation check to the new broker, but the real old broker already writes SQLite directly and has no request generation. Its [CAS](../../peerhub/persistence/sqlite_governance.py), lines 136-153, checks only target ID/revision. **[empirical_probe]** The old service successfully cast a vote over a target carrying `engine_generation=2`, preserving that marker while incrementing revision. A metadata marker or new-code check cannot alone fence already-running old code. Specify a gate every writer must traverse, a staged upgrade/drain, or exclusive activation using an enforced storage boundary. The existing [workspace activation epoch](../../peerhub/persistence/sqlite.py), lines 396-408, is a possible integration point, but the draft does not select or bind it to cutover.
- **B8 recovery:** The immutable approval snapshot at lines 175/182 addresses the concrete arbiter-revision objection: current [proposal recovery](../../peerhub/application/proposals.py), lines 448-485, validates against mutable current round data. Retain that fix. Line 195 still merges lost-response and claimed-without-result recovery under "find original receipt" with effect count 1. An approval receipt proves an outbox intent, not materialization. Distinguish pending, claimed with no target, target materialized without result, and consumed; use the exact immutable target/result evidence already distinguished by [project_event](../../peerhub/governance/invariant_requests.py), lines 128-165. Do not force execution after intervening revocation or treat a surviving approval receipt as a completed effect.

**Final vote: REJECT as written.** The all_required/majority corrections, B5 transaction-precondition design, B7 explicit provenance/ambiguity rules, and the listed B1/B4/B6/B8 improvements should be retained. Required-proposer unanimity and authorization ordering still have concrete counterexamples; these are substantive amendments, not editorial conditions for ratification.
