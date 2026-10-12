# Round 5 ratification review: consolidation remains incomplete

Author: cx.astra. Date: 2026-09-24. **Final verdict: NOT RATIFIED.**

Several previously requested fixes are now complete, including removal of forecast output, correction of retained-input replay permission, unique retry copies, content verification, and selection of durable pending-effect reconciliation for quarantine review. I accept those changes and do not request another architectural alternative.

The full revision still contains old contrary rules and leaves previously identified contracts unspecified. Examples are the blanket disagreement veto, age-only sweep, generic already-quarantined no-op, and unknown-governing-key acceptance. The missing durable policy/authorization binding, routing selection behavior, and host-proof validation requirements also remain. These affect behavior; they are not case-count or editorial objections.

This is a design-only review against the finite scope of my [Round 4 review](GOVERNANCE-DISPATCH-R4-cx-astra-ratification-2026-09-24.md), here called **Review4**. No implementation, commit, configuration activation, or hub.py/peerhub dispatch was performed. The lighter architecture and exclusion of the full D13 ledger/namespace model remain accepted.

## 1. Reviewed revision and fixes that are closed

I read the complete overwritten [revision](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), 679 lines, SHA-256 `610836568944752b9904554dffa8c523beb14f5ada2803ec8f23989faa895bfa`. **All revision line references below refer to this hash**, not the older 684-line version reviewed in Round 4. The filename still says R4; this review concerns its Round 5 contents.

These particular fixes are **RESOLVED**:

- **SM-14 and SM-17 skip guards:** lines 411 and 414 now exclude mandatory-floor cases. The compound trigger is still explicit at lines 75 and 153. Other Final Call requirements remain under B2 below.
- **CD-Q-03 and SM-12 local corrections:** lines 341 and 409 no longer impose the old ordinary-dissent veto or reject every correction as a CAS failure. The decision tree and remaining correction protocol still need B3's completion.
- **B4 certainty table and retained replay dependency:** all five rows at lines 83-87 still match the original requested rules exactly. TP-E-13, line 547, now defers replay permission to that table. No independent retained-input replay authorization remains in that row.
- **Per-attempt scratch integrity:** TP-E-06/07 at lines 540-541 require distinct retry/child copies and reject changed content. TP-E-08 now explicitly forbids age-only deletion of active copies.
- **Volatile staging direction:** the operative declaration at lines 184-189 selects private volatile scratch and consumption/death cleanup. I accept that location/lifetime choice. Its resolver representation and the contrary sweep still need consolidation under B5/6.1.
- **Quarantine transaction strategy:** line 105 now selects pending effect -> idempotent apply -> review reconciliation from the receipt. The objection to merely reversing two calls is closed. Remaining identity/authorization/release rules are under B6.
- **Specific restriction cases:** QR-E-03, HC-09 and HC-11 at lines 486, 584 and 586 respectively preserve new restrictions, preserve a stale probe's prior blocked state, and retain independent shared restrictions on siblings.
- **Normalize-before-select and forecast removal:** line 114 now requires both rate-limit representations, every named entry, identity preservation, deduplication and localized malformed-bucket errors. Forecast output and its data-source row are gone; the only forecast line is the removal note at 115. Those defects are closed.
- **Session soft-band direction and reuse checkpoint failure:** SS-02 and SS-08 at lines 451 and 457 now select checkpoint/pending behavior and hold on checkpoint failure. The prepared-generation retry rule remains at 120.
- **Proposal default mismatch and arithmetic:** `consensus.propose` is quorum both in configuration and compatibility text (143, 643). The detailed summary at 665-675 correctly totals **133 distinct case IDs**, including SM-08b.

The terminal's seven spot checks are valid as checks of these specific edits. They do not establish that every old competing rule was removed or that the previously missing contracts were supplied. No runtime implementation or security qualification is inferred from document changes.

## 2. Binary disposition of the full B-items

The requested labels apply to each **complete Review4 item**, not just its most visible edited sentence. `NOT RESOLVED` does not reopen the closed fixes above; the last column is the remaining scope only.

| Item | Verdict | Remaining scope in the current revision |
|---|---|---|
| B1 — durable gate/plan authority | **NOT RESOLVED** | Consultation recursion exemption, durable policy/authorization binding and atomic decision/effect persistence are still unspecified. |
| B2 — Final Call integrity | **NOT RESOLVED** | Old skip decision tree remains; candidate authority/dissent-origin binding, contract supersession, last-ACK live checks and concern classification remain incomplete. |
| B3 — votes/electorate | **NOT RESOLVED** | Blanket disagreement branch remains; duplicate versus correction semantics, electorate independence/absence, REVIEW timeout and bounded reopening remain incomplete. |
| B4 — execution recovery | **NOT RESOLVED** | Replay rule conflict is fixed; the already-requested crash-boundary, lost-receipt, lease-expiry and partial-broadcast recovery rules are still absent. |
| B5 — payload ownership/cleanup | **NOT RESOLVED** | Age-only sweep remains; scratch versus retained-input handling, missing-input condition, borrowed ownership and cleanup debt are not fully reconciled. |
| B6 — restrictions/review effects | **NOT RESOLVED** | Pending-effect strategy is accepted; old no-op/recovery rules and missing restriction identity, release authority, validation and review-state guards remain. |
| B7 — routing | **NOT RESOLVED** | The three mode names still lack the requested preference mapping, pin/filter/no-candidate behavior and provenance contract. |
| B8 — telemetry | **NOT RESOLVED** | Normalization and forecast fixes are accepted; applicability/shared snapshot, failure-population presentation, comparable-window trend and bounded refresh rules remain incomplete. |
| B9 — sessions | **NOT RESOLVED** | Soft-band choice is corrected, but its orchestrator action and existing compatibility/continuity/unknown-pressure guards remain unspecified. |
| B10 — host authority | **NOT RESOLVED** | Required-proof validation, subject binding, persistence/reuse and deployment qualification conditions remain only a field list and intent. |

The remaining work is therefore not confined to a small cosmetic tail. Several entire residual requirements from Review4 were not added. The following sections identify them without adding new design scope.

## 3. Precise remaining amendments and acceptance criteria

### B1 — NOT RESOLVED: durable gate and consultation exemption

**Evidence:** Lines 22-27 still name the evaluator and plan containment; lines 139-146 apply quorum to unlisted actions. “No approval recursion” appears only as required future closure evidence at 225. Lines 636 and 649-653 assert snapshotting/logging but do not bind durable effective values or an immutable policy reference and authorization receipt to the request/round. This is the same gap as Review4 B1, not a new storage architecture request.

**Required amendment:** Explicitly exempt bounded consultation/vote/ACK collection from recursive consultation while retaining actor, disclosure and effect guards. Define the persisted effective-policy/authorization references checked by the runner and the existing-store atomic boundary for decision state, receipt and pending effect. State that standing-policy activation is authorized under the prior policy. Provide the already-requested purpose-aware default/minimum table for new direction, contained work, ordinary ask/broadcast and consultation.

**Acceptance:** Reviewer request -> vote does not recurse; an architectural request cannot lower its standing floor; bounded children reuse valid approval. Restart after a TOML change recovers the original policy and authorization from durable records, not DEBUG output or an unresolvable hash.

### B2 — NOT RESOLVED: complete Final Call predicate and proof

**Evidence:** The corrected skip rows are accepted, but the phase diagram at line 377 still resolves on NEVER or no dissent with DISSENT_ONLY without the mandatory guard. Lines 412-415 still use enum variants excluded by the current configuration's allowed values. More substantially, line 76 still omits authority references and dissent disposition/origin from the candidate contract; SM-19/20 at 416-417 still say all eligible ACKs approve and any NACK holds, without the requested live-proof/candidate/concern guards.

**Required amendment:** Replace the old diagram's skip predicate with the same effective mandatory-OR-plus-optional predicate used everywhere else. Remove obsolete enum rows or map them explicitly without a bypass. Preserve dissent-origin obligation after arbiter disposition. Bind ACK/retraction to actor and exact candidate, including authority/disposition references; distinguish changed candidate from material contract/electorate change requiring fresh votes. Last ACK, live required authority, candidate validity, absence of qualifying concerns and authorization must commit together. Cosmetic feedback is not a veto. Reopening gets a defined deadline and invalidates obsolete ACKs.

**Acceptance:** Every supported optional mode obeys all three mandatory triggers. Wrong-candidate ACK and expired/replaced grant cannot authorize. Material decision changes require votes, cosmetic feedback cannot veto, and ACK/retract/revoke/claim follows the already-agreed serial history. These were all in Review4 B2.

### B3 — NOT RESOLVED: ordinary dissent still has two outcomes

**Evidence:** CD-Q-03 and SM-06 now allow ordinary majority dissent, but line 304 still says `explicit disagree -> blocked`. SM-12 permits a correction but does not distinguish an idempotent duplicate from changed content without correction or state the decisive-vote invalidation/re-evaluation rules. REVIEW still waits on a timeout at 296-298 while its `consensus` sub-policy is meaningful only at depth >= QUORUM (41-43). The previously requested logical-voter/failure-domain/required-absence and reopening proof/deadline rules remain unspecified.

**Required amendment:** Replace line 304 with the chosen threshold/required-voter/typed-BLOCK semantics. Specify same-key duplicate -> original receipt/count once; unauthorized changed vote -> conflict; explicit correction while voting -> invalidate old contribution and recompute before a fresh valid vote can restore it. Freeze logical identities, required membership, independent non-proposer condition, denominator and absences; high-risk missing required voters hold and health cannot shrink an open electorate. Give advisory REVIEW a timeout source distinct from binding minimum-two consensus, and make reopened/exceptional resolutions retain explicit proof, outcomes and deadlines.

**Acceptance:** The same majority-plus-ordinary-dissent input has one result in the tree and tables. Decisive correction loses quorum; duplicates count once; two profiles do not become independent voters; required high-risk absences hold. REVIEW and reopened rounds have bounded, defined outcomes.

“Approved” in CD-Q-03 should be edited to “vote predicate satisfied; evaluate remaining obligations.” I do not create a new blocker for that word by itself: the existing mandatory Final Call rule already prevents interpreting a majority vote as permission to execute.

### B4 — NOT RESOLVED: crash boundaries remain unspecified

**Evidence:** The exact certainty table and shared claim/revocation guard at 81-89 are sound, and TP-E-13 no longer conflicts. The only execution exception rows remain EX-01..03 at 502-504, all concerning cancellation. Review4 explicitly required recovery rules for crashes without cancellation, lost receipts, lease expiry and partial broadcast; those were not added.

**Required amendment:** Add the compact boundary table already requested: before claim -> no effect; after durable claim but uncertain handoff -> reconcile uncertainty; external effect before result commit -> no unsafe replay; committed result before response/cleanup -> return existing receipt and repair bookkeeping. Preserve unique retry attempt identity, state that lease expiry is not process death, and do not repeat successful children when recovering a partial broadcast.

**Acceptance:** The four boundaries without a cancellation request, lease expiry with a live old process, lost response after completion, and mixed broadcast results all have a defined surviving record and effect count. No new recovery framework or live tests are requested; the missing behavior must simply be specified.

### B5 — NOT RESOLVED: one age-only sweep remains active

**Evidence:** The major TP-row fixes and private volatile-root choice are accepted. However, lines 527-528 still invoke an age-based sweep and say it removes files older than max_age, contradicting line 92 and corrected TP-E-08. That sweep also names a config field removed from the example. The transport tree at 515-519 distinguishes definite/uncertain outcomes and says retained files are preserved durably, without separating disposable transport copies from retained canonical input. TP-E-12 at 546 still yields INPUT_REQUIRED on recovery unconditionally, although line 92 correctly limits this to missing bytes. Borrowed source ownership and cleanup debt are still unspecified.

**Required amendment:** Replace the sweep with managed-ownership plus no-further-consumer-use guards; age can select candidates, not authorize deletion. Apply consumption/death cleanup to scratch copies in either retention mode; retained canonical bytes use their own protection/TTL/deletion policy. Preserve INPUT_REQUIRED only when exact authorized input is unavailable, independently of prior execution certainty. State borrowed-file non-deletion, inline retention parity and cleanup-debt handling. Give `PRIVATE_VOLATILE_ROOT` a defined resolver contract and make validation/config describe that representation consistently.

**Acceptance:** Old-but-live or unknown-consumer scratch survives a sweep; safe consumption/death permits owned-copy cleanup; retained canonical input does not make scratch immortal; borrowed files survive. Missing input and uncertain earlier execution can coexist; existing exact input does not spuriously require re-entry; cleanup failure never reruns the effect.

The literal `??` tree glyphs at 516-519 and the leftover `.peerhub/` comment at 183 are editorial damage. They do not independently block approval. The age-only deletion instruction and missing ownership/recovery conditions do.

### B6 — NOT RESOLVED: accepted effect strategy still needs identity and authority guards

**Evidence:** Line 105 now supplies the correct pending-effect strategy. QR-E-03, HC-09 and HC-11 are corrected. But the decision tree at 474 still turns any already-quarantined subject into an idempotent no-op, contrary to QR-E-03. QR-E-10 at 493 still says RECOVER closes quarantine without distinguishing operational recovery from exact authorized restriction release. HC-06 at 581 still prohibits every health consequence on execution uncertainty. The document still lacks the requested restriction identity/authority/expiry and membership/release rules and pre-effect review-revision/operator-scope validation.

**Required amendment:** Keep the chosen pending-effect pattern. Bind it to a stable effect identity, exact restriction target/authority class/current revision, and a validated review revision/operator grant before committing the pending effect. Define pending/applied reconciliation and concurrent dismissal guards; identical delivery alone is a no-op. Replace line 474 accordingly. Release removes only the named current restriction under the appropriate authority; circuit recovery leaves independent manual/security holds. Registered scope membership governs shared effects. Uncertainty forbids invented failure, not independently trusted evidence or an authorized operator hold.

**Acceptance:** A crash after pending commit or after restriction application reconciles once; concurrent dismiss/escalate cannot apply an effect under a dismissed authorization. A genuinely new security hold is not swallowed; old probes/releases cannot clear a later hold; operational recovery does not release administrative quarantine; independent shared/profile restrictions compose. The Review4 B6 acceptance conditions remain the target.

### B7 — NOT RESOLVED: mode names are still the entire selection contract

**Evidence:** Lines 110-111 and 172-176 are substantively unchanged from the last review: three modes, generic capability matching/user preference, and no selection rule. “Pins and hard filters preserved” remains a closure-matrix promise at 229. The ordered declared preference mapping, explicit opt-in input, missing-hint behavior, pin handling and no-candidate result requested in Review4 B7 are absent.

**Required amendment:** Supply that small rule table. Off/advisory do not change selection; opt-in applies health, permission, verified binding/capability and approved-scope filters before declared preference ordering. A pin selects exactly the eligible target or rejects, never silently substitutes; no eligible target is an explicit blocker. Quality remains ABSENT unless evidenced, distinct from a declared preference. Remove stale prompt-length/advisory-only comments as editorial follow-through.

**Acceptance:** Advisory leaves selection unchanged; automatic opt-in selects according to the declared order among eligible bindings; blocked pins and empty candidate sets fail explicitly; missing quality never becomes a score. This is completion of the accepted O2 direction, not a new routing proposal.

### B8 — NOT RESOLVED: normalization/forecast fixes are accepted; metric contract is incomplete

**Evidence:** Line 114 now closes the normalize-both/every-entry/before-selection issue, including source/pool/limit/window/reset identity and localized errors. Forecast feature removal is complete. The remaining Review4 requirements were not carried through: explicit preservation of applicability/freshness and unknown applicability; shared diagnostic/admission snapshot; failure counts/coverage and bounded independent refresh; reset-safe trend comparison. Line 608 still says failures / total dispatches, while line 116 defines a narrower definitive-started-attempt population. Line 609 still compares windows without the reset/comparability guard.

**Required amendment:** Preserve applicability/freshness and unknown applicability through selection; use the same normalized snapshot for display/admission. Make failure rate `failed / (succeeded + failed)` over line 116's population, showing the counts, excluded cancelled/unknown/pre-admission categories and partial coverage. Restrict historical deltas to comparable pool/window observations; a reset breaks continuity. Specify bounded source-specific refresh without model calls or suppression of independent valid sources, and include the previously requested finite normalization partitions as acceptance cases.

**Acceptance:** ByLimitId-only, disjoint/duplicate/conflicting representations, unknown-family and mixed malformed/valid input retain their information. Mixed attempt outcomes produce the declared counts; no-completion is not 0%; reset is not ordinary consumption trend. Forecast removal needs no further work.

### B9 — NOT RESOLVED: soft-band intent is fixed but its action is not connected

**Evidence:** SS-02 now returns CHECKPOINT_PENDING at 451, matching the chosen soft-band direction, but the orchestrator result table at 438-444 contains no action for that result. SS-08 now holds on failed reuse checkpoint; the specification still lacks a general required-checkpoint failure rule, exact boundary partition, visible unknown-pressure handling and the previously requested compatibility/continuity/freshness/generation guards. Claim-time target/session checking at 89 is useful but does not define preparation failure or what fresh is allowed to bypass.

**Required amendment:** Either map CHECKPOINT_PENDING to an existing defined result or specify its checkpoint/pending/proceed/hold behavior. Use `< soft`, `soft <= p < hard`, `p >= hard`, plus unknown. All required checkpoint failures hold. Unknown/stale reuse may follow the selected permissive default but remains visibly unknown. Fresh and reuse cannot override verified capacity, fingerprint compatibility, ownership lease or required continuity; retain prepared-generation retry and stale-generation fencing.

**Acceptance:** Every emitted saga result has one orchestrator action; exact thresholds and unknown evidence produce the chosen behavior; failed required checkpoint holds; fingerprint mismatch never resumes; concurrent fresh/rotation and late old-generation results cannot corrupt the binding or replay uncertain work. These are the existing Review4 B9 requirements.

### B10 — NOT RESOLVED: proof rejection and binding are still missing

**Evidence:** Lines 122-124 remain the same trusted-interface field list and deployment deferral. There is still no defined result for absent, invalid, expired, revoked, wrong-subject or currently unverifiable required proof; no exact candidate/standing-scope containment rule; and no persisted proof reference or explicit valid-grant reuse rule. Review4 B10 already identified all of these.

**Required amendment:** Bind proof to exact subject/candidate or validated contained standing scope, effect/exception, trusted issuer/designated principal, expiry, replay identity and revocation version. Persist its reference with the decision. Invalid or unverifiable required proof waits/rejects without actor-string/coordinator fallback; valid scoped proof is reused. Keep concrete isolation deferred, while explicitly requiring peer-origin issuance/replay/store-modification qualification before claiming malicious-same-user resistance; until then state the cooperative-process assumption.

**Acceptance:** Wrong subject, untrusted issuer, expired/revoked proof, replay and spoofed actor reject; unavailable required validation holds; valid standing authorization is reused. Real deployment qualification remains a later task. No cryptographic subsystem inside DispatchPolicy is requested.

## 4. Remaining section 6 items

The IDs below are the seven specification corrections tracked by Review4, inherited from Review3 section 6.

| Item | Verdict | Residual scope |
|---|---|---|
| **6.1 — resolver/sub-policy contracts** | **NOT RESOLVED** | No field/default/owner or purpose-minimum table; REVIEW timeout, recovery authority, retained TTL and routing preference inputs remain unbound. `PRIVATE_VOLATILE_ROOT` needs a declared resolver representation: lines 183-185 and R-12 at 274 currently leave relative versus resolved-root interpretation unclear. Complete the existing schema; do not add another config system. |
| **6.2 — defaults** | **NOT RESOLVED** | The proposal quorum/unanimity mismatch is **resolved**. The purpose-aware table requested for consultation/contained work remains absent (B1), and line 644 still claims all defaults equal current behavior despite the proposed changes. Resolve B1; edit the broad compatibility claim. |
| **6.3 — governing-key validation** | **NOT RESOLVED** | R-06 at 268 still explicitly ignores unknown keys. Reject unknown governing keys, define inert extensions if any, and supply the previously requested enum/duration/bound/combination checks. Nested immutability and the compound-trigger config enum are already accepted. |
| **6.4 — many-to-one mapping** | **RESOLVED** | Line 11 remains accurate. No further change or numeric compatibility interface is required. |
| **6.5 — ingress/ownership** | **NOT RESOLVED** | Line 27 still says an invalid/empty source gets “the chosen outcome” without choosing it. Define supported sources, empty/invalid/size/encoding handling and borrowed ownership consistently for ask/broadcast. Exactly-one and query fidelity are already accepted. |
| **6.6 — counts and consistent oracles** | **NOT RESOLVED** | The detailed 133-case total is **resolved**. Contrary behavioral oracles remain under B2/B3/B5/B6, so this compound item cannot close. The older 132-case heading at 233 is merely editorial and is not an independent blocker. |
| **6.7 — acknowledge real service changes** | **RESOLVED** for this correction's independent purpose | The text now explicitly requires the saga signature change, existing-runner claim checks and durable pending-effect reconciliation (89, 105, 119). It no longer presents those fixes as merely calling an unchanged saga or swapping two calls. The missing behavioral contracts/ownership detail remain counted under their B-items and 6.1, not as another independent reason to reject this wording correction. |

Acceptance for the remaining schema/ingress work is unchanged: a reader can resolve each supported default/override and REVIEW/retained/opt-in case without inventing a field; a governing-key typo errors; source validation occurs before dispatch; cleanup cannot delete a borrowed source.

## 5. Closure matrix and nonblocking editorial issues

The seven-row closure scope at 222-231 remains appropriate, and `Specified` is not a claim that code is implemented or verified. However, the previous request to distinguish incomplete design from pending implementation evidence and provide native entrypoint/owner/case/evidence references is **NOT RESOLVED**: the matrix remains unchanged. Mark affected design rows incomplete/pending the B-items, and implementation evidence unverified/TEST NEEDED. Fill the trace references when the corresponding specifications are complete. Do not revive the full legacy ledger to solve this.

The following **do not independently block ratification or warrant another round**:

- The stale 132-case heading when the detailed 133-case summary is correct.
- Duplicate section numbering, the `B-5` closure heading label, and the unchanged R4 title/status on the overwritten file.
- The damaged diagram glyphs; readable equivalent ASCII is sufficient.
- Historical/advisory-only provenance wording and the unused prompt-length comment, given the explicit selected routing modes and removal of inference.
- The leftover durable-root comment, once the volatile-root resolver contract is unambiguous.
- Isolated “Approved” wording for the ordinary-dissent vote condition, provided the unchanged mandatory authorization obligations remain explicit.

These are distinguished from instructions that produce a different outcome, such as `explicit disagree -> blocked`, `remove files older than max_age`, or `already quarantined -> no-op`. Those branches are still written as operating rules and still conflict with the corresponding corrected rows.

## 6. Convergence gate

**NOT RATIFIED.** The accepted architecture does not need another redesign. The selected normalization, retention, recovery-table, pending-effect and routing directions remain accepted. The revision needs the residual requirements listed above, not a new feature set or a longer list of concepts.

To close the review, consolidate the cited remaining branches in place and supply the missing contracts that Review4 already requested. Do not report the complete B-items resolved solely because their spot-checked sentences changed. Once each residual has one behavior and its existing acceptance conditions are defined, cosmetic leftovers will not prevent ratification. No implementation or runtime evidence is required merely to finish this design consolidation.
