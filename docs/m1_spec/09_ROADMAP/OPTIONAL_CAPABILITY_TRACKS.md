# M4+ Optional Capability Tracks

M4 이후는 mandatory sequence가 아닙니다.

| Track | 이름 | 기본 상태 | 활성화 근거 |
|---|---|---|---|
| M4-A | Advanced Health & Recovery | OFF | 반복되는 stuck/dead runtime과 수동복구 비용 |
| M4-B | Resource Coordination | OFF | 실제 quota/concurrency contention |
| M4-C | Advanced Governance | OFF | 조직 승인/권한분리/compliance 요구 |
| M4-D | Role / Duty / Leadership | OFF | capability+policy만으로 책임 조정 불가 |
| M4-E | Notification | OFF | 사람이 polling해야 하는 반복 운영 부담 |
| M4-F | UI / TUI / Web | OFF | CLI/API만으로 가시성/사용성이 부족 |
| M4-G | Enterprise / Multi-user | OFF | SSO/tenant/audit/secret 요구 |
| M4-H | Advanced Eval / Optimization | OFF | route/model 선택 최적화 가치가 측정됨 |
| M4-I | HA / Federation | OFF | single-node failure cost가 허용 불가 |
| M4-J | Extension Ecosystem | OFF | 제3자 extension 수요/배포 필요 |
| M4-K | Advanced Second Brain | OFF | temporal/entity/decision lineage 수요 |
| M4-L | Connectors / Business Apps | OFF | 특정 외부 시스템 연계 반복 수요 |

## 공통 Activation Gate

1. 운영 evidence가 존재한다.
2. 5 Whys로 근본 원인을 적는다.
3. 기존 M1~M3 capability 조합으로 해결 불가함을 기록한다.
4. 새 Core concept 없이 Extension으로 격리 가능함을 확인한다.
5. Requirement + RED test + rollback/disable path를 먼저 정의한다.
6. opt-in으로 배포한다.

## Dependency 원칙
모든 Optional Track은 M3 baseline 완료 후 opt-in이 기본입니다. 서로 순차 milestone은 아니지만 기존 capability를 재사용합니다. `roadmap.json.depends_on_capabilities`는 구현 순서를 강제하기 위한 DAG가 아니라 **필요 기반 capability 의존성**을 명시합니다. 해당 dependency가 필요하지 않은 축은 PASS할 수 있습니다.
