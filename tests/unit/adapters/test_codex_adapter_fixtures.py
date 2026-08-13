"""
Note: The vendor-error byte patterns in these fixtures are synthetic, 
best-effort constructions, as there is no real capture available. 
They are marked TEST NEEDED for DIR-004 promotion to empirical_probe 
pending a real captured failure transcript from a live invocation.
"""
import pytest
from peerhub.adapters.codex_adapter import CodexOutputDecoder, RealCodexAdapter
from peerhub.adapters.contract import (
    AdapterRequest,
    Capability,
    DecoderEventKind,
    InvocationPlan,
    OutputChannel,
    ProfileDescriptor,
    SessionAction,
    SessionHint,
    TransportKind,
    TransportLimits,
)
from peerhub.core.execution import ProcessTerminalEvidence
from peerhub.core.protocol import ErrorCode


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
        profile_id="cx.standard",
        requested_session_action=session_action,
        completion_contract=FakeCompletionContract(),
    )


def _profile() -> ProfileDescriptor:
    return ProfileDescriptor(
        profile_id="cx.standard",
        profile_class="tier",
        supports_reasoning_effort=False,
    )


def _limits() -> TransportLimits:
    return TransportLimits(1, 1, 1)

def test_codex_decoder_session_invalid():
    decoder = CodexOutputDecoder()
    decoder.feed(b'{"type": "error", "error": {"code": "session_expired"}}\n')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    assert decoded.events[0].kind == DecoderEventKind.VENDOR_ERROR
    assert decoded.events[0].payload["normalized_kind"] == "session_invalid"

def test_codex_decoder_auth_unavailable():
    decoder = CodexOutputDecoder()
    decoder.feed(b'{"type": "error", "error": {"code": "auth_unavailable"}}\n')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    assert decoded.events[0].kind == DecoderEventKind.VENDOR_ERROR
    assert decoded.events[0].payload["normalized_kind"] == "auth_unavailable"

def test_codex_decoder_invocation_plan_rejected():
    decoder = CodexOutputDecoder()
    decoder.feed(b'{"type": "error", "error": {"code": "invalid_model"}}\n')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    assert decoded.events[0].kind == DecoderEventKind.VENDOR_ERROR
    assert decoded.events[0].payload["normalized_kind"] == "invocation_plan_rejected"

def test_codex_decoder_stderr_model_operand_invalid():
    decoder = CodexOutputDecoder()
    decoder.feed(
        b'model_operand_invalid\n',
        channel=OutputChannel.STDERR,
    )
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    assert decoded.events[0].kind == DecoderEventKind.VENDOR_ERROR
    assert decoded.events[0].payload["normalized_kind"] == "invocation_plan_rejected"

def test_codex_decoder_live_flat_error():
    # [cli_live] 2026-08-13
    decoder = CodexOutputDecoder()
    decoder.feed(b'{"type":"error","message":"{\\"type\\":\\"error\\",\\"status\\":400,\\"error\\":{\\"type\\":\\"invalid_request_error\\",\\"message\\":\\"The \'invalid_model_name\' model is not supported when using Codex with a ChatGPT account.\\"}}"}\n')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    assert decoded.events[0].kind == DecoderEventKind.VENDOR_ERROR
    assert decoded.events[0].payload["normalized_kind"] == "invocation_plan_rejected"

def test_codex_decoder_live_turn_failed():
    # [cli_live] 2026-08-13
    decoder = CodexOutputDecoder()
    decoder.feed(b'{"type":"turn.failed","error":{"message":"{\\"type\\":\\"error\\",\\"status\\":400,\\"error\\":{\\"type\\":\\"invalid_request_error\\",\\"message\\":\\"The \'invalid_model_name\' model is not supported when using Codex with a ChatGPT account.\\"}}"}}\n')
    decoded = decoder.finalize()
    assert len(decoded.events) == 1
    assert decoded.events[0].kind == DecoderEventKind.VENDOR_ERROR
    assert decoded.events[0].payload["normalized_kind"] == "invocation_plan_rejected"

def test_codex_interpret_output_nonzero_exit_not_internal_error():
    adapter = RealCodexAdapter()
    plan = InvocationPlan(
        argv=("test",), cwd_reference=".", environment_delta={}, transport=TransportKind.PIPE,
        stdin_payload=None, limits=TransportLimits(1, 1, 1), redacted_display="test",
        artifacts=(), session_action=SessionAction.NONE
    )
    process = ProcessTerminalEvidence(exit_code=1)
    
    # Not empty or malformed
    chunks = [b'{"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}\n']
    assessment = adapter.interpret_output(plan, process, chunks)
    assert assessment.protocol_failure is None
    
    # Malformed JSON should still yield INTERNAL_ERROR
    chunks_malformed = [b'{"bad json']
    assessment_malformed = adapter.interpret_output(plan, process, chunks_malformed)
    assert assessment_malformed.protocol_failure == ErrorCode.INTERNAL_ERROR

def test_codex_interpret_output_with_vendor_error_yields_no_protocol_failure():
    adapter = RealCodexAdapter()
    plan = InvocationPlan(
        argv=("test",), cwd_reference=".", environment_delta={}, transport=TransportKind.PIPE,
        stdin_payload=None, limits=TransportLimits(1, 1, 1), redacted_display="test",
        artifacts=(), session_action=SessionAction.NONE
    )
    process = ProcessTerminalEvidence(exit_code=1)
    chunks = [b'{"type": "error", "error": {"code": "session_expired"}}\n']
    
    decoder = adapter.new_decoder(plan)
    for chunk in chunks:
        decoder.feed(chunk)
    decoded = decoder.finalize()
    assert any(e.kind == DecoderEventKind.VENDOR_ERROR for e in decoded.events)
    
    assessment = adapter.interpret_output(plan, process, chunks)
    assert assessment.protocol_failure is None


def test_codex_plan_invocation_session_resume_uses_exact_argv():
    adapter = RealCodexAdapter()
    session = SessionHint(
        external_session_id="019c1234-5678-7abc-8def-0123456789ab",
        adapter_fingerprint=None,
        session_generation=None,
    )

    plan = adapter.plan_invocation(
        _request(SessionAction.RESUME), _profile(), session, _limits()
    )

    assert plan.argv == (
        "codex.cmd",
        "exec",
        "resume",
        "--json",
        "019c1234-5678-7abc-8def-0123456789ab",
        "Hello",
    )
    assert plan.redacted_display == (
        "codex.cmd exec resume --json <session-id> <redacted>"
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
def test_codex_plan_invocation_session_resume_requires_id(
    session: SessionHint | None,
):
    adapter = RealCodexAdapter()

    with pytest.raises(
        ValueError, match="external_session_id is required for RESUME"
    ):
        adapter.plan_invocation(
            _request(SessionAction.RESUME), _profile(), session, _limits()
        )


def test_codex_plan_invocation_session_none_is_unchanged():
    adapter = RealCodexAdapter()

    plan = adapter.plan_invocation(
        _request(SessionAction.NONE), _profile(), None, _limits()
    )

    assert plan.argv == ("codex.cmd", "exec", "--json", "Hello")
    assert plan.redacted_display == "codex.cmd exec --json <redacted>"
    assert plan.session_action == SessionAction.NONE


def test_codex_decoder_emits_session_identity_from_thread_started():
    decoder = CodexOutputDecoder()
    decoder.feed(
        b'{"type":"thread.started","thread_id":"019c1234-5678-7abc-8def-0123456789ab"}\n'
        b'{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n'
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


def test_codex_decoder_without_thread_started_is_unchanged():
    decoder = CodexOutputDecoder()
    decoder.feed(
        b'{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n'
    )

    decoded = decoder.finalize()

    assert decoded.canonical_text == "done"
    assert [event.kind for event in decoded.events] == [
        DecoderEventKind.ASSISTANT_TEXT
    ]
    assert decoded.events[0].payload["text"] == "done"


def test_codex_decoder_buffers_split_jsonl_until_line_is_complete():
    decoder = CodexOutputDecoder()

    first_events = decoder.feed(
        b'{"type":"item.completed","item":{"type":"agent_message","text":"hel'
    )
    second_events = decoder.feed(b'lo"}}\n')

    assert first_events == ()
    assert len(second_events) == 1
    assert second_events[0].kind is DecoderEventKind.ASSISTANT_TEXT
    assert dict(second_events[0].payload) == {"text": "hello"}

    decoded = decoder.finalize()
    assert decoded.canonical_text == "hello"
    assert decoded.events == second_events


def test_codex_descriptor_advertises_session():
    assert Capability.SESSION in RealCodexAdapter.descriptor.capabilities


def test_codex_descriptor_advertises_stream():
    assert Capability.STREAM in RealCodexAdapter.descriptor.capabilities


def test_codex_decoder_auth_failure_takes_precedence_over_connect():
    decoder = CodexOutputDecoder()
    decoder.feed(
        b'{"type":"turn.failed","error":{"code":"internal_error","message":"401 Unauthorized while connecting to api"}}\n'
    )

    decoded = decoder.finalize()

    vendor_events = [
        event for event in decoded.events if event.kind == DecoderEventKind.VENDOR_ERROR
    ]
    assert len(vendor_events) == 1
    assert vendor_events[0].payload["normalized_kind"] == "auth_unavailable"


def test_codex_decoder_live_tool_call_emission():
    # [cli_live] 2026-08-13
    decoder = CodexOutputDecoder()
    decoder.feed(
        b'{"type":"thread.started","thread_id":"019c1234-5678-7abc-8def-0123456789ab"}\n'
        b'{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"echo foo","aggregated_output":"foo\\n","exit_code":0,"status":"completed"}}\n'
        b'{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n'
    )
    decoded = decoder.finalize()

    assert len(decoded.events) == 3
    assert decoded.events[0].kind == DecoderEventKind.SESSION_IDENTITY
    assert dict(decoded.events[0].payload) == {"session_id": "019c1234-5678-7abc-8def-0123456789ab"}

    assert decoded.events[1].kind == DecoderEventKind.TOOL_CALL
    payload = dict(decoded.events[1].payload)
    assert payload["id"] == "item_1"
    assert payload["type"] == "command_execution"
    assert payload["command"] == "echo foo"
    assert "exit_code" not in payload
    assert "aggregated_output" not in payload
    assert "status" not in payload

    assert decoded.events[2].kind == DecoderEventKind.ASSISTANT_TEXT
    assert dict(decoded.events[2].payload) == {"text": "done"}
    assert decoded.canonical_text == "done"
