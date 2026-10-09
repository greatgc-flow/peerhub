"""Offline-safe helpers for bounded public ask measurements; never invoke providers on import."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

ASK_TIMEOUT = 240


def ask_args(peer: str, workspace: Path, prompt: str, request_id: str, *,
             resume: bool = False, writable: bool = False) -> list[str]:
    args = ["--db", str(workspace / "core.db"), "ask", peer, prompt,
            "--workspace", str(workspace), "--stream", "live-measurement",
            "--request-id", request_id, "--profile", f"{peer}.standard",
            "--timeout-seconds", str(ASK_TIMEOUT), "--max-output-bytes", "65536", "--json"]
    if resume:
        args.append("--resume")
    if writable:
        args.append("--writable")
    return args


def assert_measured_response(result: dict[str, Any]) -> None:
    assert result["status"] == "delivered" and result["certainty"] == "TERMINAL", result
    assert result["response_record_id"]
    assert isinstance(result["response"], str) and result["response"].strip()
    usage = result["usage"]  # missing usage is a measurement failure, never zero
    assert isinstance(usage, dict)
    for field in ("input_tokens", "output_tokens"):
        assert type(usage[field]) is int and usage[field] > 0, usage
    assert all(type(value) is int and value >= 0 for value in usage.values()), usage


def running_session(db: Path, peer: str) -> str | None:
    """Read committed STARTED evidence without creating a database or guessing from elapsed time."""
    if not db.is_file():
        return None
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='bridge_deliveries'").fetchone():
            return None
        row = conn.execute("SELECT external_session_id FROM bridge_deliveries WHERE peer_id=? "
                           "AND certainty='STARTED' AND acked=0 ORDER BY rowid DESC LIMIT 1", (peer,)).fetchone()
    return row[0] if row else None
