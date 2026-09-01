"""SQLite-backed legacy compatibility coverage for ``thread-new``."""

from __future__ import annotations

import json
from pathlib import Path

from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import (
    InvalidLegacyArguments,
    LegacyActionCall,
    LegacyTranslator,
    ThreadNewCommand,
    TranslatedCommand,
)
from peerhub.cli import main
from peerhub.core.protocol import CommandSuccess


def _submission(*, scope: dict[str, str] | None = None) -> SubmissionMetadata:
    return SubmissionMetadata(
        client_request_id="thread-new-request",
        correlation_id="thread-new-correlation",
        client_id="client-1",
        actor_id="peer-1",
        scope={} if scope is None else scope,
        idempotency_key="thread-new-idempotency",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )


def _create_room(runtime, room_id: str = "room-thread-new") -> None:
    runtime.rooms_service.create_room(
        room_id=room_id,
        topic_id="thread-new-topic",
        title="Thread New Room",
        creator_id="peer-1",
        participants=(),
    )


def test_thread_new_sqlite_round_trip_and_duplicate_is_legacy_noop(
    runtime_setup,
) -> None:
    runtime, client, _ = runtime_setup
    _create_room(runtime)
    call = LegacyActionCall(
        "thread-new",
        {"topic": "Architecture Design!", "from": "cx", "msg": "Opening"},
    )
    translated = LegacyTranslator().translate(call, _submission(scope={"room": "room-thread-new"}))

    assert isinstance(translated, TranslatedCommand)
    first = client.submit(translated.command)
    assert isinstance(first, CommandSuccess)
    assert first.result["thread_id"] == "architecture-design-"
    assert first.result["created"] is True
    target_before = runtime.rooms_service.get_target("architecture-design-")
    assert target_before is not None
    assert target_before.state["room_id"] == "room-thread-new"
    assert target_before.state["subject"] == "Architecture Design!"

    second_translated = LegacyTranslator().translate(
        call,
        _submission(scope={"room": "room-thread-new"}),
    )
    assert isinstance(second_translated, TranslatedCommand)
    second = client.submit(second_translated.command)
    assert isinstance(second, CommandSuccess)
    assert second.result == {
        "thread_id": "architecture-design-",
        "created": False,
        "message": (
            "Thread 'architecture-design-' already exists. "
            "Use thread-append to add messages."
        ),
        "receipt": None,
    }
    target_after = runtime.rooms_service.get_target("architecture-design-")
    assert target_after is not None
    assert (target_after.revision, target_after.state) == (
        target_before.revision,
        target_before.state,
    )


def test_thread_new_legacy_translation_uses_slug_raw_subject_and_scope_room() -> None:
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            "thread-new",
            {"topic": "MiXeD + Topic/Name", "peer": "ag", "msg": "ignored"},
        ),
        _submission(scope={"room_id": "room-from-scope"}),
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, ThreadNewCommand)
    assert translated.command.thread_id == "mixed---topic-name"
    assert translated.command.subject == "MiXeD + Topic/Name"
    assert translated.command.room_id == "room-from-scope"
    assert translated.command.creator_id == "ag"
    assert translated.command.method == "coordination.thread.create"


def test_thread_new_requires_topic() -> None:
    translated = LegacyTranslator().translate(
        LegacyActionCall("thread-new", {"from": "cx"}),
        _submission(scope={"room": "room-thread-new"}),
    )

    assert translated == InvalidLegacyArguments(
        action="thread-new",
        reason="thread-new requires --topic",
    )


def test_thread_new_requires_room_id() -> None:
    translated = LegacyTranslator().translate(
        LegacyActionCall("thread-new", {"topic": "Some Topic"}),
        _submission(),
    )

    assert translated == InvalidLegacyArguments(
        action="thread-new",
        reason="room_id is required in arguments, context, or scope",
    )


def test_cli_room_thread_new_uses_legacy_slug_and_duplicate_envelope(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = ["--workspace", str(tmp_path)]
    assert main([
        "room", "create", *workspace,
        "--room-id", "room-cli-thread-new",
        "--topic-id", "room-topic",
        "--title", "CLI Thread New",
        "--creator", "cx",
        "--participants", "cx",
        "--json",
    ]) == 0
    capsys.readouterr()

    args = [
        "room", "thread-new", *workspace,
        "--room-id", "room-cli-thread-new",
        "--topic", "CLI Topic!",
        "--creator", "cx",
        "--json",
    ]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == {
        "thread_id": "cli-topic-",
        "created": True,
        "message": None,
    }
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == {
        "thread_id": "cli-topic-",
        "created": False,
        "message": (
            "Thread 'cli-topic-' already exists. "
            "Use thread-append to add messages."
        ),
    }


def test_cli_room_thread_new_defaults_omitted_creator_to_legacy_cc(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = ["--workspace", str(tmp_path)]
    assert main([
        "room", "create", *workspace,
        "--room-id", "room-cli-thread-new-default",
        "--topic-id", "room-topic",
        "--title", "CLI Thread New Default Creator",
        "--creator", "cx",
        "--participants", "cx",
        "--json",
    ]) == 0
    capsys.readouterr()

    # --creator omitted entirely: must fall back to legacy's "cc" default,
    # not pass a bare None through to create_thread_new().
    assert main([
        "room", "thread-new", *workspace,
        "--room-id", "room-cli-thread-new-default",
        "--topic", "No Creator Given",
        "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "thread_id": "no-creator-given",
        "created": True,
        "message": None,
    }
