# Round 9 ratification review: two bounded residuals

Author: cx.astra. Date: 2026-09-24. **Final verdict: NOT RATIFIED.**

The revision now preserves both execution-recovery tables and the actual quarantine pending-effect transaction. REVIEW timing, resolution outcomes, ingress validation, retained-input TTL, staging-root representation and the reliability window are also specified. The previously resolved Final Call, session, host-authority and governing-key rules remain intact.

Two residuals prevent full ratification: the consultation exemption does not explicitly preserve the checks required by Round 7, and automatic routing still lacks a concrete ordered preference input and affirmative activation rule. The second also leaves section 6.1 partially unfinished. These are a narrow authorization qualifier and a small input/selection contract, not missing architecture or a request to reconstruct B1-B10 again.

## 1. Evidence and review boundary

I read the complete **775-line** [current revision](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), called **Revision** below, against the exact remaining amendments in the [Round 7 review](GOVERNANCE-DISPATCH-R7-cx-astra-ratification-2026-09-24.md), called **Review7**. Revision line references identify SHA-256 **`8b6ae50a16944e0ed5b7d6182ba57c8f1ff3ec0b811862c097548c7b097000f9`**. The older filename/title do not identify the reviewed contents; this hash does.

This review concerns the design specification. Proposed runtime behavior remains **declared, unverified**, with implementation/deployment evidence **TEST NEEDED**. No implementation, configuration activation, commit or hub.py/peerhub dispatch was performed. No implementation or live test is required to close the two design residuals.

## 2. Disposition of all remaining Round 7 items

| Item | Verdict | Evidence and remaining scope |
|---|---|---|
| **B1 — admission and plan authority** | **NOT RESOLVED** | Durable policy/authorization/atomic-boundary requirements, prior authorization and the four purpose rows are now present at 25-48. Only the scope of the recursion exemption at 41 remains, as specified below. |
| **B3 — REVIEW, outcomes and reopening** | **RESOLVED** | `ConsensusPolicy` is now present and explicitly supplies REVIEW timing (62-64, 273). SM-23/25 distinguishes approved/denied/exceptional outcomes and permits execution only with approval plus valid proof; SM-24/26 binds reopening to a new stored deadline (507-510). The effective timing policy and unchanged candidate/vote obligations at 95-100 supply the shared rules. |
| **B4 — execution recovery** | **RESOLVED** | Crash boundaries/partial broadcast at 105-111 and the full five certainty-to-retry rows at 115-121 are separate tables. Attempt identity, lease-expiry and atomic claim/revocation rules remain at 123-126. No accepted recovery rule was displaced this time. |
| **B5 — payload lifecycle** | **RESOLVED** | Canonical retained input and scratch are separated in the tree and TP-E-13 (608-613, 642). Cleanup debt is an explicit pending record/action (621), subject to the consumer-safe rules and no-effect-retry rule (129, 618-624). TTL is a positive field with a 30-day default (247, 284); staging is consistently a relative segment beneath the private temporary base (237, 281, 355-356, 622). |
| **B6 — review effects and restrictions** | **RESOLVED** | Pending commit, shared DISMISS/ESCALATE revision guard, idempotent application, receipt reconciliation and restart behavior are explicit at 554-569; QR-E-11/12 covers both crash boundaries (586-587). Existing identity/authority/epoch/release constraints remain at 140-146. |
| **B7 — routing inputs** | **NOT RESOLVED** | Missing-hint behavior was added at 153, and the accepted filter/pin/mode rules remain at 157-161. The actual hint-to-ordered-bindings input, unknown-hint outcome and affirmative routing activation remain unspecified. See the bounded amendment below. |
| **B8 — telemetry window** | **RESOLVED** | Line 166 restores definitive started attempts, deduplicated by attempt ID, completed within `(as_of - 24h, as_of]`, with the specified formula, exclusions and coverage. Normalization, shared snapshot and reset-safe history remain at 164-167; the display denominator agrees at 703. |
| **6.1 — schema/resolver bindings** | **NOT RESOLVED** | REVIEW availability, TTL, `default_mode` -> `mode`, staging representation and purpose rows are resolved (62-64, 43-48, 237/281, 275, 284). Only B7's routing input/activation binding remains. This is the same issue, not a third independent blocker. |
| **6.2 — purpose defaults** | **RESOLVED** | All four purpose/default/minimum rows are present at 43-48, and configuration explicitly refers to them at 192. Protected standing floors at 28 and valid containment at 27 still constrain preference overrides. Proposal quorum remains consistent at 197/739. B1's exemption qualifier does not require another default table. |
| **6.5 — ingress** | **RESOLVED** | Lines 29-38 define common ask/broadcast source handling, exactly one supported inline/file source, exit 2 for missing/multiple/empty/invalid/unsupported input, query fidelity and borrowed ownership. Stdin is explicitly unsupported. Size is a staging decision rather than an ingress rejection, with UTF-8 file validation retained at 640. |

The terminal's four spot checks are confirmed by the full-file read. B1's purpose table exists; it is not the remaining B1 objection. B4 and B6 are closed, including the content previously lost during revision.

## 3. The two required amendments

### B1 — NOT RESOLVED: limit the exemption to additional consultation

**Exact location:** Revision 41 and the corresponding purpose row at 48. **Existing requirement:** Review7 48 explicitly preserves actor, disclosure, scope and effect checks, and excludes implementing the proposal from the exemption.

Line 41 currently says bounded consultation/vote/ACK operations are “strictly exempt from recursive policy evaluations.” That supplies the missing recursion exemption, but it is broader than the requested exemption from additional consultation and omits the required retained checks. The common evaluator at 26 and live authorization checks at 126 are useful; they do not explicitly settle which checks the special exemption is permitted to suppress, particularly disclosure checks. This should not be left to an implementer to interpret as either skipping consultation or skipping the evaluator's policy checks generally.

**Required amendment:** Replace line 41 with this bounded rule, or equivalent explicit text:

> Bounded consultation, vote and ACK collection does not trigger additional consultation. Actor, disclosure, approved-scope and effect/admission checks still apply. This exemption authorizes collecting the review or decision evidence only; it cannot authorize implementing the proposal under review.

Make the purpose row at 48 refer to that rule. No extra approval round or prompt is implied by retaining existing admission checks.

**Acceptance:** A permitted reviewer request and vote terminate without recursive consultation. A request outside the actor's disclosure/scope authority still rejects or holds, even when tagged as consultation. Work implementing the proposed change must use its own valid plan/decision authorization and cannot inherit the collection exemption.

**What is closed:** I accept the new durable gate statement at 39 together with the frozen policy at 57, common runner admission at 25-28 and live checks at 126. I am not asking for database column names, another store, or a longer persistence design. Prior authorization at 40, read with the no-self-downgrade rule at 28, also closes the standing-policy activation requirement. B1 is now missing this short exemption qualifier, not its former whole persistence/default contract.

### B7 / 6.1 — NOT RESOLVED: make the routing input and activation concrete

**Exact locations:** Revision 153, 158, 226-228 and 279. **Existing requirement:** Review7 133-135 requires a named hint/work-shape input, an ordered existing-profile preference list, an affirmative caller/config opt-in and a missing/unknown-hint result.

Line 153 now chooses a useful missing-hint result: keep the explicit or fallback profile without routing. However, “e.g., requested task complexity” and a candidate field “e.g., `model_tier`” are still examples, not a defined input or ordered mapping. A candidate's tier alone does not specify which hint selects which bindings or their order. Line 158 still applies an order whose source is never supplied; the TOML/schema still has only `effort_routing` for routing.

The built-in mode remains `opt-in` at 279. The explanation that these are routing behaviors rather than privacy opt-ins does not answer the routing activation question: with no explicit caller/config choice, can a supplied hint trigger automatic profile replacement? Review7 required an affirmative routing choice, not a privacy prompt. A standing, explicitly configured routing choice can satisfy it; it need not be repeated per dispatch.

**Required amendment:** Add a compact input contract within the existing routing policy. For example, the following would close the requirement; equivalent names or a precise existing interface are sufficient:

| Input | Concrete contract |
|---|---|
| Declared request hint | Name one request field or existing CLI input carrying the caller's hint/work-shape key. It is a declaration, not measured quality. |
| Ordered preference mapping | Name one config field or exact existing source mapping each supported hint to an ordered list of registered profile-binding IDs. In automatic mode, apply the accepted hard filters and choose the first eligible binding in that list. |
| Routing activation | State that an explicit caller/config setting of `effort_routing = "opt-in"` enables automatic selection, with advisory behavior absent such a choice. The simplest schema repair is an advisory built-in default. If retaining the current built-in label, specify the additional affirmative activation input that prevents it from silently enabling selection. |
| Missing and unknown hints | Keep the already-selected missing-hint result. Explicitly choose the unknown-key result too: unchanged selection or a validation error are both bounded choices. Do not infer an order from a model name/tier. |

Keep the already-accepted exact pin-or-reject behavior, no-eligible-candidate blocker and ABSENT quality rule. Bind these inputs in the schema so R-06 does not reject the configuration that supplies them. No learned scorer, new routing architecture or policy namespace is required.

**Acceptance:** Given a declared key, ordered binding list, eligibility facts and explicit routing choice, selection has exactly one result. An ineligible first binding causes selection of the next eligible declared binding; an ineligible explicit pin rejects without substitution. Missing/unknown keys follow their stated outcomes. An absent opt-in cannot silently activate automatic replacement, while an existing explicit configuration choice requires no new prompt.

This remains a small input contract, but it determines the selected execution target. It is therefore a behavioral omission rather than an editorial example that can safely remain unspecified.

## 4. Regression check of previously resolved items

| Previously resolved item | Round 9 verdict | Preservation evidence |
|---|---|---|
| **B2 — Final Call** | **RESOLVED; no regression** | Mandatory trigger union and candidate/authority/disposition/ACK requirements remain at 99-100. The phase diagram and supported-mode cases retain mandatory floors (461-462, 495-501). Pre/post-authorization retraction and CAS ordering remain at 504-506. The new explicit resolution outcomes strengthen the shared integration. |
| **B9 — sessions** | **RESOLVED; no regression** | Signature change and prepared-generation reuse remain at 170-171. CHECKPOINT_PENDING and required-checkpoint failure are connected at 525-526. Capacity/fingerprint/lease/continuity and generation guards remain at 531; threshold, unknown/stale and concurrency cases remain at 538-546. |
| **B10 — host authority** | **RESOLVED; no regression** | Subject/standing-scope proof binding, persistence, invalid/unverifiable-proof rejection, scoped reuse and deployment qualification remain at 174-177. The “Human/Arbiter” labels at 509-510 do not supply authority by themselves: the explicit valid-proof rule still applies. |
| **6.3 — governing-key validation** | **RESOLVED; no regression** | R-06 at 350 rejects unknown core keys and requires enum/duration/bound/combination checks. The surrounding type, threshold and path checks remain. The new TTL field has a positive bound at 284. |

I also checked the retained B3 vote/electorate rules (94-96, 487-494), B6 independent restriction release and stale-epoch behavior (144-146, 585, 676-681), and B8 normalize-both/every-entry/shared-snapshot requirements (164-167). None was removed to make room for the new material. Forecast output remains absent.

## 5. Closure matrix and nonblocking cleanup

The matrix at 303-311 correctly keeps implementation status **TEST NEEDED** and now supplies section references. The two affected design rows should remain pending until the amendments above are made:

- Consultation and plan authority: specified except for B1's exemption qualifier.
- Profile selection: modes specified; B7/6.1 input/activation binding pending.

The other rows may be marked specified at design altitude on the evidence above. This does not claim implementation verification or authorize migration/deletion of legacy actions. No full D13 ledger is requested.

The following are **nonblocking editorial matters** and do not independently justify another round:

- The definitive-outcome staging branch at 609-610 still abbreviates its action as `remove_staged_prompt()`. Add “when the consumer-safe guard permits; otherwise record cleanup debt” for clarity. The unconditional copy-lifetime rule at 129/619, explicit debt at 621 and no-effect-retry rule at 624 now supply its semantics. Unlike the former “preserve staged file durably” instruction, this action label does not explicitly prescribe a contrary lifetime. I do not keep B5 open solely for that shorthand.
- Display labels can say “24h attempt failure rate, including retries” and “no definitive completions”; the population/formula itself is now fixed at 166. The generic silent reuse label can likewise defer visibly to the specific unknown/stale session cases. These remain presentation clarifications.
- The title/status/changelog still names earlier rounds; section numbering and some closure references are stale. In particular, the closure matrix's `Sec 6.5` reference should point to the actual ingress table at 29-38.
- Example TOML overrides differing from built-in schema defaults are not automatically contradictory; the block explicitly permits selective overrides at 187. The unresolved routing activation is separately identified above.
- The current file contains **135 unique enumerated case IDs**, not 133: 20 resolution, 26 consultation, 31 consensus, 10 session, 12 quarantine review, 3 execution, 13 transport, 11 health and 9 telemetry. The new QR-E-11/12 accounts for the increase from Round 7. Correct the summary; do not alter valid cases to preserve an old count.
- Short Final Call ACK/NACK labels, the isolated majority “Approved” label, and historical advisory-only provenance retain the interpretations already accepted in Round 7.

## 6. Final verdict

**NOT RATIFIED**, with **two remaining substantive amendments only**: narrow B1's consultation exemption explicitly, and supply B7's concrete routing inputs/activation rule with the matching 6.1 schema binding. The related closure status changes are bookkeeping, not additional blockers.

The recovery regressions are repaired. The remaining changes are much smaller than in Round 7 and require no architectural redesign, implementation work or new dispatch. Once these two existing requirements are explicit, the editorial items above will not prevent ratification.
