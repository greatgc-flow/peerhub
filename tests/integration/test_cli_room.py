from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def test_cli_room_create_thread_append_and_clear_preserves_old(tmp_path: Path, capsys) -> None:
    def run(args: list[str]) -> dict:
        assert main(args + ["--json"]) == 0
        return json.loads(capsys.readouterr().out)

    room = run(["room", "create", "--workspace", str(tmp_path), "--room-id", "room-1", "--topic-id", "topic", "--title", "Room", "--creator", "cx", "--participants", "cx,ag"])
    thread = run(["room", "create-thread", "--workspace", str(tmp_path), "--thread-id", "thread-1", "--room-id", "room-1", "--subject", "Work", "--creator", "cx"])
    message = run(["room", "append-message", "--workspace", str(tmp_path), "--message-id", "msg-1", "--room-id", "room-1", "--thread-id", "thread-1", "--author", "cx", "--body", "Hello"])
    assert room["room_id"] == "room-1"
    assert thread["thread_id"] == "thread-1"
    assert message["sequence"] == 1

    old_before = run(["room", "status", "--workspace", str(tmp_path), "--room-id", "room-1"])
    new_room = run(["room", "clear", "--workspace", str(tmp_path), "--room-id", "room-1", "--new-room-id", "room-2", "--subject", "Fresh", "--actor", "cx"])
    old_after = run(["room", "status", "--workspace", str(tmp_path), "--room-id", "room-1"])
    assert new_room["room_id"] == "room-2"
    assert old_after == old_before


def test_cli_room_status_missing_returns_two(tmp_path: Path, capsys) -> None:
    assert main(["room", "status", "--workspace", str(tmp_path), "--room-id", "missing"]) == 2
    assert "not found" in capsys.readouterr().err
