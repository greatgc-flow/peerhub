import pytest
from peerhub.telemetry.quota_polling import poll_claude_usage, poll_codex_usage
from peerhub.core.evidence import EvidenceState
from peerhub.telemetry.contract import UsageObserved

class DummyIdSource:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-test"

@pytest.mark.slow
def test_real_poll_claude_usage():
    ids = DummyIdSource()
    # Execute the real CLI integration test
    results = poll_claude_usage(ids, "inst-int", "prof-int")
    
    assert isinstance(results, tuple)
    assert len(results) > 0
    
    for obs in results:
        assert isinstance(obs, UsageObserved)
        if obs.evidence.state == EvidenceState.MEASURED:
            assert obs.evidence.value is not None
            assert obs.evidence.value.used_fraction >= 0.0
            assert obs.evidence.value.used_fraction <= 1.0
        else:
            assert obs.evidence.state in (EvidenceState.ABSENT, EvidenceState.ERROR)
            assert obs.evidence.value is None

@pytest.mark.slow
def test_real_poll_codex_usage():
    import psutil
    ids = DummyIdSource()
    
    # Record current 'node.exe' and 'codex.exe' pids
    initial_pids = set()
    for p in psutil.process_iter(['name']):
        try:
            if p.info['name'] and p.info['name'].lower() in ('node.exe', 'codex.exe'):
                initial_pids.add(p.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    results = poll_codex_usage(ids, "inst-int", "prof-int")
    
    assert isinstance(results, tuple)
    assert len(results) > 0
    
    for obs in results:
        assert isinstance(obs, UsageObserved)
        if obs.evidence.state == EvidenceState.MEASURED:
            assert obs.evidence.value is not None
            assert obs.evidence.value.used_fraction >= 0.0
            assert obs.evidence.value.used_fraction <= 1.0
        else:
            assert obs.evidence.state in (EvidenceState.ABSENT, EvidenceState.ERROR)
            assert obs.evidence.value is None

    # Verify no new 'node.exe' or 'codex.exe' processes were leaked
    new_pids = set()
    for p in psutil.process_iter(['name']):
        try:
            if p.info['name'] and p.info['name'].lower() in ('node.exe', 'codex.exe'):
                new_pids.add(p.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
            
    leaked = new_pids - initial_pids
    assert not leaked, f"Process leak detected: {leaked}"
