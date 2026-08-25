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

**Still open** (7, 8, 9's exact recording format, 10) — not covered in the sections read; need a further check against protocol.md's other sections or protocol.json before final ratification.

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
