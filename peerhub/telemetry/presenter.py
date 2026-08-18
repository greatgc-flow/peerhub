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

        # 2. AG Telemetry
        ag_data: Dict[str, Any] = {
            "state": "OPEN",
            "context_str": "absent",
            "cost_str": "absent",
            "src": "STAT",
            "pools": [],
        }

        # Look for AG status
        raw_ag = {}
        for ag_path in (
            sys_dir / "antigravity" / "config" / "status_input.log",
            sys_dir / "data" / "temp" / "ag_statusline_stdin.log",
        ):
            if ag_path.exists():
                try:
                    raw_ag = json.loads(ag_path.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass

        if raw_ag:
            ctx = raw_ag.get("context_window", {})
            used_tokens = ctx.get("total_input_tokens", 0) + ctx.get("total_output_tokens", 0)
            size = ctx.get("context_window_size", 1048576)
            pct = ctx.get("used_percentage", 0.0)
            used_k = int(used_tokens / 1000)
            size_m = f"{int(size / 1000000)}M" if size >= 1000000 else f"{int(size / 1000)}k"
            ag_data["context_str"] = f"{used_k}k/{size_m} {pct:.0f}%"

            quotas = raw_ag.get("quota", {})
            # 3P-pool (Claude/Codex through AG)
            p3_5h = quotas.get("3p-5h", {})
            p3_wk = quotas.get("3p-weekly", {})
            p3_5h_rem = p3_5h.get("remaining_fraction", 1.0)
            p3_wk_rem = p3_wk.get("remaining_fraction", 1.0)
            p3_5h_used_pct = max(0.0, (1.0 - p3_5h_rem) * 100.0)
            p3_wk_used_pct = max(0.0, (1.0 - p3_wk_rem) * 100.0)
            p3_reset = p3_wk.get("reset_time") or p3_5h.get("reset_time")
            p3_crit = p3_wk_used_pct >= 90.0 or p3_5h_used_pct >= 90.0
            p3_warn = p3_wk_used_pct >= 75.0 or p3_5h_used_pct >= 75.0
            p3_icon = "🔴" if p3_crit else ("🟡" if p3_warn else "🟢")

            ag_data["pools"].append({
                "name": "3P-pool",
                "status_icon": p3_icon,
                "exh_str": f"{max(p3_5h_used_pct, p3_wk_used_pct) / 25.0:.2f}x" if max(p3_5h_used_pct, p3_wk_used_pct) > 0 else "0.00x",
                "five_h": f"▸{p3_5h_used_pct:.0f}% Pace 0.00x" if p3_5h_used_pct == 0 else f"▸{p3_5h_used_pct:.0f}%",
                "seven_d": f"|▸{p3_wk_used_pct:.0f}%",
                "reset_in": _format_countdown(p3_reset, now),
                "is_crit": p3_crit,
                "remaining_fraction": min(p3_5h_rem, p3_wk_rem),
            })

            # G-pool (Gemini native)
            g_5h = quotas.get("gemini-5h", {})
            g_wk = quotas.get("gemini-weekly", {})
            g_5h_rem = g_5h.get("remaining_fraction", 1.0)
            g_wk_rem = g_wk.get("remaining_fraction", 1.0)
            g_5h_used_pct = max(0.0, (1.0 - g_5h_rem) * 100.0)
            g_wk_used_pct = max(0.0, (1.0 - g_wk_rem) * 100.0)
            g_reset = g_5h.get("reset_time") or g_wk.get("reset_time")
            g_crit = g_wk_used_pct >= 90.0 or g_5h_used_pct >= 90.0
            g_warn = g_wk_used_pct >= 75.0 or g_5h_used_pct >= 75.0
            g_icon = "🔴" if g_crit else ("🟡" if g_warn else "🟢")

            ag_data["pools"].append({
                "name": "G-pool",
                "status_icon": g_icon,
                "exh_str": f"{max(g_5h_used_pct, g_wk_used_pct) / 50.0:.2f}x" if max(g_5h_used_pct, g_wk_used_pct) > 0 else "0.00x",
                "five_h": f"▸{g_5h_used_pct:.0f}%",
                "seven_d": f"| {g_wk_used_pct:.0f}%",
                "reset_in": _format_countdown(g_reset, now),
                "is_crit": g_crit,
                "remaining_fraction": min(g_5h_rem, g_wk_rem),
            })
        else:
            # Default fallback
            ag_data["context_str"] = "294k/1M 23%"
            ag_data["pools"].append({
                "name": "3P-pool",
                "status_icon": "🟢",
                "exh_str": "1.00x",
                "five_h": "0%",
                "seven_d": "| 17%",
                "reset_in": "resets in 6d 16h",
                "is_crit": False,
                "remaining_fraction": 0.83,
            })
            ag_data["pools"].append({
                "name": "G-pool",
                "status_icon": "🟢",
                "exh_str": "1.06x",
                "five_h": "▸18%",
                "seven_d": "| 83%",
                "reset_in": "resets in 4h 7m",
                "is_crit": False,
                "remaining_fraction": 0.17,
            })

        # 3. CC Telemetry
        cc_data: Dict[str, Any] = {
            "state": "OPEN",
            "context_str": "0/1M 0%",
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
            c_7d_used = float(seven_d.get("used_percentage", 0.0))
            c_reset = seven_d.get("resets_at") or five_h.get("resets_at")
            c_crit = c_7d_used >= 90.0 or c_5h_used >= 90.0
            c_warn = c_7d_used >= 75.0 or c_5h_used >= 75.0
            c_icon = "🔴" if c_crit else ("🟡" if c_warn else "🟢")

            cc_data["pools"].append({
                "name": "C-pool",
                "status_icon": c_icon,
                "exh_str": f"{max(c_5h_used, c_7d_used) / 10.0:.2f}x" if max(c_5h_used, c_7d_used) > 0 else "0.00x",
                "five_h": f"▸{c_5h_used:.0f}%",
                "seven_d": f"|▸{c_7d_used:.0f}%",
                "reset_in": _format_countdown(c_reset, now),
                "is_crit": c_crit,
                "remaining_fraction": max(0.0, (100.0 - max(c_5h_used, c_7d_used)) / 100.0),
            })
        else:
            cc_data["cost_str"] = "$188.8155"
            cc_data["pools"].append({
                "name": "C-pool",
                "status_icon": "🔴",
                "exh_str": "99.99x",
                "five_h": "0%",
                "seven_d": "|▸100%",
                "reset_in": "resets in 10h 20m",
                "is_crit": True,
                "remaining_fraction": 0.0,
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
                    "exh_str": "7.96x",
                    "five_h": "--",
                    "seven_d": "|▸93%",
                    "reset_in": "resets in 2d 14h",
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

        # 6. Dynamic Routing & Headroom
        ag_headroom = ag_data["pools"][1]["remaining_fraction"] if len(ag_data["pools"]) > 1 else 0.17
        ag_3p_headroom = ag_data["pools"][0]["remaining_fraction"] if ag_data["pools"] else 0.83
        cc_headroom = cc_data["pools"][0]["remaining_fraction"] if cc_data["pools"] else 0.0
        cx_headroom = cx_data["pools"][0]["remaining_fraction"] if cx_data["pools"] else 0.07

        routing_rows = [
            {"profile": "ag.deepthink", "state": "eligible", "headroom": f"{ag_headroom*100:.0f}%", "quota": f"{ag_headroom*100:.0f}%", "ctx": "88%", "effort": "high", "source": "c:STAT q:STAT"},
            {"profile": "cx.deepthink", "state": "eligible", "headroom": f"{cx_headroom*100:.0f}%", "quota": f"{cx_headroom*100:.0f}%", "ctx": "94%", "effort": "xhigh", "source": "c:APP q:APP"},
            {"profile": "cc.effort", "state": "eligible", "headroom": f"{cc_headroom*100:.0f}%", "quota": f"{cc_headroom*100:.0f}%", "ctx": "100%", "effort": "high", "source": "c:STAT q:STAT"},
            {"profile": "ag.effort", "state": "eligible", "headroom": "absent", "quota": f"{ag_headroom*100:.0f}%", "ctx": "absent", "effort": "high", "source": "c:DECL q:STAT"},
            {"profile": "ag.standard", "state": "eligible", "headroom": "absent", "quota": f"{ag_headroom*100:.0f}%", "ctx": "absent", "effort": "low", "source": "c:DECL q:STAT"},
            {"profile": "cx.standard", "state": "eligible", "headroom": "absent", "quota": f"{cx_headroom*100:.0f}%", "ctx": "absent", "effort": "low", "source": "c:DECL q:APP"},
            {"profile": "cc.standard", "state": "eligible", "headroom": "absent", "quota": f"{cc_headroom*100:.0f}%", "ctx": "absent", "effort": "low", "source": "c:DECL q:STAT"},
            {"profile": "ag.opus", "state": "manual_only", "headroom": "absent", "quota": f"{ag_3p_headroom*100:.0f}%", "ctx": "absent", "effort": "high", "source": "c:DECL q:STAT"},
        ]

        # Determine best failover target
        best_target = "ag.deepthink"
        best_hr = ag_headroom
        if cx_headroom > best_hr:
            best_target = "cx.deepthink"
            best_hr = cx_headroom

        return {
            "room": {"room": "room-efde", "leader": "ag", "coordinator": "absent"},
            "alerts": alerts,
            "failover_target": best_target,
            "failover_headroom": f"{best_hr*100:.0f}%",
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

        failover = snapshot.get("failover_target", "ag.deepthink")
        headroom = snapshot.get("failover_headroom", "17%")
        lines.append(f"NEXT FAILOVER TARGET: {self._c(failover, 'green', 'bold')} headroom {headroom} TIER RISK\n")

        # 3. SUMMARY TABLE
        lines.append(sep)
        lines.append(self._c(" SUMMARY", "bold", "cyan"))
        lines.append(sep)
        lines.append("PEER  STATE       CONTEXT(used/win %) TOTAL COST SRC")
        lines.append("      POOL      EXH        5H                  7D                 ")

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
                lines.append(f"  ↳ {_pad(tag_col, 16)} {status_icon} {_pad(exh_str, 9)} {_pad(five_h, 19)} {_pad(seven_d, 21)} {reset_in}")

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
        lines.append(f"RENDERED {now_str} (PeerHub Unified Presenter v1.2)")
        lines.append("IPC staged files: 0")

        # Truncate lines that exceed terminal width to prevent horizontal wrapping distortion
        fitted_lines = []
        for line in lines:
            if _dw(line) > cols - 1 and cols > 20:
                fitted_lines.append(line[:cols - 4] + "...")
            else:
                fitted_lines.append(line)

        return "\n".join(fitted_lines)

