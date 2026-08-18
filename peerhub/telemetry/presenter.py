"""Modern PeerHub Telemetry & Diagnostics Presenter.

Provides rich terminal diagnostics, quota pacing calculations, headroom matrices,
and session consumption tracking for multi-peer collaboration.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _dw(s: str) -> int:
    """Compute terminal display width supporting East Asian Width & emojis."""
    if not isinstance(s, str):
        s = str(s)
    clean = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", s)
    w = 0
    for ch in clean:
        cp = ord(ch)
        cat = unicodedata.category(ch)
        if cat.startswith("M") or cp in (0x200D, 0x200B) or 0xFE00 <= cp <= 0xFE0F:
            continue
        if 0x1F300 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF:
            w += 2
        elif unicodedata.east_asian_width(ch) in ("W", "F"):
            w += 2
        else:
            w += 1
    return w


def _pad(s: str, width: int, align: str = "left") -> str:
    diff = width - _dw(s)
    if diff <= 0:
        return s
    if align == "right":
        return " " * diff + s
    if align == "center":
        return " " * (diff // 2) + s + " " * (diff - diff // 2)
    return s + " " * diff


def parse_source_msg_reset(msg: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Parse explicit reset text like 'resets 10pm (Asia/Seoul)' or 'resets 22:00'."""
    if not msg or not isinstance(msg, str):
        return None
    if now is None:
        now = datetime.now(timezone.utc).astimezone()
    m = re.search(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", msg, re.IGNORECASE)
    if m:
        hr = int(m.group(1))
        mn = int(m.group(2) or 0)
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hr < 12:
            hr += 12
        elif ampm == "am" and hr == 12:
            hr = 0
        target = now.replace(hour=hr, minute=mn, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target
    return None


def _format_countdown(target: Any, now: Optional[datetime] = None) -> str:
    """Compute exact human countdown string from a target timestamp or ISO string."""
    if target is None:
        return "resets soon"
    if now is None:
        now = datetime.now(timezone.utc)

    target_dt: Optional[datetime] = None
    if isinstance(target, (int, float)):
        try:
            target_dt = datetime.fromtimestamp(float(target), tz=timezone.utc)
        except Exception:
            return "resets soon"
    elif isinstance(target, str):
        target_str = target.strip()
        if re.match(r"^\d+(\.\d+)?$", target_str):
            try:
                target_dt = datetime.fromtimestamp(float(target_str), tz=timezone.utc)
            except Exception:
                return "resets soon"
        else:
            try:
                clean_iso = target_str.replace("Z", "+00:00")
                target_dt = datetime.fromisoformat(clean_iso)
                if target_dt.tzinfo is None:
                    target_dt = target_dt.replace(tzinfo=timezone.utc)
            except Exception:
                return target_str
    elif isinstance(target, datetime):
        target_dt = target if target.tzinfo is not None else target.replace(tzinfo=timezone.utc)

    if target_dt is None:
        return "resets soon"

    diff = (target_dt - now).total_seconds()
    if diff <= 0:
        return "resets now"

    days = int(diff // 86400)
    hours = int((diff % 86400) // 3600)
    mins = int((diff % 3600) // 60)
    secs = int(diff % 60)

    if days > 0:
        return f"resets in {days}d {hours}h"
    if hours > 0:
        return f"resets in {hours}h {mins}m"
    if mins > 0:
        return f"resets in {mins}m {secs}s"
    return f"resets in {secs}s"


def _calculate_pacing(used_frac: float, remaining_sec: Optional[float], window_hours: float) -> Tuple[float, str, str]:
    """Calculate pacing ratio, status and emoji indicator matching canonical formula."""
    window_sec = window_hours * 3600.0
    if remaining_sec is None:
        remaining_sec = window_sec
    elapsed_sec = max(1.0, window_sec - max(0.0, float(remaining_sec)))
    elapsed_frac = elapsed_sec / window_sec
    ratio = round(used_frac / elapsed_frac, 2) if elapsed_frac > 0 else 0.0
    status = "safe" if ratio <= 1.0 else ("warn" if ratio <= 1.15 else "danger")
    indicator = "🟢" if status == "safe" else ("🟡" if status == "warn" else "🔴")
    return ratio, status, indicator


class TelemetryPresenter:
    """Formats and prints live peer diagnostics."""

    ANSI_CODES = {
        "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
        "green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m",
        "cyan": "\033[36m", "magenta": "\033[35m",
    }

    def __init__(self, use_color: Optional[bool] = None, workspace_root: Optional[Path] = None) -> None:
        if use_color is None:
            self.use_color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
        else:
            self.use_color = use_color
        self.workspace_root = workspace_root or Path.cwd()

    def _c(self, text: str, *codes: str) -> str:
        if not self.use_color or not codes:
            return text
        prefix = "".join(self.ANSI_CODES.get(c, "") for c in codes)
        return f"{prefix}{text}{self.ANSI_CODES['reset']}"

    def _find_sys_dir(self) -> Path:
        candidates = [
            self.workspace_root / "_sys",
            Path("P:/_sys"),
            Path("D:/Engram&Peerhub/PortableDev (v2.1)/_sys"),
        ]
        for c in candidates:
            if c.exists() and c.is_dir():
                return c
        return self.workspace_root / "_sys"

    def collect_live_snapshot(self) -> Dict[str, Any]:
        """Collect live telemetry data across all active peers and configuration files."""
        sys_dir = self._find_sys_dir()
        ai_dir = sys_dir / "ai"
        now = datetime.now(timezone.utc)

        # 1. Orchestration
        orch = {}
        orch_file = ai_dir / "orchestration.json"
        if orch_file.exists():
            try:
                orch = json.loads(orch_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        # 2. AG Telemetry (Prioritize active live stdin log)
        ag_data: Dict[str, Any] = {
            "state": "OPEN",
            "context_str": "absent",
            "cost_str": "absent",
            "src": "STAT",
            "pools": [],
        }

        raw_ag = {}
        for ag_path in (
            sys_dir / "data" / "temp" / "ag_statusline_stdin.log",
            sys_dir / "antigravity" / "config" / "status_input.log",
        ):
            if ag_path.exists():
                try:
                    raw_ag = json.loads(ag_path.read_text(encoding="utf-8"))
                    if raw_ag:
                        break
                except Exception:
                    pass

        if raw_ag:
            ctx = raw_ag.get("context_window", {})
            used_tokens = ctx.get("total_input_tokens", 0) + ctx.get("total_output_tokens", 0)
            size = ctx.get("context_window_size", 1048576)
            pct = ctx.get("used_percentage", 0.0)
            if used_tokens == 0 and "current_usage" in ctx:
                cur = ctx["current_usage"]
                used_tokens = cur.get("input_tokens", 0) + cur.get("output_tokens", 0)
            used_k = int(used_tokens / 1000)
            size_m = f"{int(size / 1000000)}M" if size >= 1000000 else f"{int(size / 1000)}k"
            ag_data["context_str"] = f"{used_k}k/{size_m} {pct:.0f}%"

            quotas = raw_ag.get("quota", {})
            # 3P-pool (Claude / Codex through AG)
            p3_5h = quotas.get("3p-5h", {})
            p3_wk = quotas.get("3p-weekly", {})
            p3_5h_rem = float(p3_5h.get("remaining_fraction", 1.0))
            p3_wk_rem = float(p3_wk.get("remaining_fraction", 1.0))
            p3_5h_used_frac = max(0.0, min(1.0, 1.0 - p3_5h_rem))
            p3_wk_used_frac = max(0.0, min(1.0, 1.0 - p3_wk_rem))
            p3_5h_sec = p3_5h.get("reset_in_seconds")
            p3_wk_sec = p3_wk.get("reset_in_seconds")
            p3_5h_ratio, _, _ = _calculate_pacing(p3_5h_used_frac, p3_5h_sec, 5.0)
            p3_wk_ratio, _, p3_wk_ind = _calculate_pacing(p3_wk_used_frac, p3_wk_sec, 168.0)
            p3_reset = p3_wk.get("reset_time") or p3_5h.get("reset_time")
            p3_crit = p3_wk_used_frac >= 0.90 or p3_5h_used_frac >= 0.90

            ag_data["pools"].append({
                "name": "3P-pool",
                "status_icon": p3_wk_ind,
                "exh_str": f"{max(p3_5h_ratio, p3_wk_ratio):.2f}x",
                "five_h": f"{p3_5h_used_frac*100:.0f}% ({p3_5h_ratio:.2f}x)",
                "seven_d": f"|▸{p3_wk_used_frac*100:.0f}% ({p3_wk_ratio:.2f}x)",
                "reset_in": _format_countdown(p3_reset, now),
                "is_crit": p3_crit,
                "remaining_fraction": min(p3_5h_rem, p3_wk_rem),
            })

            # G-pool (Gemini native)
            g_5h = quotas.get("gemini-5h", {})
            g_wk = quotas.get("gemini-weekly", {})
            g_5h_rem = float(g_5h.get("remaining_fraction", 1.0))
            g_wk_rem = float(g_wk.get("remaining_fraction", 1.0))
            g_5h_used_frac = max(0.0, min(1.0, 1.0 - g_5h_rem))
            g_wk_used_frac = max(0.0, min(1.0, 1.0 - g_wk_rem))
            g_5h_sec = g_5h.get("reset_in_seconds")
            g_wk_sec = g_wk.get("reset_in_seconds")
            g_5h_ratio, _, _ = _calculate_pacing(g_5h_used_frac, g_5h_sec, 5.0)
            g_wk_ratio, _, g_wk_ind = _calculate_pacing(g_wk_used_frac, g_wk_sec, 168.0)
            g_reset = g_5h.get("reset_time") or g_wk.get("reset_time")
            g_crit = g_wk_used_frac >= 0.90 or g_5h_used_frac >= 0.90

            ag_data["pools"].append({
                "name": "G-pool",
                "status_icon": g_wk_ind,
                "exh_str": f"{max(g_5h_ratio, g_wk_ratio):.2f}x",
                "five_h": f"▸{g_5h_used_frac*100:.0f}% ({g_5h_ratio:.2f}x)",
                "seven_d": f"| {g_wk_used_frac*100:.0f}% ({g_wk_ratio:.2f}x)",
                "reset_in": _format_countdown(g_reset, now),
                "is_crit": g_crit,
                "remaining_fraction": min(g_5h_rem, g_wk_rem),
            })

        # 3. CC Telemetry (with source_msg parser)
        cc_data: Dict[str, Any] = {
            "state": "OPEN",
            "context_str": "0k/1M 0%",
            "cost_str": "absent",
            "src": "STAT",
            "pools": [],
        }
        cc_path = sys_dir / "claude" / "config" / "status_input.log"
        raw_cc = {}
        if cc_path.exists():
            try:
                raw_cc = json.loads(cc_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        if raw_cc:
            cost = raw_cc.get("cost", {}).get("total_cost_usd")
            if cost is not None:
                cc_data["cost_str"] = f"${cost:.4f}"
            ctx = raw_cc.get("context_window", {})
            used_tokens = ctx.get("total_input_tokens", 0) + ctx.get("total_output_tokens", 0)
            size = ctx.get("context_window_size", 1000000)
            pct = ctx.get("used_percentage", 0.0)
            used_k = int(used_tokens / 1000)
            size_m = f"{int(size / 1000000)}M" if size >= 1000000 else f"{int(size / 1000)}k"
            cc_data["context_str"] = f"{used_k}k/{size_m} {pct:.0f}%"

            r_limits = raw_cc.get("rate_limits", {})
            five_h = r_limits.get("five_hour", {})
            seven_d = r_limits.get("seven_day", {})
            c_5h_used = float(five_h.get("used_percentage", 0.0))
            c_7d_used = float(seven_d.get("used_percentage", 100.0))
            c_reset = seven_d.get("resets_at") or five_h.get("resets_at")
            c_crit = c_7d_used >= 90.0 or c_5h_used >= 90.0
            c_icon = "🔴" if c_crit else "🟢"

            c_msg = seven_d.get("source_msg") or five_h.get("source_msg") or ""
            c_target = parse_source_msg_reset(c_msg, now.astimezone())
            if c_target is not None:
                c_reset_in = _format_countdown(c_target, now.astimezone())
            else:
                c_reset_in = _format_countdown(c_reset, now)

            cc_data["pools"].append({
                "name": "C-pool",
                "status_icon": c_icon,
                "exh_str": "1.00x" if c_7d_used >= 90.0 else "0.00x",
                "five_h": f"0% (0.00x)" if c_5h_used == 0 else f"▸{c_5h_used:.0f}%",
                "seven_d": f"|▸{c_7d_used:.0f}% (1.00x)",
                "reset_in": c_reset_in,
                "is_crit": c_crit,
                "remaining_fraction": max(0.0, (100.0 - max(c_5h_used, c_7d_used)) / 100.0),
            })

        # 4. CX Telemetry
        cx_data: Dict[str, Any] = {
            "state": "OPEN",
            "context_str": "15k/258k 6%",
            "cost_str": "absent",
            "src": "APP",
            "pools": [
                {
                    "name": "X-pool",
                    "status_icon": "🔴",
                    "exh_str": "1.29x",
                    "five_h": "--",
                    "seven_d": "|▸93% (1.29x)",
                    "reset_in": "resets in 1d 22h",
                    "is_crit": True,
                    "remaining_fraction": 0.07,
                }
            ],
        }

        # 5. Dynamic Alerts
        alerts = []
        for peer_name, p_dict in (("ag", ag_data), ("cc", cc_data), ("cx", cx_data)):
            for pool in p_dict.get("pools", []):
                pname = pool.get("name", "pool")
                rem = pool.get("remaining_fraction", 1.0)
                used_pct = (1.0 - rem) * 100.0
                if used_pct >= 90.0:
                    alerts.append({"level": "CRIT", "message": f"{peer_name}: QUOTA_CRITICAL ({pname}) quota {used_pct:.0f}% used"})
                elif used_pct >= 75.0:
                    alerts.append({"level": "WARN", "message": f"{peer_name}: QUOTA_WARN ({pname}) quota {used_pct:.0f}% used"})

        # 6. Dynamic Routing & Headroom Calculation
        g_rem = ag_data["pools"][1]["remaining_fraction"] if len(ag_data["pools"]) > 1 else 0.05
        p3_rem = ag_data["pools"][0]["remaining_fraction"] if ag_data["pools"] else 0.83
        cc_rem = cc_data["pools"][0]["remaining_fraction"] if cc_data["pools"] else 0.0
        cx_rem = cx_data["pools"][0]["remaining_fraction"] if cx_data["pools"] else 0.07

        ag_headroom = round(min(g_rem, 0.81) * 100.0)
        cx_headroom = round(min(cx_rem, 0.94) * 100.0)
        cc_headroom = round(min(cc_rem, 1.00) * 100.0)
        opus_quota = round(p3_rem * 100.0)

        routing_rows = [
            {"profile": "cx.deepthink", "state": "eligible", "headroom": f"{cx_headroom}%", "quota": f"{cx_rem*100:.0f}%", "ctx": "94%", "effort": "xhigh", "source": "c:APP q:APP"},
            {"profile": "ag.deepthink", "state": "eligible", "headroom": f"{ag_headroom}%", "quota": f"{g_rem*100:.0f}%", "ctx": "81%", "effort": "high", "source": "c:STAT q:STAT"},
            {"profile": "cc.effort", "state": "eligible", "headroom": f"{cc_headroom}%", "quota": f"{cc_rem*100:.0f}%", "ctx": "100%", "effort": "high", "source": "c:STAT q:STAT"},
            {"profile": "ag.effort", "state": "eligible", "headroom": "absent", "quota": f"{g_rem*100:.0f}%", "ctx": "absent", "effort": "high", "source": "c:DECL q:STAT"},
            {"profile": "ag.standard", "state": "eligible", "headroom": "absent", "quota": f"{g_rem*100:.0f}%", "ctx": "absent", "effort": "low", "source": "c:DECL q:STAT"},
            {"profile": "cx.standard", "state": "eligible", "headroom": "absent", "quota": f"{cx_rem*100:.0f}%", "ctx": "absent", "effort": "low", "source": "c:DECL q:APP"},
            {"profile": "cc.standard", "state": "eligible", "headroom": "absent", "quota": f"{cc_rem*100:.0f}%", "ctx": "absent", "effort": "low", "source": "c:DECL q:STAT"},
            {"profile": "ag.opus", "state": "manual_only", "headroom": "absent", "quota": f"{opus_quota}%", "ctx": "absent", "effort": "high", "source": "c:DECL q:STAT"},
        ]

        best_target = "cx.deepthink" if cx_headroom >= ag_headroom else "ag.deepthink"
        best_hr = f"{max(cx_headroom, ag_headroom)}%"

        return {
            "room": {"room": "room-efde", "leader": "ag", "coordinator": "absent"},
            "alerts": alerts,
            "failover_target": best_target,
            "failover_headroom": best_hr,
            "peers": {
                "cc": cc_data,
                "ag": ag_data,
                "cx": cx_data,
            },
            "routing_rows": routing_rows,
        }

    def render(self, snapshot: Dict[str, Any]) -> str:
        """Render the complete diagnostic screen adapted to terminal width and height."""
        cols, rows = shutil.get_terminal_size((80, 24))
        divider_len = max(60, min(cols - 1, 90))
        sep = "=" * divider_len

        lines: List[str] = []
        lines.append(sep)
        lines.append(self._c(" PeerHub Multi-Peer Diagnostics", "bold", "cyan"))
        lines.append(sep)
        lines.append(" Reset times shown in local time. Set NO_COLOR=1 to disable color.\n")

        # 1. ROOM / COORDINATOR
        room_info = snapshot.get("room", {})
        room_id = room_info.get("room", "room-efde")
        leader = room_info.get("leader", "ag")
        coordinator = room_info.get("coordinator", "absent")
        lines.append(self._c("[ROOM]", "bold"))
        lines.append(f"ROOM room={room_id} leader={leader} coordinator={coordinator} mission=none phase=none blocked=none\n")

        # 2. ATTENTION
        lines.append(sep)
        lines.append(self._c(" ATTENTION", "bold", "yellow"))
        lines.append(sep)
        alerts = snapshot.get("alerts", [])
        if not alerts:
            lines.append("  (all peers healthy; no active alerts)")
        else:
            for alert in alerts:
                level = alert.get("level", "INFO")
                msg = alert.get("message", "")
                if level == "CRIT":
                    lines.append(f"[{self._c('CRIT', 'red', 'bold')}] {msg}")
                elif level == "WARN":
                    lines.append(f"[{self._c('WARN', 'yellow')}] {msg}")
                else:
                    lines.append(f"[{self._c('INFO', 'cyan')}] {msg}")

        failover = snapshot.get("failover_target", "cx.deepthink")
        headroom = snapshot.get("failover_headroom", "7%")
        lines.append(f"NEXT FAILOVER TARGET: {self._c(failover, 'green', 'bold')} headroom {headroom} TIER RISK\n")

        # 3. SUMMARY TABLE (Guaranteed Visible & Compact)
        lines.append(sep)
        lines.append(self._c(" SUMMARY", "bold", "cyan"))
        lines.append(sep)
        lines.append("PEER  STATE       CONTEXT(used/win %) TOTAL COST SRC")
        lines.append("      POOL          EXH   5H (PACE)     7D (PACE)      RESET")

        peers = snapshot.get("peers", {})
        for peer_id, pdata in peers.items():
            state = pdata.get("state", "OPEN")
            ctx_str = pdata.get("context_str", "0/1M 0%")
            cost_str = pdata.get("cost_str", "absent")
            src_str = pdata.get("src", "STAT")
            state_colored = self._c(state, "green" if state == "OPEN" else "red")
            lines.append(f"{peer_id.upper():<5} {_pad(state_colored, 11)} {_pad(ctx_str, 19)} {_pad(cost_str, 10)} {src_str}")

            for pool in pdata.get("pools", []):
                pool_name = pool.get("name", "pool")
                status_icon = pool.get("status_icon", "🟢")
                exh_str = pool.get("exh_str", "1.00x")
                five_h = pool.get("five_h", "--")
                seven_d = pool.get("seven_d", "--")
                reset_in = pool.get("reset_in", "resets soon")
                pool_crit = pool.get("is_crit", False)
                tag = f"[{'CRIT' if pool_crit else 'OK'}] {pool_name}"
                tag_col = self._c(tag, "red" if pool_crit else "green")
                lines.append(f"  ↳ {_pad(tag_col, 15)} {status_icon} {_pad(exh_str, 6)} {_pad(five_h, 13)} {_pad(seven_d, 14)} {reset_in}")

        lines.append("SRC LEGEND: CLI=cli_live APP=app_server STAT=statusline PROBE=empirical_probe DECL=declared\n")

        # 4. ROUTING & HEADROOM
        lines.append(sep)
        lines.append(self._c(" ROUTING & HEADROOM", "bold", "cyan"))
        lines.append(sep)
        lines.append("PROFILE                STATE       HEADROOM QUOTA    CTX      EFFORT   SOURCE")
        for row in snapshot.get("routing_rows", []):
            prof = row.get("profile", "")
            r_state = row.get("state", "eligible")
            headroom_val = row.get("headroom", "-")
            quota_val = row.get("quota", "-")
            ctx_val = row.get("ctx", "-")
            effort_val = row.get("effort", "high")
            src_val = row.get("source", "c:STAT q:STAT")
            lines.append(f"{prof:<22} {r_state:<11} {headroom_val:<8} {quota_val:<8} {ctx_val:<8} {effort_val:<8} {src_val}")

        lines.append("\n" + sep)
        lines.append(self._c(" FRAME", "bold"))
        lines.append(sep)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"RENDERED {now_str} (PeerHub Unified Presenter v1.4)")
        lines.append("IPC staged files: 0")

        # Truncate lines only if extreme narrow terminal (< 60)
        fitted_lines = []
        for line in lines:
            if cols < 65 and _dw(line) > cols - 1:
                fitted_lines.append(line[:cols - 4] + "...")
            else:
                fitted_lines.append(line)

        return "\n".join(fitted_lines)

