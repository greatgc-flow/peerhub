# M2/M3 Pre-Implementation Contract

M1 is TDD-ready. M2/M3 are roadmap-frozen in overall direction and boundaries; detailed implementation contracts for each slice are written right before coding.

Minimum deliverables for each slice:
1. Charter / non-goals
2. Authority matrix (authoritative/derived/cache/external)
3. Public port/API + request/response/error schema
4. State machine + terminal transitions
5. Null/empty/unknown semantics
6. Retry/idempotency/duplicate/crash semantics
7. Concurrency/fencing/time/freshness semantics
8. Security/trust/path/secret boundary
9. Migration/rebuild/disable/rollback
10. Exception catalog
11. Cross-extension interaction matrix
12. Requirement → RED Test → Gate → DoD

If these 12 are not terminal, it will not be promoted to `IMPLEMENTING`. For unnecessary items, leave N_A/PASS and the rationale.
