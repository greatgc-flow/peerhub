"""
Note: The vendor-error byte patterns in these fixtures are synthetic, 
best-effort constructions, as there is no real capture available. 
They are marked TEST NEEDED for DIR-004 promotion to empirical_probe 
pending a real captured failure transcript from a live invocation.
"""
import logging

import pytest
from peerhub.adapters.codex_adapter import CodexOutputDecoder, RealCodexAdapter
from peerhub.adapters.contract import (
    AdapterRequest,
    Capability,
    DecoderEventKind,
    InvocationPlan,
    ModelSelectionMode,
    OutputChannel,
    ProfileDescriptor,
    ResolvedModelBinding,
    SessionAction,
    SessionHint,
    TransportKind,
    TransportLimits,
)

_PINNED_LUNA_BINDING = ResolvedModelBinding(
    selection_mode=ModelSelectionMode.PINNED,
    model_id="gpt-5.6-luna",
    reasoning_effort=None,
    source_layer="test-fixture",
)
_PINNED_LUNA_LOW_BINDING = ResolvedModelBinding(
    selection_mode=ModelSelectionMode.PINNED,
    model_id="gpt-5.6-luna",
    reasoning_effort="low",
    source_layer="test-fixture",
)
_CLI_DEFAULT_BINDING = ResolvedModelBinding(
    selection_mode=ModelSelectionMode.CLI_DEFAULT,
    model_id=None,
    reasoning_effort=None,
    source_layer="test-fixture",
)
from peerhub.core.execution import ProcessTerminalEvidence
from peerhub.core.protocol import ErrorCode


class FakeCompletionContract:
    @property
    def contract_id(self) -> str:
        return "fake-contract"


def _request(
    session_action: SessionAction,
    *,
    model_binding: ResolvedModelBinding = _PINNED_LUNA_BINDING,
) -> AdapterRequest:
    return AdapterRequest(
        request_id="req-1",
        prompt_content="Hello",
        prompt_reference=None,
        workspace_scope=".",
        profile_id="cx.standard",
        requested_session_action=session_action,
        completion_contract=FakeCompletionContract(),
        model_binding=model_binding,
    )


def _profile(
    profile_id: str = "cx.standard",
    supports_reasoning_effort: bool = True,
) -> ProfileDescriptor:
    return ProfileDescriptor(
        profile_id=profile_id,
        profile_class="tier",
        supports_reasoning_effort=supports_reasoning_effort,
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
        "--skip-git-repo-check",
        "-c",
        'model="gpt-5.6-luna"',
        "--json",
        "019c1234-5678-7abc-8def-0123456789ab",
        "Hello",
    )
    assert plan.redacted_display == (
        "codex.cmd exec resume --skip-git-repo-check "
        "-c model=\"gpt-5.6-luna\" --json <session-id> <redacted>"
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

    assert plan.argv == (
        "codex.cmd", "exec", "--skip-git-repo-check",
        "-c", 'model="gpt-5.6-luna"', "--json", "Hello",
    )
    assert plan.redacted_display == (
        "codex.cmd exec --skip-git-repo-check -c model=\"gpt-5.6-luna\" --json <redacted>"
    )
    assert plan.session_action == SessionAction.NONE


def test_codex_plan_invocation_standard_tier_emits_reasoning_effort_low():
    adapter = RealCodexAdapter()

    plan = adapter.plan_invocation(
        _request(SessionAction.NONE, model_binding=_PINNED_LUNA_LOW_BINDING),
        _profile(),
        None,
        _limits(),
    )

    assert plan.argv == (
        "codex.cmd", "exec", "--skip-git-repo-check",
        "-c", 'model="gpt-5.6-luna"',
        "-c", 'model_reasoning_effort="low"',
        "--json", "Hello",
    )
    assert plan.redacted_display == (
        'codex.cmd exec --skip-git-repo-check -c model="gpt-5.6-luna" -c model_reasoning_effort="low" --json <redacted>'
    )
    assert plan.session_action == SessionAction.NONE


def test_codex_cli_default_omits_model_override_and_warns(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING)
    logging.getLogger("peerhub.adapters.codex_adapter").disabled = False
    adapter = RealCodexAdapter()

    plan = adapter.plan_invocation(
        _request(SessionAction.NONE, model_binding=_CLI_DEFAULT_BINDING),
        _profile(),
        None,
        _limits(),
    )

    assert "-c" not in plan.argv
    assert plan.argv == (
        "codex.cmd", "exec", "--skip-git-repo-check", "--json", "Hello",
    )
    assert "account-side default model" in caplog.text


def test_codex_unresolved_dummy_request_omits_override_without_warning(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING)
    logging.getLogger("peerhub.adapters.codex_adapter").disabled = False
    adapter = RealCodexAdapter()

    plan = adapter.plan_invocation(
        AdapterRequest(
            request_id="dummy",
            prompt_content="dummy",
            prompt_reference=None,
            workspace_scope="dummy",
            profile_id="cx.standard",
            requested_session_action=SessionAction.NONE,
            completion_contract=FakeCompletionContract(),
        ),
        _profile(),
        None,
        _limits(),
    )

    assert "-c" not in plan.argv
    assert "account-side default model" not in caplog.text


def test_codex_invocation_plan_rejection_includes_model_and_override_hint():
    adapter = RealCodexAdapter()
    plan = adapter.plan_invocation(
        _request(SessionAction.NONE), _profile(), None, _limits()
    )
    decoder = adapter.new_decoder(plan)
    decoder.feed(b'{"type": "error", "error": {"code": "invalid_model"}}\n')

    decoded = decoder.finalize()

    event = decoded.events[0]
    assert event.kind is DecoderEventKind.VENDOR_ERROR
    assert event.payload["normalized_kind"] == "invocation_plan_rejected"
    assert event.payload["resolved_model"] == "gpt-5.6-luna"
    assert "workspace binding" in str(event.payload["override_hint"])
    assert "peerhub node bind-profile" in str(event.payload["override_hint"])


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


def test_codex_descriptor_advertises_profiles():
    profiles = RealCodexAdapter.descriptor.profiles
    assert tuple(p.profile_id for p in profiles) == ("cx.standard", "cx.effort", "cx.deepthink")
    assert RealCodexAdapter.descriptor.default_profile_id == "cx.standard"
    profile_map = {p.profile_id: p for p in profiles}
    assert profile_map["cx.standard"].supports_reasoning_effort is True
    assert profile_map["cx.effort"].supports_reasoning_effort is True
    assert profile_map["cx.deepthink"].supports_reasoning_effort is True


def test_codex_plan_invocation_effort_tier_appends_reasoning_effort():
    adapter = RealCodexAdapter()
    effort_profile = next(p for p in adapter.descriptor.profiles if p.profile_id == "cx.effort")
    binding = ResolvedModelBinding(
        selection_mode=ModelSelectionMode.PINNED,
        model_id="gpt-5.6-terra",
        reasoning_effort="high",
        source_layer="config",
    )
    req = AdapterRequest(
        request_id="req-effort",
        prompt_content="Hello effort",
        prompt_reference=None,
        workspace_scope=".",
        profile_id="cx.effort",
        requested_session_action=SessionAction.NONE,
        completion_contract=FakeCompletionContract(),
        model_binding=binding,
    )
    plan = adapter.plan_invocation(req, effort_profile, None, _limits())
    assert plan.argv == (
        "codex.cmd", "exec", "--skip-git-repo-check",
        "-c", 'model="gpt-5.6-terra"',
        "-c", 'model_reasoning_effort="high"',
        "--json", "Hello effort",
    )


def test_codex_plan_invocation_deepthink_tier_appends_reasoning_effort():
    adapter = RealCodexAdapter()
    deepthink_profile = next(p for p in adapter.descriptor.profiles if p.profile_id == "cx.deepthink")
    binding = ResolvedModelBinding(
        selection_mode=ModelSelectionMode.PINNED,
        model_id="gpt-6-astra",
        reasoning_effort="xhigh",
        source_layer="config",
    )
    req = AdapterRequest(
        request_id="req-dt",
        prompt_content="Hello deepthink",
        prompt_reference=None,
        workspace_scope=".",
        profile_id="cx.deepthink",
        requested_session_action=SessionAction.NONE,
        completion_contract=FakeCompletionContract(),
        model_binding=binding,
    )
    plan = adapter.plan_invocation(req, deepthink_profile, None, _limits())
    assert plan.argv == (
        "codex.cmd", "exec", "--skip-git-repo-check",
        "-c", 'model="gpt-6-astra"',
        "-c", 'model_reasoning_effort="xhigh"',
        "--json", "Hello deepthink",
    )


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
