# PeerHub M1 최종 결정

## Core

```text
Peer
Stream
Record
Offset
```

## Core 책임

```text
identify
create/join Stream
append immutable Record
read ordered Records
track Offset
basic provenance
idempotent append
```

## M1 Extension

```text
Session Bridge  -> local AI CLI / future harness runtime 연결
Observation     -> quota/rate/session/version/capability 관측
Diag            -> Observation/Core를 읽는 strict read-only observer
```

## 하지 않음

```text
NO AI role injection
NO coordinator/supervisor
NO planner
NO automatic routing/failover
NO consensus/governance
NO task engine
NO agent memory/RAG
NO global directive/lesson injection
NO generic lease/lock platform
NO daemon requirement
NO MCP/A2A internal architecture
```

## 장기 확장 준비

```text
Collaboration memory -> Stream/Record
Runtime/session memory -> Harness/Session
Short/long-term memory -> future Memory extension
Procedure memory -> Agent Skills
Changing facts -> JSON Schema validated Catalog
Remote agents -> A2A Adapter
Tools/data -> MCP
Telemetry export -> OTel Adapter
```

## Very Simple 핵심

최신 기술을 지원하기 위해 Core를 키우지 않습니다.
**새 기술을 Session Bridge/Extension이 흡수할 수 있는 작은 Core**가 최종 선택입니다.

## 현행 repo delta 확인

2026-10-03 재검증한 main HEAD `57a137cd...`의 109 leaf commands와 model-profile 변경을 전수 분류했으며, **Core 계약 변경은 없습니다.** `workspace reset`/`backup global`은 Backup/Recovery future extension으로 유지합니다.

## TDD-ready test baseline

M1 계약은 `06_GUIDES/TEST_SET/`의 **88 requirements / 210 test cases**로 추적됩니다. Core invariants, SQLite race/crash, Bridge fencing/certainty, Observation honesty, strict read-only Diag, fake-runtime E2E, real-provider canary, clean install/publish gate까지 설계 완료했습니다.

## TDD / Recursive MECE baseline

- Requirements: **88**
- Tests: **210**
- Requirement required-dimension coverage: validator enforced
- State/Exception/Interaction inventories: terminal closure required
- Provider live와 soak는 deterministic CI와 분리

## Post-development closed loop

개발/테스트 완료 후에는 `08_LIFECYCLE/`의 **Done → Released → Operated → Closed** 모델을 적용합니다. Correctness invariant 위반은 즉시 release hold/rollback 후보이며, 운영 signal은 evidence→triage→reproduce→requirement/regression→release→recurrence watch로 다시 개발 입력에 귀환합니다. 성능/용량 threshold는 코드나 문서에 고정하지 않고 baseline을 측정한 뒤 환경별 config로 관리합니다.

## 종·횡 최종 교차점검 R2

- invariant SSOT 8/8 requirement 역추적
- test→release gate 210/210 machine mapping
- release gate DAG explicit/fail-closed
- signal route→lifecycle/runbook resolvability
- rollback 후 unresolved Problem follow-up closure 규칙
