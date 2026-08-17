import pytest
import subprocess
from peerhub.telemetry.quota_polling import poll_claude_usage
from peerhub.core.evidence import EvidenceState

class DummyIdSource:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-123"

def test_poll_claude_usage_timeout_fail_closed(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 15.0))
    
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("peerhub.telemetry.quota_polling._real_binary", lambda x: "dummy.exe")
    
    ids = DummyIdSource()
    res = poll_claude_usage(ids, "inst-1", "prof-1")
    
    assert len(res) == 1
    obs = res[0]
    assert obs.evidence.state == EvidenceState.ERROR
    assert obs.evidence.value is None

def test_poll_claude_usage_unparseable_output_fail_closed(monkeypatch):
    class FakeProc:
        stdout = "garbled nonsense"
        stderr = ""
        
    def fake_run(*args, **kwargs):
        return FakeProc()
        
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("peerhub.telemetry.quota_polling._real_binary", lambda x: "dummy.exe")
    
    ids = DummyIdSource()
    res = poll_claude_usage(ids, "inst-1", "prof-1")
    
    assert len(res) == 1
    obs = res[0]
    assert obs.evidence.state == EvidenceState.ERROR
    assert obs.evidence.value is None

def test_poll_claude_usage_success_parsing(monkeypatch):
    class FakeProc:
        stdout = "Current session: 50% used resets 12:00pm\n"
        stderr = ""
        
    def fake_run(*args, **kwargs):
        return FakeProc()
        
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("peerhub.telemetry.quota_polling._real_binary", lambda x: "dummy.exe")
    monkeypatch.setattr("peerhub.telemetry.quota_polling._parse_claude_usage_reset", lambda *args, **kwargs: __import__('datetime').datetime(2026, 8, 17, 12, 0, tzinfo=__import__('datetime').timezone.utc))
    
    ids = DummyIdSource()
    res = poll_claude_usage(ids, "inst-1", "prof-1")
    
    assert len(res) == 1
    obs = res[0]
    assert obs.evidence.state == EvidenceState.MEASURED
    assert obs.evidence.value.quota_pool_scope == "C-5H"
    assert obs.evidence.value.used_fraction == 0.5
