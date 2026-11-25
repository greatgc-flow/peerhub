"""Render monitor verdicts, alert rows, and read failures."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, cast

from peerhub.core.text import clean_text
from peerhub.core.scalars import finite_number
from peerhub.cli.monitor_layout import (
    PILL_SGR,
    RICH_SGR,
    as_dict,
    ascii_text,
    cell_text,
    cell_width,
    compact_age,
    dict_items,
    fit_text,
    format_count,
    heading_rule,
    paint_text,
    rich_section,
    section_rule,
)
from peerhub.cli.monitor_legend import FRAME_SECTIONS
from peerhub.cli.monitor_links import STATE_WORDS_ANCHOR, osc_link


_ALERT_SOURCES = (("activity", "asks"), ("capacity", "quota"), ("accounts", "accounts"), ("facts", "facts"))
_SOURCE_PHRASES = {"not_selected": "not read", "error": "read failed", "truncated": "partial", "stale": "stale",
                   "unknown": "unknown"}  # unknown (#376 P14): no 7-day pool the provider reports has a number


def frame_alerts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The read model's alerts plus the agy token-use stop, which only the live monitor knows (before the low ones)."""
    alerts = dict_items(payload.get("alerts"))
    if "STOPPED" in str(payload.get("refresh_state") or ""):
        stop: dict[str, Any] = {"code": "a7", "kind": "agy stop", "subject": "next: --allow-agy-token-use to continue",
                                "age_s": None, "severity": "high"}
        at = next((i for i, item in enumerate(alerts) if item.get("severity") != "high"), len(alerts))
        alerts = [*alerts[:at], stop, *alerts[at:]]
    return alerts


def _alert_text(alert: dict[str, Any]) -> str:
    age = finite_number(alert.get("age_s"))
    text = f"{clean_text(alert.get('kind') or '?')}: {clean_text(alert.get('subject') or '?')}"
    return text if age is None else f"{text} {compact_age(age)}"


_SOURCE_REASONS = {"not_selected": "not selected", "error": "read failed", "truncated": "partial (sample cut)",
                   "stale": "stale", "unknown": "unknown (no 7-day pool has a number)"}


def unread_sources(payload: dict[str, Any]) -> list[str]:
    """`source <id>: <reason>` for every alert source that was not read completely, then the hint (for --expand).
    A frame without `alert_sources` (a schema error, a hand-made payload) says so, as line 1 does."""
    raw: object = payload.get("alert_sources")
    if not isinstance(raw, Mapping):
        return ["source alerts: not read"]
    sources = as_dict(cast(object, raw))
    given = as_dict(sources.get("reasons"))  # the read model's own reason per source, when it has one (lane B fix 5)

    def reason(key: str) -> str:
        text = given.get(key)
        return clean_text(text) if isinstance(text, str) and text.strip() else \
            _SOURCE_REASONS.get(str(sources.get(key)), "not read")

    rows = [f"source {key}: {reason(key)}" for key, _ in _ALERT_SOURCES if sources.get(key) != "read"]
    return rows + ([f"hint: {clean_text(sources['hint'])}"] if rows and sources.get("hint") else [])


def _unread(payload: dict[str, Any]) -> str | None:
    """None when every alert source was selected and read completely; otherwise what was not, and the hint."""
    raw: object = payload.get("alert_sources")
    if not isinstance(raw, Mapping):
        return "alerts not read"
    sources = as_dict(cast(object, raw))
    phrases: dict[str, list[str]] = {}
    for key, label in _ALERT_SOURCES:
        status = sources.get(key)
        if status != "read":
            phrases.setdefault(_SOURCE_PHRASES.get(str(status), "not read"), []).append(label)
    if not phrases:
        return None
    parts = [f"{', '.join(labels)} {phrase}" for phrase, labels in phrases.items()]
    if sources.get("hint"):
        parts.append(clean_text(sources["hint"]))
    return "; ".join(parts)


def verdict_line(payload: dict[str, Any], width: int, unicode: bool, keep: str | None = None, sep: str = " | ") -> str:
    """Line 1: `OK`, `OK? (... not read)` or `ATTN n` with two named alerts; the right-hand fields drop first.
    `sep` joins the fields (the rich view uses ` · `)."""
    alerts = frame_alerts(payload)
    counts = as_dict(as_dict(as_dict(as_dict(payload.get("groups")).get("activity")).get("data")).get("counts"))
    right: list[str] = []
    if finite_number(counts.get("running")) is not None:
        right.append(f"{format_count(counts['running'])} running")
    leads = finite_number(counts.get("leads"))
    if leads is not None:
        right.append(f"{format_count(leads)} {'lead' if leads == 1 else 'leads'}")
    low = as_dict(payload.get("min_quota"))
    if low:
        state = clean_text(low.get("state") or "?")
        aged = " (aged)" if low.get("aged") is True and state != "aged" else ""  # A6: the pool's observation is stale
        if url := payload.get("_state_doc_url"):
            state = osc_link(f"{url.partition('#')[0]}#{STATE_WORDS_ANCHOR}", state)
        right.append(f"min quota {format_count(low.get('left_pct'))}% {clean_text(low.get('pool') or '?')} {state}{aged}")
    if not unicode:
        right = [ascii_text(item, keep) for item in right]
    if not alerts:
        unread = _unread(payload)
        head = "OK" if unread is None else f"OK? ({unread})"
        head = head if unicode else ascii_text(head, keep)
        for fields in range(len(right), -1, -1):
            line = sep.join([head, *right[:fields]])
            if cell_width(line) <= width:
                return line
        return fit_text(head, width, unicode)
    head = f"ATTN {len(alerts)}" + ("?" if _unread(payload) is not None else "")  # A7: a source was not read
    names = [_alert_text(alert) if unicode else ascii_text(_alert_text(alert), keep) for alert in alerts[:2]]

    def compose(shown: int, fields: int) -> str:
        rest = len(alerts) - shown
        return sep.join([head, *names[:shown], *([f"+{rest}"] if rest else []), *right[:fields]])

    for shown in range(len(names), 0, -1):
        for fields in (range(len(right), -1, -1) if shown == len(names) else (0,)):
            line = compose(shown, fields)
            if cell_width(line) <= width:
                return line
    tail = f"{sep}+{len(alerts) - 1}" if len(alerts) > 1 else ""
    room = width - cell_width(f"{head}{sep}{tail}")
    if room >= 4:
        return f"{head}{sep}{fit_text(names[0], room, unicode)}{tail}"
    return fit_text(compose(0, 0), width, unicode)


def decorate_verdict(verdict: str, sep: str, clock: str, width: int, paint: bool) -> str:
    """Line 1 of the rich look (P21): the status word as a pill, the separators dim, and the time at the right edge when
    two cells of room remain before it. `verdict` was fitted for two cells less: the pill's padding."""
    match = re.match(r"OK\??|ATTN \d+\??", verdict)
    head = match[0] if match else ""
    gap = width - cell_width(verdict) - (2 if head else 0) - cell_width(clock)
    timed = bool(clock) and gap >= 2
    if not paint:
        return verdict + (" " * (gap + (2 if head else 0)) + clock if timed else "")
    dim = RICH_SGR["dim"]
    pill = paint_text(f" {head} ", PILL_SGR["ATTN" if head.startswith("ATTN") else head]) if head else ""
    return pill + verdict[len(head):].replace(sep, paint_text(sep, dim)) + (" " * gap + paint_text(clock, dim) if timed else "")


def _alert_row(alert: dict[str, Any], unicode: bool = True, keep: str | None = None, sep: str = " | ") -> str:
    age = finite_number(alert.get("age_s"))
    return sep.join((f"{cell_text(alert.get('kind'), unicode, '?', keep)}: {cell_text(alert.get('subject'), unicode, '?', keep)}",
                     f"age {'-' if age is None else compact_age(age)}", cell_text(alert.get("severity"), unicode, "?", keep),
                     cell_text(alert.get("code"), unicode, "?", keep)))


def _safe_error_reason(value: object) -> str:
    """Expose a bounded exception class only; never print raw exception text."""
    raw = clean_text(value)
    code = raw.split(":", 1)[0].strip().split(".")[-1]
    allowed = {"OperationalError", "DatabaseError", "DataError", "IntegrityError", "ProgrammingError",
               "InterfaceError", "SchemaVersionError", "PermissionError", "FileNotFoundError", "OSError"}
    return f"{code}: database read failed" if code in allowed else "database read failed"


_ERROR_SOURCES = {  # the reads a group is made of: a group without an error of its own names the first that failed
    "overview": ("sessions", "execution"), "peers": ("sessions", "capacity"), "sessions": ("sessions", "execution"),
    "usage": ("sessions", "execution"), "context": ("sessions", "execution"), "runtime": ("sessions", "execution"),
    "execution": ("execution",), "coverage": ("execution",), "capacity": ("capacity",), "store": ("store",),
}


def group_error(payload: dict[str, Any], group_id: str) -> str:
    """The row a failed group starts with: the class of its error only (`_safe_error_reason`)."""
    errors = as_dict(payload.get("errors"))
    error = errors.get(group_id)
    if error is None:
        error = next((errors[key] for key in _ERROR_SOURCES.get(group_id, (group_id,)) if key in errors), None)
    return _safe_error_reason(error)


def alert_section(payload: dict[str, Any], width: int, unicode: bool, keep: str | None, *,
                   rich: bool, paint: bool, expand: bool) -> list[str] | None:
    alerts = frame_alerts(payload)
    unread = unread_sources(payload) if expand else []
    if not alerts and not unread:
        return None
    data = {"alerts": alerts, "unread": unread}
    head = alert_label(data)
    sep = " · " if rich and unicode else " | "
    rows = [row for row in alert_rows(data, unicode, keep, sep) if not row.startswith("Coverage:")] or ["No data"]
    if rich and expand:
        return rich_section(head, rows, width, unicode, paint)
    if rich:
        return [heading_rule(head, width, unicode, paint), *(" " + row for row in rows)]
    return [section_rule(head, width, unicode), *rows]


def alert_label(data: dict[str, Any]) -> str:
    return f"{FRAME_SECTIONS['alerts']} {len(dict_items(data.get('alerts')))}"


def alert_rows(data: dict[str, Any], unicode: bool, keep: str | None, sep: str) -> list[str]:
    rows = [_alert_row(alert, unicode, keep, sep) for alert in dict_items(data.get("alerts"))]
    rows.extend(cell_text(row, unicode, "-", keep) for row in cast(list[object], data.get("unread") or []))  # A7
    return rows or ["none"]
