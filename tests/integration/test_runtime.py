"""Integration tests for the composed Phase 1 runtime."""

import pytest
import sqlite3
from pathlib import Path

import pytest

from peerhub.adapters.codex_adapter import RealCodexAdapter
from peerhub.builtins.fake_adapter import FakePeerAdapter
from peerhub.core.context import RuntimeContext, PathLayout
from peerhub.runtime import create_read_runtime, create_runtime
from tests.fakes import DeterministicClock, SequentialIdSource

def test_composed_runtime_initializes_and_wires_services(tmp_path: Path):
    """
    Test that create_runtime successfully composes all services,
    shares the underlying StateStore correctly, and wires dependencies
    to each other appropriately.
    """
    context = RuntimeContext(
        workspace_home_id="test-workspace",
        paths=PathLayout.for_workspace(tmp_path),
        clock=DeterministicClock(),
        ids=SequentialIdSource(),
    )
    assert context.paths.workspace_config_home == tmp_path / ".peerhub" / "config"

    with create_runtime(context) as runtime:
        # Verify wiring of dependencies (identity checks)
        assert runtime.application_workflows._telemetry is runtime.telemetry_projector
        assert runtime.application_workflows._health is runtime.health_service
        assert runtime.application_workflows._routing is runtime.routing_service
        assert runtime.application_workflows._dispatch is runtime.dispatch_service
        assert isinstance(runtime.peer_adapter, FakePeerAdapter)
        assert runtime.application_workflows._peer_adapter is runtime.peer_adapter
        
        assert runtime.health_service._telemetry is runtime.telemetry_projector
        assert runtime.governance_broker._store is runtime.state_store

        # Basic smoke test for cross-service interaction:
        # Since HealthService and DispatchService use the same underlying SQLite StateStore
        # and Clock, asking for the clock should yield the same underlying fake clock logic.
        
        # Test that they both point to the same database file
        assert runtime.dispatch_service._store.database_path == runtime.state_store.database_path
        
        # We can perform a basic smoke test by inspecting the components
        assert runtime.telemetry_projector is not None


def test_create_runtime_selects_requested_real_adapter(tmp_path: Path):
    context = RuntimeContext(
        workspace_home_id="test-workspace-real-adapter",
        paths=PathLayout.for_workspace(tmp_path),
        clock=DeterministicClock(),
        ids=SequentialIdSource(),
    )

    with create_runtime(context, adapter_peer_kind="cx") as runtime:
        assert isinstance(runtime.peer_adapter, RealCodexAdapter)
        assert runtime.application_workflows._peer_adapter is runtime.peer_adapter


def test_create_read_runtime_does_not_seed_policy(tmp_path: Path) -> None:
    context = RuntimeContext(
        workspace_home_id="read-only-workspace",
        paths=PathLayout.for_workspace(tmp_path),
        clock=DeterministicClock(),
        ids=SequentialIdSource(),
    )
    with create_runtime(context):
        pass

    with sqlite3.connect(context.paths.database_path) as connection:
        connection.execute("DELETE FROM health_policy_revisions")
        connection.commit()

    with create_read_runtime(context):
        pass

    with sqlite3.connect(context.paths.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM health_policy_revisions"
        ).fetchone() == (0,)


def test_create_read_runtime_does_not_create_missing_store(tmp_path: Path) -> None:
    context = RuntimeContext(
        workspace_home_id="missing-read-only-workspace",
        paths=PathLayout.for_workspace(tmp_path),
        clock=DeterministicClock(),
        ids=SequentialIdSource(),
    )

    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        create_read_runtime(context)

    assert not context.paths.workspace_home.exists()
