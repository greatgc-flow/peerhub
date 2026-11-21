"""Keep the documented quota thresholds tied to their runtime constants."""
from pathlib import Path

import pytest

from peerhub.cli.support import MONITOR_QUOTA_MAX_AGE_SECONDS
from peerhub.extensions.observation_model import LOW_QUOTA_FRACTION, QUOTA_MAX_AGE_SECONDS, QUOTA_NOTICE_AGE_SECONDS

ROOT = Path(__file__).resolve().parents[3]
MONITOR_DOC = "docs/monitor.md"
LESSONS = "peerhub/config_data/lessons.toml"
QUOTA_MINUTES = QUOTA_MAX_AGE_SECONDS // 60
WATCH_MINUTES = MONITOR_QUOTA_MAX_AGE_SECONDS // 60
NOTICE_MINUTES = QUOTA_NOTICE_AGE_SECONDS // 60
LOW_PERCENT = f"{LOW_QUOTA_FRACTION * 100:g}%"


@pytest.mark.parametrize("path,key,expected", [
    ("README.md", "A missing or over-", f"{QUOTA_MINUTES}-minute snapshot"),
    ("README.md", "refresh when quota is missing or older than ", f"{QUOTA_MINUTES} minutes and waits"),
    (MONITOR_DOC, "keep a measured value less than ", f"{QUOTA_MINUTES} minutes old"),
    (LESSONS, "shows a measured value younger than ", f"{QUOTA_MINUTES} minutes with"),
    (LESSONS, "Missing or over-", f"{QUOTA_MINUTES}-minute quota"),
    (MONITOR_DOC, "reading for the selected peers is more than ", f"{WATCH_MINUTES} minutes old"),
    (LESSONS, "TTY watch refreshes when the newest reading is over ", f"{WATCH_MINUTES} minutes old"),
    (MONITOR_DOC, "oldest quota reading is more than ", f"{NOTICE_MINUTES} minutes old"),
    (LESSONS, "flag quota older than ", f"{NOTICE_MINUTES} minutes or missing"),
    (MONITOR_DOC, "more than 0% and at most ", f"{LOW_PERCENT} left."),
    (MONITOR_DOC, "| `fast` | `▲` | more than ", f"{LOW_PERCENT} left, but the projection"),
    (MONITOR_DOC, "orange above ", f"{LOW_PERCENT}, red at {LOW_PERCENT} or less"),
    (LESSONS, "low = positive remaining <=", f"{LOW_PERCENT};"),
    (LESSONS, "fast = >", f"{LOW_PERCENT} remaining"),
])
def test_quota_threshold_prose_matches_source(path: str, key: str, expected: str) -> None:
    prose = " ".join((ROOT / path).read_text(encoding="utf-8").split())
    _, found, rest = prose.partition(key)
    assert found, f"{path} is missing the quota threshold phrase {key.strip()!r}"
    # Normalize the spelled-out durations used in the docs and lessons.
    rest = rest.replace("five minutes", "5 minutes").replace("an hour", "60 minutes").replace("one hour", "60 minutes")
    assert rest.startswith(expected), f"{path} after {key.strip()!r} should say {expected!r}, found {rest[:60]!r}"
