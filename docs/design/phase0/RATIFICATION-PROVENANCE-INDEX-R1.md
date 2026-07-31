# PeerHub Ratification Provenance Index R1

Status: proposed documentation-governance record. This index does not amend a
historical decision, authorize implementation, or convert any specification or
legacy observation into captured evidence.

## Purpose

The Phase 0 corpus contains immutable proposal/contract bytes while Hub rounds
hold final votes. This index supplies the missing durable cross-reference. A
listed decision is authoritative only for the scope stated here and in its
underlying round; a later decision may supersede it only prospectively with a
new hash-bound unanimous round.

## Finalized design decisions

| Hub round | Decision | Bound artifacts | Explicitly not authorized |
|---|---|---|---|
| `r-aec7` | Protocol v1 and authority-cutover design semantics | Hub record `068c9d1cb3fb394c32df72b4a97037e5a50e69644fc27b9b87a8960955778a24`; protocol `7bd70ba40b4489d3523216c1e33d6012a88d72f2183d56c8354d4b62834ec0f4`; cutover `b0c7e05eba10a3c948eb581135fff6150e2c5e0bf81595af4a246c6aa7b4c2db` | implementation, database creation, migration, authority cutover, provider effects |
| `r-517f` | first controlled-fake runner bootstrap | Hub record `224fbac27b0188cf718cd289afa060cb9c525c4f18ce6e92591bd858ec3e4203`; runner R2 `30d693621885b5887bbfdff470869e2e3aaab32de71e92a009c6754210d1b422` | Phase 0 exit, production features, cutover, host mutation broker |
| `r-eb81` | corrected hash-bound controlled-fake bootstrap | Hub record `cd7be8ad1026f994a30ab036acbadc6b00fc0aa42b150f132d58f2c9263a010f`; R12 `74cf9a5e0b599a72744d22b68c5bff8a64f43457093aea4eef20916d856b2a37`, including its 13-row manifest and R11-record hash `606b20bba107515d0e84d63df3926123db403a2d49e9da1bb29b777fa8ab7125` | any scope excluded by R12, including live Hub/provider work, broker, cutover, and Phase 0 exit |
| `session-2026-07-29-ac-track` (not a `hub.py consensus-propose`/`consensus-vote` round -- see note below) | AC-01..AC-09 authority-cutover proof-matrix completion (sub-fixture-family level, 78 fixtures across 9 domain-oracle modules + the composed integration scenario) + DT-01/DT-06 faithful-mapping resolution (3-round unlimited adversarial critique between ag.deepthink/cx.deepthink, cc-reconciled) | `authority-proof-status-v1.json` SHA-256 `6a7570cd93327cff8bf578be15179ad0b89f44a20910385c87dee298055522e4`; `fixture-status-v1.json` SHA-256 `55feeb000698273af05150a34693594d35869f568811b6fb05f241b3d030e186`; `DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md` SHA-256 `e414cdb13656b744a25a227b463189995235f0a3eae2c172738bb36e8b5d0120`; `AUTHORITY-PROOF-SCOPING-DECISION-R1.md` SHA-256 `6cad44a5d9c33e48da0de3dad504cadeb4f1db93829f8c357ef6d67a038bb6f7` | TDD start (`TDD-READINESS-GATE-R1.md` condition 1 remains unmet: 35 of the original 54 behavioral IDs remain `LEGACY_CAPTURE`, unchanged by this round -- DT-01/DT-06 were already `V1_CAPTURE` before this round and are not part of that 35, they only moved from `PENDING_FAITHFUL_MAPPING_REVIEW` to `SPEC_FAITHFUL`; DP-06 remains `PENDING_FAITHFUL_MAPPING_REVIEW`); Phase 0 exit; cutover execution (AC-0X umbrella IDs remain `V1_SPEC_ONLY` per the two-track design, unchanged); any `CONTROLLED-FAKE-RUNNER-CONTRACT-R3` amendment (the 15 `OPEN` items in `DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md` remain unresolved backlog) |

**Note on the `session-2026-07-29-ac-track` row's provenance mechanism**: unlike
`r-aec7`/`r-517f`/`r-eb81`, this round did not go through `hub.py
consensus-propose`/`consensus-vote` and does not have a Hub-minted round ID.
It used the same query-file `ask` + explicit ACK mechanism used for every
individual AC module review earlier in the same session: cc proposed the
exact entry text above verbatim to ag.deepthink and cx.deepthink in parallel,
both independently replied ACK with no requested wording changes, and cc
(the terminal peer for this session) recorded its own affirming review as the
third vote. This satisfies the Integrity rule's substance (three independent
reviews, hash-bound artifacts, unanimous) but not its letter (a Hub-issued
round ID). If a future session wants this formally re-minted through
`consensus-propose`/`consensus-vote`, that is additive and does not require
rewriting this row.

| `session-2026-07-29-sl-track` (not a `hub.py consensus-propose`/`consensus-vote` round -- see note below) | SL-01..SL-06 session-lease classification and implementation (2-round unlimited unanimous adversarial critique between ag.deepthink/cx.deepthink + Final Call ACK, cc-reconciled): separate Session/SessionLease records, flat lease store, SL-05 fencing on an authenticated `owner_principal_id` rather than a self-asserted `owner_peer_id`, SL-06 dedicated `lease_authority_certainty` orthogonal to `effect_certainty` | `fixture-status-v1.json` SHA-256 `8188ec8bac5ef3924a61eeff7ca8575bd900fbd805215620304818f90fc24777`; `SL-01-06-SESSION-LEASE-CLASSIFICATION-SPEC-R1.md` SHA-256 `3e6862ab388dbf7f7bae56facfc35eca5e2175cb61177b676773df236e1f8b0d` | TDD start (`TDD-READINESS-GATE-R1.md` condition 1 remains unmet: this round moves SL-01..06 out of the 35 `LEGACY_CAPTURE` behavioral IDs; combined with prior rounds this session that already resolved DP-01..05/CR-01..06/CS-01..06/RT-01..03/GB-02/06/CJ-01/03/04/06, only HR-01..03 (3 IDs) remain `LEGACY_CAPTURE`); Phase 0 exit; a general SL-06 recovery policy engine (only one concrete trigger/decision pair was fact-injected, per the ratified OPEN backlog) |

**Note on the `session-2026-07-29-sl-track` row's provenance mechanism**: same
mechanism as `session-2026-07-29-ac-track` above -- no Hub-minted round ID;
cc proposed the design synthesis to ag.deepthink and cx.deepthink in
parallel, both independently ACKed (cx with two narrow wording
clarifications, incorporated into the bound spec doc before this row was
written), and cc recorded its own affirming review plus the implementation
or verification as the third vote.

| `session-2026-07-29-hr-track` (not a `hub.py consensus-propose`/`consensus-vote` round -- see note below) | HR-01..HR-03 health-recovery classification and implementation (2-round unlimited unanimous adversarial critique between ag.deepthink/cx.deepthink + 2-part Final Call ACK, cc-reconciled): resolved a document-vs-document scope conflict between CONTRACT.md's one-fixture-per-ID allocation and RUNTIME-HEALTH-SEMANTICS-R1.md's ten-item required-fixture list; HR-03 built as an 8-row scenario matrix under one ID with oracle-derived (never fixture-injected) classification and short-circuit checks; HR-02 folds in the previously-uncovered "revalidation unsupported by an adapter" item as its own OBS-grounded positive case; cc independently verified against the actual committed code (correcting an incorrect peer claim) that HR-05/HR-06 already cover 2 of the 3 required items neither peer's first pass had fully allocated | `fixture-status-v1.json` SHA-256 `6676adef506cb57dd064c914d3cb54233f94bd6ba6e66d73479186f49b390a78`; `HR-01-03-HEALTH-RECOVERY-CLASSIFICATION-SPEC-R1.md` SHA-256 `4b805385daacd785e8d58e9d49eeaae695d35d007072312ab14ed50543428f91` | TDD start (`TDD-READINESS-GATE-R1.md` condition 1 requires all 54 contract IDs at `V1_CAPTURE`; this round completes the last 3 of the original 35 `LEGACY_CAPTURE`-only behavioral IDs, so all 54 entries in `fixture-status-v1.json` now show `evidence_status: V1_CAPTURE` -- **but DP-06 alone, already `V1_CAPTURE` before this session started, still carries `coverage_scope: PENDING_FAITHFUL_MAPPING_REVIEW` and `phase0_exit_eligible: false` per the unrelated, still-open `DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md` finding -- this round neither touches nor resolves that gap, and condition 1's substance is not met until a future `CONTROLLED-FAKE-RUNNER-CONTRACT-R3` ratification closes it**); Phase 0 exit; a general HR-03 failure-class-to-policy-action mapping (explicit OPEN backlog, no HR-03 row asserts one) |

**Note on the `session-2026-07-29-hr-track` row's provenance mechanism**: same
mechanism as `session-2026-07-29-ac-track`/`session-2026-07-29-sl-track`
above -- no Hub-minted round ID; cc proposed the design synthesis to
ag.deepthink and cx.deepthink in parallel across two Final Call rounds (the
second incorporating three concrete schema corrections cx raised on the
first), both independently and unconditionally ACKed the corrected design,
and cc recorded its own affirming review plus the implementation and direct
code verification as the third vote.

| `session-2026-07-30-dp06-r3-track` (not a `hub.py consensus-propose`/`consensus-vote` round -- see note below) | `CONTROLLED-FAKE-RUNNER-CONTRACT-R3.md` ratified, resolving R3 backlog item #1 (DP-06's dispatch-boundary ambiguity) via an independent-verification round (not re-deriving from R2 alone, but cross-referencing fixtures/CONTRACT.md's own DP-06 line, ARCHITECTURE.md, the retained V1-CONTROLLED-FAKE-CONFORMANCE-SPEC-R1.md, and PROTOCOL-V1-FREEZE.md, all independently verified by cc against the actual files): durable journal append of `INTENT_PERSISTED` (not reduction) is the dispatch-intent replay-safety boundary; DP-06 implemented in a new `dispatch_pipe_recovery.py` module, original real mechanical capture preserved under `captures/DP-06-NARROW-V1/`. DP-06 moves `coverage_scope` from `PENDING_FAITHFUL_MAPPING_REVIEW` to `SPEC_FAITHFUL` -- **all 54 contract IDs in `fixture-status-v1.json` now show `coverage_scope: SPEC_FAITHFUL`**, closing the gap the `session-2026-07-29-hr-track` row above left open | `fixture-status-v1.json` SHA-256 `ad489af35b978b7a1b5c60c9749ec315449d66e172edd9b498e49a01c192eead`; `CONTROLLED-FAKE-RUNNER-CONTRACT-R3.md` SHA-256 `b784623ff070da0e553164a4bb9f896e4eab0d49e41d3acc724f9dc2c4c602f3` | TDD start (`TDD-READINESS-GATE-R1.md` condition 1's `V1_CAPTURE`/`SPEC_FAITHFUL` substance is now met for all 54 IDs, but conditions 2-3 -- the controlled-fake runner contract freeze and R3 health-implementation-test adoption -- are separate, unverified-by-this-round claims); Phase 0 exit; cutover; R3 backlog items #2-15 (out-of-order stream events, terminal-event precedence, `TREE_STATE`/`CANCEL_ACK` schemas, idempotency binding schema, unterminated-script classification, parse/version/schema-negotiation failure classification, exit-code domain, clock validation, full enum closure, `MAY_HAVE_STARTED` vs `UNKNOWN` distinction, multiple `CLEANUP_ERROR` handling, cleanup-degrading-success, identity-reuse handling -- all remain open backlog, genuinely independent of DP-06) |

**Note on the `session-2026-07-30-dp06-r3-track` row's provenance mechanism**:
same mechanism as the `-ac-track`/`-sl-track`/`-hr-track` rows above -- no
Hub-minted round ID; cc proposed the R3 amendment text to ag.deepthink and
cx.deepthink for Final Call ACK after an independent-verification round
where both peers confirmed the same substantive conclusion via different
argument paths (ag: architectural precedent alone suffices; cx: the
substantive answer is correct but requires an explicit R3 rule, not just
citing precedent -- cc adopted cx's procedural position while agreeing with
ag's substance), both unconditionally ACKed the final amendment text, and cc
recorded its own affirming review plus the implementation and direct
citation verification (every cited passage checked against the actual files
before being relied on) as the third vote.

| `session-2026-07-30-gate-closure-track` (not a `hub.py consensus-propose`/`consensus-vote` round -- see note below) | `TDD-READINESS-GATE-CLOSURE-R1.md` ratified: independent verification of all 6 `TDD-READINESS-GATE-R1.md` conditions against the actual repository state. Condition 1 (all 54 fixtures `V1_CAPTURE`/`SPEC_FAITHFUL`) and condition 5 (AC proof-matrix: 9 umbrella IDs `V1_SPEC_ONLY`, 52 `AC-0X-YY` children + 4 `AC-COMPOSED-0N` scenarios `V1_CAPTURE`, cutover gated) confirmed MET. Condition 2 (runner isolation, append-before-reduce, canonical transcript, deterministic clock/IDs, no provider) confirmed against the actual `runner.py` source. Conditions 3-4 confirmed as "Phase 0 evidence ready," explicitly not claimed as literally true pre-TDD since no real source test suite exists yet. An independent Fable-5 review found no discrepancies; cx.deepthink's independent review caught three real inaccuracies in the first draft that neither ag.deepthink nor the Fable-5 review caught (an overclaimed "host-only receipt minting" property contradicted by HR-04's own fact-injected `clearance_receipt` input; a false claim that `REVISION_CONFLICT`/`EPOCH_STALE` appear in `authority_fence.py`, when only `CUTOVER_EPOCH_CONTENDED` does; and an imprecise "56 child sub-fixtures" count that conflates 52 true children with 4 distinct `AC-COMPOSED` scenarios) plus a self-contradiction cc introduced while fixing the first two. All corrected and independently re-verified line-by-line by both ag.deepthink and cx.deepthink against the current file on disk | `fixture-status-v1.json` SHA-256 `ad489af35b978b7a1b5c60c9749ec315449d66e172edd9b498e49a01c192eead`; `authority-proof-status-v1.json` SHA-256 `6a7570cd93327cff8bf578be15179ad0b89f44a20910385c87dee298055522e4`; `TDD-READINESS-GATE-CLOSURE-R1.md` SHA-256 `d2eab2adcef08d76b305c6e128dd6d02d9c72e351413cf8fd3439b7c282fcc20` | TDD start (explicitly: conditions 3-4 require a real source test suite that does not yet exist; this closure certifies the Phase 0 evidence prerequisite, not the future tests themselves); Phase 0 exit; cutover execution; R3 backlog items #2-15 |

**Note on the `session-2026-07-30-gate-closure-track` row's provenance
mechanism**: same mechanism as the `-ac-track`/`-sl-track`/`-hr-track`/
`-dp06-r3-track` rows above -- no Hub-minted round ID; cc drafted the
closure document and dispatched it to ag.deepthink, cx.deepthink, and an
independently-spawned Fable-5 agent for verification (per the standing
collaboration protocol naming `cc.fable` as a required voice for
final-Phase-0-scope rounds); cx.deepthink's finding of three real
inaccuracies (and a fourth self-contradiction introduced mid-correction)
went through two further correction-and-re-verify cycles, including one
round where cx's own review response appeared to reference a stale cached
read of the file and required an explicit fresh-read request before
confirming; cc recorded its own affirming review, direct code inspection,
and the implementation as the third vote alongside the unanimous peer ACKs.

**Post-ACK correction**: the entry text ag/cx ACK'd originally said "32 of
the original 54" and "32 remaining legacy-only behavioral IDs." This was a
counting error -- DT-01/DT-06 were never part of the 35-fixture
`LEGACY_CAPTURE` bucket (they were already `V1_CAPTURE`, just
`PENDING_FAITHFUL_MAPPING_REVIEW`); resolving them does not shrink that
bucket. The correct count is 35, unchanged by this round. Fixed here along
with the same typo in `DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md` (whose bound
SHA-256 above was updated accordingly). This is a mechanical arithmetic fix,
not a change to what was actually ratified (AC-track completion and
DT-01/DT-06 resolution are unaffected either way), so it was not re-routed
through a fresh ACK round.

**Post-ACK correction (2026-07-30, found during the final full cross-review)**:
the `session-2026-07-29-ac-track` entry text says "78 fixtures across 9
domain-oracle modules." The actual `authority-proof-status-v1.json` corpus
is 56 positive evidence scripts (52 `AC-0X-YY` children + 4 `AC-COMPOSED-0N`
composed scenarios) and 109 total scripts including negatives. "78" does
not match any real count in the corpus and its origin is unclear. This is a
mechanical correction to prose describing an already-ratified, unchanged
fixture corpus (the underlying AC-track completion is unaffected), not a
new ratification, so it was not re-routed through a fresh ACK round.

| `session-2026-07-30-source-manifest-track` (not a `hub.py consensus-propose`/`consensus-vote` round -- see note below) | Source implementation, test suite, and fixture artifact SHA-256 evidence manifest (`docs/design/phase0/fixtures/source-evidence-manifest-v1.json`) ratified: closes the procedural gap where prior ratification rows bound only design documents and status overlays but never the actual domain module source bytes (`tools/phase0_fixture_runner/domain/*.py`, 27 files), test files (`tools/phase0_fixture_runner/test_*.py`, 26 files), fixture JSON vectors (`tools/phase0_fixture_runner/fixtures/*.json`, 217 files), and legacy capture records (`docs/design/phase0/fixtures/captures/*.json` including `.transcript.json`, 66 files) -- 336 files total, each bound by a deterministically computed (`hashlib.sha256` over raw file bytes, not LLM-estimated) raw-byte SHA-256. Supersedes the condition-6 disposition in `TDD-READINESS-GATE-CLOSURE-R1.md` | `source-evidence-manifest-v1.json` SHA-256 `471ef229290956765afa7a8b1ac82885a043496e79603bf4a96368dc3bce797a` (regenerated 2026-07-31 after the SL-01 SQLite atomicity implementation, superseding `728e8e4c...`) (regenerated 2026-07-31 after the HR-03 policy-action extension below; supersedes `5f53f3df...` (post-SL-02) and the manifest's initial `2ac5a69a...` hash (CR-05/06 commit), same file identity, no scope change) | TDD start; Phase 0 exit; cutover execution; R3 backlog items #2-15; SL-01/02 hardening (still open, see `FINAL-CROSS-REVIEW-REMEDIATION-R1.md`) |

**Note on the `session-2026-07-30-source-manifest-track` row's provenance
mechanism**: same mechanism as the `-ac-track`/`-sl-track`/`-hr-track`/
`-dp06-r3-track`/`-gate-closure-track` rows above -- no Hub-minted round
ID; cc generated the manifest via a small deterministic script (peers were
not asked to compute hashes themselves, since an LLM cannot reliably
reproduce a raw SHA-256 digest) and dispatched the binding-row draft
request to ag.deepthink, which independently ACKed the manifest's scope
and drafted this row's prose. ag's first draft cited a `fixture-status-
v1.json` SHA-256 copied from an earlier row in this document rather than
recomputed against the file's current (session-mutated) content; cc
caught this by independently recomputing the hash (`88b4c5aa...`, not
`ad489af3...`) and removed that citation from this row entirely rather
than including a stale value -- `fixture-status-v1.json`'s current hash is
not bound by this row and remains only informally described in the
`fixture-status-v1.json` notes it carries. cc's own script execution and
direct verification serve as the third vote alongside ag's ACK; cx was not
separately asked to re-verify this specific row.

| `session-2026-07-30-remediation-track` (not a `hub.py consensus-propose`/`consensus-vote` round -- see note below) | Closes the unlimited final full cross-review requested 2026-07-30 (explicitly authorized to revise already-ratified work). 8 real defects found and fixed: RT-03 ABSENT/UNAVAILABLE mislabeling, HR-03 oracle/subject independence violation (+ `coverage_scope` downgrade to `PENDING_FAITHFUL_MAPPING_REVIEW`), CJ-01..06 non-canonical error codes, SL-04 missing close/stale-CAS coverage, SL-05 narrow 4-field fence check + hardcoded oracle, SL-06 missing receipt fields + non-derived post-state, CR-05 self-asserted-identity trust bug, CR-06 answer-injection bug -- plus SL-02 defense-in-depth hardening and the source-evidence hash-binding manifest (previous row). Full account, fix specs, and Round 3 final-verification results (ag.deepthink thorough pass: clean ACK; cx.deepthink lighter pass: ACK on code, caught one real stale-hash citation in the remediation doc's own prose, corrected) in `FINAL-CROSS-REVIEW-REMEDIATION-R1.md`. SL-01 remains open as a pre-existing, previously-accepted narrower-scope decision, not a new finding | `FINAL-CROSS-REVIEW-REMEDIATION-R1.md` SHA-256 to be computed at commit time and is itself covered by `source-evidence-manifest-v1.json`'s successor generation, not this row directly (the manifest binds source/test/fixture bytes, not this narrative doc) | TDD start; Phase 0 exit; cutover execution; R3 backlog items #2-15; SL-01 hardening (still open) |

**Note on the `session-2026-07-30-remediation-track` row's provenance
mechanism**: same mechanism as all `-track` rows above -- no Hub-minted
round ID. cc ran the full 3-round cross-review + fix cycle across five
commits (`7a0bf02`, `560352c`, `2e88547`, `7e33b3d`, `98bc92d`), delegating
code drafting to cx.deepthink (SL-05/06, CR-05/06) from fully specified
briefs and independently verifying every draft against the live files
before applying it, then dispatched a genuinely thorough Round 3
verification to ag.deepthink (primary, unhurried, line-by-line) and a
lighter confirmatory pass to cx.deepthink in parallel. Both returned ACKs
on all code; both rounds also independently demonstrated why hash/line
citations from either peer must be re-verified rather than trusted --
ag's Round 3 summary and this document's own hash-binding-gap section
each cited a since-superseded manifest hash, both caught by direct
recomputation (ag's by cc, this document's by cx) before being left in
the permanent record. cc's own direct code verification across every
fixed item serves as the third vote alongside both peers' ACKs.

| `session-2026-07-31-hr03-policy-action-track` (not a `hub.py consensus-propose`/`consensus-vote` round -- see note below) | `HR-03-POLICY-ACTION-EXTENSION-R1.md` ratified: closes the sole remaining non-`SPEC_FAITHFUL` gap left by the `-remediation-track` row above. Re-investigation found the original HR-01-03 classification round had missed two directly relevant frozen docs (`RUNTIME-HEALTH-RECOVERY-DECISIONS-2026-07-28.md`, `RUNTIME-HEALTH-RECOVERY-ADDENDUM-R3-2026-07-28.md`) plus `health.py`'s already-shipped circuit/authority vocabulary -- the same class of gap as DP-06/CJ/RT-03. A 2-round adversarial critique (ag proposed a total classification->scope lookup; cx argued this over-claims conditional grounding and proposed fact-injected `evidence_subject` instead; ag fully conceded after cc confirmed by direct `health.py` inspection that ag's field-name and receipt-shape citations were wrong and cx's were right) converged on a `policy_action` schema (`scope`/`subject`/`circuit_state`/`quarantine_authority_class`/`receipt`) fact-derived per row, independently computed by oracle and subject via structurally distinct code. `fixture-status-v1.json`'s HR-03 entry returns to `coverage_scope: SPEC_FAITHFUL` -- **all 54 contract IDs are SPEC_FAITHFUL again**, closing the gap the `-remediation-track` row left open. Universal classification-level scope for 4 of 7 classifications remains explicit OPEN backlog | `fixture-status-v1.json` SHA-256 to be recomputed at commit time (not pre-bound here, per the same discipline that avoided binding a stale value in the prior row); `source-evidence-manifest-v1.json` SHA-256 `471ef229290956765afa7a8b1ac82885a043496e79603bf4a96368dc3bce797a` (regenerated 2026-07-31 after the SL-01 SQLite atomicity implementation, superseding `728e8e4c...`); `HR-03-POLICY-ACTION-EXTENSION-R1.md` SHA-256 to be computed at commit time | TDD start; Phase 0 exit; cutover execution; R3 backlog items #2-15; SL-01 hardening; HR-03's own remaining classification-scope OPEN backlog (items 1-4 in `HR-03-POLICY-ACTION-EXTENSION-R1.md`) |

**Note on the `session-2026-07-31-hr03-policy-action-track` row's
provenance mechanism**: same mechanism as all `-track` rows above -- no
Hub-minted round ID. cc dispatched Round 1 (independent drafts) to
ag.deepthink and cx.deepthink in fresh sessions, Round 2 (cross-critique,
with cc's own direct `health.py` verification injected as settled fact
rather than left for peers to re-derive) to both, and a Final Call
synthesis to both for ACK -- both gave unconditional ACK with no further
findings. cc implemented the code directly (not delegated, given the
design was now fully specified) and independently verified via the full
264-test suite (262 baseline + 2 new targeted unit tests proving the
no-hardcoding/non-escalation property) before committing. cc's own
implementation and verification serve as the third vote alongside both
peers' Final Call ACKs.

| `session-2026-07-31-sl01-sqlite-track` (not a `hub.py consensus-propose`/`consensus-vote` round -- see note below) | `SL-01-SQLITE-ATOMICITY-R1.md` ratified: SL-01's atomicity claim, previously proven only via pure claimed-state comparison (flagged during the `-remediation-track` round as unable to actually prove atomicity per `DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md`), now uses a real isolated SQLite transaction (BEGIN/INSERT session/INSERT lease/COMMIT) matching GB-01's already-ratified pattern in `broker.py`, with a direct unit test exercising both the real rollback path (zero rows persist) and a simulated broken-adapter partial-commit path (1 session row, 0 lease rows) against the actual SQLite file. Drafted by cx.deepthink from a single fully-specified brief (not a 2-round adversarial critique -- this applies an already-ratified pattern to a new module, not a genuinely ambiguous design question), independently verified by cc, reviewed by ag.deepthink (clean ACK). This closes the last item in the `-remediation-track` row's backlog; SL-01/02 no longer have any open items | `source-evidence-manifest-v1.json` SHA-256 `471ef229290956765afa7a8b1ac82885a043496e79603bf4a96368dc3bce797a`; `SL-01-SQLITE-ATOMICITY-R1.md` SHA-256 to be computed at commit time | TDD start; Phase 0 exit; cutover execution; R3 backlog items #2-15 |

**Note on the `session-2026-07-31-sl01-sqlite-track` row's provenance
mechanism**: same mechanism as all `-track` rows above -- no Hub-minted
round ID. cc dispatched a single comprehensive implementation brief to
cx.deepthink (citing GB-01's exact pattern as the template), applied and
independently verified the draft (transaction structure, class
inheritance, oracle purity, forbidden-imports scoping) before running the
suite, then dispatched to ag.deepthink for review -- clean ACK. cc's own
implementation and verification serve as the third vote alongside ag's
ACK; cx was not separately asked to re-verify its own draft a second
time.

## Binding interpretations

1. R11 did not bind the R11 decision-record document itself; R12 did so. The
   R11 crosswalk-hash transcription defect remains historical. R12 binds the
   measured crosswalk bytes prospectively; neither record is rewritten.
2. R12 ratifies only the status-validator *semantics*, the AC namespace
   separation, and the controlled-fake bootstrap boundary. It does not ratify
   an implementation result, fixture capture, action-policy CSV, or cutover.
3. A `LEGACY_CAPTURE` or `V1_SPEC_ONLY` item is non-exit evidence. The original
   54 behavioral IDs require verified `V1_CAPTURE` records for behavioral
   Phase 0 exit. AC-01..AC-09 are separate cutover prerequisites.
4. A draft label in a bound design document does not create an implementation
   authorization. Conversely, a final Hub vote does not erase an explicit
   evidence or authority gate in the bound document.

## Protocol v1 freeze metadata completion

This proposed index supplies an additive companion record for Protocol v1 §1;
it does not edit the protocol body or change `r-aec7`:

| Field | Value |
|---|---|
| `document_id` | `5c3297fb-3c01-4c56-90b1-c5daf6fc9178` |
| protocol SHA-256 | `7bd70ba40b4489d3523216c1e33d6012a88d72f2183d56c8354d4b62834ec0f4` |
| protocol / schema | `1.0` / `1.0.0` |
| round | `r-aec7`, 2026-07-28, electorate `cc, ag, cx`, finalized unanimous |
| architecture SHA-256 | `02cf1956bbcb736e502c71298b0ea815148dbbdb726b8bdd035a3ea9e1a9441c` |
| compatibility SHA-256 | `687ec7bb6609566fc909a4fe26d75220bb9843aa7f1de9b183c1424fcfa09a9e` |
| fixture-contract SHA-256 | `a7580d75dd6daed02f6f77309e669975c258bd8a1992de685c5186defa1496ee` |

## Remaining decision chain

As of `session-2026-07-29-hr-track` (following `-ac-track` and `-sl-track`
earlier the same session): AC-01..AC-09 authority-cutover evidence and the
V1 capture-production specification for all 35 originally legacy-only
behavioral IDs (DP-01..05, SL-01..06, CR-01..06, CS-01..06, HR-01..03,
RT-01..03, GB-02/06, CJ-01/03/04/06) are both complete -- superseding both
as future rounds below; they are now *finished*, ratified items. All 54
total contract IDs in `fixture-status-v1.json` show `evidence_status:
V1_CAPTURE`. ag.deepthink and cx.deepthink both independently ACKed this
milestone characterization (`session-2026-07-29-final-ratify`, same
query-file-ask-plus-ACK mechanism, cc's own review as the third vote); no
Hub-minted round ID.

**Superseded by `session-2026-07-30-dp06-r3-track` and
`session-2026-07-30-gate-closure-track`** (both below): `CONTROLLED-FAKE-
RUNNER-CONTRACT-R3.md` has since been ratified, resolving backlog item #1
only (the DP-06 dispatch-boundary ambiguity) -- it was never scoped to
resolve all 15 `OPEN` items in `DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md`,
only item #1; items #2-15 remain genuinely open. DP-06 now carries
`coverage_scope: SPEC_FAITHFUL`. `TDD-READINESS-GATE-CLOSURE-R1.md`
independently verified all 6 `TDD-READINESS-GATE-R1.md` conditions against
the actual repository state (see that row below) -- conditions 1, 2, and 5
are MET; conditions 3-4 are "Phase 0 evidence ready" pending a real source
test suite that does not yet exist. A further `session-2026-07-30-full-
audit-track` round (see below) found and is remediating several real
implementation-fidelity defects surfaced by an unlimited final cross-review
requested explicitly to allow revising already-ratified work.

The next non-overlapping ratifications are: static 90-action identity and
disposition inventory; the full-audit remediation's own binding round (a
new comprehensive evidence manifest covering source code, not just design
docs and status overlays -- see the full-audit-track row's finding #1).
Final action-fixture linkage remains blocked pending per-action fields and
evidence adequacy. Capture acceptance, broker implementation, cutover, and
Phase 0 exit remain separate future rounds.

## Integrity rule

This index is valid only with a raw-byte SHA-256, a unanimous round ID, and
the three substantive voter reviews. A change to this file or any referenced
interpretation requires a new index revision and ratification; it must not
edit the historical R11/R12 proposal bytes.
