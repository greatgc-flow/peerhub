"""SQLite-backed legacy compatibility coverage for ``thread-new``."""

from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


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
