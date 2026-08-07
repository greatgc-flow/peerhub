"""Integration tests for the composed Phase 1 runtime."""

import pytest
from pathlib import Path

from peerhub.core.context import RuntimeContext, PathLayout
from peerhub.runtime import create_runtime
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

    with create_runtime(context) as runtime:
        # Verify wiring of dependencies (identity checks)
        assert runtime.application_workflows._telemetry is runtime.telemetry_projector
        assert runtime.application_workflows._health is runtime.health_service
        assert runtime.application_workflows._routing is runtime.routing_service
        assert runtime.application_workflows._dispatch is runtime.dispatch_service
        
        assert runtime.health_service._telemetry is runtime.telemetry_projector
        assert runtime.governance_broker._store is runtime.state_store

        # Basic smoke test for cross-service interaction:
        # Since HealthService and DispatchService use the same underlying SQLite StateStore
        # and Clock, asking for the clock should yield the same underlying fake clock logic.
        
        # Test that they both point to the same database file
        assert runtime.dispatch_service._store.database_path == runtime.state_store.database_path
        
        # We can perform a basic smoke test by inspecting the components
        assert runtime.telemetry_projector is not None
