"""Offline lesson lookup and generated reference, sourced from packaged TOML."""
from __future__ import annotations

from importlib.resources import files
import re
import tomllib
from typing import TypedDict, cast


class Lesson(TypedDict):
    id: str
    title: str
    keywords: list[str]
    symptom: str
    fix: str
    issue: str


def load_lessons() -> list[Lesson]:
    data = tomllib.loads(files("peerhub.config_data").joinpath("lessons.toml").read_text(encoding="utf-8"))
    return sorted(cast(list[Lesson], data["lessons"]), key=lambda lesson: lesson["id"])


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", text.casefold()))


def lesson_by_id(query: str) -> list[Lesson]:
    """Exact (case-insensitive) lesson ID lookup: its own step in `explain`, after guidance codes and before keyword search."""
    wanted = query.strip().casefold()
    return [lesson for lesson in load_lessons() if lesson["id"] == wanted]


def lookup_lessons(query: str) -> list[Lesson]:
    """Require every query token in the title/keywords; recipes first, then stable ID order."""
    short_code = query.split(":", 1)[0].upper()
    if short_code in {"PROBE", "DRIFT"} and ":" in query:
        query = short_code
    tokens = _tokens(query)
    if not tokens:
        return []
    matches = [lesson for lesson in load_lessons()
               if tokens <= _tokens(" ".join([lesson["title"], *lesson["keywords"]]))]
    return sorted(matches, key=lambda lesson: not lesson["id"].endswith("-recipe"))  # stable: ID order within groups


def render_lesson(lesson: Lesson) -> str:
    return "\n".join([
        f"{lesson['id']}: {lesson['title']}",
        f"  Symptom: {lesson['symptom']}",
        f"  Fix: {lesson['fix']}",
        f"  Issue: {lesson['issue']}",
    ])


def render_lessons_reference() -> str:
    """Generate docs/lessons.md from the same catalog used by explain."""
    lines = [
        "# PeerHub Lessons",
        "",
        "Generated from `peerhub/config_data/lessons.toml`; edit that file, then regenerate with:",
        "",
        "```powershell",
        'python -c "from pathlib import Path; from peerhub.cli.lessons import render_lessons_reference; Path(\'docs/lessons.md\').write_text(render_lessons_reference(), encoding=\'utf-8\', newline=\'\\n\')"',
        "```",
        "",
        "Use `peerhub explain <free words>` for offline, case-insensitive AND-token lookup over titles and keywords.",
        "Known guidance codes and aliases take priority, then an exact lesson ID (the IDs below) before keyword lookup. Keyword matches list recipes first, then lesson ID order.",
    ]
    for lesson in load_lessons():
        lines.extend([
            "", f"## {lesson['title']} (`{lesson['id']}`)", "",
            f"- Keywords: {', '.join(lesson['keywords'])}",
            f"- Symptom: {lesson['symptom']}",
            f"- Fix: {lesson['fix']}",
            f"- Issue: {lesson['issue']}",
        ])
    return "\n".join(lines) + "\n"
