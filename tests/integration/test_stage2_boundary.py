"""Tests for Stage 2 command boundary."""

import pytest
from pathlib import Path
from typing import Any

from peerhub.application.api import ApplicationAPI, AdmissionInputsProvider, AdmissionInputs
from peerhub.application.commands import AdmitDispatch, GetDispatchRequest, GetDispatchLease, SubmissionMetadata
from peerhub.application.legacy import LegacyTranslator, LegacyActionCall, KnownLegacyActionNotBacked, TranslatedCommand, LEGACY_CATALOG
from peerhub.client import Client
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import CommandEnvelope, CommandSuccess, CommandFailure, ErrorCode, PROTOCOL_MAJOR, PROTOCOL_MINOR, SCHEMA_VERSION, IdempotencyDisposition
from peerhub.core.ports import RequestContext
from peerhub.runtime import create_runtime, RuntimeContext
from peerhub.core.context import Clock, IdSource, PathLayout


class FakeClock:
    def now(self) -> int: return 1000

class FakeIdSource:
    def new_id(self, namespace: str) -> str:
        return f"{namespace}-123"


class FakeAdmissionProvider:
    def resolve(self, command: AdmitDispatch, caller: RequestContext) -> AdmissionInputs:
        class FakeInputs:
            route_request_factory = lambda snap: None # fake
            dispatch_policy_revision = 1
            session_id = "sess-1"
            owner_principal_id = caller.principal
            owner_instance_id = "inst-1"
            authority_epoch = 1
            heartbeat_timeout_ms = 5000
            owner_peer_id = "peer-1"
        return FakeInputs()


@pytest.fixture
def runtime_setup(tmp_path: Path):
    layout = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext(
        workspace_home_id="home-1",
        paths=layout,
        clock=FakeClock(),
        ids=FakeIdSource(),
    )
    # The default create_runtime gives us the composed services
    rt = create_runtime(context, admission_provider=FakeAdmissionProvider())
    
    caller = RequestContext(principal="user-1", client_id="client-1")
    client = Client(rt.application_api, caller=caller)
    
    yield rt, client, caller
    rt.close()


def test_admit_success(runtime_setup, monkeypatch):
    rt, client, caller = runtime_setup
    
    from unittest.mock import MagicMock
    from peerhub.application.workflows import AdmissionWorkflowResult
    from peerhub.dispatch.contract import RequestSnapshot, AdmissionReceipt, RequestState, LeaseState
    from peerhub.core.protocol import CommandID
    
    req = MagicMock()
    req.command_id = "cmd-123"
    req.state = RequestState.ADMITTED
    req.revision = 1
    req.lease_id = "lease-1"
    req.selected_peer_instance_id = "inst-1"
    req.selected_profile_id = "prof-1"
    req.route_decision_digest = "digest"
    receipt = MagicMock()
    receipt.admission_receipt_id = "rec-123"
    
    lease = MagicMock()
    lease.lease_id = "lease-1"
    lease.state = LeaseState.RESERVED
    
    res = AdmissionWorkflowResult(
        projected_terminal_events=0,
        admission_snapshot=None,
        route=None,
        dispatch_admission=(req, receipt, lease)
    )
    mock_workflows = MagicMock()
    mock_workflows.admit_request.return_value = res
    rt.application_api._workflows = mock_workflows

    cmd = AdmitDispatch(
        submission=SubmissionMetadata(
            client_request_id="req-1",
            correlation_id="corr-1",
            client_id="client-1",
            actor_id="user-1",
            scope={},
            idempotency_key="idem-1",
            expected_policy_revision=None,
            expected_configuration_revision=None,
            client_timestamp=1000,
        ),
        prompt="hello",
        requested_capabilities=(),
        profile_constraints={},
        completion_contract={
            "kind": "DELIVERY_ONLY",
            "replay_safe": False,
        },
        session_policy={},
    )
    
    outcome = client.submit(cmd)
    assert isinstance(outcome, CommandSuccess)
    frozen_contract = mock_workflows.admit_request.call_args.kwargs[
        "completion_contract"
    ]
    assert frozen_contract.replay_safe is False


def test_missing_idempotency_key(runtime_setup):
    rt, client, caller = runtime_setup
    
    cmd = AdmitDispatch(
        submission=SubmissionMetadata(
            client_request_id="req-1",
            correlation_id="corr-1",
            client_id="client-1",
            actor_id="user-1",
            scope={},
            idempotency_key=None, # Missing idempotency key
            expected_policy_revision=None,
            expected_configuration_revision=None,
            client_timestamp=1000,
        ),
        prompt="hello",
        requested_capabilities=(),
        profile_constraints={},
        completion_contract={"kind": "DELIVERY_ONLY"},
        session_policy={},
    )
    
    outcome = client.submit(cmd)
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.MISSING_IDEMPOTENCY_KEY


def test_admit_validation_error(runtime_setup):
    rt, client, caller = runtime_setup
    
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-1",
        correlation_id="corr-1",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.admit",
        params={"prompt": "hello", "unexpected_extra": 123},
        idempotency_key="idem-1",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    
    outcome = rt.application_api.submit(envelope, caller=caller)
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.INVALID_PARAMS
    assert "unexpected_extra" in outcome.error.message


@pytest.mark.parametrize(
    "completion_contract",
    (
        {"replay_safe": "false"},
        {"kind": "NOT_A_COMPLETION_KIND"},
        {"kind": "ARTIFACT_REQUIRED", "requirements": []},
        {"requirements": {"field": "status"}},
        {"requirements": ["not-an-object"]},
        {"unexpected_extra": True},
    ),
)
def test_admit_rejects_malformed_completion_contract_at_decode(
    runtime_setup,
    completion_contract: dict[str, Any],
):
    rt, _, caller = runtime_setup
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-malformed-contract",
        correlation_id="corr-malformed-contract",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.admit",
        params={
            "prompt": "hello",
            "completion_contract": completion_contract,
        },
        idempotency_key="idem-malformed-contract",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )

    outcome = rt.application_api.submit(envelope, caller=caller)

    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code is ErrorCode.INVALID_PARAMS
    assert (
        outcome.error.execution_certainty
        is ExecutionCertainty.NOT_STARTED
    )

def test_unauthorized_client(runtime_setup):
    rt, client, _ = runtime_setup
    
    cmd = AdmitDispatch(
        submission=SubmissionMetadata(
            client_request_id="req-1",
            correlation_id="corr-1",
            client_id="client-WRONG",
            actor_id="user-1",
            scope={},
            idempotency_key="idem-1",
            expected_policy_revision=None,
            expected_configuration_revision=None,
            client_timestamp=1000,
        ),
        prompt="hello",
        requested_capabilities=(),
        profile_constraints={},
        completion_contract={},
        session_policy={},
    )
    
    outcome = client.submit(cmd)
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.CLIENT_UNKNOWN


def test_unbacked_command(tmp_path: Path):
    layout = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext(
        workspace_home_id="home-1",
        paths=layout,
        clock=FakeClock(),
        ids=FakeIdSource(),
    )
    # We create the runtime WITHOUT a provider to simulate NOT_BACKED
    rt = create_runtime(context, admission_provider=None)
    caller = RequestContext(principal="user-1", client_id="client-1")
    
    cmd = AdmitDispatch(
        submission=SubmissionMetadata(
            client_request_id="req-1",
            correlation_id="corr-1",
            client_id="client-1",
            actor_id="user-1",
            scope={},
            idempotency_key="idem-1",
            expected_policy_revision=None,
            expected_configuration_revision=None,
            client_timestamp=1000,
        ),
        prompt="hello",
        requested_capabilities=(),
        profile_constraints={},
        completion_contract={},
        session_policy={},
    )
    
    # The registration checks availability, but since we modify it after init we must re-register
    # In ApplicationAPI the availability is set during init. If we want it NOT_BACKED we can test with a raw envelope.
    
    env = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="r", correlation_id="c", client_id="client-1", actor_id=None, scope={},
        method="dispatch.admit", params={}, idempotency_key="i", expected_policy_revision=None, expected_configuration_revision=None, client_timestamp=0
    )
    
    # Registration availability should already be NOT_BACKED
    
    outcome = rt.application_api.submit(env, caller=caller)
    assert not outcome.ok
    assert outcome.error.code == ErrorCode.COMMAND_NOT_BACKED


def test_legacy_translation_ask():
    translator = LegacyTranslator()
    sub = SubmissionMetadata(
        client_request_id="r", correlation_id="c", client_id="c1", actor_id=None, scope={},
        idempotency_key="i", expected_policy_revision=None, expected_configuration_revision=None, client_timestamp=0
    )
    
    out = translator.translate(LegacyActionCall(action="ask", arguments={"prompt": "test"}), sub)
    assert isinstance(out, TranslatedCommand)
    assert out.command.method == "dispatch.submit"


def test_legacy_translation_unbacked():
    translator = LegacyTranslator()
    sub = SubmissionMetadata(
        client_request_id="r", correlation_id="c", client_id="c1", actor_id=None, scope={},
        idempotency_key="i", expected_policy_revision=None, expected_configuration_revision=None, client_timestamp=0
    )
    
    out = translator.translate(LegacyActionCall(action="status", arguments={}), sub)
    assert isinstance(out, KnownLegacyActionNotBacked)
    assert out.legacy_action == "status"
    assert out.target_method == LEGACY_CATALOG["status"]


def test_admit_rejected_internal_error(runtime_setup):
    rt, client, caller = runtime_setup
    
    from unittest.mock import MagicMock
    from peerhub.application.workflows import AdmissionWorkflowResult
    
    res = AdmissionWorkflowResult(
        projected_terminal_events=0,
        admission_snapshot=None,
        route=MagicMock(error_code="exhausted"),
        dispatch_admission=None
    )
    mock_workflows = MagicMock()
    mock_workflows.admit_request.return_value = res
    rt.application_api._workflows = mock_workflows

    cmd = AdmitDispatch(
        submission=SubmissionMetadata(
            client_request_id="req-1",
            correlation_id="corr-1",
            client_id="client-1",
            actor_id="user-1",
            scope={},
            idempotency_key="idem-1",
            expected_policy_revision=None,
            expected_configuration_revision=None,
            client_timestamp=1000,
        ),
        prompt="hello",
        requested_capabilities=(),
        profile_constraints={},
        completion_contract={"kind": "DELIVERY_ONLY"},
        session_policy={},
    )
    
    outcome = client.submit(cmd)
    
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.INTERNAL_ERROR
    assert outcome.error.details.get("exception") == "RuntimeError"


def test_req_get_validation_error(runtime_setup):
    rt, client, caller = runtime_setup
    
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-1",
        correlation_id="corr-1",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.request.get",
        params={"target_command_id": "cmd-123", "unexpected_extra": 123},
        idempotency_key="idem-1",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    
    outcome = rt.application_api.submit(envelope, caller=caller)
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.INVALID_PARAMS
    assert "unexpected_extra" in outcome.error.message

    # Test missing required field
    envelope2 = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-1",
        correlation_id="corr-1",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.request.get",
        params={},  # missing target_command_id
        idempotency_key="idem-1",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    
    outcome2 = rt.application_api.submit(envelope2, caller=caller)
    assert isinstance(outcome2, CommandFailure)
    assert outcome2.error.code == ErrorCode.INVALID_PARAMS
    assert "target_command_id" in outcome2.error.message


def test_request_get_enforces_resource_ownership(runtime_setup):
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    from peerhub.dispatch.contract import RequestState

    rt, _, caller = runtime_setup
    dispatch = MagicMock()
    dispatch.get_request.return_value = SimpleNamespace(
        command_id="cmd-123",
        client_id="client-1",
        client_request_id="original-request",
        correlation_id="original-correlation",
        authenticated_principal="user-1",
        command_type="dispatch.admit",
        idempotency_key="original-idempotency",
        payload_digest="0" * 64,
        scope={},
        expected_policy_revision=None,
        expected_configuration_revision=None,
        policy_revision=1,
        configuration_revision=1,
        selected_peer_instance_id="instance-1",
        selected_profile_id="profile-1",
        route_decision_digest="1" * 64,
        lease_id="lease-123",
        state=RequestState.ADMITTED,
        revision=1,
        created_at=1000,
        updated_at=1000,
        terminal_error_code=None,
    )
    rt.application_api._dispatch = dispatch

    own_envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="lookup-own",
        correlation_id="corr-own",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.request.get",
        params={"target_command_id": "cmd-123"},
        idempotency_key=None,
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    own = rt.application_api.submit(own_envelope, caller=caller)
    assert isinstance(own, CommandSuccess)
    assert own.result["command_id"] == "cmd-123"

    other_caller = RequestContext(principal="user-2", client_id="client-2")
    other_envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="lookup-other",
        correlation_id="corr-other",
        client_id="client-2",
        actor_id="user-2",
        scope={},
        method="dispatch.request.get",
        params={"target_command_id": "cmd-123"},
        idempotency_key=None,
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    other = rt.application_api.submit(other_envelope, caller=other_caller)
    assert isinstance(other, CommandFailure)
    assert other.error.code is ErrorCode.CLIENT_UNKNOWN
    assert other.error.execution_certainty is ExecutionCertainty.NOT_STARTED


def test_lease_get_validation_error(runtime_setup):
    rt, client, caller = runtime_setup
    
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-1",
        correlation_id="corr-1",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.lease.get",
        params={"lease_id": "lease-123", "unexpected_extra": 123},
        idempotency_key="idem-1",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    
    outcome = rt.application_api.submit(envelope, caller=caller)
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.INVALID_PARAMS
    assert "unexpected_extra" in outcome.error.message

    # Test missing required field
    envelope2 = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-1",
        correlation_id="corr-1",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.lease.get",
        params={},  # missing lease_id
        idempotency_key="idem-1",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )
    
    outcome2 = rt.application_api.submit(envelope2, caller=caller)
    assert isinstance(outcome2, CommandFailure)
    assert outcome2.error.code == ErrorCode.INVALID_PARAMS
    assert "lease_id" in outcome2.error.message


def test_lease_get_success(runtime_setup):
    rt, client, caller = runtime_setup
    
    from unittest.mock import MagicMock
    from peerhub.dispatch.contract import LeaseSnapshot, LeaseState, LeaseFenceTuple, ProcessBirthIdentity
    from peerhub.core.protocol import CommandID

    mock_dispatch = MagicMock()
    fence = LeaseFenceTuple(
        session_id="sess-1",
        lease_id="lease-123",
        fencing_token=1,
        revision=42,
        owner_principal_id="principal-1",
        owner_instance_id="instance-1",
        owner_process_birth_identity=ProcessBirthIdentity(
            pid=9999,
            process_creation_time=1000,
        ),
        command_id=CommandID("cmd-123"),
        authority_epoch=1,
        attempt_id="att-1",
        owner_peer_id="peer-1",
    )
    lease = LeaseSnapshot(
        lease_id="lease-123",
        session_id="sess-1",
        fence=fence,
        state=LeaseState.RESERVED,
        heartbeat_expires_at=1000,
        created_at=1000,
        updated_at=1000
    )
    mock_dispatch.get_lease.return_value = lease
    request = MagicMock()
    request.client_id = "client-1"
    mock_dispatch.get_request.return_value = request
    rt.application_api._dispatch = mock_dispatch

    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-1",
        correlation_id="corr-1",
        client_id="client-1",
        actor_id="user-1",
        scope={},
        method="dispatch.lease.get",
        params={"lease_id": "lease-123"},
        idempotency_key="idem-1",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )

    outcome = rt.application_api.submit(envelope, caller=caller)
    assert isinstance(outcome, CommandSuccess)
    assert outcome.result["revision"] == 42
    assert outcome.result["fence_revision"] == 42
    assert outcome.result["lease_id"] == "lease-123"


def test_lease_get_enforces_resource_ownership(runtime_setup):
    rt, _, caller = runtime_setup

    from unittest.mock import MagicMock
    from peerhub.dispatch.contract import LeaseSnapshot, LeaseState, LeaseFenceTuple, ProcessBirthIdentity
    from peerhub.core.protocol import CommandID

    mock_dispatch = MagicMock()
    fence = LeaseFenceTuple(
        session_id="sess-1",
        lease_id="lease-123",
        fencing_token=1,
        revision=42,
        owner_principal_id="principal-1",
        owner_instance_id="instance-1",
        owner_process_birth_identity=ProcessBirthIdentity(
            pid=9999,
            process_creation_time=1000,
        ),
        command_id=CommandID("cmd-123"),
        authority_epoch=1,
        attempt_id="att-1",
        owner_peer_id="peer-1",
    )
    lease = LeaseSnapshot(
        lease_id="lease-123",
        session_id="sess-1",
        fence=fence,
        state=LeaseState.RESERVED,
        heartbeat_expires_at=1000,
        created_at=1000,
        updated_at=1000,
    )
    mock_dispatch.get_lease.return_value = lease
    request = MagicMock()
    request.client_id = "client-1"
    mock_dispatch.get_request.return_value = request
    rt.application_api._dispatch = mock_dispatch

    other_caller = RequestContext(principal="user-2", client_id="client-2")
    envelope = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-other",
        correlation_id="corr-other",
        client_id="client-2",
        actor_id="user-2",
        scope={},
        method="dispatch.lease.get",
        params={"lease_id": "lease-123"},
        idempotency_key="idem-other",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )

    outcome = rt.application_api.submit(envelope, caller=other_caller)
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code is ErrorCode.CLIENT_UNKNOWN
    assert outcome.error.execution_certainty is ExecutionCertainty.NOT_STARTED


def test_admit_route_exhausted(runtime_setup):
    rt, client, caller = runtime_setup
    
    from unittest.mock import MagicMock
    from peerhub.application.workflows import AdmissionWorkflowResult
    
    res = AdmissionWorkflowResult(
        projected_terminal_events=0,
        admission_snapshot=None,
        route=MagicMock(error_code="exhausted"),
        dispatch_admission=None
    )
    mock_workflows = MagicMock()
    mock_workflows.admit_request.return_value = res
    rt.application_api._workflows = mock_workflows

    cmd = AdmitDispatch(
        submission=SubmissionMetadata(
            client_request_id="req-exhausted",
            correlation_id="corr-1",
            client_id="client-1",
            actor_id="user-1",
            scope={},
            idempotency_key="idem-1",
            expected_policy_revision=None,
            expected_configuration_revision=None,
            client_timestamp=1000,
        ),
        prompt="hello",
        requested_capabilities=(),
        profile_constraints={},
        completion_contract={"kind": "DELIVERY_ONLY"},
        session_policy={},
    )
    
    outcome = client.submit(cmd)
    
    assert isinstance(outcome, CommandFailure)
    assert outcome.error.code == ErrorCode.INTERNAL_ERROR

