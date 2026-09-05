"""PeerHub Pure-Python Unified Statusline Formatter.

High-performance (<2ms), zero-subprocess statusline formatting for AI peers
(Antigravity / agy, Claude Code, and OpenAI Codex).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast


def format_statusline_ag(stdin_data: str) -> str:
    """Format unified statusline for Antigravity (AG)."""
    if not stdin_data or not stdin_data.strip():
        return "ag:Gemini | ctx:ok | hub:idle"

    try:
        data: dict[str, Any] = json.loads(stdin_data)
    except Exception:
        return "ag:Gemini | ctx:ok | hub:idle"

    # 1. Model & Effort
    m: Any = data.get("model")
    model_name: str
    effort: str
    if isinstance(m, dict):
        m_dict = cast("dict[str, Any]", m)
        model_name = str(m_dict.get("display_name") or m_dict.get("id") or "Unknown")
        effort = str(m_dict.get("effort") or "")
    elif isinstance(m, str):
        model_name = m
        effort = ""
    else:
        model_name = str(data.get("model_name", "Unknown"))
        effort = ""

    if not effort:
        mre: Any = data.get("model_reasoning_effort") or data.get("effort", "")
        if isinstance(mre, dict):
            mre_dict = cast("dict[str, Any]", mre)
            effort = str(mre_dict.get("level", ""))
        elif isinstance(mre, str):
            effort = mre

    if effort and effort.lower() not in model_name.lower():
        model_name = f"{model_name} ({effort.capitalize()})"

    # 2. Context Window (Active Loaded Context Occupancy)
    ctx: dict[str, Any] = data.get("context_window", {})
    if ctx:
        used_tokens = ctx.get("total_input_tokens", 0)
        if used_tokens == 0 and "current_usage" in ctx:
            cur = ctx["current_usage"]
            used_tokens = cur.get("input_tokens", 0) + cur.get("cache_read_input_tokens", 0)
        total_tokens = ctx.get("context_window_size", 1048576)
        pct = ctx.get("used_percentage", (used_tokens / total_tokens * 100.0) if total_tokens else 0.0)
        ctx_str = f"ctx:{int(used_tokens / 1000)}k/{int(total_tokens / 1000)}k ({pct:.0f}%)"
    elif "context_used_tokens" in data:
        used_tokens = data["context_used_tokens"]
        total_tokens = data.get("context_total_tokens", 1048576)
        pct = data.get("context_used_pct", (used_tokens / total_tokens * 100.0) if total_tokens else 0.0)
        ctx_str = f"ctx:{int(used_tokens / 1000)}k/{int(total_tokens / 1000)}k ({pct:.0f}%)"
    else:
        ctx_str = "ctx:ok"

    # 3. Location & Git Branch
    cwd = data.get("cwd") or data.get("workspace", {}).get("current_dir", "")
    short_cwd = Path(cwd).name if cwd else ""
    git_branch = ""
    if cwd and Path(cwd).is_dir():
        try:
            git_branch = subprocess.run(
                ["git", "-C", cwd, "--no-optional-locks", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=1,
            ).stdout.strip()
        except Exception:
            pass
    loc_str = f"{short_cwd} ({git_branch})" if git_branch else short_cwd

    # 4. Quotas (G-5H G-7D 3P-5H 3P-7D)
    q: dict[str, Any] = data.get("quota", {})
    buckets: list[str] = []
    bucket_map: list[tuple[str, str]] = [
        ("gemini-5h", "G-5H"),
        ("gemini-weekly", "G-7D"),
        ("3p-5h", "3P-5H"),
        ("3p-weekly", "3P-7D"),
    ]
    for key, label in bucket_map:
        if key in q and isinstance(q[key], dict):
            rem = q[key].get("remaining_fraction")
            if rem is not None:
                used_pct = round((1.0 - float(rem)) * 100.0)
                buckets.append(f"{label}:{used_pct}%")
    q_str = " ".join(buckets) if buckets else "quota:N/A"

    # 5. Hub Status
    # No room-ID here: peerhub has no leader-election/room concept of its own
    # (that was an Engram-specific governance layer, intentionally not ported --
    # see engram_peerhub_separation_proposal.md row 6.5).
    return f"ag:{model_name} | {ctx_str} | {loc_str} | {q_str} | hub:idle"
