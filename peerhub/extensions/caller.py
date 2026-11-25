"""Caller evidence, detail schema v1 (#374, design v3 C6): who started an ask.

The Bridge writes this dict once per delivery and invoking process, before the provider process starts. Values are validated here and
stored raw; cleaning for display is the reader's job. Nothing in this module raises.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import unicodedata
from collections.abc import Mapping
from pathlib import Path, PurePath
from typing import Any, cast

from peerhub.core.load_limits import MINE, OTHER, UNKNOWN
from peerhub.extensions.process_tree import process_created

SCHEMA_VERSION = 1
LABEL_MAX = 40
HOST_MAX = 255
CWD_MAX = 1024
BATCH_ID_MAX = 128
_SESSION = re.compile(r"[A-Za-z0-9._-]{1,128}")
_WINDOWS_DEVICE = re.compile(r"CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³]", re.IGNORECASE)
NO_PID_NAMESPACE = "-"
OWNER_ENV = "PEERHUB_OWNER"
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
_IDENTITY_FIELDS = ("owner", "session")
_CALLER_TEXT_FIELDS = ("tool", "label", "session", "owner", "host", "ns", "workspace", "cwd", "batch", "task")


def _without_controls(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cc")


def host_id() -> str | None:
    """`node|platform`: a pid only means something on this host (another pid namespace is checked separately)."""
    try:
        return (platform.node() + "|" + sys.platform)[:HOST_MAX]
    except Exception:
        return None


def pid_namespace() -> str | None:
    """Linux: the target of /proc/self/ns/pid; "-" where pid namespaces do not exist; None when it cannot be read."""
    if not sys.platform.startswith("linux"):
        return NO_PID_NAMESPACE
    try:
        return os.readlink("/proc/self/ns/pid")
    except Exception:
        return None


def _process_created(pid: int) -> float | None:
    try:
        return process_created(pid)
    except Exception:
        return None


def _cwd() -> str | None:
    try:
        return os.getcwd()
    except Exception:
        return None


def _label(env: Mapping[str, str], cwd: str | None) -> str:
    lead = env.get("PEERHUB_LEAD")
    if isinstance(lead, str):
        cleaned = _without_controls(lead).strip()
        if cleaned:
            return cleaned[:LABEL_MAX]
    if cwd:
        folder = _without_controls(PurePath(cwd).name).strip()
        if folder:
            return folder[:LABEL_MAX]
    return "?"


def _batch_value(value: object) -> str | None:
    if isinstance(value, str) and 1 <= len(value) <= BATCH_ID_MAX and value.isprintable():
        return value
    return None


def _local_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value or value.replace("\\", "/").startswith("//"):
        return None
    if any(unicodedata.category(ch) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for ch in value):
        return None
    path = Path(value)
    if os.name == "nt" and any(
        ":" in part or _WINDOWS_DEVICE.fullmatch(part.split(".", 1)[0].rstrip(" "))
        for part in path.parts[1 if path.anchor else 0:]
    ):
        return None
    return path


def local_workspace_file(value: object, workspace: object, *, from_cwd: bool = False) -> tuple[Path, Path] | None:
    """Return the resolved regular file and workspace. Monitor reads require a recorded absolute workspace."""
    path, root = _local_path(value), _local_path(workspace)
    if path is None or root is None:
        return None
    if not from_cwd and (not root.is_absolute() or not path.is_absolute() or not path.is_relative_to(root)):
        return None
    try:
        resolved_root = root.resolve(strict=True)
        if not from_cwd and resolved_root != root:
            return None  # the recorded workspace path has been redirected
        if _local_path(str(resolved_root)) is None or not resolved_root.is_dir():
            return None
        resolved_path = path.resolve(strict=True)
        if (_local_path(str(resolved_path)) is not None and resolved_path.is_relative_to(resolved_root)
                and resolved_path.is_file()):
            return resolved_path, resolved_root
    except (OSError, RuntimeError, ValueError):  # includes inaccessible paths, deletion and symlink loops
        pass
    return None


def _identity_token(value: object) -> str | None:
    return value if isinstance(value, str) and _SESSION.fullmatch(value) else None


def caller_facts(detail: object) -> dict[str, str | int] | None:
    """Read the caller facts available in a stored v1 detail."""
    if not isinstance(detail, str):
        return None
    try:
        parsed: object = json.loads(detail)
    except (ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict):
        return None
    item = cast(dict[str, object], parsed)
    if item.get("v") != SCHEMA_VERSION:
        return None
    facts: dict[str, str | int] = {}
    for field in _CALLER_TEXT_FIELDS:
        value = item.get(field)
        if field in _IDENTITY_FIELDS:
            value = _identity_token(value)
        if isinstance(value, str) and value:
            facts[field] = value
    pid = item.get("ask_pid")
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        facts["ask_pid"] = pid
    return facts or None


def caller_relation(reader: Mapping[str, object], stored: Mapping[str, object]) -> tuple[str, str | None]:
    """Compare the stored caller to the reader and return the relation and its first applicable evidence field."""
    for key in ("host", "ns"):
        left, right = reader.get(key), stored.get(key)
        if (isinstance(left, str) and isinstance(right, str) and left and right
                and left.isprintable() and right.isprintable() and left != right):
            return OTHER, key
    for key in _IDENTITY_FIELDS:
        left, right = _identity_token(reader.get(key)), _identity_token(stored.get(key))
        if left is not None and right is not None:
            return (MINE if left == right else OTHER), key
    return UNKNOWN, None


def detect_caller(env: Mapping[str, str] | None = None, *, query_file: str | Path | None = None,
                  workspace: str | Path | None = None, include_parent: bool = False) -> dict[str, Any]:
    """The v1 caller detail, with an eligible query file and its execution workspace when available."""
    try:
        source: Mapping[str, str] = os.environ if env is None else env
        session_raw = source.get(SESSION_ENV)
        cwd = _cwd()
        file = (local_workspace_file(str(query_file), str(workspace), from_cwd=True)
                if query_file is not None and workspace is not None else None)
        pid = os.getpid()
        created = _process_created(pid)
        detail = {
            "v": SCHEMA_VERSION,
            "tool": "cc" if session_raw else "?",
            "label": _label(source, cwd),
            "session": _identity_token(session_raw),
            "owner": _identity_token(source.get(OWNER_ENV)),
            "host": host_id(),
            "ns": pid_namespace(),
            "ask_pid": pid,
            "ask_created": created,
            "cwd": cwd[:CWD_MAX] if cwd is not None else None,
            "batch": _batch_value(source.get("PEERHUB_BATCH_ID")),
            "task": _batch_value(source.get("PEERHUB_BATCH_TASK")),
            "workspace": str(file[1]) if file is not None else None,
            "query_file": str(file[0]) if file is not None else None,
            "stream_file": None,
        }
        if include_parent:
            parent = os.getppid()
            parent_created = _process_created(parent) if parent > 0 else None
            if parent_created is not None and created is not None and parent_created > created:
                # The parent PID already names a different process.
                parent, parent_created = pid, created
            detail.update(caller_pid=parent if parent > 0 else pid,
                          caller_created=parent_created if parent > 0 else created)
        return detail
    except Exception:  # never raises: an unreadable environment still yields the schema
        return {"v": SCHEMA_VERSION, "tool": "?", "label": "?", "session": None, "owner": None, "host": None, "ns": None,
                "ask_pid": None, "ask_created": None, "cwd": None, "batch": None, "task": None,
                "workspace": None, "query_file": None, "stream_file": None}
