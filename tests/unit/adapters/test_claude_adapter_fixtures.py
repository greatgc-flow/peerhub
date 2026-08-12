"""
Note: The vendor-error byte patterns in these fixtures are synthetic,
best-effort constructions, as there is no real capture available.
They are marked TEST NEEDED for DIR-004 promotion to empirical_probe
pending a real captured failure transcript from a live invocation.
"""
import pytest
from peerhub.adapters.claude_adapter import ClaudeOutputDecoder, RealClaudeAdapter
from peerhub.adapters.contract import DecoderEventKind, InvocationPlan, TransportKind, TransportLimits, SessionAction
from peerhub.core.execution import ProcessTerminalEvidence
from peerhub.core.protocol import ErrorCode

def test_claude_decoder_session_invalid():
    decoder = ClaudeOutputDecoder()
    decoder.feed(b'{"is_error": true, "error_type": "invalid_session"}')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    assert decoded.events[0].kind == DecoderEventKind.VENDOR_ERROR
    assert decoded.events[0].payload["normalized_kind"] == "session_invalid"

def test_claude_decoder_quota_exhausted():
    decoder = ClaudeOutputDecoder()
    decoder.feed(b'{"is_error": true, "error_type": "over_quota"}')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    assert decoded.events[0].kind == DecoderEventKind.VENDOR_ERROR
    assert decoded.events[0].payload["normalized_kind"] == "quota_exhausted"

def test_claude_decoder_provider_down():
    decoder = ClaudeOutputDecoder()
    decoder.feed(b'{"is_error": true, "error_type": "provider_down"}')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    assert decoded.events[0].kind == DecoderEventKind.VENDOR_ERROR
    assert decoded.events[0].payload["normalized_kind"] == "provider_unavailable"

def test_claude_decoder_invocation_plan_rejected():
    decoder = ClaudeOutputDecoder()
    decoder.feed(b'{"is_error": true, "error_type": "invalid_request_error"}')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    assert decoded.events[0].kind == DecoderEventKind.VENDOR_ERROR
    assert decoded.events[0].payload["normalized_kind"] == "invocation_plan_rejected"

def test_claude_decoder_stderr_model_operand_invalid():
    decoder = ClaudeOutputDecoder()
    decoder.feed(b'model_operand_invalid')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    assert decoded.events[0].kind == DecoderEventKind.VENDOR_ERROR
    assert decoded.events[0].payload["normalized_kind"] == "invocation_plan_rejected"

def test_claude_interpret_output_nonzero_exit_not_internal_error():
    adapter = RealClaudeAdapter()
    plan = InvocationPlan(
        argv=("test",), cwd_reference=".", environment_delta={}, transport=TransportKind.PIPE,
        stdin_payload=None, limits=TransportLimits(1, 1, 1), redacted_display="test",
        artifacts=(), session_action=SessionAction.NONE
    )
    process = ProcessTerminalEvidence(exit_code=1)

    # Not empty or malformed
    chunks = [b'{"result": "ok"}']
    assessment = adapter.interpret_output(plan, process, chunks)
    assert assessment.protocol_failure is None

    # Malformed JSON should still yield INTERNAL_ERROR
    chunks_malformed = [b'{"bad json']
    assessment_malformed = adapter.interpret_output(plan, process, chunks_malformed)
    assert assessment_malformed.protocol_failure == ErrorCode.INTERNAL_ERROR

def test_claude_interpret_output_with_vendor_error_yields_no_protocol_failure():
    adapter = RealClaudeAdapter()
    plan = InvocationPlan(
        argv=("test",), cwd_reference=".", environment_delta={}, transport=TransportKind.PIPE,
        stdin_payload=None, limits=TransportLimits(1, 1, 1), redacted_display="test",
        artifacts=(), session_action=SessionAction.NONE
    )
    process = ProcessTerminalEvidence(exit_code=1)
    chunks = [b'{"is_error": true, "error_type": "invalid_session"}']

    decoder = adapter.new_decoder(plan)
    for chunk in chunks:
        decoder.feed(chunk)
    decoded = decoder.finalize()
    assert any(e.kind == DecoderEventKind.VENDOR_ERROR for e in decoded.events)

    assessment = adapter.interpret_output(plan, process, chunks)
    assert assessment.protocol_failure is None

class FakeCompletionContract:
    @property
    def contract_id(self) -> str:
        return "fake-contract"

def test_claude_plan_invocation_session_resume():
    adapter = RealClaudeAdapter()
    from peerhub.adapters.contract import ProfileDescriptor, AdapterRequest, SessionHint
    profile = ProfileDescriptor(profile_id="cc.standard", profile_class="tier", supports_reasoning_effort=False)
    request = AdapterRequest(
        request_id="req-1",
        prompt_content="Hello",
        prompt_reference=None,
        workspace_scope=".",
        profile_id="cc.standard",
        requested_session_action=SessionAction.RESUME,
        completion_contract=FakeCompletionContract(),
    )
    session = SessionHint(external_session_id="session-123", adapter_fingerprint=None, session_generation=None)
    limits = TransportLimits(1, 1, 1)

    plan = adapter.plan_invocation(request, profile, session, limits)
    assert plan.argv[-2:] == ("--resume", "session-123")
    assert plan.redacted_display == "claude.cmd -p <redacted> --output-format json --resume <redacted>"
    assert plan.session_action == SessionAction.RESUME

def test_claude_plan_invocation_session_resume_missing_id():
    adapter = RealClaudeAdapter()
    from peerhub.adapters.contract import ProfileDescriptor, AdapterRequest, SessionHint
    profile = ProfileDescriptor(profile_id="cc.standard", profile_class="tier", supports_reasoning_effort=False)
    request = AdapterRequest(
        request_id="req-1",
        prompt_content="Hello",
        prompt_reference=None,
        workspace_scope=".",
        profile_id="cc.standard",
        requested_session_action=SessionAction.RESUME,
        completion_contract=FakeCompletionContract(),
    )
    limits = TransportLimits(1, 1, 1)

    with pytest.raises(ValueError, match="external_session_id is required for RESUME"):
        adapter.plan_invocation(request, profile, None, limits)

    session_no_id = SessionHint(external_session_id=None, adapter_fingerprint=None, session_generation=None)
    with pytest.raises(ValueError, match="external_session_id is required for RESUME"):
        adapter.plan_invocation(request, profile, session_no_id, limits)

def test_claude_plan_invocation_session_none():
    adapter = RealClaudeAdapter()
    from peerhub.adapters.contract import ProfileDescriptor, AdapterRequest, SessionHint
    profile = ProfileDescriptor(profile_id="cc.standard", profile_class="tier", supports_reasoning_effort=False)
    request = AdapterRequest(
        request_id="req-1",
        prompt_content="Hello",
        prompt_reference=None,
        workspace_scope=".",
        profile_id="cc.standard",
        requested_session_action=SessionAction.NONE,
        completion_contract=FakeCompletionContract(),
    )
    limits = TransportLimits(1, 1, 1)
    session = SessionHint(external_session_id="session-123", adapter_fingerprint=None, session_generation=None)

    plan = adapter.plan_invocation(request, profile, session, limits)
    assert "--resume" not in plan.argv
    assert plan.session_action == SessionAction.NONE
