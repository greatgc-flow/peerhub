"""
Note: The vendor-error byte patterns in these fixtures are synthetic, 
best-effort constructions, as there is no real capture available. 
They are marked TEST NEEDED for DIR-004 promotion to empirical_probe 
pending a real captured failure transcript from a live invocation.
"""
import pytest
from peerhub.adapters.agy_adapter import AgyOutputDecoder, RealAgyAdapter
from peerhub.adapters.contract import DecoderEventKind, InvocationPlan, TransportKind, TransportLimits, SessionAction
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
