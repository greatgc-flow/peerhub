"""Batch reports expose load and outcome facts without dispatching providers."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.core import load_limits
from peerhub.extensions import batch, diag_running
from tests.communication.test_batch_run import builder, write


def _task(task_id, peer="cc", **fields):
    return {"task_id": task_id, "peer": peer, "prompt": "mode:ok", **fields}


def _argv(db, manifest):
    return ["--db", str(db), "batch", "run", str(manifest), "--batch-id", "facts",
            "--max-parallel", str(batch.RECOMMENDED_MAX_PARALLEL)]


@pytest.fixture
def dry_run(monkeypatch):
    db = Path.cwd() / "batch-facts-missing.db"
    manifest = Path("batch-facts.json")

    def prepare(tasks):
        def read_manifest(path, **kwargs):
            assert path == manifest
            return json.dumps({"tasks": tasks})

        def fail_to_launch(*args, **kwargs):
            pytest.fail("manifest validation must finish before launching workers")

        monkeypatch.setattr(Path, "read_text", read_manifest)
        monkeypatch.setattr(batch, "run_batch", fail_to_launch)
        return _argv(db, manifest)

    return prepare


def test_dry_run_reports_load_facts_after_the_task_table(dry_run, monkeypatch, capsys):
    peer = "cc"
    limit = load_limits.PER_PEER_LIMITS[peer]
    ceiling = load_limits.ACTIVE_ASKS_CEILING
    count = max(limit, ceiling) + 1
    argv = dry_run([_task(f"t{i}", peer) for i in range(count)])
    active_by_peer = {peer: limit}
    sampled = load_limits.load_snapshot(active_by_peer, None)

    def sample(db):
        assert Path(db) == Path(argv[1])
        return sampled

    monkeypatch.setattr(diag_running, "sample_load", sample)
    assert main([*argv, "--dry-run", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["tasks"]) == count
    warnings = report["warnings"]
    assert len(warnings) == 3
    assert any(peer in line and f"{count} tasks" in line and f"limit {limit}" in line for line in warnings)
    assert any(f"{count} tasks" in line and f"ceiling {ceiling}" in line for line in warnings)
    assert f"active now {sum(active_by_peer.values())}" in warnings

    assert main([*argv, "--dry-run"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith("TASK PEER")
    assert [line.split()[0] for line in lines[2:2 + count]] == [row["task_id"] for row in report["tasks"]]
    assert lines[2 + count:] == warnings


@pytest.mark.parametrize("active", [None, 0, OSError("unreadable load")])
def test_dry_run_distinguishes_zero_from_unavailable_load(dry_run, monkeypatch, capsys, active):
    peer = "cc"
    argv = dry_run([_task(f"t{i}", peer) for i in range(load_limits.PER_PEER_LIMITS[peer])])

    def sample(_db):
        if isinstance(active, Exception):
            raise active
        return load_limits.load_snapshot(None if active is None else {}, None)

    monkeypatch.setattr(diag_running, "sample_load", sample)
    assert main([*argv, "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["warnings"] == ([f"active now {active}"] if active == 0 else [])


@pytest.mark.parametrize("second_workspace", [".", "docs"])
def test_dry_run_shows_writer_overlap_and_preserves_rejection(dry_run, capsys, second_workspace):
    argv = dry_run([_task("agy-writer", "agy", workspace=".", writable=False),
                    _task("explicit-writer", workspace=second_workspace, writable=True)])
    assert main([*argv, "--dry-run", "--json"]) == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["error"]["code"] == "invalid_manifest"
    assert "workspace overlap" in report["error"]["message"]
    (warning,) = report["warnings"]
    assert all(label in warning for label in ("agy-writer", "explicit-writer", "workspaces"))

    assert main([*argv, "--dry-run"]) == 1
    assert warning in capsys.readouterr().err
    assert main([*argv, "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == report["error"]


def test_dry_run_preserves_writer_reader_rejection(dry_run, capsys):
    argv = dry_run([_task("writer", writable=True), _task("reader")])
    assert main([*argv, "--dry-run", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert "workspace overlap" in report["error"]["message"]
    assert report["warnings"] == []


def test_final_text_summarizes_existing_task_rows():
    report = {"batch_id": "facts", "max_parallel": batch.RECOMMENDED_MAX_PARALLEL, "tasks": [
        {"task_id": task_id, "peer": "cc", "status": status}
        for task_id, status in [("a", "COMPLETED"), ("b", "COMPLETED"), ("c", "UNCERTAIN")]
    ]}
    assert batch.render_text(report).splitlines()[-1] == "outcomes: ok 2, uncertain 1"


def test_final_summary_uses_refusal_reasons_and_unverified_status():
    cases = [
        ("delivered", "TERMINAL", "", None),
        ("not_executed", "STARTED", None, "usage_limit"),
        ("not_executed", "STARTED", None, "auth_failure"),
    ]
    rows = []
    for i, (status, certainty, response, reason) in enumerate(cases):
        result = json.dumps(dict(status=status, certainty=certainty, response=response, reason=reason))
        worker_status, worker_reason = batch.classify_worker(int(response is None), result)
        rows.append(dict(task_id=f"t{i}", peer="cc", status=worker_status, reason=worker_reason))
    report = dict(batch_id="facts", max_parallel=batch.RECOMMENDED_MAX_PARALLEL, tasks=rows)
    assert batch.render_text(report).splitlines()[-1] == "outcomes: failed 1, uncertain 1, quota_refused 1"


def test_final_cli_counts_each_outcome_and_runs_over_limit(tmp_path, monkeypatch, capsys):
    def unexpected_sample(_db):
        pytest.fail("the execution path does not need a load sample")

    monkeypatch.setattr(diag_running, "sample_load", unexpected_sample)
    monkeypatch.setattr(batch, "default_argv_builder", builder(tmp_path))
    modes = {"notstarted": "FAILED", "empty": "UNVERIFIED", "uncertain": "UNCERTAIN",
             "claimheld": "BLOCKED", "refused": "FAILED"}
    ok_count = max(load_limits.ACTIVE_ASKS_CEILING + 1 - len(modes), 1)
    rows = [_task(f"ok-{i}") for i in range(ok_count)]
    rows.extend(_task(mode, prompt=f"mode:{mode}") for mode in modes)
    manifest = write(tmp_path, {"tasks": rows})
    assert main([*_argv(tmp_path / "x.db", manifest), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert {row["task_id"]: row["status"] for row in report["tasks"]} == {
        **{f"ok-{i}": "COMPLETED" for i in range(ok_count)}, **modes,
    }
    assert report["outcomes"] == {"ok": ok_count, "failed": 2, "uncertain": 2, "quota_refused": 1}


def test_batch_help_shows_one_task_with_every_validator_key(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["batch", "run", "--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "exactly one of" in help_text
    shape, _ = json.JSONDecoder().raw_decode(help_text[help_text.index("{"):])
    (row,) = shape["tasks"]
    assert set(row) == set(batch._KEYS)


def test_batch_help_keeps_the_parser_available_with_extensions_disabled():
    result = subprocess.run(
        [sys.executable, "-m", "tests.communication.harness.core_only", "--db", "batch-facts-missing.db", "diag", "health"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 5, result.stderr
    assert "LOADED_EXTENSIONS:[]" in result.stderr
