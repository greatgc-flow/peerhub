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


def test_cli_room_status_includes_summary_and_room_wide_unread_count(
    tmp_path: Path,
    capsys,
) -> None:
    def run(args: list[str]) -> dict:
        assert main(args + ["--json"]) == 0
        return json.loads(capsys.readouterr().out)

    workspace = ["--workspace", str(tmp_path)]
    run([
        "room", "create", *workspace,
        "--room-id", "room-unread-status",
        "--topic-id", "topic-unread-status",
        "--title", "Unread status",
        "--creator", "peer-a",
        "--participants", "peer-a,peer-b,peer-c",
    ])
    for recipient in ("peer-b", "peer-c"):
        run([
            "room", "send", *workspace,
            "--room-id", "room-unread-status",
            "--sender-instance-id", "peer-a",
            "--sender-profile-id", "profile-a",
            "--recipient-instance-id", recipient,
            "--recipient-profile-id", f"profile-{recipient[-1]}",
            "--body", f"for {recipient}",
        ])

    context = RuntimeContext(
        workspace_home_id=tmp_path.name,
        paths=PathLayout.for_workspace(tmp_path),
        clock=SystemClock(),
        ids=UuidSource(),
    )
    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        runtime.rooms_service.update_room_summary(
            "room-unread-status",
            mission="verify status",
            blocked=None,
            phase="testing",
            actor_id="peer-a",
        )

    status = run([
        "room", "status", *workspace,
        "--room-id", "room-unread-status",
    ])
    assert status["room_summary"] == {
        "mission": "verify status",
        "blocked": None,
        "phase": "testing",
    }
    assert status["unread_count"] == 2
    run([
        "room", "mark-read", *workspace,
        "--room-id", "room-unread-status",
        "--recipient-instance-id", "peer-b",
        "--recipient-profile-id", "profile-b",
        "--up-through-sequence", "1",
    ])
    assert run([
        "room", "status", *workspace,
        "--room-id", "room-unread-status",
    ])["unread_count"] == 1


def test_cli_room_update_status_preserves_omitted_fields(
    tmp_path: Path, capsys
) -> None:
    def run(args: list[str]) -> dict:
        assert main(args + ["--json"]) == 0
        return json.loads(capsys.readouterr().out)

    workspace = ["--workspace", str(tmp_path)]
    run([
        "room", "create", *workspace,
        "--room-id", "room-update-status",
        "--topic-id", "topic-update-status",
        "--title", "Update status",
        "--creator", "peer-a",
        "--participants", "peer-a",
    ])
    initial = run([
        "room", "update-status", *workspace,
        "--room-id", "room-update-status",
        "--mission", "ship update-status",
        "--blocked", "review pending",
        "--phase", "implementation",
    ])
    assert initial["mission"] == "ship update-status"
    updated = run([
        "room", "update-status", *workspace,
        "--room-id", "room-update-status",
        "--phase", "verification",
    ])
    assert updated["mission"] == "ship update-status"
    assert updated["blocked"] == "review pending"
    assert updated["phase"] == "verification"


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


def test_cli_room_append_handoff_and_checkpoint_exports(
    tmp_path: Path, capsys
) -> None:
    def run_json(args: list[str]) -> dict:
        assert main(args + ["--json"]) == 0
        return json.loads(capsys.readouterr().out)

    workspace = ["--workspace", str(tmp_path)]
    run_json([
        "room", "create", *workspace,
        "--room-id", "room-handoff",
        "--topic-id", "topic-handoff",
        "--title", "Handoff Room",
        "--creator", "cx",
        "--participants", "cx",
    ])

    context = RuntimeContext(
        workspace_home_id=tmp_path.name,
        paths=PathLayout.for_workspace(tmp_path),
        clock=SystemClock(),
        ids=UuidSource(),
    )
    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        runtime.rooms_service.set_room_goal(
            room_id="room-handoff",
            goal="Finish checkpoint support",
            actor_id="cx",
        )

    for index in range(1, 7):
        note = run_json([
            "room", "append-handoff", *workspace,
            "--room-id", "room-handoff",
            "--section", "RECENT_COMPLETED",
            "--text", f"cli-completed-{index}",
            "--actor", "cx",
        ])
        assert note["kind"] == "continuity-note"

    checkpoint = run_json([
        "room", "checkpoint", *workspace,
        "--room-id", "room-handoff",
        "--actor", "cx",
    ])
    assert checkpoint["sections"]["GOAL"]["value"] == (
        "Finish checkpoint support"
    )
    assert checkpoint["sections"]["RECENT_COMPLETED"]["items"] == [
        "cli-completed-2",
        "cli-completed-3",
        "cli-completed-4",
        "cli-completed-5",
        "cli-completed-6",
    ]

    assert main([
        "room", "checkpoint", *workspace,
        "--room-id", "room-handoff",
        "--actor", "cx",
        "--export", "markdown",
    ]) == 0
    markdown = capsys.readouterr().out
    assert "## GOAL" in markdown
    assert "## RECENT_COMPLETED" in markdown
    assert "- cli-completed-6" in markdown


def test_cli_room_context_fill_emits_filtered_json_without_writes(
    tmp_path: Path, capsys
) -> None:
    workspace = ["--workspace", str(tmp_path)]
    assert main([
        "room", "create", *workspace,
        "--room-id", "room-context",
        "--topic-id", "topic-context",
        "--title", "Context Room",
        "--creator", "cx",
        "--participants", "cx",
        "--json",
    ]) == 0
    capsys.readouterr()

    context = RuntimeContext(
        workspace_home_id=tmp_path.name,
        paths=PathLayout.for_workspace(tmp_path),
        clock=SystemClock(),
        ids=UuidSource(),
    )
    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        runtime.rooms_service.set_room_goal(
            room_id="room-context",
            goal="Provide startup context",
            actor_id="cx",
        )
        runtime.rooms_service.append_handoff_note(
            room_id="room-context",
            section="ACTIVE_THREADS",
            text="thread-context",
            actor_id="cx",
        )

    assert main([
        "room", "context-fill", *workspace,
        "--room-id", "room-context",
        "--session-id", "session-metadata",
        "--sections", "GOAL,ACTIVE_THREADS",
    ]) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["session_id"] == "session-metadata"
    assert list(envelope["sections"]) == ["GOAL", "ACTIVE_THREADS"]
    assert envelope["sections"]["GOAL"]["value"] == (
        "Provide startup context"
    )
    assert envelope["sections"]["ACTIVE_THREADS"]["items"] == [
        "thread-context"
    ]

    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        assert runtime.governance_broker.list_targets(
            "checkpoint-created", "room-context"
        ) == ()
