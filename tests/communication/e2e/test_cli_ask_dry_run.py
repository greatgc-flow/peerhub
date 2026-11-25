"""Offline ask resolution through the public CLI."""

import json
from pathlib import Path
import socket
import subprocess
from uuid import uuid4

import pytest

from peerhub.cli.app import build_parser, main
from peerhub.core import load_limits
from peerhub.core.store import CoreStore
from peerhub.extensions.adapters import CliRuntimeTarget
from peerhub.extensions.adapters.api import API_TARGETS
from peerhub.extensions.ask import default_stream


def _offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("dry run attempted to start a process or access the network")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def test_missing_selected_store_prints_resolved_ask(monkeypatch, capsys):
    db = Path.cwd() / uuid4().hex / "core.db"
    options = ["--db", str(db), "ask", "cx", "hello", "--profile", "cx.standard",
               "--request-id", "preview", "--silence-timeout-seconds", "17", "--writable"]
    parsed = build_parser().parse_args(options)
    runtime = CliRuntimeTarget(parsed.peer, Path.cwd(), profile=parsed.profile, writable=parsed.writable)
    _offline(monkeypatch)
    sample = load_limits.ResourceSample(cpu_busy_pct=load_limits.CPU_BUSY_WARN_PCT)
    monkeypatch.setattr(load_limits, "read_resources", lambda: sample)

    assert main([*options, "--dry-run", "--json"]) == 0
    captured = capsys.readouterr()
    facts = json.loads(captured.out)
    assert not captured.err
    assert facts == {
        "peer_id": parsed.peer,
        **runtime.session_binding(),
        "timeout_seconds": parsed.timeout_seconds,
        "silence_timeout_seconds": parsed.silence_timeout_seconds,
        "workspace": str(Path.cwd()),
        "store_exists": False,
        "stream_id": default_stream(parsed.peer, parsed.request_id, stateless=False),
        "stream_exists": False,
        "session_mode": "fresh",
        "session_handle": None,
        "request_id": parsed.request_id,
        "replay": False,
        "load": load_limits.load_snapshot({}, sample),
        "resource_warnings": load_limits.load_warnings(sample.facts()),
    }
    assert not db.parent.exists()

    assert main([*options, "--dry-run"]) == 0
    output = capsys.readouterr()
    lines = [line.split(": ", 1) for line in output.out.splitlines()]
    assert {key for key, value in lines} == facts.keys() - {"resource_warnings"}
    assert dict(lines)["store_exists"] == "false"
    assert "at or above warning level" in output.err
    assert not db.parent.exists()


def test_missing_implicit_store_reports_unavailable_without_process(monkeypatch, capsys):
    import os

    workspace = Path.cwd() / uuid4().hex
    # An explicit workspace limits discovery to its store and the main worktree's store.
    # A nonexistent directory cannot be used by git and cannot contain a store.
    _offline(monkeypatch)
    assert "PEERHUB_DB" not in os.environ
    assert main(["ask", "cx", "hello", "--workspace", str(workspace), "--dry-run", "--json"]) == 5
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "UNAVAILABLE"
    assert "database not found" in captured.err
    assert not workspace.exists()


@pytest.mark.parametrize("args", [
    ["unknown-peer", "hello"],
    ["cx", "hello", "--profile", "cx.does-not-exist"],
    ["cx"],
    ["cx", "  "],
    ["ag", "hello"],
])
def test_usage_and_policy_refusals_match_real_ask(args, monkeypatch, capsys):
    db = Path.cwd() / uuid4().hex / "core.db"
    options = ["--db", str(db), "ask", *args, "--json"]
    _offline(monkeypatch)
    code = main(options)
    original = capsys.readouterr()
    assert code != 0 and not db.parent.exists()
    assert main([*options, "--dry-run"]) == code
    preview = capsys.readouterr()
    assert json.loads(preview.out)["error"] == json.loads(original.out)["error"]
    assert preview.err == original.err


def test_existing_store_unchanged(monkeypatch, capsys, tmp_path):
    db = tmp_path / "core.db"
    CoreStore(db)
    before = _tree_bytes(tmp_path)
    _offline(monkeypatch)
    assert main(["--db", str(db), "ask", "cx", "hello", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["store_exists"] is True
    assert _tree_bytes(tmp_path) == before


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under root (store sidecars live in a subdirectory), by relative path."""
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_retained_session_and_settled_replay(monkeypatch, capsys, tmp_path):
    def deliver(self, external_session_id, record, catch_up):
        yield ("started", "offline")
        yield ("terminal", {"response": "ok", "vendor_session": {"id": "offline-native"}})

    db = tmp_path / "core.db"
    def argv(prompt, *extra):  # the prompt stays right after the peer (an option before a positional parses differently on 3.11)
        return ["--db", str(db), "ask", "cx", prompt, "--json", "--profile", "cx.standard", *extra]

    first = ("first prompt", "--request-id", "first")
    second = ("second prompt", "--request-id", "second")
    _offline(monkeypatch)
    with monkeypatch.context() as dispatch:
        dispatch.setattr(CliRuntimeTarget, "deliver", deliver)
        assert main(argv(*first)) == 0
        handle = json.loads(capsys.readouterr().out)["session"]["handle"]
        assert handle

    before = _tree_bytes(tmp_path)

    def preview(prompt, *extra):
        assert main(argv(prompt, "--dry-run", *extra)) == 0
        return json.loads(capsys.readouterr().out)

    latest = preview(*second)
    assert latest["store_exists"] and latest["stream_exists"] and not latest["replay"]
    assert (latest["session_mode"], latest["session_handle"]) == ("resumed", handle)
    exact = preview(*second, "--session", handle)
    assert (exact["session_mode"], exact["session_handle"]) == ("resumed", handle)
    for flag in ("--fresh", "--writable"):
        fresh = preview(*second, flag)
        assert (fresh["session_mode"], fresh["session_handle"]) == ("fresh", None)
    replay = preview(*first)
    assert replay["replay"] and replay["session_mode"] is None
    assert replay["session_handle"] is None

    assert main(argv("different prompt", "--dry-run", "--request-id", "first")) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "idempotency_conflict"
    assert _tree_bytes(tmp_path) == before


def test_stateless_second_request_refusal_matches_real_ask(monkeypatch, capsys, tmp_path):
    def deliver(self, external_session_id, record, catch_up):
        yield ("started", "offline")
        yield ("terminal", {"response": "ok"})

    db = tmp_path / "core.db"
    base = ["--db", str(db), "ask", "oa"]
    options = ["--profile", "oa.standard", "--stream", "api-review", "--json"]
    _offline(monkeypatch)
    with monkeypatch.context() as dispatch:
        dispatch.setattr(API_TARGETS["oa"], "deliver", deliver)
        assert main([*base, "first", *options, "--request-id", "first"]) == 0
    capsys.readouterr()

    second = [*base, "second", *options, "--request-id", "second"]
    before = db.read_bytes()
    code = main(second)
    original = capsys.readouterr()
    assert code != 0
    assert main([*second, "--dry-run"]) == code
    preview = capsys.readouterr()
    assert json.loads(preview.out) == json.loads(original.out)
    assert preview.err == original.err
    assert db.read_bytes() == before


def test_uncertain_prior_invocation_blocks_preview(monkeypatch, capsys, tmp_path):
    def timeout(self, external_session_id, record, catch_up):
        yield ("started", "offline")
        yield ("timeout", 1)

    db = tmp_path / "core.db"
    _offline(monkeypatch)
    with monkeypatch.context() as dispatch:
        dispatch.setattr(CliRuntimeTarget, "deliver", timeout)
        assert main(["--db", str(db), "ask", "cx", "first", "--json", "--request-id", "first"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "uncertain"
    before = db.read_bytes()

    next_ask = ["--db", str(db), "ask", "cx", "second", "--json", "--request-id", "second"]
    assert main([*next_ask, "--dry-run"]) == 1
    preview = capsys.readouterr()
    assert json.loads(preview.out)["processed_status"] == "blocked_uncertain"
    assert db.read_bytes() == before
    assert main(next_ask) == 1
    real = capsys.readouterr()
    assert json.loads(preview.out)["error"] == json.loads(real.out)["error"]
    assert preview.err == real.err
