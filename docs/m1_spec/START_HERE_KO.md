# PeerHub Very Simple Master Roadmap — FINAL R4

## 한 문장

> **PeerHub는 Peer/Stream/Record/Offset의 작은 durable communication Core를 유지하면서, M2에서 장기 Work를, M3에서 지능형/원격 협업을 Extension으로 확장하고, 이후 기능은 실제 필요가 증명될 때만 선택한다.**

## 필수 로드맵

```text
M1 Durable Communication
→ M2 Durable Work Continuity
→ M3 Federated Intelligent Collaboration
→ Required Roadmap Complete
```

M4 이후는 `Optional Capability Tracks`입니다.

## M1의 핵심

Core:

```text
Peer / Stream / Record / Offset
```

First-party Modules:

```text
Session Bridge
Observation
Readonly Diag
```

**quota / rate-limit 조회는 M1 Observation + Diag에 이미 포함됩니다.**

## 읽는 순서

1. `FINAL_ROADMAP_DECISION_KO.md`
2. `09_ROADMAP/PEERHUB_MASTER_ROADMAP_KO.md`
3. `09_ROADMAP/CAPABILITY_MILESTONE_MATRIX.md`
4. `01_M1/CORE_CONTRACT.md`
5. `01_M1/OBSERVATION_AND_DIAG.md`
6. `09_ROADMAP/M2_DURABLE_WORK_CONTINUITY.md`
7. `09_ROADMAP/M3_FEDERATED_INTELLIGENT_COLLABORATION.md`
8. `09_ROADMAP/OPTIONAL_CAPABILITY_TRACKS.md`
9. `02_EXTENSIONS/109_COMMAND_DISPOSITION.md`
10. `06_GUIDES/TEST_SET/README.md`
11. `08_LIFECYCLE/README.md`
12. `07_AUDIT/FINAL_RECURSIVE_AUDIT.md`

## 기준선

- Repo evidence: `greatgc-flow/peerhub` main `4a6994e7f73933a30c5d4e8ee538cd7d736f06ce`
- legacy v0 leaf commands: **109/109 assigned** (migration baseline)
- current M1 side-by-side CLI: **11 leaf commands**; default `peerhub` cutover is separate
- M1 test baseline: **88 requirements / 210 tests**
- global `AGENTS.md`: intentionally absent

## 개발 시작

M1 **설계계약은 TDD-ready/frozen**이고, current main `4a6994e7...`의 waves 0-9 구현은 exact-head CI/live/package/invariant/evidence gate까지 `VERIFIED`입니다. 다만 default `peerhub` public CLI cutover는 별도 maturity/gate입니다.
M2/M3 설계는 병렬로 구체화할 수 있지만 production promotion은 이전 milestone Exit PASS 이후에만 합니다.

## Unified Package Standard
이 패키지는 일반용 `VS-UNIFIED-PACKAGE-STANDARD 1.2.0`에 대한 제품 conformance map을 `10_STANDARDS_CONFORMANCE/`에 포함합니다. 공통 semantics를 따르되 제품 고유 topology를 억지로 동일화하지 않습니다.


## Conformance와 Maturity
표준 대응(`COMPLY/ADAPT/...`)은 목표 구조의 정합성이고 현재 구현 완료를 의미하지 않습니다. 설계 깊이 기준 M1은 TDD_READY이며, 실제 구현 maturity는 VERIFIED입니다. M2·M3의 formal maturity는 PLANNED이고 roadmap 방향은 frozen 상태입니다. 실제 상태는 `10_STANDARDS_CONFORMANCE/product-maturity.json`이 SSOT입니다.

- Current implementation snapshot: `07_AUDIT/CURRENT_M1_IMPLEMENTATION_SNAPSHOT_20261005.json`
- Public cutover gate: `09_ROADMAP/M1_PUBLIC_CLI_CUTOVER_GATE.md`
