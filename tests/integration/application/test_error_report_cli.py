"""CLI-level coverage for gateway-routed operational-error mutations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from peerhub.cli import main


def _requested_review(tmp_path: Path, capsys) -> str:
    """Create a review whose peer can be escalated by the real coordinator."""

    assert main([
        "node",
        "register",
        "--workspace",
        str(tmp_path),
        "--node-id",
        "cc-node",
        "--peer-kind",
        "cc",
        "--profile-id",
        "cc.standard",
        "--actor",
        "admin",
        "--json",
    ]) == 0
    capsys.readouterr()

    assert main([
        "error",
        "report",
        "--workspace",
        str(tmp_path),
        "--peer",
        "cc-node",
        "--pattern",
        "timeout",
        "--severity",
        "warn",
        "--detail",
        "dispatch timed out",
        "--actor",
        "admin",
        "--threshold",
        "1",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    review_id = payload["quarantine_review_id"]
    assert isinstance(review_id, str)
    return review_id


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


def test_cli_error_review_resolve_dismiss_routes_through_gateway(
    tmp_path: Path, capsys
) -> None:
    review_id = _requested_review(tmp_path, capsys)

    exit_code = main([
        "error",
        "review",
        "resolve",
        "--workspace",
        str(tmp_path),
        "--review-id",
        review_id,
        "--decision",
        "DISMISS",
        "--reason",
        "evidence was insufficient",
        "--actor",
        "admin",
        "--json",
    ])
    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["review_id"] == review_id
    assert payload["status"] == "DISMISSED"
    assert payload["resolved_by"] == "admin"
    assert payload["reason"] == "evidence was insufficient"


def test_cli_error_review_resolve_escalate_routes_through_gateway(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    review_id = _requested_review(tmp_path, capsys)

    # The coordinator's health authorization has dedicated integration
    # coverage. Stub it here so this gateway test reaches the successful
    # ESCALATE result without coupling to health-readiness setup.
    from peerhub.health.service import HealthService

    class DummyReceipt:
        incident = "test-incident"
        gate_generation = 1
        timestamp = 1000
        fingerprint = "test-fingerprint"

    class DummyCircuit:
        receipt = DummyReceipt()

    def _get_circuit(*args: object, **kwargs: object) -> object:
        return None

    def _open_manual_quarantine(*args: object, **kwargs: object) -> object:
        return DummyCircuit()

    monkeypatch.setattr(
        HealthService,
        "get_circuit",
        _get_circuit,
    )
    monkeypatch.setattr(
        HealthService,
        "open_manual_quarantine",
        _open_manual_quarantine,
    )

    exit_code = main([
        "error",
        "review",
        "resolve",
        "--workspace",
        str(tmp_path),
        "--review-id",
        review_id,
        "--decision",
        "ESCALATE",
        "--reason",
        "requires recovery review",
        "--actor",
        "admin",
        "--json",
    ])
    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["review_id"] == review_id
    assert payload["status"] == "ESCALATED"
    assert payload["resolved_by"] == "admin"
    assert payload["reason"] == "requires recovery review"


def test_cli_error_review_resolve_rejects_mismatched_asserted_client(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    review_id = _requested_review(tmp_path, capsys)

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
        "review",
        "resolve",
        "--workspace",
        str(tmp_path),
        "--review-id",
        review_id,
        "--decision",
        "DISMISS",
        "--reason",
        "evidence was insufficient",
        "--actor",
        "admin",
    ])
    assert exit_code == 2


def test_cli_error_review_resolve_rejects_invalid_decision(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as error:
        main([
            "error",
            "review",
            "resolve",
            "--workspace",
            str(tmp_path),
            "--review-id",
            "review-1",
            "--decision",
            "IGNORE",
            "--reason",
            "not applicable",
            "--actor",
            "admin",
        ])
    assert error.value.code == 2
