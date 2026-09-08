"""SQLite-backed legacy compatibility coverage for ``thread-new``."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pytest

from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import (
    ThreadNewCommand,
    legacy_thread_slug,
)
from peerhub.cli import main
from peerhub.core.protocol import CommandSuccess


@dataclass(frozen=True, slots=True)
class _CommandOutcome:
    """Internal test wrapper to preserve outcome.command access."""

    command: Any


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
    first_cmd = ThreadNewCommand(
        submission=_submission(scope={"room": "room-thread-new"}),
        thread_id="architecture-design-",
        room_id="room-thread-new",
        subject="Architecture Design!",
        creator_id="cx",
    )
    translated = _CommandOutcome(first_cmd)

    first = client.submit(translated.command)
    assert isinstance(first, CommandSuccess)
    assert first.result["thread_id"] == "architecture-design-"
    assert first.result["created"] is True
    target_before = runtime.rooms_service.get_target("architecture-design-")
    assert target_before is not None
    assert target_before.state["room_id"] == "room-thread-new"
    assert target_before.state["subject"] == "Architecture Design!"

    second_cmd = ThreadNewCommand(
        submission=_submission(scope={"room": "room-thread-new"}),
        thread_id="architecture-design-",
        room_id="room-thread-new",
        subject="Architecture Design!",
        creator_id="cx",
    )
    second_translated = _CommandOutcome(second_cmd)
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
    cmd = ThreadNewCommand(
        submission=_submission(scope={"room_id": "room-from-scope"}),
        thread_id=legacy_thread_slug("MiXeD + Topic/Name"),
        room_id="room-from-scope",
        subject="MiXeD + Topic/Name",
        creator_id="ag",
    )

    assert isinstance(cmd, ThreadNewCommand)
    assert cmd.thread_id == "mixed---topic-name"
    assert cmd.subject == "MiXeD + Topic/Name"
    assert cmd.room_id == "room-from-scope"
    assert cmd.creator_id == "ag"
    assert cmd.method == "coordination.thread.create"


def test_thread_new_requires_topic() -> None:
    # Native contract: ThreadNewCommand enforces subject (topic) as a required typed parameter at construction
    with pytest.raises(TypeError):
        ThreadNewCommand(  # type: ignore[call-arg]
            submission=_submission(scope={"room": "room-thread-new"}),
            thread_id="test-thread",
            room_id="room-thread-new",
            creator_id="cx",
        )


def test_thread_new_requires_room_id() -> None:
    # Native contract: ThreadNewCommand enforces room_id as a required typed parameter at construction
    with pytest.raises(TypeError):
        ThreadNewCommand(  # type: ignore[call-arg]
            submission=_submission(),
            thread_id="test-thread",
            subject="Some Topic",
            creator_id="cx",
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
