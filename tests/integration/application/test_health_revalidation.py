import pytest
from pathlib import Path
import sys

from peerhub.core.context import Clock, IdSource, RuntimeContext
from peerhub.runtime import create_runtime
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.adapters.registry import resolve_peer_target
from peerhub.application.health_revalidation import HealthRevalidationCoordinator
from peerhub.core.identity import LocalProcessCallerIdentityProvider, require_caller_identity
from peerhub.core.errors import InvalidMutationError
from peerhub.health.contract import AdmissionState, AvailabilityState, ProbeResult, PolicyScope

@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    store = SqliteStateStore(tmp_path / "test.db", workspace_home_id="test")
    store.initialize()
    return store

@pytest.fixture
def clock() -> Clock:
    class DummyClock:
        def __init__(self):
            self._now = 1000
        def now(self) -> int:
            return self._now
        def advance(self, ms: int) -> None:
            self._now += ms
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
        def new_id(self, namespace: str) -> str: return f"{namespace}-1"
    return DummyIds()

@pytest.fixture
def runtime_ctx(tmp_path: Path, clock, ids):
    class DummyPaths:
        database_path = tmp_path / "test.db"
    
    return RuntimeContext(
        workspace_home_id="test",
        paths=DummyPaths(),
        clock=clock,
        ids=ids,
    )


def create_runtime_with_ag(runtime_ctx, monkeypatch):
    from peerhub.application.bootstrap import build_direct_ask_admission_config
    import peerhub.adapters.registry
    import dataclasses
    from peerhub.core.evidence import EvidenceValue, EvidenceState, EvidenceRef
    from peerhub.telemetry.contract import ReadinessMeasurement

    target = peerhub.adapters.registry.resolve_peer_target("ag")
    admission_config = build_direct_ask_admission_config(
        target,
        clock=runtime_ctx.clock,
        ids=runtime_ctx.ids,
    )
    
    new_evidence = EvidenceValue(
        state=EvidenceState.MEASURED,
        source_tag="empirical_probe",
        provider_id="phase0-readiness",
        provider_version="1",
        observed_at=runtime_ctx.clock.now(),
        captured_at=runtime_ctx.clock.now(),
        freshness_ttl=7200,
        evidence_ref=EvidenceRef("sha256:" + ("a" * 64)),
        value=ReadinessMeasurement(
            runtime_revision="unknown",
            issued_at=runtime_ctx.clock.now(),
            valid_until=runtime_ctx.clock.now() + 7200,
            integrity_verified=True,
        ),
    )
    new_readiness = dataclasses.replace(admission_config.readiness, evidence=new_evidence)
    admission_config = dataclasses.replace(admission_config, readiness=new_readiness)
    
    return create_runtime(
        runtime_ctx,
        adapter_peer_kind="ag",
        admission_config=admission_config,
    )

def test_healthy_circuit_reconfirmation(runtime_ctx, monkeypatch: pytest.MonkeyPatch):
    import peerhub.adapters.registry
    monkeypatch.setattr(peerhub.adapters.registry, "_resolve_executable_path", lambda x: Path(sys.executable))
    
    with create_runtime_with_ag(runtime_ctx, monkeypatch) as runtime:
        coordinator = HealthRevalidationCoordinator(
            registry=runtime.peer_registry_service,
            health=runtime.health_service,
            clock=runtime_ctx.clock,
            ids=runtime_ctx.ids,
        )
        caller = require_caller_identity(LocalProcessCallerIdentityProvider())
        
        # Fresh db, it's ADMITTED by default
        result = coordinator.request_revalidation(
            peer_node_id="ag",
            caller=caller,
            reason="test",
            requested_at=runtime_ctx.clock.now()
        )
        
        assert result.probe_outcome == ProbeResult.SUCCESS
        assert result.admission_state == AdmissionState.RECOVERY_REQUIRED
        assert result.circuit_closed is False


def test_automatic_recovery_authorization_first(runtime_ctx, monkeypatch: pytest.MonkeyPatch):
    import peerhub.adapters.registry
    monkeypatch.setattr(peerhub.adapters.registry, "_resolve_executable_path", lambda x: Path(sys.executable))
    
    with create_runtime_with_ag(runtime_ctx, monkeypatch) as runtime:
        coordinator = HealthRevalidationCoordinator(
            registry=runtime.peer_registry_service,
            health=runtime.health_service,
            clock=runtime_ctx.clock,
            ids=runtime_ctx.ids,
        )
        caller = require_caller_identity(LocalProcessCallerIdentityProvider())
        
        # Open circuit so we have a circuit to recover
        from peerhub.health.contract import HealthStageObservation, HealthStage, HealthStageStatus, EvidenceSubject, PolicyScope, PolicyReceipt
        runtime.health_service.classify_and_open_circuit(
            (
                HealthStageObservation(
                    stage=HealthStage.VALIDATE_ENVIRONMENT,
                    status=HealthStageStatus.FAILED,
                ),
            ),
            evidence_subject=EvidenceSubject(
                scope=PolicyScope.PROFILE,
                subject="ag.standard",
            ),
            receipt=PolicyReceipt(incident="test-incident", gate_generation=1, timestamp=100, fingerprint="fp"),
        )
        # Advance clock to make it stale -> RECOVERY_REQUIRED
        runtime_ctx.clock.advance(1_000_000)
        
        result = coordinator.request_revalidation(
            peer_node_id="ag",
            caller=caller,
            reason="test auto recovery",
            requested_at=runtime_ctx.clock.now()
        )
        
        # Should have successfully claimed a probe and applied success
        assert result.probe_outcome == ProbeResult.SUCCESS
        assert result.admission_state == AdmissionState.RECOVERY_REQUIRED
        assert result.circuit_closed is True


def test_administrative_authorization_first(runtime_ctx, monkeypatch: pytest.MonkeyPatch):
    import peerhub.adapters.registry
    monkeypatch.setattr(peerhub.adapters.registry, "_resolve_executable_path", lambda x: Path(sys.executable))
    
    with create_runtime_with_ag(runtime_ctx, monkeypatch) as runtime:
        coordinator = HealthRevalidationCoordinator(
            registry=runtime.peer_registry_service,
            health=runtime.health_service,
            clock=runtime_ctx.clock,
            ids=runtime_ctx.ids,
        )
        caller = require_caller_identity(LocalProcessCallerIdentityProvider())
        
        # Manually quarantine (open circuit) using a raw reducer to force QUARANTINED
        from peerhub.health.contract import PolicyScope, PolicyReceipt, PolicyAction, AdmissionState, QuarantineAuthorityClass, CircuitState
        from peerhub.health.model import apply_policy_action
        action = PolicyAction(
            scope=PolicyScope.PROFILE,
            subject="ag.standard",
            circuit_state=CircuitState.CIRCUIT_OPEN,
            quarantine_authority_class=QuarantineAuthorityClass.MANUAL,
            receipt=PolicyReceipt(incident="test", gate_generation=1, timestamp=100, fingerprint="fp"),
        )
        with runtime.health_service._store.unit_of_work() as unit:
            updated = apply_policy_action(action, None, circuit_id="test-circuit", created_at=100, updated_at=100)
            runtime.health_service._write_circuit(unit, None, updated)
            unit.commit()
        
        result = coordinator.request_revalidation(
            peer_node_id="ag",
            caller=caller,
            reason="test admin recovery",
            requested_at=runtime_ctx.clock.now()
        )
        
        assert result.probe_outcome == ProbeResult.SUCCESS
        assert result.admission_state == AdmissionState.QUARANTINED
        assert result.circuit_closed is False


def test_probe_failure_produces_honest_evidence_and_does_not_close_circuit(runtime_ctx, monkeypatch: pytest.MonkeyPatch):
    import peerhub.adapters.registry
    monkeypatch.setattr(peerhub.adapters.registry, "_resolve_executable_path", lambda x: Path(sys.executable))
    
    with create_runtime_with_ag(runtime_ctx, monkeypatch) as runtime:
        coordinator = HealthRevalidationCoordinator(
            registry=runtime.peer_registry_service,
            health=runtime.health_service,
            clock=runtime_ctx.clock,
            ids=runtime_ctx.ids,
        )
        caller = require_caller_identity(LocalProcessCallerIdentityProvider())
        
        # Break the executable path so the probe fails honestly
        target = resolve_peer_target("ag")
        broken_path = target.executable_path.parent / "does-not-exist-at-all.exe"
        monkeypatch.setattr(peerhub.adapters.registry, "_resolve_executable_path", lambda x: broken_path)
        
        # Open circuit so we have a circuit to recover
        from peerhub.health.contract import HealthStageObservation, HealthStage, HealthStageStatus, EvidenceSubject, PolicyScope, PolicyReceipt
        runtime.health_service.classify_and_open_circuit(
            (
                HealthStageObservation(
                    stage=HealthStage.VALIDATE_ENVIRONMENT,
                    status=HealthStageStatus.FAILED,
                ),
            ),
            evidence_subject=EvidenceSubject(
                scope=PolicyScope.PROFILE,
                subject="ag.standard",
            ),
            receipt=PolicyReceipt(incident="test-incident", gate_generation=1, timestamp=100, fingerprint="fp"),
        )
        # Advance clock to make it stale -> RECOVERY_REQUIRED
        runtime_ctx.clock.advance(1_000_000)
        
        result = coordinator.request_revalidation(
            peer_node_id="ag",
            caller=caller,
            reason="test fail",
            requested_at=runtime_ctx.clock.now()
        )
        
        # Probe fails, but no exception. Result reflects failure.
        assert result.probe_outcome == ProbeResult.FAILURE
        # Since it failed and consumed a grant, it goes to COOLDOWN.
        assert result.admission_state == AdmissionState.COOLDOWN
        assert result.circuit_closed is False
        
        # If we try again while in COOLDOWN, it raises InvalidMutationError
        with pytest.raises(InvalidMutationError):
            coordinator.request_revalidation(
                peer_node_id="ag",
                caller=caller,
                reason="test cooldown fail",
                requested_at=runtime_ctx.clock.now()
            )

def test_cli_smoke_native_health_revalidate(runtime_ctx, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from peerhub.cli import main
    import peerhub.adapters.registry
    monkeypatch.setattr(peerhub.adapters.registry, "_resolve_executable_path", lambda x: Path(sys.executable))
    
    workspace = tmp_path
    from peerhub.core.context import PathLayout
    layout = PathLayout.for_workspace(workspace)
    layout.database_path.parent.mkdir(parents=True, exist_ok=True)
    import dataclasses
    ctx = dataclasses.replace(runtime_ctx, paths=layout, workspace_home_id=layout.workspace_home.name)
    with create_runtime_with_ag(ctx, monkeypatch) as runtime:
        from peerhub.health.contract import HealthStageObservation, HealthStage, HealthStageStatus, EvidenceSubject, PolicyScope, PolicyReceipt
        runtime.health_service.classify_and_open_circuit(
            (
                HealthStageObservation(
                    stage=HealthStage.VALIDATE_ENVIRONMENT,
                    status=HealthStageStatus.FAILED,
                ),
            ),
            evidence_subject=EvidenceSubject(
                scope=PolicyScope.PROFILE,
                subject="ag.standard",
            ),
            receipt=PolicyReceipt(incident="test-incident", gate_generation=1, timestamp=100, fingerprint="fp"),
        )
        
    code = main(["health", "revalidate", "--workspace", str(workspace), "--peer", "ag", "--reason", "smoke", "--json"])
    assert code == 0
