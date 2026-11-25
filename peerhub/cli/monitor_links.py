"""Guide, profile, and local file hyperlinks for the terminal monitor."""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from peerhub.cli.monitor_layout import wrap_text, as_dict
from peerhub.cli.monitor_legend import QUOTA_NA
from peerhub.extensions.caller import host_id, local_workspace_file


GUIDE_TEXT = "Guide"  # the link at the right end of line 2
GUIDE_DOC = "docs/monitor.md"
STATE_WORDS_ANCHOR = "state-words"
_GUIDE_BLOB = "https://github.com/greatgc-flow/peerhub/blob/"
GUIDE_MAIN_URL = f"{_GUIDE_BLOB}main/{GUIDE_DOC}"


def _guide_override(env: Mapping[str, str]) -> str | None:
    """PEERHUB_GUIDE_URL (tests and mirrors): an http(s) URL without a space or a control character, else ignored."""
    url = env.get("PEERHUB_GUIDE_URL", "")
    return url if url.isascii() and url.isprintable() and " " not in url and url.startswith(("https://", "http://")) else None


def guide_released(version: str | None) -> str | None:
    """The tag URL of a released version; None for a dev or local build (`.dev`, `-dev`, `+local`), which has no tag."""
    if version is None:
        from peerhub._version import __version__
        version = __version__
    if any(marker in version.lower() for marker in ("dev", "+", "local")):
        return None
    return f"{_GUIDE_BLOB}v{version}/{GUIDE_DOC}"


def guide_link_url(env: Mapping[str, str], version: str | None = None) -> str | None:
    """HTTPS target for the terminal link when enabled, with a release tag as the default."""
    if not monitor_links_enabled(env):
        return None
    override = _guide_override(env)
    return override if override and override.startswith("https://") else guide_released(version)


def guide_text_url(env: Mapping[str, str], version: str | None = None) -> str:
    """The URL of the plain `Guide:` line: the tag of this version, the main branch for a build without a tag."""
    return _guide_override(env) or guide_released(version) or GUIDE_MAIN_URL


def monitor_links_enabled(env: Mapping[str, str]) -> bool:
    return "NO_COLOR" not in env and _guide_enabled(env)


def _guide_enabled(env: Mapping[str, str]) -> bool:
    return env.get("PEERHUB_NO_LINKS", "").strip().lower() in {"", "0", "false", "no", "off"}


def osc_link(url: str, label: str) -> str:
    if not url.startswith(("file:///", "https://")) or not url.isascii() or not url.isprintable() or " " in url:
        return label
    return f"\x1b]8;;{url}\x1b\\{label}\x1b]8;;\x1b\\"


def link_pool_state(url: str, state: str) -> str:
    fragment = {"?": "unknown", QUOTA_NA: "na"}.get(state, state)
    return osc_link(f"{url.partition('#')[0]}#state-{fragment}", state)


def file_link_url(path: object) -> str | None:
    """An existing local file, encoded as a URI with no raw terminal controls."""
    if not isinstance(path, (str, Path)) or not path:
        return None
    try:
        file = Path(path)
        if not file.drive.startswith("\\\\") and file.is_file():
            url = file.absolute().as_uri()
            if url.startswith("file:///"):
                return url
    except (OSError, ValueError):
        pass
    return None


def location_lines(text: str, width: int, url: str | None) -> list[str]:
    lines = wrap_text(text, width)
    return [osc_link(url, line) for line in lines] if url else lines


def link_profiles(text: str, urls: Mapping[str, Any]) -> str:
    return re.sub(r"[\w.-]+", lambda match: osc_link(url, match[0]) if (url := urls.get(match[0])) else match[0], text)


def link_query(row: dict[str, Any], label: str) -> str:
    facts = as_dict(row.get("_caller_file"))
    if facts and isinstance(facts.get("host"), str) and facts["host"] == host_id():
        file = local_workspace_file(facts.get("query_file"), facts.get("workspace"))
        if file is not None:
            return osc_link(file[0].as_uri(), label)
    return label


def query_painter(rows: list[dict[str, Any]], column: int,
                   paint: Callable[[int, int, str], str] | None) -> Callable[[int, int, str], str]:
    def decorate(index: int, cell_column: int, text: str) -> str:
        if cell_column == column:
            text = link_query(rows[index], text)
        return paint(index, cell_column, text) if paint is not None else text

    return decorate
