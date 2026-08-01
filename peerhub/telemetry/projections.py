"""Replayable operational telemetry projection over canonical outbox order."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from peerhub.core.context import IdSource
from peerhub.core.errors import (
    RecordNotFoundError,
    StaleRevisionError,
)
from peerhub.core.evidence import (
    EvidenceRef,
    EvidenceState,
    EvidenceValue,
)
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import (
    ATTEMPT_TERMINAL_OBSERVED_EVENT_KIND,
    SCHEMA_VERSION,
    AttemptTerminalObserved,
    OperationalFailureCategory,
    require_text,
)
from peerhub.governance.contract import (
    OutboxEvent,
    OutboxState,
)
from peerhub.state.contract import StateStore, UnitOfWork
from peerhub.telemetry.contract import (
    OperationalObservation,
    OperationalProjectionSnapshot,
)


DEFAULT_CONSUMER_ID = "telemetry.operational.v1"
_PROVIDER_ID = "peerhub.dispatch.service"


class _CheckpointLike(Protocol):
    consumer_id: str
    outbox_position: int
    event_id: str
    revision: int


@dataclass(frozen=True)
class _CheckpointValue:
    consumer_id: str
    outbox_position: int
    event_id: str
    revision: int

    def __post_init__(self) -> None:
        require_text(self.consumer_id, "consumer_id")
        require_text(self.event_id, "event_id")
        if (
            type(self.outbox_position) is not int
            or self.outbox_position < 0
        ):
            raise ValueError(
                "outbox_position must be a nonnegative integer"
            )
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("revision must be a positive integer")


class TelemetryUnitOfWork(UnitOfWork, Protocol):
    """Store operations required by the operational projector."""

    def list_outbox_events(
        self,
        states: tuple[OutboxState, ...],
        *,
        limit: int,
        governance_only: bool = False,
        after_position: int = 0,
    ) -> tuple[OutboxEvent, ...]:
        ...

    def get_outbox_checkpoint(
        self,
        consumer_id: str,
    ) -> _CheckpointLike | None:
        ...

    def add_outbox_checkpoint(
        self,
        checkpoint: _CheckpointLike,
    ) -> None:
        ...

    def cas_update_outbox_checkpoint(
        self,
        current: _CheckpointLike,
        updated: _CheckpointLike,
    ) -> bool:
        ...

    def add_operational_observation(
        self,
        observation: OperationalObservation,
    ) -> None:
        ...

    def get_operational_observation(
        self,
        observation_id: str,
    ) -> OperationalObservation | None:
        ...

    def add_operational_projection(
        self,
        projection: OperationalProjectionSnapshot,
    ) -> None:
        ...

    def get_operational_projection(
        self,
        instance_id: str,
        profile_id: str,
    ) -> OperationalProjectionSnapshot | None:
        ...

    def cas_update_operational_projection(
        self,
        current: OperationalProjectionSnapshot,
        updated: OperationalProjectionSnapshot,
    ) -> bool:
        ...


def _required_text(
    payload: object,
    name: str,
) -> str:
    if not isinstance(payload, dict) and not hasattr(
        payload,
        "get",
    ):
        raise ValueError("terminal event payload must be an object")
    value = payload.get(name)  # type: ignore[union-attr]
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return require_text(value, name)


def _required_int(
    payload: object,
    name: str,
) -> int:
    value = payload.get(name)  # type: ignore[union-attr]
    if type(value) is not int or value < 0:
        raise ValueError(
            f"{name} must be a nonnegative integer"
        )
    return value


def decode_attempt_terminal_event(
    event: OutboxEvent,
) -> AttemptTerminalObserved:
    """Decode and integrity-check one canonical terminal event."""

    if (
        event.event_kind
        != ATTEMPT_TERMINAL_OBSERVED_EVENT_KIND
    ):
        raise ValueError(
            "event is not AttemptTerminalObserved"
        )
    if event.outbox_position is None:
        raise ValueError(
            "persisted terminal event requires outbox_position"
        )

    payload = event.payload
    raw_category = payload.get(
        "operational_failure_category"
    )
    if raw_category is None:
        category = None
    elif isinstance(raw_category, str):
        category = OperationalFailureCategory(raw_category)
    else:
        raise ValueError(
            "operational_failure_category must be "
            "a string or null"
        )

    raw_integrity = payload.get("process_integrity")
    if type(raw_integrity) is not bool:
        raise ValueError(
            "process_integrity must be a boolean"
        )

    raw_started_at = payload.get("started_at")
    if raw_started_at is not None and (
        type(raw_started_at) is not int
        or raw_started_at < 0
    ):
        raise ValueError(
            "started_at must be a nonnegative integer or null"
        )

    raw_latency = payload.get("latency")
    if raw_latency is not None and (
        type(raw_latency) is not int or raw_latency < 0
    ):
        raise ValueError(
            "latency must be a nonnegative integer or null"
        )

    raw_refs = payload.get("evidence_refs", ())
    if not isinstance(raw_refs, (list, tuple)):
        raise ValueError(
            "evidence_refs must be an array"
        )
    refs = tuple(
        require_text(value, "evidence_ref")
        for value in raw_refs
    )

    terminal = AttemptTerminalObserved(
        instance_id=_required_text(
            payload,
            "instance_id",
        ),
        profile_id=_required_text(
            payload,
            "profile_id",
        ),
        transport=_required_text(
            payload,
            "transport",
        ),
        operational_failure_category=category,
        execution_certainty=ExecutionCertainty(
            _required_text(
                payload,
                "execution_certainty",
            )
        ),
        process_integrity=raw_integrity,
        started_at=raw_started_at,
        terminal_at=_required_int(
            payload,
            "terminal_at",
        ),
        latency=raw_latency,
        evidence_refs=refs,
    )

    if terminal.terminal_at != event.occurred_at:
        raise ValueError(
            "terminal_at must equal outbox occurred_at"
        )
    if terminal.started_at is not None and (
        terminal.latency
        != terminal.terminal_at - terminal.started_at
    ):
        raise ValueError(
            "latency does not match terminal_at-started_at"
        )
    if tuple(event.evidence_refs) != terminal.evidence_refs:
        raise ValueError(
            "payload and envelope evidence_refs differ"
        )
    return terminal


def _outbox_evidence_ref(
    observation: OperationalObservation,
) -> EvidenceRef:
    return EvidenceRef(
        "outbox:"
        f"{observation.outbox_position}:"
        f"{observation.source_event_id}"
    )


def _evidence(
    observation: OperationalObservation,
    *,
    state: EvidenceState,
    value: object,
    freshness_ttl: int,
    observed_at: int | None,
) -> EvidenceValue:
    terminal = observation.terminal_event
    return EvidenceValue(
        state=state,
        source_tag=(
            ATTEMPT_TERMINAL_OBSERVED_EVENT_KIND
        ),
        provider_id=_PROVIDER_ID,
        provider_version=SCHEMA_VERSION,
        observed_at=observed_at,
        captured_at=terminal.terminal_at,
        freshness_ttl=freshness_ttl,
        evidence_ref=_outbox_evidence_ref(observation),
        value=value,
    )


def _dedupe_refs(
    refs: tuple[EvidenceRef, ...],
) -> tuple[EvidenceRef, ...]:
    seen: set[str] = set()
    result: list[EvidenceRef] = []
    for reference in refs:
        text = str(reference)
        if text in seen:
            continue
        seen.add(text)
        result.append(EvidenceRef(text))
    return tuple(result)


def project_operational_observation(
    current: OperationalProjectionSnapshot | None,
    observation: OperationalObservation,
    *,
    projection_id: str,
    freshness_ttl: int,
) -> OperationalProjectionSnapshot:
    """Purely reduce one immutable observation into a projection."""

    if type(freshness_ttl) is not int or freshness_ttl < 0:
        raise ValueError(
            "freshness_ttl must be a nonnegative integer"
        )

    terminal = observation.terminal_event
    if current is not None and (
        current.instance_id != terminal.instance_id
        or current.profile_id != terminal.profile_id
    ):
        raise ValueError(
            "projection and observation subjects differ"
        )
    if (
        current is not None
        and current.last_terminal_at is not None
        and terminal.terminal_at < current.last_terminal_at
    ):
        raise ValueError(
            "terminal time regressed in canonical outbox order"
        )

    category_state = (
        EvidenceState.MEASURED
        if terminal.operational_failure_category is not None
        else EvidenceState.ABSENT
    )
    failure_category = _evidence(
        observation,
        state=category_state,
        value=terminal.operational_failure_category,
        freshness_ttl=freshness_ttl,
        observed_at=terminal.terminal_at,
    )
    process_integrity = _evidence(
        observation,
        state=EvidenceState.MEASURED,
        value=terminal.process_integrity,
        freshness_ttl=freshness_ttl,
        observed_at=terminal.terminal_at,
    )
    latency = _evidence(
        observation,
        state=(
            EvidenceState.MEASURED
            if terminal.latency is not None
            else EvidenceState.ABSENT
        ),
        value=terminal.latency,
        freshness_ttl=freshness_ttl,
        observed_at=terminal.terminal_at,
    )

    usage = (
        current.usage
        if current is not None
        else _evidence(
            observation,
            state=EvidenceState.ABSENT,
            value=None,
            freshness_ttl=freshness_ttl,
            observed_at=None,
        )
    )

    operational_failure = (
        terminal.operational_failure_category is not None
        or not terminal.process_integrity
    )
    if operational_failure:
        failure_streak = (
            1
            if current is None
            else current.failure_streak + 1
        )
        previous_refs = (
            current.evidence_refs
            if current is not None
            and current.failure_streak > 0
            else ()
        )
    else:
        failure_streak = 0
        previous_refs = ()

    current_refs = tuple(
        EvidenceRef(reference)
        for reference in terminal.evidence_refs
    ) + (_outbox_evidence_ref(observation),)

    return OperationalProjectionSnapshot(
        projection_id=(
            current.projection_id
            if current is not None
            else projection_id
        ),
        instance_id=terminal.instance_id,
        profile_id=terminal.profile_id,
        failure_category=failure_category,
        process_integrity=process_integrity,
        latency=latency,
        usage=usage,
        failure_streak=failure_streak,
        last_terminal_at=terminal.terminal_at,
        evidence_refs=_dedupe_refs(
            previous_refs + current_refs
        ),
        revision=(
            1 if current is None else current.revision + 1
        ),
        updated_at=terminal.terminal_at,
    )


class TelemetryProjector:
    """Independent replayable consumer of canonical outbox order."""

    def __init__(
        self,
        store: StateStore[TelemetryUnitOfWork],
        *,
        ids: IdSource,
        freshness_ttl: int,
        consumer_id: str = DEFAULT_CONSUMER_ID,
    ) -> None:
        if type(freshness_ttl) is not int or freshness_ttl < 0:
            raise ValueError(
                "freshness_ttl must be a nonnegative integer"
            )
        self._store = store
        self._ids = ids
        self._freshness_ttl = freshness_ttl
        self._consumer_id = require_text(
            consumer_id,
            "consumer_id",
        )

    def get(
        self,
        instance_id: str,
        profile_id: str,
    ) -> OperationalProjectionSnapshot:
        """Implement TelemetryProjectionReader."""

        with self._store.unit_of_work() as unit:
            projection = unit.get_operational_projection(
                instance_id,
                profile_id,
            )
        if projection is None:
            raise RecordNotFoundError(
                "operational projection",
                f"{instance_id}/{profile_id}",
            )
        return projection

    def project_pending(
        self,
        *,
        limit: int = 100,
    ) -> int:
        """Checkpoint up to `limit` canonical outbox events."""

        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")

        with self._store.unit_of_work() as unit:
            checkpoint = unit.get_outbox_checkpoint(
                self._consumer_id
            )
            after_position = (
                0
                if checkpoint is None
                else checkpoint.outbox_position
            )
            events = unit.list_outbox_events(
                (
                    OutboxState.PENDING,
                    OutboxState.CLAIMED,
                    OutboxState.CONSUMED,
                ),
                limit=limit,
                after_position=after_position,
            )

        projected = 0
        for event in events:
            if self._project_one(event):
                projected += 1
        return projected

    def _project_one(self, event: OutboxEvent) -> bool:
        position = event.outbox_position
        if position is None:
            raise ValueError(
                "persisted outbox event has no position"
            )

        with self._store.unit_of_work() as unit:
            checkpoint = unit.get_outbox_checkpoint(
                self._consumer_id
            )
            if (
                checkpoint is not None
                and position <= checkpoint.outbox_position
            ):
                return False

            if (
                event.event_kind
                == ATTEMPT_TERMINAL_OBSERVED_EVENT_KIND
            ):
                terminal = decode_attempt_terminal_event(event)
                observation = OperationalObservation(
                    observation_id=self._ids.new_id(
                        "operational-observation"
                    ),
                    source_event_id=event.event_id,
                    outbox_position=position,
                    terminal_event=terminal,
                )
                current = unit.get_operational_projection(
                    terminal.instance_id,
                    terminal.profile_id,
                )
                updated = project_operational_observation(
                    current,
                    observation,
                    projection_id=self._ids.new_id(
                        "operational-projection"
                    ),
                    freshness_ttl=self._freshness_ttl,
                )

                unit.add_operational_observation(
                    observation
                )
                if current is None:
                    unit.add_operational_projection(updated)
                elif not unit.cas_update_operational_projection(
                    current,
                    updated,
                ):
                    latest = unit.get_operational_projection(
                        terminal.instance_id,
                        terminal.profile_id,
                    )
                    raise StaleRevisionError(
                        current.projection_id,
                        current.revision,
                        (
                            0
                            if latest is None
                            else latest.revision
                        ),
                    )

            updated_checkpoint = _CheckpointValue(
                consumer_id=self._consumer_id,
                outbox_position=position,
                event_id=event.event_id,
                revision=(
                    1
                    if checkpoint is None
                    else checkpoint.revision + 1
                ),
            )
            if checkpoint is None:
                unit.add_outbox_checkpoint(
                    updated_checkpoint
                )
            elif not unit.cas_update_outbox_checkpoint(
                checkpoint,
                updated_checkpoint,
            ):
                latest = unit.get_outbox_checkpoint(
                    self._consumer_id
                )
                raise StaleRevisionError(
                    self._consumer_id,
                    checkpoint.revision,
                    0 if latest is None else latest.revision,
                )

            unit.commit()
        return True


__all__ = [
    "TelemetryProjector",
    "decode_attempt_terminal_event",
    "project_operational_observation",
]
