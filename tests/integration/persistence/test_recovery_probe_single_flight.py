"""Integration tests for recovery-probe grant single-flight."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from peerhub.core.errors import RecoveryProbeGrantConflictError
from peerhub.health.contract import (
    CircuitState,
    HealthCircuitSnapshot,
    PolicyReceipt,
    PolicyScope,
    QuarantineAuthorityClass,
    RecoveryAuthorizationMode,
    RecoveryGrantState,
    RecoveryProbeGrant,
)
from peerhub.persistence.sqlite import SqliteStateStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "recovery-probe.sqlite3",
        workspace_home_id="workspace-recovery-probe",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def _receipt() -> PolicyReceipt:
    return PolicyReceipt(
        incident="incident-recovery-probe",
        gate_generation=1,
        timestamp=100,
        fingerprint="fingerprint-recovery-probe",
    )


def _circuit() -> HealthCircuitSnapshot:
    return HealthCircuitSnapshot(
        circuit_id="circuit-recovery-probe",
        scope=PolicyScope.PROFILE,
        subject="ag.default",
        state=CircuitState.CIRCUIT_OPEN,
        quarantine_authority_class=(
            QuarantineAuthorityClass.AUTOMATIC
        ),
        receipt=_receipt(),
        backoff_count=0,
        cooldown_until=None,
        revision=1,
        created_at=100,
        updated_at=100,
    )


def _grant(
    grant_id: str,
    *,
    authorized_at: int,
) -> RecoveryProbeGrant:
    return RecoveryProbeGrant(
        grant_id=grant_id,
        circuit_id="circuit-recovery-probe",
        receipt=_receipt(),
        authorized_by="administrator",
        authorized_at=authorized_at,
        authorization_mode=RecoveryAuthorizationMode.AUTOMATIC,
        authorized_circuit_revision=1,
        state=RecoveryGrantState.GRANTED,
        expires_at=authorized_at + 300,
        consumed_at=None,
        consumed_by_attempt_id=None,
        revision=1,
    )


def test_only_one_live_recovery_grant_exists_per_circuit(
    store: SqliteStateStore,
) -> None:
    with store.unit_of_work() as unit:
        unit.add_health_circuit(_circuit())
        unit.commit()

    first = _grant(
        "grant-recovery-probe-01",
        authorized_at=100,
    )
    blocked = _grant(
        "grant-recovery-probe-02",
        authorized_at=101,
    )

    with store.unit_of_work() as unit:
        unit.add_recovery_probe_grant(first)
        unit.commit()

    with pytest.raises(
        RecoveryProbeGrantConflictError,
    ) as excinfo:
        with store.unit_of_work() as unit:
            unit.add_recovery_probe_grant(blocked)
            unit.commit()
    assert excinfo.value.circuit_id == "circuit-recovery-probe"
    assert excinfo.value.current_grant_id == "grant-recovery-probe-01"

    with store.unit_of_work() as unit:
        persisted_first = unit.get_recovery_probe_grant(
            first.grant_id
        )
        assert persisted_first is not None
        consumed_first = replace(
            persisted_first,
            state=RecoveryGrantState.CLAIMED,
            consumed_at=102,
            consumed_by_attempt_id="probe-attempt-01",
            revision=persisted_first.revision + 1,
        )
        assert unit.cas_claim_recovery_probe_grant(
            persisted_first,
            consumed_first,
        )
        unit.commit()

    replacement = _grant(
        "grant-recovery-probe-03",
        authorized_at=103,
    )
    with pytest.raises(RecoveryProbeGrantConflictError):
        with store.unit_of_work() as unit:
            unit.add_recovery_probe_grant(replacement)
            unit.commit()

    with store.unit_of_work() as unit:
        claimed_first = unit.get_recovery_probe_grant(
            first.grant_id
        )
        assert claimed_first is not None
        completed_first = replace(
            claimed_first,
            state=RecoveryGrantState.SUCCEEDED,
            revision=claimed_first.revision + 1,
        )
        assert unit.cas_claim_recovery_probe_grant(
            claimed_first,
            completed_first,
        )
        unit.commit()

    with store.unit_of_work() as unit:
        unit.add_recovery_probe_grant(replacement)
        unit.commit()

    with store.unit_of_work() as unit:
        stored_first = unit.get_recovery_probe_grant(
            first.grant_id
        )
        stored_replacement = unit.get_recovery_probe_grant(
            replacement.grant_id
        )

    assert stored_first is not None
    assert stored_first.state is RecoveryGrantState.SUCCEEDED
    assert stored_first.consumed_at == 102
    assert stored_replacement is not None
    assert stored_replacement.state is RecoveryGrantState.GRANTED
    assert stored_replacement.consumed_at is None


def test_two_threads_racing_to_grant_the_same_circuit_have_one_winner(
    store: SqliteStateStore,
) -> None:
    with store.unit_of_work() as unit:
        unit.add_health_circuit(_circuit())
        unit.commit()

    contenders = (
        _grant("grant-recovery-probe-race-a", authorized_at=100),
        _grant("grant-recovery-probe-race-b", authorized_at=100),
    )

    def attempt(index: int):
        try:
            with store.unit_of_work() as unit:
                unit.add_recovery_probe_grant(contenders[index])
                unit.commit()
            return ("committed", contenders[index].grant_id)
        except RecoveryProbeGrantConflictError as error:
            return ("conflict", error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(attempt, (0, 1)))

    committed = [
        value for status, value in results if status == "committed"
    ]
    conflicts = [
        value for status, value in results if status == "conflict"
    ]
    assert len(committed) == 1
    assert len(conflicts) == 1
    assert conflicts[0].circuit_id == "circuit-recovery-probe"
    assert conflicts[0].current_grant_id == committed[0]

    with store.unit_of_work() as unit:
        live_grant = unit.get_live_recovery_probe_grant(
            "circuit-recovery-probe"
        )
    assert live_grant is not None
    assert live_grant.grant_id == committed[0]
