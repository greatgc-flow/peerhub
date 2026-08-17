"""Integration tests for record_usage_observations."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.telemetry.contract import (
    UsageMeasurement,
    UsageObserved,
)
from peerhub.telemetry.quota_polling import record_usage_observations

class DummyIdSource:
    def __init__(self, prefix: str = "id") -> None:
        self._count = 0
        self._prefix = prefix

    def new_id(self, prefix: str) -> str:
        self._count += 1
        return f"{prefix}-{self._prefix}-{self._count}"

@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "telemetry_quota_polling.sqlite3",
        workspace_home_id="workspace-telemetry",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()

def _make_measured_obs(
    obs_id: str,
    pool: str,
    used_frac: float,
    captured_at: int,
) -> UsageObserved:
    ev = EvidenceValue(
        state=EvidenceState.MEASURED,
        source_tag="test_src",
        provider_id="provider-1",
        provider_version="v1",
        observed_at=captured_at - 10,
        captured_at=captured_at,
        freshness_ttl=60,
        evidence_ref=EvidenceRef("ref-1"),
        value=UsageMeasurement(
            quota_pool_scope=pool,
            used_fraction=used_frac,
            remaining_fraction=1.0 - used_frac,
            window_started_at=100,
            resets_at=2000,
        ),
    )
    return UsageObserved(
        observation_id=obs_id,
        instance_id="inst-1",
        profile_id="prof-1",
        evidence=ev,
    )

def _make_error_obs(
    obs_id: str,
    captured_at: int,
    state: EvidenceState = EvidenceState.ERROR,
) -> UsageObserved:
    ev = EvidenceValue[UsageMeasurement](
        state=state,
        source_tag="test_src",
        provider_id="provider-1",
        provider_version="v1",
        observed_at=captured_at - 10,
        captured_at=captured_at,
        freshness_ttl=60,
        evidence_ref=EvidenceRef("ref-err"),
        value=None,
    )
    return UsageObserved(
        observation_id=obs_id,
        instance_id="inst-1",
        profile_id="prof-1",
        evidence=ev,
    )

def test_record_usage_observations_measured_new_pool(store: SqliteStateStore) -> None:
    """(a) a MEASURED observation for a NEW pool creates both the observation row and a revision-1 projection."""
    ids = DummyIdSource()
    obs = _make_measured_obs("obs-1", "pool-a", 0.1, 1000)

    with store.unit_of_work() as uow:
        record_usage_observations(uow, ids, [obs])
        uow.commit()

    with store.unit_of_work() as uow:
        # Verify observation
        saved_obs = uow.get_usage_observation("obs-1")
        assert saved_obs is not None
        assert saved_obs.evidence.state == EvidenceState.MEASURED

        # Verify projection
        proj = uow.get_usage_projection("inst-1", "prof-1", "pool-a")
        assert proj is not None
        assert proj.revision == 1
        assert proj.used_fraction == 0.1
        assert proj.updated_at == 1000
        assert proj.projection_id.startswith("usage-projection")

def test_record_usage_observations_measured_update(store: SqliteStateStore) -> None:
    """(b) a second MEASURED observation for the SAME pool updates the existing projection via CAS."""
    ids = DummyIdSource()
    obs1 = _make_measured_obs("obs-1", "pool-a", 0.1, 1000)
    obs2 = _make_measured_obs("obs-2", "pool-a", 0.2, 1050)

    with store.unit_of_work() as uow:
        record_usage_observations(uow, ids, [obs1])
        uow.commit()

    with store.unit_of_work() as uow:
        record_usage_observations(uow, ids, [obs2])
        uow.commit()

    with store.unit_of_work() as uow:
        saved_obs1 = uow.get_usage_observation("obs-1")
        saved_obs2 = uow.get_usage_observation("obs-2")
        assert saved_obs1 is not None
        assert saved_obs2 is not None

        proj = uow.get_usage_projection("inst-1", "prof-1", "pool-a")
        assert proj is not None
        assert proj.revision == 2
        assert proj.used_fraction == 0.2
        assert proj.updated_at == 1050

def test_record_usage_observations_absent_error(store: SqliteStateStore) -> None:
    """(c) an ABSENT/ERROR observation records the observation but does NOT create or modify any projection."""
    ids = DummyIdSource()
    obs_err = _make_error_obs("obs-err", 1000, EvidenceState.ERROR)
    obs_absent = _make_error_obs("obs-absent", 1050, EvidenceState.ABSENT)

    with store.unit_of_work() as uow:
        record_usage_observations(uow, ids, [obs_err, obs_absent])
        uow.commit()

    with store.unit_of_work() as uow:
        saved_err = uow.get_usage_observation("obs-err")
        assert saved_err is not None
        assert saved_err.evidence.state == EvidenceState.ERROR

        saved_abs = uow.get_usage_observation("obs-absent")
        assert saved_abs is not None
        assert saved_abs.evidence.state == EvidenceState.ABSENT

        # No projection should exist since we only had error/absent
        # We try to get for a dummy pool since the error obs has no pool
        proj = uow.get_usage_projection("inst-1", "prof-1", "pool-a")
        assert proj is None

def test_record_usage_observations_independent_pools(store: SqliteStateStore) -> None:
    """(d) two different quota_pool_scope values for the same instance_id/profile_id create two independent projections."""
    ids = DummyIdSource()
    obs1 = _make_measured_obs("obs-poolA", "pool-a", 0.1, 1000)
    obs2 = _make_measured_obs("obs-poolB", "pool-b", 0.8, 1050)

    with store.unit_of_work() as uow:
        record_usage_observations(uow, ids, [obs1, obs2])
        uow.commit()

    with store.unit_of_work() as uow:
        proj_a = uow.get_usage_projection("inst-1", "prof-1", "pool-a")
        assert proj_a is not None
        assert proj_a.quota_pool_scope == "pool-a"
        assert proj_a.used_fraction == 0.1
        assert proj_a.revision == 1

        proj_b = uow.get_usage_projection("inst-1", "prof-1", "pool-b")
        assert proj_b is not None
        assert proj_b.quota_pool_scope == "pool-b"
        assert proj_b.used_fraction == 0.8
        assert proj_b.revision == 1
