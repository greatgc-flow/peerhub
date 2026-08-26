from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def test_cli_task_lifecycle_and_json_status(tmp_path: Path, capsys) -> None:
    base = ["task", "create", "--workspace", str(tmp_path), "--task-id", "task-cli", "--summary", "Ship", "--spec", "Do it", "--creator", "cx", "--json"]
    assert main(base) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["state"] == "CREATED"
    # --room-id omitted: scope must be None, not "" -- an empty-string scope
    # would silently diverge from what TaskService.create(room_id=None)
    # (the real Python default) produces, breaking list_active_tasks()
    # scope-filtering consistency between CLI-created and API-created tasks.
    assert created["scope"] is None
    assert main(["task", "claim-start", "--workspace", str(tmp_path), "--task-id", "task-cli", "--actor", "cx", "--request-id", "req-1", "--coordinator", "cx", "--attempt-id", "attempt-1"]) == 0
    assert "RUNNING" in capsys.readouterr().out
    assert main(["task", "complete", "--workspace", str(tmp_path), "--task-id", "task-cli", "--actor", "cx"]) == 0
    assert "SUCCEEDED" in capsys.readouterr().out
    assert main(["task", "status", "--workspace", str(tmp_path), "--task-id", "task-cli", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "SUCCEEDED"


def test_cli_task_missing_status_returns_two(tmp_path: Path, capsys) -> None:
    assert main(["task", "status", "--workspace", str(tmp_path), "--task-id", "missing"]) == 2
    assert "not found" in capsys.readouterr().err
