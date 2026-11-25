"""Keep the documented quota thresholds tied to their runtime constants."""
from pathlib import Path

import pytest

from peerhub.cli.monitor_refresh import MONITOR_QUOTA_MAX_AGE_SECONDS, MONITOR_REFRESH_LEAD_SECONDS, QUOTA_REFRESH_COOLDOWN_SECONDS
from peerhub.extensions.observation_model import LOW_QUOTA_FRACTION, QUOTA_MAX_AGE_SECONDS, QUOTA_NOTICE_AGE_SECONDS

ROOT = Path(__file__).resolve().parents[3]
MONITOR_DOC = "docs/monitor.md"
QUOTA_MINUTES = QUOTA_MAX_AGE_SECONDS // 60
WATCH_SECONDS = MONITOR_QUOTA_MAX_AGE_SECONDS
NOTICE_MINUTES = QUOTA_NOTICE_AGE_SECONDS // 60
LOW_PERCENT = f"{LOW_QUOTA_FRACTION * 100:g}%"


@pytest.mark.parametrize("path,key,expected", [
    ("docs/batch.md", "Missing or over-", f"{QUOTA_MINUTES}-minute evidence"),
    ("docs/commands.md", "refresh quota when evidence is missing or older than ", f"{QUOTA_MINUTES} minutes"),
    (MONITOR_DOC, "keep a measured value less than ", f"{QUOTA_MINUTES} minutes old"),
    (MONITOR_DOC, "required quota reading reaches ", f"{WATCH_SECONDS} seconds ({MONITOR_REFRESH_LEAD_SECONDS} seconds before expiry)"),
    (MONITOR_DOC, "Failed or partial peers retry after ", f"{QUOTA_REFRESH_COOLDOWN_SECONDS} seconds"),
    (MONITOR_DOC, "oldest quota reading is more than ", f"{NOTICE_MINUTES} minutes old"),
    (MONITOR_DOC, "more than 0% and at most ", f"{LOW_PERCENT} left."),
    (MONITOR_DOC, "| `fast` | `▲` | more than ", f"{LOW_PERCENT} left, but the projection"),
    # G.7: LEFT flags low reserve at the threshold, inclusive.
    (MONITOR_DOC, "LEFT uses neutral text, a yellow low-reserve `!` at ",
     f"{LOW_PERCENT} or less, or bold red at fresh zero."),
])
def test_quota_threshold_prose_matches_source(path: str, key: str, expected: str) -> None:
    prose = " ".join((ROOT / path).read_text(encoding="utf-8").split())
    _, found, rest = prose.partition(key)
    assert found, f"{path} is missing the quota threshold phrase {key.strip()!r}"
    # Normalize the spelled-out durations used in the docs and lessons.
    rest = rest.replace("five minutes", "5 minutes").replace("an hour", "60 minutes").replace("one hour", "60 minutes")
    assert rest.startswith(expected), f"{path} after {key.strip()!r} should say {expected!r}, found {rest[:60]!r}"
