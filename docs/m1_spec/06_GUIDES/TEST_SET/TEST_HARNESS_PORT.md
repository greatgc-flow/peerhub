# Test-side Harness Port

We provide a **test-only adapter contract** so that tests do not enforce production class/module names. The implementation connects thinly to the application API and does not contain business logic.

## Core harness
```text
create_peer(peer_spec) -> Peer
get_peer(peer_id) -> Peer
create_stream(stream_spec) -> Stream
get_stream(stream_id) -> Stream
cas_stream(stream_id, expected_revision, mutation) -> Stream
append_record(append_request) -> Record
read_records(stream_id, after_position=0, limit?) -> [Record]
get_offset(peer_id, stream_id) -> Offset/effective-zero
cas_offset(peer_id, stream_id, expected_revision, new_position) -> Offset
state_digest() -> sha256
reopen() -> same workspace, fresh process/store instance
workspace_generation() -> generation identity
```
The `append_request` does not have server-owned `record_id/position/payload_digest/appended_at`. Idempotency scope follows TD-20.

## Bridge harness
```text
acquire_claim(peer_id, stream_id, owner_id) -> claim_token
renew_claim(claim_token)
heartbeat(claim_token)
delivery_cycle(peer_id, stream_id, runtime_target)
current_mapping(peer_id, stream_id)
execution_evidence(record_id, peer_id)
finalize_terminal(claim_token, result)
handle_control(record_id, runtime_target)
reconcile_uncertain(record_id, reconciliation_record)
```
All authoritative delivery writes must be able to verify the current claim token/generation.

## Observation harness
```text
capture(subject_ref, kind, source_adapter) -> Observation
latest(subject_ref, kind, read_at) -> ObservationView
list_for_resource_pool(resource_pool_ref) -> [ObservationView]
```
Expose ManualClock + persisted capture ordinal to make TTL/clock-skew/equal-time tests deterministic.

## Diag harness
```text
render_diag(workspace, sections?) -> DiagnosticReport
```
Test adapters immediately fail when accessing mutation/runtime-control ports.

## Principles
- Not a production public API.
- Decouples production naming/DI framework changes from the test contract.
- The adapter itself is for thin translation only.
