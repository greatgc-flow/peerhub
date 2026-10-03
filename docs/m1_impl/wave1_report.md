# Wave 1 report (39 ids: CORE-001..013, IDEM-001..003, STR-001..006, OFF-001..008, PROP-001..006/009/010/011)

Tests: tests/m1/core/{test_core_record_idempotency,test_stream_offset}.py, tests/m1/property/test_prop_core.py (hypothesis), reworked SCH-011/012/013.
RED run (new tests vs. old production + name stubs so they collect): 24 failed / 17 passed. Reasons:

| ids | red reason | GREEN |
|---|---|---|
| CORE-003/005/006/009/010, PROP-001/002 | requests carry caller `created_at` (TD-02); old store rejected the kwarg / digest was kind+body only | `AppendRequest`, `compute_record_digest` (TD-02 scope) |
| CORE-004 | Record mutable, no storage guard | frozen Record + BEFORE UPDATE/DELETE triggers |
| CORE-013 | `record_id` accepted as kwarg, server fields not rejected | `AppendRequest(extra=forbid)` |
| IDEM-002/003, PROP-002b | unique key was (stream,key) (FK failure / false conflict) | scope (stream, author, key), TD-20 |
| STR-001/002/004 | `cas_stream` NotImplemented | `CoreStore.cas_stream` (BEGIN IMMEDIATE + revision CAS) |
| STR-003, STR-006 | append to CLOSED allowed; unknown members silently accepted | `StreamClosedError`, `UnknownReferenceError`; member order kept by rowid |
| OFF-004/006/007/008, PROP-005/006 | rewind / past-head / unknown refs accepted | `OffsetRegressionError`, `OffsetBeyondHeadError`, `UnknownReferenceError` |
| PROP-010 | non-positive limit accepted | validation in `read_records` |
| Green on first run (existing behaviour) | CORE-001/002/007/008/011/012, IDEM-001, OFF-001/002/003/005, STR-005, PROP-003/004/009/011 | none needed |

D-W0-1 carry-over: SCH-011/012/013 for record/offset now go through `harness.persist_wire` (record -> `wire.append_request_from_wire` -> append; offset -> CAS).
Each rejection asserts a specific `WireValidationError`, unchanged row counts, per-table digests (revisions) and state digest; `_prove_port_live` positive
controls prove the record/offset ports really write (so unchanged-state asserts are not vacuous).
Harness port completed: create/get_peer, create/get/cas_stream, append_record, read_records, get_offset, cas_offset, state_digest, reopen, workspace_generation, + persist_wire, row_counts, table_digests.

## Decisions / ambiguities (spec citations)
- Digest scope (D-W0-5, Q-W0-6): TD-02 field set + schema_version, canonical JSON (TD-17/24); server fields excluded (TD-21). Omitted `created_at` projects as null (server stores now()), so a keyless-timestamp retry matches; supplied-and-changed created_at conflicts (CORE-010). `compute_payload_digest(kind, body)` kept only as canonicalization primitive (PROP-008).
- Idempotency scope (TD-20): (stream_id, author_peer_id, key). Retry lookup runs before the CLOSED check, so a retry of an already committed record still resolves.
- PROP-002 oracle ("every field incl. author/stream_id conflicts") contradicts IDEM-001/002 + TD-20: author/stream changes select another scope. Asserted conflict for the 7 other fields; for author/stream asserted independent records with different digests (Q-W1-1).
- Offset (TD-03/TD-10): equal position accepted; every accepted write bumps revision once (PROP-005); check order refs -> CAS -> monotonic -> head. Absent row = (0, rev 1); first accepted write gives rev 2. get_offset on unknown peer/stream raises (OFF-008).
- Stream CAS mutation grammar (TD-09): dict with state (OPEN->CLOSED only; reopen = IllegalTransitionError), members (replacement, peers must exist), title, metadata. Empty members valid (schema has no minItems; STR-005).
- Wire offset persistence: wire `revision` = expected current revision, `read_through_position` = requested position. Wire record persistence: server fields dropped, `payload_digest` must equal recomputed TD-02 digest.
- Existing unit test_offset_cas_flow advanced to 5 on an empty stream; contradicts TD-10, so it now appends 10 records first (spec-driven change, not an oracle weakening).
- PROP-009: no size limit is configured in M1; envelope 0..1 MiB+1 asserts exact survival across reopen.

Gate: pytest tests/m1 tests/unit/m1 = 82 passed; m1_traceability --upto 1 rc=0 (wave 1 39/39); validate_package PASS.
