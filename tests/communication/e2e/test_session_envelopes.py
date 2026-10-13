"""PR2 emitted stdout/JSON contracts, without provider calls."""
import json

from peerhub.cli import main
from peerhub.extensions import ask as module
from tests.communication.fakes import FakeRuntimeTarget


def install_runtime(monkeypatch, runtime):
    real = module.ask
    monkeypatch.setattr(module, "ask", lambda *a, **kw: real(*a, **kw, runtime=runtime))


def test_stdout_has_one_final_session_trailer_and_replay_keeps_it(tmp_path, monkeypatch, capsys):
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "exec"), ("terminal", {"response": "answer"}))
    install_runtime(monkeypatch, rt)
    argv = ["--db", str(tmp_path / "db"), "ask", "cx", "hello", "--request-id", "one"]
    assert main(argv) == 0
    first = capsys.readouterr()
    assert first.err == "" and first.out.startswith("answer\nsession: sess_")
    assert first.out.endswith(" fresh gen1 reason=no_prior\n")
    assert first.out.count("session:") == 1
    assert main(argv) == 0
    assert capsys.readouterr().out == first.out


def test_json_has_final_session_error_and_unknown_occupancy(tmp_path, monkeypatch, capsys):
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "exec"), ("terminal", {"response": "answer", "usage": {"input_tokens": 12345}}))
    install_runtime(monkeypatch, rt)
    argv = ["--db", str(tmp_path / "db"), "ask", "cx", "hello", "--request-id", "one", "--json"]
    assert main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["error"] is None and result["session"]["reason"] == "no_prior"
    assert result["session"]["telemetry"] == {"last_input_tokens": None, "context_window": None, "context_pct": None, "source": "unknown", "quota": None}
    argv[4] = "changed"
    assert main(argv) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["session"] is None and error["error"]["code"] == "idempotency_conflict"


def test_after_allocation_error_carries_actual_session(tmp_path, monkeypatch, capsys):
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "exec"), ("timeout", 1))
    install_runtime(monkeypatch, rt)
    assert main(["--db", str(tmp_path / "db"), "ask", "cx", "hello", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["session"]["handle"].startswith("sess_")
    assert result["session"]["reason"] == "no_prior" and result["error"]["code"] == "delivery_failed"


def test_storage_error_after_allocation_retains_accepted_identity(tmp_path, monkeypatch, capsys):
    from peerhub.core.store import StorageFullError
    from peerhub.extensions.bridge import Bridge
    install_runtime(monkeypatch, FakeRuntimeTarget())
    def fail(*args, **kwargs):
        raise StorageFullError("injected full store")
    monkeypatch.setattr(Bridge, "_run_runtime", fail)
    assert main(["--db", str(tmp_path / "db"), "ask", "cx", "hello", "--json"]) == 4
    result = json.loads(capsys.readouterr().out)
    assert result["session"]["generation"] == 1 and result["session"]["reason"] == "no_prior"
    assert result["error"]["code"] == "storage_corrupt"


def test_resumed_and_fallback_trailers_use_accepted_session(tmp_path, monkeypatch, capsys):
    rt = FakeRuntimeTarget()
    install_runtime(monkeypatch, rt)
    argv = ["--db", str(tmp_path / "db"), "ask", "cx", "hello", "--request-id", "one"]
    assert main(argv) == 0
    first = capsys.readouterr().out
    handle = first.split("session: ")[1].split()[0]
    argv[-1] = "two"
    assert main(argv) == 0
    assert capsys.readouterr().out == f"ok\nsession: {handle} resumed gen1\n"
    rt.set_binding("other")
    argv[-1] = "three"
    assert main(argv) == 0
    fallback = capsys.readouterr().out
    new_handle = fallback.split("session: ")[1].split()[0]
    assert new_handle != handle
    assert fallback == f"ok\nsession: {new_handle} fresh gen2 fallback reason=binding_change\n"
