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


def lookup_lessons(query: str) -> list[Lesson]:
    """Require every query token in the title/keywords; return stable ID order."""
    tokens = _tokens(query)
    if not tokens:
        return []
    return [lesson for lesson in load_lessons()
            if tokens <= _tokens(" ".join([lesson["title"], *lesson["keywords"]]))]


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
        "Known guidance codes and aliases take priority. Matches are ordered by lesson ID.",
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
