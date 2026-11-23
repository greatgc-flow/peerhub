"""Offline lessons lookup and generated documentation parity (#117)."""
from pathlib import Path
import json
import re

import pytest

from peerhub.cli.app import main
from peerhub.cli.guide import render_guide
from peerhub.cli.lessons import load_lessons, lookup_lessons, render_lesson, render_lessons_reference
from peerhub.core.peer_kinds import BUILTIN_PEER_KINDS

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(("query", "expected"), [
    ("AG SILENCE", ["ag-silence-long-asks"]),
    ("permission ag", ["ag-recipe"]),
    ("pip checkout", ["installed-vs-checkout"]),
    ("PYTHONPATH", ["installed-vs-checkout"]),
    ("read-only", ["ag-recipe"]),
    ("cx plan window", ["plan-windows"]),
    ("cx-plan-windows", ["plan-windows"]),
    ("cc absent", ["plan-windows"]),
    ("ag not reported", ["plan-windows"]),
    ("ag empty reply", ["ag-recipe"]),
    ("denied_empty", ["ag-recipe"]),
    ("ag", ["ag-recipe", "ag-bulk-profile-heuristics", "ag-requires-writable", "ag-silence-long-asks", "multi-peer-quota-shaping", "plan-windows"]),
    ("cc allow rules", ["cc-allow-rules"]),
    ("fable haiku", ["cc-allow-rules"]),
    ("monitor", ["monitor-accounts", "monitor-states", "monitor-view", "plan-windows"]),
    ("monitor-view", ["monitor-view"]),
    ("order", ["monitor-view"]),
    ("glyph", ["monitor-view"]),
    ("colour bar", ["monitor-view"]),
    ("pill", ["monitor-view"]),
    ("quota unknown", ["monitor-states"]),
    ("low fast aged suggest alternative cycles tty legend", ["monitor-states"]),
    ("accounts", ["monitor-accounts"]),
    ("review-loop", ["review-recipe"]),
    ("review-scores-to-100", ["review-recipe"]),
    ("pytest basetemp", ["cx-sandbox-and-prompt-hygiene"]),
    ("subagents", ["peer-subagents"]),
    ("api credits", ["api-peers"]),
    ("ca stateless", ["api-peers"]),
    ("ga gemini", ["api-peers"]),
    ("GEMINI_API_KEY", ["api-peers", "setup"]),
    ("OPENAI_API_KEY", ["api-peers", "setup"]),
    ("credit balance", ["api-peers"]),
    ("setup", ["setup"]),
    ("install claude", ["setup"]),
    ("ant install", ["setup"]),
    ("executable not found", ["setup"]),
    ("codex sign-in", ["setup"]),
    ("ag checkout", []),
    ("", []),
    ("!!!", []),
    ("sil", []),
    ("unfindable", []),
])
def test_lookup(query, expected):
    assert [lesson["id"] for lesson in lookup_lessons(query)] == expected


def test_free_words_cli(capsys, tmp_path):
    db = tmp_path / "unused.db"
    assert main(["--db", str(db), "explain", "ag", "silence"]) == 0
    out = capsys.readouterr().out
    assert "ag-silence-long-asks" in out
    assert "--timeout-seconds 7200 --silence-timeout-seconds 7200" in out
    assert "uncertain" in out and "new stream" in out
    assert len(out.splitlines()) <= 5
    assert not db.exists()
    assert main(["explain", "ag silence", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["lessons"][0]["id"] == "ag-silence-long-asks"


def test_explain_ag_leads_with_recipe_and_records_open_observation(capsys):
    """ag usability A1/A3: the merged recipe is listed first; E2 stays an open observation linked to #302."""
    assert main(["explain", "ag"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("ag-recipe:")
    recipe = next(lesson for lesson in load_lessons() if lesson["id"] == "ag-recipe")
    for needle in ('--query-file and say "no tools"', "trusted project root", "--writable on every turn",
                   "one task per ask", "verify claims from flash tiers", "never resume that stream",
                   "start a new stream with the content inlined", "Past observation (E2, #302, closed 2026-11-09"):
        assert needle in recipe["fix"], needle
    assert "#302" in recipe["issue"] and "#140" in recipe["issue"]


def test_cc_allow_rules_lesson_states_exact_match_and_prefix_fix():
    """#303 B2: exact-match Bash rules vs model command shape; prefix rules are the fix."""
    lesson = next(lesson for lesson in load_lessons() if lesson["id"] == "cc-allow-rules")
    for needle in ("exact command string", "Bash(peerhub:*)", "denied_actions", "Unverified hypothesis"):
        assert needle in lesson["fix"], needle
    assert lesson["issue"] == "#303"


def test_no_match_hint(capsys):
    assert main(["explain", "ag", "checkout"]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "No matching lessons" in captured.err
    assert "Try fewer keywords" in captured.err
    assert "peerhub explain lessons" in captured.err
    assert len(captured.err.splitlines()) == 1


def test_explain_lessons_prints_index_before_any_lesson_lookup(capsys, monkeypatch):
    """#366 A5: `explain lessons` is an index (id: title; --json {"lessons": [{id, title}]}), never a keyword search."""
    from peerhub.cli import lessons as lessons_mod
    calls = []
    monkeypatch.setattr(lessons_mod, "lesson_by_id", lambda q: calls.append("id") or [])
    monkeypatch.setattr(lessons_mod, "lookup_lessons", lambda q: calls.append("keywords") or [])
    catalog = load_lessons()
    assert main(["explain", "lessons"]) == 0
    assert capsys.readouterr().out.splitlines() == [f"{lesson['id']}: {lesson['title']}" for lesson in catalog]
    assert main(["explain", "lessons", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"lessons": [{"id": lesson["id"], "title": lesson["title"]} for lesson in catalog]}
    assert calls == []


def test_explain_json_envelope_is_the_same_for_index_and_keyword_lookup(capsys):
    """#366 F3: `explain lessons --json` and a keyword lookup share one {"lessons": [...]} envelope."""
    for query in (["lessons"], ["ag", "silence"]):
        assert main(["explain", *query, "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert list(payload) == ["lessons"] and isinstance(payload["lessons"], list) and payload["lessons"]
        assert all({"id", "title"} <= set(item) for item in payload["lessons"])


def test_review_recipe_merges_review_loop_and_scores_to_100(capsys):
    """#366 A5: one review recipe with a pasteable brief; the old ids still find it."""
    ids = {lesson["id"] for lesson in load_lessons()}
    assert "review-recipe" in ids and not {"review-loop", "review-scores-to-100"} & ids
    for old_id in ("review-loop", "review-scores-to-100"):
        assert main(["explain", old_id]) == 0
        assert capsys.readouterr().out.splitlines()[0].startswith("review-recipe:")
    fix = next(lesson for lesson in load_lessons() if lesson["id"] == "review-recipe")["fix"]
    for needle in ("Score each item 0-100", "violates a documented contract", "reachable from supported inputs",
                   "recommendation", "exact fix", "Re-score until 100", "re-review the exact shipped tag",
                   "another model family", "full suite once", "only targeted tests", "two rounds",
                   "narrow its contract (fail closed)", "Never `git add -A`", "agent worktrees"):
        assert needle in fix, needle


def test_lessons_without_peerhub_mechanism_are_deleted():
    ids = {lesson["id"] for lesson in load_lessons()}
    assert not {"pin-census-before-implementation", "dry-run-path-aware-checkers", "explicit-shell-wait-conditions"} & ids


def test_new_and_moved_lesson_content():
    """#366 A1/A4/A5: subagents (#361), cx tmp_path tests NOT RUN, ACCOUNTS split, vote recipe, 600 s default."""
    by_id = {lesson["id"]: lesson for lesson in load_lessons()}
    sub = by_id["peer-subagents"]["fix"]
    for needle in ("Agent tool", "spawn_agent", "names that tool explicitly", "invoke_subagent",
                   "no /loop-style persistence", "same peer quota", "disjoint task", "one merged final report"):
        assert needle in sub, needle
    assert "#361" in by_id["peer-subagents"]["issue"]
    cx_fix = by_id["cx-sandbox-and-prompt-hygiene"]["fix"]
    for needle in ("tests that use tmp_path cannot run", "also with --basetemp", "measured 2026-11-14 on Python 3.15",
                   "NOT RUN for the lead to run", "never add shims or temp folders to the repo"):
        assert needle in cx_fix, needle
    assert "--basetemp=" not in cx_fix  # the measured workaround does not work
    assert {"pytest", "tmp_path", "acl", "basetemp"} <= set(by_id["cx-sandbox-and-prompt-hygiene"]["keywords"])
    assert "ACCOUNTS" not in by_id["monitor-states"]["fix"]
    # #374: ACCOUNTS left the compact frame; account problems reach line 1 as alerts
    for needle in ("ACCOUNTS (--expand rows", "LOGIN token expiry", "reaches line 1 as an alert", "display_plan",
                   "plan_changed: true", "at least five minutes old"):
        assert needle in by_id["monitor-accounts"]["fix"], needle
    votes = by_id["independent-peer-votes"]["fix"]
    for needle in ("--max-parallel 3 --responses", "not writable", "private empty directory", "inline", "11 s",
                   "routine check = ONE reviewer, FRESH", "3 independent peers, unanimity", "VERDICT / BLOCKERS / NITS"):
        assert needle in votes, needle
    timeouts = by_id["ask-timeouts"]["fix"]
    assert "defaults to 600" in timeouts and "1800-3600" in timeouts and "7200" in timeouts
    assert "never retry on the same stream" in timeouts and "partial edits" in timeouts


def test_ag_recipe_holds_the_former_guide_lines():
    """#366 A6: the guide keeps one ag pointer line, so the lesson carries every fact the three lines had."""
    fix = next(lesson for lesson in load_lessons() if lesson["id"] == "ag-recipe")["fix"]
    for needle in ('--query-file and say "no tools"', "one task per ask", "verify claims from flash tiers",
                   "30,000 bytes on Windows", "120,000 bytes on POSIX", "use cc/cx on a separate stream",
                   "new request-id", "not a temp or scratch folder", "even for read tasks", "accept-edits, workspace-wide",
                   "never resume that stream", "Headless prompts auto-deny", "command(...) or command(*) allow rules"):
        assert needle in fix, needle


def test_ag_recipe_names_allow_list_scope_and_cx_lesson_holds_the_cx_facts():
    """#391: a `*` in a prompt path (not glob-expanded), allow-list scope in the order of causes, inline text for ag; cx facts stay in the cx lesson."""
    by_id = {lesson["id"]: lesson for lesson in load_lessons()}
    ag = by_id["ag-recipe"]
    for needle in ("A path in a prompt is not glob-expanded, and an attempt to read a literal * path can be denied",
                   "allow-list scope (agy's permissions.allow read roots and shell rules",
                   "reads outside the -w root, grep/cat and unlisted CLIs are denied headless",
                   "ag cannot run commands and cannot read outside its workspace",
                   "give it inline text and take its text answer",
                   "denied_actions reports only the tool names"):
        assert needle in ag["fix"], needle
    causes = ag["fix"].split("check in order:", 1)[1]
    order = [causes.index(cause) for cause in ("many or unreadable files", "trustedWorkspaces", "binary/NUL/huge files",
                                               "allow-list scope", "pool quota exhausted")]
    assert order == sorted(order)
    assert "#391" in ag["issue"] and {"allow-list", "scope", "glob", "wildcard"} <= set(ag["keywords"])
    for cx_fact in ("peerhub store", "checkpoint receipts", "runtime_error"):
        assert cx_fact not in ag["fix"], cx_fact  # cx sentences are not in the ag recipe
    cx = by_id["cx-sandbox-and-prompt-hygiene"]
    for needle in ("cx sandbox lanes cannot open the peerhub store",
                   "reader asks must be launched by the orchestrator",
                   "use checkpoint receipts and a silence timeout",
                   "runtime_error can leave partial edits"):
        assert needle in cx["fix"], needle
    assert "#391" in cx["issue"] and {"store", "checkpoint", "runtime_error"} <= set(cx["keywords"])
    assert [lesson["id"] for lesson in lookup_lessons("ag allow-list scope")] == ["ag-recipe"]
    assert [lesson["id"] for lesson in lookup_lessons("cx checkpoint")] == ["cx-sandbox-and-prompt-hygiene"]


SETUP_TOOLS = {  # peer -> (the tool, its official page), read 2026-11-16 (#385)
    "cc": ("claude", "https://code.claude.com/docs/en/setup"),
    "cx": ("codex", "https://developers.openai.com/codex/cli"),
    "ag": ("agy", "https://antigravity.google/docs/cli/install"),
    "ca": ("ant", "https://platform.claude.com/docs/en/cli-sdks-libraries/cli/quickstart"),
}
SETUP_KEYS = {  # peer -> (the key variable, the page where a key is made)
    "oa": ("OPENAI_API_KEY", "https://platform.openai.com/api-keys"),
    "ga": ("GEMINI_API_KEY", "https://aistudio.google.com/apikey"),
}
ANT_WINDOWS = "https://github.com/anthropics/anthropic-cli/releases"
INSTALL_COMMANDS = ("irm ", "iex", "curl", "| sh", "| bash", "brew ", "go install", "npm ", "winget ", "pip install", "pipx ")


def _links(text: str) -> set[str]:
    import re
    return {link.rstrip(".") for link in re.findall(r"https://[^\s);,|]+", text)}


def test_setup_lesson_has_one_row_per_peer_with_links_and_checks_and_no_install_command(capsys):
    """#385 S1/S2: what each peer needs, the official page, the sign-in step and the check. Links only."""
    from peerhub.cli.lessons import lesson_by_id
    from peerhub.cli.recovery import ALIASES, REGISTRY

    (lesson,) = lesson_by_id("setup")
    assert "setup" not in REGISTRY and "setup" not in ALIASES  # a valid lesson id: no guidance code or alias shadows it
    assert lesson["keywords"] and lesson["issue"] == "#385"
    fix = lesson["fix"]
    assert "never downloads or runs an installer" in fix
    for peer, (tool, page) in SETUP_TOOLS.items():
        assert f"{peer}: the {tool} CLI, signed in" in fix and page in fix and f"check: {tool} --version" in fix, peer
    assert "ant auth login" in fix and f"Windows binaries: {ANT_WINDOWS}" in fix
    for peer, (variable, page) in SETUP_KEYS.items():
        assert f"{peer}: the environment variable {variable}" in fix and page in fix, peer
    assert "in the environment of the process that runs PeerHub" in fix
    assert "never on a command line or in a file" in fix
    assert _links(fix) == {page for _tool, page in SETUP_TOOLS.values()} | {page for _var, page in SETUP_KEYS.values()} | {ANT_WINDOWS}
    assert not [command for command in INSTALL_COMMANDS if command in fix]
    assert all(peer in lesson["symptom"] or tool in lesson["symptom"] for peer, (tool, _page) in SETUP_TOOLS.items())
    assert "executable not found" in lesson["symptom"] and "set OPENAI_API_KEY" in lesson["symptom"]
    assert main(["explain", "setup"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("setup:") and len(out.splitlines()) <= 5


def test_readme_and_guide_document_the_builtin_peers():
    """#385 S3: the README table and the lesson name the same peers, pages and checks."""
    from peerhub.cli.lessons import lesson_by_id

    section = Path("README.md").read_text(encoding="utf-8").split("\n## Install\n", 1)[1].split("\n## ", 1)[0]
    assert "\n### What each peer needs\n" in section and "`peerhub explain setup`" in section
    assert "never downloads or runs an installer" in section
    rows = {line.split("`")[1]: line for line in section.splitlines() if line.startswith("| `")}
    peer_line = next(line for line in render_guide().splitlines() if line.startswith("Peer: "))
    guide_groups = re.findall(r"(?:^Peer: |; )([a-z/]+) (?:for|=)", peer_line)
    assert set(rows) == {kind for group in guide_groups for kind in group.split("/")} == set(BUILTIN_PEER_KINDS)
    for peer, (tool, page) in SETUP_TOOLS.items():
        assert f"`{tool}` CLI" in rows[peer] and page in rows[peer] and f"`{tool} --version`" in rows[peer], peer
    assert "`ant auth login`" in rows["ca"] and ANT_WINDOWS in rows["ca"]
    for peer, (variable, page) in SETUP_KEYS.items():
        assert f"`{variable}`" in rows[peer] and page in rows[peer], peer
    assert _links(section) == _links(lesson_by_id("setup")[0]["fix"])
    assert not [command for command in INSTALL_COMMANDS if command in section.split("### What each peer needs", 1)[1]]


def test_known_guidance_and_alias_take_priority(capsys):
    for query in ("ask_uncertain", "uncertain"):
        assert main(["explain", query]) == 0
        assert "Guidance Code: ask_uncertain" in capsys.readouterr().out


def test_exact_lesson_id_is_its_own_step_before_keywords(capsys):
    """#328 C: ids are printed in output, so querying one finds exactly that lesson (a separate step, not part of keyword search)."""
    from peerhub.cli.lessons import lesson_by_id
    assert [lesson["id"] for lesson in lesson_by_id("monitor-probe-age")] == ["monitor-probe-age"]
    assert [lesson["id"] for lesson in lesson_by_id("  Monitor-Probe-Age ")] == ["monitor-probe-age"]
    assert lesson_by_id("monitor probe age") == [] and lesson_by_id("probe") == [] and lesson_by_id("") == []
    assert main(["explain", "monitor-probe-age"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("monitor-probe-age:") and "No matching" not in out
    assert len(out.splitlines()) <= 5
    assert main(["explain", "monitor-probe-age", "--json"]) == 0
    assert [lesson["id"] for lesson in json.loads(capsys.readouterr().out)["lessons"]] == ["monitor-probe-age"]
    for lesson in load_lessons():  # every printed id resolves to itself through explain
        assert main(["explain", lesson["id"]]) == 0
        assert capsys.readouterr().out.splitlines()[0].startswith(f"{lesson['id']}:")


def test_explain_order_is_guidance_then_lesson_id_then_keywords(capsys, monkeypatch):
    """A later profile-id step slots between guidance and lesson id; the id lookup must stay a step of run_explain_cli."""
    from peerhub.cli import lessons, recovery
    calls = []
    real_by_id, real_lookup = lessons.lesson_by_id, lessons.lookup_lessons
    monkeypatch.setattr(lessons, "lesson_by_id", lambda q: calls.append("id") or real_by_id(q))
    monkeypatch.setattr(lessons, "lookup_lessons", lambda q: calls.append("keywords") or real_lookup(q))
    assert recovery.run_explain_cli("ask_uncertain") == 0
    assert calls == []  # a guidance code never reaches the lessons
    capsys.readouterr()
    assert recovery.run_explain_cli("monitor-probe-age") == 0
    assert calls == ["id"]  # an exact id does not fall through to keyword search
    capsys.readouterr()
    calls.clear()
    assert recovery.run_explain_cli("probe") == 0
    assert calls == ["id", "keywords"]


def test_guidance_code_beats_lesson_id_and_keywords_still_work(capsys):
    from peerhub.cli.lessons import load_lessons as _load
    from peerhub.cli.recovery import REGISTRY
    assert not {lesson["id"] for lesson in _load()} & set(REGISTRY)  # no id can shadow a guidance code
    assert main(["explain", "ask_uncertain"]) == 0
    assert "Guidance Code: ask_uncertain" in capsys.readouterr().out
    assert [lesson["id"] for lesson in lookup_lessons("probe")] != []


def test_catalog_and_rendering():
    lessons = load_lessons()
    ids = {lesson["id"] for lesson in lessons}
    assert len(lessons) == len(ids)
    assert {"ask-metrics", "caller-ownership", "suggest"} <= ids
    assert not {"ag-empty-reply-causes", "ag-permission-limit"} & ids
    for lesson in lessons:
        assert all(lesson.values())
        assert len(render_lesson(lesson).splitlines()) <= 5
        assert lesson["issue"] in render_lesson(lesson)


def test_caller_ownership_explain_matches_reference_and_is_indexed(capsys):
    from tools.command_inventory import derive

    topic = "caller-ownership"
    assert main(["explain", "lessons"]) == 0
    assert topic in capsys.readouterr().out
    assert main(["explain", topic, "--json"]) == 0
    (lesson,) = json.loads(capsys.readouterr().out)["lessons"]
    assert all(word in lesson["fix"] for word in ("mine", "other", "unknown", "PEERHUB_OWNER", "cancel", "reuse"))
    assert f'- Fix: {lesson["fix"]}' in Path("docs/lessons.md").read_text(encoding="utf-8")
    assert topic in derive()["explain_topics"]
    assert main(["guide"]) == 0
    assert len(capsys.readouterr().out.encode("utf-8")) <= 1600


def test_lessons_generation_drift():
    assert Path("docs/lessons.md").read_text(encoding="utf-8") == render_lessons_reference()
