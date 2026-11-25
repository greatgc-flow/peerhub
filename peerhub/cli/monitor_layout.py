"""Fit and style terminal cells, rows, and titles for the monitor."""
from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from peerhub._console import ANSI_ESCAPE
from peerhub.core.text import clean_text
from peerhub.core.scalars import finite_number


RULE_PREFIX_WIDTH = 3
RULE_MIN_TAIL_WIDTH = 2


FILE_HYPERLINK = re.compile(r"\x1b\]8;;file://[^\x1b]*\x1b\\([^\x1b]*)\x1b\]8;;\x1b\\")

def as_dict(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, Mapping) else {}


def dict_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    items = cast(Sequence[Any], value)
    return [cast(dict[str, Any], item) for item in items if isinstance(item, Mapping)]


def cell_width(text: str) -> int:
    return sum(0 if unicodedata.combining(char) or char == "\u200d"
               or "\ufe00" <= char <= "\ufe0f" or "\U000e0100" <= char <= "\U000e01ef"
               else 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
               for char in ANSI_ESCAPE.sub("", text))


def fit_text(text: str, width: int, unicode: bool, painted: bool = False, *, indent: bool = False) -> str:
    if width <= 0:
        return ""
    if painted and ANSI_ESCAPE.search(text):  # only for rows whose cells were all clean_text()ed before our own SGR colour was added
        if cell_width(text) <= width:
            return text
        text = ANSI_ESCAPE.sub("", text)
    leading = len(text) - len(text.lstrip(" ")) if indent else 0
    text = " " * leading + clean_text(text)
    if cell_width(text) <= width:
        return text
    mark = "…" if unicode else "~"
    if width == 1:
        return mark
    result = ""
    for char in text:
        if cell_width(result + char + mark) > width:
            break
        result += char
    return result + mark


def fit_fields(text: str, width: int, unicode: bool, painted: bool = False, *, inline_note: bool = True,
                indent: bool = False) -> str:
    """Drop trailing fields whole; mark a cut if the omission note cannot fit."""
    if cell_width(text) <= width:
        return fit_text(text, width, unicode, painted=painted, indent=indent)
    fields = re.split(r" (?:\||·) ", ANSI_ESCAPE.sub("", text))
    if len(fields) == 1:
        return fit_text(text, width, unicode, painted=painted, indent=indent)
    sep = " · " if " · " in text else " | "
    note = sep + "fields hidden" if inline_note else ""
    next_field = fields[-1]
    while len(fields) > 1 and cell_width(sep.join(fields) + note) > width:
        next_field = fields.pop()
    fitted = sep.join(fields)
    if cell_width(fitted) > width:
        return fit_text(fields[0], width, unicode)
    if cell_width(fitted + note) <= width:
        return clean_text(fitted + note)
    return fit_text(fitted + sep + next_field, width, unicode)


def fit_live_title(refresh_state: str, next_refresh: float | None, stamp: str,
                    details: Sequence[str], width: int, unicode: bool, *, inline_note: bool = True) -> tuple[str, bool]:
    """Keep live progress together, then timestamp, before provenance and age."""
    progress = [refresh_state] if refresh_state else []
    if next_refresh is not None:
        progress.append(f"refresh in {max(0, math.ceil(next_refresh))}s")
    essential = "PeerHub Monitor " + "; ".join(progress)
    if cell_width(essential) > width:
        essential = "Monitor " + "; ".join(progress)
        if cell_width(essential) > width and next_refresh is not None:
            essential = progress[-1]
            if cell_width(essential) > width:
                return fit_text("refresh pending", width, unicode), True
    title = " | ".join(field for field in [essential, stamp, *details] if field)
    dropped = cell_width(title) > width
    if dropped and inline_note and cell_width(essential + " | fields hidden") > width:
        # The caller discloses the cut separately when the live fields fill the line.
        return fit_text(essential, width, unicode), True
    return fit_fields(title, width, unicode, inline_note=inline_note), dropped


QUOTA_HEARTBEAT_ASCII = "|/-\\"
QUOTA_HEARTBEAT_UNICODE = "⠋⠙⠹⠸"


def _live_duration(seconds: float | None, *, countdown: bool = False) -> str:
    if seconds is None:
        return "?"
    seconds = max(0, math.ceil(seconds) if countdown else int(seconds))
    if seconds >= 3600:
        return format_duration(seconds)
    minutes, remainder = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m{remainder:02d}s" if remainder else f"{minutes}m"
    return f"{remainder}s"


def fit_live_quota_title(status: Mapping[str, Any], state: str, stamp: str, source: str,
                          width: int, unicode: bool) -> tuple[str, bool]:
    spinner = QUOTA_HEARTBEAT_UNICODE if unicode else QUOTA_HEARTBEAT_ASCII
    heartbeat = spinner[int(status.get("heartbeat") or 0) % len(spinner)]
    if width <= 1:
        return heartbeat, True
    peers = cast(Sequence[str], status.get("peers") or ())
    next_in = finite_number(status.get("next_in"))
    retry_in = finite_number(status.get("retry_in"))
    failed_ago = finite_number(status.get("failed_ago"))
    fields: list[str] = []
    if state:
        fields.append(state)
    elif peers:
        fields.append(f"refreshing {' '.join(clean_text(peer) for peer in peers)} {_live_duration(finite_number(status.get('elapsed')))}")
    else:
        retry = _live_duration(retry_in, countdown=True) if failed_ago is not None else None
        if failed_ago is not None:
            fields.extend((f"refresh failed {_live_duration(failed_ago)} ago", f"retry {retry}"))
        if next_in is not None:
            next_refresh = _live_duration(next_in, countdown=True)
            if next_refresh != retry:
                fields.append(f"next refresh {next_refresh}")
    fields.extend((f"oldest quota age {_live_duration(finite_number(status.get('age')))}",
                   f"PeerHub Monitor {stamp}", f"DB:{source}"))
    title = heartbeat + " " + " | ".join(fields)
    return fit_fields(title, width, unicode, inline_note=False), cell_width(title) > width


def hidden_lines(rows: int, groups: int, width: int, unicode: bool, *, expand: bool,
                  quota: bool = False, single_line: bool = False) -> list[str]:
    sep = " · " if unicode else " | "
    detail = f"+{rows} rows hidden"
    group_note = f" ({groups} {'group' if groups == 1 else 'groups'})" if groups else ""
    if expand:
        command = "peerhub monitor --snapshot --group all --expand | more"
        hint = "all rows: " + command
        if cell_width(detail + sep + hint) <= width:
            if cell_width(detail + group_note + sep + hint) <= width:
                detail += group_note
            return [detail + sep + hint]
        if single_line and cell_width(detail + sep + command) <= width:
            return [detail + sep + command]
        if not single_line and cell_width(command) <= width:
            return [fit_text(detail + group_note + "; all rows:", width, unicode), command]
        short = detail + sep + "--expand | more"
        if cell_width(short) <= width:
            return [short]
        return [fit_text(f"+{rows} --expand | more", width, unicode)]
    hint = ("peerhub monitor --snapshot --group capacity --expand" if quota else
            "peerhub monitor --snapshot --group all --expand")
    if cell_width(detail + group_note + sep + hint) <= width:
        detail += group_note
    elif cell_width(detail + sep + hint) > width:
        detail += group_note
        hint = "--expand"
    return [f"+{rows} --expand" if width < 32 else fit_fields(detail + sep + hint, width, unicode)]


def visible_fields(rows: Sequence[Sequence[str]], sep: str, width: int, keep: int) -> int:
    """How many leading fields every row shows whole: drop trailing fields until all rows fit (never below `keep`)."""
    count = max((len(row) for row in rows), default=0)
    while count > keep and any(cell_width(sep.join(field for field in row[:count] if field)) > width for row in rows):
        count -= 1
    return count


def omitted_rows(names: Sequence[str], width: int) -> list[str]:
    """The `Columns omitted:` disclosure, wrapped so narrow displays still name every dropped column."""
    rows: list[str] = []
    note = "Columns omitted:" if width > len("Columns omitted:") else "Omitted:"
    if not names:
        return rows
    for name in names:
        if cell_width(f"{note} {name}") > width:
            rows.append(note)
            note = name
        else:
            note += f" {name}"
    # A single name wider than a very narrow display is cut with a visible mark, never overflowing.
    return [row if cell_width(row) <= width else row[:max(1, width - 1)] + "~" for row in (*rows, note)]


def paint_text(text: str, code: str | None) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if code else text


def format_duration(seconds: float, *, extra_unit: bool = False) -> str:
    total_minutes, secs = divmod(math.floor(seconds), 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d{hours:02d}h" + (f"{minutes:02d}m" if extra_unit else "")
    text = f"{hours}h{minutes:02d}m" if hours > 0 else f"{total_minutes}m"
    return text + (f"{secs:02d}s" if extra_unit else "")


def title_rule(title: str, rest: str, width: int, unicode: bool, paint: bool) -> str:
    """A section head drawn as a rule (P21): `── POOLS 9 ── STATE ──────── LEFT  RESET ───`. The head's content
    stays: the title, then `rest` as it is, with the line drawn through the gap after the title (three cells or
    more), through a gap of five cells or more and to the end of the line. With `paint` the line is dim and the
    title bold."""
    dash = RICH_GLYPHS[unicode]["rule"]

    def line(count: int) -> str:
        return paint_text(dash * count, RICH_SGR["dim"]) if paint else dash * count

    room = width - RULE_PREFIX_WIDTH - cell_width(title + rest)
    rest = re.sub("^ {3,}| {5,}", lambda gap: f" {line(len(gap[0]) - 2)} ", rest)
    tail = f" {line(room - 1)}" if room >= RULE_MIN_TAIL_WIDTH else ""
    return f"{line(RULE_PREFIX_WIDTH - 1)} {paint_text(title, RICH_SGR['title']) if paint else title}{rest}{tail}"


def section_rule(label: str, width: int, unicode: bool) -> str:
    label = fit_fields(label, max(0, width - 4), unicode)
    return fit_text(title_rule(label, "", width, unicode, False), width, unicode)


def format_count(value: object) -> str:
    number = finite_number(value)
    return f"{int(number):,}" if number is not None and number >= 0 else "?"


def format_size(value: object) -> str:
    number = finite_number(value)
    if number is None or number < 0:
        return "unknown size"
    units = ("B", "KiB", "MiB", "GiB")
    amount = number
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return "unknown size"


def peer_label(item: dict[str, Any]) -> str:
    return fit_text(clean_text(item.get("peer_id") or "?"), 20, False)


def pack_fields(fields: Sequence[str], width: int, sep: str = " | ") -> list[str]:
    """Whole fields joined by `sep` and carried to the next line; only a field wider than a line wraps by words."""
    lines: list[str] = []
    line = ""
    for item in fields:
        if line and cell_width(line + sep + item) <= width:
            line += sep + item
            continue
        if line:
            lines.append(line)
        parts = [item] if cell_width(item) <= width else wrap_text(item, width)
        lines.extend(parts[:-1])
        line = parts[-1] if parts else ""
    return lines + ([line] if line else [])


def wrap_text(text: str, width: int) -> list[str]:
    """Wrap prose and paths without ellipsizing measurements or counter values."""
    lines: list[str] = []
    line = ""
    for word in re.findall(r"details: --expand|\S+", clean_text(text)):
        if line and cell_width(line + " " + word) > width:
            lines.append(line)
            line = ""
        while cell_width(word) > width:
            chunk = ""
            for char in word:
                if cell_width(chunk + char) > width:
                    break
                chunk += char
            if not chunk:
                chunk = "?"
                word = word[1:]
            else:
                word = word[len(chunk):]
            lines.append(chunk)
        line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines


def compact_age(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    if seconds < 60:
        return f"{max(0, int(seconds))}s"
    return format_duration(max(0, seconds))


def utc_text(timestamp: float, fmt: str) -> str | None:
    """A UTC date text, or None for an instant that cannot be represented (the renderer must never raise on stored data)."""
    try:  # epoch + timedelta (not fromtimestamp): the platform C time functions stop at year 3000 on Windows
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=timestamp)).strftime(fmt)
    except (OverflowError, OSError, ValueError):
        return None


# The rich look (P21). One accent per meaning, from the 256-colour palette in the semicolon SGR form (no truecolor
# branch): ok, warn, hot and bad for a state or a level, dim for structure, title for a section title.
RICH_SGR = {"ok": "38;5;78", "warn": "38;5;221", "hot": "38;5;208", "spend": "36", "out": "1;31", "bad": "38;5;203", "dim": "38;5;245", "title": "1"}
PILL_SGR = {"OK": "1;38;5;16;48;5;78", "OK?": "1;38;5;16;48;5;221", "ATTN": "1;38;5;231;48;5;160"}
# Every glyph is one cell wide (as `cell_width` counts it) and has an ASCII form for a stream that cannot show it; a
# terminal that draws these as two cells uses the plain view. `none` is a bar or meter without a value.
RICH_GLYPHS = {
    True: {"rule": "─", "sep": "·", "full": "█", "empty": "░", "cfull": "▬", "cempty": "╌", "none": "─", "on": "■",
           "off": "□", "ok": "●", "fast": "▲", "low": "▼", "out": "×", "blocked": "×", "aged": "○", "run": "►",
           "gone": "×", "dot": "●", "up": "▲", "down": "▼"},
    False: {"rule": "-", "sep": "|", "full": "#", "empty": ".", "cfull": "=", "cempty": "-", "none": "-", "on": "#",
            "off": ".", "ok": "*", "fast": "^", "low": "v", "out": "x", "blocked": "x", "aged": "o", "run": ">",
            "gone": "x", "dot": "*", "up": "^", "down": "v"},
}


def ascii_text(text: str, keep: str | None = None) -> str:
    """Text for the plain view (A5). The cut mark `…` becomes `~`. ASCII stays. With `keep` (the output encoding), a
    character that encoding can encode and whose East Asian width is not Ambiguous stays too, so Korean, Kana and CJK
    text remain readable; every other character becomes `?` (one column, so fitting before or after agrees)."""
    text = text.replace("…", "~")
    if keep is None:
        return text.encode("ascii", errors="replace").decode("ascii")
    kept: list[str] = []
    for char in text:
        if char < "\x80":
            kept.append(char)
            continue
        try:
            char.encode(keep)
        except (UnicodeError, LookupError):
            kept.append("?")
            continue
        # Zero-width marks (Thai tone marks, Kana voicing, virama, ZWJ) stay; only Ambiguous width becomes `?` (K2).
        kept.append("?" if unicodedata.east_asian_width(char) == "A" else char)
    return "".join(kept)


def cell_text(value: object, unicode: bool, empty: str = "-", keep: str | None = None) -> str:
    text = clean_text(value) if value is not None else ""
    text = text or empty
    return text if unicode else ascii_text(text, keep)


def short_text(text: str, cap: int, unicode: bool) -> str:
    """At most `cap` characters (not columns): the column fit comes later."""
    return text if len(text) <= cap else text[:cap - 1] + ("…" if unicode else "~")


def overflow(hidden: int, noun: str, width: int, unicode: bool) -> str:
    """The overflow line, fitted to the width each time it is made: the hint gives way before the number."""
    for text in (f"+{hidden} {noun} (--expand)", f"+{hidden} {noun}", f"+{hidden}"):
        if cell_width(text) <= width:
            return text
    return fit_text(f"+{hidden}", width, unicode)


def fit_bytes(text: str, budget: int, unicode: bool) -> str:
    if len(text.encode("utf-8")) <= budget:
        return text
    mark = "…" if unicode else "~"
    room = budget - len(mark.encode("utf-8"))
    if room < 0:
        return ""
    kept: list[str] = []
    used = 0
    for char in ANSI_ESCAPE.sub("", text):
        size = len(char.encode("utf-8"))
        if used + size > room:
            break
        kept.append(char)
        used += size
    return "".join(kept) + mark


def table_columns(rows: list[list[str]], width: int, elastic: int | None, floor: int = 12) -> tuple[int, list[int]]:
    """How many leading columns a table shows, and their sizes: whole columns drop from the right, and the elastic
    column (text) shrinks first, down to `floor` cells."""
    natural = [max(cell_width(row[i]) for row in rows) for i in range(len(rows[0]))] if rows else []
    sizes = list(natural)
    shown = len(natural)
    while shown > 1:
        fixed = sum(natural[i] for i in range(shown) if i != elastic) + 2 * (shown - 1)
        if elastic is not None and elastic < shown:
            if width - fixed >= min(floor, natural[elastic]):
                sizes[elastic] = min(natural[elastic], width - fixed)
                break
        elif fixed <= width:
            break
        shown -= 1
    return shown, sizes


def fit_table(rows: list[list[str]], width: int, unicode: bool, *, right: Sequence[int] = (), elastic: int | None = None,
           floor: int = 12, paint: Callable[[int, int, str], str] | None = None) -> list[str]:
    """One column set for all rows (see `table_columns`). `paint(row, column, cell)` colours a cell after it was fitted, so
    colour never changes a width (P21 C4)."""
    shown, sizes = table_columns(rows, width, elastic, floor)
    lines: list[str] = []
    for index, row in enumerate(rows):
        cells: list[str] = []
        for i in range(shown):
            text = fit_text(row[i], sizes[i], unicode) if i == elastic and cell_width(row[i]) > sizes[i] else row[i]
            pad = " " * max(0, sizes[i] - cell_width(text))
            if paint is not None and text:
                text = paint(index, i, text)
            cells.append(pad + text if i in right else text + pad)
        lines.append("  ".join(cells).rstrip())
    return lines


def heading_rule(head: str, width: int, unicode: bool, paint: bool) -> str:
    """The rule that replaces a section head in the rich view (P21 C2): the same content on the same line. The first
    two words are the title (`POOLS 9`); a ` | ` of the head is the view's separator. A head that would be cut to
    make room for the line keeps its content and loses the line."""
    ruled = cell_width(head) <= width - RULE_PREFIX_WIDTH
    head = fit_text(head, width, unicode)
    match = re.match(r"\S+ \S+", head)
    title = match[0] if match else head
    sep = f" {RICH_GLYPHS[unicode]['sep']} "
    rest = head[len(title):].replace(" | ", paint_text(sep, RICH_SGR["dim"]) if paint else sep)
    if ruled:
        return title_rule(title, rest, width, unicode, paint)
    return (paint_text(title, RICH_SGR["title"]) if paint else title) + rest


def column_painter(column: int, colours: Sequence[str | None]) -> Callable[[int, int, str], str]:
    """Paints one column of a table: each row's cell in that row's colour."""
    def paint(index: int, col: int, text: str) -> str:
        colour = colours[index] if col == column else None
        return paint_text(text, RICH_SGR[colour]) if colour else text
    return paint


FRAME_TRAILING_ROWS = 1  # render_grouped_frame's height includes the line after its final newline


def is_section_rule(line: str) -> bool:
    """A section head of the rich frame (a rule), colour or not."""
    return ANSI_ESCAPE.sub("", line).startswith(("── ", "-- "))


def rich_section(label: str, rows: Sequence[str], width: int, unicode: bool, paint: bool) -> list[str]:
    """One section of the rich --expand frame (P23): its head as a rule, like a head of the compact frame, then its
    rows from the second cell. A head keeps the leading fields that fit on its line; the others are the section's first
    row, so a narrow terminal loses none. A row wider than the line wraps, with its next lines further in (colour
    never decides that: a painted row that has to wrap is wrapped as text)."""
    sep = f" {RICH_GLYPHS[unicode]['sep']} "
    fields = label.split(" | ")
    shown = len(fields)
    while shown > 1 and cell_width(" | ".join(fields[:shown])) > width - 3:
        shown -= 1
    lines = [heading_rule(" | ".join(fields[:shown]), width, unicode, paint)]
    for row in [*pack_fields(fields[shown:], max(1, width - 1), sep), *rows]:
        if cell_width(row) <= width - 1:
            lines.append(" " + row)
            continue
        text = ANSI_ESCAPE.sub("", row)
        indent = len(text) - len(text.lstrip(" "))
        parts = wrap_text(text, max(1, width - 3 - indent))
        lines.extend(" " * (indent + (1 if index == 0 else 3)) + part for index, part in enumerate(parts))
    return lines
