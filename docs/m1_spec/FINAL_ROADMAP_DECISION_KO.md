# PeerHub 전체 로드맵 최종 결정 — R4

## 최종 구조

```text
M1 Durable Communication
  Core: Peer / Stream / Record / Offset
  Extensions: Session Bridge / Observation / Readonly Diag
        ↓
M2 Durable Work Continuity
  Extension Runtime / Artifact / Work Projection / Skill-Catalog
  MCP / Backup-Recovery / Eval-Telemetry
        ↓
M3 Federated Intelligent Collaboration
  Search / Memory-Second Brain / A2A / Routing / Orchestration
  Basic Approval-Governance / Remote Runtime Port-Adapter Contract
        ↓
Required Roadmap Complete
        ↓
M4-A ... M4-L Optional Capability Tracks
```

## 변하지 않는 결정

M1~M3 및 Optional Track 전체에서 Core는 아래 네 개뿐입니다.

```text
Peer
Stream
Record
Offset
```

M2/M3 기능을 구현하기 위해 Task, Memory, Routing, Orchestration, Role, Governance를 Core concept로 승격하지 않습니다.

## Diag / quota 위치

기존 PeerHub의 핵심 운영 요구였던 quota 정보 조회는 **M1**입니다.

```text
Observation: quota/rate-limit/resource-pool evidence 수집
Readonly Diag: evidence 조회/표시
```

M3 Routing은 이 evidence를 선택 정책에 사용하고, M4-B Resource Coordination만 실제 자원 배분을 담당합니다.

## M2 결정

M2의 핵심은 **Record가 authoritative truth이고 Extension View는 rebuildable projection**이라는 것입니다.
Artifact는 immutable digest/provenance를 갖고, Work/Task는 Stream과 동일시하지 않습니다.
Skill/Catalog/MCP/Backup/Eval은 Extension 경계에 둡니다.

## M3 결정

Search/Memory/A2A/Routing/Orchestration/Approval과 Remote Runtime Port/Adapter Contract를 도입하되 모두 Core 밖의 교체 가능한 계층으로 유지합니다.
AI 판단과 검색 index는 truth가 아니며 source/evidence를 보존합니다.
`UNKNOWN quota != unlimited`, `UNKNOWN health != healthy`를 유지합니다.

## M4 이후 결정

M4 이후는 mandatory milestone이 아닙니다. `M4-A ... M4-L`은 서로 독립적인 **Optional Capability Tracks**입니다.

Activation에는 다음이 필요합니다.

1. 실제 운영 evidence;
2. 5 Whys;
3. M1~M3 기존 기능으로 해결 불가 확인;
4. Core 변경 없이 Extension 격리 가능;
5. Requirement + RED + rollback/disable path;
6. 명시적 opt-in.

## 구현 순서

설계는 앞서갈 수 있으나 production promotion은:

```text
M1 Exit PASS
→ M2 Exit PASS
→ M3 Exit PASS
→ evidence-driven optional tracks
```

순서를 지킵니다.

## 최종 완료 정의

M3 완료 후에는 로드맵에 적힌 Optional 기능을 모두 만드는 것이 목표가 아닙니다.

> **실사용 evidence가 없는 기능은 만들지 않는 상태가 PeerHub의 정상 완료 상태다.**

machine-readable SSOT는 `09_ROADMAP/roadmap.json`입니다.


## Conformance와 Maturity
표준 대응(`COMPLY/ADAPT/...`)은 목표 구조의 정합성이고 현재 구현 완료를 의미하지 않습니다. 설계 깊이 기준 M1은 TDD_READY이고 current main 구현 maturity는 VERIFIED입니다. M2·M3의 formal maturity는 PLANNED이고 roadmap 방향은 frozen 상태입니다. 실제 상태는 `10_STANDARDS_CONFORMANCE/product-maturity.json`이 SSOT입니다.


## 2026-10-04 current implementation note

PeerHub main `4a6994e7...` now contains the M1 implementation. The legacy 109-command table is migration evidence; `peerhub-m1` is currently side-by-side and default `peerhub` cutover remains gated.
## 2026-10-05 current-head rebaseline
PeerHub main `4a6994e7...` 기준 CI 및 release-gate workflow가 모두 SUCCESS이며 G3 live validation과 release evidence도 PASS했습니다. 따라서 M1 implementation은 exact-current-head 범위에서 `VERIFIED`로 올립니다. `peerhub` legacy default ↔ `peerhub-m1` cutover와 v0.11.0 GitHub Release의 distribution 상태는 별도 축으로 유지합니다. M2/M3 상세 Pre-TDD review를 추가했으며 Core는 계속 Peer/Stream/Record/Offset 4개입니다.
