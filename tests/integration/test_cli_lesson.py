from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def test_cli_lesson_propose_approve_activate_and_status(tmp_path: Path, capsys) -> None:
    args = ["lesson", "propose", "--workspace", str(tmp_path), "--lesson-id", "l1", "--title", "Rule", "--rule", "Do this", "--category", "ops", "--severity", "HIGH", "--proposer", "cx", "--affected", "cx,ag", "--json"]
    assert main(args) == 0
    proposed = json.loads(capsys.readouterr().out)
    assert proposed["scope"]["workspace_id"] is None
    # --scope-kind omitted: must default to "global" (LessonService.propose's
    # real Python default), not "" -- an earlier version of this CLI mapped
    # every omitted optional flag to "" uniformly and got this wrong.
    assert proposed["scope"]["kind"] == "global"
    assert proposed["lifecycle"] == "PROPOSED"
    assert main(["lesson", "approve", "--workspace", str(tmp_path), "--lesson-id", "l1", "--approved-by", "human:a"]) == 0
    capsys.readouterr()
    assert main(["lesson", "activate", "--workspace", str(tmp_path), "--lesson-id", "l1", "--actor", "human:a"]) == 0
    assert "ACTIVE" in capsys.readouterr().out


def test_cli_lesson_status_missing_returns_two(tmp_path: Path, capsys) -> None:
    assert main(["lesson", "status", "--workspace", str(tmp_path), "--lesson-id", "missing"]) == 2
    assert "not found" in capsys.readouterr().err
