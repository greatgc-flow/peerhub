"""Flat, bounded-parallel `peerhub ask` batches: one OS subprocess per task through the public CLI.

Identity, claims and idempotency stay in `ask`; this module only validates a manifest, derives stable stream/request ids,
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from peerhub.extensions.process_tree import kill_process_tree

DEFAULT_TIMEOUT_S = 600.0
MAX_TASKS = 256
MAX_PARALLEL = 16
STDOUT_CAP = 4 * 1024 * 1024
STDERR_CAP = 64 * 1024
PROMPT_FILE_CAP = 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_KEYS = {"task_id", "peer", "prompt", "prompt_file", "workspace", "profile", "writable", "timeout_s"}


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
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PARALLEL:
        raise ManifestError(f"--max-parallel must be an integer 1..{MAX_PARALLEL}")
    return value


def load_manifest(path: str | Path, *, batch_id: str) -> list[Task]:
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(doc, dict) or set(doc) != {"tasks"} or not isinstance(doc["tasks"], list):
        raise ManifestError('manifest must be {"tasks": [...]}')
    rows = doc["tasks"]
    if not 1 <= len(rows) <= MAX_TASKS:
        raise ManifestError(f"manifest needs 1..{MAX_TASKS} tasks")
    tasks: list[Task] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not set(row) <= _KEYS:
            raise ManifestError(f"task has unknown keys: {sorted(set(row) - _KEYS) if isinstance(row, dict) else row!r}")
        tid = _check_id("task_id", row.get("task_id"))
        derive_ids(batch_id, tid)
        if tid in seen:
            raise ManifestError(f"duplicate task_id {tid!r}")
        seen.add(tid)
        peer = row.get("peer")
        if not isinstance(peer, str) or not peer.strip():
            raise ManifestError(f"task {tid}: peer is required")
        if ("prompt" in row) == ("prompt_file" in row):
            raise ManifestError(f"task {tid}: give exactly one of prompt or prompt_file")
        if "prompt" in row:
            prompt = row["prompt"]
        else:
            try:
                file = Path(row["prompt_file"])
                file = file if file.is_absolute() else path.parent / file
                if file.stat().st_size > PROMPT_FILE_CAP:
                    raise ManifestError(f"task {tid}: prompt_file exceeds {PROMPT_FILE_CAP} bytes")
                prompt = file.read_text(encoding="utf-8-sig")
            except (OSError, ValueError, TypeError) as exc:
                raise ManifestError(f"task {tid}: cannot read prompt_file: {exc}") from exc
        if not isinstance(prompt, str) or not prompt.strip():
            raise ManifestError(f"task {tid}: prompt must be non-empty text")
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


def validate_tasks(tasks: Sequence[Task], *, kind_of: Callable[[str], str | None]) -> None:
    """All-or-nothing checks before any worker exists."""
    spaces: list[tuple[Task, str, bool]] = []
    for t in tasks:
        kind = kind_of(t.peer)
        if kind is None:
            raise ManifestError(f"task {t.task_id}: unknown peer {t.peer!r} (register it with --adapter cx, cc or ag)")
        if t.workspace is not None and not t.workspace.is_dir():
            raise ManifestError(f"task {t.task_id}: workspace {t.workspace} is not an existing directory")
        spaces.append((t, _real(t.workspace), t.writable or kind == "ag"))  # ag writes regardless of the flag (#140)
    for i, (ta, pa, wa) in enumerate(spaces):
        for tb, pb, wb in spaces[i + 1:]:
            if (wa or wb) and _related(pa, pb):
                raise ManifestError(f"workspace overlap between tasks {ta.task_id} and {tb.task_id}: a writer may not share or nest with another task")


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
            return obj
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
    if (isinstance(guidance, dict) and guidance.get("code") == "ask_claim_held") or status == "pending_earlier_record":
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
    if result.get("certainty") == "NOT_STARTED":
        return "FAILED", "not_started"
    if status == "not_executed" or result.get("task_outcome") == "NOT_EXECUTED":
        reason = result.get("reason")
        return "FAILED", f"not_executed:{reason}" if isinstance(reason, str) and reason else "not_executed"
    if status == "uncertain":
        return "UNCERTAIN", "uncertain"
    error = result.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return "UNCERTAIN", code if isinstance(code, str) and code else f"exit_{returncode}"


def exit_code(report: Mapping[str, Any]) -> int:
    return 0 if all(r["status"] == "COMPLETED" for r in report["tasks"]) else 1


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


def _creation_time(pid: int) -> int:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return -1
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return -1
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        k32.CloseHandle(handle)


def _still_alive(pid: int, created: int) -> bool:
    if sys.platform != "win32":
        return False
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


def kill_tree_confirmed(proc: subprocess.Popen[bytes], *, wait_s: float = 5.0) -> bool:
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
              cancel: threading.Event | None = None, grace_s: float = 30.0) -> dict[str, Any]:
    check_max_parallel(max_parallel)
    cancel = cancel or threading.Event()
    rows: dict[str, dict[str, Any]] = {}
    pending = list(tasks)
    lock = threading.Lock()

    def blocked(t: Task, reason: str) -> dict[str, Any]:
        stream, request = derive_ids(batch_id, t.task_id)
        return {"task_id": t.task_id, "peer": t.peer, "stream": stream, "status": "BLOCKED", "request_id": request,
                "elapsed_s": 0.0, "reason": reason}

    def work(t: Task, tmp: Path) -> dict[str, Any]:
        stream, request = derive_ids(batch_id, t.task_id)
        row = {"task_id": t.task_id, "peer": t.peer, "stream": stream, "request_id": request}
        query = tmp / f"{t.task_id}.txt"
        query.write_text(t.prompt, encoding="utf-8")
        started = time.monotonic()
        out, err = bytearray(), bytearray()
        overflow = threading.Event()
        try:
            proc = subprocess.Popen(list(argv_builder(t, stream=stream, request_id=request, db=db, query_file=query)),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_launch_flags())
        except OSError as exc:
            return {**row, "status": "FAILED", "reason": f"spawn_failed:{type(exc).__name__}", "elapsed_s": 0.0}
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
        out_row = {**row, "status": status, "elapsed_s": round(time.monotonic() - started, 3)}
        if reason:
            out_row["reason"] = reason
        if isinstance(session, dict) and isinstance(session.get("handle"), str):
            out_row["session"] = session["handle"]
        return out_row

    def supervisor(tmp: Path) -> None:
        while True:
            with lock:
                if not pending:
                    return
                t = pending.pop(0)
                stop = cancel.is_set()
            rows[t.task_id] = blocked(t, "cancelled") if stop else work(t, tmp)

    with tempfile.TemporaryDirectory(prefix="peerhub-batch-") as d:
        threads = [threading.Thread(target=supervisor, args=(Path(d),), daemon=True) for _ in range(min(max_parallel, len(tasks)))]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    return {"batch_id": batch_id, "max_parallel": max_parallel, "tasks": [rows[t.task_id] for t in tasks]}


def render_text(report: Mapping[str, Any]) -> str:
    lines = [f"batch {report['batch_id']} max-parallel {report['max_parallel']}"]
    for r in report["tasks"]:
        lines.append(f"{r['task_id']:<24} {r['peer']:<8} {r['status']:<10} {r.get('reason') or ''}".rstrip())
    return "\n".join(lines)
