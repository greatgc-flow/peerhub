"""CLI-level coverage for the R4/P4b domain migration of lesson actions
routed through ApplicationAPI.submit()
(via peerhub.client:Client) instead of calling LessonService /
LessonBroadcastCoordinator directly -- see
docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md.

Every lesson mutation is routed through the ApplicationAPI gateway."""

from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def _propose_lesson(
    tmp_path: Path,
    *,
    lesson_id: str = "lesson-1",
    expires_at: int | None = 4_102_444_800,
    json_output: bool = False,
) -> None:
    # activation requires either an advisory validity.expires_at or a
    # PASSED enforcement result (LessonService.activate) -- pass a
    # far-future expires_at so the migrated `activate`/`retire`/`broadcast`
    # gateway calls below exercise a real, activatable lesson.
    args = [
        "lesson", "propose",
        "--workspace", str(tmp_path),
        "--lesson-id", lesson_id,
        "--title", "Always verify",
        "--rule", "Never trust unverified claims",
        "--category", "process",
        "--severity", "HIGH",
        "--proposer", "cc",
        "--affected", "cc,cx",
    ]
    if expires_at is not None:
        args.extend(["--expires-at", str(expires_at)])
    if json_output:
        args.append("--json")
    assert main(args) == 0


def _propose_and_approve(tmp_path: Path) -> None:
    _propose_lesson(tmp_path)
    assert main([
        "lesson", "approve",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--approved-by", "cx",
    ]) == 0


def _propose_approve_and_activate(
    tmp_path: Path,
    *,
    lesson_id: str = "lesson-1",
    expires_at: int = 4_102_444_800,
) -> None:
    _propose_lesson(tmp_path, lesson_id=lesson_id, expires_at=expires_at)
    assert main([
        "lesson", "approve",
        "--workspace", str(tmp_path),
        "--lesson-id", lesson_id,
        "--approved-by", "cx",
    ]) == 0
    assert main([
        "lesson", "activate",
        "--workspace", str(tmp_path),
        "--lesson-id", lesson_id,
        "--actor", "cc",
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


def test_cli_lesson_propose_routes_through_application_api_gateway_with_and_without_expiry(
    tmp_path: Path, capsys
) -> None:
    _propose_lesson(
        tmp_path,
        lesson_id="lesson-expiring",
        expires_at=4_102_444_800,
        json_output=True,
    )
    expiring = json.loads(capsys.readouterr().out)
    assert expiring["validity"]["expires_at"] == 4_102_444_800

    _propose_lesson(
        tmp_path,
        lesson_id="lesson-permanent",
        expires_at=None,
        json_output=True,
    )
    permanent = json.loads(capsys.readouterr().out)
    assert permanent["validity"]["expires_at"] is None


def test_cli_lesson_propose_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch
) -> None:
    import peerhub.cli as cli_module

    original_request_context = cli_module.RequestContext

    def _mismatched_request_context(*, principal: str, client_id: str):
        del client_id
        return original_request_context(
            principal=principal, client_id="a-different-client"
        )

    monkeypatch.setattr(cli_module, "RequestContext", _mismatched_request_context)

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
    ]) == 2


def test_cli_lesson_approve_routes_through_application_api_gateway(
    tmp_path: Path, capsys
) -> None:
    _propose_lesson(tmp_path)
    capsys.readouterr()

    assert main([
        "lesson", "approve",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--approved-by", "cx",
        "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["lifecycle"] == "APPROVED"


def test_cli_lesson_approve_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch
) -> None:
    _propose_lesson(tmp_path)

    import peerhub.cli as cli_module

    original_request_context = cli_module.RequestContext

    def _mismatched_request_context(*, principal: str, client_id: str):
        del client_id
        return original_request_context(
            principal=principal, client_id="a-different-client"
        )

    monkeypatch.setattr(cli_module, "RequestContext", _mismatched_request_context)

    assert main([
        "lesson", "approve",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--approved-by", "cx",
    ]) == 2


def test_cli_lesson_supersede_routes_through_application_api_gateway(
    tmp_path: Path, capsys
) -> None:
    _propose_approve_and_activate(tmp_path)
    capsys.readouterr()

    assert main([
        "lesson", "supersede",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--actor", "cc",
        "--replacement-lesson-id", "lesson-2",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lifecycle"] == "SUPERSEDED"
    assert payload["validity"]["superseded_by"] == "lesson-2"


def test_cli_lesson_supersede_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch
) -> None:
    _propose_approve_and_activate(tmp_path)

    import peerhub.cli as cli_module

    original_request_context = cli_module.RequestContext

    def _mismatched_request_context(*, principal: str, client_id: str):
        del client_id
        return original_request_context(
            principal=principal, client_id="a-different-client"
        )

    monkeypatch.setattr(cli_module, "RequestContext", _mismatched_request_context)

    assert main([
        "lesson", "supersede",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--actor", "cc",
        "--replacement-lesson-id", "lesson-2",
    ]) == 2


def test_cli_lesson_quarantine_routes_through_application_api_gateway(
    tmp_path: Path, capsys
) -> None:
    _propose_lesson(tmp_path)
    capsys.readouterr()

    assert main([
        "lesson", "quarantine",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--actor", "cc",
        "--reason", "incorrect guidance",
        "--evidence", "test evidence",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lifecycle"] == "QUARANTINED"
    assert payload["quarantine"]["actor_id"] == "cc"


def test_cli_lesson_quarantine_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch
) -> None:
    _propose_lesson(tmp_path)

    import peerhub.cli as cli_module

    original_request_context = cli_module.RequestContext

    def _mismatched_request_context(*, principal: str, client_id: str):
        del client_id
        return original_request_context(
            principal=principal, client_id="a-different-client"
        )

    monkeypatch.setattr(cli_module, "RequestContext", _mismatched_request_context)

    assert main([
        "lesson", "quarantine",
        "--workspace", str(tmp_path),
        "--lesson-id", "lesson-1",
        "--actor", "cc",
        "--reason", "incorrect guidance",
        "--evidence", "test evidence",
    ]) == 2


def test_cli_lesson_sweep_routes_through_application_api_gateway(
    tmp_path: Path, capsys
) -> None:
    _propose_approve_and_activate(
        tmp_path, lesson_id="lesson-expired", expires_at=0
    )
    capsys.readouterr()

    assert main([
        "lesson", "sweep",
        "--workspace", str(tmp_path),
        "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "retired": ["lesson:lesson-expired"]
    }


def test_cli_lesson_sweep_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch
) -> None:
    import peerhub.cli as cli_module

    original_request_context = cli_module.RequestContext

    def _mismatched_request_context(*, principal: str, client_id: str):
        del client_id
        return original_request_context(
            principal=principal, client_id="a-different-client"
        )

    monkeypatch.setattr(cli_module, "RequestContext", _mismatched_request_context)

    assert main([
        "lesson", "sweep",
        "--workspace", str(tmp_path),
    ]) == 2
