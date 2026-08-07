"""Tests for Stage 2 command boundary."""

import pytest
from pathlib import Path
from typing import Any

from peerhub.application.api import ApplicationAPI, AdmissionInputsProvider, AdmissionInputs
from peerhub.application.commands import AdmitDispatch, GetDispatchRequest, GetDispatchLease, SubmissionMetadata
from peerhub.application.legacy import LegacyTranslator, LegacyActionCall, KnownLegacyActionNotBacked, TranslatedCommand, LEGACY_CATALOG
from peerhub.client import Client
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
        completion_contract={"kind": "DELIVERY_ONLY"},
        session_policy={},
    )
    
    outcome = client.submit(cmd)
    assert isinstance(outcome, CommandSuccess)


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

