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
from peerhub.core import load_limits as L
from peerhub.cli.guide import GUIDE_MAX_BYTES

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
    assert len(guide.replace("\r\n", "\n").encode("utf-8")) <= GUIDE_MAX_BYTES


def test_topic_is_the_loop_in_order():
    text = _text()
    steps = ["peerhub diag --plan", "independent", "--max-parallel", "diag --plan again", "defer"]
    positions = [text.index(step) for step in steps]
    assert positions == sorted(positions), "read quota, pick pools, split, run a wave, re-read, defer when none"


def test_topic_matches_the_pool_order_and_resource_thresholds():
    text = _text()
    assert ("lowest 7D PACE first" if POOLS_USE_FIRST_ASCENDING else "highest 7D PACE first") in text
    assert "--max-parallel n" in text and "PeerHub does not cap the count" in text
    assert f"free RAM >= {L.MIN_FREE_RAM_GB} GB, CPU below {L.CPU_BUSY_WARN_PCT}%" in text
    assert "watch G-5H after each wave" in text
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
    agents = REPO.parent / "AGENTS.md"  # the workspace file lives outside the repository: checked only where it exists
    if agents.is_file():
        assert f"peerhub explain {TOPIC}" in agents.read_text(encoding="utf-8")


def test_batch_doc_explains_the_loop():
    doc = (REPO / "docs" / "batch.md").read_text(encoding="utf-8")
    assert f"peerhub explain {TOPIC}" in doc
    for needle in ("peerhub diag --plan", "peerhub observation refresh", "--max-parallel"):
        assert needle in doc


def test_ai_reads_diag_and_never_the_monitor(capsys):
    text = _text()
    assert "never peerhub monitor" in text and "human" in text
    assert "load line" in text and "free RAM" in text
    assert main(["guide"]) == 0
    guide = capsys.readouterr().out
    assert "diag --plan" in guide and "monitor" in guide and "human" in guide
    agents = REPO.parent / "AGENTS.md"
    if agents.is_file():
        assert "never `peerhub monitor`" in agents.read_text(encoding="utf-8")


def test_docs_match_the_resource_thresholds():
    doc = (REPO / "docs" / "batch.md").read_text(encoding="utf-8")
    assert f"| Free RAM | Below {L.MIN_FREE_RAM_GB} GB |" in doc
    assert f"| CPU load | At or above {L.CPU_BUSY_WARN_PCT}% |" in doc
    assert "the `load:` line" in doc and "human screen" in (REPO / "docs" / "monitor.md").read_text(encoding="utf-8")


def test_topic_gives_facts_and_leaves_the_decision_to_the_ai():
    text = _text()
    assert "You decide" in text and "default" in text
    assert "must" not in text and "do not start more" not in text


def test_guide_batch_line_does_not_cap_parallel_work_at_a_fixed_number(capsys):
    assert main(["guide"]) == 0
    guide = capsys.readouterr().out
    batch = next(line for line in guide.splitlines() if line.startswith("Batch:"))
    assert "at most" not in batch and "--max-parallel" in batch
    assert "n at once" in batch and TOPIC in guide


def test_topic_covers_inflight_spend_mid_wave_refusal_and_replay():
    text = _text()
    assert "in-flight" in text and "margin" in text
    assert "refused" in text and "still run" in text and "same --batch-id" in text
    assert text.index("after each wave run peerhub observation refresh, then diag --plan again") > text.index("--max-parallel n")


def test_help_start_line_and_batch_doc_point_to_the_one_recipe(capsys):
    top = _help_top(capsys)
    for needle in ("peerhub guide", "peerhub diag --plan", "peerhub monitor", "peerhub explain report-issues", "peerhub ask"):
        assert needle in top
    doc = (REPO / "docs" / "batch.md").read_text(encoding="utf-8")
    section_h = doc[doc.index("**H. Balancing rule**"):doc.index("**I. Concurrency**")]
    assert f"peerhub explain {TOPIC}" in section_h
    section_j = doc[doc.index("**J. Parallel work"):]
    assert "peerhub observation refresh" in section_j.split("5. After each wave")[1]


def _help_top(capsys) -> str:
    with pytest.raises(SystemExit):
        main(["--help"])
    return " ".join(capsys.readouterr().out.split())


def test_topic_says_the_json_rows_name_their_profiles():
    text = _text()
    assert "--json" in text and "profiles" in text and "load object" in text


def test_section_h_and_j_say_which_ranking_each_one_is_for():
    doc = (REPO / "docs" / "batch.md").read_text(encoding="utf-8")
    h = doc[doc.index("**H. Balancing rule**"):doc.index("**I. Concurrency**")]
    j = doc[doc.index("**J. Parallel work"):]
    assert "`suggest:`" in h and "single ask" in h and "wave" in h and "lowest 7D PACE" in h
    assert "rule H" in j and "single ask" in j


def test_guide_tells_what_to_do_with_stale_quota_and_a_caller_timeout(capsys):
    assert main(["guide"]) == 0
    lines = capsys.readouterr().out.splitlines()
    quota = next(line for line in lines if line.startswith("Quota (AI):"))
    uncertain = next(line for line in lines if line.startswith("Uncertain"))
    assert "observation refresh" in quota and "diag running" in uncertain


def test_topic_has_the_throttle_rule_and_the_reconcile_steps():
    text = _text()
    assert "do not reroute" in text and "reduce concurrency" in text
    assert "diag health --stream" in text and "diag running" in text


def test_batch_doc_lists_the_same_tops_as_the_guide(capsys):
    assert main(["guide"]) == 0
    guide = capsys.readouterr().out
    tops = re.search(r"tops \(([^)]+)\)", guide).group(1).split(", ")
    doc = (REPO / "docs" / "batch.md").read_text(encoding="utf-8")
    assert all(top in doc for top in tops), tops


def test_readme_upgrade_makes_the_backup_a_checked_step_before_the_install():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    upgrade = readme[readme.index("### Upgrade"):readme.index("### Uninstall")]
    backup = upgrade[:upgrade.index("pip install -U peerhub")]
    assert "printed backup command" in backup and "exact path" in backup
    assert "must exit 0" in backup and "new modified time" in backup


def test_topic_explains_the_unmapped_count():
    text = _text()
    assert "unmapped" in text and "in flight" in text and L.PLAN_NA_POOLS_LABEL in text


def test_topic_says_a_profile_under_several_pools_spends_each_of_them():
    text = _text()
    assert "under several pools" in text and "spends each" in text
