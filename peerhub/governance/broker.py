"""Governed mutation, idempotency, outbox, and recovery orchestration."""

from __future__ import annotations

from typing import Protocol

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import (
    ExclusiveClaimConflictError,
    IdempotencyPayloadMismatchError,
    InvalidMutationError,
    RecordNotFoundError,
    StaleRevisionError,
)
from peerhub.state.contract import StateStore, UnitOfWork

from .contract import (
    CommandBinding,
    EffectOutcome,
    EffectReceipt,
    MutationDisposition,
    MutationPlan,
    MutationRequest,
    MutationSubmission,
    OutboxEvent,
    OutboxState,
    PendingEffect,
    RecoveryDisposition,
    TargetState,
    TransitionReceipt,
)
from .mutations import (
    apply_mutation_plan,
    build_effect_receipt,
    build_outbox_event,
    build_transition_receipt,
    mutation_payload_digest,
    plan_mutation,
    validate_expected_revision,
)


class GovernanceUnitOfWork(UnitOfWork, Protocol):
    """Persistence operations required by the governance broker."""

    def get_target(self, target_id: str) -> TargetState | None:
        """Return the current target, if present."""

        ...

    def compare_and_set_target(
        self,
        current: TargetState | None,
        updated: TargetState,
    ) -> bool:
        """Insert or update a target if its revision is current."""

        ...

    def get_command_binding(
        self,
        client_id: str,
        command_type: str,
        idempotency_key: str,
    ) -> CommandBinding | None:
        """Return an existing idempotency binding."""

        ...

    def add_command_binding(
        self,
        binding: CommandBinding,
    ) -> None:
        """Insert a new immutable idempotency binding."""

        ...

    def add_mutation_request(
        self,
        request: MutationRequest,
        payload_digest: str,
        created_at: int,
    ) -> None:
        """Persist one immutable mutation request."""

        ...

    def add_mutation_plan(self, plan: MutationPlan) -> None:
        """Persist one immutable mutation plan."""

        ...

    def add_transition_receipt(
        self,
        receipt: TransitionReceipt,
    ) -> None:
        """Persist one immutable transition receipt."""

        ...

    def get_transition_receipt(
        self,
        receipt_id: str,
    ) -> TransitionReceipt | None:
        """Return a transition receipt by ID."""

        ...

    def add_outbox_event(self, event: OutboxEvent) -> None:
        """Persist a pending canonical outbox event."""

        ...

    def get_outbox_event(
        self,
        event_id: str,
    ) -> OutboxEvent | None:
        """Return an outbox event by ID."""

        ...

    def list_outbox_events(
        self,
        states: tuple[OutboxState, ...],
        *,
        limit: int,
        governance_only: bool = False,
        after_position: int = 0,
    ) -> tuple[OutboxEvent, ...]:
        """Return canonical outbox events in workspace order."""

        ...

    def list_unfinished_effect_deliveries(
        self,
        *,
        limit: int,
        after_position: int = 0,
    ) -> tuple[OutboxEvent, ...]:
        """Return unreceipted effect deliveries in workspace order."""

        ...

    def claim_outbox_event(
        self,
        event_id: str,
        owner_id: str,
        attempt_id: str,
        claimed_at: int,
    ) -> OutboxEvent | None:
        """CAS-claim one pending outbox event."""

        ...

    def mark_outbox_consumed(
        self,
        event_id: str,
        owner_id: str,
        attempt_id: str,
        consumed_at: int,
    ) -> bool:
        """CAS-mark one claimed outbox event consumed."""

        ...

    def add_effect_receipt(
        self,
        receipt: EffectReceipt,
    ) -> None:
        """Persist one immutable terminal effect receipt."""

        ...

    def get_effect_receipt(
        self,
        outbox_event_id: str,
    ) -> EffectReceipt | None:
        """Return the terminal receipt for an outbox event."""

        ...


class FaultPoint(str):
    """Named transaction boundaries available to deterministic tests."""

    AFTER_TARGET_WRITE = "AFTER_TARGET_WRITE"
    BEFORE_COMMIT = "BEFORE_COMMIT"
    AFTER_COMMIT = "AFTER_COMMIT"


class FaultInjector(Protocol):
    """A deterministic transaction-boundary fault hook."""

    def hit(self, point: str) -> None:
        """Raise at the requested point or return normally."""

        ...


class _NoFaultInjector:
    def hit(self, point: str) -> None:
        del point


class GovernanceBroker:
    """Coordinate governed mutations through an injected state store."""

    def __init__(
        self,
        store: StateStore[GovernanceUnitOfWork],
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
    def _require_governance_event(
        event: OutboxEvent,
    ) -> None:
        if (
            event.request_id is None
            or event.transition_receipt_id is None
            or event.topic is None
        ):
            raise InvalidMutationError(
                "governance effect processing requires a "
                "governance-linked outbox event"
            )

    def submit(
        self,
        request: MutationRequest,
    ) -> MutationSubmission:
        """Commit a mutation or return its stored idempotent receipt."""

        payload_digest = mutation_payload_digest(request)

        with self._store.unit_of_work() as unit:
            binding = unit.get_command_binding(
                request.client_id,
                request.command_type,
                request.idempotency_key,
            )
            if binding is not None:
                if binding.payload_digest != payload_digest:
                    raise IdempotencyPayloadMismatchError(
                        request.client_id,
                        request.command_type,
                        request.idempotency_key,
                    )
                receipt = unit.get_transition_receipt(
                    binding.receipt_id
                )
                if receipt is None:
                    raise RuntimeError(
                        "idempotency binding references "
                        "a missing transition receipt"
                    )
                return MutationSubmission(
                    disposition=(
                        MutationDisposition.IDEMPOTENCY_HIT
                    ),
                    receipt=receipt,
                )

            current = unit.get_target(request.target_id)
            validate_expected_revision(request, current)

            timestamp = self._clock.now()
            plan = plan_mutation(
                request,
                current,
                plan_id=self._ids.new_id("mutation-plan"),
                planned_at=timestamp,
            )
            receipt = build_transition_receipt(
                plan,
                receipt_id=self._ids.new_id(
                    "transition-receipt"
                ),
                outbox_event_id=self._ids.new_id(
                    "outbox-event"
                ),
                committed_at=timestamp,
            )
            event = build_outbox_event(
                plan,
                receipt,
                event_id=receipt.outbox_event_id,
                correlation_id=request.correlation_id,
                created_at=timestamp,
            )
            target = apply_mutation_plan(
                current,
                plan,
                updated_at=timestamp,
            )
            binding = CommandBinding(
                client_id=request.client_id,
                command_type=request.command_type,
                idempotency_key=request.idempotency_key,
                payload_digest=payload_digest,
                request_id=request.request_id,
                receipt_id=receipt.receipt_id,
                created_at=timestamp,
            )

            if not unit.compare_and_set_target(current, target):
                latest = unit.get_target(request.target_id)
                latest_revision = (
                    0 if latest is None else latest.revision
                )
                raise StaleRevisionError(
                    request.target_id,
                    request.expected_revision,
                    latest_revision,
                )

            self._faults.hit(FaultPoint.AFTER_TARGET_WRITE)

            unit.add_mutation_request(
                request,
                payload_digest,
                timestamp,
            )
            unit.add_mutation_plan(plan)
            unit.add_transition_receipt(receipt)
            unit.add_command_binding(binding)
            unit.add_outbox_event(event)

            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

            result = MutationSubmission(
                disposition=MutationDisposition.COMMITTED,
                receipt=receipt,
            )

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return result

    def get_target(self, target_id: str) -> TargetState | None:
        """Return the current authoritative target state."""

        with self._store.unit_of_work() as unit:
            return unit.get_target(target_id)

    def get_outbox_event(
        self,
        event_id: str,
    ) -> OutboxEvent | None:
        """Return one canonical outbox event."""

        with self._store.unit_of_work() as unit:
            return unit.get_outbox_event(event_id)

    def get_effect_receipt(
        self,
        event_id: str,
    ) -> EffectReceipt | None:
        """Return an event's immutable terminal receipt."""

        with self._store.unit_of_work() as unit:
            return unit.get_effect_receipt(event_id)

    def recover_pending_effects(
        self,
        *,
        limit: int = 100,
    ) -> tuple[PendingEffect, ...]:
        """Discover governance effects without replaying transitions."""

        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")

        with self._store.unit_of_work() as unit:
            events = unit.list_unfinished_effect_deliveries(
                limit=limit,
            )
            pending: list[PendingEffect] = []
            for event in events:
                self._require_governance_event(event)
                transition_receipt_id = (
                    event.transition_receipt_id
                )
                if transition_receipt_id is None:
                    raise RuntimeError(
                        "governance outbox event has no "
                        "transition receipt"
                    )
                receipt = unit.get_transition_receipt(
                    transition_receipt_id
                )
                if receipt is None:
                    raise RuntimeError(
                        "outbox event references a missing "
                        "transition receipt"
                    )
                disposition = (
                    RecoveryDisposition.READY_TO_CLAIM
                    if event.state is OutboxState.PENDING
                    else RecoveryDisposition.CONFIRMATION_REQUIRED
                )
                pending.append(
                    PendingEffect(
                        event=event,
                        transition_receipt=receipt,
                        disposition=disposition,
                    )
                )
            return tuple(pending)

    def claim_effect(
        self,
        event_id: str,
        *,
        owner_id: str,
        attempt_id: str,
    ) -> OutboxEvent:
        """Exclusively claim one pending governance effect intent."""

        with self._store.unit_of_work() as unit:
            event = unit.get_outbox_event(event_id)
            if event is None:
                raise RecordNotFoundError(
                    "outbox_event",
                    event_id,
                )
            self._require_governance_event(event)

            if (
                event.state is OutboxState.CLAIMED
                and event.claimed_by == owner_id
                and event.claim_attempt_id == attempt_id
            ):
                return event

            if event.state is not OutboxState.PENDING:
                raise ExclusiveClaimConflictError(
                    event_id,
                    event.claimed_by,
                )

            claimed = unit.claim_outbox_event(
                event_id,
                owner_id,
                attempt_id,
                self._clock.now(),
            )
            if claimed is None:
                latest = unit.get_outbox_event(event_id)
                raise ExclusiveClaimConflictError(
                    event_id,
                    None if latest is None else latest.claimed_by,
                )

            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return claimed

    def record_effect_result(
        self,
        event_id: str,
        *,
        owner_id: str,
        attempt_id: str,
        outcome: EffectOutcome,
        evidence_refs: tuple[str, ...] = (),
    ) -> EffectReceipt:
        """Commit one immutable terminal effect receipt."""

        with self._store.unit_of_work() as unit:
            existing = unit.get_effect_receipt(event_id)
            if existing is not None:
                if (
                    existing.owner_id == owner_id
                    and existing.attempt_id == attempt_id
                    and existing.outcome is outcome
                ):
                    return existing
                raise ExclusiveClaimConflictError(
                    event_id,
                    existing.owner_id,
                )

            event = unit.get_outbox_event(event_id)
            if event is None:
                raise RecordNotFoundError(
                    "outbox_event",
                    event_id,
                )
            self._require_governance_event(event)
            if (
                event.state is not OutboxState.CLAIMED
                or event.claimed_by != owner_id
                or event.claim_attempt_id != attempt_id
            ):
                raise ExclusiveClaimConflictError(
                    event_id,
                    event.claimed_by,
                )

            completed_at = self._clock.now()
            receipt = build_effect_receipt(
                event,
                effect_receipt_id=self._ids.new_id(
                    "effect-receipt"
                ),
                attempt_id=attempt_id,
                owner_id=owner_id,
                outcome=outcome,
                completed_at=completed_at,
                evidence_refs=evidence_refs,
            )
            unit.add_effect_receipt(receipt)
            if not unit.mark_outbox_consumed(
                event_id,
                owner_id,
                attempt_id,
                completed_at,
            ):
                raise ExclusiveClaimConflictError(
                    event_id,
                    event.claimed_by,
                )

            self._faults.hit(FaultPoint.BEFORE_COMMIT)
            unit.commit()

        self._faults.hit(FaultPoint.AFTER_COMMIT)
        return receipt
