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

### SL-01..06 (Critical, largest remaining item)

`SL-01-06-SESSION-LEASE-CLASSIFICATION-SPEC-R1.md`'s own ratified text
promises more than `session_lease.py` implements. Confirmed gaps (cx's
Round 2 finding, cc spot-verified SL-04 directly):

- **SL-04**: spec text (lines 119-123) promises the fixture proves
  create-L1+L2, renew-L1-leaves-L2-unchanged, **close-L2-leaves-L1-active**,
  and **stale-CAS-on-either-cannot-mutate-the-other**. The actual
  implementation only tests create+renew-L1. CONTRACT.md's own SL-04
  one-liner names "closable" explicitly -- this is not fully modeled.
  **Fix**: expand SL-04's fixture to a richer vector including a close-L2
  step (verify L1 remains untouched and still renewable) and a stale-CAS
  step (a renewal/close attempt using a stale fencing_token/revision on
  either lease must be rejected with zero mutation to both).
- **SL-05**: currently checks four owner fields
  (`owner_peer_id`/`owner_principal_id`/`owner_instance_id`/
  `owner_process_birth_identity`) but the ratified fence tuple also names
  `session_id`, `lease_id`, `fencing_token`, `revision` (8 fields total).
  The oracle also hardcodes the REJECTED literal rather than deriving it
  from comparing `requester` against `persisted` (relies entirely on the
  input validator's precondition to guarantee it's always the rejection
  branch). **Fix**: extend the fence-check to the full 8-field tuple; make
  `SessionLeaseOracle`'s SL-05 branch independently evaluate the predicate
  (compare each of the 8 fields) rather than returning a static dict.
- **SL-06**: the ratified spec's receipt schema (lines ~135+) names
  `recovery_receipt_id`, `detected_at`, `mismatch_dimensions`,
  `evidence_digest`, `policy_revision` (separate from `policy_id`), and an
  optional `external_effect_certainty` -- none of these are in the actual
  implemented receipt. Additionally, `post_revision`/`post_fencing_token`
  are accepted as pre-validated INPUT (constrained to `pre + 1` by the
  validator) and merely echoed into the output, rather than the
  oracle/subject deriving `post = pre + 1` themselves -- weaker
  independent-verification than the pattern established elsewhere (e.g.
  HR-03's post-fix forward/backward dual derivation). **Fix**: add the
  missing receipt fields as additional fact-injected/echoed inputs (closing
  the doc-vs-code gap is straightforward); separately, remove
  `post_revision`/`post_fencing_token` as direct inputs and instead compute
  them as `pre_revision + 1`/`pre_fencing_token + 1` inside the oracle AND
  independently inside the subject, so the fault (currently: adapter drops
  the pre-computed advance) becomes a fault in the adapter's own arithmetic
  instead of a dropped pass-through field.
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

**Recommended sequencing when resumed**: SL-04 and SL-05 first (both tie
directly to a CONTRACT.md MUST clause, both security/correctness relevant),
then SL-06's receipt-field completeness and derivation fix, then SL-01/02
as lower-priority hardening. After code changes: preserve old captures
under `-NARROW-V1`, regenerate real evidence, re-run the full suite, update
`fixture-status-v1.json` notes, get an ag.deepthink review.

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
