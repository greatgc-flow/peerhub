"""`peerhub batch run`: flat bounded-parallel asks (spec: batch B1-B10). Workers are fakes; no provider is called."""
import errno
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest

from peerhub.extensions import batch
from peerhub.extensions.batch import ManifestError, Task

FAKE = str(Path(__file__).resolve().parent / "harness" / "fake_worker.py")
KINDS = {"cc": "cc", "cx": "cx", "ag": "ag", "claude": "cc"}


def task(tid="t1", peer="cc", mode="ok", ws=None, writable=False, **kw):
    return Task(task_id=tid, peer=peer, prompt=f"mode:{mode}\n", workspace=ws, profile=None, writable=writable,
                timeout_s=kw.pop("timeout_s", 30.0))


def builder(outdir):
    def build(t, *, stream, request_id, db, query_file):
        return [sys.executable, FAKE, str(query_file), str(outdir), t.task_id]
    return build


def run(tmp_path, tasks, n=2, **kw):
    return batch.run_batch(tasks, batch_id="b1", max_parallel=n, db=str(tmp_path / "x.db"),
                           argv_builder=builder(tmp_path), **kw)


def status_of(report):
    return {r["task_id"]: r["status"] for r in report["tasks"]}


def events(tmp_path):
    rows = [ln.split() for p in tmp_path.glob("events-*.log") for ln in p.read_text().splitlines()]
    return sorted(((k, t, float(ts)) for k, t, ts in rows), key=lambda e: e[2])


def alive(pid):
    if sys.platform == "win32":
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True).stdout
        return str(pid).encode() in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---- ids -------------------------------------------------------------------------------------------------------------
def test_ids_are_stable_prefixed_and_collision_free():
    s1, r1 = batch.derive_ids("a-b", "c")
    s2, r2 = batch.derive_ids("a", "b-c")
    assert (s1, r1) == batch.derive_ids("a-b", "c")
    assert s1 != s2 and r1 != r2
    assert re.fullmatch(r"batch-[0-9a-f]{20}", s1) and re.fullmatch(r"b-[0-9a-f]{20}", r1)
    assert s1[len("batch-"):] != r1[len("b-"):]  # stream and request digests are domain-separated


@pytest.mark.parametrize("bad", ["", "a b", "a/b", "x" * 65, "a\0b", "é"])
def test_ids_reject_bad_charset(bad):
    with pytest.raises(ManifestError):
        batch.derive_ids(bad, "t")
    with pytest.raises(ManifestError):
        batch.derive_ids("b", bad)


# ---- manifest --------------------------------------------------------------------------------------------------------
def write(tmp_path, doc):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_manifest_loads_prompt_and_prompt_file(tmp_path):
    (tmp_path / "p.txt").write_text("héllo", encoding="utf-8")
    m = write(tmp_path, {"tasks": [{"task_id": "a", "peer": "cc", "prompt": "hi"},
                                   {"task_id": "b", "peer": "cx", "prompt_file": "p.txt", "profile": "cx.standard",
                                    "timeout_s": 12, "writable": True, "workspace": str(tmp_path)}]})
    a, b = batch.load_manifest(m, batch_id="b1")
    assert (a.prompt, a.writable, a.timeout_s) == ("hi", False, batch.DEFAULT_TIMEOUT_S)
    assert (b.prompt, b.profile, b.writable, b.timeout_s, b.workspace) == ("héllo", "cx.standard", True, 12.0, tmp_path)


@pytest.mark.parametrize("doc", [
    {}, {"tasks": []}, {"tasks": "x"}, {"tasks": [{"peer": "cc", "prompt": "x"}]},
    {"tasks": [{"task_id": "a", "peer": "cc"}]},
    {"tasks": [{"task_id": "a", "peer": "cc", "prompt": "x", "prompt_file": "y"}]},
    {"tasks": [{"task_id": "a", "peer": "cc", "prompt": "  "}]},
    {"tasks": [{"task_id": "a", "peer": "cc", "prompt": "x", "stream": "s"}]},       # stream field removed (B2')
    {"tasks": [{"task_id": "a", "peer": "cc", "prompt": "x", "bogus": 1}]},
    {"tasks": [{"task_id": "a", "peer": "cc", "prompt": "x"}, {"task_id": "a", "peer": "cx", "prompt": "y"}]},
    {"tasks": [{"task_id": "a", "peer": "cc", "prompt": "x", "timeout_s": 0}]},
    {"tasks": [{"task_id": "a", "peer": "cc", "prompt": "x", "timeout_s": float("inf")}]},
    {"tasks": [{"task_id": "a", "peer": "cc", "prompt": "x", "timeout_s": True}]},
    {"tasks": [{"task_id": "a", "peer": "cc", "prompt": "x", "writable": "yes"}]},
    {"tasks": [{"task_id": "a b", "peer": "cc", "prompt": "x"}]},
    {"tasks": [{"task_id": f"t{i}", "peer": "cc", "prompt": "x"} for i in range(batch.MAX_TASKS + 1)]},
])
def test_manifest_rejects_invalid(tmp_path, doc):
    with pytest.raises(ManifestError):
        batch.load_manifest(write(tmp_path, doc), batch_id="b1")


def test_manifest_rejects_missing_prompt_file_and_bad_json(tmp_path):
    with pytest.raises(ManifestError):
        batch.load_manifest(write(tmp_path, {"tasks": [{"task_id": "a", "peer": "cc", "prompt_file": "nope.txt"}]}), batch_id="b1")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError):
        batch.load_manifest(bad, batch_id="b1")
    with pytest.raises(ManifestError):
        batch.load_manifest(tmp_path / "missing.json", batch_id="b1")


@pytest.mark.parametrize("n", [0, -1, 17, True, 1.5, float("nan"), "2"])
def test_max_parallel_bounds(n):
    with pytest.raises(ManifestError):
        batch.check_max_parallel(n)


def test_max_parallel_accepts_1_to_16():
    assert [batch.check_max_parallel(n) for n in (1, 2, 16)] == [1, 2, 16]


# ---- validation: peers and workspace overlap -------------------------------------------------------------------------
def test_validate_rejects_unknown_peer(tmp_path):
    with pytest.raises(ManifestError, match="peer"):
        batch.validate_tasks([task(peer="nobody", ws=tmp_path)], kind_of=KINDS.get)


def test_validate_requires_existing_workspace(tmp_path):
    with pytest.raises(ManifestError, match="workspace"):
        batch.validate_tasks([task(ws=tmp_path / "missing")], kind_of=KINDS.get)


def mk(tmp_path, *names):
    paths = []
    for n in names:
        p = tmp_path / n
        p.mkdir(parents=True, exist_ok=True)
        paths.append(p)
    return paths


def test_overlap_two_readers_ok_writer_reader_rejected(tmp_path):
    (w,) = mk(tmp_path, "w")
    batch.validate_tasks([task("a", ws=w), task("b", ws=w)], kind_of=KINDS.get)
    with pytest.raises(ManifestError, match="overlap"):
        batch.validate_tasks([task("a", ws=w, writable=True), task("b", ws=w)], kind_of=KINDS.get)


def test_overlap_ancestor_descendant_and_siblings(tmp_path):
    parent, child, sib = mk(tmp_path, "p", "p/c", "s")
    with pytest.raises(ManifestError, match="overlap"):
        batch.validate_tasks([task("a", ws=parent, writable=True), task("b", ws=child, writable=True)], kind_of=KINDS.get)
    batch.validate_tasks([task("a", ws=child, writable=True), task("b", ws=sib, writable=True)], kind_of=KINDS.get)


def test_overlap_ag_counts_as_writer_even_if_not_flagged(tmp_path):
    (w,) = mk(tmp_path, "w")
    with pytest.raises(ManifestError, match="overlap"):
        batch.validate_tasks([task("a", peer="ag", ws=w), task("b", ws=w)], kind_of=KINDS.get)


@pytest.mark.skipif(sys.platform != "win32", reason="case-insensitive paths")
def test_overlap_casefold_on_windows(tmp_path):
    (w,) = mk(tmp_path, "Work")
    with pytest.raises(ManifestError, match="overlap"):
        batch.validate_tasks([task("a", ws=w, writable=True), task("b", ws=Path(str(w).upper()), writable=True)], kind_of=KINDS.get)


def test_overlap_default_workspace_is_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ManifestError, match="overlap"):
        batch.validate_tasks([task("a", writable=True), task("b", writable=True)], kind_of=KINDS.get)


# ---- classification (default UNCERTAIN) ------------------------------------------------------------------------------
def j(**kw):
    return json.dumps(kw)


OK = j(status="delivered", certainty="TERMINAL", response="pong", error=None)


@pytest.mark.parametrize("rc,out,kw,expect", [
    (0, OK, {}, ("COMPLETED", None)),
    (0, j(status="recovered_terminal", certainty="TERMINAL", response="x", error=None), {}, ("COMPLETED", None)),
    (0, j(status="delivered", certainty="TERMINAL", response="", error=None), {}, ("UNVERIFIED", "empty_response")),
    (0, j(status="delivered", certainty="TERMINAL", response="  \n", error=None), {}, ("UNVERIFIED", "empty_response")),
    (1, j(status="failed_not_started", certainty="NOT_STARTED", response=None, error={"code": "delivery_failed"}), {}, ("FAILED", "not_started")),
    (1, j(status="not_executed", certainty="STARTED", task_outcome="NOT_EXECUTED", reason="quota_exhausted", error={"code": "x"}), {}, ("FAILED", "not_executed:quota_exhausted")),
    (1, j(status="uncertain", certainty="STARTED", response=None, error={"code": "delivery_failed"}), {}, ("UNCERTAIN", "uncertain")),
    (1, j(status="error", response=None, error={"code": "delivery_failed"}, guidance={"code": "ask_claim_held"}), {}, ("BLOCKED", "ask_claim_held")),
    (1, j(status="pending_earlier_record", certainty="STARTED", response=None, error={"code": "x"}), {}, ("BLOCKED", "pending_earlier_record")),
    # NOT evidence of NOT_STARTED: whole-command ValueError / conflict / schema codes (spec B5''')
    (2, j(status="error", response=None, error={"code": "invalid_arguments", "message": "x"}), {}, ("UNCERTAIN", "invalid_arguments")),
    (2, j(status="error", response=None, error={"code": "idempotency_conflict", "message": "x"}), {}, ("UNCERTAIN", "idempotency_conflict")),
    (6, j(status="error", response=None, error={"code": "schema_version_error", "message": "x"}), {}, ("UNCERTAIN", "schema_version_error")),
    (3, "", {}, ("UNCERTAIN", "no_result")),
    (0, "\x00 garbage", {}, ("UNCERTAIN", "no_result")),
    (0, "[1,2]", {}, ("UNCERTAIN", "no_result")),
    (0, OK, {"overflow": True}, ("UNCERTAIN", "output_overflow")),
    (None, "", {"killed": True}, ("UNCERTAIN", "killed")),
    # delivered but exit non-zero / error present is not success
    (1, OK, {}, ("UNCERTAIN", "exit_1")),
    (0, j(status="delivered", certainty="TERMINAL", response="x", error={"code": "e"}), {}, ("UNCERTAIN", "error_present")),
    (0, j(status="delivered", certainty="STARTED", response="x", error=None), {}, ("UNCERTAIN", "certainty_STARTED")),
])
def test_classify_table(rc, out, kw, expect):
    assert batch.classify_worker(rc, out, **kw) == expect


def test_classify_uses_last_json_document_and_ignores_leading_noise():
    assert batch.classify_worker(0, "warn: x\n" + OK + "\n") == ("COMPLETED", None)


# ---- scheduler -------------------------------------------------------------------------------------------------------
def max_overlap(ev):
    cur = best = 0
    for kind, _t, _ts in sorted(ev, key=lambda e: (e[2], 0 if e[0] == "E" else 1)):
        cur += 1 if kind == "S" else -1
        best = max(best, cur)
    return best


def test_statuses_for_every_worker_mode(tmp_path):
    modes = {"ok": "COMPLETED", "empty": "UNVERIFIED", "notstarted": "FAILED", "refused": "FAILED", "uncertain": "UNCERTAIN",
             "claimheld": "BLOCKED", "valueerror": "UNCERTAIN", "crash": "UNCERTAIN", "garbage": "UNCERTAIN"}
    report = run(tmp_path, [task(f"t-{m}", mode=m) for m in modes], n=4)
    assert status_of(report) == {f"t-{m}": s for m, s in modes.items()}
    assert batch.exit_code(report) == 1


def test_all_completed_exit_zero_and_report_shape(tmp_path):
    report = run(tmp_path, [task("a"), task("b", peer="cx")], n=2)
    assert batch.exit_code(report) == 0
    assert set(report) >= {"batch_id", "max_parallel", "tasks"} and report["batch_id"] == "b1" and report["max_parallel"] == 2
    for row in report["tasks"]:
        assert set(row) >= {"task_id", "peer", "stream", "status", "request_id", "elapsed_s"}
        assert (row["stream"], row["request_id"]) == batch.derive_ids("b1", row["task_id"])
        assert "response" not in row  # responses are never included
    assert [r["task_id"] for r in report["tasks"]] == ["a", "b"]  # manifest order, not completion order


@pytest.mark.parametrize("fault,reason", [
    ("write", "query_write_failed:OSError"),
    ("argv", "worker_setup_error:ValueError"),
    ("spawn", "spawn_failed:FileNotFoundError"),
])
def test_setup_failure_emits_complete_json_and_continues(tmp_path, monkeypatch, capsys, fault, reason):
    from peerhub.cli.app import main

    manifest = write(tmp_path, {"tasks": [
        {"task_id": tid, "peer": "cx", "prompt": "mode:ok"} for tid in ("bad", "good")
    ]})
    real_write = Path.write_text
    real_popen = batch.subprocess.Popen
    build = builder(tmp_path)
    attempted = []

    def failing_write(path, *args, **kwargs):
        if path.name == "bad.txt":
            raise OSError(errno.ENOSPC, "injected full disk")
        return real_write(path, *args, **kwargs)

    def failing_builder(t, **kwargs):
        attempted.append(t.task_id)
        if fault == "argv" and t.task_id == "bad":
            raise ValueError("injected argv failure")
        return build(t, **kwargs)

    def failing_spawn(argv, **kwargs):
        if argv[-1] == "bad":
            raise OSError(errno.ENOENT, "injected spawn failure")
        return real_popen(argv, **kwargs)

    if fault == "write":
        monkeypatch.setattr(Path, "write_text", failing_write)
    if fault == "spawn":
        monkeypatch.setattr(batch.subprocess, "Popen", failing_spawn)
    monkeypatch.setattr(batch, "default_argv_builder", failing_builder)
    assert main(["--db", str(tmp_path / "x.db"), "batch", "run", str(manifest),
                 "--batch-id", "b1", "--max-parallel", "1", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [r["task_id"] for r in report["tasks"]] == ["bad", "good"]
    assert status_of(report) == {"bad": "FAILED", "good": "COMPLETED"}
    assert report["tasks"][0]["reason"] == reason
    assert attempted == (["good"] if fault == "write" else ["bad", "good"])
    assert [t for k, t, _ in events(tmp_path) if k == "S"] == ["good"]
    for row in report["tasks"]:
        assert (row["stream"], row["request_id"]) == batch.derive_ids("b1", row["task_id"])
        assert "elapsed_s" in row
    assert batch.exit_code(report) == 1


def test_exception_after_launch_cleans_up_and_continues(tmp_path, monkeypatch):
    real_start = threading.Thread.start
    real_popen = batch.subprocess.Popen
    processes = []
    failed = False

    def popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        if args[0][:2] == [sys.executable, FAKE]:
            processes.append(proc)
        return proc

    def start(thread):
        nonlocal failed
        if thread._target is batch._drain and not failed:
            failed = True
            raise RuntimeError("injected reader setup failure")
        return real_start(thread)

    monkeypatch.setattr(batch.subprocess, "Popen", popen)
    monkeypatch.setattr(threading.Thread, "start", start)
    report = run(tmp_path, [task("bad", mode="hang"), task("good")], n=1)
    assert [r["task_id"] for r in report["tasks"]] == ["bad", "good"]
    assert status_of(report) == {"bad": "UNCERTAIN", "good": "COMPLETED"}
    assert report["tasks"][0]["reason"] == "worker_error:RuntimeError"
    assert len(processes) == 2  # no retry
    assert processes[0].poll() is not None
    assert processes[0].stdout.closed and processes[0].stderr.closed
    assert batch.exit_code(report) == 1


def test_missing_worker_row_is_uncertain_in_manifest_order(tmp_path, monkeypatch):
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    monkeypatch.setattr(threading.Thread, "join", lambda self: None)
    report = run(tmp_path, [task("a"), task("b")])
    assert [r["task_id"] for r in report["tasks"]] == ["a", "b"]
    assert status_of(report) == {"a": "UNCERTAIN", "b": "UNCERTAIN"}
    assert all(r["reason"] == "missing_worker_result:KeyError" for r in report["tasks"])
    assert batch.exit_code(report) == 1


def test_never_exceeds_max_parallel_and_reaches_it(tmp_path):
    tasks = [task(f"t{i}", mode="slow") for i in range(6)]
    run(tmp_path, tasks, n=2)
    assert max_overlap(events(tmp_path)) == 2
    for p in tmp_path.glob("events-*.log"):
        p.unlink()
    run(tmp_path, tasks, n=1)
    assert max_overlap(events(tmp_path)) == 1


def test_fifo_start_order(tmp_path):
    run(tmp_path, [task(f"t{i}", mode="slow") for i in range(5)], n=1)
    starts = [t for k, t, _ in events(tmp_path) if k == "S"]
    assert starts == [f"t{i}" for i in range(5)]


def test_uncertain_is_never_retried(tmp_path):
    run(tmp_path, [task("u", mode="uncertain")], n=1)
    assert [k for k, t, _ in events(tmp_path) if t == "u"] == ["S", "E"]


def test_huge_stdout_is_capped_without_deadlock(tmp_path):
    report = run(tmp_path, [task("h", mode="huge")], n=1)
    assert status_of(report) == {"h": "UNCERTAIN"} and report["tasks"][0]["reason"] == "output_overflow"


def test_worker_timeout_is_killed_and_uncertain(tmp_path):
    report = run(tmp_path, [task("h", mode="hang", timeout_s=1)], n=1, grace_s=1.0)
    assert status_of(report) == {"h": "UNCERTAIN"}
    assert report["tasks"][0]["reason"] in ("timeout", "killed")
    assert not alive(int((tmp_path / "w-h.pid").read_text()))


def test_cancel_kills_started_and_blocks_unlaunched(tmp_path):
    cancel = threading.Event()
    tasks = [task(f"t{i}", mode="grandchild") for i in range(4)]
    box = {}
    th = threading.Thread(target=lambda: box.update(r=run(tmp_path, tasks, n=2, cancel=cancel)))
    th.start()
    deadline = time.time() + 20
    while time.time() < deadline and len(list(tmp_path.glob("gc-*.pid"))) < 2:
        time.sleep(0.05)
    assert len(list(tmp_path.glob("gc-*.pid"))) == 2
    cancel.set()
    th.join(30)
    assert not th.is_alive()
    st = status_of(box["r"])
    assert sorted(st.values()) == ["BLOCKED", "BLOCKED", "UNCERTAIN", "UNCERTAIN"]
    assert st["t0"] == st["t1"] == "UNCERTAIN"
    assert batch.exit_code(box["r"]) == 1
    for pid_file in list(tmp_path.glob("gc-*.pid")) + list(tmp_path.glob("w-*.pid")):  # whole tree is gone, grandchildren too
        assert not alive(int(pid_file.read_text()))


def test_kill_unconfirmed_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "kill_tree_confirmed", lambda proc, **kw: False)
    cancel = threading.Event()
    box = {}
    th = threading.Thread(target=lambda: box.update(r=run(tmp_path, [task("g", mode="hang")], n=1, cancel=cancel)))
    th.start()
    while not list(tmp_path.glob("w-*.pid")):
        time.sleep(0.05)
    cancel.set()
    th.join(30)
    row = box["r"]["tasks"][0]
    assert row["status"] == "UNCERTAIN" and row["reason"] == "kill_unconfirmed"
    os.kill(int((tmp_path / "w-g.pid").read_text()), 9) if sys.platform != "win32" else os.system(f"taskkill /F /T /PID {(tmp_path / 'w-g.pid').read_text()} >nul 2>&1")


def test_argv_builder_receives_derived_ids_and_prompt_file(tmp_path):
    seen = []

    def build(t, *, stream, request_id, db, query_file):
        seen.append((t.task_id, stream, request_id, db, Path(query_file).read_text(encoding="utf-8")))
        return [sys.executable, FAKE, str(query_file), str(tmp_path), t.task_id]

    batch.run_batch([task("a")], batch_id="b1", max_parallel=1, db="D:/some/abs.db", argv_builder=build)
    tid, stream, rid, db, text = seen[0]
    assert (stream, rid) == batch.derive_ids("b1", tid) and db == "D:/some/abs.db" and text == "mode:ok\n"


def test_default_argv_passes_absolute_db_and_ask_flags(tmp_path):
    t = Task("a", "cx", "p", tmp_path, "cx.standard", True, 90.0)
    argv = batch.default_argv_builder(t, stream="S", request_id="R", db=str(tmp_path / "x.db"), query_file=tmp_path / "q.txt")
    assert argv[:3] == [sys.executable, "-m", "peerhub"]
    assert argv[argv.index("--db") + 1] == str(tmp_path / "x.db") and os.path.isabs(argv[argv.index("--db") + 1])
    assert argv.index("--db") < argv.index("ask")  # global option precedes the subcommand
    for flag, val in (("--stream", "S"), ("--request-id", "R"), ("--profile", "cx.standard"), ("--query-file", str(tmp_path / "q.txt")),
                      ("-w", str(tmp_path)), ("--timeout-seconds", "90.0")):
        assert argv[argv.index(flag) + 1] == val
    assert "--writable" in argv and "--json" in argv and "cx" in argv


def test_default_argv_omits_optional_flags():
    argv = batch.default_argv_builder(Task("a", "cc", "p", None, None, False, 30.0), stream="S", request_id="R", db="D:/x.db", query_file=Path("q"))
    assert "--writable" not in argv and "--profile" not in argv and "-w" not in argv


def test_report_text_table_is_compact(tmp_path):
    report = run(tmp_path, [task("a")], n=1)
    text = batch.render_text(report)
    assert "a" in text and "COMPLETED" in text and len(text.splitlines()) <= 4
