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

2026-10-04 현재 main `4a6994e7...`에는 M1 구현이 병합되었습니다. 과거 109-command 표와 v0 importer/selector는 2026-10-08에 제거되었고(v0는 Git 보존 브랜치), 새 `peerhub-m1` side-by-side CLI와 기본 `peerhub` public cutover는 별도 maturity/gate로 관리합니다. **Core 계약은 Peer/Stream/Record/Offset 그대로입니다.** `workspace reset`/`backup global`은 Backup/Recovery future extension으로 유지합니다.

## TDD-ready test baseline

M1 계약은 `06_GUIDES/TEST_SET/`의 **85 requirements / 205 test cases**로 추적됩니다. Core invariants, SQLite race/crash, Bridge fencing/certainty, Observation honesty, strict read-only Diag, fake-runtime E2E, real-provider canary, clean install/publish gate까지 설계 완료했습니다.

## TDD / Recursive MECE baseline

- Requirements: **85**
- Tests: **205**
- Requirement required-dimension coverage: validator enforced
- State/Exception/Interaction inventories: terminal closure required
- Provider live와 soak는 deterministic CI와 분리

## Post-development closed loop

개발/테스트 완료 후에는 `08_LIFECYCLE/`의 **Done → Released → Operated → Closed** 모델을 적용합니다. Correctness invariant 위반은 즉시 release hold/rollback 후보이며, 운영 signal은 evidence→triage→reproduce→requirement/regression→release→recurrence watch로 다시 개발 입력에 귀환합니다. 성능/용량 threshold는 코드나 문서에 고정하지 않고 baseline을 측정한 뒤 환경별 config로 관리합니다.

## 종·횡 최종 교차점검 R3

- invariant SSOT 8/8 requirement 역추적
- test→release gate 205/205 machine mapping
- release gate DAG explicit/fail-closed
- signal route→lifecycle/runbook resolvability
- rollback 후 unresolved Problem follow-up closure 규칙

## 전체 Roadmap 귀속 — R4

M1 이후 필수 roadmap은 `M2 Durable Work Continuity -> M3 Federated Intelligent Collaboration`로 확정했습니다.
M4 이후는 mandatory milestone이 아니라 Optional Capability Track입니다.
전체 SSOT와 상세 Gate는 `09_ROADMAP/roadmap.json` 및 `09_ROADMAP/PEERHUB_MASTER_ROADMAP_KO.md`를 참조합니다.
기존 `diag`의 quota 조회는 M1 Observation + Readonly Diag 책임으로 유지됩니다.


## 2026-10-04 implementation rebaseline

M1 code is now present upstream. However default `peerhub` is still the legacy CLI and `peerhub-m1 diag health` does not yet expose quota/rate-limit Observation views. M1 public cutover therefore remains separate and MUST preserve quota/rate-limit display parity before promotion.
