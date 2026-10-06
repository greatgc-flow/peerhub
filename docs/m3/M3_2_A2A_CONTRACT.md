# A2A Remote Adapter Contract (M3.2)

## 1. Authority Separation & Core Invariants
- **Boundary Clarification:**
  - `A2A Task != PeerHub Work`
  - `A2A Artifact != PeerHub Artifact`
  - `A2A Message != Record`
  - `A2A context != Stream`
- **Core Invariant 5: `A2A identities never become PeerHub identities implicitly`:**
  - Remote agent identifiers from external A2A protocols are quarantined and namespaced.
  - They never automatically map into PeerHub local identities or stream author peers without explicit configured mapping.
- **Core Invariant 6: `Agent Card declarations != measured truth`:**
  - Agent Cards describe *declared* capabilities, endpoints, and attributes.
  - The local system never treats Agent Card declarations as verified, measured operational reality.
  - Health, availability, latency, and success rates must be measured separately through empirical observation/telemetry.
- **Opaque Protocol State:**
  - Remote protocol execution state is captured via opaque `ExternalExecutionRef`.
  - Only normalized, relevant evidence is bridged into local PeerHub records.
- **Zero Dev-Dependency Violation (REL-009):**
  - Pure Python standard library implementation.

## 2. Public Interfaces & Protocols
The A2A module (`peerhub.m3.a2a`) provides:
- `AgentCard`:
  - `agent_id: str`
  - `name: str`
  - `description: str`
  - `declared_capabilities: list[str]`
  - `endpoint_url: str`
  - `version: str`
  - `metadata: dict[str, Any]`
- `ExternalExecutionRef`:
  - `remote_system: str`
  - `remote_task_id: str`
  - `remote_run_id: str`
  - `opaque_state: dict[str, Any]`
- `A2ATaskRequest`:
  - `task_id: str`
  - `target_agent_id: str`
  - `action: str`
  - `input_parameters: dict[str, Any]`
- `A2ATaskResponse`:
  - `task_id: str`
  - `status: str` ("SUBMITTED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED")
  - `output: dict[str, Any] | None`
  - `error: str | None`
  - `external_ref: ExternalExecutionRef`
- `A2AAdapter`:
  - `register_agent_card(card: AgentCard) -> None`
  - `get_agent_card(agent_id: str) -> AgentCard | None`
  - `list_agent_cards() -> list[AgentCard]`
  - `dispatch_task(request: A2ATaskRequest) -> A2ATaskResponse`
  - `poll_task(task_id: str) -> A2ATaskResponse`
  - `cancel_task(task_id: str) -> A2ATaskResponse`

## 3. A2A Task State Machine
```text
[A2A Task Lifecycle]
       ┌───────────┐
       │ SUBMITTED │
       └─────┬─────┘
             ▼
       ┌───────────┐
       │  RUNNING  ├────────────────┐
       └─┬───────┬─┘                │
         │       │                  │
         ▼       ▼                  ▼
   ┌───────────┐ ┌────────┐   ┌───────────┐
   │ COMPLETED │ │ FAILED │   │ CANCELLED │
   └───────────┘ └────────┘   └───────────┘
```
