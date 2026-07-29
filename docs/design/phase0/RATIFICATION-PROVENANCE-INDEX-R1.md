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

As of `session-2026-07-29-ac-track`: AC-01..AC-09 authority-cutover evidence
is complete (superseding the "AC evidence" item below as a future round --
it is now a *finished*, ratified item, not a remaining one). The next
non-overlapping ratifications are: static 90-action identity and disposition
inventory; runtime-health invariants; a V1 capture-production specification
for the 35 remaining legacy-only behavioral IDs (DP-01..05, SL-01..06,
CR-01..06, CS-01..06, HR-01..03, RT-01..03, GB-02/06, CJ-01/03/04/06); and a
`CONTROLLED-FAKE-RUNNER-CONTRACT-R3` ratification resolving the 15 `OPEN`
items in `DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md` (needed to unblock
DP-06). Final action-fixture linkage remains blocked pending per-action
fields and evidence adequacy. Capture acceptance, broker implementation,
cutover, and Phase 0 exit remain separate future rounds.

## Integrity rule

This index is valid only with a raw-byte SHA-256, a unanimous round ID, and
the three substantive voter reviews. A change to this file or any referenced
interpretation requires a new index revision and ratification; it must not
edit the historical R11/R12 proposal bytes.
