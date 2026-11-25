"""Render compact and expanded monitor views of lead sessions."""
from __future__ import annotations

import re
from typing import Any

from peerhub.core.scalars import finite_converted_number as finite_number
from peerhub.core.text import clean_text
from peerhub.cli.monitor_layout import (
    LEAD_LABEL_COLS,
    RICH_GLYPHS,
    RICH_SGR,
    as_dict,
    cell_text,
    compact_age,
    dict_items,
    fit_table,
    format_count,
    heading_rule,
    pack_fields,
    paint_text,
    rich_entry,
    rich_section,
    short_text,
    table_columns,
    timestamp_text,
    wrap_text,
)
from peerhub.cli.monitor_legend import FRAME_SECTIONS
from peerhub.extensions.monitor_activity import LEAD_CONTEXT_MARK_PCT


LEADS_CAP = 3
METER_CELLS = 5  # the context meter of a LEADS row


def _leads_header(data: dict[str, Any], leads: list[dict[str, Any]]) -> str:
    """Count the displayed entries and matched sessions separately; a found session need not be confirmed alive.

    Keep read warnings before the session statistic so they survive a narrow frame. With no rows after an incomplete
    read the entry count stays `?`; an older payload with no read evidence must not claim zero sessions were found.
    """
    count = len(leads)
    read = as_dict(data.get("leads_read"))
    state = read.get("state")
    notes = [f"{format_count(read.get(key))} {words}" for key, words in (("unreadable", "unreadable"), ("not_read", "not read"))
             if (finite_number(read.get(key)) or 0) > 0]
    note = (" | partial" + (f" ({', '.join(notes)})" if notes else "") if state == "partial"
            else " | read failed" if state == "error" else "")
    if note and count == 0:
        return f"{FRAME_SECTIONS['leads']} ?{note}"
    header = f"{FRAME_SECTIONS['leads']} {count} {'entry' if count == 1 else 'entries'}{note}"
    sessions = sum(bool(as_dict(lead.get("session"))) for lead in leads)
    if sessions or state in {"read", "partial", "error"}:
        header += f" | {sessions} {'session' if sessions == 1 else 'sessions'} found"
    return header


def _ctx_cell(item: dict[str, Any]) -> str:
    pct, tokens = finite_number(item.get("context_pct")), finite_number(item.get("context_tokens"))
    if pct is not None:
        return f"ctx {int(pct)}%" + ("!" if pct >= LEAD_CONTEXT_MARK_PCT else "")
    if tokens is not None and tokens >= 0:
        return f"ctx {int(tokens) // 1000}k" if tokens >= 1000 else f"ctx {int(tokens)}"
    return "ctx ?"


def _lead_cells(lead: dict[str, Any], now: float, unicode: bool, keep: str | None) -> list[str]:
    """label, state with the flag, age of the last activity, ctx, running asks, model, title (empty when unknown)."""
    label = short_text(cell_text(lead.get("label"), unicode, "?", keep), LEAD_LABEL_COLS, unicode)
    run = f"{format_count(lead.get('running'))} run"
    item = as_dict(lead.get("session"))
    if not item:
        return [label, "-", "-", "-", run, "-", ""]
    known = cell_text(item.get("state"), unicode, "", keep)
    state = ("?" if item.get("alive") is None else "") + known if known else "?"  # the flag only on a known state
    active = finite_number(item.get("active_at"))
    model = re.sub(r"^claude-", "", clean_text(item.get("model") or ""))
    return [label, state, "-" if active is None else compact_age(now - active), _ctx_cell(item), run,
            short_text(cell_text(model or None, unicode, "-", keep), 16, unicode), cell_text(item.get("title"), unicode, "", keep)]


def _ctx_meter(cell: str, pct: float | None, unicode: bool) -> str:
    """The ctx cell with its meter (P21): `ctx ■■□□□ 46%`; a cell without a percent (`ctx 106k`, `ctx ?`) gets the
    meter without a value, and a lead without a session keeps its `-`."""
    if not cell.startswith("ctx "):
        return cell
    glyphs = RICH_GLYPHS[unicode]
    if pct is None:
        return f"ctx {glyphs['none'] * METER_CELLS} {cell[4:]}"
    filled = min(METER_CELLS, max(0, int(pct / 100 * METER_CELLS + 0.5)))
    return f"ctx {glyphs['on'] * filled}{glyphs['off'] * (METER_CELLS - filled)} {cell[4:]}"


def _lead_rows(lead: dict[str, Any], now: float | None, width: int, unicode: bool = True,
               keep: str | None = None, *, sep: str = " | ", meter: bool = False, paint: bool = False) -> list[str]:
    """Every field of one lead (#376 3.4): its session (the lead and process, then model, context and asks, then name
    and cwd, then title, prompt and said), or why it has none. `meter` (the rich --expand frame, P23) puts the
    compact frame's context meter before the context numbers and its `!` at 80% or more; `paint` colours the meter
    as the compact frame does (bad from 80%, dim without a percent)."""

    def txt(value: object) -> str:
        return cell_text(value, unicode, "?", keep)

    head = [txt(lead.get("label")), f"{txt(lead.get('tool'))} {txt(lead.get('id'))}"]
    asks = [f"running {format_count(lead.get('running'))}", f"asks 24h {format_count(lead.get('asks_24h'))}",
            f"last ask {timestamp_text(lead.get('last_ask_at'))}"]
    item = as_dict(lead.get("session"))
    if not item:
        why = ("no session (not a Claude Code lead)" if lead.get("tool") != "cc"
               else "no session found (ended, or its Claude folder is not in PEERHUB_LEAD_DIRS)")
        return pack_fields([*head, why, *asks], width, sep)
    active, tokens = finite_number(item.get("active_at")), finite_number(item.get("context_tokens"))
    window, pct = finite_number(item.get("context_window")), finite_number(item.get("context_pct"))
    shown_pct = pct if tokens is not None and window is not None else None  # the percent the text names
    mark = "!" if meter and shown_pct is not None and shown_pct >= LEAD_CONTEXT_MARK_PCT else ""
    ctx = ("ctx ?" if tokens is None else f"ctx {format_count(tokens)} of {format_count(window)} model max ({int(pct)}%{mark})"
           if window is not None and pct is not None else f"ctx {format_count(tokens)} (window unknown)")
    if meter:
        ctx = _ctx_meter(ctx, shown_pct, unicode)
    first = [*head, f"{txt(item.get('state'))} since {timestamp_text(item.get('state_since'))}",  # A-4: with the date
             "active ?" if active is None or now is None else f"active {compact_age(now - active)} ago",
             "alive" if item.get("alive") is True else "alive ?", f"pid {txt(item.get('pid'))}",
             f"entry {txt(item.get('entry'))}", f"claude {txt(item.get('version'))}",
             f"started {timestamp_text(item.get('started_at'), '%Y-%m-%d %H:%MZ')}"]
    second = [f"model {txt(item.get('model'))}", ctx + ("" if tokens is None else f" at {timestamp_text(item.get('context_at'))}"),
              *asks]
    third = [*([f"name {txt(item['name'])}"] if item.get("name") else []), f"cwd {txt(item.get('cwd'))}"]
    packed = [pack_fields(part, width, sep) for part in (first, second, third)]
    code = (None if not (meter and paint) else RICH_SGR["dim"] if shown_pct is None
            else RICH_SGR["bad"] if shown_pct >= LEAD_CONTEXT_MARK_PCT else None)
    if code:  # painted after packing, so the colour never changes a width; a meter cut off its `ctx` stays plain
        cells = ctx.split(" ")[1]
        for index, line in enumerate(packed[1]):
            at = line.find(f"ctx {cells} ")
            if at >= 0:
                packed[1][index] = line[:at + 4] + paint_text(cells, code) + line[at + 4 + len(cells):]
                break
    rows = [line for part in packed for line in part]
    for label, key in (("title", "title"), ("prompt", "prompt_line"), ("said", "said_line")):
        if item.get(key):
            rows.extend(wrap_text(f"{label}: {txt(item[key])}", width))
    return rows


def _activity_notes(data: dict[str, Any], width: int, sep: str = " | ") -> list[str]:
    """What the activity sample left out: a truncated sample, and why a workspace generation was not read."""
    rows: list[str] = []
    if data.get("truncated"):
        rows.extend(pack_fields([f"sample truncated: {format_count(data.get('scopes_total'))} scopes",
                           f"superseded not read {format_count(data.get('superseded_omitted'))}"], width, sep))
    if data.get("reason"):
        rows.extend(pack_fields([f"workspace generation: {clean_text(data['reason'])}"], width, sep))
    return rows


def compact_leads(groups: dict[str, Any], now: float, width: int, unicode: bool, keep: str | None = None, *,
                   rich: bool = False, paint: bool = False) -> tuple[list[str] | None, int]:
    """LEADS (header and at most LEADS_CAP rows of the leads with a session or a running ask, the total before the cap),
    or None when there is no such lead and the lead sessions were read completely. The rich view (P21) draws the head
    as a rule and the context as a meter, unless the meter would cost a column."""
    data = as_dict(as_dict(groups.get("activity")).get("data"))
    leads = [lead for lead in dict_items(data.get("leads")) if lead.get("session") or (finite_number(lead.get("running")) or 0) > 0]
    header = _leads_header(data, leads)
    if not leads and as_dict(data.get("leads_read")).get("state") not in {"partial", "error"}:
        return None, 0
    table = [_lead_cells(lead, now, unicode, keep) for lead in leads[:LEADS_CAP]]
    titled = any(row[-1] for row in table)
    table = [row if titled else row[:-1] for row in table]
    elastic = 6 if titled else None
    pcts = [finite_number(as_dict(lead.get("session")).get("context_pct")) for lead in leads[:LEADS_CAP]]
    metered = [[*row[:3], _ctx_meter(row[3], pct, unicode), *row[4:]] for row, pct in zip(table, pcts)]
    meters = rich and table_columns(metered, width - 1, elastic)[0] >= table_columns(table, width - 1, elastic)[0]

    def painter(index: int, col: int, text: str) -> str:
        if col != 3 or not text.startswith("ctx "):
            return text
        pct = pcts[index]
        if pct is None:  # no value: the meter is structure
            meter, _, value = text[4:].partition(" ")
            return f"ctx {paint_text(meter, RICH_SGR['dim'])} {value}"
        return f"ctx {paint_text(text[4:], RICH_SGR['bad'])}" if pct >= LEAD_CONTEXT_MARK_PCT else text

    rows = fit_table(metered if meters else table, width - 1, unicode, right=(2,), elastic=elastic,
                  paint=painter if meters and paint else None)
    return [heading_rule(header, width, unicode, paint) if rich else header, *(" " + line for line in rows)], len(leads)


def rich_leads(data: dict[str, Any], now: float | None, width: int, unicode: bool, paint: bool,
               keep: str | None) -> list[str]:
    """Render every lead with its session details, context meter, and activity sample notes."""
    sep = f" {RICH_GLYPHS[unicode]['sep']} "
    leads = dict_items(data.get("leads"))
    rows: list[str] = []
    for lead in leads:  # a lead has no mark: its label starts the entry
        rows.extend(rich_entry("", _lead_rows(lead, now, max(1, width - 3), unicode, keep, sep=sep, meter=True,
                                               paint=paint)))
    rows.extend(_activity_notes(data, max(1, width - 1), sep))
    return rich_section(_leads_header(data, leads), rows, width, unicode, paint)


def plain_leads(data: dict[str, Any], now: float | None, width: int, unicode: bool, keep: str | None) -> list[str]:
    """Render every lead with its session details and activity sample notes in plain text."""
    leads = dict_items(data.get("leads"))
    lead_rows = [_leads_header(data, leads)]
    for lead in leads:
        lead_rows.extend(_lead_rows(lead, now, width, unicode, keep))
    lead_rows.extend(_activity_notes(data, width))
    return lead_rows
