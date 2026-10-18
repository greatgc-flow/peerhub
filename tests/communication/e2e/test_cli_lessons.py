"""Offline lessons lookup and generated documentation parity (#117)."""
from pathlib import Path
import json

import pytest

from peerhub.cli.app import main
from peerhub.cli.lessons import load_lessons, lookup_lessons, render_lesson, render_lessons_reference

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(("query", "expected"), [
    ("AG SILENCE", ["ag-silence-long-asks"]),
    ("permission ag", ["ag-permission-limit"]),
    ("pip checkout", ["installed-vs-checkout"]),
    ("PYTHONPATH", ["installed-vs-checkout"]),
    ("read-only", ["ag-permission-limit"]),
    ("ag", ["ag-permission-limit", "ag-silence-long-asks"]),
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


def test_catalog_and_rendering():
    lessons = load_lessons()
    assert len(lessons) == len({lesson["id"] for lesson in lessons}) == 3
    for lesson in lessons:
        assert all(lesson.values())
        assert len(render_lesson(lesson).splitlines()) <= 5
        assert lesson["issue"] in render_lesson(lesson)


def test_lessons_generation_drift():
    assert Path("docs/lessons.md").read_text(encoding="utf-8") == render_lessons_reference()
