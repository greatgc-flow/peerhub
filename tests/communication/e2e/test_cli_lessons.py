"""Offline lessons lookup and generated documentation parity (#117)."""
import ast
import inspect
from pathlib import Path
import json
import re

import pytest

from peerhub.cli.app import main
from peerhub.cli.guide import render_guide
from peerhub.cli.lessons import lesson_by_id, load_lessons, lookup_lessons, render_lesson, render_lessons_reference
from peerhub.core.peer_kinds import BUILTIN_PEER_KINDS
from peerhub.extensions import monitor_alerts

pytestmark = [pytest.mark.unit]
MONITOR_LESSON = "monitor-states"


@pytest.mark.parametrize(("query", "expected"), [
    ("AG SILENCE", ["ag-recipe"]),
    ("permission ag", ["ag-recipe"]),
    ("pip checkout", ["installed-vs-checkout"]),
    ("PYTHONPATH", ["installed-vs-checkout"]),
    ("read-only", ["ag-recipe"]),
    ("cx plan window", [MONITOR_LESSON]),
    ("cx-plan-windows", [MONITOR_LESSON]),
    ("cc absent", [MONITOR_LESSON]),
    ("ag not reported", [MONITOR_LESSON]),
    ("ag empty reply", ["ag-recipe"]),
    ("denied_empty", ["ag-recipe"]),
    ("ag", ["ag-recipe", MONITOR_LESSON, "parallel-quota"]),
    ("cc allow rules", ["cc-allow-rules"]),
    ("fable haiku", ["cc-allow-rules"]),
    ("monitor", [MONITOR_LESSON]),
    ("monitor-view", [MONITOR_LESSON]),
    ("order", [MONITOR_LESSON]),
    ("glyph", [MONITOR_LESSON]),
    ("colour bar", [MONITOR_LESSON]),
    ("pill", [MONITOR_LESSON]),
    ("quota unknown", [MONITOR_LESSON]),
    ("low fast aged cycles tty legend", [MONITOR_LESSON]),
    ("accounts", [MONITOR_LESSON]),
    ("review-loop", ["independent-peer-votes"]),
    ("review-scores-to-100", ["independent-peer-votes"]),
    ("pytest basetemp", ["cc-allow-rules"]),
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
    ("handoff", []),
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
    lesson_id = "ag-recipe"
    assert main(["--db", str(db), "explain", "ag", "silence"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(f"{lesson_id}:")
    assert "--silence-timeout-seconds 7200" in "\n".join(lines)
    assert not db.exists()
    assert main(["explain", "ag silence", "--json"]) == 0
    lessons = json.loads(capsys.readouterr().out)["lessons"]
    assert [lesson["id"] for lesson in lessons] == [lesson_id]


def test_explain_ag_leads_with_recipe_and_records_open_observation(capsys):
    """ag usability A1/A3: the merged recipe is listed first; E2 stays an open observation linked to #302."""
    assert main(["explain", "ag"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("ag-recipe:")
    recipe = next(lesson for lesson in load_lessons() if lesson["id"] == "ag-recipe")
    for needle in ('--query-file and say "no tools"', "trusted project root", "--writable on every turn",
                   "start a new stream with the content inlined", "docs/adapters/ag.md"):
        assert needle in recipe["fix"], needle
    assert "#302" in recipe["issue"] and "#140" in recipe["issue"]


def test_cc_allow_rules_lesson_states_exact_match_and_prefix_fix():
    """#303 B2: exact-match Bash rules vs model command shape; prefix rules are the fix."""
    lesson = next(lesson for lesson in load_lessons() if lesson["id"] == "cc-allow-rules")
    for needle in ("exact command string", "Bash(peerhub:*)", "denied_actions", "docs/adapters/cc.md"):
        assert needle in lesson["fix"], needle
    assert "#303" in lesson["issue"]


def test_no_match_hint(capsys):
    assert main(["explain", "ag", "checkout"]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "No matching lessons" in captured.err
    assert "Try fewer keywords" in captured.err
    assert "peerhub explain lessons" in captured.err
    assert ".; next:" not in captured.err
    assert captured.err.strip().endswith("; next: peerhub explain lessons")
    assert len(captured.err.splitlines()) == 1


@pytest.mark.parametrize("query", [[], ["lessons"], ["list"]])
def test_explain_lessons_prints_index_before_any_lesson_lookup(capsys, monkeypatch, query):
    """#366 A5: `explain lessons` is an index (id: title; --json {"lessons": [{id, title}]}), never a keyword search."""
    from peerhub.cli import lessons as lessons_mod
    calls = []
    monkeypatch.setattr(lessons_mod, "lesson_by_id", lambda q: calls.append("id") or [])
    monkeypatch.setattr(lessons_mod, "lookup_lessons", lambda q: calls.append("keywords") or [])
    catalog = load_lessons()
    assert main(["explain", *query]) == 0
    assert capsys.readouterr().out.splitlines() == [f"{lesson['id']}: {lesson['title']}" for lesson in catalog]
    assert main(["explain", *query, "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"lessons": [{"id": lesson["id"], "title": lesson["title"]} for lesson in catalog]}
    assert calls == []


def test_explain_help_advertises_the_index(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["explain", "--help"])
    assert exc.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "lessons (also list)" in help_text
    assert "lesson ID or alias" in help_text


def test_explain_json_envelope_is_the_same_for_index_and_keyword_lookup(capsys):
    """Discovery JSON is IDs and titles; exact-ID JSON remains the full lesson."""
    for query in (["lessons"], ["ag", "silence"]):
        assert main(["explain", *query, "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert list(payload) == ["lessons"] and isinstance(payload["lessons"], list) and payload["lessons"]
        assert all(set(item) == {"id", "title"} for item in payload["lessons"])
    assert main(["explain", "ag-silence-long-asks", "--json"]) == 0
    full = json.loads(capsys.readouterr().out)["lessons"][0]
    assert {"id", "title", "symptom", "fix", "issue"} <= set(full)


def test_review_recipe_links_to_the_release_bar():
    """#366 A5: the review cluster holds the pasteable brief."""
    (lesson,) = lesson_by_id("review-recipe")
    fix = lesson["fix"]
    for needle in ("Score each item 0-100", "violates a documented contract", "reachable from supported inputs",
                   "recommendation", "exact fix", "CONTRIBUTING.md#release-lessons", "Re-review the exact shipped tag",
                   "another model family", "full suite once", "only targeted tests", "two rounds",
                   "narrow its contract (fail closed)", "`git add -A`", "agent worktrees"):
        assert needle in fix, needle


def test_instruction_diet_points_agents_to_the_planning_view(capsys):
    assert main(["explain", "instruction-diet"]) == 0
    assert "peerhub diag --plan each turn" in capsys.readouterr().out


def test_explain_covers_emitted_monitor_alert_codes(capsys):
    emitted: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(monitor_alerts))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_alert":
            code = node.args[0]
            assert isinstance(code, ast.Constant) and isinstance(code.value, str)
            emitted.add(code.value)
    (lesson,) = lesson_by_id(MONITOR_LESSON)
    documented = set(re.findall(r"^- (a\d+) ", lesson["fix"], re.MULTILINE))
    assert {f"a{i}" for i in range(1, 7)} <= documented
    assert emitted and emitted <= documented
    for topic in sorted(documented):
        assert main(["explain", topic]) == 0, topic
        out = capsys.readouterr().out
        assert out.startswith(f"{MONITOR_LESSON}:")
        assert "docs/monitor.md#alerts" in out
        assert sum(line.strip().startswith(f"- {topic} ") for line in out.splitlines()) == 1


def test_lessons_without_peerhub_mechanism_are_deleted():
    ids = {lesson["id"] for lesson in load_lessons()}
    assert not {"handoff", "pin-census-before-implementation", "dry-run-path-aware-checkers", "explicit-shell-wait-conditions"} & ids


def test_new_and_moved_lesson_content():
    """#366 A1/A4/A5: subagents, cx tmp_path limits, expanded ACCOUNTS, votes and timeouts."""
    by_id = {lesson["id"]: lesson for lesson in load_lessons()}
    sub = by_id["peer-subagents"]["fix"]
    for needle in ("Agent tool", "spawn_agent", "names that tool explicitly", "invoke_subagent",
                   "no /loop-style persistence", "same peer quota", "disjoint task", "one merged final report"):
        assert needle in sub, needle
    assert "#361" in by_id["peer-subagents"]["issue"]
    cx_fix = by_id["cc-allow-rules"]["fix"]
    assert "tmp_path tests as NOT RUN" in cx_fix and "docs/adapters/cx.md" in cx_fix
    cx_doc = " ".join(Path("docs/adapters/cx.md").read_text(encoding="utf-8").split())
    for needle in ("Tests that use `tmp_path` cannot run", "`--basetemp`", "measured 2026-11-14 on Python 3.15",
                   "Never add shims or temporary folders"):
        assert needle in cx_doc, needle
    assert "--basetemp=" not in cx_fix  # the measured workaround does not work
    assert {"pytest", "tmp_path", "acl", "basetemp"} <= set(by_id["cc-allow-rules"]["keywords"])
    # #374: ACCOUNTS left the compact frame; account problems reach line 1 as alerts
    account_fix = by_id[MONITOR_LESSON]["fix"]
    assert "ACCOUNTS in --expand" in account_fix and "docs/monitor.md#accounts" in account_fix
    monitor = " ".join(Path("docs/monitor.md").read_text(encoding="utf-8").split())
    for needle in ("raise a line-1 alert", "display_plan", "plan_changed: true", "at least five minutes old"):
        assert needle in monitor, needle
    votes = by_id["independent-peer-votes"]["fix"]
    for needle in ("--max-parallel n --responses", "not writable", "private empty directory", "inline",
                   "routine check = ONE reviewer, FRESH", "3 independent peers, unanimity", "VERDICT / BLOCKERS / NITS"):
        assert needle in votes, needle
    timeouts = by_id["ask-timeouts"]["fix"]
    assert "600-second default" in timeouts and "docs/guidance-reference.md#ask_uncertain" in timeouts
    commands = Path("docs/commands.md").read_text(encoding="utf-8")
    ask = commands.split("## Ask", 1)[1].split("## Diagnostics", 1)[0]
    assert "1800–3600" in ask and "7200" in ask and "600" in ask


def test_ag_adapter_holds_the_full_recipe():
    doc = " ".join(Path("docs/adapters/ag.md").read_text(encoding="utf-8").split())
    for needle in ("one task per ask", "Verify Flash-tier claims", "30,000 on Windows",
                   "120,000 elsewhere", "use cc/cx on a separate stream", "new request ID",
                   "temporary or scratch directory", "even for read tasks", "workspace-wide edit approval",
                   "after an empty or `denied_empty` reply", "agy auto-denies", "three re-tests"):
        assert needle.casefold() in doc.casefold(), needle


def test_ag_recipe_names_allow_list_scope_and_cx_lesson_holds_the_cx_facts():
    """#391: a `*` in a prompt path (not glob-expanded), allow-list scope in the order of causes, inline text for ag; cx facts stay in the cx lesson."""
    by_id = {lesson["id"]: lesson for lesson in load_lessons()}
    ag = by_id["ag-recipe"]
    doc = " ".join(Path("docs/adapters/ag.md").read_text(encoding="utf-8").split())
    for needle in ("a literal `*` is never expanded", "Allow-list scope", "Headless reads outside the workspace",
                   "`denied_actions` shows tool names only"):
        assert needle in doc, needle
    assert "#391" in ag["issue"] and {"allow-list", "scope", "glob", "wildcard"} <= set(ag["keywords"])
    for cx_fact in ("peerhub store", "checkpoint receipts", "runtime_error"):
        assert cx_fact not in ag["fix"], cx_fact  # cx sentences are not in the ag recipe
    cx = by_id["cc-allow-rules"]
    for needle in ("cx sandbox lanes cannot open the peerhub store",
                   "reader asks must be launched by the orchestrator",
                   "use checkpoint receipts and a silence timeout",
                   "runtime_error can leave partial edits"):
        assert needle in cx["fix"], needle
    assert "#391" in cx["issue"] and {"store", "checkpoint", "runtime_error"} <= set(cx["keywords"])
    assert [lesson["id"] for lesson in lookup_lessons("ag allow-list scope")] == ["ag-recipe"]
    assert [lesson["id"] for lesson in lookup_lessons("cx checkpoint")] == ["cc-allow-rules"]


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


def test_setup_lesson_points_to_the_install_table(capsys):
    from peerhub.cli.recovery_render import get_guidance

    assert get_guidance("setup") is None
    (lesson,) = lesson_by_id("setup")
    fix = lesson["fix"]
    assert lesson["keywords"] and lesson["issue"] == "#385"
    assert "README.md#what-each-peer-needs" in fix
    assert "OPENAI_API_KEY" in fix and "GEMINI_API_KEY" in fix and "Keep keys off the command line" in fix
    assert not [command for command in INSTALL_COMMANDS if command in fix]
    assert "executable not found" in lesson["symptom"] and "set OPENAI_API_KEY" in lesson["symptom"]
    assert main(["explain", "setup"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("setup:") and len(out.splitlines()) <= 5


def test_readme_and_guide_document_the_builtin_peers():
    """#385 S3: the README install table covers every built-in peer."""
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
    assert _links(section) == {page for _tool, page in SETUP_TOOLS.values()} | {page for _var, page in SETUP_KEYS.values()} | {ANT_WINDOWS}
    assert not [command for command in INSTALL_COMMANDS if command in section.split("### What each peer needs", 1)[1]]


def test_known_guidance_and_alias_take_priority(capsys):
    for query in ("ask_uncertain", "uncertain"):
        assert main(["explain", query]) == 0
        assert "Guidance Code: ask_uncertain" in capsys.readouterr().out


def test_exact_lesson_id_is_its_own_step_before_keywords():
    """#328 C: exact IDs and aliases have their own lookup step."""
    assert [lesson["id"] for lesson in lesson_by_id("monitor-probe-age")] == [MONITOR_LESSON]
    assert [lesson["id"] for lesson in lesson_by_id("  Monitor-Probe-Age ")] == [MONITOR_LESSON]
    assert lesson_by_id("monitor probe age") == [] and lesson_by_id("probe") == [] and lesson_by_id("") == []


def test_all_old_lesson_names_open_their_cluster(capsys):
    from peerhub.cli.explain_cmd import run_explain_cli

    # Pin the previous public names independently of the packaged alias list.
    old_names = {
        "ag-recipe": ("ag-requires-writable", "ag-silence-long-asks", "ag-bulk-profile-heuristics",
                      "ag-empty-reply-causes", "ag-permission-limit"),
        "cc-allow-rules": ("cx-sandbox-and-prompt-hygiene", "concurrent-cc-lanes"),
        "independent-peer-votes": ("review-recipe", "batch-review-and-verify", "review-loop", "review-scores-to-100"),
        "monitor-states": ("monitor-view", "monitor-accounts", "monitor-alerts", "monitor-insuff",
                           "monitor-stale-window", "monitor-probe-age", "monitor-drift", "plan-windows", "cx-plan-windows"),
        "parallel-quota": ("quota-aware-peer-runs", "multi-peer-quota-shaping", "batch-and-load", "stream-reuse-cost"),
    }
    standalone = {
        "api-peers", "ask-basics", "ask-metrics", "ask-timeouts", "batch-shell-crlf", "caller-ownership",
        "caller-timeout-claim-held", "discovery-commands", "exit-codes", "installed-vs-checkout", "instruction-diet",
        "invalid-arguments", "peer-subagents", "report-issues", "setup", "store-and-upgrade", "store-missing",
        "writes-and-uncertain",
    }
    by_id = {lesson["id"]: lesson for lesson in load_lessons()}
    assert not {alias for names in old_names.values() for alias in names} & by_id.keys()
    for key in sorted(standalone | old_names.keys()):
        lesson = by_id[key]
        for name in (key, *old_names.get(key, ())):
            assert run_explain_cli(name) == 0, name
            assert capsys.readouterr().out == render_lesson(lesson) + "\n", name
            assert run_explain_cli(name, json_output=True) == 0, name
            assert json.loads(capsys.readouterr().out) == {"lessons": [lesson]}, name


def test_explain_order_is_guidance_then_lesson_id_then_keywords(capsys, monkeypatch):
    """A later profile-id step slots between guidance and lesson id; the id lookup must stay a step of run_explain_cli."""
    from peerhub.cli import explain_cmd, lessons
    calls = []
    real_by_id, real_lookup = lessons.lesson_by_id, lessons.lookup_lessons
    monkeypatch.setattr(lessons, "lesson_by_id", lambda q: calls.append("id") or real_by_id(q))
    monkeypatch.setattr(lessons, "lookup_lessons", lambda q: calls.append("keywords") or real_lookup(q))
    assert explain_cmd.run_explain_cli("ask_uncertain") == 0
    assert calls == []  # a guidance code never reaches the lessons
    capsys.readouterr()
    assert explain_cmd.run_explain_cli("monitor-probe-age") == 0
    assert calls == ["id"]  # an exact id does not fall through to keyword search
    capsys.readouterr()
    calls.clear()
    assert explain_cmd.run_explain_cli("probe") == 0
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
    aliases = [alias for lesson in lessons for alias in lesson.get("aliases", ())]
    assert len(lessons) == len(ids)
    assert len(aliases) == len(set(aliases)) and ids.isdisjoint(aliases)
    assert {"ask-metrics", "caller-ownership"} <= ids
    assert not {"ag-empty-reply-causes", "ag-permission-limit"} & ids
    for lesson in lessons:
        assert all(lesson.values())
        rendered = render_lesson(lesson)
        if not lesson.get("aliases"):
            assert len(rendered.splitlines()) <= 5
        assert lesson["issue"] in rendered


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


def test_monitor_view_explains_guide_trailer_and_matches_reference(capsys):
    assert main(["explain", "monitor-view", "--json"]) == 0
    (lesson,) = json.loads(capsys.readouterr().out)["lessons"]
    guide_rule = next(sentence for sentence in lesson["fix"].split(". ") if "PEERHUB_NO_LINKS" in sentence)
    assert all(word in guide_rule for word in ("OSC 8", "Guide:", "fits"))
    reference = Path("docs/lessons.md").read_text(encoding="utf-8")
    assert guide_rule in reference and "[docs/monitor.md](monitor.md)" in reference


def test_lesson_ids_and_aliases_match_case_insensitively():
    assert [lesson["id"] for lesson in lesson_by_id("AG-REQUIRES-WRITABLE")] == ["ag-recipe"]
    assert [lesson["id"] for lesson in lesson_by_id("Ag-Recipe")] == ["ag-recipe"]


def test_monitor_states_uses_canonical_legend_and_drops_removed_states_and_dev_note(capsys):
    from peerhub.cli.monitor_legend import POOL_STATE_LEGEND, RESET_PENDING_NOTE

    assert main(["explain", "a1"]) == 0
    out = capsys.readouterr().out
    assert POOL_STATE_LEGEND in out
    assert "blocked" in out and RESET_PENDING_NOTE in out and "version_drift" in out
    for stale in ("INSUFF", "STALE:window", "failed PROBE", "Check monitor changes"):
        assert stale not in out, stale
