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
