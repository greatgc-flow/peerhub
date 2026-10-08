# PeerHub 전체 마일스톤 최종 로드맵

## 0. 최종 제품 경계

PeerHub의 장기 구조는 아래 한 줄로 고정합니다.

> **작은 durable communication Core 위에, 작업 지속성(M2)과 지능형 협업(M3)을 교체 가능한 Extension으로 쌓고, 그 이후 기능은 실제 필요가 증명될 때만 선택적으로 활성화한다.**

Core는 M1~M3 및 Optional Track 전체에서 변하지 않습니다.

```text
Peer
Stream
Record
Offset
```

## 1. M1 — Durable Communication

목표: AI CLI의 프로세스·세션·컨텍스트·모델·쿼터가 바뀌어도 협업 사실과 전달 상태를 잃지 않습니다.

### Core
- Peer
- Stream
- immutable Record
- Offset

### First-party Modules
- Session Bridge
- Observation
- Readonly Diag

### M1에서 이미 제공해야 하는 운영 가시성
- reachability
- cli version
- runtime capability
- session/activity
- **quota**
- **rate limit**
- execution failure
- resource pool
- freshness/evidence state

`Diag`는 Observation을 읽어 보여줄 뿐 refresh/repair/restart/route하지 않습니다.

### M1 Exit
- 85 requirements / 205 tests baseline GREEN
- SQLite concurrency/crash/idempotency GREEN
- Bridge fencing/execution certainty GREEN
- Observation honesty/freshness GREEN
- Diag strict readonly GREEN
- real Claude/Codex/Agy canary GREEN
- package/release/evidence gate GREEN

## 2. M2 — Durable Work Continuity

목표: 단순 메시징을 넘어 며칠~몇 달짜리 실제 작업과 결과물·증적을 안전하게 이어갑니다.

### M2.0 Generic Extension Host Foundation
Extension manifest/schema/version/migration/capability/enable-disable의 최소 공통 경계를 둡니다. Core는 Extension을 import하지 않습니다.

### M2.1 Artifact
- immutable artifact metadata
- digest/content addressing
- media type/size
- source Record
- provenance/supersession
- storage ref
- dedup

Core Record에는 opaque artifact ref만 둡니다.

### M2.2 Work / Task Projection
최소 상태만 둡니다.

```text
OPEN -> ACTIVE -> BLOCKED/PAUSED -> DONE/FAILED/CANCELLED
```

`work.created`, `work.started`, `work.checkpoint`, `work.completed` Record가 authoritative truth이고 Work DB/View는 rebuildable projection입니다.

### M2.3 Skill + Catalog

```text
HOW       -> Skill
WHAT      -> Catalog
SHAPE     -> JSON Schema
SELECTION -> declarative Policy
TRUTH     -> Stream/Record
```

전역 giant `AGENTS.md`는 만들지 않습니다.

### M2.4 MCP Surface
MCP는 boundary adapter입니다.

- Resources: Streams / Records / Artifacts / Catalog / Observation
- Tools: append_record / attach_artifact / advance_offset 등
- MCP가 SQLite를 직접 수정하지 않습니다.

### M2.5 Backup / Recovery
Authoritative와 rebuildable을 분리합니다.

```text
restore authoritative
-> validate
-> replay
-> rebuild projection
-> reconcile
```

### M2.6 Eval / Telemetry
Trace → Dataset → Eval → Regression → Requirement → RED의 feedback loop를 닫습니다.

### M2 Exit
- Artifact provenance/digest invariant GREEN
- Work projection full rebuild GREEN
- Skill/General Catalog SSOT drift=0
- MCP boundary/security GREEN
- backup→restore→replay→rebuild GREEN
- deterministic Eval/regression loop GREEN

## 3. M3 — Federated Intelligent Collaboration

목표: 필요한 지식·도구·Peer를 발견하고 선택해 협업하되 모든 지능 계층을 Extension으로 유지합니다.

### M3.0 Search / Retrieval
Record/Artifact/Catalog를 index하고 결과마다 source_ref/score/observed_at/schema_version을 남깁니다.

### M3.1 Memory / Second Brain

```text
Record / Artifact
-> Memory Candidate
-> dedup/provenance/supersession/classification
-> Memory Store
-> Retrieval
-> bounded Context Pack
```

Memory는 원본 Record/Artifact를 rewrite하지 않습니다. Procedural memory는 계속 Skill입니다.

### M3.2 A2A Adapter
Remote Agent의 protocol state를 PeerHub Core로 복사하지 않습니다.

```text
Remote Agent <-> A2A Adapter <-> Peer/Stream/Record
```

`A2A Task != PeerHub Stream`을 유지합니다.

### M3.3 Routing
처음부터 AI router를 만들지 않고 declarative policy부터 시작합니다.

입력:
- capability
- runtime availability
- quota/rate Observation
- user policy
- measured Eval

`UNKNOWN quota != unlimited`, `UNKNOWN health != healthy`입니다.

### M3.4 Orchestration
Planner → decomposition → fan-out → collect → evaluate → bounded retry를 Extension으로 둡니다. Orchestrator를 꺼도 기본 PeerHub가 정상 동작해야 합니다.

### M3.5 Basic Approval / Governance
외부 side effect나 사람 승인이 필요한 경로에 한해 최소 proposal/review/approve/execute/evidence만 둡니다. 과거의 거대한 consensus/leadership 체계를 기본으로 되살리지 않습니다.

### M3.6 Remote Runtime Port / Adapter Contract
필수 범위는 remote-runtime public port/adapter contract와 deterministic fake/loopback failure-isolation까지입니다. 실제 cross-machine worker 활성화와 daemon/cluster topology는 필요가 증명된 경우에만 추가합니다.

### M3 Exit
- Search provenance GREEN
- Memory provenance/supersession GREEN
- A2A mapping/isolation GREEN
- quota-aware Routing deterministic + live GREEN
- bounded Orchestration fault isolation GREEN
- human approval/effect evidence GREEN
- remote-runtime port/adapter contract + fake/loopback failure isolation GREEN

M3 종료 시 **필수 기능 로드맵은 완료**합니다.

## 4. M4 이후 — Optional Capability Tracks

M4는 순차 milestone이 아닙니다. 모든 track은 독립 opt-in이며 다음 조건이 있어야 활성화합니다.

```text
Observed pain/evidence
-> 5 Whys
-> existing extension으로 해결 불가 확인
-> explicit requirement
-> RED test
-> opt-in decision
```

- M4-A Advanced Health & Recovery
- M4-B Resource Coordination
- M4-C Advanced Governance
- M4-D Role / Duty / Leadership
- M4-E Notification
- M4-F UI / TUI / Web
- M4-G Enterprise / Multi-user
- M4-H Advanced Eval / Optimization
- M4-I HA / Federation
- M4-J Extension Ecosystem
- M4-K Advanced Second Brain
- M4-L Connectors / Business Apps

## 5. 쿼터/Diag의 정확한 단계

| 기능 | 단계 | 책임 |
|---|---|---|
| quota/rate 관측 | **M1** | Observation |
| quota/rate 조회/표시 | **M1** | Readonly Diag |
| quota-aware 후보 선택 | **M3** | Routing |
| 여러 Work 간 quota 배분/backpressure | **M4-B** | Resource Coordination |
| quality/cost/quota 기반 자동 정책 개선 | **M4-H** | Advanced Eval/Optimization |

## 6. 개발 순서 원칙

- M2/M3 설계는 병렬로 할 수 있습니다.
- **production merge/promotion은 M1 → M2 → M3 Exit Gate 순서를 지킵니다.**
- 다음 milestone 구현을 미리 Core에 심지 않습니다.
- 모든 milestone은 Requirement → RED → Minimum Implementation → deterministic GREEN → live/package gate → operate/learn 순서입니다.

## 7. 최종 완료 정의

M3 이후에는 기능 목록을 모두 구현하는 것을 목표로 하지 않습니다.

> 실제 사용에서 필요성이 증명되지 않은 기능을 만들지 않는 것이 완료 상태입니다.

따라서 M3 이후 PeerHub는 roadmap-driven 개발에서 evidence-driven 개발로 전환합니다.
