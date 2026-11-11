"""Offline lessons lookup and generated documentation parity (#117)."""
from pathlib import Path
import json

import pytest

from peerhub.cli.app import main
from peerhub.cli.lessons import load_lessons, lookup_lessons, render_lesson, render_lessons_reference

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
    ("ag", ["ag-recipe", "ag-bulk-profile-heuristics", "ag-silence-long-asks", "multi-peer-quota-shaping", "plan-windows"]),
    ("cc allow rules", ["cc-allow-rules"]),
    ("fable haiku", ["cc-allow-rules"]),
    ("monitor", ["monitor-states", "plan-windows"]),
    ("low fast aged suggest alternative cycles tty legend", ["monitor-states"]),
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
    assert len(captured.err.splitlines()) == 1


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
    assert len(lessons) == len({lesson["id"] for lesson in lessons}) == 27
    assert not {"ag-empty-reply-causes", "ag-permission-limit"} & {lesson["id"] for lesson in lessons}
    for lesson in lessons:
        assert all(lesson.values())
        assert len(render_lesson(lesson).splitlines()) <= 5
        assert lesson["issue"] in render_lesson(lesson)


def test_lessons_generation_drift():
    assert Path("docs/lessons.md").read_text(encoding="utf-8") == render_lessons_reference()
