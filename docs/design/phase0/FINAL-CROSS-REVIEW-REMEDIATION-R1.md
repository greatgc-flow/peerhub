# Final Cross-Review Remediation R1

Status: in-progress remediation record for the unlimited final full
cross-review requested 2026-07-30, explicitly authorized to revise
already-ratified work. Two rounds (independent ag.deepthink + cx.deepthink
audits, then cross-validation) found real defects across the session's
Phase 0 work. This document records what has been fixed and verified, and
what remains, with precise fix specifications so remaining work can be
picked up without re-deriving the analysis.

## Process

Round 1: independent full-repository audits by ag.deepthink and
cx.deepthink (fresh sessions), each producing a ranked findings list.
Round 2: each peer cross-validated the other's findings against the actual
files and produced a consolidated fix plan. cc independently verified the
most consequential claims directly (not trusting either peer blindly)
before acting: CJ's error-code mismatch against PROTOCOL-V1-FREEZE.md,
contract.py's outcome-taint denylist gap, SL-04's spec-vs-code gap, RT-03's
ABSENT/UNAVAILABLE mislabeling, and HR-03's oracle/subject independence
violation were all confirmed true by direct file inspection before any fix
was made.

## Fixed and verified (committed)

- **RT-03**: `usage_disposition`/`decision`/`oracle_id` renamed from
  ABSENT-family to UNAVAILABLE-family, matching PROTOCOL-V1-FREEZE.md's
  reserved meaning of `ABSENT` (complete authoritative observation with a
  completeness receipt) versus an undeclared/never-measured observation
  (`UNAVAILABLE`). Commit `7a0bf02`.
- **HR-03 independence**: added `_subject_scenario_result`, a genuinely
  independent derivation (forward canonical-stage scan) distinct from the
  oracle's `_derived_scenario_result` (backward last-attempted-stage
  slice); both agree on all real scenarios. Commit `7a0bf02`.
- **HR-03 coverage_scope**: downgraded `SPEC_FAITHFUL` ->
  `PENDING_FAITHFUL_MAPPING_REVIEW` since CONTRACT.md's HR-03 one-liner
  requires reaching "the correct degradation/quarantine policy," which
  this fixture does not model at all (no `policy_action`, no `decision`
  input, no state transition). Commit `7a0bf02`.
- **CJ-01..06**: renamed all invented non-canonical error codes to
  PROTOCOL-V1-FREEZE.md's actually-frozen taxonomy
  (`MALFORMED_ENVELOPE`, `PROTOCOL_VERSION_MISMATCH`,
  `SCHEMA_VERSION_UNSUPPORTED`, `CONFIGURATION_STALE`, `POLICY_STALE`),
  with CJ-04 and CJ-05 now deriving the correct one of two frozen codes
  from their own existing preconditions instead of using one generic
  term for both, and corrected exit-code family assignment
  (`CONFIGURATION_STALE`/`POLICY_STALE` = 4/admission, not 3/authorization).
  Commit `7a0bf02`.
- **Provenance index hygiene**: corrected a stale "78 fixtures" count
  (actual: 56 positive AC evidence scripts, 109 total) and rewrote the
  stale "Remaining decision chain" section, which still described
  `CONTROLLED-FAKE-RUNNER-CONTRACT-R3` as future work despite it already
  being ratified earlier the same session. Commit `7a0bf02`.
- **SL-04/05/06**: expanded to match `SL-01-06-SESSION-LEASE-CLASSIFICATION-SPEC-R1.md`'s
  own ratified text. SL-04 now also proves close-L2-leaves-L1-active and
  stale-CAS-cannot-mutate-either (previously only create+renew-L1). SL-05's
  fence check now covers the full ratified 8-field tuple (was 4 identity
  fields) and the oracle independently derives REJECTED via a
  list/`any()` mismatch scan (was a hardcoded static dict); the subject
  independently derives the same result via a boolean AND-chain, giving
  genuine algorithmic diversity between the two roles. SL-06's receipt now
  includes all ratified fields (`recovery_receipt_id`, `detected_at`,
  `mismatch_dimensions`, `evidence_digest`, `policy_revision`, optional
  `external_effect_certainty`), and `post_revision`/`post_fencing_token`
  are derived as `pre + 1` independently by the oracle (inline addition)
  and subject (generator-unpacking) instead of being accepted as
  pre-validated input and echoed. Drafted by cx.deepthink from a fully
  specified brief, cc verified the diff directly (oracle/subject
  independence, fixture symmetry, exact-fields validation) before
  running the suite. 262/262 tests green. `docs/design/phase0/fixtures/
  captures/SL-0{4,5,6}.json` were checked and confirmed to be the
  original immutable OBS-tier legacy-defect records (unrelated to the
  CANDIDATE-tier fixture schema) -- correctly untouched, no
  `-NARROW-V1` preservation applies here (that pattern is for cases where
  a persisted *runner-generated* capture is superseded, which does not
  exist for SL). ag.deepthink reviewed the full diff against 4 targeted
  questions (independence, stale-CAS genuineness, fixture/validator
  alignment) and returned a clean ACK; one of its line citations (oracle
  mismatch-scan attributed to session_lease.py:852-856) was checked and
  is wrong -- that line range is inside the input validator, the actual
  mismatch-scan is ~2165-2232 -- the same citation-accuracy pattern noted
  earlier this session with ag. The substantive finding was independently
  re-verified by cc by reading the actual code before trusting it, and
  holds. Committed.
- **DP-06 capture metadata**: reviewed and NOT changed -- the raw
  `fixture-record.json`'s `runner_contract: CONTROLLED-FAKE-RUNNER-
  CONTRACT-R2` field is accurate as written; it describes the runner's
  own journal/reduce *mechanics* (unchanged by R3), not the domain-level
  classification rule (which R3 does govern, and which is already
  correctly cross-referenced in `fixture-status-v1.json`'s DP-06 note).
  No code change needed; this closes ag/cx's finding as a documentation
  clarification rather than a defect.

## Flagged, not yet fixed (precise specs below)

### CR-05 (High, security-relevant)

`coordination_room.py`'s CR-05 positive/negative pair only tests
self-asserted `heartbeat_peer == assigned_peer` string equality -- the
exact self-asserted-identity trust pattern `session_lease.py`'s SL-05 was
built to fix. It never tests the SL-05-style spoof (asserted peer matches,
authenticated principal does not).

**Fix spec**: add `authenticated_principal_id` to `_validate_heartbeat_facts`
(coordination_room.py line ~363) alongside the existing self-asserted
`heartbeat_peer`. Acceptance requires `heartbeat_peer == assigned_peer` AND
`authenticated_principal_id` matching the assigned peer's true owner
principal (mirror SL-05's `requester`/`persisted` split exactly). Add a
new fixture vector (or a `-NEG-02`) for the spoof case specifically:
asserted peer matches, principal does not -- expect rejection with zero
mutation. Update `_validate_case_kind_specific` (line ~463),
`validate_coordination_room_output`'s CR-05 branch (line ~654), the
oracle/subject logic (lines ~786, ~884), and the fault adapter (line
~1013). fixture-status-v1.json's CR-05 note already documents this exact
spec (added 2026-07-30) so it need not be re-derived.

### SL-04/05/06 -- FIXED (2026-07-30, see "Fixed and verified" above)

### SL-01/02 (low priority, still open)

- **SL-01/02**: lower priority than SL-04/05/06. SL-01 claims atomic
  persistence via pure claimed-state comparison, which
  `DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md` says cannot actually prove
  atomicity (would need an isolated-SQLite subject adapter, matching the
  pattern already used for `broker.py`/GB-01/03/04/05, to be a genuine
  proof rather than a modeled claim) -- flagged but not required to block
  this remediation pass given the narrower scope decision already
  documented for SL-01. SL-02's `stored_session_id != new_lease_id`
  invariant is not explicitly validator-enforced; low risk since the two
  values come from unrelated fixture fields in practice, but worth adding
  as an explicit check for defense in depth.

SL-01/02 remain open, non-blocking, lower priority than everything else in
this document; pick up only after CR-05, the contract.py/CR-06 fix, and
the hash-binding manifest below are done, if at all.

### contract.py outcome-taint denylist (Moderate)

`_FORBIDDEN_OUTCOME_KEYS` (line 17) is a flat global denylist that misses
schema-specific circularity: `coordination_room.py`'s CR-06 accepts
`winning_active_id` as a direct input and echoes it as the winner with no
independent derivation from contender/CAS facts -- genuine answer
injection, the exact thing `reject_outcome_claims` exists to prevent.
`session_lease.py`'s SL-06 accepting `decision` as input is NOT the same
category of defect (it is an explicitly ratified, OPEN-tagged fact
injection per the SL-01-06 classification spec's own scope decision, not a
final-answer echo) -- do not add `decision` to a blanket denylist, per
cx's own explicit caution in Round 2.

**Fix spec**: this needs a per-schema check, not a larger global word
list. For CR-06 specifically: derive the winner from the actual
contender/CAS facts (whichever contender's fencing/CAS evidence is
authoritative) rather than accepting `winning_active_id` as a fact.
`_FORBIDDEN_OUTCOME_KEYS` itself is fine to leave as a coarse first-pass
filter; the real fix is in `coordination_room.py`'s CR-06 oracle/subject
logic, not in `contract.py`.

### Ratification hash-binding gap (Critical, procedural)

Every ratification row in `RATIFICATION-PROVENANCE-INDEX-R1.md` and
`TDD-READINESS-GATE-CLOSURE-R1.md` binds design-document and status-overlay
hashes, but never the actual domain module / test / fixture source bytes
being certified. `DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md` requires oracle,
adapter, schemas, vectors, and specifications to be hash-bound together.
Nothing currently proves the reviewed-and-ACKed source bytes are the bytes
actually shipped.

**Fix spec**: after the SL/CR-05/contract.py fixes above land, generate one
canonical evidence manifest (a new JSON file, e.g.
`docs/design/phase0/fixtures/source-evidence-manifest-v1.json`) listing
raw-byte SHA-256 for every file under `tools/phase0_fixture_runner/domain/`,
every `test_*.py`, every fixture JSON, and every capture directory's
`fixture-record.json`. Bind that manifest's own hash in a new
`RATIFICATION-PROVENANCE-INDEX-R1.md` row, through the same unanimous
ag.deepthink + cx.deepthink + cc mechanism used throughout this session.
This is the actual condition-6-grade closure this cross-review's finding
#1 was asking for; `TDD-READINESS-GATE-CLOSURE-R1.md`'s existing condition-6
disposition should be superseded by (not silently replaced by) this new
row once it exists.

## Round 3 (final validation) -- not yet run

Once the above items are fixed, a final round should send both peers the
complete before/after diff and this document, asking for a clean ACK or
further findings, before folding this remediation into the ratification
index as its own row.
