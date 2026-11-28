"""Pool table layout and notes for the grouped monitor."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from peerhub._console import ANSI_ESCAPE
from peerhub.core.text import clean_text
from peerhub.core.scalars import finite_converted_number as finite_number
from peerhub.cli.monitor_layout import (
    RICH_GLYPHS,
    RICH_SGR,
    RULE_PREFIX_WIDTH,
    cell_text,
    compact_age,
    wrap_text,
    fit_text,
    paint_text,
    cell_width,
)
from peerhub.cli.monitor_legend import POOLS_HEADING, POOL_MARGIN_COLUMN, QUOTA_NA
from peerhub.cli.monitor_links import link_profiles, link_pool_state
from peerhub.cli.quota_format import (
    BAR_CELLS,
    format_exhaust_reset,
    format_percent,
    format_pool_left,
    format_pool_pace,
    missing_pool_fact,
    pool_duration,
    quota_bar as _bar_quota,
)
from peerhub.extensions.observation_model import DISABLED_REASON
from peerhub.extensions.pool_health import PACE_GAP_BASELINE, PoolHealth, pace_gaps, parent_strictness
from peerhub.extensions.quota_windows import (
    EXPIRED_WINDOW_REASON, UNKNOWN_WINDOW_SECONDS, WEEK_SECONDS, parse_window, pool_family, scoped_weekly_parents,
)


BAR_MIDDLE_CELLS = 16
BAR_MAX_CELLS = 40


def pool_order(peer: str, pool: str) -> tuple[str, str, int, str]:
    """Identity order for the underlying pool rows: peer, family, window length, name."""
    return (peer, pool_family(pool), parse_window(pool)[0], pool)


def _quota_parents(rows: list[dict[str, Any]]) -> dict[int, int]:
    """Find the unambiguous consumer-based parents within one account."""
    by_name = {str(row["pool"]): row for row in rows}
    if len(by_name) != len(rows):
        return {}
    windows = {name: parse_window(name)[0] for name in by_name}
    members = {name: set(row["profiles"]) for name, row in by_name.items()}
    if any(a & b and not (a <= b or b <= a) for a in members.values() for b in members.values()):
        return {}
    parents: dict[int, int] = {}
    for child, profiles in members.items():
        if not profiles:
            continue
        window = windows[child]
        eligible = [name for name in by_name if name != child and profiles <= members[name]
                    and window <= windows[name] < UNKNOWN_WINDOW_SECONDS
                    and (profiles < members[name] or window < windows[name])]
        minimal = [name for name in eligible if not any(members[other] < members[name] for other in eligible)]
        if not minimal:
            continue
        shortest = min(windows[name] for name in minimal)
        candidates = [name for name in minimal if windows[name] == shortest]
        if len(candidates) > 1:
            return {}
        parents[id(by_name[child])] = id(by_name[candidates[0]])
    return parents


def quota_layout(rows: list[dict[str, Any]], unicode: bool, *,
                 order: dict[tuple[str, str], int] | None = None) -> list[dict[str, Any]]:
    """Derive a forest from each account's configured consumers, sorted by POOLS rank."""
    result: list[dict[str, Any]] = []
    order = order or {}

    def rank(row: dict[str, Any]) -> int:
        return order.get((str(row["peer"]), str(row["pool_ref"])), len(order))

    by_peer: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not row.get("retired") and row["state"] != QUOTA_NA:
            by_peer.setdefault(str(row["peer"]), []).append(row)
    for peer, active in sorted(by_peer.items(), key=lambda entry: (min(map(rank, entry[1])), entry[0])):
        by_account: dict[str, list[dict[str, Any]]] = {}
        for row in active:
            by_account.setdefault(str(row["pool_ref"]).rpartition(":")[0], []).append(row)
        parents = {child: parent for account in by_account.values() for child, parent in _quota_parents(account).items()}
        children: dict[int, list[dict[str, Any]]] = {}
        for row in active:
            if (parent := parents.get(id(row))) is not None:
                children.setdefault(parent, []).append(row)
        for siblings in children.values():
            siblings.sort(key=lambda row: (-parse_window(str(row["pool"]))[0], str(row["pool"])))

        first_peer_row = True

        def visit(row: dict[str, Any], prefix: str, continuation: str) -> None:
            nonlocal first_peer_row
            result.append({**row, "display_pool": prefix + str(row["pool"]), "display_peer": peer if first_peer_row else ""})
            first_peer_row = False
            siblings = children.get(id(row), [])
            for i, child in enumerate(siblings):
                last = i == len(siblings) - 1
                branch = ("└─ " if last else "├─ ") if unicode else ("`- " if last else "|- ")
                stem = "   " if last else "│  " if unicode else "|  "
                visit(child, continuation + branch, continuation + stem)

        def tree_rank(row: dict[str, Any]) -> int:
            # A family sorts by its best-ranked member, which can be a child.
            return min([rank(row), *(tree_rank(child) for child in children.get(id(row), []))])

        def root_order(row: dict[str, Any]) -> tuple[int, str, int, str]:
            name = str(row["pool"])
            return tree_rank(row), pool_family(name), parse_window(name)[0], name

        for row in sorted((row for row in active if id(row) not in parents), key=root_order):
            visit(row, "", "")
    return result


def _placeholder_line(line: str, suffix: str, width: int, code: str | None) -> list[str]:
    """Keep the reset placeholder note when columns are dropped."""
    if cell_width(line + " " + suffix) <= width:
        return [line + " " + paint_text(suffix, code)]
    return [line, *(paint_text(part, code) for part in wrap_text(suffix, width))]


def na_plan(item: dict[str, Any], reason: str) -> str:
    """The plan named in a not-applicable pool note: the account-supplied `display_plan` when present, else the stored reason's `plan=`."""
    shown = item.get("display_plan")
    if isinstance(shown, str) and shown:
        return clean_text(shown)
    return clean_text(reason.split("plan=", 1)[1]) if "plan=" in reason else "unknown"


def na_reason(row: dict[str, Any], reason: str) -> str:
    return reason if reason.startswith(DISABLED_REASON) else f"not reported (plan {na_plan(row, reason)})"


POOLS_USE_FIRST_ASCENDING = True


def _has_value(row: dict[str, Any]) -> bool:
    return row["state"] not in {QUOTA_NA, "?", "reset"} and finite_number(row["remaining"]) is not None


def display_min_quota(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Line 1's `min quota` from the rows POOLS draws (P9): the lowest 7-day pool that has a percent at the time of
    this redraw, with the state the table shows. The rule is monitor_quota.min_quota's."""
    weekly = [(round(float(row["remaining"]) * 100), f"{row['peer']} {row['pool']}", row) for row in rows
              if str(row["pool"]).rpartition("-")[1:] == ("-", "7D") and _has_value(row)]
    if not weekly:
        return None
    left, name, row = min(weekly, key=lambda item: item[:2])
    return {"pool": name, "left_pct": left, "state": row["state"], "aged": bool(row["aged"])}


def is_reset_pending(row: dict[str, Any]) -> bool:
    """A short window whose reading expired at its reset: POOLS keeps a placeholder until the next quota reading (#495)."""
    return (row["state"] == QUOTA_NA and row["retired"] and row["reason"] == EXPIRED_WINDOW_REASON
            and row["reading"].get("_reset_passed") and row["family"] != "?" and parse_window(str(row["pool"]))[0] < WEEK_SECONDS)


def ordered_pools(rows: list[dict[str, Any]], *, pending: bool = False) -> list[tuple[dict[str, Any], int]]:
    """Sort POOLS by each family's weekly parent, retaining all reported windows."""
    placeholders = [row for row in rows if pending and is_reset_pending(row)]
    # An old disabled short window still appears as aged evidence in expanded QUOTA.
    rows = [row for row in rows if row["state"] != QUOTA_NA
            and not (row["aged"] and row["reason"].startswith(DISABLED_REASON)
                     and parse_window(str(row["pool"]))[0] < WEEK_SECONDS)]
    scoped = scoped_weekly_parents(rows, strictness=lambda row: parent_strictness(row["health"]))

    def family_key(row: dict[str, Any]) -> tuple[str, str, str]:
        account = str(row["pool_ref"]).rpartition(":")[0]
        return str(row["peer"]), str(row["family"]), account

    weekly: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        if row["family"] != "?" and parse_window(str(row["pool"]))[0] == WEEK_SECONDS:
            weekly.setdefault(family_key(row), row)
    tops: list[dict[str, Any]] = []
    listed: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        window = parse_window(str(row["pool"]))[0]
        parent = scoped.get(id(row)) if window >= WEEK_SECONDS else weekly.get(family_key(row))
        if parent is None:
            tops.append(row)
        else:
            listed.setdefault(id(parent), []).append(row)
    for row in placeholders:
        if (parent := weekly.get(family_key(row))) is not None:
            listed.setdefault(id(parent), []).append(row)
    for children_rows in listed.values():
        children_rows.sort(key=lambda row: (id(row) in scoped,
                                            -parse_window(str(row["pool"]))[0], str(row["pool"])))

    def use_first(row: dict[str, Any]) -> tuple[int, float, str, str]:
        peer, name = str(row["peer"]), str(row["pool"])
        if row["state"] == "out" or any(child["state"] == "out" and id(child) not in scoped
                                         for child in listed.get(id(row), ())):
            return 2, 0, peer, name
        pace = finite_number(row.get("pace_pt"))  # a top row (weekly, monthly or a lone short window) sorts by its own PACE
        if pace is None:
            return 1, 0, peer, name
        return 0, pace if POOLS_USE_FIRST_ASCENDING else -pace, peer, name

    ordered: list[tuple[dict[str, Any], int]] = []

    def append_tree(row: dict[str, Any], depth: int) -> None:
        ordered.append((row, depth))
        for child in listed.get(id(row), ()):
            append_tree(child, depth + 1)

    tops.sort(key=use_first)
    for top in tops:
        append_tree(top, 0)
    return ordered


def limit_pool_rows(ordered: list[tuple[dict[str, Any], int]], cap: int) -> list[tuple[dict[str, Any], int]]:
    """Keep parents and tighter child windows ahead of nonbinding short windows when space is capped."""
    if len(ordered) <= cap:
        return ordered
    parents: list[dict[str, Any]] = []
    preferred: list[int] = []
    deferred: list[int] = []
    for index, (row, depth) in enumerate(ordered):
        del parents[depth:]
        short = parse_window(str(row["pool"]))[0] < WEEK_SECONDS
        if parents and short and row["state"] not in {QUOTA_NA, "low", "out"}:
            parent = parents[-1]
            tighter = _has_value(row) and _has_value(parent) and row["remaining"] < parent["remaining"]
            (preferred if tighter else deferred).append(index)
        else:
            preferred.append(index)
        parents.append(row)
    return [ordered[i] for i in sorted((preferred + deferred)[:cap])]


_POOL_COLUMNS = ("STATE", "BAR", "LEFT", "PACE(pp)", POOL_MARGIN_COLUMN, "EXH/RESET", "FAMILY", "AGE")
_POOL_MARGIN_WIDTH = 8
_POOL_COLUMN_GAP = "  "
POOL_NOTE_INDENT = "       "  # under the pool name of a child row ("    └─ ")
_STATE_COLOURS = {"ok": "ok", "low": "warn", "fast": "warn", "out": "out", "blocked": "out"}
_PACE_COLOURS = {"spend": "spend", "ok": "ok", "rest": "warn"}


def format_exhaust_margin(margin: float | None, *, unicode: bool = True) -> str:
    if margin is None:
        return missing_pool_fact(unicode)
    if margin == 0:
        return "0"
    sign = "-" if margin < 0 else "+"
    return fit_text(sign + pool_duration(abs(margin)), _POOL_MARGIN_WIDTH, unicode)


def _pool_bar(row: dict[str, Any], cells: int, unicode: bool, child: bool = False) -> str:
    facts: PoolHealth = row["health"]
    even_left = facts["even_left_pct"]
    remaining = row["remaining"] if _has_value(row) else None
    return _bar_quota(remaining, cells, unicode, None if even_left is None else even_left / 100, child=child)[1:-1]


def _pool_family_cell(row: dict[str, Any], width: int | None, unicode: bool, keep: str | None) -> str:
    label = cell_text(row["meaning"], unicode, keep=keep)
    profiles = [cell_text(profile, unicode, keep=keep) for profile in row["profiles"]]
    for count in range(len(profiles), -1, -1):
        names = ",".join(profiles[:count])
        extra = f"+{len(profiles) - count}" if count < len(profiles) else ""
        cell = label + (": " + " ".join(filter(None, (names, extra))) if profiles else "")
        if width is None or cell_width(cell) <= width:
            return cell
    return label


def _pool_dependency(row: dict[str, Any]) -> str:
    parent = row.get("depends_on")
    if not parent:
        return ""
    facts: PoolHealth = row["health"]
    if not facts["limited_by"]:
        return f"needs {parent}"
    note = f"{parent} out" if row["state"] == "blocked" else f"limited by {parent}"
    own = facts["own_left_pct"]
    return note if own is None else f"{note} (own {format_percent(own / 100)})"


def _pool_left_value_width(cell: str) -> int:
    return cell_width(cell.partition(" ")[0].removesuffix("!"))


def _pool_cells(row: dict[str, Any], unicode: bool, glyph: bool, guide_url: str | None = None) -> dict[str, str]:
    facts: PoolHealth = row["health"]
    state = facts["state"]
    glyphs = RICH_GLYPHS[unicode]
    age = finite_number(row["age_seconds"])
    state_text = link_pool_state(guide_url, state) if guide_url else state
    cells: tuple[str, ...] = (
        f"{glyphs.get(state, glyphs['aged'])} {state_text}" if glyph else state_text,
        "",
        format_pool_left(facts, age, unicode=unicode),
        format_pool_pace(facts["pace_pt"], unicode=unicode) + ("!" if facts["pace_band"] == "rest" else ""),
        format_exhaust_margin(facts["exhaust_margin_seconds"], unicode=unicode),
        format_exhaust_reset(facts, unicode=unicode),
        "",
        missing_pool_fact(unicode) if age is None else compact_age(age),
    )
    return dict(zip(_POOL_COLUMNS, cells))


def _paint_pool_cell(column: str, text: str, row: dict[str, Any], *, highlight_left: bool = False) -> str:
    facts: PoolHealth = row["health"]
    reserve = facts["reserve"]
    if column == "LEFT" and highlight_left and "%" in text:
        amount, gap, age = text.partition(" ")
        colour = "dim" if facts["stale"] else {"out": "out", "low": "warn"}.get(reserve or "", "title")
        return paint_text(amount, RICH_SGR[colour]) + gap + paint_text(age, RICH_SGR["dim"] if gap else None)
    if column == "STATE":
        colour = "bad" if row["reading"].get("state") == "ERROR" else _STATE_COLOURS.get(facts["state"], "dim")
    elif column in {"BAR", "PACE(pp)"}:
        colour = "out" if reserve == "out" else _PACE_COLOURS.get(facts["pace_band"] or "", "dim")
    elif column in {POOL_MARGIN_COLUMN, "EXH/RESET"} and facts["exhaust_before_reset"]:
        colour = "warn"
    elif column == "LEFT" and reserve == "low":
        return text.replace("!", paint_text("!", RICH_SGR["warn"]), 1)
    elif column == "LEFT" and reserve == "out":
        colour = "out"
    elif column == "FAMILY":
        colour = "dim"
    else:
        return text
    return paint_text(text, RICH_SGR[colour])


def pool_heading(pool_count: int) -> str:
    return f"{POOLS_HEADING} {pool_count}"


PoolEntry = tuple[str, dict[str, Any], bool, str]


def pool_table(entries: list[PoolEntry], all_rows: list[dict[str, Any]], width: int, unicode: bool,
                keep: str | None, *, rich: bool, paint: bool, details: bool = False,
                head: str = "PEER POOL", guide_url: str | None = None,
                profile_urls: Mapping[str, Any] | None = None, highlight_left: bool = False) -> tuple[list[str], list[list[str]], list[str]]:
    """Use the same column budget and cell formatting in POOLS and QUOTA."""
    width = max(1, width)
    active = [row for row in all_rows if row["state"] != QUOTA_NA]
    common = [_pool_cells(row, unicode, rich) for row in active]
    sizes = {name: max([cell_width(name), *(cell_width(cells[name]) for cells in common)]) for name in _POOL_COLUMNS}
    sizes[POOL_MARGIN_COLUMN] = _POOL_MARGIN_WIDTH
    left_cells = [cells["LEFT"] for cells in common]
    left_width = max([cell_width("LEFT"), *map(_pool_left_value_width, left_cells)])
    sizes["LEFT"] = left_width + max((cell_width(cell) - _pool_left_value_width(cell) for cell in left_cells), default=0)
    family_labels = sizes["FAMILY"]
    family_min = family_labels
    for row in active:
        label, profiles = cell_text(row["meaning"], unicode, keep=keep), row["profiles"]
        family_labels = max(family_labels, cell_width(label))
        family_min = max(family_min, cell_width(label + (f": +{len(profiles)}" if profiles else "")))
    sizes["FAMILY"] = family_min
    compact_order = ordered_pools(all_rows, pending=True)
    compact_names = [cell_width(f" {row['peer']} {row['pool']}") + 3 * depth for row, depth in compact_order]
    detail_layout = quota_layout(all_rows, unicode)
    detail_names = [cell_width(f"{row['peer']} {row['display_pool']}") for row in detail_layout]
    name_width = max([cell_width("PEER POOL"), *compact_names, *detail_names,
                      *(cell_width(name) for name, *_ in entries)])  # a placeholder row (#495) has no active row
    prefix = RULE_PREFIX_WIDTH if rich else 0
    columns: list[str] = [column for column in _POOL_COLUMNS if column not in {"BAR", "AGE"}]
    name_width = max(name_width, cell_width(pool_heading(len(compact_order))) + prefix)

    def room(names: Sequence[str]) -> int:
        return width - name_width - sum(sizes[name] + len(_POOL_COLUMN_GAP) for name in names)

    while room(columns) < 0 and len(columns) > 3:
        columns.pop()
    name_width = min(name_width, width)
    if "FAMILY" in columns:
        bar_room = room(columns) - len(_POOL_COLUMN_GAP)
        if bar_room + family_min - family_labels >= BAR_CELLS:
            sizes["BAR"] = min(BAR_MIDDLE_CELLS, max(BAR_CELLS, bar_room))
            columns.insert(1, "BAR")
            # At the minimum, a family label can yield its profile count to keep the bar.
            sizes["FAMILY"] += min(0, room(columns))
        # Reserve QUOTA's AGE in both tables once it fits. It uses space left by the bar's first stage.
        if room([*columns, "AGE"]) >= 0:
            columns.append("AGE")
    if "AGE" in columns and sizes["BAR"] == BAR_MIDDLE_CELLS:
        with_notes = sizes["FAMILY"]
        for row in active:
            family = _pool_family_cell(row, None, unicode, keep)
            dependency = _pool_dependency(row)
            width_with_note = cell_width(f"{family}; {dependency}" if dependency else family)
            with_notes = max(with_notes, width_with_note)
        sizes["FAMILY"] = min(with_notes, sizes["FAMILY"] + room(columns))
        sizes["BAR"] = min(BAR_MAX_CELLS, BAR_MIDDLE_CELLS + room(columns))
    if not details and "AGE" in columns:
        columns.remove("AGE")

    def line(name: str, cells: dict[str, str], row: dict[str, Any] | None = None) -> str:
        name = fit_text(name, name_width, unicode, indent=True)
        fields = [name + " " * max(0, name_width - cell_width(name))]
        for column in columns:
            value = cells.get(column, "")
            padding = max(0, sizes[column] - cell_width(value))
            ink = _paint_pool_cell(column, value, row, highlight_left=highlight_left) if paint and row and value else value
            leading = padding if column == "AGE" else 0
            if column == "LEFT":
                leading = max(0, left_width - _pool_left_value_width(value))
            fields.append(" " * leading + ink + " " * (padding - leading))
        return _POOL_COLUMN_GAP.join(fields).rstrip()

    header = line(head, {name: name if name != "BAR" else "" for name in columns})
    headers = [header] if cell_width(header) <= width else wrap_text(header, width)
    dim = RICH_SGR["dim"] if paint else None
    rendered: list[list[str]] = []
    for name, row, child, marker in entries:
        cells = _pool_cells(row, unicode, rich, guide_url) if row else {}
        dependency = _pool_dependency(row)
        if row and "BAR" in columns:
            cells["BAR"] = _pool_bar(row, sizes["BAR"], unicode, child)
        if row and "FAMILY" in columns:
            cells["FAMILY"] = _pool_family_cell(row, sizes["FAMILY"], unicode, keep)
            if profile_urls:
                cells["FAMILY"] = link_profiles(cells["FAMILY"], profile_urls)
            if dependency:
                family = f"{cells['FAMILY']}; {dependency}"
                if sizes["BAR"] >= BAR_MIDDLE_CELLS and cell_width(family) <= sizes["FAMILY"]:
                    cells["FAMILY"] = family
                    dependency = ""
        text = line(name, cells, row)
        if cell_width(text) > width:
            text, *continuations = wrap_text(ANSI_ESCAPE.sub("", text), width)
        else:
            continuations = []
        lines = [text, *continuations]
        # A note that does not fit the row goes below it, indented like a child row so it reads as that pool's note.
        if dependency:
            lines.extend(paint_text(POOL_NOTE_INDENT + part, dim)
                         for part in wrap_text(dependency, max(1, width - len(POOL_NOTE_INDENT))))
        if marker:
            lines[-1:] = _placeholder_line(lines[-1], marker, width, dim)
        rendered.append(lines)
    return headers, rendered, [name.lower() for name in reversed(_POOL_COLUMNS) if name not in columns]


def pool_gap(rows: list[dict[str, Any]], width: int, unicode: bool) -> list[str]:
    visible = [row for row in rows if not row["retired"] and row["state"] != QUOTA_NA
               and row["reading"].get("stored_state", row["reading"].get("state")) != "ABSENT"]
    healths: dict[str, PoolHealth | None] = {}
    for row in visible:
        pool = str(row["pool"])
        healths[pool] = None if pool in healths else row["health"]
    gaps = pace_gaps({pool: facts for pool, facts in healths.items() if facts is not None})["pools"]
    gaps = {name: pace for name, pace in gaps.items() if name in healths}
    if not gaps:
        return []
    values = ", ".join(f"{name} {format_pool_pace(pace, unicode=unicode)}" for name, pace in gaps.items())
    return wrap_text(f"gap vs {PACE_GAP_BASELINE} (pp): {values}", width)
