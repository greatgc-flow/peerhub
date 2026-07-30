# TDD Readiness Gate Closure R1

Status: proposed closure record for `TDD-READINESS-GATE-R1.md`'s condition 6
("A new ratification round binds protocol, authority, fixture, and
dependency hashes without rewriting historical frozen documents"). Produced
by cc's direct verification of conditions 1-5 against the actual repository
state (2026-07-30), pending ag.deepthink + cx.deepthink + cc.fable review.
Rewrites no historical frozen document; binds hashes only.

## Per-condition verification

**Condition 1** -- fixture inventory has an explicit status for all 54
contract IDs, none `V1_SPEC_ONLY`/`LEGACY_CAPTURE`: **MET.**
`fixture-status-v1.json` (bound below) shows all 54 entries at
`evidence_status: V1_CAPTURE`, and further, all 54 at `coverage_scope:
SPEC_FAITHFUL` (a stronger bar than condition 1 literally requires). This
closes across four ratification rounds this session
(`session-2026-07-29-ac-track`, `-sl-track`, `-hr-track`,
`session-2026-07-30-dp06-r3-track`), the last of which resolved DP-06, the
final holdout.

**Condition 2** -- the controlled-fake runner contract is frozen (isolated
root, append-before-reduce journal, canonical transcript, deterministic
clock/IDs, no provider): **VERIFIED against the actual `runner.py` code**,
not merely asserted:
- Isolated root: `run_fixture` raises `InvalidInvocationError("out-root
  already exists; a fresh root is required")` when the output root is not
  fresh (confirmed by direct execution during this session's DP-06 work).
- Append-before-reduce: `runner.py` line 1337 (`_append_journal`) executes
  before line 1350 (`_reduce_record`), with the inline comment "Durability
  precedes event validation and all state reduction" -- this is the same
  ordering `CONTROLLED-FAKE-RUNNER-CONTRACT-R3.md`'s section 3.1 relies on.
- Canonical transcript: `_canonical_json_bytes` is used consistently for
  the journal, transcript, and digest computation (`TRANSCRIPT_SCHEMA_
  VERSION = 1`).
- Deterministic clock/IDs: event records use `prepared.clock`/`prepared.
  ids`, injected by the fixture script, never wall-clock or ambient state.
- No provider: `runner.py` contains no `subprocess`, `socket`, or any
  network/process-spawning import at all.
`CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md`'s hash (bound below) is unchanged
since the `r-517f` bootstrap round; `CONTROLLED-FAKE-RUNNER-CONTRACT-R3.md`
is an additive, narrowly-scoped amendment (resolves only backlog item #1),
not a rewrite.

**Condition 3** -- health implementation tests adopt R3 (`RUNTIME-HEALTH-
RECOVERY-ADDENDUM-R3-2026-07-28.md` -- note this is a *different* document
from `CONTROLLED-FAKE-RUNNER-CONTRACT-R3.md` ratified this session;
flagging the name collision explicitly to prevent confusion): host-only
receipt minting, incident/gate-generation CAS, authority-scoped quarantine
clearing, separate health from quota/pacing admission. **Phase 0 evidence
is ready; literal "tests adopt" cannot be fully true pre-TDD.** Verified:
`health.py` (HR-04..06) models authority-scoped quarantine clearing
(`_AUTHORITY_CLASSES`, `QUARANTINE_AUTHORITY_*` codes) and incident/gate-
generation CAS (HR-06's `incident`/`gate_generation`/`timestamp`/
`fingerprint` CAS tuple, confirmed against the committed fixtures); no
quota/pacing term appears anywhere in `health.py`, confirming the required
separation by construction. **Correction (cx.deepthink caught this):**
"host-only receipt minting" is NOT modeled as "every receipt is output,
never accepted as input" -- HR-04's own input schema explicitly requires
`inputs.clearance_receipt` as a fact-injected input, since the fixture
models the receipt as an already-minted fact to be validated, not something
the oracle itself mints. This is ordinary controlled-fake fact-injection
(consistent with every other module this session), not literal host-only
minting enforcement -- that property belongs to the real host runtime, not
to a Phase 0 fixture, and this closure should not have implied otherwise.
Since no real source test suite exists yet
(pre-TDD by definition), the literal condition text ("tests adopt") is not
yet verifiable as a fact about real tests -- what this closure certifies is
that the Phase 0 evidence already encodes the three fixture-observable
properties (authority-scoped quarantine clearing, incident/gate-generation
CAS, and separation from quota/pacing), and records host-only receipt
minting as a future real-host test obligation, not something a Phase 0
fixture can itself enforce.

**Condition 4** -- protocol tests use the R1 crosswalk and canonical error
taxonomy: **Phase 0 evidence ready; same pre-TDD caveat as condition 3.**
Verified: no superseded term (`IDEMPOTENCY_CONFLICT`, `HARD_TIMEOUT`)
appears anywhere in `tools/phase0_fixture_runner/`; the canonical terms
(`IDEMPOTENCY_PAYLOAD_MISMATCH`, `PROCESS_TIMEOUT`) are used in `broker.py`,
`dispatch_pipe.py`, and `transport.py`. **Correction (cx.deepthink caught
this):** `authority_fence.py` uses `CUTOVER_EPOCH_CONTENDED` specifically
(matching `PROTOCOL-V1-FREEZE.md`'s frozen State/CAS taxonomy), but
`REVISION_CONFLICT` and `EPOCH_STALE` do not appear anywhere in
`tools/phase0_fixture_runner/domain/*.py` at all -- the original claim that
all three are "used in `authority_fence.py`" was wrong. This does not mean
condition 4 fails; it means only the taxonomy entries actually exercised by
a built fixture this session can be positively confirmed, and
`REVISION_CONFLICT`/`EPOCH_STALE` simply were not among them. No fixture
built this session uses either term incorrectly or under a superseded name;
their absence is a coverage gap in what's been exercised, not a violation.

**Condition 5** -- authority-cutover proof-matrix rows bind explicit
fixture IDs/statuses; no cutover executable while a required proof remains
spec-only: **MET.** `authority-proof-status-v1.json` (bound below, hash
unchanged since the `session-2026-07-29-ac-track` round) shows exactly the
9 AC-01..AC-09 umbrella IDs at `V1_SPEC_ONLY` and all remaining 56 entries
at `V1_CAPTURE` -- **correction (cx.deepthink caught this): those 56 are
not uniformly "child sub-fixtures"; they comprise 52 `AC-0X-YY` child
sub-fixtures plus 4 `AC-COMPOSED-0N` composed integration scenarios**, a
distinct category covered by Task #21 earlier this session, not a child of
any single AC-0X umbrella; `cutover_capture_required_before_execution:
true` is set;
the `umbrella_note` field honestly states that a `SPEC_FAITHFUL` child
proves PeerHub's decision logic given stated observations, never that
Windows actually produces those observations -- consistent with the
two-track evidence principle used throughout this session.

**Condition 6** -- this document.

## Bound hashes (raw-byte SHA-256, no historical document rewritten)

| Document | SHA-256 |
|---|---|
| `fixtures/fixture-status-v1.json` | `ad489af35b978b7a1b5c60c9749ec315449d66e172edd9b498e49a01c192eead` |
| `fixtures/authority-proof-status-v1.json` | `6a7570cd93327cff8bf578be15179ad0b89f44a20910385c87dee298055522e4` |
| `CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md` | `30d693621885b5887bbfdff470869e2e3aaab32de71e92a009c6754210d1b422` |
| `CONTROLLED-FAKE-RUNNER-CONTRACT-R3.md` | `b784623ff070da0e553164a4bb9f896e4eab0d49e41d3acc724f9dc2c4c602f3` |
| `TDD-READINESS-GATE-R1.md` | `c910bea70b0aa2b2f56f968ffe793c7dd3174fa3e5827bc5cc2d43e661824380` |
| `DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md` | `e414cdb13656b744a25a227b463189995235f0a3eae2c172738bb36e8b5d0120` |
| `SL-01-06-SESSION-LEASE-CLASSIFICATION-SPEC-R1.md` | `3e6862ab388dbf7f7bae56facfc35eca5e2175cb61177b676773df236e1f8b0d` |
| `HR-01-03-HEALTH-RECOVERY-CLASSIFICATION-SPEC-R1.md` | `4b805385daacd785e8d58e9d49eeaae695d35d007072312ab14ed50543428f91` |
| `PROTOCOL-V1-FREEZE.md` (already bound, `r-aec7`, unchanged) | `7bd70ba40b4489d3523216c1e33d6012a88d72f2183d56c8354d4b62834ec0f4` |
| `AUTHORITY-PROOF-SCOPING-DECISION-R1.md` (already bound, unchanged) | `6cad44a5d9c33e48da0de3dad504cadeb4f1db93829f8c357ef6d67a038bb6f7` |

## What this closure explicitly does NOT authorize

- TDD start is **not** authorized by this document alone. Conditions 3 and
  4 require a real source test suite to actually adopt these properties;
  no such suite exists yet. This closure certifies the Phase 0 evidence
  those future tests must conform to is complete and internally
  consistent -- it does not and cannot certify facts about tests that do
  not yet exist.
- Phase 0 exit, cutover execution, and production implementation remain
  separately gated (per `authority-proof-status-v1.json`'s
  `cutover_capture_required_before_execution` and the umbrella-note's
  real-OS-integration-track caveat).
- R3 backlog items #2-15 (`DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md`)
  remain open and are not touched by this closure.
- No historical frozen document is edited or rewritten by this closure;
  it only records hashes of documents as they already stand.

## Disposition

Pending: unanimous ACK from ag.deepthink, cx.deepthink, and an independent
cc.fable review (per the standing collaboration protocol for
final-Phase-0-scope ratification rounds), each verifying the per-condition
claims above against the actual repository state rather than taking this
document's claims on faith.
