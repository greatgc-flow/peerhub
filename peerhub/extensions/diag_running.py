"""`diag running`: lease-active Bridge deliveries from existing tables. A lease is not proof a process is alive."""
from __future__ import annotations

import json
import math
import re
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from peerhub.core.load_limits import CALLER_RELATIONS, UNKNOWN, load_snapshot, read_resources
from peerhub.extensions.caller import OWNER_ENV, caller_facts, caller_relation, detect_caller
from peerhub.extensions.diag import ReadonlyDiag
from peerhub.extensions.liveness import ORPHAN_RUNNING, PROCESSES_DEAD, ask_process_state

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


def _row(raw: dict[str, Any], now: float, reader: Mapping[str, Any]) -> dict[str, Any]:
    binding = _binding(raw["metadata_json"])
    key = raw["idempotency_key"]
    started = _started_at(raw["start_detail"])
    caller = caller_facts(raw.get("caller_detail"))
    relation, basis = caller_relation(reader, caller or {})
    row: dict[str, Any] = {
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
        "caller": caller,
        "relation": relation,
        "relation_basis": basis,
    }
    process_state = ask_process_state(raw.get("caller_detail"), raw.get("provider_detail"),
                                      this_host=reader.get("host"), this_ns=reader.get("ns"))
    if process_state == PROCESSES_DEAD:
        row.update(state=PROCESSES_DEAD, certainty="UNKNOWN", ended_at=now, ended_exact=False, elapsed_s=None)
    elif process_state == ORPHAN_RUNNING:
        row.update(state=ORPHAN_RUNNING)
    return row


def running_report(db_path: str | Path, *, read_at: float | None = None, peer: str | None = None,
                   limit: int | None = None, all_rows: bool = False) -> dict[str, Any]:
    now = time.time() if read_at is None else read_at
    limit = None if all_rows else MAX_ROWS if limit is None or limit < 1 else limit
    reader = detect_caller()
    identified = bool(reader["owner"] or reader["session"])
    # A claim stamped with another workspace generation (after a restore) is not live for the Bridge either (see bridge_claims).
    generation_file = Path(db_path).parent / "workspace.generation"
    try:
        generation = generation_file.read_text(encoding="utf-8") if generation_file.is_file() else None
    except OSError as exc:  # unreadable, or replaced by a restore mid-read: report it, never a traceback
        return {"schema_version": "1.0", "status": "FAILED", "basis": "lease", "read_at": now, "rows": [], "truncated": False,
                "error": f"{type(exc).__name__}: workspace.generation unreadable", "reader_identified": identified}
    snap = ReadonlyDiag(db_path).running(read_at=now, limit=limit, workspace_generation=generation, peer=peer)
    return {"schema_version": "1.0", "status": snap["status"], "basis": "lease", "read_at": now,
            "rows": [_row(r, now, reader) for r in snap["rows"]],
            "truncated": snap["truncated"], "error": snap["error"], "limit": limit, "peer": peer,
            "reader_identified": identified}


def sample_load(db_path: str | Path, *, read_at: float | None = None,
                pools_of_profile: Mapping[str, list[str]] | None = None) -> dict[str, Any]:
    """Sample lease-active asks per peer, pool and caller relation, and machine resources. Read-only."""
    report = running_report(db_path, read_at=read_at, all_rows=True)
    resources = read_resources()
    if report["status"] != "OK" or report["truncated"]:  # a failed or cut-off read is not a count: show ?, never 0
        return load_snapshot(None, resources)
    counts: dict[str, int] = {}
    by_pool: dict[str, int] = {}
    by_relation: dict[str, int] = dict.fromkeys(CALLER_RELATIONS, 0)
    unmapped = 0
    pool_mapping = pools_of_profile or {}
    for row in report["rows"]:
        if row.get("state") == PROCESSES_DEAD:
            continue
        counts[row["peer_id"]] = counts.get(row["peer_id"], 0) + 1
        by_relation[row.get("relation", UNKNOWN)] += 1
        if pools_of_profile is None:
            continue
        pools = pool_mapping.get(row.get("profile") or "", [])
        if not pools:
            unmapped += 1
        for pool in pools:
            by_pool[pool] = by_pool.get(pool, 0) + 1
    return load_snapshot(counts, resources, by_pool if pools_of_profile is not None else None,
                         unmapped=unmapped, active_by_relation=by_relation)


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


def _caller_suffix(row: Mapping[str, Any]) -> str:
    facts: Mapping[str, str | int] = row["caller"] or {}
    basis = row["relation_basis"]
    parts = [f"{row['relation']}({basis})" if basis else str(row["relation"])]
    for key, value in facts.items():
        text = str(value)
        if not text.isascii() or not text.isprintable() or any(ch.isspace() for ch in text):
            text = json.dumps(text, ensure_ascii=True)
        parts.append(f"{key}={text}")
    return " ".join(parts)


def format_running(report: dict[str, Any], *, width: int | None = None) -> str:
    lines = ["Lease-active deliveries: a lease is not proof a process is alive (read-only; no provider was contacted)"]
    if report.get("reader_identified") is False:
        lines.append(f"reader: unidentified (export {OWNER_ENV}=<unique token> to classify asks)")
    rows: list[dict[str, Any]] = report["rows"]
    if rows:
        table = [("PEER", "PROFILE", "STREAM", "REQUEST", "DELIVERY", "CERTAINTY", "STARTED", "ELAPSED", "LEASE")]
        table += [(r["peer_id"], r["profile"] or "-", r["stream_id"], r["request_id"] or "-", r["delivery_id"],
                   r["certainty"], _stamp(r["started_at"]), _secs(r["elapsed_s"]), _secs(r["lease_expires_in_s"])) for r in rows]
        widths = [max(len(str(line[i])) for line in table) for i in range(len(table[0]))]
        formatted = ["  ".join(str(cell).ljust(w) for cell, w in zip(line, widths)) for line in table]
        lines.append(formatted[0] + "  CALLER")
        lines.extend(f"{line}  {_caller_suffix(row)}" + (f"  {row['state']}" if "state" in row else "")
                     for line, row in zip(formatted[1:], rows))
    if report["truncated"]:
        lines.append(f"(more rows not shown; limit {report.get('limit', MAX_ROWS)})")
    elif not rows and report["status"] == "OK":
        lines.append("(none)")
    if width is not None:
        lines = [part for line in lines for part in textwrap.wrap(
            line, width=width, break_on_hyphens=False, subsequent_indent="  " if width > 2 else "")]
    return "\n".join(lines)  # a failed read prints its error once, on stderr (see the CLI), never an empty "(none)"
