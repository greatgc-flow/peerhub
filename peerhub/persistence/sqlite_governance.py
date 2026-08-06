import sqlite3
from collections.abc import Sequence
from typing import Callable
from .sqlite_helpers import (
    _json_object, 
    _json_text, 
    _string_tuple, 
    _optional_json_object
)
from peerhub.dispatch.contract import OutboxCheckpoint
from peerhub.governance.contract import (
    CommandBinding,
    EffectOutcome,
    EffectReceipt,
    MutationPlan,
    MutationRequest,
    OutboxEvent,
    OutboxState,
    TargetState,
    TransitionReceipt,
    TransitionStatus,
)

class SqliteGovernanceRepository:
    def __init__(self, db_factory: Callable[[], sqlite3.Connection]) -> None:
        self._db = db_factory

    def get_target(self, target_id: str) -> TargetState | None:
            """Return the current target, if present."""

            row = self._db().execute(
                """
                SELECT target_id, revision, state_json, updated_at
                FROM governed_targets
                WHERE target_id = ?
                """,
                (target_id,),
            ).fetchone()
            if row is None:
                return None
            return TargetState(
                target_id=row["target_id"],
                revision=row["revision"],
                state=_json_object(row["state_json"]),
                updated_at=row["updated_at"],
            )

    def compare_and_set_target(
            self,
            current: TargetState | None,
            updated: TargetState,
        ) -> bool:
            """Insert or CAS-update a versioned target."""

            connection = self._db()
            if current is None:
                try:
                    connection.execute(
                        """
                        INSERT INTO governed_targets (
                            target_id,
                            revision,
                            state_json,
                            updated_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            updated.target_id,
                            updated.revision,
                            _json_text(updated.state),
                            updated.updated_at,
                        ),
                    )
                except sqlite3.IntegrityError:
                    return False
                return True

            cursor = connection.execute(
                """
                UPDATE governed_targets
                SET revision = ?, state_json = ?, updated_at = ?
                WHERE target_id = ? AND revision = ?
                """,
                (
                    updated.revision,
                    _json_text(updated.state),
                    updated.updated_at,
                    current.target_id,
                    current.revision,
                ),
            )
            return cursor.rowcount == 1

    def get_command_binding(
            self,
            client_id: str,
            command_type: str,
            idempotency_key: str,
        ) -> CommandBinding | None:
            """Return a governance idempotency binding."""

            row = self._db().execute(
                """
                SELECT
                    client_id,
                    command_type,
                    idempotency_key,
                    payload_digest,
                    request_id,
                    receipt_id,
                    created_at
                FROM command_ledger
                WHERE
                    client_id = ?
                    AND command_type = ?
                    AND idempotency_key = ?
                """,
                (client_id, command_type, idempotency_key),
            ).fetchone()
            if row is None:
                return None
            return CommandBinding(
                client_id=row["client_id"],
                command_type=row["command_type"],
                idempotency_key=row["idempotency_key"],
                payload_digest=row["payload_digest"],
                request_id=row["request_id"],
                receipt_id=row["receipt_id"],
                created_at=row["created_at"],
            )

    def add_command_binding(
            self,
            binding: CommandBinding,
        ) -> None:
            """Insert an immutable governance command-ledger row."""

            self._db().execute(
                """
                INSERT INTO command_ledger (
                    client_id,
                    command_type,
                    idempotency_key,
                    payload_digest,
                    request_id,
                    receipt_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    binding.client_id,
                    binding.command_type,
                    binding.idempotency_key,
                    binding.payload_digest,
                    binding.request_id,
                    binding.receipt_id,
                    binding.created_at,
                ),
            )

    def add_mutation_request(
            self,
            request: MutationRequest,
            payload_digest: str,
            created_at: int,
        ) -> None:
            """Insert an immutable mutation request."""

            self._db().execute(
                """
                INSERT INTO mutation_requests (
                    request_id,
                    command_id,
                    correlation_id,
                    client_id,
                    command_type,
                    idempotency_key,
                    actor_id,
                    policy_revision,
                    target_id,
                    expected_revision,
                    operation,
                    desired_state_json,
                    effect_kind,
                    effect_payload_json,
                    payload_digest,
                    created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    request.request_id,
                    str(request.command_id),
                    request.correlation_id,
                    request.client_id,
                    request.command_type,
                    request.idempotency_key,
                    request.actor_id,
                    request.policy_revision,
                    request.target_id,
                    request.expected_revision,
                    request.operation,
                    _json_text(request.desired_state),
                    request.effect_intent.kind,
                    _json_text(request.effect_intent.payload),
                    payload_digest,
                    created_at,
                ),
            )

    def add_mutation_plan(self, plan: MutationPlan) -> None:
            """Insert an immutable mutation plan."""

            self._db().execute(
                """
                INSERT INTO mutation_plans (
                    plan_id,
                    request_id,
                    request_digest,
                    target_id,
                    previous_revision,
                    next_revision,
                    next_state_json,
                    effect_kind,
                    effect_payload_json,
                    planned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.plan_id,
                    plan.request_id,
                    plan.request_digest,
                    plan.target_id,
                    plan.previous_revision,
                    plan.next_revision,
                    _json_text(plan.next_state),
                    plan.effect_intent.kind,
                    _json_text(plan.effect_intent.payload),
                    plan.planned_at,
                ),
            )

    def add_transition_receipt(
            self,
            receipt: TransitionReceipt,
        ) -> None:
            """Insert an immutable transition receipt."""

            self._db().execute(
                """
                INSERT INTO transition_receipts (
                    receipt_id,
                    request_id,
                    plan_id,
                    target_id,
                    previous_revision,
                    next_revision,
                    status,
                    committed_at,
                    outbox_event_id,
                    evidence_refs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.receipt_id,
                    receipt.request_id,
                    receipt.plan_id,
                    receipt.target_id,
                    receipt.previous_revision,
                    receipt.next_revision,
                    receipt.status.value,
                    receipt.committed_at,
                    receipt.outbox_event_id,
                    _json_text(receipt.evidence_refs),
                ),
            )

    def get_transition_receipt(
            self,
            receipt_id: str,
        ) -> TransitionReceipt | None:
            """Return a transition receipt by ID."""

            row = self._db().execute(
                """
                SELECT
                    receipt_id,
                    request_id,
                    plan_id,
                    target_id,
                    previous_revision,
                    next_revision,
                    status,
                    committed_at,
                    outbox_event_id,
                    evidence_refs_json
                FROM transition_receipts
                WHERE receipt_id = ?
                """,
                (receipt_id,),
            ).fetchone()
            if row is None:
                return None
            return TransitionReceipt(
                receipt_id=row["receipt_id"],
                request_id=row["request_id"],
                plan_id=row["plan_id"],
                target_id=row["target_id"],
                previous_revision=row["previous_revision"],
                next_revision=row["next_revision"],
                status=TransitionStatus(row["status"]),
                committed_at=row["committed_at"],
                outbox_event_id=row["outbox_event_id"],
                evidence_refs=_string_tuple(
                    row["evidence_refs_json"]
                ),
            )

    def add_outbox_event(self, event: OutboxEvent) -> None:
            """Insert one canonical pending outbox event."""

            self._db().execute(
                """
                INSERT INTO outbox_events (
                    event_id,
                    protocol_major,
                    protocol_minor,
                    schema_version,
                    correlation_id,
                    occurred_at,
                    event_kind,
                    payload_json,
                    request_id,
                    round_id,
                    evidence_refs_json,
                    predecessor_digest,
                    recovery_context_json,
                    transition_receipt_id,
                    topic,
                    state,
                    created_at,
                    claimed_by,
                    claim_attempt_id,
                    claimed_at,
                    consumed_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    event.event_id,
                    event.protocol_major,
                    event.protocol_minor,
                    event.schema_version,
                    event.correlation_id,
                    event.occurred_at,
                    event.event_kind,
                    _json_text(event.payload),
                    event.request_id,
                    event.round_id,
                    _json_text(event.evidence_refs),
                    event.predecessor_digest,
                    (
                        _json_text(event.recovery_context)
                        if event.recovery_context is not None
                        else None
                    ),
                    event.transition_receipt_id,
                    event.topic,
                    event.state.value,
                    event.created_at,
                    event.claimed_by,
                    event.claim_attempt_id,
                    event.claimed_at,
                    event.consumed_at,
                ),
            )

    def get_outbox_event(
            self,
            event_id: str,
        ) -> OutboxEvent | None:
            """Return one canonical outbox event."""

            row = self._db().execute(
                """
                SELECT *
                FROM outbox_events
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
            return None if row is None else self._outbox_from_row(row)

    def list_outbox_events(
            self,
            states: tuple[OutboxState, ...],
            *,
            limit: int,
            governance_only: bool = False,
            after_position: int = 0,
        ) -> tuple[OutboxEvent, ...]:
            """Return matching events in workspace outbox order."""

            if not states:
                return ()
            if type(after_position) is not int or after_position < 0:
                raise ValueError(
                    "after_position must be a nonnegative integer"
                )
            if type(limit) is not int or limit < 1:
                raise ValueError("limit must be a positive integer")
            placeholders = ", ".join("?" for _ in states)
            governance_clause = (
                "AND transition_receipt_id IS NOT NULL"
                if governance_only
                else ""
            )
            parameters = tuple(
                state.value for state in states
            ) + (after_position, limit)
            rows = self._db().execute(
                f"""
                SELECT *
                FROM outbox_events
                WHERE state IN ({placeholders})
                AND outbox_position > ?
                {governance_clause}
                ORDER BY outbox_position
                LIMIT ?
                """,
                parameters,
            ).fetchall()
            return tuple(self._outbox_from_row(row) for row in rows)

    def list_outbox_events_by_command(
            self,
            command_id: str,
        ) -> tuple[OutboxEvent, ...]:
            """Return all outbox events for a given command_id, ordered by position."""
            rows = self._db().execute(
                """
                SELECT *
                FROM outbox_events
                WHERE json_extract(payload_json, '$.command_id') = ?
                ORDER BY outbox_position ASC
                """,
                (command_id,),
            ).fetchall()
            return tuple(self._outbox_from_row(row) for row in rows)

    def claim_outbox_event(
            self,
            event_id: str,
            owner_id: str,
            attempt_id: str,
            claimed_at: int,
        ) -> OutboxEvent | None:
            """CAS-claim one pending outbox event."""

            cursor = self._db().execute(
                """
                UPDATE outbox_events
                SET
                    state = ?,
                    claimed_by = ?,
                    claim_attempt_id = ?,
                    claimed_at = ?
                WHERE event_id = ? AND state = ?
                """,
                (
                    OutboxState.CLAIMED.value,
                    owner_id,
                    attempt_id,
                    claimed_at,
                    event_id,
                    OutboxState.PENDING.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
            return self.get_outbox_event(event_id)

    def mark_outbox_consumed(
            self,
            event_id: str,
            owner_id: str,
            attempt_id: str,
            consumed_at: int,
        ) -> bool:
            """CAS-mark a claimed event consumed."""

            cursor = self._db().execute(
                """
                UPDATE outbox_events
                SET state = ?, consumed_at = ?
                WHERE
                    event_id = ?
                    AND state = ?
                    AND claimed_by = ?
                    AND claim_attempt_id = ?
                """,
                (
                    OutboxState.CONSUMED.value,
                    consumed_at,
                    event_id,
                    OutboxState.CLAIMED.value,
                    owner_id,
                    attempt_id,
                ),
            )
            return cursor.rowcount == 1

    def get_outbox_checkpoint(
            self,
            consumer_id: str,
        ) -> OutboxCheckpoint | None:
            """Return a consumer's revisioned outbox checkpoint."""

            row = self._db().execute(
                """
                SELECT
                    consumer_id,
                    outbox_position,
                    event_id,
                    revision
                FROM outbox_checkpoints
                WHERE consumer_id = ?
                """,
                (consumer_id,),
            ).fetchone()
            if row is None:
                return None
            return OutboxCheckpoint(
                consumer_id=row["consumer_id"],
                outbox_position=row["outbox_position"],
                event_id=row["event_id"],
                revision=row["revision"],
            )

    def add_outbox_checkpoint(
            self,
            checkpoint: OutboxCheckpoint,
        ) -> None:
            """Insert a consumer's initial checkpoint."""

            self._db().execute(
                """
                INSERT INTO outbox_checkpoints (
                    consumer_id,
                    outbox_position,
                    event_id,
                    revision
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    checkpoint.consumer_id,
                    checkpoint.outbox_position,
                    checkpoint.event_id,
                    checkpoint.revision,
                ),
            )

    def cas_update_outbox_checkpoint(
            self,
            current: OutboxCheckpoint,
            updated: OutboxCheckpoint,
        ) -> bool:
            """CAS-advance a checkpoint using its stored revision."""

            if current.consumer_id != updated.consumer_id:
                raise ValueError(
                    "checkpoint consumer IDs do not match"
                )
            if updated.revision != current.revision + 1:
                raise ValueError(
                    "checkpoint revision must advance by one"
                )
            if updated.outbox_position < current.outbox_position:
                raise ValueError(
                    "checkpoint position cannot move backwards"
                )

            cursor = self._db().execute(
                """
                UPDATE outbox_checkpoints
                SET
                    outbox_position = ?,
                    event_id = ?,
                    revision = ?
                WHERE
                    consumer_id = ?
                    AND revision = ?
                    AND outbox_position = ?
                    AND event_id = ?
                """,
                (
                    updated.outbox_position,
                    updated.event_id,
                    updated.revision,
                    current.consumer_id,
                    current.revision,
                    current.outbox_position,
                    current.event_id,
                ),
            )
            return cursor.rowcount == 1

    def add_effect_receipt(
            self,
            receipt: EffectReceipt,
        ) -> None:
            """Insert one immutable terminal effect receipt."""

            self._db().execute(
                """
                INSERT INTO effect_receipts (
                    effect_receipt_id,
                    request_id,
                    outbox_event_id,
                    attempt_id,
                    owner_id,
                    outcome,
                    completed_at,
                    evidence_refs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.effect_receipt_id,
                    receipt.request_id,
                    receipt.outbox_event_id,
                    receipt.attempt_id,
                    receipt.owner_id,
                    receipt.outcome.value,
                    receipt.completed_at,
                    _json_text(receipt.evidence_refs),
                ),
            )

    def get_effect_receipt(
            self,
            outbox_event_id: str,
        ) -> EffectReceipt | None:
            """Return an outbox event's immutable terminal receipt."""

            row = self._db().execute(
                """
                SELECT
                    effect_receipt_id,
                    request_id,
                    outbox_event_id,
                    attempt_id,
                    owner_id,
                    outcome,
                    completed_at,
                    evidence_refs_json
                FROM effect_receipts
                WHERE outbox_event_id = ?
                """,
                (outbox_event_id,),
            ).fetchone()
            if row is None:
                return None
            return EffectReceipt(
                effect_receipt_id=row["effect_receipt_id"],
                request_id=row["request_id"],
                outbox_event_id=row["outbox_event_id"],
                attempt_id=row["attempt_id"],
                owner_id=row["owner_id"],
                outcome=EffectOutcome(row["outcome"]),
                completed_at=row["completed_at"],
                evidence_refs=_string_tuple(
                    row["evidence_refs_json"]
                ),
            )


    def _outbox_from_row(self, row: sqlite3.Row) -> OutboxEvent:
        return OutboxEvent(
            event_id=row["event_id"],
            protocol_major=row["protocol_major"],
            protocol_minor=row["protocol_minor"],
            schema_version=row["schema_version"],
            correlation_id=row["correlation_id"],
            occurred_at=row["occurred_at"],
            event_kind=row["event_kind"],
            payload=_json_object(row["payload_json"]),
            state=OutboxState(row["state"]),
            created_at=row["created_at"],
            request_id=row["request_id"],
            transition_receipt_id=(
                row["transition_receipt_id"]
            ),
            topic=row["topic"],
            outbox_position=row["outbox_position"],
            round_id=row["round_id"],
            evidence_refs=_string_tuple(
                row["evidence_refs_json"]
            ),
            predecessor_digest=row["predecessor_digest"],
            recovery_context=_optional_json_object(
                row["recovery_context_json"]
            ),
            claimed_by=row["claimed_by"],
            claim_attempt_id=row["claim_attempt_id"],
            claimed_at=row["claimed_at"],
            consumed_at=row["consumed_at"],
        )
