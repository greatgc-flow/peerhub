"""CLI-level coverage for the R4/P4b template-domain migration: `error
report` now routes through ApplicationAPI.submit() (via peerhub.client:Client)
instead of calling OperationalErrorService directly -- see
docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md."""

from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def test_cli_error_report_routes_through_application_api_gateway(
    tmp_path: Path, capsys
) -> None:
    exit_code = main([
        "error",
        "report",
        "--workspace",
        str(tmp_path),
        "--peer",
        "cx",
        "--pattern",
        "timeout",
        "--severity",
        "warn",
        "--detail",
        "dispatch timed out",
        "--actor",
        "cc",
        "--json",
    ])
    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["peer_key"] == "cx"
    assert payload["pattern"] == "timeout"
    assert payload["count"] == 1


def test_cli_error_report_rejects_mismatched_asserted_client(
    tmp_path: Path, monkeypatch
) -> None:
    """The gateway-level GovernanceAuthorizer's asserted-path check (R4/P4b)
    must actually run for this migrated command -- not a no-op. Simulate a
    caller/submission client_id mismatch by monkeypatching the CLI's
    hardcoded client_id constant used to build both the RequestContext and
    the SubmissionMetadata, so only one of the two updates."""

    import peerhub.cli as cli_module

    original_request_context = cli_module.RequestContext

    def _mismatched_request_context(*, principal: str, client_id: str):
        del client_id
        return original_request_context(
            principal=principal, client_id="a-different-client"
        )

    monkeypatch.setattr(cli_module, "RequestContext", _mismatched_request_context)

    exit_code = main([
        "error",
        "report",
        "--workspace",
        str(tmp_path),
        "--actor",
        "cc",
    ])
    assert exit_code == 2
