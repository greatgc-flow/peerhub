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
        elif lesson["id"] == "parallel-quota":
            from peerhub.core.load_limits import CPU_BUSY_WARN_PCT, MIN_FREE_RAM_GB, PLAN_NA_POOLS_LABEL

            lesson["fix"] = lesson["fix"].format(plan_na_pools=PLAN_NA_POOLS_LABEL,
                                                ram_min=MIN_FREE_RAM_GB, cpu_warn=CPU_BUSY_WARN_PCT)
    return sorted(lessons, key=lambda lesson: lesson["id"])


def lesson_index() -> list[dict[str, str]]:
    """`peerhub explain lessons`: one {id, title} per lesson, in ID order."""
    return [{"id": lesson["id"], "title": lesson["title"]} for lesson in load_lessons()]


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
        "Known guidance codes and aliases take priority, then an exact profile ID (for example `cc.effort`), then `lessons` (an index: one `id: title` line per lesson), then an exact lesson ID (the IDs below) before keyword lookup. Keyword matches list recipes first, then lesson ID order; one match prints in full, several print one `id: title` line each (open one with `peerhub explain <id>`).",
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
