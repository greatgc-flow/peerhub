# docs/reviews/ — Index

This directory holds audit reports, architecture and structure reviews, and retirement phase retrospectives.

## 1. System Audits & Structure Reviews (Active)

| Document | Date | Scope | Status |
|---|---|---|---|
| [p-drive-folder-structure-mece-review-2026-09-09.md](p-drive-folder-structure-mece-review-2026-09-09.md) | 2026-09-09 | P:\, peerhub, and Engram repository & runtime directory structure review (Rounds 1-3) | In Progress / Active Reference |
| [p-drive-mece-migration-audit-2026-09-09.md](p-drive-mece-migration-audit-2026-09-09.md) | 2026-09-09 | Comprehensive P:\ -> Engram + peerhub file & capability migration verification | In Progress (Env closed, AI-collaboration side pending cx quota reset) |

## 2. LegacyTranslator Retirement Series (Archived / Closed)

Location: [legacy-translator-retirement/](legacy-translator-retirement/)

The 9-batch effort retiring `LegacyTranslator`/`LEGACY_CATALOG` from `peerhub/application/legacy.py`, concluded at v0.1.16 (`8ae5791`):

- **Batches 1-7** (individual integration test files rewritten off `LegacyTranslator`):
  - `batch-01.md` through `batch-07.md`
- **Batch 8** (`tests/integration/test_stage2_boundary.py`, split into 8 sub-batches):
  - `batch-08-inventory.md` — subsystem-to-test-node inventory and sub-batch split plan
  - `batch-08a.md` through `batch-08h.md` — one sub-batch each
- **Batch 9** (final closure):
  - `batch-09-final.md` — created `test_command_wire_contracts.py`, deleted `LegacyTranslator`/`LEGACY_CATALOG`, concluding the retirement
- **Canary Waivers**: `gate-canary-waivers.json`, `gate-canary-waivers-batch*.json` — per-batch waiver files for `tools/legacy_retirement/gate.py`'s mechanized preservation gate.
