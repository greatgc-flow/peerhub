"""Explicit profile policy and post-spawn inactivity safety, without provider calls."""
import json
import os
import sqlite3
import sys

import pytest

from peerhub.core.models import Peer
from peerhub.core.schema_version import SchemaVersionError
from peerhub.core.store import CoreStore, IdempotencyConflictError
from peerhub.extensions.adapters import CliRuntimeTarget
from peerhub.extensions.adapters.base import SPECS
from peerhub.extensions.adapters.process import run_bounded
from peerhub.extensions.adapters.profiles import resolve_profile
from peerhub.extensions.ask import ask
from tests.communication.adapters.conftest import FAKE, calls


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "config"))


@pytest.mark.parametrize("kind", ["cx", "cc", "ag"])
def test_explicit_standard_profile_resolves_packaged_policy(tmp_path, kind):
    model, effort = resolve_profile(kind, f"{kind}.standard", tmp_path)
    runtime = CliRuntimeTarget(kind, tmp_path, profile=f"{kind}.standard")
    assert model and runtime.binding().split("#")[0] == f"{kind}.standard/{model}/{effort or 'default'}"
    assert CliRuntimeTarget(kind, tmp_path).binding().split("#")[0] == "default/default"
    assert not runtime.resumable  # selecting a policy does not invent capabilities


def test_profile_precedence_and_explicit_overrides(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "models.toml").write_text('[profiles."cx.standard"]\nmodel="global"\nreasoning_effort="medium"\n', encoding="utf-8")
    workspace_config = tmp_path / ".peerhub" / "config"
    workspace_config.mkdir(parents=True)
    (workspace_config / "models.toml").write_text('[profiles."cx.standard"]\nmodel="workspace"\n', encoding="utf-8")
    assert resolve_profile("cx", "cx.standard", tmp_path) == ("workspace", "medium")
    runtime = CliRuntimeTarget("cx", tmp_path, profile="cx.standard", model="explicit", effort="low")
    assert runtime.binding().split("#")[0] == "cx.standard/explicit/low"


@pytest.mark.parametrize("content", [
    'schema_version=2\n',
    'profiles="not a table"\n',
    '[profiles."cx.standard"]\nselection_mode="invented"\n',
    '[profiles."cx.standard"]\nmodel=12\n',
    '[profiles."cx.standard"]\nreasoning_effort=12\n',
    '[profiles."cx.standard"]\nmodel="bad model"\n',
])
def test_bad_profile_config_is_rejected_before_bootstrap(tmp_path, content):
    config = tmp_path / "config"
    config.mkdir()
    (config / "models.toml").write_text(content, encoding="utf-8")
    db = tmp_path / "new" / "core.db"
    with pytest.raises(ValueError):
        ask(db, "cx", "question", profile="cx.standard")
    assert not db.parent.exists()


def test_custom_cli_default_profile_and_registered_peer(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "models.toml").write_text('[profiles."cx.custom"]\nselection_mode="cli_default"\n', encoding="utf-8")
    assert resolve_profile("cx", "cx.custom", tmp_path) == (None, None)
    assert CliRuntimeTarget("cx", tmp_path, profile="cx.custom").binding().split("#")[0] == "cx.custom/default/default"
    db = tmp_path / "core.db"
    CoreStore(db).register_peer(Peer(peer_id="reviewer", adapter_ref="codex"))
    import peerhub.extensions.ask as module
    real_runtime = CliRuntimeTarget
    monkeypatch.setattr(module, "CliRuntimeTarget", lambda kind, workspace, **kw:
                        real_runtime(kind, workspace, command=[sys.executable, FAKE], env_extra={"FAKE_KIND": kind}, **kw))
    result = ask(db, "reviewer", "question", workspace=tmp_path, profile="cx.custom")
    assert result["certainty"] == "TERMINAL"


@pytest.mark.parametrize("profile", ["cx.unknown", "cc.standard", "unknown", "cx."])
def test_bad_profiles_do_not_bootstrap_database(tmp_path, profile):
    db = tmp_path / "new" / "core.db"
    with pytest.raises(ValueError):
        ask(db, "cx", "question", profile=profile)
    assert not db.parent.exists()


def test_short_profile_is_identical_to_fully_qualified_profile(tmp_path):
    assert resolve_profile("cx", "standard", tmp_path) == resolve_profile("cx", "cx.standard", tmp_path)
    assert CliRuntimeTarget("cx", tmp_path, profile="standard").binding() == CliRuntimeTarget("cx", tmp_path, profile="cx.standard").binding()


def test_future_schema_rejected_before_peer_lookup_or_bootstrap(tmp_path):
    db = tmp_path / "future.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version=999")
        conn.execute("CREATE TABLE future_only (id TEXT)")
    before = db.read_bytes()
    with pytest.raises(SchemaVersionError):
        ask(db, "cx", "question", profile="standard")
    assert db.read_bytes() == before


def test_corrupt_database_classification_is_preserved(tmp_path):
    from peerhub.core.store import StorageCorruptError
    db = tmp_path / "corrupt.db"
    db.write_bytes(b"not a sqlite database")
    with pytest.raises(StorageCorruptError):
        ask(db, "cx", "question", profile="standard")


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_bad_silence_timeout_does_not_bootstrap_database(tmp_path, value):
    db = tmp_path / "new" / "core.db"
    with pytest.raises(ValueError):
        ask(db, "cx", "question", silence_timeout_s=value)
    assert not db.parent.exists()


def test_profile_idempotency_uses_resolved_model_and_effort(tmp_path, monkeypatch):
    import peerhub.extensions.ask as module
    real_runtime = CliRuntimeTarget
    log = tmp_path / "calls.jsonl"

    def factory(kind, workspace, **kwargs):
        return real_runtime(kind, workspace, command=[sys.executable, FAKE],
                            env_extra={"FAKE_KIND": kind, "FAKE_MODE": "ok", "FAKE_LOG": str(log)}, **kwargs)

    monkeypatch.setattr(module, "CliRuntimeTarget", factory)
    db = tmp_path / "core.db"
    first = ask(db, "cx", "question", profile="cx.standard", workspace=tmp_path, request_id="one")
    assert first["certainty"] == "TERMINAL"
    assert ask(db, "cx", "question", profile="cx.standard", workspace=tmp_path, request_id="one")["status"] == "recovered_terminal"
    with pytest.raises(IdempotencyConflictError):
        ask(db, "cx", "question", profile="cx.standard", model="other", workspace=tmp_path, request_id="one")
    assert len(calls(log)) == 1


def test_silent_process_is_killed_and_distinguished_from_wall_timeout(tmp_path):
    result = run_bounded([sys.executable, "-c", "import time; time.sleep(20)"],
                         stdin=None, cwd=str(tmp_path), env=os.environ, timeout_s=10, silence_timeout_s=0.3)
    assert result.silence_timed_out and not result.timed_out
    assert result.returncode is not None and result.returncode != 0


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_any_output_resets_silence_deadline(tmp_path, stream):
    code = f"import sys,time\nfor i in range(5):\n print(i,file=sys.{stream},flush=True); time.sleep(0.15)"
    result = run_bounded([sys.executable, "-u", "-c", code], stdin=None, cwd=str(tmp_path), env=os.environ,
                         timeout_s=10, silence_timeout_s=0.5)
    assert result.returncode == 0 and not result.silence_timed_out


def test_wall_deadline_still_bounds_chatty_process(tmp_path):
    code = "import time\nwhile True:\n print('active',flush=True); time.sleep(0.05)"
    # The child prints every 0.05 s. The silence window must be far larger than any scheduling hiccup of a loaded CI runner
    # (a 0.5 s window was starved on a busy Windows runner), while the wall deadline stays the one that fires.
    result = run_bounded([sys.executable, "-u", "-c", code], stdin=None, cwd=str(tmp_path), env=os.environ,
                         timeout_s=4.0, silence_timeout_s=3.0)
    assert result.timed_out and not result.silence_timed_out


def test_silence_timeout_is_uncertain_and_cannot_replay(tmp_path):
    log = tmp_path / "calls.jsonl"
    runtime = CliRuntimeTarget("cx", tmp_path, command=[sys.executable, FAKE], timeout_s=10, silence_timeout_s=0.4,
                               env_extra={"FAKE_KIND": "cx", "FAKE_MODE": "hang", "FAKE_LOG": str(log)})
    db = tmp_path / "core.db"
    first = ask(db, "cx", "question", request_id="one", runtime=runtime)
    assert first["status"] == "uncertain" and first["certainty"] == "STARTED"
    assert first["response"] is None
    assert ask(db, "cx", "question", request_id="one", runtime=runtime)["status"] == "blocked_uncertain"
    assert len(calls(log)) == 1
    assert CoreStore(db).get_offset("cx", first["stream_id"]).read_through_position == 0


def test_ag_effort_is_forwarded_and_contradictory_model_is_rejected(tmp_path):
    args = SPECS["ag"].argv("gemini-3.8-flash-low", "low", "question")
    assert args[-2:] == ["--effort", "low"]
    with pytest.raises(ValueError, match="conflicts"):
        CliRuntimeTarget("ag", tmp_path, model="gemini-3.8-flash-high", effort="low")
    with pytest.raises(ValueError, match="ag effort"):
        CliRuntimeTarget("ag", tmp_path, effort="ultra")
