"""`peerhub explain parallel-quota`: the loop an AI follows to spread parallel work over the least quota.

Hints only (no new command). Every number and direction is read from its one source, not retyped here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.cli.lessons import lesson_by_id
from peerhub.cli.monitor_view import POOLS_USE_FIRST_ASCENDING
from peerhub.extensions.batch import RECOMMENDED_MAX_PARALLEL

pytestmark = [pytest.mark.unit]

REPO = Path(__file__).resolve().parents[3]
TOPIC = "parallel-quota"


def _text() -> str:
    (lesson,) = lesson_by_id(TOPIC)
    return " ".join(" ".join([lesson["title"], lesson["symptom"], lesson["fix"]]).split())


def test_topic_exists_and_is_named_by_the_guide(capsys):
    assert main(["guide"]) == 0
    guide = capsys.readouterr().out
    assert TOPIC in re.search(r"peerhub explain <topic> for: ([a-z0-9, -]+?);", guide).group(1).split(", ")
    assert len(guide.replace("\r\n", "\n").encode("utf-8")) <= 1600


def test_topic_is_the_loop_in_order():
    text = _text()
    steps = ["peerhub diag --plan", "spend (PACE", "independent", "--max-parallel", "diag --plan again", "defer"]
    positions = [text.index(step) for step in steps]
    assert positions == sorted(positions), "read quota, pick pools, split, run a wave, re-read, defer when none"


def test_topic_matches_the_pool_order_and_the_batch_cap():
    text = _text()
    assert ("lowest 7D PACE first" if POOLS_USE_FIRST_ASCENDING else "highest 7D PACE first") in text
    assert "--max-parallel n" in text and "n = " in text, "the wave size n is open, not fixed"
    assert f"{RECOMMENDED_MAX_PARALLEL} keeps one slot free" in text
    for limit in ("active asks 4, ceiling 6", "cc 3", "cx 3", "ag 1"):
        assert limit in text
    assert "at most 3 tasks" not in text
    for word in ("spend", "hold", "rest", "blocked", "stale", "<5%"):
        assert word in text
    assert "5H only gates" in text


def test_topic_names_the_cost_rules():
    text = _text()
    for rule in ("lowest tier that fits", "fresh", "facts inline", "different model", "no shared files",
                 "peerhub observation refresh", "peerhub monitor"):
        assert rule in text


def test_agents_file_and_docs_point_to_the_topic():
    docs = (REPO / "docs" / "lessons.md").read_text(encoding="utf-8")
    assert f"(`{TOPIC}`)" in docs
    assert f"peerhub explain {TOPIC}" in (REPO.parent / "AGENTS.md").read_text(encoding="utf-8")


def test_batch_doc_explains_the_loop():
    doc = (REPO / "docs" / "batch.md").read_text(encoding="utf-8")
    assert f"peerhub explain {TOPIC}" in doc
    for needle in ("peerhub diag --plan", "peerhub observation refresh", "--max-parallel"):
        assert needle in doc
