"""CLI audit 2026-11-22 (3 peers): small additive fixes. PeerHub gives facts; the AI decides.

profile stats/check take --workspace like list/show; diag running filters by peer and sets its row limit; diag and ask help
say who they are for.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from peerhub.cli.app import build_parser, main
from peerhub.extensions import diag_running
from tests.communication.harness.observation import ObservationHarness

pytestmark = [pytest.mark.unit]


def _help(*argv: str, capsys) -> str:
    with pytest.raises(SystemExit):
        build_parser().parse_args([*argv, "--help"])
    return " ".join(capsys.readouterr().out.split())


@pytest.mark.parametrize("action", ["stats", "check"])
def test_profile_stats_and_check_accept_workspace(action, tmp_path, capsys):
    db = Path(ObservationHarness(tmp_path / "ws").db_path)
    assert main(["--db", str(db), "profile", action, "--workspace", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)


def test_diag_running_filters_by_peer_and_takes_a_limit(monkeypatch):
    seen = {}

    class Stub:
        def __init__(self, db):
            pass

        def running(self, *, read_at, limit, workspace_generation=None):
            seen["limit"] = limit
            return {"status": "OK", "rows": [{"peer_id": "cx"}, {"peer_id": "ag"}, {"peer_id": "cx"}], "truncated": False, "error": None}

    monkeypatch.setattr(diag_running, "ReadonlyDiag", Stub)
    monkeypatch.setattr(diag_running, "_row", lambda raw, now, reader: raw)
    report = diag_running.running_report("x.db", peer="cx", limit=5)
    assert [r["peer_id"] for r in report["rows"]] == ["cx", "cx"] and seen["limit"] == 5 and report["limit"] == 5
    assert len(diag_running.running_report("x.db")["rows"]) == 3 and seen["limit"] == diag_running.MAX_ROWS


def test_diag_running_cli_options(tmp_path, capsys):
    db = Path(ObservationHarness(tmp_path / "ws").db_path)
    assert main(["--db", str(db), "diag", "running", "--peer", "cx", "--limit", "5", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["rows"] == [] and data["limit"] == 5


def test_diag_running_caller_facts_agree_in_json_and_text(monkeypatch, capsys):
    from peerhub.extensions import caller

    monkeypatch.setenv("PEERHUB_OWNER", "current-owner")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "same-session")
    width = 100
    monkeypatch.setenv("COLUMNS", str(width))
    facts = {**caller.detect_caller(), "owner": "foreign-owner", "tool": "cc", "session": "same-session",
             "label": "lead-x", "workspace": "work-root", "cwd": "work-cwd", "batch": "batch-x", "task": "task-x"}
    row = dict(peer_id="cx", metadata_json="{}", idempotency_key="ask:req", stream_id="stream",
               delivery_id="d1", certainty="STARTED", start_detail=None, expires_at=4_000_000_000,
               caller_detail=json.dumps(facts))

    def running(*args, **kwargs):
        return dict(status="OK", rows=[row], truncated=False, error=None)

    monkeypatch.setattr(diag_running.ReadonlyDiag, "running", running)
    argv = ["--db", str(Path(__file__).resolve()), "diag", "running"]
    assert main([*argv, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["rows"][0]
    assert result["relation"] == "other" and result["relation_basis"] == "owner"
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert f"{result['relation']}({result['relation_basis']})" in out
    assert all(len(line) <= width for line in out.splitlines())
    for key in ("tool", "label", "session", "host", "workspace", "cwd", "batch", "task"):
        assert result["caller"][key] == facts[key]
        assert f"{key}={facts[key]}" in out

    monkeypatch.delenv("PEERHUB_OWNER")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert main(argv) == 0
    assert capsys.readouterr().out.count("reader: unidentified") == 1


def test_help_says_who_each_command_is_for(capsys):
    diag = _help("diag", capsys=capsys)
    assert "AI agents" in diag and "peerhub monitor" in diag
    assert "human" in _help("diag", capsys=capsys).lower()
    assert "--query-file" in _help("ask", capsys=capsys).split("positional arguments:")[1].split("options:")[0]
