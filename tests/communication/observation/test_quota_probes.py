import pytest
import subprocess
from pathlib import Path
from peerhub.extensions.quota_probes import (
    _parse_agy_usage_output,
    _real_binary,
    resolve_sys_dir,
    poll_claude_usage,
    poll_codex_usage,
    poll_agy_usage,
)
from peerhub.extensions.observation_model import EvidenceState

class DummyIdSource:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-123"

def test_real_binary_returns_literal_path_not_resolved(monkeypatch, tmp_path):
    # On this portable install, env/nodejs/npm-global is a junction whose
    # .resolve() target contains "&" -- a cmd.exe command separator that
    # breaks .cmd/.bat invocation (.cmd files always run through cmd.exe).
    # Regression guard: _real_binary must return the literal candidate
    # path, never Path.resolve()'s output, regardless of what it resolves to.
    sys_dir = tmp_path / "_sys"
    (sys_dir / "env" / "nodejs" / "npm-global").mkdir(parents=True)
    cand = sys_dir / "env" / "nodejs" / "npm-global" / "claude.cmd"
    cand.write_text("@echo off\n")

    poisoned = tmp_path / "D-drive-stand-in & poisoned" / "claude.cmd"

    def fake_resolve(self):
        if self == cand:
            return poisoned
        return self

    monkeypatch.setenv("PEERHUB_CC_BINARY", str(cand))
    monkeypatch.setattr(Path, "resolve", fake_resolve)

    result = _real_binary("cc", sys_dir)

    assert result == str(cand)
    assert result != str(poisoned)


@pytest.mark.parametrize(
    ("peer", "binary_name"),
    (("ag", "agy.exe"), ("cc", "claude.cmd"), ("cx", "codex.cmd")),
)
def test_real_binary_falls_back_to_path_without_portable_runtime(
    monkeypatch, tmp_path, peer, binary_name
):
    standalone_binary = tmp_path / "host-bin" / binary_name
    standalone_binary.parent.mkdir()
    standalone_binary.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "peerhub.extensions.quota_probes.shutil.which",
        lambda name: str(standalone_binary) if name == binary_name else None,
    )

    assert _real_binary(peer, tmp_path / "missing-sys") == str(standalone_binary)

def test_poll_claude_usage_timeout_fail_closed(monkeypatch):
    class FakeProc:
        pid = 9999
        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="dummy.exe", timeout=timeout)

    def fake_popen(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "peerhub.extensions.quota_probes._real_binary",
        lambda _peer, _sys_dir=None: "dummy.exe",
    )

    ids = DummyIdSource()
    res = poll_claude_usage(ids, "inst-1", "prof-1")

    assert len(res) == 1
    obs = res[0]
    assert obs.evidence.state == EvidenceState.ERROR
    assert obs.evidence.value is None
    assert obs.extra["reason"] == "claude_usage_timeout"  # the stored ERROR says WHY (a loaded machine vs. a changed CLI)


def test_poll_claude_usage_spawn_failure_records_the_error_type(monkeypatch):
    def boom(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_binary", lambda _peer, _sys_dir=None: "dummy.exe")
    obs = poll_claude_usage(DummyIdSource(), "inst-1", "prof-1")[0]
    assert obs.evidence.state == EvidenceState.ERROR and obs.extra["reason"] == "claude_usage_spawn_failed:PermissionError"


def test_poll_claude_usage_unparseable_output_fail_closed(monkeypatch):
    class FakeProc:
        pid = 9999
        def communicate(self, timeout=None):
            return ("garbled nonsense", "")

    def fake_popen(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "peerhub.extensions.quota_probes._real_binary",
        lambda _peer, _sys_dir=None: "dummy.exe",
    )

    ids = DummyIdSource()
    res = poll_claude_usage(ids, "inst-1", "prof-1")

    assert len(res) == 1
    obs = res[0]
    assert obs.evidence.state == EvidenceState.ERROR
    assert obs.evidence.value is None
    assert obs.extra["reason"] == "claude_usage_unparseable"

def test_poll_claude_usage_success_parsing(monkeypatch):
    class FakeProc:
        pid = 9999
        def communicate(self, timeout=None):
            return ("Current session: 50% used resets 12:00pm\n", "")

    def fake_popen(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "peerhub.extensions.quota_probes._real_binary",
        lambda _peer, _sys_dir=None: "dummy.exe",
    )
    monkeypatch.setattr("peerhub.extensions.quota_probes._parse_claude_usage_reset", lambda *args, **kwargs: __import__('datetime').datetime(2026, 8, 17, 12, 0, tzinfo=__import__('datetime').timezone.utc))

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
        "peerhub.extensions.quota_probes._real_binary",
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
        "peerhub.extensions.quota_probes._real_binary",
        lambda _peer, _sys_dir=None: "dummy.exe",
    )
    
    ids = DummyIdSource()
    res = poll_codex_usage(ids, "inst-1", "prof-1", deadline_sec=0.5)

    assert len(res) == 1
    obs = res[0]
    assert obs.evidence.state == EvidenceState.ERROR
    assert obs.evidence.value is None

@pytest.mark.parametrize("used", [100, None, "NaN", -1, 101])
def test_poll_codex_usage_success_parsing(monkeypatch, used):
    # Shape captured from a real `codex app-server` response: primary/
    # secondary live nested under "rateLimits", not on the result envelope
    # directly (a prior version of this poller read them off the envelope
    # and so always found neither key, silently returning ERROR).
    class FakeStdout:
        def __init__(self):
            # real Popen call uses text=True, so readline() yields str, not
            # bytes -- match that here (unlike the malformed-response fixture
            # above, whose byte literals never reach json.loads as valid JSON
            # either way, which is why that test still passes for ERROR).
            quota = {"windowDurationMins": 10080, "resetsAt": 1787200158}
            if used is not None:
                quota["usedPercent"] = used
            self.lines = [
                '{"id": 0, "result": {"codexHome": "/whatever"}}\n',
                json.dumps({"id": 1, "result": {"rateLimits": {"limitId": "codex", "primary": quota,
                                      "secondary": None}, "rateLimitsByLimitId": {}}}) + "\n",
                "",
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
        "peerhub.extensions.quota_probes._real_binary",
        lambda _peer, _sys_dir=None: "dummy.exe",
    )

    ids = DummyIdSource()
    res = [r for r in poll_codex_usage(ids, "inst-1", "prof-1", deadline_sec=1.0) if hasattr(r, "evidence")]  # reset credits: test_reset_credits.py

    assert len(res) == 1
    obs = res[0]
    if used != 100:
        assert obs.evidence.state == EvidenceState.ERROR and obs.evidence.value is None
        return
    assert obs.evidence.state == EvidenceState.MEASURED
    assert obs.evidence.value.quota_pool_scope == "X-7D"
    assert obs.evidence.value.used_fraction == 1.0
    assert obs.evidence.value.resets_at == 1787200158

def test_poll_codex_usage_fallback_rate_limits_by_limit_id(monkeypatch):
    class FakeStdout:
        def __init__(self):
            self.lines = [
                '{"id": 0, "result": {"codexHome": "/whatever"}}\n',
                (
                    '{"id": 1, "result": {"rateLimits": null, '
                    '"rateLimitsByLimitId": {"codex": {"usedPercent": 50, "windowDurationMins": 300, "resetsAt": 1787200159}}}}\n'
                ),
                "",
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
        "peerhub.extensions.quota_probes._real_binary",
        lambda _peer, _sys_dir=None: "dummy.exe",
    )

    ids = DummyIdSource()
    res = [r for r in poll_codex_usage(ids, "inst-1", "prof-1", deadline_sec=1.0) if hasattr(r, "evidence")]  # reset credits: test_reset_credits.py

    assert len(res) == 1
    obs = res[0]
    assert obs.evidence.state == EvidenceState.MEASURED
    assert obs.evidence.value.quota_pool_scope == "X-5H"
    assert obs.evidence.value.used_fraction == 0.5
    assert obs.evidence.value.resets_at == 1787200159


import json


AGY_USAGE_JSON = json.dumps({
    "status": "SUCCESS",
    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    "command": {
        "name": "usage",
        "data": {
            "groups": [
                {
                    "name": "Gemini Models",
                    "buckets": [
                        {"id": "gemini-weekly", "remaining_fraction": 0.0938, "reset_time": "2026-09-30T02:26:19Z"},
                        {"id": "gemini-5h", "remaining_fraction": 0.9995, "reset_time": "2026-09-28T09:11:04Z"},
                    ],
                },
                {
                    "name": "Claude and GPT models",
                    "buckets": [
                        {"id": "3p-weekly", "remaining_fraction": 1.0, "reset_time": "2026-10-05T04:24:42Z"},
                        {"id": "3p-5h", "remaining_fraction": 1.0, "reset_time": "2026-09-28T09:24:42Z"},
                    ],
                },
            ]
        },
    },
})


def test_parse_agy_usage_output_preserves_all_four_windows():
    parsed = _parse_agy_usage_output(AGY_USAGE_JSON)
    assert set(parsed) == {"gemini-weekly", "gemini-5h", "3p-weekly", "3p-5h"}
    assert parsed["gemini-weekly"]["remaining_fraction"] == pytest.approx(0.0938)
    assert parsed["gemini-weekly"]["reset_time"] == "2026-09-30T02:26:19Z"


def test_poll_agy_usage_prefers_live_slash_command(monkeypatch, tmp_path):
    class Completed:
        returncode = 0
        stdout = AGY_USAGE_JSON
        stderr = ""

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return Completed()

    monkeypatch.delenv("PEERHUB_AG_STATUSLINE_LOG", raising=False)
    monkeypatch.setattr(
        "peerhub.extensions.quota_probes._real_command",
        lambda peer, _sys_dir=None: ["agy.exe"] if peer == "ag" else None,
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    ids = DummyIdSource()
    observed_at = 1_790_000_000

    res = poll_agy_usage(
        ids,
        "ag",
        "standard",
        clock=lambda: observed_at,
        sys_dir=tmp_path / "_sys",
    )

    assert captured["argv"][1:4] == ["-p", "/usage", "--output-format"]
    assert len(res) == 4
    scopes = {obs.evidence.value.quota_pool_scope: obs for obs in res}
    assert scopes["G-7D"].evidence.value.remaining_fraction == pytest.approx(0.0938)
    assert scopes["G-7D"].evidence.value.resets_at == 1790735179
    assert all(obs.evidence.source_tag == "agy_cli_usage" for obs in res)

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


def test_real_binary_default_and_overrides(monkeypatch, tmp_path):
    # Implicit legacy layout is ignored, even when it exists.
    sys_dir = tmp_path / "_sys"
    default_npm = sys_dir / "env" / "nodejs" / "npm-global"
    default_npm.mkdir(parents=True)
    cc_default = default_npm / "claude.cmd"
    cc_default.write_text("@echo off\n")
    cx_default = default_npm / "codex.cmd"
    cx_default.write_text("@echo off\n")

    monkeypatch.delenv("PEERHUB_NPM_GLOBAL_DIR", raising=False)
    monkeypatch.delenv("PEERHUB_CC_BINARY", raising=False)
    monkeypatch.delenv("PEERHUB_CX_BINARY", raising=False)

    monkeypatch.setattr("peerhub.extensions.quota_probes.shutil.which", lambda name: None)
    assert _real_binary("cc", sys_dir) is None
    assert _real_binary("cx", sys_dir) is None

    # 2. PEERHUB_NPM_GLOBAL_DIR override
    custom_npm = tmp_path / "custom_npm"
    custom_npm.mkdir(parents=True)
    cc_custom_npm = custom_npm / "claude.cmd"
    cc_custom_npm.write_text("@echo off\n")
    cx_custom_npm = custom_npm / "codex.cmd"
    cx_custom_npm.write_text("@echo off\n")

    monkeypatch.setenv("PEERHUB_NPM_GLOBAL_DIR", str(custom_npm))
    assert _real_binary("cc", sys_dir) == str(cc_custom_npm)
    assert _real_binary("cx", sys_dir) == str(cx_custom_npm)

    # 3. Direct binary overrides PEERHUB_CC_BINARY / PEERHUB_CX_BINARY
    direct_cc = tmp_path / "bin" / "my_claude.exe"
    direct_cc.parent.mkdir(parents=True, exist_ok=True)
    direct_cc.write_text("")
    direct_cx = tmp_path / "bin" / "my_codex.exe"
    direct_cx.write_text("")

    monkeypatch.setenv("PEERHUB_CC_BINARY", str(direct_cc))
    monkeypatch.setenv("PEERHUB_CX_BINARY", str(direct_cx))
    assert _real_binary("cc", sys_dir) == str(direct_cc)
    assert _real_binary("cx", sys_dir) == str(direct_cx)


def test_poll_claude_usage_config_dir_override(monkeypatch, tmp_path):
    captured_envs = []

    class FakeProc:
        pid = 1234
        def communicate(self, timeout=None):
            return "Usage: 50%", ""

    def fake_popen(*args, **kwargs):
        if args and args[0] and "/usage" in args[0]:
            captured_envs.append(kwargs.get("env", {}))
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "peerhub.extensions.quota_probes._real_command",
        lambda _peer, _sys_dir=None: ["dummy.exe"],
    )

    sys_dir = tmp_path / "_sys"
    sys_dir.mkdir(parents=True)
    ids = DummyIdSource()

    # Existing _sys does not override vendor configuration.
    monkeypatch.delenv("PEERHUB_CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    poll_claude_usage(ids, "inst-1", "prof-1", sys_dir=sys_dir)
    assert "CLAUDE_CONFIG_DIR" not in captured_envs[-1]

    # Override: PEERHUB_CLAUDE_CONFIG_DIR
    custom_cfg = tmp_path / "custom_claude_config"
    monkeypatch.setenv("PEERHUB_CLAUDE_CONFIG_DIR", str(custom_cfg))
    poll_claude_usage(ids, "inst-1", "prof-1", sys_dir=sys_dir)
    assert captured_envs[-1]["CLAUDE_CONFIG_DIR"] == str(custom_cfg)


def test_poll_claude_usage_standalone_does_not_inject_portable_config(
    monkeypatch, tmp_path
):
    captured_envs = []

    class FakeProc:
        pid = 1234

        def communicate(self, timeout=None):
            return "unparseable", ""

    def fake_popen(*args, **kwargs):
        captured_envs.append(kwargs.get("env", {}))
        return FakeProc()

    monkeypatch.delenv("PEERHUB_CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "peerhub.extensions.quota_probes._real_command",
        lambda _peer, _sys_dir=None: ["claude.cmd"],
    )

    poll_claude_usage(
        DummyIdSource(), "cc", "standard", sys_dir=tmp_path / "missing-sys"
    )

    assert "CLAUDE_CONFIG_DIR" not in captured_envs[-1]


def test_poll_agy_usage_log_path_override(monkeypatch, tmp_path):
    custom_log = tmp_path / "custom_ag.json"
    custom_log.write_text(json.dumps({
        "quota": {
            "gemini-5h": {
                "remaining_fraction": 0.5,
                "reset_in_seconds": 1800
            }
        }
    }), encoding="utf-8")

    monkeypatch.setenv("PEERHUB_AG_STATUSLINE_LOG", str(custom_log))
    ids = DummyIdSource()
    res = poll_agy_usage(ids, "inst-1", "prof-1", log_path=None)
    assert len(res) == 1
    assert res[0].evidence.state == EvidenceState.MEASURED


def test_resolve_sys_dir_does_not_infer_layout_from_interpreter(monkeypatch, tmp_path):
    portable_sys = tmp_path / "portable" / "_sys"
    interpreter = portable_sys / "env" / "venv" / "Scripts" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"python")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PEERHUB_SYS_DIR", raising=False)
    monkeypatch.setattr("peerhub.extensions.quota_probes.sys.executable", str(interpreter))

    assert resolve_sys_dir() == tmp_path / "_sys"


@pytest.fixture(autouse=True)
def _mock_probe_cleanup(monkeypatch):
    # Fake PIDs must never reach the OS; dedicated tests verify the shared helper.
    monkeypatch.setattr("peerhub.extensions.quota_probes.kill_process_tree", lambda proc: True)


@pytest.mark.parametrize("peer", ["ag", "cc", "cx"])
def test_missing_explicit_binary_never_searches_path(monkeypatch, tmp_path, peer):
    key = f"PEERHUB_{peer.upper()}_BINARY"
    monkeypatch.setenv(key, str(tmp_path / "missing"))
    monkeypatch.setattr("peerhub.extensions.quota_probes.shutil.which",
                        lambda name: pytest.fail("explicit override must not fall through"))
    with pytest.raises(FileNotFoundError, match=key):
        _real_binary(peer)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, -0.1, 1.5, 101])
def test_ag_invalid_fraction_is_never_measured(value, tmp_path):
    import json
    path = tmp_path / "quota.json"
    path.write_text(json.dumps({"quota": {"gemini-5h": {
        "remaining_fraction": value, "reset_in_seconds": 1800}}}), encoding="utf-8")
    obs = poll_agy_usage(DummyIdSource(), "ag", "standard", log_path=path)[0]
    assert obs.evidence.state == EvidenceState.ERROR
    assert obs.evidence.value is None
    envelope = {"command": {"name": "usage", "data": {"groups": [{"buckets": [
        {"id": "gemini-5h", "remaining_fraction": value, "reset_time": "2026-10-10T12:00:00Z"},
        {"id": "gemini-weekly", "remaining_fraction": 0.5, "reset_time": "2026-10-10T12:00:00Z"}
    ]}]}}}
    assert _parse_agy_usage_output(json.dumps(envelope)) == {}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, -0.1, 101])
def test_claude_invalid_percent_is_never_measured(value, monkeypatch):
    from peerhub.extensions.quota_probes import _claude_usage_emit
    from datetime import datetime, timezone
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    assert _claude_usage_emit("current session", value, "1pm", now) is None
    class Proc:
        pid = 9999
        def communicate(self, timeout):
            return f"Current session: {value}% used resets 1pm", ""
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_command", lambda *a: ["claude"])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: Proc())
    obs = poll_claude_usage(DummyIdSource(), "cc", "standard", clock=lambda: now.timestamp())[0]
    assert obs.evidence.state == EvidenceState.ERROR and obs.evidence.value is None


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_fraction_boundaries_are_valid(value):
    from peerhub.extensions.quota_probes import _valid_fraction
    assert _valid_fraction(value)


@pytest.mark.parametrize("value", [0, 1.5, 100])
def test_claude_percent_boundaries_are_valid(value):
    from peerhub.extensions.quota_probes import _claude_usage_emit
    assert _claude_usage_emit("current session", value, "1pm")["used_frac"] == value / 100


def test_claude_probe_uses_shared_posix_tree_cleanup(monkeypatch):
    from peerhub.extensions import process_tree
    from peerhub.extensions import quota_probes as probes
    calls, groups = [], []
    class Proc:
        pid = 12345
        def communicate(self, timeout):
            raise subprocess.TimeoutExpired("claude", timeout)
    monkeypatch.setattr(probes.sys, "platform", "linux")
    monkeypatch.setattr(probes, "kill_process_tree", process_tree.kill_process_tree)
    monkeypatch.setattr(process_tree.os, "killpg", lambda pid, sig: groups.append(pid), raising=False)
    monkeypatch.setattr(process_tree.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(probes, "_real_command", lambda *a: ["claude"])
    monkeypatch.setattr(probes.subprocess, "Popen", lambda *a, **k: (calls.append(k) or Proc()))
    obs = poll_claude_usage(DummyIdSource(), "cc", "standard")[0]
    assert obs.evidence.state == EvidenceState.ERROR
    assert calls[0]["start_new_session"] is True and groups == [12345]


def test_ag_does_not_read_implicit_legacy_log(monkeypatch, tmp_path):
    import json
    path = tmp_path / "_sys" / "data" / "temp" / "ag_statusline_stdin.log"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"quota": {"gemini-5h": {
        "remaining_fraction": 0.5, "reset_in_seconds": 1800}}}), encoding="utf-8")
    monkeypatch.delenv("PEERHUB_AG_STATUSLINE_LOG", raising=False)
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_command", lambda *a: None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PEERHUB_SYS_DIR", raising=False)
    obs = poll_agy_usage(DummyIdSource(), "ag", "standard")[0]
    assert obs.evidence.state == EvidenceState.UNKNOWN and obs.evidence.value is None


def test_claude_usage_reset_resolves_known_timezone_abbreviations():
    from datetime import datetime, timezone, timedelta
    from peerhub.extensions.quota_probes import _parse_claude_usage_reset

    # Now in KST (+09:00): 2026-10-12 19:00 KST = 2026-10-12 10:00 UTC = 2026-10-12 06:00 EDT
    kst = timezone(timedelta(hours=9))
    now_kst = datetime(2026, 10, 12, 19, 0, 0, tzinfo=kst)

    # 1. EDT (UTC-4)
    dt_edt = _parse_claude_usage_reset("5:00pm (EDT)", now=now_kst)
    assert dt_edt is not None
    assert dt_edt.utcoffset() == timedelta(hours=-4)
    # 5:00pm EDT is Oct 12 17:00 EDT = Oct 12 21:00 UTC
    assert dt_edt.astimezone(timezone.utc) == datetime(2026, 10, 12, 21, 0, 0, tzinfo=timezone.utc)

    # 2. EST (UTC-5)
    dt_est = _parse_claude_usage_reset("5:00pm (EST)", now=now_kst)
    assert dt_est is not None
    assert dt_est.utcoffset() == timedelta(hours=-5)
    assert dt_est.astimezone(timezone.utc) == datetime(2026, 10, 12, 22, 0, 0, tzinfo=timezone.utc)

    # 3. PDT (UTC-7)
    dt_pdt = _parse_claude_usage_reset("5:00pm (PDT)", now=now_kst)
    assert dt_pdt is not None
    assert dt_pdt.utcoffset() == timedelta(hours=-7)
    assert dt_pdt.astimezone(timezone.utc) == datetime(2026, 10, 13, 0, 0, 0, tzinfo=timezone.utc)

    # 4. PST (UTC-8)
    dt_pst = _parse_claude_usage_reset("5:00pm (PST)", now=now_kst)
    assert dt_pst is not None
    assert dt_pst.utcoffset() == timedelta(hours=-8)
    assert dt_pst.astimezone(timezone.utc) == datetime(2026, 10, 13, 1, 0, 0, tzinfo=timezone.utc)

    # 5. UTC
    dt_utc = _parse_claude_usage_reset("5:00pm (UTC)", now=now_kst)
    assert dt_utc is not None
    assert dt_utc.utcoffset() == timedelta(0)


def test_claude_usage_reset_midnight_boundaries():
    from datetime import datetime, timezone, timedelta
    from peerhub.extensions.quota_probes import _parse_claude_usage_reset

    # Case A: Local system is in KST, already past midnight (Oct 12 05:00 KST),
    # but in EDT (UTC-4) it is still previous day: Oct 11 16:00 EDT!
    kst = timezone(timedelta(hours=9))
    now = datetime(2026, 10, 12, 5, 0, 0, tzinfo=kst)
    # Reset is 5:00pm EDT. In EDT, 5:00pm today (Oct 11) is 1 hour in the future!
    res = _parse_claude_usage_reset("5:00pm (EDT)", now=now)
    assert res is not None
    assert res.year == 2026 and res.month == 10 and res.day == 11
    assert res.hour == 17 and res.minute == 0
    assert res.utcoffset() == timedelta(hours=-4)
    # In UTC, Oct 11 17:00 EDT = Oct 11 21:00 UTC
    assert res.astimezone(timezone.utc) == datetime(2026, 10, 11, 21, 0, 0, tzinfo=timezone.utc)

    # Case B: Reset time was earlier than now in EDT -> rolls over midnight to next calendar day
    edt = timezone(timedelta(hours=-4))
    now_late = datetime(2026, 10, 11, 23, 50, 0, tzinfo=edt)
    res_next = _parse_claude_usage_reset("12:15am (EDT)", now=now_late)
    assert res_next is not None
    assert res_next.year == 2026 and res_next.month == 10 and res_next.day == 12
    assert res_next.hour == 0 and res_next.minute == 15
    assert res_next - now_late == timedelta(minutes=25)


def test_claude_usage_rejects_unsupported_explicit_timezone(monkeypatch):
    import subprocess
    from peerhub.extensions.quota_probes import (
        _parse_claude_usage_reset,
        poll_claude_usage,
        UnsupportedTimezoneError,
    )
    from peerhub.extensions.observation_model import EvidenceState

    # Direct parse raises UnsupportedTimezoneError
    with pytest.raises(UnsupportedTimezoneError, match="unsupported timezone: UNSUPPORTED_TZ"):
        _parse_claude_usage_reset("5:00pm (UNSUPPORTED_TZ)")

    # Probe poll fails closed with UNAVAILABLE evidence
    class FakeProc:
        pid = 9999
        def communicate(self, timeout=None):
            return ("Current session: 50% used resets 5:00pm (UNSUPPORTED_TZ)\n", "")

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_binary", lambda *a, **k: "dummy.exe")

    res = poll_claude_usage(DummyIdSource(), "inst-1", "prof-1")
    assert len(res) == 1
    obs = res[0]
    assert obs.evidence.state == EvidenceState.UNAVAILABLE
    assert obs.extra["reason"] == "unsupported_timezone:UNSUPPORTED_TZ"


@pytest.mark.parametrize(
    ("zone", "now_text", "reset_text", "expected_utc", "offset_hours"),
    [
        ("Asia/Seoul", "2026-10-12T10:00:00+00:00", "8pm", "2026-10-12T11:00:00+00:00", 9),
        ("Asia/Seoul", "2026-10-12T15:05:00+00:00", "12:15am", "2026-10-12T15:15:00+00:00", 9),
        ("Asia/Seoul", "2026-10-12T14:50:00+00:00", "12:15am", "2026-10-12T15:15:00+00:00", 9),
        ("Asia/Seoul", "2026-12-31T15:05:00+00:00", "Jan 1, 12:15am", "2026-12-31T15:15:00+00:00", 9),
        ("America/New_York", "2026-03-07T17:00:00+00:00", "5pm", "2026-03-07T22:00:00+00:00", -5),
        ("America/New_York", "2026-03-08T16:00:00+00:00", "5pm", "2026-03-08T21:00:00+00:00", -4),
        ("America/New_York", "2026-03-08T04:50:00+00:00", "3:15am", "2026-03-08T07:15:00+00:00", -4),
        ("America/New_York", "2026-10-31T16:00:00+00:00", "5pm", "2026-10-31T21:00:00+00:00", -4),
        ("America/New_York", "2026-11-01T17:00:00+00:00", "5pm", "2026-11-01T22:00:00+00:00", -5),
        ("America/New_York", "2026-11-01T03:50:00+00:00", "2:15am", "2026-11-01T07:15:00+00:00", -5),
        ("America/New_York", "2026-10-12T00:00:00+00:00", "9pm", "2026-10-12T01:00:00+00:00", -4),
        ("America/Los_Angeles", "2026-01-12T18:00:00+00:00", "5pm", "2026-01-13T01:00:00+00:00", -8),
        ("America/Los_Angeles", "2026-07-12T17:00:00+00:00", "5pm", "2026-07-13T00:00:00+00:00", -7),
        ("America/Los_Angeles", "2026-10-12T06:50:00+00:00", "12:15am", "2026-10-12T07:15:00+00:00", -7),
    ],
)
def test_claude_iana_reset_dates_and_dst(zone, now_text, reset_text, expected_utc, offset_hours):
    from datetime import datetime, timedelta, timezone
    from peerhub.extensions.quota_probes import _parse_claude_usage_reset

    reset = _parse_claude_usage_reset(
        f"{reset_text} ({zone})", now=datetime.fromisoformat(now_text)
    )

    assert reset is not None
    assert reset.astimezone(timezone.utc) == datetime.fromisoformat(expected_utc)
    assert reset.utcoffset() == timedelta(hours=offset_hours)


@pytest.mark.parametrize(
    ("abbreviation", "offset_hours"),
    [("EDT", -4), ("EST", -5), ("PDT", -7), ("PST", -8),
     ("CDT", -5), ("CST", -6), ("MDT", -6), ("MST", -7),
     ("UTC", 0), ("GMT", 0), ("KST", 9), ("JST", 9)],
)
def test_claude_abbreviations_work_without_timezone_database(monkeypatch, abbreviation, offset_hours):
    from datetime import datetime, timedelta, timezone
    from peerhub.extensions import quota_probes as probes

    def missing_zone(name):
        pytest.fail(f"fixed offsets must not require a timezone database: {name}")

    monkeypatch.setattr(probes, "ZoneInfo", missing_zone)
    reset = probes._parse_claude_usage_reset(
        f"5pm ({abbreviation})", now=datetime(2026, 10, 12, tzinfo=timezone.utc)
    )
    assert reset is not None
    assert reset.utcoffset() == timedelta(hours=offset_hours)


@pytest.mark.parametrize("output", [
    "Current session: 50% used resets 8pm (Asia/Seoul)\n",
    "Current session\n50% used\nResets 8pm (Asia/Seoul)\n"
    "Current week (all models)\n25% used\nResets Oct 16, 12pm (Asia/Seoul)\n",
])
def test_claude_provider_output_with_iana_zone_is_measured(monkeypatch, output):
    from datetime import datetime, timezone
    from peerhub.extensions import quota_probes as probes

    class FakeProc:
        pid = 9999

        def communicate(self, timeout=None):
            return output, ""

    monkeypatch.setattr(probes, "_real_command", lambda *a: ["claude"])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeProc())
    now = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
    observations = poll_claude_usage(
        DummyIdSource(), "inst-1", "prof-1", clock=lambda: now.timestamp()
    )

    assert len(observations) == (2 if "Current week" in output else 1)
    assert all(obs.evidence.state == EvidenceState.MEASURED for obs in observations)
    measurement = observations[0].evidence.value
    assert measurement is not None
    assert measurement.quota_pool_scope == "C-5H"
    assert measurement.used_fraction == 0.5
    assert measurement.resets_at == int(datetime(2026, 10, 12, 11, tzinfo=timezone.utc).timestamp())


def test_claude_missing_timezone_database_is_unavailable(monkeypatch):
    from zoneinfo import ZoneInfoNotFoundError
    from peerhub.extensions import quota_probes as probes

    def missing_zone(name):
        raise ZoneInfoNotFoundError(name)

    class FakeProc:
        pid = 9999

        def communicate(self, timeout=None):
            return "Current session: 50% used resets 8pm (Asia/Seoul)\n", ""

    monkeypatch.setattr(probes, "ZoneInfo", missing_zone)
    monkeypatch.setattr(probes, "_real_command", lambda *a: ["claude"])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeProc())

    observations = poll_claude_usage(DummyIdSource(), "inst-1", "prof-1")

    assert len(observations) == 1
    assert observations[0].evidence.state == EvidenceState.UNAVAILABLE
    assert observations[0].evidence.value is None
    assert observations[0].extra["reason"] == "timezone_database_missing"
