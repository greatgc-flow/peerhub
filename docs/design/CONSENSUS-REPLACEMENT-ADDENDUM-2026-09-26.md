# Consensus replacement — implementation addendum (2026-09-26)

Amends `CONSENSUS-REPLACEMENT-R1-ag-deepthink-2026-09-25.md` where implementation and the
cx.astra reviews (activation-design review, final audit of `683644a`) showed the ratified text
cannot be built as written. Everything not listed here stands.

## A1. B8.1 activation: option C replaces the table rename
`governed_targets` is the **shared** table of every governance service (rooms, tasks, lessons,
directives, registry, roles, artifacts, proposals, …). Renaming it (original B8.1) fences all of
them. Ratified replacement (cx.astra: option C):

* A dedicated `consensus_targets` table plus a one-row `consensus_activation` metadata table
  (migration 0036), same SQLite DB.
* `activate_consensus_v2` runs in **one `BEGIN IMMEDIATE` transaction**: re-checks under the write
  lock that the state is *complete pre-activation* (already-activated / inconsistent ⇒ refused with
  zero mutation), installs guard triggers on `governed_targets`, bumps `activation_epoch` in the same
  transaction (never via `mint_new_epoch`, which commits itself), records metadata. Any failure rolls
  everything back (verified with fault injection at every step boundary).
* Guard triggers abort any INSERT/UPDATE/DELETE whose OLD **or** NEW row is a consensus round
  (denormalised `target_kind` **and** `json_extract(state_json,'$.kind')`).
* Effect fence: after activation only owners prefixed `consensus-v2:` may claim `consensus.*`
  effects, and only `peerhub.invariant-request-projector` may claim the ratified-invariant effect.
  Work claimed before activation may still complete.
* Repository routing when activated: consensus rounds live in `consensus_targets` (V2-first read,
  legacy fallback, de-duplicated listing, first mutation promotes at the original revision + 1).
  **V1-shaped consensus state is refused** at the repository on insert and on promotion.
* The runtime activation probe validates the complete state and fails closed on `inconsistent`.
* `cutover.classify_activation_state` (table-rename model) is retired; its role is
  `persistence.consensus_activation.read_activation_state`.

## A2. V1 rounds are held after activation (design 7.2 made concrete)
No implicit upcast of in-flight V1 rounds. After activation the facade fails closed on any mutation
of a V1 round ("held; needs administrator migration"). Reads and listings keep working.
`provenance.upcast_v1_round` and `cutover.migration_disposition` / `FrozenLegacyEvaluator` remain
pure specs with no production caller until an administrator-migration tool is built. **Operational
precondition for activation:** drain or resolve every open V1 round and every pending/claimed
consensus effect first (inventory required).

## A3. Authorization (audit findings 1, 2, 6)
* `system:*` strings are **not** trusted. The shell takes an explicit allowlist
  (`PROPOSAL_SYSTEM_PRINCIPALS`) and only for `request_escalation`, `reject_on_dissent`,
  `mark_timeout`; such principals never vote/ACK/NACK/resolve.
* `resolve` requires a regular authorized electorate member (+ credential when the round requires
  one); terminal decisions are immutable.
* Final Call: a blocking NACK or unresolved dissent (`block`/`need_more_info`) prevents approval
  until the holder's own ACK; dissent forces the mandatory floor even when the configured rule is
  not `always`; the completing ACK revalidates the whole electorate; `correction` voids votes,
  concerns, candidate and ACK ledger.
* Public-command identity is the canonical tuple `(operation, round, actor, client key)`; the
  argument digest lives in the round's command log; a replay is re-authenticated; every command
  carries idempotency key and expected revision end to end.

## A4. Atomicity (audit findings 4, 7)
* Every mutation validates the authority version **inside** the committing transaction
  (`broker.submit(precondition=…)`, creation included).
* Post-authorization retraction commits the revocation record, the authority increment, both
  receipts and effects in one transaction (`broker.submit_atomic`).
* The V2 effect worker resumes a crashed claim under its recorded attempt, completes no-op effects
  and pages past the discovery window.

## A5. High-risk proposals (design 4.2 / 6.2 made explicit)
A high-risk proposal never auto-approves on its last agreement: it opens a mandatory Final Call and
is approved atomically with its ratified-invariant effect by the last ACK. This is the single
deliberate behavioural difference from V1; `test_proposals_v2.py` replays the entire V1 proposal
suite against activated V2 storage with only that test rewritten.

## A6. Policy
The frozen round snapshot comes from the layered dispatch policy
(`ConsensusPolicyProvider`: defaults < global < workspace `dispatch-policy.toml`); an unreadable or
invalid layer is a `ConfigurationError`. Proposals are always strict unanimous of the electorate.

## Still open (not implemented)
Arbiter opinion recording on V2 rounds (facade fails closed); real-DB inventory/rehearsal runbook;
migration tool for held V1 rounds; the unused pure helpers listed in the audit
(`AuthorityFence.claim`, `policy_snapshot.select_policy/effective_depth/load_config`,
`provenance.action_name/validate_required_votes/count_fixed_agrees`).
