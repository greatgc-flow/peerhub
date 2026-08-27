from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import LegacyActionCall, LegacyTranslator, TranslatedCommand


def _submission() -> SubmissionMetadata:
    return SubmissionMetadata("req", "corr", "client", "cx", {}, "idem", None, None, 1)


def test_new_topic_translates_to_thread_create_wire_command() -> None:
    outcome = LegacyTranslator().translate(
        LegacyActionCall("new-topic", {"thread_id": "thread-1", "room_id": "room-1", "subject": "Topic", "creator_id": "cx"}),
        _submission(),
    )
    assert isinstance(outcome, TranslatedCommand)
    assert outcome.command.method == "coordination.topic.create"
    assert outcome.command.encode_params() == {"thread_id": "thread-1", "room_id": "room-1", "subject": "Topic", "creator_id": "cx"}


def test_clear_room_translates_all_room_boundary_fields() -> None:
    outcome = LegacyTranslator().translate(
        LegacyActionCall("clear-room", {"old_room_id": "old", "new_room_id": "new", "subject": "Fresh", "actor_id": "cx"}),
        _submission(),
    )
    assert isinstance(outcome, TranslatedCommand)
    assert outcome.command.method == "coordination.room.clear"
    assert outcome.command.encode_params() == {"old_room_id": "old", "new_room_id": "new", "subject": "Fresh", "actor_id": "cx"}


def test_thread_react_translates_all_reaction_fields() -> None:
    outcome = LegacyTranslator().translate(
        LegacyActionCall(
            "thread-react",
            {
                "message_id": "message-1",
                "room_id": "room-1",
                "actor_instance_id": "cx-terminal",
                "actor_profile_id": "cx",
                "reaction_type": "ACK",
            },
        ),
        _submission(),
    )
    assert isinstance(outcome, TranslatedCommand)
    assert outcome.command.method == "coordination.thread.react"
    assert outcome.command.encode_params() == {
        "message_id": "message-1",
        "room_id": "room-1",
        "actor_instance_id": "cx-terminal",
        "actor_profile_id": "cx",
        "reaction_type": "ACK",
        "action": "ADD",
    }
