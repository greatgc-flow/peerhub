# M2/M3 Pre-Implementation Contract

M1은 TDD-ready입니다. M2/M3는 전체 방향과 경계가 roadmap-frozen인 상태이며 각 slice별 상세 구현계약은 코딩 직전에 작성합니다.

각 slice의 최소 산출물:
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

이 12개가 terminal하지 않으면 `IMPLEMENTING`으로 승격하지 않습니다. 불필요한 항목은 N_A/PASS와 근거를 남깁니다.
