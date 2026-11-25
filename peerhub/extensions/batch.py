"""Flat `peerhub ask` batches: one OS subprocess per task through the public CLI.

Identity, claims and idempotency stay in `ask`; this module validates and describes a manifest, derives stable stream/request ids,
runs at most N workers, classifies each worker's outcome (default UNCERTAIN) and cancels process trees.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, cast

from peerhub.core.load_limits import LOAD_WARNING_MESSAGES, ResourceSample, load_warnings as load_warnings, read_resources
from peerhub.core.peer_kinds import BUILTIN_PEER_CHOICES
from peerhub.core.shell import format_shell_command
from peerhub.core.workspace import store_scratch_dir
from peerhub.extensions.process_tree import creation_ticks as _creation_time, kill_process_tree

DEFAULT_TIMEOUT_S = 600.0
RESOURCE_POLL_SECONDS = 3.0
STDOUT_CAP = 4 * 1024 * 1024
STDERR_CAP = 64 * 1024
PROMPT_FILE_CAP = 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_PROMPT_KEY = "prompt"
_PROMPT_FILE_KEY = "prompt_file"
_TASK_FIELDS = {
    "task_id": "review",
    "peer": "cc",
    _PROMPT_KEY: "Review the code",
    _PROMPT_FILE_KEY: "prompt.txt",
    "workspace": ".",
    "profile": "cc.standard",
    "writable": False,
    "timeout_s": DEFAULT_TIMEOUT_S,
}
_KEYS = _TASK_FIELDS.keys()
_PROMPT_CHOICE = f"exactly one of {_PROMPT_KEY} or {_PROMPT_FILE_KEY}"
_WARNING_MESSAGES = {
    **LOAD_WARNING_MESSAGES,
    "workspace_overlap": "writable tasks {first} and {second} have {relationship} workspaces",
}
DEFAULT_PROFILE_DISPLAY = "(peer default)"
RESOLVED_DEFAULT_PROFILE_DISPLAY = "(default: {profile})"
REPLAY_LINE = "replay: {settled} settled, {uncertain} uncertain"
_NOT_EXECUTED = "not_executed"
_QUOTA_REFUSAL_REASONS = {f"{_NOT_EXECUTED}:{reason}" for reason in ("quota_exhausted", "usage_limit")}
_QUOTA_REFUSED = "quota_refused"
_OUTCOME_BY_STATUS = {
    "COMPLETED": "ok",
    "FAILED": "failed",
    "BLOCKED": "failed",
    "UNCERTAIN": "uncertain",
    "UNVERIFIED": "uncertain",
}


class ManifestError(ValueError):
    """The manifest or batch options are invalid; nothing was launched."""


@dataclass(frozen=True)
class Task:
    task_id: str
    peer: str
    prompt: str
    workspace: Path | None
    profile: str | None
    writable: bool
    timeout_s: float


ArgvBuilder = Callable[..., Sequence[str]]


def _check_id(label: str, value: object) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None or not value.isascii():
        raise ManifestError(f"{label} must match [A-Za-z0-9._-]{{1,64}}")
    return value


def derive_ids(batch_id: str, task_id: str) -> tuple[str, str]:
    """(stream, request_id): pure functions of (batch_id, task_id); NUL-separated so tuples cannot collide."""
    _check_id("batch_id", batch_id)
    _check_id("task_id", task_id)
    stream = hashlib.sha256(f"s\0{batch_id}\0{task_id}".encode()).hexdigest()[:20]
    request = hashlib.sha256(f"r\0{batch_id}\0{task_id}".encode()).hexdigest()[:20]
    return f"batch-{stream}", f"b-{request}"


def check_max_parallel(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ManifestError("--max-parallel must be an integer >= 1")
    return value


def manifest_help() -> str:
    example = {key: _TASK_FIELDS[key] for key in _KEYS if key != _PROMPT_FILE_KEY}
    return "\n".join((
        "Manifest example (task_id and peer required):",
        json.dumps({"tasks": [example]}, indent=2),
        f"or {_PROMPT_FILE_KEY} instead of {_PROMPT_KEY}",
        f"Dry-run warning kinds: {', '.join(_WARNING_MESSAGES)}",
        "--responses prints each completed task's reply in full (can be large).",
        "Reusing a --batch-id replays settled tasks. Reconcile uncertain rows first.",
    ))


def load_manifest(path: str | Path, *, batch_id: str) -> list[Task]:
    path = Path(path)
    try:
        doc: Any = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(doc, dict) or set(cast(dict[str, Any], doc)) != {"tasks"} or not isinstance(doc["tasks"], list):
        raise ManifestError('manifest must be {"tasks": [...]}')
    rows = cast(list[Any], doc["tasks"])
    if not rows:
        raise ManifestError("manifest needs at least one task")
    tasks: list[Task] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not set(cast(dict[str, Any], row)) <= _KEYS:
            raise ManifestError(f"task has unknown keys: {sorted(set(cast(dict[str, Any], row)) - _KEYS) if isinstance(row, dict) else row!r}")
        row = cast(dict[str, Any], row)
        tid = _check_id("task_id", row.get("task_id"))
        derive_ids(batch_id, tid)
        if tid in seen:
            raise ManifestError(f"duplicate task_id {tid!r}")
        seen.add(tid)
        peer = row.get("peer")
        if not isinstance(peer, str) or not peer.strip():
            raise ManifestError(f"task {tid}: peer is required")
        if (_PROMPT_KEY in row) == (_PROMPT_FILE_KEY in row):
            raise ManifestError(f"task {tid}: give {_PROMPT_CHOICE}")
        if _PROMPT_KEY in row:
            prompt = row[_PROMPT_KEY]
        else:
            try:
                file = Path(row[_PROMPT_FILE_KEY])
                file = file if file.is_absolute() else path.parent / file
                if file.stat().st_size > PROMPT_FILE_CAP:
                    raise ManifestError(f"task {tid}: {_PROMPT_FILE_KEY} exceeds {PROMPT_FILE_CAP} bytes")
                prompt = file.read_text(encoding="utf-8-sig")
            except (OSError, ValueError, TypeError) as exc:
                raise ManifestError(f"task {tid}: cannot read {_PROMPT_FILE_KEY}: {exc}") from exc
        if not isinstance(prompt, str) or not prompt.strip():
            raise ManifestError(f"task {tid}: {_PROMPT_KEY} must be non-empty text")
        timeout = row.get("timeout_s", DEFAULT_TIMEOUT_S)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ManifestError(f"task {tid}: timeout_s must be a finite positive number")
        writable = row.get("writable", False)
        if not isinstance(writable, bool):
            raise ManifestError(f"task {tid}: writable must be true or false")
        profile = row.get("profile")
        if profile is not None and (not isinstance(profile, str) or not profile.strip()):
            raise ManifestError(f"task {tid}: profile must be a non-empty string")
        ws = row.get("workspace")
        if ws is not None and (not isinstance(ws, str) or not ws.strip()):
            raise ManifestError(f"task {tid}: workspace must be a non-empty string")
        tasks.append(Task(tid, peer, prompt, Path(ws) if ws else None, profile, writable, float(timeout)))
    return tasks


def _real(path: Path | None) -> str:
    return os.path.normcase(os.path.realpath(path if path is not None else Path.cwd()))


def _related(a: str, b: str) -> bool:
    return a == b or a.startswith(b.rstrip("\\/") + os.sep) or b.startswith(a.rstrip("\\/") + os.sep)


def validate_tasks(tasks: Sequence[Task], *, kind_of: Callable[[str], str | None],
                   warnings: list[str] | None = None) -> dict[str, str]:
    """Validate the entire manifest and return the workspace paths used for overlap checks."""
    spaces: list[tuple[Task, str, bool]] = []
    for t in tasks:
        kind = kind_of(t.peer)
        if kind is None:
            raise ManifestError(f"task {t.task_id}: unknown peer {t.peer!r} (register it with --adapter {BUILTIN_PEER_CHOICES})")
        if t.writable and kind in ("ca", "oa", "ga"):
            raise ManifestError(f"task {t.task_id}: {kind} has no workspace access; remove writable")
        if t.workspace is not None and not t.workspace.is_dir():
            raise ManifestError(f"task {t.task_id}: workspace {t.workspace} is not an existing directory")
        spaces.append((t, _real(t.workspace), t.writable or kind == "ag"))  # ag writes regardless of the flag (#140)
    workspaces = {task.task_id: path for task, path, _ in spaces}
    if not any(writable for _, _, writable in spaces):
        return workspaces
    first_overlap = None
    for i, (ta, pa, wa) in enumerate(spaces):
        for tb, pb, wb in spaces[i + 1:]:
            if not (wa or wb) or not _related(pa, pb):
                continue
            if first_overlap is None:
                first_overlap = (f"workspace overlap between tasks {ta.task_id} and {tb.task_id}: "
                                 "a writer may not share or nest with another task")
                if warnings is None:
                    raise ManifestError(first_overlap)
            if warnings is not None and wa and wb:
                relationship = "equal" if pa == pb else "nested"
                warnings.append(_WARNING_MESSAGES["workspace_overlap"].format(
                    first=ta.task_id, second=tb.task_id, relationship=relationship))
    if first_overlap is not None:
        raise ManifestError(first_overlap)
    return workspaces


def _parse_result(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    starts = [0] + [m.end() for m in re.finditer(r"\n(?=\{)", text)]
    decoder = json.JSONDecoder()
    for start in starts:
        try:
            obj, end = decoder.raw_decode(text[start:])
        except ValueError:
            continue
        if isinstance(obj, dict) and not text[start + end:].strip():
            return cast(dict[str, Any], obj)
    return None


def classify_worker(returncode: int | None, stdout: str, *, overflow: bool = False, killed: bool = False,
                    timed_out: bool = False) -> tuple[str, str | None]:
    """Default UNCERTAIN: only explicit positive fields in the ask JSON prove another outcome (spec B5)."""
    if overflow:
        return "UNCERTAIN", "output_overflow"
    if timed_out:
        return "UNCERTAIN", "timeout"
    if killed:
        return "UNCERTAIN", "killed"
    result = _parse_result(stdout)
    if result is None:
        return "UNCERTAIN", "no_result"
    guidance = result.get("guidance")
    status = result.get("status")
    if (isinstance(guidance, dict) and cast(dict[str, Any], guidance).get("code") == "ask_claim_held") or status == "pending_earlier_record":
        return "BLOCKED", "ask_claim_held" if status != "pending_earlier_record" else "pending_earlier_record"
    if status in ("delivered", "recovered_terminal"):
        if returncode != 0:
            return "UNCERTAIN", f"exit_{returncode}"
        if result.get("error") is not None:
            return "UNCERTAIN", "error_present"
        if result.get("certainty") != "TERMINAL":
            return "UNCERTAIN", f"certainty_{result.get('certainty')}"
        response = result.get("response")
        return ("COMPLETED", None) if isinstance(response, str) and response.strip() else ("UNVERIFIED", "empty_response")
    if status == "denied_empty":
        return "UNVERIFIED", "denied_empty"
    if result.get("certainty") == "NOT_STARTED":
        return "FAILED", "not_started"
    if status == _NOT_EXECUTED or result.get("task_outcome") == "NOT_EXECUTED":
        reason = result.get("reason")
        return "FAILED", f"{_NOT_EXECUTED}:{reason}" if isinstance(reason, str) and reason else _NOT_EXECUTED
    if status == "uncertain":
        return "UNCERTAIN", "uncertain"
    error = result.get("error")
    code = cast(dict[str, Any], error).get("code") if isinstance(error, dict) else None
    if status in ("truncated", "incomplete"):  # API peers (#378): the reply ended and is not usable
        return "FAILED", str(status)
    if code == "request_rejected":  # refused before anything was started
        return "FAILED", "request_rejected"
    return "UNCERTAIN", code if isinstance(code, str) and code else f"exit_{returncode}"


def exit_code(report: Mapping[str, Any]) -> int:
    return 0 if all(r["status"] == "COMPLETED" for r in report["tasks"]) else 1


def _outcome_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys((*_OUTCOME_BY_STATUS.values(), _QUOTA_REFUSED), 0)
    for row in rows:
        status = row["status"]
        outcome = _OUTCOME_BY_STATUS[status]
        if status == "FAILED" and row.get("reason") in _QUOTA_REFUSAL_REASONS:
            outcome = _QUOTA_REFUSED
        counts[outcome] += 1
    return counts


def default_argv_builder(t: Task, *, stream: str, request_id: str, db: str, query_file: Path) -> list[str]:
    argv = [sys.executable, "-m", "peerhub", "--db", os.path.abspath(db), "ask", t.peer, "--query-file", str(query_file),
            "--stream", stream, "--request-id", request_id, "--json", "--timeout-seconds", str(t.timeout_s)]
    if t.profile:
        argv += ["--profile", t.profile]
    if t.workspace is not None:
        argv += ["-w", str(t.workspace)]
    if t.writable:
        argv.append("--writable")
    return argv


# ---- process-tree containment ----------------------------------------------------------------------------------------
def _descendants(pid: int) -> list[tuple[int, int]]:
    """(pid, creation-time) of every live descendant, so a kill can be verified without trusting reused PIDs."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class ENTRY(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if snap in (None, wintypes.HANDLE(-1).value):
        return []
    children: dict[int, list[int]] = {}
    try:
        entry = ENTRY()
        entry.dwSize = ctypes.sizeof(ENTRY)
        ok = k32.Process32FirstW(wintypes.HANDLE(snap), ctypes.byref(entry))
        while ok:
            children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            ok = k32.Process32NextW(wintypes.HANDLE(snap), ctypes.byref(entry))
    finally:
        k32.CloseHandle(wintypes.HANDLE(snap))
    found: list[int] = []
    todo = [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            if child not in found and child != pid:
                found.append(child)
                todo.append(child)
    return [(p, _creation_time(p)) for p in found]


def _still_alive(pid: int, created: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        handle = k32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
                return False
        finally:
            k32.CloseHandle(handle)
        return _creation_time(pid) == created

    return False

def kill_tree_confirmed(proc: subprocess.Popen[Any], *, wait_s: float = 5.0) -> bool:
    """Kill the worker and everything below it; True only when the worker and every recorded descendant are gone."""
    victims = _descendants(proc.pid) if proc.poll() is None else []
    kill_process_tree(proc)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        gone = proc.poll() is not None and not any(_still_alive(p, c) for p, c in victims)
        if sys.platform != "win32":
            try:
                os.killpg(proc.pid, 0)
                gone = False
            except OSError:
                gone = proc.poll() is not None
        if gone:
            return True
        time.sleep(0.05)
    return False


def _drain(stream: Any, cap: int, sink: bytearray, flag: threading.Event | None) -> None:
    try:
        while chunk := stream.read(65536):
            room = cap - len(sink)
            if room > 0:
                sink.extend(chunk[:room])
            if len(chunk) > room and flag is not None:
                flag.set()
    except (OSError, ValueError):
        pass


def _launch_flags() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def run_batch(tasks: Sequence[Task], *, batch_id: str, max_parallel: int, db: str, argv_builder: ArgvBuilder = default_argv_builder,
              cancel: threading.Event | None = None, grace_s: float = 30.0, responses: bool = False,
              resource_reader: Callable[[], ResourceSample] | None = None) -> dict[str, Any]:
    check_max_parallel(max_parallel)
    cancel = cancel or threading.Event()
    resource_reader = resource_reader or read_resources
    rows: dict[str, dict[str, Any]] = {}

    def blocked(t: Task, reason: str) -> dict[str, Any]:
        stream, request = derive_ids(batch_id, t.task_id)
        return {"task_id": t.task_id, "peer": t.peer, "stream": stream, "status": "BLOCKED", "request_id": request,
                "elapsed_s": 0.0, "reason": reason}

    def work(t: Task, tmp: Path, launched: list[subprocess.Popen[Any]]) -> dict[str, Any]:
        stream, request = derive_ids(batch_id, t.task_id)
        row = {"task_id": t.task_id, "peer": t.peer, "stream": stream, "request_id": request}
        query = tmp / f"{t.task_id}.txt"
        try:
            query.write_text(t.prompt, encoding="utf-8")
        except OSError as exc:
            return {**row, "status": "FAILED", "reason": f"query_write_failed:{type(exc).__name__}", "elapsed_s": 0.0}
        started = time.monotonic()
        out, err = bytearray(), bytearray()
        overflow = threading.Event()
        argv = list(argv_builder(t, stream=stream, request_id=request, db=db, query_file=query))
        try:
            # #374: the child ask records which batch task started it (caller evidence); raw ids as the user gave them.
            env = {**os.environ, "PEERHUB_BATCH_ID": batch_id, "PEERHUB_BATCH_TASK": t.task_id}
            proc = subprocess.Popen(argv, env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_launch_flags())
        except OSError as exc:
            return {**row, "status": "FAILED", "reason": f"spawn_failed:{type(exc).__name__}", "elapsed_s": 0.0}
        launched.append(proc)
        readers = [threading.Thread(target=_drain, args=(proc.stdout, STDOUT_CAP, out, overflow), daemon=True),
                   threading.Thread(target=_drain, args=(proc.stderr, STDERR_CAP, err, None), daemon=True)]
        for r in readers:
            r.start()
        deadline = started + t.timeout_s + grace_s
        killed = timed_out = unconfirmed = False
        while proc.poll() is None:
            if cancel.is_set() or overflow.is_set() or time.monotonic() > deadline:
                timed_out = not cancel.is_set() and not overflow.is_set()
                killed = cancel.is_set()
                unconfirmed = not kill_tree_confirmed(proc)
                break
            time.sleep(0.05)
        for r in readers:
            r.join(5)
        status, reason = classify_worker(proc.returncode, out.decode("utf-8", "replace"), overflow=overflow.is_set(),
                                         killed=killed, timed_out=timed_out)
        if unconfirmed:
            status, reason = "UNCERTAIN", "kill_unconfirmed"
        elif killed:
            reason = "cancelled"
        result = _parse_result(out.decode("utf-8", "replace"))
        session = result.get("session") if isinstance(result, dict) else None
        out_row: dict[str, Any] = {**row, "status": status, "elapsed_s": round(time.monotonic() - started, 3)}
        if reason:
            out_row["reason"] = reason
        if isinstance(session, dict) and isinstance(cast(dict[str, Any], session).get("handle"), str):
            out_row["session"] = session["handle"]
        guidance = result.get("guidance") if result is not None else None
        explain = cast(dict[str, Any], guidance).get("explain_cmd") if isinstance(guidance, dict) else None
        if status != "COMPLETED" and isinstance(explain, str) and explain:
            out_row["next"] = [explain]
        if responses and status == "COMPLETED" and result is not None:
            out_row["response"] = result["response"]  # the stored reply, as printed by `ask --json`
        return out_row

    def supervise(t: Task, tmp: Path) -> None:
        launched: list[subprocess.Popen[Any]] = []
        started = time.monotonic()
        try:
            row = blocked(t, "cancelled") if cancel.is_set() else work(t, tmp, launched)
        except Exception as exc:
            reason = f"worker_error:{type(exc).__name__}" if launched else f"worker_setup_error:{type(exc).__name__}"
            if launched:
                proc = launched[0]
                try:
                    if kill_tree_confirmed(proc):
                        proc.wait(timeout=5)
                        for pipe in (proc.stdout, proc.stderr):
                            if pipe is not None:
                                pipe.close()
                    else:
                        reason += ":kill_unconfirmed"
                except Exception as cleanup_exc:
                    reason += f":cleanup_error:{type(cleanup_exc).__name__}"
            row = {**blocked(t, ""), "status": "UNCERTAIN" if launched else "FAILED", "reason": reason,
                   "elapsed_s": round(time.monotonic() - started, 3)}
        rows[t.task_id] = row

    scratch = store_scratch_dir(db)
    scratch.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(max_parallel, len(tasks)))
    waiting_reported = False
    with (tempfile.TemporaryDirectory(prefix="peerhub-batch-", dir=scratch) as d,
          ThreadPoolExecutor(max_workers=workers) as executor):
        tmp = Path(d)
        in_flight: set[Future[None]] = set()
        for task in tasks:
            if len(in_flight) >= workers:
                _, in_flight = wait(in_flight, return_when=FIRST_COMPLETED)
            # Low resources hold further launches while asks run; with none running the task starts (with the
            # warning), so a machine that never recovers cannot stall the batch forever.
            while not cancel.is_set():
                warnings = load_warnings(resource_reader().facts())
                in_flight = {future for future in in_flight if not future.done()}
                if not warnings or not in_flight:
                    if warnings:
                        print(f"batch: starting with no ask running ({'; '.join(warnings)})", file=sys.stderr, flush=True)
                    break
                if not waiting_reported:
                    print(f"batch: waiting for machine resources ({'; '.join(warnings)})", file=sys.stderr, flush=True)
                    waiting_reported = True
                cancel.wait(RESOURCE_POLL_SECONDS)
            if cancel.is_set():
                rows[task.task_id] = blocked(task, "cancelled")
            else:
                in_flight.add(executor.submit(supervise, task, tmp))
    results: list[dict[str, Any]] = []
    diag = ["peerhub", "--db", os.path.abspath(db), "diag"]
    for t in tasks:
        try:
            row = rows[t.task_id]
        except KeyError as exc:
            row = {**blocked(t, f"missing_worker_result:{type(exc).__name__}"), "status": "UNCERTAIN"}
        if row["status"] != "COMPLETED":
            row["next"] = [
                format_shell_command([*diag, "running", "--peer", t.peer]),
                format_shell_command([*diag, "health", "--stream", row["stream"]]),
                *row.get("next", ()),
            ]
        results.append(row)
    return {"batch_id": batch_id, "max_parallel": max_parallel, "tasks": results, "outcomes": _outcome_counts(results)}


def render_text(report: Mapping[str, Any]) -> str:
    lines = [f"batch {report['batch_id']} max-parallel {report['max_parallel']}"]
    for r in report["tasks"]:
        lines.append(f"{r['task_id']:<24} {r['peer']:<8} {r['status']:<10} {r.get('reason') or ''}".rstrip())
        if r.get("next"):
            lines.append(f"    next: {', '.join(r['next'])} (request_id={r['request_id']})")
    outcomes = report.get("outcomes")
    if outcomes is None:
        outcomes = _outcome_counts(report["tasks"])
    summary = ", ".join(f"{outcome} {count}" for outcome, count in outcomes.items() if count)
    lines.append(f"outcomes: {summary}".rstrip())
    for r in report["tasks"]:
        if "response" in r:
            lines += [f"--- {r['task_id']} reply ---", r["response"]]
    return "\n".join(lines)
