"""Prepare a schema 2.0 monitor payload for human rendering."""
from __future__ import annotations

import os
from typing import Any

from peerhub.cli.monitor_compact import (
    COMPACT_MAX_BYTES,
    COMPACT_MAX_LINES,
    COMPACT_WIDTH,
    render_compact_frame,
)
from peerhub.cli.monitor_expanded import render_expanded_frame
from peerhub.cli.monitor_layout import FRAME_TRAILING_ROWS, as_dict, cell_width
from peerhub.cli.monitor_links import (
    GUIDE_MAIN_URL,
    GUIDE_TEXT,
    file_link_url,
    guide_link_url,
    guide_released,
    guide_text_url,
    monitor_links_enabled,
    osc_link,
)
from peerhub.cli.monitor_preferences import SHOW_QUOTA_NOTES_DEFAULT
from peerhub.cli.monitor_quota_view import display_retired_windows, with_retained_quota


def render_grouped_frame(payload: dict[str, Any], *, view: str = "rich", color: bool = False,
                         unicode: bool = True, width: int = 100, height: int | None = None,
                         expand: bool = False, text_encoding: str | None = None,
                         show_quota_notes: bool = SHOW_QUOTA_NOTES_DEFAULT,
                         links: bool = False, tty: bool = False) -> str:
    """Compact human default; the established renderer supplies expanded detail.

    `text_encoding` is the output's encoding: without `unicode`, text cells keep what it can encode (A5).
    `links` indicates a VT terminal for the rich view. The link opt-out applies to both views.
    The plain Guide trailer uses a spare line when the header has no Guide link."""
    enabled = view == "rich" and links and monitor_links_enabled(os.environ)
    guide_url = guide_link_url(os.environ) if enabled else None
    store = as_dict(payload.get("store_selection"))
    payload = {**with_retained_quota(display_retired_windows(payload)),
               "_show_quota_notes": show_quota_notes, "_guide_url": guide_url,
               "_state_doc_url": (guide_released(None) or GUIDE_MAIN_URL) if enabled else None,
               "_db_url": file_link_url(store.get("path")) if enabled else None,
               "_profile_urls": payload.get("_profile_urls") if enabled else None,
               "_file_links": enabled and os.environ.get("PEERHUB_MONITOR_TEXT", "").strip().lower() != "off"}
    if expand:
        screen = render_expanded_frame(payload, view=view, color=color, unicode=unicode,
                                        width=width, height=height, expand=True, keep=text_encoding)
        columns = max(0, width - 1)
    else:
        screen = render_compact_frame(payload, view=view, color=color, unicode=unicode,
                                       width=width, height=height, keep=text_encoding)
        columns = max(1, min(COMPACT_WIDTH, width) - 1)
    guide_rendered = guide_url is not None and osc_link(guide_url, GUIDE_TEXT) in screen
    if screen and not guide_rendered:
        guide = f"{GUIDE_TEXT}: {guide_text_url(os.environ)}"
        line_count = screen.count("\n")
        room = height is None or line_count < height - FRAME_TRAILING_ROWS
        if not expand:
            room = (room and line_count < COMPACT_MAX_LINES
                    and len((screen + guide).encode("utf-8")) < COMPACT_MAX_BYTES)
        if room and cell_width(guide) <= columns:
            return screen + guide + "\n"
    return screen
