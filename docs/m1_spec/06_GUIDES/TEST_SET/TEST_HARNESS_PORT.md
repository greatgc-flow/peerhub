# Test-side Harness Port

테스트가 production class/module 이름을 강제하지 않도록 **test-only adapter contract**를 둡니다. 구현체는 application API를 얇게 연결하며 business logic을 넣지 않습니다.

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
`append_request`에는 server-owned `record_id/position/payload_digest/appended_at`가 없습니다. Idempotency scope는 TD-20을 따릅니다.

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
모든 authoritative delivery write는 current claim token/generation을 검증할 수 있어야 합니다.

## Observation harness
```text
capture(subject_ref, kind, source_adapter) -> Observation
latest(subject_ref, kind, read_at) -> ObservationView
list_for_resource_pool(resource_pool_ref) -> [ObservationView]
```
ManualClock + persisted capture ordinal을 노출하여 TTL/clock-skew/equal-time test를 결정론적으로 만듭니다.

## Diag harness
```text
render_diag(workspace, sections?) -> DiagnosticReport
```
mutation/runtime-control port 접근 시 test adapter가 즉시 실패합니다.

## 원칙
- production public API 아님.
- production naming/DI framework 변경과 test contract 분리.
- adapter 자체는 thin translation only.
