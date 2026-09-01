"""Integration coverage for the legacy read-only ``lease-status`` action."""

from __future__ import annotations

import json
import os
from pathlib import Path

from peerhub.application.commands import SubmissionMetadata
from peerhub.application.lease_status import collect_lease_status
from peerhub.application.legacy import (
    LeaseStatusCommand,
    LegacyActionCall,
    LegacyTranslator,
    TranslatedCommand,
)
from peerhub.cli import main
from peerhub.client import Client
from peerhub.core.context import PathLayout, RuntimeContext
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.ports import RequestContext
from peerhub.core.protocol import CommandID, CommandSuccess
from peerhub.dispatch.contract import (
    LeaseCreateRequest,
    ProcessBirthIdentity,
    SessionBindingKey,
)
from peerhub.runtime import create_runtime
from tests.fakes import SequentialIdSource


class FixedClock:
    def __init__(self, value: int = 100_000) -> None:
        self.value = value

    def now(self) -> int:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += milliseconds


def _submission() -> SubmissionMetadata:
    return SubmissionMetadata(
        client_request_id="lease-status-request",
        correlation_id="lease-status-correlation",
        client_id="lease-status-client",
        actor_id="operator",
        scope={},
        idempotency_key=None,
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=100_000,
    )


def _create_lease(runtime, clock: FixedClock, *, peer: str, suffix: str):
    return runtime.dispatch_service.create_session_and_lease(
        SessionBindingKey("scope", f"instance-{suffix}", "profile", f"conversation-{suffix}"),
        LeaseCreateRequest(
            session_id=f"session-{suffix}",
            owner_principal_id="operator",
            owner_instance_id=f"instance-{suffix}",
            owner_process_birth_identity=ProcessBirthIdentity(os.getpid(), 0),
            heartbeat_timeout_ms=1_000,
            command_id=CommandID(f"command-{suffix}"),
            attempt_id=f"attempt-{suffix}",
            authority_epoch=1,
            owner_peer_id=peer,
        ),
        "adapter-fingerprint",
        "readiness-binding",
    )[1]


def _runtime(tmp_path: Path, clock: FixedClock):
    return create_runtime(
        RuntimeContext(
            "lease-status-workspace",
            PathLayout.for_workspace(tmp_path),
            clock,
            SequentialIdSource(),
        ),
        adapter_peer_kind="fake",
    )


def test_lease_status_sqlite_roundtrip_lists_multiple_active_leases(tmp_path: Path):
    clock = FixedClock()
    with _runtime(tmp_path, clock) as runtime:
        first = _create_lease(runtime, clock, peer="ag", suffix="one")
        second = _create_lease(runtime, clock, peer="cx", suffix="two")

        rows = collect_lease_status(runtime.dispatch_service, now=clock.now())

    assert [row["lease_id"] for row in rows] == [first.lease_id, second.lease_id]
    assert [row["peer"] for row in rows] == ["ag", "cx"]
    assert all(row["status"] == "open" for row in rows)
    assert all(row["lease_state"] == "ACTIVE" for row in rows)
    assert all(row["pid"] == os.getpid() for row in rows)
    assert all(row["alive"] == "YES" for row in rows)
    assert all(row["expires_at"] == 101_000 for row in rows)
    assert all(row["heartbeat_at"] == 100_000 for row in rows)


def test_lease_status_reports_expired_lease_without_mutating_it(tmp_path: Path):
    clock = FixedClock()
    with _runtime(tmp_path, clock) as runtime:
        lease = _create_lease(runtime, clock, peer="cc", suffix="expired")
        clock.advance(1_001)

        rows = collect_lease_status(runtime.dispatch_service, now=clock.now())
        persisted = runtime.dispatch_service.get_lease(lease.lease_id)

    assert len(rows) == 1
    assert rows[0]["lease_id"] == lease.lease_id
    assert rows[0]["expired"] is True
    assert rows[0]["status"] == "open !"
    assert persisted == lease


def test_lease_status_legacy_translation_and_api_execution(tmp_path: Path):
    clock = FixedClock()
    with _runtime(tmp_path, clock) as runtime:
        _create_lease(runtime, clock, peer="ag", suffix="api")
        translated = LegacyTranslator().translate(
            LegacyActionCall(action="lease-status", arguments={}), _submission()
        )
        assert isinstance(translated, TranslatedCommand)
        assert isinstance(translated.command, LeaseStatusCommand)

        client = Client(
            runtime.application_api,
            caller=RequestContext(
                principal=AuthenticatedSubject("operator", "system").principal_id,
                client_id="lease-status-client",
            ),
        )
        outcome = client.submit(translated.command)

    assert isinstance(outcome, CommandSuccess)
    assert isinstance(outcome.result["leases"], tuple)
    assert outcome.result["leases"][0]["peer"] == "ag"


def test_cli_lease_status_json(tmp_path: Path, capsys):
    clock = FixedClock()
    with _runtime(tmp_path, clock) as runtime:
        _create_lease(runtime, clock, peer="cx", suffix="cli")

    assert main(["lease", "status", "--workspace", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["leases"][0]["peer"] == "cx"
    assert payload["leases"][0]["status"].startswith("open")
