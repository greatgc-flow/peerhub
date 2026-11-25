"""Render compact and expanded monitor views of running asks."""
from __future__ import annotations

from typing import Any, cast

from peerhub.core.scalars import finite_number
from peerhub.cli.monitor_layout import (
    ACTIVITY_SNIPPET_COLS,
    RICH_GLYPHS,
    RICH_SGR,
    as_dict,
    cell_text,
    column_painter,
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
from peerhub.cli.monitor_legend import FRAME_SECTIONS, SUPERSEDED_MARK
from peerhub.cli.monitor_links import link_query, query_painter
from peerhub.extensions.liveness import ORPHAN_RUNNING


RUNNING_CAP = 5
_RUN_MARKS = {"": ("run", "", "ok"), "!gone": ("gone", " gone", "bad"), "!overdue": ("", "! overdue", "warn"),
              "!orphan": ("gone", " orphan", "bad"), "?": ("", "?", "warn")}  # glyph, word, colour


def _flag(row: dict[str, Any]) -> str:
    if row.get("state") == ORPHAN_RUNNING:
        return "!orphan"
    if row.get("state") == "overdue":
        return "!overdue"
    if row.get("alive") is False:
        return "!gone"
    return "?" if row.get("alive") is None else ""


def _is_open(row: dict[str, Any]) -> bool:
    return row.get("ended_at") is None and row.get("state") in ("run", "overdue", ORPHAN_RUNNING)


def ask_split(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Running, ended and superseded rows, each in the order the read model gives."""
    asks = dict_items(data.get("asks"))
    main = [row for row in asks if not row.get("superseded")]
    return ([row for row in main if _is_open(row)], [row for row in main if not _is_open(row)],
            [row for row in asks if row.get("superseded")])


def running_total(data: dict[str, Any], running: list[dict[str, Any]]) -> int:
    number = finite_number(as_dict(data.get("counts")).get("running"))
    return int(number) if number is not None and number >= 0 else len(running)


def running_head(data: dict[str, Any], running: list[dict[str, Any]], unicode: bool,
                  keep: str | None = None) -> tuple[str, int]:
    total = running_total(data, running)
    by_peer = as_dict(as_dict(data.get("counts")).get("by_peer"))
    peers = " ".join(f"{cell_text(peer, unicode, '-', keep)} {format_count(n)}" for peer, n in by_peer.items())
    return f"{FRAME_SECTIONS['running']} {total}" + (f" | {peers}" if peers and total else ""), total


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
                  else "provider process still alive" if flag == "!orphan"
                  else txt(row.get("alive_reason"), "liveness unknown") if flag == "?" else "caller alive")
        fields.append(f"flag {flag or 'none'} ({reason})")
    fields.append(f"state {state}" + (f" {SUPERSEDED_MARK}" if row.get("superseded") else ""))
    if open_row:
        left = finite_number(row.get("lease_left_s"))
        fields += [f"elapsed {compact_age(finite_number(row.get('elapsed_s')))}", f"started {timestamp_text(row.get('started_at'))}",
                   "lease ?" if left is None else f"lease left {compact_age(left)}" if left >= 0
                   else f"lease expired {compact_age(-left)} ago"]
        if "_lease_age_s" in row:
            age = finite_number(row["_lease_age_s"])
            fields.append("lease confirmed -" if age is None else f"lease confirmed {compact_age(age)} ago")
    else:
        exact = row.get("ended_exact")
        ended = timestamp_text(row.get("ended_at")) + ("" if row.get("ended_at") is None
                                               else " (exact)" if exact else " (latest possible)")
        fields += [f"age {compact_age(finite_number(row.get('age_s'))) if exact else '?'}",
                   f"elapsed {compact_age(finite_number(row.get('elapsed_s')))}", f"started {timestamp_text(row.get('started_at'))}",
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


def compact_running(data: dict[str, Any], width: int, unicode: bool, keep: str | None = None, *,
                    rich: bool = False, paint: bool = False, links: bool = False) -> tuple[list[str], int]:
    """Render the RUNNING header and capped table, and return the total before the cap."""
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
              short_text(cell(row.get("ask_line")), ACTIVITY_SNIPPET_COLS, unicode), cell(row.get("name"))]
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
    return [heading_rule(head, width, unicode, paint) if rich else head, *rows], total


def rich_running(data: dict[str, Any], width: int, unicode: bool, paint: bool, keep: str | None,
                 *, links: bool = False) -> list[str]:
    """Render every running ask with its state glyph and wrapped detail."""
    glyphs = RICH_GLYPHS[unicode]
    sep = f" {glyphs['sep']} "
    inner = max(1, width - 3)  # an entry's text starts in the fourth cell: the indent, the mark and a space

    def marked(glyph: str, colour: str, lines: list[str]) -> list[str]:
        return rich_entry(paint_text(glyph, RICH_SGR[colour]) if paint else glyph, lines)

    running, _, _ = ask_split(data)
    rows: list[str] = []
    for row in running:
        glyph, word, colour = _RUN_MARKS[_flag(row)]
        rows.extend(marked(glyphs.get(glyph) or word.strip()[:1], colour,
                           _ask_lines(row, inner, inner, unicode, keep, sep, links=links)))
    return rich_section(running_head(data, running, unicode, keep)[0], rows, width, unicode, paint)


def plain_running(data: dict[str, Any], width: int, unicode: bool, keep: str | None) -> list[str]:
    """Render every running ask in plain detail, marking the start of each entry."""
    running = ask_split(data)[0]
    rows = [running_head(data, running, unicode, keep)[0]]
    for row in running:
        rows.extend(_ask_rows(row, width, unicode, keep))
    return rows
