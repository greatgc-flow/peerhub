"""Run candidate CI and local live validation, then prepare a GitHub release draft."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol
from xml.etree.ElementTree import ParseError

from tools.live_gate import EVIDENCE_DIR, ROOT, STAGES
from tools.release_ci import REQUIRED_JOBS
from tools.release_evidence import _bound_props, source_version
from tools.release_notes import release_notes

WORKFLOW = "ci.yml"
EVENT = "workflow_dispatch"
BRANCH = "main"
JUNIT_NAMES = tuple(name for _, _, name in STAGES)
POLL_SECONDS = 10
LIVE_WAIT_SECONDS = 1
LIVE_STOP_SECONDS = 30
RETRY_POLLS = 18


def retry_is_safe(jobs: dict[str, str]) -> bool:
    """Recognize the matrix cancellation eligible for a failed-only rerun."""
    cells = {jobs.get(name) for name in REQUIRED_JOBS - {"ci-pass"}}
    return ("cancelled" in cells and cells <= {"success", "cancelled", "skipped"}
            and jobs.get("ci-pass") in {"failure", "success", "skipped"}
            and all(conclusion in {"success", "skipped"}
                    for name, conclusion in jobs.items() if name not in REQUIRED_JOBS))


class FlowError(Exception):
    pass


class LiveProcess(Protocol):
    @property
    def pid(self) -> int: ...
    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...


def wait_for_live(live: LiveProcess) -> int:
    while True:
        try:
            return live.wait(timeout=LIVE_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            continue


CommandResult = subprocess.CompletedProcess[bytes] | LiveProcess


class Runner(Protocol):
    def __call__(self, cmd: list[str], *, cwd: Path, log: Path | None = None,
                 timeout: float | None = None) -> CommandResult: ...


def is_windows() -> bool:
    return sys.platform == "win32"


def run(cmd: list[str], *, cwd: Path, log: Path | None = None, timeout: float | None = None) -> CommandResult:
    if log is None:
        return subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    windows = is_windows()
    # Keep the gate alive through a console Ctrl+C so the caller can stop its tree by PID.
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if windows else 0
    with log.open("wb") as stream:
        return subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stream,
                                stderr=subprocess.STDOUT, creationflags=flags, start_new_session=not windows)


@dataclass
class State:
    head: str
    ci_run_id: int
    live_status: str = "pending"
    evidence_sha256: dict[str, str] = field(default_factory=dict)
    retry_requested: bool = False


class ReleaseFlow:
    def __init__(self, version: str, root: Path, runner: Runner, sleep: Callable[[float], None],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.version = version
        self.tag = f"v{version}"
        self.root = root
        self.evidence_dir = root / EVIDENCE_DIR.relative_to(ROOT)
        self.state_path = self.evidence_dir.with_name("release-flow") / f"state-{version}.json"
        self.lock_path = self.state_path.with_name(f"lock-{version}")
        self.live_log = self.evidence_dir / "release-flow-live.log"
        self.run = runner
        self.sleep = sleep
        self.clock = clock
        self.keep_lock = False
        self.live_process: LiveProcess | None = None  # registered the moment the gate is launched
        self.repo: str
        self.head: str

    @staticmethod
    def checked(result: CommandResult) -> bytes:
        if not isinstance(result, subprocess.CompletedProcess):
            raise TypeError("expected a completed command")
        if result.returncode:
            detail = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace").strip()
            raise FlowError(f"{' '.join(result.args)} exited {result.returncode}" + (f": {detail}" if detail else ""))
        return result.stdout

    def git(self, *args: str) -> str:
        return self.checked(self.run(["git", *args], cwd=self.root)).decode("utf-8", errors="replace").strip()

    def gh(self, *args: str, cwd: Path | None = None) -> CommandResult:
        return self.run(["gh", *args, "--repo", f"github.com/{self.repo}"], cwd=cwd or self.root)

    def api_bytes(self, endpoint: str, *flags: str) -> bytes:
        return self.checked(self.run(
            ["gh", "api", "--hostname", "github.com", *flags, f"repos/{self.repo}/{endpoint}"], cwd=self.root))

    def api(self, endpoint: str, *flags: str) -> Any:
        return json.loads(self.api_bytes(endpoint, *flags))

    def check_checkout(self) -> None:
        if self.git("status", "--porcelain", "--untracked-files=all"):
            raise FlowError("working tree is dirty")
        if (current := self.git("rev-parse", "HEAD")) != self.head:
            raise FlowError(f"checkout HEAD changed to {current}; expected {self.head}")

    def fetch_main(self) -> None:
        self.git("fetch", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}", "--tags")

    def check_main(self) -> None:
        # The full ref name: a local branch or tag called origin/main must not shadow the fetched remote-tracking ref.
        ref = f"refs/remotes/origin/{BRANCH}"
        if (remote := self.git("rev-parse", "--verify", f"{ref}^{{commit}}")) != self.head:
            raise FlowError(f"origin/{BRANCH} is {remote}; expected HEAD {self.head}")

    def release(self) -> dict[str, Any] | None:
        pages = self.api("releases", "--paginate", "--slurp")
        matches = [release for page in pages for release in page if release["tag_name"] == self.tag]
        if len(matches) > 1:
            raise FlowError(f"multiple GitHub releases match {self.tag}")
        return matches[0] if matches else None

    def check_remote_tag(self) -> None:
        if self.git("ls-remote", "--tags", "origin", f"refs/tags/{self.tag}"):
            raise FlowError(f"remote tag {self.tag} already exists on origin")

    def check_existing(self, resume: bool) -> dict[str, Any] | None:
        if self.git("tag", "--list", self.tag):
            target = self.git("rev-parse", "--verify", f"refs/tags/{self.tag}^{{commit}}")
            if target != self.head:
                raise FlowError(f"tag {self.tag} resolves to {target}; expected HEAD {self.head}")
            raise FlowError(f"tag {self.tag} already exists")
        self.check_remote_tag()
        draft = self.release()
        if draft is not None:
            if not resume or draft.get("draft") is not True:
                raise FlowError(f"release {self.tag} already exists")
            if draft.get("target_commitish") != self.head:
                raise FlowError(f"draft {self.tag} target {draft.get('target_commitish')!r} differs from HEAD {self.head}")
        return draft

    def preflight(self, resume: bool) -> None:
        self.fetch_main()
        self.head = self.git("rev-parse", "HEAD")
        if not re.fullmatch(r"[0-9a-f]{40}", self.head):
            raise FlowError(f"expected a full HEAD SHA, got {self.head!r}")
        self.check_checkout()
        self.check_main()
        if (actual := source_version(self.root)) != self.version:
            raise FlowError(f"peerhub/_version.py is {actual}. Expected {self.version}. "
                            "Include the version bump, STATUS heading and changelog in the last feature PR for the release.")
        self.notes()
        remote = self.git("remote", "get-url", "origin")
        match = re.fullmatch(r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
                             r"([\w.-]+/[\w.-]+?)(?:\.git)?/?", remote)
        if not match:
            raise FlowError("origin must point to a GitHub repository")
        self.repo = match[1]
        self.check_existing(resume)

    def notes(self) -> str:
        return release_notes((self.root / "CHANGELOG.md").read_text(encoding="utf-8"), self.version)

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError as exc:
            pid = ""
            with suppress(OSError):
                pid = self.lock_path.read_text(encoding="ascii", errors="replace").strip()
            owner = pid if pid.isascii() and pid.isdecimal() else "unknown"
            raise FlowError(f"release lock already exists at {self.lock_path} (PID {owner}). "
                            "If the previous flow and its live gate have stopped, delete that file and retry.") from exc
        try:
            with os.fdopen(fd, "w", encoding="ascii") as lock:
                lock.write(f"{os.getpid()}\n")
            yield
        finally:
            if not self.keep_lock:
                self.lock_path.unlink(missing_ok=True)

    def save_state(self, state: State) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(dir=self.state_path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write((json.dumps(asdict(state), indent=2) + "\n").encode("utf-8"))
            temporary.replace(self.state_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def ci_run(self, run_id: int) -> dict[str, Any]:
        row = self.api(f"actions/runs/{run_id}")
        if row["head_sha"] != self.head:
            raise FlowError(f"CI run {run_id} head SHA {row['head_sha']} differs from HEAD {self.head}")
        if row["event"] != EVENT:
            raise FlowError(f"CI run {run_id} event {row['event']!r}; expected {EVENT}")
        if (row["id"] != run_id or row["head_branch"] != BRANCH
                or row["path"].partition("@")[0] != f".github/workflows/{WORKFLOW}"
                or any((row.get(key) or {}).get("full_name", "").casefold() != self.repo.casefold()
                       for key in ("repository", "head_repository"))):
            raise FlowError(f"CI run {run_id} identity mismatch for {self.repo} {WORKFLOW} on {BRANCH}")
        return row

    def dispatch(self) -> int:
        self.fetch_main()
        self.check_main()
        output = self.checked(self.gh("workflow", "run", WORKFLOW, "--ref", BRANCH, "-f", "full_matrix=true"))
        lines = output.decode("utf-8", errors="replace").replace("\r", "")
        urls = re.findall(rf"^https://github\.com/{re.escape(self.repo)}/actions/runs/([1-9][0-9]*)[ \t]*$",
                          lines.strip(), re.IGNORECASE | re.MULTILINE)
        if len(urls) != 1:
            raise FlowError(f"gh workflow run did not return a single CI run URL for {self.repo}. "
                            "Check whether it dispatched and use a GitHub CLI that prints the run URL.")
        run_id = int(urls[0])
        self.fetch_main()
        self.check_main()
        self.ci_run(run_id)
        return run_id

    def jobs(self, run_id: int, attempt: int) -> dict[str, str]:
        latest: dict[str, str] = {}
        # A failed-only rerun keeps successful jobs from previous attempts.
        for number in range(attempt, 0, -1):
            pages = self.api(f"actions/runs/{run_id}/attempts/{number}/jobs", "--paginate", "--slurp")
            seen: set[str] = set()
            for page in pages:
                for job in page["jobs"]:
                    name = job.get("name")
                    conclusion = job.get("conclusion")
                    if (not isinstance(name, str) or not isinstance(conclusion, str) or not conclusion
                            or name in seen or job.get("run_id") != run_id or job.get("head_sha") != self.head
                            or job.get("status") != "completed"):
                        raise FlowError(f"ambiguous result for CI job {name!r} in run {run_id} attempt {number}")
                    seen.add(name)
                    latest.setdefault(name, conclusion)
            if REQUIRED_JOBS <= latest.keys():
                break
        return latest

    def wait_for_ci(self, state: State, check_live: Callable[[], None] | None = None) -> int:
        run_id = state.ci_run_id
        waiting_for_retry = 0
        while True:
            if check_live is not None:
                check_live()
            row = self.ci_run(run_id)
            attempt = row["run_attempt"]
            if type(attempt) is not int or attempt < 1:
                raise FlowError(f"CI run {run_id} has invalid attempt {attempt!r}")
            if attempt > 2 or (attempt == 2 and not state.retry_requested):
                raise FlowError(f"CI run {run_id} attempt {attempt} not started by this flow")
            if state.retry_requested and attempt == 1:
                # The API can still show attempt 1 just after accepting the rerun.
                waiting_for_retry += 1
                if waiting_for_retry > RETRY_POLLS:
                    raise FlowError(f"CI run {run_id} never started the requested retry")
            elif row["status"] == "completed":
                jobs = self.jobs(run_id, attempt)
                missing = REQUIRED_JOBS - jobs.keys()
                green = not missing and all(jobs[name] == "success" for name in REQUIRED_JOBS)
                if row["conclusion"] == "success" and green:
                    print(f"CI run {run_id} is green on {self.head}.", flush=True)
                    return attempt
                problems = [f"{name}={conclusion}" for name, conclusion in sorted(jobs.items())
                            if conclusion != "success"]
                problems.extend(f"{name}=missing" for name in sorted(missing))
                detail = ", ".join(problems) or "all reported jobs succeeded"
                if (row["conclusion"] not in {"failure", "cancelled"} or attempt != 1 or state.retry_requested
                        or missing or not retry_is_safe(jobs)):
                    raise FlowError(f"CI run {run_id} attempt {attempt} concluded {row['conclusion']}: {detail}")
                latest = self.ci_run(run_id)
                if latest["run_attempt"] != 1 or latest["status"] != "completed":
                    raise FlowError(f"CI run {run_id} changed before retry: attempt {latest['run_attempt']}, "
                                    f"status {latest['status']}")
                print(f"WARNING: rerunning failed jobs ONCE for CI run {run_id}: {detail}", flush=True)
                self.checked(self.gh("run", "rerun", str(run_id), "--failed"))
                state.retry_requested = True
                self.save_state(state)
            elif row["status"] not in {"queued", "in_progress", "requested", "waiting", "pending"}:
                raise FlowError(f"CI run {run_id} has unknown status {row['status']!r}")
            self.sleep(POLL_SECONDS)

    def is_evidence_json(self, name: str) -> bool:
        return re.fullmatch(rf"[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}-{re.escape(self.tag)}\.json", name) is not None

    def digest(self, name: str) -> str:
        path = self.evidence_dir / name
        if not path.is_file():
            raise FlowError(f"evidence file missing: {name}")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def json_names(self) -> list[str]:
        return sorted(path.name for path in self.evidence_dir.glob(f"*-{self.tag}.json")
                      if path.is_file() and self.is_evidence_json(path.name))

    def start_live(self) -> tuple[LiveProcess, dict[str, str]]:
        self.check_checkout()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        before = {name: self.digest(name) for name in (*JUNIT_NAMES, *self.json_names())
                  if (self.evidence_dir / name).is_file()}
        print(f"Running the live gate (spends provider quota). Log: {self.live_log}", flush=True)
        # Ctrl+C between Popen and the registration would leave the gate running with no owner: hold it until the
        # child is registered for cleanup, then raise it.
        interrupted: list[bool] = []
        main = threading.current_thread() is threading.main_thread()
        previous = signal.getsignal(signal.SIGINT) if main else None
        if main:
            signal.signal(signal.SIGINT, lambda *_: interrupted.append(True))
        try:
            process = self.run([sys.executable, "-m", "tools.live_gate", "--yes"], cwd=self.root, log=self.live_log)
            if isinstance(process, subprocess.CompletedProcess):
                raise TypeError("expected an asynchronous live-gate process")
            self.live_process = process
        finally:
            if main:
                signal.signal(signal.SIGINT, previous)  # type: ignore[arg-type]
        if interrupted:
            raise KeyboardInterrupt
        return process, before

    def stop_live(self, live: LiveProcess) -> None:
        self.keep_lock = True
        terminate_failed = False
        try:
            if is_windows():
                if live.poll() is None:
                    # Unconfirmed tree termination (any error, a timeout, or "not found" after the parent exited) keeps
                    # the lock: descendants of the gate could still be spending provider quota.
                    terminate_failed = True
                    result = self.run(["taskkill", "/PID", str(live.pid), "/T", "/F"],
                                      cwd=self.root, timeout=LIVE_STOP_SECONDS)
                    if (isinstance(result, subprocess.CompletedProcess) and result.returncode == 128
                            and live.poll() is not None):
                        return
                    self.checked(result)
                    terminate_failed = False
            else:
                with suppress(ProcessLookupError):
                    os.killpg(live.pid, signal.SIGKILL)
        finally:
            deadline = self.clock() + LIVE_STOP_SECONDS
            while True:
                remaining = deadline - self.clock()
                if remaining <= 0:
                    if live.poll() is None:
                        raise FlowError(f"ERROR: Live gate PID {live.pid} is still running. Provider quota may still be spent. "
                                        f"The lock at {self.lock_path} stays in place until the live gate has stopped.")
                    break
                try:
                    live.wait(timeout=min(LIVE_WAIT_SECONDS, remaining))
                except subprocess.TimeoutExpired:
                    continue
                break
            self.keep_lock = terminate_failed

    def live_evidence(self, before: dict[str, str], returncode: int) -> dict[str, str]:
        if returncode:
            raise FlowError(f"live gate exited {returncode}; see {self.live_log}")
        self.check_checkout()
        evidence: dict[str, str] = {}
        for name in JUNIT_NAMES:
            digest = self.digest(name)
            if digest == before.get(name):
                raise FlowError(f"live gate did not rewrite {name}")
            props = _bound_props(self.evidence_dir / name) or {}
            if props.get("source_revision") != self.head or props.get("gate") != "G3":
                raise FlowError(f"{name} must be stamped for HEAD {self.head} and gate G3")
            evidence[name] = digest
        # The live JSON has no revision field. Bind the JSON written during this run
        # to the same checkout as the three stamped reports.
        changed = {name: digest for name in self.json_names()
                   if (digest := self.digest(name)) != before.get(name)}
        if len(changed) != 1:
            names = ", ".join(changed) or "none"
            raise FlowError(f"expected one new or updated <date>-{self.tag}.json from the live gate; found {names}")
        return evidence | changed

    def check_hashes(self, state: State) -> list[str]:
        hashes = state.evidence_sha256
        if not isinstance(hashes, dict):
            raise FlowError("state evidence_sha256 must map evidence names to their SHA-256")
        json_names = [name for name in hashes if self.is_evidence_json(name)]
        if missing := set(JUNIT_NAMES) - hashes.keys():
            raise FlowError(f"state SHA-256 missing for {', '.join(sorted(missing))}")
        if len(json_names) != 1 or len(hashes) != len(JUNIT_NAMES) + 1:
            raise FlowError(f"state must contain SHA-256 for exactly one <date>-{self.tag}.json alongside the JUnit files")
        names = [*JUNIT_NAMES, *json_names]
        for name in names:
            if (actual := self.digest(name)) != hashes[name]:
                raise FlowError(f"SHA-256 mismatch for {name}: expected {hashes[name]}, got {actual}")
        return names

    def resume_state(self) -> State:
        state = State(**json.loads(self.state_path.read_text(encoding="utf-8")))
        if state.head != self.head:
            raise FlowError(f"state HEAD {state.head} differs from checkout HEAD {self.head}")
        if type(state.ci_run_id) is not int or state.ci_run_id < 1:
            raise FlowError(f"state CI run ID is invalid: {state.ci_run_id!r}")
        if type(state.retry_requested) is not bool:
            raise FlowError("state retry_requested must be true or false")
        self.ci_run(state.ci_run_id)
        if state.live_status != "passed":
            raise FlowError(f"state live gate status is {state.live_status!r}; expected 'passed'")
        self.check_hashes(state)
        return state

    def validate(self, state: State, ci_first: bool) -> int:
        attempt = self.wait_for_ci(state) if ci_first else None
        live: LiveProcess | None = None
        try:
            state.live_status = "running"
            self.save_state(state)
            process, before = self.start_live()
            live = process

            def collect(returncode: int) -> None:
                state.evidence_sha256 = self.live_evidence(before, returncode)
                state.live_status = "passed"
                self.save_state(state)

            def collect_if_done() -> None:
                if state.live_status == "running" and (code := process.poll()) is not None:
                    collect(code)

            if attempt is None:
                attempt = self.wait_for_ci(state, collect_if_done)
            if state.live_status == "running":
                collect(wait_for_live(process))
            return attempt
        except BaseException as exc:
            try:
                live = live or self.live_process
                if live is not None and state.live_status == "running":
                    self.stop_live(live)
            finally:
                if state.live_status == "running":
                    state.live_status = "failed"
                    state.evidence_sha256 = {}
                    self.save_state(state)
            if not isinstance(exc, (Exception, KeyboardInterrupt)):
                raise FlowError(f"live validation interrupted ({type(exc).__name__})") from exc
            raise

    def asset_mismatch(self, asset: dict[str, Any], digest: str) -> str | None:
        name = asset["name"]
        if asset.get("state") != "uploaded":
            return f"{name} has upload state {asset.get('state')!r}"
        size = (self.evidence_dir / name).stat().st_size
        remote_size = asset.get("size")
        if type(remote_size) is not int:
            raise FlowError(f"draft verification failed: {name} has invalid size {remote_size!r}")
        if remote_size != size:
            return f"{name} size mismatch (GitHub {remote_size}, local {size} bytes)"
        remote_digest = asset.get("digest")
        if remote_digest is not None and remote_digest != f"sha256:{digest}":
            return f"{name} SHA-256 digest mismatch"
        if remote_digest is None:
            asset_id = asset.get("id")
            if type(asset_id) is not int or asset_id < 1:
                raise FlowError(f"draft verification failed: {name} has invalid asset ID {asset_id!r}")
            data = self.api_bytes(f"releases/assets/{asset_id}", "-H", "Accept: application/octet-stream")
            if hashlib.sha256(data).hexdigest() != digest:
                return f"{name} downloaded SHA-256 mismatch"
        return None

    def inspect_draft(self, draft: dict[str, Any] | None, state: State, names: list[str],
                      *, repair: bool = False) -> tuple[list[str], list[str]]:
        if draft is None or draft.get("draft") is not True or draft.get("target_commitish") != self.head:
            raise FlowError(f"draft verification failed: {self.tag} must be a draft targeting HEAD {self.head}")
        assets = draft["assets"]
        asset_names = [asset["name"] for asset in assets]
        present = set(asset_names)
        missing = [name for name in names if name not in present]
        if len(present) != len(asset_names) or present - set(names) or (missing and not repair):
            raise FlowError(f"draft verification failed: expected assets {', '.join(names)}; found {', '.join(asset_names)}")
        corrupt = []
        for asset in assets:
            name = asset["name"]
            if mismatch := self.asset_mismatch(asset, state.evidence_sha256[name]):
                if not repair:
                    raise FlowError(f"draft verification failed: {mismatch}")
                corrupt.append(name)
        return missing, corrupt

    def repair_draft(self, draft: dict[str, Any], state: State, names: list[str]) -> None:
        missing, corrupt = self.inspect_draft(draft, state, names, repair=True)
        if missing:
            self.check_hashes(state)
            self.checked(self.gh("release", "upload", self.tag, *missing, cwd=self.evidence_dir))
        for name in corrupt:
            self.check_hashes(state)
            self.checked(self.gh("release", "upload", self.tag, name, "--clobber", cwd=self.evidence_dir))
        if missing or corrupt:
            self.inspect_draft(self.release(), state, names)

    def prepare_draft(self, state: State, resume: bool, attempt: int) -> None:
        self.fetch_main()
        self.check_checkout()
        self.check_main()
        current = self.ci_run(state.ci_run_id)
        if current["run_attempt"] != attempt or current["status"] != "completed" or current["conclusion"] != "success":
            raise FlowError(f"CI run {state.ci_run_id} changed after verification of attempt {attempt}")
        draft = self.check_existing(resume)
        if draft is None:
            (self.evidence_dir / "notes.md").write_bytes(self.notes().encode("utf-8"))
            names = self.check_hashes(state)
            self.checked(self.gh("release", "create", self.tag, "--draft", "--target", self.head,
                                 "--title", self.tag, "--notes-file", "notes.md", *names, cwd=self.evidence_dir))
            self.inspect_draft(self.release(), state, names)
        else:
            self.repair_draft(draft, state, self.check_hashes(state))
        self.check_remote_tag()
        print(f"Publish the verified draft manually:\ngh release edit {self.tag} --draft=false --repo github.com/{self.repo}")
        print(f"After the publish workflow succeeds, verify PyPI with a clean install of peerhub=={self.version}.")


def main(argv: list[str] | None = None, *, runner: Runner = run, root: Path = ROOT,
         sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True).add_parser("run")
    command.add_argument("--version", required=True, metavar="X.Y.Z")
    command.add_argument("--dry-run", action="store_true", help="print the plan without executing it")
    mode = command.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true", help="reuse recorded CI and evidence and repair partial draft uploads")
    mode.add_argument("--ci-first", action="store_true", help="wait for green matrix CI before spending provider quota")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
        command.error("--version must be X.Y.Z")
    if args.dry_run:
        print(f"1. Fetch origin and preflight v{args.version} on clean origin/{BRANCH}.")
        if args.resume:
            print("2. Check the recorded SHA, CI run, and SHA-256 of the four live evidence files.")
            print("3. Wait for the recorded CI matrix.")
        else:
            print(f"2. Dispatch {WORKFLOW} with full_matrix=true and bind the run to HEAD.")
            print("3. Wait for green CI, then run the live gate."
                  if args.ci_first else "3. Wait for CI while the local live gate runs.")
        print("4. Prepare and verify the draft. Publishing is manual.")
        return 0
    flow = ReleaseFlow(args.version, root, runner, sleep, clock)
    try:
        with flow.lock():
            flow.preflight(args.resume)
            if args.resume:
                state = flow.resume_state()
                attempt = flow.wait_for_ci(state)
            else:
                state = State(flow.head, flow.dispatch())
                flow.save_state(state)
                print(f"Bound CI run {state.ci_run_id} to {state.head}.", flush=True)
                attempt = flow.validate(state, args.ci_first)
            flow.prepare_draft(state, args.resume, attempt)
    except (FlowError, OSError, ValueError, KeyError, TypeError, AttributeError, ParseError, subprocess.SubprocessError) as exc:
        print(f"Release flow stopped: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Release flow interrupted.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
