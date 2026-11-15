"""Pure human presentation for the schema 2.0 grouped monitor frame."""
from __future__ import annotations

import math
import os
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

from peerhub.cli.monitor_preferences import GROUPS

# Mirrors peerhub.extensions.observation_model.NOT_REPORTED_REASON (the human renderer imports no extension code).
_NOT_REPORTED = "not reported by provider"
_DISABLED = "disabled by provider"  # mirrors observation_model.DISABLED_REASON: the window exists but the provider switched it off
_NA_REASONS = (_NOT_REPORTED, _DISABLED)
_LEAD_MARK_PCT = 80  # mirrors monitor_read_model.LEAD_CONTEXT_MARK_PCT: the `!` of a lead's ctx cell
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_BIDI = {"\u061c", "\u200e", "\u200f", *[chr(code) for code in range(0x202A, 0x202F)],
         *[chr(code) for code in range(0x2066, 0x206A)]}
MONITOR_LEGEND = (
    "ok enough | low >0-10% | fast >10% + threat (2+ samples) | out 0% | aged stale | reset passed | n/a plan omits | ? unknown",
    "PACE=used%-elapsed% pp, + ahead of even burn, - behind; '-' cell = unavailable",
    "BAR=remaining, | marks the even-burn level (^ ahead, v behind)",
    "TIME: the header time is the snapshot time (when the store was read), not the time quota was measured",
    "LEADS: label | state (?busy = alive not confirmed, - = no session) | age of the last activity"
    " | ctx = context of the model's maximum input (! 80% or more; 106k = window unknown; ? unknown) | running asks | model | title",
    "CTX est: estimated context of a cc session at the end of its last ask (percent of the model's maximum input, with"
    " its age); not a live measurement",
)


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


def _quota_layout(rows: list[dict[str, Any]], unicode: bool) -> tuple[list[dict[str, Any]], list[str]]:
    """Derive a forest from configured consumers, leaving every pool's values intact."""
    result: list[dict[str, Any]] = []
    notes: list[str] = []
    for peer in sorted({str(row["peer"]) for row in rows}):
        peer_rows = [dict(row) for row in rows if row["peer"] == peer]
        retired = [row for row in peer_rows if row.get("retired")]
        if retired:
            names = ",".join(str(row["pool"]) for row in retired)
            plans = ",".join(sorted({str(row.get("display_plan") or "unknown") for row in retired}))
            notes.append(f"{peer} {names} n/a (not reported by plan {plans})")
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
        children: dict[str, list[str]] = {name: [] for name in by_name}
        for child, parent in parents.items():
            if parent is not None:
                children[parent].append(child)
        for names in children.values():
            names.sort(key=lambda name: (-windows[name], name))

        def visit(name: str, prefix: str, continuation: str) -> None:
            row = by_name[name]
            append_row(row, prefix + name)
            # A folded parent cannot carry a displayed dependency. Keep such n/a
            # nodes as full rows so descendants still have their actual parent.
            folded = [child for child in children[name] if by_name[child]["state"].lower() == "n/a" and not children[child]]
            if folded:
                row["suffix"] = "(" + ",".join(folded) + " n/a)"
            visible = [child for child in children[name] if child not in folded]
            for i, child in enumerate(visible):
                last = i == len(visible) - 1
                branch = ("└─ " if last else "├─ ") if unicode else ("`- " if last else "|- ")
                stem = "   " if last else "│  " if unicode else "|  "
                visit(child, continuation + branch, continuation + stem)

        for name in sorted((name for name in by_name if parents[name] is None),
                           key=lambda name: (_pool_family(name), windows[name], name)):
            visit(name, "", "")
    return result, notes


def _quota_suffix(line: str, suffix: str, width: int, unicode: bool, color: bool) -> list[str]:
    """Keep folded names even when the trailing columns were dropped."""
    if not suffix:
        return [line]
    note = _paint(suffix, "2") if color else suffix
    if _width(line + " " + suffix) <= width:
        return [line + " " + note]
    return [line, *[_paint(part, "2") if color else part for part in _compact_wrap(suffix, width)]]


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
    if column in {"STATE", "BAR", "[BAR]"}:
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


def _section_rule(label: str, width: int, unicode: bool, color: bool = False) -> str:
    label = _fit_fields(label, max(0, width - 4), unicode)
    return _fit(_rule(label, "", width, unicode, color), width, unicode, painted=True)


_FRONT_GROUPS = ("alerts", "activity")  # --expand: every alert, then the activity section, then today's groups


def _reorder_groups(group_ids: list[str]) -> list[str]:
    first = [gid for gid in (*_FRONT_GROUPS, "capacity", "peers") if gid in group_ids]
    return first + [gid for gid in group_ids if gid not in first]


def _group_label(gid: str, group: dict[str, Any], status: str, read_at: float | None, elapsed: float) -> str:
    if gid == "capacity":
        return _quota_label(status, group, read_at, elapsed)
    if gid == "alerts":
        return f"ALERTS {len(_items(_obj(group.get('data')).get('alerts')))}"
    label = f"{gid.upper()} [{status}]"
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


def _session_contexts(groups: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """The context of the newest session (highest generation) of each stream and peer: what an ended ask of that
    stream and peer is joined to. Ask rows carry no context of their own."""
    newest: dict[tuple[str, str], dict[str, Any]] = {}
    for item in _items(_obj(_obj(groups.get("sessions")).get("data")).get("items")):
        key = (str(item.get("stream_id")), str(item.get("peer_id")))
        if key not in newest or (_number(item.get("generation")) or 0) > (_number(newest[key].get("generation")) or 0):
            newest[key] = item
    return {key: _obj(_obj(item.get("telemetry")).get("context")) for key, item in newest.items()}


def _group_lines(group_id: str, group: dict[str, Any], *, width: int, unicode: bool,
                 elapsed_seconds: float = 0.0, read_at: float | None = None,
                 expand: bool = False, color: bool = False, bounded: bool = False,
                 keep: str | None = None) -> tuple[list[str], int]:
    """Return rendered data rows and the length of the QUOTA table prefix."""
    data = _obj(group.get("data"))
    if data == {} and group.get("data") is None:
        return ["data unavailable"], 0
    rows: list[str] = []
    table_size = 0
    sep = " · " if unicode else " | "
    dot = "·" if unicode else "|"
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
                    q_wide = "? (not reported)"
                    q_narr = "? not reported"
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
                    plan_note = _clean(_obj(data.get("plan_notes")).get(pid) or "")
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
            shown = _visible_fields(narrow_peers, " | ", width - 2, keep=1)
            rows.extend(" | ".join(field for field in fields[:shown] if field) for fields in narrow_peers)
            if shown < 4:
                rows.extend(_omitted_rows([name for name, i in (("profile", 1), ("session", 2), ("ctx", 3))
                                           if i >= shown and any(fields[i] for fields in narrow_peers)], width - 2))

        if bounded:
            rows = [row for row in rows if "-> latest session:" not in row] + [
                row for row in rows if "-> latest session:" in row]
        defaults_rev: object = data.get("defaults_revision")
        observed_at: object = data.get("observed_at")

        rev_str = f"rev {_clean(defaults_rev)}" if defaults_rev else "rev unknown"
        if observed_at:
            obs_clean = _clean(observed_at)
            age_str = "(unknown)"
            if read_at is not None:
                try:
                    read_date = datetime.fromtimestamp(read_at, timezone.utc).date()
                    obs_date = date.fromisoformat(obs_clean)
                    days = (read_date - obs_date).days
                    age_str = "(future)" if days < 0 else f"({days}d ago)"
                except (ValueError, OSError, OverflowError):
                    pass
            obs_str = f"checked {obs_clean} {age_str}"
        else:
            obs_str = "checked unknown"

        footer = (
            f"Model facts: {rev_str}{sep}{obs_str}{sep}"
            "sources: codex debug models | agy models | claude: known ids"
        )
        rows.append(_fit(footer, max(0, width - 1), unicode))

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
                facts_line = f"Facts: needs_recheck{reason_str} - refresh-model-profiles"
            elif status == "ok":
                chk_date = str(rep_dict.get("checked_at") or "")
                chk_date_str = f" (checked {chk_date[:10]})" if chk_date else ""
                facts_line = f"Facts: ok{chk_date_str}"
            elif status == "unverified":
                facts_line = "Facts: unverified"
            else:
                facts_line = f"Facts: {status}" if status else "Facts: unknown"
            rows.append(_fit(facts_line, max(0, width - 1), unicode))

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
            rows.append("Delivery certainty: " + ", ".join(f"{key.replace('_', ' ')} {_count(value)}"
                         for key, value in totals.items()))
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
            # Columns are listed by priority (left first); a too-wide table drops whole columns from the right.
            column_widths = {"PEER": 4, "POOL": 8, "STATE": 12, "REM": 5, "RESET-IN": 9, "PACE": 9,
                             "[BAR]": 10, "EXHAUST-IN": 13, "AGE": 5, "PROFILES (configured)": 24}
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
                        na_notes.append(f"{peer} {pool_id} n/a {what}" if fresh else
                                        f"{peer} {pool_id} ? {what}, aged; next: peerhub observation refresh --peers {peer}")
                    sources.append(item)
                    table.append({
                        "PEER": peer, "POOL": pool_id, "REM": "n/a" if fresh else "-",
                        "[BAR]": _bar_quota(None), "RESET-IN": "-", "EXHAUST-IN": "-", "PACE": "-",
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
                else:
                    # STALE/UNKNOWN rows keep identity+reason, no usable capacity shown
                    rem_str = "-"
                    bar_str = _bar_quota(None)
                    reset_str = "-"
                    exhaust_str = "-"
                    pace_str = "-"
                    state_str = "STALE:window" if state == "STALE" else state

                sources.append(item)
                table.append({"PEER": peer, "POOL": pool_id, "REM": rem_str,
                              "[BAR]": bar_str, "RESET-IN": reset_str,
                              "EXHAUST-IN": exhaust_str, "PACE": pace_str,
                              "STATE": state_str, "AGE": age_str,
                              "PROFILES (configured)": _clean(_obj(data.get("consumers")).get(f"{peer}:{pool_id}") or "unmapped")})

            layout, retired_notes = _quota_layout([
                {"peer": cells["PEER"], "pool": cells["POOL"], "state": cells["STATE"],
                 "profiles": cells["PROFILES (configured)"], "cells": cells, "source": item,
                 "retired": item.get("display_retired"), "display_plan": item.get("display_plan")}
                for cells, item in zip(table, sources)], unicode)
            for row in layout:
                cells = row["cells"]
                cells["PEER"], cells["POOL"] = row["display_peer"], row["display_pool"]
            table = [row["cells"] for row in layout]
            # One column set for the whole table; PEER and POOL always stay, and no cell is cut.
            sizes = {c: max(_width(c) if bounded else w, *(_width(cells[c]) for cells in table))
                     if table else (_width(c) if bounded else w) for c, w in column_widths.items()}
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
            previous_peer = ""
            for row in layout:
                if previous_peer and previous_peer != row["peer"]:
                    rows.append("")
                previous_peer = row["peer"]
                item = row["source"]
                summary = _compact_pool(item, read_at or 0, elapsed_seconds, {})
                invalid = (item.get("state") == "STALE" or bool(summary["aged"])
                           or summary["state"] in {"reset", "?", "n/a"})
                code = _quota_color(row["cells"]["STATE"], _number(summary["remaining"]), aged=invalid) if color else None
                line = (_paint(table_line(row["cells"]), "2") if color and row["state"] == "n/a"
                        else table_line(row["cells"], code))
                rows.extend(_quota_suffix(line, str(row["suffix"]), width, unicode, color))
            table_size = len(rows)
            # Keep reasons for folded pools; retired pools already have a combined note.
            rows.extend(_paint(_fit(note, width, unicode), "2") if color else _fit(note, width, unicode)
                        for note in [*retired_notes, *na_notes])
            rows.extend(_fit(f"{peer} {' '.join(sorted(codes))}; next: peerhub observation refresh --peers {peer}", width, unicode) for peer, codes in probe_notes.items())
            if len(columns) < len(column_widths):
                rows.extend(_omitted_rows(["bar" if c == "[BAR]" else c.lower() for c in list(column_widths)[len(columns):]], width))

        else:
            rows.append("Quota capacity: no observations")
            table_size = len(rows)
        for credit in _items(data.get("reset_credits")):
            count = _count(credit.get("available_count"))
            expires = _number(credit.get("nearest_expires_at"))
            left = None if expires is None or read_at is None else expires - read_at - elapsed_seconds
            expiry = "-" if expires is None else f"in {_format_duration(left)}" if left and left > 0 else datetime.fromtimestamp(expires, timezone.utc).strftime("%Y-%m-%d %H:%MZ")
            state = _clean(credit.get("state") or "UNKNOWN")
            rows.append(f"{_peer(credit)} reset credits: {count} | soonest {expiry} [{state}]")
        account_items = _items(data.get("accounts"))
        if account_items:
            rows.extend(_account_rows(account_items, elapsed_seconds))
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
        rows.extend(_alert_row(alert, unicode, keep) for alert in _items(data.get("alerts")))
        rows.extend(_cell(row, unicode, "-", keep) for row in cast(list[object], data.get("unread") or []))  # A7
        if not rows:
            rows.append("none")

    elif group_id == "activity":
        rows.extend(_activity_rows(data, max(1, width - 2), unicode, keep,
                                   None if read_at is None else read_at + elapsed_seconds))

    else:
        # Keep future fields visible without exposing a JSON-shaped dump.
        rows.extend(_fallback_rows(data))

    return [row for row in rows if not row.startswith("Coverage:")] or ["No data"], table_size


def _alert_row(alert: dict[str, Any], unicode: bool = True, keep: str | None = None) -> str:
    age = _number(alert.get("age_s"))
    return (f"{_cell(alert.get('kind'), unicode, '?', keep)}: {_cell(alert.get('subject'), unicode, '?', keep)} | "
            f"age {'-' if age is None else _compact_age(age)} | {_cell(alert.get('severity'), unicode, '?', keep)} | "
            f"{_cell(alert.get('code'), unicode, '?', keep)}")


def _stamp(value: object) -> str:
    number = _number(value)
    return "?" if number is None else _utc_text(number, "%Y-%m-%d %H:%M:%SZ") or "?"


def _pack(fields: Sequence[str], width: int) -> list[str]:
    """Whole fields joined by ' | ' and carried to the next line; only a field wider than a line wraps by words."""
    lines: list[str] = []
    line = ""
    for item in fields:
        if line and _width(line + " | " + item) <= width:
            line += " | " + item
            continue
        if line:
            lines.append(line)
        parts = [item] if _width(item) <= width else _compact_wrap(item, width)
        lines.extend(parts[:-1])
        line = parts[-1] if parts else ""
    return lines + ([line] if line else [])


def _ask_fields(row: dict[str, Any], unicode: bool = True, keep: str | None = None,
                context: str | None = None) -> list[str]:
    """Every field of one ask row (#374 C): the expand view hides nothing the read model gives. Text is made plain-view
    safe here, before packing, so a replaced character can never widen a packed line (A5). `context` is the estimate
    of the session an ended ask is joined to (`2% est, 3m ago`), after the tokens."""

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
    if context:
        fields.append(f"ctx {context}")
    if row.get("note"):
        fields.append(f"note {txt(row['note'])}")
    reasons = [txt(reason) for reason in cast(list[object], row.get("reasons") or []) if reason]
    if reasons:
        fields.append("reasons " + ", ".join(reasons))
    return fields


def _ask_rows(row: dict[str, Any], width: int, unicode: bool = True, keep: str | None = None,
              context: str | None = None) -> list[str]:
    lines = _pack(_ask_fields(row, unicode, keep, context), max(1, width - 2))
    lines[0] = "- " + lines[0]  # marks where each ask starts; continuation lines carry no mark
    texts = [] if _is_open(row) else [("reply", row.get("reply_line_long"))]
    for label, text in [*texts, ("ask", row.get("ask_line_long"))]:
        lines.extend(_compact_wrap(f"{label}: {_cell(text, unicode, '-', keep)}", width))
    return lines


def _clock(value: object, fmt: str) -> str:
    number = _number(value)
    return "?" if number is None else _utc_text(number, fmt) or "?"


def _lead_rows(lead: dict[str, Any], now: float | None, width: int, unicode: bool = True,
               keep: str | None = None) -> list[str]:
    """Every field of one lead (#376 3.4): its session (the lead and process, then model, context and asks, then name
    and cwd, then title, prompt and said), or why it has none."""

    def txt(value: object) -> str:
        return _cell(value, unicode, "?", keep)

    head = [txt(lead.get("label")), f"{txt(lead.get('tool'))} {txt(lead.get('id'))}"]
    asks = [f"running {_count(lead.get('running'))}", f"asks 24h {_count(lead.get('asks_24h'))}",
            f"last ask {_stamp(lead.get('last_ask_at'))}"]
    item = _obj(lead.get("session"))
    if not item:
        why = ("no session (not a Claude Code lead)" if lead.get("tool") != "cc"
               else "no session found (ended, or its Claude folder is not in PEERHUB_LEAD_DIRS)")
        return _pack([*head, why, *asks], width)
    active, tokens = _number(item.get("active_at")), _number(item.get("context_tokens"))
    window, pct = _number(item.get("context_window")), _number(item.get("context_pct"))
    ctx = ("ctx ?" if tokens is None else f"ctx {_count(tokens)} of {_count(window)} model max ({int(pct)}%)"
           if window is not None and pct is not None else f"ctx {_count(tokens)} (window unknown)")
    first = [*head, f"{txt(item.get('state'))} since {_stamp(item.get('state_since'))}",  # A-4: with the date
             "active ?" if active is None or now is None else f"active {_compact_age(now - active)} ago",
             "alive" if item.get("alive") is True else "alive ?", f"pid {txt(item.get('pid'))}",
             f"entry {txt(item.get('entry'))}", f"claude {txt(item.get('version'))}",
             f"started {_clock(item.get('started_at'), '%Y-%m-%d %H:%MZ')}"]
    second = [f"model {txt(item.get('model'))}", ctx + ("" if tokens is None else f" at {_stamp(item.get('context_at'))}"),
              *asks]
    third = [*([f"name {txt(item['name'])}"] if item.get("name") else []), f"cwd {txt(item.get('cwd'))}"]
    rows = [line for part in (first, second, third) for line in _pack(part, width)]
    for label, key in (("title", "title"), ("prompt", "prompt_line"), ("said", "said_line")):
        if item.get(key):
            rows.extend(_compact_wrap(f"{label}: {txt(item[key])}", width))
    return rows


def _activity_rows(data: dict[str, Any], width: int, unicode: bool = True, keep: str | None = None,
                   now: float | None = None) -> list[str]:
    """RUNNING (every row), RECENT (the 20 newest ended rows and the superseded rows), LEADS: every field. The newest
    ended ask of a stream and peer shows the estimated context of that stream's and peer's newest session
    (`_session_contexts`, #376 P2) when that ask produced it: the estimate was read at or after the ask's start (both
    times come from the one clock of the store's writer). An ask that never started or ended without a reading, an
    earlier ended ask of the same stream and peer, a running ask and a superseded ask never show it."""
    contexts = dict(cast(dict[tuple[str, str], dict[str, Any]], data.get("_session_contexts") or {}))  # used up below
    running, ended, superseded = _ask_split(data)
    head, _ = _running_head(data, running, unicode, keep)
    rows = [head]
    for row in running:
        rows.extend(_ask_rows(row, width, unicode, keep))
    newest = sorted(ended, key=lambda row: -(_number(row.get("ended_at")) or 0.0))[:RECENT_EXPAND]
    recent = f"RECENT {len(ended)}" + (f" (newest {RECENT_EXPAND} shown)" if len(ended) > RECENT_EXPAND else "")
    omitted = _number(data.get("superseded_omitted")) or 0
    if superseded or omitted:
        recent += f" | superseded {len(superseded)}" + (f" (+{int(omitted)} not read)" if omitted else "")
    rows.append(recent)
    for row in newest:  # newest first, so the first row of a stream and peer is its newest ended ask
        context = contexts.pop((str(row.get("stream")), str(row.get("peer"))), None) or {}
        started, read = _number(row.get("started_at")), _number(context.get("observed_at"))
        own = started is not None and read is not None and read >= started
        rows.extend(_ask_rows(row, width, unicode, keep, _estimate(context, now) if own else None))
    for row in superseded:
        rows.extend(_ask_rows(row, width, unicode, keep))
    leads = _items(data.get("leads"))
    rows.append(_leads_header(data, len(leads)))
    for lead in leads:
        rows.extend(_lead_rows(lead, now, width, unicode, keep))
    if data.get("truncated"):
        rows.extend(_pack([f"sample truncated: {_count(data.get('scopes_total'))} scopes",
                           f"superseded not read {_count(data.get('superseded_omitted'))}"], width))
    if data.get("reason"):
        rows.extend(_pack([f"workspace generation: {_clean(data['reason'])}"], width))
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


def _priority(group: dict[str, Any]) -> int:
    status = str(group.get("status") or "UNKNOWN").upper()
    if status in {"ERROR", "FAILED"}:
        return 0
    if status in {"UNAVAILABLE", "NOT_AVAILABLE"}:
        return 1
    if status in {"STALE", "PARTIAL", "UNKNOWN"}:
        return 2
    if _group_has_uncertain_measurement(_obj(group.get("data")), depth=0):
        return 2
    if status == "OK":
        return 3
    return 2


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


def _render_unbounded_frame(payload: dict[str, Any], *, view: str, color: bool,
                            unicode: bool, width: int, expand: bool = False, keep: str | None = None) -> str:
    """Keep the append-only pipe/plain layout stable; it is not a TTY viewport."""
    target_width = max(0, width - 1)
    if target_width == 0:
        return ""
    if target_width < 19:
        return _fit("Narrow; use >=20 columns", target_width, unicode) + "\n"
    selected = payload.get("selected_groups")
    raw_group_ids = [str(g) for g in cast(Sequence[Any], selected)] if isinstance(selected, Sequence) and not isinstance(selected, str) else []
    groups = _obj(payload.get("groups"))
    if not raw_group_ids:
        raw_group_ids = [str(g) for g in groups.keys()]
    group_ids = _reorder_groups(raw_group_ids)

    status_label = _clean(payload.get("status") or "UNKNOWN")
    title = f"PeerHub Monitor | {status_label}"
    read_at = _number(payload.get("read_at"))
    next_refresh = _number(payload.get("next_refresh_in"))
    elapsed_seconds = _number(payload.get("display_elapsed_seconds")) or 0.0
    refresh_state = _clean(payload.get("refresh_state") or "").upper()
    if refresh_state:
        title += f" | {refresh_state}"
    if next_refresh is not None:
        title += f" | refresh in {max(0, math.ceil(next_refresh))}s"
    if target_width < 48:
        title = f"Monitor {status_label}"
    elif read_at is not None:
        try:
            title += " | " + datetime.fromtimestamp(read_at, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        except (OverflowError, OSError, ValueError):
            title += " | time unknown"

    lines: list[str] = [_fit(title, target_width, unicode)]

    for group_id in group_ids:
        group = _obj(groups.get(group_id))
        status = _clean(group.get("status") or "UNKNOWN").upper()
        label = _group_label(group_id, group, status, read_at, elapsed_seconds)

        rule = _section_rule(label, target_width, unicode, color)
        lines.append(rule)

        group_data, _ = _group_lines(group_id, group, width=target_width, unicode=unicode,
                                  elapsed_seconds=elapsed_seconds, read_at=read_at, expand=expand, color=color, keep=keep)
        for content in group_data:
            if group_id == "capacity":
                lines.append(_fit(content, target_width, unicode, painted=color, indent=True))
            else:
                lines.append(_fit("  " + content, target_width, unicode))

    if not unicode:  # A5: text the output encoding can show stays; the decoration is ASCII
        lines = [_ascii(line.replace(" · ", " | ").replace("·", "|"), keep) for line in lines]
    return "\n".join(lines) + "\n"


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
    return {"peer": peer, "pool": pool, "meaning": meaning, "remaining": remaining, "left": left,
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


def _account_rows(accounts: list[dict[str, Any]], elapsed: float) -> list[str]:
    """Expanded ACCOUNTS rows (--expand only since #374): the stored fields of JSON capacity.accounts, labels spelled out."""
    rows = ["ACCOUNTS (plan is display data; the expiry is the login token's, not billing)"]
    for item in accounts:
        peer = _clean(item.get("peer_id") or "?")
        age = _number(item.get("age_seconds"))
        tail = f"age {_compact_age(age + elapsed if age is not None else None)} [{_clean(item.get('state') or 'UNKNOWN')}]"
        if _account_failed(item):
            rows.append(f"{peer} account ? | probe error: {_clean(item.get('probe_error') or 'unknown')} | {tail}")
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
        rows.append(" | ".join(parts + [tail]))
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


# ---- compact frame (#374, P21): line 1 verdict, line 2 header, POOLS, the suggestion, RUNNING, LEADS, RECENT -----------

COMPACT_MAX_LINES = 24
COMPACT_MAX_BYTES = 8192
RUNNING_CAP = 5
LEADS_CAP = 3
LEAD_LABEL_COLS = 16
RECENT_CAP = 2
POOLS_CAP = 8
REQUEST_CAP = 60
RECENT_EXPAND = 20
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
    True: {"rule": "─", "sep": "·", "full": "█", "empty": "░", "none": "─", "on": "■", "off": "□", "ok": "●", "fast": "▲",
           "low": "▼", "out": "×", "aged": "○", "run": "►", "gone": "×", "dot": "●", "up": "▲", "down": "▼"},
    False: {"rule": "-", "sep": "|", "full": "#", "empty": ".", "none": "-", "on": "#", "off": ".", "ok": "*", "fast": "^",
            "low": "v", "out": "x", "aged": "o", "run": ">", "gone": "x", "dot": "*", "up": "^", "down": "v"},
}
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
    the suggestion, RUNNING, LEADS, RECENT (P21: the quota comes first; what is cut first is unchanged).

    Every line is fitted to `width`. While the frame is over the height bound (height - 1 lines), `max_lines` or
    `max_bytes` (the bytes of the lines as given, SGR codes included), lines go in this single order: RECENT rows from
    the bottom (down to one), LEADS rows (down to one),
    RUNNING rows after the third, POOLS rows after the fourth, the second suggestion line, the remaining RUNNING rows,
    the remaining POOLS rows, the RECENT header with its last row, the LEADS header with its last row, the first
    suggestion line, the POOLS header, the RUNNING header, line 2 (its attached lines first). Line 1 is the fallback:
    alone, it is fitted to the byte ceiling; a budget of 0 prints nothing. An overflow line is refitted whenever its
    count changes; the removal that creates one removes two rows. A row step with a floor of one or more never leaves a
    header without a row: when those two rows are the last ones, the step does nothing, and the rows leave with their
    header in its own step (#376 A-3).
    """
    limit = max_lines if height is None else min(max_lines, max(0, height - 1))
    if limit <= 0 or width <= 0:
        return ""

    def fit(line: str) -> str:
        return _fit(line if unicode else _ascii(line, keep), width, unicode, painted=True, indent=True)

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
        return [verdict, *header, *section("pools"), *suggest, *section("running"), *section("leads"), *section("recent")]

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

    steps: list[Callable[[], bool]] = [
        lambda: pop_line(parts["recent"], 2),
        lambda: pop_rows("leads", 1),
        lambda: pop_rows("running", 3),
        lambda: pop_rows("pools", 4),
        lambda: pop_line(suggest, 1),
        lambda: pop_rows("running", 0),
        lambda: pop_rows("pools", 0),
        lambda: drop("recent"),
        lambda: drop("leads"),
        lambda: pop_line(suggest, 0),
        lambda: drop("pools"),
        lambda: drop("running"),
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
    """RUNNING (header and at most five rows, the total before the cap) and RECENT (at most two rows, or None).
    The rich view (P21) draws each head as a rule, starts a RUNNING row with its state glyph (and the word of a flag)
    and a RECENT row with a state dot; such a cell is used only when it costs no column."""
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

    running, ended, _ = _ask_split(data)
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
    if ended:
        table = [[cell(row.get("state")),
                  _compact_age(_number(row.get("age_s"))) if row.get("ended_exact") else "?",
                  cell(row.get("profile") or row.get("peer")), cell(_obj(row.get("lead")).get("label")),
                  _short(cell(_recent_text(row)), REQUEST_CAP, unicode), cell(row.get("name"))]
                 for row in ended[:RECENT_CAP]]
        colours = [_RECENT_COLOURS.get(str(row.get("state")), "bad") for row in ended[:RECENT_CAP]]
        if rich:
            dotted = [[f"{glyphs['dot']} {row[0]}", *row[1:]] for row in table]
            if _columns(dotted, width - 1, 4)[0] >= _columns(table, width - 1, 4)[0]:
                table = dotted
        painter = _column_painter(0, colours) if rich and paint else None
        recent = [head_line(f"RECENT {len(ended)}"),
                  *(" " + line for line in _table(table, width - 1, unicode, right=(1,), elastic=4, paint=painter))]
    return [head_line(head), *rows], total, recent


def _leads_header(data: dict[str, Any], count: int) -> str:
    """`LEADS n`, with `| partial (...)` or `| read failed` when the lead sessions were not read completely (#376 3.4);
    the count is `?` when such a read leaves nothing to count."""
    read = _obj(data.get("leads_read"))
    state = read.get("state")
    notes = [f"{_count(read.get(key))} {words}" for key, words in (("unreadable", "unreadable"), ("not_read", "not read"))
             if (_number(read.get(key)) or 0) > 0]
    note = (" | partial" + (f" ({', '.join(notes)})" if notes else "") if state == "partial"
            else " | read failed" if state == "error" else "")
    return f"LEADS {'?' if note and count == 0 else count}{note}"


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
    header = _leads_header(data, len(leads))
    if not leads and header == "LEADS 0":
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


def _na_note(row: dict[str, Any]) -> str:
    reason = str(row["reason"])
    if reason.startswith(_DISABLED):
        return "disabled by provider"
    match = re.search(r"(?:^|;)\s*plan=([^;]+)", reason)
    plan = _clean(row["display_plan"]) if row.get("display_plan") else (match[1] if match else None)
    return f"not reported by plan {plan}" if plan else "not reported"


def _ordered_pools(rows: list[dict[str, Any]]) -> list[tuple[dict[str, Any], bool]]:
    """Classes out, low, binding (a 7-day pool whose 5-hour sub-row is shown), fast, the rest; then peer and pool."""
    weekly: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row["family"] != "?" and _parse_window(str(row["pool"]))[0] == _WEEK:
            weekly.setdefault((str(row["peer"]), str(row["family"])), row)
    tops: list[dict[str, Any]] = []
    children: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        parent = weekly.get((str(row["peer"]), str(row["family"])))
        if parent is None or parent is row or _parse_window(str(row["pool"]))[0] >= _WEEK:
            tops.append(row)
        elif _binds(row, parent):
            children.setdefault(id(parent), []).append(row)

    def rank(row: dict[str, Any]) -> tuple[int, tuple[str, str, int, str]]:
        # The most severe class of the pool and its shown 5-hour row (K3/L6); fast is the pool's own state only.
        states = {row["state"], *(child["state"] for child in children.get(id(row), ()))}
        cls = (0 if "out" in states else 1 if "low" in states else 2 if id(row) in children
               else 3 if row["state"] == "fast" else 4)
        return cls, _pool_order(str(row["peer"]), str(row["pool"]))

    ordered: list[tuple[dict[str, Any], bool]] = []
    for top in sorted(tops, key=rank):
        ordered.append((top, False))
        ordered.extend((child, True) for child in children.get(id(top), []))
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


def _pool_bar(row: dict[str, Any], cells: int, unicode: bool) -> str:
    """What is left as a bar; a non-zero remainder shows at least one cell, and a row without a value shows none."""
    glyphs = RICH_GLYPHS[unicode]
    if not _has_value(row):
        return glyphs["none"] * cells
    remaining = float(row["remaining"])
    filled = max(int(remaining * cells + 0.5), 1 if remaining > 0 else 0)
    return glyphs["full"] * filled + glyphs["empty"] * (cells - filled)


def _pool_section(rows: list[dict[str, Any]], width: int, unicode: bool, keep: str | None = None, *,
                  rich: bool = False, paint: bool = False) -> tuple[list[str], int]:
    """`POOLS n` with the column titles, then at most eight rows: POOL STATE LEFT RESET PACE FAMILY.

    The rich view (P21) draws the head as a rule, puts a state glyph before the state word and a bar of what is left
    after it (POOL STATE bar LEFT ...). A decoration never costs a data column: the glyph stays only when the table
    shows the columns of the plain view, and the bar takes the cells left over when every cell is whole (at most
    BAR_CELLS; fewer than BAR_MIN: no bar), so it shrinks and goes before a column drops or the FAMILY note is cut."""
    ordered = _ordered_pools(rows)
    shown = ordered[:POOLS_CAP]
    glyphs = RICH_GLYPHS[unicode]
    branch = "└─ " if unicode else "`- "
    head = f"POOLS {len(ordered)}"
    table = [[head, "STATE", "LEFT", "RESET", "PACE", "FAMILY"]]
    for row, child in shown:
        peer, pool = _cell(row["peer"], unicode, "-", keep), _cell(row["pool"], unicode, "-", keep)
        name = f"{' ' * _width(peer)} {branch}{pool}" if child else f"{peer} {pool}"
        family = str(row["meaning"]) + (f"; {_na_note(row)}" if row["state"] == "n/a" else "")
        table.append([" " + name, str(row["state"]), str(row["left"]), str(row["resets"]),
                      _pace_glyph(str(row["pace"]), unicode), _cell(family, unicode, "-", keep)])
    # Whole columns drop from the right (FAMILY first). FAMILY shrinks only down to its widest family name, so a long
    # n/a note is cut (with the cut mark) rather than taking the whole column away; no other cell is ever cut.
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
        bars = ["", *(_pool_bar(row, min(BAR_CELLS, room), unicode) for row, _ in shown)]
        chosen = [[*cells[:2], bar, *cells[2:]] for bar, cells in zip(bars, chosen)]
        names.insert(2, "BAR")
        right, elastic = (3, 5), 6
    colours = [None, *(_pool_colour(row) for row, _ in shown)]

    def painter(index: int, col: int, text: str) -> str:
        colour = colours[index]
        if index == 0:
            return text
        if names[col] == "FAMILY":
            return _paint(text, RICH_SGR["dim"])
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
    return observations, quota, rows


def _with_min_quota(payload: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """P9, the one rule of both frames: the read model's `min_quota` is as of the read, and a redraw ages the pools,
    so line 1 takes its pool from the aged rows. A payload without the human observations cannot age a stale pool
    (its last value is not public): it keeps the read model's `min_quota`."""
    return {**payload, "min_quota": _min_quota(rows)} if "_human_observations" in payload else payload


def _render_compact_frame(payload: dict[str, Any], *, view: str, color: bool, unicode: bool,
                          width: int, height: int | None, keep: str | None = None) -> str:
    width = max(1, min(100, width) - 1)  # leave the terminal's autowrap column unused
    rich = view == "rich"  # P21: the rich look; `unicode` picks its glyphs and `paint` adds the colour
    paint = color and rich and "NO_COLOR" not in os.environ
    sep = f" {RICH_GLYPHS[unicode]['sep']} " if rich else " | "
    groups = _obj(payload.get("groups"))
    selected = payload.get("selected_groups")
    selected_ids = set(cast(list[str], selected)) if isinstance(selected, list) else set(groups)
    read_at = _number(payload.get("read_at"))
    now = read_at if read_at is not None else 0
    elapsed = _number(payload.get("display_elapsed_seconds")) or 0
    consumers: dict[str, list[str]] = {}
    configured_profiles = _items(payload.get("configured_profiles"))
    for profile in configured_profiles:
        name = _clean(profile.get("profile") or "?")
        peer, _, suffix = name.partition(".")
        for raw_pool in cast(list[object], _obj(_obj(profile.get("fields")).get("pools")).get("value") or []):
            consumers.setdefault(f"{peer}:{_clean(raw_pool)}", []).append(suffix)
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
    header = [_paint(title, RICH_SGR["dim"]) if paint else title, *([f"Fields hidden{sep}--expand"] if cut else [])]
    errors = _obj(payload.get("errors"))
    nearest = payload.get("_nearest_store_path")
    same_store = bool(nearest and store.get("path") and os.path.normcase(os.path.abspath(str(nearest))) == os.path.normcase(os.path.abspath(str(store["path"]))))
    read_errors = any(value != "truncated" for value in errors.values())
    if (not same_store or payload.get("status") in {"FAILED", "ERROR"} or read_errors) and store.get("path"):
        header.extend(_compact_wrap(f"DB: {_clean(store['path'])}", width))
    if payload.get("_store_note"):  # #338: --db overrides a PEERHUB_DB that points elsewhere
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
    if "activity" in selected_ids:
        frame.running, frame.running_total, frame.recent = _activity_sections(groups, width, unicode, keep,
                                                                               rich=rich, paint=paint)
        frame.leads, frame.leads_total = _leads_section(groups, now + elapsed, width, unicode, keep, rich=rich, paint=paint)
    if "capacity" in selected_ids:
        frame.suggest = _suggest_lines(_compact_suggestions(configured_profiles, rows), width, unicode)
        if rows:
            frame.pools, frame.pools_total = _pool_section(rows, width, unicode, keep, rich=rich, paint=paint)
        else:
            notes = (["no quota evidence", "next: peerhub observation refresh"] if height is not None
                     else _compact_wrap("no quota evidence; next: peerhub observation refresh", width))
            frame.pools = [_head_rule("POOLS 0", width, unicode, paint) if rich else "POOLS 0", *notes]
    return fit_compact_frame(frame, width=width, height=height, unicode=unicode, keep=keep)


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


def render_grouped_frame(payload: dict[str, Any], *, view: str = "rich", color: bool = False,
                         unicode: bool = True, width: int = 100, height: int | None = None,
                         expand: bool = False, text_encoding: str | None = None) -> str:
    """Compact human default; the established renderer supplies expanded detail.

    `text_encoding` is the output's encoding: without `unicode`, text cells keep what it can encode (A5)."""
    payload = _display_plan_omissions(payload)
    if expand:
        return _render_expanded_frame(payload, view=view, color=color, unicode=unicode,
                                      width=width, height=height, expand=True, keep=text_encoding)
    return _render_compact_frame(payload, view=view, color=color, unicode=unicode,
                                 width=width, height=height, keep=text_encoding)


def _render_expanded_frame(payload: dict[str, Any], *, view: str = "rich", color: bool = False,
                         unicode: bool = True, width: int = 100, height: int | None = None,
                         expand: bool = False, keep: str | None = None) -> str:
    """Fit text first, then apply the optional quota palette without changing alignment."""
    color = color and view == "rich" and "NO_COLOR" not in os.environ
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
    display_groups = {**groups,
        "capacity": {**cap, "data": {**data, "consumers": {key: ",".join(sorted(set(names))) for key, names in consumers.items()}}}}
    activity = _obj(groups.get("activity"))
    if isinstance(activity.get("data"), Mapping):  # #376 P2: what an ended ask's row is joined to (display data only)
        display_groups["activity"] = {**activity, "data": {**_obj(activity.get("data")),
                                                             "_session_contexts": _session_contexts(groups)}}
    # X5: the plan the provider reports is display text only, shown beside the peer's quota in the PEERS block.
    plan_windows: dict[str, tuple[str, list[str]]] = {}
    for obs in _items(data.get("observations")):
        reason = _clean(obs.get("reason") or "")
        if obs.get("state") == "ABSENT" and reason.startswith(_NOT_REPORTED):
            pool = _clean(obs.get("pool_ref") or "").split(":")[-1]
            plan = _na_plan(obs, reason)
            window = pool.split("-", 1)[-1].lower() if "-" in pool else pool.lower()
            plan_windows.setdefault(_clean(obs.get("peer_id") or "?"), (plan, []))[1].append(window)
    peers_group = _obj(groups.get("peers"))
    if plan_windows and peers_group:
        display_groups["peers"] = {**peers_group, "data": {**_obj(peers_group.get("data")), "plan_notes": {
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
    payload = _with_min_quota(payload, _frame_pools(payload, {})[2])
    verdict = verdict_line(payload, max(1, width - 1 - (2 if color else 0)), unicode, keep)
    if color:  # P21: the pill of the compact frame (fitted above for its padding); the words of line 1 are unchanged
        verdict = _decorate_verdict(verdict, " | ", "", max(1, width - 1), True)
    # #374: the verdict is line 1; the grouped frame below it is laid out for one terminal line less.
    inner_height = None if height is None else height - 1
    overhead = 1 + len(store_lines)
    rendered = _render_grouped_frame(display, view=view, color=color, unicode=unicode, width=width,
                                     height=inner_height, expand=expand, reserved_lines=overhead, keep=keep)
    if (height is not None and height <= 1) or width <= 1:
        return ""
    if inner_height is not None and inner_height - overhead <= 3:
        tiny = [verdict, *rendered.splitlines()][:max(0, inner_height)]
        return "\n".join(tiny) + "\n"
    lines = rendered.splitlines()
    start = next((i for i, line in enumerate(lines) if "QUOTA [" in line), None)
    if start is None:
        start = 0
    end = next((i for i in range(start + 1, len(lines))
                if _ANSI.sub("", lines[i]).startswith(("-- ", "── "))
                or "rows hidden" in lines[i]
                or lines[i].startswith(("Warning:", "Next:", "+"))), len(lines))
    legend = "STATE: INSUFF=insufficient projection data; THREAT=exhausts before reset"
    if (_width(legend) <= width - 1 and (inner_height is None or len(lines) < inner_height - 1 - overhead)
            and any("PEER" in line and "POOL" in line for line in lines[start:end])):
        lines.insert(end, legend)
    report = _obj(_obj(_obj(groups.get("peers")).get("data")).get("facts_report"))
    # Every facts_report reason is a drift reason ("ag: unknown_pool", "cc: version_drift").
    drift_peers = sorted({str(reason).split(":")[0] for reason in cast(list[object], report.get("reasons") or [])})
    if drift_peers:
        footer += "; DRIFT:" + ",".join(drift_peers)
    lines[1 if lines else 0:1 if lines else 0] = store_lines
    lines.insert(0, verdict)
    # The legend moved here from the compact frame (#374). A bounded (live) view ends with the `| more` command that
    # prints the whole frame, so the legend prints in the unbounded frame only and never displaces data rows.
    if height is None:
        lines.extend(part for text in MONITOR_LEGEND for part in _compact_wrap(text, max(1, width - 1)))
    lines.append(_fit(footer, max(0, width - 1), unicode))
    if height is not None:
        lines = lines[:max(0, height - 1)]
    return "\n".join(lines) + ("\n" if lines else "")


def _render_grouped_frame(payload: dict[str, Any], *, view: str = "rich", color: bool = False,
                         unicode: bool = True, width: int = 100, height: int | None = None,
                         expand: bool = False, reserved_lines: int = 0, keep: str | None = None) -> str:
    """Render a cached schema-2 frame; height enables bounded interactive layout."""
    if height is None:
        return _render_unbounded_frame(payload, view=view, color=color, unicode=unicode, width=width, expand=expand,
                                       keep=keep)
    width = max(1, width)
    height = max(1, height)
    target_width = width - 1
    if target_width <= 0 or height <= 1:
        return ""
    tiny_height = height if reserved_lines and height - reserved_lines <= 3 else None
    height = max(3 if tiny_height is not None else 1, height - reserved_lines)
    max_lines = height - 1
    refresh_clean = str(payload.get("refresh_state") or "")
    warning_labels = _safe_refresh_warning_labels(payload.get("refresh_warnings"))
    stopped_agy = (refresh_clean == "STOPPED · AGY TOKEN USE")

    def finish(lines: list[str], *, tail: bool) -> str:
        # One exit for every return path. tail=True appends the agy-stop hint and warnings (frames that end
        # the normal way); tail=False is for frames that already end with their own omission notice.
        # Every path then gets the same height cap and ASCII fold, so the steps cannot drift apart.
        # Reads from the enclosing frame: max_lines, stopped_agy, warning_labels, target_width, unicode.
        if tail:
            if stopped_agy and len(lines) < max_lines:
                lines.append(_fit("Next: --allow-agy-token-use to continue", target_width, unicode))
            for warning in warning_labels:
                if len(lines) < max_lines:
                    lines.append(_fit(f"Warning: {warning}", target_width, unicode))
        lines = lines[:max_lines]
        if not unicode:  # A5: text the output encoding can show stays; the decoration is ASCII
            lines = [_ascii(line.replace(" · ", " | ").replace("·", "|"), keep) for line in lines]
        return "\n".join(lines) + "\n"

    selected = payload.get("selected_groups")
    raw_group_ids = [str(g) for g in cast(Sequence[Any], selected)] if isinstance(selected, Sequence) and not isinstance(selected, str) else []
    groups = _obj(payload.get("groups"))
    if not raw_group_ids:
        raw_group_ids = [str(g) for g in groups.keys()]
    group_ids = _reorder_groups(raw_group_ids)

    if height == 2:
        hint = "peerhub monitor --snapshot --group capacity --expand" if width >= 64 else "--expand"
        return finish([_fit(f"+{len(group_ids)} {hint}", target_width, unicode)], tail=False)

    status_label = _clean(payload.get("status") or "UNKNOWN")
    statuses = [str(_obj(groups.get(gid)).get("status") or "UNKNOWN").upper() for gid in group_ids]
    errors = sum(s in {"ERROR", "FAILED"} for s in statuses)
    partial = sum(s in {"PARTIAL", "STALE", "UNKNOWN", "UNAVAILABLE"} for s in statuses)
    refresh_state = _clean(payload.get("refresh_state") or "").upper()
    read_at = _number(payload.get("read_at"))
    next_refresh = _number(payload.get("next_refresh_in"))
    elapsed_seconds = _number(payload.get("display_elapsed_seconds")) or 0.0

    title_fields_hidden = False
    if refresh_state or next_refresh is not None:
        stamp = _utc_text(read_at + elapsed_seconds, "%Y-%m-%d %H:%MZ") if read_at is not None else None
        details = [status_label, f"{errors} err / {partial} partial" if errors or partial else ""]
        fitted_title, dropped = _fit_live_title(refresh_state, next_refresh, stamp or "", details, target_width, unicode)
        title_fields_hidden = dropped and "fields hidden" not in fitted_title
    else:
        title = f"PeerHub Monitor | {status_label}"
        if errors or partial:
            title += f" | {errors} err {'·' if unicode else '|'} {partial} partial"
        if width < 48:
            title = f"Monitor {status_label}"
            if errors or partial:
                title += f" E{errors}/P{partial}"
        elif read_at is not None:
            stamp = _utc_text(read_at, "%H:%M:%SZ" if width >= 120 else "%Y-%m-%d %H:%M:%SZ")
            if stamp:
                title += " | " + stamp
        fitted_title = _fit_fields(title, target_width, unicode)
    lines: list[str] = [fitted_title]
    if title_fields_hidden:
        lines.append(_fit("Title fields hidden", target_width, unicode))

    extra_overhead = (1 if stopped_agy else 0) + len(warning_labels)

    # Prepare blocks: list of (gid, priority, heading_rule, [content_lines])
    blocks: list[tuple[str, int, str, list[str]]] = []
    for gid in group_ids:
        grp = _obj(groups.get(gid))
        st = _clean(grp.get("status") or "UNKNOWN").upper()
        lbl = _group_label(gid, grp, st, read_at, elapsed_seconds)
        rule = _section_rule(lbl, target_width, unicode, color)
        data_rows, table_size = _group_lines(gid, grp, width=target_width, unicode=unicode,
                                 elapsed_seconds=elapsed_seconds, read_at=read_at, expand=expand, color=color, bounded=True,
                                 keep=keep)
        error_map = _obj(payload.get("errors"))
        error_val = error_map.get(gid)
        if error_val is None:
            deps = {
                "overview": ("sessions", "execution"), "peers": ("sessions", "capacity"),
                "sessions": ("sessions", "execution"),
                "usage": ("sessions", "execution"), "context": ("sessions", "execution"),
                "runtime": ("sessions", "execution"), "execution": ("execution",),
                "coverage": ("execution",), "capacity": ("capacity",), "store": ("store",),
            }
            error_val = next((error_map[k] for k in deps.get(gid, (gid,)) if k in error_map), None)
        if st in {"ERROR", "FAILED"}:
            data_rows.insert(0, _safe_error_reason(error_val))

        content_lines: list[str] = []
        for r in data_rows:
            if gid == "capacity":
                content_lines.append(_fit_fields(r, target_width, unicode, painted=color, indent=True))
            else:
                content_lines.append(_fit_fields("  " + r, target_width, unicode))
        blocks.append((gid, _priority(grp), rule, content_lines))
        if gid == "capacity" and "peers" in group_ids and st not in {"ERROR", "FAILED"}:
            if table_size < len(content_lines):
                blocks[-1] = (gid, _priority(grp), rule, content_lines[:table_size])
                blocks.append(("capacity_details", _priority(grp),
                               _section_rule(f"QUOTA [{st}] notes / ACCOUNTS", target_width, unicode, color),
                               content_lines[table_size:]))

    peer_count = len(_items(_obj(_obj(groups.get("peers")).get("data")).get("peers")))
    peer_priority = any(block[0] == "capacity" for block in blocks) and "peers" in group_ids

    def summary_size(block: tuple[str, int, str, list[str]]) -> int:
        count = max(1, peer_count) if peer_priority and block[0] == "peers" else 3 if block[0] == "usage" else 1
        return min(count, len(block[3]))

    def minimum(block: tuple[str, int, str, list[str]]) -> int:
        return 1 + summary_size(block) + int(len(block[3]) > summary_size(block))

    # Peer spacing yields before any data rows, including section details.
    required = len(lines) + extra_overhead + sum(1 + len(block[3]) for block in blocks)
    if required > height - 1:
        blocks = [(gid, priority, rule, [row for row in content if row]) for gid, priority, rule, content in blocks]

    if tiny_height is not None:
        hidden = sum(len(block[3]) for block in blocks)
        if tiny_height >= 3 and width >= 60:
            title = _fit(f"PeerHub Monitor | +{hidden} rows hidden; all rows:", target_width, unicode)
            hint = "peerhub monitor --snapshot --group all --expand" + (" | more" if expand else "")
            return finish([title, hint], tail=False)
        notice = f"+{hidden} rows hidden | --expand"
        if _width(notice) > target_width:
            notice = f"+{hidden} --expand"
        return finish([_fit(notice, target_width, unicode)], tail=False)

    # Quota-first height allocation
    omitted_quota = 0
    cap_block = next((b for b in blocks if b[0] == "capacity"), None)
    other_blocks = [b for b in blocks if b[0] != "capacity"]
    if peer_priority:
        order = {"alerts": -2, "activity": -1, "peers": 0, "capacity_details": 1, "execution": 2, "usage": 3, "sessions": 4}
        other_blocks.sort(key=lambda block: (order.get(block[0], 5), block[1]))
    front_blocks = [block for block in other_blocks if block[0] in _FRONT_GROUPS]

    # A complete frame needs no omission notice or allocation reserve. This also
    # lets the separators fit exactly without ever displacing a detail row.
    if len(lines) + extra_overhead + sum(1 + len(block[3]) for block in blocks) <= max_lines:
        for block in front_blocks + ([cap_block] if cap_block is not None else []) + [
                block for block in other_blocks if block not in front_blocks]:
            lines.append(block[2])
            lines.extend(block[3])
        return finish(lines, tail=True)
    front_at = len(lines)  # alerts and activity print above the quota table even though quota is allocated first

    extra_overhead += len(_hidden_lines(sum(len(block[3]) for block in blocks), len(blocks),
                                        target_width, unicode, expand=expand)) - 1

    visible_other: list[tuple[str, int, str, list[str]]] = []

    if cap_block is None and len(other_blocks) == 1 and not extra_overhead:
        content = other_blocks[0][3]
        budget = max_lines - len(lines)
        if 2 <= budget < 1 + len(content):
            shown = budget - 1  # reclaim the heading; reserve one exact omission line
            lines.extend(content[:shown])
            lines.extend(_hidden_lines(len(content) - shown, 0, target_width, unicode, expand=expand))
            return finish(lines, tail=False)

    def allocate_other(budget: int) -> list[tuple[str, int, str, list[str]]]:
        if peer_priority:
            visible: list[tuple[str, int, str, list[str]]] = []
            for block in other_blocks:
                if minimum(block) > budget - 1:
                    if not visible and block[0] == "peers" and budget >= 3:
                        return [block]
                    break
                visible.append(block)
                budget -= minimum(block)
            return visible
        # Even when every summary fits, hidden details need a disclosure row.
        full_size = sum(1 + max(1, len(block[3])) for block in other_blocks)
        summary_budget = budget - int(full_size > budget)
        count = max(0, summary_budget // 2)
        if count >= len(other_blocks):
            return list(other_blocks)
        ranked = sorted(other_blocks, key=lambda block: (block[1], other_blocks.index(block)))
        return ranked[:count]

    if cap_block is not None:
        cap_rule = cap_block[2]
        cap_rows = cap_block[3]
        budget = max_lines - len(lines) - extra_overhead
        quota_budget = budget - (1 if other_blocks else 0)
        show_cap_rule = quota_budget >= 1 + len(cap_rows)
        if not show_cap_rule:
            cap_status = _clean(_obj(groups.get("capacity")).get("status") or "UNKNOWN").upper()
            lines[0] = _fit_fields(f"{fitted_title} | QUOTA [{cap_status}]", target_width, unicode)
        quota_lines_needed = len(cap_rows) + int(show_cap_rule)

        if quota_budget >= quota_lines_needed:
            # All quota rows fit!
            if show_cap_rule:
                lines.append(cap_rule)
            lines.extend(cap_rows)
            # Allocate to other blocks
            rem_budget = max_lines - len(lines) - extra_overhead
            visible_other = allocate_other(rem_budget)
        elif budget >= 2:
            # Partial quota rows fit; lower sections suppressed
            lines.append(cap_rows[0] if cap_rows else "")  # table header
            data_cap_rows = cap_rows[1:]
            can_show = max(0, budget - 2)  # reserve 1 for omission
            lines.extend(data_cap_rows[:can_show])
            omitted_quota = len(data_cap_rows) - can_show
        else:
            omitted_quota = len(cap_rows)
    else:
        # No capacity group, allocate other blocks by severity
        budget = max_lines - len(lines) - extra_overhead
        visible_other = allocate_other(budget)

    # Allocate first, then emit each complete block together with its own cut notice.
    # Reserving a summary and local notice prevents details leaking beneath a later header.
    available = max(0, max_lines - len(lines) - extra_overhead)
    while visible_other and sum(minimum(ob) for ob in visible_other) + 1 > available:
        if len(visible_other) == 1 and available >= 3:
            break  # the final group's cut notice can share the global disclosure
        visible_other.pop()
    counts = [summary_size(ob) for ob in visible_other]
    if len(counts) == 1:
        counts[0] = min(counts[0], max(1, available - 2))
    allocated = sum(1 + shown + int(len(ob[3]) > shown) for ob, shown in zip(visible_other, counts))
    share_tail_notice = len(visible_other) == 1 and allocated + 1 > available
    spare = max(0, available - allocated - 1 + int(share_tail_notice))
    allocation_order = sorted(range(len(visible_other)), key=lambda i:
                              (visible_other[i][0] == "peers", i)) if peer_priority else range(len(visible_other))
    for i in allocation_order:
        ob = visible_other[i]
        extra = len(ob[3]) - counts[i]
        # Finishing a block reclaims its local omission notice.
        reclaim = int(extra > 0 and not share_tail_notice)
        take = extra if extra <= spare + reclaim else min(extra, spare)
        counts[i] += take
        spare -= max(0, take - (reclaim if take == extra else 0))

    omitted_details = 0
    for ob, shown in zip(visible_other, counts):
        chunk = [ob[2], *ob[3][:shown]]
        hidden = len(ob[3]) - shown
        omitted_details += hidden
        if hidden:
            if len(lines) + len(chunk) < max_lines - extra_overhead - 1:
                chunk.append(_fit(f"  +{hidden} more", target_width, unicode))
        if ob[0] in _FRONT_GROUPS:
            lines[front_at:front_at] = chunk
            front_at += len(chunk)
        else:
            lines.extend(chunk)

    omitted_group_rows = sum(len(ob[3]) for ob in other_blocks if ob not in visible_other)
    omitted_details += omitted_group_rows
    omitted_blocks = sum(ob[0] != "capacity_details" for ob in other_blocks if ob not in visible_other)

    if omitted_quota or omitted_blocks or omitted_details:
        hidden_rows = omitted_quota + omitted_details
        lines.extend(_hidden_lines(hidden_rows, omitted_blocks, target_width, unicode,
                                   expand=expand, quota=bool(omitted_quota)))

    return finish(lines, tail=True)

