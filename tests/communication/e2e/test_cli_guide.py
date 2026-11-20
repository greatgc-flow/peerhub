"""`peerhub guide` (0.25.0 plan D5/D6/A5): decisions only, <= 1600 bytes; the rest moved to `peerhub explain <topic>`.

Tests:
- size cap, one pointer line, every pointer topic resolves
- max-parallel 3 from ONE constant (no stale literal anywhere)
- the vote policy and the verdict template (D6/A5)
- every fact of the former guide is still reachable through `explain`
- works without store or providers; pointers in --help and README.md
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from peerhub.cli.app import build_parser, main
from peerhub.cli.lessons import lesson_by_id, load_lessons

pytestmark = [pytest.mark.unit]

GUIDE_CAP = 1600
REPO = Path(__file__).resolve().parents[3]
TOPICS = ("ask-basics", "writes-and-uncertain", "store-and-upgrade", "batch-and-load", "discovery-commands", "report-issues")


def _guide(capsys, monkeypatch=None, tmp_path=None) -> str:
    if monkeypatch is not None:
        monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "no-global"))
    assert main(["guide"]) == 0
    return capsys.readouterr().out.replace("\r\n", "\n")


def _explained(topic: str) -> str:
    """The whole text of one topic (title, symptom, fix) with whitespace folded."""
    (lesson,) = lesson_by_id(topic)
    return " ".join(" ".join([lesson["title"], lesson["symptom"], lesson["fix"]]).split())


def _topics_named_by(guide: str) -> list[str]:
    named = re.findall(r"peerhub explain ([a-z][a-z0-9-]+)", guide)
    listed = re.search(r"peerhub explain <topic> for: ([a-z0-9, -]+?);", guide)
    assert listed, "the guide has one pointer line: 'peerhub explain <topic> for: a, b, c; ...'"
    return [*named, *listed.group(1).split(", ")]


# ---- size and shape ---------------------------------------------------------------------------------------------

def test_guide_is_at_most_1600_bytes_of_lf_text(capsys):
    out = _guide(capsys)
    assert len(out.encode("utf-8")) <= GUIDE_CAP, f"{len(out.encode('utf-8'))} bytes"
    assert "\r" not in out and out.isascii()
    assert len(out.splitlines()) <= 14


def test_guide_does_not_read_profiles_and_ignores_a_hostile_overlay(capsys, monkeypatch, tmp_path):
    """The profile table moved to `peerhub profile list`: a broken or hostile overlay cannot change the guide."""
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "no-global"))
    baseline = _guide(capsys)
    home = tmp_path / "cfg"
    home.mkdir()
    (home / "models.toml").write_text('schema_version = 1\n[profiles."cx.standard"]\nselection_mode = "pinned"\n'
                                      'model = "X\\u001b[2JINJECTED"\n', encoding="utf-8")
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(home))
    assert _guide(capsys) == baseline
    (home / "models.toml").write_text("[not toml", encoding="utf-8")
    assert _guide(capsys) == baseline


def test_guide_works_without_store_or_providers(tmp_path, monkeypatch, capsys):
    empty_dir = tmp_path / "empty_workspace"
    empty_dir.mkdir()
    monkeypatch.chdir(empty_dir)
    monkeypatch.delenv("PEERHUB_DB", raising=False)
    monkeypatch.delenv("PEERHUB_CONFIG_HOME", raising=False)
    assert main(["guide"]) == 0
    assert "PeerHub guide" in capsys.readouterr().out
    assert not (empty_dir / ".peerhub").exists()


def test_guide_json_carries_the_same_text(capsys):
    import json

    assert main(["guide"]) == 0
    text = capsys.readouterr().out
    assert main(["guide", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["guide"].strip() == text.strip()


# ---- the decisions ----------------------------------------------------------------------------------------------

def test_guide_states_the_decisions(capsys, monkeypatch, tmp_path):
    out = _guide(capsys, monkeypatch, tmp_path)
    flat = " ".join(out.split())
    assert re.search(r"(?m)^Peer: ", out) and "cc/cx" in out and "ag" in out and "ca/oa/ga" in out  # peer choice
    assert "standard" in out and "effort" in out and "deepthink" in out  # tiers
    assert re.search(r"(?m)^Stream: ", out) and "new task = new stream" in out  # stream reuse rule
    assert re.search(r"(?m)^Votes: ", out) and "FRESH" in out  # fresh votes
    assert re.search(r"(?m)^Batch: ", out) and "peerhub batch run" in out
    assert re.search(r"(?m)^Timeout: ", out) and "--timeout-seconds 1800-3600" in out and "7200" in out
    assert re.search(r"(?m)^Uncertain", out) and "never blind-retry" in flat and "new stream" in flat  # uncertain handling
    assert re.search(r"(?m)^More: .*peerhub profile list", out) and "peerhub explain <topic>" in out  # the pointer line
    assert "peerhub explain lessons" in out


def test_guide_vote_policy_and_verdict_template(capsys, monkeypatch, tmp_path):
    """D6/A5: routine = ONE fresh reviewer; consequential = 3 independent peers, unanimity; always FRESH; template."""
    out = _guide(capsys, monkeypatch, tmp_path)
    votes = next(line for line in out.splitlines() if line.startswith("Votes: "))
    flat = " ".join(votes.split())
    assert "routine check = ONE reviewer" in flat
    assert "consequential (release, schema, deletion, public API) = 3 independent peers, unanimity" in flat
    assert "FRESH" in flat and "even if a stream name is given" in flat
    assert "VERDICT / BLOCKERS / NITS" in flat and "<= 300 tokens" in flat
    assert "peerhub explain independent-peer-votes" in flat or "peerhub explain independent-peer-votes" in out


def test_guide_has_no_vote_command_and_no_new_cli_surface():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["vote"])
    subparsers = next(action for action in parser._actions if action.dest == "subcommand")  # pyright: ignore[reportPrivateUsage]
    assert "vote" not in subparsers.choices


def test_guide_example_commands_parse_via_real_argparse(capsys):
    out = _guide(capsys)
    parser = build_parser()
    found = 0
    for match in re.finditer(r"(?m)^[A-Za-z]+: (peerhub batch run [^\n]*?)(?: \(|$)", out):
        parser.parse_args(shlex.split(match.group(1))[1:])
        found += 1
    assert found == 1
    for command in ("peerhub profile list", "peerhub explain lessons"):
        assert command in out
        parser.parse_args(shlex.split(command)[1:])


# ---- pointers ---------------------------------------------------------------------------------------------------

def test_every_pointer_topic_resolves_to_its_own_lesson(capsys):
    out = _guide(capsys)
    topics = _topics_named_by(out)
    assert {*TOPICS, "setup"} <= set(topics)
    ids = {lesson["id"] for lesson in load_lessons()}
    for topic in dict.fromkeys(topics):
        assert topic in ids or topic == "lessons", topic
        assert main(["explain", topic]) == 0, topic
        printed = capsys.readouterr().out
        assert topic == "lessons" or printed.splitlines()[0].startswith(f"{topic}:"), topic


def test_topic_ids_do_not_shadow_guidance_codes_or_profile_ids():
    from peerhub.cli.recovery import REGISTRY

    assert not set(TOPICS) & set(REGISTRY)
    assert all("." not in topic for topic in TOPICS)


def test_every_topic_is_one_short_explain_entry(capsys):
    from peerhub.cli.lessons import render_lesson

    for topic in TOPICS:
        (lesson,) = lesson_by_id(topic)
        assert len(render_lesson(lesson).splitlines()) <= 5 and lesson["keywords"] and lesson["issue"], topic


# ---- max-parallel: one constant ----------------------------------------------------------------------------------

def test_guide_separates_batch_validation_from_quota_spend(capsys):
    batch = next(line for line in _guide(capsys).splitlines() if line.startswith("Batch: "))
    assert "--dry-run (validates only)" in batch
    assert "execution spends quota" in batch


def test_max_parallel_three_comes_from_one_constant(capsys):
    from peerhub.extensions import batch

    assert batch.RECOMMENDED_MAX_PARALLEL == 3 and 1 <= batch.RECOMMENDED_MAX_PARALLEL <= batch.MAX_PARALLEL
    out = _guide(capsys)
    assert f"--max-parallel {batch.RECOMMENDED_MAX_PARALLEL} --dry-run" in out
    source = (REPO / "peerhub" / "cli" / "guide.py").read_text(encoding="utf-8")
    assert "RECOMMENDED_MAX_PARALLEL" in source and not re.search(r"--max-parallel \d", source)


def test_no_stale_max_parallel_literal_in_guide_docs_or_lessons(capsys):
    from peerhub.extensions import batch

    out = _guide(capsys)
    assert "--max-parallel 2" not in out
    texts = {"guide": out}
    for rel in ("README.md", "docs/batch.md", "peerhub/config_data/lessons.toml", "docs/lessons.md"):
        texts[rel] = (REPO / rel).read_text(encoding="utf-8")
    for name, text in texts.items():
        assert "--max-parallel 2" not in text, name
        for value in re.findall(r"--max-parallel (\d+)", text):
            assert int(value) == batch.RECOMMENDED_MAX_PARALLEL, (name, value)
    assert "--max-parallel 3" in texts["README.md"] and "--max-parallel 3" in texts["docs/batch.md"]


# ---- the former guide: every fact is still reachable --------------------------------------------------------------

FORMER_GUIDE_FACTS: dict[str, tuple[str, ...]] = {
    "ask-basics": (
        'peerhub ask cc "Explain the architecture" --profile cc.standard --stream arch-review --request-id req-001',
        'peerhub ask cc "Now drill into data flow" --profile cc.standard --stream arch-review --request-id req-002',
        "a --stream resumes (same store/binding, 1 peer)", "new task = new stream", "votes one-shot",
        "bulk=standard, code=effort, review/design=deepthink (docs/batch.md)",
        "--query-file and --json select transport/format (no token saving)",
        "blocks until the peer finishes, then prints reply + 'session:'",
        "foreground/background-with-exit-notification", "never detach/poll",
        "--timeout-seconds 1800-3600", "ag prints nothing until done", "--timeout-seconds 7200",
        "--silence-timeout-seconds unset or >= it",
    ),
    "writes-and-uncertain": (
        "ag recipe: 'peerhub explain ag-recipe'", "cx sandbox enforced; cc enforced; ag NOT enforced (issue #140)",
        "when edits are intended, pass --writable on every turn, including resumed turns",
        "pending_earlier_record: inspect processed_status; delivered => retry same request-id/prompt/binding/selector; uncertain => stop",
        "stop that stream (timeouts too), check the working tree for partial edits, 'peerhub diag health'",
        "skipped oversized line: cc ok if an intact result follows (#325), cx needs answer+turn.completed",
        "never blind-retry original prompt under a new request-id, with --fresh, or delete claims",
        "continue genuinely new work on a new stream with a summary", "'peerhub explain <code>'",
        "prompts/streams persist in local store; no tokens/passwords",
    ),
    "store-and-upgrade": (
        "--db > PEERHUB_DB > nearest upward .peerhub/core.db", "peerhub explain store-missing",
        "back up core.db before upgrading", "new schema not openable by old versions",
        "stop 'peerhub monitor' -> pip install -U peerhub -> restart monitor", "locks peerhub.exe on Windows",
        "also wait for running asks (diag running)", "reinstall, check results before resend",
        "don't run dev builds on the real store", "after restore, reopen stores (old instances fenced)",
    ),
    "batch-and-load": (
        "executes asks (spends quota)", "ag tasks always writable", "overlapping writers rejected",
        "cancel: Windows tree kill, POSIX best-effort", "inspect UNCERTAIN", "keep manifest + batch-id",
        "4 active asks (max 6; cc 3/cx 3/ag 1/ca 3/oa 3/ga 3)", "suggest: is advisory (rule H)",
        "reject stale/unknown/reset/<5% pools, prefer pools without fast, all-fast = slow/defer",
        "compare 7D PACE (5H only gates)", "PACE (pp, used%-elapsed%) needs a fresh quota age",
        "+ = ahead of even burn", "peerhub batch run tasks.json --batch-id review-001 --max-parallel 3 --dry-run",
        "TTY monitor watch refreshes quota when due", "--no-probe", "diag read committed evidence",
        "uncertain streams may stay quota-blocked after reset", "aged quota is last-known; refresh first",
        "accounts: cc/cx only; new peer+root per login; protected roots; overrides scrubbed (names kept for failures/replay); bound quota unobserved; backups have roots",
    ),
    "discovery-commands": (
        "peerhub explain lessons", "peerhub explain setup", "peerhub monitor --group capacity",
        "suggest a|b = best + alternative peer; peerhub explain monitor-states", "peerhub monitor --expand",
        "peerhub diag --plan", "per-pool spend/hold/rest until reset", "peerhub profile list; slugs: agy models; --model <slug>",
        "peerhub session list", "peerhub account list", "peerhub observation refresh", "peerhub explain peer-subagents",
        "the line-1 'facts:' alert (needs_recheck) clears via refresh-model-profiles skill or release upgrade",
    ),
    "report-issues": (
        "github.com/greatgc-flow/peerhub/issues", "gh issue list -R greatgc-flow/peerhub --search KEYWORD",
        "peerhub --version, OS, exact command, status/exit code, and workaround (no secrets/private paths)",
    ),
    "api-peers": (
        "ca (Claude API through the ant CLI", "oa (OpenAI Responses API", "ga (Gemini API",
        "spend API credits, not a subscription pool", "ca, oa and ga read no file", "One ask per stream",
        "Also refused: --writable",
    ),
}


@pytest.mark.parametrize("topic", sorted(FORMER_GUIDE_FACTS))
def test_former_guide_facts_are_reachable_through_explain(topic, capsys):
    text = _explained(topic).replace("’", "'")
    missing = [fact for fact in FORMER_GUIDE_FACTS[topic] if " ".join(fact.split()).casefold() not in text.casefold()]
    assert not missing, missing
    assert main(["explain", topic]) == 0
    assert capsys.readouterr().out.splitlines()[0].startswith(f"{topic}:")


def test_old_ag_recipe_and_store_missing_pointers_still_resolve(capsys):
    for topic in ("ag-recipe", "store-missing", "monitor-states", "peer-subagents", "setup", "ask-timeouts", "stream-reuse-cost", "suggest"):
        assert main(["explain", topic]) == 0
        assert capsys.readouterr().out.splitlines()[0].startswith(f"{topic}:")


# ---- D6 text in the existing explain topic -----------------------------------------------------------------------

def test_independent_peer_votes_is_extended_not_duplicated():
    ids = [lesson["id"] for lesson in load_lessons()]
    assert ids.count("independent-peer-votes") == 1 and not [i for i in ids if "vote" in i and i != "independent-peer-votes"]
    text = _explained("independent-peer-votes")
    for needle in ("routine check = ONE reviewer, FRESH", "consequential (release, schema, deletion, public API)",
                   "3 independent peers, unanimity", "even when a stream name is given",
                   "a named stream is for review rounds on one diff only", "VERDICT / BLOCKERS / NITS", "<= 300 tokens",
                   "no `vote` command", "--max-parallel 3 --responses",
                   # the earlier facts stay
                   "did not author it", "not writable", "private empty directory", "11 s"):
        assert needle in text, needle


# ---- freeze rule (D12) --------------------------------------------------------------------------------------------

def test_freeze_rule_is_recorded_in_status_and_changelog():
    status = (REPO / "docs" / "STATUS.md").read_text(encoding="utf-8")
    open_row = next(line for line in status.splitlines() if line.startswith("| Open |"))
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## [0.24.29]", 1)[0]  # the 0.25.0 section and anything above it
    for name, text in (("STATUS Open row", open_row), ("CHANGELOG Unreleased", unreleased)):
        flat = " ".join(text.split())
        assert "no further monitor cosmetics" in flat, name
        assert "no top-tier benchmark reruns" in flat, name
        assert "0.25.0 telemetry data exists" in flat, name


def test_changelog_unreleased_names_each_m1_change():
    unreleased = (REPO / "CHANGELOG.md").read_text(encoding="utf-8").split("## [0.24.29]", 1)[0]
    changed = unreleased.split("### Changed", 1)[1].split("\n### ", 1)[0]
    flat = " ".join(changed.split())
    for needle in ("`peerhub guide`", "1600 bytes", "--max-parallel 3", "VERDICT / BLOCKERS / NITS", "`alias_of`",
                   "`effort_override`", "`suggest`", "`measurement`", "`cx.deepthink`", "unmeasured", "0.25.0 plan"):
        assert needle in flat, needle
    assert "## [Unreleased]" in (REPO / "CHANGELOG.md").read_text(encoding="utf-8") and "\r" not in changed


# ---- pointers elsewhere -------------------------------------------------------------------------------------------

def test_guide_pointers_in_help_and_readme(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "guide" in capsys.readouterr().out
    readme_path = Path("README.md")
    assert readme_path.is_file()
    assert "peerhub guide" in readme_path.read_text(encoding="utf-8")


def test_remote_ask_ag_silence_timeout_guidance():
    """Issue #118: docs/remote-ask.md documents ag silence timeout behavior."""
    remote_ask = Path("docs/remote-ask.md")
    assert remote_ask.is_file()
    text = remote_ask.read_text(encoding="utf-8")
    assert "ag prints nothing until done" in text
    assert "--silence-timeout-seconds" in text
    assert "--timeout-seconds 7200" in text


def test_readme_upgrade_guidance():
    """Issue #123: README Upgrade section documents monitor stopping and PyPI lag."""
    readme_path = Path("README.md")
    assert readme_path.is_file()
    text = readme_path.read_text(encoding="utf-8")
    assert "peerhub monitor" in text
    assert "locks `peerhub.exe` on Windows" in text
    assert "PyPI index lag" in text


def test_contributing_release_steps_cover_the_matrix_time_limit_the_full_commit_id_and_the_exe_lock():
    """#390: a full-matrix job cancelled at its time limit fails `ci-pass`; the step names the rerun command."""
    steps = Path("CONTRIBUTING.md").read_text(encoding="utf-8").split("\n## Releasing\n", 1)[1].split("\n### Release lessons\n", 1)[0]
    steps = " ".join(steps.split())
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    limit = re.search(r"\n  full-matrix:\n(?:    .*\n)*?    timeout-minutes: (\d+)\n", workflow)
    assert limit is not None
    assert f"{limit.group(1)} minute limit" in steps
    assert "full-matrix" in steps and "Windows with Python 3.11" in steps and "is cancelled" in steps
    assert "fails `ci-pass`" in steps and "`gh run rerun <RUN-ID> --failed`" in steps
    assert "the 40-character commit id" in steps and "`git rev-parse HEAD`" in steps
    assert "locks `peerhub.exe` on Windows" in steps
