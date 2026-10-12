"""Pure, width-aware A-G dashboard presentation of committed diagnostic evidence."""
from __future__ import annotations

import math
import os
import re
import textwrap
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, tzinfo
from typing import cast

from peerhub.extensions.diag import DiagnosticReport
from peerhub.extensions.diag_quota import _projection

RESET, RED, YELLOW, CYAN = "\033[0m", "\033[31m", "\033[33m", "\033[36m"
_ANSI = re.compile(r"\033\[[0-9;]*m")
Obj = Mapping[str, object]


def _safe_text(value: object) -> str:
    """Remove terminal controls from data before adding presentation escapes."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", str(value))


def visible_len(text: str) -> int:
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in _ANSI.sub("", text))


def _paint(text: str, code: str, on: bool) -> str:
    return f"{code}{text}{RESET}" if on and code else text


def _obj(value: object) -> Obj:
    return cast(Obj, value) if isinstance(value, Mapping) else {}


def _items(value: object) -> list[Obj]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    seq = cast("Sequence[object]", value)
    return [cast(Obj, v) for v in seq if isinstance(v, Mapping)]


def _num(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _section(rep: DiagnosticReport, name: str) -> Obj:
    sec = rep.sections.get(name)
    return _obj(sec.data) if sec is not None else {}


def _ellipsis(text: str, limit: int, unicode: bool = True) -> str:
    if visible_len(text) <= limit:
        return text
    mark = "…" if unicode else "..."
    mark = mark[:max(0, limit)]
    kept = ""
    for char in text:
        if visible_len(kept + char + mark) > limit:
            break
        kept += char
    return kept + mark


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - visible_len(text))


def human_duration(seconds: float | None) -> str:
    """Round to minutes from two minutes onward; missing values stay explicit."""
    if seconds is None or seconds < 0:
        return "?"
    s = int(seconds) if seconds < 120 else int(seconds / 60 + 0.5) * 60
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    if d:
        return f"{d}d{h:02d}h"
    if h:
        return f"{h}h{m:02d}m"
    return (f"{m}m{sec:02d}s" if sec else f"{m}m") if m else f"{sec}s"


def _forecast_duration(seconds: float) -> str:
    # Forecasts never claim seconds, even when exhaustion is imminent.
    return human_duration(seconds) if seconds >= 120 else f"{int(seconds / 60 + 0.5)}m"


def bar(used: float, cells: int, unicode: bool) -> str:
    used = min(1.0, max(0.0, used))
    filled = int(used * cells + 0.5)
    if unicode:
        return "█" * filled + "░" * (cells - filled)
    return "#" * filled + "." * (cells - filled)


def _unknown_bar(cells: int) -> str:
    return "?" * cells


def _local(at: float, tz: tzinfo | None) -> datetime:
    return datetime.fromtimestamp(at, tz) if tz is not None else datetime.fromtimestamp(at).astimezone()


def _eta(at: float, now: float, tz: tzinfo | None) -> str:
    dt = _local(at, tz)
    dated = dt.date() != _local(now, tz).date()
    return dt.strftime("%a %H:%M" if dated else "%H:%M")


class _Row:
    def __init__(self, item: Obj, read_at: float, tz: tzinfo | None = None) -> None:
        payload = _obj(item.get("payload"))
        self.peer = _safe_text(item.get("subject_ref") or "?")
        self.ref = _safe_text(item.get("resource_pool_ref") or "?")
        self.pool = self.ref.rsplit(":", 1)[-1]
        self.state = _safe_text(item.get("state") or "UNKNOWN")
        self.age = _num(item.get("age_seconds"))
        self.reason = _safe_text(payload.get("reason") or "")
        rf = _num(payload.get("remaining_fraction"))
        self.used = 1 - rf if rf is not None and 0 <= rf <= 1 and self.state in ("MEASURED", "STALE") else None
        start, reset = _num(payload.get("window_started_at")), _num(payload.get("resets_at"))
        self.span = reset - start if reset is not None and start is not None and reset > start else float("inf")
        # A declared suffix supplies a display/sort window for a registered
        # unmeasured pool, never an anchor for projection arithmetic.
        suffix = re.search(r"(\d+)([HD])$", self.pool.upper())
        if self.span == float("inf") and suffix and int(suffix[1]) > 0:
            self.span = float(int(suffix[1]) * (3600 if suffix[2] == "H" else 86400))
        self.left = None if reset is None else reset - read_at
        self.projection = _projection({**item, "state": self.state, "payload": dict(payload)}, read_at)
        self.diff = self.projection["pace_pt"]
        self.now, self.tz = read_at, tz

    def forecast(self, unicode: bool = False, short: bool = False) -> str:
        state = self.projection["state"]
        glyph = ({"threat": "▲", "on_pace": "●", "insufficient": "○", "exhausted": "✖",
                  "unknown": "◌", "stale": "△", "expired": "⊘"} if unicode else
                 {"threat": "!", "on_pace": "=", "insufficient": ":", "exhausted": "x",
                  "unknown": "?", "stale": "~", "expired": "/"})[state]
        if state == "threat":
            at = self.projection["exhaustion_at"]
            assert at is not None
            clock, left = _eta(at, self.now, self.tz), _forecast_duration(max(0, at - self.now))
            if short:
                clock = _local(at, self.tz).strftime("%a %H:%M" if _local(at, self.tz).date() != _local(self.now, self.tz).date() else "%H:%M")
                return f"{glyph} exh {clock} ({left})"
            return f"{glyph} exhausts {clock} (in {left})"
        return glyph + " " + {"unknown": "unknown", "stale": "stale", "expired": "expired", "exhausted": "exhausted",
                              "insufficient": "not enough data", "on_pace": "on pace"}[state]

    @property
    def ink(self) -> str:
        return RED if self.projection["state"] in ("threat", "exhausted") else YELLOW if self.projection["state"] in ("stale", "expired", "insufficient") else CYAN if self.projection["state"] == "on_pace" else ""


def _badge(row: _Row, color: bool, unicode: bool) -> str:
    if row.age is None:
        return "?"
    stale = row.state == "STALE"
    dot = ("○" if stale else "●") if unicode else ("~" if stale else "*")
    return dot + human_duration(row.age)


def _quota_rows(items: list[Obj], pools: list[Obj], read_at: float, tz: tzinfo | None = None) -> list[_Row]:
    # A probe without a pool belongs to F. Collapse shared pool evidence to one row.
    selected: dict[str, Obj] = {}
    for item in items:
        ref = item.get("resource_pool_ref")
        if item.get("kind") in ("quota", "rate_limit") and ref:
            key = str(ref)
            old = selected.get(key)
            if old is None or str(item.get("observed_at") or "") > str(old.get("observed_at") or ""):
                selected[key] = item
    for pool in pools:
        ref = pool.get("resource_pool_id")
        if ref and pool.get("kind") in ("QUOTA", "RATE_LIMIT"):
            selected.setdefault(str(ref), {"subject_ref": pool.get("provider"), "resource_pool_ref": ref, "state": "UNKNOWN", "payload": {}})
    return sorted((_Row(i, read_at, tz) for i in selected.values()),
                  key=lambda r: (r.peer, re.sub(r"-?\d+[HD]$", "", r.pool.upper()), r.span, r.ref))


def _quota_table(rows: list[_Row], width: int, color: bool, unicode: bool) -> list[str]:
    cells = 10 if width >= 100 else 8
    peer_w = max(4, max((visible_len(r.peer) for r in rows), default=4))
    pool_w = max(5, max((visible_len(r.pool) for r in rows), default=5))
    reset_w = max(5, max((visible_len(human_duration(r.left)) for r in rows), default=5))
    fresh_w = max(5, max((visible_len(_badge(r, False, unicode)) for r in rows), default=5))
    def prefix(peer: str, pool: str, usage: str, used: str, pace: str, reset: str) -> str:
        return (_pad(peer, peer_w) + " " + _pad(pool, pool_w) + " " +
                f"{usage:<{cells + 2}} {used:>4} {pace:>8} " + _pad(reset, reset_w) + " ")
    head_lead = prefix("PEER", "POOL", "USAGE-bar", "USED", "PACE(pt)", "RESET")
    forecast_w = max(0, width - visible_len(head_lead) - fresh_w - 1)
    head = head_lead + _pad(_ellipsis("FORECAST", forecast_w, unicode), forecast_w) + " FRESH"
    out = [head]
    for row in rows:
        body = bar(row.used, cells, unicode) if row.used is not None else _unknown_bar(cells)
        used = "?" if row.used is None else f"{row.used * 100:.0f}%"
        pace = "?" if row.diff is None else f"{row.diff:+.1f}"
        lead = prefix(row.peer, row.pool, f"[{body}]", used, pace, human_duration(row.left))
        forecast = _ellipsis(row.forecast(unicode, width < 100), forecast_w, unicode)
        fresh = _badge(row, False, unicode)
        out.append(_paint(lead + _pad(forecast, forecast_w) + " " + fresh, row.ink, color))
    return out


def _threat_summary(rows: list[_Row]) -> dict[str, object]:
    fresh = sum(r.projection["state"] in ("threat", "on_pace", "insufficient", "exhausted") for r in rows)
    stale = [r for r in rows if r.state == "STALE"]
    threats = [r for r in rows if r.projection["state"] == "threat"]
    exhausted = [r for r in rows if r.projection["state"] == "exhausted"]
    oldest = max((r.age for r in stale if r.age is not None), default=None)
    state = "threat" if threats or exhausted else "clear" if fresh else "unknown"
    reason = None if fresh else "evidence is stale" if stale else "evidence is missing or invalid"
    return {"state": state, "reason": reason, "fresh_pools": fresh, "stale_pools": len(stale),
            "unknown_pools": len(rows) - fresh - len(stale), "oldest_stale_seconds": oldest,
            "threatened_pools": [r.ref for r in threats], "exhausted_pools": [r.ref for r in exhausted]}


def dashboard_data(report: DiagnosticReport) -> dict[str, object]:
    """Dashboard JSON uses the same evidence coverage and projections as text."""
    data = asdict(report)
    items = data.get("sections", {}).get("observations", {}).get("data", {}).get("items", [])
    for item in items:
        if item.get("kind") in ("quota", "rate_limit"):
            item["projection"] = _projection(item, report.read_at)
    rows = _quota_rows(_items(items), _items(_section(report, "resource_pools").get("pools")), report.read_at)
    data["threats"] = _threat_summary(rows)
    data["forecast_basis"] = "conditional window-average (cond)"
    return data


def _wrap(line: str, width: int) -> list[str]:
    """Wrap evidence instead of dropping pools, asks or alerts; count display cells."""
    if visible_len(line) <= width:
        return [line]
    # Wrapping happens before adding colour, so no escape is split.
    out: list[str] = []
    current = ""
    for word in textwrap.wrap(line, width=width, break_long_words=True, break_on_hyphens=False):
        for char in word:
            if visible_len(current + char) > width:
                out.append(current)
                current = ""
            current += char
        out.append(current)
        current = ""
    return out


def _credit_lines(items: list[Obj], read_at: float, color: bool, unicode: bool, alerts: list[str], tz: tzinfo | None = None) -> list[str]:
    out: list[str] = []
    for item in items:
        if item.get("kind") != "reset_credit":
            continue
        p, state = _obj(item.get("payload")), str(item.get("state") or "UNKNOWN")
        peer = _safe_text(item.get("subject_ref") or "?")
        count = p.get("available_count") if state in ("MEASURED", "STALE") else None
        exp = _num(p.get("nearest_expires_at")) if count is not None else None
        expiry = (_local(exp, tz).strftime("%a %m-%d %H:%M" if _local(exp, tz).date() != _local(read_at, tz).date() else "%H:%M")
                  if exp is not None else "unknown")
        line = f"Credits {peer}:{count if count is not None else '?'} available"
        if exp is not None:
            line += f"; expiry {expiry} (in {human_duration(max(0, exp - read_at))})"
        if state != "MEASURED":
            word = "stale" if state == "STALE" else "unknown"
            glyph = ("△" if word == "stale" else "◌") if unicode else ("~" if word == "stale" else "?")
            line += f"; {glyph} {word} {_badge(_Row(item, read_at), False, unicode)}"
        out.append(line)
        if exp is not None and 0 <= exp - read_at < 72 * 3600 and isinstance(count, int) and count > 0:
            alerts.append(f"{peer} {expiry} in {human_duration(exp - read_at)}")
    return out


def _activity_lines(items: list[Obj], limit: int, color: bool) -> list[str]:
    latest: dict[str, Obj] = {}
    for item in items:
        if item.get("kind") != "activity":
            continue
        peer = _safe_text(item.get("subject_ref") or "?")
        old = latest.get(peer)
        if old is None or (_num(item.get("capture_seq")) or 0, str(item.get("observed_at") or "")) >= (_num(old.get("capture_seq")) or 0, str(old.get("observed_at") or "")):
            latest[peer] = item
    return [f"Asks {peer} {_safe_text(_obj(item.get('payload')).get('status') or 'unknown')} "
            f"{human_duration(_num(item.get('age_seconds')))} ago, took "
            f"{human_duration(_num(_obj(item.get('payload')).get('operation_elapsed_seconds')))}"
            for peer, item in sorted(latest.items())]


def _ask_lines(report: DiagnosticReport, items: list[Obj]) -> list[str]:
    sec = _section(report, "activity")
    out: list[str] = []
    for p in _items(sec.get("peers")):
        if not (_num(p.get("asks")) or _num(p.get("running")) or p.get("last_status")):
            continue
        out.append(f"Asks {_safe_text(p.get('peer'))} {_safe_text(p.get('last_status') or 'unknown')} "
                   f"{human_duration(_num(p.get('last_age_seconds')))} ago, took {human_duration(_num(p.get('last_seconds')))}")
    return out if out else _activity_lines(items, 0, False)


def _probe_line(items: list[Obj], refresh: Obj | None) -> str:
    peers: dict[str, str] = {}
    latest: dict[str, tuple[float, str, int]] = {}
    for item in items:
        if item.get("kind") not in ("quota", "rate_limit", "probe", "collector"):
            continue
        peer = _safe_text(item.get("subject_ref") or "?")
        state, p = item.get("state"), _obj(item.get("payload"))
        key = (_num(item.get("capture_seq")) or 0,
               str(item.get("captured_at") or item.get("observed_at") or ""),
               0 if item.get("resource_pool_ref") else 1)
        # Last committed successful measurement is an OK probe outcome, even
        # after its quota freshness expires. A newer failure takes precedence.
        outcome = "OK" if state in ("MEASURED", "STALE") else "?"
        if not item.get("resource_pool_ref"):
            reason = _safe_text(p.get("reason") or state or "unknown")
            outcome = "TIMEOUT" if state == "TIMEOUT" else f"ERR({reason})" if state in ("ERROR", "UNAVAILABLE") else outcome
            if not item.get("captured_at") and not item.get("observed_at"):
                key = (float("inf"), "", 1)
        if peer not in latest or key >= latest[peer]:
            latest[peer], peers[peer] = key, outcome
    for probe in _items((refresh or {}).get("probes")):
        peer = _safe_text(probe.get("peer") or probe.get("subject_ref") or "?")
        status = _safe_text(probe.get("status") or "?")
        peers[peer] = f"ERR({_safe_text(probe.get('reason') or 'unknown')})" if status == "ERR" else status
    return "Probes " + (" ".join(f"{peer}:{status}" for peer, status in sorted(peers.items())) or "outcomes unavailable")


def advance_report(report: DiagnosticReport, now: float) -> DiagnosticReport:
    """The same snapshot as seen `now - read_at` seconds later: clock, evidence ages and last-ask ages move, nothing is re-read."""
    import copy
    from dataclasses import replace

    dt = max(0.0, now - report.read_at)
    sections = dict(report.sections)
    obs = sections.get("observations")
    if obs is not None:
        data = copy.deepcopy(obs.data)
        for it in cast("list[dict[str, object]]", data.get("items") or []):
            age = it.get("age_seconds")
            if isinstance(age, (int, float)) and not isinstance(age, bool):
                it["age_seconds"] = age + dt
                ttl = _num(it.get("ttl_seconds"))
                if it.get("state") == "MEASURED" and ttl is not None and age + dt >= ttl:
                    it["state"] = "STALE"
        sections["observations"] = replace(obs, data=data)
    act = sections.get("activity")
    if act is not None:
        data = copy.deepcopy(act.data)
        for p in cast("list[dict[str, object]]", data.get("peers") or []):
            age = p.get("last_age_seconds")
            if isinstance(age, (int, float)) and not isinstance(age, bool):
                p["last_age_seconds"] = age + dt
        sections["activity"] = replace(act, data=data)
    return replace(report, read_at=report.read_at + dt, sections=sections)


def render_view(report: DiagnosticReport, *, width: int = 100, color: bool = False, unicode: bool = True,
                db_name: str = "", cycle: int | None = None, refresh: Obj | None = None, next_refresh_in: float | None = None,
                interval: float | None = None, refresh_every: int | None = None, top_streams: int = 3, recent_asks: int = 3,
                tz: tzinfo | None = None, store_path: str = "", store_source: str = "") -> str:
    """A-G frame; caller supplies terminal capabilities. No collection or cursor controls.

    Plain and rich share decisions. Width/clock changes only affect layout,
    countdowns and evaluated freshness, never the measurement-time PACE/ETA.
    """
    width = max(1, width)
    color = color and "NO_COLOR" not in os.environ
    items = _items(_section(report, "observations").get("items"))
    rows = _quota_rows(items, _items(_section(report, "resource_pools").get("pools")), report.read_at, tz)
    when = _local(report.read_at, tz).strftime("%Y-%m-%d %H:%M:%S%z" if width >= 100 else "%Y-%m-%d %H:%M%z")
    tail = f" ({_safe_text(store_source)})" if store_source else ""
    if cycle is not None:
        tail += f" c{cycle}"
        if next_refresh_in is not None and next_refresh_in >= 0:
            tail += f" next: {human_duration(next_refresh_in)}"
    path = _safe_text(store_path or db_name or "workspace").replace("\\", "/")
    if "/" in path:
        parts = path.split("/")
        path = ".../" + "/".join(parts[-2:] if parts[-2] == ".peerhub" else parts[-1:])
    lead = f"PeerHub {_safe_text(report.status)} {when} "
    budget = max(0, width - visible_len(lead + tail))
    if visible_len(path) > budget:
        path = "..." + path[-max(0, budget - 3):] if budget >= 4 else ""
    header = lead + path + tail
    threats = [r for r in rows if r.projection["state"] == "threat"]
    first = min(threats, key=lambda r: r.projection["exhaustion_at"] or float("inf")) if threats else None
    earliest_at = cast(float, first.projection["exhaustion_at"]) if first is not None else None
    exhausted = ", ".join(f"{r.peer}/{r.pool}" for r in rows if r.projection["state"] == "exhausted")
    credit_alerts: list[str] = []
    credits = _credit_lines(items, report.read_at, False, unicode, credit_alerts, tz)
    summary = _threat_summary(rows)
    if summary["state"] == "unknown":
        oldest = summary["oldest_stale_seconds"]
        age = f" (oldest {human_duration(cast(float, oldest))})" if oldest is not None else ""
        alert = f"Threats: unknown - {summary['reason']}{age}; refresh"
    elif exhausted:
        alert = "Threats: observed exhaustion"
    else:
        alert = "Threats: No threats"
    if first is not None and earliest_at is not None:
        alert = (f"Threats: {len(threats)} threats - earliest {first.peer}/{first.pool} {_eta(earliest_at, report.read_at, tz)} "
                 f"(in {_forecast_duration(max(0, earliest_at - report.read_at))})")
        others = ", ".join(f"{r.peer}/{r.pool}" for r in threats if r is not first)
        if others:
            alert += "; " + others
    if summary["fresh_pools"] and summary["stale_pools"]:
        alert += f"; {summary['stale_pools']} pools stale"
    if summary["fresh_pools"] and summary["unknown_pools"]:
        alert += f"; {summary['unknown_pools']} pools unknown"
    if exhausted:
        alert += f"; exhausted {exhausted}"
    if credit_alerts:
        alert += "; credit expiry<72h " + ", ".join(credit_alerts)
    lines = [_paint(line, CYAN, color) for line in _wrap(header, width)]
    lines += [_paint(line, RED if threats or exhausted else YELLOW if credit_alerts else '', color) for line in _wrap(alert, width)]
    # Wrap before adding colour so no escape sequence can be split.
    table = _quota_table(rows, width, False, unicode)
    lines.append(table[0])
    for row, line in zip(rows, table[1:]):
        lines.append(_paint(line, row.ink, color))
    for line in [*credits, *_ask_lines(report, items)]:
        lines.extend(_wrap(line, width))
    probe = _probe_line(items, refresh)
    if report.error:
        probe += f"; diagnostic ERR({_safe_text(report.error)})"
    lines.extend(_paint(line, RED if "ERR(" in probe or "TIMEOUT" in probe else "", color)
                 for line in _wrap(probe, width))
    legend = "PACE + = burning faster than an even pace; forecasts are conditional window-average estimates; Ctrl-C quit"
    lines.extend(_wrap(legend, width))
    return "\n".join(lines)


def format_frame(report: DiagnosticReport, view: str, *, color: bool = False, unicode: bool = True, width: int = 100,
                 db_name: str = "", cycle: int | None = None, refresh: Obj | None = None, next_refresh_in: float | None = None,
                 interval: float | None = None, refresh_every: int | None = None,
                 store_path: str = "", store_source: str = "") -> str:
    """Both styles use A-G; plain always emits ASCII with zero escapes."""
    return render_view(report, width=width, color=color if view == "rich" else False,
                       unicode=unicode if view == "rich" else False, db_name=db_name, cycle=cycle, refresh=refresh,
                       next_refresh_in=next_refresh_in, interval=interval, refresh_every=refresh_every,
                       store_path=store_path, store_source=store_source)


def use_color(stream_is_tty: bool, env: Mapping[str, str]) -> bool:
    """Colour requires a terminal; presence of NO_COLOR always wins."""
    return stream_is_tty and "NO_COLOR" not in env
