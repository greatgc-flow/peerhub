"""Offline lesson lookup and generated reference, sourced from packaged TOML."""
from __future__ import annotations

from importlib.resources import files
import re
import tomllib
from typing import NotRequired, TypedDict, cast


class Lesson(TypedDict):
    id: str
    title: str
    keywords: list[str]
    symptom: str
    fix: str
    issue: str
    guide_order: NotRequired[int]
    aliases: NotRequired[list[str]]


def load_lessons() -> list[Lesson]:
    data = tomllib.loads(files("peerhub.config_data").joinpath("lessons.toml").read_text(encoding="utf-8"))
    lessons = cast(list[Lesson], data["lessons"])
    for lesson in lessons:
        if lesson["id"] == "exit-codes":
            from peerhub._exit_codes import DIAG_EXIT_BY_STATUS
            from peerhub.cli.recovery import REGISTRY, RESULT_STATUS_REGISTRY

            lesson["fix"] = lesson["fix"].format(
                ask={status: entry.exit_code for status, entry in RESULT_STATUS_REGISTRY.items()},
                error={code: entry.exit_code for code, entry in REGISTRY.items()},
                diag=DIAG_EXIT_BY_STATUS,
            )
    return sorted(lessons, key=lambda lesson: lesson["id"])


def lesson_index() -> list[dict[str, str]]:
    """`peerhub explain lessons`: one {id, title} per lesson, in ID order."""
    return [{"id": lesson["id"], "title": lesson["title"]} for lesson in load_lessons()]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", text.casefold()))


def lesson_by_id(query: str) -> list[Lesson]:
    """Match an exact lesson ID or alias, case-insensitively."""
    wanted = query.strip().casefold()
    return [lesson for lesson in load_lessons()
            if wanted == lesson["id"].casefold()
            or wanted in (alias.casefold() for alias in lesson.get("aliases", ()))]


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
    fix = lesson["fix"].replace("\n", "\n       ")
    return "\n".join([
        f"{lesson['id']}: {lesson['title']}",
        f"  Symptom: {lesson['symptom']}",
        f"  Fix: {fix}",
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
        "Guidance codes and result labels take priority, followed by exact profile IDs (for example `cc.effort`), the index (bare `peerhub explain`, `lessons` or `list`), exact lesson IDs and aliases, and keyword lookup. Each alias opens its lesson in full. Keyword matches list recipes first, then sort by ID. A single match prints in full. Open one of several matches with `peerhub explain <id>`.",
    ]
    doc_path = re.compile(r"(?<![\w/])(?:docs/[\w./-]+|README|CONTRIBUTING)\.md(?:#[\w-]+)?")
    for lesson in load_lessons():
        fix = doc_path.sub(lambda m: f"[{m[0]}]({m[0][5:] if m[0].startswith('docs/') else '../' + m[0]})", lesson["fix"])
        fix = fix.replace("\n", "\n  ")
        lines.extend([
            "", f"## {lesson['title']} (`{lesson['id']}`)", "",
        ])
        if aliases := lesson.get("aliases"):
            lines.append("- Aliases: " + ", ".join(f"`{alias}`" for alias in aliases))
        lines.extend([
            f"- Keywords: {', '.join(lesson['keywords'])}",
            f"- Symptom: {lesson['symptom']}",
            f"- Fix: {fix}",
            f"- Issue: {lesson['issue']}",
        ])
    return "\n".join(lines) + "\n"
