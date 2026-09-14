# Atomicity Matrix - DispatchService

| Workflow | Touched Tables/Rows | Atomicity Requirement | Test Coverage |
|---|---|---|---|
| record_artifact_manifest | ArtifactManifest (insert), ArtifactMetadata (insert) | Manifest and item metadata must be persisted together. | MISSING (closed via test_missing_fault_boundaries.py) |
| mark_artifacts_orphaned_if_manifest_exists | ArtifactManifest (update), ArtifactMetadata (update) | Marking the manifest and all associated items orphaned must succeed or fail together. | MISSING (closed via test_missing_fault_boundaries.py) |
| admit_request | ClientRequestBinding (insert), CommandIdempotencyBinding (insert), Request (insert), Lease (insert), AdmissionReceipt (insert), OutboxEvent (insert) | Identity bindings, initial request/lease state, and outbox event must all commit atomically. | test_request_attempt_fault_boundaries.py |
| reject_policy | Request (CAS update), OutboxEvent (insert) | Setting request terminal state and emitting outbox event must commit atomically. | MISSING (closed via test_missing_fault_boundaries.py) |
| prepare_request | Request (CAS update), Lease (read), SessionBinding (read) | Updating the request state to PREPARED must commit atomically. | test_request_attempt_fault_boundaries.py |
| create_attempt | Attempt (insert with monotonic number) | Attempt snapshot insertion and attempt number generation must be atomic. | test_request_attempt_fault_boundaries.py |
| fail_pre_dispatch | Request (CAS update), Attempt (CAS update), OutboxEvent x2 (insert) | Terminating request and attempt, and emitting state + terminal outbox events must commit together. | test_request_attempt_fault_boundaries.py |
| record_dispatch_intent | Request (CAS update), Attempt (CAS update), Lease (CAS update), OutboxEvent (insert) | Dispatch bundle CAS (request, attempt, lease) and outbox emission must commit atomically. | test_request_attempt_fault_boundaries.py |
| record_dispatch_intent_and_reserve_artifacts | Request (CAS update), Attempt (CAS update), Lease (CAS update), OutboxEvent (insert), ArtifactManifest/Metadata (update) | Dispatch bundle CAS, outbox emission, and artifact reservation must commit atomically. | MISSING (closed via test_missing_fault_boundaries.py) |
| record_start_uncertain | Request (CAS update), Attempt (CAS update), OutboxEvent (insert) | Request/attempt CAS and outbox emission must commit atomically. | MISSING (closed via test_missing_fault_boundaries.py) |
| record_running | Request (CAS update), Attempt (CAS update), Lease (CAS update), OutboxEvent (insert) | Dispatch bundle CAS (binding process identity) and outbox emission must commit atomically. | MISSING (closed via test_missing_fault_boundaries.py) |
| begin_cancellation | Request (CAS update), Attempt (CAS update) | Request and attempt CAS to CANCELLING must commit atomically. | MISSING (closed via test_missing_fault_boundaries.py) |
| begin_assessment | Request (CAS update), Attempt (CAS update) | Request and attempt CAS to ASSESSING must commit atomically. | MISSING (closed via test_missing_fault_boundaries.py) |
| complete_attempt | Request (CAS update), Attempt (CAS update), OutboxEvent x2 (insert) | Terminal request/attempt CAS and emitting state/terminal outbox events must commit together. | MISSING (closed via test_missing_fault_boundaries.py) |
| authorize_retry | Request (CAS update), Attempt (CAS update), Lease (insert) | Request/attempt CAS and rotating to a new RESERVED lease must commit atomically. | MISSING (closed via test_missing_fault_boundaries.py) |
| create_session_and_lease | SessionBinding (insert), Lease (insert) | Session binding creation and initial lease creation must commit atomically. | test_session_lease_fault_boundaries.py |
| resume_session | SessionBinding (CAS update) | Session binding generation/fingerprint update must commit atomically. | MISSING (closed via test_missing_fault_boundaries.py) |
| renew_lease | Lease (CAS update) | Lease heartbeat extension must commit atomically. | MISSING (closed via test_missing_fault_boundaries.py) |
| close_lease | Lease (CAS update) | Lease CAS to CLOSED must commit atomically. | MISSING (closed via test_missing_fault_boundaries.py) |
| complete_attempt_with_artifacts_and_lease | Request (CAS update), Attempt (CAS update), Lease (CAS update), OutboxEvent x2 (insert), ArtifactManifest/Metadata (update) | Terminal request/attempt CAS, outbox emission, artifact consumption, and lease closure must commit atomically. | MISSING (closed via test_missing_fault_boundaries.py) |
| recover_lease | Lease (CAS update), RecoveryReceipt (insert) | Lease CAS fencing and recovery receipt insertion must commit atomically. | test_session_lease_fault_boundaries.py |
