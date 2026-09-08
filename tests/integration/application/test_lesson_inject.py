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

