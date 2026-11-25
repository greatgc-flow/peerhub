"""#374: caller detail schema v1 (`detect_caller`)."""
import os
import platform
import sys
from pathlib import Path

import pytest

from peerhub.extensions import caller

pytestmark = pytest.mark.unit

KEYS = {"v", "tool", "label", "session", "host", "ns", "ask_pid", "ask_created", "cwd", "batch", "task",
        "workspace", "query_file", "stream_file", "owner"}


@pytest.mark.parametrize("bad", ["//server/share/query.txt", r"\\?\C:\query.txt", r"\\.\NUL",
                                 "query\x1b.txt", "query\x85.txt", "query\n.txt", "query\u202e.txt"])
def test_unsafe_query_paths_are_rejected_before_resolution(bad, monkeypatch):
    known = Path(__file__).resolve()
    resolve = Path.resolve

    def fake_resolve(self, *args, **kwargs):
        return known if self == Path(bad) else resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    detail = caller.detect_caller({}, query_file=bad, workspace=known.parent)
    assert set(detail) == KEYS and detail["query_file"] is None and detail["stream_file"] is None


def test_relative_query_uses_caller_cwd_and_records_the_larger_execution_workspace(monkeypatch):
    query = Path(__file__).resolve()
    workspace = query.parents[1]
    monkeypatch.chdir(query.parent)
    detail = caller.detect_caller({}, query_file=query.name, workspace=workspace)
    assert detail["cwd"] == str(query.parent)
    assert detail["workspace"] == str(workspace)
    assert detail["query_file"] == str(query) and detail["stream_file"] is None


@pytest.mark.parametrize("outside", ["escape", "control", "directory"])
def test_query_path_is_checked_after_resolution(outside, monkeypatch):
    known = Path(__file__).resolve()
    query = known.parent / "alias.txt"
    if outside == "escape":
        target = known.parents[1] / "__init__.py"
    elif outside == "control":
        target = known.with_name("control\x1b.txt")
    else:
        target = known.parent
    resolve, is_file = Path.resolve, Path.is_file
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **kw: target if self == query else resolve(self, *a, **kw))
    monkeypatch.setattr(Path, "is_file", lambda self: True if self == target and outside == "control" else is_file(self))
    assert caller.detect_caller({}, query_file=query, workspace=known.parent)["query_file"] is None


def test_workspace_symlink_escape_is_rejected_at_write_and_read(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.touch()
    link = workspace / "alias.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    assert caller.detect_caller({}, query_file=link, workspace=workspace)["query_file"] is None
    assert caller.local_workspace_file(str(link), str(workspace)) is None


def test_claude_code_with_lead_label(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = caller.detect_caller({"CLAUDE_CODE_SESSION_ID": "abc-123_X.y", "PEERHUB_LEAD": "lead-one"})
    assert set(c) == KEYS
    assert (c["v"], c["tool"], c["label"], c["session"]) == (1, "cc", "lead-one", "abc-123_X.y")
    assert c["host"] == platform.node() + "|" + sys.platform
    assert c["ask_pid"] == os.getpid()
    assert isinstance(c["ask_created"], float)
    assert c["cwd"] == os.getcwd()
    assert c["batch"] is None and c["task"] is None


def test_unknown_tool_falls_back_to_the_folder_name(tmp_path, monkeypatch):
    folder = tmp_path / "my-project"
    folder.mkdir()
    monkeypatch.chdir(folder)
    c = caller.detect_caller({})
    assert (c["tool"], c["label"], c["session"]) == ("?", "my-project", None)


def test_default_env_is_os_environ(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PEERHUB_LEAD", "from-env")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    c = caller.detect_caller()
    assert (c["tool"], c["label"]) == ("?", "from-env")


def test_label_strips_controls_and_is_capped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert caller.detect_caller({"PEERHUB_LEAD": "a\x1b[31mb\x07\x85c"})["label"] == "a[31mbc"
    assert caller.detect_caller({"PEERHUB_LEAD": "x" * 100})["label"] == "x" * 40
    # only control characters: the override is empty and the folder name wins
    assert caller.detect_caller({"PEERHUB_LEAD": "\x00\x1b\n\r"})["label"] == tmp_path.name[:40]
    assert caller.detect_caller({"PEERHUB_LEAD": ""})["label"] == tmp_path.name[:40]


def test_label_is_unknown_without_cwd(monkeypatch):
    def gone():
        raise FileNotFoundError

    monkeypatch.setattr(caller.os, "getcwd", gone)
    c = caller.detect_caller({})
    assert (c["label"], c["cwd"]) == ("?", None)


@pytest.mark.parametrize("session", ["", "a b", "a/b", "x" * 129, "é", "a\nb", "a;rm", "\x00"])
def test_hostile_session_is_dropped_but_tool_stays_known(session):
    c = caller.detect_caller({"CLAUDE_CODE_SESSION_ID": session})
    assert c["session"] is None
    assert c["tool"] == ("cc" if session else "?")


def test_session_at_its_length_limit():
    assert caller.detect_caller({"CLAUDE_CODE_SESSION_ID": "s" * 128})["session"] == "s" * 128


def test_batch_ids_are_kept_raw():
    c = caller.detect_caller({"PEERHUB_BATCH_ID": "batch 1/é", "PEERHUB_BATCH_TASK": "t" * 128})
    assert (c["batch"], c["task"]) == ("batch 1/é", "t" * 128)


@pytest.mark.parametrize("bad", ["", "t" * 129, "a\x1bb", "a\nb", "a\u2028b", "a\x85b"])
def test_hostile_batch_ids_are_dropped(bad):
    c = caller.detect_caller({"PEERHUB_BATCH_ID": bad, "PEERHUB_BATCH_TASK": bad})
    assert c["batch"] is None and c["task"] is None


def test_psutil_failure_leaves_creation_unknown(monkeypatch):
    import psutil

    def boom(*_a, **_k):
        raise psutil.AccessDenied(os.getpid())

    monkeypatch.setattr(psutil, "Process", boom)
    c = caller.detect_caller({})
    assert c["ask_created"] is None and c["ask_pid"] == os.getpid()


def test_cwd_and_host_are_capped(monkeypatch):
    monkeypatch.setattr(caller.os, "getcwd", lambda: os.sep + "d" * 2000)  # a root path on every platform
    monkeypatch.setattr(caller.platform, "node", lambda: "n" * 400)
    c = caller.detect_caller({})
    assert len(c["cwd"]) == 1024 and len(c["host"]) == 255 and c["label"] == "d" * 40


def test_pid_namespace_per_platform(monkeypatch):
    monkeypatch.setattr(caller.sys, "platform", "win32")
    assert caller.pid_namespace() == "-"
    monkeypatch.setattr(caller.sys, "platform", "linux")
    monkeypatch.setattr(caller.os, "readlink", lambda p: "pid:[4026531836]" if p == "/proc/self/ns/pid" else "")
    assert caller.pid_namespace() == "pid:[4026531836]"

    def unreadable(_p):
        raise PermissionError

    monkeypatch.setattr(caller.os, "readlink", unreadable)
    assert caller.pid_namespace() is None
    # Restore the real sys.platform before detect_caller: a first `import psutil` under a faked "linux" breaks psutil
    # for the whole process.
    monkeypatch.undo()
    monkeypatch.setattr(caller, "pid_namespace", lambda: None)
    assert caller.detect_caller({})["ns"] is None


def test_batch_children_carry_the_raw_batch_and_task_ids(tmp_path, monkeypatch):
    import json

    from peerhub.extensions import batch

    monkeypatch.delenv("PEERHUB_BATCH_ID", raising=False)
    monkeypatch.delenv("PEERHUB_BATCH_TASK", raising=False)
    probe = ("import json, os, sys; open(sys.argv[1], 'w').write(json.dumps("
             "[os.environ.get('PEERHUB_BATCH_ID'), os.environ.get('PEERHUB_BATCH_TASK'), os.environ.get('PATH') is not None]))")

    def build(t, *, stream, request_id, db, query_file):
        return [sys.executable, "-c", probe, str(tmp_path / f"env-{t.task_id}.json")]

    tasks = [batch.Task(task_id=tid, peer="cc", prompt="p", workspace=None, profile=None, writable=False, timeout_s=30.0)
             for tid in ("t.1", "t-2")]
    batch.run_batch(tasks, batch_id="nightly_7", max_parallel=2, db=str(tmp_path / "x.db"), argv_builder=build)
    for tid in ("t.1", "t-2"):
        assert json.loads((tmp_path / f"env-{tid}.json").read_text()) == ["nightly_7", tid, True]
    assert "PEERHUB_BATCH_ID" not in os.environ and "PEERHUB_BATCH_TASK" not in os.environ


def _provider_env_case(monkeypatch):
    monkeypatch.setenv("PEERHUB_BATCH_ID", "nightly")
    monkeypatch.setenv("PEERHUB_BATCH_TASK", "t1")
    monkeypatch.setenv("PEERHUB_LEAD", "lead-x")


def test_the_provider_does_not_inherit_the_batch_ids(tmp_path, monkeypatch):
    """A nested `peerhub ask` made by the provider must not be recorded as a task of the outer batch."""
    from peerhub.extensions.adapters.cli_runtime import CliRuntimeTarget

    _provider_env_case(monkeypatch)
    env = CliRuntimeTarget("cc", tmp_path, command=["fake.exe"])._env  # pyright: ignore[reportPrivateUsage]
    assert "PEERHUB_BATCH_ID" not in env and "PEERHUB_BATCH_TASK" not in env
    assert env["PEERHUB_LEAD"] == "lead-x"


def test_an_account_bound_provider_does_not_inherit_the_batch_ids(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from peerhub.extensions.accounts import AccountRef
    from peerhub.extensions.adapters.cli_runtime import CliRuntimeTarget

    _provider_env_case(monkeypatch)
    target = CliRuntimeTarget("cc", tmp_path, command=["fake.exe"], account=AccountRef("slot-1"))
    target.bind_account_env(SimpleNamespace(slot_id="slot-1", provider="cc", root_realpath=tmp_path))  # type: ignore[arg-type]
    env = target._env  # pyright: ignore[reportPrivateUsage]
    assert "PEERHUB_BATCH_ID" not in env and "PEERHUB_BATCH_TASK" not in env
    assert env["PEERHUB_LEAD"] == "lead-x"
    assert "PEERHUB_BATCH_ID" not in target.env_scrubbed  # not an account secret: no claim of scrubbing


@pytest.mark.parametrize(("reader", "stored", "expected"), [
    ({"host": "a", "owner": "same"}, {"host": "b", "owner": "same"}, ("other", "host")),
    ({"host": "a", "owner": "same"}, {"host": "", "owner": "same"}, ("mine", "owner")),
    ({"ns": "pid:[1]", "owner": "same"}, {"ns": "pid:[2]", "owner": "same"}, ("other", "ns")),
    ({"owner": "same", "session": "a"}, {"owner": "same", "session": "b"}, ("mine", "owner")),
    ({"owner": "a", "session": "same"}, {"owner": "b", "session": "same"}, ("other", "owner")),
    ({"owner": "a", "session": "same"}, {"session": "same"}, ("mine", "session")),
    ({"session": "a"}, {"session": "b"}, ("other", "session")),
    ({"session": "bad id"}, {"session": "bad id"}, ("unknown", None)),
    ({"label": "same", "batch": "same"}, {"label": "same", "batch": "same"}, ("unknown", None)),
    ({}, {}, ("unknown", None)),
])
def test_ownership_precedence(reader, stored, expected):
    assert caller.caller_relation(reader, stored) == expected


def test_owner_and_session_share_validation():
    for value in ("lead-01_a.b", "not valid", "", "a" * 129):
        detail = caller.detect_caller({"PEERHUB_OWNER": value, "CLAUDE_CODE_SESSION_ID": value})
        assert detail["owner"] == detail["session"]
    assert caller.detect_caller({"PEERHUB_OWNER": "lead-01_a.b"})["owner"] == "lead-01_a.b"
    assert caller.detect_caller({})["owner"] is None


def test_provider_environment_makes_a_nested_ask_mine(monkeypatch):
    from peerhub.extensions.adapters.cli_runtime import CliRuntimeTarget

    _provider_env_case(monkeypatch)
    monkeypatch.setenv("PEERHUB_OWNER", "this-lead")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "this-session")
    reader = caller.detect_caller()
    env = CliRuntimeTarget("cc", Path.cwd(), command=["fake.exe"])._env  # pyright: ignore[reportPrivateUsage]
    nested = caller.detect_caller(env)
    assert nested["owner"] == reader["owner"] and nested["session"] == reader["session"]
    assert nested["batch"] is None and nested["task"] is None
    assert caller.caller_relation(reader, nested) == ("mine", "owner")


def test_never_raises(monkeypatch):
    class Hostile(dict):
        def get(self, *_a, **_k):
            raise RuntimeError("env exploded")

    c = caller.detect_caller(Hostile())
    assert set(c) == KEYS and c["v"] == 1 and c["tool"] == "?"
