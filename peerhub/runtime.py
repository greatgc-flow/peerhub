"""Production dependency composition for PeerHub."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

from .core.context import RuntimeContext
from .dispatch.service import DispatchService
from .governance.broker import GovernanceBroker
from .persistence.sqlite import SqliteStateStore

from .telemetry.projections import TelemetryProjector
from .health.contract import HealthPolicy, HealthScopeMembershipSnapshot
from .health.service import HealthService
from .routing.service import RoutingService
from .application.workflows import ApplicationWorkflows
from .application.api import ApplicationAPI


@dataclass
class Runtime:
    """A composed PeerHub runtime and its owned infrastructure."""

    context: RuntimeContext
    state_store: SqliteStateStore
    governance_broker: GovernanceBroker
    dispatch_service: DispatchService
    
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


from .application.workflows import ApplicationWorkflows
from .application.api import ApplicationAPI, AdmissionInputsProvider

def create_runtime(
    context: RuntimeContext,
    *,
    admission_provider: AdmissionInputsProvider | None = None,
) -> Runtime:
    """Create the composed Phase 1 runtime."""

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
    # AMBIGUITY FLAG: HealthPolicy and HealthScopeMembershipSnapshot require configuration values
    # (e.g. configuration_digest) that are not available in RuntimeContext.
    # We are injecting dummy/default values here to allow composition to succeed.
    policy = HealthPolicy(
        policy_id="v1-health-default-r1",
        revision=1,
        readiness_freshness_seconds=7200,
        recovery_backoff_seconds=(30, 60, 120, 240, 480, 900),
        recovery_jitter_fraction=0.2,
        readiness_observation_threshold=1,
        administrative_recovery_probe_limit=1,
    )
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
    )

    application_api = ApplicationAPI(
        workflows=application_workflows,
        dispatch=dispatch_service,
        admission_provider=admission_provider,
    )

    # Note: session-rotation wiring is deliberately deferred pending a proper unit-of-work-sharing design.

    return Runtime(
        context=context,
        state_store=state_store,
        governance_broker=governance_broker,
        dispatch_service=dispatch_service,
        telemetry_projector=telemetry_projector,
        health_service=health_service,
        routing_service=routing_service,
        application_workflows=application_workflows,
        application_api=application_api,
    )

