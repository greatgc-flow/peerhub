# M2 — Durable Work Continuity

## 목표
장기 Work, Artifact, Skill/Catalog, Backup/Recovery, Eval을 durable하게 이어가되 M1 Core를 변경하지 않습니다.

## Vertical slices

| Slice | 결과 | 핵심 invariant | Release 전 최소 증명 |
|---|---|---|---|
| M2.0 Generic Extension Host | Extension 공통 경계 | Core imports Extension = 0 | enable/disable/migration/failure isolation |
| M2.1 Artifact | 결과물/증적 참조 | digest/provenance immutable | tamper/path/dedup/restart |
| M2.2 Work Projection | 장기 작업 View | Record authoritative; projection rebuildable | replay/rebuild/crash/concurrency |
| M2.3 Skill + General Capability Catalog | HOW/WHAT SSOT | generated vendor views only | schema/drift/version/supersession |
| M2.4 MCP | tool/data/context boundary | MCP bypass write 금지 | auth/input/idempotency/error isolation |
| M2.5 Backup/Recovery | disaster continuity | authoritative 먼저 복구 | restore/replay/reconcile/generation fencing |
| M2.6 Eval/Telemetry | 품질 feedback | eval != truth | deterministic dataset/regression/evidence |

## Entry
M1 Exit Gate PASS.

## Exit
각 slice의 Requirement/Test/Gate가 모두 terminal closure이며, Extension을 모두 꺼도 M1 기능이 정상 동작해야 합니다. 또한 authoritative state만으로 derived projection을 삭제/재구축해 logical equivalence를 증명합니다.

상세 구현 경계는 `M2_DETAILED_PRE_TDD_REVIEW.md`가 기준입니다.

## M1과 M2.0의 경계
M1의 Session Bridge/Observation/Diag는 제품에 정적으로 조립되는 first-party module boundary입니다. **M2.0 Generic Extension Host**는 그 존재를 소급해서 재정의하지 않습니다. M2.0부터 manifest/version/capability/config schema/enable-disable/migration/failure isolation을 갖춘 일반 확장 hosting contract를 도입합니다.

## Catalog 명칭
M1의 `Runtime Target Catalog`는 RuntimeTarget을 식별하기 위한 최소 model/profile/runtime facts만 소유합니다. M2의 `General Skill/Capability Catalog`는 Skill, 일반 capability/current facts 및 vendor projection을 소유합니다. 두 catalog를 하나의 권위 저장소로 합치지 않습니다.

## 구현 전 필수 Pre-TDD Contract
M2는 현재 **roadmap-frozen**이며 구현-ready를 뜻하지 않습니다. 각 slice 코드 작성 전 최소한 아래를 freeze합니다.

- authority: authoritative / projection / cache / external evidence
- public port/API와 schema/version
- state machine 및 terminal/error 상태
- null/empty/unknown semantics
- retry/idempotency/duplicate/crash boundary
- concurrency/fencing 및 security/trust boundary
- migration/rebuild/disable/rollback 경로
- exception catalog + high-risk interaction
- Requirement → RED Test → Release Gate → DoD

이 항목이 없으면 해당 slice는 `PLANNED`에서 구현으로 승격하지 않습니다.
