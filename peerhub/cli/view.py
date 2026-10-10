"""Dashboard view: turns one read-only DiagnosticReport into a single, scannable screen.

Pure presentation: no I/O, no sleeping, no database access. The caller supplies the already built report, the
clock reading and the colour/unicode decisions, so the output is deterministic and testable. Information design:

  header   what / when / where / refresh state, one line
  QUOTA    the focus: one row per pool, grouped by peer; usage bar, headroom, pace against the window, reset countdown,
           freshness badge. Evidence that is not a measurement (UNKNOWN/UNAVAILABLE/ERROR) is never drawn as a bar
           that could be read as "empty" or "unlimited".
  CREDITS  Codex reset coupons (count, nearest expiry)
  ACTIVITY the latest asks (peer, outcome, certainty, how long ago)
  SYSTEM   counts and the few most recently active streams instead of the full stream list
  ALERTS   one line with everything that needs attention, or "all clear"
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, tzinfo
from typing import cast

from peerhub.extensions.diag import DiagnosticReport
from peerhub.extensions.quota_projection import project_exhaustion

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
RED, GREEN, YELLOW, CYAN = "\033[31m", "\033[32m", "\033[33m", "\033[36m"
_ANSI = re.compile(r"\033\[[0-9;]*m")
HEADROOM_ALERT = 0.10    # alert when less than 10% of a window is left
PACE_YELLOW = 0.10       # used share ahead of the elapsed share of the window by more than 10 points
PACE_RED = 0.25          # ... by more than 25 points
CREDIT_WARN_HOURS = 72.0  # same threshold as `diag quota`
Obj = Mapping[str, object]


def _safe_text(value: object) -> str:
    """Remove terminal controls from data before adding presentation escapes."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", str(value))


def visible_len(text: str) -> int:
    return len(_ANSI.sub("", text))


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
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value else None


def _section(rep: DiagnosticReport, name: str) -> Obj:
    sec = rep.sections.get(name)
    return _obj(sec.data) if sec is not None else {}


def _ellipsis(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:max(1, limit - 1)] + "…"


def human_duration(seconds: float | None) -> str:
    """2h13m / 3d4h / 45s; negative or missing -> '-'."""
    if seconds is None or seconds < 0:
        return "-"
    s = int(seconds)
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{sec:02d}s" if m else f"{sec}s"


def bar(used: float, cells: int, unicode: bool) -> str:
    used = min(1.0, max(0.0, used))
    filled = int(round(used * cells))
    if unicode:
        return "█" * filled + "░" * (cells - filled)
    return "#" * filled + "." * (cells - filled)


def _unknown_bar(cells: int) -> str:
    return "?" * cells


def _severity(headroom: float | None, diff: float | None) -> str:
    if headroom is not None and headroom < HEADROOM_ALERT:
        return RED
    if diff is None:
        return GREEN
    return RED if diff > PACE_RED else YELLOW if diff > PACE_YELLOW else GREEN


def _pool_label(ref: object) -> str:
    return _safe_text(ref).rsplit(":", 1)[-1] if ref else "(probe)"


class _Row:
    __slots__ = ("peer", "pool", "kind", "state", "age", "used", "headroom", "diff", "window", "left", "reason",
                 "projection", "eta_text", "flagged", "threatened")

    def __init__(self, item: Obj, read_at: float, tz: tzinfo | None = None) -> None:
        payload = _obj(item.get("payload"))
        self.peer = _safe_text(item.get("subject_ref") or "-")
        self.pool = _pool_label(item.get("resource_pool_ref"))
        self.kind = _safe_text(item.get("kind") or "quota")
        self.state = _safe_text(item.get("state") or "UNKNOWN")
        self.age = _num(item.get("age_seconds"))
        why = payload.get("reason")
        self.reason = _safe_text(why) if isinstance(why, str) else ""
        rf = _num(payload.get("remaining_fraction"))
        measured = self.state in ("MEASURED", "STALE") and rf is not None
        self.headroom = min(1.0, max(0.0, rf)) if measured and rf is not None else None
        self.used = None if self.headroom is None else 1.0 - self.headroom
        start, reset = _num(payload.get("window_started_at")), _num(payload.get("resets_at"))
        self.window, self.left, self.diff = "-", None, None
        if start is not None and reset is not None and reset > start:
            span = reset - start
            self.window = f"{int(span // 86400)}d" if span >= 86400 else f"{int(span // 3600)}h"
            self.left = reset - read_at
            if self.used is not None:
                self.diff = self.used - min(1.0, max(0.0, (read_at - start) / span))

        self.projection = None
        self.eta_text = ""
        self.flagged = False
        self.threatened = False
        if self.used is not None and start is not None and reset is not None:
            meas_at = None
            if item.get("observed_at"):
                try:
                    meas_at = datetime.fromisoformat(str(item["observed_at"]).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    pass
            if meas_at is None and self.age is not None:
                meas_at = read_at - self.age
            self.projection = project_exhaustion(self.used, start, meas_at, reset, read_at)
            st = self.projection.get("status")
            exh_at = self.projection["exhaustion_at"]
            if st == "ok" and exh_at is not None:
                dt = datetime.fromtimestamp(exh_at, tz) if tz is not None else datetime.fromtimestamp(exh_at).astimezone()
                clock_str = dt.strftime("%H:%M")
                rel_str = human_duration(max(0.0, exh_at - read_at))
                self.eta_text = f"exhausts ~{clock_str} (in {rel_str}), before reset"
                self.flagged = True
                self.threatened = True
            elif st == "safe_until_reset":
                self.eta_text = "safe until reset"
            elif st == "exhausted":
                self.eta_text = "exhausted"
                self.flagged = True
                self.threatened = True
            elif st == "idle":
                self.eta_text = "idle"


def _badge(row: _Row, color: bool, unicode: bool) -> str:
    dot, warn, cross, ask = ("●", "▲", "✖", "?") if unicode else ("*", "!", "x", "?")
    age = human_duration(row.age) if row.age is not None else ""
    if row.state == "MEASURED":
        return _paint(f"{dot} {age}", GREEN, color)
    if row.state == "STALE":
        return _paint(f"{warn} STALE {age}".rstrip(), RED + BOLD, color)
    if row.state == "ERROR":
        return _paint(f"{cross} ERROR" + (f" {_ellipsis(row.reason, 28)}" if row.reason else ""), RED + BOLD, color)
    if row.state == "UNAVAILABLE":
        return _paint(f"{cross} UNAVAILABLE" + (f" {_ellipsis(row.reason, 28)}" if row.reason else ""), YELLOW, color)
    return _paint(f"{ask} UNKNOWN", DIM, color)


def _quota_rows(items: list[Obj], pools: list[Obj], read_at: float, tz: tzinfo | None = None) -> list[_Row]:
    rows = [_Row(i, read_at, tz) for i in items if i.get("kind") in ("quota", "rate_limit")]
    seen = {i.get("resource_pool_ref") for i in items if i.get("kind") in ("quota", "rate_limit")}
    for p in pools:  # a registered quota pool without evidence is shown explicitly as UNKNOWN, never omitted
        pid = p.get("resource_pool_id")
        if p.get("kind") in ("QUOTA", "RATE_LIMIT") and pid not in seen:
            rows.append(_Row({"subject_ref": p.get("provider"), "resource_pool_ref": pid, "kind": _safe_text(p.get("kind")).lower(),
                              "state": "UNKNOWN"}, read_at, tz))
    return sorted(rows, key=lambda r: (r.peer, r.pool))


def _quota_lines(rows: list[_Row], width: int, color: bool, unicode: bool, alerts: list[str]) -> list[str]:
    bad: dict[str, dict[str, list[str]]] = {}
    lines = _quota_table(rows, width, color, unicode, alerts, bad)
    for state, peers in bad.items():  # one alert per evidence state, not one per row
        who = ", ".join(f"{peer} {len(pools)}" for peer, pools in sorted(peers.items()))
        alerts.append(f"{state} evidence ({who})" + (": run `peerhub observation refresh`" if state == "STALE" else ""))
    threatened_rows = [(r.projection["exhaustion_at"], r) for r in rows
                       if r.threatened and r.projection and r.projection["exhaustion_at"] is not None]
    earliest_threat = min(threatened_rows, key=lambda pair: pair[0])[1] if threatened_rows else None
    if earliest_threat:
        alerts.insert(0, f"{earliest_threat.peer}/{earliest_threat.pool} {earliest_threat.eta_text}")
    return lines


def _quota_table(rows: list[_Row], width: int, color: bool, unicode: bool, alerts: list[str],
                 bad: dict[str, dict[str, list[str]]]) -> list[str]:
    if not rows:
        return ["  (no quota evidence yet: run `peerhub observation refresh`)"]
    cells = 16 if width >= 100 else 10 if width >= 72 else 8
    out: list[str] = []
    stacked = width < 60
    if not stacked:
        head = f"  {'PEER':<5} {'POOL':<7} {'WIN':<4} {'USED':<{cells + 6}} {'LEFT':>5} {'PACE':>6} {'RESET':>7}  FRESH"
        out.append(_paint(head, DIM, color))
    last_peer = ""
    for r in rows:
        peer_cell = r.peer if r.peer != last_peer else ""
        last_peer = r.peer
        if r.used is None or r.headroom is None:
            body = _unknown_bar(cells)
            used_txt, left_txt, pace_txt, sev = "  ?  ", "  ?", "     ?", DIM
        else:
            sev = _severity(r.headroom, r.diff)
            body = _paint(bar(r.used, cells, unicode), sev, color)
            used_txt, left_txt = f"{r.used * 100:3.0f}% ", f"{r.headroom * 100:3.0f}%"
            if r.diff is None:
                pace_txt = "     -"
            else:
                sign = ("▲" if unicode else "+") if r.diff > 0.005 else ("▼" if unicode else "-") if r.diff < -0.005 else ("■" if unicode else "=")
                pace_txt = _paint(f"{sign}{abs(r.diff) * 100:3.0f}pt", sev, color)
            if r.headroom < HEADROOM_ALERT:
                alerts.append(f"{r.peer}/{r.pool} {r.headroom * 100:.0f}% left")
        if r.state in ("STALE", "ERROR", "UNAVAILABLE"):
            bad.setdefault(r.state, {}).setdefault(r.peer, []).append(r.pool)
        reset_txt = human_duration(r.left)
        if stacked:
            out.append(f"  {r.peer}/{r.pool} [{body}] {left_txt} left  reset {reset_txt}  {_badge(r, color, unicode)}")
        else:
            out.append(f"  {peer_cell:<5} {r.pool:<7} {r.window:<4} {body} {used_txt}{left_txt:>5} {pace_txt:>{6 + (len(pace_txt) - visible_len(pace_txt))}} "
                       f"{reset_txt:>7}  {_badge(r, color, unicode)}")
        if r.eta_text:
            eta_col = RED + BOLD if r.flagged else DIM
            out.append(_paint(f"    ETA: {r.eta_text}", eta_col, color))
    return out


def _credit_lines(items: list[Obj], read_at: float, color: bool, unicode: bool, alerts: list[str]) -> list[str]:
    credits = [i for i in items if i.get("kind") == "reset_credit"]
    if not credits:
        return []
    out = [_paint("RESET CREDITS", BOLD, color)]
    for it in credits:
        payload, state = _obj(it.get("payload")), _safe_text(it.get("state") or "UNKNOWN")
        peer = _safe_text(it.get("subject_ref") or "-")
        if state not in ("MEASURED", "STALE"):
            out.append(f"  {peer:<5} {_paint(state, DIM if state == 'UNKNOWN' else RED, color)}  (no count: evidence is {state.lower()})")
            continue
        count = payload.get("available_count")
        count_txt = str(count) if isinstance(count, int) and not isinstance(count, bool) else "?"
        near = _num(payload.get("nearest_expires_at"))
        exp = ""
        if near is not None:
            hours = (near - read_at) / 3600.0
            exp = f"  nearest expires in {human_duration(near - read_at)}"
            if 0 <= hours < CREDIT_WARN_HOURS:
                exp += _paint(" (WARN <72h)", YELLOW, color)
                alerts.append(f"{peer} reset credit expires in {human_duration(near - read_at)}")
        stale = _paint(" STALE", RED + BOLD, color) if state == "STALE" else ""
        out.append(f"  {peer:<5} {count_txt} available{exp}{stale}")
    return out


def _activity_lines(items: list[Obj], limit: int, color: bool) -> list[str]:
    acts = sorted((i for i in items if i.get("kind") == "activity"), key=lambda i: _num(i.get("age_seconds")) or 0.0)[:limit]
    if not acts:
        return []
    out = [_paint("LATEST ASKS", BOLD, color)]
    for it in acts:
        p = _obj(it.get("payload"))
        took = _num(p.get("operation_elapsed_seconds"))
        ok = _safe_text(p.get("status") or "?")
        code = {"delivered": GREEN, "recovered_terminal": GREEN}.get(ok, YELLOW)
        out.append(f"  {_safe_text(it.get('subject_ref') or '-'):<5} {_paint(ok, code, color)} {_safe_text(p.get('certainty') or '')}  "
                   f"{human_duration(_num(it.get('age_seconds')))} ago" + (f"  took {human_duration(took)}" if took is not None else ""))
    return out


def _ask_lines(report: DiagnosticReport, items: list[Obj], limit: int, color: bool, unicode: bool, alerts: list[str]) -> list[str]:
    """Per-peer ask history from the optional `activity` section (newest N asks per peer); falls back to the latest-ask lines."""
    sec = _section(report, "activity")
    peers = _items(sec.get("peers"))
    if not peers:
        return _activity_lines(items, limit, color)
    out = [_paint(f"ASKS (newest {int(_num(sec.get('window')) or 0)} per peer)", BOLD, color),
           f"  {'PEER':<5} {'ASKS':>4} {'OK':>4} {'UNC':>4} {'FAIL':>4} {'RATE':>5} {'MEDIAN':>7}  LAST"]
    for p in peers:
        asks, ok = int(_num(p.get("asks")) or 0), int(_num(p.get("ok")) or 0)
        unc, bad = int(_num(p.get("uncertain")) or 0), int(_num(p.get("failed")) or 0)
        rate = ok / asks if asks else None
        rate_txt = "    -" if rate is None else f"{rate * 100:4.0f}%"
        rate_col = DIM if rate is None else GREEN if rate >= 0.9 else YELLOW if rate >= 0.7 else RED
        med = _num(p.get("median_seconds"))
        last = _safe_text(p.get("last_status") or "?")
        last_ok = last in ("delivered", "recovered_terminal")
        peer = _safe_text(p.get("peer") or "-")
        if not last_ok:
            alerts.append(f"{peer} last ask {last}")
        out.append(f"  {peer:<5} {asks:>4} {ok:>4} {_paint(f'{unc:>4}', YELLOW if unc else DIM, color)} "
                   f"{_paint(f'{bad:>4}', RED if bad else DIM, color)} {_paint(rate_txt, rate_col, color)} "
                   f"{human_duration(med) if med is not None else '-':>7}  "
                   f"{human_duration(_num(p.get('last_age_seconds')))} ago {_paint(last, GREEN if last_ok else YELLOW, color)}")
    return out


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


def _system_line(rep: DiagnosticReport, top: int, color: bool) -> str:
    peers, streams = _items(_section(rep, "peers").get("peers")), _items(_section(rep, "streams").get("streams"))
    chip = _paint(_safe_text(rep.status), GREEN if rep.status == "OK" else RED if rep.status == "FAILED" else YELLOW, color)
    records = _num(rep.snapshot.get("records_total"))
    busiest = sorted(streams, key=lambda s: (str(s.get("stream_id") or "").startswith("legacy:"),
                                             -(_num(s.get("head_position")) or 0.0)))[:max(0, top)]
    names = ", ".join(_ellipsis(_safe_text(s.get("stream_id")), 22) for s in busiest)
    tail = f"  |  busiest: {names}" if names else ""
    return (f"SYSTEM  {len(peers)} peers | {len(streams)} streams | {'?' if records is None else int(records)} records | {chip}{tail}")


def _header(rep: DiagnosticReport, db_name: str, cycle: int | None, refresh: Obj | None, next_in: float | None,
            tz: tzinfo | None, color: bool, store_path: str = "", store_source: str = "",
            earliest_threat: _Row | None = None) -> str:
    when = datetime.fromtimestamp(rep.read_at, tz) if tz is not None else datetime.fromtimestamp(rep.read_at).astimezone()
    if store_path:
        store_desc = f"{store_path} ({store_source})" if store_source else store_path
    else:
        store_desc = db_name
    parts = [_paint("PeerHub", BOLD + CYAN, color), when.strftime("%H:%M:%S %z"), _safe_text(store_desc)]
    if earliest_threat:
        parts.append(_paint(f"THREAT: {earliest_threat.peer}/{earliest_threat.pool} {earliest_threat.eta_text}", RED + BOLD, color))
    if cycle is not None:
        parts.append(f"cycle {cycle}")
    if refresh is not None:
        if refresh.get("skipped"):
            parts.append(_paint("redraw only", DIM, color))
        else:
            st = _safe_text(refresh.get("status") or "?")
            parts.append("refresh " + _paint(st, GREEN if st == "OK" else YELLOW if st == "PARTIAL" else RED, color))
    if next_in is not None:
        parts.append(f"next in {human_duration(next_in)}")
    return "  ·  ".join(parts)


def render_view(report: DiagnosticReport, *, width: int = 100, color: bool = False, unicode: bool = True,
                db_name: str = "", cycle: int | None = None, refresh: Obj | None = None, next_refresh_in: float | None = None,
                interval: float | None = None, refresh_every: int | None = None, top_streams: int = 3, recent_asks: int = 3,
                tz: tzinfo | None = None, store_path: str = "", store_source: str = "") -> str:
    """One screen. `report.read_at` is the clock: the same report always renders to the same text."""
    width = max(40, width)
    items = _items(_section(report, "observations").get("items"))
    pools = _items(_section(report, "resource_pools").get("pools"))
    alerts: list[str] = []
    rule = ("─" if unicode else "-") * min(width, 100)
    rows = _quota_rows(items, pools, report.read_at, tz)
    threatened_rows = [(r.projection["exhaustion_at"], r) for r in rows
                       if r.threatened and r.projection and r.projection["exhaustion_at"] is not None]
    earliest_threat = min(threatened_rows, key=lambda pair: pair[0])[1] if threatened_rows else None
    lines = [_header(report, db_name or "workspace", cycle, refresh, next_refresh_in, tz, color,
                     store_path=store_path, store_source=store_source, earliest_threat=earliest_threat),
             _paint(rule, DIM, color),
             _paint("QUOTA", BOLD, color)]
    lines += _quota_lines(rows, width, color, unicode, alerts)
    for block in (_credit_lines(items, report.read_at, color, unicode, alerts), _ask_lines(report, items, recent_asks, color, unicode, alerts)):
        if block:
            lines += ["", *block]
    if refresh is not None:
        for w in cast("Sequence[object]", refresh.get("warnings") or []):
            alerts.append(str(w)[:60])
    system = _system_line(report, top_streams, color)
    lines += ["", system if visible_len(system) <= width or color else _ellipsis(system, width)]
    if report.error:
        alerts.append(f"diag error: {_safe_text(report.error)[:50]}")
    shown = alerts[:4] + ([f"+{len(alerts) - 4} more"] if len(alerts) > 4 else [])
    alert_text = _ellipsis("; ".join(shown), max(10, width - 8))
    lines.append(_paint("ALERTS  ", BOLD, color) + (_paint(alert_text, YELLOW + BOLD, color) if alerts else _paint("all clear", GREEN, color)))
    hints = ["Ctrl-C quit", "PACE = used% - window elapsed%  (▲ burning faster than an even pace)" if unicode else "PACE = used% - window elapsed%  (+ faster than an even pace)"]
    if interval is not None:
        hints.append(f"every {human_duration(interval)}")
    if refresh_every is not None and refresh_every > 1:
        hints.append(f"collect every {refresh_every} cycles")
    lines += [_paint(rule, DIM, color), _paint("  " + "  ·  ".join(hints), DIM, color)]
    text = "\n".join(lines)
    return text if unicode else text.replace("·", "|").replace("…", "~")


def format_frame(report: DiagnosticReport, view: str, *, color: bool = False, unicode: bool = True, width: int = 100,
                 db_name: str = "", cycle: int | None = None, refresh: Obj | None = None, next_refresh_in: float | None = None,
                 interval: float | None = None, refresh_every: int | None = None,
                 store_path: str = "", store_source: str = "") -> str:
    """`plain` = the existing text dashboard, `rich` = this view. Shared by `diag --view` and `monitor --view`."""
    if view == "rich":
        return render_view(report, width=width, color=color, unicode=unicode, db_name=db_name, cycle=cycle, refresh=refresh,
                           next_refresh_in=next_refresh_in, interval=interval, refresh_every=refresh_every,
                           store_path=store_path, store_source=store_source)
    from peerhub.extensions import diag_quota
    return diag_quota.format_dashboard(report, store_path=store_path or None, store_source=store_source or None)


def use_color(stream_is_tty: bool, env: Mapping[str, str]) -> bool:
    """NO_COLOR wins, FORCE_COLOR next, otherwise colour only on a terminal."""
    if env.get("NO_COLOR"):
        return False
    return bool(env.get("FORCE_COLOR")) or stream_is_tty
