"""
Note: The vendor-error byte patterns in these fixtures are synthetic, 
best-effort constructions, as there is no real capture available. 
They are marked TEST NEEDED for DIR-004 promotion to empirical_probe 
pending a real captured failure transcript from a live invocation.
"""
import pytest
from peerhub.adapters.agy_adapter import AgyOutputDecoder, RealAgyAdapter
from peerhub.adapters.contract import (
    AdapterRequest,
    Capability,
    DecoderEventKind,
    InvocationPlan,
    ProfileDescriptor,
    SessionAction,
    SessionHint,
    TransportKind,
    TransportLimits,
)
from peerhub.core.execution import ProcessTerminalEvidence
from peerhub.core.protocol import ErrorCode

def test_agy_decoder_session_invalid():
    decoder = AgyOutputDecoder()
    decoder.feed(b'{"error": {"type": "session_not_found"}}')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    event = decoded.events[0]
    assert event.kind == DecoderEventKind.VENDOR_ERROR
    assert event.payload["normalized_kind"] == "session_invalid"
    assert event.payload["evidence_source"] == "structured_vendor_output"

def test_agy_decoder_invocation_plan_rejected():
    decoder = AgyOutputDecoder()
    decoder.feed(b'{"error": {"type": "invalid_model"}}')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    event = decoded.events[0]
    assert event.kind == DecoderEventKind.VENDOR_ERROR
    assert event.payload["normalized_kind"] == "invocation_plan_rejected"
    assert event.payload["evidence_source"] == "structured_vendor_output"

def test_agy_decoder_rate_limited():
    decoder = AgyOutputDecoder()
    decoder.feed(b'{"error": {"type": "rate_limit_exceeded"}}')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    event = decoded.events[0]
    assert event.kind == DecoderEventKind.VENDOR_ERROR
    assert event.payload["normalized_kind"] == "rate_limited"
    assert event.payload["evidence_source"] == "structured_vendor_output"

def test_agy_decoder_network_error():
    decoder = AgyOutputDecoder()
    decoder.feed(b'{"error": {"type": "network_error"}}')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    event = decoded.events[0]
    assert event.kind == DecoderEventKind.VENDOR_ERROR
    assert event.payload["normalized_kind"] == "network_unavailable"
    assert event.payload["evidence_source"] == "structured_vendor_output"

def test_agy_decoder_stderr_model_operand_invalid():
    decoder = AgyOutputDecoder()
    decoder.feed(b'Some stderr error: model_operand_invalid occurred')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    event = decoded.events[0]
    assert event.kind == DecoderEventKind.VENDOR_ERROR
    assert event.payload["normalized_kind"] == "invocation_plan_rejected"
    assert event.payload["evidence_source"] == "known_terminal_pattern"

def test_agy_interpret_output_nonzero_exit_not_internal_error():
    adapter = RealAgyAdapter()
    plan = InvocationPlan(
        argv=("test",), cwd_reference=".", environment_delta={}, transport=TransportKind.PIPE,
        stdin_payload=None, limits=TransportLimits(1, 1, 1), redacted_display="test",
        artifacts=(), session_action=SessionAction.NONE
    )
    process = ProcessTerminalEvidence(exit_code=1)
    
    # Not empty or malformed
    chunks = [b'{"response": "ok"}']
    assessment = adapter.interpret_output(plan, process, chunks)
    assert assessment.protocol_failure is None
    
    # Malformed JSON should still yield INTERNAL_ERROR
    chunks_malformed = [b'{"bad json']
    assessment_malformed = adapter.interpret_output(plan, process, chunks_malformed)
    assert assessment_malformed.protocol_failure == ErrorCode.INTERNAL_ERROR

def test_agy_interpret_output_with_vendor_error_yields_no_protocol_failure():
    adapter = RealAgyAdapter()
    plan = InvocationPlan(
        argv=("test",), cwd_reference=".", environment_delta={}, transport=TransportKind.PIPE,
        stdin_payload=None, limits=TransportLimits(1, 1, 1), redacted_display="test",
        artifacts=(), session_action=SessionAction.NONE
    )
    process = ProcessTerminalEvidence(exit_code=1)
    chunks = [b'{"error": {"type": "session_not_found"}}']
    
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


def _request(session_action: SessionAction) -> AdapterRequest:
    return AdapterRequest(
        request_id="req-1",
        prompt_content="Hello",
        prompt_reference=None,
        workspace_scope=".",
        profile_id="ag.standard",
        requested_session_action=session_action,
        completion_contract=FakeCompletionContract(),
    )


def _profile() -> ProfileDescriptor:
    return ProfileDescriptor(
        profile_id="ag.standard",
        profile_class="tier",
        supports_reasoning_effort=True,
    )


def _limits() -> TransportLimits:
    return TransportLimits(1, 1, 1)


def test_agy_plan_invocation_session_resume_uses_exact_argv():
    adapter = RealAgyAdapter()
    session = SessionHint(
        external_session_id="019c1234-5678-7abc-8def-0123456789ab",
        adapter_fingerprint=None,
        session_generation=None,
    )

    plan = adapter.plan_invocation(
        _request(SessionAction.RESUME), _profile(), session, _limits()
    )

    assert plan.argv == (
        "agy.exe",
        "-p",
        "Hello",
        "--output-format",
        "json",
        "--conversation",
        "019c1234-5678-7abc-8def-0123456789ab",
    )
    assert plan.redacted_display == (
        "agy.exe -p <redacted> --output-format json --conversation <redacted>"
    )
    assert plan.session_action == SessionAction.RESUME


@pytest.mark.parametrize(
    "session",
    [
        None,
        SessionHint(
            external_session_id=None,
            adapter_fingerprint=None,
            session_generation=None,
        ),
    ],
)
def test_agy_plan_invocation_session_resume_requires_id(
    session: SessionHint | None,
):
    adapter = RealAgyAdapter()

    with pytest.raises(
        ValueError, match="external_session_id is required for RESUME"
    ):
        adapter.plan_invocation(
            _request(SessionAction.RESUME), _profile(), session, _limits()
        )


def test_agy_plan_invocation_session_none_is_unchanged():
    adapter = RealAgyAdapter()

    plan = adapter.plan_invocation(
        _request(SessionAction.NONE), _profile(), None, _limits()
    )

    assert plan.argv == ("agy.exe", "-p", "Hello", "--output-format", "json")
    assert plan.redacted_display == "agy.exe -p <redacted> --output-format json"
    assert plan.session_action == SessionAction.NONE


def test_agy_decoder_emits_session_identity_from_top_level_conversation_id():
    decoder = AgyOutputDecoder()
    decoder.feed(
        b'{"conversation_id": "019c1234-5678-7abc-8def-0123456789ab", "response": "done"}'
    )

    decoded = decoder.finalize()
    session_events = [
        event
        for event in decoded.events
        if event.kind == DecoderEventKind.SESSION_IDENTITY
    ]

    assert len(session_events) == 1
    assert dict(session_events[0].payload) == {
        "session_id": "019c1234-5678-7abc-8def-0123456789ab"
    }
    assert decoded.canonical_text == "done"


def test_agy_decoder_without_conversation_id_is_unchanged():
    decoder = AgyOutputDecoder()
    decoder.feed(b'{"response": "done"}')

    decoded = decoder.finalize()

    assert decoded.canonical_text == "done"
    assert [event.kind for event in decoded.events] == [
        DecoderEventKind.ASSISTANT_TEXT
    ]
    assert decoded.events[0].payload["text"] == "done"


def test_agy_descriptor_advertises_session():
    assert Capability.SESSION in RealAgyAdapter.descriptor.capabilities
