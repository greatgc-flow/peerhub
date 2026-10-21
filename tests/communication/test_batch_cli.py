"""Batch CLI contracts using scratch stores and fake workers only."""
import json
import signal
import sqlite3
import sys
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.extensions import batch

FAKE = str(Path(__file__).resolve().parent / "harness" / "fake_worker.py")


def manifest(tmp_path, *, peer="cx", prompt="mode:ok", rows=None):
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps({"tasks": rows or [{"task_id": "a", "peer": peer, "prompt": prompt}]}), encoding="utf-8")
    return path


def argv(tmp_path, path, *options):
    return ["--db", str(tmp_path / "missing" / "core.db"), "batch", "run", str(path),
            "--batch-id", "b1", "--max-parallel", "2", *options]


@pytest.mark.parametrize("flag", ["--batch-id", "--max-parallel"])
def test_required_options(tmp_path, flag):
    args = argv(tmp_path, manifest(tmp_path))
    at = args.index(flag)
    del args[at:at + 2]
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2


@pytest.mark.parametrize("limit", ["0", "17"])
@pytest.mark.parametrize("json_output", [False, True])
def test_parallel_limit_error(tmp_path, capsys, limit, json_output):
    args = argv(tmp_path, manifest(tmp_path), *(["--json"] if json_output else []))
    args[args.index("--max-parallel") + 1] = limit
    assert main(args) == 1
    output = capsys.readouterr()
    if json_output:
        result = json.loads(output.out)
        assert result["status"] == "error" and result["error"]["code"] == "invalid_manifest"
        assert "--max-parallel" in result["error"]["message"] and not output.err
    else:
        assert "ERROR:" in output.err and "--max-parallel" in output.err and not output.out


@pytest.mark.parametrize("peer,writable", [("cx", False), ("ag", True), ("agy", True)])
def test_dry_run_no_spawn_or_store(tmp_path, capsys, monkeypatch, peer, writable):
    def forbidden(*args, **kwargs):
        raise AssertionError("dry run must not execute workers")

    monkeypatch.setattr(batch, "run_batch", forbidden)
    assert main(argv(tmp_path, manifest(tmp_path, peer=peer), "--dry-run", "--json")) == 0
    plan = json.loads(capsys.readouterr().out)
    stream, request = batch.derive_ids("b1", "a")
    assert plan == {"dry_run": True, "batch_id": "b1", "max_parallel": 2, "tasks": [
        {"task_id": "a", "peer": peer, "stream": stream, "request_id": request, "writable": writable}]}
    assert not (tmp_path / "missing").exists()


def test_unknown_peer(tmp_path, capsys):
    assert main(argv(tmp_path, manifest(tmp_path, peer="unknown"), "--dry-run", "--json")) == 1
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "invalid_manifest" and "unknown peer" in error["message"]
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("adapter", ["codex", "agy", "unsupported", None])
def test_registered_peer_readonly_lookup(tmp_path, capsys, adapter):
    db = tmp_path / "missing" / "core.db"
    db.parent.mkdir()
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE peers (peer_id TEXT, adapter_ref TEXT)")
        conn.execute("INSERT INTO peers VALUES (?, ?)", ("worker", adapter))
    before = db.read_bytes()
    code = main(argv(tmp_path, manifest(tmp_path, peer="worker"), "--dry-run", "--json"))
    result = json.loads(capsys.readouterr().out)
    if adapter in ("codex", "agy"):
        assert code == 0 and result["tasks"][0]["writable"] == (adapter == "agy")
    else:
        assert code == 1 and result["error"]["code"] == "invalid_manifest"
    assert db.read_bytes() == before


def test_missing_peers_table(tmp_path, capsys):
    db = tmp_path / "missing" / "core.db"
    db.parent.mkdir()
    sqlite3.connect(db).close()
    assert main(argv(tmp_path, manifest(tmp_path, peer="worker"), "--dry-run", "--json")) == 1
    assert "unknown peer" in json.loads(capsys.readouterr().out)["error"]["message"]


def test_ag_overlap_rejected_before_spawn(tmp_path, capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid batches must not execute")

    monkeypatch.setattr(batch, "run_batch", forbidden)
    path = manifest(tmp_path, rows=[{"task_id": "a", "peer": "ag", "prompt": "x"},
                                    {"task_id": "b", "peer": "cx", "prompt": "x"}])
    assert main(argv(tmp_path, path, "--json")) == 1
    assert "overlap" in json.loads(capsys.readouterr().out)["error"]["message"]


@pytest.mark.parametrize("mode,status,code", [("ok", "COMPLETED", 0), ("uncertain", "UNCERTAIN", 1)])
def test_fake_worker_run(tmp_path, capsys, monkeypatch, mode, status, code):
    monkeypatch.chdir(tmp_path)
    seen = []

    def builder(task, *, stream, request_id, db, query_file):
        seen.append(db)
        return [sys.executable, FAKE, str(query_file), str(tmp_path), task.task_id]

    monkeypatch.setattr(batch, "default_argv_builder", builder)
    args = argv(tmp_path, manifest(tmp_path, prompt=f"mode:{mode}"), "--json")
    args[1] = "relative/core.db"
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    assert main(args) == code
    report = json.loads(capsys.readouterr().out)
    assert report["batch_id"] == "b1" and report["max_parallel"] == 2
    assert report["tasks"][0]["status"] == status and "response" not in report["tasks"][0]
    assert seen == [str(tmp_path / "relative" / "core.db")]
    assert all(signal.getsignal(sig) == handler for sig, handler in previous.items())


@pytest.mark.parametrize("fail", [False, True])
def test_signal_cancellation_and_restore(tmp_path, capsys, monkeypatch, fail):
    signals = [signal.SIGINT, signal.SIGTERM]
    if sys.platform == "win32":
        signals.append(signal.SIGBREAK)
    previous = {sig: signal.getsignal(sig) for sig in signals}

    def run(tasks, *, cancel, **kwargs):
        for sig in signals:
            handler = signal.getsignal(sig)
            assert callable(handler)
            handler(sig, None)
        assert cancel.is_set()
        if fail:
            raise RuntimeError("injected failure")
        return {"batch_id": "b1", "max_parallel": 2, "tasks": [{"task_id": "a", "peer": "cx", "status": "BLOCKED"}]}

    monkeypatch.setattr(batch, "run_batch", run)
    assert main(argv(tmp_path, manifest(tmp_path), "--json")) == 1
    assert all(signal.getsignal(sig) == handler for sig, handler in previous.items())
    output = capsys.readouterr()
    if fail:
        assert "injected failure" in output.err
    else:
        assert json.loads(output.out)["tasks"][0]["status"] == "BLOCKED"


def test_text_output(tmp_path, capsys, monkeypatch):
    path = manifest(tmp_path)
    assert main(argv(tmp_path, path, "--dry-run")) == 0
    assert "TASK PEER STREAM REQUEST_ID WRITABLE" in capsys.readouterr().out

    def builder(task, *, stream, request_id, db, query_file):
        return [sys.executable, FAKE, str(query_file), str(tmp_path), task.task_id]

    monkeypatch.setattr(batch, "default_argv_builder", builder)
    assert main(argv(tmp_path, path)) == 0
    assert "COMPLETED" in capsys.readouterr().out
