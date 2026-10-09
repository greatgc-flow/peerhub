# Extension Catalog — Milestone Assigned

Core does not import any Extension.

## M1 — Durable Communication

| Family | Responsibility |
|---|---|
| Session Bridge | runtime/session/interrupt/resume/execution evidence |
| Observation | quota/rate/version/session/capability/resource-pool evidence |
| Diag | Core/Observation/log strict read-only troubleshooting |

> **Quota lookup in the existing `peerhub diag` belongs to M1.** Observation collects evidence, and Diag reads and displays it.

## M2 — Durable Work Continuity

| Family | Responsibility |
|---|---|
| Extension Runtime Foundation | manifest/schema/version/migration/enable-disable/failure isolation |
| Artifact | content/digest/provenance/supersession/storage ref |
| Work/Task Projection | Record-based rebuildable work lifecycle |
| Skill/Catalog | canonical Skills + changing facts Catalog |
| MCP | tool/data/context boundary |
| Backup/Recovery | authoritative restore + projection rebuild/reconcile |
| Eval/Telemetry | traces/datasets/regression feedback |

## M3 — Federated Intelligent Collaboration

| Family | Responsibility |
|---|---|
| Search/Retrieval | provenance-preserving index/query |
| Memory/Second Brain | episodic/semantic memory + bounded context |
| A2A | remote independent Peer adapter |
| Routing | capability/quota/eval based declarative selection |
| Orchestration | bounded planner/fan-out/retry/collect |
| Basic Approval/Governance | minimal side-effect approval/evidence |
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

Do not activate an Optional Track merely because the feature existed before. Operational evidence + 5 Whys + explicit RED + an opt-in decision are required.
