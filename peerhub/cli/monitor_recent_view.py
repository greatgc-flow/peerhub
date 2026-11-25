"""Render compact and expanded monitor views of finished asks."""
from __future__ import annotations

import re
from typing import Any

from peerhub.core.scalars import finite_converted_number as finite_number
from peerhub.core.text import clean_text
from peerhub.cli.monitor_layout import (
    ACTIVITY_SNIPPET_COLS,
    LEAD_LABEL_COLS,
    RICH_GLYPHS,
    RICH_SGR,
    as_dict,
    cell_text,
    cell_width,
    column_painter,
    compact_age,
    dict_items,
    fit_table,
    fit_text,
    heading_rule,
    paint_text,
    short_text,
    timestamp_text,
    wrap_text,
)
from peerhub.cli.monitor_legend import CONTEXT_ESTIMATE_LABEL, FRAME_SECTIONS, SUPERSEDED_MARK
from peerhub.cli.monitor_links import link_query, query_painter
from peerhub.cli.monitor_running_view import ask_split, running_total
from peerhub.extensions.monitor_queries import TEXT_NEWEST_ROWS


RECENT_CAP = 5
RECENT_EXPAND = 8
NAME_CAP = 24
_RECENT_DROPS = (("cache",), ("lead",), ("mode", "uses"), ("ctx",))  # the order narrow widths drop columns in
_RECENT_COLOURS = {"ok": "ok", "unsure": "warn", "overdue": "warn"}  # every other ended state is bad


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
    head = f"{FRAME_SECTIONS['recent']} {len(ended)} ({running_total(data, ask_split(data)[0])} running)"
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


def _recent_text(row: dict[str, Any]) -> object:
    if row.get("reply_line"):
        return row["reply_line"]
    if row.get("note"):
        return f"({clean_text(row['note'])})"
    return "-" if row.get("state") == "ok" else "(no reply)"


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
    replies = [(short_text(cell(_recent_text(row)), ACTIVITY_SNIPPET_COLS, unicode), bool(row.get("reply_line"))) for row in shown]
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
        f"{timestamp_text(row.get('started_at'), '%H:%M')}-{timestamp_text(row.get('ended_at'), '%H:%M')}",
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


def compact_recent(data: dict[str, Any], groups: dict[str, Any], width: int, unicode: bool, keep: str | None, *,
                   rich: bool = False, paint: bool = False, links: bool = False) -> list[str] | None:
    """Render the RECENT header and capped table when the sample has finished asks."""
    ended = _recent_ended(data, groups)
    if not ended:
        return None
    head = _recent_head(data, ended)
    return [heading_rule(head, width, unicode, paint) if rich else head,
            *_recent_compact(ended, width, unicode, keep, rich=rich, paint=paint, links=links)]


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
