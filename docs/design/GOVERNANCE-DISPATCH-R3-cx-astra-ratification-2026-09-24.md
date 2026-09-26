# Round 3 ratification review: governance and dispatch

Author: cx.astra. Date: 2026-09-24. **Verdict: NOT RATIFIED. A bounded revision is required.**

The `DispatchPolicy` / `PolicyResolver` / separate `DispatchOrchestrator` structure is an acceptable, simpler vessel. A single TOML authoring surface and named consultation modes are also acceptable. The draft's behavior is not yet sound: several grafts preserve the names of the original protections while omitting their enforcement conditions, and the inherited tables contradict some of the new rules. This is not a request to restore my entire Round 1 framework.

This review is design-only. It does not authorize implementation, configuration activation, legacy deletion, or deployment. No hub.py/peerhub dispatch, implementation edit, or commit was performed for this review.

## 1. Review basis

I read ag.opus's Round 1 proposal and MECE companion, then R2, R2b, and R3 in the requested order. I subsequently read both cx.astra Round 1 documents to check the original intent rather than reconstruct it from memory. I also checked the relevant earlier ratification and selected local source boundaries where R3 relies on an existing service being sufficient.

References used below:

- **R3**: [unified synthesis under review](GOVERNANCE-DISPATCH-R3-unified-synthesis-DRAFT-pending-cx-astra-2026-09-24.md). Line numbers refer to the reviewed file, SHA-256 `06d0237fa8f1049a12f2a9073aeb5d52151f011d6a698a3885bccfed9f25a8a6`.
- **A1 / AM**: [ag.opus proposal](unified-governance-dispatch-layer-R1-ag-opus-2026-09-24.md) / [MECE companion](unified-governance-dispatch-layer-R1-MECE-spec-ag-opus-2026-09-24.md).
- **R2 / R2b**: [cross-review](GOVERNANCE-DISPATCH-R2-cross-review-ag-deepthink-2026-09-24.md) / [exception-parity follow-up](GOVERNANCE-DISPATCH-R2b-exception-parity-check-ag-deepthink-2026-09-24.md).
- **C1 / CM**: [cx.astra proposal](GOVERNANCE-DISPATCH-R1-cx-astra-2026-09-24.md) / [MECE companion](GOVERNANCE-DISPATCH-R1-cx-astra-MECE-SPEC-2026-09-24.md). Dxx and case IDs identify their sections.
- **Prior ratification**: [2026-08-26 pre-TDD decisions](HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md), particularly items 11-15 and 41.

Source inspection establishes source structure only. It does not verify current CLI capability, runtime permissions, quota reset, deployment isolation, or provider behavior. No new live capability claim is made; runtime qualification remains **TEST NEEDED**. Historical runtime statements in other documents retain their original attribution, not a new source tag from this review.

## 2. Explicit O1/O2/O3 votes

| Original uncertainty | Vote on R3's proposed resolution | Reason and concrete resolution |
|---|---|---|
| **O1: quarantine interpretation** | **RATIFY** the interpretation. | Preserve useful containment, explicit operator quarantine, and typed-evidence circuit opening; reject fabricated evidence and blind prose-failure counters. An authorized operator's review disposition may directly request quarantine. It need not pass through my larger intent vocabulary. However, authenticated identity alone is not quarantine authority, and marking the review resolved is not proof the restriction was applied. R3 must satisfy B6 before the mechanism is ratified. |
| **O2: routing before quality evidence** | **CONTEST** advisory-only as a complete resolution of the original capability question. | C1 D07/D15 recommended actual opt-in automatic selection from declared preferences, independently of measured quality. Printing a recommendation does not select a profile. R2 incorrectly equates these two choices. Keep advisory as the default if desired, but add a bounded opt-in declared-preference selection mode with explicit pins, hard admission checks, and truthful provenance; see B7. Alternatively, explicitly retain automatic routing as an unclosed gap. Advisory-only is safe as a narrower deliverable, but it does not establish automatic-routing parity. |
| **O3: host authority boundary** | **CONTEST** deferring the whole issue to deployment. | Defer the concrete isolation mechanism and its real-environment qualification, not the authorization interface or fail-closed behavior. Require a designated-human issuer/validator contract now, with exact scope, expiry, replay identity, and revocation binding. Reuse existing proof references outside `DispatchPolicy`; a new cryptographic receipt format in that dataclass is unnecessary. Deployment must either prove issuer/store isolation or explicitly limit its claim to cooperative processes; see B10. |

These are separate votes. Agreement with O1's policy meaning is not blanket approval of the quarantine implementation sketch; disagreement with O2's parity claim is not an objection to truthful advisory output.

## 3. Fidelity of the four grafts

| Graft | Fidelity verdict | What survived; what did not |
|---|---|---|
| **SM-29/30/31: ACK retraction** | **PARTIAL; not sufficient yet.** | Pre-authorization withdrawal and historical immutability after authorization are faithful. A separate revocation record is a good fit for the lighter structure. Missing are candidate-bound ACKs, a frozen ACK set, candidate replacement invalidating prior ACKs, and the atomic connection between revocation and execution claim. CAS on the round alone does not fence a separately claimed effect. B2/B4 specify the missing conditions. |
| **EX-01/02/03: execution certainty** | **PARTIAL; essential recovery cases missing.** | The certainty vocabulary and prohibition on replacing a completed result with cancellation are faithful. All three rows concern cancellation, although a crash can happen without cancellation. There is no complete certainty-to-retry table, durable claim/revocation ordering, or settlement-only recovery case. Retention elsewhere incorrectly becomes replay permission. B4 supplies the minimum contract. |
| **TP-E-12/13: retention** | **CONTEST; materially mistranslated.** | Explicit retained-input opt-in is faithful. “Ephemeral = deleted on crash/uncertainty” is not what C1 D10 said. It required no durable canonical prompt retention, while temporary transport copies survive until consumption or confirmed consumer death makes cleanup safe. A crashed process cannot promise to execute a deletion handler. Retained bytes also do not prove replay is safe. B5 replaces these rules. |
| **HC-09/10/11: restrictions** | **PARTIAL; identity and release semantics missing.** | Scope-limited containment and rejection of stale recovery are faithful. An epoch counter alone omits authority-class separation, exact restriction/release identity, current membership, claim-time revalidation, and overlapping restrictions. HC-09 also says the circuit remains “closed,” whereas rejection must preserve its current state, normally OPEN when blocked. B6 specifies the correction and avoids duplicating existing fences. |

R2b was right to restore these four subjects. It was not correct that restoring a few isolated cases establishes exception parity. Their interactions are part of the requirement.

## 4. Blocking objections and minimal amendments

All B-items below require a design disposition before ratification. Their acceptance cases are specifications for later TDD, not tests implemented in this review.

### B1. Make the common policy gate unavoidable, bounded to plan altitude, and unable to weaken standing rules

**Location:** R3 lines 17-60, 93-116, R-05/R-17, and the per-dispatch tree at lines 237-254. C1 D02.3/D03.1/D05.1; CM POL-11..23 and INT-02/04/14.

R3 names a separate orchestrator but does not state which public paths must use it, where authorization is enforced, or how an approved plan covers child work. A1 explicitly leaves `ApplicationAPI.submit()` unchanged and puts the orchestrator above it. Separation of classes is fine; an optional wrapper around a callable execution gateway is not an enforcement guarantee.

The default is `quorum` for every unlisted action. A minimum of NONE on a vote does not exempt it from a higher default. Without an explicit consultation-purpose exemption, asking a reviewer or submitting a vote can itself require another consensus round. Conversely, NONE for an ask cannot make a new architectural direction exempt from DIR-006 merely because its command name is `ask`.

**Required amendment:**

1. Retain one resolver and one orchestrator, but identify the mandatory application admission point and the persisted authorization reference checked by the existing runner. Native ask, broadcast children, proposal/consensus transitions, and review effects in this round must reach the same evaluator. No alternate proposal auto-resolution path may bypass it.
2. Distinguish a new direction/material effect from contained work under a current plan authorization. A child uses the existing approval only when its operation, resource/effect scope, generation and relevant bounds are contained. Expired, revoked, or expanded work cannot inherit it. This can be a small relation in existing request records, not a universal new framework.
3. Exempt bounded consultation/vote/ACK collection from recursive consultation, while retaining actor, disclosure, and effect restrictions. Advisory REVIEW may time out without blocking only where no stronger obligation exists.
4. Merge preferences by the stated config precedence, then enforce protected standing obligations. CLI/workspace config cannot disable DIR-006, mandatory Final Call, or required human authority. A change to standing governance policy must be authorized under the prior policy; editing a lower-trust TOML file cannot authorize its own downgrade.
5. Persist the resolved effective values and their identity with the request/round before waiting or executing. A hash plus a DEBUG log is not enough to recover the frozen policy after its source file changes. State, decision receipt, and pending effect intent need a defined atomic commit boundary.

**Acceptance:** Equivalent CLI/API/proposal submissions get the same gate result; a new architecture request with NONE still receives the standing floor; two contained child operations reuse the plan approval; a changed resource requires a new review; a consultation does not recursively open consultation; restart uses the original snapshot, and an unapproved TOML downgrade has no governing effect.

### B2. Restore the complete Final Call integrity predicate, not only retraction

**Location:** R3 lines 109-110, SM-13..22/29..31. C1 D04.1; CM FIN-01..16. Prior ratification items 12, 13, 15, 41.

The four mutually exclusive trigger choices cannot express the ratified default “Tier-0 OR high-risk OR unresolved-dissent origin.” `tier_0_only` omits dissent-origin decisions; `dissent_only` omits high-risk unanimous decisions; `never` is currently allowed without a mandatory floor. High risk is also absent from the R3 trigger cases. This is an existing policy regression, not a request to make every ordinary decision run Final Call.

“All eligible ACK” does not say which candidate or which frozen set. A NACK blocks regardless of concern class, contrary to the ratified distinction between integrity objections and cosmetic preferences. No transition clears old ACKs after a concern disposition changes the candidate. A boolean ACK with a timestamp is insufficient to establish review of a particular final result.

**Required amendment:**

- Define effective Final Call as the mandatory trigger union plus optional extra triggers. Preserve unresolved-dissent ancestry after an exceptional arbiter disposition. `never` may disable only optional additional Final Call.
- Freeze candidate identity over the decision/effect bounds, effective policy, active vote/disposition set, and authority references. Freeze required ACK identities when the barrier opens; do not derive them from currently reachable peers or only agreeing voters.
- Require ACK and retraction to name candidate identity and actor. A changed contract/electorate requires fresh votes; a changed candidate under the same contract clears all ACKs. Classify blocking authority/safety/integrity/irreversibility concerns separately from nonblocking cosmetic feedback.
- Last ACK, all remaining live authority checks, and authorization must commit as one transition. Retraction wins first: hold. Authorization wins first: append a revocation request and apply B4's claim fence. Preserve withdrawn ACK history rather than deleting the attestation.
- Clarify SM-27: a resolved round cannot be rewritten, but an authorized adjacent revocation record is legal. Reopening after timeout/escalation must state a new deadline and invalidate any stale candidate ACKs; it cannot reuse indefinite old authority.

**Acceptance:** High-risk unanimous and dissent-origin low-risk cases both require Final Call; ordinary low-risk unanimous work need not. Wrong-candidate/pre-barrier ACKs reject; a changed candidate clears ACKs; a cosmetic preference does not veto; all serial orders of last ACK/retraction/claim have the defined outcome.

### B3. Resolve contradictory vote, dissent, and quorum semantics

**Location:** R3 CD-Q-03, CD-U-04/05, SM-06/07/12/18/24/26 and REVIEW rows. C1 D03.2-4; CM VOT-01..24. Prior ratification item 11.

R2b calls vote correction a settled semantic equivalence, but R3 still says a second vote is always `StaleRevisionError`, while CD-U-04 allows a superseding disagreement. It never specifies the correction event or removal from the active tally. CAS conflicts and semantic duplicate/conflicting votes are different conditions.

R3 also makes every explicit disagreement a QUORUM veto, while SM-07 reasons about remaining possible votes and `DISSENT_ONLY` expects a dissenting round to reach quorum. Those are different approval predicates. My C1 proposal deliberately distinguished ordinary disagreement from a qualifying blocking concern. R2's claim of complete convergence hid this real policy choice.

**Required amendment:**

1. Specify duplicate same-key vote -> same receipt/count once; changed vote without explicit correction -> conflict; authorized correction while voting -> superseding event, remove previous tally contribution, then require a fresh vote. After Final Call opens, use concern/new-candidate/new-contract rules, not silent vote replacement.
2. Use the recommended simple distinction: ordinary disagreement may coexist with a configured majority threshold, but not unanimity or a separately required affirmative voter; a qualifying concern remains blocking. If universal dissent veto is intentionally preferred, state that policy change explicitly and define how the dissent-origin Final Call/arbiter path is reached. Do not leave both predicates normative.
3. Freeze logical voter identities, independent non-proposer condition, required membership, denominator and absence reasons. Multiple profiles of one peer are not independent voters. Formation with fewer than the required minimum holds/fails; high-risk required absences cannot silently disappear through health filtering. No mid-round shrinkage.
4. Separate one-reviewer advisory REVIEW from binding consensus with the minimum-two rule. Define its timeout source even though the current dataclass says `consensus` is meaningful only at depth >= QUORUM.
5. Specify explicit outcomes for `resolved`: approved, denied, or exceptional authorization with its proof. A timeout or generic coordinator decision is not automatically permission to execute. Reopened rounds must either satisfy unchanged valid obligations under a defined new deadline or create a superseding round when material facts changed.

**Acceptance:** Correcting the decisive vote removes quorum; majority-plus-ordinary-dissent follows the chosen rule; a typed integrity concern blocks; same-peer profiles cannot satisfy independence; unreachable high-risk required voters hold; REVIEW does not violate binding consensus's two-voter floor; timeout escalation never manufactures agreement.

### B4. Define durable claim/revocation and crash recovery independently of cancellation

**Location:** R3 lines 64-66, SM-30/31, EX-01..03 and TP-E-13. C1 D05.2-3; CM S09, especially C3..C7 and EXE-05..17/25..28.

EX-01..03 do not cover a crash with no cancellation request. Nor does “replay requires idempotency validation” define which evidence permits another attempt. Local mutation idempotency does not prove an external operation is idempotent. A retained file and a request ID cannot establish that a prior external side effect did not happen.

**Required amendment:** Keep execution state in the existing runner/store, with the orchestrator invoking it. Specify this minimum table:

| Observed certainty | Recovery rule |
|---|---|
| NOT_STARTED, proven | A new attempt may run only if current authorization, admission, input, deadline and retry bounds permit. |
| MAY_HAVE_STARTED | Reconcile using stable process/provider request identity. No automatic non-idempotent replay. Independently proven safe/idempotent work may retry within bounds. |
| STARTED, no definitive terminal outcome | Same replay restriction; a cancellation request is not proof of death or non-effect. |
| COMPLETED success | Return/settle that result. Retry cleanup/accounting/notification only, never the model/effect. |
| COMPLETED failure | Retry only when the operation-specific effect semantics establish safety. A nonzero exit alone does not prove zero side effects. |

Claim must atomically check the relevant live authorization/revocation version, restriction epoch, target/session binding and current attempt ownership. Concurrent claim and revocation must share a serializable guard, not only separate CAS updates on unrelated records. If revocation wins before claim, no new claim. If claim wins, recheck before adapter invocation and apply best-effort cancellation/future-work fencing; do not promise atomic remote cancellation without an appropriate adapter guarantee.

Every retry has its own attempt ID and transport lifetime. Lease expiry does not prove the old process is dead. A lost result commit leaves an uncertain attempt; a lost response after result commit returns the stored receipt. Partial broadcast recovery never repeats successful children to repair a sibling or the parent summary.

**Acceptance:** Inject crashes before claim, after claim before observed handoff, after external effect before result commit, and after result commit before cleanup. No-cancellation cases must be included. Race revocation with claim in both orders; retained non-idempotent input with unknown execution remains blocked; cleanup failure does not cause a second successful invocation.

### B5. Replace the inconsistent prompt lifecycle with separate input retention and transport ownership

**Location:** R3 lines 68-69, 142-154, 456-497; TP-E-06/07/08/12/13. C1 D10.2-3; CM IPC-12..30. Also [dot-directory proposal, section 6](dotdir-consolidation-peerhub-proposal-2026-09-09.md).

There are five concrete defects:

1. The inherited tree says UNCERTAIN -> “preserve always,” while section 2.3 says ephemeral content is not retained on disk on uncertainty. TP-E-12's “normal lifecycle” cannot resolve two opposite rules.
2. Ephemeral staging still defaults under durable `.peerhub/`, the location identified in the IPC hygiene gap. Deletion after an outcome does not prevent prior backup/durable-state exposure.
3. Deleting an uncertain consumer's file can break a delayed read. Conversely, a crash cannot guarantee immediate deletion. This mistranslation first appears in R2b section C, not C1.
4. TP-E-13 declares retained input “safe for automated replay.” Availability of bytes and safety of replay are independent conditions.
5. Age-only sweeping, request-level file reuse, and accepting externally modified staging content do not preserve attempt ownership or the authorized payload identity. TP-E-08 expressly relies on a dispatch not taking too long, despite uncertain/zombie consumers being part of this design.

**Required amendment:**

- Ephemeral means no canonical prompt bytes in durable request/audit/WAL/backup state. Prefer memory/stdin; when a pathname is required, use a private volatile temp root with attempt-owned transport files. This is not a secure-erasure or crash-immediate-deletion promise.
- Keep a copy while its consumer may still need it. Delete after trusted consumption acknowledgement or confirmed consumer exit/death. Unknown consumer lifetime creates bounded recovery/cleanup debt; age alone is never sufficient proof for deletion. Do not reinterpret that temporary safety hold as opt-in durable replay retention.
- Make every retry and broadcast child own a distinct exclusively created transport copy. Verify expected content identity before consumption where the adapter boundary can enforce it; a changed payload rejects before execution rather than silently becoming the approved prompt. State the cooperative-filesystem limit honestly.
- Retained mode opts into a protected canonical input with explicit TTL and deletion policy, separate from scratch copies. Restart checks both input availability/integrity and B4's replay predicate. An unchanged borrowed source can be another permitted source; it remains caller-owned and is not deleted by cleanup.
- Use `INPUT_REQUIRED` only when exact authorized input bytes are actually unavailable. It is an input blocker, not an execution-certainty reset. A request can need input and still need reconciliation of its earlier attempt. Re-supplying bytes does not authorize unsafe replay.
- Define retention/cleanup precedence. Ephemeral scratch must not inherit “keep failures for debugging” as indefinite durable retention. Cleanup failure leaves debt separate from the actual outcome. Retention policy applies to inline prompts too, not only payloads crossing the staging threshold.

**Acceptance:** Crash with a still-live late reader preserves only its owned temporary copy until safe cleanup; stale-age live/unknown consumers are not swept; ephemeral durable stores contain no raw prompt; retained uncertain non-idempotent work does not replay; expired/missing input yields INPUT_REQUIRED; one broadcast child cannot delete another's copy; modified bytes reject; a borrowed source is never implicitly consumed.

### B6. Preserve independent restriction authority and make review application recoverable

**Location:** R3 section 2.5, QR-E-02/03/07/09/10, HC-05/06/09..11 and concurrency table. C1 D06; CM HLT-05..26.

The graft preserves scope and stale-probe intent but does not define how multiple restrictions compose, what a release clears, or what claims recheck. HC-11 must mean that a profile-specific restriction alone does not restrict siblings; a sibling may independently be blocked by a shared restriction. HC-09 must preserve the blocked circuit, not set it CLOSED.

“Already quarantined -> no-op” is too broad. An identical retried impose event should be idempotent; a new security/manual restriction or a genuinely new incident must not vanish merely because another restriction already blocks admission. A recovery probe cannot clear a later manual/security hold, even if the operational circuit recovers.

The review-to-health transaction is also load-bearing. In the inspected source, [quarantine_review.py](../../peerhub/application/quarantine_review.py) lines 124-153 submit the resolved review before invoking health. Replacing the called function alone leaves a crash/failure window where the review is closed but the restriction is absent. This is a static call-order finding, not a live failure claim.

**Required amendment:**

1. Define restriction identity as scope/subject binding plus authority class and current revision/epoch, with explicit expiry policy. Effective admission is the conjunction of operational readiness and absence of every applicable blocking restriction. Release names the exact current restriction and valid authority; it does not manufacture health or clear unrelated holds.
2. Keep automatic recovery proof separate from administrative release. Define shared account/provider membership through registered binding identity, including changes to membership; no profile-name prefix inference. Claim rechecks the live applicable epoch, and running work follows an explicit stop/drain policy without rewriting votes.
3. Authenticate and authorize the operator separately from the quarantine target. A review vote/recommendation is not an operator grant. `ESCALATE` may mean direct impose only for an operator whose existing grant covers that target/scope; otherwise it requests authority and remains pending.
4. Use either one local transaction for review plus restriction, or a durable pending effect plus idempotent applied receipt and reconciliation. A review becomes applied only after the matching effect is known applied. Validate target/authority before any terminal review update.
5. Treat uncertain execution and health evidence as independent dimensions. HC-06 may prohibit inventing failure from uncertainty, but must not discard independently trustworthy provider evidence or prevent an authorized operator hold merely because effect certainty is unknown. Similarly, recovery of one circuit never implicitly releases another restriction.

**Reuse correction:** R2b's claim that existing health has no relevant scope/fencing capability is too broad. Static inspection finds ROOT/PROFILE/QUOTA_FAMILY/ENVIRONMENT scopes in [health/contract.py](../../peerhub/health/contract.py) lines 112-118 and receipt-identity/authority checks in [health/model.py](../../peerhub/health/model.py) lines 567-606. Reuse those where their semantics satisfy the contract; explicitly add only missing membership, composition, or claim fencing. Do not create a second health authority merely to adopt the word epoch.

**Call-boundary correction:** A1's example passes `AuthenticatedSubject` as the quarantine target and uses `REVIEW_ESCALATION`. The inspected [service signature](../../peerhub/health/service.py) at lines 1159-1178 takes a string target subject, and the declared authority enum currently lists AUTOMATIC/MANUAL/SECURITY/POLICY. The design must map operator, target, and authority class explicitly; it is not a drop-in one-line replacement. This is a source-level compatibility observation, not a demand to preserve the current API spelling.

**Acceptance:** Crash after request/pending-effect commit and after actual restriction application reconciles exactly once; invalid target/authority cannot leave a falsely applied review; old probe and stale release preserve a newer restriction; circuit recovery leaves manual quarantine; shared and profile restrictions compose; a new security hold is not swallowed as an existing-open no-op; newly restricted admission blocks a previously selected route.

### B7. Resolve routing parity honestly and remove unsupported effort inference

**Location:** R3 provenance, `[routing]` at lines 129-136, O2; A1 section 4.5; C1 D07/D15 O2.

Advisory-only output is a legitimate simpler first deliverable. It is not the automatic work-shape routing I proposed, and R2's assertion that both proposals choose the same behavior is incorrect. Verified invocation identity/capability and measured task quality also remain different facts.

The inherited `prompt_length` option treats length as an effort proxy; A1 calls the resulting estimate compliant merely because it is labeled `[estimated]`. DIR-004 does not grant that exemption. Byte/character count can be an observed input; it does not measure task difficulty or model suitability. R3 also never explains how an effort-aware advisory ranks profiles when the native-v1 capability design deliberately leaves effort-quality ABSENT.

**Required amendment:** Use a small declared preference mode: off, advisory, or explicit opt-in automatic selection. The policy maps a declared work-shape/hint to ordered existing profile bindings. If deterministic shape rules are included, record them as operator-declared routing rules, not measured effort. Remove prompt-length-based effort inference from this round; size remains a transport constraint.

Explicit profile pins win over suggestions but never over health, permission, actual binding/capability, or scope requirements. Automatic selection applies hard filters before the configured preference order; unavailable quality stays ABSENT. No candidate means an explicit blocker, not bypass through a pinned quarantined profile. Selection is a preference decision, not a claim of achieved quality. Existing advisory default may remain unchanged.

The [native-v1 backlog narrowing](PEERHUB-BACKLOG-2026-08-27.md) deliberately deferred effort-quality scoring. This amendment would be an explicit new declared-preference policy revision for this redesign, not a claim that the older scoring decision already allowed it. If that revision is rejected, record automatic routing as deferred and do not call the six-gap capability set complete.

**Acceptance:** Advisory never alters the selected profile; opt-in automatic mode follows declared preferences among eligible real bindings; explicit pin is preserved or rejected, never silently substituted; quality remains ABSENT; no effort recommendation is advertised as measured from prompt length.

### B8. Restore the actual telemetry normalization requirement

**Location:** R3 telemetry configuration and section 8, TH-01..08. C1 D09; CM TEL-01..06/15..19 and INT-10/11; R2 section 3.

The current R3 contains display tiers and a refresh hook but no normalization rule for `rateLimitsByLimitId` and no corresponding case. A refresh cannot recover a limit discarded by normalization. This is one of the six original gaps, not a feature outside the agreed scope.

The source read corroborates the specific boundary: [quota_polling.py](../../peerhub/telemetry/quota_polling.py) lines 495-503 consume `rateLimits`; that observation does not establish live provider response contents. R2 already distinguished the earlier envelope fix from missing per-limit normalization. R3 loses that distinction.

**Required amendment:**

- Normalize both `rateLimits` and every `rateLimitsByLimitId` entry before selecting a profile/family view. Preserve source, account/pool identity, limit ID, window/reset identity, applicability and freshness. Deduplicate identical representations; retain/report conflicts and unknown applicability. A malformed bucket must not erase independent valid buckets.
- Derive display/admission values from the same normalized snapshot. If the public `rate_limit_state` field is retained, define how it maps these facts; naming the field alone is insufficient.
- Define the 24h reliability denominator and observation unit. Recommended minimum: started attempts with definitive success/failure, deduplicated by attempt ID, completed within `(as_of - 24h, as_of]`; show failure and total counts. Show cancellation, unknown result and pre-admission rejection separately. Label retry-inclusive attempt rate distinctly from request outcome rate; show partial coverage if retention is incomplete.
- Bound optional refresh and report source-specific absence/error/staleness. Default diagnostics remain read-only and never invoke a model to manufacture health data.
- Remove “headroom forecast” from this round. R3 lines 548/560 add prediction beyond observed usage and historical deltas, while A1's main proposal explicitly said no forecasting. A measured historical delta within the same pool/window can remain; resets or changed window identity break that comparison. Two points alone do not justify an exhaustion forecast under DIR-004.

**Acceptance:** ByLimitId-only, both-disjoint, duplicate, conflicting, mixed-valid/malformed and unknown-family fixtures preserve their information. A retry/cancel/unknown mixture yields the specified counts; empty coverage does not render 0%; a pool reset does not appear as a consumption trend; failed refresh does not suppress other valid observations.

### B9. Wire meaningful session policy values instead of promising an unchanged saga implements them

**Location:** R3 `[session]`, SS-02..08; A1 section 4.4 and its “unchanged saga” claim. C1 D08; CM SES-05/09/12..20.

Choosing ag.opus's more permissive unknown-pressure/default reuse policy is a legitimate design choice. I do not insist that my proposed soft-band rotation or a fourth `none` mode return. But the choice must be explicit, and the configurable thresholds must actually determine the saga's decision.

Static inspection of [session_saga.py](../../peerhub/application/session_saga.py) lines 98-134 shows no soft/hard threshold parameters and a peer-name branch selecting 0.90 for `cc`, 0.75 otherwise. Therefore wiring that method unchanged cannot realize R3's single configured soft=75/hard=90 policy. Its existing signature and behavior are evidence against the “just connect it” claim, not live qualification of a replacement.

**Required amendment:** Pass a normalized session sub-policy into the saga and remove general-layer peer identity decisions. Choose exact boundaries and the meaning of both thresholds. My suggested small reconciliation is: below soft reuse; soft through below hard checkpoint/mark pending; at or above hard request safe rotation for auto; explicit reuse checkpoints and proceeds only if no separately established hard capacity/compatibility restriction forbids it. Pressure thresholds are policy triggers, not proof of a provider's physical limit.

Unknown/stale pressure may follow R3's permissive reuse choice, but render it as unknown rather than “no pressure,” and retain actual capability/context-error admission guards. `fresh` bypasses pressure preference, not lease ownership, compatible binding, required continuity, or execution-uncertainty fences. Checkpoint failure cannot be reported as checkpoint success; define whether it holds the attempt. Retry after a successful rotation reuses that prepared generation instead of rotating again merely because the original mode was fresh.

**Acceptance:** Exact threshold and unknown/stale cases use policy values for every peer; required checkpoint failure holds; two fresh claims cannot corrupt one binding; fingerprint mismatch never resumes; an old uncertain attempt cannot be duplicated by rotation; changed room generation fences the old active pointer.

### B10. Specify the host authority contract now; qualify its enforcement at deployment

**Location:** R3 O3, timeout/escalation transitions, QR-E-09. C1 D04.2/D15 O3; CM AUT-01..10. Prior ratification's designated-human decision and item 15.

The choice presented in O3 is a false binary. A cryptographic schema inside `DispatchPolicy` is unnecessary, but “deployment concern” cannot leave dispatch accepting an actor string or any authenticated worker as a human override. Authentication, operator designation, and scope-specific authorization are separate checks.

**Required amendment:** Define a trusted approval/validation interface whose proof binds designated principal, request/candidate or allowed standing scope, effect/exception, expiry, replay identity and revocation version. Persist its proof reference with the decision. Reuse already-valid scoped authorization; do not ask the human again for each child. If proof is absent, invalid, stale, revoked, out of scope, or cannot currently be validated, the relevant effect waits/rejects. No coordinator/peer-consensus fallback impersonates the human.

Choose and qualify the deployment boundary later using a test that attempts peer-origin issuance, proof replay and direct authority-store modification. Signing alone is not a boundary if the peer can use the issuer secret or alter the authoritative store. Until isolation is demonstrated, describe only the actual cooperative-process trust model; do not advertise resistance to a malicious same-user peer. This does not block pure design work or unrelated operations without a human-grant requirement.

**Acceptance:** Actor-string spoofing, wrong subject, expired proof, cross-candidate replay and untrusted issuance are rejected at application boundaries; existing valid scoped grants are reusable. Strong anti-impersonation deployment claims remain gated on real boundary evidence.

## 5. D13 and the 20-plus policy namespaces

**I agree with dropping the full D13 action ledger from this round's implementation scope and with collapsing the configuration model.** There is no reason to implement a universal Intent/Contract/Attestation framework, a namespace for every legacy service, or a permanent compatibility engine merely to complete these dispatch/governance changes.

I disagree with the explanation that the ledger itself requires importing every old action into the runtime. C1 D13 described a migration/review inventory, not 20 new runtime subsystems. Several rows also need reconciliation with later explicit out-of-scope/host-tooling decisions. Keeping that work outside this redesign is sensible; erasing the eventual useful-capability/caller check is not.

The minimum retained obligation is a **small closure matrix for this round**, not the old full ledger:

| Scope | Required closure evidence in later implementation |
|---|---|
| Consultation and plan authority | Actual ask/API entry path, protected floor, inherited plan scope, no approval recursion. |
| Final Call | Proposal and consensus paths converge; trigger/candidate/ACK/retraction and exceptional-authority cases reach the same gate. |
| Review quarantine | Authorized target restriction actually applied, with crash reconciliation and independent release semantics. |
| Session rotation | Public dispatch reaches policy-driven saga with binding/lease/context guards. |
| Profile selection | Advisory versus opt-in automatic behavior identified honestly; pins and hard filters preserved. |
| Telemetry | Limit-identity normalization and explicit reliability counts visible through native diagnostics. |
| IPC and execution recovery | Query-file/inline/broadcast ownership, input retention, claim certainty, retry and cleanup agree. |

Each row needs its native entrypoint, owner, relevant case IDs, eventual boundary-test evidence, and status such as specified/verified/deferred. An unwired unit-tested primitive cannot close it. The broader legacy caller/capability migration check remains at the separately governed cutover gate; this round cannot declare full hub.py replacement readiness or retire unrelated capabilities.

The parts of my richer model still needed **beyond the four graft labels** are narrowly identified: plan-scoped authorization and protected floors (B1); candidate-bound Final Call plus ordinary vote correction/independence (B2/B3); original telemetry normalization (B8); and actual session-policy wiring (B9). They fit existing records/services and the single TOML surface. Most of B4-B6 are completing the four already-accepted grafts, not expanding the redesign.

## 6. Additional specification corrections

These are smaller than the blocking semantics above, but should be fixed in the same revision so a self-contained document has one executable interpretation:

1. **Publish the actual resolver and sub-policy contracts.** R3's dataclass only references `ConsensusPolicy`, `RoutingPolicy`, `TransportPolicy`, etc.; it does not define them. The TOML omits `recovery_authority`, routing fallback/capability fields and normalization behavior that the dataclass/tree refer to. Include a compact field/default/type/owner table and the explicit merge order. The per-command minimum table from A1 is absent. Do not call comments a complete schema.
2. **Correct default contradictions.** `[consultation.overrides]` assigns unanimous to `consensus.propose`, while the compatibility table says its default is quorum. Default quorum for all unlisted actions also conflicts with “all defaults = current behavior” unless ask/broadcast/vote purposes are explicitly resolved. State actual intended defaults and their intentional semantic changes.
3. **Reject misspelled governing keys.** R-06 silently ignores all unknown keys. A typo in retention or consultation must not silently select another policy. Reject unknown core keys; if extensions are needed, give them an explicit inert namespace. Add validation for retention mode, recovery authority, durations, threshold ranges, missing mandatory sub-policies and impossible combinations. A frozen dataclass must contain immutable nested values, not caller-mutable lists/dicts.
4. **Remove the false “bijective” mapping claim.** Mapping eleven integer values into five modes is many-to-one, and C1's R:3 meant independent review while A1 maps R:3 to NOTIFY. I accept dropping the numeric interface in favor of named modes; do not claim the Round 1 presets were identical or behaviorally bijective. If old numeric callers are later supported, give them an explicit authorized migration mapping.
5. **Make ingress rules self-contained.** State exactly-one-input-source semantics, encoding/size handling, original query preservation, and borrowed-file ownership on ask and broadcast. R3's staging cases alone omit several CLI validation cases from A1's main proposal. An invalid/empty source must get the explicitly chosen outcome before any dispatch.
6. **Fix counts and misleading certainty.** I counted 132 distinct enumerated case-ID rows in the reviewed R3, not the stated 133. Consultation has 26 rows; health has 11; EX-01..03 are omitted from the summary. The count error is minor. The stronger issue is the assertion that the table is ready for direct TDD translation while several rows say `depends` or contradict another table. Enumerate the decisive guards, illegal-event fallback, deadline boundaries, and coupled race cases; a large list is not by itself MECE coverage.
7. **State the actual service changes.** Frozen-policy persistence, candidate ACKs, review effect reconciliation, scoped admission fencing, threshold-driven saga input and payload ownership are real changes where existing contracts do not supply them. The orchestrator may remain thin because services own these behaviors; do not claim the services stay unchanged or that their current signatures already implement the proposed guarantees.

## 7. Next-round ratification gate

Retain the lighter architecture. Revise its predicates and tables in place; do not append another layer of exceptions underneath contradictory inherited rules. Resolve B1-B10 with the concrete contracts and acceptance cases above, and make the configuration describe the resulting behavior exactly.

The next review does not need the full legacy ledger, twenty namespaces, a new workflow engine, a dependency migration, or a live model dispatch. It does need one consistent answer for authorization, candidate integrity, claim/revocation, input availability, execution uncertainty, cleanup ownership, restriction release, routing parity, and the two original telemetry/session wiring gaps.

**Final vote: NOT RATIFIED as written.** O1's interpretation and the lighter structural direction are accepted. O2's parity claim and O3's blanket deferral are contested with concrete alternatives. All four grafts need the corrections above before their original protection can be claimed preserved.

<!-- terminal-verified 2026-09-24: spot-checked the "bijective" wording claim (confirmed present verbatim in unified-governance-dispatch-layer-R1-ag-opus-2026-09-24.md line 90) and the quarantine_review.py call-order claim (confirmed: _submit() at line ~124 precedes authorize_administrative_recovery() at line ~145, exactly as described). Both accurate. -->
