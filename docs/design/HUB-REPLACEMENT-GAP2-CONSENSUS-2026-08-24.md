# Gap 2 Design: Consensus and Coordinator Workflows (DRAFT — architecture ratifiable, 10 policy details need ratification)

Status: first-round draft from `cx`, 2026-08-24. Covers legacy `ask-coordinator`,
`consensus-propose`, `consensus-vote`, `consensus-check`, `consensus-sweep`.
Built within gap-1's boundary ("peerhub owns semantics; compatibility owns
translation," versioned JSON envelope). Part of the 7-category
design-reinforcement effort. **Note**: `cx` flagged it could not access
`P:\_sys\docs-v2\general\protocol.md`/`protocol.json` directly from its
sandbox this round — grounded in the audit/gap-1 context supplied in the
dispatch prompt instead; the 10 open items below should be re-checked
against the live protocol.md/protocol.json text directly before final
ratification.

## 1. Native consensus data model

A durable governance aggregate layered on peerhub's existing request/attempt
state machine (not a separate persistence subsystem) — request/attempt
provides identity/idempotency/room ownership/actor auth/event
ordering/timeout/audit; consensus adds voter-eligibility snapshot, vote
uniqueness, quorum calc, disagreement veto, COLLAB_RATE policy,
timeout/escalation, finalization proof. A round = append-only event stream +
materialized view (`ConsensusRoundProposed`, `ConsensusVoteCast`,
`ConsensusFinalCallIssued`, `ConsensusTimedOut`,
`ConsensusEscalationRequested/Resolved`, `ConsensusRoundResolved/Abandoned`).

Round envelope (illustrative): `round_id`, `room_id`,
`protocol_major`/`protocol_minor`/`schema_version` (gap-1's version fields),
`proposal{summary,payload_ref,risk_level,proposer,created_at}`,
`policy_snapshot{collab_rate,eligible_voters,required_voters,quorum,
non_proposer_required,timeout_seconds,offline_auto_abstain,
final_call_required}` (**immutable after round creation** — health changes
after creation don't silently change the denominator; a voter-set change
requires a new round), `voters{peer:{health_at_start,eligible,required,vote}}`,
`state`, `escalation`, `resolution`. Votes are immutable facts — a
correction is a superseding event, never an in-place overwrite.

## 2. Consensus state machine

States: `proposed`, `voting`, `quorum_reached`, `timeout`,
`forced_escalation`, `resolved`, `abandoned` (terminal cleanup, not a
successful resolution). **No state may transition directly from `voting` to
`resolved`** — must pass through `quorum_reached`, `timeout`, or
`forced_escalation`, so the resolution reason is always explicit.

| Current | Event | Next |
|---|---|---|
| `proposed` | eligibility snapshot created | `voting` |
| `proposed` | validation failure/duplicate/invalid voter set | `abandoned` |
| `voting` | valid vote + resolution predicate true | `quorum_reached` |
| `voting` | deadline passes | `timeout` |
| `voting` | authorized force-escalation | `forced_escalation` |
| `voting` | voter set must change | `abandoned` |
| `quorum_reached` | final-call satisfied (where applicable) | `resolved` |
| `quorum_reached` | final-call concern / new blocking condition | `voting` |
| `timeout` | policy selects human/coordinator escalation | `forced_escalation` |
| `timeout` | authorized resolution accepts timeout outcome | `resolved` |
| `forced_escalation` | human/coordinator decision recorded | `resolved` |
| `forced_escalation` | escalation rejected, round continues | `voting` |
| any non-terminal | explicit admin cancellation | `abandoned` |

Resolution predicates at R:10: every required gate-open voter in the
round-start snapshot must explicitly `agree`; explicit `disagree` blocks;
a required voter going offline without a prior `agree` blocks; abstention
doesn't satisfy agreement; proposer can't self-finalize; ≥1 distinct
non-proposer must agree; Final Call mandatory where required. At R<10: a
configured minimum quorum may allow `agree`+permitted `abstain`; explicit
`disagree` still blocks; proposer still can't be sole approver. **Quorum
function `f(N, risk)` in `minimum_quorum = max(2, f(N, risk))` is named but
undefined in available sources — must not be invented silently (open item
1 below).**

## 3. Voter-health filtering and COLLAB_RATE

Eligibility evaluated ONCE at round start: registered for
room/protocol + in the voter set + health gate `OPEN` at snapshot time +
not quarantined/suspended/revoked. `RED`/quarantined/gate-closed peers
excluded from the initial denominator (never counted as implicit
approval). After snapshot: a cast vote stays valid; a voter going
unhealthy doesn't reduce `N`; at R:10 an uncast required vote from a
now-unhealthy voter blocks finalization and escalates; changing the
eligible set requires abandon+restart. Round retains both
`health_at_snapshot` and `health_events_after_snapshot`.

COLLAB_RATE policy table (native requirement per rate): 0=no binding
consensus; 3=informal notification/one peer review; 5=majority ACK
(≥2 peers, exact definition open — item 2); 8=all active eligible peers,
supermajority/unanimous per config, Final Call required; 10=all required
snapshot voters explicitly agree, disagreement/missing-required
escalates. Policy snapshot captures the effective rate at proposal
time — a later config change must not mutate an active round.
`collab_rate` controls governance requirements only, never quota/model
availability/peer capability.

## 4. Native command surface + legacy mapping

`peerhub consensus propose/vote/status/sweep/escalate`, `peerhub
coordinator ask` (a coordinator-facing inquiry, NOT itself a vote — if
binding agreement is needed, it creates a consensus round, preventing
`ask-coordinator` from being confused with approval).

- `propose --room --subject --voters --risk --payload-ref`: creates round, captures policy+health snapshots.
- `vote --round --voter --value(agree|disagree|abstain) --reason`: idempotent per voter/round/vote-event; `disagree` requires a reason; a changed vote needs an explicit correction/supersession mechanism.
- `status --round` / `--room --active`: returns state, immutable policy snapshot, voter health snapshot, votes+missing, quorum calc, blocking reasons, deadline, escalation state, resolution evidence.
- `sweep [--room --older-than]`: applies timeout transition to expired rounds; must never silently resolve as approved.
- `escalate --round --reason`: requires risk-appropriate authorization, records actor+reason.

Legacy mapping (adapter parses legacy args → versioned native request envelope → invokes ONLY the native op → translates response back → preserves native round IDs/resolution evidence → explicit incompatibility error if unrepresentable): `ask-coordinator→coordinator ask`, `consensus-propose→consensus propose`, `consensus-vote→consensus vote`, `consensus-check→consensus status`, `consensus-sweep→consensus sweep`. **The adapter must never reimplement quorum/health logic — that belongs exclusively to peerhub.**

## Items requiring explicit ratification (genuinely ambiguous from available sources)

1. **Exact quorum function** `f(N, risk)` — only `max(2, f(N,risk))` with default `N` is known; must pick `N` universally, a risk-tier table, or another deterministic function.
2. **"Majority" at R:5** — 2 absolute approvals, >50% of eligible voters, or both?
3. **R:8 disagreement behavior** — "supermajority/all active peers" vs "any disagreement blocks below R:10" need one canonical statement for R:8 specifically.
4. **Timeout duration/ownership** — 30-min stale sweep + offline-auto-abstain setting mentioned, but authoritative values and whether timeout auto-escalates to a human aren't fully established here.
5. **Forced-escalation authority matrix** — human only / active coordinator / designated arbiter / combination; must not let forced escalation become a peer override of a required human decision.
6. **Final Call semantics** — separate vote, an acknowledgment event, or a prerequisite before `quorum_reached`?
7. **Coordinator identity/failover** — precise native meaning when the active coordinator is stale/unhealthy/rate-limited/absent; request/attempt layer owns routing, but the governance replacement rule needs confirmation.
8. **Vote correction policy** — may a voter change a vote before finalization? Must be explicit for auditability.
9. **Human override representation** — record as first-class resolution events; exact identity/auth/required-reason fields need ratification.
10. **Persistence/recovery guarantees** — should follow peerhub's existing request/attempt guarantees rather than being invented here; needs confirmation, not new design.

**All 10 items above are now resolved** — items 1-6 later in this same
document ("Ratification of open items 1-6"), items 7-10 via
`HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md` (the
authoritative final status for every item across all 7 gap docs). This
numbered list is kept as the original framing of the question, not a
currently-open item list.

## Ratification recommendation

Ratify NOW: consensus is a peerhub-native aggregate over existing
request/attempt infrastructure; policy+health are snapshot-based; no
silent voter-set mutation; no direct `voting→resolved`; compat adapters
translate but never own semantics. **Do NOT ratify implementation
behavior** until the 10 items above (especially quorum calculation,
timeout escalation, R:8 semantics, forced-escalation authority) are
resolved against the actual live `protocol.md`/`protocol.json` text.

## Ratification of open items 1-6 (2026-08-24, terminal, verified directly against `P:\_sys\docs-v2\general\protocol.md` §4)

`cx` couldn't access this file from its sandbox this round; the terminal read it directly. Real text resolves 6 of the 10 open items:

1. **Quorum function**: CONFIRMED as guessed — `max(2, f(N, risk))`, f undefined above N=3, defaults to N (§4.4, "Quorum Authority Principle").
2. **R:5 "majority"**: RESOLVED — literally "2+ peers" (an absolute count, not a percentage of N).
3. **R:8 disagreement**: RESOLVED — "Supermajority ACK (All active peers)" is one requirement, not two competing ones — R:8 requires ALL active peers to ACK. Any explicit `disagree` from a gate-OPEN voter blocks at any rate (§4.4).
4. **Timeout duration/ownership**: RESOLVED — 30 minutes (`timeout_minutes`), auto-escalates to Human (Tier 0) via `consensus-sweep` (§4.2, §4.7).
5. **Forced-escalation authority**: RESOLVED — Human (Tier 0) exclusively; "no peer can override" (§4.6 Tiebreak). Disagree and timeout both escalate to Human (Tier 0) specifically, never to a peer or the coordinator.
6. **Final Call semantics**: RESOLVED — a distinct post-quorum step, not itself a vote: proposer sends "Any additional feedback or missed context?", all peers reply ACK/Proceed or raise concerns, round finalizes only after all ACKs received (§4.5, INV-02). Mandatory at R:8+.

**Still open at the time this paragraph was written** (7, 8, 9's exact recording format, 10) — since resolved: #7 (coordinator identity/failover) by reusing `DutyLeaseCreateRequest`, #8 (vote correction) by the "3 more ratification items resolved" section below (allowed via a new `cast_vote` mutation, only pre-Final-Call), #9 (human-override representation) and #10 (persistence/recovery) both by `HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md` items 15/17. See that doc for the authoritative current status of every item in this section — it supersedes this paragraph.

**New rules found, not in `cx`'s draft — must be added to the design**:
- **Retroactive veto**: NONE for procedurally valid rounds — a gate-OPEN voter who didn't vote `disagree` before FINALIZE cannot retroactively block. Exception: a finalization that violates a higher-order invariant (INV-01~19) may be voided by Human (§4.4, "Quorum Authority Principle").
- **Tiebreak (2v2 or N/2 split)**: check `protocol.json["workload"]["capability_registry"]` for the disputed task's domain → highest-domain-expertise peer recommends → Human (Tier 0) makes the final decision, no peer override (§4.6).
- **PTY peer (ag) vote submission**: writes its vote directly to `.ai/consensus/{round_id}.json`, OR relays via `hub.py send --to cc` — **NEVER** `hub.py ask` (PTY deadlock risk) (§4.4). The native `peerhub consensus vote` command's PTY-peer path needs an equivalent non-`ask`-shaped submission mechanism, not just a CLI flag translation.

## RECONCILIATION AGAINST REAL SOURCE (2026-08-24)

Per `HUB-REPLACEMENT-REAL-SOURCE-GROUNDTRUTH-2026-08-24.md`, `peerhub/governance/`
has a real, GENERIC governed-mutation broker (`MutationDisposition`,
`TransitionStatus`, `OutboxState`, `EffectOutcome`, `RecoveryDisposition`
enums; `EffectIntent`, `MutationRequest`, `TargetState`, `MutationPlan`,
`CommandBinding`, `TransitionReceipt`, `OutboxEvent`, `EffectReceipt`,
`MutationSubmission`, `PendingEffect` dataclasses) — domain-agnostic,
NOT consensus-specific. `cx` reconciled the proposed event model against
it.

**Revised recommendation**: a consensus round = a `TargetState` (round ID
as `target_id`, current domain state, participants, policy ref, vote
material, deadlines, resolution metadata — subject to real field
support). Operations submitted as domain-specific `MutationRequest`
payloads (propose/open, cast-or-revise vote, final call, timeout,
escalate, resolve, abandon). Broker validates + expected-revision-checks
→ `MutationPlan` → applies transition → `TransitionReceipt` →
`OutboxEvent` for durable notification; side effects via `EffectIntent`/
`EffectReceipt`/`PendingEffect` + broker recovery. **The originally
proposed named events (`ConsensusRoundProposed`, `ConsensusVoteCast`,
etc.) are now domain event NAMES for projections/audit/notification —
NOT a second, parallel event-persistence mechanism.**

**Critical unresolved question the broker's existence does NOT answer**:
does it support MULTIPLE sequential mutations against ONE `target_id`
(accumulating votes), or is it shaped for one-request-to-one-terminal-
transition? If the latter, **a consensus-specific coordinator/reducer is
still required above the broker** — for quorum calculation, voter
eligibility, deadline enforcement, legal-transition determination — but
that coordinator should USE the broker for persistence/idempotency/
revision-control/outbox/recovery, not build a parallel system.

**Enum mapping is INFERENCE ONLY (names, not values, were available)**:
`TransitionStatus` most plausibly describes ONE mutation's lifecycle, not
the consensus round's richer business lifecycle
(`proposed→voting→quorum_reached→resolved`, side paths
`voting→timeout→forced_escalation→resolved`,
`any-nonterminal→abandoned`) — the round's domain state should live as an
aggregate field inside `TargetState`, with the 5 generic enums describing
the mutation/publication/effect/recovery mechanics AROUND it, not
replacing it.

**`proposal-vote` (gap-6) claim CONFIRMED, more precisely**: a
domain-specific mutation against a proposal `TargetState`, same broker +
consensus/quorum logic as any consensus round. Two shapes possible — a
proposal owns its own voting lifecycle in its own `TargetState`, OR a
proposal references a separate consensus-round target whose resolution
mutates the proposal target. **Which one is correct depends on real
target/reference/transaction semantics — not assumable from class names
alone.**

### Verified vs inferred vs test-needed (explicit split)

**Verified**: generic governed mutation/idempotency/outbox/recovery
vocabulary exists; generic event log (`EventLogRecord`/`ConsumerOffset`)
exists. **Inferred (plausible, not confirmed)**: one target per evolving
aggregate, one mutation per vote/transition. **Test-needed / genuinely
unverifiable without field-level reads**: repeated-mutation-per-target
support, concurrent-vote serialization vs rejection, whether
`MutationPlan` supports conditional/multi-step transitions based on the
full vote set, per-mutation vs per-effect outbox emission, receipt→
resulting-revision linkage, sync/async/retryable/compensatable effects,
logical-only vs durable-rollback recovery, same-vs-different broker path
for target creation vs mutation, payload validation/authorization hooks,
atomic multi-target updates.

**Next step**: field-level read of `MutationRequest`, `TargetState`,
`MutationPlan`, `OutboxEvent` dataclass definitions (not just names) to
resolve the "does it support accumulating rounds" question, which
determines whether a separate consensus coordinator is mandatory.

## CONFIRMED (2026-08-24, terminal, field-level read): broker DOES support accumulating rounds — biggest open question closed

Direct read of `peerhub/governance/contract.py`'s real dataclass fields
(not just names) resolves the "does the broker support multiple
sequential mutations against one target_id" question definitively: **YES.**

```python
class TargetState:
    target_id: str
    revision: int            # strictly positive, incremented per mutation
    state: Mapping[str, JsonValue]   # arbitrary JSON blob -- the round's domain state
    updated_at: int

class MutationRequest:
    request_id: str
    command_id: CommandID
    correlation_id: str
    client_id: str
    command_type: str
    idempotency_key: str
    actor_id: str             # who cast this vote/action
    policy_revision: str
    target_id: str
    expected_revision: int    # optimistic-concurrency CAS check
    operation: str            # e.g. "cast_vote", "final_call", "timeout"
    desired_state: Mapping[str, JsonValue]  # the new state blob after this op
    effect_intent: EffectIntent

class MutationPlan:
    plan_id: str
    request_id: str
    request_digest: str
    target_id: str
    previous_revision: int
    next_revision: int        # ENFORCED: next_revision == previous_revision + 1
    next_state: Mapping[str, JsonValue]
    effect_intent: EffectIntent
    planned_at: int
```

This IS exactly a revision-based optimistic-concurrency accumulating
aggregate: a consensus round's `TargetState.state` holds the round's full
domain state as an arbitrary JSON blob (e.g. `{"phase": "voting",
"votes": {"cc": "agree", "ag": null, "cx": "agree"}, "quorum": 3,
"deadline": ...}`); each vote is a `MutationRequest(operation="cast_vote",
actor_id=<voter>, target_id=<round_id>, expected_revision=<current>,
desired_state=<blob with the new vote folded in>)`; the broker enforces
`next_revision == previous_revision + 1` (real, code-enforced CAS), so
concurrent votes race on `expected_revision` — one wins, the loser's
request fails with a stale-revision error and must retry against the new
current state. **This closes the "is a separate consensus coordinator
mandatory" question too**: the coordinator's role is computing
`desired_state` from `current state + new vote + quorum policy`, then
submitting via the broker with CAS — the broker handles persistence/
idempotency/concurrency/outbox itself; the coordinator is a thin
domain-logic layer (quorum math, voter eligibility, deadline checks)
over this real, confirmed-adequate substrate.

**This is no longer "inferred" — reclassify from the earlier
verified/inferred/test-needed split**: repeated-mutation-per-target
support is now VERIFIED (was test-needed). Concurrent-vote handling is
VERIFIED to use revision-based CAS rejection (was test-needed) — the
exact retry semantics (does the caller get a typed error to retry, or
does something else happen?) still needs a read of `broker.py`'s actual
`apply_mutation_plan`/`validate_expected_revision` function bodies (not
yet done), but the MECHANISM (CAS on `expected_revision`) is now
code-confirmed, not guessed.

## CONCRETE SCHEMA DESIGN (2026-08-24, cx, built on the field-level confirmation above)

Proposed `TargetState.state` envelope (`schema: "peerhub.consensus-round.v1"`) — stable across all phases, phase transitions change `phase`/`status` and append immutable evidence, never redefine existing field meanings:

```json
{
  "schema": "peerhub.consensus-round.v1",
  "round_id": "round-2026-08-24-001",
  "phase": "voting",
  "status": "open",
  "proposal": {"title": "...", "question": "...", "body": "...", "proposer_id": "cx", "proposed_at": 1756000000, "source_hash": "sha256:..."},
  "participants": {"required": ["cc","cx","ag"], "eligible": ["cc","cx","ag"], "quorum": {"formula": "max(2, f(N, risk))", "required": 3, "risk": "normal", "basis": "protocol-v2"}},
  "votes": {"cc": {"choice": "agree", "actor_id": "cc", "cast_at": 1756000010, "mutation_id": "mut-001"}},
  "quorum": {"reached": false, "reached_at": null, "counted_votes": 1, "required_votes": 3},
  "final_call": null,
  "escalation": null,
  "resolution": null,
  "abandonment": null,
  "audit": {"last_operation": "cast_vote", "last_actor_id": "cc", "operation_count": 2}
}
```

`phase` values: `proposed → voting → quorum_reached → final_call → resolved | abandoned`. `status` (redundant, guard-friendly): `open | resolved | abandoned`. `final_call` object during that phase: `{required, opened_at, opened_by, question, acks: {actor: {ack, actor_id, acked_at, mutation_id}}, required_acks, ack_count, complete}` — **Final Call is an ACK round, not a second ordinary vote**; an ACK exposing a retroactive invariant violation transitions to escalation/abandonment per policy. `resolution`: `{outcome, resolved_at, resolved_by, basis, decision_hash, effective_state}`. `abandonment`: `{reason_code, reason, abandoned_at, abandoned_by, preceded_by}`.

### Operation → state-computation table

| Operation | Valid transition | Computation |
|---|---|---|
| `propose` | nonexistent → `proposed` | Create canonical envelope, empty votes/quorum, null phase objects. |
| `cast_vote` | `proposed`/`voting` → `voting`/`quorum_reached` | Copy state, validate actor eligibility+choice, set `votes[actor_id]`, recompute counted-votes+quorum. |
| `final_call_ack` | `final_call` → `final_call`/`resolved`/escalation | Copy state, validate ACK actor+uniqueness, set `final_call.acks[actor_id]`, recompute completion, create `resolution` if complete. |
| `mark_timeout` | any open phase → timeout handling | Copy state, record timeout evidence; if protocol mandates Human Tier-0 escalation, create escalation record — **never silently resolve**. |
| `request_escalation` | open/timeout → escalation pending | Copy state, set `escalation{reason, requester, tier, deadline, required_authority}`. |
| `resolve` | `quorum_reached`/`final_call`/human-escalation → `resolved` | Copy state, validate resolution authority+prerequisites, set immutable resolution data + `status:"resolved"`. |
| `abandon` | any nonterminal → `abandoned` | Copy state, validate abandonment authority/reason, set immutable abandonment data + `status:"abandoned"`. |

**`desired_state` is a COMPLETE replacement candidate, not a patch** — every operation reads current state, validates against phase+policy, constructs the FULL next state. `expected_revision` is broker-CAS-checked; a stale `desired_state` fails with a CAS conflict, **never auto-merged**.

### PTY-peer (ag) vote submission — architectural recommendation, not yet confirmable from field-level evidence alone

**UPDATE 2026-08-26 (terminal, direct peerhub source check)**: grepped
`peerhub/` for any `cast_vote`/consensus-round/vote-import handler outside
`legacy.py`'s catalog entries — none exists. This is not a stalled research
item; it genuinely cannot be resolved by reading more source because the
real handler doesn't exist yet (consistent with `LEGACY_CATALOG`: only
`ask`/`ask-all`/`ask-coordinator` are backed today). **Correct status: this
resolves naturally at gap-2 implementation time, once `consensus.round.
propose`/`cast_vote` handlers are actually written** — not a design-phase
blocker, and not something more dialectical discussion can shortcut.

Two possible architectures: (1) the direct `.ai/consensus/{round_id}.json` write is authoritative and ag bypasses `MutationRequest` entirely (needs a broker-side importer/reconciler to validate + convert + dedupe); (2) the file/`hub.py send` path is transport-only, and a coordinator/adapter receives the payload and issues a normal `MutationRequest(actor_id="ag", operation="cast_vote", ...)`. **cx recommends (2)** — keeps authorization/CAS/idempotency/audit/invariants centralized, ag's transport constraint stays intact. **Cannot be fully resolved without inspecting the actual write/send handler code** (not yet done). Standing requirement regardless of which: **all accepted ag votes MUST become governed `MutationRequest`s before affecting `TargetState` — a raw file must never be treated as committed consensus state.**

### 3 more ratification items resolved

- **Vote correction**: allowed via another `cast_vote` mutation (same `actor_id`, includes prior vote/hash, new `request_id`/`command_id`/idempotency key), **only while `proposed`/`voting`**, rejected after Final Call opens unless explicitly permitted. Visible `votes[actor_id]` = latest valid vote only; correction history lives in the broker journal. A correction recomputes quorum (may LOSE a previously-reached quorum if policy permits pre-Final-Call corrections).
- **Human Tier-0 override**: must NOT be an ordinary peer vote / must NOT use a peer identity. If the identity model supports a distinct authenticated authority class: `{actor_id: "human:<subject-id>", actor_kind: "human", authority_tier: 0, operation: "human_override", desired_state: ...}`, with `resolution.authority: {kind:"human", tier:0, subject_id, credential_evidence}` recorded separately. **A process merely claiming `actor_id:"human"` is insufficient** — if no authenticated human-admission path exists yet, the override needs a separate escalation/control plane materialized into a broker mutation by a trusted authority adapter afterward. Unresolved without the real admission/authority implementation.
- **Coordinator identity/failover**: the coordinator is a **role/lease over the domain, NOT a consensus participant/voter**. Round state may record `coordination: {role:"domain-coordinator", holder_id, term, lease_expires_at}`; broker stays stateless about "who is coordinator" except validated lease/term metadata; any eligible process may acquire the next term; old coordinators fenced by term/lease checks. Whether this needs its OWN separate `TargetState` (a lease/target abstraction) depends on real lease/fencing primitives not yet confirmed (see gap-4's field-level finding: `LeaseCreateRequest` is session/attempt-specific, so coordinator leadership likely needs its own lease type here too, consistent with that finding).

### Still unresolved (needs source inspection or explicit user policy decision)

Whether `desired_state` must be the complete canonical state or may be a broker-validated patch (this doc assumes complete-replacement, needs confirming against `mutations.py`'s real `plan_mutation`/`apply_mutation_plan` bodies); exact `f(N,risk)` for N>3; whether quorum may be lost after a correction; whether Final Call is mandatory for every quorum or only selected risk classes; whether Final Call ACKs veto on all invariant violations or a defined subset; exact timeout/escalation deadline mechanics past the 30-min Human Tier-0 trigger; the authoritative ag transport→MutationRequest conversion (see above); the actual `effect_intent` structure (consensus-state field or broker metadata?); human authentication/authority evidence and whether `human_override` can safely enter the normal broker; coordinator lease/term/fencing/failover semantics; whether audit history belongs in `TargetState.state`, an external journal, or both.

## DEFINITIVE CONFIRMATION (2026-08-26, terminal, real function bodies): `desired_state` is a COMPLETE replacement, CAS mechanism fully specified

Direct read of `peerhub/governance/mutations.py`'s real
`validate_expected_revision`/`plan_mutation`/`apply_mutation_plan`
bodies — this was the last major "unresolved" item flagged across
gap-2/5/6's concrete schemas, now closed with code, not inference:

```python
def validate_expected_revision(request, current) -> int:
    if current is not None and current.target_id != request.target_id:
        raise InvalidMutationError("current target does not match the mutation request")
    current_revision = 0 if current is None else current.revision
    if request.expected_revision != current_revision:
        raise StaleRevisionError(request.target_id, request.expected_revision, current_revision)
    return current_revision

def plan_mutation(request, current, *, plan_id, planned_at) -> MutationPlan:
    previous_revision = validate_expected_revision(request, current)
    return MutationPlan(
        plan_id=plan_id, request_id=request.request_id,
        request_digest=mutation_payload_digest(request),
        target_id=request.target_id, previous_revision=previous_revision,
        next_revision=previous_revision + 1,
        next_state=request.desired_state,   # <-- VERBATIM, no merge/patch logic
        effect_intent=request.effect_intent, planned_at=planned_at,
    )

def apply_mutation_plan(current, plan, *, updated_at) -> TargetState:
    current_revision = 0 if current is None else current.revision
    if current is not None and current.target_id != plan.target_id:
        raise InvalidMutationError("plan target does not match current target")
    if current_revision != plan.previous_revision:
        raise InvalidMutationError("plan previous revision is no longer current")
    return TargetState(target_id=plan.target_id, revision=plan.next_revision,
                        state=plan.next_state, updated_at=updated_at)
```

**Definitively confirmed**:
1. **`desired_state` MUST be the complete, full canonical state — never a patch.** `plan_mutation` copies `request.desired_state` into `next_state` with zero transformation. The domain coordinator (not the broker) is responsible for computing the complete next state before submitting.
2. **CAS rejection uses a specific typed exception, `StaleRevisionError(target_id, expected_revision, current_revision)`** — carries both the caller's stale value and the actual current value, enabling a precise retry (re-read current, recompute `desired_state`, resubmit with the correct `expected_revision`). `InvalidMutationError` is a separate exception for target-ID mismatches (a different, more serious class of caller error).
3. **A brand-new target's first mutation (e.g. `propose`) MUST set `expected_revision=0`** — `current_revision = 0 if current is None else current.revision` treats "target doesn't exist yet" as revision 0.
4. `apply_mutation_plan` performs a SECOND, redundant revision check (defense in depth against a stale plan being applied after another mutation landed in between planning and applying) — real double-checked CAS, not a single-check race window.

**This closes gap-2/5/6's shared "still unresolved: patch vs. full replacement" item across all 3 concrete schemas** — every schema drafted (consensus round, lesson, task) should now be read with "desired_state = the complete next canonical object" as a hard, code-confirmed requirement, not an assumption.
