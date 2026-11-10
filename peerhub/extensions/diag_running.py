"""`diag running`: lease-active Bridge deliveries from existing tables. A lease is not proof a process is alive."""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from peerhub.extensions.diag import ReadonlyDiag

MAX_ROWS = 20


def _started_at(detail: object) -> float | None:
    """The delivery's recorded start time, or None when the evidence is absent or not a finite number."""
    if not isinstance(detail, str):
        return None
    try:
        parsed: object = json.loads(detail)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("started_at")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _binding(metadata_json: object) -> str | None:
    if not isinstance(metadata_json, str):
        return None
    try:
        meta: object = json.loads(metadata_json)
    except ValueError:
        return None
    value = meta.get("binding") if isinstance(meta, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return value if isinstance(value, str) and value else None  # pyright: ignore[reportUnknownVariableType]


_PROFILE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\.[A-Za-z0-9_.-]*[A-Za-z0-9_-]")


def _profile(binding: str | None) -> str | None:
    """`profile/model/effort[#scope]` names a profile; a profile-less ask binds `model/effort[#scope]`, whose first part is a model.
    The profile is read from the binding alone: a custom or account-bound peer (`cc-work`) may use any builtin provider's profile."""
    if not binding:
        return None
    parts = binding.split("#", 1)[0].split("/")
    return parts[0] if len(parts) == 3 and _PROFILE_ID.fullmatch(parts[0]) else None


def _row(raw: dict[str, Any], now: float) -> dict[str, Any]:
    binding = _binding(raw["metadata_json"])
    key = raw["idempotency_key"]
    started = _started_at(raw["start_detail"])
    return {
        "peer_id": raw["peer_id"],
        "profile": _profile(binding),
        "binding": binding,
        "stream_id": raw["stream_id"],
        "request_id": key[len("ask:"):] if isinstance(key, str) and key.startswith("ask:") else None,
        "delivery_id": raw["delivery_id"],
        "certainty": raw["certainty"],
        "started_at": started,
        "elapsed_s": None if started is None else round(max(0.0, now - started), 3),
        "lease_expires_in_s": round(float(raw["expires_at"]) - now, 3),
    }


def running_report(db_path: str | Path, *, read_at: float | None = None) -> dict[str, Any]:
    now = time.time() if read_at is None else read_at
    # A claim stamped with another workspace generation (after a restore) is not live for the Bridge either (see bridge_claims).
    generation_file = Path(db_path).parent / "workspace.generation"
    try:
        generation = generation_file.read_text(encoding="utf-8") if generation_file.is_file() else None
    except OSError as exc:  # unreadable, or replaced by a restore mid-read: report it, never a traceback
        return {"schema_version": "1.0", "status": "FAILED", "basis": "lease", "read_at": now, "rows": [], "truncated": False,
                "error": f"{type(exc).__name__}: workspace.generation unreadable"}
    snap = ReadonlyDiag(db_path).running(read_at=now, limit=MAX_ROWS, workspace_generation=generation)
    return {"schema_version": "1.0", "status": snap["status"], "basis": "lease", "read_at": now,
            "rows": [_row(r, now) for r in snap["rows"]], "truncated": snap["truncated"], "error": snap["error"]}


def _secs(value: float | None) -> str:
    return "?" if value is None else f"{value:.0f}s"  # "?" = no valid start evidence (elapsed) / not applicable to the lease


def _stamp(started_at: float | None) -> str:
    """The recorded start, UTC, or "?" when there is no valid start evidence."""
    if started_at is None:
        return "?"
    try:
        return datetime.fromtimestamp(started_at, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return "?"


def format_running(report: dict[str, Any]) -> str:
    lines = ["Lease-active deliveries: a lease is not proof a process is alive (read-only; no provider was contacted)"]
    rows: list[dict[str, Any]] = report["rows"]
    if rows:
        table = [("PEER", "PROFILE", "STREAM", "REQUEST", "DELIVERY", "CERTAINTY", "STARTED", "ELAPSED", "LEASE")]
        table += [(r["peer_id"], r["profile"] or "-", r["stream_id"], r["request_id"] or "-", r["delivery_id"],
                   r["certainty"], _stamp(r["started_at"]), _secs(r["elapsed_s"]), _secs(r["lease_expires_in_s"])) for r in rows]
        widths = [max(len(str(line[i])) for line in table) for i in range(len(table[0]))]
        lines += ["  ".join(str(cell).ljust(w) for cell, w in zip(line, widths)).rstrip() for line in table]
        if report["truncated"]:
            lines.append(f"(more rows not shown; limit {MAX_ROWS})")
    elif report["status"] == "OK":
        lines.append("(none)")
    return "\n".join(lines)  # a failed read prints its error once, on stderr (see the CLI), never an empty "(none)"
