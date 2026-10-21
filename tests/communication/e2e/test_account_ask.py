"""P1 RED ask integration with real adapters over a temporary fake vendor CLI.

Only command selection is injected; account lookup, claims, validation, adapter
env binding, subprocesses, receipts and settled replay remain production paths.
Post-claim validation failures must use PrespawnError -> NOT_STARTED handling.
"""
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from peerhub.core.models import Peer
from peerhub.core.store import CoreStore
from peerhub.extensions import accounts
from peerhub.extensions import ask as ask_module
from peerhub.extensions.adapters import CliRuntimeTarget
from peerhub.extensions.bridge import PrespawnError
from peerhub.extensions.bridge_claims import ClaimStore
from peerhub.extensions.quota_capture import refresh_quota
from tests.communication.fakes import FakeRuntimeTarget
from tests.communication.test_accounts_registry import link_dir

PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_", "OPENAI_", "CODEX_")
FAKE_SOURCE = '''import hashlib, json, os, sys
kind = os.environ["FAKE_KIND"]
args = sys.argv[1:]
if "--version" in args:
    print("9.8.7 (fake)"); sys.exit(0)
if "--help" in args:
    print("usage: fake --resume ID resume"); sys.exit(0)
sys.stdin.read()
names = ("CLAUDE_CONFIG_DIR", "CODEX_HOME")
env = {k: v for k, v in os.environ.items() if k in names or k.upper().startswith(
    ("ANTHROPIC_", "CLAUDE_CODE_", "OPENAI_", "CODEX_"))}
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"kind": kind, "argv": args, "env": env}) + "\\n")
root = os.environ.get("CLAUDE_CONFIG_DIR" if kind == "cc" else "CODEX_HOME", "default")
native = "fake-" + hashlib.sha256(root.encode()).hexdigest()[:16]
if kind == "cc" and "--resume" in args:
    native = args[args.index("--resume") + 1]
if kind == "cx" and "resume" in args:
    native = args[args.index("resume") + 1]
mode = os.environ.get("FAKE_ACCOUNT_MODE", "ok")
if mode == "nonzero":
    sys.stderr.write("cannot open account root " + json.dumps(root)[1:-1])
    sys.exit(3)
if mode == "hang":
    import time
    time.sleep(60)
if mode == "vendor_error":
    error = "RESOURCE_EXHAUSTED"
    event = ({"type":"result", "subtype":"error", "is_error":True, "result":error, "session_id":native}
             if kind == "cc" else {"type":"turn.failed", "error":{"message":error}})
    print(json.dumps(event))
    sys.exit(1)
text = "\\n".join(key + "=" + env[key] for key in sorted(env))
if kind == "cc":
    print(json.dumps({"type":"system", "subtype":"init", "session_id":native}))
    print(json.dumps({"type":"result", "subtype":"success", "is_error":False,
                      "result":text, "session_id":native}))
else:
    print(json.dumps({"type":"thread.started", "thread_id":native}))
    print(json.dumps({"type":"item.completed", "item":{"type":"agent_message", "text":text}}))
    print(json.dumps({"type":"turn.completed", "usage":{}}))
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    for name in list(os.environ):
        if name.upper().startswith(PREFIXES) or name.upper() == "CLAUDE_CONFIG_DIR":
            monkeypatch.delenv(name)
    ambient = {}
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        path = tmp_path / f"ambient-{name}"
        path.mkdir()
        ambient[name] = str(path)
        monkeypatch.setenv(name, str(path))
    credentials = {"ANTHROPIC_API_KEY": "sentinel-anthropic-secret", "CLAUDE_CODE_AUTH_TOKEN": "sentinel-claude-secret",
                   "OPENAI_API_KEY": "sentinel-openai-secret", "CODEX_API_KEY": "sentinel-codex-secret"}
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)
    db, log, script = tmp_path / "core.db", tmp_path / "child.jsonl", tmp_path / "fake_account_cli.py"
    script.write_text(FAKE_SOURCE, encoding="utf-8", newline="\n")
    CoreStore(db)
    targets = []

    def factory(kind, workspace, **kwargs):
        target = CliRuntimeTarget(kind, workspace, command=[sys.executable, str(script)],
                                  env_extra={"FAKE_KIND": kind, "FAKE_LOG": str(log)}, **kwargs)
        targets.append(target)
        return target

    monkeypatch.setattr(ask_module, "CliRuntimeTarget", factory)

    def bind(peer="worker", provider="cc"):
        root = tmp_path / f"root-{peer}"
        root.mkdir()
        result = accounts.bind_account(db, peer_id=peer, provider=provider, alias=f"alias-{peer}", root=root)
        return result, root

    def run(peer="worker", request="req", **kwargs):
        return ask_module.ask(db, peer, "inspect account environment", request_id=request,
                              workspace=kwargs.pop("workspace", workspace), timeout_s=kwargs.pop("timeout_s", 10), **kwargs)

    def calls():
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    return SimpleNamespace(db=db, home=home, workspace=workspace, bind=bind, run=run,
                           calls=calls, targets=targets, ambient=ambient, credentials=credentials)


def assert_claim_released(db, peer="worker"):
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT expires_at FROM bridge_claims WHERE peer_id=?", (peer,)).fetchall()
    assert rows and all(expires <= time.time() for (expires,) in rows)


@pytest.mark.parametrize("provider,var", [("cc", "CLAUDE_CONFIG_DIR"), ("cx", "CODEX_HOME")])
def test_bound_child_env_disclosure_receipts_and_schema(env, provider, var):
    with sqlite3.connect(env.db) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    binding, root = env.bind(provider=provider)
    result = env.run()
    assert result["status"] == "delivered" and result["certainty"] == "TERMINAL"
    call, = env.calls()
    assert call["env"][var] == str(root)
    assert all(name not in call["env"] for name in env.credentials)
    assert all(name.upper() == var or not name.upper().startswith(PREFIXES) for name in call["env"])
    other_var = "CODEX_HOME" if provider == "cc" else "CLAUDE_CONFIG_DIR"
    assert call["env"].get(other_var) != str(root)
    expected = set(env.credentials) | ({"CODEX_HOME"} if provider == "cc" else set())
    assert set(result["env_scrubbed"]) == expected
    assert str(root) not in result["response"] and "[REDACTED]" in result["response"]
    assert_claim_released(env.db)
    with sqlite3.connect(env.db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == version
        metadata = [json.loads(row[0]) for row in conn.execute("SELECT detail FROM bridge_evidence")]
    assert any(set(item.get("env_scrubbed", [])) == expected for item in metadata)
    serialized = json.dumps(metadata)
    assert str(root) not in serialized.replace("\\\\", "\\") and binding.alias not in serialized
    assert all(secret not in serialized for secret in env.credentials.values())
    assert all(os.environ[name] == value for name, value in env.credentials.items())


@pytest.mark.parametrize("provider", ["cc", "cx"])
def test_two_bound_peers_and_unbound_peer_have_separate_sessions(env, provider):
    a, root_a = env.bind("worker-a", provider)
    b, root_b = env.bind("worker-b", provider)
    first, second, plain = env.run("worker-a"), env.run("worker-b"), env.run(provider)
    assert all(result["status"] == "delivered" for result in (first, second, plain))
    assert len({result["session"]["handle"] for result in (first, second, plain)}) == 3
    var = "CLAUDE_CONFIG_DIR" if provider == "cc" else "CODEX_HOME"
    assert [call["env"][var] for call in env.calls()] == [str(root_a), str(root_b), env.ambient[var]]
    assert all("--resume" not in c["argv"] and "resume" not in c["argv"] for c in env.calls())
    assert len({target.binding() for target in env.targets}) == 3
    assert a.slot_id != b.slot_id
    again = env.run("worker-a", request="next")
    assert again["session"]["handle"] == first["session"]["handle"]
    assert "--resume" in env.calls()[-1]["argv"] or "resume" in env.calls()[-1]["argv"]
    with sqlite3.connect(env.db) as conn:
        rows = conn.execute("SELECT peer_id,binding FROM bridge_session_history").fetchall()
    assert len({binding for _, binding in rows}) == 3


def test_runtime_injection_refused_before_runtime_or_worker_use(env):
    env.bind()
    runtime = FakeRuntimeTarget()
    with pytest.raises(PrespawnError, match="(?i)runtime|account|bound"):
        env.run(runtime=runtime)
    assert runtime.calls == [] and env.calls() == []


@pytest.mark.parametrize("mutation", ["delete", "slot", "root"])
def test_claim_then_binding_recheck_is_not_started_and_releases_claim(env, monkeypatch, mutation):
    env.bind()
    acquire = ClaimStore.acquire
    acquired = []

    def raced(store, *args, **kwargs):
        token = acquire(store, *args, **kwargs)
        acquired.append(token)
        with sqlite3.connect(env.db) as conn:
            if mutation == "delete":
                conn.execute("DELETE FROM account_bindings WHERE peer_id='worker'")
            elif mutation == "slot":
                conn.execute("UPDATE account_bindings SET slot_id=? WHERE peer_id='worker'", ("f" * 16,))
            else:
                conn.execute("UPDATE account_bindings SET config_root=? WHERE peer_id='worker'", (str(env.home),))
        return token

    monkeypatch.setattr(ClaimStore, "acquire", raced)
    result = env.run()
    assert len(acquired) == 1
    assert result["status"] == "failed_not_started" and result["certainty"] == "NOT_STARTED"
    assert env.calls() == []
    assert_claim_released(env.db)


@pytest.mark.parametrize("provider", ["cc", "cx"])
@pytest.mark.parametrize("change", ["delete", "move"])
def test_settled_replay_does_not_validate_or_spawn_deleted_root(env, monkeypatch, provider, change):
    _, root = env.bind(provider=provider)
    first = env.run()
    assert first["status"] == "delivered"
    calls = env.calls()
    if change == "delete":
        root.rmdir()
    else:
        root.rename(root.with_name(root.name + "-moved"))

    def forbidden(*args, **kwargs):
        raise AssertionError("settled replay must not bind an environment or spawn")

    monkeypatch.setattr(CliRuntimeTarget, "bind_account_env", forbidden)
    monkeypatch.setattr("peerhub.extensions.adapters.base.BoundedProcess", forbidden)
    replay = env.run()
    assert replay["status"] == "recovered_terminal" and replay["response"] == first["response"]
    assert replay["session"] == first["session"] and env.calls() == calls


def test_root_replaced_with_link_refused_before_spawn(env):
    _, root = env.bind()
    actual = root.with_name("replacement")
    root.rename(actual)
    link_dir(root, actual)
    result = env.run()
    assert result["status"] == "failed_not_started" and result["certainty"] == "NOT_STARTED"
    assert env.calls() == []
    assert_claim_released(env.db)


@pytest.mark.parametrize("relation", ["equal", "ancestor", "descendant"])
def test_workspace_root_overlap_is_prespawn_and_releases_claim(env, relation):
    _, root = env.bind()
    workspace = {"equal": root, "ancestor": root.parent, "descendant": root / "workspace"}[relation]
    workspace.mkdir(exist_ok=True)
    result = env.run(workspace=workspace)
    assert result["status"] == "failed_not_started" and result["certainty"] == "NOT_STARTED"
    assert env.calls() == []
    assert_claim_released(env.db)


def test_missing_root_prespawn_failure_can_be_retried_after_repair(env):
    _, root = env.bind()
    root.rmdir()
    failure = env.run()
    assert failure["status"] == "failed_not_started" and failure["certainty"] == "NOT_STARTED"
    assert env.calls() == []
    assert_claim_released(env.db)
    root.mkdir()
    assert env.run()["status"] == "delivered"
    assert len(env.calls()) == 1
    with sqlite3.connect(env.db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM records WHERE kind='prompt'").fetchone()[0] == 1


@pytest.mark.parametrize("provider", ["cc", "cx"])
def test_quota_only_probes_builtin_aliases(env, provider):
    env.bind(provider=provider)
    calls = []

    def poll(**kwargs):
        calls.append(kwargs["instance_id"])
        return []  # deterministic unmeasured probe, never a network call

    pollers = {provider: poll}
    with pytest.raises(ValueError, match="quota|provider|bound"):
        refresh_quota(env.db, ["worker"], pollers=pollers, version_checker=lambda _: None)
    assert calls == []
    alias = "claude" if provider == "cc" else "codex"
    refresh_quota(env.db, [provider, alias], pollers=pollers, version_checker=lambda _: None)
    assert calls == [provider, alias] and env.calls() == []


@pytest.mark.parametrize("provider", ["cc", "cx"])
def test_unbound_ask_golden_identity_and_output(env, provider):
    result = env.run(provider)
    assert {key: result[key] for key in ("status", "certainty", "error")} == {
        "status": "delivered", "certainty": "TERMINAL", "error": None}
    assert result["request_id"] == "req" and result["peer_id"] == provider
    assert result["stream_id"] == f"peer:{provider}:chat"
    assert result["session"]["mode"] == "fresh" and result["session"]["reason"] == "no_prior"
    assert "env_scrubbed" not in result  # unbound wire contract remains unchanged
    call, = env.calls()
    assert all(call["env"][name] == value for name, value in env.credentials.items())
    assert all(call["env"][name] == value for name, value in env.ambient.items())
    golden_values = {**env.ambient, **{name: "[REDACTED]" for name in env.credentials}}
    assert result["response"] == "\n".join(name + "=" + golden_values[name] for name in sorted(golden_values))
    target, = env.targets
    scope = os.path.normcase(str(env.workspace.resolve())) + "|r"
    binding = "default/default#" + hashlib.sha256(scope.encode()).hexdigest()[:12]
    assert target.binding() == binding
    prompt = next(r for r in CoreStore(env.db).read_records(result["stream_id"]) if r.kind == "prompt")
    assert prompt.metadata["binding"] == binding
    assert prompt.metadata["adapter_fingerprint"] == f"adapter:{provider}:v2"
    assert "account" not in prompt.metadata and "env_scrubbed" not in prompt.metadata


@pytest.mark.parametrize("provider", ["cc", "cx"])
def test_json_escaped_root_error_is_redacted_in_result_evidence_and_cli(env, monkeypatch, capsys, provider):
    from peerhub.cli.app import main

    _, root = env.bind(provider=provider)
    monkeypatch.setenv("FAKE_ACCOUNT_MODE", "nonzero")
    args = ["--db", str(env.db), "ask", "worker", "inspect account environment", "--request-id", "req",
            "--workspace", str(env.workspace), "--timeout-seconds", "10", "--json"]
    assert main(args) == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["status"] == "uncertain"
    with sqlite3.connect(env.db) as conn:
        evidence = [json.loads(row[0]) for row in conn.execute("SELECT detail FROM bridge_evidence")]
    blobs = [output.out, output.err, json.dumps(result, ensure_ascii=False),
             *[str(value) for value in result.values()],
             *[str(value) for item in evidence for value in item.values()],
             *[str(value) for item in env.targets[0].evidence for value in item.values()]]
    raw = str(root)
    spellings = (raw, json.dumps(raw)[1:-1], raw.replace("\\", "/"), raw.casefold())
    assert all(spelling not in blob for spelling in spellings for blob in blobs)
    assert any("[REDACTED]" in blob for blob in blobs)


@pytest.mark.parametrize("provider", ["cc", "cx"])
@pytest.mark.parametrize("mode,status", [("vendor_error", "not_executed"), ("nonzero", "uncertain"), ("hang", "uncertain")])
def test_failed_bound_ask_disclosure_is_durable_for_replay(env, monkeypatch, provider, mode, status):
    env.bind(provider=provider)
    monkeypatch.setenv("FAKE_ACCOUNT_MODE", mode)
    result = env.run(timeout_s=0.5 if mode == "hang" else 10)
    expected = set(env.credentials) | ({"CODEX_HOME"} if provider == "cc" else set())
    assert result["status"] == status and set(result["env_scrubbed"]) == expected
    with sqlite3.connect(env.db) as conn:
        evidence = [json.loads(row[0]) for row in conn.execute("SELECT detail FROM bridge_evidence WHERE delivery_id=?", (result["delivery_id"],))]
    assert any(set(item.get("env_scrubbed", [])) == expected for item in evidence)
    before = env.calls()
    for name in expected:
        monkeypatch.delenv(name, raising=False)
    replay = env.run()
    assert replay["status"] == ("not_executed" if mode == "vendor_error" else "blocked_uncertain")
    assert set(replay["env_scrubbed"]) == expected and env.calls() == before
    for payload in (result, replay, evidence):
        serialized = json.dumps(payload)
        assert all(value not in serialized for value in env.credentials.values())
