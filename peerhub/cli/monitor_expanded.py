"""Assemble and fit expanded monitor frames."""
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any, cast

from peerhub.core.text import clean_text
from peerhub.core.scalars import finite_converted_number as finite_number
from peerhub.cli.monitor_activity_view import plain_activity, rich_activity
from peerhub.cli.monitor_alert_view import group_error
from peerhub.cli.monitor_compact import build_compact_frame, frame_line_ladder
from peerhub.cli.monitor_layout import (
    FRAME_TRAILING_ROWS,
    RICH_GLYPHS,
    as_dict,
    ascii_text,
    dict_items,
    fit_fields,
    fit_text,
    hidden_lines,
    is_section_rule,
    rich_section,
    section_rule,
    wrap_text,
)
from peerhub.cli.monitor_legend import FRAME_DETAIL_ORDER, MONITOR_LEGEND
from peerhub.cli.monitor_links import link_profiles
from peerhub.cli.monitor_quota_view import display_peer_quotas, frame_consumers, frame_pools
from peerhub.cli.monitor_running_view import ask_split, running_total
from peerhub.cli.monitor_sections import group_label, group_lines


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


def render_expanded_frame(payload: dict[str, Any], *, view: str = "rich", color: bool = False,
                         unicode: bool = True, width: int = 100, height: int | None = None,
                         expand: bool = False, keep: str | None = None) -> str:
    """The --expand frame (`_expanded_frame`): the compact frame's top and the detail sections, rich or plain."""
    groups = as_dict(payload.get("groups"))
    selected_raw = payload.get("selected_groups")
    selected = ([str(g) for g in cast(Sequence[Any], selected_raw)]
                if isinstance(selected_raw, Sequence) and not isinstance(selected_raw, str) else list(groups))
    selected = [g for g in selected if g not in {"overview", "coverage"}]
    context_sessions = dict_items(as_dict(as_dict(groups.get("context")).get("data")).get("sessions"))
    if not any(as_dict(item.get("context")).get("state") in {"MEASURED", "ESTIMATED"} for item in context_sessions):
        selected = [g for g in selected if g != "context"]
    coverage_partial = any(as_dict(group).get("status") not in {"OK", None}
                           or _group_has_uncertain_measurement(as_dict(group).get("data"), depth=0)
                           or as_dict(as_dict(group).get("data")).get("truncated")
                           or as_dict(as_dict(group).get("coverage")).get("read") == "partial"
                           for group in groups.values())
    coverage_data = as_dict(as_dict(groups.get("coverage")).get("data"))
    scope_note = "No scope evidence" if "scopes" in coverage_data and not coverage_data["scopes"] else ""
    footer = "Coverage: " + (scope_note or ("partial" if coverage_partial else "complete")) + "; peerhub explain <code>"
    cap = as_dict(groups.get("capacity"))
    data = as_dict(cap.get("data"))
    observations, _, rows = frame_pools(payload, frame_consumers(payload))
    pool_rows = {(str(row["peer"]), str(row["pool_ref"])): row for row in rows}
    display_groups = {**groups, "capacity": {**cap, "data": {**data, "_pool_rows": pool_rows}}}
    activity = as_dict(groups.get("activity"))
    execution = as_dict(groups.get("execution"))
    if isinstance(activity.get("data"), Mapping) and isinstance(execution.get("data"), Mapping):
        # #394: the leases decide what runs; the sampled certainty counts of EXECUTION do not
        activity_data = as_dict(activity.get("data"))
        running_asks = running_total(activity_data, ask_split(activity_data)[0])
        display_groups["execution"] = {**execution, "data": {**as_dict(execution.get("data")), "_running_asks": running_asks}}
    peers_group = as_dict(groups.get("peers"))
    if isinstance(peers_group.get("data"), Mapping):
        peers_data = as_dict(peers_group.get("data"))
        display_groups["peers"] = {**peers_group, "data": {**peers_data,
            "peers": display_peer_quotas(payload, dict_items(peers_data.get("peers")), observations, pool_rows)}}
    display = {**payload, "selected_groups": selected, "groups": display_groups}
    report = as_dict(as_dict(as_dict(groups.get("peers")).get("data")).get("facts_report"))
    # Every facts_report reason is a drift reason ("ag: unknown_pool", "cc: version_drift").
    drift_peers = sorted({str(reason).split(":")[0] for reason in cast(list[object], report.get("reasons") or [])})
    if drift_peers:
        footer += "; DRIFT:" + ",".join(drift_peers)
    return _expanded_frame(payload, display, footer, view=view, color=color, unicode=unicode, width=width,
                           height=height, expand=expand, keep=keep)


_PROFILE_DETAIL_GROUPS = {"peers", "execution", "sessions", "usage", "runtime"}


def _expanded_frame(payload: dict[str, Any], display: dict[str, Any], footer: str, *,
                    view: str, color: bool, unicode: bool, width: int, height: int | None, expand: bool,
                    keep: str | None) -> str:
    """Build the header and priority sections first, then fit the expanded details."""
    rich = view == "rich"
    target = max(0, width - 1)
    if target <= 0 or (height is not None and height <= FRAME_TRAILING_ROWS):
        return ""
    paint = color and rich and "NO_COLOR" not in os.environ
    sep = f" {RICH_GLYPHS[unicode]['sep']} " if rich else " | "
    frame = build_compact_frame(payload, view=view, color=color, unicode=unicode, width=target, height=height, keep=keep,
                           expand=True)

    lines: list[str] = []
    groups = as_dict(display.get("groups"))
    selected = cast(list[str], display.get("selected_groups") or [])
    read_at = finite_number(payload.get("read_at"))
    elapsed = finite_number(payload.get("display_elapsed_seconds")) or 0.0
    now = None if read_at is None else read_at + elapsed
    if "activity" in selected:
        if rich:
            frame.running, frame.leads = rich_activity(as_dict(groups.get("activity")), payload, now, target, unicode, paint, keep)
        else:
            frame.running, frame.leads = plain_activity(as_dict(groups.get("activity")), payload, now, target, unicode, keep)
    frame.pools_total = 0  # the expanded frame's final hidden-row hint counts the trim
    ladder = frame_line_ladder(frame, target, unicode, keep)
    top = next(ladder)
    detail_groups = [gid for gid in FRAME_DETAIL_ORDER if gid in selected]
    detail_groups.extend(gid for gid in selected if gid != "activity" and gid not in FRAME_DETAIL_ORDER)
    profile_urls = as_dict(payload.get("_profile_urls"))
    for group_id in detail_groups:
        group = as_dict(groups.get(group_id))
        status = clean_text(group.get("status") or "UNKNOWN").upper()
        if group_id == "capacity":  # a group that was not read has no data: it says so, as every other group does
            read = as_dict(as_dict(payload.get("groups")).get("capacity"))
            group = read if read.get("data") is None else group
        group_width = max(1, target - 1) if rich and group_id != "capacity" else target
        rows, _ = group_lines(group_id, group, width=group_width, unicode=unicode,
                                elapsed_seconds=elapsed, read_at=read_at, expand=expand, color=paint, keep=keep, rich=rich,
                                bounded=height is not None and not rich, guide_url=payload.get("_state_doc_url"),
                                profile_urls=profile_urls)
        if status in {"ERROR", "FAILED"}:
            rows.insert(0, group_error(payload, group_id))
        label = group_label(group_id, group, status, read_at, elapsed)
        if rich:
            section = rich_section(label, rows, target, unicode, paint)
        else:
            # The append-only frame cuts a long row; a viewport drops whole trailing fields and says so.
            fit_row = fit_text if height is None else fit_fields
            section = [section_rule(label, target, unicode),
                       *(fit_row(row, target, unicode, indent=group_id == "capacity") for row in rows)]
        if profile_urls and group_id in _PROFILE_DETAIL_GROUPS:
            section = [link_profiles(line, profile_urls) for line in section]
        lines.extend(section)

    def fit(line: str) -> str:  # the one exit of every line: the glyph set of the stream, and never past the width
        if not unicode:  # A5: text the output encoding can show stays; the decoration is ASCII
            line = ascii_text(line.replace(" · ", " | ").replace("·", "|"), keep)
        return fit_text(line, target, unicode, painted=True, indent=True)

    rest = [fit(line) for line in lines]  # the top was fitted by its owner
    footer = fit(footer)
    if height is None:
        explanations = [text.replace(" | ", sep) for text in MONITOR_LEGEND] if rich else MONITOR_LEGEND
        legend = [fit(part) for text in explanations for part in wrap_text(text, max(1, target))]
        return "\n".join([*top, *rest, *legend, footer]) + "\n"
    limit = height - FRAME_TRAILING_ROWS

    every = [*top, *rest]
    total = len(every)
    if total + 1 <= limit:
        return "\n".join([*every, footer]) + "\n"

    def hint(kept: int, *, single_line: bool = False) -> list[str]:
        return [fit(line) for line in hidden_lines(total - kept, 0, target, unicode, expand=True,
                                                    single_line=single_line)]

    for tail in ([footer], []):  # coverage gives way before the top does
        for kept in range(min(total, limit - len(tail) - 1), len(top) - 1, -1):
            if kept > len(top) and is_section_rule(every[kept - 1]):
                continue  # never keep a detail heading without a row
            hidden = hint(kept)
            if kept + len(hidden) + len(tail) <= limit:
                return "\n".join([*every[:kept], *hidden, *tail]) + "\n"

    # Details have given way. Keep the verdict, title and final pool row with its gap ahead of the hint.
    protected = 2 + (frame.pools_header_lines + 1 + len(frame.pool_gap) if frame.pools else 0)
    if len(top) <= limit <= protected:
        return "\n".join(top) + "\n"
    for head in ladder:
        kept = len(head)
        if kept == limit and limit <= protected:
            return "\n".join(head) + "\n"
        if kept < limit:
            hidden = hint(kept, single_line=limit - kept == 1)
            tail = [footer] if kept + len(hidden) < limit else []
            return "\n".join([*head, *hidden, *tail]) + "\n"
    return top[0] + "\n"
