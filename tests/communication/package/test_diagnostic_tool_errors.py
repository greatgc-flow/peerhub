"""Offline argument and failure paths; no provider CLI is invoked."""
from types import SimpleNamespace
import json
import sys

import pytest

from tools import context_bench, model_ping


@pytest.mark.parametrize("peer", ["cc", "cx"])
def test_context_bench_resolves_npm_shims_and_stringifies_cwd(tmp_path, monkeypatch, peer):
    binary = "claude" if peer == "cc" else "codex"
    shim = tmp_path / "npm & tools" / f"{binary}.cmd"
    if peer == "cc":
        direct = shim.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        expected = [str(direct)]
    else:
        direct = shim.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        node = shim.parent / "node.exe"
        expected = [str(node), str(direct)]
    direct.parent.mkdir(parents=True)
    direct.touch()
    shim.touch()
    if peer == "cx":
        node.touch()
    monkeypatch.setattr(context_bench.shutil, "which", lambda name: str(shim))

    def run(argv, **kwargs):
        assert argv[:len(expected)] == expected
        assert kwargs["cwd"] == str(tmp_path)
        payload = ({"result": "answer", "usage": {"input_tokens": 1}} if peer == "cc" else
                   {"type": "turn.completed", "usage": {"input_tokens": 1}})
        return SimpleNamespace(stdout=json.dumps(payload).encode(), stderr=b"", returncode=0,
                               timed_out=False, output_exceeded=False, output_truncated=False,
                               aborted=False, drain_timed_out=False)

    monkeypatch.setattr(context_bench, "run_bounded", run)
    context_bench.invoke(SimpleNamespace(peer=peer, model="test", effort=None), "prompt", tmp_path, "")


@pytest.mark.parametrize("peer", ["cc", "cx", "ag"])
def test_context_bench_bounded_runner_preserves_results_and_session(tmp_path, monkeypatch, peer):
    from peerhub.extensions.adapters.process import run_bounded

    if peer == "cc":
        payload = json.dumps({"result": "answer", "session_id": "native", "usage": {
            "input_tokens": 38, "cache_read_input_tokens": 2, "output_tokens": 4}})
    elif peer == "cx":
        payload = "\n".join(map(json.dumps, [
            {"type": "thread.started", "thread_id": "native"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}},
            {"type": "turn.completed", "usage": {"input_tokens": 40, "cached_input_tokens": 2, "output_tokens": 4}},
        ]))
    else:
        payload = json.dumps({"response": "answer", "conversation_id": "native", "usage": {
            "input_tokens": 40, "cache_read_tokens": 2, "output_tokens": 4}})
    monkeypatch.setattr(context_bench.shutil, "which", lambda name: "fake-provider")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return run_bounded([sys.executable, "-c", f"import sys; sys.stdout.write({payload!r})"], **kwargs)

    monkeypatch.setattr(context_bench, "run_bounded", run)
    args = SimpleNamespace(peer=peer, model="test", effort="low")
    assert context_bench.invoke(args, "prompt", tmp_path, "prior") == (
        "answer", "native", [40, 2, 0, 4, 0.0])
    argv, options = calls[0]
    assert "prior" in argv and "test" in argv
    assert options["stdin"] == (None if peer == "ag" else b"prompt")
    assert options["cwd"] == str(tmp_path)
    assert options["timeout_s"] == 240 and options["max_bytes"] == 1_048_576


@pytest.mark.parametrize("script,reason", [
    ("import time; time.sleep(30)", "timed out"),
    ("import sys; sys.stdout.write('x' * 100000); sys.stdout.flush()", "output limit"),
])
def test_context_bench_bounds_child_time_and_output(tmp_path, monkeypatch, script, reason):
    from peerhub.extensions.adapters.process import run_bounded

    monkeypatch.setattr(context_bench, "TIMEOUT_S", 0.5, raising=False)
    monkeypatch.setattr(context_bench, "MAX_OUTPUT_BYTES", 256, raising=False)
    monkeypatch.setattr(context_bench.shutil, "which", lambda name: sys.executable)

    def run(argv, **kwargs):
        return run_bounded([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(context_bench, "run_bounded", run, raising=False)
    args = SimpleNamespace(peer="cc", model="test", effort=None)
    with pytest.raises(ValueError, match=reason):
        context_bench.invoke(args, "prompt", str(tmp_path), "")


@pytest.mark.parametrize("argv,message", [
    (["unknown.profile"], "unknown profile ids"),
    (["cc.standard", "--peer", "cx"], "no profiles selected"),
])
def test_model_ping_rejects_invalid_selection(argv, message, tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "profiles.json"
    manifest.write_text('{"profiles": {"cc.standard": {"model": "example"}}}', encoding="utf-8")
    monkeypatch.setattr(model_ping, "MANIFEST", manifest)
    monkeypatch.setattr(model_ping, "PINGS", {"cc": lambda *a: pytest.fail("must not call CLI"),
                                             "cx": lambda *a: pytest.fail("must not call CLI")})
    with pytest.raises(SystemExit) as exc:
        model_ping.main(argv)
    assert exc.value.code == 2 and message in capsys.readouterr().err


@pytest.mark.parametrize("turns", ["0", "-1", "260"])
def test_context_bench_rejects_invalid_turns(turns, monkeypatch, capsys):
    monkeypatch.setattr(context_bench, "benchmark", lambda *a: pytest.fail("must not benchmark"))
    with pytest.raises(SystemExit) as exc:
        context_bench.main(["--peer", "cc", "--turns", turns])
    assert exc.value.code == 2 and "turns must be 1..259" in capsys.readouterr().err


@pytest.mark.parametrize("strategy", ["FRESH", "RESUME"])
def test_context_bench_propagates_failed_turn(strategy, monkeypatch):
    def fail(*a):
        raise ValueError("invalid usage")
    monkeypatch.setattr(context_bench, "invoke", fail)
    args = SimpleNamespace(peer="cc", gap_seconds=0)
    with pytest.raises(ValueError, match=f"{strategy} turn 1 failed: invalid usage"):
        context_bench.benchmark(args, strategy, "document", [("question", "expected")])


def test_context_bench_rejects_incomplete_resume(monkeypatch):
    monkeypatch.setattr(context_bench, "invoke", lambda *a: ("expected", "", [1, 0, 0, 1, 0.0]))
    with pytest.raises(ValueError, match="no native session ID"):
        context_bench.benchmark(SimpleNamespace(peer="cc", gap_seconds=0), "RESUME", "doc",
                                [("question", "expected")])


def test_context_bench_rejects_empty_or_incorrect_runs(monkeypatch):
    monkeypatch.setattr(context_bench, "invoke", lambda *a: ("wrong", "session", [1, 0, 0, 1, 0.0]))
    for questions in ([], [("question", "expected")]):
        with pytest.raises(ValueError, match="invalid run"):
            context_bench.benchmark(SimpleNamespace(peer="cc", gap_seconds=0), "FRESH", "doc", questions)
