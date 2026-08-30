"""Integration tests for Gap A: centralized read-time health-freshness check.

SQLite-backed integration tests -- no mocks. Follows the same pattern
as tests/integration/application/test_role_assignment.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from peerhub.application.peer_registry import PeerRegistryService
from peerhub.application.role_assignment import (
    RoleAssigneeUnavailableError,
    RoleAssignmentService,
)
from peerhub.core.context import Clock
from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.governance.broker import GovernanceBroker
from peerhub.health.contract import (
    AdmissionState,
    AvailabilityState,
    CircuitState,
    EvidenceSubject,
    HealthPolicy,
    HealthProjectionRead,
    HealthProjectionSnapshot,
    HealthScopeMembershipSnapshot,
    HealthStage,
    HealthStageObservation,
    HealthStageStatus,
    PolicyReceipt,
    PolicyScope,
    QuarantineAuthorityClass,
)
from peerhub.health.model import evaluate_projection_at
from peerhub.health.service import HealthService
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.telemetry.contract import ReadinessMeasurement, ReadinessObserved
from peerhub.telemetry.projections import TelemetryProjector
from tests.fakes import SequentialIdSource


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class FixedClock(Clock):
    def __init__(self, value: int = 100_000) -> None:
        self.value = value

    def now(self) -> int:
        return self.value


_POLICY = HealthPolicy(
    policy_id="freshness-test-v1",
    revision=1,
    readiness_freshness_seconds=3600,  # 1 hour
    recovery_backoff_seconds=(30, 60),
    recovery_jitter_fraction=0.0,
    readiness_observation_threshold=1,
    administrative_recovery_probe_limit=1,
)

_INSTANCE_ID = "cc"
_PROFILE_ID = "cc.standard"
_RUNTIME_REVISION = "a" * 64


def _make_readiness(
    ids: SequentialIdSource,
    *,
    observed_at: int,
    valid_until: int | None = None,
) -> ReadinessObserved:
    """Build a ReadinessObserved with controlled timestamps."""
    if valid_until is None:
        valid_until = observed_at + _POLICY.readiness_freshness_seconds
    return ReadinessObserved(
        observation_id=ids.new_id("readiness-obs"),
        instance_id=_INSTANCE_ID,
        profile_id=_PROFILE_ID,
        evidence=EvidenceValue(
            state=EvidenceState.MEASURED,
            source_tag="test",
            provider_id="test-probe",
            provider_version="1",
            observed_at=observed_at,
            captured_at=observed_at,
            freshness_ttl=3600,
            evidence_ref=EvidenceRef("sha256:" + "0" * 64),
            value=ReadinessMeasurement(
                runtime_revision=_RUNTIME_REVISION,
                issued_at=observed_at,
                valid_until=valid_until,
                integrity_verified=True,
            ),
        ),
    )


@pytest.fixture
def services(
    tmp_path: Path,
) -> Iterator[
    tuple[
        HealthService,
        SqliteStateStore,
        FixedClock,
        SequentialIdSource,
        GovernanceBroker,
        PeerRegistryService,
    ]
]:
    store = SqliteStateStore(
        tmp_path / "freshness-test.sqlite3",
        workspace_home_id="freshness-test",
    )
    store.initialize()
    clock = FixedClock()
    ids = SequentialIdSource()
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    peer_registry = PeerRegistryService(broker, clock=clock, ids=ids)

    with store.unit_of_work() as unit:
        unit.add_health_policy_revision(_POLICY)
        unit.commit()

    telemetry = TelemetryProjector(
        store,
        ids=ids,
        freshness_ttl=3600,
    )
    health = HealthService(
        store,
        telemetry=telemetry,
        policy=_POLICY,
        membership=HealthScopeMembershipSnapshot(
            configuration_revision=1,
            configuration_digest="a" * 64,
            configured_members=((_INSTANCE_ID, _PROFILE_ID),),
            bindings=(),
        ),
        clock=clock,
        ids=ids,
    )
    try:
        yield health, store, clock, ids, broker, peer_registry
    finally:
        store.close()


def _seed_projection_with_readiness(
    health: HealthService,
    store: SqliteStateStore,
    ids: SequentialIdSource,
    *,
    readiness_observed_at: int,
    readiness_valid_until: int | None = None,
) -> HealthProjectionSnapshot:
    """Persist readiness evidence and produce a health projection."""
    readiness = _make_readiness(
        ids,
        observed_at=readiness_observed_at,
        valid_until=readiness_valid_until,
    )
    projection = health.evaluate_and_persist_readiness(
        readiness,
        sealed_runtime_revision=_RUNTIME_REVISION,
        adapter_declares_probe_safe=True,
    )
    return projection


# ---------------------------------------------------------------------------
# Test 1: Fresh projection -- effective states unchanged from stored
# ---------------------------------------------------------------------------

def test_fresh_projection_preserves_stored_states(
    services: tuple[HealthService, SqliteStateStore, FixedClock, SequentialIdSource, GovernanceBroker, PeerRegistryService],
) -> None:
    """A projection that is fresh at read time: effective states unchanged."""
    health, store, clock, ids, _, _ = services

    # Readiness observed 10 minutes ago -- well within 1hr freshness.
    clock.value = 100_000
    projection = _seed_projection_with_readiness(
        health, store, ids, readiness_observed_at=99_400
    )
    assert projection.availability_state is AvailabilityState.HEALTHY
    assert projection.admission_state is AdmissionState.OPEN

    # Read at current time -- should be fresh.
    read = health.read_health_projection(
        _INSTANCE_ID, _PROFILE_ID, evaluated_at=100_000
    )
    assert read is not None
    assert read.stale_at_read is False
    assert read.effective_availability_state is AvailabilityState.HEALTHY
    assert read.effective_admission_state is AdmissionState.OPEN


# ---------------------------------------------------------------------------
# Test 2: Stale evidence purely due to elapsed time
# ---------------------------------------------------------------------------

def test_stale_evidence_produces_stale_availability_and_recovery_required(
    services: tuple[HealthService, SqliteStateStore, FixedClock, SequentialIdSource, GovernanceBroker, PeerRegistryService],
) -> None:
    """Projection stale purely due to elapsed time (not a circuit event):
    effective availability becomes STALE, effective admission becomes
    at least RECOVERY_REQUIRED."""
    health, store, clock, ids, _, _ = services

    # Readiness observed at t=90_000.  Freshness window = 3600s.
    # At t=100_000, evidence is 10_000s old > 3600s => stale.
    clock.value = 90_000
    projection = _seed_projection_with_readiness(
        health, store, ids, readiness_observed_at=90_000
    )
    assert projection.availability_state is AvailabilityState.HEALTHY
    assert projection.admission_state is AdmissionState.OPEN

    # Read at t=100_000 -- stale.
    read = health.read_health_projection(
        _INSTANCE_ID, _PROFILE_ID, evaluated_at=100_000
    )
    assert read is not None
    assert read.stale_at_read is True
    assert read.effective_availability_state is AvailabilityState.STALE
    assert read.effective_admission_state is AdmissionState.RECOVERY_REQUIRED


# ---------------------------------------------------------------------------
# Test 3: Monotonic worst-of -- QUARANTINED stays QUARANTINED
# ---------------------------------------------------------------------------

def test_monotonic_worst_of_quarantined_stays_quarantined(
    services: tuple[HealthService, SqliteStateStore, FixedClock, SequentialIdSource, GovernanceBroker, PeerRegistryService],
) -> None:
    """Monotonic worst-of case: a projection with circuit-derived
    QUARANTINED admission AND stale readiness evidence -- effective
    admission must remain QUARANTINED (the worse state), not get
    downgraded to RECOVERY_REQUIRED."""
    health, store, clock, ids, _, _ = services

    # Seed fresh readiness.
    clock.value = 90_000
    projection = _seed_projection_with_readiness(
        health, store, ids, readiness_observed_at=90_000
    )

    # Manually update to QUARANTINED (simulating circuit-derived state).
    quarantined = replace(
        projection,
        admission_state=AdmissionState.QUARANTINED,
        revision=projection.revision + 1,
        updated_at=90_000,
    )
    with store.unit_of_work() as unit:
        unit.cas_update_health_projection(projection, quarantined)
        unit.commit()

    # Read at t=100_000 -- evidence is stale AND circuit is QUARANTINED.
    read = health.read_health_projection(
        _INSTANCE_ID, _PROFILE_ID, evaluated_at=100_000
    )
    assert read is not None
    assert read.stale_at_read is True
    # Effective admission must be QUARANTINED (the worse state),
    # NOT downgraded to RECOVERY_REQUIRED.
    assert read.effective_admission_state is AdmissionState.QUARANTINED
    assert read.effective_availability_state is AvailabilityState.STALE


# ---------------------------------------------------------------------------
# Test 4: Regression test for bug #2 -- _recompute_members
# ---------------------------------------------------------------------------

def test_circuit_recompute_does_not_hide_stale_evidence(
    services: tuple[HealthService, SqliteStateStore, FixedClock, SequentialIdSource, GovernanceBroker, PeerRegistryService],
) -> None:
    """A projection recomputed via _recompute_members (circuit-state change)
    with NO new readiness observation: confirm the new read-time logic still
    correctly identifies the underlying evidence as stale.
    This is the regression test for bug #2 -- proves updated_at is no
    longer trusted as the staleness clock."""
    health, store, clock, ids, _, _ = services

    # 1) Seed readiness at t=50_000.
    clock.value = 50_000
    projection = _seed_projection_with_readiness(
        health, store, ids, readiness_observed_at=50_000
    )
    assert projection.availability_state is AvailabilityState.HEALTHY

    # 2) At t=52_000 (still within freshness window), open a circuit.
    #    This triggers _recompute_members, advancing updated_at to 52_000.
    clock.value = 52_000
    health.classify_and_open_circuit(
        (
            HealthStageObservation(
                stage=HealthStage.CALL_PROVIDER,
                status=HealthStageStatus.FAILED,
            ),
        ),
        evidence_subject=EvidenceSubject(
            scope=PolicyScope.PROFILE,
            subject=_PROFILE_ID,
        ),
        receipt=PolicyReceipt(
            incident="test-incident-1",
            gate_generation=1,
            timestamp=52_000,
            fingerprint="f" * 64,
        ),
    )

    # 3) Verify projection.updated_at advanced (bug #2 precondition).
    with store.unit_of_work() as unit:
        stored = unit.get_health_projection(_INSTANCE_ID, _PROFILE_ID)
    assert stored is not None
    assert stored.updated_at >= 52_000  # updated_at advanced

    # 4) Read at t=60_000 -- well beyond readiness freshness (50_000 + 3600 = 53_600).
    #    The NEW code anchors staleness on readiness.observed_at=50_000,
    #    so 60_000 > 53_600 => correctly stale.
    read = health.read_health_projection(
        _INSTANCE_ID, _PROFILE_ID, evaluated_at=60_000
    )
    assert read is not None
    assert read.stale_at_read is True
    assert read.effective_availability_state is AvailabilityState.STALE


# ---------------------------------------------------------------------------
# Test 5: direct_ask eligibility -- stale availability excludes candidate
# ---------------------------------------------------------------------------

def test_stale_availability_excludes_from_admission_snapshot(
    services: tuple[HealthService, SqliteStateStore, FixedClock, SequentialIdSource, GovernanceBroker, PeerRegistryService],
) -> None:
    """A candidate with fresh admission_state=OPEN but stale availability_state
    must now be correctly excluded. This is the regression test for bug #1.
    We test via freeze_admission_snapshot which is what direct_ask consumes."""
    health, store, clock, ids, _, _ = services

    # Seed readiness at t=50_000.
    clock.value = 50_000
    _seed_projection_with_readiness(
        health, store, ids, readiness_observed_at=50_000
    )

    # At t=60_000 (>3600s later), freeze an admission snapshot.
    clock.value = 60_000
    snapshot = health.freeze_admission_snapshot()

    assert len(snapshot.entries) == 1
    entry = snapshot.entries[0]
    # Key assertion: with stale evidence, the frozen snapshot must NOT
    # show HEALTHY/OPEN -- it must reflect the read-time evaluation.
    assert entry.availability_state is AvailabilityState.STALE
    assert entry.admission_state is AdmissionState.RECOVERY_REQUIRED


# ---------------------------------------------------------------------------
# Test 6: freeze_admission_snapshot with stale evidence
# ---------------------------------------------------------------------------

def test_frozen_snapshot_reflects_stale_effective_states(
    services: tuple[HealthService, SqliteStateStore, FixedClock, SequentialIdSource, GovernanceBroker, PeerRegistryService],
) -> None:
    """freeze_admission_snapshot() with stale underlying evidence: frozen
    snapshot reflects the STALE/RECOVERY_REQUIRED effective states, not
    the raw stored (possibly stale-but-unmarked) ones."""
    health, store, clock, ids, _, _ = services

    # Seed fresh readiness.
    clock.value = 90_000
    _seed_projection_with_readiness(
        health, store, ids, readiness_observed_at=90_000
    )

    # Advance clock well past freshness window.
    clock.value = 100_000
    snapshot = health.freeze_admission_snapshot()

    entry = snapshot.entries[0]
    assert entry.availability_state is AvailabilityState.STALE
    assert entry.admission_state is AdmissionState.RECOVERY_REQUIRED


def test_frozen_snapshot_fresh_evidence_preserves_stored_states(
    services: tuple[HealthService, SqliteStateStore, FixedClock, SequentialIdSource, GovernanceBroker, PeerRegistryService],
) -> None:
    """freeze_admission_snapshot() with fresh evidence preserves the
    raw stored states."""
    health, store, clock, ids, _, _ = services

    # Seed readiness 10 minutes ago -- within freshness.
    clock.value = 100_000
    _seed_projection_with_readiness(
        health, store, ids, readiness_observed_at=99_400
    )

    snapshot = health.freeze_admission_snapshot()
    entry = snapshot.entries[0]
    assert entry.availability_state is AvailabilityState.HEALTHY
    assert entry.admission_state is AdmissionState.OPEN


# ---------------------------------------------------------------------------
# Test: read_health_projection returns None for absent projection
# ---------------------------------------------------------------------------

def test_read_health_projection_returns_none_for_absent(
    services: tuple[HealthService, SqliteStateStore, FixedClock, SequentialIdSource, GovernanceBroker, PeerRegistryService],
) -> None:
    """read_health_projection returns None when no projection exists,
    preserving fail-open behaviour."""
    health, _, _, _, _, _ = services
    assert health.read_health_projection("nonexistent", "nonexistent") is None
