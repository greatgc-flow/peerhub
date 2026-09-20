"""CLI-level coverage for task commands routed through ApplicationAPI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from peerhub.cli import main


def _create_and_start(tmp_path: Path) -> None:
    assert main([
        "task", "create",
        "--workspace", str(tmp_path),
        "--task-id", "task-1",
        "--summary", "do the thing",
        "--spec", "full spec text",
        "--creator", "cc",
    ]) == 0
    assert main([
        "task", "claim-start",
        "--workspace", str(tmp_path),
        "--task-id", "task-1",
        "--actor", "cx",
        "--request-id", "req-1",
        "--coordinator", "cc",
        "--attempt-id", "attempt-1",
    ]) == 0


def _mismatched_request_context(monkeypatch: pytest.MonkeyPatch) -> None:
    import peerhub.cli as cli_module

    original_request_context = cli_module.RequestContext

    def mismatched_request_context(*, principal: str, client_id: str):
        del client_id
        return original_request_context(
            principal=principal, client_id="a-different-client"
        )

    monkeypatch.setattr(
        cli_module, "RequestContext", mismatched_request_context
    )


def _prepare_action(tmp_path: Path, action: str) -> None:
    if action == "claim-start":
        assert main([
            "task", "create",
            "--workspace", str(tmp_path),
            "--task-id", "task-1",
            "--summary", "do the thing",
            "--spec", "full spec text",
            "--creator", "cc",
        ]) == 0
    elif action in {"complete", "fail", "cancel"}:
        _create_and_start(tmp_path)


def _action_args(tmp_path: Path, action: str) -> list[str]:
    shared = [
        "task", action,
        "--workspace", str(tmp_path),
        "--task-id", "task-1",
    ]
    action_args = {
        "create": [
            "--summary", "do the thing",
            "--spec", "full spec text",
            "--creator", "cc",
        ],
        "claim-start": [
            "--actor", "cx",
            "--request-id", "req-1",
            "--coordinator", "cc",
            "--attempt-id", "attempt-1",
        ],
        "complete": ["--actor", "cx"],
        "fail": [
            "--actor", "cx",
            "--failure-class", "validation_error",
            "--reason", "the validation failed",
        ],
        "cancel": [
            "--actor", "cx",
            "--reason", "no longer needed",
        ],
    }
    return [*shared, *action_args[action], "--json"]


def test_cli_task_checkpoint_routes_through_application_api_gateway(
    tmp_path: Path, capsys
) -> None:
    _create_and_start(tmp_path)
    capsys.readouterr()

    exit_code = main([
        "task", "checkpoint",
        "--workspace", str(tmp_path),
        "--task-id", "task-1",
        "--actor", "cx",
        "--checkpoint-id", "chk-1",
        "--stage", "halfway",
        "--request-id", "req-1",
        "--attempt-id", "attempt-1",
        "--completed", "unit-1",
        "--remaining", "unit-2",
        "--json",
    ])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "CHECKPOINTED"


def test_cli_task_checkpoint_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch
) -> None:
    _create_and_start(tmp_path)
    _mismatched_request_context(monkeypatch)

    exit_code = main([
        "task", "checkpoint",
        "--workspace", str(tmp_path),
        "--task-id", "task-1",
        "--actor", "cx",
        "--checkpoint-id", "chk-1",
        "--stage", "halfway",
        "--request-id", "req-1",
        "--attempt-id", "attempt-1",
    ])
    assert exit_code == 2


@pytest.mark.parametrize(("action", "expected_state"), [
    ("create", "CREATED"),
    ("claim-start", "RUNNING"),
    ("complete", "SUCCEEDED"),
    ("fail", "FAILED"),
    ("cancel", "CANCELLED"),
])
def test_cli_task_actions_route_through_application_api_gateway(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], action: str,
    expected_state: str,
) -> None:
    _prepare_action(tmp_path, action)
    capsys.readouterr()

    assert main(_action_args(tmp_path, action)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == expected_state


@pytest.mark.parametrize("action", [
    "create", "claim-start", "complete", "fail", "cancel",
])
def test_cli_task_actions_reject_mismatched_asserted_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    _prepare_action(tmp_path, action)
    _mismatched_request_context(monkeypatch)

    assert main(_action_args(tmp_path, action)) == 2
