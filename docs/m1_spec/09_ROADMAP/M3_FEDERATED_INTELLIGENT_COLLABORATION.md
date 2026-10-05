# M3 — Federated Intelligent Collaboration

## 목표
Search/Memory/A2A/Routing/Orchestration/Approval과 Remote Runtime Port/Adapter Contract를 M2 위의 교체 가능한 지능 계층으로 제공합니다. 실제 HA/replication/failover는 M4-I입니다.

| Slice | 책임 | 반드시 피할 것 |
|---|---|---|
| M3.0 Search | provenance-preserving retrieval | 검색 index를 truth로 취급 |
| M3.1 Memory | episodic/semantic memory + bounded context | 원본 Record rewrite |
| M3.2 A2A | remote independent Peer adapter | A2A state machine을 Core schema로 복사 |
| M3.3 Routing | capability/quota/eval 기반 policy | model/provider hardcoding |
| M3.4 Orchestration | bounded planner/fan-out/retry | Orchestrator를 PeerHub 자체로 만들기 |
| M3.5 Basic Approval | side-effect 승인/evidence | full consensus engine 기본 탑재 |
| M3.6 Remote Runtime Port/Adapter Contract | public remote-runtime port + fake/loopback failure isolation | real cross-machine runtime/daemon/cluster 의무화 |

## Entry
M2 Exit Gate PASS.

## Exit
지능형 계층 전체를 끄더라도 M1/M2가 정상 동작하고, 각 결정은 source/evidence까지 역추적 가능해야 합니다. Search rebuild, Memory supersession/revocation replay, deterministic Routing, bounded Orchestration crash recovery, exact-effect Approval을 증명합니다.

상세 구현 경계는 `M3_DETAILED_PRE_TDD_REVIEW.md`가 기준입니다.

## M3.6과 M4-I의 경계
M3.6의 필수 범위는 remote worker/agent를 연결할 **public port/adapter contract와 deterministic fake/loopback failure-isolation 증명**까지입니다. 다중 PeerHub replication, automatic failover, split-brain protection, cross-site federation은 증거가 있을 때 활성화하는 M4-I HA/Federation입니다. 따라서 M3 완료를 위해 cluster/daemon topology를 의무화하지 않습니다.

## 구현 전 필수 Pre-TDD Contract
M3도 현재 **roadmap-frozen**이며 구현-ready가 아닙니다. Search/Memory/A2A/Routing/Orchestration/Approval/Remote Runtime 각각에 대해 authority, API/schema, 상태기계, 결정 provenance, retry/idempotency, failure isolation, security, migration/disable, exception/interaction, RED/Release Gate를 먼저 freeze합니다.

특히 AI 판단 결과는 collaboration truth가 아니며 source/evidence ref 없는 decision을 authoritative state로 승격하지 않습니다.
