"""End-to-end coverage for the legacy mutating ``lease-sweep`` action."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import (
    LeaseSweepCommand,
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
    LeaseState,
    ProcessBirthIdentity,
    RecoveryDecision,
    RecoveryTrigger,
    SessionBindingKey,
)
from peerhub.runtime import create_runtime
from tests.fakes import SequentialIdSource


class FixedClock:
    def __init__(self, value: int = 100_000) -> None:
        self.value = value

    def now(self) -> int:
        return self.value

    def advance(self, amount: int) -> None:
        self.value += amount


def _submission(*, suffix: str = "default") -> SubmissionMetadata:
    return SubmissionMetadata(
        client_request_id=f"lease-sweep-request-{suffix}",
        correlation_id=f"lease-sweep-correlation-{suffix}",
        client_id="lease-sweep-client",
        actor_id="operator",
        scope={},
        idempotency_key=f"lease-sweep-idempotency-{suffix}",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=100_000,
    )


def _runtime(tmp_path: Path, clock: FixedClock):
    return create_runtime(
        RuntimeContext(
            "lease-sweep-workspace",
            PathLayout.for_workspace(tmp_path),
            clock,
            SequentialIdSource(),
        ),
        adapter_peer_kind="fake",
    )


def _create_lease(
    runtime: Any,
    *,
    suffix: str,
    profile_id: str = "ag.deepthink",
    pid: int | None = None,
    process_creation_time: int = 0,
    heartbeat_timeout: int = 1_000,
):
    process_pid = os.getpid() if pid is None else pid
    return runtime.dispatch_service.create_session_and_lease(
        SessionBindingKey(
            "scope",
            f"instance-{suffix}",
            profile_id,
            f"conversation-{suffix}",
        ),
        LeaseCreateRequest(
            session_id=f"session-{suffix}",
            owner_principal_id="operator",
            owner_instance_id=f"instance-{suffix}",
            owner_process_birth_identity=ProcessBirthIdentity(
                process_pid,
                process_creation_time,
            ),
            heartbeat_timeout_ms=heartbeat_timeout,
            command_id=CommandID(f"command-{suffix}"),
            attempt_id=f"attempt-{suffix}",
            authority_epoch=1,
            owner_peer_id=profile_id,
        ),
        "adapter-fingerprint",
        "readiness-binding",
    )[1]


def test_expired_lease_recovers_and_applies_policy_backoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = FixedClock()
    with _runtime(tmp_path, clock) as runtime:
        lease = _create_lease(runtime, suffix="expired")
        clock.advance(1_001)

        calls: list[tuple[str, int, str]] = []
        apply_backoff = runtime.health_service.apply_transient_backoff

        def record_backoff(
            profile_id: str,
            duration_seconds: int,
            reason: str,
        ) -> None:
            calls.append((profile_id, duration_seconds, reason))
            apply_backoff(profile_id, duration_seconds, reason)

        monkeypatch.setattr(
            runtime.health_service,
            "apply_transient_backoff",
            record_backoff,
        )

        report = runtime.process_lease_sweep_coordinator.sweep(
            recovery_actor_principal_id="system:lease-sweeper",
            reap=False,
        )
        persisted = runtime.dispatch_service.get_lease(lease.lease_id)
        with runtime.state_store.unit_of_work() as unit:
            receipt = unit.get_recovery_receipt(
                report.swept[0].recovery_receipt_id
            )

        assert persisted is not None
        assert persisted.state is LeaseState.FENCED
        assert receipt is not None
        assert receipt.trigger is RecoveryTrigger.HEARTBEAT_TIMEOUT
        assert receipt.pre_lifecycle_state is LeaseState.ACTIVE
        assert receipt.post_lifecycle_state is LeaseState.FENCED
        assert report.swept[0].lease_id == lease.lease_id
        assert report.swept[0].reaped is False
        assert calls == [("ag.deepthink", 30, "lease_expired")]
        assert report.swept[0].backoff_duration_seconds == 30
        assert (
            runtime.process_lease_sweep_coordinator.backoff_duration_seconds
            == runtime.health_service.policy.recovery_backoff_seconds[0]
            == 30
        )
        assert runtime.health_service.is_profile_gate_backed_off(
            "ag.deepthink",
            evaluated_at=clock.now(),
        )


def test_nonexpired_lease_is_completely_untouched(tmp_path: Path) -> None:
    clock = FixedClock()
    with _runtime(tmp_path, clock) as runtime:
        lease = _create_lease(runtime, suffix="current")

        report = runtime.process_lease_sweep_coordinator.sweep(
            recovery_actor_principal_id="system:lease-sweeper",
            reap=False,
        )
        persisted = runtime.dispatch_service.get_lease(lease.lease_id)

        assert report.swept == ()
        assert persisted == lease
        assert not runtime.health_service.is_profile_gate_backed_off(
            "ag.deepthink",
            evaluated_at=clock.now(),
        )


def test_sweep_limit_uses_expiry_then_lease_id_order(tmp_path: Path) -> None:
    clock = FixedClock()
    with _runtime(tmp_path, clock) as runtime:
        later = _create_lease(
            runtime,
            suffix="later",
            pid=2_000_000_000,
            process_creation_time=1,
            heartbeat_timeout=20,
        )
        clock.advance(5)
        earlier = _create_lease(
            runtime,
            suffix="earlier",
            pid=2_000_000_000,
            process_creation_time=1,
            heartbeat_timeout=5,
        )
        clock.advance(16)

        first = runtime.process_lease_sweep_coordinator.sweep(
            recovery_actor_principal_id="system:lease-sweeper",
            limit=1,
        )

        assert tuple(item.lease_id for item in first.swept) == (
            earlier.lease_id,
        )
        assert runtime.dispatch_service.get_lease(later.lease_id) == later

        second = runtime.process_lease_sweep_coordinator.sweep(
            recovery_actor_principal_id="system:lease-sweeper",
            limit=1,
        )
        assert tuple(item.lease_id for item in second.swept) == (
            later.lease_id,
        )


def test_second_sweep_is_a_convergent_noop(tmp_path: Path) -> None:
    clock = FixedClock()
    with _runtime(tmp_path, clock) as runtime:
        lease = _create_lease(
            runtime,
            suffix="convergent",
            pid=2_000_000_000,
            process_creation_time=1,
            heartbeat_timeout=1,
        )
        clock.advance(2)

        first = runtime.process_lease_sweep_coordinator.sweep(
            recovery_actor_principal_id="system:lease-sweeper",
        )
        after_first = runtime.dispatch_service.get_lease(lease.lease_id)
        second = runtime.process_lease_sweep_coordinator.sweep(
            recovery_actor_principal_id="system:lease-sweeper",
        )
        after_second = runtime.dispatch_service.get_lease(lease.lease_id)

        assert len(first.swept) == 1
        assert second.swept == ()
        assert after_first == after_second


def test_unknown_dead_pid_recovers_without_reap(tmp_path: Path) -> None:
    clock = FixedClock()
    with _runtime(tmp_path, clock) as runtime:
        lease = _create_lease(
            runtime,
            suffix="dead",
            profile_id="cx.deepthink",
            pid=2_000_000_000,
            process_creation_time=1,
            heartbeat_timeout=1,
        )
        clock.advance(2)

        report = runtime.process_lease_sweep_coordinator.sweep(
            recovery_actor_principal_id="system:lease-sweeper",
        )

        item = report.swept[0]
        assert item.lease_id == lease.lease_id
        assert item.process_alive is False
        assert item.process_identity_matches is True
        assert item.reaped is False
        assert item.reap_signal is None
        assert item.post_state is LeaseState.FENCED
        assert item.recovery_decision is RecoveryDecision.MARK_INTERRUPTED


def test_legacy_translation_and_api_execution(tmp_path: Path) -> None:
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            action="lease-sweep",
            arguments={"limit": 7, "no_reap": True},
        ),
        _submission(suffix="translate"),
    )
    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, LeaseSweepCommand)
    assert translated.command.limit == 7
    assert translated.command.reap is False

    clock = FixedClock()
    with _runtime(tmp_path, clock) as runtime:
        lease = _create_lease(
            runtime,
            suffix="api",
            pid=2_000_000_000,
            process_creation_time=1,
            heartbeat_timeout=1,
        )
        clock.advance(2)
        command = LeaseSweepCommand(
            submission=_submission(suffix="api"),
            limit=3,
            reap=False,
        )
        client = Client(
            runtime.application_api,
            caller=RequestContext(
                principal=AuthenticatedSubject(
                    "system:lease-sweeper",
                    "system",
                ).principal_id,
                client_id="lease-sweep-client",
            ),
        )

        outcome = client.submit(command)

        assert isinstance(outcome, CommandSuccess)
        assert isinstance(outcome.result["swept"], tuple)
        assert outcome.result["swept"][0]["lease_id"] == lease.lease_id
        assert outcome.result["swept"][0]["post_state"] == "FENCED"


def test_cli_lease_sweep_json(tmp_path: Path, capsys) -> None:
    clock = FixedClock()
    with _runtime(tmp_path, clock) as runtime:
        lease = _create_lease(
            runtime,
            suffix="cli",
            profile_id="cc.deepthink",
            pid=2_000_000_000,
            process_creation_time=1,
            heartbeat_timeout=1,
        )

    assert main([
        "lease",
        "sweep",
        "--workspace",
        str(tmp_path),
        "--limit",
        "5",
        "--no-reap",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["swept"][0]["lease_id"] == lease.lease_id
    assert payload["swept"][0]["profile_id"] == "cc.deepthink"
    assert payload["swept"][0]["post_state"] == "FENCED"
    assert payload["swept"][0]["backoff_duration_seconds"] == 30
