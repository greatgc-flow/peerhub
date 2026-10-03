# M1 implementation decisions (orchestrator)

## Wave 0 gate (ag.pro, 2026-10-03): BLOCK on vacuous SCH-011/012/013 persistence asserts for record/offset
- D-W0-1 (Q-W0-2): Record/Offset persistence port is built in Wave 1. SCH-011/012/013 for record/offset MUST be re-asserted with real
  persistence (row counts and revisions unchanged on rejection) as part of Wave 1; Wave 1 exit requires it. The catalog is not amended.
- D-W0-2 (Q-W0-3): schema-only validation for extension-owned models (Observation, Resource Pool) is accepted in Wave 0; persistence
  assertions are enforced in Wave 5.
- D-W0-3 (Q-W0-4): moving the CLI composition root to `peerhub/m1_cli.py` is accepted (Core entrypoints must not import extensions).
- D-W0-4 (Q-W0-5): ARCH-004 on the ReadonlyDiag class is accepted for now; module split and module-level re-check in Wave 5.
- D-W0-5 (Q-W0-6): digest scope is decided in Wave 1 strictly from CORE_CONTRACT.md / STREAM_RECORD_OFFSET.md / TD-20; no ad-hoc scope.
