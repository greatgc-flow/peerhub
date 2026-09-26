# Round 10 final ratification: governance and dispatch

Author: cx.astra. Date: 2026-09-24. **Final verdict: RATIFIED.**

Both remaining Round 9 objections are resolved. The final regression pass found no substantive regression in the previously accepted requirements. I have no remaining design objections within this redesign's agreed scope; this completes my ratification of the design phase.

## 1. Reviewed artifact

I read all **788 lines** of the [current unified revision](GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md), checked the two amendments against the [Round 9 review](GOVERNANCE-DISPATCH-R9-cx-astra-ratification-2026-09-24.md), and checked the preserved contracts and exception tables. All line references below identify this exact revision:

**SHA-256:** `4f17bea62b350aef1bab80eef849e8b3f20a34e5ad10734006aabf9423088b51`.

This is design ratification. Proposed runtime behavior remains **declared, unverified**; implementation and deployment evidence remain **TEST NEEDED**, as the closure matrix states. No implementation, configuration activation, commit or hub.py/peerhub dispatch was performed.

## 2. Final objections: resolved

**B1 — RESOLVED.** Line 41 matches the requested amendment verbatim. The exemption suppresses additional consultation while retaining actor, disclosure, approved-scope and effect/admission checks. It explicitly cannot authorize implementing the proposal under review. The purpose row at 48 now consistently limits it to evidence collection. Together with the common admission gate, containment, protected floors and durable authorization at 25-40, this closes the exemption without creating a bypass or recursive approval requirement.

**B7 / 6.1 — RESOLVED.** The input table at 155-160 now defines every previously missing binding:

- `--effort-hint` / `AdapterRequest.effort_hint` carries the declared work-shape key.
- `routing.preference_map` supplies ordered registered binding IDs; selection applies the hard filters before choosing the first eligible binding.
- Explicit `effort_routing = "opt-in"` activates automatic selection. The built-in default is advisory, consistently stated at 159 and 291 and used in the example at 235.
- Missing hints preserve selection; unknown keys produce a validation error. No ordering is inferred from model names or tier strings.

The example mapping at 237-240 and schema entry at 292 make the field usable under core-key rejection. Exact pin-or-reject, no-eligible-candidate blocking and ABSENT quality remain at 164-168. Thus a blocked first preference advances only within the declared eligible order, an explicit pin cannot silently substitute another target, and absent opt-in cannot activate automatic replacement. No additional routing contract is required.

## 3. Regression verification

All items below remain **RESOLVED**.

| Item | Preserved specification and location |
|---|---|
| **B2 — Final Call** | Mandatory union, candidate/authority/disposition binding and atomic last-ACK checks (99-100); mandatory-floor transitions (474-475, 508-514); ACK retraction and serialized races (517-519). |
| **B3 — votes, REVIEW and outcomes** | Present REVIEW timing policy (62-64, 285); duplicate/correction/electorate rules (94-96, 507); approval versus denial/exception with valid proof, and new stored reopening deadlines (520-523). |
| **B4 — execution recovery** | Both separate tables remain complete: crash boundaries/partial broadcast (105-111) and all five certainty-to-retry rules (115-121). Attempt identity, lease-expiry and atomic claim/revocation fencing remain (123-126). |
| **B5 — payload lifecycle** | Consumer-safe temporary copies, retained canonical input and distinct verified ownership (129-131); positive retained TTL and consistent staging binding (249, 259, 294-297); cleanup debt, borrowed ownership and no-effect-retry rule (631-637); independent input availability and replay permission (654-655). |
| **B6 — restrictions and review effects** | Scope/epoch/authority and independent release rules (140-146); durable pending -> idempotent apply -> receipt reconciliation, shared dismissal guard and restart behavior (567-582, 599-600); stale-probe/shared/profile restrictions preserved (692-694). |
| **B8 — telemetry** | Normalize both representations/every entry, shared snapshot, exact completion-time window, attempt formula/exclusions and reset-safe bounded refresh (171-174). Display denominator agrees (716); forecast output remains absent. |
| **B9 — sessions** | Policy-driven saga/prepared-generation reuse (177-178); checkpoint action and required-failure hold (538-539); capacity/fingerprint/lease/continuity and generation guards (544); threshold/unknown/concurrency cases (551-559). |
| **B10 — host authority** | Exact subject or contained scope, proof persistence, invalid/unverifiable-proof rejection, valid scoped reuse and deferred deployment qualification (181-184). |
| **6.2 — defaults** | Four purpose/default/minimum rows (43-48), protected floors (28), configuration reference (199), and consistent proposal quorum (204, 752). |
| **6.3 — validation** | Unknown governing keys reject; enum/duration/bound/combination checks remain mandatory (363-376). The new routing mapping is explicitly in the schema (292). |
| **6.5 — ingress** | Common ask/broadcast contract, exactly one supported source, concrete exit-2 validation outcomes, unsupported stdin, fidelity and borrowed ownership (29-38); UTF-8 file handling remains (653). |

The counted **135 unique case IDs** agree with the corrected summary at 774-783: 20/26/31/10/12/3/13/11/9. The closure matrix at 315-324 may now mark all seven rows complete at design altitude while retaining **TEST NEEDED** for implementation.

## 4. Final disposition

**RATIFIED.** The lighter `DispatchPolicy` / `DispatchOrchestrator` structure, four restored exception mechanisms and bounded seven-row closure scope are accepted. The exclusion of the full D13 legacy ledger and twenty-plus namespace model remains accepted.

Earlier nonblocking editorial matters remain nonblocking: stale round labels/section references and short display/diagram labels interpreted through their explicit governing rules. In particular, cleanup shorthand remains subject to the consumer-safe lifetime rule, and generic human/arbiter labels remain subject to validated authority. None requires another design round.

There are **no remaining required design amendments** from this review. Implementation, acceptance-test execution and deployment qualification are subsequent work; this ratification does not claim those results. The Round 9 rejection is superseded for the exact revision identified above.
