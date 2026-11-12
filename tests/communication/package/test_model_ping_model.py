"""#341: `model_ping --peer P --model M` pings one exact model without reading or writing any profile overlay."""
import pytest
import json
import subprocess

from tools import model_ping


@pytest.mark.parametrize("events, verdict", [
    ([{"type": "turn.failed", "error": {"message": "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account."}}], "REJECTED:"),
    ([{"type": "error", "message": "bad\n\x1b\u2603" + "x" * 200}], "REJECTED:"),
    ([], "NO EVIDENCE"),
    ([{"type": "item.completed", "item": {"type": "agent_message", "text": "PONG"}}], "NO EVIDENCE"),
    ([{"type": "turn.completed"}], "NO EVIDENCE"),
    ([{"type": "item.completed", "item": {"type": "agent_message", "text": ""}}, {"type": "turn.completed"}], "NO EVIDENCE"),
    ([{"type": "item.completed", "item": {"type": "agent_message", "text": "PONG"}}, {"type": "turn.completed"}], "ok"),
    ([{"type": "item.completed", "item": {"type": "agent_message", "text": "PONG"}}, {"type": "turn.completed"},
      {"type": "turn.failed", "error": {"message": "rejected"}}], "REJECTED:"),
])
def test_cx_requires_answer_and_terminal_success(tmp_path, monkeypatch, capsys, events, verdict):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "thread-test.jsonl").write_text(json.dumps({"type": "turn_context", "payload": {"model": "gpt-6.1-sol", "effort": "high"}}), encoding="utf-8")
    stdout = "\n".join(json.dumps(event) for event in [{"type": "thread.started", "thread_id": "thread-test"}, *events])
    monkeypatch.setattr(model_ping, "_run", lambda argv, cwd: subprocess.CompletedProcess(argv, 0, stdout, ""))
    monkeypatch.setattr(model_ping, "_codex_home", lambda: tmp_path)
    assert model_ping.main(["--peer", "cx", "--model", "gpt-6.1-sol", "--effort", "high"]) == (0 if verdict == "ok" else 1)
    out = capsys.readouterr().out
    assert verdict in out
    assert ("answered=gpt-6.1-sol/high" if verdict == "ok" else "answered=-") in out
    assert all(32 <= ord(char) <= 126 for char in out.rstrip("\n"))
    if verdict == "REJECTED:":
        message = out.split("REJECTED:", 1)[1].split("  (no PONG)", 1)[0].strip()
        assert len(message) <= 160
        expected = next(event for event in events if event["type"] in {"error", "turn.failed"})
        if expected.get("error"):
            assert expected["error"]["message"] in message


@pytest.fixture
def no_profiles(monkeypatch):
    from peerhub.extensions.adapters import profiles

    monkeypatch.setattr(profiles, "list_profiles", lambda workspace: pytest.fail("must not read profiles/overlays"))


def test_model_ping_exact_model(no_profiles, monkeypatch, capsys):
    calls = []
    monkeypatch.setitem(model_ping.PINGS, "cc", lambda model, effort, cwd: calls.append((model, effort)) or (True, "claude-x", "ok"))
    assert model_ping.main(["--peer", "cc", "--model", "claude-x", "--effort", "high"]) == 0
    assert calls == [("claude-x", "high")]
    out = capsys.readouterr().out
    assert "PASS" in out and "want=claude-x/high" in out and "answered=claude-x" in out


def test_model_ping_exact_model_mismatch_fails(no_profiles, monkeypatch, capsys):
    monkeypatch.setitem(model_ping.PINGS, "cx", lambda model, effort, cwd: (True, "other/-", "MODEL MISMATCH"))
    assert model_ping.main(["--peer", "cx", "--model", "m1"]) == 1
    assert "FAIL" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["--model", "m"], ["--model", "m", "--peer", "cc", "cc.standard"], ["--peer", "cc", "--effort", "high"]])
def test_model_ping_model_option_validation(argv, no_profiles, monkeypatch, capsys):
    monkeypatch.setattr(model_ping, "PINGS", {"cc": lambda *a: pytest.fail("must not call CLI")})
    with pytest.raises(SystemExit) as exc:
        model_ping.main(argv)
    assert exc.value.code == 2 and capsys.readouterr().err
