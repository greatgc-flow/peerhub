import pytest
import subprocess
from peerhub.telemetry.quota_polling import poll_claude_usage, poll_codex_usage
from peerhub.core.evidence import EvidenceState

class DummyIdSource:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-123"

def test_poll_claude_usage_timeout_fail_closed(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 15.0))
    
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "peerhub.telemetry.quota_polling._real_binary",
        lambda _peer, _sys_dir=None: "dummy.exe",
    )
    
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
    monkeypatch.setattr(
        "peerhub.telemetry.quota_polling._real_binary",
        lambda _peer, _sys_dir=None: "dummy.exe",
    )
    
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
    monkeypatch.setattr(
        "peerhub.telemetry.quota_polling._real_binary",
        lambda _peer, _sys_dir=None: "dummy.exe",
    )
    monkeypatch.setattr("peerhub.telemetry.quota_polling._parse_claude_usage_reset", lambda *args, **kwargs: __import__('datetime').datetime(2026, 8, 17, 12, 0, tzinfo=__import__('datetime').timezone.utc))
    
    ids = DummyIdSource()
    res = poll_claude_usage(ids, "inst-1", "prof-1")
    
    assert len(res) == 1
    obs = res[0]
    assert obs.evidence.state == EvidenceState.MEASURED
    assert obs.evidence.value.quota_pool_scope == "C-5H"
    assert obs.evidence.value.used_fraction == 0.5

def test_poll_codex_usage_timeout_fail_closed(monkeypatch):
    import time
    
    class FakeProc:
        def __init__(self):
            self.stdin = None
            self.stdout = self
            self.pid = 9999
        def readline(self):
            time.sleep(0.5)
            return ""
        def poll(self):
            return None
            
    def fake_popen(*args, **kwargs):
        return FakeProc()
        
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "peerhub.telemetry.quota_polling._real_binary",
        lambda _peer, _sys_dir=None: "dummy.exe",
    )
    
    ids = DummyIdSource()
    res = poll_codex_usage(ids, "inst-1", "prof-1", deadline_sec=0.1)
    
    assert len(res) == 1
    obs = res[0]
    assert obs.evidence.state == EvidenceState.ERROR
    assert obs.evidence.value is None

def test_poll_codex_usage_malformed_response(monkeypatch):
    class FakeStdout:
        def __init__(self):
            self.lines = [
                b'{"id": 0, "result": {}}\n',
                b'{"id": 1, "result": "not a dict"}\n',
                b""
            ]
        def readline(self):
            if not self.lines:
                return ""
            return self.lines.pop(0)
            
    class FakeProc:
        def __init__(self):
            class FakeStdin:
                def write(self, _): pass
                def flush(self): pass
            self.stdin = FakeStdin()
            self.stdout = FakeStdout()
            self.pid = 9999
        def poll(self):
            return None
            
    def fake_popen(*args, **kwargs):
        return FakeProc()
        
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "peerhub.telemetry.quota_polling._real_binary",
        lambda _peer, _sys_dir=None: "dummy.exe",
    )
    
    ids = DummyIdSource()
    res = poll_codex_usage(ids, "inst-1", "prof-1", deadline_sec=0.5)
    
    assert len(res) == 1
    obs = res[0]
    assert obs.evidence.state == EvidenceState.ERROR
    assert obs.evidence.value is None

import json
from peerhub.telemetry.quota_polling import poll_agy_usage

def test_poll_agy_usage_missing_file(tmp_path):
    ids = DummyIdSource()
    res = poll_agy_usage(ids, "inst-1", "prof-1", log_path=tmp_path / "nonexistent.json")
    assert len(res) == 1
    assert res[0].evidence.state == EvidenceState.ABSENT

def test_poll_agy_usage_stale_file(tmp_path, monkeypatch):
    import time
    log_file = tmp_path / "ag.json"
    log_file.write_text('{"quota": {}}', encoding="utf-8")
    
    # fake clock to be far in the future
    clock = lambda: time.time() + 1000
    
    ids = DummyIdSource()
    res = poll_agy_usage(ids, "inst-1", "prof-1", clock=clock, freshness_ttl=60, log_path=log_file)
    assert len(res) == 1
    assert res[0].evidence.state == EvidenceState.STALE

def test_poll_agy_usage_malformed_json(tmp_path):
    log_file = tmp_path / "ag.json"
    log_file.write_text('{not valid json', encoding="utf-8")
    
    ids = DummyIdSource()
    res = poll_agy_usage(ids, "inst-1", "prof-1", log_path=log_file)
    assert len(res) == 1
    assert res[0].evidence.state == EvidenceState.ERROR

def test_poll_agy_usage_success(tmp_path):
    log_file = tmp_path / "ag.json"
    log_file.write_text(json.dumps({
        "quota": {
            "gemini-5h": {
                "remaining_fraction": 0.2,
                "reset_in_seconds": 3600
            },
            "3p-weekly": {
                "remaining_fraction": 0.8,
                "reset_time": "2026-08-18T12:00:00Z"
            }
        }
    }), encoding="utf-8")
    
    ids = DummyIdSource()
    res = poll_agy_usage(ids, "inst-1", "prof-1", log_path=log_file)
    assert len(res) == 2
    
    scopes = {obs.evidence.value.quota_pool_scope: obs for obs in res}
    
    assert "G-5H" in scopes
    obs_g = scopes["G-5H"]
    assert obs_g.evidence.state == EvidenceState.MEASURED
    assert obs_g.evidence.value.used_fraction == pytest.approx(0.8)
    
    assert "3P-7D" in scopes
    obs_3p = scopes["3P-7D"]
    assert obs_3p.evidence.state == EvidenceState.MEASURED
    assert obs_3p.evidence.value.used_fraction == pytest.approx(0.2)
