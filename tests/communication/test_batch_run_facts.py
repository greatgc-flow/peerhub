"""Batch reports expose load and outcome facts without dispatching providers."""

import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from peerhub.cli import suggest
from peerhub.cli.app import main
from peerhub.core import load_limits, migrations
from peerhub.extensions import ask, batch, bridge_schema, diag_running
from peerhub.extensions.bridge_control import MAY_HAVE_STARTED, NOT_STARTED, STARTED, TERMINAL
from tests.communication.test_batch_run import builder, write


def _task(task_id, peer="cc", **fields):
    return {"task_id": task_id, "peer": peer, "prompt": "mode:ok", **fields}


def _argv(db, manifest, max_parallel=1):
    return ["--db", str(db), "batch", "run", str(manifest), "--batch-id", "facts",
            "--max-parallel", str(max_parallel)]


@pytest.fixture
def dry_run(monkeypatch):
    db = Path.cwd() / "batch-facts-missing.db"
    manifest = Path("batch-facts.json")
    monkeypatch.setattr(diag_running, "sample_load", lambda _db: load_limits.load_snapshot(None, load_limits.ResourceSample()))

    def prepare(tasks, max_parallel=1):
        def read_manifest(path, **kwargs):
            assert path == manifest
            return json.dumps({"tasks": tasks})

        def fail_to_launch(*args, **kwargs):
            pytest.fail("manifest validation must finish before launching workers")

        monkeypatch.setattr(Path, "read_text", read_manifest)
        monkeypatch.setattr(batch, "run_batch", fail_to_launch)
        return _argv(db, manifest, max_parallel)

    return prepare


def test_dry_run_reports_large_batches_and_active_counts_without_warnings(dry_run, monkeypatch, capsys):
    count = diag_running.MAX_ROWS ** 2
    argv = dry_run([_task(f"t{i}") for i in range(count)], count)
    resources = load_limits.ResourceSample(load_limits.MIN_FREE_RAM_GB * load_limits._GB,
                                           load_limits.CPU_BUSY_WARN_PCT - 1, os.cpu_count())
    sample = load_limits.load_snapshot({"cc": count}, resources)
    monkeypatch.setattr(diag_running, "sample_load", lambda _db: sample)
    assert main([*argv, "--dry-run", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["max_parallel"] == len(report["tasks"]) == count
    assert report["load"] == sample and report["warnings"] == []
    assert main([*argv, "--dry-run"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith("TASK PEER")
    assert [line.split()[0] for line in lines[2:2 + count]] == [row["task_id"] for row in report["tasks"]]
    assert lines[2 + count:] == [load_limits.format_load(sample)]


def test_dry_run_displays_the_same_ownership_split_as_plan(dry_run, monkeypatch, capsys):
    argv = dry_run([_task("a", "cx")])
    counts = dict.fromkeys(load_limits.CALLER_RELATIONS, 1)
    sample = load_limits.load_snapshot({"cx": sum(counts.values())}, load_limits.ResourceSample(),
                                       active_by_relation=counts)
    monkeypatch.setattr(diag_running, "sample_load", lambda _db: sample)
    assert main([*argv, "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["load"]["active_by_relation"] == counts
    assert main([*argv, "--dry-run"]) == 0
    assert load_limits.format_load(sample) in capsys.readouterr().out


@pytest.mark.parametrize("active", [None, 0, OSError("unreadable load")])
def test_dry_run_distinguishes_zero_from_unavailable_load(dry_run, monkeypatch, capsys, active):
    argv = dry_run([_task("sample")])

    def sample(_db):
        if isinstance(active, Exception):
            raise active
        return load_limits.load_snapshot(None if active is None else {}, load_limits.ResourceSample())

    monkeypatch.setattr(diag_running, "sample_load", sample)
    assert main([*argv, "--dry-run", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["warnings"] == []
    assert report["load"]["active"] == (0 if active == 0 else None)
    assert main([*argv, "--dry-run"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == load_limits.format_load(report["load"])


def test_dry_run_reports_replay_counts_from_the_selected_store(dry_run, monkeypatch, capsys):
    dangling = "dangling-invocation"
    cases = {
        "settled": (TERMINAL, True),
        "settled-refusal": (STARTED, True),
        "uncertain": (MAY_HAVE_STARTED, False),
        "pending": (NOT_STARTED, False),
        dangling: (NOT_STARTED, False),
        "new": None,
    }
    argv = dry_run([_task(task_id) for task_id in cases])
    batch_id = argv[argv.index("--batch-id") + 1]
    connect = sqlite3.connect
    memory = "file:batch-replay-facts?mode=memory&cache=shared"
    with closing(connect(memory, uri=True)) as store:
        migrations.get_migration_registry()[0].apply(store)
        for statement in bridge_schema._DDL:
            if statement.startswith("CREATE TABLE"):
                store.execute(statement)
        for task_id, state in cases.items():
            if state is None:
                continue
            stream, request = batch.derive_ids(batch_id, task_id)
            store.execute("INSERT INTO records (record_id, stream_id, position, author_peer_id, kind, targets_json, "
                          "idempotency_key, payload_digest, created_at, appended_at) "
                          "VALUES (?, ?, 1, ?, 'prompt', ?, ?, '', '', '')",
                          (task_id, stream, ask._DEFAULT_AUTHOR, json.dumps(["cc"]), f"{ask._ASK_KEY_PREFIX}{request}"))
            certainty, acked = state
            store.execute("INSERT INTO bridge_deliveries (delivery_id, seq, stream_id, peer_id, record_id, "
                          "record_position, attempt, certainty, claim_generation, result_json, acked) "
                          "VALUES (?, 1, ?, 'cc', ?, 1, 1, ?, 1, ?, ?)",
                          (task_id, stream, task_id, certainty, "{}" if acked else None, acked))
            if task_id == dangling:
                store.execute("INSERT INTO bridge_invocations (delivery_id, invocation_no, stream_id, peer_id, "
                              "claim_generation) VALUES (?, 1, ?, 'cc', 1)", (task_id, stream))
        store.commit()

        def open_read_only(database, *, uri, **kwargs):
            assert database == Path(argv[1]).as_uri() + "?mode=ro"
            assert uri
            conn = connect(memory, uri=True)
            conn.execute("PRAGMA query_only = ON")
            return conn

        monkeypatch.setattr(sqlite3, "connect", open_read_only)
        assert main([*argv, "--dry-run", "--json"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["replay"] == {"settled": 2, "uncertain": 2}

        assert main([*argv, "--dry-run"]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines[2 + len(cases):] == [batch.REPLAY_LINE.format(**report["replay"]), load_limits.format_load(report["load"]), *report["warnings"]]

        argv[argv.index("--batch-id") + 1] = "unseen"
        assert main([*argv, "--dry-run", "--json"]) == 0
        fresh = json.loads(capsys.readouterr().out)
        assert "replay" not in fresh
        assert main([*argv, "--dry-run"]) == 0
        assert capsys.readouterr().out.splitlines()[2 + len(cases):] == [load_limits.format_load(fresh["load"]), *fresh["warnings"]]


def test_batch_docs_list_all_dry_run_warning_kinds():
    docs = Path(__file__).resolve().parents[2] / "docs" / "batch.md"
    paragraph = next(part for part in docs.read_text(encoding="utf-8").split("\n\n")
                     if part.startswith("Dry-run warnings"))
    documented = {kind for kind in batch._WARNING_MESSAGES if f"`{kind}`" in paragraph}
    assert documented == set(batch._WARNING_MESSAGES)
    assert f"`{batch.REPLAY_LINE.format(settled='N', uncertain='M')}`" in paragraph


@pytest.mark.parametrize("peer", ["ag", "agy"])
@pytest.mark.parametrize("second_workspace", [".", "docs"])
def test_dry_run_shows_writer_overlap_and_preserves_rejection(dry_run, capsys, second_workspace, peer):
    argv = dry_run([_task("agy-writer", peer, workspace=".", writable=False),
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


def test_dry_run_shows_manifest_profile_and_resolved_workspaces(dry_run, capsys):
    profile = batch._TASK_FIELDS["profile"]
    workspace = Path("docs") / ".." / "tests"
    argv = dry_run([_task("configured", profile=profile, workspace=str(workspace)), _task("defaults")])
    assert main([*argv, "--dry-run", "--json"]) == 0
    configured, defaults = json.loads(capsys.readouterr().out)["tasks"]
    assert (configured["profile"], configured["workspace"]) == (profile, os.path.normcase(str(workspace.resolve())))
    assert (defaults["profile"], defaults["workspace"]) == (None, os.path.normcase(str(Path.cwd().resolve())))
    assert configured["profile_display"] == profile
    assert defaults["profile_display"] == batch.DEFAULT_PROFILE_DISPLAY

    assert main([*argv, "--dry-run"]) == 0
    _, headings, configured_line, defaults_line, *_ = capsys.readouterr().out.splitlines()
    assert headings.split()[-2:] == ["PROFILE", "WORKSPACE"]
    assert configured_line.endswith(f" {profile} {configured['workspace']}")
    assert defaults_line.endswith(f" {defaults['profile_display']} {defaults['workspace']}")


def test_dry_run_displays_the_named_profile_resolved_by_ask(dry_run, monkeypatch, capsys):
    workspace = Path("docs").resolve()
    peer = "codex"

    class TargetWithDefault(ask.CliRuntimeTarget):
        def __init__(self, kind, task_workspace, **options):
            if Path(task_workspace).resolve() == workspace and options.get("profile") is None:
                options["profile"] = "effort"
            super().__init__(kind, task_workspace, **options)

    monkeypatch.setattr(ask, "CliRuntimeTarget", TargetWithDefault)
    argv = dry_run([_task("resolved", peer, workspace=str(workspace)), _task("unavailable", "oa")])
    assert main([*argv[:2], "ask", peer, "preview", "--workspace", str(workspace), "--dry-run", "--json"]) == 0
    resolved = json.loads(capsys.readouterr().out)["profile"]
    assert resolved

    assert main([*argv, "--dry-run", "--json"]) == 0
    named, unavailable = json.loads(capsys.readouterr().out)["tasks"]
    assert named["profile"] is unavailable["profile"] is None
    assert named["profile_display"] == batch.RESOLVED_DEFAULT_PROFILE_DISPLAY.format(profile=resolved)
    assert unavailable["profile_display"] == batch.DEFAULT_PROFILE_DISPLAY

    assert main([*argv, "--dry-run"]) == 0
    assert capsys.readouterr().out.splitlines()[2].endswith(f" {named['profile_display']} {named['workspace']}")


@pytest.mark.parametrize(("ram_delta", "cpu_delta", "expected"), [
    (-1, -1, ["free_ram"]),
    (0, 0, ["cpu_busy"]),
    (1, 1, ["cpu_busy"]),
    (-1, 1, ["free_ram", "cpu_busy"]),
    (0, -1, []),
    (None, None, []),
])
def test_dry_run_warns_at_resource_thresholds(dry_run, monkeypatch, capsys, ram_delta, cpu_delta, expected):
    ram = None if ram_delta is None else (load_limits.MIN_FREE_RAM_GB + ram_delta) * load_limits._GB
    cpu = None if cpu_delta is None else load_limits.CPU_BUSY_WARN_PCT + cpu_delta
    sample = load_limits.load_snapshot(None, load_limits.ResourceSample(ram, cpu))
    monkeypatch.setattr(diag_running, "sample_load", lambda _db: sample)
    argv = dry_run([_task("resources")])

    assert main([*argv, "--dry-run", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["load"] == sample
    messages = {
        "free_ram": f"below minimum {load_limits.MIN_FREE_RAM_GB} GB",
        "cpu_busy": f"at or above warning level {load_limits.CPU_BUSY_WARN_PCT}%",
    }
    assert len(report["warnings"]) == len(expected)
    assert all(messages[kind] in warning for kind, warning in zip(expected, report["warnings"]))


@pytest.fixture
def no_scratch(monkeypatch):
    monkeypatch.setattr(batch, "store_scratch_dir", lambda _db: Mock(spec=Path))
    monkeypatch.setattr(batch, "tempfile", SimpleNamespace(TemporaryDirectory=lambda **_: nullcontext(".")))
    monkeypatch.setattr(Path, "write_text", lambda *_args, **_kwargs: None)


def test_runner_waits_on_both_resources_before_launch_and_leaves_running_work_alone(no_scratch, monkeypatch, capsys):
    started = threading.Event()
    finish = threading.Event()
    exited = threading.Event()
    launches = []
    sample = load_limits.ResourceSample
    min_ram = load_limits.MIN_FREE_RAM_GB * load_limits._GB
    warn = load_limits.CPU_BUSY_WARN_PCT
    reply = b'{"status": "delivered", "certainty": "TERMINAL", "response": "reply"}'

    def spawn(argv, **kwargs):
        task_id, = argv
        launches.append(task_id)

        def poll():
            if task_id == "first":
                if not finish.is_set():
                    return None
                exited.set()
            return 0

        proc = SimpleNamespace(poll=poll, returncode=0, stdout=io.BytesIO(reply), stderr=io.BytesIO())
        started.set()
        return proc

    reads = iter(range(4))

    def read():
        step = next(reads)
        if step == 0:
            return sample(min_ram, warn - 1)
        assert started.wait(5) and launches == ["first"]
        if step == 1:
            return sample(min_ram - 1, warn - 1)
        if step == 2:
            assert not exited.is_set()
            return sample(min_ram, warn)
        finish.set()
        assert exited.wait(5)
        return sample(min_ram, warn - 1)

    monkeypatch.setattr(batch.subprocess, "Popen", spawn)
    monkeypatch.setattr(batch, "RESOURCE_POLL_SECONDS", 0)
    tasks = [batch.Task(name, "cx", "prompt", None, None, False, batch.DEFAULT_TIMEOUT_S)
             for name in ("first", "second")]
    report = batch.run_batch(tasks, batch_id="facts", max_parallel=len(tasks), db="x.db",
                             argv_builder=lambda task, **_: [task.task_id], resource_reader=read)
    assert launches == [task.task_id for task in tasks]
    assert [row["status"] for row in report["tasks"]] == ["COMPLETED"] * len(tasks)
    output = capsys.readouterr()
    assert not output.out
    assert len(output.err.splitlines()) == 1 and "waiting for machine resources" in output.err


def test_runner_cancel_interrupts_resource_wait_without_launch(no_scratch, monkeypatch):
    cancel = threading.Event()
    launches = []
    reply = b'{"status": "delivered", "certainty": "TERMINAL", "response": "reply"}'

    def spawn(argv, **kwargs):
        launches.append(argv[0])
        return SimpleNamespace(poll=lambda: 0 if cancel.is_set() else None, returncode=0,
                               stdout=io.BytesIO(reply), stderr=io.BytesIO())

    def cancel_wait(timeout):
        assert timeout == batch.RESOURCE_POLL_SECONDS
        cancel.set()
        return True

    readings = iter([load_limits.ResourceSample()])
    monkeypatch.setattr(batch.subprocess, "Popen", spawn)
    monkeypatch.setattr(cancel, "wait", cancel_wait)
    tasks = [batch.Task(name, "cx", "prompt", None, None, False, batch.DEFAULT_TIMEOUT_S) for name in ("running", "held")]
    report = batch.run_batch(tasks, batch_id="facts", max_parallel=len(tasks), db="x.db", cancel=cancel,
                             argv_builder=lambda task, **_: [task.task_id],
                             resource_reader=lambda: next(readings, load_limits.ResourceSample(
                                 cpu_busy_pct=load_limits.CPU_BUSY_WARN_PCT)))
    assert launches == ["running"]
    assert {row["task_id"]: row.get("reason") for row in report["tasks"]}["held"] == "cancelled"


def test_runner_starts_with_a_warning_when_no_ask_is_running(no_scratch, monkeypatch, capsys):
    reply = b'{"status": "delivered", "certainty": "TERMINAL", "response": "reply"}'
    monkeypatch.setattr(batch.subprocess, "Popen", lambda argv, **_: SimpleNamespace(
        poll=lambda: 0, returncode=0, stdout=io.BytesIO(reply), stderr=io.BytesIO()))
    task = batch.Task("only", "cx", "prompt", None, None, False, batch.DEFAULT_TIMEOUT_S)
    report = batch.run_batch([task], batch_id="facts", max_parallel=1, db="x.db",
                             argv_builder=lambda task, **_: [task.task_id],
                             resource_reader=lambda: load_limits.ResourceSample(cpu_busy_pct=load_limits.CPU_BUSY_WARN_PCT))
    assert report["tasks"][0]["status"] == "COMPLETED"
    assert "starting with no ask running" in capsys.readouterr().err


def test_final_text_summarizes_existing_task_rows():
    report = {"batch_id": "facts", "max_parallel": 1, "tasks": [
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
    report = dict(batch_id="facts", max_parallel=1, tasks=rows)
    assert batch.render_text(report).splitlines()[-1] == "outcomes: failed 1, uncertain 1, quota_refused 1"


def test_final_cli_counts_each_outcome(tmp_path, monkeypatch, capsys):
    def unexpected_sample(_db):
        pytest.fail("the execution path does not need a load sample")

    monkeypatch.setattr(diag_running, "sample_load", unexpected_sample)
    monkeypatch.setattr(batch, "default_argv_builder", builder(tmp_path))
    modes = {"notstarted": "FAILED", "empty": "UNVERIFIED", "uncertain": "UNCERTAIN",
             "claimheld": "BLOCKED", "refused": "FAILED"}
    monkeypatch.setattr(batch, "read_resources", load_limits.ResourceSample)
    ok_count = len(modes)
    rows = [_task(f"ok-{i}") for i in range(ok_count)]
    rows.extend(_task(mode, prompt=f"mode:{mode}") for mode in modes)
    manifest = write(tmp_path, {"tasks": rows})
    assert main([*_argv(tmp_path / "x.db", manifest), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert {row["task_id"]: row["status"] for row in report["tasks"]} == {
        **{f"ok-{i}": "COMPLETED" for i in range(ok_count)}, **modes,
    }
    assert report["outcomes"] == {"ok": ok_count, "failed": 2, "uncertain": 2, "quota_refused": 1}


def test_final_report_exposes_task_diagnostics_and_child_explain(no_scratch):
    explain = "peerhub explain ask_blocked_uncertain"
    results = {
        "ok": {"status": "delivered", "certainty": "TERMINAL", "response": "reply"},
        "failed": {"certainty": "NOT_STARTED"},
        "uncertain": {"status": "uncertain", "guidance": {"explain_cmd": explain}},
        "blocked": {"status": "pending_earlier_record"},
        "unverified": {"status": "denied_empty"},
    }

    def build(t, **_kwargs):
        if t.task_id == "setup-error":
            raise ValueError("worker setup failed")
        return [sys.executable, "-c", "import sys; print(sys.argv[1])", json.dumps(results[t.task_id])]

    tasks = [batch.Task(task_id, "cx", "prompt", None, None, False, batch.DEFAULT_TIMEOUT_S)
             for task_id in [*results, "setup-error"]]
    db = Path("lane e") / "peer's store.db"
    report = batch.run_batch(tasks, batch_id="facts", max_parallel=1,
                             db=str(db), argv_builder=build, resource_reader=load_limits.ResourceSample)
    lines = batch.render_text(report).splitlines()
    assert batch.exit_code(report) == 1
    assert "next" not in report["tasks"][0]
    for row in report["tasks"][1:]:
        stream, request = batch.derive_ids(report["batch_id"], row["task_id"])
        assert (row["stream"], row["request_id"]) == (stream, request)
        prefix = ["peerhub", "--db", str(db.absolute()), "diag"]
        commands = [suggest._shell_command([*prefix, "running", "--peer", row["peer"]]),
                    suggest._shell_command([*prefix, "health", "--stream", stream])]
        if row["task_id"] == "uncertain":
            commands.append(explain)
        assert row["next"] == commands
        index = next(i for i, line in enumerate(lines) if line.startswith(row["task_id"] + " "))
        assert lines[index + 1].startswith("    next: ")
        assert all(part in lines[index + 1] for part in [*commands, request])


def test_batch_help_shows_a_valid_manifest_and_prompt_alternative(capsys, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        main(["batch", "run", "--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    example = help_text[help_text.index("{"):]
    shape, end = json.JSONDecoder().raw_decode(example)
    (row,) = shape["tasks"]
    monkeypatch.setattr(Path, "read_text", lambda _path, **_kwargs: example[:end])
    tasks = batch.load_manifest("example.json", batch_id="help")
    batch.validate_tasks(tasks, kind_of=lambda peer: peer)
    assert set(row) == set(batch._KEYS) - {"prompt_file"}
    assert "or prompt_file instead of prompt" in help_text.splitlines()


def test_batch_help_lists_warning_kinds_and_full_replies(capsys):
    with pytest.raises(SystemExit):
        main(["batch", "run", "--help"])
    lines = capsys.readouterr().out.splitlines()
    warning_line = next(line for line in lines if line.startswith("Dry-run warning kinds:"))
    warning_kinds = set(warning_line.split(": ", 1)[1].split(", "))
    assert warning_kinds == set(batch._WARNING_MESSAGES)
    assert {"cpu_busy", "free_ram"} <= warning_kinds
    assert any("--batch-id" in line and "replays settled tasks" in line and "reconcile uncertain rows first" in line.lower()
               for line in lines)
    assert any("--responses" in line and "in full" in line and "large" in line for line in lines)


def test_batch_help_keeps_the_parser_available_with_extensions_disabled():
    result = subprocess.run(
        [sys.executable, "-m", "tests.communication.harness.core_only", "--db", "batch-facts-missing.db", "diag", "health"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 5, result.stderr
    assert "LOADED_EXTENSIONS:[]" in result.stderr
