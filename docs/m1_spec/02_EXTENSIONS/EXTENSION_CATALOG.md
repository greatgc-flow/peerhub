# Extension Catalog — Milestone Assigned

Core는 어느 Extension도 import하지 않습니다.

## M1 — Durable Communication

| Family | 책임 |
|---|---|
| Session Bridge | runtime/session/interrupt/resume/execution evidence |
| Observation | quota/rate/version/session/capability/resource-pool evidence |
| Diag | Core/Observation/log strict read-only troubleshooting |

> **기존 `peerhub diag`의 quota 조회는 M1입니다.** Observation이 증거를 수집하고 Diag가 읽어 표시합니다.

## M2 — Durable Work Continuity

| Family | 책임 |
|---|---|
| Extension Runtime Foundation | manifest/schema/version/migration/enable-disable/failure isolation |
| Artifact | content/digest/provenance/supersession/storage ref |
| Work/Task Projection | Record 기반 rebuildable work lifecycle |
| Skill/Catalog | canonical Skills + changing facts Catalog |
| MCP | tool/data/context boundary |
| Backup/Recovery | authoritative restore + projection rebuild/reconcile |
| Eval/Telemetry | traces/datasets/regression feedback |

## M3 — Federated Intelligent Collaboration

| Family | 책임 |
|---|---|
| Search/Retrieval | provenance-preserving index/query |
| Memory/Second Brain | episodic/semantic memory + bounded context |
| A2A | remote independent Peer adapter |
| Routing | capability/quota/eval based declarative selection |
| Orchestration | bounded planner/fan-out/retry/collect |
| Basic Approval/Governance | 최소 side-effect approval/evidence |
| Remote Runtime Adapter | M3 public boundary/failure isolation; real distributed topology/HA belongs M4-I |

## M4+ Optional Capability Tracks

| Track | Family |
|---|---|
| M4-A | Advanced Health/Recovery |
| M4-B | Resource Coordination |
| M4-C | Advanced Governance/Consensus |
| M4-D | Role/Leadership/Duty |
| M4-E | Notification |
| M4-F | UI/TUI/Web/Collaboration UX |
| M4-G | Enterprise/Multi-user |
| M4-H | Advanced Eval/Optimization |
| M4-I | HA/Federation |
| M4-J | Extension Ecosystem |
| M4-K | Advanced Second Brain |
| M4-L | Connectors/Business Apps |

Optional Track은 기존 기능이 있었다는 이유로 활성화하지 않습니다. 운영 evidence + 5 Whys + explicit RED + opt-in decision이 필요합니다.
