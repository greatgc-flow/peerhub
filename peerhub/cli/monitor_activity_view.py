"""Render running asks, recent asks, and leads in both monitor layouts."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from peerhub.cli.monitor_alert_view import group_error
from peerhub.cli.monitor_layout import (
    RICH_GLYPHS,
    RICH_SGR,
    as_dict,
    cell_text,
    cell_width,
    clean_text,
    column_painter,
    compact_age,
    dict_items,
    finite_number,
    fit_table,
    fit_text,
    format_count,
    heading_rule,
    pack_fields,
    paint_text,
    rich_section,
    short_text,
    table_columns,
    utc_text,
    wrap_text,
)
from peerhub.cli.monitor_legend import CONTEXT_ESTIMATE_LABEL, FRAME_SECTIONS
from peerhub.cli.monitor_links import link_query, query_painter
from peerhub.extensions.monitor_activity import LEAD_CONTEXT_MARK_PCT
from peerhub.extensions.monitor_queries import TEXT_NEWEST_ROWS


RUNNING_CAP = 5
LEADS_CAP = 3
LEAD_LABEL_COLS = 16
RECENT_CAP = 5
REQUEST_CAP = 60
RECENT_EXPAND = 8
METER_CELLS = 5  # the context meter of a LEADS row


def _stamp(value: object) -> str:
    number = finite_number(value)
    return "?" if number is None else utc_text(number, "%Y-%m-%d %H:%M:%SZ") or "?"


def _ask_fields(row: dict[str, Any], unicode: bool = True, keep: str | None = None) -> list[str]:
    """Every field of one ask row (#374 C): the expand view hides nothing the read model gives. Text is made plain-view
    safe here, before packing, so a replaced character can never widen a packed line (A5)."""

    def txt(value: object, empty: str = "?") -> str:
        return cell_text(value, unicode, empty, keep)

    state = txt(row.get("state"))
    open_row = _is_open(row)
    fields: list[str] = []
    if open_row:
        flag = _flag(row)
        left = finite_number(row.get("lease_left_s"))
        reason = (f"lease expired {compact_age(-left)} ago" if flag == "!overdue" and left is not None and left < 0
                  else "lease expired" if flag == "!overdue" else "caller process gone" if flag == "!gone"
                  else txt(row.get("alive_reason"), "liveness unknown") if flag == "?" else "caller alive")
        fields.append(f"flag {flag or 'none'} ({reason})")
    fields.append(f"state {state}" + (f" {SUPERSEDED_MARK}" if row.get("superseded") else ""))
    if open_row:
        left = finite_number(row.get("lease_left_s"))
        fields += [f"elapsed {compact_age(finite_number(row.get('elapsed_s')))}", f"started {_stamp(row.get('started_at'))}",
                   "lease ?" if left is None else f"lease left {compact_age(left)}" if left >= 0
                   else f"lease expired {compact_age(-left)} ago"]
        if "_lease_age_s" in row:
            age = finite_number(row["_lease_age_s"])
            fields.append("lease confirmed -" if age is None else f"lease confirmed {compact_age(age)} ago")
    else:
        exact = row.get("ended_exact")
        ended = _stamp(row.get("ended_at")) + ("" if row.get("ended_at") is None
                                               else " (exact)" if exact else " (latest possible)")
        fields += [f"age {compact_age(finite_number(row.get('age_s'))) if exact else '?'}",
                   f"elapsed {compact_age(finite_number(row.get('elapsed_s')))}", f"started {_stamp(row.get('started_at'))}",
                   f"ended {ended}"]
    lead = as_dict(row.get("lead"))
    tokens = as_dict(row.get("tokens"))
    fields += [f"profile {txt(row.get('profile'))}", f"peer {txt(row.get('peer'))}",
               f"model {txt(row.get('model'))}", f"effort {txt(row.get('effort'))}",
               f"lead {txt(lead.get('label'))} ({txt(lead.get('tool'))} {txt(lead.get('id'))})" if lead else "lead none",
               f"name {txt(row.get('name'))}", f"stream {txt(row.get('stream'))}",
               f"tokens in {format_count(tokens.get('input'))} out {format_count(tokens.get('output'))}" if tokens else "tokens ?"]
    if row.get("note"):
        fields.append(f"note {txt(row['note'])}")
    reasons = [txt(reason) for reason in cast(list[object], row.get("reasons") or []) if reason]
    if reasons:
        fields.append("reasons " + ", ".join(reasons))
    return fields


def _ask_lines(row: dict[str, Any], fields_width: int, text_width: int, unicode: bool = True, keep: str | None = None,
               sep: str = " | ", *, links: bool = False) -> list[str]:
    """One ask without a mark: its fields packed to `fields_width`, then its reply (an ended ask) and its ask text
    wrapped to `text_width`."""
    lines = pack_fields(_ask_fields(row, unicode, keep), max(1, fields_width), sep)
    texts = [] if _is_open(row) else [("reply", row.get("reply_line_long"))]
    for label, text in [*texts, ("ask", row.get("ask_line_long"))]:
        wrapped = wrap_text(f"{label}: {cell_text(text, unicode, '-', keep)}", max(1, text_width))
        if links and label == "ask" and wrapped[0].startswith("ask:"):
            wrapped[0] = link_query(row, "ask") + wrapped[0][3:]
        lines.extend(wrapped)
    return lines


def _ask_rows(row: dict[str, Any], width: int, unicode: bool = True, keep: str | None = None) -> list[str]:
    lines = _ask_lines(row, width - 2, width, unicode, keep)
    lines[0] = "- " + lines[0]  # marks where each ask starts; continuation lines carry no mark
    return lines


def _clock(value: object, fmt: str) -> str:
    number = finite_number(value)
    return "?" if number is None else utc_text(number, fmt) or "?"


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
            f"last ask {_stamp(lead.get('last_ask_at'))}"]
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
    first = [*head, f"{txt(item.get('state'))} since {_stamp(item.get('state_since'))}",  # A-4: with the date
             "active ?" if active is None or now is None else f"active {compact_age(now - active)} ago",
             "alive" if item.get("alive") is True else "alive ?", f"pid {txt(item.get('pid'))}",
             f"entry {txt(item.get('entry'))}", f"claude {txt(item.get('version'))}",
             f"started {_clock(item.get('started_at'), '%Y-%m-%d %H:%MZ')}"]
    second = [f"model {txt(item.get('model'))}", ctx + ("" if tokens is None else f" at {_stamp(item.get('context_at'))}"),
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


def _recent_ended(data: dict[str, Any], groups: dict[str, Any]) -> list[dict[str, Any]]:
    """Finished asks in end order, with live session facts only on the latest ask of each scope."""
    running, ended, _ = ask_split(data)
    ended.sort(key=lambda row: (finite_number(row.get("ended_at")) is None, -(finite_number(row.get("ended_at")) or 0.0)))
    newest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted([*running, *ended], key=lambda row: finite_number(row.get("started_at")) or 0, reverse=True):
        if row.get("stream"):
            newest.setdefault((str(row["stream"]), str(row.get("peer"))), row)

    samples: dict[tuple[str, str], dict[str, Any]] = {}
    for item in dict_items(as_dict(as_dict(groups.get("sessions")).get("data")).get("items")):
        key = (str(item.get("stream_id")), str(item.get("peer_id")))
        if key not in samples or (finite_number(item.get("generation")) or 0) > (finite_number(samples[key].get("generation")) or 0):
            samples[key] = item

    result: list[dict[str, Any]] = []
    for row in ended:
        key = (str(row.get("stream")), str(row.get("peer")))
        if key in newest and newest[key] is not row:
            result.append({**row, "session": None})
            continue
        session = as_dict(row.get("session"))
        sample = samples.get(key, {})
        context = as_dict(as_dict(sample.get("telemetry")).get("context"))
        started, observed = finite_number(row.get("started_at")), finite_number(context.get("observed_at"))
        if (not _recent_context(as_dict(session.get("context"))) and _recent_context(context)
                and (sample.get("provider") or session.get("provider") or row.get("peer")) in {"cx", "ag"}
                and not sample.get("tombstoned_at") and sample.get("state") != "TOMBSTONED"
                and (session.get("generation") is None or session.get("generation") == sample.get("generation"))
                and started is not None and observed is not None and observed >= started):
            row = {**row, "_context_estimate": context}
        result.append(row)
    return result


def _recent_head(data: dict[str, Any], ended: list[dict[str, Any]], *, superseded: bool = False) -> str:
    """`RECENT 130 (2 running)`: the finished asks, and how many more run (they are listed under RUNNING). The detail
    frames also report the superseded sample."""
    head = f"{FRAME_SECTIONS['recent']} {len(ended)} ({running_head(data, ask_split(data)[0], True)[1]} running)"
    omitted = finite_number(data.get("superseded_omitted")) or 0
    replaced = len(ask_split(data)[2])
    if superseded and (replaced or omitted):
        head += f" | superseded {replaced}" + (f" (+{int(omitted)} not read)" if omitted else "")
    return head


def _kilo(value: object) -> str | None:
    """A token count as `850`, `62k`, `1M`, `1.2M`; None when it is not a count."""
    number = finite_number(value)
    if number is None or number < 0:
        return None
    count = int(number)
    if count < 1000:
        return str(count)
    if count < 999_500:
        return f"{round(count / 1000)}k"
    return f"{count / 1_000_000:.1f}".removesuffix(".0") + "M"


def _whole(value: object, high: float | None = None) -> int | None:
    number = finite_number(value)
    return int(number) if number is not None and number >= 0 and (high is None or number <= high) else None


def _recent_context(context: dict[str, Any]) -> bool:
    return (context.get("state") in {"ESTIMATED", "MEASURED"}
            and (_whole(context.get("occupancy_tokens")) is not None or _whole(context.get("percent"), 100) is not None))


def _recent_facts(row: dict[str, Any]) -> dict[str, Any]:
    """The ask's own tokens and any eligible live session or sampled context values."""
    session = as_dict(row.get("session"))
    mode_raw = session.get("execution_mode")
    estimate = as_dict(row.get("_context_estimate"))
    context = estimate or as_dict(session.get("context"))
    usable = _recent_context(context)
    tokens = as_dict(row.get("tokens"))
    read, total = _whole(tokens.get("cache_read")), _whole(tokens.get("input"))
    return {"mode": "R" if mode_raw in {"reused", "reuse", "resume"} else "F" if mode_raw == "fresh" else None,
            "uses": _whole(session.get("uses")), "generation": _whole(session.get("generation")),
            "ctx_tokens": _whole(context.get("occupancy_tokens")) if usable else None,
            "ctx_window": _whole(context.get("window_tokens")) if usable else None,
            "ctx_pct": _whole(context.get("percent"), 100) if usable else None, "ctx_estimate": bool(estimate),
            "input": total, "output": _whole(tokens.get("output")), "cache_read": read,
            "cache_write": _whole(tokens.get("cache_write")),
            "cache_pct": (int(read / total * 100 + 0.5) if read is not None and total and read <= total else None)}


NAME_CAP = 24
SUPERSEDED_MARK = "(superseded)"
_RECENT_DROPS = (("cache",), ("lead",), ("mode", "uses"), ("ctx",))  # the order narrow widths drop columns in


def _recent_compact(ended: list[dict[str, Any]], width: int, unicode: bool, keep: str | None, *, rich: bool = False,
                    paint: bool = False, links: bool = False, limit: int | None = None) -> list[str]:
    """The compact RECENT rows (the first `limit`, RECENT_CAP by default, of `ended`):
    `ok 3m cc.effort lead7 s-ab12 R u3 62k/6% c91% "reply snippet"`: state, age since the end, profile, lead, name,
    R resume | F fresh, `u<uses>`, context, cache-read share, the reply. A narrow width drops the cache share, then the
    lead, then the mode with the uses, then the context; the name and the reply stay, and the reply is cut by cell width.
    The rich view puts a state dot before the state when that costs no column."""
    glyphs = RICH_GLYPHS[unicode]

    def cell(value: object) -> str:
        return cell_text(value, unicode, "-", keep)

    shown = ended[:limit or RECENT_CAP]
    table: list[dict[str, str]] = []
    for row in shown:
        facts = _recent_facts(row)
        tokens, pct = _kilo(facts["ctx_tokens"]), facts["ctx_pct"]
        ctx = f"{tokens}/{pct}%" if tokens and pct is not None else tokens or (f"{pct}%" if pct is not None else "-")
        if facts["ctx_estimate"]:
            ctx += f" {CONTEXT_ESTIMATE_LABEL}"
        table.append({
            "state": cell(row.get("state")),
            "age": compact_age(finite_number(row.get("age_s"))) if row.get("ended_exact") else "?",
            "profile": cell(row.get("profile") or row.get("peer")),
            "lead": short_text(cell(as_dict(row.get("lead")).get("label")), LEAD_LABEL_COLS, unicode),
            "name": short_text(cell(row.get("name")), NAME_CAP, unicode),
            "mode": facts["mode"] or "-", "uses": "-" if facts["uses"] is None else f"u{facts['uses']}",
            "ctx": ctx, "cache": "-" if facts["cache_pct"] is None else f"c{facts['cache_pct']}%"})
    replies = [(short_text(cell(_recent_text(row)), REQUEST_CAP, unicode), bool(row.get("reply_line"))) for row in shown]
    order = ["state", "age", "profile", "lead", "name", "mode", "uses", "ctx", "cache"]
    floor = min(24, max(cell_width(text) + (2 if quoted else 0) for text, quoted in replies))  # what the reply is given first

    def room(columns: list[str], dot: int) -> int:
        fixed = sum(max(cell_width(cells[name]) for cells in table) + (dot if name == "state" else 0) + 2 for name in columns)
        return width - 1 - fixed

    def select(dot: int) -> list[str]:
        gone: set[str] = set()
        for step in range(len(_RECENT_DROPS) + 1):
            columns = [name for name in order if name not in gone]
            if room(columns, dot) >= floor or step == len(_RECENT_DROPS):
                return columns
            gone.update(_RECENT_DROPS[step])
        return order  # pragma: no cover - the loop always returns

    columns = select(0)
    dotted = rich and len(select(2)) >= len(columns)
    if dotted:
        columns = select(2)
    left = room(columns, 2 if dotted else 0)
    need = min(10, max(cell_width(text) + (2 if quoted else 0) for text, quoted in replies))
    for name in ("name", "profile"):  # nothing is left to drop: the name, then the profile, give up cells to the reply
        if left >= need:
            break
        widest = max(cell_width(fields[name]) for fields in table)
        cap = max(8, widest - (need - left))
        if cap < widest:
            for fields in table:
                fields[name] = fit_text(fields[name], cap, unicode)
            left = room(columns, 2 if dotted else 0)
    left = max(1, left)
    cells: list[list[str]] = []
    for fields, (text, quoted) in zip(table, replies):
        reply = f'"{fit_text(text, max(1, left - 2), unicode)}"' if quoted and left >= 3 else fit_text(text, left, unicode)
        row = [fields[name] for name in columns]
        if dotted:
            row[0] = f"{glyphs['dot']} {row[0]}"
        cells.append([*row, reply])
    colours = [_RECENT_COLOURS.get(str(row.get("state")), "bad") for row in shown]
    painter = column_painter(0, colours) if dotted and paint else None
    if links:
        painter = query_painter(shown, columns.index("name"), painter)
    return [" " + line for line in fit_table(cells, width - 1, unicode, right=(1,), elastic=len(columns), paint=painter)]


def _duration(seconds: object) -> str:
    """An elapsed time with its seconds: `45s`, `3m12s`, `1h02m`, `2d03h`; `?` when unknown."""
    number = finite_number(seconds)
    if number is None or number < 0:
        return "?"
    total = int(number)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    if total < 86400:
        return f"{total // 3600}h{total % 3600 // 60:02d}m"
    return f"{total // 86400}d{total % 86400 // 3600:02d}h"


def _recent_entry(row: dict[str, Any], width: int, unicode: bool, keep: str | None, sep: str = " | ", *,
                  links: bool = False) -> list[str]:
    """One expanded ask, with wrapped metadata followed by a token and text line."""
    facts = _recent_facts(row)

    def cell(value: object) -> str:
        return cell_text(value, unicode, "-", keep)

    model = re.sub(r"^claude-", "", clean_text(row.get("model") or ""))
    peer = cell(row.get("peer"))
    who = f"{peer}/{cell(model or None)}" + (f" [{cell(row.get('effort'))}]" if row.get("effort") else "")
    tokens, window, pct = _kilo(facts["ctx_tokens"]), _kilo(facts["ctx_window"]), facts["ctx_pct"]
    ctx = "-" if tokens is None and pct is None else " ".join(
        part for part in (f"{tokens}/{window}" if tokens and window else tokens, f"{pct}%" if pct is not None else None)
        if part)
    joined = any(facts[key] is not None for key in ("mode", "uses", "generation")) or (ctx != "-" and not facts["ctx_estimate"])
    if facts["ctx_estimate"]:
        ctx += f" {CONTEXT_ESTIMATE_LABEL}"
    first = " ".join([
        cell(row.get("state")), *([SUPERSEDED_MARK] if row.get("superseded") else []),
        f"{_clock(row.get('started_at'), '%H:%M')}-{_clock(row.get('ended_at'), '%H:%M')}",
        f"({_duration(row.get('elapsed_s'))})", who, cell(as_dict(row.get("lead")).get("label")), cell(row.get("name")),
        "gen-" if facts["generation"] is None else f"gen{facts['generation']}", facts["mode"] or "-",
        "u-" if facts["uses"] is None else f"u{facts['uses']}", f"ctx {ctx}", *(["now"] if joined else [])])
    cache = [part for part in (None if facts["cache_read"] is None else f"r{_kilo(facts['cache_read'])}",
                               None if facts["cache_write"] is None else f"w{_kilo(facts['cache_write'])}",
                               None if facts["cache_pct"] is None else f"{facts['cache_pct']}%") if part]

    def count(key: str) -> str:
        return _kilo(facts[key]) or "-"

    prefix = f"in {count('input')} out {count('output')} cache {' '.join(cache) or '-'}{sep}"
    ask = row.get("ask_line_long") or row.get("ask_line")
    said = row.get("reply_line_long") or row.get("reply_line")  # a note stands in for a reply that was never written
    reply = said or (f"({clean_text(row['note'])})" if row.get("note") else None)
    texts = [cell(ask), cell(reply)]
    room = max(2, width - cell_width(prefix) - cell_width('ask "" -> reply ""'))
    if sum(cell_width(text) for text in texts) > room:  # the shorter text keeps what it needs; the other takes the rest
        half = room // 2
        short, long = sorted(range(2), key=lambda i: cell_width(texts[i]))
        if cell_width(texts[short]) <= half:
            texts[long] = fit_text(texts[long], max(1, room - cell_width(texts[short])), unicode)
        else:
            texts = [fit_text(texts[0], max(1, half), unicode), fit_text(texts[1], max(1, room - half), unicode)]
    quote = [f'"{text}"' if value else text for text, value in zip(texts, (ask, said))]
    second = fit_text(f"{prefix}ask {quote[0]} -> reply {quote[1]}", width, unicode)
    if links and second.startswith(prefix + "ask "):
        second = prefix + link_query(row, "ask") + second[len(prefix) + 3:]
    return [*wrap_text(first, width), second]


def _recent_expand_lines(ended: list[dict[str, Any]], width: int, unicode: bool, keep: str | None, *, rich: bool = False,
                         paint: bool = False, links: bool = False) -> list[str]:
    """Expanded RECENT entries, with the outcome command below the ask and reply."""
    glyphs = RICH_GLYPHS[unicode]
    lines: list[str] = []
    for row in ended:
        inner = max(1, width - 3)
        first, *details = _recent_entry(row, inner, unicode, keep, f" {glyphs['sep']} " if rich else " | ", links=links)
        mark = glyphs["dot"] if rich else ""
        if mark and paint:
            mark = paint_text(mark, RICH_SGR[_RECENT_COLOURS.get(str(row.get("state")), "bad")])
        lines.extend([" " + (f"{mark} " if mark else "") + first, *("   " + detail for detail in details)])
        if row.get("_ask_id"):
            lines.extend("   " + part for part in wrap_text(
                f"peerhub outcome {row['_ask_id']} <accepted|rejected|retried>", inner))
    return lines


def _activity_notes(data: dict[str, Any], width: int, sep: str = " | ") -> list[str]:
    """What the activity sample left out: a truncated sample, and why a workspace generation was not read."""
    rows: list[str] = []
    if data.get("truncated"):
        rows.extend(pack_fields([f"sample truncated: {format_count(data.get('scopes_total'))} scopes",
                           f"superseded not read {format_count(data.get('superseded_omitted'))}"], width, sep))
    if data.get("reason"):
        rows.extend(pack_fields([f"workspace generation: {clean_text(data['reason'])}"], width, sep))
    return rows


def _flag(row: dict[str, Any]) -> str:
    if row.get("state") == "overdue":
        return "!overdue"
    if row.get("alive") is False:
        return "!gone"
    return "?" if row.get("alive") is None else ""


def _is_open(row: dict[str, Any]) -> bool:
    """`run`, or `overdue` without an end: the read model ends an overdue row once the alert window has passed."""
    return row.get("state") == "run" or (row.get("state") == "overdue" and row.get("ended_at") is None)


def ask_split(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Running, ended and superseded rows, each in the order the read model gives."""
    asks = dict_items(data.get("asks"))
    main = [row for row in asks if not row.get("superseded")]
    return ([row for row in main if _is_open(row)], [row for row in main if not _is_open(row)],
            [row for row in asks if row.get("superseded")])


def running_head(data: dict[str, Any], running: list[dict[str, Any]], unicode: bool,
                  keep: str | None = None) -> tuple[str, int]:
    counts = as_dict(data.get("counts"))
    number = finite_number(counts.get("running"))
    total = int(number) if number is not None and number >= 0 else len(running)
    by_peer = as_dict(counts.get("by_peer"))
    peers = " ".join(f"{cell_text(peer, unicode, '-', keep)} {format_count(n)}" for peer, n in by_peer.items())
    return f"{FRAME_SECTIONS['running']} {total}" + (f" | {peers}" if peers and total else ""), total


def _recent_text(row: dict[str, Any]) -> object:
    if row.get("reply_line"):
        return row["reply_line"]
    if row.get("note"):
        return f"({clean_text(row['note'])})"
    return "-" if row.get("state") == "ok" else "(no reply)"


_RUN_MARKS = {"": ("run", "", "ok"), "!gone": ("gone", " gone", "bad"), "!overdue": ("", "! overdue", "warn"),
              "?": ("", "?", "warn")}  # a RUNNING row's flag: its glyph, the word beside it, its colour
_RECENT_COLOURS = {"ok": "ok", "unsure": "warn", "overdue": "warn"}  # every other ended state is bad


def compact_activity(groups: dict[str, Any], width: int, unicode: bool, keep: str | None = None, *,
                       rich: bool = False, paint: bool = False, links: bool = False) -> tuple[list[str], int, list[str] | None]:
    """RUNNING (header and at most five rows, the total before the cap) and RECENT (`recent_compact`: at most RECENT_CAP
    finished asks, newest first, or None). The rich view (P21) draws each head as a rule, starts a RUNNING row with its
    state glyph (and the word of a flag) and a RECENT row with a state dot; such a cell is used only when it costs no
    column."""
    def head_line(head: str) -> str:
        return heading_rule(head, width, unicode, paint) if rich else head

    group = as_dict(groups.get("activity"))
    if not isinstance(group.get("data"), Mapping):
        why = "read failed" if clean_text(group.get("status") or "").upper() in {"ERROR", "FAILED"} else "not read"
        return [head_line(f"{FRAME_SECTIONS['running']} ? | activity {why}")], 0, None
    data = as_dict(group.get("data"))
    glyphs = RICH_GLYPHS[unicode]

    def cell(value: object) -> str:  # text cells keep what the plain view's encoding can show (A5)
        return cell_text(value, unicode, "-", keep)

    running = ask_split(data)[0]
    head, total = running_head(data, running, unicode, keep)
    shown = running[:RUNNING_CAP]
    flags = [_flag(row) for row in shown]
    flagged = any(flags)
    table = [[*([flag] if flagged else []), compact_age(finite_number(row.get("elapsed_s"))),
              cell(row.get("profile") or row.get("peer")), cell(as_dict(row.get("lead")).get("label")),
              short_text(cell(row.get("ask_line")), REQUEST_CAP, unicode), cell(row.get("name"))]
             for row, flag in zip(shown, flags)]
    first = 1 if flagged else 0
    painter = None
    if rich and table:
        marks = [_RUN_MARKS[flag] for flag in flags]
        marked = [[glyphs.get(glyph, "") + word, *row[first:]] for (glyph, word, _), row in zip(marks, table)]
        if table_columns(marked, width - 1, 4)[0] - 1 >= table_columns(table, width - 1, first + 3)[0] - first:
            table, first = marked, 1
            painter = column_painter(0, [colour for _, _, colour in marks]) if paint else None
    if links:
        painter = query_painter(shown, first + 3, painter)
    rows = [" " + line for line in fit_table(table, width - 1, unicode, right=(first,), elastic=first + 3, paint=painter)]
    recent: list[str] | None = None
    ended = _recent_ended(data, groups)
    if ended:
        recent = [head_line(_recent_head(data, ended)),
                  *_recent_compact(ended, width, unicode, keep, rich=rich, paint=paint, links=links)]
    return [head_line(head), *rows], total, recent


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


def _rich_entry(mark: str, lines: Sequence[str]) -> list[str]:
    """One entry of RUNNING, LEADS or RECENT: its first line from the second cell (after its mark, if it has one),
    its other lines three cells in."""
    return [("" if index == 0 else "  ") + (f"{mark} " if mark and index == 0 else "") + line
            for index, line in enumerate(lines)]


def rich_activity(group: dict[str, Any], payload: dict[str, Any], now: float | None, width: int, unicode: bool,
                   paint: bool, keep: str | None) -> tuple[list[str], list[str]]:
    """(RUNNING, LEADS) of the rich --expand frame (P23): every running ask and every lead with every field, as the
    plain view lists them, under the compact frame's rules and with its markers: a RUNNING entry starts with the
    glyph of its flag (► ! × ?), and a lead's context has its meter. RECENT is not here: it follows POOLS in the top of
    the frame (`build_compact_frame`, #412). What the activity sample left out ends LEADS."""
    glyphs = RICH_GLYPHS[unicode]
    sep = f" {glyphs['sep']} "
    if not isinstance(group.get("data"), Mapping):
        failed = clean_text(group.get("status") or "").upper() in {"ERROR", "FAILED"}
        why = group_error(payload, "activity") if failed else "data unavailable"
        head = f"{FRAME_SECTIONS['running']} ? | activity {'read failed' if failed else 'not read'}"
        return rich_section(head, [why], width, unicode, paint), []
    data = as_dict(group.get("data"))
    inner = max(1, width - 3)  # an entry's text starts in the fourth cell: the indent, the mark and a space

    def marked(glyph: str, colour: str, lines: list[str]) -> list[str]:
        return _rich_entry(paint_text(glyph, RICH_SGR[colour]) if paint else glyph, lines)

    running, _, _ = ask_split(data)
    rows: list[str] = []
    for row in running:
        glyph, word, colour = _RUN_MARKS[_flag(row)]
        rows.extend(marked(glyphs.get(glyph) or word.strip()[:1], colour,
                           _ask_lines(row, inner, inner, unicode, keep, sep, links=bool(payload.get("_file_links")))))
    front = rich_section(running_head(data, running, unicode, keep)[0], rows, width, unicode, paint)
    leads = dict_items(data.get("leads"))
    rows = []
    for lead in leads:  # a lead has no mark: its label starts the entry
        rows.extend(_rich_entry("", _lead_rows(lead, now, max(1, width - 3), unicode, keep, sep=sep, meter=True,
                                               paint=paint)))
    rows.extend(_activity_notes(data, max(1, width - 1), sep))
    return front, rich_section(_leads_header(data, leads), rows, width, unicode, paint)


def plain_activity(group: dict[str, Any], payload: dict[str, Any], now: float | None, width: int, unicode: bool,
                    keep: str | None) -> tuple[list[str], list[str]]:
    """(RUNNING, LEADS) of the plain --expand frame: the heads are the compact frame's words, every running ask and every
    lead has every field (`- ` starts an ask). RECENT is in the top (`build_compact_frame`)."""
    if not isinstance(group.get("data"), Mapping):
        failed = clean_text(group.get("status") or "").upper() in {"ERROR", "FAILED"}
        why = group_error(payload, "activity") if failed else "data unavailable"
        return [f"{FRAME_SECTIONS['running']} ? | activity {'read failed' if failed else 'not read'}", why], []
    data = as_dict(group.get("data"))
    running = ask_split(data)[0]
    rows = [running_head(data, running, unicode, keep)[0]]
    for row in running:
        rows.extend(_ask_rows(row, width, unicode, keep))
    leads = dict_items(data.get("leads"))
    lead_rows = [_leads_header(data, leads)]
    for lead in leads:
        lead_rows.extend(_lead_rows(lead, now, width, unicode, keep))
    lead_rows.extend(_activity_notes(data, width))
    return rows, lead_rows


def expanded_recent(data: dict[str, Any], groups: dict[str, Any], width: int, unicode: bool, keep: str | None, *,
                    bounded: bool, rich: bool, paint: bool, links: bool
                    ) -> tuple[list[str] | None, list[str] | None]:
    """Render expanded RECENT and its single-line entries for height fitting."""
    ended = _recent_ended(data, groups)
    recent = ended[:RECENT_EXPAND if bounded else TEXT_NEWEST_ROWS]
    if not bounded:
        recent.extend({**row, "session": None} for row in ask_split(data)[2])
    if not recent:
        return None, None
    head = _recent_head(data, ended, superseded=True)
    lines = [heading_rule(head, width, unicode, paint) if rich else head,
             *_recent_expand_lines(recent, width, unicode, keep, rich=rich, paint=paint, links=links)]
    short = (_recent_compact(recent, width, unicode, keep, rich=rich, paint=paint, links=links, limit=RECENT_EXPAND)
             if bounded else None)
    return lines, short
