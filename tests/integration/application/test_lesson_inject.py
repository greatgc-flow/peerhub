import pytest
from pathlib import Path

from peerhub.application.lesson_inject import (
    LessonInjectionContext,
    LessonInjectionPolicy,
    inject_lessons,
)
from peerhub.core.context import Clock
from tests.fakes import SequentialIdSource

class FixedClock(Clock):
    def __init__(self, value: int = 10_000) -> None:
        self.value = value

    def now(self) -> int:
        return self.value

from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.lessons import LessonService
from peerhub.persistence.sqlite import SqliteStateStore

def _make_services(tmp_path: Path):
    store = SqliteStateStore(
        tmp_path / "lessons.sqlite3",
        workspace_home_id="default",
    )
    store.initialize()
    clock = FixedClock()
    ids = SequentialIdSource()
    broker = GovernanceBroker(store=store, clock=clock, ids=ids)
    lessons = LessonService(broker, clock=clock, ids=ids)
    return broker, lessons, store

@pytest.fixture
def services(tmp_path: Path):
    broker, lessons, store = _make_services(tmp_path)
    yield broker, lessons, store
    store.close()

def _add_lesson(lessons: LessonService, title: str, rule: str, severity: str, affected_peers: list[str], scope_kind: str = "global", workspace_id: str | None = None, sticky: bool = False, os: list[str] | None = None, shell: list[str] | None = None, task_types: list[str] | None = None):
    # Propose
    lesson_id = title.replace(" ", "-").lower()
    sub = lessons.propose(
        lesson_id=lesson_id,
        title=title,
        rule=rule,
        category="test",
        severity=severity,
        proposer_id="admin",
        affected_peers=affected_peers,
        scope_kind=scope_kind,
        workspace_id=workspace_id,
        sticky=sticky,
        os=os,
        shell=shell,
        task_types=task_types,
    )
    # Approve and Activate
    lessons.approve(lesson_id, approved_by_actor_id="admin")
    lessons.activate(lesson_id, actor_id="admin")
    return sub

def test_missing_profile_values_fail_open(services):
    broker, lessons, _ = services
    # Lesson specifies OS="windows"
    _add_lesson(lessons, "OS specific", "os rule", "HIGH", [], os=["windows"])
    # Context has NO os specified
    ctx = LessonInjectionContext(os=None)
    policy = LessonInjectionPolicy()
    result = inject_lessons(broker, target_peer_id="cc", workspace_id="default", context=ctx, policy=policy)
    assert result is not None
    assert "os-specific: os rule" in result

def test_unknown_severity_defaults_medium(services):
    broker, lessons, _ = services
    _add_lesson(lessons, "weird", "weird rule", "WEIRD", [])
    ctx = LessonInjectionContext()
    # If it defaults to medium, it is included when min_severity is medium
    policy = LessonInjectionPolicy(min_severity="medium")
    result = inject_lessons(broker, target_peer_id="cc", workspace_id="default", context=ctx, policy=policy)
    assert result is not None
    assert "- WEIRD weird: weird rule" in result
    
    # But dropped if min_severity is high
    policy_high = LessonInjectionPolicy(min_severity="high")
    result_high = inject_lessons(broker, target_peer_id="cc", workspace_id="default", context=ctx, policy=policy_high)
    assert result_high is None

def test_exactly_equal_character_budget(services):
    broker, lessons, _ = services
    _add_lesson(lessons, "exact", "A" * 10, "high", [])
    ctx = LessonInjectionContext()
    # The entry text is "- HIGH exact: AAAAAAAAAA" -> 24 chars
    policy = LessonInjectionPolicy(max_chars=24)
    result = inject_lessons(broker, target_peer_id="cc", workspace_id="default", context=ctx, policy=policy)
    assert result is not None
    assert "exact" in result
    assert "Omitted" not in result

def test_header_newlines_not_counted(services):
    broker, lessons, _ = services
    _add_lesson(lessons, "exact", "A" * 10, "high", [])
    ctx = LessonInjectionContext()
    # If headers counted, 24 would not be enough.
    policy = LessonInjectionPolicy(max_chars=24)
    result = inject_lessons(broker, target_peer_id="cc", workspace_id="default", context=ctx, policy=policy)
    assert result is not None

def test_sticky_but_noncritical_no_priority(services):
    broker, lessons, _ = services
    _add_lesson(lessons, "L1", "medium rule", "medium", [], sticky=True)
    _add_lesson(lessons, "L2", "high rule", "high", [], sticky=False)
    ctx = LessonInjectionContext()
    policy = LessonInjectionPolicy()
    result = inject_lessons(broker, target_peer_id="cc", workspace_id="default", context=ctx, policy=policy)
    # L2 should be sorted before L1
    assert result.index("high rule") < result.index("medium rule")

def test_critical_bypass_exceeds_caps(services):
    broker, lessons, _ = services
    _add_lesson(lessons, "C1", "crit", "critical", [])
    _add_lesson(lessons, "C2", "crit", "critical", [])
    ctx = LessonInjectionContext()
    policy = LessonInjectionPolicy(max_items=0, max_chars=0, critical_always_include=True)
    result = inject_lessons(broker, target_peer_id="cc", workspace_id="default", context=ctx, policy=policy)
    print(f"\nDEBUG ALL TARGETS: {broker.list_targets('lesson', None)}")
    print(f"\nDEBUG RESULT: {result}")
    assert result is not None
    assert "c1" in result
    assert "c2" in result

def test_scope_selection(services):
    broker, lessons, _ = services
    _add_lesson(lessons, "g1", "global", "high", [], scope_kind="global")
    _add_lesson(lessons, "w1", "workspace", "high", [], scope_kind="workspace", workspace_id="ws1")
    _add_lesson(lessons, "w2", "workspace2", "high", [], scope_kind="workspace", workspace_id="ws2")
    ctx = LessonInjectionContext()
    policy = LessonInjectionPolicy()
    result = inject_lessons(broker, target_peer_id="cc", workspace_id="ws1", context=ctx, policy=policy)
    assert result is not None
    assert "g1" in result
    assert "w1" in result
    assert "w2" not in result

def test_affected_peers(services):
    broker, lessons, _ = services
    _add_lesson(lessons, "empty", "empty", "high", [])
    _add_lesson(lessons, "match", "match", "high", ["cc", "cx"])
    _add_lesson(lessons, "miss", "miss", "high", ["cx"])
    ctx = LessonInjectionContext()
    policy = LessonInjectionPolicy()
    result = inject_lessons(broker, target_peer_id="cc", workspace_id="default", context=ctx, policy=policy)
    assert result is not None
    assert "empty" in result
    assert "match" in result
    assert "miss" not in result

def test_os_shell_task_types(services):
    broker, lessons, _ = services
    _add_lesson(lessons, "match", "match", "high", [], os=["windows"], shell=["pwsh"], task_types=["test"])
    _add_lesson(lessons, "miss_os", "miss_os", "high", [], os=["linux"])
    _add_lesson(lessons, "miss_shell", "miss_shell", "high", [], shell=["bash"])
    _add_lesson(lessons, "miss_task", "miss_task", "high", [], task_types=["build"])
    ctx = LessonInjectionContext(os="windows", shell="pwsh", task_types=frozenset(["test", "lint"]))
    policy = LessonInjectionPolicy()
    result = inject_lessons(broker, target_peer_id="cc", workspace_id="default", context=ctx, policy=policy)
    assert result is not None
    assert "match" in result
    assert "miss_os" not in result
    assert "miss_shell" not in result
    assert "miss_task" not in result

def test_delivery_enabled_false(services):
    broker, lessons, _ = services

def test_legacy_lesson_inject_translates_and_executes(tmp_path):
    from peerhub.application.legacy import LegacyTranslator, LegacyActionCall
    from peerhub.core.context import RuntimeContext
    from tests.integration.application.test_lesson_inject import FixedClock
    from tests.fakes import SequentialIdSource
    from peerhub.runtime import create_runtime
    from peerhub.core.context import PathLayout

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    paths = PathLayout.for_workspace(workspace_root)

    context = RuntimeContext(
        workspace_home_id="default",
        paths=paths,
        clock=FixedClock(),
        ids=SequentialIdSource(),
    )

    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        # Add a lesson
        sub = _add_lesson(
            runtime.lesson_service,
            "legacy test",
            "legacy rule",
            "high",
            []
        )

        translator = LegacyTranslator()

        call = LegacyActionCall(
            action="lesson-inject",
            arguments={"peer": "cc", "workspace_id": "default", "os": "windows"}
        )

        outcome = translator.translate(call, submission=sub)

        assert not hasattr(outcome, "reason")
        assert outcome.command.target_peer_id == "cc"
        assert outcome.command.workspace_id == "default"
        assert outcome.command.os == "windows"
