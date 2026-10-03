# Correctness Invariant Catalog

> `closed-loop.json`의 `invariants[]`에서 생성한 사람용 View입니다. Machine JSON이 SSOT입니다.

| ID | Invariant | Linked requirements | Breach action |
|---|---|---|---|
| INV-001 | Stream canonical position은 충돌/중복되지 않는다. | REQ-ORDER-001, REQ-CORE-003 | `HOLD_OR_ROLLBACK` |
| INV-002 | Offset은 후퇴하거나 Stream head를 초과하지 않는다. | REQ-OFFSET-002, REQ-OFFSET-003 | `HOLD_OR_ROLLBACK` |
| INV-003 | stale/fenced Bridge는 authoritative write를 하지 못한다. | REQ-FAULT-005, REQ-BRIDGE-002, REQ-BRIDGE-014 | `HOLD_OR_ROLLBACK` |
| INV-004 | MAY_HAVE_STARTED는 blind automatic replay되지 않는다. | REQ-FAULT-003, REQ-CERTAINTY-001, REQ-CERTAINTY-002 | `HOLD_OR_ROLLBACK` |
| INV-005 | Diag read-only 경로는 상태를 변경하지 않는다. | REQ-DIAG-001 | `HOLD_OR_ROLLBACK` |
| INV-006 | corrupt authoritative store는 silent repair/continue하지 않는다. | REQ-PERSIST-004 | `INCIDENT` |
| INV-007 | 동일 idempotency key의 상이 payload를 성공 처리하지 않는다. | REQ-IDEM-002, REQ-IDEM-004 | `HOLD_OR_ROLLBACK` |
| INV-008 | Migration/cutover는 committed Core identity, provenance, ordering, idempotency 및 Offset 불변식을 손실하거나 재해석하지 않는다. | REQ-MIG-001, REQ-MIG-002, REQ-IMP-001, REQ-IMP-002 | `HOLD_OR_ROLLBACK` |
