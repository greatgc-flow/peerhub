"""Offline argument and failure paths; no provider CLI is invoked."""
from types import SimpleNamespace

import pytest

from tools import context_bench, model_ping


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
