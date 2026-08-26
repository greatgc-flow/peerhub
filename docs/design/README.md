# docs/design/ — Index

71 files. No prior master index existed (found during the 2026-08-24
interface/MECE audit — see `INTERFACE-MECE-AESTHETIC-AUDIT-2026-08-24.md`).
This index groups by effort/era from filename/title alone; it does NOT
re-verify each doc's current accuracy or staleness (several older docs
are known-superseded by later phases — check each doc's own header before
trusting it as current, the same caution `HUB-REPLACEMENT-ROADMAP-
2026-08-09.md` needed).

## 2026-08-23/24 — hub.py → peerhub replacement effort (current, active)

Start here: **`HUB-REPLACEMENT-DESIGN-REINFORCEMENT-INDEX-2026-08-24.md`**
(the index for this cluster specifically — points to the critical
`LEGACY_CATALOG` finding and all 7 gap-category design docs).

- `HUB-REPLACEMENT-GAP-AUDIT-2026-08-23.md` — original gap audit (peerhub cannot replace hub.py today, 5 CLI commands vs hub.py's 80+)
- `HUB-REPLACEMENT-CRITICAL-FINDING-LEGACY-CATALOG-2026-08-24.md` — ★ `LEGACY_CATALOG` maps ~90 legacy actions to real target names
- `HUB-REPLACEMENT-REAL-SOURCE-GROUNDTRUTH-2026-08-24.md` — real peerhub module map, ground truth for reconciling gaps 1-7
- `HUB-REPLACEMENT-REAL-CLI-AND-TUI-2026-08-24.md` — real CLI surface + TUI/diagnostics design
- `HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md` — **★ final closure, read this first**: all 53 remaining open items ratified/decided, TDD-ready verdict
- `HUB-REPLACEMENT-GAP1-COMPAT-STRATEGY-2026-08-24.md` through `GAP7-DIAGNOSTICS-2026-08-24.md` — the 7 blocking-category designs
- `HUB-REPLACEMENT-ROADMAP-2026-08-09.md` — older roadmap, flagged partially stale, kept for phase-sequencing history only
- `INTERFACE-MECE-AESTHETIC-AUDIT-2026-08-24.md` — separate but related: cross-CLI (`_sys/cli/`) consistency/robustness audit, 5 real bugs found and fixed

## Phase 0 — compatibility inventory (earliest)

- `PHASE0-COMPATIBILITY.md`

## Phase 1 — the largest cluster (2026-08-19 through 2026-08-21), architecture consolidation + shim registry

- `PHASE1-KICKOFF-R1.md`
- `PHASE1-AUTODETECT-SIDECAR-2026-08-19.md`, `-V2-2026-08-20.md`
- `PHASE1-TEST-TAXONOMY-2026-08-19.md`, `-V2-2026-08-20.md`, `-V3-2026-08-20.md`
- `PHASE1-MANIFEST-SCHEMA-V1-2026-08-20.md`, `-V2-2026-08-20.md`
- `PHASE1-ADMISSION-RECEIPTS-REAL-2026-08-20.md`
- `PHASE1-PROMOTION-SCHEMA-V1-2026-08-20.md` (66-round-ratified; has a known errata note re: item A's rename)
- `PHASE1-CAPABILITY-CROSSWALK-CLI-2026-08-20.md`, `-CORE-2026-08-20.md`
- `PHASE1-PARITY-LEDGER-BATCH1` through `BATCH5-2026-08-20.md` (90 actions total, 18/batch)
- `PHASE1-ENGRAM-BRIDGE-INTERFACES-V1-2026-08-20.md`, `-V2-2026-08-20.md`
- `PHASE1-THIRDPARTY-DEFERRAL-AND-SHIMS-2026-08-20.md` (has an errata note re: item C's supersession)
- `PHASE1-CX-COUNTERCRITIQUE-ROUND1/2/4-2026-08-20.md`
- `PHASE1-ARCHITECTURE-CONSOLIDATION-2026-08-21.md` — **items A/B/C/D, the 57-round item-C shim-registry state machine, Rounds A-D follow-up fixes** (the single largest, most-reviewed design in this directory)
- `PHASE1-PROCESS-BACKLOG-2026-08-20.md` — the complete round-by-round history for the above (146+ rounds, then Rounds A-D)

## Phase 3 — dispatch loop (2026-08-12 to 08-14)

- `PHASE3-DISPATCH-LOOP-CONTRACT-DESIGN-2026-08-12.md`
- `PHASE3-T1-INCREMENT5-RETRY-LOOP-DESIGN-R1-2026-08-13.md`
- `PHASE3-T1-INCREMENT5B-AUTHORIZATION-PLAN-2026-08-13.md`
- `PHASE3-T1-INCREMENT5C-OUTER-LOOP-PLAN-2026-08-14.md`

## Phase 4 — shadow validation

- `PHASE4-SCOPING-MEMO-2026-08-17.md`

## Slice 3/4/5 — earlier dispatch/health/routing vertical slices (2026-08-04 era)

- `SLICE3-KICKOFF-R1.md`, `SLICE4-KICKOFF-R1.md`, `SLICE5-KICKOFF-R1.md`, `SLICE5-NEXT-STEPS-2026-08-04.md`

## Standalone architecture / infrastructure docs (various dates)

- `ARCHITECTURE.md` — peerhub target architecture (v1, pre-TDD)
- `ATOMICITY-MATRIX-2026-08-06.md`
- `BACKLOG-CONSOLIDATED-2026-08-16.md`
- `CAPABILITY-LEASE-DESIGN-2026-08-08.md` + `-ERRATA.md`
- `EVIDENCE-ARTIFACT-DESIGN-2026-08-16.md`
- `FACT-REFRESH-PROCEDURE-R1.md`
- `HEALTH-QUOTA-TRACKING-DESIGN-2026-08-16.md`
- `MIGRATION-STATUS-2026-08-06.md`
- `OSS-ADOPTION-STRATEGY-2026-08-15.md`
- `OUTBOX-SPLIT-PROGRESS-2026-08-09.md`
- `OVERNIGHT-INFRA-LESSONS-2026-08-10.md`
- `peerhub-architecture-debate.md`
- `PEERHUB-CODEX-SUBST-SANDBOX-CONFLICT-2026-08-21.md`
- `PEERHUB-MULTIPEER-BROADCAST-DESIGN-2026-08-11.md`
- `PEERHUB-P-DRIVE-ISOLATION-2026-08-09.md`
- `SESSION-SUMMARY-2026-08-07-tier2.md`
- `STAGE3-ADAPTER-SCOPING-2026-08-08.md`
- `TDD-READINESS-GATE-R1.md`, `TDD-READINESS-INVENTORY-R1.md`
- `TRACEABILITY-CONVENTION-R1.md`
- `WINDOWS-BROKERED-REDUCERS-DESIGN-2026-08-16.md`

## Maintenance note

This index was built 2026-08-24 from filenames + first-line titles only
(a mechanical scan, not a re-read of each doc's content or a staleness
audit). If you add a new design doc, add one line here. If you determine
an older doc is fully superseded, mark it explicitly in its own header
(matching the pattern `HUB-REPLACEMENT-ROADMAP-2026-08-09.md` and
`PHASE1-ARCHITECTURE-CONSOLIDATION-2026-08-21.md`'s Section D already
use) rather than deleting it — this project's own convention is
supersede-and-point, never delete history.
