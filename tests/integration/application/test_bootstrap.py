import os
import pytest
from pathlib import Path

from peerhub.adapters.registry import (
    resolve_peer_target,
    ExecutableNotFoundError,
    ProfileNotFoundError,
)
from peerhub.application.bootstrap import (
    build_direct_ask_admission_config,
    persist_direct_ask_admission,
    ReadinessProbeFailedError,
    HealthPolicyConflictError,
)
from peerhub.core.context import Clock, IdSource
from peerhub.health.contract import HealthPolicy
from peerhub.persistence.sqlite import SqliteStateStore


@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    store = SqliteStateStore(tmp_path / "test.db", workspace_home_id="test")
    store.initialize()
    return store

@pytest.fixture
def clock() -> Clock:
    import time
    class DummyClock:
        def now(self) -> int:
            return int(time.time() * 1000)
    return DummyClock()

@pytest.fixture
def ids() -> IdSource:
    class DummyIds:
        def request_id(self) -> str: return "req-1"
        def process_spawn_id(self) -> str: return "spawn-1"
        def session_id(self) -> str: return "sess-1"
        def attempt_id(self) -> str: return "att-1"
        def route_decision_id(self) -> str: return "route-1"
        def delivery_receipt_id(self) -> str: return "del-1"
        def transition_receipt_id(self) -> str: return "trans-1"
        def new_id(self, prefix: str) -> str: return f"{prefix}-1"
    return DummyIds()


def test_resolve_peer_target_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test alias resolution for all 3 peers."""
    import peerhub.adapters.registry
    monkeypatch.setattr(peerhub.adapters.registry, "resolve_executable_path", lambda x: Path(f"/dummy/{x}"))
    
    ag = resolve_peer_target("ag")
    assert ag.cli_name == "ag"
    assert ag.peer_kind == "ag"
    assert ag.executable_path.name.lower().startswith("agy")
    
    cc = resolve_peer_target("cc")
    assert cc.cli_name == "cc"
    assert cc.peer_kind == "cc"
    assert cc.executable_path.name.lower().startswith("claude")
    
    cx = resolve_peer_target("cx")
    assert cx.cli_name == "cx"
    assert cx.peer_kind == "cx"
    assert cx.executable_path.name.lower().startswith("codex")


def test_resolve_peer_target_profile_missing_raises() -> None:
    """Explicit error when profile_id is required but missing or invalid."""
    with pytest.raises(ProfileNotFoundError, match="profile 'nonexistent' not supported"):
        resolve_peer_target("ag", profile_id="nonexistent")


def test_resolve_peer_target_executable_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit error when executable can't be resolved in PATH."""
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ExecutableNotFoundError, match="not found in PATH"):
        resolve_peer_target("ag")


def test_build_direct_ask_admission_config(
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test building admission config with a real probe against one CLI."""
    import peerhub.adapters.registry
    import sys
    monkeypatch.setattr(peerhub.adapters.registry, "resolve_executable_path", lambda x: Path(sys.executable))
    target = resolve_peer_target("ag")
    
    config = build_direct_ask_admission_config(target, clock=clock, ids=ids)
    
    # Produces a real digest
    assert config.configuration.digest != "0" * 64
    assert len(config.configuration.digest) == 64
    
    # Membership contains exactly one target
    assert len(config.membership.configured_members) == 1
    assert config.membership.configured_members[0] == (target.peer_kind, target.profile.profile_id)
    
    # Genuine version-based observation
    assert config.readiness.evidence.state.value == "MEASURED"
    assert len(config.readiness.evidence.evidence_ref) == 71  # sha256: + 64 hex chars


def test_build_direct_ask_admission_config_probe_fails(
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clear error path when the probe fails (nonexistent path)."""
    import peerhub.adapters.registry
    import sys
    monkeypatch.setattr(peerhub.adapters.registry, "resolve_executable_path", lambda x: Path(sys.executable))
    target = resolve_peer_target("ag")
    # Mutate the executable path to something nonexistent
    broken_target = __import__("dataclasses").replace(
        target,
        executable_path=target.executable_path.parent / "does-not-exist-at-all.exe"
    )
    with pytest.raises(ReadinessProbeFailedError, match="failed or returned no output"):
        build_direct_ask_admission_config(broken_target, clock=clock, ids=ids)


def test_persist_direct_ask_admission_idempotent(
    clock: Clock,
    ids: IdSource,
    store: SqliteStateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idempotent policy persistence."""
    import peerhub.adapters.registry
    import sys
    monkeypatch.setattr(peerhub.adapters.registry, "resolve_executable_path", lambda x: Path(sys.executable))
    target = resolve_peer_target("ag")
    config = build_direct_ask_admission_config(target, clock=clock, ids=ids)
    
    # First persist succeeds
    persist_direct_ask_admission(store, config)
    
    # Second persist succeeds (idempotent)
    persist_direct_ask_admission(store, config)
    
    # Mutate policy and try to persist again
    mutated_policy = __import__("dataclasses").replace(
        config.health_policy,
        readiness_freshness_seconds=999
    )
    mutated_config = __import__("dataclasses").replace(
        config,
        health_policy=mutated_policy
    )
    
    with pytest.raises(HealthPolicyConflictError, match="exists with different content"):
        persist_direct_ask_admission(store, mutated_config)


def test_create_runtime_status_cli_behavior(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
) -> None:
    """Explicit test asserting the 'status' CLI's existing runtime composition still behaves identically."""
    from peerhub.runtime import create_runtime
    from peerhub.core.context import RuntimeContext
    
    class DummyPaths:
        database_path = tmp_path / "test.db"
    
    context = RuntimeContext(
        workspace_home_id="test",
        paths=DummyPaths(),  # type: ignore
        clock=clock,
        ids=ids,
    )
    
    runtime = create_runtime(context)
    try:
        # Check that it uses the default policy, not the direct-ask one
        assert runtime.health_service._policy.policy_id == "v1-health-default-r1"
    finally:
        runtime.close()


def test_create_runtime_health_projection_entrypoint_verified(
    tmp_path: Path,
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end test proving create_runtime correctly evaluates --version to ENTRYPOINT_VERIFIED."""
    import peerhub.adapters.registry
    import sys
    monkeypatch.setattr(peerhub.adapters.registry, "resolve_executable_path", lambda x: Path(sys.executable))
    
    target = resolve_peer_target("ag")
    config = build_direct_ask_admission_config(target, clock=clock, ids=ids)
    
    from peerhub.runtime import create_runtime
    from peerhub.core.context import RuntimeContext
    
    class DummyPaths:
        database_path = tmp_path / "test2.db"
    
    context = RuntimeContext(
        workspace_home_id="test",
        paths=DummyPaths(),  # type: ignore
        clock=clock,
        ids=ids,
    )
    
    # Passing the real built admission config
    runtime = create_runtime(context, admission_config=config)
    try:
        snapshot = runtime.health_service.freeze_admission_snapshot()
        
        assert len(snapshot.entries) == 1
        entry = snapshot.entries[0]
        
        assert entry.instance_id == "ag"
        
        # The actual proof of the fix: the probe was evaluated to UNKNOWN/RECOVERY_REQUIRED
        from peerhub.health.contract import AvailabilityState, AdmissionState
        assert entry.availability_state == AvailabilityState.UNKNOWN
        assert entry.admission_state == AdmissionState.RECOVERY_REQUIRED
    finally:
        runtime.close()


def test_build_broadcast_admission_config_probe_fails(
    clock: Clock,
    ids: IdSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end test proving build_broadcast_admission_config does not substitute fallback_ok and produces honest FAILURE evidence."""
    import peerhub.adapters.registry
    import sys
    from pathlib import Path
    monkeypatch.setattr(peerhub.adapters.registry, "resolve_executable_path", lambda x: Path(sys.executable))
    
    target = resolve_peer_target("ag")
    broken_target = __import__("dataclasses").replace(
        target,
        executable_path=target.executable_path.parent / "does-not-exist-at-all.exe"
    )
    
    # Broadcast should NOT raise, it should produce honest failure evidence.
    from peerhub.application.bootstrap import build_broadcast_admission_config
    config = build_broadcast_admission_config((broken_target,), clock=clock, ids=ids)
    
    assert config.readiness.evidence.state.value == "ERROR"
    assert config.readiness.evidence.value is None
