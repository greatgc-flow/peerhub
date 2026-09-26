# Peerhub governance and dispatch: MECE behavioral specification

Independent Round 1 by cx.astra, 2026-09-24. **DRAFT, PRE-TDD, NOT RATIFIED. No implementation or test code.**

Architecture: [one contract, one execution path](GOVERNANCE-DISPATCH-R1-cx-astra-2026-09-24.md). Dxx references identify its sections. Case IDs below are stable review/TDD references. Outcomes describe proposed behavior, not claims that current CLI paths implement it. G1-G7 in D00 are accepted session-supplied audit ground truth, not re-tested here.

## S00. How to read and implement this specification later

### S00.1 Exhaustiveness rule

MECE means mutually exclusive input/state partitions with a defined outcome for every member of the modeled space. It does not mean a finite document proves every possible operating-system or provider failure absent.

Every mechanism below defines its finite states, event classes, guard partitions, and fallback. For **every state x event** pair, instantiate either its listed transition or `INVALID_TRANSITION` with unchanged authoritative state. For **every guard**, test valid, invalid, and absent values where representable. Unknown schema enum values are invalid input, never silently mapped to a convenient existing state.

Use full Cartesian coverage for dimensions that determine a safety predicate: gate state x quorum x authority x Final Call; execution certainty x idempotency x retry; restriction class x release authority x evidence epoch; payload ownership x consumer lifetime x cleanup action. Pairwise sampling is acceptable only for unrelated presentation fields after independence is justified. Boundary values and race histories are mandatory, not randomly hoped for.

For each case the future test record must include: case ID, operation surface, fixture/config revision, actor/origin, initial state+revision+epoch, evidence state+time, input/idempotency key, injected fault/schedule, expected state, returned reason/receipt, effect count, audit/outbox delta, and cleanup result. A rejection checks **zero unauthorized effects**, not only an exception string.

### S00.2 Shared ordered guards

The first failing guard defines the primary reason. Additional diagnostic reasons may be listed without changing the transition. This ordering makes overlapping failures mutually exclusive for the primary outcome.

| Order | Guard partition | Required result |
|---|---|---|
| U1 | Malformed/unsupported command schema, field type, enum, encoding, oversized metadata | `INVALID_INPUT`; no domain state mutation. |
| U2 | Actor authentication or read/write scope absent/invalid; origin cannot perform this operation | `UNAUTHORIZED`; no receipt disclosure beyond allowed metadata. |
| U3 | Same idempotency identity and exact same content / same identity different content / unseen identity | Return existing authorized receipt without effects / `IDEMPOTENCY_CONFLICT` / continue. Read authorization still applies to replay. |
| U4 | Expected resource/intent revision or identity epoch stale, scope mismatched | `STALE_REVISION` or `SCOPE_MISMATCH`; caller reloads, never overwrites blindly. |
| U5 | Deadline/lease/grant validity and revocation guard fails | `EXPIRED`, `REVOKED`, or `STALE_FENCE` as typed by the object; no new effect. |
| U6 | Event not legal in current state | `INVALID_TRANSITION`; no repair by guessing. |
| U7 | Domain obligation/evidence/eligibility/semantic predicate unsatisfied | Explicit `WAITING`, `BLOCKED`, or domain rejection with unmet rule IDs. No generic transport-failure label. |
| U8 | Reservation/storage/resource cannot safely be obtained | `RESOURCE_UNAVAILABLE`; retain prior committed state and reconcile partial external preparation. |
| U9 | All prior guards pass | Commit the defined transition, receipt, and effect intent atomically. |

Timeout transitions are explicit system events with their own authority. Ordinary rejected commands do not secretly expire unrelated records. A status projection may render a deadline as elapsed before a sweep persists the expiry; it must expose both committed state and effective blocker.

### S00.3 Common invariants and fixture dimensions

| ID | Invariant / required partitions |
|---|---|
| INV-01 | A completed decision is not a completed effect. No effect without a currently valid claim and authority proof. |
| INV-02 | Contract subject, electorate, quorum rule, and policy revision are immutable after opening. No health/config refresh changes a denominator. |
| INV-03 | Votes, Final Call ACKs, operator grants, and observations are distinct types. None implicitly substitutes for another. |
| INV-04 | Same logical peer's profiles provide at most one vote; required independent agreement uses configured failure-domain identity. |
| INV-05 | State, transition receipt, and outbox intent commit together. A duplicate request creates no duplicate effect intent. |
| INV-06 | Provider/process result uncertainty remains explicit; exactly-once external execution is never inferred from local idempotency. |
| INV-07 | Health/admission, registry, sessions, policy, and receipts each have one owner. Status is not another writer. |
| INV-08 | Missing/stale/unavailable/error evidence never becomes zero, unlimited, healthy, or measured quality. |
| INV-09 | Only typed trusted input can satisfy measured-evidence predicates; prompt text cannot grant authority. |
| INV-10 | Every waiting state has configured expiry/escalation; every owned temporary payload has a tracked cleanup path. |
| INV-11 | Config reload cannot change a frozen contract; explicit revocation can prevent further execution. |
| INV-12 | All supported command surfaces use the same policy/gate/runner and outcome normalizer. No legacy auto-resolve shortcut. |
| INV-13 | General code is peer-agnostic. Adapters supply syntax, verified binding, transport/session capabilities, and typed receipts. |
| INV-14 | Plan-child containment uses typed effect bounds and aggregate resource budgets, not semantic similarity. |
| INV-15 | Rejection never causes an unrelated cleanup, quarantine, external call, or policy mutation. |
| INV-16 | Prompt content is absent from durable telemetry/audit/WAL in ephemeral mode; permitted retained inputs are explicitly labeled. |

Common fixture axes: actor `{designated-human, worker, terminal/router, system-maintenance, unauthenticated}`; object `{missing, live, terminal, archived}`; time `{before, equal, after deadline, backward jump, forward jump, restart}`; evidence `{MEASURED, ABSENT, UNAVAILABLE, ERROR, STALE}` plus independent `{consistent, conflicting}`; transport `{connected, unavailable before send, disconnected after send}`; identity `{same, changed binding, stale epoch, reused display name}`.

Test IDs must use deterministic clocks, IDs, peer ordering, fake adapter observations, and injected CAS/process/storage boundaries. No real sleep is required to test a deadline. A deployment qualification suite later supplies actual Windows/PTY/process/file evidence; unit simulations alone do not establish it.

## S01. Policy, classification, and scoped delegation (D02-D03/D11)

### S01.1 States and transitions

Policy revision states: `DRAFT -> VALIDATED -> ACTIVE -> RETIRED`; invalid validation stays DRAFT with errors. Activation under prior authority atomically selects the new revision and retires the prior active one. Already-open contracts reference their immutable revision; retirement does not delete it. A draft cannot authorize its own activation.

Preparation states: `UNPREPARED -> PREPARED | NEEDS_SCOPE | REJECTED`. PREPARED includes immutable resolved obligations and either a verified parent-grant binding or a new gate. NEEDS_SCOPE permits bounded investigation only. Changed material input starts a new intent revision; it never edits the old contract.

Grant validity is a projection separate from an authorized decision: `LIVE | EXPIRED | REVOKED | EXHAUSTED`. Only LIVE can cover new claims. Completed decision receipts remain historical facts in every case.

| Case | Exclusive condition/event | Expected result and effect constraint |
|---|---|---|
| POL-01 | Valid operation with fully bounded effects and one applicable rule set | PREPARED; record each selected rule and policy revision. |
| POL-02 | Unknown operation kind or unsupported effect class | Reject; no fallback to generic shell/write execution. |
| POL-03 | Multiple rules, comparable strictness | Intersect permission scope, union required obligations/prohibitions; never last-file-wins weakening. |
| POL-04 | Mutually incompatible/incomparable required rules | `POLICY_CONFLICT` naming both rules; no guessed precedence. |
| POL-05 | Risk evidence absent, ambiguous, or contradicts declared low risk | Apply configured conservative floor; unknown material effect scope remains NEEDS_SCOPE. |
| POL-06 | Prompt says read-only/human-approved but typed command can write | Typed effects control scope; prompt assertion supplies no authority. |
| POL-07 | Explicit R is one of 0/3/5/8/10 and >= floor | Use requested preset; show resolved rule. |
| POL-08 | Explicit R is valid but below floor | Preserve floor and report requested/effective values; no silent downgrade. |
| POL-09 | R outside aliases, fractional, negative, string coerced as number, NaN/infinity | INVALID_INPUT; no interpolation or rounding. |
| POL-10 | Auto selector at either side/exact boundary of each configured risk/work-shape rule | Deterministic inclusive/exclusive rule table outcome; if rule overlap changes authority, resolve strictness or POL-04. |
| POL-11 | New direction/architecture/standing policy/destructive scope | DIR-006 floor applies even when a requested preset is low. |
| POL-12 | Child kind, resources, permissions, budget, profile envelope, generation, and assumptions all contained | Reuse LIVE parent grant; no new peer consultation. Record the containment proof. |
| POL-13 | Exactly one containment dimension outside bounds | New contract/NEEDS_SCOPE, parameterized over every dimension; no child effect. |
| POL-14 | Multiple children concurrently reserve last available budget | Only admissible aggregate reservations commit; no per-child duplicate budget allowance. |
| POL-15 | Parent expired/revoked/exhausted while child waiting | New claim blocked; child cannot refresh parent expiry. |
| POL-16 | Nonmaterial config change while round open | Existing contract unchanged; new intents use new revision. |
| POL-17 | Explicit critical revocation while round/authorized grant exists | Advance authority epoch; new claims fail even though historical quorum remains recorded. |
| POL-18 | Actor can edit authoring file but lacks policy activation grant | No operational change; proposed import remains unactivated. |
| POL-19 | Unknown core config key / `x-*` extension / missing required key | Reject / preserve inert extension / reject activation. Extension cannot affect authorization. |
| POL-20 | Impossible quorum, threshold order, cyclic fallback, missing operator/binding, invalid duration/size | Reject policy activation with field-specific diagnostics. |
| POL-21 | Review/consultation request within disclosure and read scope | Bounded nonbinding analysis allowed; no recursive consensus requirement; implementation effects prohibited. |
| POL-22 | Policy activation races two drafts at same expected revision | One wins; loser STALE_REVISION; no mixed revision. |
| POL-23 | Authorizing draft proposes to lower its own approval requirement | Evaluate activation under old policy; cannot self-ratify. |
| POL-24 | No parent grant and actor has an applicable scoped standing user grant | Reuse exact matching grant without requesting it again; peer/Final Call obligations still separately evaluated. |

## S02. Electorates, votes, and gate transitions (D03)

### S02.1 Gate transition table

Gate events are `open`, `vote`, `invalidate_vote`, `evaluate`, `open_final_call`, `ack`, `retract_ack`, `concern`, `grant`, `arbiter_opinion`, `deadline`, `deny`, `cancel`, `supersede`. Grant/arbiter events update associated proof records only if authorized, then call the same evaluation function. Unknown events reject under U6.

| Current state | Event and guard | Next state | Mandatory side effect/receipt |
|---|---|---|---|
| Absent | open with valid prepared contract | REVIEWING | Freeze contract/electorate, record unmet obligations. |
| REVIEWING | valid vote/grant/opinion/invalidation, requirements still unmet | REVIEWING | Append attestation or invalidation; recompute current tally. |
| REVIEWING | evaluate, all non-final obligations satisfied, Final Call required | FINAL_CALL | Record quorum/exception basis and new immutable candidate+ACK set atomically. |
| REVIEWING | evaluate, all obligations satisfied, no Final Call required | AUTHORIZED | Authorization receipt; no execution yet. |
| REVIEWING | deadline / deny / cancel / changed subject or electorate | EXPIRED / DENIED / CANCELLED / SUPERSEDED | Reason, unresolved obligations, audit; no grant. |
| FINAL_CALL | valid ACK, some required ACKs missing | FINAL_CALL | Store candidate-bound ACK. |
| FINAL_CALL | last ACK and all live grant/concern guards valid | AUTHORIZED | Atomic evaluation + authorization receipt. |
| FINAL_CALL | retract ACK or raise qualifying concern before authorize | FINAL_CALL | Invalidate ACK or record blocker; no authorization. |
| FINAL_CALL | concern resolved without contract change | FINAL_CALL | New candidate epoch/digest; clear all prior ACKs. |
| FINAL_CALL | changed subject/policy obligation/electorate | SUPERSEDED | New contract required; old ACKs invalid. |
| FINAL_CALL | deadline / deny / cancel | EXPIRED / DENIED / CANCELLED | No effects; retain all prior attestations. |
| AUTHORIZED | authorized revocation/expiry event | AUTHORIZED | Grant validity changes separately; historical decision is not rewritten. |
| Any terminal decision | new vote/ACK/ordinary correction | Same | INVALID_TRANSITION; an explicit new intent/contract is needed. |

No action transitions REVIEWING directly to execution. `quorum_reached` is recorded as the condition satisfied, never exposed as permission to run. An R:0 contract can pass REVIEWING -> AUTHORIZED in the opening transaction when its non-peer obligations are satisfied; no fake votes are created.

### S02.2 Quorum partitions

Let E be the frozen eligible electorate, V the required voter set, P proposer, D failure-domain mapping, A current valid agreeing identities. The predicate includes `|A| >= q`, all members of V when required, and at least one agreeing non-P from a distinct D. Thresholds are those of D03's active preset. Require q <= achievable electorate; an unsatisfiable required set causes explicit waiting/formation failure, not a reduced denominator.

| Case | Condition/event | Expected outcome |
|---|---|---|
| VOT-01 | N=0, N=1, N=2, N=3, N>3 under each preset | R:0 has no peer vote requirement; governed presets cannot self-authorize below minimum; apply configured conservative N>3 rule. |
| VOT-02 | Agree count q-1, q, q+1 with other guards satisfied | Wait, progress, progress; boundary count never rounds unexpectedly. |
| VOT-03 | Enough total agreement but one required identity missing/abstains/disagrees | Wait or dissent disposition per rule; required obligation unsatisfied. |
| VOT-04 | Proposer-only, same peer other profile, or same failure domain agreement | Independent-agree requirement unsatisfied. |
| VOT-05 | All required active identities agree plus independent condition | Record quorum; select Final Call/authority wait/authorization through common gate. |
| VOT-06 | Nonrequired abstention at majority threshold | Does not count positive and does not shrink denominator; quorum uses original E. |
| VOT-07 | DISAGREE under majority / unanimity | Majority may proceed if allowed and no blocking concern; unanimity cannot. Store original dissent either way. |
| VOT-08 | BLOCK with valid qualifying concern / cosmetic text / absent category | Create concern obligation / ordinary dissent or invalid category per schema / reject; no arbitrary veto from prose. |
| VOT-09 | Duplicate identical vote with same key | Return same receipt, count once. |
| VOT-10 | Same actor submits a different vote without explicit correction | Conflict; history cannot be silently overwritten. |
| VOT-11 | Explicit invalidate/correction in REVIEWING | Supersede old vote; recompute quorum without it; fresh attestation required. |
| VOT-12 | Correction after Final Call opened | Reject ordinary vote replacement; qualifying concern or new contract path is available. |
| VOT-13 | Previously eligible voter becomes unreachable mid-round | Keep denominator and prior legitimate vote; wait for missing proof until deadline/escalation. |
| VOT-14 | Unreachable before formation, non-high-risk reachable exception authorized and >= configured minimum | Freeze reduced reachable electorate plus explicit absences under DIR-006; no abstention fiction. |
| VOT-15 | Unreachable before formation, high-risk or too few reachable | Hold or authenticated named exception; never infer agreement. |
| VOT-16 | Electorate changes while open | Supersede; all votes required anew against new contract. |
| VOT-17 | Voter/display-name reused with different principal/binding | Reject wrong identity; authorized registry change cannot rewrite past voter identity. |
| VOT-18 | Identity compromise discovered after a vote | Auditable integrity invalidation; block candidate/claim and review; ordinary health change alone cannot do this. |
| VOT-19 | Two simultaneous valid votes at same target revision | Serialize or CAS-reload/reapply with original IDs; both legitimate votes eventually retained, never lost update. |
| VOT-20 | Concurrent conflicting proposals on same subject | Independent review possible; expected resource revision prevents two incompatible effects from committing. |
| VOT-21 | Network partition from authority store | No offline finalization/new effects using cached votes; reconcile authenticated idempotent submissions on reconnect. |
| VOT-22 | Last vote exactly at/after deadline | Deadline rule `now >= expiry` wins; no late authorization. Before deadline is accepted. |
| VOT-23 | Coordinator dies/relinquishes role | Successor with valid duty fence continues same contract; no transfer of extra votes or reset of deadline. |
| VOT-24 | Proposal CLI, consensus CLI, API, and task approval facade submit equivalent votes | Same gate/receipt semantics; no alternate auto-resolve implementation. |

## S03. Final Call, operator authority, and arbiter (D04)

### S03.1 Final Call partitions

| Case | Condition/event | Expected outcome |
|---|---|---|
| FIN-01 | Tier-0 / high-risk / unresolved-dissent origin, independently and in all combinations | Final Call mandatory regardless of R alias; unresolved-dissent ancestry survives an arbiter resolution. |
| FIN-02 | Ordinary low-risk with no mandatory trigger, R:8 or other alias | No automatic Final Call solely because of R; explicit additional policy can require it. |
| FIN-03 | ACK before barrier opened / wrong candidate digest / nonrequired unauthorized actor | Reject; cannot bank a pre-ACK. Authorized observers can comment without satisfying required ACKs. |
| FIN-04 | Correct ACKs count k-1, k, duplicates | Wait / authorize only with all other guards / duplicate counts once. |
| FIN-05 | ACK from ordinary dissenter after authorized exceptional resolution | Procedural ACK accepted; original substantive disagreement remains visible. |
| FIN-06 | Cosmetic/schema preference after quorum | Record nonblocking feedback; no new authority/safety veto. |
| FIN-07 | Authority/safety/integrity/irreversibility concern | Block; disposition requires authorized concern review, never the proposer silently marking it cosmetic. |
| FIN-08 | Resolve concern with unchanged contract | New candidate includes resolution; prior ACKs cleared. |
| FIN-09 | Resolve concern by changing scope/subject/electorate | SUPERSEDED; new contract and fresh votes, not only fresh ACKs. |
| FIN-10 | ACK retracted before authorization | Remove current ACK; wait; record superseding receipt. |
| FIN-11 | Last ACK races retraction | Exactly one serial history: retract first -> wait; authorize first -> post-authorization revocation path. |
| FIN-12 | Retraction after authorization, before claim / after claim / after completion | Prevent new claim if revocation wins / request cancellation+fence later work / record concern and remediation intent, no undo claim. |
| FIN-13 | Required reviewer offline mid-Final Call | Hold until deadline or explicit permissible human exception; absence is not ACK. |
| FIN-14 | Grant expires or is revoked while ACKs arrive | No authorization; report missing live authority. A replacement grant requires a new candidate and all ACKs. |
| FIN-15 | Attempt to disable required flag by task override, policy reload, or CLI finalize | Reject/bypass prevented; contract immutable. |
| FIN-16 | Last ACK committed, response lost | Same key replay returns receipt; reconcile uses same gate; no duplicate authorization/effect. |

### S03.2 Authority partitions

Operator grant states: `REQUESTED -> GRANTED | DENIED | EXPIRED | WITHDRAWN`; a granted permission subsequently projects LIVE/EXPIRED/REVOKED/EXHAUSTED. A new request is required after denial/expiry unless an already-valid matching standing grant exists.

| Case | Condition/event | Expected outcome |
|---|---|---|
| AUT-01 | Trusted designated-human proof, exact subject/scope, fresh nonce/epoch | Store bound grant; evaluate remaining peer/Final Call obligations. |
| AUT-02 | Actor string, terminal model prose, forged signature/token, wrong issuer or principal | UNAUTHORIZED; no human-grant receipt. |
| AUT-03 | Valid issuer but wrong subject, effect, generation, resource revision, or expiry | SCOPE_MISMATCH/EXPIRED; parameterize every binding field. |
| AUT-04 | Duplicate same grant request/receipt vs nonce reused for another subject | Idempotent replay vs replay-conflict rejection. |
| AUT-05 | Existing user authorization exactly covers intent | Reuse without asking again; record provenance. |
| AUT-06 | Grant attempts to waive nonwaivable integrity/authentication rule | Reject exception; human authority is not falsification of evidence. |
| AUT-07 | Grant explicitly waives a permitted missing-voter requirement | Store named exception and absence; do not record fabricated unanimous votes. Remaining Final Call obligations remain explicit. |
| AUT-08 | Human veto races finalization/claim | Respect transaction order and revocation fence; no unclaimed effect after veto commits. Claimed external race reported honestly. |
| AUT-09 | Peer can access same-user host secrets/database in deployment probe | Strong anti-impersonation acceptance FAILS. Resolve D15 O3; do not report secure authority from UID alone. |
| AUT-10 | Approval boundary cannot authenticate currently | WAITING_AUTHORITY/UNAVAILABLE; no convenience role fallback. |

### S03.3 Arbiter partitions

| Case | Condition/event | Expected outcome |
|---|---|---|
| ARB-01 | No exceptional trigger, valid arbiter opinion | Advisory record only; no vote/authority substitution. |
| ARB-02 | Unresolved dissent/tie or high-risk trigger explicitly permits canonical opinion | Record exceptional resolution with untouched original votes and exact trigger. |
| ARB-03 | Canonical opinion but missing human grant/Final Call | Remain waiting; arbiter cannot waive reserved authority. |
| ARB-04 | Budget below / at / above cap, with simultaneous requests | Atomic reservation permits only allowed requests; full budget waits/escalates. |
| ARB-05 | Arbiter unavailable, invalid binding, or premium profile bulk-excluded | Escalation remains bounded; bulk exclusion does not ban explicit permitted arbitration, nor promote a substitute silently. |
| ARB-06 | Arbiter asks for another arbiter / retry limit reached | Reject recursion or terminate/escalate per policy; no unbounded chain. |
| ARB-07 | Old coordinator named as arbiter but not in configured arbiter list | Ignore role-derived authority; profile selection follows current contract/registry. |
| ARB-08 | Returned prose claims canonical status without authenticated allowed invocation | Store untrusted recommendation only; no canonical authority. |

## S04. Health, quarantine review, restriction, and recovery (D06)

### S04.1 Orthogonal state space

This specification uses semantic labels; an implementation may map existing enum spellings without changing distinctions:

- Availability: `AVAILABLE | UNAVAILABLE | UNKNOWN`.
- Readiness for operation kind: `READY | NOT_READY | UNKNOWN`.
- Circuit: `CLOSED | OPEN | PROBE_RESERVED`.
- Administrative restrictions: a set of independently identified records, each `ACTIVE | RELEASED | EXPIRED`, scoped by subject and authority class. Empty set means no restriction, not measured operational health.

The admission result is derived for a requested kind, never a writable global health enum. Enumerate the full product of availability x readiness x circuit x applicable restriction x resource/permission guard. Ordinary execution admits only the configured positive operational states, CLOSED circuit, no active applicable restriction, and satisfied resource/permission predicates. Recovery-probe execution may use only its separately granted circuit exception. An unknown conjunct cannot be silently converted into a positive one.

| Current circuit | Event/guard | Next | Other dimensions |
|---|---|---|---|
| CLOSED | Trusted typed trip evidence satisfying configured rule | OPEN, new circuit epoch | No administrative restriction created. |
| CLOSED | Free-form error or untrusted telemetry | CLOSED | Review request only. |
| OPEN | Authorized exclusive probe after configured cooldown | PROBE_RESERVED | Existing restrictions remain; permitted probe scope explicit. |
| PROBE_RESERVED | Matching fresh successful probe under same epoch | CLOSED | Availability/readiness update only from their own actual evidence; restrictions unchanged. |
| PROBE_RESERVED | Matching failure or bounded probe timeout | OPEN | Apply configured backoff; no invented manual quarantine. |
| Any | Stale probe/evidence identity from earlier epoch | Same | Stale no-op receipt; no reopening. |

Administrative record transitions: absent -> ACTIVE only via authorized impose effect; ACTIVE -> RELEASED by applicable authorized release; ACTIVE -> EXPIRED only if its explicit expiry policy permits automatic expiry. RELEASED/EXPIRED records stay historical; reimposition gets a new restriction ID/epoch. An ordinary successful ask is not a release event.

### S04.2 Review lifecycle

`OPEN -> WAITING_EVIDENCE | WAITING_AUTHORITY | EFFECT_PENDING | RESOLVED_NO_ACTION | RESOLVED_REJECTED | EXPIRED`.

WAITING_EVIDENCE can return OPEN after new evidence; WAITING_AUTHORITY can enter EFFECT_PENDING only after the linked typed intent authorizes. EFFECT_PENDING becomes RESOLVED_APPLIED only with a matching applied receipt; it remains pending/uncertain on delivery ambiguity, or resolves rejected/cancelled with a reason if no effect occurred. Escalation changes the authority request, never directly performs recovery. Terminal review records do not reopen silently; new information creates a linked review.

| Case | Input/event partition | Required outcome |
|---|---|---|
| HLT-01 | Fresh trusted exit/provider failure matches trip rule | Open appropriate circuit with source/epoch; no administrative quarantine. |
| HLT-02 | Prose report repeated below/at/above recurrence threshold | Dedup/review/halt-safe-retry according to rule; no fabricated measured evidence or manual restriction. |
| HLT-03 | Structured-looking JSON in model output claims trusted source | Untrusted input unless authenticated adapter observation; cannot trip trusted-evidence path. |
| HLT-04 | Trusted measurement absent/stale/error/conflicting | Retain state/reason; block evidence-required transitions or collect authorized evidence. |
| HLT-05 | Operator imposes quarantine with concern but no transport measurement | Apply administrative restriction if grant valid; label operator basis, no fake transport claim. |
| HLT-06 | Peers unanimously recommend quarantine but no operator grant | WAITING_AUTHORITY; recommendation alone cannot impose. |
| HLT-07 | Review PROPOSE_QUARANTINE + valid grant | Actual quarantine effect is queued/applied; review receipt links target restriction and effect receipt. |
| HLT-08 | Review ESCALATE_AUTHORITY | Missing grant request only; assert zero administrative-recovery calls/effects. |
| HLT-09 | Review PROPOSE_RELEASE or PROPOSE_PROBE | Distinct typed intent with appropriate proof; cannot dispatch quarantine handler by default. |
| HLT-10 | Effect delivery duplicated / same key different target | Apply once / idempotency conflict; review never marks applied twice. |
| HLT-11 | Effect denied/cancelled/expired before application | Review records rejection/cancellation/expiry; restriction unchanged. |
| HLT-12 | Crash after effect commit before review update | Reconcile from receipt, mark applied once; do not impose another restriction. |
| HLT-13 | Profile-specific vs shared provider/account scope | Restrict only declared bound subjects; no string-based scope expansion. |
| HLT-14 | Probe success for operational restriction while manual/security restriction exists | Close eligible circuit only; normal admission stays blocked. |
| HLT-15 | Manual release while circuit open/not ready | Remove named administrative restriction only; no ordinary admission. |
| HLT-16 | Wrong authority class releases security/manual restriction | UNAUTHORIZED; each class independently validated. |
| HLT-17 | Old probe succeeds after newer failure/restriction epoch | Stale no-op; no reopening or release. |
| HLT-18 | Concurrent recovery probes | At most one valid reservation per configured recovery scope; loser blocked/reuses receipt. |
| HLT-19 | Probe deadline/cooldown boundary before/equal/after | Configured inclusive rule; `now >= probe expiry` disallows late success transition. |
| HLT-20 | Restriction arrives after route selection but before claim | Live epoch check blocks claim/replans permitted target. |
| HLT-21 | Restriction arrives during running attempt | Apply declared stop/drain policy; fence future work; report actual outcome without assuming cancellation succeeded. |
| HLT-22 | Quarantined peer was required voter | Electorate unchanged; no automatic denominator shrink or historical vote erasure. |
| HLT-23 | Direct health-update requests GREEN/OPEN without required recovery proof | Reject bypass; observation submission can be accepted only with valid typed provenance. |
| HLT-24 | Concurrent impose/release at same restriction revision | One serialized winner; stale release cannot clear a newer restriction. |
| HLT-25 | Restriction expiry configured vs manual/security no-expiry | Expire only first class with explicit policy; no common TTL clears everything. |
| HLT-26 | Unknown target, disabled registry member, changed shared-scope membership | Reject wrong target or reevaluate new binding; no restriction transfer by reused display name. |

## S05. Routing and automatic profile selection (D07)

### S05.1 Route decision states

`UNRESOLVED -> SELECTED | NO_ELIGIBLE_TARGET | NEEDS_EVIDENCE | INVALID_REQUEST`; SELECTED is an immutable plan snapshot, not an execution claim. If live binding/admission changes before claim, produce a new route decision or reject according to explicit-target rules. Do not mutate historical selection reasons.

Decision partitions, in order: malformed/unknown target; explicit eligible target; explicit ineligible target; automatic mode with unresolved hard requirement; automatic eligible nonempty set; automatic empty set. Classification, filtering, and preference ranking are separate recorded stages.

| Case | Partition/event | Expected result |
|---|---|---|
| RTE-01 | Explicit instance/profile, eligible and within contract | Select exactly it; classifier recommendation remains advisory metadata. |
| RTE-02 | Explicit unknown/disabled/blocked/incompatible profile | Explain rejection; do not substitute model/peer silently. |
| RTE-03 | Auto mode, routine/change/ambiguous/complex work shape | Use configured shape mapping, then hard filters; store all five evidence-layer inputs, including absence. |
| RTE-04 | No signals, conflicting signals, supported multilingual signals, unknown language | Configured ambiguity/tie rule; no claimed measured complexity or invented confidence. |
| RTE-05 | Prompt tries to force route by spoofing metadata delimiter/authority | Only authenticated structured field counts as explicit selection; text marker cannot change authority. |
| RTE-06 | Declared preferred profile has no achieved-quality evidence | Preference may be used under recommended O2; quality remains ABSENT, visibly declared, unverified. |
| RTE-07 | Required actual model/permission/capability evidence missing or stale | NEEDS_EVIDENCE/no eligible route; never substitute declaration as proof. |
| RTE-08 | Binding verified but task-quality not measured | Distinguish invocation identity from quality; cannot advertise measured superiority. |
| RTE-09 | Some candidates fail health/quota/permission/context/budget/scope, others pass | Exclude with typed reasons before ranking; cross each hard-filter dimension independently. |
| RTE-10 | Cost/headroom absent vs known zero | Absent follows configured unknown policy; measured zero may close gate; absence is not cheapest/unlimited. |
| RTE-11 | Same declared preference, continuity equal/unequal, remaining tie | Apply configured lexicographic order and deterministic tie-break; no unordered map iteration. |
| RTE-12 | Equal candidates and randomized fairness explicitly configured | Seed/selection provenance retained for replay; randomness cannot bypass hard filters. |
| RTE-13 | Fallback eligible within same peer and approved envelope | New selection receipt; preserve original selection/failure reason. |
| RTE-14 | Fallback graph cyclic, forbidden downward capability loss, or unauthorized cross-peer hop | Reject graph/route; no infinite loop or silent quality-floor relaxation. |
| RTE-15 | Premium profile excluded from bulk, same peer has ordinary profile | Exclude only configured profile; peer remains eligible via ordinary binding. |
| RTE-16 | Selected profile becomes unavailable or binding changes before claim | Recheck; explicit pin blocks; auto may replan within envelope with new receipt. |
| RTE-17 | Two asks compete for final quota/budget reservation | One admissible reservation; loser replans/waits; cached headroom cannot oversubscribe known reservation budget. |
| RTE-18 | Actual adapter/provider receipt resolves another model/permission class | Record drift, fence further incompatible work, reconcile current effects; no clean verified-success claim for requested binding. |
| RTE-19 | Leader suggestion vs ask with same candidate facts | Shared selection reasons; role/term constraints applied only where relevant, no separate model-quality fabrication. |
| RTE-20 | Policy/registry change after decision used by a contract | New attempt plan within approved envelope or new contract if scope changes; old reasons remain immutable. |
| RTE-21 | Runtime feedback is process success with unmeasured task correctness | Update operational reliability only; no automatic achieved-quality score. |
| RTE-22 | No candidates and all possible fallback exhausted | Explicit NO_ELIGIBLE_TARGET with per-candidate blockers; no model ping loop. |

## S06. Conversation bindings and rotation (D08)

### S06.1 Binding and rotation states

Binding lifecycle: `ABSENT -> READY -> LEASED -> READY`, with `READY -> ROTATING -> READY(new binding)` and old binding `RETIRED`. A failed rotation may restore old READY only if still compatible/admissible; otherwise it yields BLOCKED preparation. RETIRED bindings never resume. Provider-start uncertainty is a recovery obligation, not READY.

Rotation phases: `RESERVED -> CHECKPOINTED -> NEW_BINDING_CREATED -> SWITCHED -> OLD_RETIRED`; each durable boundary has an idempotent receipt. The old binding remains addressable until SWITCHED commits. A crash after external creation before its identity is recorded requires reconciliation; it cannot produce an invented binding ID.

Pressure partitions are mutually exclusive: measured `<soft`, measured `soft<=p<hard`, measured `p>=hard`, and unknown (absent/stale/error/unavailable/conflicting/invalid-unit). Cross all four modes with these partitions and binding `{absent, compatible, incompatible, busy, retired}`; use D08's table as oracle and the overrides below.

| Case | Condition/event | Expected result |
|---|---|---|
| SES-01 | auto, no compatible binding | Fresh managed binding; preserve room/task/contract identity. |
| SES-02 | auto, compatible p below/at soft/below hard/at hard/above hard | Reuse / rotate / rotate / rotate / rotate according to the exact boundary convention. |
| SES-03 | reuse, compatible low/soft-band pressure | Reuse; soft-band reason visible. |
| SES-04 | reuse, hard pressure | Block reuse; no silent fresh substitution unless caller submits changed policy. |
| SES-05 | fresh with any prior binding | New managed conversation; old attempts fenced/serialized as required, never resumed. |
| SES-06 | none | No managed resume/binding; still enforce general authority and transport permissions. |
| SES-07 | Pressure unknown under auto/reuse | Configured explicit unknown action; recommended fresh for auto and hold for reuse; no fabricated percentage. |
| SES-08 | Missing reuse capability | Forced reuse rejects; auto uses supported fresh; capabilities are adapter contracts with required evidence. |
| SES-09 | Fingerprint drift, explicit reuse vs auto/fresh | Never resume; explicit reuse follows named reuse_on_drift policy with semantic receipt, default block; others fresh safely. |
| SES-10 | Path relocation only, normative compatibility unchanged | Stable fingerprint; no unnecessary rotation from drive-letter change. |
| SES-11 | Changed model/permissions/adapter compatibility/required directive revision | Mismatch handled before resume; enumerate each fingerprint field. |
| SES-12 | Two concurrent asks on non-parallel-safe conversation | Serialize on binding lease; no concurrent history mutation. |
| SES-13 | Two rotations racing | One SWITCHED binding pointer; stale epoch cannot overwrite it. |
| SES-14 | Topic generation changes during preparation/running | New claims require new generation; old result remains tied to old generation; no implicit context merge. |
| SES-15 | Required continuity capsule missing/corrupt/over limit | Hold and explain; no silent omission of required directives/lessons/checkpoint. |
| SES-16 | Checkpoint/new binding creation fails before switch | Preserve old binding if safe, release/settle reservation; failed fresh binding not active. |
| SES-17 | Crash at each rotation boundary | Restart reconciles exactly one active pointer and safe old binding state; no duplicate task execution. |
| SES-18 | Resume definitively rejected before execution | Retire and bounded safe fresh retry per config; unique attempt ID/payload. |
| SES-19 | Resume times out after possible execution | MAY_HAVE_STARTED recovery; no automatic task replay. |
| SES-20 | Late old attempt returns after new binding active | Record its result under old binding; cannot reset current active pointer. |
| SES-21 | Provider hard-context error without pressure telemetry | Typed admission/rotation reason; no numeric pressure inferred. |
| SES-22 | Rotation requested during independent cross-review | Preserve required governance context, exclude unrequested other-model answers by declared context policy. |
| SES-23 | Provider session ID reused across instance/account or invalid fingerprint | Scope mismatch; no cross-identity resume. |
| SES-24 | Auto pressure near boundary across repeated calls | Boundary behavior plus configured rotation cooldown; no change to hard safety block and no unbounded churn. |

## S07. Payload ingress, IPC, retention, and cleanup (D10)

### S07.1 Two lifecycles, two owners

Input snapshot: `ABSENT -> SNAPSHOTTED -> AVAILABLE -> RELEASED | EXPIRED`; a recovered request with no available canonical bytes is `INPUT_REQUIRED`. Borrowed source ownership stays with the caller. Explicit managed consumed source ownership transfers only by the input contract.

Attempt transport copy: `UNSTAGED -> STAGED -> IN_USE -> CLEANUP_PENDING -> CLEANED`. Any failure after STAGED also goes through CLEANUP_PENDING. A copy can be cleaned early only after a trustworthy consumption receipt says no later read is needed. Unknown process lifetime remains IN_USE/cleanup-blocked with deadline and recovery, not an unconditional delete. CLEANED paths are never reused.

The request outcome and copy cleanup status are independent. A SUCCEEDED attempt may have cleanup debt. A cleaned copy does not prove an attempt succeeded.

| Case | Input/event partition | Required result |
|---|---|---|
| IPC-01 | Exactly one inline/stdin/file source | Same canonical bytes/encoding/digest contract; shared downstream path. |
| IPC-02 | Zero sources or >1 source | INVALID_INPUT; no priority guessing. |
| IPC-03 | Empty content under allow_empty true/false | Accept exact empty payload / reject; no accidental missing-source conflation. |
| IPC-04 | Valid configured encoding vs invalid/unsupported encoding | Preserve decoded content and raw-byte digest / reject without replacement characters. |
| IPC-05 | CRLF/LF, BOM, Unicode, spaces, ampersands, quotes, leading dash, shell metacharacters | Data preserved through adapter framing; zero shell evaluation or accidental flags. |
| IPC-06 | Size max-1/max/max+1; memory threshold either side | Enforce max; select supported memory/staged transport without hidden durable retention. |
| IPC-07 | Missing/unreadable/nonregular input; outside allowed root; forbidden reparse target | Explicit file/input error; no partial dispatch. |
| IPC-08 | Source replaced/modified while reading | Stable-handle snapshot verified or reject; never hash one file then dispatch another. |
| IPC-09 | Borrowed source after read/success/failure/cancel | Never delete source. |
| IPC-10 | Managed consume source with valid ownership and stable file identity | Secure snapshot first, then remove only exact owned file; record lifecycle. |
| IPC-11 | Timestamp-looking filename without managed ownership | No deletion authority inferred from name. |
| IPC-12 | Unique creation collision/preexisting zombie path | Exclusive create rejects collision and generates a new unique candidate within bounded policy; no overwrite/reuse. |
| IPC-13 | Retry or broadcast child | Unique attempt-owned path and manifest each time; same content digest does not mean same file lifetime. |
| IPC-14 | Adapter supports stdin vs requires pathname | Use declared transport; never invent `--query-file` support for provider CLI from peerhub's own option. |
| IPC-15 | Late provider read, no consume receipt yet | Keep staged bytes until consumed or process confirmed ended. |
| IPC-16 | Trustworthy consume receipt and no later reads declared | Cleanup can begin before process exit; validate adapter contract in boundary test. |
| IPC-17 | Exception/cancel before spawn, after spawn, during read, after completion | Cleanup owner follows actual lifetime; unknown running consumer triggers recovery, not unsafe deletion. |
| IPC-18 | Windows delete denied by open handle/ACL/antivirus | Cleanup debt with bounded retry and alert; do not re-run successful request. |
| IPC-19 | Crash before manifest commit / after commit / during cleanup | Recover owned orphan via private-root identity policy or manifest; never sweep unrelated files; deletion idempotent. |
| IPC-20 | Sweep encounters live consumer, stale PID with new birth identity, or unknown consumer | Preserve/live; reconcile identity mismatch; hold unknown until safe proof/deadline policy. No PID-only kill/delete. |
| IPC-21 | Symlink/junction/reparse swap at cleanup | Verify handle/file identity and managed-root containment; refuse outside-root delete. |
| IPC-22 | Ephemeral request process crashes and bytes no longer available | INPUT_REQUIRED; digest is not replay data. |
| IPC-23 | Explicit retained input within TTL / expired / protected store inaccessible | Resume exact bytes / INPUT_REQUIRED and cleanup / BLOCKED; no plaintext fallback into durable logs. |
| IPC-24 | Borrowed replay source same path, changed digest | Reject replay as changed input; new request/approval required where material. |
| IPC-25 | Durable audit, SQL/WAL, log, exception, metric export inspection in ephemeral mode | No raw prompt content or staging bytes; metadata redaction rules hold. |
| IPC-26 | Broadcast one child finishes while another still reads | Clean only finished child copy; parent/source lifetime follows explicit ownership/reference policy. |
| IPC-27 | Staging quota/debt/age limit reached | Block new staging with reason; status/cleanup remain usable; no fallback into durable state. |
| IPC-28 | File already cleaned, duplicate cleanup request | Idempotent CLEANED receipt; never remove a different new file occupying old path. |
| IPC-29 | Retention request changed after input creation | Govern broader retention/disclosure if necessary; cannot retroactively promise erasure or recover deleted bytes. |
| IPC-30 | Asynchronous long wait exceeds ephemeral input lifetime | Explicit INPUT_REQUIRED/expiry; no silent prompt persistence to make queue appear durable. |

## S08. Telemetry, quota normalization, and diagnostic views (D09)

### S08.1 Observation and snapshot contract

Raw observation records are immutable. Normalization yields typed metric/subject/unit/scope/source values. Freshness is evaluated at snapshot time; it does not rewrite history. Snapshot selectors are pure and record policy revision, as-of time, coverage bounds, and ingestion watermark. Corrections create new records/snapshots.

State partitions: source payload `{valid, absent, unavailable, malformed/error}`; age `{fresh, exactly TTL, stale, future/skewed}`; semantic compatibility `{same scope/units, convertible declared units, incompatible, ambiguous}`; overlap `{none, duplicate, complementary, conflict}`. Every combination has a result: measured normalized value only with valid compatible fresh evidence; otherwise a typed non-measured/blocked selection with retained provenance.

| Case | Condition/event | Expected result |
|---|---|---|
| TEL-01 | Only rateLimits populated | Normalize all recognized windows with origin; missing explicit IDs get a source-scoped anonymous identity, never silently equal a named limit. |
| TEL-02 | Only rateLimitsByLimitId populated | Normalize every entry, retain limit ID and model-family applicability; no upstream loss. |
| TEL-03 | Both populated, disjoint / identical / complementary / conflicting | Union / deduplicate / preserve both fields by provenance / retain conflict and use configured per-use resolution. |
| TEL-04 | Multiple model families plus unknown family | Filter only after normalization; retain unknown applicability separately. Wrong-family bucket never authorizes route. |
| TEL-05 | Malformed one bucket, other valid buckets | Preserve valid independent observations; error localized to malformed bucket. |
| TEL-06 | Empty/absent file, absent status, one collector unavailable | Other independent sources still execute/normalize; no early-return suppression. |
| TEL-07 | Source tag invalid/untrusted or model claims actual quota in prose | Not measured evidence; do not normalize into authoritative limit. |
| TEL-08 | Age below/equal/above TTL, future observation, clock jump | Document exact freshness rule (fresh only age < TTL); equal/above stale; skew follows configured policy, no negative-age freshness hack. |
| TEL-09 | Known zero balance vs missing balance | Zero closes relevant resource gate; missing follows unknown policy and displays absence. |
| TEL-10 | Shared account pool used by multiple profiles/windows | One underlying pool reservation; no summing alternate windows or multiplying a shared balance. |
| TEL-11 | Reset in past/present/future, invalid timestamp/timezone | Preserve typed time and configured reset handling; invalid cannot reopen quota. |
| TEL-12 | Input includes cached subset, exclusive cached tokens, or unknown convention | Apply source-specific declared semantics; never double count inclusive subsets or assume unknown convention. |
| TEL-13 | Delta vs cumulative response/session/thread scopes; reset/decrease | Sum deduplicated deltas only; scoped cumulative differences only with valid lineage; decreases/reset need explicit epoch. |
| TEL-14 | Duplicate provider response ID, conflicting duplicate, late receipt | Count once / discrepancy record / new snapshot at newer watermark. |
| TEL-15 | Failure-window boundary at start/end | Include completed times in `(as_of-W, as_of]`; exact start excluded, exact end included. |
| TEL-16 | Started SUCCEEDED/FAILED/CANCELLED/unknown vs pre-admission rejection | Rate denominator only definitive started successes+failures; all excluded categories shown separately. |
| TEL-17 | No definitive completions / all fail / all succeed / mixed | UNAVAILABLE with 0/0 / 100% / 0% / exact failed/(succeeded+failed), integer counts always visible. |
| TEL-18 | One request retries several times | Attempt reliability counts each definitive attempt; request final failure counts one request outcome. |
| TEL-19 | Retention shorter than W or collector coverage missing | Partial coverage with bounds; no complete-24h claim. |
| TEL-20 | Task quality fails with transport success | Separate quality and transport metrics; no misleading health quarantine from quality prose. |
| TEL-21 | status text, status JSON, diag text, diag JSON use same snapshot | Same values, absence states, blockers, and policy/source provenance. |
| TEL-22 | Default diag/status vs explicit refresh | Default triggers no model calls or mutating recovery; refresh only allowed bounded collectors, reports partial failures. |
| TEL-23 | Credit/quota view vs credit consume | Read view cannot consume resource; consumption goes through gate+effect receipt. |
| TEL-24 | Raw provider identifiers/prompt/credentials in collector output | Redact before presentation/export; raw evidence access separately authorized. |
| TEL-25 | Metric formula revision or source precedence changes | New derived snapshot labeled with revision; old snapshot not silently recomputed. |
| TEL-26 | Concurrent attempt settlements during snapshot | Consistent as-of/watermark; no half-old/half-new numerator/denominator. |
| TEL-27 | Unknown profile binding/actual-model drift | Show attempted identity and actual verified evidence separately; do not attribute measurements by guessed name. |
| TEL-28 | Export labels approach cardinality cap | Drop/aggregate only export dimension per policy with notice; authoritative evidence retained under retention rules. |

## S09. Request, attempt, effect, and recovery (D02/D05)

### S09.1 Execution state machines

Request: `RECEIVED -> WAITING | READY | FAILED`; `WAITING -> READY | FAILED | CANCELLED`; `READY -> RUNNING | WAITING | CANCELLED`; `RUNNING -> SUCCEEDED | FAILED | CANCELLED | NEEDS_RECONCILIATION | READY` (READY only for a proven-safe bounded retry); `NEEDS_RECONCILIATION -> SUCCEEDED | FAILED | CANCELLED | READY` only with a receipt/proof justifying the transition. Terminal request outcomes never reopen silently; a deliberate new intent may reference them.

Attempt: `PREPARED -> CLAIMED -> RUNNING -> SUCCEEDED | FAILED | CANCELLED | UNKNOWN`; PREPARED can fail/cancel without start; CLAIMED can become UNKNOWN if the handoff to the adapter is ambiguous. UNKNOWN resolves only through matching outcome/non-execution proof. A retry creates a new attempt; it does not reset a failed attempt.

Effect: `PENDING -> CLAIMED -> APPLIED | NOT_APPLIED | UNKNOWN`; a local database effect may transition PENDING -> APPLIED in one transaction. External delivery uncertainty stays UNKNOWN. Effects have independently typed reconciliation; there is no universal retry-all callback.

| Certainty | Meaning | Retry permission |
|---|---|---|
| NOT_STARTED | Authoritative pre-start rejection or equivalent proof | May retry within policy, input, scope, and budget. |
| MAY_HAVE_STARTED | Dispatch intent exists but start/result cannot be excluded | No automatic non-idempotent replay; reconcile or seek explicit duplicate-risk authority. |
| STARTED | Process/remote identity proves start; completion unconfirmed | Same as above; cancellation needs an observed outcome. |
| COMPLETED | Definitive typed outcome receipt | Retry failure only if safe by operation-specific semantics; success is not replayed. |

Even an explicit duplicate-risk human authorization cannot make the prior effect absent. A later intentional repeat is a new effect with the known duplication risk linked in its contract.

### S09.2 Crash boundaries

Every mutation family must exercise these shared injection points; command-specific rows add special cases rather than duplicating a crash protocol.

| Boundary | Expected durable state after restart |
|---|---|
| C0 before database transaction | No state/receipt/effect intent. |
| C1 after state write but before transaction commit | Roll back state+receipt+outbox together. |
| C2 after commit before response/notification | Existing receipt found by idempotency identity; outbox pending; no duplicate state mutation. |
| C3 after authorization before resource preparation | No execution; recheck grant and live admission on resume. |
| C4 after file/reservation preparation before claim | No model effect; reconcile owned preparation and cleanup/reservation state. |
| C5 after claim before verified provider/process handoff | Potential start must be resolved from adapter evidence; do not infer absence from missing response. |
| C6 after provider start/effect before local result commit | UNKNOWN/MAY_HAVE_STARTED or STARTED; reconcile by actual process/request identity. |
| C7 after result commit before resource settlement/cleanup completion | Return same result; settle resources/cleanup once; never repeat the effect to repair bookkeeping. |
| C8 after notification dispatch before notification ACK | Duplicate notification may occur; consumer deduplicates by event/receipt ID; it cannot authorize a second effect. |

### S09.3 Cases

| Case | Condition/event | Expected outcome |
|---|---|---|
| EXE-01 | All preparation, gate, route, session, payload and claim predicates pass | One valid attempt launched; durable result and cleanup receipt; all IDs trace to one contract. |
| EXE-02 | Gate REVIEWING/FINAL_CALL/DENIED/EXPIRED despite quorum claim in caller | No adapter execution. |
| EXE-03 | Same key same content retry after timeout | Return existing identity/receipt; no duplicate attempt unless request-level retry explicitly creates new one. |
| EXE-04 | Same key changed prompt/scope/recipient/options | IDEMPOTENCY_CONFLICT; no effect. |
| EXE-05 | Concurrent claim of one effect/attempt | One current claim/fence; stale worker cannot write result or start another process under that claim. |
| EXE-06 | Grant revoked, target revision changed, admission closed, or registry drift before claim | Block/replan/new contract as required; cover each predicate independently and combinations by ordered guards. |
| EXE-07 | Valid claim but revocation before final adapter check | Cancel unstarted operation if observed; no invocation. |
| EXE-08 | Revocation after final check races external start | Honest possible execution; request cancellation, fence next effects; never promise external atomic revocation without adapter support. |
| EXE-09 | Child tries resource outside parent scope, even if terminal supplied it | Reject containment; do not treat terminal routing as authority. |
| EXE-10 | Child has inherited plan grant but material risk changes | Stop new effects, create revision/review obligation; inherited grant cannot cover new risk. |
| EXE-11 | Failure NOT_STARTED vs MAY_HAVE_STARTED vs STARTED vs COMPLETED | Full certainty x idempotency x retryable-class table follows S09.1; no automatic unsafe replay. |
| EXE-12 | Adapter/probe proves same provider request completed after local timeout | Settle original attempt once; no new billed/request count. |
| EXE-13 | No authoritative reconciliation possible | NEEDS_RECONCILIATION with bounded escalation and actionable context; never silently FAILED-safe-to-retry. |
| EXE-14 | Cancel before claim / during execution / after completion | Zero effect / cancellation requested with actual outcome / retain completed receipt and record late request. |
| EXE-15 | Heartbeat/output active vs idle timeout; hard user deadline separate | Active work not killed as idle; explicit deadline applies per config, terminal outcome reflects certainty. |
| EXE-16 | Lease expires while old process still alive | Fence old commit/new work; inspect process identity, no automatic duplicate task execution. |
| EXE-17 | Old worker submits completion after takeover | Retain observation if authentic but reject stale authoritative update; reconciler determines task outcome. |
| EXE-18 | Process PID recycled, missing birth identity, wrong workspace/job | Do not kill unrelated process or infer death; explicit uncertainty. |
| EXE-19 | Disk full, database busy, transaction failure at C0-C2 | No torn mutation or false success; safe bounded transaction retry under same key. |
| EXE-20 | Corrupt database, unsupported schema, network filesystem, failed lock probe | Fail startup/mutation safely; status reports unavailable using allowed evidence, never creates a second authority store. |
| EXE-21 | Clock backward/forward jump or restart during lease/grant wait | Use injected time model; unsafe discontinuity fences/holds per policy, never extends authority silently. |
| EXE-22 | Reservation acquired, then gate/replan fails | Release only proven unused reservation; uncertain usage remains explicitly held. |
| EXE-23 | Actual billed usage differs from reservation | Reconcile from authoritative provider receipt; expose discrepancy; no fabricated zero/cost estimate. |
| EXE-24 | Broadcast zero/one/many recipients; duplicates | Validate empty-recipient policy; unique child per frozen logical recipient, duplicates not extra work. |
| EXE-25 | Broadcast success/failure/cancel/unknown mixture | Parent exposes per-child outcomes and partial/unresolved aggregate; no false all-success. |
| EXE-26 | Retrying partial broadcast | Retry only proven-safe unsuccessful children within grant; no repeated successful recipients. |
| EXE-27 | One child revocation/room-generation change while siblings complete | Preserve completed receipts; unclaimed affected children block; no pretend all-or-nothing external rollback. |
| EXE-28 | Outbox duplicate/lost-ACK/out-of-order delivery | Consumers deduplicate by receipt identity and check target revision; effects obey S09 certainty rules. |
| EXE-29 | Local DB-only administrative effect | Target change+receipt+outbox commit in one transaction; no fictitious external uncertainty needed. |
| EXE-30 | File/external effect applied but materialization receipt lost | Reconcile artifact hash/file identity/provider ID; do not overwrite unknown external changes blindly. |
| EXE-31 | Peer-specific flags/PTY requirements | Adapter contract exercised; general pipeline unchanged, actual permissions/model support need source-tagged qualification. |
| EXE-32 | Proposed command metadata falsely declares no effects | Registered trusted operation schema, not caller declaration, controls enforcement; reject unauthorized kind/metadata. |

## S10. Supporting capabilities must also survive replacement (D13)

These mechanisms reuse shared guards, transactions, evidence, and effect recovery; they do not share identical business states. Their legacy syntax is not mandatory, but the useful operation and its negative paths are. This section closes the broader capability scope beyond G1-G7.

### S10.1 Rooms, messages, membership, threads, and duty

Room: `OPEN -> ARCHIVED | CLOSED`; a topic change increments generation without changing room identity. Reopen/archive restoration requires a named authorized operation. Membership: `ACTIVE -> ENDED | EXPIRED | ABANDONED`; rejoin creates a new interval with an explicit resume reference. Thread: `OPEN -> RESOLVED | ARCHIVED`; message/reaction append is immutable with unique IDs and ordered room sequence. Required redaction is an authorized auditable operation, not silent history rewrite.

Duty lease: `PENDING -> ACTIVE -> RELEASED | EXPIRED | REVOKED`; the room/role scope and authority epoch fence stale holders. Leadership challenge/election is a separate pending record; a claim does not itself confer policy authority. Term counters advance on actual term acquisition, not heartbeat renewal. Locks use scoped lease semantics; owner display labels do not authenticate actors.

| Case | Event/partition | Expected outcome |
|---|---|---|
| PAR-01 | Cold open, valid resume, stale rejoin, unknown room, archived/closed room | Correct membership interval and context-fill requirement; invalid scope rejected. |
| PAR-02 | Message/send/broadcast duplicate or concurrent append | One message per identity, ordered room sequence, shared visibility; no peer-model call for plain messaging. |
| PAR-03 | mark-read current/old/future cursor or wrong actor | Monotonic valid cursor; out-of-range/unauthorized rejects; delivery is not decision approval. |
| PAR-04 | Topic change/clear while tasks/rounds/attempts active | New generation and explicit old-work disposition; do not delete unfinished obligations silently. |
| PAR-05 | Thread reaction duplicate, append after archive, promotion | Idempotent reaction / invalid transition / reference to new governed intent, never discussion-as-approval. |
| PAR-06 | Handoff checkpoint/export then crash or oversized projection | Durable checkpoint remains authoritative; projection rebuild/trimming obeys section/retention policy. |
| PAR-07 | Context hash or capsule stale/missing/for another generation | Rebuild from authorized source or block required context; hash is not content. |
| PAR-08 | Concurrent terminal claim/handoff/heartbeat/close | Single active duty fence; successor sees durable handoff; stale terminal rejected. |
| PAR-09 | Terminal/router attempts worker-only governance mutation | Reject origin; identified authorized worker path remains available. No fixed peer-name exception. |
| PAR-10 | Duty expires during uncompleted task | Preserve task and attempt uncertainty; takeover does not replay the task automatically. |
| PAR-11 | Multiple leadership challengers before/at/after window | Configured score/tie-break at boundary, one winner; no last-writer-wins overwrite. |
| PAR-12 | Coordinator consecutive-term cap reached or no eligible successor | Block disallowed acquisition; explicit wait/exception path; do not silently reset history. |
| PAR-13 | Role assignment/release with lease conflict or unavailable peer | Scoped eligibility and fence rules; role never adds vote weight or grants administrative powers. |
| PAR-14 | Lock owner opaque nonempty label vs empty, release wrong principal/fence | Preserve allowed opaque labels; authentication independent; reject empty or unauthorized release. |
| PAR-15 | File lock expires while process alive or path aliases another locked file | Canonical resource conflict/fence; no unsafe duplicate write based only on TTL/path spelling. |
| PAR-16 | List/query during concurrent create/update/archive | Authorized consistent snapshot/cursor ordering; no skipped/duplicated records within promised pagination snapshot. |

### S10.2 Tasks, approvals, and checkpoints

Task: `CREATED -> READY -> RUNNING -> CHECKPOINTED | AWAITING_APPROVAL | FAILOVER_PENDING | SUCCEEDED | FAILED | CANCELLED`. CHECKPOINTED -> READY only from a durable declared stage boundary; FAILOVER_PENDING -> READY only after new owner epoch plus execution certainty reconciliation; AWAITING_APPROVAL -> READY only with valid exact-stage grant, otherwise explicit expiry/denial/cancellation outcome. Multiple child requests can run when declared dependency edges allow it; task and request identities remain distinct.

| Case | Event/partition | Expected outcome |
|---|---|---|
| PAR-17 | Duplicate checkpoint, stale epoch, corrupt artifact, opaque resume token | Idempotent same checkpoint; stale/corrupt rejects; opaque token preserved without inventing its semantics. |
| PAR-18 | Reassign from safe checkpoint vs unknown external stage effect | New owner may resume safe boundary; uncertain effect blocks replay pending reconciliation. |
| PAR-19 | Human approval for prior stage/revision after task spec changed | Scope mismatch; no continuation from stale grant. |
| PAR-20 | Approval expires/denied/withdrawn vs granted | Explicit terminal wait disposition vs READY; no fire-and-forget bypass. |
| PAR-21 | Parallel children with satisfied/unsatisfied/cyclic dependencies | Dispatch ready children only; reject cyclic plan; preserve partial outcomes. |
| PAR-22 | Task cancellation races child completion or failover | Single authoritative task revision; actual child receipts retained; stale owner cannot revive task. |

### S10.3 Directives, lessons, feedback, alerts, and artifacts

Runtime directive: `PROPOSED -> ACTIVE -> RESOLVED | EXPIRED | SUPERSEDED`; governed activation, TTL and scope apply. User standing directives remain a human-authority category, never a destination for automatic lesson promotion. Lesson: `PROPOSED -> APPROVED -> ACTIVE -> RETIRED | SUPERSEDED`; invalidation/quarantine is a separate restriction with explicit authority. Delivery has its own per-recipient state and cannot mutate lesson content revision.

Feedback/alert: `OPEN -> ACKNOWLEDGED -> UNDER_REVIEW -> RESOLVED | ESCALATED | EXPIRED`; duplicate links preserve evidence; a domain may skip nonrequired intermediate presentation states using a declared transition. Escalation links an intent when effects are needed. Artifact: `UNCLAIMED -> CLAIMED -> VALIDATED -> FINALIZED | ABANDONED`; write/materialization certainty follows S09. Archive, signature update, and credit consumption are typed effects, not exemptions from authority.

| Case | Event/partition | Expected outcome |
|---|---|---|
| PAR-23 | Runtime directive activate/expire/clear; global vs room conflict | Scope/TTL/precedence enforced; lower scope cannot silently contradict protected rule. |
| PAR-24 | Automated feedback or lesson attempts user-directive mutation | Reject unauthorized authority promotion. |
| PAR-25 | Lesson approval by peers vs explicitly authorized proposal vs designated human | Only allowed authority class activates; unanimous votes alone do not impersonate human approval. |
| PAR-26 | Lesson sweep finds stale/ineffective item | Mark for review; no unauthorized retirement. |
| PAR-27 | Lesson broadcast and dispatch injection under retries, unavailable peer, new revision | Next-dispatch eligibility + per-peer delivery receipts; high-severity ACK rule independent of content lifecycle. |
| PAR-28 | Required context/lesson delivery fails | Explicit missing-context blocker according to policy; no claim that model received it. |
| PAR-29 | Feedback/alert duplicate, resolved recurrence, authority-changing resolution | Dedup or new linked record; consequential resolution uses gate; no alert auto-authority. |
| PAR-30 | Concurrent artifact claim/finalize, invalid checksum/signature, stale claim | One valid claim; validation required; reject stale or mismatched materialization. |
| PAR-31 | Archive-file path outside authorization, destination conflict, crash during replace | Guard scope and identity; reconcile file effect; no destructive blind retry. |
| PAR-32 | Update-signatures modifies approval-bound material | New artifact/contract revision where material; signature update cannot conceal changed contents. |
| PAR-33 | Credit consume same identity, wrong identity, uncertain provider outcome | Exactly one local intent, authorized scope, external reconciliation; no consume from status. |
| PAR-34 | Register/discover custom node type, duplicate identity, cyclic binding, disable profile | Preserve valid extensible labels; reject conflicting/cyclic identity; eligibility reflects canonical revision. |
| PAR-35 | Broker submit arbitrary desired-state blob or drain without operation authority | Reject; typed effect handler and current grant required. Status is read-only. |
| PAR-36 | Connector operation outside built-in peer set | Use registered capability/permission/effect schema; no peer-name special case or raw-shell injection. |

## S11. Cross-mechanism acceptance scenarios (G1-G7)

These scenarios exercise public boundary wiring. Calling an internal method directly is insufficient. Each must run through the native CLI parser/application path and the equivalent typed API; backend/adapter doubles may be used first, then qualified real boundary fixtures. No live model dispatch is performed in this design round.

| ID | Scenario | Acceptance oracle |
|---|---|---|
| INT-01 | Routine read using auto profile and a query file | Input snapshot -> policy -> eligible declared-preference route -> session decision -> unique transport -> settled result -> cleanup -> shared status. No fabricated quality, no peer vote when none required. |
| INT-02 | Architecture direction with explicit R:0 attempt to bypass standing floor | Effective R:10; freeze electorate; votes -> mandatory Final Call as applicable -> valid authority -> claim. No effect before all obligations. |
| INT-03 | Ordinary R:8 quorum, low risk, no mandatory Final Call trigger | Authorization via common gate without unnecessary Final Call; no `quorum_reached` stranded command. |
| INT-04 | High-risk proposal-vote path reaches unanimous votes | Same Final Call/human gate as direct consensus path; unanimous auto-resolve bypass fails test. |
| INT-05 | Last Final Call ACK races operator revocation and attempt claim | Result equals a valid serial history; preclaim revocation prevents start, later race is disclosed/cancelled according to adapter support. |
| INT-06 | Free-form repeated error -> review -> operator approval -> quarantine | No automatic evidence fabrication; real administrative restriction applied; review RESOLVED_APPLIED points to exact receipt. |
| INT-07 | Probe recovers circuit while manual quarantine remains | Diagnostic shows operational improvement and administrative block; routing still rejects ordinary ask. |
| INT-08 | Auto ask at soft/hard session pressure, then a resume failure | Rotation actually invoked from ask; room membership survives; safe prestart failure retries within bound; ambiguous start does not replay. |
| INT-09 | Explicit profile vs auto routing on same prompt | Explicit stays pinned if eligible; auto records classification/preferences and hard filters; no silent fallback of explicit profile. |
| INT-10 | ByLimitId-only rate limit for selected model family | Survives normalization and drives both diag and route eligibility; wrong-family limits neither erase nor substitute. |
| INT-11 | Mixed successes/failures/retries/cancels/unknowns over configured 24h window | Exact attempt and request counts/rates with coverage, consistent status/diag JSON/text; no denominator fabrication. |
| INT-12 | Oversized sensitive query-file broadcast, one child crash, one success | Independent attempt files/cleanup; no durable prompt leak; success not rerun; uncertain child explicitly reconciled. |
| INT-13 | Ephemeral queued request loses bytes during restart | INPUT_REQUIRED, never dispatch hash or stale zombie file as prompt. Retained mode resumes only within policy/TTL. |
| INT-14 | Approved plan executes several children, then adds a new resource | In-scope children reuse grant; out-of-scope addition opens new contract. No per-tool re-voting and no scope creep. |
| INT-15 | Concurrent proposals, topic change, and restriction on selected peer | Frozen decision history stays intact; live generation/admission/revision fences prevent stale effects. |
| INT-16 | Adapter actual model differs from selected binding | Drift evidence reaches status, admission/review, and request outcome; no verified-success assertion based solely on process exit. |
| INT-17 | Manual credit consume and read-only diag run concurrently | Read view never triggers consumption; reservation/effect receipt reflected in next consistent snapshot. |
| INT-18 | Terminal handoff during pending high-risk review | Successor sees exact missing obligations and deadline; predecessor fenced; no new assumed votes or private authoritative state. |
| INT-19 | Later config revision changes voters/quorum/TTL | Open contract remains frozen; new requests use new revision; explicit revocation blocks claims through its own mechanism. |
| INT-20 | All observation sources absent, every peer blocked | Status remains usable, explains absence; dispatch says NO_ELIGIBLE_TARGET; no model pings or invented healthy defaults. |

## S12. Migration, MECE closure, and review gates (D14-D15)

### S12.1 Capability and caller receipts

For each D13 row and every discovered command/flag/caller, create a later migration receipt:

`capability_id; legacy operation; caller set; native operation; preserved useful outcome; semantic delta; policy basis; success-case IDs; failure/recovery-case IDs; production wiring evidence; migration owner; cutover/rollback evidence; status`.

Status is `UNMAPPED | SPECIFIED | VERIFIED | RETIRED_WITH_AUTHORITY`. A feature cannot be VERIFIED from a design document, a unit method test, a `--help` declaration, or absence of a grep match alone. Ground-truth gaps G1-G7 remain implementation gaps until INT tests prove boundary wiring. DIR-004 runtime claims attach actual source tags from the allowed set.

| Case | Migration/qualification partition | Expected result |
|---|---|---|
| MIG-01 | Legacy function exists but no mapping | Cutover blocked; add capability or explicit user-authorized retirement, not silent unsupported state. |
| MIG-02 | Function mapped but wrapper/flag/non-hub diag caller missing | Cutover blocked; static action inventory is insufficient. |
| MIG-03 | Caller expects changed exit/output semantics | Migrate caller and characterize success/failure behavior; no permanent byte-compat assumption. |
| MIG-04 | Pending legacy round imported without verifiable electorate/authority proof | Preserve as historical/pending needing new proof; never upgrade it to native authorization. |
| MIG-05 | In-flight attempt at cutover with known completion / known no start / unknown | Import receipt / safely schedule under native authority / NEEDS_RECONCILIATION. |
| MIG-06 | ID collisions, duplicate imports, partial import crash | Immutable mapping and idempotent receipt; atomic rollback/recovery, no silently merged identities. |
| MIG-07 | Pending session/lease/task maps to wrong room/generation/profile | Reject unsafe binding; preserve source archive and explicit recovery requirement. |
| MIG-08 | Temporary shim remains live or writes state independently | Deletion/simplicity gate fails; shim must translate only and retire after callers migrate. |
| MIG-09 | Static references absent but observation window incomplete | Caller-absence claim remains incomplete; configured evidence window required. |
| MIG-10 | Rollback after a native effect executed | Restore routing/code only with effect reconciliation; never replay legacy request as if native effect never occurred. |
| MIG-11 | Final deletion attempted without concrete designated-human receipt | Reject; this Round 1 document supplies no deletion permission. |
| MIG-12 | Unsupported OS/storage/PTY/provider configuration in release matrix | Mark TEST NEEDED/unsupported truthfully; no declaration-only readiness claim. |

### S12.2 Required trace coverage

| Design concern | Primary spec cases | Boundary proof |
|---|---|---|
| G1 consultation and risk | POL, VOT | INT-02/03/14/19 |
| G2 Final Call and legacy proposal convergence | FIN, AUT, ARB | INT-04/05/18 |
| G3 quarantine action/recovery separation | HLT | INT-06/07/15 |
| G4 session rotation vs participation | SES, PAR-01..10 | INT-08/18 |
| G5 auto profile selection | RTE | INT-01/09/16/20 |
| G6 normalization and failure telemetry | TEL | INT-10/11/17/20 |
| G7 IPC input/staging cleanup | IPC | INT-01/12/13 |
| Broader functional superset | PAR, MIG | Capability/caller receipts for every D13 row |
| Core transactions, uncertainty, concurrency | INV, EXE, C0..C8 | All INT scenarios plus restart/crash matrix |

Coverage generation rules for the later TDD stage:

1. Enumerate all listed legal transitions and every illegal state/event pair via U6.
2. Enumerate guards U1-U9 at first failure and verify rejection has no unauthorized effects.
3. Use full truth tables for safety-coupled predicates identified in S00.1. Add before/equal/after tests at every configured threshold/deadline/TTL/count/size.
4. Inject C0-C8 at every applicable transition, including public commands and sweeps. An inapplicable crash point needs a written reason.
5. For each pair of operations that touches a common grant, target revision, lease, reservation, session pointer, or payload, execute both serial orders and forced interleavings. The concurrent result must match a legal serial history, or explicitly be a documented external-effect uncertainty. Particularly cover vote/correction, ACK/retract, grant/revoke, claim/quarantine, session rotate/topic change, cleanup/late read, settle/snapshot, and import/replay.
6. Use stateful generated sequences to search beyond named examples, with each invariant checked after every step. Retain minimized counterexamples as new named cases; do not claim random sampling is an exhaustive proof.
7. Verify each configurable field in D11 has valid, missing, invalid, scoped-override, and mid-operation-change coverage appropriate to its type. Fixed state names/invariants are schema, not configurable bypasses.
8. Execute the integration scenarios through every supported user entry surface. Require a production call-path receipt for each formerly unreachable primitive; a unit suite alone cannot close G1-G7.

### S12.3 Round 1 closure criteria and unresolved choices

This document is complete as an independent modeled specification when every defined mechanism has a state/guard/fallback, every G1-G7 concern has cases and an integration path, and D13 capability families have preservation cases. It is **not** a proof of implementation readiness or external-system behavior.

Before synthesis is ratified, resolve D15 O1 (meaning of superset versus rejected legacy pathology), O2 (declared preference routing before measured quality), and O3 (enforceable named-human host boundary). The associated recommended behavior is specified here so disagreement can target a concrete alternative. If synthesis chooses another option, revise the affected oracle and configuration rules together before TDD; do not leave contradictory defaults for implementers to guess.

Further explicit decision points: R:3 notification versus mandatory independent review, larger-electorate quorum rule, ACK set after arbiter override, unknown-pressure/forced-reuse drift behavior, retained-input default for asynchronous work, and downward/cross-peer fallback limits. Values of timings, thresholds, quotas, sizes, and retries come from a ratified config preset rather than hardcoded tests. Tests override them to exercise boundaries.

No implementation code, test code, schema migration, configuration change, peer dispatch, or commit is part of this Round 1 artifact.
