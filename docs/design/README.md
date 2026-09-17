# docs/design/ — Index

This index categorizes the 34 documents in this directory into two tiers based on their current authority and relevance. Tier C's 62 older drafts/debates, and the separate closed `phase0/` corpus (133 files, own index), were physically relocated to `../history/design/2026-07/`, `../history/design/2026-08/`, and `../history/design/phase0/` on 2026-09-14 (section 5.3 of `peerhub-holistic-renewal-RATIFIED-cx-astra-2026-09-13.md`) and are kept below only as a dated index into their new location -- every real repo-wide reference to their old `docs/design/` path was updated in the same change.
Never delete history; if a document is superseded, mark it in its own header and point to the successor.

## Tier A: Current implementation/status source of truth

- `PEERHUB-BACKLOG-2026-08-27.md` — The consolidated active backlog and true current status.
- `HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md` — Real implementation status, what's actually built and tested vs. still missing.

## Tier B: Ratified design authority

- `HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md` — Final design closure for pre-TDD phase.
- `HUB-REPLACEMENT-GAP5-HEALTH-CLUSTER-2026-08-30.md`
- `HUB-REPLACEMENT-GAP6-CAPABILITY-MATCHING-2026-08-30.md`
- `HUB-REPLACEMENT-GAP7-HEALTH-FRESHNESS-AND-EVIDENCE-PRODUCER-2026-08-30.md`
- `HUB-REPLACEMENT-GAP8-LESSON-INJECT-2026-08-30.md`
- `PHASE1-MANIFEST-SCHEMA-V2-PRELIM-SECURITY-REVIEW-2026-09-04.md`
- `PHASE1-MANIFEST-SCHEMA-V2-FINAL-SECURITY-REVIEW-2026-09-05.md`
- `PHASE2-ENGRAM-SEPARATION-MASTER-PLAN-2026-09-02.md`
- `PEERHUB-LEAK-SAFETY-AUDIT-2026-08-31.md`
- `peer-permissions.md`
- `SESSION-LESSONS-INDEX-2026-09-02.md`
- `HUB-REPLACEMENT-DESIGN-REINFORCEMENT-INDEX-2026-08-24.md`
- `HUB-REPLACEMENT-GAP1-COMPAT-STRATEGY-2026-08-24.md`
- `HUB-REPLACEMENT-GAP2-CONSENSUS-2026-08-24.md`
- `HUB-REPLACEMENT-GAP3-SESSION-CONTINUITY-2026-08-24.md`
- `HUB-REPLACEMENT-GAP4-HEALTH-LEADERSHIP-2026-08-24.md`
- `HUB-REPLACEMENT-GAP5-TASK-LIFECYCLE-2026-08-24.md`
- `HUB-REPLACEMENT-GAP6-GOVERNANCE-2026-08-24.md`
- `HUB-REPLACEMENT-GAP7-DIAGNOSTICS-2026-08-24.md`
- `HUB-REPLACEMENT-REAL-CLI-AND-TUI-2026-08-24.md`
- `dotdir-consolidation-RATIFIED-2026-09-09.md` — Final ratification for the `.engram`/`.peerhub` config-consolidation backlog (both PeerHub and Engram); user-facing reference: `../config-hierarchy.md`.
- `dotdir-consolidation-peerhub-proposal-2026-09-09.md` — Round-1 proposal (superseded by the ratification above; kept for its citations).
- `ask-context-injection-design-2026-09-09.md` — Ratified implementation design for direct-ask context injection, session lifecycle, resilient dispatch, and consensus effects (Backlog Item B).
- `peerhub-ux-simplification-proposal-A-2026-09-12.md` — Round-1 proposal, voice A (superseded by the ratification below; kept for its citations).
- `peerhub-ux-simplification-proposal-B-2026-09-12.md` — Round-1 proposal, voice B (superseded by the ratification below; kept for its citations).
- `peerhub-ux-simplification-cc-deepthink-review-2026-09-12.md` — Third independent review that fed into the ratification below (read-and-propose only, not itself a vote).
- `peerhub-ux-simplification-RATIFIED-2026-09-12.md` — Final ratification for the UX/structure simplification backlog (CLI ergonomics, install experience, config layout, root hygiene, README).
- `quota-efficiency-discussion-ag-2026-09-13.md` — Independent proposal round on quota-efficient multi-peer dispatch usage (superseded by the ratification below; kept for its citations).
- `quota-efficiency-RATIFIED-cx-astra-2026-09-13.md` — Final ratification/adjudication for quota-efficient dispatch usage.
- `peerhub-dctx-proposal-1-2026-09-13.md` — D-CTX Round-1 proposal, voice 1 (rejected as ready-to-implement by `peerhub-holistic-renewal-RATIFIED-cx-astra-2026-09-13.md` section 8; kept for its citations, and D0/D1 remain an open follow-up).
- `peerhub-dctx-independent-review-ag-opus-2026-09-13.md` — First non-cx D-CTX independent security review voice; input to the still-open D0 independent-review gate (see the ratification below, section 8.4).
- `peerhub-dctx-independent-review-cc-2026-09-14.md` — Second non-cx voice; concurs with ag.opus's REVISE verdict and proposes a concrete D0-closing disposition (D7's environment-composition bug fixed first, D1 held until a workspace-trust-anchor answer exists) pending DIR-006 unanimous confirmation.
- `peerhub-dctx-d0-closure-2026-09-14.md` — **D0 CLOSED**: DIR-006 unanimous (cc/ag/cx) confirmation of the disposition above. Authorizes fixing D7 immediately; D1 (credential/carrier/issuer/ledger) stays held until a workspace-trust-anchor answer to Q2/D2 exists and is independently reviewed.
- `peerhub-dctx-trust-anchor-proposal-cc-2026-09-14.md` — PROPOSAL: answers Q2/D2 (admission-transaction-scoped credential table, keyed to R2's opaque workspace_home_id/activation_epoch; explicitly claims confusion-detection only, not compromised-worker resistance).
- `peerhub-dctx-trust-anchor-closure-2026-09-14.md` — **Q2/D2 CLOSED**: DIR-006 unanimous (cc/ag.opus/cx) acceptance. Authorizes implementing the credential/epoch table as its own bounded increment; carrier delivery (PEERHUB_CONTEXT_FILE) and CLI/adapter wiring remain separately held pending D5/D6 and their own review.
- `peerhub-dctx-carrier-matrix-verification-2026-09-16.md` — **Increment 1 CLOSED**: live-dispatch evidence (real Agy/Codex subprocesses reporting the D-CTX context-file env var and its unpredictable per-attempt content) satisfying the proposal's own section 6 Carrier Matrix requirement; Increment 1 (credential carrier + verified consensus path, including legacy proposal-vote and context-derived optional identity) is fully closed end-to-end.
- `peerhub-holistic-renewal-RATIFIED-cx-astra-2026-09-13.md` — Current superseding renewal direction for the whole PeerHub product/architecture (R0–R5, D0/D1 work-package ordering); R0/R2/R3/R5 and D0/D1's Increment 1 (see above) are all shipped; R4 (P4b governance convergence + typed call) design is in progress (see below), D1's own Increment 2/3 remain future staged work, not yet started.
- `peerhub-r4-p4b-independent-review-cc-2026-09-17.md` — cc's independent critique of ag.deepthink's R4/P4b proposal (second voice, cx rate-limited); corrects the "exact grant" framing against the original D-CTX asserted/verified duality, flags a real compatibility break, proposes two-axis call-map tracking.
- `peerhub-r4-p4b-converged-design-2026-09-17.md` — **R4/P4b DESIGN DRAFT, pending cx**: 2-voice (ag+cc) converged design synthesizing the proposal and critique above. NOT DIR-006 ratified; implementation must not start until cx (genuinely rate-limited until 2026-09-19) reviews and unanimous agreement is recorded.
- `engram-peerhub-single-folder-backup-simplification-2026-09-17.md` — 2-voice (ag+cc) converged design, pending cx: physical durable/cache splitting inside `.engram/` is a dead end (no vendor support, would require reintroducing already-removed junctions); recommends elevating `backup_personal_data.py` into first-class `engram backup/restore/reset` CLI verbs instead. PeerHub's `.peerhub/` already satisfies the goal for reset; backup still needs the existing SQLite-online-backup CLI command (WAL-mode correctness, not a simplification target).
- `engram-sys-folder-rename-feasibility-2026-09-17.md` — **CONCLUDED, not pursued**: dynamic `_sys` renameability would reproduce the cancelled 2026-06-18 root-swap's exact failure mode (31 files independently re-derive their own root path, 14 have a hard Python import-namespace dependency on the literal string). Recommends against attempting it; a separate, narrowly-scoped one-time migration tool remains a possible future ask.

## Tier C: Older drafts/debates (Historical rationale only, not current status)

These files were retained for historical record and now live under `../history/design/2026-07/` or `../history/design/2026-08/` (linked below); this section is kept only as a dated index pointer, not a file listing for this directory. Check their headers for supersession notices before assuming any content is current.

### 2026-08-23/24 — hub.py → peerhub replacement effort (Early Audits)
- [`HUB-REPLACEMENT-GAP-AUDIT-2026-08-23.md`](../history/design/2026-08/HUB-REPLACEMENT-GAP-AUDIT-2026-08-23.md)
- [`HUB-REPLACEMENT-CRITICAL-FINDING-LEGACY-CATALOG-2026-08-24.md`](../history/design/2026-08/HUB-REPLACEMENT-CRITICAL-FINDING-LEGACY-CATALOG-2026-08-24.md)
- [`HUB-REPLACEMENT-REAL-SOURCE-GROUNDTRUTH-2026-08-24.md`](../history/design/2026-08/HUB-REPLACEMENT-REAL-SOURCE-GROUNDTRUTH-2026-08-24.md)
- [`HUB-REPLACEMENT-ROADMAP-2026-08-09.md`](../history/design/2026-08/HUB-REPLACEMENT-ROADMAP-2026-08-09.md)
- [`INTERFACE-MECE-AESTHETIC-AUDIT-2026-08-24.md`](../history/design/2026-08/INTERFACE-MECE-AESTHETIC-AUDIT-2026-08-24.md)

### Phase 1
- [`PHASE1-KICKOFF-R1.md`](../history/design/2026-07/PHASE1-KICKOFF-R1.md)
- [`PHASE1-AUTODETECT-SIDECAR-2026-08-19.md`](../history/design/2026-08/PHASE1-AUTODETECT-SIDECAR-2026-08-19.md)
- [`PHASE1-AUTODETECT-SIDECAR-V2-2026-08-20.md`](../history/design/2026-08/PHASE1-AUTODETECT-SIDECAR-V2-2026-08-20.md)
- [`PHASE1-TEST-TAXONOMY-2026-08-19.md`](../history/design/2026-08/PHASE1-TEST-TAXONOMY-2026-08-19.md)
- [`PHASE1-TEST-TAXONOMY-V2-2026-08-20.md`](../history/design/2026-08/PHASE1-TEST-TAXONOMY-V2-2026-08-20.md)
- [`PHASE1-TEST-TAXONOMY-V3-2026-08-20.md`](../history/design/2026-08/PHASE1-TEST-TAXONOMY-V3-2026-08-20.md)
- [`PHASE1-MANIFEST-SCHEMA-V1-2026-08-20.md`](../history/design/2026-08/PHASE1-MANIFEST-SCHEMA-V1-2026-08-20.md)
- [`PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md`](../history/design/2026-08/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md)
- [`PHASE1-ADMISSION-RECEIPTS-REAL-2026-08-20.md`](../history/design/2026-08/PHASE1-ADMISSION-RECEIPTS-REAL-2026-08-20.md)
- [`PHASE1-PROMOTION-SCHEMA-V1-2026-08-20.md`](../history/design/2026-08/PHASE1-PROMOTION-SCHEMA-V1-2026-08-20.md)
- [`PHASE1-CAPABILITY-CROSSWALK-CLI-2026-08-20.md`](../history/design/2026-08/PHASE1-CAPABILITY-CROSSWALK-CLI-2026-08-20.md)
- [`PHASE1-CAPABILITY-CROSSWALK-CORE-2026-08-20.md`](../history/design/2026-08/PHASE1-CAPABILITY-CROSSWALK-CORE-2026-08-20.md)
- [`PHASE1-PARITY-LEDGER-BATCH1-2026-08-20.md`](../history/design/2026-08/PHASE1-PARITY-LEDGER-BATCH1-2026-08-20.md)
- [`PHASE1-PARITY-LEDGER-BATCH2-2026-08-20.md`](../history/design/2026-08/PHASE1-PARITY-LEDGER-BATCH2-2026-08-20.md)
- [`PHASE1-PARITY-LEDGER-BATCH3-2026-08-20.md`](../history/design/2026-08/PHASE1-PARITY-LEDGER-BATCH3-2026-08-20.md)
- [`PHASE1-PARITY-LEDGER-BATCH4-2026-08-20.md`](../history/design/2026-08/PHASE1-PARITY-LEDGER-BATCH4-2026-08-20.md)
- [`PHASE1-PARITY-LEDGER-BATCH5-2026-08-20.md`](../history/design/2026-08/PHASE1-PARITY-LEDGER-BATCH5-2026-08-20.md)
- [`PHASE1-ENGRAM-BRIDGE-INTERFACES-V1-2026-08-20.md`](../history/design/2026-08/PHASE1-ENGRAM-BRIDGE-INTERFACES-V1-2026-08-20.md)
- [`PHASE1-ENGRAM-BRIDGE-INTERFACES-V2-2026-08-20.md`](../history/design/2026-08/PHASE1-ENGRAM-BRIDGE-INTERFACES-V2-2026-08-20.md)
- [`PHASE1-THIRDPARTY-DEFERRAL-AND-SHIMS-2026-08-20.md`](../history/design/2026-08/PHASE1-THIRDPARTY-DEFERRAL-AND-SHIMS-2026-08-20.md)
- [`PHASE1-CX-COUNTERCRITIQUE-ROUND1-2026-08-20.md`](../history/design/2026-08/PHASE1-CX-COUNTERCRITIQUE-ROUND1-2026-08-20.md)
- [`PHASE1-CX-COUNTERCRITIQUE-ROUND2-2026-08-20.md`](../history/design/2026-08/PHASE1-CX-COUNTERCRITIQUE-ROUND2-2026-08-20.md)
- [`PHASE1-CX-COUNTERCRITIQUE-ROUND4-2026-08-20.md`](../history/design/2026-08/PHASE1-CX-COUNTERCRITIQUE-ROUND4-2026-08-20.md)
- [`PHASE1-ARCHITECTURE-CONSOLIDATION-2026-08-21.md`](../history/design/2026-08/PHASE1-ARCHITECTURE-CONSOLIDATION-2026-08-21.md)
- [`PHASE1-PROCESS-BACKLOG-2026-08-20.md`](../history/design/2026-08/PHASE1-PROCESS-BACKLOG-2026-08-20.md)

### Phase 0, 3, 4 & Slice 3/4/5
- [`PHASE0-COMPATIBILITY.md`](../history/design/2026-07/PHASE0-COMPATIBILITY.md)
- [`PHASE3-DISPATCH-LOOP-CONTRACT-DESIGN-2026-08-12.md`](../history/design/2026-08/PHASE3-DISPATCH-LOOP-CONTRACT-DESIGN-2026-08-12.md)
- [`PHASE3-T1-INCREMENT5-RETRY-LOOP-DESIGN-R1-2026-08-13.md`](../history/design/2026-08/PHASE3-T1-INCREMENT5-RETRY-LOOP-DESIGN-R1-2026-08-13.md)
- [`PHASE3-T1-INCREMENT5B-AUTHORIZATION-PLAN-2026-08-13.md`](../history/design/2026-08/PHASE3-T1-INCREMENT5B-AUTHORIZATION-PLAN-2026-08-13.md)
- [`PHASE3-T1-INCREMENT5C-OUTER-LOOP-PLAN-2026-08-14.md`](../history/design/2026-08/PHASE3-T1-INCREMENT5C-OUTER-LOOP-PLAN-2026-08-14.md)
- [`PHASE4-SCOPING-MEMO-2026-08-17.md`](../history/design/2026-08/PHASE4-SCOPING-MEMO-2026-08-17.md)
- [`SLICE3-KICKOFF-R1.md`](../history/design/2026-07/SLICE3-KICKOFF-R1.md)
- [`SLICE4-KICKOFF-R1.md`](../history/design/2026-07/SLICE4-KICKOFF-R1.md)
- [`SLICE5-KICKOFF-R1.md`](../history/design/2026-08/SLICE5-KICKOFF-R1.md)
- [`SLICE5-NEXT-STEPS-2026-08-04.md`](../history/design/2026-08/SLICE5-NEXT-STEPS-2026-08-04.md)

### Standalone architecture / infrastructure docs
- [`ARCHITECTURE.md`](../history/design/2026-07/ARCHITECTURE.md)
- [`ATOMICITY-MATRIX-2026-08-06.md`](../history/design/2026-08/ATOMICITY-MATRIX-2026-08-06.md)
- [`BACKLOG-CONSOLIDATED-2026-08-16.md`](../history/design/2026-08/BACKLOG-CONSOLIDATED-2026-08-16.md)
- [`CAPABILITY-LEASE-DESIGN-2026-08-08.md`](../history/design/2026-08/CAPABILITY-LEASE-DESIGN-2026-08-08.md)
- [`CAPABILITY-LEASE-DESIGN-2026-08-08-ERRATA.md`](../history/design/2026-08/CAPABILITY-LEASE-DESIGN-2026-08-08-ERRATA.md)
- [`EVIDENCE-ARTIFACT-DESIGN-2026-08-16.md`](../history/design/2026-08/EVIDENCE-ARTIFACT-DESIGN-2026-08-16.md)
- [`FACT-REFRESH-PROCEDURE-R1.md`](../history/design/2026-08/FACT-REFRESH-PROCEDURE-R1.md)
- [`HEALTH-QUOTA-TRACKING-DESIGN-2026-08-16.md`](../history/design/2026-08/HEALTH-QUOTA-TRACKING-DESIGN-2026-08-16.md)
- [`MIGRATION-STATUS-2026-08-06.md`](../history/design/2026-08/MIGRATION-STATUS-2026-08-06.md)
- [`OSS-ADOPTION-STRATEGY-2026-08-15.md`](../history/design/2026-08/OSS-ADOPTION-STRATEGY-2026-08-15.md)
- [`OUTBOX-SPLIT-PROGRESS-2026-08-09.md`](../history/design/2026-08/OUTBOX-SPLIT-PROGRESS-2026-08-09.md)
- [`OVERNIGHT-INFRA-LESSONS-2026-08-10.md`](../history/design/2026-08/OVERNIGHT-INFRA-LESSONS-2026-08-10.md)
- [`peerhub-architecture-debate.md`](../history/design/2026-07/peerhub-architecture-debate.md)
- [`PEERHUB-CODEX-SUBST-SANDBOX-CONFLICT-2026-08-21.md`](../history/design/2026-08/PEERHUB-CODEX-SUBST-SANDBOX-CONFLICT-2026-08-21.md)
- [`PEERHUB-MULTIPEER-BROADCAST-DESIGN-2026-08-11.md`](../history/design/2026-08/PEERHUB-MULTIPEER-BROADCAST-DESIGN-2026-08-11.md)
- [`PEERHUB-P-DRIVE-ISOLATION-2026-08-09.md`](../history/design/2026-08/PEERHUB-P-DRIVE-ISOLATION-2026-08-09.md)
- [`SESSION-SUMMARY-2026-08-07-tier2.md`](../history/design/2026-08/SESSION-SUMMARY-2026-08-07-tier2.md)
- [`STAGE3-ADAPTER-SCOPING-2026-08-08.md`](../history/design/2026-08/STAGE3-ADAPTER-SCOPING-2026-08-08.md)
- [`TDD-READINESS-GATE-R1.md`](../history/design/2026-08/TDD-READINESS-GATE-R1.md)
- [`TDD-READINESS-INVENTORY-R1.md`](../history/design/2026-08/TDD-READINESS-INVENTORY-R1.md)
- [`TRACEABILITY-CONVENTION-R1.md`](../history/design/2026-08/TRACEABILITY-CONVENTION-R1.md)
- [`WINDOWS-BROKERED-REDUCERS-DESIGN-2026-08-16.md`](../history/design/2026-08/WINDOWS-BROKERED-REDUCERS-DESIGN-2026-08-16.md)

### Phase 0 (closed 2026-07-30; own index)
- [`phase0/`](../history/design/phase0/README.md) — 133 files (fixture/authority/health/session-lease classification specs, controlled-fake-runner contracts, and their ratification rounds); see its own README for the full breakdown.
