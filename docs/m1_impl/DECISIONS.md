# M1 implementation decisions (orchestrator)

## Wave 0 gate (ag.pro, 2026-10-03): BLOCK on vacuous SCH-011/012/013 persistence asserts for record/offset
- D-W0-1 (Q-W0-2): Record/Offset persistence port is built in Wave 1. SCH-011/012/013 for record/offset MUST be re-asserted with real
  persistence (row counts and revisions unchanged on rejection) as part of Wave 1; Wave 1 exit requires it. The catalog is not amended.
- D-W0-2 (Q-W0-3): schema-only validation for extension-owned models (Observation, Resource Pool) is accepted in Wave 0; persistence
  assertions are enforced in Wave 5.
- D-W0-3 (Q-W0-4): moving the CLI composition root to `peerhub/m1_cli.py` is accepted (Core entrypoints must not import extensions).
- D-W0-4 (Q-W0-5): ARCH-004 on the ReadonlyDiag class is accepted for now; module split and module-level re-check in Wave 5.
- D-W0-5 (Q-W0-6): digest scope is decided in Wave 1 strictly from CORE_CONTRACT.md / STREAM_RECORD_OFFSET.md / TD-20; no ad-hoc scope.

## Wave 1 gate (cx.pro, 2026-10-03): BLOCK, fixes required; rulings
- D-W1-1 (Q-W1-1): TD-20 wins (scope = stream+author+key; corroborated by IDEM-001/002). PROP-002 as written in the catalog contradicts TD-20:
  tests assert conflict on mutations within an unchanged scope, and independent Records for author/stream changes. SPEC ERRATUM to report to the
  spec owner (user) in the final report; do not edit the frozen catalog.
- D-W1-2 (Q-W1-2): no asymmetric default. Follow `record.schema.json` required fields: if `created_at` is required on the wire, append rejects an
  omission; stored Record digest must be reproducible from the stored Record.
- D-W1-3 (Q-W1-3): member changes on a CLOSED stream allowed via revision CAS; append and reopen forbidden (TD-09).
- D-W1-4 (Q-W1-4): peer upsert preserves identity and original created_at; mutable fields replaced; no conflict semantics (document).
- D-W1-5 (Q-W1-5): no body size limit; exact preservation required (PROP-009).
- Positions may have gaps (TD-01): CORE-005/PROP-010 must assert on actual committed positions, not gapless sequences.
