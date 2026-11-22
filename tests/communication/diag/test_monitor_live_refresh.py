"""Offline coverage of the interactive quota scheduler, redraws and probe shutdown."""
from __future__ import annotations

import argparse
import subprocess
from contextlib import nullcontext
from pathlib import Path
from threading import Event, get_ident
from time import monotonic
from types import SimpleNamespace

import pytest

from peerhub.cli import monitor, monitor_view, support, view
from peerhub.cli.app import main
from peerhub.extensions import monitor_read_model, quota_probes
from tests.communication.diag.test_monitor_activity_view import pool
from tests.communication.diag.test_monitor_plain_golden import _payload, full_payload


def _reading(peer: str, pool: str, age: float) -> dict[str, object]:
    return {"kind": "quota", "subject_ref": peer, "resource_pool_ref": f"quota:{peer}:{pool}",
            "stored_state": "MEASURED", "age_seconds": age, "payload": {"remaining_fraction": .5}}


def _result(peer: str, pool: str) -> dict[str, str]:
    return {"kind": "quota", "subject_ref": peer, "resource_pool_ref": f"quota:{peer}:{pool}", "state": "MEASURED"}


def test_per_peer_due_at_lead_with_missing_and_partial_pools():
    from peerhub.cli.monitor_refresh import RefreshSchedule
    from peerhub.extensions.observation_model import QUOTA_TTL_SECONDS, FreshnessPolicy

    lead = support.MONITOR_REFRESH_LEAD_SECONDS
    due = QUOTA_TTL_SECONDS - lead
    assert FreshnessPolicy().ttl_for("quota") == QUOTA_TTL_SECONDS
    assert 0 < support.MONITOR_PROBE_MAX_TIMEOUT_SECONDS < lead < QUOTA_TTL_SECONDS
    configured = {"cc": {"quota:cc:C-7D", "quota:cc:C-5H"}, "cx": {"quota:cx:X-7D"}}
    schedule = RefreshSchedule(configured)
    now = 500.0
    readings = [_reading("cc", "C-7D", due - 1), _reading("cc", "C-5H", 5), _reading("cx", "X-7D", 20)]
    schedule.update(readings, now)
    assert schedule.oldest_age(now) == due - 1
    assert schedule.due(now) == ()
    assert schedule.due(now + 1) == ("cc",)
    assert schedule.next_in(now) == 1
    finish = now + 1 + lead / 2
    assert schedule.oldest_age(finish) < QUOTA_TTL_SECONDS
    schedule.update([_reading("cc", "C-7D", 0), _reading("cc", "C-5H", 0), _reading("cx", "X-7D", finish - now + 20)], finish)
    assert schedule.due(finish) == ()

    # A successful cx refresh cannot reset cc's backoff. The missing cc window is due immediately.
    schedule.update([_reading("cc", "C-7D", 5), _reading("cx", "X-7D", due)], finish)
    assert schedule.due(finish) == ("cc", "cx")
    schedule.update([_reading("cc", "C-7D", 0), _reading("cx", "X-7D", 0)], finish)
    schedule.completed(("cc", "cx"), {"status": "PARTIAL", "observations": [
        _result("cc", "C-7D"), _result("cx", "X-7D"),
    ]}, finish)
    cooldown = support.QUOTA_REFRESH_COOLDOWN_SECONDS
    assert schedule.next_in(finish) == cooldown
    assert schedule.due(finish + cooldown - 1) == ()
    assert schedule.due(finish + cooldown) == ("cc",)
    assert schedule.due(finish + due) == ("cc", "cx")

    finish += due + 1
    schedule.update([_reading("cc", "C-7D", 0), _reading("cc", "C-5H", 0), _reading("cx", "X-7D", 0)], finish)
    schedule.completed(("cc", "cx"), {"status": "PARTIAL", "observations": [
        {"subject_ref": "cc", "kind": "quota", "state": "ERROR"}, _result("cx", "X-7D"),
    ]}, finish)
    assert schedule.retry_in(finish) == cooldown
    assert schedule.due(finish + cooldown) == ("cc",)
    assert schedule.due(finish + cooldown + 1) == ("cc",)


def test_prior_fresh_window_does_not_make_partial_probe_complete():
    from peerhub.cli.monitor_refresh import RefreshSchedule

    schedule = RefreshSchedule({"cc": {"quota:cc:C-7D", "quota:cc:C-5H"}, "cx": {"quota:cx:X-7D"}})
    now = 100.0
    schedule.update([_reading("cc", "C-7D", 0), _reading("cc", "C-5H", 80), _reading("cx", "X-7D", 0)], now)
    # Some partial provider replies return a subset of measured windows without an explicit error row.
    schedule.completed(("cc", "cx"), {"status": "OK", "observations": [
        _result("cc", "C-7D"), _result("cx", "X-7D"),
    ]}, now)
    assert schedule.due(now + support.QUOTA_REFRESH_COOLDOWN_SECONDS) == ("cc",)


@pytest.mark.parametrize("expand", [False, True])
def test_slow_failed_refresh_ages_the_same_committed_value(expand):
    from peerhub.extensions.observation_model import QUOTA_TTL_SECONDS

    row = pool("cc", "C-7D", .5)
    row.update(age_seconds=support.MONITOR_QUOTA_MAX_AGE_SECONDS, ttl_seconds=QUOTA_TTL_SECONDS)
    payload = _payload([], [row])
    payload["_live_refresh"] = {"heartbeat": 0, "peers": ["cc"], "elapsed": 0}

    def rendered():
        return monitor_view.render_grouped_frame(payload, view="plain", unicode=False, width=100, expand=expand)

    def pool_state(frame):
        return next(line.split()[2] for line in frame.splitlines() if line.startswith(" cc C-7D"))

    assert pool_state(rendered()) == "ok"
    payload["display_elapsed_seconds"] = support.MONITOR_REFRESH_LEAD_SECONDS
    payload["_live_refresh"]["elapsed"] = support.MONITOR_REFRESH_LEAD_SECONDS
    during = rendered()
    assert pool_state(during) == "aged" and "refreshing cc" in during
    payload["_live_refresh"].update(peers=[], failed_ago=0, retry_in=support.QUOTA_REFRESH_COOLDOWN_SECONDS)
    failed = rendered()
    assert pool_state(failed) == "aged" and "refresh failed" in failed
    assert row["state"] == "MEASURED"


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("style", ["plain", "rich"])
def test_live_title_status_and_truncation(expand, style):
    payload = full_payload()
    status = {"heartbeat": 0, "age": 82, "next_in": 80}
    payload["_live_refresh"] = status

    def title(width):
        return monitor_view.render_grouped_frame(payload, view=style, unicode=style == "rich",
                                                 width=width, expand=expand).splitlines()[1]

    assert "oldest quota age 1m22s" in title(100)
    assert "next refresh 1m20s" in title(100)
    status.update(peers=("cc", "cx"), elapsed=12)
    assert "refreshing cc cx 12s" in title(100)
    status.update(peers=(), failed_ago=120, retry_in=40)
    assert "refresh failed 2m ago" in title(100) and "retry 40s" in title(100)
    first = title(18)
    status["heartbeat"] = 1
    second = title(18)
    assert first[0] != second[0]
    assert view.visible_len(first) < 18 and view.visible_len(second) < 18


@pytest.mark.parametrize("expand", [False, True])
def test_live_title_lists_distinct_countdowns(expand):
    payload = _payload([], [])
    status = payload["_live_refresh"] = {"heartbeat": 0, "failed_ago": 120, "retry_in": 39.2, "next_in": 39.8}

    def title():
        return monitor_view.render_grouped_frame(payload, view="plain", width=100, expand=expand).splitlines()[1]

    assert "retry 40s" in title() and "next refresh" not in title()
    status["next_in"] = 40.2
    assert "next refresh 41s" in title()


@pytest.mark.parametrize("columns", [0, 1])
def test_live_title_at_minimum_width_defaults_heartbeat(columns):
    assert monitor_view._fit_live_quota_title({}, "", "", "", columns, False) == (
        monitor_view.QUOTA_HEARTBEAT_ASCII[0], True)


def test_live_title_at_zero_width_keeps_heartbeat():
    assert monitor_view._fit_live_quota_title({"heartbeat": 0}, "", "", "", 0, False) == (
        monitor_view.QUOTA_HEARTBEAT_ASCII[0], True)


@pytest.mark.parametrize("second_status", ["OK", "PARTIAL", "FAILED", "raises"])
def test_job_commits_each_peer_before_starting_the_next(monkeypatch, second_status):
    from peerhub.cli.monitor_refresh import RefreshJob

    committed = []
    calls = []
    first = _result("cc", "C-7D")
    second = _result("cx", "X-7D")

    def collect(db, peers, **options):
        assert options["deadline_sec"] == support.MONITOR_PROBE_MAX_TIMEOUT_SECONDS
        calls.append(peers)
        if peers == ("cc",):
            committed.append(first)
            return {"status": "OK", "observations": [first], "warnings": ["cc warning"]}
        assert peers == ("cx",) and committed == [first]
        if second_status == "raises":
            raise OSError("provider unavailable")
        if second_status == "OK":
            committed.append(second)
        return {"status": second_status, "observations": [second], "warnings": ["cx warning"]}

    monkeypatch.setattr(support, "quota_refresh_lock", lambda *a, **kw: nullcontext(True))
    monkeypatch.setattr(support, "refresh_with_profile_pools", collect)
    args = argparse.Namespace(db=__file__, sys_dir=None, timeout=support.MONITOR_REFRESH_LEAD_SECONDS)
    peers = ("cc", "cx")
    job = RefreshJob(args, peers, dict.fromkeys(peers, set()), force=True, retry=())
    job._run(args, dict.fromkeys(peers, set()), True, ())

    assert calls == [("cc",), ("cx",)]
    assert job.result == {
        "status": "OK" if second_status == "OK" else "PARTIAL",
        "observations": [first] if second_status == "raises" else [first, second],
        "warnings": ["cc warning", "OSError" if second_status == "raises" else "cx warning"],
    }


def test_job_reports_failed_if_every_peer_fails(monkeypatch):
    from peerhub.cli.monitor_refresh import RefreshJob

    monkeypatch.setattr(support, "quota_refresh_lock", lambda *a, **kw: nullcontext(True))
    monkeypatch.setattr(support, "refresh_with_profile_pools", lambda db, peers, **kw: {
        "status": "FAILED", "warnings": list(peers),
    })
    args = argparse.Namespace(db=__file__, sys_dir=None, timeout=15)
    peers = ("cc", "cx")
    required = dict.fromkeys(peers, set())
    job = RefreshJob(args, peers, required, force=True, retry=())
    job._run(args, required, True, ())

    assert job.result == {"status": "FAILED", "observations": [], "warnings": ["cc", "cx"]}


def test_job_cancelled_during_one_peer_skips_the_next(monkeypatch):
    from peerhub.cli.monitor_refresh import RefreshJob

    calls = []

    def collect(db, peers, **options):
        calls.append(peers)
        options["cancellation"].stop()
        return {"status": "OK"}

    monkeypatch.setattr(support, "quota_refresh_lock", lambda *a, **kw: nullcontext(True))
    monkeypatch.setattr(support, "refresh_with_profile_pools", collect)
    args = argparse.Namespace(db=__file__, sys_dir=None, timeout=15)
    peers = ("cc", "cx")
    required = dict.fromkeys(peers, set())
    job = RefreshJob(args, peers, required, force=True, retry=())
    job._run(args, required, True, ())

    assert calls == [("cc",)]
    assert job.result is None


@pytest.mark.parametrize("expand", [False, True])
def test_watch_defers_a_lock_timeout_without_reporting_failure(monkeypatch, expand):
    from peerhub.cli import monitor_refresh

    now = 100.0
    launches, frames = [], []
    last = now + support.MONITOR_TICK_SECONDS + support.QUOTA_REFRESH_COOLDOWN_SECONDS
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(view, "enable_vt", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr(view.FrameOutput, "write", lambda self, frame: frames.append(frame))
    monkeypatch.setattr(view.FrameOutput, "geometry", staticmethod(lambda: (100, 40)))
    monkeypatch.setattr("peerhub.cli.store_select.report_missing_store", lambda *a, **kw: False)
    monkeypatch.setattr(monitor_read_model, "render_monitor_frame", lambda *a, **kw: full_payload())
    monkeypatch.setattr(monitor_refresh, "read_quota_readings", lambda db: [])
    monkeypatch.setattr(support, "configured_pools", lambda *a: ["C-7D"])
    monkeypatch.setattr(support, "quota_refresh_lock", lambda *a, **kw: nullcontext(False))
    monkeypatch.setattr(monitor, "monotonic", lambda: now)
    start_job = monitor_refresh.RefreshJob.start

    def launch(*args, **kwargs):
        launches.append(now)
        job = start_job(*args, **kwargs)
        assert job is not None
        job._worker.join(1)
        assert job.done() and job.result is None
        return job

    def tick(seconds):
        nonlocal now
        if now >= last:
            raise KeyboardInterrupt
        now += seconds

    monkeypatch.setattr(monitor_refresh.RefreshJob, "start", staticmethod(launch))
    monkeypatch.setattr(monitor, "sleep", tick)
    assert main(["--db", __file__, "monitor", "--group", "capacity", "--peers", "cc", "--view", "plain",
                 "--cycles", "0", "--probe", *(["--expand"] if expand else [])]) == 0
    assert launches == [100, last]
    assert any("next refresh 1m" in frame for frame in frames)
    assert all("refresh failed" not in frame for frame in frames)


@pytest.mark.parametrize("forced", [False, True])
def test_watch_ticks_while_probe_runs_and_reads_once_at_completion(monkeypatch, forced):
    from peerhub.cli import monitor_refresh

    started, release = Event(), Event()
    frames, reads, captures, geometries, jobs = [], [], [], [], []
    ui_thread = get_ident()
    now = 100.0
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(view, "enable_vt", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.delenv("PEERHUB_MONITOR_PROBE", raising=False)
    monkeypatch.setattr(view.FrameOutput, "write", lambda self, frame: frames.append((get_ident(), frame)))
    monkeypatch.setattr(view.FrameOutput, "geometry", staticmethod(lambda: geometries.append(now) or (100, 40)))
    monkeypatch.setattr("peerhub.cli.store_select.report_missing_store", lambda *a, **kw: False)
    monkeypatch.setattr(monitor_read_model, "render_monitor_frame", lambda *a, **kw: reads.append(now) or full_payload())
    monkeypatch.setattr(monitor_refresh, "read_quota_readings", lambda db: (
        [_reading("cc", "C-7D", now - 103)] if release.is_set() else []))
    monkeypatch.setattr(support, "configured_pools", lambda *a: ["C-7D"])
    monkeypatch.setattr(support, "quota_refresh_lock", lambda *a, **kw: nullcontext(True))
    monkeypatch.setattr(monitor, "monotonic", lambda: now)

    def collect(db, peers, **options):
        captures.append((get_ident(), peers, options["deadline_sec"]))
        started.set()
        assert release.wait(2)
        return {"status": "OK", "observations": [_result("cc", "C-7D")]}

    monkeypatch.setattr(support, "refresh_with_profile_pools", collect)
    start_job = monitor_refresh.RefreshJob.start

    def launch(*args, **kwargs):
        job = start_job(*args, **kwargs)
        jobs.append(job)
        return job

    monkeypatch.setattr(monitor_refresh.RefreshJob, "start", staticmethod(launch))

    def tick(seconds):
        nonlocal now
        assert 0 < seconds <= 1
        assert started.wait(1)
        now += seconds
        if now == 102:
            assert len(frames) >= 2 and reads == [100]
        if now == 103:
            release.set()
            jobs[0]._worker.join(1)
            assert jobs[0].done()
        if now >= 104 and len(reads) > 1:
            raise KeyboardInterrupt
        if now > 110:
            pytest.fail("watch did not apply the worker result")

    monkeypatch.setattr(monitor, "sleep", tick)
    argv = ["--db", __file__, "monitor", "--group", "capacity", "--peers", "cc", "--view", "plain",
            "--timeout-seconds", "120"]
    if forced:
        argv.extend(["--cycles", "0", "--probe"])
    try:
        assert main(argv) == 0
    finally:
        release.set()
    assert len(captures) == 1 and captures[0][0] != ui_thread
    assert list(captures[0][1]) == ["cc"] and captures[0][2] == support.MONITOR_PROBE_MAX_TIMEOUT_SECONDS
    assert len(reads) == 2
    assert len(geometries) == len(frames) >= 4 and all(thread == ui_thread for thread, _ in frames)
    titles = [frame.splitlines()[1] for _, frame in frames]
    assert all(left[0] != right[0] for left, right in zip(titles, titles[1:]))
    assert any("refreshing cc 1s" in title for title in titles)
    assert "next refresh" in titles[-1]


@pytest.fixture
def posix_probe_cleanup(monkeypatch):
    monkeypatch.setattr(quota_probes, "os", SimpleNamespace(name="posix", environ=quota_probes.os.environ))


def test_interrupt_kills_the_probe_and_single_flight_blocks_probe_and_auto(monkeypatch, posix_probe_cleanup):
    from peerhub.cli import monitor_refresh

    started, killed = Event(), Event()
    processes = []

    class Process:
        pid = 123
        returncode = None

        def __init__(self, command, **options):
            assert options["stdout"] == subprocess.PIPE and options["stderr"] == subprocess.PIPE
            processes.append(self)

        def communicate(self, timeout):
            started.set()
            assert killed.wait(2)
            return "", ""

        def poll(self):
            return self.returncode

        def wait(self, *, timeout):
            assert killed.wait(timeout)

    def kill(process, **kwargs):
        assert process in processes
        killed.set()
        return True

    monkeypatch.setattr(quota_probes.subprocess, "Popen", Process)
    monkeypatch.setattr(quota_probes, "kill_process_tree", kill)
    monkeypatch.setattr(quota_probes, "_real_command", lambda *a: ["fake-claude"])
    monkeypatch.setattr(support, "quota_refresh_lock", lambda *a, **kw: nullcontext(True))
    monkeypatch.setattr(support, "configured_pools", lambda *a: [])
    monkeypatch.setattr(monitor_refresh, "read_quota_readings", lambda db: [])
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(view, "enable_vt", lambda: True)
    monkeypatch.setattr(view.FrameOutput, "write", lambda *a: None)
    monkeypatch.setattr(monitor, "_render_human_frame", lambda *a, **kw: "")
    monkeypatch.setattr("peerhub.cli.store_select.report_missing_store", lambda *a, **kw: False)
    monkeypatch.setattr(monitor_read_model, "render_monitor_frame", lambda *a, **kw: full_payload())

    class Ids:
        def new_id(self, prefix):
            return prefix

    def collect(db, peers, **options):
        quota_probes.poll_claude_usage(Ids(), "cc", "default", deadline_sec=options["deadline_sec"],
                                       cancellation=options["cancellation"])
        return {"status": "OK"}

    monkeypatch.setattr(support, "refresh_with_profile_pools", collect)
    args = argparse.Namespace(db=__file__, db_source="explicit", json=False, view="plain", cycles=0,
                              interval=60, collect_every=1, peers=["cc"], probe=True, no_probe=False,
                              sys_dir=None, timeout=15, allow_agy_token_use=False)

    def interrupt(seconds):
        assert started.wait(1)
        assert monitor_refresh.RefreshJob.start(args, ("cc",), force=False) is None
        assert monitor_refresh.RefreshJob.start(args, ("cc",), force=True) is None
        raise KeyboardInterrupt

    monkeypatch.setattr(monitor, "sleep", interrupt)
    before = monotonic()
    assert monitor._run_monitor_grouped_watch(args, ["capacity"], {}) == 0
    assert monotonic() - before < 2
    assert killed.is_set() and len(processes) == 1


def test_job_cleans_up_a_lingering_probe_before_releasing_the_guard(monkeypatch, posix_probe_cleanup):
    from contextlib import contextmanager

    from peerhub.cli.monitor_refresh import RefreshJob

    events = []

    class Process:
        def poll(self):
            return None

        def wait(self, *, timeout):
            events.append("wait")

    process = Process()

    @contextmanager
    def guard(*args, **kwargs):
        try:
            yield True
        finally:
            events.append("release")

    def collect(db, peers, **options):
        options["cancellation"].spawn(["fake-provider"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return {"status": "PARTIAL"}

    monkeypatch.setattr(quota_probes.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(quota_probes, "kill_process_tree", lambda proc, **kw: events.append(proc) or True)
    monkeypatch.setattr(support, "quota_refresh_lock", guard)
    monkeypatch.setattr(support, "refresh_with_profile_pools", collect)
    args = argparse.Namespace(db=__file__, sys_dir=None, timeout=15)
    job = RefreshJob.start(args, ("cc",), force=True, required={"cc": set()})
    assert job is not None
    job._worker.join(1)
    assert job.done() and job.result == {"status": "PARTIAL", "observations": [], "warnings": []}
    assert events == [process, "wait", "release"]


@pytest.mark.parametrize("mode", ["snapshot", "redirected", "finite", "json"])
def test_scripted_monitor_bytes_match_main(monkeypatch, capsys, mode):
    from peerhub.cli import store_select

    db = "README.md"
    monkeypatch.setattr(store_select, "report_missing_store", lambda *a, **kw: False)
    monkeypatch.setattr(store_select, "select_store", lambda *a, **kw: (
        SimpleNamespace(source="discovered", path=Path(db).resolve())))
    monkeypatch.setattr(monitor_read_model, "render_monitor_frame", lambda *a, **kw: {
        "schema_version": "2.0", "status": "OK", "read_at": 1800000000, "groups": {},
    })
    monkeypatch.setattr(monitor, "_cached_profiles", lambda *a: [])
    monkeypatch.setattr(view.FrameOutput, "width", staticmethod(lambda: 100))
    monkeypatch.setattr("sys.stdout.isatty", lambda: mode == "finite")
    monkeypatch.setattr(view, "enable_vt", lambda: False)
    parser = argparse.ArgumentParser()
    monitor.register_monitor_parser(parser.add_subparsers())
    options = {"snapshot": ["--snapshot"], "redirected": [], "finite": ["--cycles", "1"], "json": ["--json"]}
    args = parser.parse_args(["monitor", "--group", "capacity", "--no-probe", "--view", "plain", *options[mode]])
    args.db, args.db_source = db, "explicit"
    assert monitor.run_monitor(args, parser) == 0

    # Pinned from main with a fixed read time and store selection.
    frame = (
        b"OK? (alerts not read)\n"
        b"PeerHub Monitor 2027-01-15 08:00Z | DB:explicit | quota age ?\n"
        b"quota: no reading -> peerhub observation refresh\n"
        b"POOLS 0\nno quota evidence; next: peerhub observation refresh\n"
        b"suggest: no eligible profiles; next: peerhub observation refresh\n"
    )
    snapshot = (
        b'{"schema_version":"2.0","status":"OK","read_at":1800000000,"groups":{},"selected_groups":["capacity"],'
        b'"available_groups":["overview","activity","peers","execution","sessions","usage","context","capacity",'
        b'"runtime","coverage","store"],"preference":{"status":"OK","source":"command","reason":null},'
        b'"store_selection":{"path":"README.md","source":"explicit"}}'
    )
    expected = {
        "snapshot": frame,
        "redirected": frame + b"\n",
        "finite": (frame + b"\n") * 2,
        "json": b'{"schema_version":"2.0","cycle":0,"phase":"initial","refresh":{"status":"NOT_STARTED"},"snapshot":'
                + snapshot + b'}\n{"schema_version":"2.0","cycle":0,"phase":"complete","refresh":{"skipped":true},"snapshot":'
                + snapshot + b"}\n",
    }
    captured = capsys.readouterr()
    assert captured.out.encode("utf-8") == expected[mode]
    assert captured.err == ""


def test_a_deferred_peer_that_another_process_refreshed_is_not_due_again_at_the_cooldown():
    from peerhub.cli.monitor_refresh import RefreshSchedule

    schedule = RefreshSchedule({"cc": {"quota:cc:C-7D"}})
    stale = support.MONITOR_QUOTA_MAX_AGE_SECONDS + 5
    schedule.update([_reading("cc", "C-7D", stale)], 100.0)
    assert schedule.due(100.0) == ("cc",)
    schedule.deferred(("cc",), 100.0)  # the refresh lock was held elsewhere
    cooldown = 100.0 + support.QUOTA_REFRESH_COOLDOWN_SECONDS
    assert schedule.due(cooldown - 1) == ()
    schedule.update([_reading("cc", "C-7D", 3)], cooldown)  # the other process committed a fresh reading
    assert schedule.due(cooldown) == () and schedule.next_in(cooldown) > 0  # no tight loop of useless jobs
    schedule.update([_reading("cc", "C-7D", stale)], cooldown + support.MONITOR_QUOTA_MAX_AGE_SECONDS)
    assert schedule.due(cooldown + support.MONITOR_QUOTA_MAX_AGE_SECONDS) == ("cc",)
