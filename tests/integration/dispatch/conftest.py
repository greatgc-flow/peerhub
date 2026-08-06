"""Fixtures for vertical dispatch integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import pytest

from peerhub.application.workflows import ApplicationWorkflows
from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.dispatch.service import DispatchService
from peerhub.health.contract import (
    AdmissionSnapshot,
    HealthPolicy,
    HealthScopeMembershipSnapshot,
)
from peerhub.health.service import HealthService
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.routing.service import RoutingService
from peerhub.telemetry.contract import ReadinessMeasurement, ReadinessObserved
from peerhub.telemetry.projections import TelemetryProjector
from tests.fakes import DeterministicClock, SequentialIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "vertical_dispatch.sqlite3",
        workspace_home_id="workspace-vertical-dispatch",
    )
    state_store.initialize()
    _seed_health(state_store)
    try:
        yield state_store
    finally:
        state_store.close()


@pytest.fixture
def fake_peer_script() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "tools" / "fake_peer" / "pipe_executable.py"
    assert script_path.exists(), f"fake_peer script not found at {script_path}"
    return script_path


def _policy() -> HealthPolicy:
    return HealthPolicy(
        policy_id="v1-health-default-r1",
        revision=1,
        readiness_freshness_seconds=7200,
        recovery_backoff_seconds=(30, 60, 120, 240, 480, 900),
        recovery_jitter_fraction=0.2,
        readiness_observation_threshold=1,
        administrative_recovery_probe_limit=1,
    )


def _membership(*, configuration_revision: int = 11) -> HealthScopeMembershipSnapshot:
    return HealthScopeMembershipSnapshot(
        configuration_revision=configuration_revision,
        configuration_digest="e" * 64,
        configured_members=(("ag", "ag.deepthink"),),
        bindings=(),
    )


def _readiness(
    observation_id: str,
    *,
    observed_at: int = 100,
) -> ReadinessObserved:
    return ReadinessObserved(
        observation_id=observation_id,
        instance_id="ag",
        profile_id="ag.deepthink",
        evidence=EvidenceValue(
            state=EvidenceState.MEASURED,
            source_tag="empirical_probe",
            provider_id="phase0-readiness",
            provider_version="1",
            observed_at=observed_at,
            captured_at=observed_at,
            freshness_ttl=7200,
            evidence_ref=EvidenceRef(f"sha256:{observation_id}"),
            value=ReadinessMeasurement(
                runtime_revision="runtime-r17",
                issued_at=1,
                valid_until=10_000,
                integrity_verified=True,
            ),
        ),
    )


def _seed_health(store: SqliteStateStore) -> None:
    with store.unit_of_work() as unit:
        unit.add_health_policy_revision(_policy())
        unit.commit()

    telemetry = TelemetryProjector(
        store,
        ids=SequentialIdSource(),
        freshness_ttl=3600,
    )
    health = HealthService(
        store,
        telemetry=telemetry,
        policy=_policy(),
        membership=_membership(),
        clock=DeterministicClock(start=100),
        ids=SequentialIdSource(),
    )
    health.evaluate_and_persist_readiness(
        _readiness("readiness-01"),
        sealed_runtime_revision="runtime-r17",
        adapter_declares_probe_safe=True,
    )
