"""Production dependency composition for PeerHub."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peerhub.application.bootstrap import DirectAskAdmissionConfig

from .adapters.contract import PeerAdapter
from .adapters.registry import resolve_peer_adapter
from .application.api import AdmissionInputsProvider, ApplicationAPI
from .application.workflows import ApplicationWorkflows
from .core.context import RuntimeContext
from .dispatch.service import DispatchService
from .governance.broker import GovernanceBroker
from .governance.consensus import ConsensusService
from .governance.tasks import TaskService
from .governance.lessons import LessonService
from .governance.rooms import RoomsService
from .dispatch.duty_lease import DutyLeaseCoordinator
from .dispatch.terminal_duty import TerminalDutyService
from .health.contract import HealthPolicy, HealthScopeMembershipSnapshot
from .health.service import HealthService
from .persistence.sqlite import SqliteStateStore
from .routing.service import RoutingService
from .telemetry.projections import TelemetryProjector


@dataclass
class Runtime:
    """A composed PeerHub runtime and its owned infrastructure."""

    context: RuntimeContext
    state_store: SqliteStateStore
    governance_broker: GovernanceBroker
    consensus_service: ConsensusService
    task_service: TaskService
    lesson_service: LessonService
    rooms_service: RoomsService
    duty_lease_coordinator: DutyLeaseCoordinator
    terminal_duty_service: TerminalDutyService
    dispatch_service: DispatchService
    peer_adapter: PeerAdapter

    telemetry_projector: TelemetryProjector
    health_service: HealthService
    routing_service: RoutingService
    application_workflows: ApplicationWorkflows
    application_api: ApplicationAPI

    def close(self) -> None:
        """Release resources owned by this runtime."""

        self.state_store.close()

    def __enter__(self) -> Runtime:
        """Return this runtime for use as a context manager."""

        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release runtime resources when leaving a context."""

        del exception_type, exception, traceback
        self.close()

def create_runtime(
    context: RuntimeContext,
    *,
    admission_provider: AdmissionInputsProvider | None = None,
    adapter_peer_kind: str = "fake",
    admission_config: "DirectAskAdmissionConfig | None" = None,
) -> Runtime:
    """Create the composed Phase 1 runtime."""

    peer_adapter = resolve_peer_adapter(adapter_peer_kind)

    state_store = SqliteStateStore(
        context.paths.database_path,
        workspace_home_id=context.workspace_home_id,
    )
    state_store.initialize()

    governance_broker = GovernanceBroker(
        state_store,
        clock=context.clock,
        ids=context.ids,
    )
    consensus_service = ConsensusService(
        governance_broker, clock=context.clock, ids=context.ids
    )
    task_service = TaskService(governance_broker, clock=context.clock, ids=context.ids)
    lesson_service = LessonService(governance_broker, clock=context.clock, ids=context.ids)
    rooms_service = RoomsService(governance_broker, clock=context.clock, ids=context.ids)
    duty_lease_coordinator = DutyLeaseCoordinator(state_store, clock=context.clock, ids=context.ids)
    terminal_duty_service = TerminalDutyService(duty_lease_coordinator)
    dispatch_service = DispatchService(
        state_store,
        clock=context.clock,
        ids=context.ids,
    )
    
    # ── Telemetry ──
    # AMBIGUITY FLAG: freshness_ttl is hardcoded. Where should it come from in a shared runtime?
    telemetry_projector = TelemetryProjector(
        state_store,  # pyright: ignore[reportArgumentType]
        ids=context.ids,
        freshness_ttl=7200,
    )

    # ── Health ──
    if admission_config is not None:
        from peerhub.application.bootstrap import persist_direct_ask_admission
        persist_direct_ask_admission(state_store, admission_config)
        policy = admission_config.health_policy
        membership = admission_config.membership
    else:
        policy = HealthPolicy(
            policy_id="v1-health-default-r1",
            revision=1,
            readiness_freshness_seconds=7200,
            recovery_backoff_seconds=(30, 60, 120, 240, 480, 900),
            recovery_jitter_fraction=0.2,
            readiness_observation_threshold=1,
            administrative_recovery_probe_limit=1,
        )
        with state_store.unit_of_work() as uow:
            existing = uow.get_health_policy_revision(policy.policy_id, policy.revision)
            if existing is None:
                uow.add_health_policy_revision(policy)
                uow.commit()
        membership = HealthScopeMembershipSnapshot(
            configuration_revision=1,
            configuration_digest="0" * 64,
            configured_members=(),
            bindings=(),
        )
    
    health_service = HealthService(
        state_store,
        telemetry=telemetry_projector,  # pyright: ignore[reportArgumentType]
        policy=policy,
        membership=membership,
        clock=context.clock,
        ids=context.ids,
    )

    if admission_config is not None:
        readiness_items = (
            admission_config.readiness_list
            if admission_config.readiness_list
            else (admission_config.readiness,)
        )
        for r in readiness_items:
            health_service.evaluate_and_persist_readiness(
                r,
                sealed_runtime_revision=r.evidence.value.runtime_revision,  # type: ignore[reportOptionalMemberAccess]
                adapter_declares_probe_safe=True,
            )

    # ── Routing ──
    routing_service = RoutingService(
        state_store,
        clock=context.clock,
        ids=context.ids,
    )

    # ── Workflows ──
    application_workflows = ApplicationWorkflows(
        telemetry=telemetry_projector,
        health=health_service,
        routing=routing_service,
        dispatch=dispatch_service,
        peer_adapter=peer_adapter,
    )

    application_api = ApplicationAPI(
        workflows=application_workflows,
        dispatch=dispatch_service,
        admission_provider=admission_provider,
        consensus=consensus_service,
        task=task_service,
        lesson=lesson_service,
        lesson_broker=governance_broker,
        room=rooms_service,
        duty=duty_lease_coordinator,
        terminal_duty=terminal_duty_service,
    )

    # Note: session-rotation wiring is deliberately deferred pending a proper unit-of-work-sharing design.

    return Runtime(
        context=context,
        state_store=state_store,
        governance_broker=governance_broker,
        consensus_service=consensus_service,
        task_service=task_service,
        lesson_service=lesson_service,
        rooms_service=rooms_service,
        duty_lease_coordinator=duty_lease_coordinator,
        terminal_duty_service=terminal_duty_service,
        dispatch_service=dispatch_service,
        peer_adapter=peer_adapter,
        telemetry_projector=telemetry_projector,
        health_service=health_service,
        routing_service=routing_service,
        application_workflows=application_workflows,
        application_api=application_api,
    )
