"""CLI-level coverage for the R4/P4b domain migration of `lesson
activate`/`retire`/`broadcast`: routed through ApplicationAPI.submit()
(via peerhub.client:Client) instead of calling LessonService /
LessonBroadcastCoordinator directly -- see
docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md.

`lesson propose` (registered command is missing expires_at),
`approve`/`supersede`/`quarantine`/`sweep` (no registered command) remain
direct calls."""

from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def _propose_and_approve(tmp_path: Path) -> None:
    # activation requires either an advisory validity.expires_at or a
    # PASSED enforcement result (LessonService.activate) -- pass a
    # far-future expires_at so the migrated `activate`/`retire`/`broadcast`
    # gateway calls below exercise a real, activatable lesson.
    assert main([
        "lesson", "propose",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--title", "Always verify",
        "--rule", "Never trust unverified claims",
        "--category", "process",
        "--severity", "HIGH",
        "--proposer", "cc",
        "--affected", "cc,cx",
        "--expires-at", "4102444800",
    ]) == 0
    assert main([
        "lesson", "approve",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--approved-by", "cx",
    ]) == 0


def test_cli_lesson_activate_and_retire_route_through_application_api_gateway(
    tmp_path: Path, capsys
) -> None:
    _propose_and_approve(tmp_path)
    capsys.readouterr()

    exit_code = main([
        "lesson", "activate",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--actor", "cc",
        "--json",
    ])
    assert exit_code == 0
    activate_payload = json.loads(capsys.readouterr().out)
    assert activate_payload["lifecycle"] == "ACTIVE"

    exit_code = main([
        "lesson", "retire",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--actor", "cc",
        "--reason", "superseded by newer guidance",
        "--json",
    ])
    assert exit_code == 0
    retire_payload = json.loads(capsys.readouterr().out)
    assert retire_payload["lifecycle"] == "RETIRED"


def test_cli_lesson_broadcast_routes_through_application_api_gateway(
    tmp_path: Path, capsys
) -> None:
    assert main([
        "room", "create",
        "--workspace", str(tmp_path),
        "--room-id", "room-1",
        "--topic-id", "topic-1",
        "--title", "General",
        "--creator", "cc",
        "--participants", "cc,cx",
    ]) == 0
    _propose_and_approve(tmp_path)
    assert main([
        "lesson", "activate",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--actor", "cc",
    ]) == 0
    capsys.readouterr()

    exit_code = main([
        "lesson", "broadcast",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--room-id", "room-1",
        "--sender-instance-id", "inst-cc",
        "--sender-profile-id", "cc",
        "--json",
    ])
    assert exit_code == 0
    broadcast_payload = json.loads(capsys.readouterr().out)
    assert broadcast_payload["lesson_id"] == "lesson-1"
    assert broadcast_payload["room_id"] == "room-1"


def test_cli_lesson_activate_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch
) -> None:
    _propose_and_approve(tmp_path)

    import peerhub.cli as cli_module

    original_request_context = cli_module.RequestContext

    def _mismatched_request_context(*, principal: str, client_id: str):
        del client_id
        return original_request_context(
            principal=principal, client_id="a-different-client"
        )

    monkeypatch.setattr(cli_module, "RequestContext", _mismatched_request_context)

    exit_code = main([
        "lesson", "activate",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--actor", "cc",
    ])
    assert exit_code == 2
