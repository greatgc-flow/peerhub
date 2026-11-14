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
from pathlib import PurePath
from typing import Any

SCHEMA_VERSION = 1
LABEL_MAX = 40
HOST_MAX = 255
CWD_MAX = 1024
BATCH_ID_MAX = 128
_SESSION = re.compile(r"[A-Za-z0-9._-]{1,128}")
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


def detect_caller(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The frozen v1 caller detail: v, tool, label, session, host, ns, ask_pid, ask_created, cwd, batch, task."""
    try:
        source: Mapping[str, str] = os.environ if env is None else env
        session_raw = source.get("CLAUDE_CODE_SESSION_ID")
        cwd = _cwd()
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
        }
    except Exception:  # never raises: an unreadable environment still yields the schema
        return {"v": SCHEMA_VERSION, "tool": "?", "label": "?", "session": None, "host": None, "ns": None,
                "ask_pid": None, "ask_created": None, "cwd": None, "batch": None, "task": None}
