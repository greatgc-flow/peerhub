# Correctness Invariant Catalog

> This is a human-readable view generated from `invariants[]` in `closed-loop.json`. The machine-readable JSON is the SSOT.

| ID | Invariant | Linked requirements | Breach action |
|---|---|---|---|
| INV-001 | Stream canonical positions do not collide or duplicate. | REQ-ORDER-001, REQ-CORE-003 | `HOLD_OR_ROLLBACK` |
| INV-002 | Offset does not move backward or exceed the Stream head. | REQ-OFFSET-002, REQ-OFFSET-003 | `HOLD_OR_ROLLBACK` |
| INV-003 | A stale/fenced Bridge cannot perform authoritative writes. | REQ-FAULT-005, REQ-BRIDGE-002, REQ-BRIDGE-014 | `HOLD_OR_ROLLBACK` |
| INV-004 | MAY_HAVE_STARTED is not blindly replayed automatically. | REQ-FAULT-003, REQ-CERTAINTY-001, REQ-CERTAINTY-002 | `HOLD_OR_ROLLBACK` |
| INV-005 | Diag read-only paths do not change state. | REQ-DIAG-001 | `HOLD_OR_ROLLBACK` |
| INV-006 | A corrupt authoritative store is not silently repaired or used for continued operation. | REQ-PERSIST-004 | `INCIDENT` |
| INV-007 | Different payloads with the same idempotency key are not treated as successful. | REQ-IDEM-002, REQ-IDEM-004 | `HOLD_OR_ROLLBACK` |
| INV-008 | Migration/cutover does not lose or reinterpret committed Core identity, provenance, ordering, idempotency, or Offset invariants. | REQ-MIG-001, REQ-MIG-002 | `HOLD_OR_ROLLBACK` |
