"""Assemble monitor activity sections and handle missing activity data."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from peerhub.cli.monitor_alert_view import group_error
from peerhub.cli.monitor_layout import as_dict, heading_rule, rich_section
from peerhub.cli.monitor_leads_view import plain_leads, rich_leads
from peerhub.cli.monitor_legend import FRAME_SECTIONS
from peerhub.cli.monitor_recent_view import compact_recent
from peerhub.cli.monitor_running_view import compact_running, plain_running, rich_running
from peerhub.core.text import clean_text


def compact_activity(groups: dict[str, Any], width: int, unicode: bool, keep: str | None = None, *,
                     rich: bool = False, paint: bool = False, links: bool = False) -> tuple[list[str], int, list[str] | None]:
    """Build the compact RUNNING and RECENT sections from the activity group."""
    group = as_dict(groups.get("activity"))
    if not isinstance(group.get("data"), Mapping):
        why = "read failed" if clean_text(group.get("status") or "").upper() in {"ERROR", "FAILED"} else "not read"
        head = f"{FRAME_SECTIONS['running']} ? | activity {why}"
        return [heading_rule(head, width, unicode, paint) if rich else head], 0, None
    data = as_dict(group.get("data"))
    running, total = compact_running(data, width, unicode, keep, rich=rich, paint=paint, links=links)
    recent = compact_recent(data, groups, width, unicode, keep, rich=rich, paint=paint, links=links)
    return running, total, recent


def rich_activity(group: dict[str, Any], payload: dict[str, Any], now: float | None, width: int, unicode: bool,
                  paint: bool, keep: str | None) -> tuple[list[str], list[str]]:
    """Build the expanded rich RUNNING and LEADS sections."""
    if not isinstance(group.get("data"), Mapping):
        failed = clean_text(group.get("status") or "").upper() in {"ERROR", "FAILED"}
        why = group_error(payload, "activity") if failed else "data unavailable"
        head = f"{FRAME_SECTIONS['running']} ? | activity {'read failed' if failed else 'not read'}"
        return rich_section(head, [why], width, unicode, paint), []
    data = as_dict(group.get("data"))
    return (
        rich_running(data, width, unicode, paint, keep, links=bool(payload.get("_file_links"))),
        rich_leads(data, now, width, unicode, paint, keep),
    )


def plain_activity(group: dict[str, Any], payload: dict[str, Any], now: float | None, width: int, unicode: bool,
                   keep: str | None) -> tuple[list[str], list[str]]:
    """Build the expanded plain RUNNING and LEADS sections."""
    if not isinstance(group.get("data"), Mapping):
        failed = clean_text(group.get("status") or "").upper() in {"ERROR", "FAILED"}
        why = group_error(payload, "activity") if failed else "data unavailable"
        return [f"{FRAME_SECTIONS['running']} ? | activity {'read failed' if failed else 'not read'}", why], []
    data = as_dict(group.get("data"))
    return plain_running(data, width, unicode, keep), plain_leads(data, now, width, unicode, keep)
