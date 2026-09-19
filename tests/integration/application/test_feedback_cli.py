"""CLI-level coverage for the R4/P4b domain migration of `feedback
add`/`feedback resolve`: routed through ApplicationAPI.submit() (via
peerhub.client:Client) instead of calling FeedbackService directly -- see
docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md."""

from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def test_cli_feedback_add_and_resolve_route_through_application_api_gateway(
    tmp_path: Path, capsys
) -> None:
    exit_code = main([
        "feedback",
        "add",
        "--workspace",
        str(tmp_path),
        "--source-peer",
        "cx",
        "--category",
        "correctness",
        "--severity",
        "high",
        "--title",
        "wrong result",
        "--detail",
        "output mismatched expectation",
        "--actor",
        "cc",
        "--json",
    ])
    assert exit_code == 0
    add_payload = json.loads(capsys.readouterr().out)
    feedback_id = add_payload["feedback_id"]
    assert add_payload["source_peer"] == "cx"
    assert add_payload["status"] == "open"

    exit_code = main([
        "feedback",
        "resolve",
        "--workspace",
        str(tmp_path),
        "--feedback-id",
        feedback_id,
        "--status",
        "resolved",
        "--actor",
        "cc",
        "--json",
    ])
    assert exit_code == 0
    resolve_payload = json.loads(capsys.readouterr().out)
    assert resolve_payload["status"] == "resolved"


def test_cli_feedback_add_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch
) -> None:
    """The gateway-level GovernanceAuthorizer's asserted-path check must
    actually run for this migrated command."""

    import peerhub.cli as cli_module

    original_request_context = cli_module.RequestContext

    def _mismatched_request_context(*, principal: str, client_id: str):
        del client_id
        return original_request_context(
            principal=principal, client_id="a-different-client"
        )

    monkeypatch.setattr(cli_module, "RequestContext", _mismatched_request_context)

    exit_code = main([
        "feedback",
        "add",
        "--workspace",
        str(tmp_path),
        "--source-peer",
        "cx",
        "--category",
        "correctness",
        "--severity",
        "high",
        "--title",
        "wrong result",
        "--actor",
        "cc",
    ])
    assert exit_code == 2
