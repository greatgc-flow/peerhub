# Basic Approval Contract (M3.5)

## 1. Authority Separation & Core Invariants
- **Core Invariant 11: `Approval exact-effect / single-use`:**
  - Approval tokens are strictly bound to the cryptographic SHA-256 digest of the exact target side-effect payload (`target_effect_digest`).
  - Executing a side-effect with even a single bit of difference from the approved payload is immediately rejected with `ApprovalEffectMismatchError`.
  - An approved grant is strictly single-use. Once transitioned to `CONSUMED`, any further consumption attempts are rejected with `ApprovalAlreadyConsumedError`.
- **Expiration and Lifespan:**
  - Every approval request has an explicit `valid_until` ISO 8601 deadline.
  - Expired tokens transition to `EXPIRED` and cannot be approved or consumed.
- **Zero Consensus Engine Bloat:**
  - Complex multi-party consensus, quorum voting, and delegated threshold signatures belong to M4-C/D. M3 implements the single-use exact-effect gate required for side-effect safety.
- **Zero Dev-Dependency Violation (REL-009):**
  - Pure Python standard library implementation using `sqlite3`.

## 2. Public Interfaces & Protocols
The Approval module (`peerhub.m3.approval`) provides:
- `compute_effect_digest(effect_payload: dict[str, Any]) -> str`
- `ApprovalRequest`:
  - `approval_id: str`
  - `action_type: str`
  - `effect_payload: dict[str, Any]`
  - `target_effect_digest: str`
  - `requested_by: str`
  - `state: str` ("REQUESTED" | "APPROVED" | "REJECTED" | "EXPIRED" | "CONSUMED")
  - `approver: str | None`
  - `rejection_reason: str | None`
  - `valid_until: str`
  - `consumed_at: str | None`
  - `created_at: str`
- `ApprovalEngine(db_path: Path)`:
  - `request_approval(action_type: str, effect_payload: dict[str, Any], requested_by: str, ttl_seconds: int = 3600) -> ApprovalRequest`
  - `approve(approval_id: str, approver: str) -> ApprovalRequest`
  - `reject(approval_id: str, reason: str, approver: str) -> ApprovalRequest`
  - `consume_approval(approval_id: str, effect_payload: dict[str, Any]) -> None`
  - `get_approval(approval_id: str) -> ApprovalRequest | None`
  - `clear() -> None`
  - `close() -> None`

## 3. Approval Lifecycle State Machine
```text
[Approval Lifecycle]
           ┌───────────┐
           │ REQUESTED │
           └─┬───┬───┬─┘
    (approve)│   │   │(expire)
             ▼   │   ▼
  ┌──────────┐   │ ┌─────────┐
  │ APPROVED │   │ │ EXPIRED │
  └────┬─────┘   │ └─────────┘
(consume)        │(reject)
       ▼         ▼
 ┌──────────┐ ┌──────────┐
 │ CONSUMED │ │ REJECTED │
 └──────────┘ └──────────┘
```
- Re-consuming `CONSUMED` approval raises `ApprovalAlreadyConsumedError`.
- Consuming with mismatching payload raises `ApprovalEffectMismatchError`.
