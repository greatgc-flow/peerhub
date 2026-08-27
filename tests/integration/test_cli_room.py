from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import SystemClock, UuidSource, main
from peerhub.core.context import PathLayout, RuntimeContext
from peerhub.runtime import create_runtime


def test_cli_room_create_thread_append_and_clear_preserves_old(tmp_path: Path, capsys) -> None:
    def run(args: list[str]) -> dict:
        assert main(args + ["--json"]) == 0
        return json.loads(capsys.readouterr().out)

    room = run(["room", "create", "--workspace", str(tmp_path), "--room-id", "room-1", "--topic-id", "topic", "--title", "Room", "--creator", "cx", "--participants", "cx,ag"])
    thread = run(["room", "create-thread", "--workspace", str(tmp_path), "--thread-id", "thread-1", "--room-id", "room-1", "--subject", "Work", "--creator", "cx"])
    message = run(["room", "append-message", "--workspace", str(tmp_path), "--message-id", "msg-1", "--room-id", "room-1", "--thread-id", "thread-1", "--author", "cx", "--body", "Hello"])
    reaction = run(["room", "react", "--workspace", str(tmp_path), "--message-id", "msg-1", "--room-id", "room-1", "--actor-instance-id", "cx-terminal", "--actor-profile-id", "cx", "--reaction-type", "ACK"])
    assert room["room_id"] == "room-1"
    assert thread["thread_id"] == "thread-1"
    assert message["sequence"] == 1
    assert reaction["status"] == "ACTIVE"

    old_before = run(["room", "status", "--workspace", str(tmp_path), "--room-id", "room-1"])
    new_room = run(["room", "clear", "--workspace", str(tmp_path), "--room-id", "room-1", "--new-room-id", "room-2", "--subject", "Fresh", "--actor", "cx"])
    old_after = run(["room", "status", "--workspace", str(tmp_path), "--room-id", "room-1"])
    assert new_room["room_id"] == "room-2"
    assert old_after == old_before


def test_cli_room_status_missing_returns_two(tmp_path: Path, capsys) -> None:
    assert main(["room", "status", "--workspace", str(tmp_path), "--room-id", "missing"]) == 2
    assert "not found" in capsys.readouterr().err


def test_cli_room_react_unreact_react_round_trip(
    tmp_path: Path, capsys
) -> None:
    def run(args: list[str]) -> dict:
        assert main(args + ["--json"]) == 0
        return json.loads(capsys.readouterr().out)

    workspace = ["--workspace", str(tmp_path)]
    run([
        "room", "create", *workspace,
        "--room-id", "room-reactions",
        "--topic-id", "topic-reactions",
        "--title", "Reaction Room",
        "--creator", "cx",
        "--participants", "cx",
    ])
    run([
        "room", "create-thread", *workspace,
        "--thread-id", "thread-reactions",
        "--room-id", "room-reactions",
        "--subject", "Reaction Round Trip",
        "--creator", "cx",
    ])
    run([
        "room", "append-message", *workspace,
        "--message-id", "message-reactions",
        "--room-id", "room-reactions",
        "--thread-id", "thread-reactions",
        "--author", "cx",
        "--body", "Toggle ACK",
    ])
    reaction_args = [
        *workspace,
        "--message-id", "message-reactions",
        "--room-id", "room-reactions",
        "--actor-instance-id", "cx-terminal",
        "--actor-profile-id", "cx",
        "--reaction-type", "ACK",
    ]

    added = run(["room", "react", *reaction_args])
    removed = run(["room", "unreact", *reaction_args])
    readded = run(["room", "react", *reaction_args])

    assert added["status"] == "ACTIVE"
    assert removed["status"] == "REMOVED"
    assert readded["status"] == "ACTIVE"

    context = RuntimeContext(
        workspace_home_id=tmp_path.name,
        paths=PathLayout.for_workspace(tmp_path),
        clock=SystemClock(),
        ids=UuidSource(),
    )
    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        projection = runtime.rooms_service.get_reaction_state(
            "message-reactions",
            "cx-terminal",
            "cx",
            "ACK",
        )
        events = runtime.governance_broker.list_targets(
            "reaction-event", "room-reactions"
        )

    assert projection is not None
    assert projection.state["status"] == "ACTIVE"
    assert projection.state["latest_action"] == "ADD"
    assert projection.revision == 3
    assert len(events) == 3
    assert len({event.target_id for event in events}) == 3
    assert sorted(event.state["action"] for event in events) == [
        "ADD",
        "ADD",
        "REMOVE",
    ]
