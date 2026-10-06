# Remote Runtime Port & Adapter Contract (M3.6)

## 1. Authority Separation & Core Invariants
- **Separation between A2A and Runtime Port:**
  - Independent autonomous peer agent collaboration belongs to **A2A (M3.2)**.
  - Calling concrete external compute capability or execution worker belongs to **Execution Runtime Port (M3.6)**.
- **Core Invariant 12: `Remote runtime capability-bound, no general remote shell`:**
  - Execution capability is strictly bounded to declared capability contracts (`CapabilityInvocation`).
  - General arbitrary interactive/remote shells (e.g., unbounded SSH/bash remote execution) are strictly forbidden.
  - Invocation requires explicit capability name and typed parameter payloads.
- **Deterministic Fake / Loopback Isolation Proof:**
  - M3 does not require live distributed clusters, daemons, or replication topologies (which belong to M4-I).
  - M3 Exit Gate requires public port/adapter contract with deterministic fake/loopback failure-isolation proof.
- **Required Operations:**
  - `probe`: Check capability availability, health, and latency.
  - `submit`: Submit capability invocation job with tracking ID.
  - `get`: Query current job execution status and output.
  - `cancel`: Request cancellation of active job.
  - `collect`: Collect completed job artifacts or outputs.
- **Zero Dev-Dependency Violation (REL-009):**
  - Pure Python standard library implementation.

## 2. Public Interfaces & Protocols
The Runtime Port module (`peerhub.m3.runtime_port`) provides:
- `RuntimeCapability`:
  - `capability_id: str`
  - `name: str`
  - `description: str`
  - `schema: dict[str, Any]`
- `InvocationJob`:
  - `job_id: str`
  - `capability_id: str`
  - `params: dict[str, Any]`
  - `status: str` ("SUBMITTED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED")
  - `output: dict[str, Any] | None`
  - `error: str | None`
  - `created_at: str`
  - `completed_at: str | None`
- `RuntimePort` (Abstract protocol / interface):
  - `probe(capability_id: str) -> dict[str, Any]`
  - `submit(capability_id: str, params: dict[str, Any]) -> InvocationJob`
  - `get(job_id: str) -> InvocationJob`
  - `cancel(job_id: str) -> InvocationJob`
  - `collect(job_id: str) -> dict[str, Any]`
- `LoopbackRuntimeAdapter(RuntimePort)`:
  - Deterministic in-memory fake/loopback adapter.
  - Capable of simulating failures, latency, and success deterministically.

## 3. Runtime Job State Machine
```text
[Runtime Job Lifecycle]
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
