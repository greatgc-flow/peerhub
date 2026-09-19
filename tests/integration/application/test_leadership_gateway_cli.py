"""CLI-level coverage for the R4/P4b domain migration of `leadership
claim`/`yield`: routed through ApplicationAPI.submit() (via
peerhub.client:Client) instead of calling LeadershipService directly --
see docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md.

Note: the gateway's registered command returns its own flat wire shape
(disposition/status/term/challenge_until alongside receipt fields) for
`claim`, not the old CLI-local nested {"disposition":, "target": {...}}
shape -- a deliberate, documented JSON-shape change for this action."""

from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def test_cli_leadership_claim_and_yield_route_through_application_api_gateway(
    tmp_path: Path, capsys
) -> None:
    exit_code = main([
        "leadership", "claim",
        "--workspace", str(tmp_path),
        "--peer-node-id", "cc",
        "--actor", "cc",
        "--json",
    ])
    assert exit_code == 0
    claim_payload = json.loads(capsys.readouterr().out)
    assert claim_payload["status"] == "PENDING"

    exit_code = main([
        "leadership", "yield",
        "--workspace", str(tmp_path),
        "--peer-node-id", "cc",
        "--actor", "cc",
        "--json",
    ])
    assert exit_code == 0
    yield_payload = json.loads(capsys.readouterr().out)
    assert yield_payload["owner_mismatch"] is False


def test_cli_leadership_claim_rejects_mismatched_asserted_client(
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
        "leadership", "claim",
        "--workspace", str(tmp_path),
        "--peer-node-id", "cc",
        "--actor", "cc",
    ])
    assert exit_code == 2
