"""CLI-level coverage for the R4/P4b domain migration of `artifact
claim`/`status`/`finalize`: routed through ApplicationAPI.submit() (via
peerhub.client:Client) instead of calling ArtifactRecordService directly --
see docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md."""

from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def test_cli_artifact_claim_status_finalize_route_through_gateway(
    tmp_path: Path, capsys
) -> None:
    exit_code = main([
        "artifact",
        "claim",
        "--workspace",
        str(tmp_path),
        "--name",
        "design-doc",
        "--peer",
        "cc",
        "--json",
    ])
    assert exit_code == 0
    claim_payload = json.loads(capsys.readouterr().out)
    assert claim_payload["owner"] == "cc"

    draft_file = tmp_path / "draft.md"
    draft_file.write_text("draft content", encoding="utf-8")
    exit_code = main([
        "artifact",
        "status",
        "--workspace",
        str(tmp_path),
        "--name",
        "design-doc",
        "--peer",
        "cc",
        "--draft-path",
        str(draft_file),
        "--json",
    ])
    assert exit_code == 0
    draft_payload = json.loads(capsys.readouterr().out)
    assert draft_payload["status"] == "draft"
    assert draft_payload["drafts"]["cc"] == str(draft_file)

    final_file = tmp_path / "final.md"
    final_file.write_text("final content", encoding="utf-8")
    exit_code = main([
        "artifact",
        "finalize",
        "--workspace",
        str(tmp_path),
        "--name",
        "design-doc",
        "--file",
        str(final_file),
        "--json",
    ])
    assert exit_code == 0
    finalize_payload = json.loads(capsys.readouterr().out)
    assert finalize_payload.get("hash")

    exit_code = main([
        "artifact",
        "status",
        "--workspace",
        str(tmp_path),
        "--name",
        "design-doc",
        "--json",
    ])
    assert exit_code == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["hash"] == finalize_payload["hash"]


def test_cli_artifact_claim_rejects_mismatched_asserted_client(
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

    exit_code = main([
        "artifact",
        "claim",
        "--workspace",
        str(tmp_path),
        "--name",
        "design-doc",
        "--peer",
        "cc",
    ])
    assert exit_code == 2
