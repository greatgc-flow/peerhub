"""Claude Code lead sessions read from the owner's registered config folders (#376 design v5, section 3.2).

Read-only and bounded: nothing is written, no store is opened, and no path comes from a file's content. Every file and
folder is confined below its registered folder (no link or junction), and the reader never raises."""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import stat
import sys
from collections.abc import Callable, Mapping
from typing import Any, cast

from peerhub.extensions import process_tree
from peerhub.extensions.monitor_queries import ACTIVITY_SPAN_S
from peerhub.extensions.monitor_values import SAFE_MODEL, activity_lines, display_text, iso_seconds

LEAD_DIRS_CAP = 8
LEAD_FILES_CAP = 64
LEAD_FILE_MAX = 16_384
LEAD_PROJECTS_CAP = 2_000
LEAD_TAIL_BYTES = 262_144
LEAD_CATALOG_MAX = 1_048_576
LEAD_PID_SLACK_S = 2.0

_SESSION_NAME = re.compile(r"[0-9]{1,10}\.json")
_SESSION_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
_PROC_START = re.compile(r"[0-9]{1,20}")
_STATE = re.compile(r"[a-z_]{1,16}")
_ENTRY = re.compile(r"[a-z0-9_-]{1,24}")

# The file system calls that tests replace (a resolver, a lister, a stat and an open that depend on the platform).
_realpath = os.path.realpath
_listdir = os.listdir
_stat = os.stat
_open = os.open

Probe = Callable[[int], object]
Listing = tuple[str, list[str]]
Read = tuple[bytes, int, float]


class _Unreadable(Exception):
    """A file or folder exists but cannot be used: an OS error, not a regular file, too large, or behind a link."""


def lead_dirs(env: Mapping[str, str]) -> list[str]:
    """CLAUDE_CONFIG_DIR (else ~/.claude), then the PEERHUB_LEAD_DIRS entries: absolute, without normcase duplicates.

    `read_leads` reads the first LEAD_DIRS_CAP of them and counts the rest as not read."""
    entries = [env.get("CLAUDE_CONFIG_DIR") or "~/.claude", *(p for p in env.get("PEERHUB_LEAD_DIRS", "").split(os.pathsep) if p)]
    dirs: list[str] = []
    for entry in entries:
        path = os.path.abspath(os.path.expanduser(entry))
        if all(os.path.normcase(path) != os.path.normcase(known) for known in dirs):
            dirs.append(path)
    return dirs


def process_ticks(pid: int, *, os_name: str | None = None) -> int | None:
    """The creation FILETIME of `pid` as one int (Windows only), else None; never raises."""
    if ((os_name or sys.platform) != "win32" or not isinstance(pid, int)  # pyright: ignore[reportUnnecessaryIsInstance]
            or isinstance(pid, bool) or not 1 <= pid <= 2**32 - 1):
        return None
    try:
        value = process_tree.creation_ticks(pid)
    except Exception:  # any failure of the kernel calls
        return None
    return None if value == -1 else value


def read_leads(dirs: list[str], *, now: float, text_on: bool, secrets: list[str], created: Probe,
               ticks: Probe = process_ticks, os_name: str = sys.platform,
               host: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(sessions, read) of the registered folders; read = {state: read | partial | error, unreadable, not_read}.

    A folder whose sessions cannot be listed (or that fails unexpectedly) makes the state `error`; the next folder is
    still read. One row per session id: the first kept file, replaced (in its place) by a later file of the same id only
    when the first could not be confirmed (`alive` None) and the later one is (`alive` True). Each session carries `key`,
    the full SHA-256 digest of its id, which callers must not emit."""
    domain = f"{os_name}:{platform.node().lower() if host is None else host}"
    counts = {"unreadable": 0, "not_read": max(0, len(dirs) - LEAD_DIRS_CAP)}
    kept: dict[str, dict[str, Any]] = {}  # session id -> row
    failed = False
    for base in dirs[:LEAD_DIRS_CAP]:
        try:
            kept.update(_read_folder(base, kept, counts, now=now, text_on=text_on, secrets=secrets,
                                     created=created, ticks=ticks, domain=domain))
        except Exception:  # _Unreadable for the sessions folder; anything else is a defect that must not stop the frame
            failed = True
    state = "error" if failed else "partial" if counts["unreadable"] + counts["not_read"] else "read"
    return list(kept.values()), {"state": state, **counts}


def _read_folder(base: str, kept: dict[str, dict[str, Any]], counts: dict[str, int], *, now: float, text_on: bool,
                 secrets: list[str], created: Probe, ticks: Probe, domain: str) -> dict[str, dict[str, Any]]:
    """The rows of one folder by session id: new sessions, and whole rows that replace an unconfirmed row of `kept`."""
    names = _candidates(base, counts)
    if not names:
        return {}
    projects = _projects(base, counts)
    windows = _windows(base, counts)
    rows: dict[str, dict[str, Any]] = {}
    tails: dict[str, Read | None] = {}  # one read (and at most one `unreadable`) per transcript, however many files share it
    for name in names:
        item = _candidate(base, name, counts, created)
        if item is None or item.get("kind") != "interactive" or not isinstance(item.get("entrypoint"), str) \
                or item["entrypoint"].startswith("sdk-"):
            continue
        sid = item["sessionId"]
        known = rows.get(sid) or kept.get(sid)  # only a kept row hides a later file of the same session (a dead file never does)
        if known is not None and known["alive"] is True:
            continue
        alive = _liveness(item, created=created, ticks=ticks, domain=domain)
        if alive is False or (known is not None and alive is None):
            continue
        if sid not in tails:
            try:
                tails[sid] = _transcript(base, projects, sid)
            except _Unreadable:
                counts["unreadable"] += 1
                tails[sid] = None
        tail = tails[sid]
        if alive is None and (tail is None or now - tail[2] > ACTIVITY_SPAN_S):
            continue
        facts = _tail_facts(tail[0], tail[1], text_on) if tail else {}
        rows[sid] = _row(item, alive, tail[2] if tail else None, facts, windows, text_on, secrets)
    return rows


def _confined(base: str, parts: tuple[str, ...]) -> str | None:
    """The resolved path of `parts` below `base`, or None when a link or junction below `base` changes where it leads."""
    try:
        root = _realpath(base)
        resolved = _realpath(os.path.join(base, *parts))
    except (OSError, ValueError):
        return None
    return resolved if os.path.normcase(resolved) == os.path.normcase(os.path.join(root, *parts)) else None


def _list(base: str, parts: tuple[str, ...]) -> Listing | None:
    """(resolved folder, names) of a confined folder; None when it does not exist; _Unreadable otherwise."""
    folder = _confined(base, parts)
    if folder is None:
        raise _Unreadable
    try:
        return folder, _listdir(folder)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _Unreadable from exc


def _read(base: str, parts: tuple[str, ...], limit: int, *, tail: bool = False) -> Read | None:
    """(data, offset, mtime) of a confined regular file: the whole file of at most `limit` bytes, or with `tail` its last
    `limit` bytes. None when it does not exist; _Unreadable when it exists and cannot be used."""
    path = _confined(base, parts)
    if path is None:
        raise _Unreadable
    try:
        if not stat.S_ISREG(_stat(path).st_mode):  # a FIFO or a device is never opened
            raise _Unreadable
        fd = _open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _Unreadable from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or (not tail and info.st_size > limit):
                raise _Unreadable
            offset = max(0, info.st_size - limit) if tail else 0
            handle.seek(offset)
            data = handle.read(limit if tail else limit + 1)
    except OSError as exc:
        raise _Unreadable from exc
    if len(data) > limit or _confined(base, parts) != path:
        raise _Unreadable
    return data, offset, info.st_mtime


def _mtime(path: str) -> float:
    try:
        return os.lstat(path).st_mtime
    except OSError:
        return 0.0


def _candidates(base: str, counts: dict[str, int]) -> list[str]:
    """The newest LEAD_FILES_CAP session file names; the rest count as not read. _Unreadable when the listing fails."""
    listed = _list(base, ("sessions",))
    if listed is None:
        return []
    folder, names = listed
    names = sorted((n for n in names if _SESSION_NAME.fullmatch(n)), key=lambda n: _mtime(os.path.join(folder, n)), reverse=True)
    counts["not_read"] += max(0, len(names) - LEAD_FILES_CAP)
    return names[:LEAD_FILES_CAP]


def _candidate(base: str, name: str, counts: dict[str, int], created: Probe) -> dict[str, Any] | None:
    """A valid session file, or None; an invalid one counts as unreadable only when a process has the pid of its name."""
    pid = int(name[:-len(".json")])
    try:
        read = _read(base, ("sessions", name), LEAD_FILE_MAX)
    except _Unreadable:
        read = (b"", 0, 0.0)
    if read is None:  # removed since the listing (a headless ask that ended)
        return None
    item = _json_object(read[0])
    if item is not None and _valid_session(item, pid):
        return item
    if created(pid) is not None:
        counts["unreadable"] += 1
    return None


def _valid_session(item: dict[str, Any], pid: int) -> bool:
    sid, own = item.get("sessionId"), item.get("pid")
    started = _finite(item.get("startedAt"))
    return (isinstance(sid, str) and _SESSION_ID.fullmatch(sid) is not None
            and isinstance(own, int) and not isinstance(own, bool) and own > 0 and own == pid
            and started is not None and started > 0)


def _liveness(item: dict[str, Any], *, created: Probe, ticks: Probe, domain: str) -> bool | None:
    """True alive, None unknown, False excluded (section 3.2 step 4): the machine, then the process, then its identity."""
    if item.get("pidDomain") != domain:
        return None
    born = created(item["pid"])
    if born is None:
        return False
    raw = item.get("procStart")
    recorded = int(raw) if isinstance(raw, str) and _PROC_START.fullmatch(raw) else None
    actual = ticks(item["pid"]) if recorded is not None else None
    if recorded is not None and isinstance(actual, int) and not isinstance(actual, bool):
        return recorded == actual
    seconds = _finite(born)
    return False if seconds is not None and seconds > item["startedAt"] / 1000 + LEAD_PID_SLACK_S else None


def _projects(base: str, counts: dict[str, int]) -> Listing | None:
    """The sorted project folder names (at most LEAD_PROJECTS_CAP) of one registered folder, listed once."""
    try:
        listed = _list(base, ("projects",))
    except _Unreadable:
        counts["unreadable"] += 1
        return None
    if listed is None:
        return None
    folder, names = listed
    counts["not_read"] += max(0, len(names) - LEAD_PROJECTS_CAP)
    return folder, sorted(names)[:LEAD_PROJECTS_CAP]


def _transcript(base: str, projects: Listing | None, sid: str) -> Read | None:
    """The tail of the first `<project>/<sid>.jsonl` that exists; None when none does; _Unreadable when it cannot be read."""
    if projects is None:
        return None
    folder, names = projects
    for project in names:
        if os.path.lexists(os.path.join(folder, project, f"{sid}.jsonl")):
            return _read(base, ("projects", project, f"{sid}.jsonl"), LEAD_TAIL_BYTES, tail=True)
    return None


def _windows(base: str, counts: dict[str, int]) -> dict[str, int]:
    """{model id: max input tokens} from the newest catalog file; a catalog that cannot be read counts as unreadable."""
    try:
        listed = _list(base, ("cache", "model-catalog"))
        if listed is None:
            return {}
        folder, names = listed
        files = [n for n in names if n.endswith(".json") and not os.path.isdir(os.path.join(folder, n))]
        read = _read(base, ("cache", "model-catalog", max(files, key=lambda n: _mtime(os.path.join(folder, n)))),
                     LEAD_CATALOG_MAX) if files else None
    except _Unreadable:
        counts["unreadable"] += 1
        return {}
    catalog = _json_object(read[0]) if read else None
    if read and catalog is None:
        counts["unreadable"] += 1
    return _catalog_windows(catalog) if catalog else {}


def _catalog_windows(catalog: dict[str, Any]) -> dict[str, int]:
    windows: dict[str, int] = {}
    for model in _items(_obj(_obj(catalog.get("catalog")).get("config")).get("models")):
        entry = _obj(model)
        size = _obj(entry.get("runtime")).get("max_input_tokens")
        if isinstance(entry.get("id"), str) and isinstance(size, int) and not isinstance(size, bool) and size > 0:
            windows[entry["id"]] = size
    return windows


def _obj(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _items(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _json_object(piece: bytes) -> dict[str, Any] | None:
    try:
        value: object = json.loads(piece.decode("utf-8"))
    except (ValueError, RecursionError):  # invalid UTF-8 or JSON, or a deeply nested value
        return None
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _tail_facts(data: bytes, offset: int, text_on: bool) -> dict[str, Any]:
    """The newest context, said, title and prompt values of a transcript tail, walking from the last line backwards."""
    pieces = data.split(b"\n")[1 if offset > 0 else 0:]
    wanted = {"context", "said", "title", "prompt"} if text_on else {"context"}
    found: dict[str, Any] = {}
    for piece in reversed(pieces):
        if wanted <= found.keys():
            break
        line = _json_object(piece) if piece else None
        if line is None:
            continue
        kind = line.get("type")
        if kind == "assistant":
            context = _context(line)
            if context is None:
                continue
            found.setdefault("context", context)
            said = _said(line["message"]) if "said" in wanted and "said" not in found else None
            if said is not None:
                found["said"] = said
        elif kind == "ai-title" and "title" in wanted and isinstance(line.get("aiTitle"), str):
            found.setdefault("title", line["aiTitle"])
        elif kind == "last-prompt" and "prompt" in wanted and isinstance(line.get("lastPrompt"), str):
            found.setdefault("prompt", line["lastPrompt"])
    return found


def _context(line: dict[str, Any]) -> tuple[int, float | None, str | None] | None:
    """(tokens, at, model) of a main-chain assistant line with a usable usage, else None."""
    message: object = line.get("message")
    usage: object = _obj(message).get("usage")
    if line.get("isSidechain") is True or not isinstance(message, dict) or not isinstance(usage, dict):
        return None
    tokens = 0
    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        value: object = cast(dict[str, Any], usage).get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        tokens += value
    model = cast(dict[str, Any], message).get("model")
    return (tokens, iso_seconds(line.get("timestamp")), _matching(SAFE_MODEL, model)) if tokens > 0 else None


def _said(message: object) -> str | None:
    blocks = [_obj(block) for block in _items(_obj(message).get("content"))]
    texts = [b["text"] for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"]]
    return texts[-1] if texts else None


def _row(item: dict[str, Any], alive: bool | None, active_at: float | None, facts: dict[str, Any],
         windows: dict[str, int], text_on: bool, secrets: list[str]) -> dict[str, Any]:
    digest = hashlib.sha256(item["sessionId"].encode("ascii")).hexdigest()
    tokens, context_at, raw_model = facts.get("context", (None, None, None))
    model = _cleaned_match(SAFE_MODEL, raw_model, 128, secrets)
    window = windows.get(model) if model else None
    cleaned_cwd = display_text(item.get("cwd"), LEAD_FILE_MAX, secrets)  # cleaned and redacted whole before its last part is taken
    return {
        "key": digest, "id": digest[:8], "tool": "cc", "pid": item["pid"], "alive": alive,
        "entry": _cleaned_match(_ENTRY, item.get("entrypoint"), 24, secrets),
        "name": display_text(item.get("name"), 40, secrets) if text_on else None,
        "folder": display_text(re.split(r"[\\/]", cleaned_cwd.rstrip("\\/"))[-1], 40, secrets) if cleaned_cwd else None,
        "cwd": display_text(item.get("cwd"), 200, secrets),
        "state": _cleaned_match(_STATE, item.get("status"), 16, secrets),
        "state_since": _epoch_ms(item.get("statusUpdatedAt")),
        "started_at": item["startedAt"] / 1000,
        "version": display_text(item.get("version"), 20, secrets),
        "active_at": active_at, "model": model, "context_tokens": tokens, "context_window": window,
        "context_pct": min(100, tokens * 100 // window) if tokens and window else None,
        "context_at": context_at,
        "title": display_text(facts.get("title"), 80, secrets),
        "prompt_line": _line(facts.get("prompt"), last=False, secrets=secrets),
        "said_line": _line(facts.get("said"), last=True, secrets=secrets),
    }


def _line(text: object, *, last: bool, secrets: list[str]) -> str | None:
    """The line as cut for --expand: the first line with a letter or digit, or with `last` the last such line."""
    return activity_lines(text, last=last, secrets=secrets)[1] if isinstance(text, str) else None


def _matching(pattern: re.Pattern[str], value: object) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _cleaned_match(pattern: re.Pattern[str], value: object, cap: int, secrets: list[str]) -> str | None:
    """`value` cleaned and redacted like every string from a file (#376 3.5) when it matches `pattern` before and after;
    None otherwise (a redacted secret no longer matches)."""
    return _matching(pattern, display_text(value, cap, secrets)) if _matching(pattern, value) else None


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:  # an integer of 2**1024 or more
        return None
    return number if math.isfinite(number) else None


def _epoch_ms(value: object) -> float | None:
    number = _finite(value)
    return number / 1000 if number is not None and number > 0 else None
