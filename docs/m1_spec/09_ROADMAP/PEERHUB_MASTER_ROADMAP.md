# PeerHub Master Milestone Final Roadmap

## 0. Final Product Boundary

The long-term structure of PeerHub is fixed to the single line below.

> **On top of a small durable communication Core, stack work continuity (M2) and intelligent collaboration (M3) as replaceable Extensions, and selectively activate subsequent features only when actual necessity is proven.**

The Core does not change across M1~M3 and all Optional Tracks.

```text
Peer
Stream
Record
Offset
```

## 1. M1 — Durable Communication

Goal: The facts of collaboration and delivery states are not lost even if the AI CLI's process, session, context, model, or quota changes.

### Core
- Peer
- Stream
- immutable Record
- Offset

### First-party Modules
- Session Bridge
- Observation
- Readonly Diag

### Operational Visibility Already Required in M1
- reachability
- cli version
- runtime capability
- session/activity
- **quota**
- **rate limit**
- execution failure
- resource pool
- freshness/evidence state

`Diag` only reads and displays Observation; it does not refresh/repair/restart/route.

### M1 Exit
- 85 requirements / 207 tests baseline GREEN
- SQLite concurrency/crash/idempotency GREEN
- Bridge fencing/execution certainty GREEN
- Observation honesty/freshness GREEN
- Diag strict readonly GREEN
- real Claude/Codex/Agy canary GREEN
- package/release/evidence gate GREEN

## 2. M2 — Durable Work Continuity

Goal: Go beyond simple messaging to safely continue actual multi-day to multi-month tasks, their outputs, and artifacts.

### M2.0 Generic Extension Host Foundation
Establish the minimum common boundary for Extension manifest/schema/version/migration/capability/enable-disable. The Core does not import Extensions.

### M2.1 Artifact
- immutable artifact metadata
- digest/content addressing
- media type/size
- source Record
- provenance/supersession
- storage ref
- dedup

Core Records only contain opaque artifact refs.

### M2.2 Work / Task Projection
Maintain minimal states.

```text
OPEN -> ACTIVE -> BLOCKED/PAUSED -> DONE/FAILED/CANCELLED
```

`work.created`, `work.started`, `work.checkpoint`, `work.completed` Records are the authoritative truth, and the Work DB/View is a rebuildable projection.

### M2.3 Skill + Catalog

```text
HOW       -> Skill
WHAT      -> Catalog
SHAPE     -> JSON Schema
SELECTION -> declarative Policy
TRUTH     -> Stream/Record
```

We do not create a global giant `AGENTS.md`.

### M2.4 MCP Surface
MCP is a boundary adapter.

- Resources: Streams / Records / Artifacts / Catalog / Observation
- Tools: append_record / attach_artifact / advance_offset, etc.
- MCP does not modify SQLite directly.

### M2.5 Backup / Recovery
Separate authoritative and rebuildable.

```text
restore authoritative
-> validate
-> replay
-> rebuild projection
-> reconcile
```

### M2.6 Eval / Telemetry
Close the feedback loop of Trace → Dataset → Eval → Regression → Requirement → RED.

### M2 Exit
- Artifact provenance/digest invariant GREEN
- Work projection full rebuild GREEN
- Skill/General Catalog SSOT drift=0
- MCP boundary/security GREEN
- backup→restore→replay→rebuild GREEN
- deterministic Eval/regression loop GREEN

## 3. M3 — Federated Intelligent Collaboration

Goal: Discover and select necessary knowledge, tools, and Peers to collaborate, while maintaining all intelligence layers as Extensions.

### M3.0 Search / Retrieval
Index Records/Artifacts/Catalogs and leave source_ref/score/observed_at/schema_version for each result.

### M3.1 Memory / Second Brain

```text
Record / Artifact
-> Memory Candidate
-> dedup/provenance/supersession/classification
-> Memory Store
-> Retrieval
-> bounded Context Pack
```

Memory does not rewrite original Records/Artifacts. Procedural memory remains as Skills.

### M3.2 A2A Adapter
Do not copy the protocol state of Remote Agents into the PeerHub Core.

```text
Remote Agent <-> A2A Adapter <-> Peer/Stream/Record
```

Maintain `A2A Task != PeerHub Stream`.

### M3.3 Routing
Start with declarative policies instead of building an AI router from the beginning.

Input:
- capability
- runtime availability
- quota/rate Observation
- user policy
- measured Eval

`UNKNOWN quota != unlimited` and `UNKNOWN health != healthy`.

### M3.4 Orchestration
Place Planner → decomposition → fan-out → collect → evaluate → bounded retry as Extensions. Even if the Orchestrator is disabled, basic PeerHub must operate normally.

### M3.5 Basic Approval / Governance
Maintain minimal proposal/review/approve/execute/evidence only for paths requiring external side effects or human approval. Do not revive the massive consensus/leadership architectures of the past as defaults.

### M3.6 Remote Runtime Port / Adapter Contract
The mandatory scope extends up to the remote-runtime public port/adapter contract and deterministic fake/loopback failure-isolation. Actual cross-machine worker activation and daemon/cluster topology are added only when necessity is proven.

### M3 Exit
- Search provenance GREEN
- Memory provenance/supersession GREEN
- A2A mapping/isolation GREEN
- quota-aware Routing deterministic + live GREEN
- bounded Orchestration fault isolation GREEN
- human approval/effect evidence GREEN
- remote-runtime port/adapter contract + fake/loopback failure isolation GREEN

Upon completing M3, **the required feature roadmap is complete**.

## 4. Beyond M4 — Optional Capability Tracks

M4 is not a sequential milestone. All tracks are independent opt-ins and activate only under the following conditions.

```text
Observed pain/evidence
-> 5 Whys
-> Confirm unresolvability with existing extensions
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

## 5. Exact Stages for Quota/Diag

| Feature | Stage | Responsibility |
|---|---|---|
| quota/rate observation | **M1** | Observation |
| quota/rate lookup/display | **M1** | Readonly Diag |
| quota-aware candidate selection | **M3** | Routing |
| quota allocation/backpressure across multiple Works | **M4-B** | Resource Coordination |
| automatic policy improvement based on quality/cost/quota | **M4-H** | Advanced Eval/Optimization |

## 6. Development Sequence Principles

- M2/M3 design can be done in parallel.
- **production merge/promotion strictly follows the M1 → M2 → M3 Exit Gate sequence.**
- Do not implant the next milestone's implementation into the Core in advance.
- All milestones follow the sequence of Requirement → RED → Minimum Implementation → deterministic GREEN → live/package gate → operate/learn.

## 7. Final Completion Definition

After M3, the goal is not to implement all the features in the list.

> The completion state is not building features whose necessity has not been proven in actual usage.

Therefore, after M3, PeerHub transitions from roadmap-driven development to evidence-driven development.
