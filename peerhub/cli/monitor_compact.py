"""Assemble the monitor's shared header and priority sections and fit the compact frame."""
from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, cast

from peerhub.core.text import clean_text
from peerhub.core.scalars import finite_number
from peerhub.cli.monitor_activity_view import compact_activity, compact_leads, expanded_recent
from peerhub.cli.monitor_alert_view import alert_section, decorate_verdict, verdict_line
from peerhub.cli.monitor_format import na_pool_note, pool_gap
from peerhub.cli.monitor_layout import (
    FILE_HYPERLINK,
    FRAME_TRAILING_ROWS,
    RICH_GLYPHS,
    RICH_SGR,
    as_dict,
    ascii_text,
    cell_width,
    compact_age,
    dict_items,
    fit_bytes,
    fit_fields,
    fit_live_quota_title,
    fit_live_title,
    fit_text,
    heading_rule,
    overflow,
    paint_text,
    wrap_text,
)
from peerhub.cli.monitor_legend import FRAME_SECTIONS, FRAME_SECTION_ORDER, POOL_LEGEND, QUOTA_NA
from peerhub.cli.monitor_links import GUIDE_TEXT, link_profiles, location_lines, osc_link
from peerhub.cli.monitor_preferences import SHOW_QUOTA_NOTES_DEFAULT
from peerhub.cli.monitor_quota_view import (
    HIDDEN_QUOTA_WINDOWS,
    compact_suggestions,
    frame_consumers,
    frame_pools,
    pool_section,
    with_min_quota,
)
from peerhub.extensions.diag_quota import quota_stale_line
from peerhub.extensions.observation_model import QUOTA_PROBE_LOAD_DELAY, QUOTA_PROBE_LOAD_LABEL, oldest_measured_quota_age


COMPACT_WIDTH = 100
COMPACT_MAX_LINES = 24
COMPACT_MAX_BYTES = 8192
SECTION_WITH_DETAIL = 2  # one heading and one detail line


def _safe_refresh_warning_labels(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    warnings = cast(Sequence[Any], value)
    labels: list[str] = []
    safe_reasons = {"probe_warning", "probe_timeout", "claude_usage_timeout", "gemini_usage_timeout",
                    "codex_usage_timeout", "provider_probe_error", "provider_probe_unavailable",
                    "auth_error", "quota_exhausted", "invalid_model", "usage_limit", "timeout",
                    "unavailable", "error", QUOTA_PROBE_LOAD_DELAY}
    for item in warnings[:3]:
        if not isinstance(item, str):
            labels.append("probe_warning")
            continue
        parts = [clean_text(part).strip() for part in item.split(":")]
        peer = parts[0].lower() if parts and parts[0].lower() in {"cc", "cx", "ag"} else ""
        reason = parts[1] if peer and len(parts) > 1 else (parts[0] if parts else "")
        code = reason.lower() if reason.lower() in safe_reasons else "probe_warning"
        label = f"probe {QUOTA_PROBE_LOAD_LABEL}" if code == QUOTA_PROBE_LOAD_DELAY else code
        labels.append(f"{peer} {label}".strip())
    if len(warnings) > 3:
        labels.append(f"+{len(warnings) - 3} warnings")
    return labels


@dataclass
class CompactFrame:
    """The compact frame before the budget: each section as built, with the totals its overflow line discloses."""
    verdict: str
    header: list[str] = field(default_factory=lambda: list[str]())  # line 2, then the lines that belong to it
    alerts: list[str] | None = None
    running: list[str] | None = None  # [RUNNING header, *rows]; None when `activity` is not selected
    running_total: int = 0
    leads: list[str] | None = None  # [LEADS header, *rows]; None when there is no lead to show and the read was complete
    leads_total: int = 0
    recent: list[str] | None = None  # [RECENT header, *rows]; None when there is no ended ask
    recent_short: list[str] | None = None  # --expand: the rows of `recent` (entries of two lines) as one line each
    suggest: list[str] = field(default_factory=lambda: list[str]())
    pool_notes: list[str] = field(default_factory=lambda: list[str]())
    pool_gap: list[str] = field(default_factory=lambda: list[str]())
    pool_legend: list[str] = field(default_factory=lambda: list[str]())
    pools: list[str] | None = None  # [POOLS header, *rows]; None when `capacity` is not selected
    pools_total: int = 0
    pools_header_lines: int = 1


def fit_compact_frame(frame: CompactFrame, *, width: int, height: int | None, unicode: bool,
                      max_lines: int = COMPACT_MAX_LINES, max_bytes: int = COMPACT_MAX_BYTES,
                      keep: str | None = None, pool_width: int | None = None) -> str:
    """Fit the shared section order to the width, line and byte budgets."""
    limit = max_lines if height is None else min(max_lines, height - FRAME_TRAILING_ROWS)
    if limit <= 0 or width <= 0:
        return ""

    verdict = frame.verdict
    for lines in frame_line_ladder(frame, width, unicode, keep, pool_width=pool_width):
        verdict = lines[0]
        if len(lines) <= limit:
            if sum(len(line.encode("utf-8")) + 1 for line in lines) <= max_bytes:
                return "\n".join(lines) + "\n"
            lines = [FILE_HYPERLINK.sub(r"\1", line) for line in lines]
            if sum(len(line.encode("utf-8")) + 1 for line in lines) <= max_bytes:
                return "\n".join(lines) + "\n"
    return fit_bytes(verdict, max_bytes - 1, unicode) + "\n"


def frame_line_ladder(frame: CompactFrame, width: int, unicode: bool, keep: str | None,
                       *, pool_width: int | None = None) -> Iterator[list[str]]:
    """Yield the fitted frame, then shrink and drop each section from the bottom up."""

    def fit(line: str, columns: int = width) -> str:
        return fit_text(line if unicode else ascii_text(line.replace(" · ", " | ").replace("·", "|"), keep), columns, unicode,
                    painted=True, indent=True)

    def fitted(lines: list[str] | None, columns: int) -> list[str] | None:
        return None if lines is None else [fit(line, columns) for line in lines]

    verdict = fit(frame.verdict)
    header = [fit(line) for line in frame.header]
    parts = {key: fitted(getattr(frame, key), (pool_width or width) if key == "pools" else width)
             for key in FRAME_SECTION_ORDER}
    pool_notes = [fit(line, pool_width or width) for line in frame.pool_notes]
    pool_gap = [fit(line) for line in frame.pool_gap]
    pool_legend = [fit(line) for line in frame.pool_legend]
    totals = {"running": frame.running_total, "leads": frame.leads_total, "pools": frame.pools_total}
    nouns = {"running": "more", "leads": "leads", "pools": "pools"}
    header_lines = {"pools": frame.pools_header_lines}

    def section(key: str) -> list[str]:
        lines = parts[key]
        if lines is None:
            return []
        rows = len(lines) - header_lines.get(key, 1)
        hidden = totals.get(key, 0) - rows
        extra = [overflow(hidden, nouns[key], width, unicode)] if hidden > 0 and rows > 0 else []
        return [*lines, *extra, *pool_notes, *pool_gap, *pool_legend] if key == "pools" else [*lines, *extra]

    def assemble() -> list[str]:
        return [verdict, *header, *(line for key in FRAME_SECTION_ORDER for line in section(key))]

    def pop_rows(key: str, floor: int) -> bool:
        lines = parts[key]
        heading = header_lines.get(key, 1)
        if lines is None or len(lines) - heading <= floor:
            return False
        total = totals.get(key, 0)
        rows = len(lines) - heading
        created = total - rows <= 0 < total  # this removal makes the overflow line
        if created and floor >= 1 and rows <= 2:  # A-3: K5 would leave the header without a row; do nothing
            return False
        lines.pop()
        if created and len(lines) > heading:  # one row for one overflow line would save nothing (K5)
            lines.pop()
        return True

    yield assemble()
    for key in reversed(FRAME_SECTION_ORDER):
        rows = parts[key]
        if key == "pools" and pool_notes:
            pool_notes.clear()
            yield assemble()
        if rows and key == "recent" and frame.recent_short:
            rows[1:] = [fit(line) for line in frame.recent_short]
            yield assemble()
        if key in totals:
            while pop_rows(key, 1):
                yield assemble()
        elif rows:
            floor = 1 if key == "suggest" else SECTION_WITH_DETAIL
            while len(rows) > floor:
                rows.pop()
                yield assemble()
        if key == "pools":
            if pool_legend:
                pool_legend.clear()
                yield assemble()
            while len(header) > 1:  # title details give way before the final pool row
                header.pop()
                yield assemble()
        if rows:
            parts[key] = None
            yield assemble()
    if header:
        header.pop()
        yield assemble()

def _suggest_lines(text: str, width: int, unicode: bool) -> list[str]:
    lines = wrap_text(text, width)
    return lines if len(lines) <= 2 else [lines[0], fit_text(" ".join(lines[1:]), width, unicode)]

def render_compact_frame(payload: dict[str, Any], *, view: str, color: bool, unicode: bool,
                          width: int, height: int | None, keep: str | None = None) -> str:
    pool_width = max(1, width - 1)  # leave the terminal's autowrap column unused
    width = min(COMPACT_WIDTH - 1, pool_width)
    frame = build_compact_frame(payload, view=view, color=color, unicode=unicode, width=width, height=height, keep=keep,
                           pool_width=pool_width)
    return fit_compact_frame(frame, width=width, height=height, unicode=unicode, keep=keep, pool_width=pool_width)


def build_compact_frame(payload: dict[str, Any], *, view: str, color: bool, unicode: bool, width: int,
                   height: int | None, keep: str | None = None, expand: bool = False,
                   pool_width: int | None = None) -> CompactFrame:
    """Build the sections shared by both layouts before height fitting."""
    rich = view == "rich"  # P21: the rich look; `unicode` picks its glyphs and `paint` adds the colour
    paint = color and rich and "NO_COLOR" not in os.environ
    sep = f" {RICH_GLYPHS[unicode]['sep']} " if rich else " | "
    groups = as_dict(payload.get("groups"))
    selected = payload.get("selected_groups")
    selected_ids = set(cast(list[str], selected)) if isinstance(selected, list) else set(groups)
    read_at = finite_number(payload.get("read_at"))
    now = read_at if read_at is not None else 0
    elapsed = finite_number(payload.get("display_elapsed_seconds")) or 0
    configured_profiles = dict_items(payload.get("configured_profiles"))
    consumers = frame_consumers(payload)
    observations, quota, rows = frame_pools(payload, consumers)
    payload = with_min_quota(payload, rows)
    store = as_dict(payload.get("store_selection"))
    source = clean_text(store.get("source") or "unknown")
    try:
        stamp = datetime.fromtimestamp(now + elapsed, timezone.utc).strftime("%Y-%m-%d %H:%MZ") if read_at is not None else "time unknown"
    except (ValueError, OverflowError, OSError):
        stamp = "time unknown"
    age_readings = quota
    if "_quota_age_readings" in payload:
        age_readings = [item for item in dict_items(payload["_quota_age_readings"])
                        if (expires := finite_number(item.get("expires_in"))) is None or expires >= elapsed]
    oldest = oldest_measured_quota_age(age_readings)
    age = oldest + elapsed if oldest is not None else None
    age_label = compact_age(age)
    guide_url = payload.get("_guide_url")
    guide_room = cell_width(GUIDE_TEXT) + 1
    title_width = width - guide_room if guide_url and width > guide_room else width
    refresh_state = clean_text(payload.get("refresh_state") or "")
    next_refresh = finite_number(payload.get("next_refresh_in"))
    live_refresh = as_dict(payload.get("_live_refresh"))

    def fit_title(room: int) -> tuple[str, bool]:
        if live_refresh:
            return fit_live_quota_title(live_refresh, refresh_state, stamp, source, room, unicode)
        details = [f"DB:{source}", f"quota age {age_label}"]
        if refresh_state or next_refresh is not None:
            return fit_live_title(refresh_state, next_refresh, stamp, details, room, unicode, inline_note=False)
        title = " | ".join([f"PeerHub Monitor {stamp}", *details])
        return fit_fields(title, room, unicode, inline_note=False), cell_width(title) > room

    title, cut = fit_title(title_width)
    title = title.replace(" | ", sep)  # the rich view's separator; line 2 is structure, so its colour is dim
    if guide_url and title_width < width:
        if not unicode:
            title = ascii_text(title.replace(" · ", " | ").replace("·", "|"), keep)
        title += " " * (width - cell_width(title) - cell_width(GUIDE_TEXT)) + osc_link(guide_url, GUIDE_TEXT)
        if cut and not expand:
            # Reserving the Guide may shorten the title, but it must not consume another row in the viewport.
            _, cut = fit_title(width)
    # Fields dropped from line 2 are disclosed on a line of their own, so a narrow terminal keeps the timestamp.
    header = [paint_text(title, RICH_SGR["dim"]) if paint else title]
    if (selected_ids & {"capacity", "peers"} or "_quota_age_readings" in payload) and (notice := quota_stale_line(age)):
        header.extend(wrap_text(notice, width))
    header.extend(part for note in cast(list[str], payload.get("_quota_probe_notes", []))
                  for part in wrap_text(note, width))
    if cut and not expand:
        header.append(f"Fields hidden{sep}--expand")
    errors = as_dict(payload.get("errors"))
    nearest = payload.get("_nearest_store_path")
    same_store = bool(nearest and store.get("path") and os.path.normcase(os.path.abspath(str(nearest))) == os.path.normcase(os.path.abspath(str(store["path"]))))
    read_errors = any(value != "truncated" for value in errors.values())
    if ((not same_store or payload.get("status") in {"FAILED", "ERROR"} or read_errors) and store.get("path")
            and not expand):  # expanded frames include the full path in their header
        header.extend(location_lines(f"DB: {clean_text(store['path'])}", width, payload.get("_db_url")))
    if payload.get("_store_note") and not expand:  # #338: --db overrides a PEERHUB_DB that points elsewhere
        header.extend(wrap_text(str(payload["_store_note"]), width))
    header.extend(fit_text(f"Warning: {warning}", width, unicode)
                  for warning in _safe_refresh_warning_labels(payload.get("refresh_warnings")))
    if selected_ids & {"capacity", "peers"}:  # #328: a peer's newest quota probe failed before it named a pool
        for peer in sorted({str(item.get("peer_id") or "?") for item in observations if item.get("kind", "quota") == "quota"}):
            probes = [item for item in observations if str(item.get("peer_id") or "?") == peer and item.get("kind", "quota") == "quota"]
            latest = max(probes, key=lambda item: finite_number(item.get("effective_at_us")) or
                         (now - (finite_number(item.get("age_seconds")) or 0)) * 1_000_000)
            if not latest.get("pool_ref") and (latest.get("state") in {"ERROR", "UNAVAILABLE", "UNKNOWN"}
                                               or latest.get("stored_state") in {"ERROR", "UNAVAILABLE", "UNKNOWN"}):
                age = finite_number(latest.get("age_seconds"))
                failure = QUOTA_PROBE_LOAD_LABEL if latest.get("reason") == QUOTA_PROBE_LOAD_DELAY else "error"
                header.append(f"{clean_text(peer)} probe {failure} ({compact_age(age + elapsed if age is not None else None)})")
    evidenced = {f"{row['peer']}:{row['pool']}" for row in rows}
    missing_pool = any(key not in evidenced for key in consumers)
    if (selected_ids & {"capacity", "peers"} and (missing_pool or any(row["needs_refresh"] for row in rows))
            and (rows or "capacity" not in selected_ids)):
        header.append("next: peerhub observation refresh")
    if rich:  # the pill's padding takes two cells; the time is that of line 2
        verdict = decorate_verdict(verdict_line(payload, max(1, width - 2), unicode, keep, sep), sep,
                                    stamp[-6:] if read_at is not None and stamp.endswith("Z") else "", width, paint)
    else:
        verdict = verdict_line(payload, width, unicode, keep)
    frame = CompactFrame(verdict=verdict, header=header)
    frame.alerts = alert_section(payload, width, unicode, keep, rich=rich, paint=paint, expand=expand)
    links = bool(payload.get("_file_links"))
    if "activity" in selected_ids and not expand:
        frame.running, frame.running_total, frame.recent = compact_activity(groups, width, unicode, keep,
                                                                               rich=rich, paint=paint, links=links)
        frame.leads, frame.leads_total = compact_leads(groups, now + elapsed, width, unicode, keep, rich=rich, paint=paint)
    elif "activity" in selected_ids and isinstance(as_dict(groups.get("activity")).get("data"), Mapping):
        data = as_dict(as_dict(groups.get("activity")).get("data"))
        frame.recent, frame.recent_short = expanded_recent(data, groups, width, unicode, keep, bounded=height is not None,
                                                           rich=rich, paint=paint, links=links)
    if "capacity" in selected_ids:
        suggestion = compact_suggestions(configured_profiles, rows)
        frame.suggest = wrap_text(suggestion, width) if expand else _suggest_lines(suggestion, width, unicode)
        profile_urls = as_dict(payload.get("_profile_urls"))
        if profile_urls:
            frame.suggest = [link_profiles(line, profile_urls) for line in frame.suggest]
        show_notes = bool(payload.get("_show_quota_notes", SHOW_QUOTA_NOTES_DEFAULT))
        visible_rows = rows if show_notes else [row for row in rows if row["state"] != QUOTA_NA]
        if visible_rows:
            frame.pool_gap = pool_gap(rows, width, unicode, show_quota_notes=show_notes)
            frame.pool_legend = wrap_text(POOL_LEGEND, width)
            frame.pools, frame.pools_total, frame.pools_header_lines = pool_section(
                visible_rows, pool_width or width, unicode, keep, rich=rich, paint=paint, every=expand,
                guide_url=payload.get("_state_doc_url"), profile_urls=profile_urls)
            if not expand and show_notes:
                dim = RICH_SGR["dim"] if paint else None
                frame.pool_notes = [
                    paint_text(line, dim) for row in rows if row["state"] == QUOTA_NA and (note := na_pool_note(row))
                    for line in wrap_text(note, max(1, pool_width or width))
                ]
        else:
            if rows:
                notes = [] if expand else wrap_text(HIDDEN_QUOTA_WINDOWS, width)
            else:
                notes = (["no quota evidence", "next: peerhub observation refresh"] if height is not None
                         else wrap_text("no quota evidence; next: peerhub observation refresh", width))
            head = f"{FRAME_SECTIONS['pools']} 0"
            frame.pools = [heading_rule(head, width, unicode, paint) if rich else head, *notes]
    return frame
