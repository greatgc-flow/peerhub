"""Pure human presentation for the schema 2.0 grouped monitor frame."""
from __future__ import annotations

import math
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

from peerhub.cli.monitor_preferences import GROUPS

# Mirrors peerhub.extensions.observation_model.NOT_REPORTED_REASON (the human renderer imports no extension code).
_NOT_REPORTED = "not reported by provider"
_DISABLED = "disabled by provider"  # mirrors observation_model.DISABLED_REASON: the window exists but the provider switched it off
_NA_REASONS = (_NOT_REPORTED, _DISABLED)
_LEAD_MARK_PCT = 80  # mirrors monitor_read_model.LEAD_CONTEXT_MARK_PCT: the `!` of a lead's ctx cell
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ANSI = re.compile(r"\x1b\[[0-9;]*m|\x1b\]8;[^\x07\x1b]*(?:\x1b\\|\x07)")  # SGR, and OSC 8 (a hyperlink's open and close)
_BIDI = {"\u061c", "\u200e", "\u200f", *[chr(code) for code in range(0x202A, 0x202F)],
         *[chr(code) for code in range(0x2066, 0x206A)]}
MONITOR_LEGEND = (
    "ok enough | low >0-10% | fast >10% + threat (2+ samples) | out 0% | aged stale | reset passed | n/a plan no longer reports it (diag quota may keep an older value) | ? unknown",
    "PACE=used%-elapsed% pp, + ahead of even burn, - behind; '-' cell = unavailable",
    "BAR=remaining, | marks the even-burn level (^ ahead, v behind)",
    "TIME: the header time is the snapshot time (when the store was read), not the time quota was measured",
    "LEADS: label | state (?busy = alive not confirmed, - = no session) | age of the last activity"
    " | ctx = context of the model's maximum input (! 80% or more; 106k = window unknown; ? unknown) | running asks | model | title",
    "CTX est: estimated context of a cc session at the end of its last ask (percent of the model's maximum input, with"
    " its age); not a live measurement",
)
# The legend line of the FORECAST column in the QUOTA details of the rich --expand frame (P23).
FORECAST_LEGEND = "FORECAST: insuff=insufficient projection data; threat=exhausts before reset"


def _rich_legend(sep: str) -> list[str]:
    """MONITOR_LEGEND for the rich --expand frame: the view's separator, and no even-burn tick sentence (that frame
    draws the POOLS bar, which has no tick; the 8-cell bar with the tick is the plain view's)."""
    return [("BAR=remaining" if text.startswith("BAR=") else text).replace(" | ", sep) for text in MONITOR_LEGEND]


def _obj(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, Mapping) else {}


def _items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    items = cast(Sequence[Any], value)
    return [cast(dict[str, Any], item) for item in items if isinstance(item, Mapping)]


def _clean(value: object) -> str:
    text = str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    text = _CONTROL.sub("�", text)
    return "".join("�" if char in _BIDI or unicodedata.category(char) in {"Cf", "Cn", "Cs", "Zl", "Zp"}
                   and char != "\u200d" else char
                   for char in text).strip()


def _width(text: str) -> int:
    return sum(0 if unicodedata.combining(char) or char == "\u200d"
               or "\ufe00" <= char <= "\ufe0f" or "\U000e0100" <= char <= "\U000e01ef"
               else 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
               for char in _ANSI.sub("", text))


def _fit(text: str, width: int, unicode: bool, painted: bool = False, *, indent: bool = False) -> str:
    if width <= 0:
        return ""
    if painted and _ANSI.search(text):  # only for rows whose cells were all _clean()ed before our own SGR colour was added
        if _width(text) <= width:
            return text
        text = _ANSI.sub("", text)
    leading = len(text) - len(text.lstrip(" ")) if indent else 0
    text = " " * leading + _clean(text)
    if _width(text) <= width:
        return text
    mark = "…" if unicode else "~"
    if width == 1:
        return mark
    result = ""
    for char in text:
        if _width(result + char + mark) > width:
            break
        result += char
    return result + mark


def _pool_order(peer: str, pool: str) -> tuple[str, str, int, str]:
    """The one pool ordering every monitor view uses: peer, family, window length, name."""
    return (peer, _pool_family(pool), _parse_window(pool)[0], pool)


def _quota_layout(rows: list[dict[str, Any]], unicode: bool, *, show_quota_notes: bool = True,
                  order: dict[tuple[str, str], int] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Derive a forest from configured consumers, leaving every pool's values intact. `order` (#394) is the position of
    each (peer, pool) in POOLS: the peers follow it (the peer of the most urgent pool first), so POOLS and QUOTA name the
    peers in one order. Within a peer the trees keep their family and window order."""
    result: list[dict[str, Any]] = []
    notes: list[str] = []
    hidden: dict[str, list[str]] = {}  # the n/a pools that have no parent row to carry their name
    noted: set[str] = set()  # peers whose retired pools already have their note
    order = order or {}

    def rank(peer: str, name: str) -> int:
        return order.get((peer, name), len(order))

    for peer in sorted({str(row["peer"]) for row in rows},
                       key=lambda p: (min(rank(p, str(row["pool"])) for row in rows if row["peer"] == p), p)):
        peer_rows = [dict(row) for row in rows if row["peer"] == peer]
        retired = [row for row in peer_rows if row.get("retired")]
        if retired:  # #400: named after the peer's first row; --show-quota-notes adds the plan reason as a note
            hidden.setdefault(peer, []).extend(str(row["pool"]) for row in retired)
            if show_quota_notes:
                plans = ",".join(sorted({str(row.get("display_plan") or "unknown") for row in retired}))
                notes.append(f"{peer} {','.join(str(row['pool']) for row in retired)} n/a (not reported by plan {plans})")
                noted.add(peer)
        active = [row for row in peer_rows if not row.get("retired")]
        first_peer_row = True

        def append_row(row: dict[str, Any], display_pool: str) -> None:
            nonlocal first_peer_row
            row.update(display_pool=display_pool, display_peer=peer if first_peer_row else "", suffix="")
            first_peer_row = False
            result.append(row)

        # Names are tree identities only when unique. Preserve every source row
        # in the flat fallback, including duplicate names with different values.
        if len({row["pool"] for row in active}) != len(active):
            for row in active:
                if row["state"].lower() == "n/a":
                    hidden.setdefault(peer, []).append(str(row["pool"]))
                else:
                    append_row(row, str(row["pool"]))
            continue
        by_name = {str(row["pool"]): row for row in active}
        windows = {name: _parse_window(name)[0] for name in by_name}
        members = {name: set(str(row["profiles"]).split(",")) - {"unmapped", ""} for name, row in by_name.items()}
        parents: dict[str, str | None] = dict.fromkeys(by_name)
        flat = any(a & b and not (a <= b or b <= a) for a in members.values() for b in members.values())
        for child in by_name:
            if flat or not members[child]:
                continue
            window = windows[child]
            eligible = [name for name in by_name if name != child and members[child] <= members[name]
                        and window <= windows[name] < 999999999
                        and (members[child] < members[name] or window < windows[name])]
            minimal = [name for name in eligible if not any(members[other] < members[name] for other in eligible)]
            if minimal:
                shortest = min(windows[name] for name in minimal)
                candidates = [name for name in minimal if windows[name] == shortest]
                if len(candidates) != 1:
                    flat = True
                    break
                parents[child] = candidates[0]
        if flat:
            parents = dict.fromkeys(by_name)
        for name, row in by_name.items():  # #400: an n/a pool is never a row; its children go to its own parent
            if row["state"].lower() == "n/a":
                parents.update({child: parents[name] for child, parent in parents.items() if parent == name})
        children: dict[str, list[str]] = {name: [] for name in by_name}
        for child, parent in parents.items():
            if parent is not None:
                children[parent].append(child)
        for names in children.values():
            names.sort(key=lambda name: (-windows[name], name))

        def visit(name: str, prefix: str, continuation: str) -> None:
            row = by_name[name]
            append_row(row, prefix + name)
            folded = [child for child in children[name] if by_name[child]["state"].lower() == "n/a"]
            row["folded"] = folded
            visible = [child for child in children[name] if child not in folded]
            for i, child in enumerate(visible):
                last = i == len(visible) - 1
                branch = ("└─ " if last else "├─ ") if unicode else ("`- " if last else "|- ")
                stem = "   " if last else "│  " if unicode else "|  "
                visit(child, continuation + branch, continuation + stem)

        for name in sorted((name for name in by_name if parents[name] is None),
                           key=lambda name: (_pool_family(name), windows[name], name)):
            if by_name[name]["state"].lower() == "n/a":
                hidden.setdefault(peer, []).append(name)
            else:
                visit(name, "", "")
    for row in result:  # an n/a pool with no parent row is named after its peer's first row
        names = [*row.pop("folded", []), *(hidden.pop(row["peer"], []) if row["display_peer"] else [])]
        if names:
            row["suffix"] = "(" + ",".join(names) + " n/a)"
    notes.extend(f"{peer} {','.join(names)} n/a" for peer, names in hidden.items() if peer not in noted)  # no row left
    return result, notes


def _quota_suffix(line: str, suffix: str, width: int, unicode: bool, code: str | None) -> list[str]:
    """Keep folded names even when the trailing columns were dropped. `code` paints them (None: no colour)."""
    if not suffix:
        return [line]
    if _width(line + " " + suffix) <= width:
        return [line + " " + _paint(suffix, code)]
    return [line, *[_paint(part, code) for part in _compact_wrap(suffix, width)]]


def _fit_fields(text: str, width: int, unicode: bool, painted: bool = False, *, inline_note: bool = True,
                indent: bool = False) -> str:
    """Drop trailing fields whole; mark a cut if the omission note cannot fit."""
    if _width(text) <= width:
        return _fit(text, width, unicode, painted=painted, indent=indent)
    fields = re.split(r" (?:\||·) ", _ANSI.sub("", text))
    if len(fields) == 1:
        return _fit(text, width, unicode, painted=painted, indent=indent)
    sep = " · " if " · " in text else " | "
    note = sep + "fields hidden" if inline_note else ""
    next_field = fields[-1]
    while len(fields) > 1 and _width(sep.join(fields) + note) > width:
        next_field = fields.pop()
    fitted = sep.join(fields)
    if _width(fitted) > width:
        return _fit(fields[0], width, unicode)
    if _width(fitted + note) <= width:
        return _clean(fitted + note)
    return _fit(fitted + sep + next_field, width, unicode)


def _fit_live_title(refresh_state: str, next_refresh: float | None, stamp: str,
                    details: Sequence[str], width: int, unicode: bool, *, inline_note: bool = True) -> tuple[str, bool]:
    """Keep live progress together, then timestamp, before provenance and age."""
    progress = [refresh_state] if refresh_state else []
    if next_refresh is not None:
        progress.append(f"refresh in {max(0, math.ceil(next_refresh))}s")
    essential = "PeerHub Monitor " + "; ".join(progress)
    if _width(essential) > width:
        essential = "Monitor " + "; ".join(progress)
        if _width(essential) > width and next_refresh is not None:
            essential = progress[-1]
            if _width(essential) > width:
                return _fit("refresh pending", width, unicode), True
    title = " | ".join(field for field in [essential, stamp, *details] if field)
    dropped = _width(title) > width
    if dropped and inline_note and _width(essential + " | fields hidden") > width:
        # The caller discloses the cut separately when the live fields fill the line.
        return _fit(essential, width, unicode), True
    return _fit_fields(title, width, unicode, inline_note=inline_note), dropped


def _hidden_lines(rows: int, groups: int, width: int, unicode: bool, *, expand: bool,
                  quota: bool = False) -> list[str]:
    sep = " · " if unicode else " | "
    detail = f"+{rows} rows hidden"
    group_note = f" ({groups} {'group' if groups == 1 else 'groups'})" if groups else ""
    if expand:
        command = "peerhub monitor --snapshot --group all --expand | more"
        hint = "all rows: " + command
        if _width(detail + sep + hint) <= width:
            if _width(detail + group_note + sep + hint) <= width:
                detail += group_note
            return [detail + sep + hint]
        if _width(command) <= width:
            return [_fit(detail + group_note + "; all rows:", width, unicode), command]
        return [_fit(f"+{rows} --expand | more", width, unicode)]
    hint = ("peerhub monitor --snapshot --group capacity --expand" if quota else
            "peerhub monitor --snapshot --group all --expand")
    if _width(detail + group_note + sep + hint) <= width:
        detail += group_note
    elif _width(detail + sep + hint) > width:
        detail += group_note
        hint = "--expand"
    return [f"+{rows} --expand" if width < 32 else _fit_fields(detail + sep + hint, width, unicode)]


def _visible_fields(rows: Sequence[Sequence[str]], sep: str, width: int, keep: int) -> int:
    """How many leading fields every row shows whole: drop trailing fields until all rows fit (never below `keep`)."""
    count = max((len(row) for row in rows), default=0)
    while count > keep and any(_width(sep.join(field for field in row[:count] if field)) > width for row in rows):
        count -= 1
    return count


def _omitted_rows(names: Sequence[str], width: int) -> list[str]:
    """The `Columns omitted:` disclosure, wrapped so narrow displays still name every dropped column."""
    rows: list[str] = []
    note = "Columns omitted:" if width > len("Columns omitted:") else "Omitted:"
    if not names:
        return rows
    for name in names:
        if _width(f"{note} {name}") > width:
            rows.append(note)
            note = name
        else:
            note += f" {name}"
    # A single name wider than a very narrow display is cut with a visible mark, never overflowing.
    return [row if _width(row) <= width else row[:max(1, width - 1)] + "~" for row in (*rows, note)]


def _bar(used: float, cells: int, unicode: bool) -> str:
    used = min(1.0, max(0.0, used))
    filled = int(used * cells + 0.5)
    full, empty = ("█", "░") if unicode else ("#", ".")
    return full * filled + empty * (cells - filled)


def _bar_quota(remaining: float | None, cells: int = 8, unicode: bool = True, even_burn: float | None = None) -> str:
    """Remaining-quota bar; `even_burn` is the remaining fraction an even burn would leave, drawn as `|` unless it sits on the filled edge."""
    if remaining is None or not (0.0 <= remaining <= 1.0):
        return f"[{'-' * cells}]"
    filled = int(remaining * cells + 0.5)
    full = "█" if unicode else "#"
    empty = "░" if unicode else "."
    body = [full] * filled + [empty] * (cells - filled)
    edge = filled - 1
    if remaining > 0 and filled == 0 and cells > 0:
        body[0] = "▏" if unicode else ":"
        edge = 0
    if even_burn is not None and cells > 0:
        tick = min(cells - 1, max(0, int(even_burn * cells)))
        if tick != edge:
            body[tick] = "|"
    return f"[{''.join(body)}]"


def _even_burn(pool: str, reset_in: float | None) -> float | None:
    """Fraction of the pool's window left at reset_in: the remaining quota an even burn would show."""
    window = _parse_window(pool)[0]
    if reset_in is None or window <= 0 or window >= 999999999:
        return None
    return min(1.0, max(0.0, reset_in / window))


def _pace_glyph(text: str, unicode: bool) -> str:
    """Direction glyph before a signed non-zero pace number (the number itself is unchanged)."""
    match = re.match(r"[+-]\d+(?:\.\d+)?", text)
    if not match or float(match[0]) == 0:
        return text
    glyphs = RICH_GLYPHS[unicode]
    return (glyphs["up"] if match[0][0] == "+" else glyphs["down"]) + text


def _quota_color(state: str, remaining: float | None = None, *, aged: bool = False) -> str | None:
    """Evidence validity wins over the traffic-light precedence; retain the state word."""
    state = state.split(":")[0].upper()
    if state == "ERROR":
        return "1;31"
    if aged or state not in {"OUT", "EXHAUSTED", "THREAT", "LOW", "FAST", "WATCH", "INSUFF", "INSUFFICIENT",
                             "OK", "ON_PACE", "SAFE", "MEASURED"}:
        return "2"
    if state in {"OUT", "EXHAUSTED"} or remaining == 0:
        return "1;31"
    if state == "THREAT":
        return "31"
    if state in {"LOW", "FAST", "WATCH"} or remaining is not None and 0 < remaining <= .10:
        return "33"
    if state in {"INSUFF", "INSUFFICIENT"}:
        return None
    return "32" if state in {"OK", "ON_PACE", "SAFE", "MEASURED"} else "2"


def _quota_cell(text: str, column: str, code: str | None) -> str:
    if column == "STATE":
        return _paint(text, code)
    if column == "PACE" and text[:1] in {"▲", "▼", "^", "v"}:
        return _paint(text[0], code) + text[1:]
    return text


def _paint(text: str, code: str | None) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if code else text


def _pct(rem: float) -> str:
    """Whole percent; a tiny non-zero remainder is never shown as 0% (0% means exhausted)."""
    return "<1%" if 0.0 < rem <= 0.005 else f"{rem * 100:.0f}%"


def _alert_exhaustion_at(item: dict[str, Any]) -> float | None:
    projection = _obj(item.get("projection"))
    if _obj(item.get("measurements")).get("remaining_fraction") != 0 and projection.get("state") != "threat":
        return None
    return _number(projection.get("exhaustion_at"))


def _quota_label(status: str, group: dict[str, Any], read_at: float | None,
                 elapsed_seconds: float) -> str:
    label = f"QUOTA [{status}] | measured remaining"
    candidates: list[tuple[float, str]] = []
    for item in _items(_obj(group.get("data")).get("observations")):
        exhaustion = _alert_exhaustion_at(item)
        rem = _number(_obj(item.get("measurements")).get("remaining_fraction"))
        if item.get("state") != "MEASURED" or exhaustion is None or rem is None or not 0 <= rem <= 1:
            continue
        pool = _clean(item.get("pool_ref") or item.get("pool_kind") or "?").split(":")[-1]
        remaining_s = exhaustion - (read_at or 0.0) - elapsed_seconds
        when = "exhausted" if rem == 0 else "past forecast" if remaining_s <= 0 else f"in {_format_duration(remaining_s)}"
        candidates.append((exhaustion, f"{_peer(item)} {pool} {_pct(rem)} {when}"))
    if candidates:
        label += f" | soonest exhaust: {min(candidates)[1]}"
    return label


def _parse_window(pool_ref: str) -> tuple[int, str]:
    token = pool_ref.split(":")[-1] if ":" in pool_ref else pool_ref
    part = token.split("-")[-1] if "-" in token else token
    match = re.match(r"^(\d+)([a-zA-Z]+)$", part)
    if match:
        num, unit = int(match.group(1)), match.group(2).upper()
        mult = 3600 if unit == "H" else 86400 if unit == "D" else 60 if unit == "M" else 1
        return (num * mult, f"{num}{unit}")
    return (999999999, token)


def _format_duration(seconds: float) -> str:
    if seconds >= 86400:
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        return f"{days}d{hours:02d}h"
    elif seconds >= 3600:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h{mins:02d}m"
    else:
        mins = int(seconds // 60)
        return f"{mins}m"


def _rule(title: str, rest: str, width: int, unicode: bool, paint: bool) -> str:
    """A section head drawn as a rule (P21): `── POOLS 9 ── STATE ──────── LEFT  RESET ───`. The head's content
    stays: the title, then `rest` as it is, with the line drawn through the gap after the title (three cells or
    more), through a gap of five cells or more and to the end of the line. With `paint` the line is dim and the
    title bold."""
    dash = RICH_GLYPHS[unicode]["rule"]

    def line(count: int) -> str:
        return _paint(dash * count, RICH_SGR["dim"]) if paint else dash * count

    room = width - 3 - _width(title + rest)
    rest = re.sub("^ {3,}| {5,}", lambda gap: f" {line(len(gap[0]) - 2)} ", rest)
    tail = f" {line(room - 1)}" if room >= 2 else ""
    return f"{line(2)} {_paint(title, RICH_SGR['title']) if paint else title}{rest}{tail}"


def _section_rule(label: str, width: int, unicode: bool) -> str:
    label = _fit_fields(label, max(0, width - 4), unicode)
    return _fit(_rule(label, "", width, unicode, False), width, unicode)


def _sample_note(gid: str, data: dict[str, Any]) -> str:
    """`sample N/M` for a section that read a capped sample (M = the total, when the read model counted it)."""
    if gid not in {"execution", "sessions", "usage", "context"} or not data.get("truncated"):
        return ""
    read = _number(data.get("sample_size"))
    if read is None:
        read = next((len(cast(list[object], value)) for value in data.values() if isinstance(value, list)), None)
    total = _number(data.get("sample_total"))
    return f"sample {_count(read)}" + (f"/{_count(total)}" if total is not None and total >= (read or 0) else "")


def _group_label(gid: str, group: dict[str, Any], status: str, read_at: float | None, elapsed: float) -> str:
    if gid == "capacity":
        return _quota_label(status, group, read_at, elapsed)
    if gid == "alerts":
        return f"ALERTS {len(_items(_obj(group.get('data')).get('alerts')))}"
    label = f"{gid.upper()} [{status}]"
    note = _sample_note(gid, _obj(group.get("data")))
    label += f" | {note}" if note else ""
    return label if gid in {str(item["id"]) for item in GROUPS} else label + " [unrecognized group]"


def _number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _count(value: object) -> str:
    number = _number(value)
    return f"{int(number):,}" if number is not None and number >= 0 else "?"


def _size(value: object) -> str:
    number = _number(value)
    if number is None or number < 0:
        return "unknown size"
    units = ("B", "KiB", "MiB", "GiB")
    amount = number
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return "unknown size"


def _coverage(data: dict[str, Any], unicode: bool = True) -> str:
    coverage = _obj(data.get("coverage"))
    if coverage.get("truncated") or data.get("truncated"):
        return f"partial {'·' if unicode else '|'} sample truncated"
    return ""


def _peer(item: dict[str, Any]) -> str:
    return _fit(_clean(item.get("peer_id") or "?"), 20, False)


def _usage_values(telemetry: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    normalized, reported = _obj(telemetry.get("normalized")), _obj(telemetry.get("reported"))
    tokens = _number(normalized.get("provider_total_tokens", reported.get("total_tokens")))
    inputs = _number(normalized.get("provider_input_tokens", reported.get("input_tokens")))
    outputs = _number(normalized.get("output_tokens", reported.get("output_tokens")))
    cached = _number(normalized.get("cached_input_tokens", reported.get("cached_input_tokens")))
    if tokens is None and inputs is not None and outputs is not None and inputs >= 0 and outputs >= 0:
        tokens = inputs + outputs
    if inputs is None or inputs <= 0 or cached is None or not 0 <= cached <= inputs:
        inputs = cached = None
    return tokens if tokens is not None and tokens >= 0 else None, inputs, cached


def _estimate(context: dict[str, Any], now: float | None) -> str | None:
    """An estimated context (#376 P2) as text: `2% est, 3m ago`. The read model's ESTIMATED state is the cc adapter's
    reading at the end of an ask, so it always carries the word and its age (`age ?` without a time) and is never shown
    as measured. None for every other state or an invalid percent."""
    percent = _number(context.get("percent"))
    if context.get("state") != "ESTIMATED" or percent is None or not 0 <= percent <= 100:
        return None
    at = _number(context.get("observed_at"))
    return f"{percent:.0f}% est, " + ("age ?" if at is None or now is None else f"{_compact_age(max(0.0, now - at))} ago")


def _group_lines(group_id: str, group: dict[str, Any], *, width: int, unicode: bool,
                 elapsed_seconds: float = 0.0, read_at: float | None = None,
                 expand: bool = False, color: bool = False, bounded: bool = False,
                 keep: str | None = None, rich: bool = False) -> tuple[list[str], int]:
    """Return rendered data rows and the length of the QUOTA table prefix.

    `rich` (the rich --expand frame, P23): a row joins its fields with the view's separator, never ` | `; the QUOTA
    table has the compact STATE cell and a FORECAST column instead of the 8-cell bar, with its own legend line; and
    `color` paints with the palette of the compact frame. Without `rich` the rows are the plain view's, unpainted."""
    data = _obj(group.get("data"))
    if data == {} and group.get("data") is None:
        return ["data unavailable"], 0
    rows: list[str] = []
    table_size = 0
    sep = " · " if unicode else " | "
    dot = "·" if unicode else "|"
    bar = sep if rich else " | "  # joins the fields of a row that the plain view joins with a pipe in both glyph sets
    now = None if read_at is None else read_at + elapsed_seconds  # the time of this frame, for the age of an estimate

    if group_id == "overview":
        peers = _items(data.get("peers"))
        if peers:
            names = [f"{_peer(item)} ({_clean(item.get('display_name') or item.get('adapter_ref') or 'peer')})"
                     for item in peers]
            rows.append(f"Peers: {', '.join(names)}")
        else:
            rows.append(f"Peers: {_count(data.get('sample_size'))} registered")
        if data.get("truncated") or data.get("session_sample_truncated") or data.get("peer_sample_truncated"):
            rows.append(f"Coverage: partial {dot} sample truncated")

    elif group_id == "peers":
        peer_items = _items(data.get("peers"))
        narrow_peers: list[list[str]] = []
        if not peer_items:
            rows.append("No peer evidence")
        for item in peer_items:
            pid = _clean(item.get("peer_id") or "?")
            q_obj = _obj(item.get("quota")) if isinstance(item.get("quota"), Mapping) else None
            p_obj = _obj(item.get("profile")) if isinstance(item.get("profile"), Mapping) else None
            s_obj = _obj(item.get("session")) if isinstance(item.get("session"), Mapping) else None
            c_obj = _obj(item.get("context")) if isinstance(item.get("context"), Mapping) else None

            if q_obj is None:
                q_wide = "unknown"
                q_narr = "unk"
            else:
                q_state = _clean(q_obj.get("state") or "").upper()
                q_pool = _clean(q_obj.get("pool_ref") or "")
                if ":" in q_pool:
                    q_pool = q_pool.split(":")[-1]
                q_rem = _number(q_obj.get("remaining_fraction"))
                if q_rem is None:
                    q_pct = _number(q_obj.get("percent"))
                    q_rem = q_pct / 100 if q_pct is not None else None
                q_age = _number(q_obj.get("age_seconds"))

                q_scope = _clean(q_obj.get("scope") or "")

                if q_pool == "not_reported":  # every configured pool omitted by the provider: unknown, never 100% free
                    q_wide = "? (not reported)" if data.get("_show_quota_notes") else "unknown"
                    q_narr = "? not reported" if data.get("_show_quota_notes") else "unk"
                elif q_state == "INCOMPLETE" or q_pool == "incomplete":
                    q_wide = "incomplete"
                    q_narr = "incomplete"
                elif q_state == "STALE":
                    q_wide = "STALE"
                    q_narr = "STALE"
                elif q_state != "MEASURED" or q_rem is None or not 0 <= q_rem <= 1:
                    q_wide = "unknown"
                    q_narr = "unk"
                else:
                    age_val = max(0.0, (q_age or 0.0) + elapsed_seconds)
                    age_str = f" ({int(age_val // 60)}m ago)" if q_age is not None and age_val >= 60 else ""
                    if q_scope in ("unmapped", "account-wide"):
                        pool_label = f"{q_pool} unmapped" if q_pool else "unmapped"
                    else:
                        pool_label = q_pool
                    pool_str = f" ({pool_label})" if pool_label else ""
                    plan_note = _clean(_obj(data.get("plan_notes")).get(pid) or "") if data.get("_show_quota_notes") else ""
                    q_wide = f"{_pct(q_rem)}{pool_str}{age_str}" + (f" [{plan_note}]" if plan_note else "")
                    q_narr = f"{_pct(q_rem)}{pool_str}"

            if p_obj is None:
                p_wide = "unknown [unknown]"
                p_narr = "unk [unk]"
            else:
                p_name = _clean(p_obj.get("name") or "")
                if not p_name:
                    prof = _clean(p_obj.get("profile") or "unknown")
                    mod = _clean(p_obj.get("model") or "")
                    p_name = f"{prof}/{mod}" if mod else prof
                layer = _clean(p_obj.get("source") or p_obj.get("config_layer") or "unknown")
                layer_map = {"workspace": "ws", "global": "glob", "packaged": "pkg"}
                p_wide = f"{p_name} [{layer}]"
                p_narr = f"{p_name} [{layer_map.get(layer, layer[:4])}]"

            if s_obj is None and item.get("session") is None:
                if data.get("session_sample_truncated") or data.get("truncated"):
                    s_wide = "sample truncated"
                    s_narr = "sample truncated"
                else:
                    s_wide = "none"
                    s_narr = "none"
            elif s_obj is None:
                s_wide = "unknown"
                s_narr = "unk"
            else:
                mode = _clean(s_obj.get("execution_mode") or s_obj.get("mode") or "").lower()
                gen = s_obj.get("generation")
                if mode in ("reuse", "reused"):
                    mode_label = "reused"
                elif mode == "fresh":
                    mode_label = "fresh"
                else:
                    mode_label = "unknown"
                if mode_label == "unknown" and gen is None:
                    s_wide = "unknown"
                    s_narr = "unk"
                else:
                    gen_part = f"gen {_count(gen)}" if gen is not None else "gen unknown"
                    s_wide = f"{mode_label} {gen_part}"
                    s_narr = s_wide

            if c_obj is None:
                c_wide = "unknown"
                c_narr = "unk"
            else:
                c_state = _clean(c_obj.get("state") or "").upper()
                c_pct = _number(c_obj.get("percent"))
                c_tok = c_obj.get("tokens") or c_obj.get("occupancy_tokens")
                if isinstance(c_tok, (int, float)) and c_tok >= 1000:
                    tok_str = f"{int(c_tok) // 1000}k"
                elif c_tok is not None:
                    tok_str = _clean(c_tok)
                else:
                    tok_str = ""

                if c_state == "MEASURED" and c_pct is not None:
                    pct_str = f"({c_pct:.0f}%)" if tok_str else f"{c_pct:.0f}%"
                    c_wide = f"{tok_str} {pct_str}".strip()
                    c_narr = f"{tok_str} {pct_str}".strip()
                else:  # an estimate (#376 P2) says so and gives its age; anything else is unknown
                    estimate = _estimate(c_obj, now)
                    c_wide, c_narr = estimate or "unknown", estimate or "unk"

            if width < 48:
                narrow_peers.append([f"{pid}: quota {q_narr}", p_narr, s_narr, f"ctx: {c_narr}" if c_narr != "unk" else ""])
            else:
                rows.append(f"{pid:<4}quota: {q_wide} -> {p_wide}")
                prefix = f"{pid} " if bounded else "    "
                rows.append(f"{prefix}-> latest session: {s_wide}{' -> context: ' + c_wide if c_wide != 'unknown' else ''}")

        if narrow_peers:  # one field set for all peers: drop trailing fields whole, never cut one with `~`
            shown = _visible_fields(narrow_peers, bar, width - 2, keep=1)
            rows.extend(bar.join(field for field in fields[:shown] if field) for fields in narrow_peers)
            if shown < 4:
                rows.extend(_omitted_rows([name for name, i in (("profile", 1), ("session", 2), ("ctx", 3))
                                           if i >= shown and any(fields[i] for fields in narrow_peers)], width - 2))

        if bounded:
            rows = [row for row in rows if "-> latest session:" not in row] + [
                row for row in rows if "-> latest session:" in row]
        if data.get("peer_sample_truncated"):  # #394: a capped list says so
            total = _number(data.get("peers_total"))
            rows.append(f"showing {len(peer_items)} of {_count(total)} peers" if total is not None
                        else f"showing {len(peer_items)} peers; more are not shown")
        defaults_rev: object = data.get("defaults_revision")
        observed_at: object = data.get("observed_at")

        # One label per date (#394): the packaged defaults revision and its date here; "version check" is the facts report's.
        rev_str = f"profile defaults rev {_clean(defaults_rev)}" if defaults_rev else "profile defaults rev unknown"
        if observed_at:
            obs_clean = _clean(observed_at)
            age_str = "age unknown"
            if read_at is not None:
                try:
                    read_date = datetime.fromtimestamp(read_at, timezone.utc).date()
                    obs_date = date.fromisoformat(obs_clean)
                    days = (read_date - obs_date).days
                    age_str = "future" if days < 0 else f"{days}d ago"
                except (ValueError, OSError, OverflowError):
                    pass
            obs_str = f"({obs_clean}, {age_str})"
        else:
            obs_str = "(date unknown)"

        footer = (
            f"Model facts: {rev_str} {obs_str}{sep}"
            f"sources: codex debug models{bar}agy models{bar}claude: known ids"
        )
        # The rich --expand frame wraps a long line (nothing is cut); the plain view fits it to one line.
        rows.extend(_compact_wrap(footer, max(1, width)) if rich else [_fit(footer, max(0, width - 1), unicode)])

        facts_rep: object = data.get("facts_report")
        if isinstance(facts_rep, dict):
            rep_dict = cast(dict[str, Any], facts_rep)
            status = _clean(rep_dict.get("status") or "")
            reasons_raw = rep_dict.get("reasons")
            reasons = [str(r) for r in cast(list[object], reasons_raw)] if isinstance(reasons_raw, list) else []
            if status == "needs_recheck":
                raw_unk: object = rep_dict.get("unknown_pools")
                unk_pools: list[str] = [str(p) for p in cast(list[object], raw_unk)] if isinstance(raw_unk, list) else []
                display_reasons: list[str] = []
                for r in reasons:
                    if r == "ag: unknown_pool" and unk_pools:
                        display_reasons.append(f"ag: unknown_pool: {', '.join(unk_pools)}")
                    else:
                        display_reasons.append(r)
                reason_str = f" ({', '.join(display_reasons)})" if display_reasons else ""
                chk_date = _clean(str(rep_dict.get("checked_at") or "")[:10])
                facts_line = (f"Facts: needs_recheck{reason_str} - refresh-model-profiles"
                              + (f" (version check {chk_date})" if chk_date else ""))
            elif status == "ok":
                chk_date = str(rep_dict.get("checked_at") or "")
                chk_date_str = f" (version check {chk_date[:10]})" if chk_date else ""
                facts_line = f"Facts: ok{chk_date_str}"
            elif status == "unverified":
                facts_line = "Facts: unverified"
            else:
                facts_line = f"Facts: {status}" if status else "Facts: unknown"
            rows.extend(_compact_wrap(facts_line, max(1, width)) if rich
                        else [_fit(facts_line, max(0, width - 1), unicode)])

    elif group_id == "execution":
        scopes = _items(data.get("scopes"))
        totals = {key: 0 for key in ("not_started", "may_have_started", "started", "terminal")}
        latency_ns = latency_count = 0
        for scope in scopes:
            for key in totals:
                totals[key] += int(_number(_obj(scope.get("certainty")).get(key)) or 0)
            latency = _obj(scope.get("latency_30d"))
            latency_ns += int(_number(latency.get("sum_ns")) or 0)
            latency_count += int(_number(latency.get("count")) or 0)
        if scopes:
            sampled = bool(data.get("truncated"))  # #394: the counts of a sample are not the store's totals
            rows.append("Delivery certainty" + (" (sample only)" if sampled else "") + ": " + ", ".join(
                f"{key.replace('_', ' ')} {_count(value)}" for key, value in totals.items()))
            if sampled and data.get("_running_asks") is not None:  # the leases, which the sample does not decide
                rows.append(f"Running asks now: {_count(data['_running_asks'])} (leases, not sampled)")
            avg = latency_ns / latency_count / 1_000_000_000 if latency_count else None
            rows.append(f"Measured delivery latency: {avg:.2f}s avg{sep}{latency_count} samples" if avg is not None
                        else "Measured delivery latency: unknown")
        else:
            rows.append("No delivery evidence")
        note = _coverage(data, unicode)
        if note:
            rows.append(f"Coverage: {note}")

    elif group_id == "sessions":
        sessions = _items(data.get("items"))
        if sessions:
            limit = len(sessions) if expand else min(2, len(sessions))
            field_rows: list[list[str]] = []
            for session in sessions[:limit]:
                binding = _obj(session.get("configured_binding"))
                profile = _clean(binding.get("profile") or "profile unknown")
                model = _clean(binding.get("model") or "model unknown")
                effort = _clean(binding.get("effort") or "effort unknown")
                resume = "resumable" if session.get("resumable") else "not resumable"
                handle = _clean(session.get("handle") or "") if expand else ""
                uses = f"uses {_count(session.get('uses'))}" if expand else ""
                estimate = _estimate(_obj(_obj(session.get("telemetry")).get("context")), now) if expand else None
                fields = [f"{_peer(session)} {_clean(session.get('state') or 'UNKNOWN')}", handle,
                          f"gen {_count(session.get('generation'))}", uses, f"{profile} / {model} ({effort})", resume,
                          f"ctx {estimate}" if estimate else ""]
                if bounded and expand:
                    tokens, inputs, cached = _usage_values(_obj(_obj(session.get("telemetry")).get("usage")))
                    usage_text = f"tok {_count(tokens)}"
                    if inputs is not None and cached is not None:
                        usage_text += f"{sep}cache {cached / inputs * 100:.0f}%"
                    fields.insert(2, usage_text)
                field_rows.append(fields)
            # One field set for the whole table: drop trailing fields whole (peer+state and handle always stay).
            shown = _visible_fields(field_rows, sep, width - 2, keep=2)
            rows.extend(sep.join(field for field in fields[:shown] if field) for fields in field_rows)
            names = ["peer", "handle", "gen", "uses", "profile", "resumable", "ctx"]
            if bounded and expand:
                names.insert(2, "usage")
            if shown < len(names):
                rows.extend(_omitted_rows([name for i, name in enumerate(names) if i >= shown
                                           and any(fields[i] for fields in field_rows)], width - 2))
            if not expand and len(sessions) > 2:
                rows.append(f"+{len(sessions) - 2} sessions not shown")
        else:
            rows.append(f"Retained sessions: {_count(data.get('sample_size', 0))}")
        note = _coverage(data, unicode)
        if note:
            rows.append(f"Coverage: {note}")

    elif group_id == "usage":
        sessions = _items(data.get("sessions"))
        if sessions and bounded:
            for peer in sorted({_peer(item) for item in sessions}):
                values = [_usage_values(_obj(item.get("telemetry"))) for item in sessions if _peer(item) == peer]
                measured = [tokens for tokens, _, _ in values if tokens is not None]
                inputs = sum(inputs or 0 for _, inputs, _ in values)
                cached = sum(cached or 0 for _, _, cached in values)
                cache_part = f"{sep}cache {cached / inputs * 100:.0f}%" if inputs > 0 else ""
                rows.append(f"{peer} provider-reported tokens: {_count(sum(measured) if measured else None)}"
                            f"{sep}sessions {len(measured)} with/{len(values) - len(measured)} without usage{cache_part}")
        elif sessions:
            limit = len(sessions) if expand else min(2, len(sessions))
            for session in sessions[:limit]:
                telemetry = _obj(session.get("telemetry"))
                normalized = _obj(telemetry.get("normalized"))
                reported = _obj(telemetry.get("reported"))
                tokens = normalized.get("provider_total_tokens", reported.get("total_tokens"))
                in_tok = _number(normalized.get("provider_input_tokens", reported.get("input_tokens")))
                out_tok = _number(normalized.get("output_tokens", reported.get("output_tokens")))
                cached_tok = _number(normalized.get("cached_input_tokens", reported.get("cached_input_tokens")))
                if tokens is None and in_tok is not None and out_tok is not None and in_tok >= 0 and out_tok >= 0:
                    tokens = int(in_tok + out_tok)
                cache_part = ""
                if cached_tok is not None and in_tok is not None and in_tok > 0 and 0 <= cached_tok <= in_tok:
                    cache_part = f"{sep}cache {cached_tok / in_tok * 100:.0f}%"
                rows.append(f"{_peer(session)} provider-reported tokens: {_count(tokens)}{cache_part}")
            if not expand and len(sessions) > 2:
                rows.append(f"+{len(sessions) - 2} sessions not shown")
        else:
            rows.append("Provider-reported usage: unknown")
        coverage = _items(data.get("coverage_by_scope"))
        complete = sum(1 for item in coverage if item.get("complete") is True)
        if coverage:
            rows.append(f"Telemetry coverage: {complete}/{len(coverage)} scopes complete")

    elif group_id == "context":
        sessions = _items(data.get("sessions"))
        if sessions:
            limit = len(sessions) if expand else min(2, len(sessions))
            for session in sessions[:limit]:
                context = _obj(session.get("context"))
                percent = _number(context.get("percent"))
                state = _clean(context.get("state") or "UNKNOWN").upper()
                estimate = _estimate(context, now)
                cells = max(3, min(12, width // 12))
                if estimate and percent is not None:  # #376 P2: an estimate with its age and its numbers, never MEASURED
                    rows.append(f"{_peer(session)} {estimate} {_bar(percent / 100, cells, unicode)}{sep}"
                                f"{_count(context.get('occupancy_tokens'))} of {_count(context.get('window_tokens'))} tokens")
                elif percent is not None and 0 <= percent <= 100 and state != "ESTIMATED":
                    rows.append(f"{_peer(session)} {percent:.0f}% {_bar(percent / 100, cells, unicode)}{sep}{state}")
                else:
                    rows.append(f"{_peer(session)} UNKNOWN{sep}{_clean(context.get('reason') or 'context occupancy unreported')}")
            if not expand and len(sessions) > 2:
                rows.append(f"+{len(sessions) - 2} sessions not shown")
        else:
            rows.append("Runtime context occupancy: unknown")

    elif group_id == "capacity":
        observations = _items(data.get("observations"))
        if observations:
            def _quota_sort_key(item: dict[str, Any]) -> tuple[str, str, int, str]:
                pool_ref = _clean(item.get("pool_ref") or item.get("pool_kind") or "quota")
                return _pool_order(_clean(item.get("peer_id") or "?"), pool_ref.split(":")[-1])

            sorted_obs = sorted(observations, key=_quota_sort_key)
            probe_notes: dict[str, set[str]] = {}
            na_notes: list[str] = []
            evidence_notes: list[str] = []  # rich: the evidence state that the compact STATE cell does not name
            # Columns are listed by priority (left first); a too-wide table drops whole columns from the right.
            column_widths = {"PEER": 4, "POOL": 8, "STATE": 12, "REM": 5, "RESET-IN": 9, "PACE": 9,
                             "[BAR]": 10, "EXHAUST-IN": 13, "AGE": 5, "PROFILES (configured)": 24}
            if rich:  # P23: STATE is the POOLS cell (glyph and word), the forecast has its own column, no 8-cell bar
                column_widths = dict.fromkeys(("PEER", "POOL", "STATE", "FORECAST", "REM", "RESET-IN", "PACE",
                                               "EXHAUST-IN", "AGE", "PROFILES (configured)"), 0)
            table: list[dict[str, str]] = []
            sources: list[dict[str, Any]] = []

            for item in sorted_obs:
                peer = _clean(item.get("peer_id") or "?")
                pool_ref = _clean(item.get("pool_ref") or item.get("pool_kind") or "quota")
                pool_id = pool_ref.split(":")[-1] if ":" in pool_ref else pool_ref
                state = _clean(item.get("state") or "UNKNOWN").upper()
                measurements = _obj(item.get("measurements"))
                projection = _obj(item.get("projection"))
                age = _number(item.get("age_seconds"))
                age_str = f"{max(0.0, age + elapsed_seconds):.0f}s" if age is not None else "-"

                reason_text = _clean(item.get("reason") or "")
                if reason_text.startswith(_NA_REASONS) and state in {"ABSENT", "STALE"}:
                    # The provider answered completely but has no such window (plan-dependent) or switched it off: n/a, not 0% and not unlimited.
                    fresh = state == "ABSENT"
                    if reason_text.startswith(_DISABLED):
                        what = reason_text
                    else:
                        plan = _na_plan(item, reason_text)
                        what = f"(not reported by plan {plan})" if item.get("display_retired") else f"not reported (plan {plan})"
                    if not item.get("display_retired"):
                        if fresh and data.get("_show_quota_notes"):
                            na_notes.append(f"{peer} {pool_id} n/a {what}")
                        elif not fresh:
                            why = f"{what}, " if data.get("_show_quota_notes") else ""
                            na_notes.append(f"{peer} {pool_id} ? {why}aged; next: peerhub observation refresh --peers {peer}")
                    sources.append(item)
                    table.append({
                        "PEER": peer, "POOL": pool_id, "REM": "n/a" if fresh else "-",
                        "[BAR]": _bar_quota(None), "RESET-IN": "-", "EXHAUST-IN": "-", "PACE": "-", "FORECAST": "-",
                        "STATE": "n/a" if fresh else "?", "AGE": "-" if item.get("display_retired") else age_str,
                        "PROFILES (configured)": _clean(_obj(data.get("consumers")).get(f"{peer}:{pool_id}") or "unmapped")})
                    continue

                if pool_id == "quota" and state != "MEASURED":
                    probe_age = max(0.0, age + elapsed_seconds) if age is not None else 0.0
                    probe_code = f"PROBE:{int(probe_age // 86400)}d" if probe_age >= 86400 else f"PROBE:{int(probe_age // 3600)}h" if probe_age >= 3600 else f"PROBE:{int(probe_age // 60)}m"
                    if age is None:
                        probe_code = "PROBE:unknown"
                    probe_notes.setdefault(peer, set()).add(probe_code)
                    continue

                if state == "STALE":
                    probe_notes.setdefault(peer, set()).add("STALE:window")
                is_usable = (state == "MEASURED")
                if is_usable:
                    rem = _number(measurements.get("remaining_fraction"))
                    rem_str = _pct(rem) if rem is not None and 0.0 <= rem <= 1.0 else "-"

                    reset_in = _number(item.get("reset_in_seconds"))
                    if reset_in is None:
                        reset_in = _number(measurements.get("reset_in_seconds"))
                    burn = None
                    if reset_in is not None and reset_in >= 0:
                        rem_reset = max(0.0, reset_in - elapsed_seconds)
                        reset_str = _format_duration(rem_reset)
                        burn = _even_burn(pool_id, rem_reset)
                    else:
                        reset_str = "-"
                    bar_str = _bar_quota(rem, 8, unicode, burn)

                    exhaustion = _alert_exhaustion_at(item)
                    if exhaustion is not None:
                        ref_time = read_at if read_at is not None else 0.0
                        rem_exhaust = max(0.0, exhaustion - ref_time - elapsed_seconds)
                        exhaust_str = "exhausted" if rem == 0 else "past forecast" if rem is not None and rem > 0 and rem_exhaust == 0 else _format_duration(rem_exhaust)
                    else:
                        exhaust_str = "-"

                    pace = _number(projection.get("pace_pt"))
                    pace_str = _pace_glyph(f"{pace:+.1f}pp", unicode) if pace is not None else "-"

                    proj_state = _clean(projection.get("state") or "").upper()
                    state_str = "INSUFF" if proj_state == "INSUFFICIENT" else proj_state or "MEASURED"
                    forecast = "insuff" if proj_state == "INSUFFICIENT" else proj_state.lower() or "-"
                else:
                    # STALE/UNKNOWN rows keep identity+reason, no usable capacity shown
                    rem_str = "-"
                    bar_str = _bar_quota(None)
                    reset_str = "-"
                    exhaust_str = "-"
                    pace_str = "-"
                    state_str = "STALE:window" if state == "STALE" else state
                    forecast = "-"
                    if state != "STALE":  # a stale window has its `STALE:window` note below
                        evidence_notes.append(f"{peer} {pool_id} evidence {state}; "
                                              f"next: peerhub observation refresh --peers {peer}")

                sources.append(item)
                table.append({"PEER": peer, "POOL": pool_id, "REM": rem_str,
                              "[BAR]": bar_str, "RESET-IN": reset_str,
                              "EXHAUST-IN": exhaust_str, "PACE": pace_str, "FORECAST": forecast,
                              "STATE": state_str, "AGE": age_str,
                              "PROFILES (configured)": _clean(_obj(data.get("consumers")).get(f"{peer}:{pool_id}") or "unmapped")})

            pool_rows = cast(dict[tuple[str, str], dict[str, Any]], data.get("_pool_rows") or {})
            # POOLS' order (#394): the position of each pool in the rows POOLS draws.
            position = {(str(row["peer"]), str(row["pool"])): index
                        for index, (row, _) in enumerate(_ordered_pools(list(pool_rows.values()), every=True))}
            layout, retired_notes = _quota_layout([
                {"peer": cells["PEER"], "pool": cells["POOL"], "state": cells["STATE"],
                 "profiles": cells["PROFILES (configured)"], "cells": cells, "source": item,
                 "retired": item.get("display_retired"), "display_plan": item.get("display_plan")}
                for cells, item in zip(table, sources)], unicode, show_quota_notes=bool(data.get("_show_quota_notes")),
                order=position)
            by_pool = {(str(row["peer"]), str(row["pool"])): row for row in layout}
            for row in layout:
                cells = row["cells"]
                key = (str(row["source"].get("peer_id")), str(row["source"].get("pool_ref")))
                summary = pool_rows.get(key) or (_compact_pool(row["source"], read_at or 0, elapsed_seconds, {}) if rich else None)
                if summary is not None and summary.get("limited"):  # #394: the pool under its parent, as POOLS draws it
                    if not rich:
                        cells["STATE"] = str(summary["state"]).upper()
                    parent = by_pool.get((str(row["peer"]), str(summary["depends_on"])))
                    if parent is not None and summary.get("own_left"):  # the parent has less left: its values, the nearer reset
                        for column in ("REM", "PACE", "[BAR]", "EXHAUST-IN", "FORECAST"):
                            cells[column] = parent["cells"][column]
                        cells["RESET-IN"] = _format_duration(max(0.0, float(summary["reset_seconds"])))
                if rich and summary is not None:  # the row POOLS draws for this pool (the frame's rows), so both show one glyph and word
                    row["summary"] = summary
                    glyphs = RICH_GLYPHS[unicode]
                    cells["STATE"] = f"{glyphs.get(str(summary['state']), glyphs['aged'])} {summary['state']}"
                    cells["REM"] = summary["left"]
                    if summary["pace"] == "-":  # no pace after the reset time, as in POOLS
                        cells["PACE"] = "-"
                if row["display_pool"] != row["pool"]:  # a child row: its 8-cell bar has the child glyphs, as in POOLS
                    cells["[BAR]"] = cells["[BAR]"].translate(_CHILD_BAR[unicode])
                cells["PEER"], cells["POOL"] = row["display_peer"], row["display_pool"]
            table = [row["cells"] for row in layout]
            natural = bounded or rich  # a column is as wide as its widest cell; the unbounded plain table has minimums
            # One column set for the whole table; PEER and POOL always stay, and no cell is cut.
            sizes = {c: max(_width(c) if natural else w, *(_width(cells[c]) for cells in table))
                     if table else (_width(c) if natural else w) for c, w in column_widths.items()}
            sizes["POOL"] = max(_width("POOL"), *(_width(cells["POOL"]) for cells in table)) if table else 4
            suffix_width = max((_width(str(row["suffix"])) + 1 for row in layout if row["suffix"]), default=0)
            columns = list(column_widths)
            while len(columns) > 2 and sum(sizes[c] for c in columns) + 2 * (len(columns) - 1) + suffix_width > width:
                columns.pop()

            def table_line(cells: dict[str, str], code: str | None = None) -> str:
                shown = {c: _quota_cell(cells[c], c, code) for c in columns}
                return "  ".join(" " * (sizes[c] - _width(cells[c])) + shown[c] if c in {"REM", "AGE"}
                                 else shown[c] + " " * (sizes[c] - _width(cells[c])) for c in columns).rstrip()

            rows.append(table_line({c: c for c in column_widths}))
            # Only the rich view paints (P23): a row takes the one colour of its POOLS row (`_pool_colour`) on the
            # STATE cell and the pace arrow; an n/a row and a note are dim.
            dim = RICH_SGR["dim"] if rich and color else None
            previous_peer = ""
            for row in layout:
                if previous_peer and previous_peer != row["peer"] and not rich:
                    rows.append("")
                previous_peer = row["peer"]
                colour = _pool_colour(row["summary"]) if rich and color else None
                line = table_line(row["cells"], RICH_SGR[colour] if colour else None)
                rows.extend(_quota_suffix(line, str(row["suffix"]), width, unicode, dim))
            table_size = len(rows)

            def note_rows(text: str) -> list[str]:  # the rich view wraps a note; the plain view fits it to one line
                return _compact_wrap(text, max(1, width)) if rich else [_fit(text, width, unicode)]

            # Keep reasons for folded pools; retired pools already have a combined note.
            rows.extend(_paint(part, dim) for note in [*retired_notes, *na_notes] for part in note_rows(note))
            if rich:
                rows.extend(part for note in evidence_notes for part in note_rows(note))
            rows.extend(part for peer, codes in probe_notes.items() for part in note_rows(
                f"{peer} {' '.join(sorted(codes))}; next: peerhub observation refresh --peers {peer}"))
            if len(columns) < len(column_widths):
                rows.extend(_omitted_rows(["bar" if c == "[BAR]" else c.lower() for c in list(column_widths)[len(columns):]], width))
            if rich and layout and "FORECAST" in columns:
                rows.extend(_paint(part, dim) for part in _compact_wrap(FORECAST_LEGEND, max(1, width)))

        else:
            rows.append("Quota capacity: no observations")
            table_size = len(rows)
        for credit in _items(data.get("reset_credits")):
            count = _count(credit.get("available_count"))
            expires = _number(credit.get("nearest_expires_at"))
            left = None if expires is None or read_at is None else expires - read_at - elapsed_seconds
            expiry = "-" if expires is None else f"in {_format_duration(left)}" if left and left > 0 else datetime.fromtimestamp(expires, timezone.utc).strftime("%Y-%m-%d %H:%MZ")
            state = _clean(credit.get("state") or "UNKNOWN")
            rows.append(f"{_peer(credit)} reset credits: {count}{bar}soonest {expiry} [{state}]")
        account_items = _items(data.get("accounts"))
        if account_items:
            rows.extend(_account_rows(account_items, elapsed_seconds, bar, max(1, width) if rich else None))
        note = _coverage(data, unicode)
        if note:
            rows.append(f"Coverage: {note}")

    elif group_id == "runtime":
        sessions = _items(data.get("sessions"))
        if sessions:
            limit = len(sessions) if expand else min(2, len(sessions))
            for session in sessions[:limit]:
                binding = _obj(session.get("configured_binding"))
                current = _obj(session.get("current_profile"))
                rows.append(f"{_peer(session)} profile {_clean(binding.get('profile') or 'unknown')}{sep}current {_clean(current.get('source') or 'unknown')} / {_clean(current.get('status') or 'UNKNOWN')}")
            if not expand and len(sessions) > 2:
                rows.append(f"+{len(sessions) - 2} runtime sessions not shown")
        else:
            rows.append("Runtime profile provenance: no sessions")

    elif group_id == "coverage":
        scopes = _items(data.get("scopes"))
        complete = sum(1 for scope in scopes if scope.get("delivery_complete") is True
                       and _obj(scope.get("usage_coverage")).get("complete") is True)
        rows.append(f"Scopes complete: {complete}/{len(scopes)}" if scopes else "No scope evidence")
        if data.get("truncated"):
            rows.append(f"Coverage: partial {dot} sample truncated")

    elif group_id == "store":
        rows.append(f"Schema {_count(data.get('schema_version'))}{sep}database {_size(data.get('database_size_bytes'))}")

    elif group_id == "alerts":
        rows.extend(_alert_row(alert, unicode, keep, bar) for alert in _items(data.get("alerts")))
        rows.extend(_cell(row, unicode, "-", keep) for row in cast(list[object], data.get("unread") or []))  # A7
        if not rows:
            rows.append("none")

    else:
        # Keep future fields visible without exposing a JSON-shaped dump.
        rows.extend(_fallback_rows(data))

    return [row for row in rows if not row.startswith("Coverage:")] or ["No data"], table_size


def _alert_row(alert: dict[str, Any], unicode: bool = True, keep: str | None = None, sep: str = " | ") -> str:
    age = _number(alert.get("age_s"))
    return sep.join((f"{_cell(alert.get('kind'), unicode, '?', keep)}: {_cell(alert.get('subject'), unicode, '?', keep)}",
                     f"age {'-' if age is None else _compact_age(age)}", _cell(alert.get("severity"), unicode, "?", keep),
                     _cell(alert.get("code"), unicode, "?", keep)))


def _stamp(value: object) -> str:
    number = _number(value)
    return "?" if number is None else _utc_text(number, "%Y-%m-%d %H:%M:%SZ") or "?"


def _pack(fields: Sequence[str], width: int, sep: str = " | ") -> list[str]:
    """Whole fields joined by `sep` and carried to the next line; only a field wider than a line wraps by words."""
    lines: list[str] = []
    line = ""
    for item in fields:
        if line and _width(line + sep + item) <= width:
            line += sep + item
            continue
        if line:
            lines.append(line)
        parts = [item] if _width(item) <= width else _compact_wrap(item, width)
        lines.extend(parts[:-1])
        line = parts[-1] if parts else ""
    return lines + ([line] if line else [])


def _ask_fields(row: dict[str, Any], unicode: bool = True, keep: str | None = None) -> list[str]:
    """Every field of one ask row (#374 C): the expand view hides nothing the read model gives. Text is made plain-view
    safe here, before packing, so a replaced character can never widen a packed line (A5)."""

    def txt(value: object, empty: str = "?") -> str:
        return _cell(value, unicode, empty, keep)

    state = txt(row.get("state"))
    open_row = _is_open(row)
    fields: list[str] = []
    if open_row:
        flag = _flag(row)
        left = _number(row.get("lease_left_s"))
        reason = (f"lease expired {_compact_age(-left)} ago" if flag == "!overdue" and left is not None and left < 0
                  else "lease expired" if flag == "!overdue" else "caller process gone" if flag == "!gone"
                  else txt(row.get("alive_reason"), "liveness unknown") if flag == "?" else "caller alive")
        fields.append(f"flag {flag or 'none'} ({reason})")
    fields.append(f"state {state}" + (" (superseded)" if row.get("superseded") else ""))
    if open_row:
        left = _number(row.get("lease_left_s"))
        fields += [f"elapsed {_compact_age(_number(row.get('elapsed_s')))}", f"started {_stamp(row.get('started_at'))}",
                   "lease ?" if left is None else f"lease left {_compact_age(left)}" if left >= 0
                   else f"lease expired {_compact_age(-left)} ago"]
    else:
        exact = row.get("ended_exact")
        ended = _stamp(row.get("ended_at")) + ("" if row.get("ended_at") is None
                                               else " (exact)" if exact else " (latest possible)")
        fields += [f"age {_compact_age(_number(row.get('age_s'))) if exact else '?'}",
                   f"elapsed {_compact_age(_number(row.get('elapsed_s')))}", f"started {_stamp(row.get('started_at'))}",
                   f"ended {ended}"]
    lead = _obj(row.get("lead"))
    tokens = _obj(row.get("tokens"))
    fields += [f"profile {txt(row.get('profile'))}", f"peer {txt(row.get('peer'))}",
               f"model {txt(row.get('model'))}", f"effort {txt(row.get('effort'))}",
               f"lead {txt(lead.get('label'))} ({txt(lead.get('tool'))} {txt(lead.get('id'))})" if lead else "lead none",
               f"name {txt(row.get('name'))}", f"stream {txt(row.get('stream'))}",
               f"tokens in {_count(tokens.get('input'))} out {_count(tokens.get('output'))}" if tokens else "tokens ?"]
    if row.get("note"):
        fields.append(f"note {txt(row['note'])}")
    reasons = [txt(reason) for reason in cast(list[object], row.get("reasons") or []) if reason]
    if reasons:
        fields.append("reasons " + ", ".join(reasons))
    return fields


def _ask_lines(row: dict[str, Any], fields_width: int, text_width: int, unicode: bool = True, keep: str | None = None,
               sep: str = " | ") -> list[str]:
    """One ask without a mark: its fields packed to `fields_width`, then its reply (an ended ask) and its ask text
    wrapped to `text_width`."""
    lines = _pack(_ask_fields(row, unicode, keep), max(1, fields_width), sep)
    texts = [] if _is_open(row) else [("reply", row.get("reply_line_long"))]
    for label, text in [*texts, ("ask", row.get("ask_line_long"))]:
        lines.extend(_compact_wrap(f"{label}: {_cell(text, unicode, '-', keep)}", max(1, text_width)))
    return lines


def _ask_rows(row: dict[str, Any], width: int, unicode: bool = True, keep: str | None = None) -> list[str]:
    lines = _ask_lines(row, width - 2, width, unicode, keep)
    lines[0] = "- " + lines[0]  # marks where each ask starts; continuation lines carry no mark
    return lines


def _clock(value: object, fmt: str) -> str:
    number = _number(value)
    return "?" if number is None else _utc_text(number, fmt) or "?"


def _lead_rows(lead: dict[str, Any], now: float | None, width: int, unicode: bool = True,
               keep: str | None = None, *, sep: str = " | ", meter: bool = False, paint: bool = False) -> list[str]:
    """Every field of one lead (#376 3.4): its session (the lead and process, then model, context and asks, then name
    and cwd, then title, prompt and said), or why it has none. `meter` (the rich --expand frame, P23) puts the
    compact frame's context meter before the context numbers and its `!` at 80% or more; `paint` colours the meter
    as the compact frame does (bad from 80%, dim without a percent)."""

    def txt(value: object) -> str:
        return _cell(value, unicode, "?", keep)

    head = [txt(lead.get("label")), f"{txt(lead.get('tool'))} {txt(lead.get('id'))}"]
    asks = [f"running {_count(lead.get('running'))}", f"asks 24h {_count(lead.get('asks_24h'))}",
            f"last ask {_stamp(lead.get('last_ask_at'))}"]
    item = _obj(lead.get("session"))
    if not item:
        why = ("no session (not a Claude Code lead)" if lead.get("tool") != "cc"
               else "no session found (ended, or its Claude folder is not in PEERHUB_LEAD_DIRS)")
        return _pack([*head, why, *asks], width, sep)
    active, tokens = _number(item.get("active_at")), _number(item.get("context_tokens"))
    window, pct = _number(item.get("context_window")), _number(item.get("context_pct"))
    shown_pct = pct if tokens is not None and window is not None else None  # the percent the text names
    mark = "!" if meter and shown_pct is not None and shown_pct >= _LEAD_MARK_PCT else ""
    ctx = ("ctx ?" if tokens is None else f"ctx {_count(tokens)} of {_count(window)} model max ({int(pct)}%{mark})"
           if window is not None and pct is not None else f"ctx {_count(tokens)} (window unknown)")
    if meter:
        ctx = _ctx_meter(ctx, shown_pct, unicode)
    first = [*head, f"{txt(item.get('state'))} since {_stamp(item.get('state_since'))}",  # A-4: with the date
             "active ?" if active is None or now is None else f"active {_compact_age(now - active)} ago",
             "alive" if item.get("alive") is True else "alive ?", f"pid {txt(item.get('pid'))}",
             f"entry {txt(item.get('entry'))}", f"claude {txt(item.get('version'))}",
             f"started {_clock(item.get('started_at'), '%Y-%m-%d %H:%MZ')}"]
    second = [f"model {txt(item.get('model'))}", ctx + ("" if tokens is None else f" at {_stamp(item.get('context_at'))}"),
              *asks]
    third = [*([f"name {txt(item['name'])}"] if item.get("name") else []), f"cwd {txt(item.get('cwd'))}"]
    packed = [_pack(part, width, sep) for part in (first, second, third)]
    code = (None if not (meter and paint) else RICH_SGR["dim"] if shown_pct is None
            else RICH_SGR["bad"] if shown_pct >= _LEAD_MARK_PCT else None)
    if code:  # painted after packing, so the colour never changes a width; a meter cut off its `ctx` stays plain
        cells = ctx.split(" ")[1]
        for index, line in enumerate(packed[1]):
            at = line.find(f"ctx {cells} ")
            if at >= 0:
                packed[1][index] = line[:at + 4] + _paint(cells, code) + line[at + 4 + len(cells):]
                break
    rows = [line for part in packed for line in part]
    for label, key in (("title", "title"), ("prompt", "prompt_line"), ("said", "said_line")):
        if item.get(key):
            rows.extend(_compact_wrap(f"{label}: {txt(item[key])}", width))
    return rows


def _recent_ended(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The finished asks of RECENT, newest first by their end; a running ask and a superseded one are never in it, and an
    ask without a known end goes last."""
    return sorted(_ask_split(data)[1],
                  key=lambda row: (_number(row.get("ended_at")) is None, -(_number(row.get("ended_at")) or 0.0)))


def _recent_head(data: dict[str, Any], ended: list[dict[str, Any]], *, superseded: bool = False) -> str:
    """`RECENT 130 (2 running)`: the finished asks, and how many more run (they are listed under RUNNING). The detail
    frames add the superseded asks, which the read model counts and does not list."""
    head = f"RECENT {len(ended)} ({_running_head(data, _ask_split(data)[0], True)[1]} running)"
    omitted = _number(data.get("superseded_omitted")) or 0
    replaced = len(_ask_split(data)[2])
    if superseded and (replaced or omitted):
        head += f" | superseded {replaced}" + (f" (+{int(omitted)} not read)" if omitted else "")
    return head


def _kilo(value: object) -> str | None:
    """A token count as `850`, `62k`, `1M`, `1.2M`; None when it is not a count."""
    number = _number(value)
    if number is None or number < 0:
        return None
    count = int(number)
    if count < 1000:
        return str(count)
    if count < 999_500:
        return f"{round(count / 1000)}k"
    return f"{count / 1_000_000:.1f}".removesuffix(".0") + "M"


def _whole(value: object, high: float | None = None) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number >= 0 and (high is None or number <= high) else None


def _recent_facts(row: dict[str, Any]) -> dict[str, Any]:
    """What a RECENT row shows beyond the ask's own fields (None where it is not known: never 0). The session values are
    those of the stream's newest session now; the context is the cc adapter's estimate, so cx, ag and every other peer
    show none (and a lead's context is never used). The cache share is the cache-read tokens of the ask's input."""
    session = _obj(row.get("session"))
    mode_raw = session.get("execution_mode")
    context = _obj(session.get("context"))
    provider = session.get("provider") or row.get("peer")  # the runtime of the session; a peer id may be anything
    usable = provider == "cc" and context.get("state") in {"ESTIMATED", "MEASURED"}
    tokens = _obj(row.get("tokens"))
    read, total = _whole(tokens.get("cache_read")), _whole(tokens.get("input"))
    return {"mode": "R" if mode_raw in {"reused", "reuse", "resume"} else "F" if mode_raw == "fresh" else None,
            "uses": _whole(session.get("uses")), "generation": _whole(session.get("generation")),
            "ctx_tokens": _whole(context.get("occupancy_tokens")) if usable else None,
            "ctx_window": _whole(context.get("window_tokens")) if usable else None,
            "ctx_pct": _whole(context.get("percent"), 100) if usable else None,
            "input": total, "output": _whole(tokens.get("output")), "cache_read": read,
            "cache_write": _whole(tokens.get("cache_write")),
            "cache_pct": (int(read / total * 100 + 0.5) if read is not None and total and read <= total else None)}


NAME_CAP = 24
_RECENT_DROPS = (("cache",), ("lead",), ("mode", "uses"), ("ctx",))  # the order narrow widths drop columns in


def _recent_compact(ended: list[dict[str, Any]], width: int, unicode: bool, keep: str | None, *, rich: bool = False,
                    paint: bool = False, limit: int | None = None) -> list[str]:
    """The compact RECENT rows (the first `limit`, RECENT_CAP by default, of `ended`):
    `ok 3m cc.effort lead7 s-ab12 R u3 62k/6% c91% "reply snippet"`: state, age since the end, profile, lead, name,
    R resume | F fresh, `u<uses>`, context, cache-read share, the reply. A narrow width drops the cache share, then the
    lead, then the mode with the uses, then the context; the name and the reply stay, and the reply is cut by cell width.
    The rich view puts a state dot before the state when that costs no column."""
    glyphs = RICH_GLYPHS[unicode]

    def cell(value: object) -> str:
        return _cell(value, unicode, "-", keep)

    shown = ended[:limit or RECENT_CAP]
    table: list[dict[str, str]] = []
    for row in shown:
        facts = _recent_facts(row)
        tokens, pct = _kilo(facts["ctx_tokens"]), facts["ctx_pct"]
        ctx = f"{tokens}/{pct}%" if tokens and pct is not None else tokens or (f"{pct}%" if pct is not None else "-")
        table.append({
            "state": cell(row.get("state")),
            "age": _compact_age(_number(row.get("age_s"))) if row.get("ended_exact") else "?",
            "profile": cell(row.get("profile") or row.get("peer")),
            "lead": _short(cell(_obj(row.get("lead")).get("label")), LEAD_LABEL_COLS, unicode),
            "name": _short(cell(row.get("name")), NAME_CAP, unicode),
            "mode": facts["mode"] or "-", "uses": "-" if facts["uses"] is None else f"u{facts['uses']}",
            "ctx": ctx, "cache": "-" if facts["cache_pct"] is None else f"c{facts['cache_pct']}%"})
    replies = [(_short(cell(_recent_text(row)), REQUEST_CAP, unicode), bool(row.get("reply_line"))) for row in shown]
    order = ["state", "age", "profile", "lead", "name", "mode", "uses", "ctx", "cache"]
    floor = min(24, max(_width(text) + (2 if quoted else 0) for text, quoted in replies))  # what the reply is given first

    def room(columns: list[str], dot: int) -> int:
        fixed = sum(max(_width(cells[name]) for cells in table) + (dot if name == "state" else 0) + 2 for name in columns)
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
    need = min(10, max(_width(text) + (2 if quoted else 0) for text, quoted in replies))
    for name in ("name", "profile"):  # nothing is left to drop: the name, then the profile, give up cells to the reply
        if left >= need:
            break
        widest = max(_width(fields[name]) for fields in table)
        cap = max(8, widest - (need - left))
        if cap < widest:
            for fields in table:
                fields[name] = _fit(fields[name], cap, unicode)
            left = room(columns, 2 if dotted else 0)
    left = max(1, left)
    cells: list[list[str]] = []
    for fields, (text, quoted) in zip(table, replies):
        reply = f'"{_fit(text, max(1, left - 2), unicode)}"' if quoted and left >= 3 else _fit(text, left, unicode)
        row = [fields[name] for name in columns]
        if dotted:
            row[0] = f"{glyphs['dot']} {row[0]}"
        cells.append([*row, reply])
    colours = [_RECENT_COLOURS.get(str(row.get("state")), "bad") for row in shown]
    painter = _column_painter(0, colours) if dotted and paint else None
    return [" " + line for line in _table(cells, width - 1, unicode, right=(1,), elastic=len(columns), paint=painter)]


def _duration(seconds: object) -> str:
    """An elapsed time with its seconds: `45s`, `3m12s`, `1h02m`, `2d03h`; `?` when unknown."""
    number = _number(seconds)
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


def _recent_entry(row: dict[str, Any], width: int, unicode: bool, keep: str | None, sep: str = " | ") -> list[str]:
    """One RECENT entry of --expand, two lines (each at most `width` cells; the session values are those of now):
    `ok 14:02-14:05 (3m12s) cc/opus-4 [high] lead7 s-ab12 gen2 R u3 ctx 62k/1M 6% now` and
    `in 41k out 2k cache r38k w3k 91% | ask "..." -> reply "..."`. `sep` is the view's separator (the rich view's
    dot), the one between the tokens and the texts."""
    facts = _recent_facts(row)

    def cell(value: object) -> str:
        return _cell(value, unicode, "-", keep)

    model = re.sub(r"^claude-", "", _clean(row.get("model") or ""))
    peer = cell(row.get("peer"))
    who = f"{peer}/{cell(model or None)}" + (f" [{cell(row.get('effort'))}]" if row.get("effort") else "")
    tokens, window, pct = _kilo(facts["ctx_tokens"]), _kilo(facts["ctx_window"]), facts["ctx_pct"]
    ctx = "-" if tokens is None and pct is None else " ".join(
        part for part in (f"{tokens}/{window}" if tokens and window else tokens, f"{pct}%" if pct is not None else None)
        if part)
    joined = any(facts[key] is not None for key in ("mode", "uses", "generation")) or ctx != "-"
    first = " ".join([
        cell(row.get("state")), f"{_clock(row.get('started_at'), '%H:%M')}-{_clock(row.get('ended_at'), '%H:%M')}",
        f"({_duration(row.get('elapsed_s'))})", who, cell(_obj(row.get("lead")).get("label")), cell(row.get("name")),
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
    reply = said or (f"({_clean(row['note'])})" if row.get("note") else None)
    texts = [cell(ask), cell(reply)]
    room = max(2, width - _width(prefix) - _width('ask "" -> reply ""'))
    if sum(_width(text) for text in texts) > room:  # the shorter text keeps what it needs; the other takes the rest
        half = room // 2
        short, long = sorted(range(2), key=lambda i: _width(texts[i]))
        if _width(texts[short]) <= half:
            texts[long] = _fit(texts[long], max(1, room - _width(texts[short])), unicode)
        else:
            texts = [_fit(texts[0], max(1, half), unicode), _fit(texts[1], max(1, room - half), unicode)]
    quote = [f'"{text}"' if value else text for text, value in zip(texts, (ask, said))]
    second = f"{prefix}ask {quote[0]} -> reply {quote[1]}"
    return [_fit(first, width, unicode), _fit(second, width, unicode)]


def _recent_expand_lines(ended: list[dict[str, Any]], width: int, unicode: bool, keep: str | None, *, rich: bool = False,
                         paint: bool = False) -> list[str]:
    """The RECENT entries of the --expand frame (RECENT_EXPAND at most), two lines each, one cell in: the first line
    starts with the state dot (the second is three cells in), as the entries of RUNNING do."""
    glyphs = RICH_GLYPHS[unicode]
    lines: list[str] = []
    for row in ended[:RECENT_EXPAND]:
        first, second = _recent_entry(row, max(1, width - 3), unicode, keep, f" {glyphs['sep']} " if rich else " | ")
        mark = glyphs["dot"] if rich else ""
        if mark and paint:
            mark = _paint(mark, RICH_SGR[_RECENT_COLOURS.get(str(row.get("state")), "bad")])
        lines.extend([" " + (f"{mark} " if mark else "") + first, "   " + second])
    return lines


def _activity_notes(data: dict[str, Any], width: int, sep: str = " | ") -> list[str]:
    """What the activity sample left out: a truncated sample, and why a workspace generation was not read."""
    rows: list[str] = []
    if data.get("truncated"):
        rows.extend(_pack([f"sample truncated: {_count(data.get('scopes_total'))} scopes",
                           f"superseded not read {_count(data.get('superseded_omitted'))}"], width, sep))
    if data.get("reason"):
        rows.extend(_pack([f"workspace generation: {_clean(data['reason'])}"], width, sep))
    return rows


def _safe_error_reason(value: object) -> str:
    """Expose a bounded exception class only; never print raw exception text."""
    raw = _clean(value)
    code = raw.split(":", 1)[0].strip().split(".")[-1]
    allowed = {"OperationalError", "DatabaseError", "DataError", "IntegrityError", "ProgrammingError",
               "InterfaceError", "SchemaVersionError", "PermissionError", "FileNotFoundError", "OSError"}
    return f"{code}: database read failed" if code in allowed else "database read failed"


def _safe_refresh_warning_labels(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    warnings = cast(Sequence[Any], value)
    labels: list[str] = []
    safe_reasons = {"probe_warning", "probe_timeout", "claude_usage_timeout", "gemini_usage_timeout",
                    "codex_usage_timeout", "provider_probe_error", "provider_probe_unavailable",
                    "auth_error", "quota_exhausted", "invalid_model", "usage_limit", "timeout",
                    "unavailable", "error"}
    for item in warnings[:3]:
        if not isinstance(item, str):
            labels.append("probe_warning")
            continue
        parts = [_clean(part).strip() for part in item.split(":")]
        peer = parts[0].lower() if parts and parts[0].lower() in {"cc", "cx", "ag"} else ""
        reason = parts[1] if peer and len(parts) > 1 else (parts[0] if parts else "")
        code = reason.lower() if reason.lower() in safe_reasons else "probe_warning"
        labels.append(f"{peer} {code}".strip())
    if len(warnings) > 3:
        labels.append(f"+{len(warnings) - 3} warnings")
    return labels


def _group_has_uncertain_measurement(value: object, *, depth: int) -> bool:
    if depth >= 4:
        return False
    if isinstance(value, Mapping):
        for key, child in list(cast(Mapping[str, Any], value).items())[:100]:
            if str(key).lower() in {"status", "state"} and isinstance(child, str):
                if child.upper() in {"ERROR", "FAILED", "UNAVAILABLE", "STALE", "PARTIAL", "UNKNOWN"}:
                    return True
            if _group_has_uncertain_measurement(child, depth=depth + 1):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_group_has_uncertain_measurement(item, depth=depth + 1)
                   for item in cast(Sequence[Any], value)[:100])
    return False


def _fallback_rows(data: dict[str, Any], prefix: str = "", depth: int = 0) -> list[str]:
    if depth >= 2:
        return []
    rows: list[str] = []
    for key, value in list(data.items())[:4]:
        label = f"{prefix}{_clean(key).replace('_', ' ')}"
        if isinstance(value, Mapping):
            rows.extend(_fallback_rows(_obj(cast(Mapping[str, Any], value)), f"{label} · ", depth + 1))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            rows.append(f"{label}: {_count(len(cast(Sequence[Any], value)))} items")
        elif value is None:
            rows.append(f"{label}: unknown")
        else:
            rows.append(f"{label}: {_clean(value)}")
    return [row for row in rows if not row.startswith("Coverage:")] or ["No data"]


def _compact_wrap(text: str, width: int) -> list[str]:
    """Wrap prose and paths without ellipsizing measurements or counter values."""
    lines: list[str] = []
    line = ""
    for word in re.findall(r"details: --expand|\S+", _clean(text)):
        if line and _width(line + " " + word) > width:
            lines.append(line)
            line = ""
        while _width(word) > width:
            chunk = ""
            for char in word:
                if _width(chunk + char) > width:
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


def _compact_age(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    if seconds < 60:
        return f"{max(0, int(seconds))}s"
    return _format_duration(max(0, seconds))


FAMILIES = {"G": "flash", "A": "argon", "3P": "claude-via-ag", "C": "claude",
            "F": "fable", "X": "codex"}


def _pool_family(pool: str) -> str:
    family, _, window = pool.partition("-")
    return family if family in FAMILIES and re.fullmatch(r"[0-9]+[HD]", window) else "?"


def not_applicable(state: object, reason: object, *, aged: bool) -> bool:
    """Fresh ABSENT evidence for a window the plan omits or the provider disabled: n/a, never 0% or unknown."""
    return state == "ABSENT" and not aged and _clean(reason or "").startswith(_NA_REASONS)


def _compact_pool(item: dict[str, Any], now: float, elapsed: float,
                  consumers: dict[str, list[str]]) -> dict[str, Any]:
    peer = _clean(item.get("peer_id") or "?")
    pool = _clean(item.get("pool_ref") or item.get("pool_kind") or "?").split(":")[-1]
    family = _pool_family(pool)
    meaning = FAMILIES.get(family, "?")
    measurements = _obj(item.get("last_measurements")) or _obj(item.get("measurements"))
    remaining = _number(measurements.get("remaining_fraction"))
    if item.get("state") not in {"MEASURED", "STALE"} or (remaining is not None and not 0 <= remaining <= 1):
        remaining = None
    age = _number(item.get("age_seconds"))
    age = max(0, age + elapsed) if age is not None else None
    ttl = _number(item.get("ttl_seconds"))
    aged = (age is not None and ttl is not None and age >= ttl) or (ttl is None and item.get("state") == "STALE")
    resets_at = _number(item.get("resets_at"))
    reset_in = _number(item.get("reset_in_seconds"))
    if reset_in is None:
        reset_in = _number(measurements.get("reset_in_seconds"))
    if resets_at is not None:
        reset_in = resets_at - now - elapsed
    elif reset_in is not None:
        reset_in -= elapsed
    reason = _clean(item.get("reason") or "")
    not_reported = not_applicable(item.get("state"), reason, aged=aged)
    projection = _obj(item.get("projection"))
    samples = _number(item.get("projection_samples")) or 0
    if not_reported:
        state = "n/a"
    elif remaining is None:
        state = "aged" if item.get("stored_state") == "ABSENT" and aged else "?"
    elif resets_at is not None and resets_at <= now + elapsed:
        state = "reset"
    elif reset_in is not None and reset_in <= 0:
        state = "reset"
    elif remaining == 0:
        state = "out"
    elif remaining <= .10:
        state = "low"
    elif samples >= 2 and projection.get("state") == "threat":
        state = "fast"
    else:
        state = "aged" if aged else "ok"
    left = _pct(remaining) if remaining is not None and state not in {"n/a", "?", "reset"} else "-"
    resets = ("-" if state in {"n/a", "?"} or reset_in is None else
              "passed" if reset_in <= 0 else _compact_age(reset_in))
    pace = _number(projection.get("pace_pt"))
    pace_text = "-"
    if pace is not None and remaining is not None and state not in {"n/a", "?", "reset"}:
        rounded = round(pace)
        pace_text = f"{rounded:+d}" if rounded else "0"
    return {"peer": peer, "pool": pool, "pool_ref": _clean(item.get("pool_ref") or ""),
            "meaning": meaning, "remaining": remaining, "left": left,
            "resets": resets, "pace": pace_text, "reset_seconds": reset_in, "family": family, "age": _compact_age(age), "state": state, "aged": aged,
            "color_state": "ERROR" if item.get("state") == "ERROR" else
                           state if state in {"n/a", "?", "reset", "aged"} else _clean(projection.get("state") or state),
            "needs_refresh": aged or age is None or state in {"?", "reset"}, "reason": reason, "display_plan": item.get("display_plan"),
            "retired": bool(item.get("display_retired")), "stale": item.get("state") == "STALE",
            "profiles": ",".join(consumers.get(f"{peer}:{pool}", [])) or "unmapped"}


def _na_plan(item: dict[str, Any], reason: str) -> str:
    """The plan named in a not-applicable pool note: the account-supplied `display_plan` when present, else the stored reason's `plan=`."""
    shown = item.get("display_plan")
    if isinstance(shown, str) and shown:
        return _clean(shown)
    return _clean(reason.split("plan=", 1)[1]) if "plan=" in reason else "unknown"


def _utc_text(timestamp: float, fmt: str) -> str | None:
    """A UTC date text, or None for an instant that cannot be represented (the renderer must never raise on stored data)."""
    try:  # epoch + timedelta (not fromtimestamp): the platform C time functions stop at year 3000 on Windows
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=timestamp)).strftime(fmt)
    except (OverflowError, OSError, ValueError):
        return None


def _account_failed(item: dict[str, Any]) -> bool:
    return item.get("stored_state") in {"ERROR", "UNAVAILABLE", "UNKNOWN"}


def _account_plan(item: dict[str, Any]) -> str:
    """The plan label (display only), or n/a (provider does not expose it) / ? (could not be determined)."""
    status = item.get("plan_status")
    return _clean(item.get("plan")) if status == "reported" and item.get("plan") else "n/a" if status == "n/a" else "?"


def _account_login(item: dict[str, Any]) -> str:
    login = item.get("logged_in")
    return "ok" if login is True else "out" if login is False else "?"


def _account_rows(accounts: list[dict[str, Any]], elapsed: float, sep: str = " | ", wrap: int | None = None) -> list[str]:
    """Expanded ACCOUNTS rows (--expand only since #374): the stored fields of JSON capacity.accounts, labels spelled out.
    `sep` joins the fields of a row; with `wrap` (the rich --expand frame) a row wider than that carries whole fields
    to the next line."""
    rows = ["ACCOUNTS (plan is display data; the expiry is the login token's, not billing)"]
    if wrap is not None:
        rows = _compact_wrap(rows[0], wrap)

    def add(fields: list[str]) -> None:
        rows.extend([sep.join(fields)] if wrap is None else _pack(fields, wrap, sep))

    for item in accounts:
        peer = _clean(item.get("peer_id") or "?")
        age = _number(item.get("age_seconds"))
        tail = f"age {_compact_age(age + elapsed if age is not None else None)} [{_clean(item.get('state') or 'UNKNOWN')}]"
        if _account_failed(item):
            add([f"{peer} account ?", f"probe error: {_clean(item.get('probe_error') or 'unknown')}", tail])
            continue
        parts = [f"{peer} login {_account_login(item)}", f"plan {_account_plan(item)}"]
        if item.get("plan_changed"):
            parts.append("plan changed")
        expires = _number(item.get("login_token_expires_at"))
        status = item.get("login_expiry_status")
        shown = _utc_text(expires, "%Y-%m-%d") if status == "reported" and expires is not None else None
        parts.append("login token expiry " + (shown or ("n/a" if status == "n/a" else "?")))
        if item.get("endpoint_access") is not None:
            parts.append(f"credits endpoint {_clean(item.get('endpoint_access'))}")
        if item.get("auth_method"):
            parts.append(f"auth {_clean(item.get('auth_method'))}")
        if item.get("probe_error"):  # the last probe failed; the reading above is the last good one
            parts.append(f"probe error: {_clean(item.get('probe_error'))}")
        add([*parts, tail])
    return rows


def _compact_suggestions(profiles: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    choices: dict[str, list[tuple[bool, float, str]]] = {tier: [] for tier in ("eco", "normal", "quality", "top")}
    by_pool = {f"{row['peer']}:{row['pool']}": row for row in rows}
    for profile in profiles:
        name = _clean(profile.get("profile") or "")
        peer, _, suffix = name.partition(".")
        pools = cast(list[str], _obj(_obj(profile.get("fields")).get("pools")).get("value") or [])
        scores: list[float] = []
        fast = False
        for pool in pools:
            row = by_pool.get(f"{peer}:{pool}")
            if row is None or row["aged"] or row["state"] in {"?", "reset", "aged"}:
                break
            if row["state"] == "n/a":
                continue
            remaining = _number(row["remaining"])
            reset = _number(row["reset_seconds"])
            if remaining is None or remaining < .05 or reset is None or reset <= 0:
                break
            scores.append(remaining * 100 / max(reset / 86400, .2))
            fast = fast or row["state"] == "fast"
        else:
            if scores:
                tier = {"standard": "eco", "effort": "normal", "deepthink": "quality"}.get(suffix, "top")
                choices[tier].append((fast, min(scores), name))
    if not any(choices.values()):
        return "suggest: no eligible profiles; next: peerhub observation refresh"
    suggestions: list[str] = []
    for tier, items in choices.items():
        ranked = sorted(items, key=lambda item: (item[0], -item[1], item[2]))
        pick = ranked[0][2] if ranked else "-"
        peer = pick.partition(".")[0]
        alternative = next((name for _, _, name in ranked if name.partition(".")[0] != peer), None)
        if alternative is not None:
            pick += f"|{alternative}"
        suggestions.append(f"{tier}={pick}")
    return "suggest: " + " ".join(suggestions)


# ---- compact frame (#374, P21, #412): line 1 verdict, line 2 header, POOLS, the suggestion, RECENT, RUNNING, LEADS -----

COMPACT_MAX_LINES = 24
COMPACT_MAX_BYTES = 8192
RUNNING_CAP = 5
LEADS_CAP = 3
LEAD_LABEL_COLS = 16
RECENT_CAP = 5
POOLS_CAP = 8
REQUEST_CAP = 60
RECENT_EXPAND = 8
_WEEK = 7 * 86400
_ALERT_SOURCES = (("activity", "asks"), ("capacity", "quota"), ("accounts", "accounts"), ("facts", "facts"))
_SOURCE_PHRASES = {"not_selected": "not read", "error": "read failed", "truncated": "partial", "stale": "stale",
                   "unknown": "unknown"}  # unknown (#376 P14): no 7-day pool the provider reports has a number

# The rich look (P21). One accent per meaning, from the 256-colour palette in the semicolon SGR form (no truecolor
# branch): ok, warn, hot and bad for a state or a level, dim for structure, title for a section title.
RICH_SGR = {"ok": "38;5;78", "warn": "38;5;221", "hot": "38;5;208", "bad": "38;5;203", "dim": "38;5;245", "title": "1"}
PILL_SGR = {"OK": "1;38;5;16;48;5;78", "OK?": "1;38;5;16;48;5;221", "ATTN": "1;38;5;231;48;5;160"}
# Every glyph is one cell wide (as `_width` counts it) and has an ASCII form for a stream that cannot show it; a
# terminal that draws these as two cells uses the plain view. `none` is a bar or meter without a value.
RICH_GLYPHS = {
    True: {"rule": "─", "sep": "·", "full": "█", "empty": "░", "cfull": "▬", "cempty": "╌", "none": "─", "on": "■",
           "off": "□", "ok": "●", "fast": "▲", "low": "▼", "out": "×", "blocked": "×", "aged": "○", "run": "►",
           "gone": "×", "dot": "●", "up": "▲", "down": "▼"},
    False: {"rule": "-", "sep": "|", "full": "#", "empty": ".", "cfull": "=", "cempty": "-", "none": "-", "on": "#",
            "off": ".", "ok": "*", "fast": "^", "low": "v", "out": "x", "blocked": "x", "aged": "o", "run": ">",
            "gone": "x", "dot": "*", "up": "^", "down": "v"},
}
# `cfull` and `cempty` draw the bar of a child pool (`└─ F-7D`), so a child never reads as its parent: filled in the
# state colour, empty in SGR dim (`\x1b[2m`), and never a space, so 0% still shows.
CHILD_EMPTY_SGR = "2"
_CHILD_BAR = {unicode: str.maketrans({RICH_GLYPHS[unicode]["full"]: RICH_GLYPHS[unicode]["cfull"],
                                      RICH_GLYPHS[unicode]["empty"]: RICH_GLYPHS[unicode]["cempty"]})
              for unicode in (True, False)}  # the plain QUOTA table's `[BAR]` of a child row (`_bar_quota` draws `█░` / `#.`)
BAR_CELLS = 20  # the quota bar of a POOLS row; it shrinks to BAR_MIN cells, then goes, before a data column drops
BAR_MIN = 4
METER_CELLS = 5  # the context meter of a LEADS row


def _ascii(text: str, keep: str | None = None) -> str:
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


def _cell(value: object, unicode: bool, empty: str = "-", keep: str | None = None) -> str:
    text = _clean(value) if value is not None else ""
    text = text or empty
    return text if unicode else _ascii(text, keep)


def _short(text: str, cap: int, unicode: bool) -> str:
    """At most `cap` characters (not columns): the column fit comes later."""
    return text if len(text) <= cap else text[:cap - 1] + ("…" if unicode else "~")


def frame_alerts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The read model's alerts plus the agy token-use stop, which only the live monitor knows (before the low ones)."""
    alerts = _items(payload.get("alerts"))
    if "STOPPED" in str(payload.get("refresh_state") or ""):
        stop: dict[str, Any] = {"code": "a7", "kind": "agy stop", "subject": "next: --allow-agy-token-use to continue",
                                "age_s": None, "severity": "high"}
        at = next((i for i, item in enumerate(alerts) if item.get("severity") != "high"), len(alerts))
        alerts = [*alerts[:at], stop, *alerts[at:]]
    return alerts


def _alert_text(alert: dict[str, Any]) -> str:
    age = _number(alert.get("age_s"))
    text = f"{_clean(alert.get('kind') or '?')}: {_clean(alert.get('subject') or '?')}"
    return text if age is None else f"{text} {_compact_age(age)}"


_SOURCE_REASONS = {"not_selected": "not selected", "error": "read failed", "truncated": "partial (sample cut)",
                   "stale": "stale", "unknown": "unknown (no 7-day pool has a number)"}


def unread_sources(payload: dict[str, Any]) -> list[str]:
    """`source <id>: <reason>` for every alert source that was not read completely, then the hint (for --expand).
    A frame without `alert_sources` (a schema error, a hand-made payload) says so, as line 1 does."""
    raw: object = payload.get("alert_sources")
    if not isinstance(raw, Mapping):
        return ["source alerts: not read"]
    sources = _obj(cast(object, raw))
    given = _obj(sources.get("reasons"))  # the read model's own reason per source, when it has one (lane B fix 5)

    def reason(key: str) -> str:
        text = given.get(key)
        return _clean(text) if isinstance(text, str) and text.strip() else \
            _SOURCE_REASONS.get(str(sources.get(key)), "not read")

    rows = [f"source {key}: {reason(key)}" for key, _ in _ALERT_SOURCES if sources.get(key) != "read"]
    return rows + ([f"hint: {_clean(sources['hint'])}"] if rows and sources.get("hint") else [])


def _unread(payload: dict[str, Any]) -> str | None:
    """None when every alert source was selected and read completely; otherwise what was not, and the hint."""
    raw: object = payload.get("alert_sources")
    if not isinstance(raw, Mapping):
        return "alerts not read"
    sources = _obj(cast(object, raw))
    phrases: dict[str, list[str]] = {}
    for key, label in _ALERT_SOURCES:
        status = sources.get(key)
        if status != "read":
            phrases.setdefault(_SOURCE_PHRASES.get(str(status), "not read"), []).append(label)
    if not phrases:
        return None
    parts = [f"{', '.join(labels)} {phrase}" for phrase, labels in phrases.items()]
    if sources.get("hint"):
        parts.append(_clean(sources["hint"]))
    return "; ".join(parts)


def verdict_line(payload: dict[str, Any], width: int, unicode: bool, keep: str | None = None, sep: str = " | ") -> str:
    """Line 1: `OK`, `OK? (... not read)` or `ATTN n` with two named alerts; the right-hand fields drop first.
    `sep` joins the fields (the rich view uses ` · `)."""
    alerts = frame_alerts(payload)
    counts = _obj(_obj(_obj(_obj(payload.get("groups")).get("activity")).get("data")).get("counts"))
    right: list[str] = []
    if _number(counts.get("running")) is not None:
        right.append(f"{_count(counts['running'])} running")
    leads = _number(counts.get("leads"))
    if leads is not None:
        right.append(f"{_count(leads)} {'lead' if leads == 1 else 'leads'}")
    low = _obj(payload.get("min_quota"))
    if low:
        state = _clean(low.get("state") or "?")
        aged = " (aged)" if low.get("aged") is True and state != "aged" else ""  # A6: the pool's observation is stale
        right.append(f"min quota {_count(low.get('left_pct'))}% {_clean(low.get('pool') or '?')} {state}{aged}")
    if not unicode:
        right = [_ascii(item, keep) for item in right]
    if not alerts:
        unread = _unread(payload)
        head = "OK" if unread is None else f"OK? ({unread})"
        head = head if unicode else _ascii(head, keep)
        for fields in range(len(right), -1, -1):
            line = sep.join([head, *right[:fields]])
            if _width(line) <= width:
                return line
        return _fit(head, width, unicode)
    head = f"ATTN {len(alerts)}" + ("?" if _unread(payload) is not None else "")  # A7: a source was not read
    names = [_alert_text(alert) if unicode else _ascii(_alert_text(alert), keep) for alert in alerts[:2]]

    def compose(shown: int, fields: int) -> str:
        rest = len(alerts) - shown
        return sep.join([head, *names[:shown], *([f"+{rest}"] if rest else []), *right[:fields]])

    for shown in range(len(names), 0, -1):
        for fields in (range(len(right), -1, -1) if shown == len(names) else (0,)):
            line = compose(shown, fields)
            if _width(line) <= width:
                return line
    tail = f"{sep}+{len(alerts) - 1}" if len(alerts) > 1 else ""
    room = width - _width(f"{head}{sep}{tail}")
    if room >= 4:
        return f"{head}{sep}{_fit(names[0], room, unicode)}{tail}"
    return _fit(compose(0, 0), width, unicode)


def _decorate_verdict(verdict: str, sep: str, clock: str, width: int, paint: bool) -> str:
    """Line 1 of the rich look (P21): the status word as a pill, the separators dim, and the time at the right edge when
    two cells of room remain before it. `verdict` was fitted for two cells less: the pill's padding."""
    match = re.match(r"OK\??|ATTN \d+\??", verdict)
    head = match[0] if match else ""
    gap = width - _width(verdict) - (2 if head else 0) - _width(clock)
    timed = bool(clock) and gap >= 2
    if not paint:
        return verdict + (" " * (gap + (2 if head else 0)) + clock if timed else "")
    dim = RICH_SGR["dim"]
    pill = _paint(f" {head} ", PILL_SGR["ATTN" if head.startswith("ATTN") else head]) if head else ""
    return pill + verdict[len(head):].replace(sep, _paint(sep, dim)) + (" " * gap + _paint(clock, dim) if timed else "")


@dataclass
class CompactFrame:
    """The compact frame before the budget: each section as built, with the totals its overflow line discloses."""
    verdict: str
    header: list[str] = field(default_factory=lambda: list[str]())  # line 2, then the lines that belong to it
    running: list[str] | None = None  # [RUNNING header, *rows]; None when `activity` is not selected
    running_total: int = 0
    leads: list[str] | None = None  # [LEADS header, *rows]; None when there is no lead to show and the read was complete
    leads_total: int = 0
    recent: list[str] | None = None  # [RECENT header, *rows]; None when there is no ended ask
    recent_short: list[str] | None = None  # --expand: the rows of `recent` (entries of two lines) as one line each
    suggest: list[str] = field(default_factory=lambda: list[str]())
    pools: list[str] | None = None  # [POOLS header, *rows]; None when `capacity` is not selected
    pools_total: int = 0


def _overflow(hidden: int, noun: str, width: int, unicode: bool) -> str:
    """The overflow line, fitted to the width each time it is made: the hint gives way before the number."""
    for text in (f"+{hidden} {noun} (--expand)", f"+{hidden} {noun}", f"+{hidden}"):
        if _width(text) <= width:
            return text
    return _fit(f"+{hidden}", width, unicode)


def _fit_bytes(text: str, budget: int, unicode: bool) -> str:
    if len(text.encode("utf-8")) <= budget:
        return text
    mark = "…" if unicode else "~"
    room = budget - len(mark.encode("utf-8"))
    if room < 0:
        return ""
    kept: list[str] = []
    used = 0
    for char in _ANSI.sub("", text):
        size = len(char.encode("utf-8"))
        if used + size > room:
            break
        kept.append(char)
        used += size
    return "".join(kept) + mark


def fit_compact_frame(frame: CompactFrame, *, width: int, height: int | None, unicode: bool,
                      max_lines: int = COMPACT_MAX_LINES, max_bytes: int = COMPACT_MAX_BYTES,
                      keep: str | None = None) -> str:
    """The one owner of the compact frame's size (#374 H2) and of its order: line 1, line 2 with its lines, POOLS,
    the suggestion, RECENT, RUNNING, LEADS (#412: RECENT sits under the quota; --expand has the same order).

    Every line is fitted to `width`. While the frame is over the height bound (height - 1 lines), `max_lines` or
    `max_bytes` (the bytes of the lines as given, SGR codes included), lines go in this single order, rows first and from
    the bottom up, then the lower sections: LEADS rows (down to one), RUNNING rows after the third, RECENT rows after the
    third, POOLS rows after the fourth, the second suggestion line, RUNNING rows (down to one), RECENT rows (down to one:
    its floor), POOLS rows (down to one), the LEADS header, the remaining RUNNING rows and its header, the first
    suggestion line, what hangs on line 2 (the title stays). Only a terminal too small for the top of the frame gets
    further: the POOLS rows, RECENT, the POOLS header, line 2. Line 1 is the fallback: alone, it is fitted to the byte
    ceiling; a budget of 0 prints nothing. An overflow line is refitted whenever its
    count changes; the removal that creates one removes two rows. A row step with a floor of one or more never leaves a
    header without a row: when those two rows are the last ones, the step does nothing, and the rows leave with their
    header in its own step (#376 A-3).
    """
    limit = max_lines if height is None else min(max_lines, max(0, height - 1))
    if limit <= 0 or width <= 0:
        return ""

    def fit(line: str) -> str:
        return _fit(line if unicode else _ascii(line.replace(" · ", " | ").replace("·", "|"), keep), width, unicode,
                    painted=True, indent=True)

    def fitted(lines: list[str] | None) -> list[str] | None:
        return None if lines is None else [fit(line) for line in lines]

    verdict = fit(frame.verdict)
    header = [fit(line) for line in frame.header]
    suggest = [fit(line) for line in frame.suggest]
    parts: dict[str, list[str] | None] = {"running": fitted(frame.running), "leads": fitted(frame.leads),
                                          "recent": fitted(frame.recent), "pools": fitted(frame.pools)}
    totals = {"running": frame.running_total, "leads": frame.leads_total, "pools": frame.pools_total}
    nouns = {"running": "more", "leads": "leads", "pools": "pools"}

    def section(key: str) -> list[str]:
        lines = parts[key]
        if lines is None:
            return []
        rows = len(lines) - 1
        hidden = totals.get(key, 0) - rows
        return [*lines, _overflow(hidden, nouns[key], width, unicode)] if key in totals and hidden > 0 and rows > 0 else lines

    def assemble() -> list[str]:
        return [verdict, *header, *section("pools"), *suggest, *section("recent"), *section("running"), *section("leads")]

    def pop_rows(key: str, floor: int) -> bool:
        lines = parts[key]
        if lines is None or len(lines) - 1 <= floor:
            return False
        total = totals.get(key, 0)
        rows = len(lines) - 1
        created = total - rows <= 0 < total  # this removal makes the overflow line
        if created and floor >= 1 and rows <= 2:  # A-3: K5 would leave the header without a row; do nothing
            return False
        lines.pop()
        if created and len(lines) > 1:  # even below the floor: one row for one overflow line would save nothing (K5)
            lines.pop()
        return True

    def drop(key: str) -> bool:
        if parts[key] is None:
            return False
        parts[key] = None
        return True

    def pop_line(lines: list[str] | None, floor: int) -> bool:
        if lines is None or len(lines) <= floor:
            return False
        lines.pop()
        return True

    steps: list[Callable[[], bool]] = [  # rows shrink first, from the bottom up, then the lower sections go
        lambda: pop_rows("leads", 1),
        lambda: pop_rows("running", 3),
        lambda: pop_line(parts["recent"], 4),  # the header and three rows
        lambda: pop_rows("pools", 4),
        lambda: pop_line(suggest, 1),
        lambda: pop_rows("running", 1),
        lambda: pop_line(parts["recent"], 2),  # the header and one row: the floor of RECENT
        lambda: pop_rows("pools", 1),
        lambda: drop("leads"),
        lambda: pop_rows("running", 0),
        lambda: drop("running"),
        lambda: pop_line(suggest, 0),
        lambda: pop_line(header, 1),  # what hangs on the title line; the title stays
        # only a terminal too small for the top of the frame gets to these
        lambda: pop_rows("pools", 0),
        lambda: drop("recent"),
        lambda: drop("pools"),
        lambda: pop_line(header, 0),
    ]
    while True:
        lines = assemble()
        if len(lines) <= limit and sum(len(line.encode("utf-8")) + 1 for line in lines) <= max_bytes:
            break
        if not any(step() for step in steps):
            lines = [_fit_bytes(verdict, max_bytes - 1, unicode)]
            break
    return "\n".join(lines) + "\n"


def _columns(rows: list[list[str]], width: int, elastic: int | None, floor: int = 12) -> tuple[int, list[int]]:
    """How many leading columns a table shows, and their sizes: whole columns drop from the right, and the elastic
    column (text) shrinks first, down to `floor` cells."""
    natural = [max(_width(row[i]) for row in rows) for i in range(len(rows[0]))] if rows else []
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


def _table(rows: list[list[str]], width: int, unicode: bool, *, right: Sequence[int] = (), elastic: int | None = None,
           floor: int = 12, paint: Callable[[int, int, str], str] | None = None) -> list[str]:
    """One column set for all rows (see `_columns`). `paint(row, column, cell)` colours a cell after it was fitted, so
    colour never changes a width (P21 C4)."""
    shown, sizes = _columns(rows, width, elastic, floor)
    lines: list[str] = []
    for index, row in enumerate(rows):
        cells: list[str] = []
        for i in range(shown):
            text = _fit(row[i], sizes[i], unicode) if i == elastic and _width(row[i]) > sizes[i] else row[i]
            pad = " " * max(0, sizes[i] - _width(text))
            if paint is not None and text:
                text = paint(index, i, text)
            cells.append(pad + text if i in right else text + pad)
        lines.append("  ".join(cells).rstrip())
    return lines


def _head_rule(head: str, width: int, unicode: bool, paint: bool) -> str:
    """The rule that replaces a section head in the rich view (P21 C2): the same content on the same line. The first
    two words are the title (`POOLS 9`); a ` | ` of the head is the view's separator. A head that would be cut to
    make room for the line keeps its content and loses the line."""
    ruled = _width(head) <= width - 3
    head = _fit(head, width, unicode)
    match = re.match(r"\S+ \S+", head)
    title = match[0] if match else head
    sep = f" {RICH_GLYPHS[unicode]['sep']} "
    rest = head[len(title):].replace(" | ", _paint(sep, RICH_SGR["dim"]) if paint else sep)
    if ruled:
        return _rule(title, rest, width, unicode, paint)
    return (_paint(title, RICH_SGR["title"]) if paint else title) + rest


def _flag(row: dict[str, Any]) -> str:
    if row.get("state") == "overdue":
        return "!overdue"
    if row.get("alive") is False:
        return "!gone"
    return "?" if row.get("alive") is None else ""


def _is_open(row: dict[str, Any]) -> bool:
    """`run`, or `overdue` without an end: the read model ends an overdue row once the alert window has passed."""
    return row.get("state") == "run" or (row.get("state") == "overdue" and row.get("ended_at") is None)


def _ask_split(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Running, ended and superseded rows, each in the order the read model gives."""
    asks = _items(data.get("asks"))
    main = [row for row in asks if not row.get("superseded")]
    return ([row for row in main if _is_open(row)], [row for row in main if not _is_open(row)],
            [row for row in asks if row.get("superseded")])


def _running_head(data: dict[str, Any], running: list[dict[str, Any]], unicode: bool,
                  keep: str | None = None) -> tuple[str, int]:
    counts = _obj(data.get("counts"))
    number = _number(counts.get("running"))
    total = int(number) if number is not None and number >= 0 else len(running)
    by_peer = _obj(counts.get("by_peer"))
    peers = " ".join(f"{_cell(peer, unicode, '-', keep)} {_count(n)}" for peer, n in by_peer.items())
    return f"RUNNING {total}" + (f" | {peers}" if peers and total else ""), total


def _recent_text(row: dict[str, Any]) -> object:
    if row.get("reply_line"):
        return row["reply_line"]
    if row.get("note"):
        return f"({_clean(row['note'])})"
    return "-" if row.get("state") == "ok" else "(no reply)"


_RUN_MARKS = {"": ("run", "", "ok"), "!gone": ("gone", " gone", "bad"), "!overdue": ("", "! overdue", "warn"),
              "?": ("", "?", "warn")}  # a RUNNING row's flag: its glyph, the word beside it, its colour
_RECENT_COLOURS = {"ok": "ok", "unsure": "warn", "overdue": "warn"}  # every other ended state is bad


def _column_painter(column: int, colours: Sequence[str | None]) -> Callable[[int, int, str], str]:
    """Paints one column of a table: each row's cell in that row's colour."""
    def paint(index: int, col: int, text: str) -> str:
        colour = colours[index] if col == column else None
        return _paint(text, RICH_SGR[colour]) if colour else text
    return paint


def _activity_sections(groups: dict[str, Any], width: int, unicode: bool, keep: str | None = None, *,
                       rich: bool = False, paint: bool = False) -> tuple[list[str], int, list[str] | None]:
    """RUNNING (header and at most five rows, the total before the cap) and RECENT (`_recent_compact`: at most RECENT_CAP
    finished asks, newest first, or None). The rich view (P21) draws each head as a rule, starts a RUNNING row with its
    state glyph (and the word of a flag) and a RECENT row with a state dot; such a cell is used only when it costs no
    column."""
    def head_line(head: str) -> str:
        return _head_rule(head, width, unicode, paint) if rich else head

    group = _obj(groups.get("activity"))
    if not isinstance(group.get("data"), Mapping):
        why = "read failed" if _clean(group.get("status") or "").upper() in {"ERROR", "FAILED"} else "not read"
        return [head_line(f"RUNNING ? | activity {why}")], 0, None
    data = _obj(group.get("data"))
    glyphs = RICH_GLYPHS[unicode]

    def cell(value: object) -> str:  # text cells keep what the plain view's encoding can show (A5)
        return _cell(value, unicode, "-", keep)

    running = _ask_split(data)[0]
    head, total = _running_head(data, running, unicode, keep)
    shown = running[:RUNNING_CAP]
    flags = [_flag(row) for row in shown]
    flagged = any(flags)
    table = [[*([flag] if flagged else []), _compact_age(_number(row.get("elapsed_s"))),
              cell(row.get("profile") or row.get("peer")), cell(_obj(row.get("lead")).get("label")),
              _short(cell(row.get("ask_line")), REQUEST_CAP, unicode), cell(row.get("name"))]
             for row, flag in zip(shown, flags)]
    first = 1 if flagged else 0
    painter = None
    if rich and table:
        marks = [_RUN_MARKS[flag] for flag in flags]
        marked = [[glyphs.get(glyph, "") + word, *row[first:]] for (glyph, word, _), row in zip(marks, table)]
        if _columns(marked, width - 1, 4)[0] - 1 >= _columns(table, width - 1, first + 3)[0] - first:
            table, first = marked, 1
            painter = _column_painter(0, [colour for _, _, colour in marks]) if paint else None
    rows = [" " + line for line in _table(table, width - 1, unicode, right=(first,), elastic=first + 3, paint=painter)]
    recent: list[str] | None = None
    ended = _recent_ended(data)
    if ended:
        recent = [head_line(_recent_head(data, ended)), *_recent_compact(ended, width, unicode, keep, rich=rich, paint=paint)]
    return [head_line(head), *rows], total, recent


def _leads_header(data: dict[str, Any], leads: list[dict[str, Any]]) -> str:
    """Count the displayed entries and matched sessions separately; a found session need not be confirmed alive.

    Keep read warnings before the session statistic so they survive a narrow frame. With no rows after an incomplete
    read the entry count stays `?`; an older payload with no read evidence must not claim zero sessions were found.
    """
    count = len(leads)
    read = _obj(data.get("leads_read"))
    state = read.get("state")
    notes = [f"{_count(read.get(key))} {words}" for key, words in (("unreadable", "unreadable"), ("not_read", "not read"))
             if (_number(read.get(key)) or 0) > 0]
    note = (" | partial" + (f" ({', '.join(notes)})" if notes else "") if state == "partial"
            else " | read failed" if state == "error" else "")
    if note and count == 0:
        return f"LEADS ?{note}"
    header = f"LEADS {count} {'entry' if count == 1 else 'entries'}{note}"
    sessions = sum(bool(_obj(lead.get("session"))) for lead in leads)
    if sessions or state in {"read", "partial", "error"}:
        header += f" | {sessions} {'session' if sessions == 1 else 'sessions'} found"
    return header


def _ctx_cell(item: dict[str, Any]) -> str:
    pct, tokens = _number(item.get("context_pct")), _number(item.get("context_tokens"))
    if pct is not None:
        return f"ctx {int(pct)}%" + ("!" if pct >= _LEAD_MARK_PCT else "")
    if tokens is not None and tokens >= 0:
        return f"ctx {int(tokens) // 1000}k" if tokens >= 1000 else f"ctx {int(tokens)}"
    return "ctx ?"


def _lead_cells(lead: dict[str, Any], now: float, unicode: bool, keep: str | None) -> list[str]:
    """label, state with the flag, age of the last activity, ctx, running asks, model, title (empty when unknown)."""
    label = _short(_cell(lead.get("label"), unicode, "?", keep), LEAD_LABEL_COLS, unicode)
    run = f"{_count(lead.get('running'))} run"
    item = _obj(lead.get("session"))
    if not item:
        return [label, "-", "-", "-", run, "-", ""]
    known = _cell(item.get("state"), unicode, "", keep)
    state = ("?" if item.get("alive") is None else "") + known if known else "?"  # the flag only on a known state
    active = _number(item.get("active_at"))
    model = re.sub(r"^claude-", "", _clean(item.get("model") or ""))
    return [label, state, "-" if active is None else _compact_age(now - active), _ctx_cell(item), run,
            _short(_cell(model or None, unicode, "-", keep), 16, unicode), _cell(item.get("title"), unicode, "", keep)]


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


def _leads_section(groups: dict[str, Any], now: float, width: int, unicode: bool, keep: str | None = None, *,
                   rich: bool = False, paint: bool = False) -> tuple[list[str] | None, int]:
    """LEADS (header and at most LEADS_CAP rows of the leads with a session or a running ask, the total before the cap),
    or None when there is no such lead and the lead sessions were read completely. The rich view (P21) draws the head
    as a rule and the context as a meter, unless the meter would cost a column."""
    data = _obj(_obj(groups.get("activity")).get("data"))
    leads = [lead for lead in _items(data.get("leads")) if lead.get("session") or (_number(lead.get("running")) or 0) > 0]
    header = _leads_header(data, leads)
    if not leads and _obj(data.get("leads_read")).get("state") not in {"partial", "error"}:
        return None, 0
    table = [_lead_cells(lead, now, unicode, keep) for lead in leads[:LEADS_CAP]]
    titled = any(row[-1] for row in table)
    table = [row if titled else row[:-1] for row in table]
    elastic = 6 if titled else None
    pcts = [_number(_obj(lead.get("session")).get("context_pct")) for lead in leads[:LEADS_CAP]]
    metered = [[*row[:3], _ctx_meter(row[3], pct, unicode), *row[4:]] for row, pct in zip(table, pcts)]
    meters = rich and _columns(metered, width - 1, elastic)[0] >= _columns(table, width - 1, elastic)[0]

    def painter(index: int, col: int, text: str) -> str:
        if col != 3 or not text.startswith("ctx "):
            return text
        pct = pcts[index]
        if pct is None:  # no value: the meter is structure
            meter, _, value = text[4:].partition(" ")
            return f"ctx {_paint(meter, RICH_SGR['dim'])} {value}"
        return f"ctx {_paint(text[4:], RICH_SGR['bad'])}" if pct >= _LEAD_MARK_PCT else text

    rows = _table(metered if meters else table, width - 1, unicode, right=(2,), elastic=elastic,
                  paint=painter if meters and paint else None)
    return [_head_rule(header, width, unicode, paint) if rich else header, *(" " + line for line in rows)], len(leads)


def _has_value(row: dict[str, Any]) -> bool:
    return row["state"] not in {"n/a", "?", "reset"} and _number(row["remaining"]) is not None


def _min_quota(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Line 1's `min quota` from the rows POOLS draws (P9): the lowest 7-day pool that has a percent at the time of
    this redraw, with the state the table shows. The rule is monitor_read_model.min_quota's."""
    weekly = [(round(float(row["remaining"]) * 100), f"{row['peer']} {row['pool']}", row) for row in rows
              if str(row["pool"]).rpartition("-")[1:] == ("-", "7D") and _has_value(row)]
    if not weekly:
        return None
    left, name, row = min(weekly, key=lambda item: item[:2])
    return {"pool": name, "left_pct": left, "state": row["state"], "aged": bool(row["aged"] or row["stale"])}


def _binds(child: dict[str, Any], parent: dict[str, Any]) -> bool:
    """A 5-hour sub-row is shown when it has less left than its 7-day pool, or is low or out."""
    if child["state"] in {"low", "out"}:
        return True
    return (_has_value(child) and _has_value(parent)
            and float(child["remaining"]) < float(parent["remaining"]))


def _scoped_weekly_parents(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Fable's scoped weekly window depends on the all-models window when configured consumers confirm it.

    As in the detailed quota tree, require a strict consumer-set inclusion. Equal sets, duplicate display names
    and different account identities cannot establish a parent. This is presentation only, never a quota rewrite.
    """
    parents: dict[int, dict[str, Any]] = {}
    for peer in {row["peer"] for row in rows}:
        weekly = [row for row in rows if row["peer"] == peer and row["pool"] in {"C-7D", "F-7D"}
                  and not row.get("retired")]
        common = [row for row in weekly if row["pool"] == "C-7D"]
        scoped = [row for row in weekly if row["pool"] == "F-7D"]
        if len(common) != 1 or len(scoped) != 1:
            continue
        parent, child = common[0], scoped[0]
        members = set(str(child.get("profiles", "")).split(",")) - {"unmapped", ""}
        all_members = set(str(parent.get("profiles", "")).split(",")) - {"unmapped", ""}
        parent_ref, child_ref = str(parent.get("pool_ref") or ""), str(child.get("pool_ref") or "")
        if (members and members < all_members and parent_ref and child_ref
                and parent_ref.rpartition(":")[0] == child_ref.rpartition(":")[0]):
            parents[id(child)] = parent
    return parents


def _ordered_pools(rows: list[dict[str, Any]], *, every: bool = False) -> list[tuple[dict[str, Any], int]]:
    """Classes out, low, binding (a 7-day pool whose 5-hour sub-row is shown), fast, the rest; then peer and pool.
    `every` (the rich --expand frame, P23) also lists the 5-hour rows that do not bind, under their 7-day pool; the
    order of the other rows is unchanged, because a short window that does not bind never decides a class. A scoped
    Fable weekly row always follows its configured all-models parent; each tuple carries its indentation depth."""
    scoped = _scoped_weekly_parents(rows)
    weekly: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row["family"] != "?" and _parse_window(str(row["pool"]))[0] == _WEEK:
            weekly.setdefault((str(row["peer"]), str(row["family"])), row)
    tops: list[dict[str, Any]] = []
    children: dict[int, list[dict[str, Any]]] = {}  # the sub-rows that bind: they decide the class of their pool
    listed: dict[int, list[dict[str, Any]]] = {}  # the sub-rows the frame lists
    for row in rows:
        parent = scoped.get(id(row)) or weekly.get((str(row["peer"]), str(row["family"])))
        if parent is None or parent is row or (id(row) not in scoped and _parse_window(str(row["pool"]))[0] >= _WEEK):
            tops.append(row)
            continue
        binds = _binds(row, parent)
        if binds:
            children.setdefault(id(parent), []).append(row)
        if binds or every or id(row) in scoped:
            listed.setdefault(id(parent), []).append(row)
    for children_rows in listed.values():
        children_rows.sort(key=lambda row: (-_parse_window(str(row["pool"]))[0], str(row["pool"])))

    def rank(row: dict[str, Any]) -> tuple[int, tuple[str, str, int, str]]:
        # The most severe class of the pool and its binding short rows, including a scoped weekly child's class.
        states = {row["state"], *(child["state"] for child in children.get(id(row), ())),
                  *(child["state"] for child in listed.get(id(row), ()) if id(child) in scoped)}
        cls = (0 if "out" in states else 1 if "low" in states else 2 if id(row) in children
               else 3 if "fast" in states else 4)
        cls = min([cls, *(rank(child)[0] for child in listed.get(id(row), ()) if id(child) in scoped)])
        return cls, _pool_order(str(row["peer"]), str(row["pool"]))

    ordered: list[tuple[dict[str, Any], int]] = []

    def append_tree(row: dict[str, Any], depth: int) -> None:
        ordered.append((row, depth))
        for child in listed.get(id(row), ()):
            append_tree(child, depth + 1)

    for top in sorted(tops, key=rank):
        append_tree(top, 0)
    return ordered


def _pool_colour(row: dict[str, Any]) -> str | None:
    """A POOLS row's one colour in the rich view (P21 C5). `_quota_color` decides first, so evidence that is aged,
    unknown, reset or n/a is dim and a threat or an exhausted pool is bad; a valid pool takes the colour of what is
    left (ok above 50%, warn above 25%, hot above 10%, bad at 10% or less), and never a milder one than its state's."""
    remaining = _number(row["remaining"])
    code = "2" if row["state"] == "n/a" else _quota_color(str(row["color_state"]), remaining,
                                                           aged=bool(row["aged"] or row["stale"]))
    if code == "2":
        return "dim"
    if code in {"1;31", "31"}:
        return "bad"
    order = ("ok", "warn", "hot", "bad")
    state = "warn" if code == "33" else "ok"
    if remaining is None:
        return state if code else None
    left = "ok" if remaining > .50 else "warn" if remaining > .25 else "hot" if remaining > .10 else "bad"
    return max(state, left, key=order.index)


def _pool_bar(row: dict[str, Any], cells: int, unicode: bool, child: bool = False) -> str:
    """What is left as a bar; a non-zero remainder shows at least one cell, and a row without a value shows none. A child
    pool's bar has its own glyphs (`▬╌`, `=-`), a parent's stays `█░` (`#.`)."""
    glyphs = RICH_GLYPHS[unicode]
    if not _has_value(row):
        return glyphs["none"] * cells
    remaining = float(row["remaining"])
    filled = max(int(remaining * cells + 0.5), 1 if remaining > 0 else 0)
    full, empty = (glyphs["cfull"], glyphs["cempty"]) if child else (glyphs["full"], glyphs["empty"])
    return full * filled + empty * (cells - filled)


def _pool_section(rows: list[dict[str, Any]], width: int, unicode: bool, keep: str | None = None, *,
                  rich: bool = False, paint: bool = False, every: bool = False) -> tuple[list[str], int]:
    """`POOLS n` with the column titles, then at most eight rows: POOL STATE LEFT RESET PACE FAMILY. `every` (the
    rich --expand frame, P23): every pool, the 5-hour rows that do not bind included, and no cap. An n/a pool (#400) is
    never a row and is not counted; the QUOTA details name it.

    The rich view (P21) draws the head as a rule, puts a state glyph before the state word and a bar of what is left
    after it (POOL STATE bar LEFT ...). A decoration never costs a data column: the glyph stays only when the table
    shows the columns of the plain view, and the bar takes the cells left over when every cell is whole (at most
    BAR_CELLS; fewer than BAR_MIN: no bar), so it shrinks and goes before a column drops or the FAMILY note is cut."""
    ordered = _ordered_pools([row for row in rows if row["state"] != "n/a"], every=every)
    shown = ordered if every else ordered[:POOLS_CAP]
    glyphs = RICH_GLYPHS[unicode]
    head = f"POOLS {len(ordered)}"
    table = [[head, "STATE", "LEFT", "RESET", "PACE", "FAMILY"]]
    for index, (row, depth) in enumerate(shown):
        peer, pool = _cell(row["peer"], unicode, "-", keep), _cell(row["pool"], unicode, "-", keep)
        prefix = ""
        for level in range(1, depth + 1):
            sibling = next((d == level for _, d in shown[index + 1:] if d <= level), False)
            if level == depth:
                prefix += ("├─ " if sibling else "└─ ") if unicode else ("|- " if sibling else "`- ")
            else:
                prefix += ("│  " if unicode else "|  ") if sibling else "   "
        name = f"{' ' * _width(peer)} {prefix}{pool}" if depth else f"{peer} {pool}"
        family = str(row["meaning"])
        if row.get("depends_on"):
            own = f" (own {row['own_left']})" if row.get("own_left") else ""  # the pool's own LEFT, when the parent is lower
            family += (f"; {row['depends_on']} out{own}" if row["state"] == "blocked" else
                       f"; limited by {row['depends_on']}{own}" if row.get("limited") else f"; needs {row['depends_on']}")
        table.append([" " + name, str(row["state"]), str(row["left"]), str(row["resets"]),
                      _pace_glyph(str(row["pace"]), unicode), _cell(family, unicode, "-", keep)])
    # Whole columns drop from the right (FAMILY first). FAMILY shrinks only down to its widest family name, so a long
    # dependency note is cut (with the cut mark) rather than taking the whole column away; no other cell is ever cut.
    floor = max([_width("FAMILY"), *(_width(_cell(row["meaning"], unicode, "-", keep)) for row, _ in shown)])
    if not rich:
        return _table(table, width, unicode, right=(2, 4), elastic=5, floor=floor), len(ordered)
    plain_shown = _columns(table, width, 5, floor)[0]
    ruled = [[f"{glyphs['rule'] * 2} {head}", *table[0][1:]], *table[1:]]  # the rule's first cells, so titles stay aligned
    states = [glyphs.get(str(row["state"]), glyphs["aged"]) for row, _ in shown]
    glyphed = [ruled[0], *([cells[0], f"{glyph} {cells[1]}", *cells[2:]] for glyph, cells in zip(states, ruled[1:]))]
    chosen = next((cells for cells in (glyphed, ruled) if _columns(cells, width, 5, floor)[0] >= plain_shown), table)
    names = ["POOL", "STATE", "LEFT", "RESET", "PACE", "FAMILY"]
    right, elastic = (2, 4), 5
    room = width - sum(max(_width(cells[i]) for cells in chosen) for i in range(6)) - 2 * 6
    if chosen is not table and min(BAR_CELLS, room) >= BAR_MIN:
        bars = ["", *(_pool_bar(row, min(BAR_CELLS, room), unicode, child=depth > 0) for row, depth in shown)]
        chosen = [[*cells[:2], bar, *cells[2:]] for bar, cells in zip(bars, chosen)]
        names.insert(2, "BAR")
        right, elastic = (3, 5), 6
    colours = [None, *(_pool_colour(row) for row, _ in shown)]
    kids = [False, *(depth > 0 and _has_value(row) for row, depth in shown)]  # a child bar with a value: two spans

    def painter(index: int, col: int, text: str) -> str:
        colour = colours[index]
        if index == 0:
            return text
        if names[col] == "FAMILY":
            return _paint(text, RICH_SGR["dim"])
        if names[col] == "BAR" and kids[index]:  # filled in the state colour, empty in SGR dim
            filled = len(text) - len(text.lstrip(glyphs["cfull"]))
            return "".join(_paint(part, code) for part, code in ((text[:filled], RICH_SGR[colour] if colour else None),
                                                                 (text[filled:], CHILD_EMPTY_SGR)) if part)
        if colour and names[col] in {"STATE", "BAR"}:
            return _paint(text, RICH_SGR[colour])
        if colour and names[col] == "PACE" and text[0] in {glyphs["up"], glyphs["down"]}:
            return _paint(text[0], RICH_SGR[colour]) + text[1:]  # only the direction glyph, never the number
        return text

    lines = _table(chosen, width, unicode, right=right, elastic=elastic, floor=floor, paint=painter if paint else None)
    lines[0] = _head_rule(lines[0] if chosen is table else lines[0][3:], width, unicode, paint)
    return lines, len(ordered)


def _suggest_lines(text: str, width: int, unicode: bool) -> list[str]:
    lines = _compact_wrap(text, width)
    return lines if len(lines) <= 2 else [lines[0], _fit(" ".join(lines[1:]), width, unicode)]


def _frame_consumers(payload: dict[str, Any]) -> dict[str, list[str]]:
    consumers: dict[str, list[str]] = {}
    for profile in _items(payload.get("configured_profiles")):
        name = _clean(profile.get("profile") or "?")
        peer, _, suffix = name.partition(".")
        for raw_pool in cast(list[object], _obj(_obj(profile.get("fields")).get("pools")).get("value") or []):
            consumers.setdefault(f"{peer}:{_clean(raw_pool)}", []).append(suffix)
    return consumers


def _frame_pools(payload: dict[str, Any], consumers: dict[str, list[str]]
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(the frame's observations, its quota pools, their rows): each pool as it is at the time of this redraw (the read
    time plus the display's elapsed time). The human observations are used when the payload has them."""
    read_at = _number(payload.get("read_at"))
    elapsed = _number(payload.get("display_elapsed_seconds")) or 0
    observations = (_items(payload.get("_human_observations")) if "_human_observations" in payload else
                    _items(_obj(_obj(_obj(payload.get("groups")).get("capacity")).get("data")).get("observations")))
    quota = [item for item in observations if item.get("kind", "quota") == "quota" and item.get("pool_ref")]
    rows = [_compact_pool(item, read_at if read_at is not None else 0, elapsed, consumers) for item in quota]
    rows.sort(key=lambda row: _pool_order(str(row["peer"]), str(row["pool"])))
    parents = _scoped_weekly_parents(rows)
    for row in rows:
        parent = parents.get(id(row))
        if parent is None:
            continue
        row["depends_on"] = parent["pool"]
        _limit_by_parent(row, parent)
    return observations, quota, rows


_SEVERITY = {"ok": 0, "fast": 1, "low": 2, "out": 3}  # of two fresh pools; the worse state wins


def _limit_by_parent(row: dict[str, Any], parent: dict[str, Any]) -> None:
    """#394: a scoped weekly pool (cc F-7D) is limited by its parent (C-7D). With both pools fresh and before their reset:
    LEFT = the lower of the two (the pool's own value stays as `own_left`); when the parent has less left, the reset is the
    nearer of the two and PACE is the parent's; the state is the worse of the two (a fresh `out` parent is `blocked`).
    The same rule as quota_projection.limit_by_parent (this renderer imports no extension code). An aged, unknown, reset
    or missing parent never limits the child, and the payload is never rewritten (these are display rows)."""
    def current(item: dict[str, Any]) -> bool:
        return _has_value(item) and not item["needs_refresh"] and not item["stale"]

    if not (current(row) and current(parent) and (_number(parent["reset_seconds"]) or 0) > 0):
        return
    parent_lower = float(parent["remaining"]) < float(row["remaining"])
    if parent_lower:
        resets = [value for value in (_number(row["reset_seconds"]), _number(parent["reset_seconds"])) if value is not None]
        reset = min(resets)
        row.update(own_left=row["left"], remaining=parent["remaining"], left=parent["left"], pace=parent["pace"],
                   reset_seconds=reset, resets="passed" if reset <= 0 else _compact_age(reset))
    worse = _SEVERITY.get(parent["state"], -1) > _SEVERITY.get(row["state"], -1)
    if worse:
        row.update(state="blocked" if parent["state"] == "out" else parent["state"], color_state=parent["color_state"])
    row["limited"] = parent_lower or worse


def _with_min_quota(payload: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """P9, the one rule of both frames: the read model's `min_quota` is as of the read, and a redraw ages the pools,
    so line 1 takes its pool from the aged rows. A payload without the human observations cannot age a stale pool
    (its last value is not public): it keeps the read model's `min_quota`."""
    return {**payload, "min_quota": _min_quota(rows)} if "_human_observations" in payload else payload


def _render_compact_frame(payload: dict[str, Any], *, view: str, color: bool, unicode: bool,
                          width: int, height: int | None, keep: str | None = None) -> str:
    width = max(1, min(100, width) - 1)  # leave the terminal's autowrap column unused
    frame = _compact_frame(payload, view=view, color=color, unicode=unicode, width=width, height=height, keep=keep)
    return fit_compact_frame(frame, width=width, height=height, unicode=unicode, keep=keep)


def _compact_frame(payload: dict[str, Any], *, view: str, color: bool, unicode: bool, width: int,
                   height: int | None, keep: str | None = None, expand: bool = False) -> CompactFrame:
    """The compact frame before the budget, for lines of `width` cells.

    `expand` (P23) builds the top of the rich --expand frame, which starts as the compact frame does: line 1, line 2
    with its lines, POOLS and the suggestion. There POOLS lists every pool and the suggestion wraps without a cap;
    line 2 does not say that fields are hidden; and the store path, the --db note and the activity are left to the
    lines and sections that follow the top."""
    rich = view == "rich"  # P21: the rich look; `unicode` picks its glyphs and `paint` adds the colour
    paint = color and rich and "NO_COLOR" not in os.environ
    sep = f" {RICH_GLYPHS[unicode]['sep']} " if rich else " | "
    groups = _obj(payload.get("groups"))
    selected = payload.get("selected_groups")
    selected_ids = set(cast(list[str], selected)) if isinstance(selected, list) else set(groups)
    read_at = _number(payload.get("read_at"))
    now = read_at if read_at is not None else 0
    elapsed = _number(payload.get("display_elapsed_seconds")) or 0
    configured_profiles = _items(payload.get("configured_profiles"))
    consumers = _frame_consumers(payload)
    observations, quota, rows = _frame_pools(payload, consumers)
    payload = _with_min_quota(payload, rows)
    store = _obj(payload.get("store_selection"))
    source = _clean(store.get("source") or "unknown")
    try:
        stamp = datetime.fromtimestamp(now + elapsed, timezone.utc).strftime("%Y-%m-%d %H:%MZ") if read_at is not None else "time unknown"
    except (ValueError, OverflowError, OSError):
        stamp = "time unknown"
    ages = [_number(item.get("age_seconds")) for item in quota if not item.get("display_retired")]
    known_ages = [age + elapsed for age in ages if age is not None]
    age_label = _compact_age(max(known_ages) if known_ages else None)
    title = f"PeerHub Monitor {stamp} | DB:{source} | quota age {age_label}"
    refresh_state = _clean(payload.get("refresh_state") or "")
    next_refresh = _number(payload.get("next_refresh_in"))
    if refresh_state or next_refresh is not None:
        title, cut = _fit_live_title(refresh_state, next_refresh, stamp, [f"DB:{source}", f"quota age {age_label}"],
                                     width, unicode, inline_note=False)
    else:
        cut = _width(title) > width
        title = _fit_fields(title, width, unicode, inline_note=False)
    title = title.replace(" | ", sep)  # the rich view's separator; line 2 is structure, so its colour is dim
    # Fields dropped from line 2 are disclosed on a line of their own, so a narrow terminal keeps the timestamp.
    header = [_paint(title, RICH_SGR["dim"]) if paint else title,
              *([f"Fields hidden{sep}--expand"] if cut and not expand else [])]
    errors = _obj(payload.get("errors"))
    nearest = payload.get("_nearest_store_path")
    same_store = bool(nearest and store.get("path") and os.path.normcase(os.path.abspath(str(nearest))) == os.path.normcase(os.path.abspath(str(store["path"]))))
    read_errors = any(value != "truncated" for value in errors.values())
    if ((not same_store or payload.get("status") in {"FAILED", "ERROR"} or read_errors) and store.get("path")
            and not expand):  # --expand names the store near the end of the frame (`_expanded_frame`)
        header.extend(_compact_wrap(f"DB: {_clean(store['path'])}", width))
    if payload.get("_store_note") and not expand:  # #338: --db overrides a PEERHUB_DB that points elsewhere
        header.extend(_compact_wrap(str(payload["_store_note"]), width))
    header.extend(_fit(f"Warning: {warning}", width, unicode)
                  for warning in _safe_refresh_warning_labels(payload.get("refresh_warnings")))
    if selected_ids & {"capacity", "peers"}:  # #328: a peer's newest quota probe failed before it named a pool
        for peer in sorted({str(item.get("peer_id") or "?") for item in observations if item.get("kind", "quota") == "quota"}):
            probes = [item for item in observations if str(item.get("peer_id") or "?") == peer and item.get("kind", "quota") == "quota"]
            latest = max(probes, key=lambda item: _number(item.get("effective_at_us")) or
                         (now - (_number(item.get("age_seconds")) or 0)) * 1_000_000)
            if not latest.get("pool_ref") and (latest.get("state") in {"ERROR", "UNAVAILABLE", "UNKNOWN"}
                                               or latest.get("stored_state") in {"ERROR", "UNAVAILABLE", "UNKNOWN"}):
                age = _number(latest.get("age_seconds"))
                header.append(f"{_clean(peer)} probe error ({_compact_age(age + elapsed if age is not None else None)})")
    evidenced = {f"{row['peer']}:{row['pool']}" for row in rows}
    missing_pool = any(key not in evidenced for key in consumers)
    if (selected_ids & {"capacity", "peers"} and (missing_pool or any(row["needs_refresh"] for row in rows))
            and (rows or "capacity" not in selected_ids)):
        header.append("next: peerhub observation refresh")
    if rich:  # the pill's padding takes two cells; the time is that of line 2
        verdict = _decorate_verdict(verdict_line(payload, max(1, width - 2), unicode, keep, sep), sep,
                                    stamp[-6:] if read_at is not None and stamp.endswith("Z") else "", width, paint)
    else:
        verdict = verdict_line(payload, width, unicode, keep)
    frame = CompactFrame(verdict=verdict, header=header)
    if "activity" in selected_ids and not expand:
        frame.running, frame.running_total, frame.recent = _activity_sections(groups, width, unicode, keep,
                                                                               rich=rich, paint=paint)
        frame.leads, frame.leads_total = _leads_section(groups, now + elapsed, width, unicode, keep, rich=rich, paint=paint)
    elif "activity" in selected_ids and isinstance(_obj(groups.get("activity")).get("data"), Mapping):
        data = _obj(_obj(groups.get("activity")).get("data"))  # --expand: RECENT is in the top, RUNNING and LEADS follow it
        ended = _recent_ended(data)
        if ended:
            frame.recent = [_head_rule(_recent_head(data, ended, superseded=True), width, unicode, paint) if rich
                            else _recent_head(data, ended, superseded=True),
                            *_recent_expand_lines(ended, width, unicode, keep, rich=rich, paint=paint)]
            frame.recent_short = _recent_compact(ended, width, unicode, keep, rich=rich, paint=paint, limit=RECENT_EXPAND)
    if "capacity" in selected_ids:
        suggestion = _compact_suggestions(configured_profiles, rows)
        frame.suggest = _compact_wrap(suggestion, width) if expand else _suggest_lines(suggestion, width, unicode)
        if rows:
            frame.pools, frame.pools_total = _pool_section(rows, width, unicode, keep, rich=rich, paint=paint,
                                                           every=expand)
        else:
            notes = (["no quota evidence", "next: peerhub observation refresh"] if height is not None
                     else _compact_wrap("no quota evidence; next: peerhub observation refresh", width))
            frame.pools = [_head_rule("POOLS 0", width, unicode, paint) if rich else "POOLS 0", *notes]
    return frame


def _display_plan_omissions(payload: dict[str, Any]) -> dict[str, Any]:
    """Retire old windows only behind fresh successful quota evidence from the same source.

    This is a presentation copy. Failures, partial reports, other sources, and
    missing plan evidence preserve the old row and its refresh guidance.
    """
    groups = _obj(payload.get("groups"))
    cap = _obj(groups.get("capacity"))
    data = _obj(cap.get("data"))
    observations = _items(payload.get("_human_observations")) if "_human_observations" in payload else _items(data.get("observations"))
    plans = {str(item.get("peer_id")): _clean(item["plan"]) for item in _items(data.get("accounts"))
             if item.get("state") == "MEASURED" and item.get("plan") and not item.get("probe_error")}
    elapsed = _number(payload.get("display_elapsed_seconds")) or 0
    latest: dict[str, list[dict[str, Any]]] = {}
    for item in observations:
        if item.get("kind", "quota") != "quota" or _number(item.get("effective_at_us")) is None:
            continue
        peer = str(item.get("peer_id"))
        previous = latest.get(peer, [])
        if not previous or item["effective_at_us"] > previous[0]["effective_at_us"]:
            latest[peer] = [item]
        elif item["effective_at_us"] == previous[0]["effective_at_us"]:
            previous.append(item)
    replacements: dict[tuple[str, str], dict[str, Any]] = {}
    for item in observations:
        peer = str(item.get("peer_id"))
        report = latest.get(peer, [])
        if (peer not in plans or not report or item.get("state") != "STALE" or not item.get("pool_ref")
                or _clean(item.get("reason") or "").startswith(_DISABLED)):
            continue
        source = item.get("source")
        if not source or any(row.get("source") != source for row in report):
            continue
        successful = all(row.get("pool_ref") and (row.get("state") == "MEASURED" or
                         (row.get("state") == "ABSENT" and _clean(row.get("reason") or "").startswith(_NA_REASONS)))
                         and (_number(row.get("age_seconds")) or 0) + elapsed < (_number(row.get("ttl_seconds")) or 0)
                         for row in report)
        at = _number(item.get("effective_at_us"))
        if not successful or at is None or at >= report[0]["effective_at_us"]:
            continue
        replacements[(peer, str(item["pool_ref"]))] = {**item, "state": "ABSENT", "stored_state": "ABSENT",
            "reason": f"{_NOT_REPORTED}; plan={plans[peer]}", "display_plan": plans[peer], "display_retired": True,
            "age_seconds": report[0].get("age_seconds"), "ttl_seconds": report[0].get("ttl_seconds"),
            "measurements": None, "last_measurements": None, "projection": {}, "resets_at": None, "reset_in_seconds": None}
    if not replacements:
        return payload

    def replace(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [replacements.get((str(item.get("peer_id")), str(item.get("pool_ref"))), item) for item in rows]

    return {**payload, "groups": {**groups, "capacity": {**cap, "data": {
        **data, "observations": replace(_items(data.get("observations")))}}},
        **({"_human_observations": replace(observations)} if "_human_observations" in payload else {})}


GUIDE_TEXT = "Guide"  # the one link of the rich view: the right end of line 2 (#414)
GUIDE_DOC = "docs/monitor.md"
_GUIDE_BLOB = "https://github.com/greatgc-flow/peerhub/blob/"


def _guide_override(env: Mapping[str, str]) -> str | None:
    """PEERHUB_GUIDE_URL (tests and mirrors): an http(s) URL without a space or a control character, else ignored."""
    url = env.get("PEERHUB_GUIDE_URL", "")
    return url if url.isascii() and url.isprintable() and " " not in url and url.startswith(("https://", "http://")) else None


def _guide_released(version: str | None) -> str | None:
    """The tag URL of a released version; None for a dev or local build (`.dev`, `-dev`, `+local`), which has no tag."""
    if version is None:
        from peerhub._version import __version__
        version = __version__
    if any(marker in version.lower() for marker in ("dev", "+", "local")):
        return None
    return f"{_GUIDE_BLOB}v{version}/{GUIDE_DOC}"


def guide_link_url(env: Mapping[str, str], version: str | None = None) -> str | None:
    """The target of the Guide link, or None when the link is not drawn: PEERHUB_NO_LINKS set (not empty or 0), or a dev
    build without a tag (PEERHUB_GUIDE_URL overrides that for tests)."""
    if env.get("PEERHUB_NO_LINKS", "").strip().lower() not in {"", "0", "false", "no", "off"}:
        return None
    return _guide_override(env) or _guide_released(version)


def guide_text_url(env: Mapping[str, str], version: str | None = None) -> str:
    """The URL of the plain `Guide:` line: the tag of this version, the main branch for a build without a tag."""
    return _guide_override(env) or _guide_released(version) or f"{_GUIDE_BLOB}main/{GUIDE_DOC}"


def _with_guide_link(screen: str, width: int, url: str | None, paint: bool) -> str:
    """The Guide link at the right end of line 2, right-aligned to `width` (the columns the frame uses). It is added to the
    finished frame, so no data is ever dropped for it; it is simply not drawn when line 2 leaves no room (one blank at
    least), and never cut: it is whole, opened and closed (OSC 8), or absent. Layout and truncation ignore its bytes."""
    lines = screen.split("\n")
    if url is None or len(lines) < 2 or not _ANSI.sub("", lines[1]).startswith("PeerHub Monitor"):
        return screen
    gap = width - _width(lines[1]) - len(GUIDE_TEXT)
    if gap < 1:
        return screen
    link = f"\x1b]8;;{url}\x1b\\{GUIDE_TEXT}\x1b]8;;\x1b\\"
    lines[1] += " " * gap + (_paint(link, RICH_SGR["dim"]) if paint else link)
    return "\n".join(lines)


def render_grouped_frame(payload: dict[str, Any], *, view: str = "rich", color: bool = False,
                         unicode: bool = True, width: int = 100, height: int | None = None,
                         expand: bool = False, text_encoding: str | None = None, show_quota_notes: bool = False,
                         links: bool = False) -> str:
    """Compact human default; the established renderer supplies expanded detail.

    `text_encoding` is the output's encoding: without `unicode`, text cells keep what it can encode (A5). `links` (the
    rich view on a terminal only; the caller decides) adds the Guide link (OSC 8) to line 2."""
    payload = {**_display_plan_omissions(payload), "_show_quota_notes": show_quota_notes}
    if expand:
        screen = _render_expanded_frame(payload, view=view, color=color, unicode=unicode,
                                        width=width, height=height, expand=True, keep=text_encoding)
        columns = max(0, width - 1)
    else:
        screen = _render_compact_frame(payload, view=view, color=color, unicode=unicode,
                                       width=width, height=height, keep=text_encoding)
        columns = max(1, min(100, width) - 1)
    if links and view == "rich":
        return _with_guide_link(screen, columns, guide_link_url(os.environ),
                                color and "NO_COLOR" not in os.environ)
    return screen


def _render_expanded_frame(payload: dict[str, Any], *, view: str = "rich", color: bool = False,
                         unicode: bool = True, width: int = 100, height: int | None = None,
                         expand: bool = False, keep: str | None = None) -> str:
    """The --expand frame (`_expanded_frame`): the compact frame's top and the detail sections, rich or plain."""
    groups = _obj(payload.get("groups"))
    selected_raw = payload.get("selected_groups")
    selected = ([str(g) for g in cast(Sequence[Any], selected_raw)]
                if isinstance(selected_raw, Sequence) and not isinstance(selected_raw, str) else list(groups))
    selected = [g for g in selected if g not in {"overview", "coverage"}]
    context_sessions = _items(_obj(_obj(groups.get("context")).get("data")).get("sessions"))
    if not any(_obj(item.get("context")).get("state") in {"MEASURED", "ESTIMATED"} for item in context_sessions):
        selected = [g for g in selected if g != "context"]
    coverage_partial = any(_obj(group).get("status") not in {"OK", None}
                           or _group_has_uncertain_measurement(_obj(group).get("data"), depth=0)
                           or _obj(_obj(group).get("data")).get("truncated")
                           or _obj(_obj(group).get("coverage")).get("read") == "partial"
                           for group in groups.values())
    coverage_data = _obj(_obj(groups.get("coverage")).get("data"))
    scope_note = "No scope evidence" if "scopes" in coverage_data and not coverage_data["scopes"] else ""
    footer = "Coverage: " + (scope_note or ("partial" if coverage_partial else "complete")) + "; peerhub explain <code>"
    store = _obj(payload.get("store_selection"))
    db_line = f"DB:{_clean(store.get('source'))} ({_clean(store.get('path'))})" if store.get("path") else ""
    consumers: dict[str, list[str]] = {}
    for profile in _items(payload.get("configured_profiles")):
        fields = _obj(profile.get("fields"))
        name = str(profile.get("profile") or "")
        for pool in cast(list[object], _obj(fields.get("pools")).get("value") or []):
            consumers.setdefault(f"{name.split('.')[0]}:{pool}", []).append(name)
    # Explicit pure-render fixtures may supply configured pool membership directly.
    for peer in _items(_obj(_obj(groups.get("peers")).get("data")).get("peers")):
        profile = _obj(peer.get("profile"))
        for pool in cast(list[object], profile.get("pools") or []):
            consumers.setdefault(f"{peer.get('peer_id')}:{pool}", []).append(str(profile.get("name") or profile.get("profile")))
    cap = _obj(groups.get("capacity"))
    data = _obj(cap.get("data"))
    # Use POOLS' human observations and full identities: distinct pools can share a display name. Both views take their
    # pool order and each pool's effective values from these rows (#394).
    pool_rows = {(str(row["peer"]), str(row["pool_ref"])): row
                 for row in _frame_pools(payload, _frame_consumers(payload))[2]}
    display_groups = {**groups,
        "capacity": {**cap, "data": {**data, "_show_quota_notes": bool(payload.get("_show_quota_notes")),
                                     "_pool_rows": pool_rows,
                                     "consumers": {key: ",".join(sorted(set(names))) for key, names in consumers.items()}}}}
    activity = _obj(groups.get("activity"))
    execution = _obj(groups.get("execution"))
    if isinstance(activity.get("data"), Mapping) and isinstance(execution.get("data"), Mapping):
        # #394: the leases decide what runs; the sampled certainty counts of EXECUTION do not
        running_asks = _running_head(_obj(activity.get("data")), _ask_split(_obj(activity.get("data")))[0], True)[1]
        display_groups["execution"] = {**execution, "data": {**_obj(execution.get("data")), "_running_asks": running_asks}}
    # X5: the plan the provider reports is display text only, shown beside the peer's quota in the PEERS block.
    plan_windows: dict[str, tuple[str, list[str]]] = {}
    for obs in _items(data.get("observations")) if payload.get("_show_quota_notes") else []:
        reason = _clean(obs.get("reason") or "")
        if obs.get("state") == "ABSENT" and reason.startswith(_NOT_REPORTED):
            pool = _clean(obs.get("pool_ref") or "").split(":")[-1]
            plan = _na_plan(obs, reason)
            window = pool.split("-", 1)[-1].lower() if "-" in pool else pool.lower()
            plan_windows.setdefault(_clean(obs.get("peer_id") or "?"), (plan, []))[1].append(window)
    peers_group = _obj(groups.get("peers"))
    if isinstance(peers_group.get("data"), Mapping):
        display_groups["peers"] = {**peers_group, "data": {**_obj(peers_group.get("data")),
                                                          "_show_quota_notes": bool(payload.get("_show_quota_notes"))}}
    if plan_windows and isinstance(peers_group.get("data"), Mapping):
        display_groups["peers"] = {**peers_group, "data": {**_obj(peers_group.get("data")), "_show_quota_notes": True, "plan_notes": {
            peer: f"plan {plan}: no {', '.join(windows)} window" for peer, (plan, windows) in plan_windows.items()}}}
    alerts = frame_alerts(payload)
    unread = unread_sources(payload)
    if alerts or unread:  # every alert, then every source not read (A7); with neither, no section is printed
        display_groups["alerts"] = {"status": "OK", "data": {"alerts": alerts, "unread": unread}}
        selected = ["alerts", *selected]
    display = {**payload, "selected_groups": selected, "groups": display_groups}
    # #338: the store path and the --db/PEERHUB_DB note wrap; a truncated path cannot name the store.
    store_lines = [*(_compact_wrap(db_line, max(1, width - 1)) if db_line else []),
                   *(_compact_wrap(str(payload["_store_note"]), max(1, width - 1)) if payload.get("_store_note") else [])]
    report = _obj(_obj(_obj(groups.get("peers")).get("data")).get("facts_report"))
    # Every facts_report reason is a drift reason ("ag: unknown_pool", "cc: version_drift").
    drift_peers = sorted({str(reason).split(":")[0] for reason in cast(list[object], report.get("reasons") or [])})
    if drift_peers:
        footer += "; DRIFT:" + ",".join(drift_peers)
    return _expanded_frame(payload, display, footer, store_lines, view=view, color=color, unicode=unicode, width=width,
                           height=height, expand=expand, keep=keep)


_ERROR_SOURCES = {  # the reads a group is made of: a group without an error of its own names the first that failed
    "overview": ("sessions", "execution"), "peers": ("sessions", "capacity"), "sessions": ("sessions", "execution"),
    "usage": ("sessions", "execution"), "context": ("sessions", "execution"), "runtime": ("sessions", "execution"),
    "execution": ("execution",), "coverage": ("execution",), "capacity": ("capacity",), "store": ("store",),
}


def _group_error(payload: dict[str, Any], group_id: str) -> str:
    """The row a failed group starts with: the class of its error only (`_safe_error_reason`)."""
    errors = _obj(payload.get("errors"))
    error = errors.get(group_id)
    if error is None:
        error = next((errors[key] for key in _ERROR_SOURCES.get(group_id, (group_id,)) if key in errors), None)
    return _safe_error_reason(error)


_RICH_ORDER = ("alerts", "activity", "capacity", "peers", "execution", "sessions", "usage", "context")


def _is_head(line: str) -> bool:
    """A section head of the rich frame (a rule), colour or not."""
    return _ANSI.sub("", line).startswith(("── ", "-- "))


def _rich_section(label: str, rows: Sequence[str], width: int, unicode: bool, paint: bool) -> list[str]:
    """One section of the rich --expand frame (P23): its head as a rule, like a head of the compact frame, then its
    rows from the second cell. A head keeps the leading fields that fit on its line; the others are the section's first
    row, so a narrow terminal loses none. A row wider than the line wraps, with its next lines further in (colour
    never decides that: a painted row that has to wrap is wrapped as text)."""
    sep = f" {RICH_GLYPHS[unicode]['sep']} "
    fields = label.split(" | ")
    shown = len(fields)
    while shown > 1 and _width(" | ".join(fields[:shown])) > width - 3:
        shown -= 1
    lines = [_head_rule(" | ".join(fields[:shown]), width, unicode, paint)]
    for row in [*_pack(fields[shown:], max(1, width - 1), sep), *rows]:
        if _width(row) <= width - 1:
            lines.append(" " + row)
            continue
        text = _ANSI.sub("", row)
        indent = len(text) - len(text.lstrip(" "))
        parts = _compact_wrap(text, max(1, width - 3 - indent))
        lines.extend(" " * (indent + (1 if index == 0 else 3)) + part for index, part in enumerate(parts))
    return lines


def _rich_entry(mark: str, lines: Sequence[str]) -> list[str]:
    """One entry of RUNNING, LEADS or RECENT: its first line from the second cell (after its mark, if it has one),
    its other lines three cells in."""
    return [("" if index == 0 else "  ") + (f"{mark} " if mark and index == 0 else "") + line
            for index, line in enumerate(lines)]


def _rich_activity(group: dict[str, Any], payload: dict[str, Any], now: float | None, width: int, unicode: bool,
                   paint: bool, keep: str | None) -> tuple[list[str], list[str]]:
    """(RUNNING, LEADS) of the rich --expand frame (P23): every running ask and every lead with every field, as the
    plain view lists them, under the compact frame's rules and with its markers: a RUNNING entry starts with the
    glyph of its flag (► ! × ?), and a lead's context has its meter. RECENT is not here: it follows POOLS in the top of
    the frame (`_compact_frame`, #412). What the activity sample left out ends LEADS."""
    glyphs = RICH_GLYPHS[unicode]
    sep = f" {glyphs['sep']} "
    if not isinstance(group.get("data"), Mapping):
        failed = _clean(group.get("status") or "").upper() in {"ERROR", "FAILED"}
        why = _group_error(payload, "activity") if failed else "data unavailable"
        return _rich_section(f"RUNNING ? | activity {'read failed' if failed else 'not read'}", [why], width, unicode,
                             paint), []
    data = _obj(group.get("data"))
    inner = max(1, width - 3)  # an entry's text starts in the fourth cell: the indent, the mark and a space

    def marked(glyph: str, colour: str, lines: list[str]) -> list[str]:
        return _rich_entry(_paint(glyph, RICH_SGR[colour]) if paint else glyph, lines)

    running, _, _ = _ask_split(data)
    rows: list[str] = []
    for row in running:
        glyph, word, colour = _RUN_MARKS[_flag(row)]
        rows.extend(marked(glyphs.get(glyph) or word.strip()[:1], colour,
                           _ask_lines(row, inner, inner, unicode, keep, sep)))
    front = _rich_section(_running_head(data, running, unicode, keep)[0], rows, width, unicode, paint)
    leads = _items(data.get("leads"))
    rows = []
    for lead in leads:  # a lead has no mark: its label starts the entry
        rows.extend(_rich_entry("", _lead_rows(lead, now, max(1, width - 3), unicode, keep, sep=sep, meter=True,
                                               paint=paint)))
    rows.extend(_activity_notes(data, max(1, width - 1), sep))
    return front, _rich_section(_leads_header(data, leads), rows, width, unicode, paint)


def _plain_activity(group: dict[str, Any], payload: dict[str, Any], now: float | None, width: int, unicode: bool,
                    keep: str | None) -> tuple[list[str], list[str]]:
    """(RUNNING, LEADS) of the plain --expand frame: the heads are the compact frame's words, every running ask and every
    lead has every field (`- ` starts an ask). RECENT is in the top (`_compact_frame`)."""
    if not isinstance(group.get("data"), Mapping):
        failed = _clean(group.get("status") or "").upper() in {"ERROR", "FAILED"}
        why = _group_error(payload, "activity") if failed else "data unavailable"
        return [f"RUNNING ? | activity {'read failed' if failed else 'not read'}", why], []
    data = _obj(group.get("data"))
    running = _ask_split(data)[0]
    rows = [_running_head(data, running, unicode, keep)[0]]
    for row in running:
        rows.extend(_ask_rows(row, width, unicode, keep))
    leads = _items(data.get("leads"))
    lead_rows = [_leads_header(data, leads)]
    for lead in leads:
        lead_rows.extend(_lead_rows(lead, now, width, unicode, keep))
    lead_rows.extend(_activity_notes(data, width))
    return rows, lead_rows


_PLAIN_QUOTA_LEGEND = "STATE: INSUFF=insufficient projection data; THREAT=exhausts before reset"


def _expanded_frame(payload: dict[str, Any], display: dict[str, Any], footer: str, store_lines: list[str], *,
                    view: str, color: bool, unicode: bool, width: int, height: int | None, expand: bool,
                    keep: str | None) -> str:
    """The --expand frame (P23, #412), rich and plain in one order, the compact frame's top first: line 1, the title,
    POOLS with every pool, the whole suggestion, RECENT (entries of two lines). Then RUNNING, ALERTS, LEADS, QUOTA, PEERS,
    EXECUTION, SESSIONS, USAGE, CONTEXT, any other group, and the store (`DB:env (<path>)`, wrapped, never cut) near the
    end. `display` is the payload with the display data of the groups. The rich view draws the heads as rules and
    marks the entries; the plain view is ASCII, never painted, and keeps its QUOTA table with the 8-cell bar.

    One width rule: every line leaves the terminal's last column free (the compact frame alone stops at 100 columns).
    A bounded height ends with the count of the lines it hides and the `| more` command (and the coverage line, when
    there is room). RECENT is allocated before the sections below it: up to 8 entries of two lines when the top with
    them fits; else its entries become one-line rows, then fewer rows (two at least), the smallest form that fits
    being the last; the sections below the top take what is left, from the top down, and go from the bottom up. The
    ATTN line, the title, POOLS, the suggestion and
    RECENT's header with two rows stay; a terminal too small even for those loses, in this order, the rows of POOLS
    (down to four), the suggestion and the rest of POOLS (down to one row), and RECENT's second row. The legend prints in
    the unbounded frame only."""
    rich = view == "rich"
    target = max(0, width - 1)
    if target <= 0 or (height is not None and height <= 1):
        return ""
    paint = color and rich and "NO_COLOR" not in os.environ
    sep = f" {RICH_GLYPHS[unicode]['sep']} " if rich else " | "
    frame = _compact_frame(payload, view=view, color=color, unicode=unicode, width=target, height=None, keep=keep,
                           expand=True)

    def top_of(shown: CompactFrame) -> list[str]:
        # The top is the compact frame's, from its one owner, without the budget of a frame that has to fit a screen.
        return fit_compact_frame(shown, width=target, height=None, unicode=unicode, max_lines=sys.maxsize,
                                 max_bytes=sys.maxsize, keep=keep).splitlines()

    top = top_of(frame)
    lines: list[str] = []
    groups = _obj(display.get("groups"))
    selected = cast(list[str], display.get("selected_groups") or [])
    read_at = _number(payload.get("read_at"))
    elapsed = _number(payload.get("display_elapsed_seconds")) or 0.0
    now = None if read_at is None else read_at + elapsed
    pool_rows = {(str(row["peer"]), str(row["pool_ref"])): row  # the rows POOLS draws (see `_render_expanded_frame`)
                 for row in _frame_pools(payload, _frame_consumers(payload))[2]}
    leads: list[str] = []
    if "activity" in selected:  # RUNNING follows RECENT; LEADS takes the place of the activity group, after ALERTS
        if rich:
            running, leads = _rich_activity(_obj(groups.get("activity")), payload, now, target, unicode, paint, keep)
        else:
            running, leads = _plain_activity(_obj(groups.get("activity")), payload, now, target, unicode, keep)
        lines.extend(running)
    for group_id in [*(gid for gid in _RICH_ORDER if gid in selected), *(gid for gid in selected if gid not in _RICH_ORDER)]:
        group = _obj(groups.get(group_id))
        if group_id == "activity":
            lines.extend(leads)
            continue
        status = _clean(group.get("status") or "UNKNOWN").upper()
        if group_id == "capacity":  # a group that was not read has no data: it says so, as every other group does
            read = _obj(_obj(payload.get("groups")).get("capacity"))
            group = read if read.get("data") is None else {**group, "data": {**_obj(group.get("data")),
                                                                              "_pool_rows": pool_rows}}
        rows, _ = _group_lines(group_id, group, width=max(1, target - 1) if rich else target, unicode=unicode,
                               elapsed_seconds=elapsed, read_at=read_at, expand=expand, color=paint, keep=keep, rich=rich,
                               bounded=height is not None and not rich)  # the plain view's rows for a viewport
        if status in {"ERROR", "FAILED"}:
            rows.insert(0, _group_error(payload, group_id))
        label = _group_label(group_id, group, status, read_at, elapsed)
        if rich:
            lines.extend(_rich_section(label, rows, target, unicode, paint))
            continue
        if group_id == "capacity" and _width(_PLAIN_QUOTA_LEGEND) <= target and any(
                "PEER" in row and "POOL" in row for row in rows):
            rows.append(_PLAIN_QUOTA_LEGEND)
        lines.append(_section_rule(label, target, unicode))
        if height is None:  # the append-only frame cuts a long row; a viewport drops whole trailing fields and says so
            lines.extend(_fit(row, target, unicode, indent=group_id == "capacity") for row in rows)
        else:
            lines.extend(_fit_fields(row, target, unicode, indent=group_id == "capacity") for row in rows)
    lines.extend(store_lines)  # #412: the store near the end, never between POOLS and RECENT

    def fit(line: str) -> str:  # the one exit of every line: the glyph set of the stream, and never past the width
        if not unicode:  # A5: text the output encoding can show stays; the decoration is ASCII
            line = _ascii(line.replace(" · ", " | ").replace("·", "|"), keep)
        return _fit(line, target, unicode, painted=True, indent=True)

    rest = [fit(line) for line in lines]  # the top was fitted by its owner
    footer = fit(footer)
    # The plain view's `Guide: <url>` line (VS Code makes it a link): in the unbounded frame only, which has room for it,
    # never cut and never wider than the frame. A bounded frame keeps every row for the data.
    guide = f"Guide: {guide_text_url(os.environ)}" if not rich and height is None else ""
    foot = [footer, guide] if guide and _width(guide) <= target else [footer]
    if height is None:
        explanations: list[str] = _rich_legend(sep) if rich else list(MONITOR_LEGEND)
        if rich and any(row.get("depends_on") for row in pool_rows.values()):
            explanations.append("blocked=the parent pool is out; a pool under its parent shows the parent's LEFT, PACE and state "
                                "when the parent has less left, and the nearer of the two resets (own = its own LEFT)")
        legend = [fit(part) for text in explanations for part in _compact_wrap(text, max(1, target))]
        return "\n".join([*top, *rest, *legend, *foot]) + "\n"
    limit = height - 1

    def bounded(head: list[str], floor: int | None = None) -> str | None:
        """The frame with `head` whole (down to `floor` lines of it, when given) and the sections below it cut to the
        limit; None when it does not fit."""
        every = [*head, *rest]
        keep = len(head) if floor is None else floor
        if len(every) + 1 <= limit:
            return "\n".join([*every, footer]) + "\n"
        for tail in ([footer], []):  # the coverage line gives way before a line of the frame's top does
            kept = limit - len(tail) - 1
            while kept >= keep:
                while kept > max(1, keep) and _is_head(every[kept - 1]):  # never a head without a row before the hint
                    kept -= 1
                hint = _hidden_lines(len(every) - kept, 0, target, unicode, expand=True)
                if kept + len(hint) + len(tail) <= limit:
                    return "\n".join([*every[:kept], *(fit(line) for line in hint), *tail]) + "\n"
                kept -= 1
        return None

    # RECENT is allocated before the sections below it: the richest form whose top still fits with the hint (entries of
    # two lines, then rows of one line, then fewer rows); those sections get the lines that are left, from the top down.
    smallest, head = frame, top
    result = bounded(head)
    if result is not None:
        return result
    for smaller in _recent_ladder(frame):
        smallest, head = smaller, top_of(smaller)
        result = bounded(head)
        if result is not None:
            return result
    for smaller in _last_resort(smallest, target, unicode):  # a terminal too small for the protected top
        smallest, head = smaller, top_of(smaller)
        result = bounded(head)
        if result is not None:
            return result
    # even that does not fit: its own lines from the first, cut at the bottom, then the hint
    result = bounded(head, floor=1)
    if result is not None:
        return result
    short = _fit(f"+{len(top) + len(rest) - 1} --expand | more", target, unicode)
    return "\n".join([top[0], short][:limit]) + "\n"


def _recent_ladder(frame: CompactFrame) -> Iterator[CompactFrame]:
    """The top of the --expand frame with RECENT smaller each time (#412): the entries of two lines become rows of one
    line (the compact frame's row), then fewer rows, two at least (or the one there is)."""
    if not frame.recent or not frame.recent_short:
        return
    head, rows = frame.recent[0], frame.recent_short
    for count in range(len(rows), min(2, len(rows)) - 1, -1):
        yield replace(frame, recent=[head, *rows[:count]], recent_short=rows[:count])


def _last_resort(frame: CompactFrame, width: int, unicode: bool) -> Iterator[CompactFrame]:
    """What a terminal too small for the protected top gives up, one step at a time: the rows of POOLS down to four, the
    suggestion down to its first line, the rows of POOLS down to one (a pool row that goes is counted on one line,
    `+N pools`), the rest of the suggestion, and RECENT's second row. The ATTN line, the title, the POOLS header and
    RECENT's header with a row stay."""
    pools = len(frame.pools) - 1 if frame.pools else 0
    suggest = len(frame.suggest)
    rows = (len(frame.recent) - 1) if frame.recent and frame.recent_short else 0

    def variant(p: int, s: int, r: int) -> CompactFrame:
        shrunk = replace(frame, suggest=frame.suggest[:s])
        if frame.recent and frame.recent_short:
            shrunk.recent = [frame.recent[0], *frame.recent[1:1 + r]]
        if frame.pools and p < pools:
            shrunk.pools = [*frame.pools[:p + 1], _fit(f"+{pools - p} pools", width, unicode)]
            shrunk.pools_total = 0  # the line above is the overflow line: the frame adds none of its own
        return shrunk

    p, s, r = pools, suggest, rows
    while p > 4:
        p -= 1
        yield variant(p, s, r)
    while s > 1:
        s -= 1
        yield variant(p, s, r)
    while p > 1:
        p -= 1
        yield variant(p, s, r)
    while s > 0:
        s -= 1
        yield variant(p, s, r)
    while r > 1:
        r -= 1
        yield variant(p, s, r)

