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
from peerhub.extensions.observation_model import ABSENT_FACT_REASONS, DISABLED_REASON
from peerhub.extensions.pool_health import PACE_GAP_BASELINE, PoolHealth, pace_gaps
from peerhub.extensions.quota_windows import (
    EXPIRED_WINDOW_REASON, UNKNOWN_WINDOW_SECONDS, WEEK_SECONDS, parse_window, pool_family, scoped_weekly_parents,
)


BAR_MIDDLE_CELLS = 16
BAR_MAX_CELLS = 40


def pool_order(peer: str, pool: str) -> tuple[str, str, int, str]:
    """Identity order for the underlying pool rows: peer, family, window length, name."""
    return (peer, pool_family(pool), parse_window(pool)[0], pool)


def _na_suffix(names: Sequence[str]) -> str:
    return f"({','.join(names)} {QUOTA_NA})"


def _retired_pool_note(peer: str, pools: str, plans: str) -> str:
    return f"{peer} {pools} {QUOTA_NA} (not reported by plan {plans})"


def quota_layout(rows: list[dict[str, Any]], unicode: bool, *, show_quota_notes: bool,
                  order: dict[tuple[str, str], int] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Derive a forest from configured consumers. For QUOTA, `order` gives each pool's POOLS rank."""
    result: list[dict[str, Any]] = []
    notes: list[str] = []
    hidden: dict[str, list[str]] = {}  # the n/a pools that have no parent row to carry their name
    noted: set[str] = set()  # peers whose retired pools already have their note
    order = order or {}

    def rank(row: dict[str, Any]) -> int:
        return order.get((str(row["peer"]), str(row["pool_ref"])), len(order))

    visible = [row for row in rows if not row.get("retired") and row["state"] != QUOTA_NA]
    peer_order = {peer: min(rank(row) for row in visible if row["peer"] == peer)
                  for peer in {str(row["peer"]) for row in visible}}
    for peer in sorted({str(row["peer"]) for row in (rows if show_quota_notes else visible)},
                       key=lambda p: (peer_order.get(p, len(order)), p)):
        peer_rows = [dict(row) for row in rows if row["peer"] == peer]
        retired = [row for row in peer_rows if row.get("retired")]
        if retired and show_quota_notes:
            hidden.setdefault(peer, []).extend(str(row["pool"]) for row in retired)
            expired = [str(row["pool"]) for row in retired if row.get("reason") == EXPIRED_WINDOW_REASON]
            omitted = [row for row in retired if row.get("reason") != EXPIRED_WINDOW_REASON]
            if expired:
                notes.append(f"{peer} {','.join(expired)} {QUOTA_NA} ({EXPIRED_WINDOW_REASON})")
            if omitted:
                plans = ",".join(sorted({str(row.get("display_plan") or "unknown") for row in omitted}))
                notes.append(_retired_pool_note(peer, ",".join(str(row["pool"]) for row in omitted), plans))
            noted.add(peer)
        active = [dict(row) for row in visible if row["peer"] == peer]
        unavailable = [row for row in peer_rows if row["state"] == QUOTA_NA and not row.get("retired")]
        first_peer_row = True

        def append_row(row: dict[str, Any], display_pool: str) -> None:
            nonlocal first_peer_row
            row.update(display_pool=display_pool, display_peer=peer if first_peer_row else "", suffix="")
            first_peer_row = False
            result.append(row)

        # Names are tree identities only when unique. Preserve every source row
        # in the flat fallback, including duplicate names with different values.
        if len({row["pool"] for row in active}) != len(active):
            for row in sorted(active, key=rank):
                append_row(row, str(row["pool"]))
            if show_quota_notes:
                hidden.setdefault(peer, []).extend(str(row["pool"]) for row in unavailable)
            continue
        by_name = {str(row["pool"]): row for row in active}
        windows = {name: parse_window(name)[0] for name in by_name}
        members = {name: set(row["profiles"]) for name, row in by_name.items()}
        parents: dict[str, str | None] = dict.fromkeys(by_name)
        flat = any(a & b and not (a <= b or b <= a) for a in members.values() for b in members.values())
        def parent_candidates(child: str, profiles: set[str], window: int) -> list[str]:
            if not profiles:
                return []
            eligible = [name for name in by_name if name != child and profiles <= members[name]
                        and window <= windows[name] < UNKNOWN_WINDOW_SECONDS
                        and (profiles < members[name] or window < windows[name])]
            minimal = [name for name in eligible if not any(members[other] < members[name] for other in eligible)]
            if not minimal:
                return []
            shortest = min(windows[name] for name in minimal)
            return [name for name in minimal if windows[name] == shortest]

        for child in by_name:
            if flat:
                break
            candidates = parent_candidates(child, members[child], windows[child])
            if len(candidates) > 1:
                flat = True
                break
            if candidates:
                parents[child] = candidates[0]
        if flat:
            parents = dict.fromkeys(by_name)
        if show_quota_notes:
            for row in unavailable:
                name = str(row["pool"])
                candidates = [] if flat else parent_candidates(name, set(row["profiles"]), parse_window(name)[0])
                if len(candidates) == 1:
                    by_name[candidates[0]].setdefault("folded", []).append(name)
                else:
                    hidden.setdefault(peer, []).append(name)
        children: dict[str, list[str]] = {name: [] for name in by_name}
        for child, parent in parents.items():
            if parent is not None:
                children[parent].append(child)
        for names in children.values():
            names.sort(key=lambda name: (-windows[name], name))

        def visit(name: str, prefix: str, continuation: str) -> None:
            row = by_name[name]
            append_row(row, prefix + name)
            for i, child in enumerate(children[name]):
                last = i == len(children[name]) - 1
                branch = ("└─ " if last else "├─ ") if unicode else ("`- " if last else "|- ")
                stem = "   " if last else "│  " if unicode else "|  "
                visit(child, continuation + branch, continuation + stem)

        def tree_rank(name: str) -> int:
            # A family sorts by its best-ranked member: a 30D root holding a 7D child ranks by that child.
            return min([rank(by_name[name]), *(tree_rank(child) for child in children[name])])

        for name in sorted((name for name in by_name if parents[name] is None),
                           key=lambda name: (tree_rank(name), pool_family(name), windows[name], name)):
            visit(name, "", "")
    if not show_quota_notes:
        return result, notes
    for row in result:  # an n/a pool with no parent row is named after its peer's first row
        folded = sorted(row.pop("folded", []), key=lambda name: (-parse_window(name)[0], name))
        names = [*folded, *(hidden.pop(row["peer"], []) if row["display_peer"] else [])]
        if names:
            row["suffix"] = _na_suffix(names)
    notes.extend(f"{peer} {','.join(names)} {QUOTA_NA}" for peer, names in hidden.items() if peer not in noted)  # no row left
    return result, notes


def _quota_suffix(line: str, suffix: str, width: int, unicode: bool, code: str | None) -> list[str]:
    """Keep folded names even when the trailing columns were dropped. `code` paints them (None: no colour)."""
    if not suffix:
        return [line]
    if cell_width(line + " " + suffix) <= width:
        return [line + " " + paint_text(suffix, code)]
    return [line, *[paint_text(part, code) for part in wrap_text(suffix, width)]]


def na_plan(item: dict[str, Any], reason: str) -> str:
    """The plan named in a not-applicable pool note: the account-supplied `display_plan` when present, else the stored reason's `plan=`."""
    shown = item.get("display_plan")
    if isinstance(shown, str) and shown:
        return clean_text(shown)
    return clean_text(reason.split("plan=", 1)[1]) if "plan=" in reason else "unknown"


def na_reason(row: dict[str, Any], reason: str) -> str:
    return reason if reason.startswith(DISABLED_REASON) else f"not reported (plan {na_plan(row, reason)})"


def na_pool_note(row: dict[str, Any]) -> str:
    peer, pool, reason = row["peer"], row["pool"], row["reason"]
    if reason == EXPIRED_WINDOW_REASON:
        return f"{peer} {pool} {QUOTA_NA} ({EXPIRED_WINDOW_REASON})"
    if row["retired"]:
        return _retired_pool_note(peer, pool, na_plan(row, reason))
    return f"{peer} {pool} {QUOTA_NA} {na_reason(row, reason)}" if reason.startswith(ABSENT_FACT_REASONS) else ""


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


def _binds(child: dict[str, Any], parent: dict[str, Any]) -> bool:
    """A 5-hour sub-row is shown when it has less left than its 7-day pool, or is low or out."""
    if child["state"] in {"low", "out"}:
        return True
    return (_has_value(child) and _has_value(parent)
            and float(child["remaining"]) < float(parent["remaining"]))


def ordered_pools(rows: list[dict[str, Any]], *, every: bool = False) -> list[tuple[dict[str, Any], int]]:
    """Sort POOLS by each family's weekly parent.

    Expanded POOLS also lists the short windows that do not bind. Each tuple carries its indentation depth.
    """
    retired_weekly = {(row["peer"], row["family"], str(row["pool_ref"]).rpartition(":")[0]) for row in rows
                      if row["retired"] and row["family"] != "?" and parse_window(str(row["pool"]))[0] == WEEK_SECONDS}
    rows = [row for row in rows if row["state"] != QUOTA_NA]
    scoped = scoped_weekly_parents(rows)
    weekly: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row["family"] != "?" and parse_window(str(row["pool"]))[0] == WEEK_SECONDS:
            weekly.setdefault((str(row["peer"]), str(row["family"])), row)
    tops: list[dict[str, Any]] = []
    children: dict[int, list[dict[str, Any]]] = {}  # binding sub-rows contribute to sorting
    listed: dict[int, list[dict[str, Any]]] = {}  # the sub-rows the frame lists
    for row in rows:
        window = parse_window(str(row["pool"]))[0]
        parent = scoped.get(id(row)) or weekly.get((str(row["peer"]), str(row["family"])))
        if parent is None or parent is row or (id(row) not in scoped and window >= WEEK_SECONDS):
            # A retired parent must not promote a stale short window with no quota value.
            if (not every and window < WEEK_SECONDS and row["state"] == "aged" and not _has_value(row)
                    and (row["peer"], row["family"], str(row["pool_ref"]).rpartition(":")[0]) in retired_weekly):
                continue
            tops.append(row)
            continue
        binds = _binds(row, parent)
        if binds:
            children.setdefault(id(parent), []).append(row)
        if binds or every or id(row) in scoped:
            listed.setdefault(id(parent), []).append(row)
    for children_rows in listed.values():
        children_rows.sort(key=lambda row: (id(row) in scoped,
                                            -parse_window(str(row["pool"]))[0], str(row["pool"])))

    def use_first(row: dict[str, Any]) -> tuple[int, float, str, str]:
        peer, name = str(row["peer"]), str(row["pool"])
        if row["state"] == "out" or any(child["state"] == "out" and id(child) not in scoped
                                         for child in children.get(id(row), ())):
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


def _pool_notes(row: dict[str, Any], marker: str) -> list[tuple[str, str]]:
    return [(gap, note) for gap, note in (("; ", _pool_dependency(row)), (" ", marker)) if note]


def _pool_left_value_width(cell: str) -> int:
    return cell_width(cell.partition(" ")[0].removesuffix("!"))


def fold_pool_markers(ordered: list[tuple[dict[str, Any], int]], unavailable: list[dict[str, Any]],
                       unicode: bool, keep: str | None) -> tuple[dict[int, str], set[int]]:
    """Attach unavailable windows to their peer, adding a marker-only entry for a peer with no visible pool."""
    suffixes: dict[int, list[str]] = {}
    marker_rows: set[int] = set()
    for absent in unavailable:
        peers = [row for row, _ in ordered if row["peer"] == absent["peer"]]
        parent = next((row for row in peers if row["family"] == absent["family"]), peers[0] if peers else absent)
        if not peers:
            ordered.append((parent, 0))
            marker_rows.add(id(parent))
        suffixes.setdefault(id(parent), []).append(cell_text(absent["pool"], unicode, "-", keep))
    return {row: _na_suffix(names) for row, names in suffixes.items()}, marker_rows


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


def _paint_pool_cell(column: str, text: str, row: dict[str, Any]) -> str:
    facts: PoolHealth = row["health"]
    reserve = facts["reserve"]
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


PoolEntry = tuple[str, dict[str, Any], bool, str]


def pool_table(entries: list[PoolEntry], all_rows: list[dict[str, Any]], width: int, unicode: bool,
                keep: str | None, *, rich: bool, paint: bool, details: bool = False,
                head: str = "PEER POOL", guide_url: str | None = None,
                profile_urls: Mapping[str, Any] | None = None) -> tuple[list[str], list[list[str]], list[str]]:
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
    compact_order = ordered_pools(active, every=True)
    compact_names = [cell_width(f" {row['peer']} {row['pool']}") + 3 * depth for row, depth in compact_order]
    detail_layout, _ = quota_layout(all_rows, unicode, show_quota_notes=False)
    detail_names = [cell_width(f"{row['peer']} {row['display_pool']}") for row in detail_layout]
    name_width = max([cell_width("PEER POOL"), *compact_names, *detail_names])
    if rich:
        pool_title_width = cell_width(f"{POOLS_HEADING} {len(all_rows)}")
        name_width = max(name_width, pool_title_width + RULE_PREFIX_WIDTH)
    columns: list[str] = [column for column in _POOL_COLUMNS if column not in {"BAR", "AGE"}]

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
        unavailable = [row for row in all_rows if row["state"] == QUOTA_NA]
        compact_markers, _ = fold_pool_markers(compact_order, unavailable, unicode, keep)
        for layout in ([(row, compact_markers.get(id(row), "")) for row in active],
                       [(row, str(row["suffix"])) for row in detail_layout]):
            for row, marker in layout:
                family = _pool_family_cell(row, None, unicode, keep)
                with_notes = max(with_notes, cell_width(family) + sum(cell_width(gap + note) for gap, note in _pool_notes(row, marker)))
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
            ink = _paint_pool_cell(column, value, row) if paint and row and value else value
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
        notes = _pool_notes(row, marker)
        wrapped: list[str] = []
        if row and "BAR" in columns:
            cells["BAR"] = _pool_bar(row, sizes["BAR"], unicode, child)
        if row and "FAMILY" in columns:
            cells["FAMILY"] = _pool_family_cell(row, sizes["FAMILY"], unicode, keep)
            if profile_urls:
                cells["FAMILY"] = link_profiles(cells["FAMILY"], profile_urls)
            for gap, note in notes:
                family = cells["FAMILY"] + gap + note
                if sizes["BAR"] >= BAR_MIDDLE_CELLS and cell_width(family) <= sizes["FAMILY"]:
                    cells["FAMILY"] = family
                else:
                    wrapped.append(note)
        elif dependency := _pool_dependency(row):
            wrapped.append(dependency)
        text = line(name, cells, row)
        if cell_width(text) > width:
            text, *continuations = wrap_text(ANSI_ESCAPE.sub("", text), width)
        else:
            continuations = []
        lines = [text, *continuations]
        # A note that does not fit the row goes below it, indented like a child row so it reads as that pool's note.
        lines.extend(paint_text(POOL_NOTE_INDENT + part, dim) for note in wrapped
                     for part in wrap_text(note, max(1, width - len(POOL_NOTE_INDENT))))
        if not row or "FAMILY" not in columns:
            lines[-1:] = _quota_suffix(lines[-1], marker, width, unicode, dim)
        rendered.append(lines)
    return headers, rendered, [name.lower() for name in reversed(_POOL_COLUMNS) if name not in columns]


def pool_gap(rows: list[dict[str, Any]], width: int, unicode: bool, *, show_quota_notes: bool) -> list[str]:
    visible = [row for row in rows if not row["retired"] and row["state"] != QUOTA_NA]
    healths: dict[str, PoolHealth | None] = {}
    for row in visible:
        pool = str(row["pool"])
        healths[pool] = None if pool in healths else row["health"]
    gaps = pace_gaps({pool: facts for pool, facts in healths.items() if facts is not None})["pools"]
    if not show_quota_notes:
        measured = {str(row["pool"]) for row in visible if row["health"]["measured"]}
        gaps = {name: pace for name, pace in gaps.items() if name in measured}
    if not gaps:
        return []
    values = ", ".join(f"{name} {format_pool_pace(pace, unicode=unicode)}" for name, pace in gaps.items())
    return wrap_text(f"gap vs {PACE_GAP_BASELINE} (pp): {values}", width)
