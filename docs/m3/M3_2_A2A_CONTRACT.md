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
  - `reconcile_task(task_id: str, remote_task_id: str | None = None) -> A2ATaskResponse | None`
  - `abandon_task(task_id: str, reason: str) -> None`

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

---

## Closure update (2026-10-08)

Written after the M2/M3 closure work (`docs/implementation/M2_M3_CLOSURE_2026-10-08.md`) and checked against the code and tests. Where it conflicts with the text above, **this section wins**. The state-machine and exception JSON catalogs carry the same update.

- A2A execution with a journal configured writes (via `a2a_journal.py`) a durable `submitting` record (containing an atomic claim token, endpoint, and attempt number) before a remote call. An adapter without a journal tracks uncertain submissions only in memory: nothing survives a restart.
- Any unresolved `submitting` state on restart resolves to `UNCERTAIN` to prevent blind replay.
- `reconcile_task(task_id, remote_task_id=None)`: For HTTP, the operator must supply a candidate server-assigned `remote_task_id`; the task is adopted only if its history carries our local task id as `messageId`. A missing candidate or missing/mismatched history raises `A2AExecutionUncertainError` and leaves the task `UNCERTAIN`. If the remote has no record, the task is safely resolved to `NOT_STARTED` ONLY on guaranteed deterministic transports (e.g., loopback). On non-guaranteed transports (e.g., HTTP), the task remains `UNCERTAIN` to protect against late arrivals. Reconciliation can only be made to the originally submitted endpoint.
- `abandon_task`: Explicitly gives up an `UNCERTAIN` task that cannot be reconciled. It permanently burns the ID, records a reason, and a new ID must be used for retries.
- Single external binding (`a2a_http.py`): Supports A2A 1.0 JSON-RPC over HTTPS or loopback HTTP (`SendMessage`, `GetTask`, `CancelTask`, `A2A-Version: 1.0`; redirects strictly forbidden). Maps `TASK_STATE_SUBMITTED`/`WORKING`/`COMPLETED`/`FAILED`/`REJECTED`/`CANCELED` to local `SUBMITTED`/`RUNNING`/`COMPLETED`/`FAILED`/`FAILED`/`CANCELLED`. Direct Message answers and streaming are unsupported and fail closed. Enforces JSON-RPC 2.0 envelopes, integer error codes, response size bounds, an overall call deadline, and unmapped remote states fail closed.

**Superseded or missing in the original text:**
- The old contract's A2A lifecycle totally lacked the `SUBMITTING`, `UNCERTAIN`, and `ABANDONED` states.
- It originally lacked the `abandon_task` and `reconcile_task` definitions on the interface.
- It assumed immediate synchronous outcomes rather than handling durable reconciliation, state-locking on restarts, and the total call deadline.

**Interop result and limits (checked 2026-10-08):** `tools/a2a_sdk_interop.py` verified `SendMessage`, `GetTask`, `CancelTask`, reconciliation by history `messageId`, and `TASK_NOT_FOUND` against the official `a2a-sdk` 1.2.2 server using A2A 1.0 JSON-RPC. The server assigns task ids; our local task id is sent as `message.messageId` and the returned remote id is kept in `ExternalExecutionRef`. A lost `SendMessage` answer cannot be looked up by our local id: reconciliation requires the operator's candidate remote id and matching history, otherwise it remains uncertain until successful reconciliation or `abandon_task`. HTTP `TASK_NOT_FOUND` does not prove absence or release the local id. Cancelling a held task returned adapter `CANCELLED` and remote `TASK_STATE_CANCELED`; other third-party servers are untested. Direct Message answers and streaming remain unsupported (fail closed).
