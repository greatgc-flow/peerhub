# PeerHub 사용/발전 가이드

이 문서는 목표 UX와 milestone별 사용 가능 범위를 설명합니다. 기존 v0.x CLI 문법을 그대로 보장하는 문서가 아닙니다.

## M1 — 기본 협업

```text
workspace
→ Peer 발견/등록
→ Stream 생성
→ immutable Record append
→ Session Bridge 전달/응답
→ Offset catch-up
```

장기 작업 제어:
- 진행현황: Record/Observation/log 우선 조회;
- pause: durable control Record 먼저 저장 후 best-effort interrupt;
- resume: provider session이 호환되면 재개, 아니면 fresh session + bounded catch-up;
- redirect: 최종 목적이 같으면 같은 Stream;
- major goal change: old Stream close + new Stream reference.

### quota / diag

```text
M1 Observation -> quota/rate/resource-pool evidence
M1 Diag        -> strict read-only 표시
```

관측되지 않은 값은 0/healthy/unlimited로 만들지 않습니다.

## M2 — 실제 Work 지속

Artifact, Work View, Skill/Catalog, MCP, Backup/Recovery, Eval을 사용합니다.
Record/Artifact provenance가 authoritative이고 Work/Search 같은 View는 재구축 가능해야 합니다.

## M3 — 지능형/원격 협업

Search/Memory/A2A/Routing/Orchestration/Approval/Remote Runtime Port·Adapter를 조합합니다. 실제 multi-node/HA는 M4-I가 활성화될 때만 추가합니다.
Routing은 M1 Observation의 quota/health evidence를 사용할 수 있지만 Core를 변경하지 않습니다.

## M4+ — Optional

필요한 Track만 opt-in합니다. UI/Notification/Enterprise/HA/Role 등은 M3 완료의 조건이 아닙니다.

## 패키지 검증

```powershell
python .\tools\validate_package.py
```

화면 결과와 `PACKAGE_VALIDATION.txt`가 동일하게 남습니다.

## 개발/운영 closed loop

```text
Requirement
→ RED
→ Minimum Implementation
→ deterministic/live/package GREEN
→ Release
→ Observe
→ Learn
→ next Requirement
↺
```

Gate는 exact candidate-bound fresh PASS만 유효합니다. queued/running/cancelled/stale/unavailable은 blocking gate를 만족시키지 않습니다.


## 2026-10-04 current implementation note

PeerHub main `4a6994e7...` now contains the M1 implementation. The legacy 109-command table is migration evidence; `peerhub-m1` is currently side-by-side and default `peerhub` cutover remains gated.
