"""Integration tests for telemetry usage measurement round-trip."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import OperationalFailureCategory
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.telemetry.contract import (
    OperationalProjectionSnapshot,
    UsageMeasurement,
)

@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "telemetry_usage.sqlite3",
        workspace_home_id="workspace-telemetry",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()

def test_usage_measurement_roundtrips(store: SqliteStateStore) -> None:
    # 1. Construct a valid UsageMeasurement inside an EvidenceValue
    usage_val = UsageMeasurement(
        quota_pool_scope="global-api",
        used_fraction=0.85,
        remaining_fraction=0.15,
        window_started_at=1000000,
        resets_at=2000000,
    )
    
    usage_ev = EvidenceValue(
        state=EvidenceState.MEASURED,
        source_tag="cli_live",
        provider_id="system-probe",
        provider_version="1.0.0",
        observed_at=1234567,
        captured_at=1234568,
        freshness_ttl=3600,
        evidence_ref=EvidenceRef("ref-123"),
        value=usage_val,
    )

    # 2. Construct the snapshot
    snapshot = OperationalProjectionSnapshot(
        projection_id="proj-123",
        instance_id="inst-1",
        profile_id="prof-1",
        failure_category=EvidenceValue(
            state=EvidenceState.ABSENT,
            source_tag="none",
            provider_id="none",
            provider_version="none",
            observed_at=0,
            captured_at=0,
            freshness_ttl=0,
            evidence_ref=EvidenceRef("none"),
            value=None,
        ),
        process_integrity=EvidenceValue(
            state=EvidenceState.ABSENT,
            source_tag="none",
            provider_id="none",
            provider_version="none",
            observed_at=0,
            captured_at=0,
            freshness_ttl=0,
            evidence_ref=EvidenceRef("none"),
            value=None,
        ),
        latency=EvidenceValue(
            state=EvidenceState.ABSENT,
            source_tag="none",
            provider_id="none",
            provider_version="none",
            observed_at=0,
            captured_at=0,
            freshness_ttl=0,
            evidence_ref=EvidenceRef("none"),
            value=None,
        ),
        usage=usage_ev,
        failure_streak=0,
        last_terminal_at=0,
        evidence_refs=(),
        updated_at=1234568,
        revision=1,
    )

    # 3. Add to store
    with store.unit_of_work() as uow:
        uow.telemetry.add_operational_projection(snapshot)
        uow.commit()

    # 4. Read it back
    with store.unit_of_work() as uow:
        retrieved = uow.telemetry.get_operational_projection("inst-1", "prof-1")

    # 5. Assert round-trip
    assert retrieved is not None
    assert retrieved.usage.value is not None
    
    ret_val = retrieved.usage.value
    assert ret_val.quota_pool_scope == "global-api"
    assert ret_val.used_fraction == 0.85
    assert ret_val.remaining_fraction == 0.15
    assert ret_val.window_started_at == 1000000
    assert ret_val.resets_at == 2000000
