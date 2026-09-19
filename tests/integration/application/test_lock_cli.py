"""CLI-level coverage for the R4/P4b domain migration of `lock
acquire`/`release`: routed through ApplicationAPI.submit() (via
peerhub.client:Client) instead of calling FileLockService directly -- see
docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md."""

from __future__ import annotations

import json
from pathlib import Path

from peerhub.cli import main


def test_cli_lock_acquire_and_release_route_through_application_api_gateway(
    tmp_path: Path, capsys
) -> None:
    exit_code = main([
        "lock",
        "acquire",
        "--workspace",
        str(tmp_path),
        "--name",
        "shared.txt",
        "--owner",
        "cc",
        "--json",
    ])
    assert exit_code == 0
    acquire_payload = json.loads(capsys.readouterr().out)
    assert acquire_payload["owner"] == "cc"

    exit_code = main([
        "lock",
        "release",
        "--workspace",
        str(tmp_path),
        "--name",
        "shared.txt",
        "--owner",
        "cc",
        "--json",
    ])
    assert exit_code == 0
    release_payload = json.loads(capsys.readouterr().out)
    assert release_payload["disposition"] == "RELEASED"


def test_cli_lock_acquire_rejects_mismatched_asserted_client(
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
        "lock",
        "acquire",
        "--workspace",
        str(tmp_path),
        "--name",
        "shared.txt",
        "--owner",
        "cc",
    ])
    assert exit_code == 2
