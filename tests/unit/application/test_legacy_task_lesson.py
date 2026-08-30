from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import LegacyActionCall, LegacyTranslator, TranslatedCommand


def _submission() -> SubmissionMetadata:
    return SubmissionMetadata("req", "corr", "client", "cx", {}, "idem", None, None, 1)


def test_task_and_lesson_legacy_actions_translate_with_wire_params() -> None:
    translator = LegacyTranslator()
    checkpoint = translator.translate(LegacyActionCall("task-checkpoint", {
        "task_id": "t1", "actor_id": "cx", "checkpoint_id": "cp1", "stage": "build",
        "request_id": "r1", "attempt_id": "a1", "resume_token_ref": "token-1",
        "completed_units": ["u1"], "remaining_units": ["u2"], "expected_revision": 3,
    }), _submission())
    assert isinstance(checkpoint, TranslatedCommand)
    assert checkpoint.command.method == "coordination.task.checkpoint"
    assert checkpoint.command.encode_params() == {
        "task_id": "t1", "actor_id": "cx", "checkpoint_id": "cp1", "stage": "build",
        "request_id": "r1", "attempt_id": "a1", "resume_token_ref": "token-1",
        "completed_units": ("u1",), "remaining_units": ("u2",), "expected_revision": 3,
    }
    status = translator.translate(LegacyActionCall("task-status", {"task_id": "t1"}), _submission())
    assert isinstance(status, TranslatedCommand)
    assert status.command.method == "coordination.task.status"
    assert status.command.encode_params() == {"task_id": "t1"}
    failover = translator.translate(LegacyActionCall("task-failover", {
        "task_id": "t1", "to_actor_id": "ag", "reason": "unavailable", "expected_revision": 4,
    }), _submission())
    assert isinstance(failover, TranslatedCommand)
    assert failover.command.method == "coordination.task.failover"
    assert failover.command.encode_params() == {"task_id": "t1", "to_actor_id": "ag", "reason": "unavailable", "expected_revision": 4}
    propose = translator.translate(LegacyActionCall("lessons-propose", {
        "lesson_id": "l1", "title": "Title", "rule": "Rule", "category": "cat", "severity": "high",
        "proposer_id": "cx", "affected_peers": ["ag"], "scope_kind": "room", "workspace_id": "w1",
    }), _submission())
    assert isinstance(propose, TranslatedCommand)
    assert propose.command.method == "governance.lesson.propose"
    assert propose.command.encode_params() == {
        "lesson_id": "l1", "title": "Title", "rule": "Rule", "category": "cat", "severity": "high",
        "proposer_id": "cx", "affected_peers": ("ag",), "scope_kind": "room", "workspace_id": "w1",
        "sticky": False, "os": None, "shell": None, "task_types": None,
    }
    activate = translator.translate(LegacyActionCall("lessons-activate", {"lesson_id": "l1", "actor_id": "cx", "expected_revision": 2}), _submission())
    assert isinstance(activate, TranslatedCommand)
    assert activate.command.method == "governance.lesson.activate"
    assert activate.command.encode_params() == {"lesson_id": "l1", "actor_id": "cx", "expected_revision": 2}
    retire = translator.translate(LegacyActionCall("lessons-retire", {"lesson_id": "l1", "actor_id": "cx", "reason": "superseded", "expected_revision": 5}), _submission())
    assert isinstance(retire, TranslatedCommand)
    assert retire.command.method == "governance.lesson.retire"
    assert retire.command.encode_params() == {"lesson_id": "l1", "actor_id": "cx", "reason": "superseded", "expected_revision": 5}
