"""CLI-level coverage for the R4/P4b domain migration of the room
domain's mutating actions: routed through ApplicationAPI.submit() (via
peerhub.client:Client) instead of calling RoomsService directly -- see
docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md.

`room create` and `room rebuild-session-bindings` remain direct calls
(no registered ApplicationAPI command for them yet)."""

from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def _create_room(tmp_path: Path) -> None:
    assert main([
        "room", "create",
        "--workspace", str(tmp_path),
        "--room-id", "room-1",
        "--topic-id", "topic-1",
        "--title", "General",
        "--creator", "cc",
        "--participants", "cc,cx",
    ]) == 0


def test_cli_room_mutating_actions_route_through_application_api_gateway(
    tmp_path: Path, capsys
) -> None:
    _create_room(tmp_path)
    capsys.readouterr()

    assert main([
        "room", "create-thread",
        "--workspace", str(tmp_path),
        "--thread-id", "thread-1",
        "--room-id", "room-1",
        "--subject", "kickoff",
        "--creator", "cc",
        "--json",
    ]) == 0
    thread_payload = json.loads(capsys.readouterr().out)
    assert thread_payload["thread_id"] == "thread-1"

    assert main([
        "room", "append-message",
        "--workspace", str(tmp_path),
        "--message-id", "msg-1",
        "--room-id", "room-1",
        "--thread-id", "thread-1",
        "--author", "cc",
        "--body", "hello",
        "--json",
    ]) == 0
    append_payload = json.loads(capsys.readouterr().out)
    assert append_payload["body"] == "hello"

    assert main([
        "room", "react",
        "--workspace", str(tmp_path),
        "--message-id", "msg-1",
        "--room-id", "room-1",
        "--actor-instance-id", "inst-1",
        "--actor-profile-id", "cc",
        "--reaction-type", "ack",
        "--json",
    ]) == 0
    capsys.readouterr()

    assert main([
        "room", "unreact",
        "--workspace", str(tmp_path),
        "--message-id", "msg-1",
        "--room-id", "room-1",
        "--actor-instance-id", "inst-1",
        "--actor-profile-id", "cc",
        "--reaction-type", "ack",
        "--json",
    ]) == 0
    capsys.readouterr()

    assert main([
        "room", "update-status",
        "--workspace", str(tmp_path),
        "--room-id", "room-1",
        "--mission", "ship it",
        "--actor", "cc",
        "--json",
    ]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["mission"] == "ship it"
    capsys.readouterr()

    assert main([
        "room", "checkpoint",
        "--workspace", str(tmp_path),
        "--room-id", "room-1",
        "--actor", "cc",
        "--export", "json",
    ]) == 0
    checkpoint_payload = json.loads(capsys.readouterr().out)
    assert "markdown" in checkpoint_payload
    capsys.readouterr()

    assert main([
        "room", "send",
        "--workspace", str(tmp_path),
        "--room-id", "room-1",
        "--sender-instance-id", "inst-cc",
        "--sender-profile-id", "cc",
        "--recipient-instance-id", "inst-cx",
        "--recipient-profile-id", "cx",
        "--body", "ping",
        "--json",
    ]) == 0
    send_payload = json.loads(capsys.readouterr().out)
    assert send_payload["body"] == "ping"

    assert main([
        "room", "mark-read",
        "--workspace", str(tmp_path),
        "--room-id", "room-1",
        "--recipient-instance-id", "inst-cx",
        "--recipient-profile-id", "cx",
        "--up-through-sequence", str(send_payload["sequence"]),
        "--json",
    ]) == 0
    mark_read_payload = json.loads(capsys.readouterr().out)
    assert mark_read_payload["read_through_sequence"] == send_payload["sequence"]

    assert main([
        "room", "promote-message",
        "--workspace", str(tmp_path),
        "--message-id", send_payload["message_id"],
        "--room-id", "room-1",
        "--thread-id", "thread-1",
        "--actor", "cc",
        "--json",
    ]) == 0
    capsys.readouterr()

    assert main([
        "room", "append-handoff",
        "--workspace", str(tmp_path),
        "--room-id", "room-1",
        "--section", "PENDING_ISSUES",
        "--text", "note from gateway test",
        "--actor", "cc",
        "--json",
    ]) == 0
    capsys.readouterr()

    assert main([
        "room", "thread-new",
        "--workspace", str(tmp_path),
        "--room-id", "room-1",
        "--topic", "legacy topic",
        "--creator", "cc",
        "--json",
    ]) == 0
    thread_new_payload = json.loads(capsys.readouterr().out)
    assert thread_new_payload["created"] is True

    assert main([
        "room", "broadcast",
        "--workspace", str(tmp_path),
        "--room-id", "room-1",
        "--from", "cc",
        "--msg", "status update",
        "--targets", "cx",
        "--json",
    ]) == 0
    broadcast_payload = json.loads(capsys.readouterr().out)
    assert broadcast_payload["room_id"] == "room-1"
    capsys.readouterr()

    assert main([
        "room", "clear",
        "--workspace", str(tmp_path),
        "--room-id", "room-1",
        "--new-room-id", "room-2",
        "--subject", "fresh start",
        "--actor", "cc",
        "--json",
    ]) == 0


def test_cli_room_create_thread_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch
) -> None:
    _create_room(tmp_path)

    import peerhub.cli as cli_module

    original_request_context = cli_module.RequestContext

    def _mismatched_request_context(*, principal: str, client_id: str):
        del client_id
        return original_request_context(
            principal=principal, client_id="a-different-client"
        )

    monkeypatch.setattr(cli_module, "RequestContext", _mismatched_request_context)

    exit_code = main([
        "room", "create-thread",
        "--workspace", str(tmp_path),
        "--thread-id", "thread-1",
        "--room-id", "room-1",
        "--subject", "kickoff",
        "--creator", "cc",
    ])
    assert exit_code == 2
