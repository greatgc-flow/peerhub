import subprocess
import pytest
from peerhub.adapters.contract import (
    AdapterRequest,
    CompletionContractView,
    SessionAction,
    TransportLimits,
)
from peerhub.core.execution import ProcessTerminalEvidence
from peerhub.adapters.codex_adapter import RealCodexAdapter, _CODEX_PROFILE

class FakeCompletionContractView:
    @property
    def contract_id(self) -> str:
        return "fake-contract"

@pytest.mark.slow
def test_real_codex_adapter_shells_out():
    """Integration test that shells out to real codex.cmd (not mocked)."""
    adapter = RealCodexAdapter()
    
    request = AdapterRequest(
        request_id="req-125",
        prompt_content="say hello in two words",
        prompt_reference=None,
        workspace_scope=".",
        profile_id="cx.standard",
        requested_session_action=SessionAction.NONE,
        completion_contract=FakeCompletionContractView(),
    )
    
    limits = TransportLimits(
        process_timeout_ms=60000,
        silence_timeout_ms=60000,
        max_output_bytes=1000000,
    )
    
    # 1. Plan
    plan = adapter.plan_invocation(
        request=request,
        profile=_CODEX_PROFILE,
        session=None,
        limits=limits,
    )
    assert plan.argv == ("codex.cmd", "exec", "--json", "say hello in two words")
    
    # 2. Execute
    proc = subprocess.run(
        plan.argv,
        capture_output=True,
        cwd=plan.cwd_reference,
        # Redirect stdin to DEVNULL to avoid codex.cmd waiting for EOF
        stdin=subprocess.DEVNULL,
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
    assert len(decoded.events) >= 1
    assert decoded.events[-1].kind.value == "ASSISTANT_TEXT"
    
