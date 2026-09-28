import subprocess
import pytest
from peerhub.adapters.contract import (
    AdapterRequest,
    CompletionContractView,
    SessionAction,
    TransportLimits,
)
from peerhub.core.execution import ProcessTerminalEvidence
from peerhub.adapters.claude_adapter import RealClaudeAdapter, _CLAUDE_PROFILE
from peerhub.application.model_config import ModelConfigService

class FakeCompletionContractView:
    @property
    def contract_id(self) -> str:
        return "fake-contract"

@pytest.mark.slow
def test_real_claude_adapter_shells_out(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """Integration test that shells out to real claude.cmd (not mocked)."""
    adapter = RealClaudeAdapter()
    
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "empty-config"))
    request = AdapterRequest(
        request_id="req-124",
        prompt_content="say hello in two words",
        prompt_reference=None,
        workspace_scope=".",
        profile_id="cc.standard",
        requested_session_action=SessionAction.NONE,
        completion_contract=FakeCompletionContractView(),
        model_binding=ModelConfigService(None).resolve(
            node_id="cc", profile_id="cc.standard"
        ),
    )
    
    limits = TransportLimits(
        process_timeout_ms=60000,
        silence_timeout_ms=60000,
        max_output_bytes=1000000,
    )
    
    # 1. Plan
    plan = adapter.plan_invocation(
        request=request,
        profile=_CLAUDE_PROFILE,
        session=None,
        limits=limits,
    )
    assert request.model_binding.model_id is not None
    assert plan.argv == (
        "claude.cmd", "-p", "-", "--output-format", "stream-json", "--verbose",
        "--model", request.model_binding.model_id,
    )
    
    # 2. Execute
    proc = subprocess.run(
        plan.argv,
        input=plan.stdin_payload,
        capture_output=True,
        cwd=plan.cwd_reference,
    )
    
    # 3. Assess output
    evidence = ProcessTerminalEvidence(
        exit_code=proc.returncode,
    )
    
    # Pass stdout bytes directly
    raw_chunks = [proc.stdout]
    
    assessment = adapter.interpret_output(plan, evidence, raw_chunks)
    
    assert assessment.parsed is True
    assert assessment.response_present is True
    assert assessment.protocol_failure is None
    
    # Also verify decoder
    decoder = adapter.new_decoder(plan)
    decoder.feed(proc.stdout)
    decoded = decoder.finalize()
    assert decoded.canonical_text
    assert any(event.kind.value == "ASSISTANT_TEXT" for event in decoded.events)
    
