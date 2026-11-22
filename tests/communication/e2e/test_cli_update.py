"""Quota-free fixtures for the public update commands."""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from peerhub import _console
from peerhub.cli.app import main
from peerhub.core.models import Peer
from peerhub.core.store import CoreStore, StoreReplacedError
from peerhub.core.workspace import Workspace
from peerhub.extensions.bridge_claims import _DDL as CLAIMS_DDL
from peerhub.extensions.bridge_registry import DDL as SESSION_DDL, POINTER_DDL
from peerhub.extensions.caller import host_id

_PLAN_TIME = datetime(2026, 11, 22, 1, 2, 3, tzinfo=UTC)


def test_update_requires_exactly_one_mode(capsys):
    for flags, message in (([], "one of the arguments"), (["--check", "--plan"], "not allowed with argument")):
        with pytest.raises(SystemExit) as error:
            main(["update", *flags])
        assert error.value.code == 2
        assert message in capsys.readouterr().err


def fake_check(monkeypatch, *, offline):
    from peerhub.cli import update

    calls = []
    monkeypatch.setattr(update, "real_command", lambda peer: [{"cx": "codex", "cc": "claude", "ag": "agy"}[peer]])
    monkeypatch.setattr(update.shutil, "which", lambda exe: exe if exe == "ant" else None)

    def run(argv, **kwargs):
        calls.append(tuple(argv))
        assert kwargs["stdin"] is None and 0 < kwargs["timeout_s"] <= 5
        assert 0 < kwargs["max_bytes"] <= 2 * 1024 * 1024
        output = {
            ("codex", "--version"): "codex-cli 0.172.0",
            ("claude", "--version"): "2.1.329 (Claude Code)",
            ("agy", "--version"): "",
            ("ant", "--version"): "ant 1.2.3",
            ("codex", "debug", "models", "--bundled"): '{"models": [{"slug": "gpt-6.2-sol"}]}',
        }[tuple(argv)]
        return SimpleNamespace(returncode=0 if output else 1, stdout=output.encode(), stderr=b"",
                               timed_out=False, output_truncated=False, drain_timed_out=False)

    def fetch(request, *, timeout):
        assert 0 < timeout <= 3
        url = request.full_url
        assert url.startswith("https://") and ("/releases/latest" in url or url == "https://pypi.org/pypi/peerhub/json")
        if offline:
            raise TimeoutError("offline")
        if "pypi.org" in url:
            payload = {"info": {"version": "9.0.0"}}
        else:
            tag = "rust-v0.173.0" if "/openai/codex/" in url else "v1.2.3"
            payload = {"tag_name": tag}
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(update, "run_bounded", run)
    monkeypatch.setattr(update, "urlopen", fetch)
    return calls


@pytest.mark.parametrize("offline", [True, False])
def test_check_versions_and_bundled_models(monkeypatch, capsys, offline):
    calls = fake_check(monkeypatch, offline=offline)
    assert main(["update", "--check", "--json"]) == 0
    captured = capsys.readouterr()
    assert not captured.err
    report = json.loads(captured.out)
    versions = report["versions"]
    assert versions["peerhub"]["latest"] == (None if offline else "9.0.0")
    assert versions["codex"]["latest"] == (None if offline else "0.173.0")
    assert versions["codex"]["packaged"] == "0.171.0"
    assert versions["codex"]["installed"] == "0.172.0"
    assert versions["codex"]["differs_from_packaged"] is True
    assert versions["codex"]["update_available"] is (None if offline else True)
    assert versions["agy"]["installed"] is None
    assert report["codex_models"]["missing"]["cx.standard"] == "gpt-6-luna"
    assert report["codex_models"]["source"] == "bundled"
    assert set(calls) == {("codex", "--version"), ("claude", "--version"), ("agy", "--version"), ("ant", "--version"),
                          ("codex", "debug", "models", "--bundled")}


def seed_plan(conn, cwd, *, active):
    conn.executescript(CLAIMS_DDL + ";" + POINTER_DDL + ";" + SESSION_DDL[0] + ";")
    conn.executescript("""
        CREATE TABLE bridge_deliveries (
            delivery_id TEXT, stream_id TEXT, peer_id TEXT, external_session_id TEXT, session_generation INTEGER
        );
        CREATE TABLE bridge_evidence (
            seq INTEGER PRIMARY KEY, delivery_id TEXT, stream_id TEXT, peer_id TEXT, kind TEXT, detail TEXT
        );
    """)
    now = time.time()
    for idx, (stream, path, vendor) in enumerate([
        ("ready-stream", cwd, "vendor-id-1"),
        ("missing-cwd", None, "vendor-id-2"),
        ("missing-vendor", cwd, None),
    ], 1):
        handle, external = f"ps_{idx}", f"opaque-{idx}"
        conn.execute("INSERT INTO bridge_sessions VALUES (?, 'cx', ?, 1, ?)", (stream, handle, now))
        conn.execute(
            "INSERT INTO bridge_session_history (session_handle, stream_id, peer_id, runtime_kind, external_session_id, "
            "session_generation, adapter_fingerprint, binding, resumable, state, last_seen, vendor_session_id, "
            "context_watermark, created_at) VALUES (?, ?, 'cx', 'cx', ?, 1, 'fp', 'binding', 1, 'ACTIVE', ?, ?, 1, ?)",
            (handle, stream, external, now, vendor, now),
        )
        conn.execute("INSERT INTO bridge_deliveries VALUES (?, ?, 'cx', ?, 1)", (str(idx), stream, external))
        detail = dict(v=1, cwd=path, host=host_id(), tool="cc", session="lead-1", batch="batch-1")
        conn.execute("INSERT INTO bridge_evidence VALUES (?, ?, ?, 'cx', 'caller', ?)",
                     (idx, str(idx), stream, json.dumps(detail)))
    if active:
        conn.execute("INSERT INTO bridge_claims VALUES ('ready-stream', 'cx', 'owner', 1, ?, 60, 'ws')", (now + 60,))
    conn.commit()


def test_plan_snapshot():
    from peerhub.cli import update

    db = Path("test-update.db").resolve()
    with closing(sqlite3.connect(":memory:")) as conn:
        seed_plan(conn, str(db.parent), active=True)
        conn.execute("PRAGMA query_only=1")
        active = update._store_plan(conn, db, "ws")
        conn.rollback()
        idle = update._store_plan(conn, db, "replacement")
        assert not idle[0] and all(hint["kind"] != "stream" for hint in idle[1])
        conn.rollback()
        conn.execute("PRAGMA query_only=0")
        conn.execute("DELETE FROM bridge_claims")
        conn.commit()
        before = list(conn.iterdump())
        conn.execute("PRAGMA query_only=1")
        idle = update._store_plan(conn, db, None)
        assert list(conn.iterdump()) == before
    assert active == ([{"stream": "ready-stream", "peer": "cx"}], [])
    assert [(hint["kind"], hint["id"]) for hint in idle[1]] == [
        ("stream", "ready-stream"), ("lead", "lead-1"), ("batch", "batch-1"),
    ]
    assert "--session=ps_1" in idle[1][0]["hint"]
    assert "vendor-id-1" not in idle[1][0]["hint"]
    assert not db.exists()


def test_plan_missing_and_legacy_rows(capsys):
    from peerhub.cli import update

    db = Path("missing-update-fixture.db").resolve()
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("CREATE TABLE bridge_sessions (stream_id TEXT, external_session_id TEXT)")
        conn.execute("CREATE TABLE bridge_evidence (seq INTEGER, kind TEXT, detail TEXT)")
        conn.execute("INSERT INTO bridge_evidence VALUES (1,'caller','{}')")
        conn.commit()
        assert update._store_plan(conn, db, None) == ([], [])
    assert main(["--db", str(db), "update", "--plan", "--json"]) == 5
    output = capsys.readouterr()
    assert "[store_missing]" in output.err and not json.loads(output.out)["commands"]
    assert not db.exists()


def test_process_scan_classifies_invocations():
    from peerhub.cli.update import _process_command

    assert _process_command("python", ["python", "-m", "peerhub.cli", "--json", "--db", "ask", "monitor"]) == "peerhub monitor"
    assert _process_command("peerhub.exe", ["peerhub.exe", "--db=core.db", "ask", "cc", "private"]) == "peerhub ask"
    assert _process_command("peerhub.exe", ["peerhub.exe", "update", "--plan"]) is None
    assert _process_command("peerhub.exe", []) == "peerhub (arguments unavailable)"
    assert _process_command("node", ["node", "/usr/bin/codex.js", "exec"]) == "codex"


@pytest.mark.parametrize("active", [True, False])
def test_plan_selected_store(tmp_path, monkeypatch, capsys, active):
    from peerhub.cli import update

    monkeypatch.setattr(update, "_blocking_processes", lambda: [{"pid": 321, "command": "peerhub monitor"}])
    db = tmp_path / "spaced store.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        seed_plan(conn, str(tmp_path), active=active)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}

    code = main(["--db", str(db), "update", "--plan", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert report["processes"] == [{"pid": 321, "command": "peerhub monitor"}]
    if active:
        assert code == 1 and report["running_asks"] == [{"stream": "ready-stream", "peer": "cx"}]
        assert report["commands"] == []
    else:
        assert code == 0 and report["running_asks"] == []
        assert ".backup" in report["commands"][0]["command"]
        assert "upgrade peerhub" in report["commands"][1]["command"]
        assert "peerhub" in report["commands"][-1]["command"] and "monitor" in report["commands"][-1]["command"]
        assert [(hint["kind"], hint["id"]) for hint in report["resume"]] == [
            ("stream", "ready-stream"), ("lead", "lead-1"), ("batch", "batch-1"),
        ]
        assert "--session" in report["resume"][0]["hint"]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("shell", ["nt", "posix"])
def test_plan_prints_restore_after_backup(shell, monkeypatch, capsys):
    from peerhub.cli import update

    db = Path("a space [and 'quotes' and ’quotes’]") / "core.db"
    monkeypatch.setattr(update, "os", SimpleNamespace(name=shell))
    monkeypatch.setattr(_console, "os", SimpleNamespace(name=shell, environ=os.environ))
    clock = SimpleNamespace(time=_PLAN_TIME.timestamp)
    monkeypatch.setattr(update, "time", clock)
    monkeypatch.setattr(update, "_plan", lambda _: (
        dict(status="READY", db=str(db), running_asks=[], processes=[], commands=update._upgrade_commands(db),
             resume=[], next="Ready"), 0,
    ))
    argv = ["--db", str(db), "update", "--plan"]
    assert main([*argv, "--json"]) == 0
    commands = json.loads(capsys.readouterr().out)["commands"]
    backup, upgrade, *_ = commands
    restore = backup["restore"]
    health = update._shell("peerhub", "--db", str(db), "diag", "health")
    health_suffix = f"; if ($?) {{ {health} }}" if shell == "nt" else f" && {health}"
    assert restore["command"].startswith(update._python("-c").rsplit("-c", 1)[0] + "-c ")
    assert restore["command"].endswith(health_suffix)
    assert "stop all clients" in restore["step"].lower()
    timestamp = _PLAN_TIME.strftime(update.UPDATE_BACKUP_TIMESTAMP_FORMAT)
    backup_path = str(db) + update.UPDATE_BACKUP_SUFFIX.format(timestamp=timestamp)
    paths = update._shell(str(db), backup_path)
    assert backup["command"].endswith(paths)
    assert restore["command"].removesuffix(health_suffix).endswith(paths)

    assert main(argv) == 0
    out = capsys.readouterr().out
    lines = [line.strip() for line in out.splitlines()]
    idx = lines.index(backup["command"])
    assert lines[idx + 1:idx + 3] == [restore["step"], restore["command"]]
    assert idx + 3 < lines.index(upgrade["command"])


def test_update_plans_use_distinct_utc_backup_paths(monkeypatch):
    from peerhub.cli import update

    db = Path("core.db")
    moments = (_PLAN_TIME, _PLAN_TIME + timedelta(seconds=1))
    times = iter(moment.timestamp() for moment in moments)
    monkeypatch.setattr(update, "time", SimpleNamespace(time=lambda: next(times)))
    plans = [update._upgrade_commands(db)[0] for _ in moments]
    assert plans[0]["command"] != plans[1]["command"]
    for plan, moment in zip(plans, moments, strict=True):
        suffix = update.UPDATE_BACKUP_SUFFIX.format(timestamp=moment.strftime(update.UPDATE_BACKUP_TIMESTAMP_FORMAT))
        quoted_snapshot = update._shell(str(db) + suffix)
        assert plan["command"].endswith(quoted_snapshot)
        assert quoted_snapshot in plan["restore"]["command"]


def _windows_powershell() -> str:
    """The sandboxed release run strips PATH: find Windows PowerShell by its fixed system location when it is not on PATH."""
    found = shutil.which("powershell.exe")
    if found:
        return found
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def _run_printed_python(command: str) -> subprocess.CompletedProcess[str]:
    shell = ([_windows_powershell(), "-NoProfile", "-NonInteractive", "-Command", f"{command}; exit $LASTEXITCODE"]
             if os.name == "nt" else ["sh", "-c", command])
    return subprocess.run(shell, capture_output=True, text=True, timeout=30, check=False)


@pytest.mark.parametrize("db_name", ["core.db", "core.restore.tmp"])
def test_printed_backup_and_restore_replace_the_selected_store(tmp_path, monkeypatch, capsys, db_name):
    from peerhub.cli import update

    monkeypatch.setattr(update, "_blocking_processes", lambda: [])
    workspace = Workspace(tmp_path / "workspace [with 'quotes' and ’quotes’]")
    db = workspace.root / db_name
    store = CoreStore(db)
    original = store.register_peer(Peer(peer_id="saved", display_name="Before backup"))
    generation = workspace.generation()

    assert main(["--db", str(db), "update", "--plan", "--json"]) == 0
    backup = json.loads(capsys.readouterr().out)["commands"][0]
    separator = "; if ($?) { " if os.name == "nt" else " && "
    restore, delimiter, _ = backup["restore"]["command"].partition(separator)
    assert delimiter and restore.startswith(update._python("-c").rsplit("-c", 1)[0] + "-c ")

    result = _run_printed_python(backup["command"])
    assert result.returncode == 0, result.stderr
    (snapshot,) = workspace.root.glob(db.name + update.UPDATE_BACKUP_SUFFIX.format(timestamp="*"))
    backup_bytes = snapshot.read_bytes()
    assert CoreStore(snapshot, readonly=True).get_peer(original.peer_id) == original

    changed = store.register_peer(Peer(peer_id=original.peer_id, display_name="After backup"))
    assert changed != original
    rerun = _run_printed_python(backup["command"])
    assert rerun.returncode != 0 and "FileExistsError" in rerun.stderr
    assert snapshot.read_bytes() == backup_bytes

    result = _run_printed_python(restore)
    assert result.returncode == 0, result.stderr
    assert workspace.generation() != generation
    with pytest.raises(StoreReplacedError):
        store.get_peer(original.peer_id)
    assert CoreStore(db, readonly=True).get_peer(original.peer_id) == original
    assert snapshot.read_bytes() == backup_bytes


def test_plan_reads_committed_wal(tmp_path, monkeypatch, capsys):
    from peerhub.cli import update

    monkeypatch.setattr(update, "_blocking_processes", lambda: [])
    db = tmp_path / "core.db"
    with closing(sqlite3.connect(db)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        seed_plan(writer, str(tmp_path), active=True)
        assert main(["--db", str(db), "update", "--plan", "--json"]) == 1
        report = json.loads(capsys.readouterr().out)
        assert report["running_asks"] == [{"stream": "ready-stream", "peer": "cx"}] and not report["commands"]
