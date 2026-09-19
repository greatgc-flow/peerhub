"""CLI-level coverage for the R4/P4b domain migration of `task
checkpoint`: routed through ApplicationAPI.submit() (via
peerhub.client:Client) instead of calling TaskService directly -- see
docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md. `task
create`/`claim-start`/`complete`/`fail`/`cancel` remain direct calls
(no registered ApplicationAPI command for them yet)."""

from __future__ import annotations

import json
from pathlib import Path

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

    import peerhub.cli as cli_module

    original_request_context = cli_module.RequestContext

    def _mismatched_request_context(*, principal: str, client_id: str):
        del client_id
        return original_request_context(
            principal=principal, client_id="a-different-client"
        )

    monkeypatch.setattr(cli_module, "RequestContext", _mismatched_request_context)

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
