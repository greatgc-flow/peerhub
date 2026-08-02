# Final Cross-Review Remediation R1

Status: CLOSED (2026-07-30) remediation record for the unlimited final
full cross-review requested 2026-07-30, explicitly authorized to revise
already-ratified work. Three rounds (independent ag.deepthink +
cx.deepthink audits, cross-validation, then a final Round 3 verification
pass) found and fixed 8 real defects across the session's Phase 0 work,
plus closed the ratification hash-binding gap. Only SL-01 remains open,
as a previously-accepted narrower-scope decision, not a new finding.
Folded into `RATIFICATION-PROVENANCE-INDEX-R1.md` as the
`session-2026-07-30-remediation-track` row.

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
- **CR-05**: `coordination_room.py`'s heartbeat facts now include
  `assigned_peer_principal_id`/`authenticated_principal_id` alongside the
  self-asserted `heartbeat_peer`; acceptance requires
  `heartbeat_peer == assigned_peer` AND
  `authenticated_principal_id == assigned_peer_principal_id`, mirroring
  SL-05's `requester`/`persisted` split. CR-05-NEG-01 was redefined
  (rather than adding a `-NEG-02`, per this doc's own "or a `-NEG-02`"
  alternative) to test the actually-security-relevant spoof case --
  asserted peer matches, authenticated principal does not -- since that
  fully subsumes the old blunt self-assertion-mismatch case once
  authenticated_principal_id is the real authority check. Oracle derives
  acceptance with a plain `and`-expression; subject derives it via a
  tuple-of-booleans + `all()` (structurally distinct, same independence
  discipline as SL-05).
- **CR-06 (contract.py outcome-taint denylist)**: fixed at the actual
  defect site per this doc's own prior finding -- `winning_active_id` was
  removed as a direct input fact entirely and replaced with
  `contender_fencing_tokens` (a distinct fencing token per contender,
  matching this codebase's existing fencing-token-as-authority
  convention). The oracle derives the winner via
  `sorted(...)[-1][0]`; the subject derives it independently via
  `max(..., key=...)` over the contender-ID list -- structurally
  distinct. `contract.py`'s `_FORBIDDEN_OUTCOME_KEYS` itself was left
  unchanged, as originally decided (the fix belongs in
  `coordination_room.py`'s derivation, not a bigger denylist).
  Both CR-05 and CR-06 drafted by cx.deepthink from a fully specified
  brief in one dispatch; cc verified the diff directly (validator
  exact-fields, oracle/subject independence, fixture value consistency)
  before running the suite. 262/262 tests green. Committed.
- **DP-06 capture metadata**: reviewed and NOT changed -- the raw
  `fixture-record.json`'s `runner_contract: CONTROLLED-FAKE-RUNNER-
  CONTRACT-R2` field is accurate as written; it describes the runner's
  own journal/reduce *mechanics* (unchanged by R3), not the domain-level
  classification rule (which R3 does govern, and which is already
  correctly cross-referenced in `fixture-status-v1.json`'s DP-06 note).
  No code change needed; this closes ag/cx's finding as a documentation
  clarification rather than a defect.

## Flagged, not yet fixed (precise specs below)

### CR-05, CR-06/contract.py, SL-04/05/06 -- FIXED (2026-07-30, see "Fixed and verified" above)

### SL-01/02 (low priority)

- **SL-02**: FIXED (2026-07-30) -- added an explicit
  `stored_session_id != new_lease_id` check to `_validate_sl02_inputs`
  (session_lease.py), defense-in-depth per the ratified design's own
  "`session_id != lease_id`; neither is derived from the other" rule.
  Trivial, done directly by cc (not delegated -- too small to be worth a
  peer round-trip). 262/262 tests still green; hash manifest regenerated.
- **SL-01**: intentionally left as-is. It claims atomic persistence via
  pure claimed-state comparison, which
  `DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md` says cannot actually prove
  atomicity (would need an isolated-SQLite subject adapter, matching the
  pattern already used for `broker.py`/GB-01/03/04/05, to be a genuine
  proof rather than a modeled claim). This was already an accepted,
  documented narrower-scope decision for SL-01 before this cross-review,
  not a new finding -- reopening it into a full SQLite-adapter rebuild is
  a real scope expansion, not a bug fix, so it stays open backlog rather
  than being folded into this remediation pass.

### Ratification hash-binding gap -- FIXED (2026-07-30)

`docs/design/phase0/fixtures/source-evidence-manifest-v1.json` now binds
raw-byte SHA-256 for all 336 files across 4 categories: 27 domain modules,
26 test modules, 217 fixture JSON scripts, 66 legacy capture records
(`.json` + `.transcript.json`). Generated by a small deterministic script
(`hashlib.sha256` over raw bytes) run directly by cc, NOT delegated to a
peer -- an LLM cannot reliably reproduce a SHA-256 digest, and this was
concretely demonstrated in this same fix: ag.deepthink's drafted
ratification row cited a `fixture-status-v1.json` hash copied from an
earlier row in the document rather than recomputed, which cc caught as
stale/wrong by direct recomputation before it entered the permanent
record (see `RATIFICATION-PROVENANCE-INDEX-R1.md`'s new row's provenance
note for the full account). The manifest's own hash
(`5f53f3df646835bba0f286cc41d53ae722d328b3fc24cd9b7be45e44f008b013` as of
the SL-02 regeneration below; the manifest's initial hash from the
CR-05/06 commit was `2ac5a69a...`, superseded, not silently overwritten --
see `RATIFICATION-PROVENANCE-INDEX-R1.md`'s row for both values) is
bound in a new `session-2026-07-30-source-manifest-track` row in
`RATIFICATION-PROVENANCE-INDEX-R1.md`, and `TDD-READINESS-GATE-CLOSURE-R1.md`'s
condition 6 now carries an explicit supersession note pointing at that
row (additive, not a silent rewrite). ag.deepthink ACKed the manifest's
scope and drafted the row prose; cx was not separately asked to re-verify
this specific item.

## Round 3 (final validation)

ag.deepthink ran a thorough, unhurried line-by-line pass over all 10
fixed items (RT-03, HR-03, CJ-01..06 as one item, SL-04, SL-05, SL-06,
CR-05, CR-06, SL-02, hash manifest) against the live code and this
document: **clean ACK**, no drift, no new gaps, backlog scoping and
document self-consistency both confirmed correct. One citation in ag's
own summary (item #10) quoted the manifest's hash as `2ac5a69a...` --
that was the value from the CR-05/06 commit message, already superseded
by the `5f53f3df...` regeneration after the SL-02 fix; cc verified the
row in `RATIFICATION-PROVENANCE-INDEX-R1.md` itself is correct (carries
`5f53f3df...`), so this is a stale citation in ag's response text only,
not an error in the actual ratified record.

cx.deepthink ran a lighter confirmatory pass in parallel (self-consistency
check on the SL-05/06 and CR-05/06 fixes it drafted itself, independent
check on RT-03/HR-03/CJ-01..06 via direct line citations it verified
itself, plus sampled hash checks against the manifest): **ACK on the
code, with one real documentation-only finding** -- this document's own
hash-binding-gap section (above) still cited the manifest's superseded
`2ac5a69a...` hash instead of the current `5f53f3df...` one, which
`RATIFICATION-PROVENANCE-INDEX-R1.md` already had correct. Fixed
immediately per cx's finding. cx could not re-run the test suite itself
(local `python.exe` did not start in its environment) and said so
explicitly rather than silently skipping that check -- its ACK rests on
static code/hash verification only, which is disclosed here rather than
presented as equivalent to a live test run.

Net: two independent peers, two independently-caught real staleness
issues in prose (ag's own summary text citing an outdated hash; this
document's own hash-binding section citing an outdated hash) -- zero
issues found in the actual committed code or fixtures across either
review. This remediation is folded into
`RATIFICATION-PROVENANCE-INDEX-R1.md` as its own closing row below.

## Addendum (2026-07-31): stale generated-capture-record defect, found and fixed

A separate unlimited final closure ratification round (dispatched to
decide whether to freeze Phase 0 at this state) found a real,
freeze-blocking defect that every prior round in this document missed:
`tools/phase0_fixture_runner/captures/<ID>/` holds the actual generated
V1_CAPTURE evidence (`event-script.json`, `fixture-record.json`, etc.)
for every fixture -- a directory distinct from
`docs/design/phase0/fixtures/captures/`, which holds only flat, immutable
legacy OBS-tier records. Every code/fixture fix in this remediation
(SL-01, SL-04/05/06, CR-05/06, HR-03 -- 7 base IDs, 14 with their NEG-01
pairs) updated `tools/phase0_fixture_runner/fixtures/*.json` and verified
via the live test suite (which regenerates fresh output in temp
directories on every run), but never regenerated the PERSISTED capture
directories -- so the checked-in canonical evidence for all 14 of those
fixtures still reflected the pre-fix scripts and outputs, while
`fixture-status-v1.json` claimed `SPEC_FAITHFUL` for all of them.

Found by cx.deepthink (a repository-wide script-hash comparison between
`fixtures/*.json` and each `captures/<ID>/event-script.json`), verified
directly by cc (confirmed `captures/CR-06/event-script.json` still had
`winning_active_id` instead of `contender_fencing_tokens`). ag.deepthink
independently reviewed 5 unrelated fixtures and found no equivalent
issue in them, but did not itself catch this specific defect in its own
first-pass review -- the repository-wide script-hash comparison cx ran is
what surfaced it.

**Fix**: all 14 stale capture directories preserved under `-NARROW-V1`
(matching the established pattern from the RT-03/HR-03/CJ fixes earlier
in this remediation, which correctly did this), then regenerated fresh
via `run_fixture.py` from the current fixture scripts. All 7 positives
now show `V1_CAPTURE`/`SPEC_FAITHFUL`/`PASS`; all 7 negatives show
`DOMAIN_ASSERTION_FAILED`/`FAIL`; every regenerated `event-script.json`
is now byte-identical to its source `fixtures/*.json` (spot-verified for
CR-06 directly).

**Also fixed in the same pass** (all found by cx in the same round):
- `fixture-status-v1.json`'s top-level `v1_capture_status` field was
  stale at `"NOT_CAPTURED"` despite all 54 per-ID entries correctly
  showing `V1_CAPTURE` -- corrected to `V1_CAPTURE_COMPLETE` with an
  explanatory note; per-ID entries were never wrong.
- `RATIFICATION-PROVENANCE-INDEX-R1.md`'s top-level "Status: proposed"
  line and `TDD-READINESS-GATE-CLOSURE-R1.md`'s "Status: proposed... /
  Disposition: pending" language both predated the ACK rounds those same
  documents go on to describe as completed -- corrected to reflect
  actual ratified/completed status.
- `source-evidence-manifest-v1.json` extended (schema v1 -> v2) to also
  cover `runner.py`/`run_fixture.py` and every generated
  `fixture-record.json` under `tools/phase0_fixture_runner/captures/`
  (259 records) -- v1 only covered domain/test/fixture/legacy-capture
  source, never the actual generated evidence or the entry points that
  produce it, which is exactly the class of gap this round found.

**Not fixed, named as remaining backlog rather than silently dropped**
(cx's findings, not independently re-verified item-by-item by cc beyond
confirming they are real open items in the cited files): the
"Finalized design decisions" table in `RATIFICATION-PROVENANCE-INDEX-R1.md`
interleaves prose notes between table rows, which some renderers may
show as multiple separate tables rather than one continuous table
(cosmetic, not a content error); the index still names the static
90-action inventory ratification and action-fixture linkage as
unresolved (lines ~239-244 of that file); `authority-proof-status-v1.json`
records unratified public error codes, safe-abort classification,
rollback vocabulary, and real-OS integration as open items. All were
already known open items, not newly discovered defects, but were missing
from the "here is the complete deferred backlog" framing this round used
when proposing the freeze -- named explicitly now so the freeze decision
is made with the complete list in view.

ag.deepthink and cx.deepthink both re-reviewed after these fixes; see
`RATIFICATION-PROVENANCE-INDEX-R1.md`'s `session-2026-07-31-final-closure-track`
row for the outcome.
