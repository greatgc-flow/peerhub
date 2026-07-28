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

The next non-overlapping ratifications are: static 90-action identity and
disposition inventory; runtime-health invariants; and a V1 capture-production
specification for the 35 legacy-only behavioral IDs. Final action-fixture
linkage remains blocked pending per-action fields and evidence adequacy.
Capture acceptance, broker implementation, AC evidence, cutover, and Phase 0
exit remain separate future rounds.

## Integrity rule

This index is valid only with a raw-byte SHA-256, a unanimous round ID, and
the three substantive voter reviews. A change to this file or any referenced
interpretation requires a new index revision and ratification; it must not
edit the historical R11/R12 proposal bytes.
