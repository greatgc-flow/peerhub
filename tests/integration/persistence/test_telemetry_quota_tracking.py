"""Integration tests for usage quota tracking telemetry."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.telemetry.contract import (
    UsageMeasurement,
    UsageObserved,
    UsageProjectionSnapshot,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "telemetry_quota_tracking.sqlite3",
        workspace_home_id="workspace-telemetry",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def test_usage_observation_round_trip(store: SqliteStateStore) -> None:
    ev = EvidenceValue(
        state=EvidenceState.MEASURED,
        source_tag="test_src",
        provider_id="provider-1",
        provider_version="v1",
        observed_at=1000,
        captured_at=1050,
        freshness_ttl=60,
        evidence_ref=EvidenceRef("ref-1"),
        value=UsageMeasurement(
            quota_pool_scope="session",
            used_fraction=0.25,
            remaining_fraction=0.75,
            window_started_at=500,
            resets_at=2000,
        ),
    )
    obs = UsageObserved(
        observation_id="obs-1",
        instance_id="inst-1",
        profile_id="prof-1",
        evidence=ev,
    )

    with store.unit_of_work() as unit:
        unit.add_usage_observation(obs)
        unit.commit()

    with store.unit_of_work() as unit:
        retrieved = unit.get_usage_observation("obs-1")
        assert retrieved is not None
        assert retrieved.observation_id == "obs-1"
        assert retrieved.instance_id == "inst-1"
        assert retrieved.profile_id == "prof-1"
        
        rev = retrieved.evidence
        assert rev.state == EvidenceState.MEASURED
        assert rev.source_tag == "test_src"
        
        val = rev.value
        assert val is not None
        assert val.quota_pool_scope == "session"
        assert val.used_fraction == 0.25
        assert val.remaining_fraction == 0.75
        assert val.window_started_at == 500
        assert val.resets_at == 2000


def test_usage_projection_cas_round_trip(store: SqliteStateStore) -> None:
    proj_v1 = UsageProjectionSnapshot(
        projection_id="proj-1",
        instance_id="inst-1",
        profile_id="prof-1",
        quota_pool_scope="session",
        used_fraction=0.25,
        remaining_fraction=0.75,
        window_started_at=500,
        resets_at=2000,
        revision=1,
        updated_at=1000,
    )

    with store.unit_of_work() as unit:
        unit.add_usage_projection(proj_v1)
        unit.commit()

    with store.unit_of_work() as unit:
        retrieved = unit.get_usage_projection("inst-1", "prof-1", "session")
        assert retrieved is not None
        assert retrieved.revision == 1
        assert retrieved.used_fraction == 0.25

        proj_v2 = UsageProjectionSnapshot(
            projection_id="proj-1",
            instance_id="inst-1",
            profile_id="prof-1",
            quota_pool_scope="session",
            used_fraction=0.50,
            remaining_fraction=0.50,
            window_started_at=500,
            resets_at=2000,
            revision=2,
            updated_at=1100,
        )
        assert unit.cas_update_usage_projection(retrieved, proj_v2) is True
        unit.commit()

    with store.unit_of_work() as unit:
        retrieved_v2 = unit.get_usage_projection("inst-1", "prof-1", "session")
        assert retrieved_v2 is not None
        assert retrieved_v2.revision == 2
        assert retrieved_v2.used_fraction == 0.50

        proj_stale = UsageProjectionSnapshot(
            projection_id="proj-1",
            instance_id="inst-1",
            profile_id="prof-1",
            quota_pool_scope="session",
            used_fraction=0.99,
            remaining_fraction=0.01,
            window_started_at=500,
            resets_at=2000,
            revision=3,
            updated_at=1200,
        )
        # Pass the old retrieved object (rev 1) while the DB is at rev 2
        assert unit.cas_update_usage_projection(retrieved, proj_stale) is False
        unit.commit()


def test_usage_projection_concurrent_pools_do_not_clobber(store: SqliteStateStore) -> None:
    session_proj = UsageProjectionSnapshot(
        projection_id="proj-session",
        instance_id="inst-1",
        profile_id="prof-1",
        quota_pool_scope="session",
        used_fraction=0.1,
        remaining_fraction=0.9,
        window_started_at=100,
        resets_at=200,
        revision=1,
        updated_at=150,
    )
    
    week_proj = UsageProjectionSnapshot(
        projection_id="proj-week",
        instance_id="inst-1",
        profile_id="prof-1",
        quota_pool_scope="week-all-models",
        used_fraction=0.8,
        remaining_fraction=0.2,
        window_started_at=100,
        resets_at=800,
        revision=1,
        updated_at=150,
    )

    with store.unit_of_work() as unit:
        unit.add_usage_projection(session_proj)
        unit.add_usage_projection(week_proj)
        unit.commit()

    with store.unit_of_work() as unit:
        retrieved_session = unit.get_usage_projection("inst-1", "prof-1", "session")
        retrieved_week = unit.get_usage_projection("inst-1", "prof-1", "week-all-models")
        
        assert retrieved_session is not None
        assert retrieved_session.quota_pool_scope == "session"
        assert retrieved_session.used_fraction == 0.1
        
        assert retrieved_week is not None
        assert retrieved_week.quota_pool_scope == "week-all-models"
        assert retrieved_week.used_fraction == 0.8
