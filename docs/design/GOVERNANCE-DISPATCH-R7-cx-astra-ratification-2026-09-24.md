# Round 7 ratification review: remaining specification gaps

Author: cx.astra. Date: 2026-09-24. **Final verdict: NOT RATIFIED.**

The revision closes substantial parts of the Round 5 findings. The compound Final Call transitions are now consistent; vote correction, electorate constraints, routing mode behavior, telemetry normalization, session guards and host-proof validation have real specifications. Those improvements are accepted. This review does not reopen the lighter architecture, the chosen policies, or the exclusion of the full D13 ledger and namespace model.

There are still substantive omissions, including the durable admission/consultation contract and loss of the previously accepted certainty-to-retry table. Some other residuals are now small: routing input bindings, a reliability time-window definition, and particular schema/lifecycle inconsistencies. The verdict is based on these bounded residuals, not on labels, counts, diagram glyphs or implementation evidence.

## 1. Reviewed artifact and scope

I read all **733 lines** of the current [revision](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), here called **Revision**, against the remaining requirements in the [Round 5 review](GOVERNANCE-DISPATCH-R5-cx-astra-ratification-2026-09-24.md), here called **Review5**. I also checked the inherited requirements in [Review4](GOVERNANCE-DISPATCH-R4-cx-astra-ratification-2026-09-24.md) and the previously accepted recovery table in [Review3](GOVERNANCE-DISPATCH-R3-cx-astra-ratification-2026-09-24.md).

**All Revision line references in this review identify SHA-256 `dc6159dd5b649a9f846b674e5399b035208877c3265720ac6e7c0f2fc6b2c98b`.** The filename still says R4. Earlier hashes and line numbers must not be used to interpret these findings.

This is a document review. Proposed service/CLI behavior remains **declared, unverified**; runtime and deployment qualification are **TEST NEEDED**. No implementation, configuration activation, commit or hub.py/peerhub dispatch was performed. The review does not require implementation or live tests to close its design findings.

## 2. Per-item verdicts

Each unresolved entry names only its remaining scope. Shared omissions are cross-referenced rather than counted as new architectural objections.

| Item | Verdict | Remaining scope or reason for closure |
|---|---|---|
| B1 — admission and plan authority | **NOT RESOLVED** | Bounded consultation exemption, durable effective-policy/authorization binding, atomic decision/receipt/pending-effect boundary and purpose defaults remain missing. |
| B2 — Final Call integrity | **RESOLVED** | Mandatory union, supported-mode transitions, candidate/authority/dissent binding, qualifying concerns and atomic last-ACK authorization are specified. Shared REVIEW/reopen wiring remains under B3/6.1. |
| B3 — votes and electorate | **NOT RESOLVED** | Vote/electorate rules are resolved. REVIEW's optional-policy access and explicit exceptional/denied resolution and reopening bindings still need completion. |
| B4 — execution recovery | **NOT RESOLVED** | The requested crash-boundary table was added, but replaced the accepted five certainty-to-retry rules. Restore those rules alongside it. |
| B5 — payload lifecycle | **NOT RESOLVED** | Ownership and age-only sweep fixes are resolved. The staging tree/TP-E-13 still conflate retained canonical input with scratch; cleanup debt and retention/root bindings need completion. |
| B6 — review effects and restrictions | **NOT RESOLVED** | Scope/epoch/release rules are resolved. The actual durable pending -> idempotent apply -> receipt reconciliation transitions and dismissal guard are no longer specified. |
| B7 — routing | **NOT RESOLVED** | Mode behavior is resolved. The declared preference input/mapping, explicit opt-in input and missing-hint outcome remain unbound. |
| B8 — telemetry | **NOT RESOLVED** | Normalization, formula, counts, comparability and refresh rules are resolved. Restore the precise completion-time window for the 24h attempt metric. |
| B9 — sessions | **RESOLVED** | Policy bands, CHECKPOINT_PENDING action, required-checkpoint hold, binding/continuity guards and generation fencing are specified. |
| B10 — host authority | **RESOLVED** | Proof binding, validation, persistence, reuse and deployment qualification boundary are specified. |
| 6.1 — schema/resolver bindings | **NOT RESOLVED** | Schema exists, but several selected behaviors still lack fields or fixed bindings; REVIEW availability, retention TTL, routing inputs and config/resolved names need reconciliation. |
| 6.2 — purpose defaults | **NOT RESOLVED** | Proposal default and broad compatibility wording are corrected; the actual purpose/default/minimum table remains absent. Same missing work as B1. |
| 6.3 — governing-key validation | **RESOLVED** | Unknown governing keys reject, and enum/duration/bound/combination validation is required. Missing field contracts are tracked under 6.1. |
| 6.5 — ingress | **NOT RESOLVED** | File ownership/UTF-8 handling are improved, but supported sources and concrete invalid/empty-input outcomes remain unspecified. |

## 3. Required amendments and acceptance

### B1 — NOT RESOLVED: the durable gate contract is still missing

**Evidence:** Revision 24-29 defines the common evaluator, protected floors and plan containment, which remain accepted. It does not exempt bounded consultation/vote/ACK collection from recursive approval. Unlisted actions still inherit quorum at 162-169. The new schema at 233-254 is a field table, not the requested purpose/default/minimum table. Line 698 refers to purpose-aware floors without supplying that table anywhere else.

Persistence of a host proof reference at 145 is useful, but does not supply persistence of the effective dispatch policy, runner authorization binding, or the atomic decision/receipt/pending-effect boundary. Lines 68-70, 690 and 703-713 still give provenance, snapshot assertions and logging rather than that contract. Prior-policy authorization of standing-policy activation is also absent. These are Review5 B1's requirements at 55-57, not new storage scope.

**Required amendment:** Add one compact contract to section 2.1:

- Bounded consultation/vote/ACK collection is exempt from additional consultation, while actor, disclosure, scope and effect checks still apply. That exemption cannot authorize implementation of the proposal being reviewed.
- The request/round durably binds immutable effective policy values or a resolvable immutable policy reference, and the applicable authorization/decision receipt. The existing runner requires those references and live admission checks; restart does not reconstruct authority from a changed TOML file or a DEBUG log.
- Name the existing-store atomic boundary for decision state, receipt and pending effect. This is not a requirement for a second store or a general legacy-action ledger.
- Standing-policy activation is authorized under the prior policy.
- Supply the four purpose rows already requested: new direction with protected standing floor; valid contained child reusing approval; ordinary ask/broadcast with an explicitly chosen default; bounded consultation/vote/ACK collection with no recursive consultation. State how purpose floors interact with per-action preferences.

**Acceptance:** Reviewer request -> vote terminates without approval recursion; an architecture ask cannot lower the standing floor; a contained child reuses valid approval and expanded work does not. Recovery after process loss and a TOML edit retrieves the original policy and authorization. No extra architecture is needed, but this remains a substantive missing contract.

### B2 — RESOLVED: Final Call's distinct integrity requirements are present

**Evidence:** Revision 80 states the mandatory union. The phase diagram at 427-428 now uses mandatory floors, and SM-13..17 at 461-465 uses only the supported configuration values at 175-176/239. The old mutually exclusive mode bypass is gone.

Line 81 binds actor/candidate ACKs to policy, votes, authority/disposition references; preserves dissent-origin obligations after arbiter disposition; distinguishes material changes requiring fresh votes; clears obsolete ACKs; excludes cosmetic vetoes; and requires atomic final authorization checks. SM-19/20 at 466-467 carries the live-authority/candidate/qualifying-concern rules. The pre/post-authorization and CAS retraction cases remain at 470-472. B10 now supplies the required host-proof validation.

I read the shorter "all ACK"/"NACK" diagram labels at 430-432 through those explicit definitions. They do not independently restore an unqualified cosmetic veto. Reopening's new deadline/invalidated ACK requirement is present at 81; B3/6.1 still needs to bind the common timeout and exceptional outcome machinery. That shared wiring does not require another Final Call policy decision.

**No further amendment to the Final Call predicate, candidate or ACK contract is required.**

### B3 — NOT RESOLVED: only timeout/outcome integration remains

**Resolved:** Revision 75-77 and 460 now distinguish duplicate delivery, unauthorized change and explicit correction, including recomputation. Frozen logical identities, independent non-proposer, required membership, denominator and high-risk absence rules are present. The ordinary-dissent branch at 353 agrees with the majority rows; typed BLOCK and mathematically impossible quorum are separately represented at 423-424 and 454-456. I do not reopen these choices.

**Remaining evidence:** Line 78 says to give REVIEW a timeout and reopened/exceptional resolutions explicit proof/outcomes/deadlines. The schema at 240 supplies `consensus.timeout_seconds` to REVIEW, but 43-44 still makes `consensus` optional and meaningful only at QUORUM or higher. A legal REVIEW policy can therefore lack the object from which it is told to read its timeout. Rows 473-476 still express only "Human override", "Terminal" and "Back to voting", without the explicit approved/denied/exceptional resolution contract requested in Review5 71-73 and Review4 81. B10 validates authority; it does not define whether a particular resolution approves or denies dispatch.

**Required amendment:** Make the timeout available for REVIEW independently of binding consensus's minimum-two rule. Either retain a present timing sub-policy at REVIEW depth or give REVIEW a separate bound timeout field/fixed invariant; reconcile the dataclass and schema accordingly. Define approved, denied and exceptional authorization outcomes, with only an approving outcome plus valid required proof permitting execution. Bind reopening to a new stored deadline using the effective timing policy; unchanged valid obligations remain required, material changes require supersession/fresh votes, and stale ACKs are cleared as already specified in B2.

**Acceptance:** REVIEW can resolve a timeout without reading `None`; a denial or mere timeout/coordinator action cannot dispatch; reopened voting has a newly bound deadline and cannot reuse obsolete approval evidence. This is now a small integration contract, not a missing vote/electorate design.

### B4 — NOT RESOLVED: restore the accepted certainty table alongside the new crash table

**Evidence:** Revision 88-95 now covers the four requested crash boundaries, partial broadcast, unique attempt identity and lease expiry. Those additions satisfy the new material requested by Review5 81-83. The shared claim/revocation guard remains at 97.

However, the five certainty-to-retry rows that Review5 19 and 79 expressly accepted have disappeared. Their former position now contains the boundary table under the same "Observed certainty" heading. EX-01..03 at 555-557 only describes cancellation. Neither that table nor "No unsafe replay" defines the lost rules for proven NOT_STARTED, uncertain STARTED, COMPLETED success and COMPLETED failure. In particular, the document no longer says that a nonzero exit is insufficient evidence of zero side effects. TP-E-13 still points to B4, so this is a real loss of replay specification, not a renamed heading.

**Required amendment:** Keep the new crash table and restore this already-approved table from Review3 110-116 as a separate table:

| Observed certainty | Recovery rule |
|---|---|
| NOT_STARTED, proven | A new attempt may run only if current authorization, admission, input, deadline and retry bounds permit. |
| MAY_HAVE_STARTED | Reconcile using stable process/provider request identity. No automatic non-idempotent replay. Independently proven safe/idempotent work may retry within bounds. |
| STARTED, no definitive terminal outcome | Same replay restriction; a cancellation request is not proof of death or non-effect. |
| COMPLETED success | Return/settle that result. Retry cleanup/accounting/notification only, never the model/effect. |
| COMPLETED failure | Retry only when the operation-specific effect semantics establish safety. A nonzero exit alone does not prove zero side effects. |

**Acceptance:** The new boundary table identifies the surviving record; this certainty table decides whether another effect attempt is allowed. Retained uncertain non-idempotent input stays blocked; a failure exit cannot independently authorize replay; committed success only retries bookkeeping/cleanup. This is restoration of accepted content, not a new recovery design.

### B5 — NOT RESOLVED: finish separating scratch from retained input

**Resolved:** Revision 577/595 removes age-only deletion. Lines 100-102, 578-579 and TP-E-06/07 now cover consumer-safe lifetime, distinct verified copies, borrowed-file non-deletion and inline retention parity. TP-E-12 at 599 has one complete expected-outcome cell and conditions INPUT_REQUIRED on missing exact authorized bytes independently of execution certainty. The terminal's fix to that row is confirmed.

**Remaining evidence:** The tree at 567-572 is still a lifecycle for the staged `prompt_reference`, ending in retained -> "preserve durably". TP-E-13 at 600 still says "Staged file preserved". That contradicts 101/578, where only canonical retained input is durable and scratch in either mode is eligible for consumer-safe cleanup. The tree's definite-outcome branch also invokes cleanup without the consumer-use guard. "Cleanup-debt is handled gracefully" at 579 does not identify the pending cleanup action/record, although 582 correctly prohibits rerunning the effect.

The retained-input TTL still has no actual field, fixed value or named policy reference: 250 says only "Binds retained TTL to own policy". The root resolver now selects an OS-temporary base at 580, but TOML supplies `$PRIVATE_VOLATILE_ROOT/prompt-staging` at 207 while the schema and R-12 accept a relative segment at 248/322. A literal segment and an expanded root expression are different inputs unless their interpretation is explicitly defined.

**Required amendment:**

1. Replace the scratch tree's outcome-based cleanup branches with the same owned-copy plus trusted-consumption/confirmed-death rule for both retention modes. Label durable preservation as applying only to separately retained canonical input. Make TP-E-13 say the same; keep its dependency on B4 for replay permission.
2. Define cleanup debt as pending cleanup owned by transport/recovery, retried only under the safe-consumer guard and independently of the committed effect result. A compact record/action description is sufficient.
3. Bind retained TTL/deletion policy to a real field, fixed invariant or named existing policy, including what happens if retained mode lacks that binding. Complete the shared 6.1 schema correction.
4. Use one root representation. The smallest repair is `staging_dir = "prompt-staging"`, resolved beneath the private temporary base by the existing resolver contract; alternatively explicitly define and validate the root-expression syntax. No new path system is requested.

**Acceptance:** Retained canonical bytes survive only under their retention policy; safe scratch cleanup works in either mode; unknown/live consumers retain their copy; cleanup debt never reruns the effect. The example staging value resolves to the intended directory using the declared schema. These are narrow lifecycle/binding repairs, but durable retention of the wrong object changes behavior.

### B6 — NOT RESOLVED: the selected transaction pattern needs its actual transitions restored

**Resolved:** Revision 111-117 provides scope vocabulary, epochs, exact restriction target/authority/current revision, operator/review validation, independent release and registered membership. The old blanket already-restricted no-op is gone at 527/539. QR-E-10 at 546 no longer lets operational recovery release administrative quarantine; HC-06/09/11 at 634/637/639 has the requested uncertainty, stale-probe and sibling behavior.

**Remaining evidence:** The current 113-114 names a pending effect and says "Pending/applied reconciliation and concurrent dismissal guards are defined". It does not define those transitions. The actual pending-commit -> idempotent-apply -> reconcile-from-receipt sequence accepted in Review5 22/99 is no longer present in the current file. The decision tree at 521-530 still goes from REQUESTED directly to the restriction call; QR-E-07 at 543 has no pending-recovery path. A statement that guards "are defined" cannot replace the definition or a concrete reference to it.

**Required amendment:** Restore the chosen option (b) without reopening alternatives. Specify these transitions in the existing review/effect store:

- Validate the current REQUESTED revision, target and operator grant; atomically commit the stable pending-effect identity and the review's pending disposition before applying the restriction.
- DISMISS and ESCALATE contend on that same review revision. If dismissal wins, no pending effect may be committed. If pending commitment wins, dismissal cannot subsequently succeed as though the review were still REQUESTED; any release uses the separately authorized release path.
- Apply that exact effect idempotently and obtain its applied receipt. Reconcile the pending review from that receipt to its final disposition. Restart after pending commit retries/reconciles that identity; restart after application uses the receipt rather than inventing a new effect.

The spelling of the pending state is an implementation choice. The durable ordering, revision guard and receipt relationship are the missing design content.

**Acceptance:** A crash on either side of restriction application produces one effect and the correct review disposition. Concurrent dismiss/escalate cannot leave a dismissed review authorizing a hold. New independent restrictions are still recorded. This residual is a short state contract, but it is a substantive crash/race guarantee, not editorial wording.

### B7 — NOT RESOLVED: only the selection inputs remain missing

**Resolved:** Revision 127-131 supplies real off/advisory/opt-in/pin behavior, filtering before ordering, exact pin-or-reject, explicit no-candidate blocking and ABSENT quality. This closes the main routing-rule omission. There is no request for a learned scorer or different mode design.

**Remaining evidence:** Lines 128 and 246 refer to "declared preference ordering" and "routing-preference inputs" without an actual input name, ordered mapping, or missing/unknown-hint result. TOML 195-197 supplies only `effort_routing`. The schema defaults that mode to `opt-in`; the document still does not identify the affirmative caller/config input that authorizes automatic selection. These are the specific invocation/mapping omissions recorded in Review5 105 and 141 and Review4 134.

**Required amendment:** Bind the declared hint/work shape, ordered existing-profile preference list and affirmative opt-in to a request field, configuration field or exact existing interface. State the missing/unknown-hint outcome. Choose the first eligible binding in that declared order; preserve the already-defined exact pin and no-candidate rules. Explain how the absent-config `opt-in` default relates to actual caller consent, or retain advisory as the non-opted-in default.

**Acceptance:** Given a hint, declared list, eligibility facts and opt-in/pin inputs, selection has one result without guessing a preference source. Absent opt-in cannot silently become automatic selection. This is a small missing input contract, not another routing architecture.

### B8 — NOT RESOLVED: one metric-window binding remains

**Resolved:** Revision 134-137 now requires normalization of both representations/every entry before selection, identities, dedup/conflict retention, localized malformed input, applicability/freshness and a shared display/admission snapshot. It defines the correct attempt population and `failed / (succeeded + failed)`, counts and exclusions, partial coverage, comparable-window history and bounded source-specific refresh. The data-source denominator at 661 agrees. Forecast output is absent. TH-09 at 676 adds normalization/no-completion/reset acceptance coverage.

**Remaining evidence:** The metric is still explicitly "24h" at 651/660-661, but 136 no longer binds inclusion to completion timestamps within a particular interval. Review5 115 required its already-accepted precise population to survive; Review4 38 accepted that time-window choice, and Review3 199 specified completed attempts within `(as_of - 24h, as_of]`. The current text could count attempts started in the interval or completed in it, producing different results for long-running attempts.

**Required amendment:** Restore the single rule: use deduplicated definitive started attempts **completed within `(as_of - 24h, as_of]`**, and label the displayed metric as an attempt rate, including retries. Preserve the existing separate cancelled/unknown/pre-admission and coverage counts. TH-05's "no dispatches" label may be clarified to "no definitive completions" for a zero denominator; TH-09 already forbids reporting that as 0%.

**Acceptance:** An attempt begun before the window but completed inside it is included; one begun inside but not definitively completed is excluded from the denominator; both views use the same as-of boundary. This is a small but behaviorally meaningful metric definition. The normalization and refresh design no longer needs another round of reconstruction.

### B9 — RESOLVED: session actions and guards are connected

**Evidence:** Revision 140 requires the saga signature change. Lines 141 and 504-506 specify the policy bands, including exact soft/hard boundaries. CHECKPOINT_PENDING now has its real checkpoint/mark-pending/proceed action at 491. Required checkpoint failure holds at 492/510. The unknown-pressure reuse choice is visible at 507; stale evidence is explicitly untrusted at 508. Capacity, fingerprint, ownership and continuity guards plus generation fencing are explicit at 497, and prepared-generation reuse remains at 141.

The generic silent PROCEED_WITH_REUSE label at 490 should be qualified to respect the specific unknown/stale evidence presentation at 507-508. I treat that as a display-label cleanup, not as permission to override those specific evidence states or a new safety-policy gap. Restoring B4's shared recovery rules remains necessary for the overall design; it does not require a different session saga.

**No further session mechanism is required for this review.**

### B10 — RESOLVED: host authority now has a validation contract

**Evidence:** Revision 144-147 binds the exact candidate or contained standing scope, effect/exception, trusted issuer/designated principal, expiry, replay identity and revocation version; persists the proof reference; requires wait/reject for invalid or unverifiable required proof without actor/coordinator fallback; and reuses valid scoped proof. Required proof that is absent cannot satisfy that validation contract.

The deployment boundary explicitly requires issuance/replay/store-modification qualification before claiming resistance to malicious peers sharing a user account, with the cooperative-process assumption until then. Concrete isolation remains properly deferred. Generic "human/coordinator" labels elsewhere are subject to this explicit validation rule, not a new authority source.

**No cryptographic subsystem, deployment implementation or live security test is required to ratify this contract.** B1/B3 still must bind the durable execution decision and its outcome; that is distinct from defining proof validity.

## 4. Section 6 corrections

### 6.1 — NOT RESOLVED: finish the existing schema

The field/default/owner table at Revision 233-254 is a substantial addition. Recovery authority now has a default and owner at 252. The remaining schema work is finite:

| Location | Missing or inconsistent binding | Required repair |
|---|---|---|
| 43-44 versus 240 | REVIEW reads a timeout from a sub-policy allowed to be absent. | Complete B3's timing-policy availability rule. |
| 101, 250, 578 | Retained TTL/deletion policy is named but has no value or reference. | Add the selected field, fixed invariant or existing-policy binding and its retained-mode validation. |
| 128, 246 | Declared routing preferences/opt-in lack inputs. | Complete B7's small input mapping. |
| 186 versus 242 | TOML names `session.default_mode`; the schema section names `session.mode`. | Mark the config-to-resolved-field mapping explicitly, so unknown-key rejection does not reject the supplied example. |
| 207 versus 248/322 | Root expression in TOML versus relative-segment schema. | Use one representation, as in B5. |
| 24-29, 162-169 | Purpose defaults/minima are not tabulated. | Complete B1/6.2 once. |

The TOML block explicitly says it supplies selective overrides (157). Consequently, its values differing from the schema's built-in defaults are **not automatically contradictions**: for example, an example timeout of 1800 can override a built-in 300. Labeling that distinction more clearly would help, but I do not manufacture a blocker from those differences alone. The unbound automatic opt-in semantics is separately substantive under B7.

**Acceptance:** A reader can resolve absent-config, REVIEW, retained-input and opted-in routing cases using named values and one config-to-record mapping, without inventing fields.

### 6.2 — NOT RESOLVED: purpose/default table remains absent

Revision 166/697 consistently uses quorum for `consensus.propose`; 698 no longer claims every default is identical to current behavior. Those corrections are closed. A field/default table is not a purpose/default/minimum table, however. Complete B1's four purpose rows and make 162-169/698 refer to them. **This is one shared omission, not an additional design demand.**

### 6.3 — RESOLVED: governing typos no longer silently choose policy

R-06 at 316 rejects unknown core keys, confines any inert extension to an explicit namespace, and requires enum/duration/bound/combination checks. The surrounding R rows specify type, enum, threshold, path and positive-inline-size validation; immutability remains at 38. This closes the former ignore-unknown-keys behavior. Finalizing the missing fields and config mappings under 6.1 must naturally make them validatable, but does not reopen the key-rejection decision.

### 6.5 — NOT RESOLVED: choose the source-validation outcomes

Revision 29 still says an invalid/empty source gets "the chosen outcome" without choosing it. The staging tree at 564 names inline and `--query-file`; directory and non-UTF-8 file rejection are concrete at 597-598, and borrowed ownership is now explicit at 579. Those improvements do not say whether stdin is supported, what zero/multiple sources or empty text/file do, or how ask and broadcast share the ingress contract.

**Required amendment:** Give a small source-validation table: supported inline/stdin/file selectors (explicitly exclude unsupported forms), exactly-one enforcement, concrete missing/multiple/empty/invalid-source result before dispatch, encoding and byte-size handling, and the common ask/broadcast behavior. Choosing exit 2 for invalid/empty input would align with the existing file-error rows, but the selected result must be stated. `max_inline_bytes` is a staging threshold; state separately any ingress size rejection, or state that there is none at this layer. Preserve query fidelity and borrowed ownership.

**Acceptance:** Each valid source reaches the same canonical input contract; missing, multiple, invalid and empty sources have one outcome before dispatch; no transport cleanup deletes the borrowed original. This is completion of the original ingress table, not a request for a new CLI feature.

## 5. Closure matrix and editorial disposition

Revision 269-277 now correctly separates design status from implementation status and says **TEST NEEDED** for every implementation row. That improvement is accepted. No runtime readiness is inferred.

The blanket design status **Complete** remains premature. Update only the affected statuses and connect the existing compact rows to the corresponding section/case references:

| Closure row | Accurate design status for this snapshot |
|---|---|
| Consultation and plan authority | Incomplete: B1/B3 and 6.1/6.2/6.5. |
| Final Call | Core predicate/candidate/ACK contract specified; common resolution/timing integration pending B3/6.1. |
| Review quarantine | Restriction semantics specified; effect transaction/reconciliation pending B6. |
| Session rotation | Session contract specified; shared recovery/schema integration pending B4/6.1. |
| Profile selection | Modes specified; input mapping/opt-in binding pending B7. |
| Telemetry | Main contract specified; completion-time window pending B8. |
| IPC and execution recovery | Incomplete: restore B4 table and finish B5/6.1/6.5. |

The entrypoint/owner column is improved; `(Pending Test)` is an honest evidence placeholder, but it is not a case/acceptance reference. Add the relevant existing case IDs or section references when marking a row specified. Do not add the full legacy ledger or implement tests merely to fill this matrix.

These matters **do not independently prevent ratification or justify another round**:

- The file/title round labels, duplicate subsection numbering and historical advisory-only provenance wording.
- The corrected tree glyphs and minor rendering details.
- "Approved" in CD-Q-03, interpreted as satisfying the vote predicate subject to the mandatory remaining obligations.
- The remaining workspace-root comment at 205 once the operative root representation is reconciled.
- Short diagram labels where the detailed normative rule supplies the guard, including Final Call's ACK/NACK shorthand.
- The session silent-output label and telemetry zero-population label noted above, interpreted through their explicit unknown/no-completion rules.
- Case-count bookkeeping: direct enumeration still gives **133 unique IDs**, but the breakdown is now **31 consensus and 9 telemetry**, rather than the summary's 32 and 8. SM-18 was removed and TH-09 added. There is no reason to restore an obsolete case just to preserve numbering.

## 6. Final verdict

**NOT RATIFIED.** The architecture and several major contracts have converged; the remaining issues are not all cosmetic. B1 still lacks its durable admission/consultation specification, B4 lost an accepted safety table, and B6 lacks the operative pending-effect reconciliation transitions. B3/B5/B7/B8 and the schema/ingress corrections have narrower, precisely identified residuals.

The next change can be a bounded completion of the amendments above: preserve accepted content, restore the two lost recovery specifications, bind the remaining inputs/outcomes, and update the affected tables/statuses. No new architecture, broader legacy migration, implementation, live dispatch or additional feature set is requested.
