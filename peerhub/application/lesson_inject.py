from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Iterable

from peerhub.core.protocol import JsonValue
from peerhub.governance.activity import list_active_lessons
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import TargetState


def _as_str_tuple(value: JsonValue | None) -> tuple[str, ...] | None:
    if not isinstance(value, tuple):
        return None
    strings = tuple(item for item in value if isinstance(item, str))
    if len(strings) != len(value):
        return None
    return strings


def _as_mapping(value: JsonValue | None) -> Mapping[str, JsonValue]:
    if isinstance(value, Mapping):
        return value
    return {}


@dataclass(frozen=True, slots=True)
class LessonInjectionPolicy:
    enabled: bool = True
    min_severity: str = "medium"
    max_chars: int = 1200
    max_items: int = 8
    critical_always_include: bool = True


@dataclass(frozen=True, slots=True)
class LessonInjectionContext:
    os: str | None = None
    shell: str | None = None
    task_types: frozenset[str] = frozenset()


def render_lesson_block(lessons: Iterable[TargetState], policy: LessonInjectionPolicy) -> str | None:
    if not policy.enabled:
        return None

    # Severity rank dictionary (lowercase)
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    min_rank = severity_rank.get(policy.min_severity.lower(), 2)

    filtered: list[TargetState] = []
    for target in lessons:
        state = target.state
        content = _as_mapping(state.get("content"))
        sev = content.get("severity", "medium")
        if not isinstance(sev, str):
            sev = "medium"
        rank = severity_rank.get(sev.lower(), 2)
        if rank > min_rank:
            continue
        filtered.append(target)

    # Sort
    def sort_key(t: TargetState) -> tuple[int, int]:
        state = t.state
        content = _as_mapping(state.get("content"))
        sev = content.get("severity", "medium")
        if not isinstance(sev, str):
            sev = "medium"
        is_sticky = bool(state.get("sticky", False))
        is_critical = (sev.lower() == "critical")
        priority = 0 if (is_sticky and is_critical) else 1
        return (priority, severity_rank.get(sev.lower(), 2))

    sorted_lessons = sorted(filtered, key=sort_key)

    lines: list[str] = []
    chars = 0
    omitted = 0

    for i, target in enumerate(sorted_lessons):
        state = target.state
        content = _as_mapping(state.get("content"))
        sev = content.get("severity", "medium")
        if not isinstance(sev, str):
            sev = "medium"
        is_critical = (sev.lower() == "critical")
        always_include = policy.critical_always_include and is_critical

        if i >= policy.max_items and not always_include:
            omitted += 1
            continue

        lesson_id = state.get("lesson_id", target.target_id.split(":", 1)[-1])
        rule = content.get("rule", "")
        # Compact rule: limit newlines? The legacy behavior format is "- SEVERITY ID: compact_rule"
        entry = f"- {sev.upper()} {lesson_id}: {rule}"

        if chars + len(entry) > policy.max_chars and not always_include:
            omitted += 1
            continue

        lines.append(entry)
        chars += len(entry)

    if not lines:
        return None

    block_lines = ["[PEER LESSONS]"]
    block_lines.extend(lines)
    if omitted > 0:
        pack_path = "_sys/ai/knowledge/general/active-lessons.jsonl"
        block_lines.append(f"Omitted: {omitted} lower-priority matches. Full pack: {pack_path}")
    return "\n".join(block_lines)


def inject_lessons(
    broker: GovernanceBroker,
    *,
    target_peer_id: str,
    workspace_id: str,
    context: LessonInjectionContext,
    policy: LessonInjectionPolicy,
) -> str | None:
    if not policy.enabled:
        return None

    # Fetch all global and workspace active lessons
    all_active = list_active_lessons(broker, scope=None)

    applicable: list[TargetState] = []
    for target in all_active:
        state = target.state
        scope = state.get("scope", {})
        if not isinstance(scope, Mapping):
            continue

        kind = scope.get("kind")
        wid = scope.get("workspace_id")
        if kind == "global" and wid is None:
            pass  # Match
        elif kind == "workspace" and wid == workspace_id:
            pass  # Match
        else:
            continue  # Exclude others

        # Affected peers filtering
        affected_peers = _as_str_tuple(state.get("affected_peers"))
        if affected_peers:
            if target_peer_id not in affected_peers:
                continue

        # Applicability
        applicability = state.get("applicability")
        if isinstance(applicability, Mapping):
            req_os = _as_str_tuple(applicability.get("os"))
            if req_os and context.os:
                if context.os not in req_os:
                    continue

            req_shell = _as_str_tuple(applicability.get("shell"))
            if req_shell and context.shell:
                if context.shell not in req_shell:
                    continue

            req_tasks = _as_str_tuple(applicability.get("task_types"))
            if req_tasks and context.task_types:
                if not set(req_tasks).intersection(context.task_types):
                    continue

        applicable.append(target)

    return render_lesson_block(applicable, policy)
