"""Integration test suite for the 6 reclassified health quick win actions:
- configuration.peer.status / peer-status
- health.check / health-check
- health.peer.recover / peer-recover
- health.precheck / health-precheck
- health.gate.check / check-gate
- health.sweep / health-sweep
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Mapping, cast
import pytest

from peerhub.application.api import (
    ApplicationAPI,
    CommandEnvelope,
)
from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import (
    LegacyActionCall,
    LegacyTranslator,
    TranslatedCommand,
)
from peerhub.application.peer_registry import (
    PeerRegistryService,
    collect_peer_status,
)
from peerhub.application.health_revalidation import (
    HealthRevalidationCoordinator,
    collect_health_check,
    execute_peer_recover,
    collect_health_precheck,
    collect_check_gate,
    collect_health_sweep,
)
from peerhub.client import Client
from peerhub.cli import main
from peerhub.core.context import Clock, IdSource, RuntimeContext
from peerhub.core.evidence import EvidenceRef, EvidenceState, EvidenceValue
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.ports import RequestContext
from peerhub.core.protocol import CommandSuccess
from peerhub.governance.broker import GovernanceBroker
from peerhub.health.contract import (
    AdmissionState,
    AvailabilityState,
    CircuitState,
    HealthPolicy,
    HealthScopeMembershipSnapshot,
    PolicyAction,
    PolicyReceipt,
    PolicyScope,
    ProbeResult,
    QuarantineAuthorityClass,
)
from peerhub.health.model import apply_policy_action
from peerhub.health.service import HealthService
from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.routing.contract import ConfigurationSnapshot
from peerhub.runtime import create_runtime
from peerhub.telemetry.contract import ReadinessMeasurement, ReadinessObserved
from tests.fakes import SequentialIdSource

# The 3 peers exercised across this suite's multi-peer scenarios
# (peer-status/health-sweep display all of them; single-peer tests
# only ever touch "ag"). Declared once so the membership snapshot
# below and _record_healthy_evidence's instance_id=peer_kind calls
# stay in lockstep. instance_id == peer_kind matches this codebase's
# real single-instance-per-kind model (see the already-shipped
# HealthRevalidationCoordinator.request_revalidation(), which reads
# the same peer_kind straight from the registry node as the
# instance_id for read_health_projection()).
_QUICKWIN_MEMBERS: tuple[tuple[str, str], ...] = (
    ("ag", "ag.standard"),
    ("cc", "cc.standard"),
    ("cx", "cx.standard"),
)


class FixedClock(Clock):
    def __init__(self, value: int = 100_000) -> None:
        self.value = value

    def now(self) -> int:
        return self.value

    def advance(self, ms: int) -> None:
        self.value += ms


def _quickwin_admission_config(observed_at: int) -> "DirectAskAdmissionConfig":
    """Build a DirectAskAdmissionConfig whose membership pre-registers
    the 3 synthetic peers this suite records readiness for -- without
    this, HealthService.evaluate_and_persist_readiness() rejects every
    call with InvalidMutationError("... not present in the injected
    configuration population"), since create_runtime() defaults to an
    EMPTY membership when no admission_config is supplied."""
    from peerhub.application.bootstrap import DirectAskAdmissionConfig

    digest = "0" * 64
    policy = HealthPolicy(
        policy_id="quickwin-test-policy",
        revision=1,
        readiness_freshness_seconds=7200,
        recovery_backoff_seconds=(30, 60, 120, 240, 480, 900),
        recovery_jitter_fraction=0.2,
        readiness_observation_threshold=1,
        # Generous on purpose: this suite's "recover all" scenarios
        # quarantine several peers and expect a single sweep to clear
        # all of them. The real per-workspace administrative-recovery
        # budget (a genuine cross-peer throttle -- see
        # AdministrativeRecoveryBudgetSnapshot in health/service.py)
        # would otherwise correctly reject the 2nd+ recovery in the
        # same round with AdministrativeRecoveryBudgetExceededError.
        administrative_recovery_probe_limit=10,
    )
    membership = HealthScopeMembershipSnapshot(
        configuration_revision=1,
        configuration_digest=digest,
        configured_members=_QUICKWIN_MEMBERS,
        bindings=(),
    )
    placeholder_readiness = ReadinessObserved(
        observation_id=f"obs-bootstrap-{observed_at}",
        instance_id="ag",
        profile_id="ag.standard",
        evidence=EvidenceValue(
            state=EvidenceState.MEASURED,
            source_tag="empirical_probe",
            provider_id="phase0-readiness",
            provider_version="1",
            observed_at=observed_at,
            captured_at=observed_at,
            freshness_ttl=7200,
            evidence_ref=EvidenceRef("sha256:" + ("a" * 64)),
            value=ReadinessMeasurement(
                runtime_revision="1.0.0",
                issued_at=observed_at,
                valid_until=observed_at + 60_000,
                integrity_verified=True,
            ),
        ),
    )
    return DirectAskAdmissionConfig(
        configuration=ConfigurationSnapshot(revision=1, digest=digest),
        health_policy=policy,
        membership=membership,
        readiness=placeholder_readiness,
    )


@pytest.fixture
def test_setup(tmp_path: Path):
    class DummyPaths:
        database_path = tmp_path / "peerhub.sqlite3"
        workspace_root = tmp_path

    clock = FixedClock(value=100_000)
    ids = SequentialIdSource()
    ctx = RuntimeContext(
        workspace_home_id="quickwin-test",
        paths=DummyPaths(),
        clock=clock,
        ids=ids,
    )
    runtime = create_runtime(
        ctx,
        adapter_peer_kind="fake",
        admission_config=_quickwin_admission_config(clock.now()),
    )
    caller = AuthenticatedSubject("test-principal", "system")

    return {
        "tmp_path": tmp_path,
        "runtime": runtime,
        "store": runtime.state_store,
        "clock": clock,
        "ids": ids,
        "registry": runtime.peer_registry_service,
        "health": runtime.health_service,
        "coordinator": runtime.health_revalidation_coordinator,
        "caller": caller,
    }


def _record_healthy_evidence(
    health: HealthService,
    peer_kind: str,
    profile_id: str,
    observed_at: int,
) -> None:
    health.evaluate_and_persist_readiness(
        ReadinessObserved(
            observation_id=f"obs-{peer_kind}-{observed_at}",
            instance_id=peer_kind,
            profile_id=profile_id,
            evidence=EvidenceValue(
                state=EvidenceState.MEASURED,
                source_tag="empirical_probe",
                provider_id="phase0-readiness",
                provider_version="1",
                observed_at=observed_at,
                captured_at=observed_at,
                freshness_ttl=7200,
                evidence_ref=EvidenceRef("sha256:" + ("a" * 64)),
                value=ReadinessMeasurement(
                    runtime_revision="1.0.0",
                    issued_at=observed_at,
                    valid_until=observed_at + 60_000,
                    integrity_verified=True,
                ),
            ),
        ),
        sealed_runtime_revision="1.0.0",
        adapter_declares_probe_safe=True,
    )


def _mock_successful_probe(monkeypatch: pytest.MonkeyPatch, clock: FixedClock) -> None:
    """Patch HealthRevalidationCoordinator's actual probe seam so
    request_revalidation()'s internal call succeeds without a real
    subprocess. `produce_readiness_evidence` (imported by name into
    peerhub.application.health_revalidation) -- NOT a nonexistent
    `coordinator.execute_probe` -- is what request_revalidation()
    actually calls; must be patched where it's looked up (module-level
    name in health_revalidation, not its origin in bootstrap)."""

    import peerhub.application.health_revalidation as health_revalidation_module

    def fake_produce_readiness_evidence(target: Any, *, clock: Clock, ids: Any) -> ReadinessObserved:
        now = clock.now()
        return ReadinessObserved(
            observation_id=f"obs-mock-{target.peer_kind}-{now}",
            instance_id=target.peer_kind,
            profile_id=target.profile.profile_id,
            evidence=EvidenceValue(
                state=EvidenceState.MEASURED,
                source_tag="empirical_probe",
                provider_id="phase0-readiness",
                provider_version="1",
                observed_at=now,
                captured_at=now,
                freshness_ttl=7200,
                evidence_ref=EvidenceRef("sha256:" + ("b" * 64)),
                value=ReadinessMeasurement(
                    runtime_revision="1.0.0",
                    issued_at=now,
                    valid_until=now + 60_000,
                    integrity_verified=True,
                ),
            ),
        )

    monkeypatch.setattr(
        health_revalidation_module,
        "produce_readiness_evidence",
        fake_produce_readiness_evidence,
    )


def _write_test_circuit(
    health: HealthService,
    peer_kind: str,
    profile_id: str,
    clock: FixedClock,
    *,
    authority_class: QuarantineAuthorityClass,
    opened_at: int,
) -> None:
    """Write a real HealthCircuitSnapshot -- NOT just the projection's
    admission_state -- since HealthService.authorize_administrative_recovery()
    / authorize_recovery() (called by HealthRevalidationCoordinator during
    peer-recover / health-check --recover) look up the circuit table
    directly (unit.get_health_circuit(scope, subject)) independently of
    read_health_projection()'s own admission_state derivation. Follows
    the proven pattern from test_health_revalidation.py's
    test_administrative_authorization_first."""

    action = PolicyAction(
        scope=PolicyScope.PROFILE,
        subject=profile_id,
        circuit_state=CircuitState.CIRCUIT_OPEN,
        quarantine_authority_class=authority_class,
        receipt=PolicyReceipt(
            incident=f"test-{peer_kind}",
            gate_generation=1,
            timestamp=opened_at,
            fingerprint=f"fp-{peer_kind}",
        ),
    )
    with health._store.unit_of_work() as unit:  # noqa: SLF001 -- test-only, mirrors the shipped precedent
        updated = apply_policy_action(
            action,
            None,
            circuit_id=f"test-circuit-{peer_kind}",
            created_at=opened_at,
            updated_at=opened_at,
        )
        health._write_circuit(unit, None, updated)  # noqa: SLF001
        unit.commit()


def _quarantine_peer(
    health: HealthService,
    store: SqliteStateStore,
    peer_kind: str,
    profile_id: str,
    clock: FixedClock,
) -> None:
    """Simulate a manually-quarantined (operator-authority) peer: real
    circuit MANUAL + CIRCUIT_OPEN (never self-clears -- read side stays
    QUARANTINED regardless of elapsed time), plus the matching
    projection admission_state for direct read_health_projection()
    callers (collect_check_gate/collect_health_sweep/etc.)."""

    _record_healthy_evidence(health, peer_kind, profile_id, clock.now())
    _write_test_circuit(
        health,
        peer_kind,
        profile_id,
        clock,
        authority_class=QuarantineAuthorityClass.MANUAL,
        opened_at=clock.now(),
    )
    with store.unit_of_work() as uow:
        stored = uow.get_health_projection(peer_kind, profile_id)
        assert stored is not None
        updated = replace(
            stored,
            admission_state=AdmissionState.QUARANTINED,
            availability_state=AvailabilityState.UNAVAILABLE,
            revision=stored.revision + 1,
            updated_at=clock.now(),
        )
        uow.cas_update_health_projection(stored, updated)
        uow.commit()


def _degrade_peer(
    health: HealthService,
    store: SqliteStateStore,
    peer_kind: str,
    profile_id: str,
    clock: FixedClock,
) -> None:
    """Simulate an automatic (self-clearing) circuit already past its
    first backoff stage (30s) as of clock.now(), so its real admission
    state resolves to RECOVERY_REQUIRED rather than COOLDOWN -- see
    CooldownEvaluation in peerhub/health/model.py."""

    _record_healthy_evidence(health, peer_kind, profile_id, clock.now())
    _write_test_circuit(
        health,
        peer_kind,
        profile_id,
        clock,
        authority_class=QuarantineAuthorityClass.AUTOMATIC,
        opened_at=clock.now() - 40_000,
    )
    with store.unit_of_work() as uow:
        stored = uow.get_health_projection(peer_kind, profile_id)
        assert stored is not None
        updated = replace(
            stored,
            availability_state=AvailabilityState.DEGRADED,
            admission_state=AdmissionState.RECOVERY_REQUIRED,
            revision=stored.revision + 1,
            updated_at=clock.now(),
        )
        uow.cas_update_health_projection(stored, updated)
        uow.commit()


def test_peer_status_registered_and_base_nodes(test_setup):
    registry: PeerRegistryService = test_setup["registry"]
    health: HealthService = test_setup["health"]
    clock: FixedClock = test_setup["clock"]

    # Register an explicit custom node
    registry.register_node(
        node_id="custom-worker",
        peer_kind="ag",
        profile_id="ag.standard",
        tier=2,
        node_type="agent",
        actor_id="actor-1",
    )

    # Base nodes: ag, cc, cx + custom-worker
    rows = collect_peer_status(registry, health, now=clock.now())
    assert len(rows) >= 4
    peer_ids = [r["peer"] for r in rows]
    assert "ag" in peer_ids
    assert "cc" in peer_ids
    assert "cx" in peer_ids
    assert "custom-worker" in peer_ids

    # Check fields
    custom_row = next(r for r in rows if r["peer"] == "custom-worker")
    assert custom_row["lifecycle"] in {"active", "registered"}
    assert "version" in custom_row
    assert "health" in custom_row


def test_peer_status_specific_peer_and_unknown(test_setup):
    registry: PeerRegistryService = test_setup["registry"]
    health: HealthService = test_setup["health"]
    clock: FixedClock = test_setup["clock"]

    # Specific valid peer
    rows = collect_peer_status(registry, health, node_id="cc", now=clock.now())
    assert len(rows) == 1
    assert rows[0]["peer"] == "cc"

    # Specific unknown peer
    unknown_rows = collect_peer_status(registry, health, node_id="nonexistent-node", now=clock.now())
    assert len(unknown_rows) == 0


def test_peer_status_degraded_and_quarantined_display(test_setup):
    registry: PeerRegistryService = test_setup["registry"]
    health: HealthService = test_setup["health"]
    store: SqliteStateStore = test_setup["store"]
    clock: FixedClock = test_setup["clock"]

    # Quarantine peer "cc"
    _quarantine_peer(health, store, "cc", "cc.standard", clock)

    rows = collect_peer_status(registry, health, node_id="cc", now=clock.now())
    assert len(rows) == 1
    assert rows[0]["peer"] == "cc"
    assert rows[0]["gate"] == "closed"
    assert rows[0]["health"] == "RED"


def test_health_check_read_only(test_setup):
    registry: PeerRegistryService = test_setup["registry"]
    health: HealthService = test_setup["health"]
    clock: FixedClock = test_setup["clock"]

    # Check all peers
    result = collect_health_check(registry, health, peer=None, recover=False, now=clock.now())
    assert "peers" in result
    peers_list = result["peers"]
    assert len(peers_list) >= 3
    for p in peers_list:
        assert "peer" in p
        assert "status" in p

    # Check single peer
    single_res = collect_health_check(registry, health, peer="ag", recover=False, now=clock.now())
    assert len(single_res["peers"]) == 1
    assert single_res["peers"][0]["peer"] == "ag"


def test_health_check_with_recover_reconciles_circuit(test_setup, monkeypatch):
    registry: PeerRegistryService = test_setup["registry"]
    health: HealthService = test_setup["health"]
    coordinator: HealthRevalidationCoordinator = test_setup["coordinator"]
    store: SqliteStateStore = test_setup["store"]
    caller: AuthenticatedSubject = test_setup["caller"]
    clock: FixedClock = test_setup["clock"]

    # Quarantine ag so its circuit is dead
    _quarantine_peer(health, store, "ag", "ag.standard", clock)

    _mock_successful_probe(monkeypatch, clock)

    # Calling health.check with recover=True
    result = collect_health_check(
        registry,
        health,
        coordinator=coordinator,
        caller=caller,
        peer="ag",
        recover=True,
        now=clock.now(),
    )
    assert len(result["peers"]) == 1
    assert result["peers"][0]["peer"] == "ag"
    assert result["peers"][0]["status"] == "GREEN"
    assert result["peers"][0]["recovered"] is True
    assert result["peers"][0]["circuit_closed"] is True


def test_execute_peer_recover_single_peer(test_setup, monkeypatch):
    registry: PeerRegistryService = test_setup["registry"]
    health: HealthService = test_setup["health"]
    coordinator: HealthRevalidationCoordinator = test_setup["coordinator"]
    store: SqliteStateStore = test_setup["store"]
    caller: AuthenticatedSubject = test_setup["caller"]
    clock: FixedClock = test_setup["clock"]

    # Quarantine cx
    _quarantine_peer(health, store, "cx", "cx.standard", clock)

    _mock_successful_probe(monkeypatch, clock)

    result = execute_peer_recover(
        registry,
        coordinator,
        caller,
        peer_id="cx",
        reason="Manual repair",
        now=clock.now(),
    )
    results = result["results"]
    assert len(results) == 1
    assert results[0]["peer"] == "cx"
    assert results[0]["probe_outcome"] == "SUCCESS"
    assert results[0]["admission_state"] == "OPEN"
    assert results[0]["circuit_closed"] is True
    assert results[0]["status"] == "OK"


def test_execute_peer_recover_all_peers_multi_iteration(test_setup, monkeypatch):
    registry: PeerRegistryService = test_setup["registry"]
    health: HealthService = test_setup["health"]
    coordinator: HealthRevalidationCoordinator = test_setup["coordinator"]
    store: SqliteStateStore = test_setup["store"]
    caller: AuthenticatedSubject = test_setup["caller"]
    clock: FixedClock = test_setup["clock"]

    # Quarantine ag and cc
    _quarantine_peer(health, store, "ag", "ag.standard", clock)
    _quarantine_peer(health, store, "cc", "cc.standard", clock)

    _mock_successful_probe(monkeypatch, clock)

    result = execute_peer_recover(
        registry,
        coordinator,
        caller,
        peer_id="all",
        reason="Sweep all",
        now=clock.now(),
    )
    results = result["results"]
    assert len(results) >= 3
    peer_names = [r["peer"] for r in results]
    assert "ag" in peer_names
    assert "cc" in peer_names
    assert "cx" in peer_names
    assert all(r["status"] == "OK" for r in results)


def test_execute_peer_recover_unknown_peer(test_setup):
    registry: PeerRegistryService = test_setup["registry"]
    coordinator: HealthRevalidationCoordinator = test_setup["coordinator"]
    caller: AuthenticatedSubject = test_setup["caller"]
    clock: FixedClock = test_setup["clock"]

    result = execute_peer_recover(
        registry,
        coordinator,
        caller,
        peer_id="unknown-node",
        reason="Manual",
        now=clock.now(),
    )
    results = result["results"]
    assert len(results) == 1
    assert results[0]["peer"] == "unknown-node"
    assert results[0]["status"] == "ERROR"
    assert "Unknown peer node" in str(results[0]["error"])


def test_health_precheck_all_healthy(test_setup):
    registry: PeerRegistryService = test_setup["registry"]
    health: HealthService = test_setup["health"]
    clock: FixedClock = test_setup["clock"]

    # Seed fresh health evidence for all base peers
    _record_healthy_evidence(health, "ag", "ag.standard", clock.now())
    _record_healthy_evidence(health, "cc", "cc.standard", clock.now())
    _record_healthy_evidence(health, "cx", "cx.standard", clock.now())

    result = collect_health_precheck(registry, health, now=clock.now())
    assert result["ok"] is True
    assert result["scope"] == "all"
    assert len(result["peers"]) >= 3
    assert all(p["eligible"] is True for p in result["peers"])


def test_health_precheck_degraded_fails_closed(test_setup):
    registry: PeerRegistryService = test_setup["registry"]
    health: HealthService = test_setup["health"]
    store: SqliteStateStore = test_setup["store"]
    clock: FixedClock = test_setup["clock"]

    # Seed fresh health evidence for ag and cx
    _record_healthy_evidence(health, "ag", "ag.standard", clock.now())
    _record_healthy_evidence(health, "cx", "cx.standard", clock.now())

    # Degrade peer "cc"
    _degrade_peer(health, store, "cc", "cc.standard", clock)

    # Precheck across all peers should fail closed (ok=False)
    result_all = collect_health_precheck(registry, health, now=clock.now())
    assert result_all["ok"] is False
    assert any(p["peer"] == "cc" and p["eligible"] is False for p in result_all["peers"])

    # Precheck scoped to ag and cx should succeed (ok=True)
    result_scoped = collect_health_precheck(registry, health, peers="ag,cx", now=clock.now())
    assert result_scoped["ok"] is True
    assert len(result_scoped["peers"]) == 2
    assert all(p["eligible"] is True for p in result_scoped["peers"])


def test_check_gate_open_and_closed(test_setup):
    registry: PeerRegistryService = test_setup["registry"]
    health: HealthService = test_setup["health"]
    store: SqliteStateStore = test_setup["store"]
    clock: FixedClock = test_setup["clock"]

    # Seed fresh health evidence for ag
    _record_healthy_evidence(health, "ag", "ag.standard", clock.now())

    # Normal healthy gate
    gate_ag = collect_check_gate(registry, health, agent="ag", now=clock.now())
    assert gate_ag["open"] is True
    assert gate_ag["gate"] == "ON"
    assert gate_ag["agent"] == "ag"

    # Quarantine cc
    _quarantine_peer(health, store, "cc", "cc.standard", clock)

    gate_cc = collect_check_gate(registry, health, agent="cc", now=clock.now())
    assert gate_cc["open"] is False
    assert gate_cc["gate"] == "OFF"
    assert gate_cc["admission_state"] == "QUARANTINED"

    # Unknown agent
    gate_unknown = collect_check_gate(registry, health, agent="nonexistent", now=clock.now())
    assert gate_unknown["open"] is False
    assert gate_unknown["gate"] == "OFF"
    assert "unknown peer node" in str(gate_unknown.get("reason", ""))


def test_health_sweep_fresh_and_stale(test_setup):
    registry: PeerRegistryService = test_setup["registry"]
    health: HealthService = test_setup["health"]
    clock: FixedClock = test_setup["clock"]

    # Record fresh evidence observed at clock.now()
    _record_healthy_evidence(health, "ag", "ag.standard", clock.now())
    _record_healthy_evidence(health, "cc", "cc.standard", clock.now())
    _record_healthy_evidence(health, "cx", "cx.standard", clock.now())

    # Sweep at current time -> 0 stale
    sweep_fresh = collect_health_sweep(registry, health, now=clock.now())
    assert sweep_fresh["stale_count"] == 0
    assert sweep_fresh["total_peers"] >= 3

    # Advance clock far into future (past the 7200s readiness_freshness_seconds)
    future_time = clock.now() + 7_200_000 + 60_000
    sweep_stale = collect_health_sweep(registry, health, now=future_time)
    assert sweep_stale["stale_count"] >= 3
    stale_peers = list(sweep_stale["stale_peers"])
    assert "ag" in stale_peers


def _quickwin_submission(*, idempotency_key: str | None) -> SubmissionMetadata:
    return SubmissionMetadata(
        client_request_id="quickwin-request",
        correlation_id="quickwin-correlation",
        client_id="client-1",
        actor_id="test-principal",
        scope={},
        idempotency_key=idempotency_key,
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1_000,
    )


def test_legacy_translation_and_api_execution(test_setup, monkeypatch):
    runtime = test_setup["runtime"]
    coordinator: HealthRevalidationCoordinator = test_setup["coordinator"]
    health: HealthService = test_setup["health"]
    clock: FixedClock = test_setup["clock"]
    caller: AuthenticatedSubject = test_setup["caller"]

    client = Client(
        runtime.application_api,
        caller=RequestContext(principal=caller.principal_id, client_id="client-1"),
    )

    # Seed fresh health evidence for ag
    _record_healthy_evidence(health, "ag", "ag.standard", clock.now())
    _record_healthy_evidence(health, "cc", "cc.standard", clock.now())
    _record_healthy_evidence(health, "cx", "cx.standard", clock.now())

    translator = LegacyTranslator()

    def translate(action: str, arguments: dict[str, Any], key: str) -> Any:
        outcome = translator.translate(
            LegacyActionCall(action=action, arguments=arguments),
            _quickwin_submission(idempotency_key=key),
        )
        assert isinstance(outcome, TranslatedCommand)
        return outcome.command

    # 1. Translate and submit peer-status (READ_ONLY -- no idempotency key needed)
    res_status = client.submit(translate("peer-status", {}, "quickwin-1"))
    assert isinstance(res_status, CommandSuccess)
    assert "peers" in res_status.result

    # 2. Translate and submit health-check
    res_health = client.submit(translate("health-check", {"peer": "ag"}, "quickwin-2"))
    assert isinstance(res_health, CommandSuccess)
    assert "peers" in res_health.result

    # 3. Translate and submit check-gate
    res_gate = client.submit(translate("check-gate", {"agent": "ag"}, "quickwin-3"))
    assert isinstance(res_gate, CommandSuccess)
    assert res_gate.result["open"] is True

    # 4. Translate and submit health-precheck
    res_precheck = client.submit(translate("health-precheck", {}, "quickwin-4"))
    assert isinstance(res_precheck, CommandSuccess)
    assert res_precheck.result["ok"] is True

    # 5. Translate and submit health-sweep
    res_sweep = client.submit(translate("health-sweep", {}, "quickwin-5"))
    assert isinstance(res_sweep, CommandSuccess)
    assert "stale_count" in res_sweep.result

    # 6. Translate and submit peer-recover (MUTATING -- requires idempotency key)
    _mock_successful_probe(monkeypatch, clock)
    res_recover = client.submit(
        translate("peer-recover", {"peer": "ag", "reason": "test"}, "quickwin-6")
    )
    assert isinstance(res_recover, CommandSuccess)
    assert "results" in res_recover.result


def test_cli_commands_execution(tmp_path: Path, capsys):
    # Initialize workspace DB
    store = SqliteStateStore(
        tmp_path / ".peerhub" / "state.sqlite3",
        workspace_home_id="cli-test",
    )
    store.initialize()

    # 1. peer status --json
    code_status = main(["peer", "status", "--workspace", str(tmp_path), "--json"])
    assert code_status == 0
    out_status, _ = capsys.readouterr()
    data_status = json.loads(out_status)
    assert "peers" in data_status

    # 2. health check --json
    code_check = main(["health", "check", "--workspace", str(tmp_path), "--json"])
    assert code_check == 0
    out_check, _ = capsys.readouterr()
    data_check = json.loads(out_check)
    assert "peers" in data_check

    # 3. health precheck --json (on empty DB, all are UNKNOWN so exits 1)
    code_precheck = main(["health", "precheck", "--workspace", str(tmp_path), "--json"])
    assert code_precheck == 1
    out_precheck, _ = capsys.readouterr()
    data_precheck = json.loads(out_precheck)
    assert data_precheck["ok"] is False

    # 4. health sweep --json
    code_sweep = main(["health", "sweep", "--workspace", str(tmp_path), "--json"])
    assert code_sweep == 0
    out_sweep, _ = capsys.readouterr()
    data_sweep = json.loads(out_sweep)
    assert "stale_count" in data_sweep

    # 5. gate check ag --json (on empty DB, ag is UNKNOWN/closed so exits 1)
    code_gate = main(["gate", "check", "ag", "--workspace", str(tmp_path), "--json"])
    assert code_gate == 1
    out_gate, _ = capsys.readouterr()
    data_gate = json.loads(out_gate)
    assert data_gate["open"] is False
