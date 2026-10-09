# M3 — Federated Intelligent Collaboration

## Goal
Provide Search/Memory/A2A/Routing/Orchestration/Approval and Remote Runtime Port/Adapter Contract as an interchangeable intelligence layer on top of M2. Actual HA/replication/failover belongs to M4-I.

| Slice | Responsibility | Must Avoid |
|---|---|---|
| M3.0 Search | provenance-preserving retrieval | Treating search index as truth |
| M3.1 Memory | episodic/semantic memory + bounded context | Original Record rewrite |
| M3.2 A2A | remote independent Peer adapter | Copying A2A state machine to Core schema |
| M3.3 Routing | capability/quota/eval based policy | model/provider hardcoding |
| M3.4 Orchestration | bounded planner/fan-out/retry | Making Orchestrator the PeerHub itself |
| M3.5 Basic Approval | side-effect approval/evidence | Building in a full consensus engine by default |
| M3.6 Remote Runtime Port/Adapter Contract | public remote-runtime port + fake/loopback failure isolation | Mandating real cross-machine runtime/daemon/cluster |

## Entry
M2 Exit Gate PASS.

## Exit
Even with the entire intelligence layer turned off, M1/M2 must operate normally, and each decision must be traceable back to its source/evidence. Prove Search rebuild, Memory supersession/revocation replay, deterministic Routing, bounded Orchestration crash recovery, and exact-effect Approval.

Detailed implementation boundaries are based on `M3_DETAILED_PRE_TDD_REVIEW.md`.

## Boundary between M3.6 and M4-I
The required scope of M3.6 is up to the **public port/adapter contract and deterministic fake/loopback failure-isolation proof** to connect remote workers/agents. Multi-PeerHub replication, automatic failover, split-brain protection, and cross-site federation are M4-I HA/Federation, activated when evidence exists. Therefore, a cluster/daemon topology is not mandated to complete M3.

## Required Pre-TDD Contract Before Implementation
M3 is also currently **roadmap-frozen** and not implementation-ready. Freeze the authority, API/schema, state machine, decision provenance, retry/idempotency, failure isolation, security, migration/disable, exception/interaction, and RED/Release Gate for each of Search/Memory/A2A/Routing/Orchestration/Approval/Remote Runtime before coding.

In particular, AI judgment results are not collaboration truth, and decisions without source/evidence refs are not promoted to authoritative state.
