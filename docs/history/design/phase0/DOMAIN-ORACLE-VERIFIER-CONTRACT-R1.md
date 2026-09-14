# Domain-Oracle Verifier Contract R1

**Status:** Proposed Phase 0 design specification — design-only, provider-free.
Authorizes no product implementation, live Hub/database access, broker
authority, authority cutover, or Phase 0 exit.

**Scope:** A minimal verification layer that can genuinely unblock the 16
`NARROW_COVERAGE` fixtures identified in
`NARROW-COVERAGE-EVIDENCE-DECISION-R1.md`: `DT-02..05` (process-transport,
not a policy domain), `HR-04..06` (health), `RT-04..06` (routing),
`GB-01/03/04/05` (broker), `CJ-02/05` (command authorization). Existing
narrow captures under `tools/phase0_fixture_runner/captures/` remain
immutable historical evidence and stay `NARROW_COVERAGE`/non-exit-eligible;
this document defines what a *new*, faithful capture must additionally
prove.

## 1. Problem and exact fixture scope

The controlled-fake runner's 9-event vocabulary is process-lifecycle-only.
For the 16 IDs above, their own spec docs describe domain behavior
(incremental transport framing, health circuit-breaker/backoff state, CAS
transaction atomicity, routing weight/selection, actor authorization) that
the runner cannot observe — existing captures for these IDs only prove
that generic idempotency/lifecycle plumbing ran, not that the described
behavior is correct. A generic new event type would not fix this: it would
still be self-reported script input unless something independently
computes and checks the expected outcome. This document defines that
independent check.

## 2. Runner integration and sole status authority

`runner.py`'s process-lifecycle engine and its `V1_CAPTURE` gate
(`tools/phase0_fixture_runner/runner.py`, already committed/tested) are
**not modified**. A new pre-finalization verification step runs after
lifecycle reduction and before `_finish_fixture` writes
`fixture-record.json`.

Event-script schema extends prospectively to v2 with a `domain_case` block,
additive to the existing v1 fields:

```json
{
  "schema_version": 2,
  "clock": [],
  "ids": [],
  "events": [],
  "domain_case": {
    "contract_version": 1,
    "fixture_id": "RT-04",
    "oracle_id": "routing.rt04.exclusion",
    "oracle_version": 1,
    "inputs": {}
  }
}
```

Each fixture's `inputs` schema is closed: unknown fields fail, and the
schema explicitly forbids any pre-computed outcome field (`expected`,
`claimed_result`, `selected_candidate`, `required_result`, `dispatch`,
`unauthorized`, or equivalent). Only raw facts are supplied. If a script
carries a forbidden outcome-shaped key, verification fails closed with
`ORACLE_INPUT_TAINTED` before the oracle or adapter runs.

`V1_CAPTURE` becomes a conjunctive gate:

```text
V1_CAPTURE =
    existing_core_gate_passed
    AND expectations_passed
    AND (
        fixture_has_no_required_domain_oracle
        OR domain_verification == PASS
    )
```

Missing required verification produces `DOMAIN_VERIFICATION_REQUIRED`;
a mismatch produces `DOMAIN_ASSERTION_FAILED`. `fixture-record.json`
remains the single artifact written last by `_finish_fixture`, gaining
relative paths, digests, and a `domain_verification` summary. No companion
tool may upgrade an already-written record.

A new passing execution creates a new capture root and may carry
`coverage_scope: "SPEC_FAITHFUL"` only when this full conjunctive gate
passes.

## 3. Shared oracle and adapter contract

```text
tools/phase0_fixture_runner/
  domain/
    contract.py
    transport.py       # DT-02..05
    health.py           # HR-04..06
    routing.py           # RT-04..06
    broker.py           # GB-01/03/04/05
    command_authz.py    # CJ-02/05
```

Two roles, kept structurally separate:

```python
class DomainOracle(Protocol):
    oracle_id: str
    oracle_version: int
    fixture_ids: frozenset[str]

    def compute_expected(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, JsonValue],
    ) -> Mapping[str, JsonValue]: ...


class DomainSubjectAdapter(Protocol):
    adapter_id: str
    adapter_version: int
    fixture_ids: frozenset[str]

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, JsonValue],
        context: IsolatedDomainContext,
    ) -> Mapping[str, JsonValue]: ...
```

The **oracle** is pure: no filesystem, clock, ID generation, environment,
network, or access to the adapter's output. The **adapter** receives only
validated raw inputs and the runner's injected clock/IDs, and executes the
reference behavior being checked. Only the broker's adapter (`GB-01`) may
use a fresh-root SQLite database — transaction atomicity cannot be
meaningfully proven through a pure claimed-state comparison alone.

Verification order:

1. Validate the closed input schema; reject taint (§2).
2. Run the subject adapter to produce `domain-actual.json`.
3. Independently run the oracle to produce `domain-expected.json`.
4. Validate both against the same fixture-specific output schema.
5. Compare canonical JSON bytes exactly.
6. Write `domain-verification.json` with input, actual, expected, adapter,
   and oracle digests.

The oracle never returns `V1_CAPTURE`, `SPEC_FAITHFUL`, or any fixture
status — the runner remains the sole status authority (§2). Oracle and
adapter modules for the same fixture **must not** import each other or
share domain-decision helper code; they may share only canonical-JSON and
envelope-validation utilities. No script-supplied Python module, import
path, expression, or arbitrary oracle name is permitted — a static,
checked-in fixture-to-oracle registry table is the only dispatch
mechanism.

## 4. Transport verifier (DT-02..05)

`DT-02..05` are process-lifecycle fixtures whose gap is transport
correctness under chunking/timing, not a policy domain — they route
through the same mechanical `DomainOracle`/`DomainSubjectAdapter` contract
via `transport.py`, kept separate from the four policy modules below.

- **DT-02 — incremental framing:** given only ordered raw byte chunks,
  incremental UTF-8 decoding plus `\r`, `\n`, `\r\n` handling must produce
  the same canonical text/line sequence as whole-buffer decoding,
  regardless of chunk boundaries.
- **DT-03 — independent timeout selection:** given two separate timelines,
  silence expiry classifies as `SILENCE_TIMEOUT` and process-deadline
  expiry as `PROCESS_TIMEOUT`; neither case may inherit the other's first
  terminal result.
- **DT-04 — cancellation ladder:** given deadline and cancellation
  observations, compute the ordered cancel/escalate steps and retain
  `PROCESS_TIMEOUT` with `MAY_HAVE_STARTED` uncertainty when termination is
  not conclusively reconciled.
- **DT-05 — tree closure:** given the initial stable identity tokens and
  post-cancellation observations, every initial identity must be proven
  terminated or appear in an unresolved set that yields an explicit
  `CANCELLATION_CLEANUP_FAILED` evidence record.

## 5. Health oracle (HR-04..06)

- **HR-04 — authority-sensitive clearance:** an automatic circuit clears
  only for a receipt exactly matching current incident, generation,
  timestamp, and fingerprint; automatic evidence cannot clear a manual,
  security, or policy quarantine.
- **HR-05 — one-probe grant:** one administrative recovery grant
  authorizes exactly one probe and leaves the health value and gate state
  unchanged until a separately verified probe receipt is applied.
- **HR-06 — CAS-gated probe transition:** a failed current probe
  increments backoff and keeps `CIRCUIT_OPEN`; a successful probe opens
  only when revision, incident, generation, timestamp, and fingerprint all
  match; a stale or changed-fingerprint receipt is an exact no-op.

## 6. Routing oracle (RT-04..06)

- **RT-04 — exclusion weighting:** when an eligible non-terminal candidate
  exists, terminal and otherwise-excluded candidates receive automatic
  weight zero and a deterministic exclusion reason; only eligible
  candidates remain selectable.
- **RT-05 — deterministic tie selection:** the same request ID, immutable
  snapshot digest, and equal candidate set must reproduce both the audit
  seed and the selected candidate. **This fixture has an additional,
  unresolved blocker**: the seed-serialization and selection algorithm are
  not frozen anywhere in the existing corpus (prior docs only say a
  candidate is chosen "deterministically," not how). This document
  proposes freezing it as: `seed = SHA-256(canonical_json({request_id,
  snapshot_digest}))`; selection index = unsigned big-endian integer of the
  seed's first 8 bytes, modulo the count of the lexicographically sorted
  eligible-candidate set. `RT-05` cannot move past
  `DOMAIN_VERIFICATION_REQUIRED` until this specific formula (or an
  alternative) is ratified — it is called out separately so it is not
  silently absorbed into the general ratification gate in §9.
- **RT-06 — pre-dispatch drift:** any difference between the frozen
  configuration/admission snapshot and the current revision returns
  `CONFIGURATION_STALE`, performs zero dispatches, and supplies current
  state as the input to a new plan.

## 7. Broker oracle and isolated transaction adapter (GB-01/03/04/05)

- **GB-01 — atomic CAS commit:** under success and injected failure
  points, target revision, pending receipt, and outbox row are either all
  committed or all absent. This requires the isolated SQLite subject
  adapter (§3) — a pure self-reported state object cannot prove
  transaction atomicity.
- **GB-03 — idempotency sequence:** the first key/payload mutates once;
  the identical repetition returns the original receipt without mutation;
  a changed payload returns exactly `IDEMPOTENCY_PAYLOAD_MISMATCH`.
- **GB-04 — recovery without replay:** from a durable committed transition
  plus pending outbox state, startup reconciliation must not reapply the
  transition and must not redispatch an uncertain external effect;
  counters must remain `transition_applies=1`, `blind_replays=0`.
- **GB-05 — immutable terminal receipt:** a CAS claim admits one owner,
  and the first `EFFECT_SUCCEEDED` or `EFFECT_FAILED` receipt remains
  bound to request, outbox, attempt, and result; all competing or later
  terminal writes are rejected.

## 8. Command-authorization oracle (CJ-02/05)

- **CJ-02 — valid admission:** from a valid envelope, authorization facts,
  current revisions, and an injected command-ID value, admission returns
  that command ID while preserving all identity fields and recording zero
  provider/dispatch calls.
- **CJ-05 — authorization-before-effects:** missing actor authority
  returns `ACTOR_UNAUTHORIZED`, exit code 3, `NOT_STARTED`, non-retryable,
  and a null command ID, with zero state, receipt, outbox, provider, or
  dispatch mutations.

## 9. Anti-circular verification and negative proof

A reference adapter plus an oracle cannot prove future product
correctness; it can only create a non-vacuous, spec-faithful acceptance
harness. That limit is stated explicitly, not implied.

Required controls:

- Scripts contain raw facts only; fixture-specific schemas reject claimed
  outcomes, checked before the oracle or adapter runs (`ORACLE_INPUT_TAINTED`, §2).
- Oracle and subject-adapter modules for one fixture cannot import each
  other or share domain-decision helpers.
- Oracle, adapter, schemas, fixture vectors, and source specifications are
  independently hash-bound at ratification.
- Every fixture requires at least one deliberately wrong subject
  observation that must fail verification — a green-only suite is
  insufficient evidence that the check can fail at all.
- Boundary/metamorphic vector pairs are required per family: reordered DT
  chunks, stale-vs-current HR receipts, permuted RT candidate input,
  injected GB transaction failures, and one-field CJ authorization
  changes.
- Oracle and adapter implementations for a given fixture should be
  authored or reviewed independently rather than by the same single pass.
- Unknown oracle ID/version, a missing artifact, a digest mismatch, or an
  unsupported input all fail closed.
- A later real product implementation replaces the *reference* subject
  adapter with the production one, while keeping the same oracle and
  vectors — the reference capture must never be described as
  production-behavior evidence.

## 10. Shared versus separate contracts

One shared, mechanical `DomainOracle`/`DomainSubjectAdapter` envelope and
comparison contract, but five separately scoped modules (four policy
domains plus DT transport). A single generic rule engine or transition DSL
would recreate a configuration god-object and obscure which specific rule
each fixture proves; four/five fully separate runners would duplicate
journaling, digesting, isolation, and `V1_CAPTURE` authority that
`runner.py` already owns. The hybrid keeps one status gate while making
every domain rule small, explicit, statically registered, and
independently hashable.

## 11. Artifacts, digests, and coverage promotion

Each domain-verified run adds `domain-input.json`, `domain-actual.json`,
`domain-expected.json`, and `domain-verification.json` to its capture
root, each with a raw-byte SHA-256 digest recorded in
`fixture-record.json`. `coverage_scope: "SPEC_FAITHFUL"` may replace
`NARROW_COVERAGE` only in a *new* capture that passes the full conjunctive
gate in §2 — an existing `NARROW_COVERAGE` record is never edited in
place.

## 12. TDD and ratification gate

This document, the five new modules' contracts, the closed input/output
schemas, the required negative/metamorphic vector list (§9), the source
specification documents for each fixture, and
`NARROW-COVERAGE-EVIDENCE-DECISION-R1.md` must be hash-bound together in a
new unanimous round before any implementation starts. `RT-05`'s seed/
selection formula (§6) is called out as its own explicit ratification item
within that round, not assumed. No implementation, database creation, or
`phase0_exit_eligible` flip is authorized by this document alone,
consistent with `TDD-READINESS-GATE-R1.md`.

## 13. Provenance

Drafted independently by `ag.deepthink` and `cx.deepthink`. `cx`'s
architecture was adopted in full: it correctly separated `DT-02..05` as a
process-transport concern rather than folding them into routing/pacing (a
mixup in `ag`'s draft, which also misapplied an unrelated quota-pacing
formula from `QUOTA-PERIOD-SCALING-POLICY-R1.md` to `RT-04..06`), and it
identified that `GB-01`'s atomicity claim requires a real isolated
subject adapter rather than a pure comparison, which `ag`'s draft did not
address. `ag`'s concrete taint-rejection mechanism
(`ORACLE_INPUT_TAINTED`) and its "4 narrow adapters, 1 shared interface"
structural recommendation were convergent with `cx`'s and are retained.
See `DOMAIN-ORACLE-RECONCILIATION-R1.md` for the full reconciliation.
