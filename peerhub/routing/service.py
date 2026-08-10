"""Transactional routing selection and pre-dispatch orchestration.

This module persists the immutable audits produced by the pure routing
reducers. It consumes pre-supplied configuration, admission, candidate,
and policy facts; it does not derive health, mutate configuration, or
call sibling feature services.
"""

from __future__ import annotations

from typing import Protocol

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    InvalidMutationError,
    RecordNotFoundError,
)
from peerhub.health.contract import AdmissionSnapshot
from peerhub.state.contract import ReadUnitOfWork, StateStore, UnitOfWork

from .contract import (
    RouteDecision,
    RoutePlanResult,
    RoutePreDispatchResult,
    RouteRequest,
)
from .model import (
    replan_route as reduce_replan_route,
)
from .model import (
    select_route as reduce_select_route,
)
from .model import (
    validate_route_for_dispatch as reduce_validate_route,
)


class RoutingReadUnitOfWork(ReadUnitOfWork, Protocol):
    """Read-only persistence operations required by the routing service."""

    def get_route_decision(
        self,
        decision_id: str,
    ) -> RouteDecision | None:
        """Return one immutable route decision audit."""

        ...


class RoutingUnitOfWork(RoutingReadUnitOfWork, UnitOfWork, Protocol):
    """Persistence operations required by the routing service."""

    def get_admission_snapshot(
        self,
        snapshot_id: str,
    ) -> AdmissionSnapshot | None:
        """Return one immutable admission snapshot."""

        ...

    def add_route_decision(
        self,
        decision: RouteDecision,
    ) -> None:
        """Insert one immutable route decision audit."""

        ...

    def get_route_decision(
        self,
        decision_id: str,
    ) -> RouteDecision | None:
        """Return one immutable route decision audit."""

        ...


class FaultPoint(str):
    """Named transaction boundaries for deterministic tests."""

    AFTER_ROUTE_DECISION_WRITE = (
        "AFTER_ROUTE_DECISION_WRITE"
    )
    BEFORE_COMMIT = "BEFORE_COMMIT"
    AFTER_COMMIT = "AFTER_COMMIT"


class FaultInjector(Protocol):
    """Transaction-boundary fault injection hook."""

    def hit(self, point: str) -> None:
        """Raise a fault or return normally."""

        ...


class _NoFaultInjector:
    def hit(self, point: str) -> None:
        del point


class RoutingService:
    """Persist route plans and replace stale decisions."""

    def __init__(
        self,
        store: StateStore[RoutingUnitOfWork, RoutingReadUnitOfWork],
        *,
        clock: Clock,
        ids: IdSource,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids
        self._faults = fault_injector or _NoFaultInjector()

    @staticmethod
    def _require_admission_snapshot(
        unit: RoutingUnitOfWork,
        snapshot: AdmissionSnapshot,
    ) -> AdmissionSnapshot:
        persisted = unit.get_admission_snapshot(
            snapshot.snapshot_id
        )
        if persisted is None:
            raise RecordNotFoundError(
                "admission_snapshot",
                snapshot.snapshot_id,
            )
        if persisted != snapshot:
            raise InvalidMutationError(
                "pre-supplied admission snapshot differs "
                "from its persisted immutable audit"
            )
        return persisted

    @staticmethod
    def _require_decision(
        unit: RoutingUnitOfWork,
        decision_id: str,
    ) -> RouteDecision:
        decision = unit.get_route_decision(decision_id)
        if decision is None:
            raise RecordNotFoundError(
                "route_decision",
                decision_id,
            )
        return decision

    def _add_route_decision(
        self,
        unit: RoutingUnitOfWork,
        result: RoutePlanResult,
    ) -> None:
        unit.add_route_decision(result.decision)
        self._faults.hit(
            FaultPoint.AFTER_ROUTE_DECISION_WRITE
        )

    def select_route(
        self,
        request: RouteRequest,
    ) -> RoutePlanResult:
        """Evaluate, select, and persist one route decision."""

        timestamp = self._clock.now()
        with self._store.unit_of_work() as unit:
            self._require_admission_snapshot(
                unit,
                request.admission_snapshot,
            )
            result = reduce_select_route(
                request,
                decision_id=self._ids.new_id(
                    "route-decision"
                ),
                created_at=timestamp,
            )
            self._add_route_decision(unit, result)
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return result

    def get_route_decision(
        self,
        decision_id: str,
    ) -> RouteDecision | None:
        """Return one persisted route decision audit."""

        with self._store.read_unit_of_work() as unit:
            return unit.get_route_decision(decision_id)

    def validate_route_for_dispatch(
        self,
        decision_id: str,
        *,
        current_request: RouteRequest,
    ) -> RoutePreDispatchResult:
        """Validate a decision and append a new plan on drift.

        ``current_request`` is a complete, externally composed immutable
        input. This service does not fetch current health or configuration
        through sibling services.
        """

        timestamp = self._clock.now()
        with self._store.unit_of_work() as unit:
            decision = self._require_decision(
                unit,
                decision_id,
            )
            validation = reduce_validate_route(
                decision,
                current_configuration=(
                    current_request.configuration
                ),
            )

            if validation.dispatch_permitted:
                return RoutePreDispatchResult(
                    validation=validation,
                    replanned_route=None,
                )

            self._require_admission_snapshot(
                unit,
                current_request.admission_snapshot,
            )
            replanned_route = reduce_replan_route(
                decision,
                current_request,
                decision_id=self._ids.new_id(
                    "route-decision"
                ),
                created_at=timestamp,
            )
            self._add_route_decision(
                unit,
                replanned_route,
            )
            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return RoutePreDispatchResult(
            validation=validation,
            replanned_route=replanned_route,
        )
