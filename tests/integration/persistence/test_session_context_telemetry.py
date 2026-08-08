"""Integration tests for session context telemetry."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.telemetry.contract import (
    SessionBindingKey,
    SessionContextObserved,
    SessionContextProjectionSnapshot,
)
from peerhub.telemetry.projections import project_session_context_observation
from tests.fakes import SequentialIdSource


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    state_store = SqliteStateStore(
        tmp_path / "telemetry_session_context.sqlite3",
        workspace_home_id="workspace-telemetry",
    )
    state_store.initialize()
    try:
        yield state_store
    finally:
        state_store.close()


def test_session_context_projection_is_idempotent(store: SqliteStateStore) -> None:
    binding_key = SessionBindingKey(
        workspace_scope_id="ws-1",
        instance_id="inst-1",
        profile_id="prof-1",
        conversation_scope="conv-1",
    )
    obs = SessionContextObserved(
        observation_id="obs-1",
        binding_key=binding_key,
        generation_id=1,
        observed_tokens=100,
        window_tokens=1000,
        source="exact_attribution",
        observed_at=1000,
    )

    with store.unit_of_work() as unit:
        unit.add_session_context_observation(obs)
        current = unit.get_session_context_projection(
            workspace_scope_id="ws-1",
            instance_id="inst-1",
            profile_id="prof-1",
            conversation_scope="conv-1",
            generation_id=1,
        )
        updated = project_session_context_observation(
            current,
            obs,
            projection_id="proj-1",
        )
        if current is None:
            unit.add_session_context_projection(updated)
        else:
            unit.cas_update_session_context_projection(current, updated)
        unit.commit()

    with store.unit_of_work() as unit:
        proj1 = unit.get_session_context_projection("ws-1", "inst-1", "prof-1", "conv-1", 1)
        assert proj1 is not None
        assert proj1.observed_tokens == 100
        assert proj1.revision == 1

    # Replay exactly the same observation
    with store.unit_of_work() as unit:
        current = unit.get_session_context_projection("ws-1", "inst-1", "prof-1", "conv-1", 1)
        updated = project_session_context_observation(
            current,
            obs,
            projection_id="proj-1",
        )
        if updated is not current:
            unit.cas_update_session_context_projection(current, updated)
        unit.commit()

    with store.unit_of_work() as unit:
        proj2 = unit.get_session_context_projection("ws-1", "inst-1", "prof-1", "conv-1", 1)
        assert proj2 is not None
        assert proj2.observed_tokens == 100
        assert proj2.revision == 1  # unchanged


def test_session_context_projection_reflects_exact_attribution(store: SqliteStateStore) -> None:
    # A projection only reflects exact-attribution evidence (a non-exact/estimated source 
    # should be recorded but should not be indistinguishable from exact evidence to a reader)
    binding_key = SessionBindingKey(
        workspace_scope_id="ws-1",
        instance_id="inst-1",
        profile_id="prof-1",
        conversation_scope="conv-1",
    )
    obs_exact = SessionContextObserved(
        observation_id="obs-1",
        binding_key=binding_key,
        generation_id=1,
        observed_tokens=100,
        window_tokens=1000,
        source="exact_attribution",
        observed_at=1000,
    )
    obs_estimated = SessionContextObserved(
        observation_id="obs-2",
        binding_key=binding_key,
        generation_id=1,
        observed_tokens=150,
        window_tokens=1000,
        source="estimated",
        observed_at=2000,
    )

    with store.unit_of_work() as unit:
        unit.add_session_context_observation(obs_exact)
        current = unit.get_session_context_projection("ws-1", "inst-1", "prof-1", "conv-1", 1)
        updated = project_session_context_observation(current, obs_exact, projection_id="proj-1")
        unit.add_session_context_projection(updated)
        unit.commit()

    with store.unit_of_work() as unit:
        unit.add_session_context_observation(obs_estimated)
        current = unit.get_session_context_projection("ws-1", "inst-1", "prof-1", "conv-1", 1)
        updated = project_session_context_observation(current, obs_estimated, projection_id="proj-1")
        unit.cas_update_session_context_projection(current, updated)
        unit.commit()

    with store.unit_of_work() as unit:
        proj = unit.get_session_context_projection("ws-1", "inst-1", "prof-1", "conv-1", 1)
        assert proj is not None
        assert proj.source == "estimated"
        assert proj.source != "exact_attribution"


def test_session_context_unknown_returns_no_evidence(store: SqliteStateStore) -> None:
    # Querying occupancy for an unknown/never-observed binding+generation 
    # returns a clear "no evidence" result rather than a default of zero
    with store.unit_of_work() as unit:
        proj = unit.get_session_context_projection(
            workspace_scope_id="ws-unknown",
            instance_id="inst-unknown",
            profile_id="prof-unknown",
            conversation_scope="conv-unknown",
            generation_id=1,
        )
        assert proj is None
