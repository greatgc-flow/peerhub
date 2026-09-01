"""SQLite-backed integration coverage for the core native status view."""

from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import StatusReadCommand
from peerhub.core.protocol import CommandSuccess


def _submission(request_id: str) -> SubmissionMetadata:
    return SubmissionMetadata(
        client_request_id=request_id,
        correlation_id=f"correlation-{request_id}",
        client_id="client-1",
        actor_id="status-reader",
        scope={},
        idempotency_key=None,
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1_000,
    )


def test_native_status_reports_room_summary_and_unread_count(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id="room-status",
        topic_id="topic-status",
        title="Status",
        creator_id="peer-a",
        participants=("peer-a", "peer-b", "peer-c"),
    )
    runtime.rooms_service.update_room_summary(
        "room-status",
        mission="ship the core status view",
        blocked="waiting for review",
        phase="verification",
        actor_id="peer-a",
    )
    for recipient in ("peer-b", "peer-c"):
        runtime.rooms_service.send_message(
            room_id="room-status",
            sender_instance_id="peer-a",
            sender_profile_id="profile-a",
            recipient_instance_id=recipient,
            recipient_profile_id=f"profile-{recipient[-1]}",
            body=f"status note for {recipient}",
        )

    outcome = client.submit(
        StatusReadCommand(_submission("status-with-data"), "room-status")
    )

    assert isinstance(outcome, CommandSuccess)
    assert outcome.result == {
        "room_id": "room-status",
        "room_summary": {
            "mission": "ship the core status view",
            "blocked": "waiting for review",
            "phase": "verification",
        },
        "unread_count": 2,
    }


def test_native_status_defaults_cleanly_without_summary_or_mail(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id="room-status-empty",
        topic_id="topic-status-empty",
        title="Empty Status",
        creator_id="peer-a",
        participants=(),
    )

    outcome = client.submit(
        StatusReadCommand(_submission("status-empty"), "room-status-empty")
    )

    assert isinstance(outcome, CommandSuccess)
    assert outcome.result == {
        "room_id": "room-status-empty",
        "room_summary": None,
        "unread_count": 0,
    }
