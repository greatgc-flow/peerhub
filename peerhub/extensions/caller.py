"""Caller evidence, detail schema v1 (#374, design v3 C6): who started an ask.

The Bridge writes this dict once per delivery and invoking process, before the provider process starts. Values are validated here and
stored raw; cleaning for display is the reader's job. Nothing in this module raises.
"""
from __future__ import annotations

import os
import platform
import re
import sys
import unicodedata
from collections.abc import Mapping
from pathlib import Path, PurePath
from typing import Any

SCHEMA_VERSION = 1
LABEL_MAX = 40
HOST_MAX = 255
CWD_MAX = 1024
BATCH_ID_MAX = 128
_SESSION = re.compile(r"[A-Za-z0-9._-]{1,128}")
_WINDOWS_DEVICE = re.compile(r"CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³]", re.IGNORECASE)
NO_PID_NAMESPACE = "-"


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


def _process_created() -> float | None:
    try:
        import psutil
        return float(psutil.Process(os.getpid()).create_time())
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


def detect_caller(env: Mapping[str, str] | None = None, *, query_file: str | Path | None = None,
                  workspace: str | Path | None = None) -> dict[str, Any]:
    """The v1 caller detail, with an eligible query file and its execution workspace when available."""
    try:
        source: Mapping[str, str] = os.environ if env is None else env
        session_raw = source.get("CLAUDE_CODE_SESSION_ID")
        cwd = _cwd()
        file = (local_workspace_file(str(query_file), str(workspace), from_cwd=True)
                if query_file is not None and workspace is not None else None)
        return {
            "v": SCHEMA_VERSION,
            "tool": "cc" if session_raw else "?",
            "label": _label(source, cwd),
            "session": session_raw if isinstance(session_raw, str) and _SESSION.fullmatch(session_raw) else None,
            "host": host_id(),
            "ns": pid_namespace(),
            "ask_pid": os.getpid(),
            "ask_created": _process_created(),
            "cwd": cwd[:CWD_MAX] if cwd is not None else None,
            "batch": _batch_value(source.get("PEERHUB_BATCH_ID")),
            "task": _batch_value(source.get("PEERHUB_BATCH_TASK")),
            "workspace": str(file[1]) if file is not None else None,
            "query_file": str(file[0]) if file is not None else None,
            "stream_file": None,
        }
    except Exception:  # never raises: an unreadable environment still yields the schema
        return {"v": SCHEMA_VERSION, "tool": "?", "label": "?", "session": None, "host": None, "ns": None,
                "ask_pid": None, "ask_created": None, "cwd": None, "batch": None, "task": None,
                "workspace": None, "query_file": None, "stream_file": None}
