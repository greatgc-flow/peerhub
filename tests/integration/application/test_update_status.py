"""SQLite-backed coverage for legacy ``update-status`` wiring."""

import pytest

from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import (
    LegacyActionCall,
    LegacyTranslator,
    TranslatedCommand,
    UpdateStatusCommand,
)
from peerhub.core.protocol import CommandSuccess


def _submission(
    *,
    scope: dict[str, object] | None = None,
    idempotency_key: str = "update-status-idempotency-key",
) -> SubmissionMetadata:
    return SubmissionMetadata(
        client_request_id="update-status-request",
        correlation_id="update-status-correlation",
        client_id="client-1",
        actor_id="peer-1",
        scope={} if scope is None else scope,
        idempotency_key=idempotency_key,
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1_000,
    )


@pytest.mark.parametrize(
    ("arguments", "scope", "expected_room_id"),
    (
        ({"room_id": "room-explicit", "mission": "explicit"}, {}, "room-explicit"),
        (
            {"context": {"current_room": "room-context"}, "phase": "scouting"},
            {},
            "room-context",
        ),
        ({"blocked": "waiting"}, {"room": "room-scope"}, "room-scope"),
    ),
)
def test_legacy_update_status_resolves_room_and_executes(
    runtime_setup,
    arguments,
    scope,
    expected_room_id,
) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id=expected_room_id,
        topic_id=f"topic-{expected_room_id}",
        title=expected_room_id,
        creator_id="peer-1",
        participants=(),
    )

    outcome = LegacyTranslator().translate(
        LegacyActionCall(action="update-status", arguments=arguments),
        _submission(scope=scope),
    )

    assert isinstance(outcome, TranslatedCommand)
    assert isinstance(outcome.command, UpdateStatusCommand)
    assert outcome.command.room_id == expected_room_id
    submitted = client.submit(outcome.command)
    assert isinstance(submitted, CommandSuccess)
    summary = runtime.rooms_service.get_room_summary(expected_room_id)
    assert summary is not None
    assert summary.state["room_id"] == expected_room_id


def test_legacy_update_status_round_trip_preserves_omitted_fields(runtime_setup) -> None:
    runtime, client, _ = runtime_setup
    runtime.rooms_service.create_room(
        room_id="room-round-trip",
        topic_id="topic-round-trip",
        title="Round trip",
        creator_id="peer-1",
        participants=(),
    )
    translator = LegacyTranslator()

    initial = translator.translate(
        LegacyActionCall(
            action="update-status",
            arguments={
                "room_id": "room-round-trip",
                "mission": "ship update-status",
                "blocked": "review pending",
                "phase": "implementation",
            },
        ),
        _submission(idempotency_key="update-status-idempotency-key-initial"),
    )
    assert isinstance(initial, TranslatedCommand)
    assert isinstance(client.submit(initial.command), CommandSuccess)

    partial = translator.translate(
        LegacyActionCall(
            action="update-status",
            arguments={"room_id": "room-round-trip", "phase": "verification"},
        ),
        _submission(idempotency_key="update-status-idempotency-key-partial"),
    )
    assert isinstance(partial, TranslatedCommand)
    assert isinstance(client.submit(partial.command), CommandSuccess)

    summary = runtime.rooms_service.get_room_summary("room-round-trip")
    assert summary is not None
    assert summary.revision == 2
    assert summary.state["mission"] == "ship update-status"
    assert summary.state["blocked"] == "review pending"
    assert summary.state["phase"] == "verification"
