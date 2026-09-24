# Round 4 ratification review: governance and dispatch

Author: cx.astra. Date: 2026-09-24. **Verdict: NOT RATIFIED.**

R4 makes real progress: it accepts the common admission gate, plan containment, the compound Final Call requirement, the full execution-certainty table, the corrected meaning of ephemeral input, opt-in declared-preference routing, and policy-driven session thresholds. Those choices need no further architectural debate from me.

The remaining defects are **substantive design conflicts and omitted enforcement conditions, not merely cosmetic errors**. R4 adds the right rules near the beginning but leaves contradictory normative configuration, decision trees, and test oracles later in the same document. Its assertion that all B1-B10 objections are resolved is therefore premature. In addition, reversing the quarantine/review call order does not supply the atomicity or recovery contract requested in B6.

This review keeps the same scope and requirements as my [Round 3 review](GOVERNANCE-DISPATCH-R3-cx-astra-ratification-2026-09-24.md), referred to below as **Review3**. I am not reopening the choice of a thin orchestrator, requiring the full D13 ledger, or expanding the policy namespaces. No implementation, configuration activation, commit, or hub.py/peerhub dispatch was performed.

## 1. Evidence and direct answers to the requested checks

I read all 684 lines of [R4](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), compared every Review3 B-item and section 6 correction, and inspected the R3-to-R4 document diff. R4 line references below identify this exact reviewed artifact: SHA-256 `ecc24b8f7513fccc02a52279a4a4b071228c4fe2276be9ed699db0dfc4d46ec8`. These are document/source findings; no runtime capability or deployment-security verification is claimed. Implementation evidence remains **TEST NEEDED**.

| Requested check | Finding |
|---|---|
| Compound Final Call condition | **Yes in the new prose and selected default; not consistently throughout the specification.** Lines 75 and 153 express the union. Lines 378 and 412-415 still authorize skipping Final Call solely from the old mutually exclusive mode checks. |
| Certainty-to-recovery table | **Imported faithfully.** All five rows at lines 83-87 match Review3's five rows exactly, with no weakening. Line 89 also adds the shared serializable claim/revocation guard. TP-E-13 at line 550 still independently calls retained input safe for automatic replay, contradicting that table. |
| One consistent retention/cleanup rule | **No.** Lines 92-94 are correct, but the old delete-on-uncertainty config comment, preserve-always tree, age-only sweep, shared retry file, modified-content acceptance, and unconditional retained replay remain. See B5. |
| Real ByLimitId normalization and forecast removal | **Normalization is now a mandatory requirement, not a passing mention** at line 114. Its required data/selection contract is incomplete. Forecast removal is declared at line 115 but the forecast feature remains normative at lines 601 and 613. |
| Closure matrix readiness claims | **It does not claim implementation verification.** All seven rows say `Specified`, and the evidence column explicitly refers to later implementation. However, `Specified` overstates design closure while contradictory/missing rules remain, and the requested owner/entrypoint/case/evidence trace fields are absent. |

The terminal's spot checks identify genuine improvements. Two need narrower conclusions after reading the whole file: B3's new ordinary-dissent rule still conflicts with CD-Q-03 and the decision tree; B6's line 105 does not establish crash-safe review application. I do not dispute the existence of those new sentences, the saga signature change, the closure matrix, or the corrected many-to-one wording.

## 2. B1-B10 disposition

`PARTIALLY RESOLVED` below means specific requirements have been adopted but the complete B-item does not yet match Review3's required amendments and acceptance criteria. It does not withdraw approval of the resolved portions.

| Item | Status | Resolved portion | Precise remaining scope |
|---|---|---|---|
| **B1 — common gate and plan authority** | **PARTIALLY RESOLVED** | Unavoidable application evaluator, bounded parent approval, protected policy floors (lines 22-26). | No operative consultation/vote/ACK recursion exemption; no durable effective-policy/authorization record and atomic state/receipt/effect contract; defaults remain contradictory. |
| **B2 — Final Call integrity** | **PARTIALLY RESOLVED** | Trigger union, candidate identity, clearing ACKs on candidate change, frozen ACK set (75-76); retraction retained. | Old bypass rows remain; candidate/grant/dissent-origin binding and last-ACK authorization guards are incomplete; changed contract versus changed candidate and qualifying versus cosmetic concerns are not fully distinguished. |
| **B3 — vote/quorum semantics** | **PARTIALLY RESOLVED** | Ordinary dissent need not veto majority; typed BLOCK does; supersession is described (73-74, SM-06/08b). | Contrary dissent/duplicate-vote rows remain; explicit correction identity/state rules, electorate independence/absence, REVIEW timeout, and bounded reopening/outcome semantics remain incomplete. |
| **B4 — execution recovery** | **PARTIALLY RESOLVED** | All five recovery rows are exact; shared atomic claim/revocation guard is specified (81-89). | TP-E-13 contradicts replay permission; recovery around lost receipts/lease expiry and the no-cancellation crash acceptance cases have not been carried through to the state/exception specification. |
| **B5 — payload lifecycle** | **PARTIALLY RESOLVED** | Correct temporary-copy lifetime, retained canonical input separation, unique copies, content verification and INPUT_REQUIRED distinction (91-94). | Configuration and all principal transport counterexample rows still prescribe the old incompatible behavior; borrowed ownership, cleanup debt and retention precedence remain missing. |
| **B6 — restriction authority and review application** | **PARTIALLY RESOLVED** | Existing scope vocabulary is reused; stale-epoch rejection is retained; effect-before-final-resolution intent is recognizable (101-105). | Reordering is not atomic/recoverable application; authority-class composition, exact release, membership and operator/target distinction remain unspecified; old unsafe health rows remain. |
| **B7 — routing parity** | **PARTIALLY RESOLVED** | Off/advisory/opt-in choice and removal of prompt-length inference are explicitly selected (110-111, 174). | No binding-order preference rule or explicit-pin/hard-filter/no-candidate behavior is defined beyond matrix promises; quality provenance and invocation semantics remain unspecified. |
| **B8 — telemetry** | **PARTIALLY RESOLVED** | Mandatory ByLimitId normalization, dedup/conflict retention and a precise attempt inclusion window (113-116). | Normalize-both/preserve-identity/select-afterward contract and cases are missing; forecasts remain; old denominator/trend rules are not reconciled with the new rules. |
| **B9 — session policy** | **PARTIALLY RESOLVED** | Actual saga signature change, peer-agnostic thresholds and reuse of prepared generation after retry (118-120). | SS-02 contradicts the soft-band action; boundary/unknown-pressure presentation, checkpoint-failure, fingerprint/lease/continuity and generation acceptance rules are incomplete. |
| **B10 — host authority** | **PARTIALLY RESOLVED** | Trusted interface fields and no peer-consensus impersonation; deployment mechanism deferred (122-124). | Proof validation/rejection, exact subject or standing-scope containment, persisted proof, grant reuse and cooperative-process/deployment qualification conditions remain unspecified. |

## 3. Remaining amendments and acceptance criteria

These retain B1-B10 as the finite remaining issue set. Cross-references identify shared fixes; they are not additional blockers. The normative rules need to be consolidated in the tables and config, not placed in another introductory paragraph that leaves two incompatible outcomes.

### B1 — persist the gate's decision and prevent approval recursion

**R4 locations:** lines 22-27, 41-43, 139-146, 295-306, 640, 647-657. **Review3 requirement:** B1 amendments 1, 3, 4, 5 and its restart/consultation acceptance cases.

The common entrypoint and parent containment are now clear. But `default_depth = quorum` still applies to all unlisted actions, including consultation/vote/ACK purposes unless another rule exempts them. The closure matrix says “no approval recursion” without supplying that rule. The only policy retention details are a provenance hash, an assertion that the policy was snapshotted, and DEBUG logging. No record binding or durable commit boundary establishes what a restarted runner may execute.

**Required amendment:** State the bounded consultation/vote/ACK exemption explicitly, with actor/disclosure/effect guards retained and no exemption for implementing the proposed work. Specify that the request/round stores immutable effective policy values or an immutable durable reference, and that the runner requires the corresponding authorization reference. Define the atomic state/decision-receipt/pending-effect boundary in the existing store. Policy activation must use prior authority. Choose defaults for new direction, contained child, ordinary ask/broadcast, and consultation purposes, then make the compatibility table agree. A field/default/owner table is sufficient; no new framework is required.

**Acceptance:** A reviewer request and its vote do not open recursive consultation; an architecture ask with NONE still receives its protected floor; contained children reuse the same valid approval; changed scope does not. After process loss and a TOML edit, recovery uses the original durable policy and cannot execute from a hash or DEBUG log alone.

### B2 — apply the compound Final Call rule to every transition

**R4 locations:** lines 75-76, 152-153, 378, 411-428. **Review3 requirement:** all B2 amendments, especially candidate binding, mandatory trigger guards and atomic final authorization.

The compound rule itself is resolved. It must now govern the old rows. For example, a high-risk round with optional rule NEVER must enter Final Call under line 75 but resolves immediately under SM-14. SM-17 similarly permits a high-risk no-dissent round to skip. The TOML value at line 153 is also absent from the allowed-value comment immediately above it.

Candidate freezing is improved but omits authority references and dissent disposition/ancestry. “Changed candidate clears ACKs” does not distinguish a changed final presentation from changed subject/electorate requiring new votes. “All eligible ACK” still lacks an explicit candidate-match/live-authority predicate and atomic authorization transition. A blanket NACK still blocks cosmetic feedback.

**Required amendment:**

- Use one effective predicate: `is_tier0 OR is_high_risk OR has_unresolved_dissent_origin OR optional_final_call_requested`. Preserve dissent-origin obligation after arbiter disposition. Every skip row must require the mandatory portion to be false; update the config's allowed values accordingly.
- Bind candidate identity to decision/effect bounds, policy, current vote/disposition set and authority references. ACK/retraction names actor and that candidate. Changed subject/electorate/material contract requires fresh votes; changed candidate under an unchanged contract clears ACKs.
- Make the final ACK, candidate/live-grant/concern checks and authorization one atomic transition. Only a qualifying authority/safety/integrity/irreversibility concern blocks; cosmetic feedback does not. Apply B10 to exceptional human resolution.
- Clarify that SM-27 rejects rewriting the resolved round, not the separately authorized revocation record. Reopening must specify a new deadline and invalidate obsolete ACKs; B3 covers the approval basis.

**Acceptance:** Tier-0, high-risk and dissent-origin each independently require Final Call under every optional mode; ordinary low-risk no-dissent may skip. Reject wrong-candidate/pre-barrier ACKs; grant/candidate changes invalidate old proof; material contract changes require fresh votes. Cosmetic feedback cannot veto. Last ACK/retraction/revocation/claim races yield the already-agreed serial outcomes.

### B3 — replace the remaining contradictory vote oracles

**R4 locations:** lines 73-74, 305, 342, 359, 399-410, 425-427; REVIEW at 41-43 and 297-299. **Review3 requirement:** B3 amendments 1-5.

The selected ordinary-dissent/BLOCK policy is acceptable. Nevertheless, line 305 and CD-Q-03 still declare any explicit disagreement blocking. SM-12 still rejects every second vote as a CAS error, despite line 74 allowing duplicates/corrections. That is not just outdated commentary: these are the stated test oracles for exactly the cases being fixed.

**Required amendment:** Replace those rows with the chosen distinctions: identical mutation -> original receipt and one tally contribution; changed content without correction -> semantic conflict; explicit correction while voting -> supersede/invalidate the previous active vote, recalculate quorum, require a fresh valid vote. Define the post-Final-Call concern/supersession path instead of letting a second vote reopen anything automatically.

Add the frozen electorate's logical identities, required identities, independent non-proposer/failure-domain condition, denominator and absence reasons. High-risk required absences hold, and health changes never shrink an open electorate. Separate advisory one-reviewer REVIEW and its actual timeout source from binding consensus's minimum-two rule. Give resolved approval/denial/exception outcomes and reopening an explicit proof/deadline; a generic human/coordinator decision or timeout is not itself approval.

**Acceptance:** Majority plus ordinary dissent passes the configured vote predicate; BLOCK prevents authorization until valid disposition; correcting a decisive vote loses quorum; duplicate delivery counts once. Two profiles of one logical voter do not establish independence. A required high-risk absence holds; REVIEW has a defined timeout; reopening cannot reuse stale ACKs or expired authority.

### B4 — keep the exact table and close its recovery/transport integration

**R4 locations:** lines 81-89, 93, 99, EX-01..03 at 503-505, TP-E-13 at 550. **Review3 requirement:** B4's paragraphs following the certainty table and its no-cancellation crash acceptance matrix.

**No rewrite of the five recovery rules is requested. They are correct verbatim.** The serializable claim/revocation guard also answers the central atomic-fence objection. The remaining contradiction is TP-E-13's independent replay permission; fix it once under B5 and reference this table.

The exception specification still contains only the original three cancellation cases. It does not state the relevant lost-receipt/lease-expiry recovery outcomes that Review3 required. Adding the certainty table does not identify which receipt survives each crash boundary.

**Required amendment:** Add a compact crash table: before claim -> no effect; after durable claim with unobserved handoff -> uncertain until authoritative evidence; external effect before result commit -> reconcile, no unsafe replay; committed result before response/cleanup -> return that receipt and repair bookkeeping only. Require unique retry attempt identity, prohibit treating lease expiry as process death, and preserve successful broadcast children when reconciling siblings/parent. These rules belong in the existing runner, not a second recovery subsystem.

**Acceptance:** Exercise the four boundaries without any cancellation request, both claim/revocation serial orders, a still-live process after lease expiry, and a partial broadcast. Retained uncertain non-idempotent work remains blocked; a lost response or failed cleanup never causes another successful effect. These are future acceptance specifications, not a demand to implement or run tests during design review.

### B5 — make all transport rules use the corrected two-lifecycle model

**R4 locations:** lines 91-94 versus 183-195, 516-531 and TP-E-06..08/12/13 at 543-550. **Review3 requirement:** B5 in full.

The new section is faithful. The following old rules are still normative and cannot coexist with it:

| New rule | Remaining contrary rule |
|---|---|
| Copies live until consumed or consumer death; age is insufficient (92). | Config says ephemeral is deleted on crash/uncertainty (191); tree says preserve always (522); sweep deletes by age (531/545). |
| Distinct exclusively created retry/child copy; verify approved content (94). | Retry reuses its old file (543); modified content is read without mitigation (544). |
| Retained canonical bytes are separate from scratch; replay depends on certainty (93). | A preserved staged file is called safe for automated replay (550). |
| No durable prompt retention in default mode (92). | Default staging remains relative to durable `.peerhub/` (183-184), with optional failure debugging retention (520). |

**Required amendment:** Replace the staging tree, sweep rule and those TP rows. Use a private volatile scratch root; a sweeper requires managed ownership plus proof no consumer still needs the copy, not merely age. Keep retained canonical input under its separate protection/TTL policy. Cleanup policy cannot override consumer safety or silently retain ephemeral input durably. Define cleanup debt/recovery separately from request outcome. `INPUT_REQUIRED` means authorized bytes are actually unavailable, not that every crash resets execution certainty. Borrowed query files remain caller-owned, including on retries and cleanup; inline input follows the same retention policy.

**Acceptance:** A late live/unknown consumer survives a stale-age sweep; safe consumption/death permits cleanup; retries/children do not share paths; altered bytes reject. Missing input can coexist with an uncertain earlier effect. Retained bytes do not authorize non-idempotent replay. No default prompt bytes enter durable state/backup, a successful effect is not retried for cleanup failure, and borrowed files are not deleted.

### B6 — specify atomic review application and independent restriction release

**R4 locations:** lines 101-105, 469-494, 570-589 and 638. **Review3 requirement:** B6 amendments 1-5, not only ordering of the two calls.

Line 105 literally says apply the restriction “before creating the review target,” although the decision tree starts with an existing REQUESTED target. Even interpreting that charitably as “before finalizing the review,” it does not close the crash window. If restriction application commits and the process dies before review resolution, the review remains pending with an already-applied effect. A retry, concurrent DISMISS, or newer restriction needs an exact receipt and revision rule. Ordering alone establishes none of those.

The remaining health rows also retain the earlier semantic errors: already-open means generic no-op; any recovery closes quarantine; stale recovery leaves the circuit “closed”; profile-local restriction makes siblings unconditionally routable; execution uncertainty forbids every health consequence. None expresses independent, overlapping restriction classes.

**Required amendment:** Choose one of Review3's two bounded options and write its actual state transitions: (a) validate and commit review resolution, exact restriction, receipt and audit in one local transaction; or (b) commit a pending effect with stable identity, apply it idempotently, then reconcile the review from its applied receipt. In either case, validate review revision/target/operator scope first. Identical delivery is idempotent; a different incident/security/manual hold is not swallowed just because admission is already blocked.

Define restriction identity by scope/subject binding, authority class and current revision/epoch, with expiry policy. Release clears only the named current restriction under the proper authority; circuit recovery does not release administrative/security holds. Scope membership comes from registered bindings and is re-evaluated on binding change. HC-09 preserves the prior blocked state; HC-11 adds no sibling restriction but does not override other blockers. HC-06 prohibits inferring failure from uncertainty, while retaining independently trusted evidence and authorized operator actions. Separate authenticated operator/grant from target subject; absent authority leaves the effect pending, not applied. Keep the existing claim epoch check from line 89.

**Acceptance:** Crash after pending-effect commit and after restriction application reconciles one effect and one correct review disposition. Concurrent dismiss/escalate cannot leave a dismissed review silently authorizing a restriction. Wrong authority/target cannot produce an applied review. An old probe/release cannot clear a new hold; recovery leaves independent manual/security restrictions; shared and profile restrictions compose; a new security restriction is recorded even when another circuit is already open.

### B7 — turn the accepted routing modes into a bounded selection rule

**R4 locations:** lines 11, 110-111, 172-176, closure row 230. **Review3 requirement:** B7's preference mapping, pin precedence, eligibility and provenance rules.

The addition of opt-in automatic selection resolves the original O2 direction dispute. I accept it. The remaining gap is operational meaning: “capability matching and user preference” plus three mode names does not define how a hint selects a real profile, how a pin behaves, or what happens when every preferred binding is blocked. The matrix promises pins and hard filters but does not define them.

**Required amendment:** Specify an ordered preference mapping from declared hint/work shape to existing profile bindings, how the caller opts in, and the missing/unknown-hint outcome. Off/advisory cannot alter the selected binding; opt-in filters candidates by health, permission, actual binding/capability and scope before applying the declared order. An explicit pin selects exactly that eligible target or fails; no silent substitute. No eligible candidate yields an explicit blocker. Quality evidence remains ABSENT unless actually supplied; configuration preference is declared, unverified quality, not measured achievement. Remove the stale `prompt_length` source comment and advisory-only provenance claim.

**Acceptance:** Advisory leaves the selected profile unchanged; opt-in chooses the first eligible declared binding; a blocked pin rejects without fallback; no candidates explains the block; unknown quality is not scored from names or prompt length. No broader automatic-routing framework or learned scorer is requested.

### B8 — complete normalization and actually remove forecast behavior

**R4 locations:** lines 113-116, 601, 607-626. **Review3 requirement:** B8's five amendments and normalization/failure-window acceptance cases.

ByLimitId normalization is now a real requirement and should stay. It is still underspecified: there is no explicit rule to normalize both representations and every entry before family selection, no required identity/applicability preservation, and no ByLimitId-only/mixed-bucket case. “Retained safely” is not the conflict-selection or malformed-bucket behavior. Forecasts are still part of `full` output and the data-source table despite line 115 removing them. The failure denominator remains “total dispatches,” inconsistent with the newly selected definitive-started-attempt population unless explicitly restricted to that population.

**Required amendment:** Normalize `rateLimits` and every `rateLimitsByLimitId` entry before profile/family selection; preserve source, pool/account, limit/window/reset identity, applicability and freshness. Deduplicate identical observations, retain/report conflicts and unknown applicability, and localize malformed-bucket errors. Display and admission consume the same normalized snapshot. Add the finite input partitions from Review3 rather than naming the field only.

Delete the forecast output/data-source rows; historical deltas may use comparable observations within the same pool/window, with reset/change breaking continuity. Define displayed failure rate as `failed / (succeeded + failed)` over line 116's population, with both counts, separate cancelled/unknown/pre-admission counts, and partial coverage where appropriate. Label retry-inclusive attempt metrics accurately. Optional refresh is bounded, source-specific, and does not invoke a model or suppress independent valid sources.

**Acceptance:** ByLimitId-only, both-disjoint, duplicate, conflicting, unknown-family and mixed-valid/malformed inputs preserve the required information in diagnostic/admission views. Mixed retries/cancels/unknowns yield the selected counts; zero denominator is no data/completions, not 0%. Reset is not an ordinary trend, and no output requests or presents a forecast.

### B9 — reconcile the saga's decision table with its new thresholds and guards

**R4 locations:** lines 118-120, 437-458. **Review3 requirement:** B9's normalized policy input and boundary/guard acceptance cases.

The signature change is accepted and no longer disputed. The new soft-band action at line 120 still contradicts SS-02, which requests only PROCEED_WITH_REUSE with silent output. Exact soft/hard equalities are not consistently covered. Checkpoint issuance has a success message but no failure disposition. Unknown-pressure, forced-fresh and forced-reuse rows do not preserve the other admission/binding guards explicitly.

**Required amendment:** Use the chosen disjoint bands `< soft`, `soft <= p < hard`, `p >= hard`, plus unknown. Update SS-02 and the orchestrator response so the selected checkpoint/pending action actually occurs. Specify that a required checkpoint failure holds the attempt. Unknown/stale evidence may retain the chosen permissive reuse default but is displayed as unknown. Pressure policy cannot override verified capacity, fingerprint compatibility, lease ownership or required continuity. Fresh skips pressure preference, not those checks. Preserve the prepared-generation retry rule and explicit generation fencing.

**Acceptance:** Below/equal/above each threshold uses the same policy for every peer; unknown remains visibly unknown; required checkpoint failure holds; fingerprint mismatch does not resume; fresh/concurrent rotation cannot violate binding ownership; a stale generation cannot replace the active pointer, and rotation does not authorize replay of an uncertain old attempt.

### B10 — make the trusted approval interface fail closed

**R4 locations:** lines 122-124, 387, 424-427, 493. **Review3 requirement:** B10's validation, proof persistence/reuse and deployment qualification contract.

The field list and no-impersonation statement are useful, but there is no specified validation result when proof is missing, unverifiable, wrong-subject, expired or revoked. Only standing scope is listed; the exact request/candidate or validated containment of a standing grant remains undefined. The document does not bind the proof reference to the decision or state how existing authorization is reused.

**Required amendment:** Bind exact subject/candidate or an explicitly contained standing scope, effect/exception, issuer/designated principal, expiry, replay identity and revocation version; store its proof reference with the decision. Invalid, absent, out-of-scope or currently unverifiable required proof yields WAIT/REJECT, never a coordinator/actor-string substitute. Reuse valid scoped grants without another prompt. Deployment must test peer-origin issuance, proof replay and authority-store modification; until that boundary is demonstrated, state only a cooperative-process trust assumption and do not claim malicious-same-user resistance. The concrete isolation mechanism remains deferred as agreed.

**Acceptance:** Reject actor-string spoofing, untrusted issuer, wrong subject, expired/revoked proof and cross-candidate replay; hold when required validation is unavailable; reuse an existing valid grant. These application cases are separate from later real deployment isolation qualification.

## 4. Review3 section 6 corrections

| Correction | Status | R4 finding and remaining amendment |
|---|---|---|
| **6.1 — resolver/sub-policy contracts** | **PARTIALLY RESOLVED** | Comments now add types and nested immutability (33-68), but no complete field/default/owner table or explicit precedence/minimum table exists. `recovery_authority` is still absent from TOML; retained-input TTL and preference mapping lack a policy binding; REVIEW still needs timeout despite its optional consensus sub-policy. Complete the compact schema and bind each selected behavior to a field or fixed invariant. |
| **6.2 — default contradictions** | **NOT RESOLVED** | `consensus.propose = unanimous` at 143 still conflicts with quorum-default compatibility at 647; blanket current-behavior claims remain at 648. Changing `task.create` to quorum does not fix either issue or approval recursion. Publish one purpose-aware default/minimum table and describe intentional changes. |
| **6.3 — unknown-key rejection and validation** | **PARTIALLY RESOLVED** | Nested immutability is now explicit. Core-key rejection itself is **not resolved**: R-06 at 269 still ignores unknown keys. Reject misspelled governing keys, define inert extensions if needed, and validate retention/recovery enums, durations, bounds and incompatible combinations. The compound Final Call enum must be accepted consistently. |
| **6.4 — many-to-one mapping** | **RESOLVED** | Line 11 correctly calls the mapping many-to-one and explicitly not bijective. No further change is required for this item; no numeric compatibility interface is demanded. |
| **6.5 — ingress/source ownership** | **PARTIALLY RESOLVED** | Line 27 supplies exactly-one and query preservation, but “gets the chosen outcome” does not choose the empty/invalid outcome. Define allowed inline/stdin/file forms, invalid/empty behavior, encoding/size handling and borrowed ownership for ask/broadcast; connect those rules to B5's cleanup path. |
| **6.6 — counts and deterministic oracles** | **PARTIALLY RESOLVED** | SM-07/31 are more specific, but contradictory rows remain. I count **133 distinct case IDs**, including new SM-08b: 20 resolution + 26 consultation + 32 consensus + 10 session + 10 quarantine + 3 execution + 13 transport + 11 health + 8 telemetry. The unnumbered cross-cutting examples are separate. The arithmetic error is cosmetic; incompatible expected outcomes are not. |
| **6.7 — actual service changes** | **PARTIALLY RESOLVED** | The saga signature change is now explicit, and runner ownership is clear. Review-effect reconciliation, durable gate records and other missing contracts still need defined service owners. Do not label a call-order swap sufficient. No implementation code or lines-of-code estimate is needed. |

For schema acceptance, a reader should be able to resolve an absent-file default, a workspace preference, a protected-floor conflict, REVIEW timeout, retained input and opt-in routing without inventing a field or consulting an obsolete table. A misspelled governing key must produce an error. This is the same self-contained-specification requirement as Review3, not a new configuration system.

The case-count total, duplicate section numbering, the closure heading's `B-5` label and minor wording are **nonblocking editorial corrections**. I would not require another review round solely for those. The numbered contradiction rows above remain blockers regardless of how the summary is counted.

## 5. Closure matrix disposition

The seven-row scope is **accepted**. Keeping the full legacy ledger and namespaces out of this round remains **accepted**. The column “Required closure evidence in later implementation” is correctly forward-looking: I do not read `Specified` as `Implemented` or `Verified`.

However, design specification and implementation verification are two different axes. For the current revision, use the following honest status until the relevant B-items close:

| Matrix row | Design status now | Implementation/boundary evidence |
|---|---|---|
| Consultation and plan authority | Partial — B1/B3/B10 and schema corrections | Not verified by this design round; TEST NEEDED |
| Final Call | Partial — B2/B3/B10 | Not verified by this design round; TEST NEEDED |
| Review quarantine | Partial — B6/B10 | Not verified by this design round; TEST NEEDED |
| Session rotation | Partial — B9, with B4 recovery guards | Not verified by this design round; TEST NEEDED |
| Profile selection | Partial — B7 | Not verified by this design round; TEST NEEDED |
| Telemetry | Partial — B8 | Not verified by this design round; TEST NEEDED |
| IPC and execution recovery | Partial — B4/B5 and ingress/schema corrections | Not verified by this design round; TEST NEEDED |

Add each row's native entrypoint, service owner and relevant case/acceptance IDs, with an explicitly empty/pending evidence reference until implementation supplies one. This is the compact traceability requested in Review3; it does not require creating or running tests now. Once the contradictions are removed, `Specified; implementation unverified` is appropriate even before implementation exists.

The broader legacy capability/caller check remains a separate cutover obligation. Ratifying this design would not by itself establish full replacement readiness or authorize deletion.

## 6. Bounded path to convergence

No additional architectural round is needed to choose the vessel. The next revision should perform these already-required repairs:

1. Replace the obsolete dissent/correction/Final Call, transport, health, session and telemetry rows at the cited locations; make their expected outcomes follow the accepted new rules.
2. Supply the missing compact durable gate, review-effect transaction/recovery, authority validation, routing and normalization contracts. These are the residuals of B1-B10, not a new feature list.
3. Make defaults, field validation, ingress rules and closure status agree with those contracts. Preserve the exact B4 table. Remove forecasts and unsafe retained-replay language from every normative location.

A statement that introductory sections take precedence would clarify some conflicts but would not supply the missing state/authority/recovery rules. Conversely, fixing a cosmetic count alone would not change this verdict. I will not reopen the accepted structural choices or demand broader legacy work to close these items.

**Final verdict: NOT RATIFIED.** The core direction has converged; the self-contained behavioral specification has not. The remaining work is a bounded consolidation and completion of the previously requested rules, including several execution/privacy/authority guarantees that cannot be left for implementers to guess.
