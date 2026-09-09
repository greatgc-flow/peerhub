# docs/reviews/ — Index

This directory holds audit reports, architecture and structure reviews, and retirement phase retrospectives.

## 1. System Audits & Structure Reviews (Active)

| Document | Date | Scope | Status |
|---|---|---|---|
| [p-drive-folder-structure-mece-review-2026-09-09.md](p-drive-folder-structure-mece-review-2026-09-09.md) | 2026-09-09 | P:\, peerhub, and Engram repository & runtime directory structure review (Rounds 1–3) | In Progress / Active Reference |
| [p-drive-mece-migration-audit-2026-09-09.md](p-drive-mece-migration-audit-2026-09-09.md) | 2026-09-09 | Comprehensive P:\ → Engram + peerhub file & capability migration verification | In Progress (Env closed, AI-Collab pending CX quota) |

## 2. LegacyTranslator Retirement Series (Archived / Closed)

Location: [legacy-translator-retirement/](legacy-translator-retirement/)

The 9-batch migration sequence retiring LegacyTranslator across all peer CLI adapters, concluded at v0.1.16 (8ae5791):

- **Batches 1–7 (Phased Migration)**:
  - atch-01.md: Initial contract translation framework
  - atch-02.md: Foundation adapter migration
  - atch-03.md: Command envelope serialization
  - atch-04.md: Capability lease and session management
  - atch-05.md: Health service and authority
  - atch-06.md: Governance and dual-write cutover
  - atch-07.md: Telemetry and presenter pipeline
- **Batch 8 (Sub-Batches 8a–8h)**:
  - atch-08-inventory.md: Inventory of remaining LegacyTranslator call sites
  - atch-08a.md through atch-08h.md: Granular adapter call site eliminations and canary waivers
- **Batch 9 (Final Closure)**:
  - atch-09.md: Final sweep verification
  - atch-09-final.md: Complete LegacyTranslator retirement sign-off and cutover verification
- **Canary Waivers**:
  - gate-canary-waivers.json, gate-canary-waivers-batch*.json: Machine-readable admission waivers generated during test cutovers.
