"""Exercise the local release flow without GitHub or live providers."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import ci_platform, live_gate, release_evidence, release_flow
from tools.release_ci import REPORT_PATTERN, REQUIRED_JOBS, candidate_runs_endpoint, matrix_junit_paths
from tools.release_evidence import RELEASE_POLICY_REL, stamp_junit

pytestmark = [pytest.mark.package, pytest.mark.release]
VERSION = "1.2.3"
TAG = f"v{VERSION}"
SHA = "a" * 40
OTHER_SHA = "b" * 40
REPO = "owner/peerhub"
RUN_ID = 42
RUN_URL = f"https://github.com/{REPO}/actions/runs/{RUN_ID}"
FETCH_MAIN = ("git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main", "--tags")
NOW = datetime(2026, 11, 21, 4, 5, 6, 500, tzinfo=timezone.utc)
JSON_NAME = f"{NOW.date()}-{TAG}.json"
NAMES = (*release_flow.JUNIT_NAMES, JSON_NAME)
CELL = next(name for name in sorted(REQUIRED_JOBS) if name.startswith("full-matrix"))
PUBLISH = f"gh release edit {TAG} --draft=false"
NOTES = b"### Added\n\n- Candidate note.\n"


def job(name, conclusion="success"):
    return {"name": name, "run_id": RUN_ID, "head_sha": SHA, "status": "completed",
            "conclusion": conclusion}


def draft(target=SHA, *, is_draft=True, names=NAMES, tag=TAG):
    return {"tag_name": tag, "draft": is_draft, "target_commitish": target,
            "assets": [{"name": name} for name in names]}


class FakeLive:
    pid = 54321

    def __init__(self, fake):
        self.fake = fake
        self.returncode = None
        self.terminated = False
        self.reaped = False

    def poll(self):
        if self.returncode is None and not (self.fake.hold_live or (self.fake.overlap and not self.fake.ci_polled)):
            return self.wait()
        return self.returncode

    def wait(self, timeout=None):
        assert self.fake.lock_path.is_file()
        if self.fake.live_stream is not None:
            assert self.fake.live_stream.closed
        if timeout is not None:
            self.fake.wait_timeouts.append(timeout)
            if self.returncode is None and self.fake.wait_errors:
                raise self.fake.wait_errors.pop(0)
            if self.returncode is None and self.fake.live_never_exits:
                self.fake.clock_seconds += timeout
                raise subprocess.TimeoutExpired("live_gate", timeout)
        self.reaped = True
        if self.returncode is None:
            self.returncode = self.fake.live_exit
            self.fake.complete_live()
        return self.returncode

    def stop(self):
        assert self.fake.lock_path.is_file()
        self.terminated = True
        self.returncode = -1


class Fake:
    def __init__(self, root):
        self.root = root
        self.previous_version = "1.2.2"
        self.evidence = root / release_flow.EVIDENCE_DIR.relative_to(release_flow.ROOT)
        self.state_path = self.evidence.with_name("release-flow") / f"state-{VERSION}.json"
        self.checkpoint = self.state_path.with_name(f"live-{VERSION}.json")
        self.lock_path = self.state_path.with_name(f"lock-{VERSION}")
        self.calls = []
        self.dirty = ""
        self.main_sha = SHA
        self.tag_sha = None
        self.remote_tag_sha = None
        self.remote_tag_reads = 0
        self.remote_tag_on_read = 1
        self.release = None
        self.created_release = draft()
        self.hidden_release_reads = 0  # #486: list reads that do not show a just-created draft yet
        self.create_exit = 0
        self.omit_digests = set()
        self.initial_upload_bytes = {}
        self.repair_upload_bytes = {}
        self.asset_bytes = {}
        self.next_asset_id = 100
        self.asset_download_exit = 0
        self.dispatched = False
        self.dispatch_output = f"{RUN_URL}\n".encode()
        self.sleeps = []
        self.run_sha = SHA
        self.event = "workflow_dispatch"
        self.conclusions = {1: "success", 2: "success"}
        self.jobs = {1: [job(name) for name in sorted(REQUIRED_JOBS)], 2: [job(CELL)]}
        self.attempt = 1
        self.run_overrides = {}
        self.listed_runs = []
        self.push_visible_after = 0
        self.discovery_reads = 0
        self.bad_ci_report = None
        self.raise_while_polling = None
        self.rerun_requested = False
        self.rerun_exit = 0
        self.stale_retry_polls = 0
        self.live_exit = 0
        self.live_sha = SHA
        self.write_json = True
        self.notes = NOTES
        self.overlap = False
        self.live_started = False
        self.ci_polled = False
        self.hold_live = False
        self.live_process = None
        self.live_runner = None
        self.live_completed = None
        self.live_stages_run = []
        self.resuming_live = False
        self.interrupt_at_launch = False
        self.live_stream = None
        self.wait_timeouts = []
        self.wait_errors = []
        self.clock_seconds = 0.0
        self.live_never_exits = False
        self.taskkill_exit = 0
        self.exit_during_taskkill = False
        self.run_reads = 0
        self.fetches = 0
        self.move_on_fetch = None

    def __call__(self, cmd, *, cwd, log=None, timeout=None):
        assert self.lock_path.is_file()
        self.calls.append(tuple(cmd))
        if cmd[0] == "git":
            return self.git(cmd, cwd)
        if cmd[:2] == ["gh", "api"]:
            return self.api(cmd, cwd)
        if cmd[0] == "gh":
            return self.gh(cmd, cwd)
        if cmd[0] == "taskkill":
            assert cwd == self.root
            assert cmd == ["taskkill", "/PID", str(FakeLive.pid), "/T", "/F"]
            assert timeout == release_flow.LIVE_STOP_SECONDS
            if not self.taskkill_exit:
                self.live_process.stop()
            elif self.exit_during_taskkill:
                self.live_process.returncode = 0
            return subprocess.CompletedProcess(cmd, self.taskkill_exit, b"", b"process not found")
        expected = [sys.executable, "-m", "tools.live_gate", "--yes", "--checkpoint", str(self.checkpoint)]
        assert cmd in (expected, [*expected, "--resume"])
        self.resuming_live = cmd[-1] == "--resume"
        assert cwd == self.root
        assert log == self.evidence / "release-flow-live.log"
        self.live_started = True
        if self.live_runner is None:
            log.write_bytes(b"Live gate output\n")
            self.live_process = FakeLive(self)
        else:
            self.live_process = self.live_runner(cmd, cwd=cwd, log=log)
        if self.interrupt_at_launch:  # Ctrl+C lands right after Popen returned, before the flow registered the child
            import signal as signal_module

            signal_module.getsignal(signal_module.SIGINT)(signal_module.SIGINT, None)
        return self.live_process

    def complete_live(self):
        limit = self.live_completed if self.live_completed is not None else (0 if self.live_exit else len(NAMES) - 1)
        hashes = json.loads(self.checkpoint.read_text(encoding="utf-8"))["evidence_sha256"] if self.checkpoint.exists() else {}
        for name in release_flow.JUNIT_NAMES[:limit]:
            if name in hashes:
                continue
            self.live_stages_run.append(name)
            path = self.evidence / name
            path.write_bytes(b'<testsuite><testcase name="test_live_case"/></testsuite>')
            stamp_junit(path, commit=self.live_sha, gate="G3", at=NOW)
            hashes[name] = live_gate.file_digest(path)
        if limit and (self.write_json or JSON_NAME in hashes):
            if JSON_NAME not in hashes:
                (self.evidence / JSON_NAME).write_bytes(b'{"providers": {}}\n')
                hashes[JSON_NAME] = live_gate.file_digest(self.evidence / JSON_NAME)
            live_gate.write_checkpoint(self.checkpoint, SHA, hashes)

    def git_output(self, cwd, *args):
        assert cwd == self.root
        versions = {f"{SHA}:peerhub/_version.py": VERSION,
                    f"{SHA}^:peerhub/_version.py": self.previous_version}
        command, rev = args
        assert command == "show"
        return f'__version__ = "{versions[rev]}"\n'

    def git(self, cmd, cwd):
        assert cwd == self.root
        match cmd[1:]:
            case ["fetch", "origin", refspec, "--tags"] if refspec == FETCH_MAIN[3]:
                self.fetches += 1
                if self.fetches == self.move_on_fetch:
                    self.main_sha = OTHER_SHA
                out = ""
            case ["status", "--porcelain", "--untracked-files=all"]:
                out = self.dirty
            case ["rev-parse", "HEAD"]:
                out = SHA
            case ["rev-parse", "--verify", "refs/remotes/origin/main^{commit}"]:
                out = self.main_sha
            case ["tag", "--list", which] if which == TAG:
                out = TAG if self.tag_sha else ""
            case ["rev-parse", "--verify", ref] if ref == f"refs/tags/{TAG}^{{commit}}":
                assert self.tag_sha is not None
                out = self.tag_sha
            case ["remote", "get-url", "origin"]:
                out = f"https://github.com/{REPO}.git"
            case ["ls-remote", "--tags", "origin", ref] if ref == f"refs/tags/{TAG}":
                self.remote_tag_reads += 1
                out = (f"{self.remote_tag_sha}\t{ref}"
                       if self.remote_tag_sha and self.remote_tag_reads >= self.remote_tag_on_read else "")
            case _:
                pytest.fail(f"Unexpected git command {cmd}")
        return subprocess.CompletedProcess(cmd, 0, (out + "\n").encode())

    def api(self, cmd, cwd):
        assert cwd == self.root
        assert cmd[2:4] == ["--hostname", "github.com"]
        base = f"repos/{REPO}/actions/runs/{RUN_ID}"
        if cmd[-1] == f"repos/{REPO}/releases":
            assert cmd[4:-1] == ["--paginate", "--slurp"]
            visible = self.release is not None and not (self.release["draft"] and self.hidden_release_reads)
            if self.release is not None and self.release["draft"] and self.hidden_release_reads:
                self.hidden_release_reads -= 1
            out = [[draft(tag="v1.2.2", is_draft=False)], [self.release] if visible else []]
        elif cmd[-1] == f"repos/{REPO}/{candidate_runs_endpoint(SHA)}":
            assert cmd[4:-1] == ["--paginate", "--slurp"]
            self.discovery_reads += 1
            out = [{"workflow_runs": self.listed_runs if self.discovery_reads > self.push_visible_after else []}]
        elif cmd[-1].startswith(f"repos/{REPO}/releases/assets/"):
            assert cmd[4:-1] == ["-H", "Accept: application/octet-stream"]
            asset_id = int(cmd[-1].rsplit("/", 1)[-1])
            if self.asset_download_exit:
                return subprocess.CompletedProcess(cmd, self.asset_download_exit, b"", b"asset unavailable")
            return subprocess.CompletedProcess(cmd, 0, self.asset_bytes[asset_id])
        elif cmd[-1] == base:
            self.run_reads += 1
            if self.live_started and not self.ci_polled:
                if self.overlap:
                    assert self.live_process.poll() is None, "live gate already finished before CI polled"
                self.ci_polled = True
                if self.raise_while_polling is not None:
                    raise self.raise_while_polling
            if self.rerun_requested:
                if self.stale_retry_polls:
                    self.stale_retry_polls -= 1
                else:
                    self.attempt = 2
            out = {"id": RUN_ID, "head_sha": self.run_sha, "event": self.event,
                   "head_branch": "main", "path": ".github/workflows/ci.yml",
                   "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
                   "run_attempt": self.attempt, "status": "completed",
                   "conclusion": self.conclusions[self.attempt], **self.run_overrides.get(self.run_reads, {})}
        elif (push := next((row for row in self.listed_runs
                           if cmd[-1] == f"repos/{REPO}/actions/runs/{row['id']}"), None)) is not None:
            out = push
        else:
            attempt = next(number for number in self.jobs if cmd[-1] == f"{base}/attempts/{number}/jobs")
            assert cmd[4:-1] == ["--paginate", "--slurp"]
            out = [{"jobs": self.jobs[attempt][:3]}, {"jobs": self.jobs[attempt][3:]}]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out).encode())

    def upload_asset(self, name, overrides):
        data = overrides.get(name, (self.evidence / name).read_bytes())
        asset_id = self.next_asset_id
        self.next_asset_id += 1
        self.asset_bytes[asset_id] = data
        asset = {"name": name, "id": asset_id, "size": len(data), "state": "uploaded"}
        if name not in self.omit_digests:
            asset["digest"] = f"sha256:{hashlib.sha256(data).hexdigest()}"
        return asset

    def gh(self, cmd, cwd):
        assert cmd[-2:] == ["--repo", f"github.com/{REPO}"]
        match cmd[1:-2]:
            case ["workflow", "run", "ci.yml", "--ref", "main", "-f", "full_matrix=true"]:
                assert not self.dispatched
                self.dispatched = True
                return subprocess.CompletedProcess(cmd, 0, self.dispatch_output)
            case ["run", "download", "42", "--pattern", pattern, "--dir", directory] if pattern == REPORT_PATTERN:
                for path in matrix_junit_paths(Path(directory)):
                    path.parent.mkdir()
                    if path.name == self.bad_ci_report:
                        continue
                    path.write_bytes(b'<testsuite><testcase name="test_ci_case"/></testsuite>')
                    stamp_junit(path, commit=SHA, gate="G2", at=NOW)
                return subprocess.CompletedProcess(cmd, 0, b"")
            case ["run", "rerun", "42", "--failed"]:
                assert not self.rerun_requested
                assert self.state()["retry_requested"] is False
                if self.rerun_exit:
                    return subprocess.CompletedProcess(cmd, self.rerun_exit, b"", b"rerun rejected")
                self.rerun_requested = True
                return subprocess.CompletedProcess(cmd, 0, b"")
            case ["release", "create", tag, "--draft", "--target", target, "--title", title,
                  "--notes-file", "notes.md", *names] if tag == title == TAG:
                assert cwd == self.evidence
                assert target == SHA
                assert tuple(names) == NAMES
                assert (cwd / "notes.md").read_bytes() == self.notes
                assets = [self.upload_asset(asset["name"], self.initial_upload_bytes) | asset
                          for asset in self.created_release["assets"]]
                self.release = self.created_release | {"assets": assets}
                return subprocess.CompletedProcess(cmd, self.create_exit, b"", b"upload failed")
            case ["release", "upload", tag, *files] if tag == TAG:
                assert cwd == self.evidence and self.release["draft"]
                assert files
                assets = self.release["assets"]
                if files[-1] == "--clobber":
                    name, _ = files
                    assert any(asset["name"] == name for asset in assets)
                    assets[:] = [asset for asset in assets if asset["name"] != name]
                    files = [name]
                for name in files:
                    assert all(asset["name"] != name for asset in assets)
                    assets.append(self.upload_asset(name, self.repair_upload_bytes))
                return subprocess.CompletedProcess(cmd, 0, b"")
            case _:
                pytest.fail(f"Unexpected gh command {cmd}")

    def invoke(self, *flags):
        return release_flow.main(["run", "--version", VERSION, *flags],
                                 runner=self, root=self.root, sleep=self.sleeps.append,
                                 clock=lambda: self.clock_seconds)

    def called(self, *prefix):
        return [call for call in self.calls if call[:len(prefix)] == prefix]

    def state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def replace_job(self, name, conclusion, attempt=1):
        jobs = self.jobs[attempt]
        jobs[:] = [item for item in jobs if item["name"] != name] + [job(name, conclusion)]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(release_flow, "is_windows", lambda: True)
    monkeypatch.setattr(release_evidence, "_now", lambda: NOW)
    (tmp_path / "peerhub").mkdir()
    (tmp_path / "peerhub/_version.py").write_text(f'__version__ = "{VERSION}"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(f"## [{VERSION}]\n\n" + NOTES.decode(), encoding="utf-8")
    for rel, contents in {
        RELEASE_POLICY_REL: (release_flow.ROOT / RELEASE_POLICY_REL).read_text(encoding="utf-8"),
        "docs/m1_spec/06_GUIDES/TEST_SET/test-catalog.json": '{"tests": []}',
        "docs/m1_spec/06_GUIDES/TEST_SET/TEST_RELEASE_GATE_MAP.json": '{"entries": []}',
        "docs/m2/test-catalog.m2.json": '{"tests": []}',
        "docs/m3/test-catalog.m3.json": '{"tests": []}',
    }.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    flow_fake = Fake(tmp_path)
    monkeypatch.setattr(ci_platform, "git_output", flow_fake.git_output)
    return flow_fake


def test_fake_checkout_uses_pytest_tmp_path(fake, tmp_path):
    assert fake.root == tmp_path


def test_happy_path_binds_evidence_and_runs_ci_concurrently(fake, capsys):
    fake.overlap = True
    assert fake.invoke() == 0
    assert fake.calls[0] == FETCH_MAIN
    assert len(fake.called("gh", "release", "create")) == 1
    assert f"{PUBLISH} --repo github.com/{REPO}" in capsys.readouterr().out
    assert fake.state() == {
        "head": SHA, "ci_run_id": RUN_ID, "live_status": "passed", "retry_requested": False, "initial_ci_attempt": 1,
        "evidence_sha256": {name: hashlib.sha256((fake.evidence / name).read_bytes()).hexdigest() for name in NAMES},
    }


def test_preflight_refreshes_tracking_main_before_comparing_it_to_head(fake, capsys):
    assert fake.main_sha == SHA
    fake.move_on_fetch = 1
    assert fake.invoke() == 1
    assert fake.calls[0] == FETCH_MAIN
    assert f"origin/main is {OTHER_SHA}" in capsys.readouterr().err
    assert not fake.dispatched


@pytest.mark.parametrize(("attribute", "value", "message"), [
    ("dirty", "?? changed.py", "working tree is dirty"),
    ("main_sha", OTHER_SHA, "origin/main"),
    ("tag_sha", OTHER_SHA, f"tag {TAG} resolves to"),
    ("tag_sha", SHA, f"tag {TAG} already exists"),
    ("release", draft(), f"release {TAG} already exists"),
    ("release", draft(is_draft=False), f"release {TAG} already exists"),
])
def test_preflight_refuses_before_dispatch_or_live(fake, capsys, attribute, value, message):
    setattr(fake, attribute, value)
    assert fake.invoke() == 1
    output = capsys.readouterr()
    assert message in output.err
    assert PUBLISH not in output.out
    assert not fake.dispatched
    assert not fake.live_started
    assert not fake.state_path.exists()
    assert not fake.lock_path.exists()


@pytest.mark.parametrize(("path", "contents", "message"), [
    ("CHANGELOG.md", "## [Unreleased]\n", f"No CHANGELOG section for {VERSION}"),
    ("peerhub/_version.py", '__version__ = "2.0.0"\n',
     f"peerhub/_version.py is 2.0.0. Expected {VERSION}. "
     "Include the version bump, STATUS heading, changelog and NOW.md Updated/Released fields "
     "in the last feature PR for the release."),
])
def test_preflight_checks_version_and_notes(fake, capsys, path, contents, message):
    (fake.root / path).write_text(contents, encoding="utf-8")
    assert fake.invoke() == 1
    assert message in capsys.readouterr().err
    assert not fake.dispatched


@pytest.mark.parametrize(("attribute", "value", "message"), [
    ("run_sha", OTHER_SHA, "CI run 42 head SHA"),
    ("move_on_fetch", 3, "origin/main"),
    ("event", "push", "expected workflow_dispatch"),
])
def test_dispatch_binding_refuses_mismatches_before_live(fake, capsys, attribute, value, message):
    setattr(fake, attribute, value)
    assert fake.invoke() == 1
    assert message in capsys.readouterr().err
    assert fake.dispatched
    assert not fake.live_started
    assert not fake.state_path.exists()


def test_main_moving_after_preflight_refuses_before_dispatch(fake, capsys):
    fake.move_on_fetch = 2
    assert fake.invoke() == 1
    assert "origin/main" in capsys.readouterr().err
    assert not fake.dispatched


def test_dispatch_binds_stdout_url_with_crlf_and_invalid_utf8_elsewhere(fake):
    fake.dispatch_output = b"\xff\r\n" + f"{RUN_URL}\r\n".encode()
    assert fake.invoke("--ci-first") == 0
    assert not fake.called("gh", "run", "list")
    assert fake.state()["ci_run_id"] == RUN_ID


@pytest.mark.parametrize("stdout", [
    b"",
    b"https://github.com/other/peerhub/actions/runs/42\r\n",
    f"{RUN_URL}\n{RUN_URL.replace('/42', '/43')}\n".encode(),
    b"\xff error without a run URL",
])
def test_dispatch_without_one_matching_url_fails_closed(fake, capsys, stdout):
    fake.dispatch_output = stdout
    assert fake.invoke() == 1
    assert "did not return a single CI run URL" in capsys.readouterr().err
    assert fake.dispatched
    assert not fake.called("gh", "run", "list")
    assert not fake.run_reads
    assert not fake.state_path.exists()
    assert not fake.live_started


def test_release_lookup_uses_paginated_rest_for_preflight_and_verification(fake):
    assert fake.invoke("--ci-first") == 0
    lookups = [call for call in fake.called("gh", "api") if call[-1] == f"repos/{REPO}/releases"]
    assert len(lookups) == 3
    assert all(call[4:-1] == ("--paginate", "--slurp") for call in lookups)


def test_draft_notes_are_extracted_in_process_as_utf8(fake):
    notes = "### Added\n\n- Café and 한글.\n"
    (fake.root / "CHANGELOG.md").write_bytes((f"## [{VERSION}]\r\n\r\n" +
                                             notes.replace("\n", "\r\n")).encode())
    fake.notes = notes.encode("utf-8")
    assert fake.invoke("--ci-first") == 0
    assert (fake.evidence / "notes.md").read_bytes() == fake.notes
    assert not fake.called(sys.executable, "-m", "tools.release_notes")


def test_cancelled_jobs_retry_once_and_keep_earlier_successes(fake, capsys):
    fake.conclusions[1] = "failure"
    fake.replace_job(CELL, "cancelled")
    fake.stale_retry_polls = 2
    assert fake.invoke("--ci-first") == 0
    assert len(fake.called("gh", "run", "rerun", str(RUN_ID), "--failed")) == 1
    assert "WARNING:" in capsys.readouterr().out
    assert fake.state()["retry_requested"] is True


def test_failed_rerun_command_does_not_persist_retry_ownership(fake, capsys):
    fake.conclusions[1] = "failure"
    fake.replace_job(CELL, "cancelled")
    fake.rerun_exit = 37
    assert fake.invoke("--ci-first") == 1
    assert "exited 37" in capsys.readouterr().err
    assert len(fake.called("gh", "run", "rerun")) == 1
    assert not fake.state()["retry_requested"]


def test_resume_refuses_retry_after_interruption_before_the_flag_was_saved(fake, capsys, monkeypatch):
    assert fake.invoke("--ci-first") == 0
    capsys.readouterr()
    fake.conclusions[1] = "failure"
    fake.replace_job(CELL, "cancelled")
    save_state = release_flow.ReleaseFlow.save_state

    def interrupt_retry_save(flow, state):
        if state.retry_requested:
            assert fake.rerun_requested
            raise KeyboardInterrupt
        save_state(flow, state)

    monkeypatch.setattr(release_flow.ReleaseFlow, "save_state", interrupt_retry_save)
    assert fake.invoke("--resume") == 1
    assert not fake.state()["retry_requested"]
    assert "interrupted" in capsys.readouterr().err

    assert fake.invoke("--resume") == 1
    output = capsys.readouterr()
    assert "attempt 2 not started by this flow" in output.err
    assert PUBLISH not in output.out
    assert len(fake.called("gh", "run", "rerun")) == 1


@pytest.mark.parametrize("name", ["other-job", next(name for name in sorted(REQUIRED_JOBS)
                                                   if name.startswith("full-matrix") and name != CELL)])
@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "startup_failure", "neutral", "action_required"])
def test_failed_or_ambiguous_job_prevents_retry(fake, capsys, name, conclusion):
    fake.conclusions[1] = "failure"
    fake.replace_job(CELL, "cancelled")
    fake.replace_job(name, conclusion)
    assert fake.invoke("--ci-first") == 1
    assert f"{name}={conclusion}" in capsys.readouterr().err
    assert not fake.rerun_requested
    assert not fake.live_started
    assert not fake.called("gh", "release", "create")


@pytest.mark.parametrize(("name", "conclusion"), [
    ("other-job", "cancelled"),
    ("ci-pass", "timed_out"),
    ("ci-pass", "cancelled"),
    ("ci-pass", "startup_failure"),
    ("ci-pass", "neutral"),
    ("ci-pass", "action_required"),
])
def test_ineligible_non_matrix_or_ci_pass_conclusion_blocks_retry(fake, capsys, name, conclusion):
    fake.conclusions[1] = "failure"
    fake.replace_job(CELL, "cancelled")
    fake.replace_job(name, conclusion)
    assert fake.invoke("--ci-first") == 1
    assert f"{name}={conclusion}" in capsys.readouterr().err
    assert not fake.called("gh", "run", "rerun")
    assert not fake.live_started


def test_real_timeout_shape_retries_once(fake, capsys):
    """Run 44813266419 (0.26.0): run `cancelled`, one cell `cancelled`, `ci-pass` `failure`, the rest skipped."""
    fake.conclusions[1] = "cancelled"
    fake.replace_job(CELL, "cancelled")
    fake.replace_job("ci-pass", "failure")
    fake.replace_job("other-job", "skipped")
    fake.replace_job("ci-pass", "success", attempt=2)  # `rerun --failed` reruns the failed ci-pass too
    assert fake.invoke("--ci-first") == 0
    assert len(fake.called("gh", "run", "rerun")) == 1
    assert "WARNING: rerunning failed jobs ONCE" in capsys.readouterr().out


def test_a_failed_cell_beside_a_cancelled_one_prevents_retry(fake, capsys):
    other = next(name for name in sorted(REQUIRED_JOBS) if name.startswith("full-matrix") and name != CELL)
    fake.conclusions[1] = "failure"
    fake.replace_job(CELL, "cancelled")
    fake.replace_job(other, "failure")
    fake.replace_job("ci-pass", "failure")
    assert fake.invoke("--ci-first") == 1
    assert f"{other}=failure" in capsys.readouterr().err
    assert not fake.rerun_requested


@pytest.mark.parametrize("reran_skipped_cell", [True, False])
def test_skipped_required_cell_allows_retry_but_must_end_green(fake, capsys, reran_skipped_cell):
    other = next(name for name in sorted(REQUIRED_JOBS) if name.startswith("full-matrix") and name != CELL)
    fake.conclusions[1] = "failure"
    fake.replace_job(CELL, "cancelled")
    fake.replace_job(other, "skipped")
    if reran_skipped_cell:
        fake.replace_job(other, "success", attempt=2)
    assert fake.invoke("--ci-first") == (0 if reran_skipped_cell else 1)
    output = capsys.readouterr()
    assert len(fake.called("gh", "run", "rerun")) == 1
    if not reran_skipped_cell:
        assert f"{other}=skipped" in output.err
        assert PUBLISH not in output.out


@pytest.mark.parametrize("run_conclusion", ["failure", "success"])
def test_retry_still_red_stops_even_with_green_run_conclusion(fake, capsys, run_conclusion):
    fake.conclusions = {1: "failure", 2: run_conclusion}
    fake.replace_job(CELL, "cancelled")
    fake.replace_job(CELL, "cancelled", attempt=2)
    assert fake.invoke("--ci-first") == 1
    output = capsys.readouterr()
    assert f"{CELL}=cancelled" in output.err
    assert len(fake.called("gh", "run", "rerun")) == 1
    assert not fake.live_started
    assert PUBLISH not in output.out


@pytest.mark.parametrize("resume", [False, True])
def test_missing_matrix_job_refuses_a_green_run(fake, capsys, resume):
    if resume:
        assert fake.invoke("--ci-first") == 0
        capsys.readouterr()
    fake.jobs[1] = [item for item in fake.jobs[1] if item["name"] != CELL]
    assert fake.invoke("--resume" if resume else "--ci-first") == 1
    output = capsys.readouterr()
    assert f"{CELL}=missing" in output.err
    assert PUBLISH not in output.out
    assert fake.live_started is resume


@pytest.mark.parametrize(("attempt", "retry_requested"), [(2, False), (3, True)])
def test_resume_refuses_external_attempt_even_if_green(fake, capsys, attempt, retry_requested):
    assert fake.invoke("--ci-first") == 0
    capsys.readouterr()
    fake.attempt = attempt
    fake.conclusions[attempt] = "success"
    fake.jobs[attempt] = list(fake.jobs[1])
    fake.state_path.write_text(json.dumps(fake.state() | {"retry_requested": retry_requested}), encoding="utf-8")
    assert fake.invoke("--resume") == 1
    output = capsys.readouterr()
    assert f"attempt {attempt} not started by this flow" in output.err
    assert PUBLISH not in output.out


@pytest.mark.parametrize("latest", [{"run_attempt": 2}, {"status": "in_progress"}])
def test_retry_rechecks_attempt_and_completed_status_before_rerun(fake, capsys, latest):
    fake.conclusions[1] = "failure"
    fake.replace_job(CELL, "cancelled")
    fake.run_overrides[3] = latest
    assert fake.invoke("--ci-first") == 1
    assert "changed before retry" in capsys.readouterr().err
    assert not fake.rerun_requested
    assert not fake.state()["retry_requested"]


def test_external_attempt_started_after_green_ci_blocks_draft(fake, capsys):
    fake.run_overrides[3] = {"run_attempt": 2, "status": "in_progress"}
    assert fake.invoke("--ci-first") == 1
    assert "changed after verification" in capsys.readouterr().err
    assert not fake.called("gh", "release", "create")


def test_live_gate_nonzero_writes_log_and_no_draft(fake, capsys):
    fake.live_exit = 5
    assert fake.invoke() == 1
    assert "live gate exited 5" in capsys.readouterr().err
    assert fake.state()["live_status"] == "failed"
    assert not fake.called("gh", "release", "create")
    assert (fake.evidence / "release-flow-live.log").read_bytes() == b"Live gate output\n"


def test_live_gate_refuses_wrong_junit_sha(fake, capsys):
    fake.live_sha = OTHER_SHA
    assert fake.invoke() == 1
    assert "bound to a different candidate: source_revision" in capsys.readouterr().err
    assert not fake.called("gh", "release", "create")


def test_live_gate_does_not_accept_existing_json_without_an_update(fake, capsys):
    fake.evidence.mkdir(parents=True)
    (fake.evidence / JSON_NAME).write_bytes(b'{"providers": {}}\n')
    fake.write_json = False
    assert fake.invoke() == 1
    assert "live gate did not complete all three stages" in capsys.readouterr().err
    assert not fake.called("gh", "release", "create")


@pytest.mark.parametrize("existing_draft", [False, True])
def test_resume_reuses_verified_ci_and_evidence(fake, capsys, existing_draft):
    assert fake.invoke() == 0
    capsys.readouterr()
    fake.calls.clear()
    if not existing_draft:
        fake.release = None
    assert fake.invoke("--resume") == 0
    assert not fake.called("gh", "workflow", "run")
    assert not fake.called(sys.executable, "-m", "tools.live_gate")
    assert bool(fake.called("gh", "release", "create")) is not existing_draft
    assert PUBLISH in capsys.readouterr().out


@pytest.mark.parametrize("tag_location", ["tag_sha", "remote_tag_sha"], ids=["local", "remote"])
def test_resume_refuses_existing_tag_even_with_matching_draft(fake, capsys, tag_location):
    assert fake.invoke("--ci-first") == 0
    capsys.readouterr()
    fake.calls.clear()
    setattr(fake, tag_location, SHA)
    assert fake.invoke("--resume") == 1
    output = capsys.readouterr()
    assert f"tag {TAG} already exists" in output.err
    assert not fake.called("gh", "api")
    assert PUBLISH not in output.out


@pytest.mark.parametrize("name", NAMES)
def test_resume_refuses_tampered_evidence_and_names_file(fake, capsys, name):
    assert fake.invoke() == 0
    capsys.readouterr()
    (fake.evidence / name).write_bytes(b"modified")
    fake.calls.clear()
    assert fake.invoke("--resume") == 1
    output = capsys.readouterr()
    assert f"SHA-256 mismatch for {name}" in output.err
    assert not fake.called("gh", "release", "create")
    assert PUBLISH not in output.out


@pytest.mark.parametrize(("mismatch", "message"), [
    ("state", "state HEAD"),
    ("run", "CI run 42 head SHA"),
    ("draft", f"draft {TAG} target"),
])
def test_resume_refuses_sha_mismatches(fake, capsys, mismatch, message):
    assert fake.invoke() == 0
    capsys.readouterr()
    if mismatch == "state":
        fake.state_path.write_text(json.dumps(fake.state() | {"head": OTHER_SHA}), encoding="utf-8")
    elif mismatch == "run":
        fake.run_sha = OTHER_SHA
    else:
        fake.release = draft(OTHER_SHA)
    assert fake.invoke("--resume") == 1
    output = capsys.readouterr()
    assert message in output.err
    assert PUBLISH not in output.out


def test_a_draft_missing_from_the_first_reads_after_create_is_still_verified(fake, capsys):
    # #486: the releases list lagged `gh release create` and a correct draft failed verification.
    fake.hidden_release_reads = 2
    assert fake.invoke("--ci-first") == 0
    assert PUBLISH in capsys.readouterr().out
    assert fake.sleeps.count(release_flow.DRAFT_READ_SECONDS) == 2


def test_a_draft_that_never_appears_fails_after_bounded_reads(fake, capsys):
    fake.hidden_release_reads = release_flow.DRAFT_READ_POLLS
    assert fake.invoke("--ci-first") == 1
    output = capsys.readouterr()
    assert "draft verification failed" in output.err and PUBLISH not in output.out
    assert fake.sleeps.count(release_flow.DRAFT_READ_SECONDS) == release_flow.DRAFT_READ_POLLS - 1


@pytest.mark.parametrize("created", [draft(names=NAMES[:-1]), draft("main"), draft(is_draft=False)])
def test_bad_draft_never_prints_publish_command(fake, capsys, created):
    fake.created_release = created
    assert fake.invoke() == 1
    output = capsys.readouterr()
    assert "draft verification failed" in output.err
    assert PUBLISH not in output.out


@pytest.mark.parametrize("resume", [False, True])
def test_wrong_remote_asset_size_blocks_fresh_publish_and_is_repaired_on_resume(fake, capsys, resume):
    name = NAMES[-1]
    if resume:
        assert fake.invoke("--ci-first") == 0
        capsys.readouterr()
        fake.calls.clear()
        fake.release["assets"][-1]["size"] += 1
    else:
        fake.created_release["assets"][-1]["size"] = 1
    assert fake.invoke("--resume" if resume else "--ci-first") == (0 if resume else 1)
    output = capsys.readouterr()
    if resume:
        assert PUBLISH in output.out
        upload, = fake.called("gh", "release", "upload")
        assert upload[3:-2] == (TAG, name, "--clobber")
    else:
        assert f"{name} size mismatch" in output.err
        assert PUBLISH not in output.out
        assert not fake.called("gh", "release", "upload")
        assert len(fake.called("git", "ls-remote")) == 2


@pytest.mark.parametrize(("field", "value", "message"), [
    ("state", "new", "has upload state 'new'"),
    ("digest", f"sha256:{'0' * 64}", "SHA-256 digest mismatch"),
])
@pytest.mark.parametrize("resume", [False, True])
def test_draft_verification_requires_uploaded_state_and_matching_digest(fake, capsys, field, value, message, resume):
    if resume:
        assert fake.invoke("--ci-first") == 0
        capsys.readouterr()
        assets = fake.release["assets"]
    else:
        assets = fake.created_release["assets"]
    assets[-1][field] = value
    repairing = resume
    assert fake.invoke("--resume" if resume else "--ci-first") == (0 if repairing else 1)
    output = capsys.readouterr()
    if repairing:
        assert PUBLISH in output.out
        upload, = fake.called("gh", "release", "upload")
        assert upload[3:-2] == (TAG, JSON_NAME, "--clobber")
    else:
        assert f"{JSON_NAME} {message}" in output.err
        assert PUBLISH not in output.out
        assert not fake.called("gh", "release", "upload")


def test_missing_remote_digests_download_and_verify_each_asset(fake, capsys):
    fake.omit_digests.update(NAMES[:2])
    assert fake.invoke("--ci-first") == 0
    output = capsys.readouterr()
    downloads = [call[-1] for call in fake.called("gh", "api") if "/releases/assets/" in call[-1]]
    assert downloads == [f"repos/{REPO}/releases/assets/{asset['id']}" for asset in fake.release["assets"][:2]]
    assert not output.err
    assert PUBLISH in output.out


def test_downloaded_asset_with_wrong_bytes_and_matching_size_blocks_publish(fake, capsys):
    fake.omit_digests.add(JSON_NAME)
    fake.initial_upload_bytes[JSON_NAME] = b'{"providers": []}\n'
    assert fake.invoke("--ci-first") == 1
    output = capsys.readouterr()
    asset = fake.release["assets"][-1]
    assert asset["size"] == (fake.evidence / JSON_NAME).stat().st_size
    assert f"{JSON_NAME} downloaded SHA-256 mismatch" in output.err
    assert PUBLISH not in output.out


def test_resume_retries_partial_create_without_overwriting_verified_assets(fake, capsys):
    fake.created_release = draft(names=NAMES[:2])
    fake.create_exit = 1
    assert fake.invoke("--ci-first") == 1
    output = capsys.readouterr()
    assert "upload failed" in output.err
    assert PUBLISH not in output.out
    retained = fake.release["assets"][:]
    fake.calls.clear()

    assert fake.invoke("--resume") == 0
    upload, = fake.called("gh", "release", "upload")
    assert upload[3:-2] == (TAG, *NAMES[2:])
    assert fake.release["assets"][:2] == retained
    assert not fake.called("gh", "release", "create")
    assert not fake.called(sys.executable, "-m", "tools.live_gate")
    assert PUBLISH in capsys.readouterr().out


@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_resume_replaces_only_mismatched_download_and_verifies_replacement(fake, capsys, repair_succeeds):
    fake.omit_digests.add(JSON_NAME)
    bad_data = b'{"providers": []}\n'
    fake.initial_upload_bytes[JSON_NAME] = bad_data
    assert fake.invoke("--ci-first") == 1
    capsys.readouterr()
    retained = fake.release["assets"][:-1]
    old_id = fake.release["assets"][-1]["id"]
    fake.calls.clear()
    if not repair_succeeds:
        fake.repair_upload_bytes[JSON_NAME] = bad_data

    assert fake.invoke("--resume") == (0 if repair_succeeds else 1)
    output = capsys.readouterr()
    upload, = fake.called("gh", "release", "upload")
    assert upload[3:-2] == (TAG, JSON_NAME, "--clobber")
    assert fake.release["assets"][:-1] == retained
    new_id = fake.release["assets"][-1]["id"]
    downloads = [call[-1] for call in fake.called("gh", "api") if "/releases/assets/" in call[-1]]
    assert downloads == [f"repos/{REPO}/releases/assets/{asset_id}" for asset_id in (old_id, new_id)]
    assert (PUBLISH in output.out) is repair_succeeds
    if not repair_succeeds:
        assert f"{JSON_NAME} downloaded SHA-256 mismatch" in output.err


def test_resume_leaves_existing_asset_alone_if_download_errors(fake, capsys):
    fake.omit_digests.add(JSON_NAME)
    assert fake.invoke("--ci-first") == 0
    capsys.readouterr()
    fake.calls.clear()
    fake.asset_download_exit = 1
    assert fake.invoke("--resume") == 1
    output = capsys.readouterr()
    assert "asset unavailable" in output.err
    assert not fake.called("gh", "release", "upload")
    assert PUBLISH not in output.out


@pytest.mark.parametrize("name", NAMES)
def test_rehashes_every_asset_after_preparing_notes_and_before_creating_draft(fake, capsys, monkeypatch, name):
    extract_notes = release_flow.ReleaseFlow.notes

    def change_evidence(flow):
        notes = extract_notes(flow)
        file = flow.evidence_dir / name
        if file.is_file():
            file.write_bytes(file.read_bytes() + b" ")
        return notes

    monkeypatch.setattr(release_flow.ReleaseFlow, "notes", change_evidence)
    assert fake.invoke("--ci-first") == 1
    output = capsys.readouterr()
    assert f"SHA-256 mismatch for {name}" in output.err
    assert PUBLISH not in output.out
    assert not fake.called("gh", "release", "create")


def test_existing_lock_refuses_before_touching_evidence_and_explains_recovery(fake, capsys):
    fake.lock_path.parent.mkdir(parents=True)
    fake.lock_path.write_bytes(b"54321\n")
    assert fake.invoke() == 1
    output = capsys.readouterr()
    assert str(fake.lock_path) in output.err
    assert "PID 54321" in output.err
    assert "delete that file and retry" in output.err
    assert fake.lock_path.read_bytes() == b"54321\n"
    assert not fake.calls
    assert not fake.evidence.exists()


def test_lock_records_current_pid_and_tolerates_a_missing_file_on_cleanup(fake):
    flow = release_flow.ReleaseFlow(VERSION, fake.root, fake, fake.sleeps.append)
    with flow.lock():
        assert flow.lock_path.read_text(encoding="ascii").strip() == str(os.getpid())
        flow.lock_path.unlink()
    assert not flow.lock_path.exists()


@pytest.mark.parametrize("resume", [False, True])
def test_remote_tag_appearing_after_fetch_blocks_manual_publish(fake, capsys, resume):
    if resume:
        assert fake.invoke("--ci-first") == 0
        capsys.readouterr()
        fake.calls.clear()
    fake.remote_tag_sha = SHA
    fake.remote_tag_on_read = fake.remote_tag_reads + 3
    assert fake.invoke("--resume" if resume else "--ci-first") == 1
    output = capsys.readouterr()
    assert f"remote tag {TAG} already exists" in output.err
    assert PUBLISH not in output.out
    assert len(fake.called("git", "ls-remote")) == 3
    assert fake.calls[-1] == ("git", "ls-remote", "--tags", "origin", f"refs/tags/{TAG}")
    assert not fake.lock_path.exists()


def test_main_moving_before_draft_creation_is_refused(fake, capsys):
    fake.move_on_fetch = 4
    assert fake.invoke("--ci-first") == 1
    output = capsys.readouterr()
    assert "origin/main" in output.err
    assert PUBLISH not in output.out
    assert not fake.called("gh", "release", "create")


class AbortValidation(BaseException):
    pass


@pytest.mark.parametrize("exception", [KeyboardInterrupt, AbortValidation])
@pytest.mark.parametrize("windows", [True, False], ids=["windows", "posix"])
def test_parallel_interruption_stops_live_tree_saves_state_and_releases_lock(fake, capsys, monkeypatch, exception, windows):
    fake.hold_live = True
    fake.raise_while_polling = exception()
    fake.live_runner = release_flow.run
    signalled = []
    sigkill = getattr(release_flow.signal, "SIGKILL", 9)
    new_process_group = 0x200

    def popen(cmd, *, cwd, stdin, stdout, stderr, start_new_session, creationflags):
        assert cmd == [sys.executable, "-m", "tools.live_gate", "--yes", "--checkpoint", str(fake.checkpoint)]
        assert cwd == fake.root
        assert stdin == subprocess.DEVNULL
        assert stderr == subprocess.STDOUT
        assert start_new_session is not windows
        assert creationflags == (new_process_group if windows else 0)
        assert not stdout.closed
        fake.live_stream = stdout
        return FakeLive(fake)

    def killpg(pid, sig):
        assert fake.live_stream.closed
        signalled.append((pid, sig))
        fake.live_process.stop()

    monkeypatch.setattr(release_flow, "is_windows", lambda: windows)
    monkeypatch.setattr(release_flow.subprocess, "Popen", popen)
    monkeypatch.setattr(release_flow.subprocess, "CREATE_NEW_PROCESS_GROUP", new_process_group, raising=False)
    monkeypatch.setattr(release_flow.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(release_flow.signal, "SIGKILL", sigkill, raising=False)
    assert fake.invoke() == 1
    assert fake.live_process.terminated and fake.live_process.reaped
    assert fake.live_stream.closed
    assert fake.called("taskkill") == ([("taskkill", "/PID", str(FakeLive.pid), "/T", "/F")] if windows else [])
    assert signalled == ([] if windows else [(FakeLive.pid, sigkill)])
    assert fake.state()["live_status"] == "failed"
    assert fake.state()["evidence_sha256"] == {}
    assert not fake.lock_path.exists()
    output = capsys.readouterr()
    assert "interrupted" in output.err
    assert PUBLISH not in output.out
    assert not fake.called("gh", "release", "create")


def test_interrupt_during_timed_live_wait_after_green_ci_stops_the_tree(fake, capsys):
    fake.hold_live = True
    fake.wait_errors = [subprocess.TimeoutExpired("live_gate", release_flow.LIVE_WAIT_SECONDS), KeyboardInterrupt()]
    assert fake.invoke("--ci-first") == 1
    output = capsys.readouterr()
    assert "is green" in output.out
    assert "interrupted" in output.err
    assert PUBLISH not in output.out
    assert fake.wait_timeouts == [release_flow.LIVE_WAIT_SECONDS] * 3
    assert fake.live_process.terminated and fake.live_process.reaped
    assert fake.state()["live_status"] == "failed"
    assert not fake.lock_path.exists()


@pytest.mark.parametrize("child_exited", [True, False])
def test_windows_taskkill_128_is_tolerated_only_if_child_exited(fake, capsys, child_exited):
    fake.hold_live = True
    fake.raise_while_polling = KeyboardInterrupt()
    fake.taskkill_exit = 128
    fake.exit_during_taskkill = child_exited
    assert fake.invoke() == 1
    output = capsys.readouterr()
    assert ("interrupted" in output.err) is child_exited
    assert ("exited 128" in output.err) is not child_exited
    assert PUBLISH not in output.out
    assert fake.live_process.reaped
    assert fake.state()["live_status"] == "failed"
    # taskkill reporting an error leaves it unknown whether grandchildren of the gate are gone: the lock stays then.
    assert fake.lock_path.exists()  # tree termination unconfirmed (128 included): descendants may still run


@pytest.mark.parametrize("windows", [True, False], ids=["taskkill-fails", "signal-fails"])
def test_shutdown_deadline_retains_lock_when_live_gate_is_still_running(fake, capsys, monkeypatch, windows):
    fake.hold_live = True
    fake.raise_while_polling = KeyboardInterrupt()
    fake.live_never_exits = True
    fake.taskkill_exit = 128
    signals = []

    def killpg(pid, sig):
        signals.append(pid)
        raise PermissionError("signal rejected")

    monkeypatch.setattr(release_flow, "is_windows", lambda: windows)
    monkeypatch.setattr(release_flow.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(release_flow.signal, "SIGKILL", 9, raising=False)
    assert fake.invoke() == 1
    assert bool(fake.called("taskkill")) is windows
    assert signals == ([] if windows else [FakeLive.pid])
    assert fake.clock_seconds == release_flow.LIVE_STOP_SECONDS
    assert all(0 < timeout <= release_flow.LIVE_WAIT_SECONDS for timeout in fake.wait_timeouts)
    assert not fake.live_process.reaped
    assert fake.state()["live_status"] == "failed"
    assert fake.lock_path.is_file()
    output = capsys.readouterr()
    assert f"ERROR: Live gate PID {FakeLive.pid} is still running" in output.err
    assert "Provider quota may still be spent" in output.err
    assert str(fake.lock_path) in output.err
    assert PUBLISH not in output.out

    fake.calls.clear()
    assert fake.invoke("--resume") == 1
    assert "release lock already exists" in capsys.readouterr().err
    assert not fake.calls


@pytest.mark.parametrize("flags", [(), ("--resume",), ("--ci-first",)])
def test_dry_run_executes_nothing(fake, capsys, flags):
    assert fake.invoke("--dry-run", *flags) == 0
    assert not fake.calls
    assert not fake.evidence.exists()
    assert not fake.state_path.exists()
    assert not fake.lock_path.exists()
    assert "Publishing is manual" in capsys.readouterr().out


def test_ci_first_waits_for_all_jobs_before_starting_live_gate(fake):
    assert fake.invoke("--ci-first") == 0
    jobs_call, = [call for call in fake.called("gh", "api") if call[-1].endswith("/jobs")]
    live_call, = fake.called(sys.executable, "-m", "tools.live_gate")
    assert fake.calls.index(jobs_call) < fake.calls.index(live_call)
    download, = fake.called("gh", "run", "download")
    assert fake.calls.index(jobs_call) < fake.calls.index(download) < fake.calls.index(live_call)


@pytest.mark.parametrize("status", ["queued", "completed"])
def test_adopts_exact_main_push_without_dispatch(fake, status):
    fake.event = "push"
    fake.listed_runs = [{"id": RUN_ID, "event": "push", "head_sha": SHA, "head_branch": "main",
                         "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO}}]
    fake.run_overrides[1] = {"status": status}
    fake.push_visible_after = 1
    assert fake.invoke("--ci-first") == 0
    assert not fake.dispatched
    assert fake.discovery_reads == 2
    assert fake.state()["ci_run_id"] == RUN_ID
    assert fake.called("gh", "run", "download")


def test_dispatches_full_matrix_for_non_candidate_push(fake):
    fake.previous_version = VERSION
    fake.listed_runs = [{"id": RUN_ID + 1, "event": "push", "head_sha": SHA, "head_branch": "main",
                         "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
                         "path": ".github/workflows/ci.yml", "run_attempt": 1, "status": "in_progress"}]
    flow = release_flow.ReleaseFlow(VERSION, fake.root, fake, fake.sleeps.append)
    with flow.lock():
        flow.preflight(resume=False)
        assert flow.select_ci(None).ci_run_id == RUN_ID
    assert fake.dispatched
    assert fake.discovery_reads == 0


def test_explicit_recovery_run_reuses_verified_rerun(fake):
    fake.attempt = 2
    assert fake.invoke("--ci-first", "--ci-run-id", str(RUN_ID)) == 0
    assert not fake.dispatched
    assert fake.state()["initial_ci_attempt"] == 2


@pytest.mark.parametrize("bad_report", [False, True])
def test_failed_live_resume_keeps_run_and_completed_stage(fake, capsys, bad_report):
    fake.live_exit = 1
    fake.live_completed = 1
    assert fake.invoke("--ci-first") == 1
    first = fake.evidence / release_flow.JUNIT_NAMES[0]
    digest = live_gate.file_digest(first)
    fake.live_exit = 0
    fake.live_completed = None
    fake.calls.clear()
    if bad_report:
        fake.bad_ci_report = matrix_junit_paths(Path("."))[-1].name
    assert fake.invoke("--resume") == int(bad_report)
    assert not fake.called("gh", "workflow", "run")
    assert live_gate.file_digest(first) == digest
    assert fake.live_stages_run == list(release_flow.JUNIT_NAMES[:1] if bad_report else release_flow.JUNIT_NAMES)
    output = capsys.readouterr()
    if bad_report:
        assert "missing" in output.err and fake.bad_ci_report in output.err
        assert not fake.called(sys.executable, "-m", "tools.live_gate")
        assert not fake.called("gh", "release", "create")
    else:
        assert fake.resuming_live
        assert PUBLISH in output.out


def test_invalid_version_is_usage_error(fake):
    with pytest.raises(SystemExit) as exc:
        release_flow.main(["run", "--version", "v1.2.3"], runner=fake, root=fake.root)
    assert exc.value.code == 2
    assert not fake.calls


def test_resume_and_ci_first_are_mutually_exclusive(fake, capsys):
    with pytest.raises(SystemExit) as exc:
        fake.invoke("--resume", "--ci-first")
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert "--ci-first" in output.err and "--resume" in output.err and "not allowed" in output.err
    assert not fake.calls
    assert not fake.lock_path.exists()


def test_interrupt_during_launch_still_stops_the_gate_and_releases_the_lock(fake, capsys):
    fake.hold_live = True
    fake.interrupt_at_launch = True
    assert fake.invoke("--ci-first") == 1
    output = capsys.readouterr()
    assert "interrupted" in output.err
    assert fake.called("taskkill")  # the just-launched gate was registered and its tree stopped
    assert fake.live_process.terminated or fake.live_process.returncode is not None
    assert fake.state()["live_status"] == "failed"
    assert not fake.lock_path.exists()
    assert PUBLISH not in output.out
