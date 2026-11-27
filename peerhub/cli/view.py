"""Width-aware diagnostic dashboard and quota-plan rendering."""
from __future__ import annotations

import textwrap
from collections.abc import Mapping, Sequence
from datetime import datetime, tzinfo
from typing import TYPE_CHECKING, Any, cast

from peerhub._console import ellipsize, pad_cell, visible_len
from peerhub.cli.quota_format import (
    forecast_duration as _forecast_duration,
    format_exhaust_reset,
    format_percent,
    format_pool_left,
    format_pool_pace,
    fraction_bar as bar,
    human_duration as human_duration,
)
from peerhub.core.quota_policy import format_pace
from peerhub.core.text import strip_controls
from peerhub.extensions.diag_read_model import (
    PLAN_GATE_TAG,
    PLAN_PACE_LEGEND,
    Obj,
    QuotaRow,
    as_object,
    dashboard_quota_rows,
    object_items,
    plan_budget,
    plan_gaps,
    plan_gate_reason,
    plan_pools,
    report_number,
    section_data,
    threat_summary,
)

if TYPE_CHECKING:
    from peerhub.extensions.diag import DiagnosticReport
    from peerhub.extensions.pool_health import PoolHealth

def _local(at: float, tz: tzinfo | None) -> datetime:
    return datetime.fromtimestamp(at, tz) if tz is not None else datetime.fromtimestamp(at).astimezone()


def _eta(at: float, now: float, tz: tzinfo | None) -> str:
    dt = _local(at, tz)
    dated = dt.date() != _local(now, tz).date()
    return dt.strftime("%a %H:%M" if dated else "%H:%M")


def _forecast(row: QuotaRow, now: float, tz: tzinfo | None, short: bool) -> str:
    if row.na:
        return "- n/a"
    state = row.forecast
    glyph = {"threat": "!", "on_pace": "=", "insufficient": ":", "exhausted": "x",
             "unknown": "?", "stale": "~", "expired": "/"}[state]
    if state == "threat":
        at = row.exhaustion_at
        assert at is not None
        clock, left = _eta(at, now, tz), _forecast_duration(max(0, at - now))
        if short:
            clock = _local(at, tz).strftime("%a %H:%M" if _local(at, tz).date() != _local(now, tz).date() else "%H:%M")
            text = f"{glyph} exh {clock} ({left})"
        else:
            text = f"{glyph} exhausts {clock} (in {left})"
    else:
        text = glyph + " " + {"unknown": "unknown", "stale": "stale", "expired": "expired", "exhausted": "exhausted",
                              "insufficient": "not enough data", "on_pace": "on pace"}[state]
    if row.limited_by:
        text += f" (cap {row.limited_by})"
    return text


def _badge(row: QuotaRow) -> str:
    if row.age is None:
        return "?"
    dot = "~" if row.state == "STALE" else "*"
    return dot + human_duration(row.age)


def _quota_table(rows: list[QuotaRow], width: int, *,
                 now: float, tz: tzinfo | None = None) -> list[str]:
    cells = 10 if width >= 100 else 8
    peer_w = max(4, max((visible_len(r.peer) for r in rows), default=4))
    pool_w = max(5, max((visible_len(r.pool) for r in rows), default=5))
    reset_w = max(5, max((visible_len("-" if r.na else human_duration(r.left)) for r in rows), default=5))
    fresh_w = max(5, max((visible_len(_badge(r)) for r in rows), default=5))
    def prefix(peer: str, pool: str, usage: str, left: str, pace: str, reset: str) -> str:
        return (pad_cell(peer, peer_w) + " " + pad_cell(pool, pool_w) + " " +
                f"{usage:<{cells + 2}} {left:>4} {pace:>8} " + pad_cell(reset, reset_w) + " ")
    head_lead = prefix("PEER", "POOL", "USAGE-bar", "LEFT", "PACE(pp)", "RESET")
    forecast_w = max(0, width - visible_len(head_lead) - fresh_w - 1)
    head = head_lead + pad_cell(ellipsize("FORECAST", forecast_w, False), forecast_w) + " FRESH"
    out = [head]
    for row in rows:
        body = "-" * cells if row.na else bar(row.left_fraction, cells, False)
        left = "-" if row.na else ("?" if row.left_fraction is None else format_percent(row.left_fraction, show_tiny=False))
        pace = "-" if row.na else ("?" if row.diff is None else format_pace(row.diff))
        reset = "-" if row.na else human_duration(row.left)
        lead = prefix(row.peer, row.pool, f"[{body}]", left, pace, reset)
        forecast = ellipsize(_forecast(row, now, tz, width < 100), forecast_w, False)
        fresh = _badge(row)
        out.append(lead + pad_cell(forecast, forecast_w) + " " + fresh)
    return out


PLAN_FAST_TAG = "fast"
PLAN_TAG_LEGEND = f"{PLAN_FAST_TAG}/{PLAN_GATE_TAG} tags"
PLAN_BUDGET_COLUMN = "BUDGET%/h"
_PLAN_COLUMNS = ("PEER", "POOL", "LEFT", "PACE(pp)", "EXH/RESET", PLAN_BUDGET_COLUMN, "ACTION", "REASON")
_PLAN_TEXT_LEGEND = f"{PLAN_PACE_LEGEND}/early | blocked 0%/{{missing}}/? | - n/a | {PLAN_BUDGET_COLUMN} | {PLAN_TAG_LEGEND}"
PLAN_EARLY_TAG = "!early"


def _plan_cells(row: Obj, health: PoolHealth, *, unicode: bool) -> list[str]:
    peer_pool = [str(row["peer"]), str(row["pool"])]
    if row["action"] == "n/a":
        return [*peer_pool, "-", "-", "-", "-", "-"]
    left = format_pool_left(health, report_number(row["age_seconds"]), unicode=unicode)
    pace = format_pool_pace(health["pace_pt"], unicode=unicode)
    budget = plan_budget(health["left_pct"], health["reset_in_seconds"])
    return [*peer_pool, left,
            pace + ("!" if health["pace_band"] == "rest" else ""), format_exhaust_reset(health, unicode=unicode),
            "?" if budget is None else f"{budget:.2f}", str(row["action"])]


def render_plan(report: DiagnosticReport, *, width: int = 100, tz: tzinfo | None = None, store_path: str = "",
                store_source: str = "", store_note: str = "", load: Mapping[str, Any] | None = None,
                profiles_by_pool: Mapping[str, list[str]] | None = None, peers: frozenset[str] = frozenset(),
                separate_observation_store: bool = False, unicode: bool = True) -> str:
    from peerhub.extensions.pool_health import PACE_GAP_BASELINE

    width = max(1, width)
    when = _local(report.read_at, tz).strftime("%Y-%m-%d %H:%M%z")
    lines = _wrap(f"PeerHub plan {strip_controls(report.status)} {when} {strip_controls(store_path)} ({strip_controls(store_source)})", width)
    lines += _wrap(strip_controls(store_note), width) if store_note else []
    all_pools = plan_pools(report, profiles_by_pool=profiles_by_pool)
    pools = [pool for pool in all_pools if not peers or pool[0] in peers]
    cells = [_plan_cells(row, health, unicode=unicode) for _, row, health in pools]
    head = _PLAN_COLUMNS[:-1]
    widths = [max(visible_len(row[i]) for row in [head, *cells]) for i in range(len(head))]
    def lead(row: Sequence[str]) -> str:
        return " ".join(pad_cell(c, w) if i < 2 or i == 6 else " " * (w - visible_len(c)) + c
                        for i, (c, w) in enumerate(zip(row, widths))) + " "
    lines.append(ellipsize(lead(head) + _PLAN_COLUMNS[-1], width, False))
    for row, (_, p, health) in zip(cells, pools):
        reason = lead(row) + strip_controls(p["reason"])
        tags = [PLAN_EARLY_TAG] if health["exhaust_before_reset"] else []
        if health["state"] == "fast":
            tags.append(f"({PLAN_FAST_TAG})")
        tags.extend(f"({PLAN_GATE_TAG}: {strip_controls(gate['pool'])})" for gate in object_items(p["gates"])
                    if gate["action"] in {"rest", "blocked"}
                    and not str(p["reason"]).endswith(plan_gate_reason(gate["pool"], gate["reason"]))
                    and not (p["limited_by"] and gate["pool"] == p["binding"] == p["parent"]))
        lines.extend(_wrap(" ".join([reason, *tags]), width) if tags else [ellipsize(reason, width, False)])
    if not pools:
        lines.append("no quota evidence; next: peerhub observation refresh")
    lines.extend(_wrap(_PLAN_TEXT_LEGEND.format(missing="—" if unicode else "-"), width))
    applicable_pools = [p for _, p, _ in pools if p["action"] != "n/a"]
    profile_groups = [f"{p['pool']} {','.join(cast(list[str], p['profiles']))}"
                      for p in applicable_pools if p.get("profiles")]
    if profile_groups:
        lines.extend(_wrap(strip_controls("profiles: " + " | ".join(profile_groups)), width))
    gaps = plan_gaps(pools)["pools"]
    paced_pools = {str(row["pool"]) for _, row, health in pools if health["pace_pt"] is not None}
    values = ", ".join(f"{name} {format_pool_pace(pace, unicode=unicode)}"
                       for name, pace in gaps.items() if name in paced_pools)
    if values:
        lines.extend(_wrap(f"gap: vs {PACE_GAP_BASELINE} (pp): {values}", width))
    if load is not None:
        from peerhub.core.load_limits import PLAN_NA_POOLS_LABEL, PLAN_NA_POOLS_LIMIT, format_load
        if separate_observation_store:
            load_line = format_load(load)
        else:
            # Use the full plan's applicability for this store, independent of the peer filter.
            pool_names = {str(p["pool"]) for _, p, _ in all_pools if p["action"] != "n/a"}
            in_flight = as_object(load.get("by_pool"))
            shown = {pool: count for pool, count in in_flight.items() if pool in pool_names}
            load_line = format_load({**load, "by_pool": shown})
            hidden = sorted(in_flight.keys() - pool_names)
            if hidden:
                names = ", ".join(strip_controls(pool) for pool in hidden[:PLAN_NA_POOLS_LIMIT])
                overflow = len(hidden) - PLAN_NA_POOLS_LIMIT
                suffix = f" +{overflow}" if overflow > 0 else ""
                load_line += f" | {PLAN_NA_POOLS_LABEL} {names}{suffix}"
        lines.extend(_wrap(load_line, width))
    return "\n".join(lines)


def _wrap(line: str, width: int) -> list[str]:
    """Wrap evidence instead of dropping pools, asks or alerts; count display cells."""
    if visible_len(line) <= width:
        return [line]
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


def _credit_lines(items: list[Obj], read_at: float, alerts: list[str], tz: tzinfo | None = None) -> list[str]:
    out: list[str] = []
    for item in items:
        if item.get("kind") != "reset_credit":
            continue
        p, state = as_object(item.get("payload")), str(item.get("state") or "UNKNOWN")
        peer = strip_controls(item.get("subject_ref") or "?")
        count = p.get("available_count") if state in ("MEASURED", "STALE") else None
        exp = report_number(p.get("nearest_expires_at")) if count is not None else None
        expiry = (_local(exp, tz).strftime("%a %m-%d %H:%M" if _local(exp, tz).date() != _local(read_at, tz).date() else "%H:%M")
                  if exp is not None else "unknown")
        line = f"Credits {peer}:{count if count is not None else '?'} available"
        if exp is not None:
            line += f"; expiry {expiry} (in {human_duration(max(0, exp - read_at))})"
        if state != "MEASURED":
            word = "stale" if state == "STALE" else "unknown"
            glyph = "~" if word == "stale" else "?"
            line += f"; {glyph} {word} {_badge(QuotaRow(item))}"
        out.append(line)
        if exp is not None and 0 <= exp - read_at < 72 * 3600 and isinstance(count, int) and count > 0:
            alerts.append(f"{peer} {expiry} in {human_duration(exp - read_at)}")
    return out


def _activity_lines(items: list[Obj]) -> list[str]:
    latest: dict[str, Obj] = {}
    for item in items:
        if item.get("kind") != "activity":
            continue
        peer = strip_controls(item.get("subject_ref") or "?")
        old = latest.get(peer)
        if old is None or (report_number(item.get("capture_seq")) or 0, str(item.get("observed_at") or "")) >= (report_number(old.get("capture_seq")) or 0, str(old.get("observed_at") or "")):
            latest[peer] = item
    return [f"Asks {peer} {strip_controls(as_object(item.get('payload')).get('status') or 'unknown')} "
            f"{human_duration(report_number(item.get('age_seconds')))} ago, took "
            f"{human_duration(report_number(as_object(item.get('payload')).get('operation_elapsed_seconds')))}"
            for peer, item in sorted(latest.items())]


def _ask_lines(report: DiagnosticReport, items: list[Obj]) -> list[str]:
    sec = section_data(report, "activity")
    out: list[str] = []
    for p in object_items(sec.get("peers")):
        if not (report_number(p.get("asks")) or report_number(p.get("running")) or p.get("last_status")):
            continue
        out.append(f"Asks {strip_controls(p.get('peer'))} {strip_controls(p.get('last_status') or 'unknown')} "
                   f"{human_duration(report_number(p.get('last_age_seconds')))} ago, took {human_duration(report_number(p.get('last_seconds')))}")
    return out if out else _activity_lines(items)


def _probe_line(items: list[Obj]) -> str:
    from peerhub.extensions.observation_model import ABSENT_FACT_REASONS, QUOTA_PROBE_LOAD_DELAY, QUOTA_PROBE_LOAD_LABEL

    peers: dict[str, str] = {}
    latest: dict[str, tuple[float, str, int]] = {}
    for item in items:
        if item.get("kind") not in ("quota", "rate_limit", "probe", "collector"):
            continue
        peer = strip_controls(item.get("subject_ref") or "?")
        state, p = item.get("state"), as_object(item.get("payload"))
        reason_raw = str(p.get("reason") or "")
        is_na = state == "ABSENT" and reason_raw.startswith(ABSENT_FACT_REASONS)
        key = (report_number(item.get("capture_seq")) or 0,
               str(item.get("captured_at") or item.get("observed_at") or ""),
               0 if item.get("resource_pool_ref") else 1)
        # Last committed successful measurement is an OK probe outcome, even
        # after its quota freshness expires. A newer failure takes precedence.
        outcome = "OK" if (state in ("MEASURED", "STALE") or is_na) else "?"
        if not item.get("resource_pool_ref"):
            reason = strip_controls(p.get("reason") or state or "unknown")
            if reason == QUOTA_PROBE_LOAD_DELAY:
                outcome = QUOTA_PROBE_LOAD_LABEL
            elif state == "TIMEOUT":
                outcome = "TIMEOUT"
            elif state in ("ERROR", "UNAVAILABLE"):
                outcome = f"ERR({reason})"
            if not item.get("captured_at") and not item.get("observed_at"):
                key = (float("inf"), "", 1)
        if peer not in latest or key >= latest[peer]:
            if outcome == "?" and peer in latest and key[1] and key[1] == latest[peer][1] and peers.get(peer) == "OK":
                latest[peer] = key
                continue
            latest[peer], peers[peer] = key, outcome
    return "Probes " + (" ".join(f"{peer}:{status}" for peer, status in sorted(peers.items())) or "outcomes unavailable")


def render_view(report: DiagnosticReport, *, width: int = 100, tz: tzinfo | None = None,
                store_path: str = "", store_source: str = "", store_note: str = "",
                profiles_by_pool: Mapping[str, list[str]] | None = None) -> str:
    """Render a plain-text diagnostic snapshot."""
    width = max(1, width)
    items = object_items(section_data(report, "observations").get("items"))
    rows = dashboard_quota_rows(report, profiles_by_pool=profiles_by_pool)
    when = _local(report.read_at, tz).strftime("%Y-%m-%d %H:%M:%S%z" if width >= 100 else "%Y-%m-%d %H:%M%z")
    tail = f" ({strip_controls(store_source)})" if store_source else ""
    # #338: the full resolved store path; a narrow terminal wraps it instead of truncating it.
    header = f"PeerHub {strip_controls(report.status)} {when} " + strip_controls(store_path or "workspace") + tail
    threats = [r for r in rows if r.forecast == "threat"]
    first = min(threats, key=lambda r: r.exhaustion_at or float("inf")) if threats else None
    earliest_at = first.exhaustion_at if first is not None else None
    exhausted = ", ".join(f"{r.peer}/{r.pool}" for r in rows if r.forecast == "exhausted")
    credit_alerts: list[str] = []
    credits = _credit_lines(items, report.read_at, credit_alerts, tz)
    summary = threat_summary(rows)
    if summary["state"] == "unknown":
        oldest = summary["oldest_stale_seconds"]
        age = f" (oldest {human_duration(cast(float, oldest))})" if oldest is not None else ""
        alert = f"Threats: unknown - {summary['reason']}{age}; refresh"
    elif exhausted:
        alert = "Threats: observed exhaustion"
    else:
        alert = "Threats: No threats"
    if first is not None and earliest_at is not None:
        alert = (f"Threats: {len(threats)} threat{'s' if len(threats) != 1 else ''} - earliest {first.peer}/{first.pool} {_eta(earliest_at, report.read_at, tz)} "
                 f"(in {_forecast_duration(max(0, earliest_at - report.read_at))})")
        others = ", ".join(f"{r.peer}/{r.pool}" for r in threats if r is not first)
        if others:
            alert += "; " + others
    if summary["fresh_pools"] and summary["stale_pools"]:
        n_stale = cast(int, summary["stale_pools"])
        alert += f"; {n_stale} pool{'' if n_stale == 1 else 's'} stale"
    if summary["fresh_pools"] and summary["unknown_pools"]:
        n_unk = cast(int, summary["unknown_pools"])
        alert += f"; {n_unk} pool{'' if n_unk == 1 else 's'} unknown"
    if exhausted:
        alert += f"; exhausted {exhausted}"
    if credit_alerts:
        alert += "; credit expiry<72h " + ", ".join(credit_alerts)
    lines = _wrap(header, width)
    if store_note:
        lines.extend(_wrap(strip_controls(store_note), width))
    lines.extend(_wrap(alert, width))
    lines.extend(_quota_table(rows, width, now=report.read_at, tz=tz))
    for line in [*credits, *_ask_lines(report, items)]:
        lines.extend(_wrap(line, width))
    probe = _probe_line(items)
    if report.error:
        probe += f"; diagnostic ERR({strip_controls(report.error)})"
    lines.extend(_wrap(probe, width))
    legend = "PACE + = faster than even | forecasts are conditional window-average"
    lines.append(ellipsize(legend, width, False))
    return "\n".join(lines)
