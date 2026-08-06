"""Characterization tests for previously untested dispatch workflows."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterator

import pytest

from peerhub.core.protocol import (
    CommandEnvelope,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    ErrorCode,
)
from peerhub.dispatch.contract import (
    CompletionContract,
    CompletionContractKind,
    LeaseCreateRequest,
    SessionBindingKey,
    LeaseRenewRequest,
    LeaseCloseRequest,
    LeaseFenceTuple,
    ProcessBirthIdentity,
    ArtifactManifestRecord,
    ArtifactMetadata,
    AskResult,
    ExecutionOutcome,
    ExecutionCertainty,
    ProtocolAssessment,
    CompletionAssessment,
    CompletionAssessmentState,
    SessionResumeRequest,
)
from peerhub.dispatch.service import (
    DispatchService,
    FaultInjector,
    FaultPoint,
)
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource

class RaisingFaultInjector(FaultInjector):
    def __init__(self, target: str) -> None:
        self._target = target

    def hit(self, point: str) -> None:
        if point == self._target:
            raise RuntimeError(f"injected fault at {point}")

@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "missing-faults.sqlite3",
        workspace_home_id="workspace-faults",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()

@pytest.fixture
def ids() -> SequentialIdSource:
    return SequentialIdSource()

def _service(
    store: SqliteStateStore,
    *,
    fault_point: str | None = None,
    clock_start: int = 100,
    ids: SequentialIdSource | None = None,
) -> DispatchService:
    return DispatchService(
        store,
        clock=DeterministicClock(start=clock_start),
        ids=ids if ids is not None else SequentialIdSource(),
        fault_injector=(
            RaisingFaultInjector(fault_point)
            if fault_point is not None
            else None
        ),
    )

def _setup_request_and_attempt(store: SqliteStateStore, ids: SequentialIdSource):
    s = _service(store, clock_start=10, ids=ids)
    env = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR, protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION, client_request_id="cr", correlation_id="cor",
        client_id="c", actor_id="a", scope={"workspace_id": "w", "home_id": "h"},
        method="peer.ask", params={}, expected_policy_revision=1, expected_configuration_revision=1, client_timestamp=10,
        idempotency_key="ik"
    )
    cc = CompletionContract(contract_id="cc", kind=CompletionContractKind.DELIVERY_ONLY, requirements=(), replay_safe=False)
    req, _, _ = s.admit_request(
        env, authenticated_principal="ap", actor_authorized=True, completion_contract=cc,
        policy_revision=1, configuration_revision=1, selected_peer_instance_id="i",
        selected_profile_id="p", route_decision_digest="d"*64, session_id="s",
        owner_principal_id="op", owner_instance_id="oi", authority_epoch=1, heartbeat_timeout_ms=5000,
    )
    s.prepare_request(req.command_id)
    att = s.create_attempt(req.command_id)
    return req.command_id, att.attempt_id

def _manifest(att_id: str) -> ArtifactManifestRecord:
    return ArtifactManifestRecord(attempt_id=att_id, workspace_scope_id="w", staging_root_ref="r", manifest_digest="d"*64, revision=1, item_count=0, created_at=10, intent_event_id=None)

def _fence(req_id: str, att_id: str, revision: int = 3) -> LeaseFenceTuple:
    return LeaseFenceTuple(session_id="s", lease_id="lease-1", fencing_token=1, revision=revision, owner_principal_id="op", owner_instance_id="oi", owner_process_birth_identity=ProcessBirthIdentity(pid=1, process_creation_time=1), command_id=req_id, attempt_id=att_id, authority_epoch=1, owner_peer_id="")

def _verified_result() -> AskResult:
    return AskResult(
        execution=ExecutionOutcome(
            started=True,
            exit_code=0,
            timed_out=False,
            cancelled=False,
            execution_certainty=ExecutionCertainty.TERMINAL,
        ),
        protocol=ProtocolAssessment(
            parsed=True,
            response_present=True,
            vendor_completion_marker=True,
            suspected_truncation=False,
            protocol_failure=None,
        ),
        completion=CompletionAssessment(
            state=CompletionAssessmentState.VERIFIED,
            contract_kind=(
                CompletionContractKind.DELIVERY_ONLY
            ),
            evidence_refs=("terminal-receipt-01",),
        ),
        policy_revision=7,
    )

# 1. record_artifact_manifest
def test_record_artifact_manifest(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    req_id, att_id = _setup_request_and_attempt(store, ids)
    s = _service(store, clock_start=100, ids=ids)
    rec = _manifest(att_id)
    s.record_artifact_manifest(rec, [])
    with store.unit_of_work() as u:
        assert u.get_artifact_manifest(att_id) is not None

# 2. mark_artifacts_orphaned_if_manifest_exists
def test_mark_artifacts_orphaned(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    req_id, att_id = _setup_request_and_attempt(store, ids)
    s0 = _service(store, clock_start=20, ids=ids)
    rec = _manifest(att_id)
    s0.record_artifact_manifest(rec, [])
    
    s = _service(store, clock_start=100, ids=ids)
    res = s.mark_artifacts_orphaned_if_manifest_exists(att_id, failure_code="x")
    assert res is True

# 3. reject_policy
def test_reject_policy_rollback(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    s0 = _service(store, clock_start=10, ids=ids)
    env = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR, protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION, client_request_id="cr2", correlation_id="cor",
        client_id="c", actor_id="a", scope={"workspace_id": "w", "home_id": "h"},
        method="peer.ask", params={}, expected_policy_revision=1, expected_configuration_revision=1, client_timestamp=10,
        idempotency_key="ik2"
    )
    cc = CompletionContract(contract_id="cc", kind=CompletionContractKind.DELIVERY_ONLY, requirements=(), replay_safe=False)
    req, _, _ = s0.admit_request(
        env, authenticated_principal="ap", actor_authorized=True, completion_contract=cc,
        policy_revision=1, configuration_revision=1, selected_peer_instance_id="i",
        selected_profile_id="p", route_decision_digest="d"*64, session_id="s",
        owner_principal_id="op", owner_instance_id="oi", authority_epoch=1, heartbeat_timeout_ms=5000,
    )
    s = _service(store, fault_point=FaultPoint.BEFORE_COMMIT, clock_start=100, ids=ids)
    with pytest.raises(RuntimeError):
        s.reject_policy(req.command_id, error_code=ErrorCode.POLICY_STALE)
    with store.unit_of_work() as u:
        r = u.get_request(req.command_id)
        assert r.state.value != "REJECTED_POLICY"

# 4. record_dispatch_intent_and_reserve_artifacts
def test_reserve_artifacts_rollback(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    req_id, att_id = _setup_request_and_attempt(store, ids)
    s0 = _service(store, clock_start=20, ids=ids)
    rec = _manifest(att_id)
    s0.record_artifact_manifest(rec, [])
    
    s = _service(store, fault_point=FaultPoint.BEFORE_COMMIT, clock_start=100, ids=ids)
    with pytest.raises(RuntimeError):
        s.record_dispatch_intent_and_reserve_artifacts(req_id, att_id, expected_manifest_digest="d"*64)
    with store.unit_of_work() as u:
        r = u.get_request(req_id)
        assert r.state.value != "DISPATCHING"

# 5. record_start_uncertain
def test_start_uncertain_rollback(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    req_id, att_id = _setup_request_and_attempt(store, ids)
    s0 = _service(store, clock_start=20, ids=ids)
    s0.record_dispatch_intent(req_id, att_id)
    
    s = _service(store, fault_point=FaultPoint.BEFORE_COMMIT, clock_start=100, ids=ids)
    with pytest.raises(RuntimeError):
        s.record_start_uncertain(req_id, att_id)
    with store.unit_of_work() as u:
        r = u.get_request(req_id)
        assert r.state.value != "START_UNCERTAIN"

# 6. record_running
def test_running_rollback(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    req_id, att_id = _setup_request_and_attempt(store, ids)
    s0 = _service(store, clock_start=20, ids=ids)
    s0.record_dispatch_intent(req_id, att_id)
    
    s = _service(store, fault_point=FaultPoint.BEFORE_COMMIT, clock_start=100, ids=ids)
    identity = ProcessBirthIdentity(pid=123, process_creation_time=1000)
    with pytest.raises(RuntimeError):
        s.record_running(req_id, att_id, process_identity=identity)
    with store.unit_of_work() as u:
        r = u.get_request(req_id)
        assert r.state.value != "RUNNING"

# 7. begin_cancellation
def test_begin_cancellation_rollback(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    req_id, att_id = _setup_request_and_attempt(store, ids)
    s0 = _service(store, clock_start=20, ids=ids)
    s0.record_dispatch_intent(req_id, att_id)
    identity = ProcessBirthIdentity(pid=1, process_creation_time=1)
    s0.record_running(req_id, att_id, process_identity=identity)

    s = _service(store, fault_point=FaultPoint.BEFORE_COMMIT, clock_start=100, ids=ids)
    with pytest.raises(RuntimeError):
        s.begin_cancellation(req_id, att_id)
    with store.unit_of_work() as u:
        r = u.get_request(req_id)
        assert r.state.value != "CANCELLING"

# 8. begin_assessment
def test_begin_assessment_rollback(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    req_id, att_id = _setup_request_and_attempt(store, ids)
    s0 = _service(store, clock_start=20, ids=ids)
    s0.record_dispatch_intent(req_id, att_id)
    identity = ProcessBirthIdentity(pid=1, process_creation_time=1)
    s0.record_running(req_id, att_id, process_identity=identity)
    s0.begin_cancellation(req_id, att_id)

    s = _service(store, fault_point=FaultPoint.BEFORE_COMMIT, clock_start=100, ids=ids)
    with pytest.raises(RuntimeError):
        s.begin_assessment(req_id, att_id)
    with store.unit_of_work() as u:
        r = u.get_request(req_id)
        assert r.state.value != "ASSESSING"

# 9. complete_attempt
def test_complete_attempt_rollback(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    req_id, att_id = _setup_request_and_attempt(store, ids)
    s0 = _service(store, clock_start=20, ids=ids)
    s0.record_dispatch_intent(req_id, att_id)
    identity = ProcessBirthIdentity(pid=1, process_creation_time=1)
    s0.record_running(req_id, att_id, process_identity=identity)
    s0.begin_assessment(req_id, att_id)

    s = _service(store, fault_point=FaultPoint.BEFORE_COMMIT, clock_start=100, ids=ids)
    res = _verified_result()
    with pytest.raises(RuntimeError):
        s.complete_attempt(req_id, att_id, result=res, transport="t", started_at=10, process_integrity=True)
    with store.unit_of_work() as u:
        r = u.get_request(req_id)
        assert r.state.value != "COMPLETED"

# 10. authorize_retry
def test_authorize_retry_rollback(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    req_id, att_id = _setup_request_and_attempt(store, ids)
    s0 = _service(store, clock_start=20, ids=ids)
    s0.record_dispatch_intent(req_id, att_id)
    s0.record_start_uncertain(req_id, att_id)

    s = _service(store, fault_point=FaultPoint.AFTER_LEASE_WRITE, clock_start=100, ids=ids)
    with pytest.raises(RuntimeError):
        s.authorize_retry(req_id, att_id, reconciliation_complete=True, heartbeat_timeout_ms=1000)
    with store.unit_of_work() as u:
        r = u.get_request(req_id)
        assert r.state.value != "PREPARED"

# 11. resume_session
def test_resume_session_rollback(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    req_id, att_id = _setup_request_and_attempt(store, ids)
    s0 = _service(store, clock_start=20, ids=ids)
    key = SessionBindingKey(workspace_scope_id="w", instance_id="i", profile_id="p", conversation_scope="c")
    s0.create_session_and_lease(key, LeaseCreateRequest(session_id="s", owner_principal_id="op", owner_instance_id="oi", owner_process_birth_identity=ProcessBirthIdentity(pid=1, process_creation_time=1), heartbeat_timeout_ms=1, command_id=req_id, attempt_id=att_id, authority_epoch=1, owner_peer_id=""), "fp", "rb")
    
    s = _service(store, fault_point=FaultPoint.BEFORE_COMMIT, clock_start=100, ids=ids)
    with pytest.raises(RuntimeError):
        s.resume_session(SessionResumeRequest(key=key, requested_session_id="s", adapter_fingerprint="fp2", session_generation=1, readiness_binding="rb2"))
    with store.unit_of_work() as u:
        b = u.get_session_binding(key)
        assert b.session_generation == 1

# 12. renew_lease
def test_renew_lease_rollback(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    req_id, att_id = _setup_request_and_attempt(store, ids)
    s0 = _service(store, clock_start=20, ids=ids)
    s0.record_dispatch_intent(req_id, att_id)
    identity = ProcessBirthIdentity(pid=1, process_creation_time=1)
    s0.record_running(req_id, att_id, process_identity=identity)

    with store.unit_of_work() as uow:
        req = uow.get_request(req_id)
    s = _service(store, fault_point=FaultPoint.BEFORE_COMMIT, clock_start=100, ids=ids)
    req_renew = LeaseRenewRequest(lease_id=req.lease_id, fence=_fence(req_id, att_id, revision=3))
    with pytest.raises(RuntimeError):
        s.renew_lease(req_renew, heartbeat_timeout_ms=10000)

# 13. close_lease
def test_close_lease_rollback(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    req_id, att_id = _setup_request_and_attempt(store, ids)
    s0 = _service(store, clock_start=20, ids=ids)
    s0.record_dispatch_intent(req_id, att_id)
    identity = ProcessBirthIdentity(pid=1, process_creation_time=1)
    s0.record_running(req_id, att_id, process_identity=identity)

    with store.unit_of_work() as uow:
        req = uow.get_request(req_id)
    s = _service(store, fault_point=FaultPoint.BEFORE_COMMIT, clock_start=100, ids=ids)
    req_close = LeaseCloseRequest(lease_id=req.lease_id, fence=_fence(req_id, att_id, revision=3))
    with pytest.raises(RuntimeError):
        s.close_lease(req_close)

# 14. complete_attempt_with_artifacts_and_lease
def test_complete_attempt_with_artifacts_rollback(store: SqliteStateStore, ids: SequentialIdSource) -> None:
    req_id, att_id = _setup_request_and_attempt(store, ids)
    s0 = _service(store, clock_start=20, ids=ids)
    s0.record_dispatch_intent(req_id, att_id)
    identity = ProcessBirthIdentity(pid=1, process_creation_time=1)
    s0.record_running(req_id, att_id, process_identity=identity)
    s0.begin_assessment(req_id, att_id)

    with store.unit_of_work() as uow:
        req = uow.get_request(req_id)
    s = _service(store, fault_point=FaultPoint.BEFORE_COMMIT, clock_start=100, ids=ids)
    res = _verified_result()
    with pytest.raises(RuntimeError):
        s.complete_attempt_with_artifacts_and_lease(req_id, att_id, result=res, transport="t", started_at=10, final_fence=_fence(req_id, att_id, revision=3), process_integrity=True)
