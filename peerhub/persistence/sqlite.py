"""SQLite WAL implementation of the PeerHub state-store port.

Migrations are version-guarded and applied exactly once. Migration 0003
fails closed when pre-Slice-3 lease rows exist because no ratified source
can supply their missing command, attempt, or authority-epoch identities.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from types import TracebackType
from typing import Any

from peerhub.core.errors import WorkspaceIdentityMismatchError
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import (
    CommandID,
    ErrorCode,
    canonical_json_bytes,
)
from peerhub.dispatch.contract import (
    AdmissionReceipt,
    AskResult,
    AttemptSnapshot,
    ClientRequestBinding,
    CommandIdempotencyBinding,
    CompletionAssessment,
    CompletionAssessmentState,
    CompletionContract,
    CompletionContractKind,
    ExecutionOutcome,
    LeaseAuthorityCertainty,
    LeaseFenceTuple,
    LeaseSnapshot,
    LeaseState,
    OutboxCheckpoint,
    ProcessBirthIdentity,
    ProtocolAssessment,
    RecoveryDecision,
    RecoveryReceipt,
    RecoveryTrigger,
    RequestSnapshot,
    RequestState,
    SessionBindingKey,
    SessionBindingSnapshot,
    SessionBindingState,
)
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


def _json_text(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _json_value(raw: str) -> Any:
    return json.loads(raw)


def _json_object(raw: str) -> dict[str, Any]:
    value = _json_value(raw)
    if not isinstance(value, dict):
        raise RuntimeError("stored JSON value is not an object")
    return value


def _optional_json_object(
    raw: str | None,
) -> dict[str, Any] | None:
    if raw is None:
        return None
    return _json_object(raw)


def _string_tuple(raw: str) -> tuple[str, ...]:
    value = _json_value(raw)
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) for item in value)
    ):
        raise RuntimeError(
            "stored evidence_refs is not a string array"
        )
    return tuple(value)


def _stored_revision(raw: str) -> str | int:
    value = _json_value(raw)
    if type(value) is int or isinstance(value, str):
        return value
    raise RuntimeError("stored revision is not a string or integer")


def _stored_optional_revision(
    raw: str,
) -> str | int | None:
    value = _json_value(raw)
    if value is None or type(value) is int or isinstance(value, str):
        return value
    raise RuntimeError(
        "stored expected revision has an invalid type"
    )


def _completion_contract_data(
    contract: CompletionContract,
) -> Mapping[str, object]:
    return contract.canonical_projection()


def _completion_contract_from_raw(
    raw: str,
) -> CompletionContract:
    value = _json_object(raw)
    requirements = value.get("requirements")
    if not isinstance(requirements, list) or any(
        not isinstance(item, dict)
        for item in requirements
    ):
        raise RuntimeError(
            "stored completion requirements are invalid"
        )
    return CompletionContract(
        contract_id=str(value["contract_id"]),
        kind=CompletionContractKind(str(value["kind"])),
        requirements=tuple(requirements),
        replay_safe=bool(value["replay_safe"]),
    )


def _ask_result_data(result: AskResult) -> Mapping[str, object]:
    return {
        "execution": {
            "started": result.execution.started,
            "exit_code": result.execution.exit_code,
            "timed_out": result.execution.timed_out,
            "cancelled": result.execution.cancelled,
            "execution_certainty": (
                result.execution.execution_certainty.value
            ),
        },
        "protocol": {
            "parsed": result.protocol.parsed,
            "response_present": result.protocol.response_present,
            "vendor_completion_marker": (
                result.protocol.vendor_completion_marker
            ),
            "suspected_truncation": (
                result.protocol.suspected_truncation
            ),
            "protocol_failure": (
                result.protocol.protocol_failure.value
                if result.protocol.protocol_failure is not None
                else None
            ),
        },
        "completion": {
            "state": result.completion.state.value,
            "failed_requirements": (
                result.completion.failed_requirements
            ),
            "evidence_refs": result.completion.evidence_refs,
        },
        "policy_revision": result.policy_revision,
    }


def _ask_result_from_raw(raw: str) -> AskResult:
    value = _json_object(raw)
    execution = value.get("execution")
    protocol = value.get("protocol")
    completion = value.get("completion")
    if not isinstance(execution, dict):
        raise RuntimeError("stored execution outcome is invalid")
    if not isinstance(protocol, dict):
        raise RuntimeError("stored protocol assessment is invalid")
    if not isinstance(completion, dict):
        raise RuntimeError("stored completion assessment is invalid")

    raw_failure = protocol.get("protocol_failure")
    failure = (
        ErrorCode(str(raw_failure))
        if raw_failure is not None
        else None
    )
    failed_requirements = completion.get(
        "failed_requirements"
    )
    evidence_refs = completion.get("evidence_refs")
    if not isinstance(failed_requirements, list):
        raise RuntimeError(
            "stored failed_requirements is invalid"
        )
    if not isinstance(evidence_refs, list):
        raise RuntimeError("stored evidence_refs is invalid")

    policy_revision = value.get("policy_revision")
    if not (
        type(policy_revision) is int
        or isinstance(policy_revision, str)
    ):
        raise RuntimeError(
            "stored AskResult policy revision is invalid"
        )

    return AskResult(
        execution=ExecutionOutcome(
            started=bool(execution["started"]),
            exit_code=execution.get("exit_code"),
            timed_out=bool(execution["timed_out"]),
            cancelled=bool(execution["cancelled"]),
            execution_certainty=ExecutionCertainty(
                str(execution["execution_certainty"])
            ),
        ),
        protocol=ProtocolAssessment(
            parsed=bool(protocol["parsed"]),
            response_present=bool(
                protocol["response_present"]
            ),
            vendor_completion_marker=protocol.get(
                "vendor_completion_marker"
            ),
            suspected_truncation=bool(
                protocol["suspected_truncation"]
            ),
            protocol_failure=failure,
        ),
        completion=CompletionAssessment(
            state=CompletionAssessmentState(
                str(completion["state"])
            ),
            failed_requirements=tuple(
                str(item) for item in failed_requirements
            ),
            evidence_refs=tuple(
                str(item) for item in evidence_refs
            ),
        ),
        policy_revision=policy_revision,
    )


class SqliteStateStore:
    """One local SQLite database bound to one workspace identity."""

    def __init__(
        self,
        database_path: Path,
        *,
        workspace_home_id: str,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if not workspace_home_id.strip():
            raise ValueError(
                "workspace_home_id must be non-empty"
            )
        if (
            type(busy_timeout_ms) is not int
            or busy_timeout_ms < 1
        ):
            raise ValueError(
                "busy_timeout_ms must be a positive integer"
            )

        self._database_path = database_path
        self._workspace_home_id = workspace_home_id
        self._busy_timeout_ms = busy_timeout_ms

    @property
    def database_path(self) -> Path:
        """Return the database file owned by this store."""

        return self._database_path

    def initialize(self) -> None:
        """Apply each schema migration once and bind workspace identity."""

        self._database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        connection = self._connect()
        try:
            if not self._table_exists(
                connection,
                "schema_migrations",
            ):
                connection.executescript(
                    self._migration_text(
                        "0001_phase1_kernel.sql"
                    )
                )

            versions = self._migration_versions(connection)

            if 1 not in versions:
                raise RuntimeError(
                    "schema_migrations is missing migration 1"
                )

            if 2 not in versions:
                migration_2 = self._migration_text(
                    "0002_dispatch_session_lease.sql"
                )
                connection.executescript(
                    "\n".join(
                        (
                            "BEGIN IMMEDIATE;",
                            migration_2,
                            (
                                "INSERT INTO schema_migrations"
                                "(version, name) VALUES "
                                "(2, "
                                "'0002_dispatch_session_lease');"
                            ),
                            "PRAGMA user_version = 2;",
                            "COMMIT;",
                        )
                    )
                )

            versions = self._migration_versions(connection)
            if 3 not in versions:
                connection.executescript(
                    self._migration_text(
                        "0003_command_request_attempt.sql"
                    )
                )

            violations = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if violations:
                raise RuntimeError(
                    "database contains foreign-key violations"
                )

            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT workspace_home_id
                    FROM workspace_identity
                    WHERE singleton = 1
                    """
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO workspace_identity (
                            singleton,
                            workspace_home_id
                        ) VALUES (1, ?)
                        """,
                        (self._workspace_home_id,),
                    )
                elif row["workspace_home_id"] != (
                    self._workspace_home_id
                ):
                    raise WorkspaceIdentityMismatchError(
                        self._workspace_home_id,
                        row["workspace_home_id"],
                    )
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        finally:
            connection.close()

    def unit_of_work(self) -> SqliteUnitOfWork:
        """Return a new SQLite unit of work."""

        return SqliteUnitOfWork(self)

    def close(self) -> None:
        """Release store resources.

        Connections are transaction-scoped, so there is no retained
        connection to close.
        """

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self._database_path),
            isolation_level=None,
            timeout=self._busy_timeout_ms / 1_000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            f"PRAGMA busy_timeout = {self._busy_timeout_ms}"
        )
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _table_exists(
        connection: sqlite3.Connection,
        table_name: str,
    ) -> bool:
        row = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _migration_versions(
        connection: sqlite3.Connection,
    ) -> frozenset[int]:
        rows = connection.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()
        return frozenset(int(row["version"]) for row in rows)

    @staticmethod
    def _migration_text(name: str) -> str:
        return (
            resources.files("peerhub.persistence.migrations")
            .joinpath(name)
            .read_text(encoding="utf-8")
        )


class SqliteUnitOfWork:
    """One `BEGIN IMMEDIATE` SQLite transaction."""

    def __init__(self, store: SqliteStateStore) -> None:
        self._store = store
        self._connection: sqlite3.Connection | None = None
        self._finished = False

    def __enter__(self) -> SqliteUnitOfWork:
        """Open a connection and begin an immediate transaction."""

        if self._connection is not None:
            raise RuntimeError(
                "SQLite unit of work cannot be re-entered"
            )
        self._connection = self._store._connect()
        self._connection.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back uncommitted work and close the connection."""

        del exception_type, exception, traceback
        connection = self._connection
        if connection is None:
            return
        try:
            if not self._finished and connection.in_transaction:
                connection.execute("ROLLBACK")
        finally:
            connection.close()
            self._connection = None

    def commit(self) -> None:
        """Commit the complete transaction."""

        connection = self._db()
        if self._finished:
            raise RuntimeError(
                "SQLite unit of work is already finished"
            )
        connection.execute("COMMIT")
        self._finished = True

    def rollback(self) -> None:
        """Roll back the complete transaction."""

        connection = self._db()
        if self._finished:
            raise RuntimeError(
                "SQLite unit of work is already finished"
            )
        connection.execute("ROLLBACK")
        self._finished = True

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
    ) -> tuple[OutboxEvent, ...]:
        """Return matching events in workspace outbox order."""

        if not states:
            return ()
        placeholders = ", ".join("?" for _ in states)
        governance_clause = (
            "AND transition_receipt_id IS NOT NULL"
            if governance_only
            else ""
        )
        parameters = tuple(state.value for state in states) + (
            limit,
        )
        rows = self._db().execute(
            f"""
            SELECT *
            FROM outbox_events
            WHERE state IN ({placeholders})
            {governance_clause}
            ORDER BY outbox_position
            LIMIT ?
            """,
            parameters,
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

    def get_client_request_binding(
        self,
        client_id: str,
        client_request_id: str,
    ) -> ClientRequestBinding | None:
        """Return a caller-request identity binding."""

        row = self._db().execute(
            """
            SELECT
                client_id,
                client_request_id,
                payload_digest,
                command_id,
                admission_receipt_id,
                created_at
            FROM client_request_bindings
            WHERE client_id = ? AND client_request_id = ?
            """,
            (client_id, client_request_id),
        ).fetchone()
        if row is None:
            return None
        return ClientRequestBinding(
            client_id=row["client_id"],
            client_request_id=row["client_request_id"],
            payload_digest=row["payload_digest"],
            command_id=CommandID(row["command_id"]),
            admission_receipt_id=row[
                "admission_receipt_id"
            ],
            created_at=row["created_at"],
        )

    def add_client_request_binding(
        self,
        binding: ClientRequestBinding,
    ) -> None:
        """Insert an immutable caller-request identity."""

        self._db().execute(
            """
            INSERT INTO client_request_bindings (
                client_id,
                client_request_id,
                payload_digest,
                command_id,
                admission_receipt_id,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                binding.client_id,
                binding.client_request_id,
                binding.payload_digest,
                str(binding.command_id),
                binding.admission_receipt_id,
                binding.created_at,
            ),
        )

    def get_command_idempotency_binding(
        self,
        client_id: str,
        command_type: str,
        idempotency_key: str,
    ) -> CommandIdempotencyBinding | None:
        """Return a Slice 3 idempotency-key binding."""

        row = self._db().execute(
            """
            SELECT
                client_id,
                command_type,
                idempotency_key,
                payload_digest,
                command_id,
                admission_receipt_id,
                created_at
            FROM command_idempotency_bindings
            WHERE
                client_id = ?
                AND command_type = ?
                AND idempotency_key = ?
            """,
            (client_id, command_type, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        return CommandIdempotencyBinding(
            client_id=row["client_id"],
            command_type=row["command_type"],
            idempotency_key=row["idempotency_key"],
            payload_digest=row["payload_digest"],
            command_id=CommandID(row["command_id"]),
            admission_receipt_id=row[
                "admission_receipt_id"
            ],
            created_at=row["created_at"],
        )

    def add_command_idempotency_binding(
        self,
        binding: CommandIdempotencyBinding,
    ) -> None:
        """Insert an immutable Slice 3 idempotency binding."""

        self._db().execute(
            """
            INSERT INTO command_idempotency_bindings (
                client_id,
                command_type,
                idempotency_key,
                payload_digest,
                command_id,
                admission_receipt_id,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                binding.client_id,
                binding.command_type,
                binding.idempotency_key,
                binding.payload_digest,
                str(binding.command_id),
                binding.admission_receipt_id,
                binding.created_at,
            ),
        )

    def add_admission_receipt(
        self,
        receipt: AdmissionReceipt,
    ) -> None:
        """Insert an immutable admission receipt."""

        self._db().execute(
            """
            INSERT INTO admission_receipts (
                admission_receipt_id,
                command_id,
                client_id,
                client_request_id,
                command_type,
                idempotency_key,
                payload_digest,
                completion_contract_id,
                lease_id,
                policy_revision_json,
                configuration_revision_json,
                admitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.admission_receipt_id,
                str(receipt.command_id),
                receipt.client_id,
                receipt.client_request_id,
                receipt.command_type,
                receipt.idempotency_key,
                receipt.payload_digest,
                receipt.completion_contract_id,
                receipt.lease_id,
                _json_text(receipt.policy_revision),
                _json_text(receipt.configuration_revision),
                receipt.admitted_at,
            ),
        )

    def get_admission_receipt(
        self,
        admission_receipt_id: str,
    ) -> AdmissionReceipt | None:
        """Return an admission receipt by ID."""

        row = self._db().execute(
            """
            SELECT *
            FROM admission_receipts
            WHERE admission_receipt_id = ?
            """,
            (admission_receipt_id,),
        ).fetchone()
        if row is None:
            return None
        return AdmissionReceipt(
            admission_receipt_id=row["admission_receipt_id"],
            command_id=CommandID(row["command_id"]),
            client_id=row["client_id"],
            client_request_id=row["client_request_id"],
            command_type=row["command_type"],
            idempotency_key=row["idempotency_key"],
            payload_digest=row["payload_digest"],
            completion_contract_id=row[
                "completion_contract_id"
            ],
            lease_id=row["lease_id"],
            policy_revision=_stored_revision(
                row["policy_revision_json"]
            ),
            configuration_revision=_stored_revision(
                row["configuration_revision_json"]
            ),
            admitted_at=row["admitted_at"],
        )

    def add_request(self, request: RequestSnapshot) -> None:
        """Insert an admitted request snapshot."""

        self._db().execute(
            """
            INSERT INTO dispatch_requests (
                command_id,
                client_id,
                client_request_id,
                correlation_id,
                authenticated_principal,
                command_type,
                idempotency_key,
                payload_digest,
                scope_json,
                params_json,
                expected_policy_revision_json,
                expected_configuration_revision_json,
                policy_revision_json,
                configuration_revision_json,
                completion_contract_json,
                selected_peer_instance_id,
                selected_profile_id,
                route_decision_digest,
                lease_id,
                state,
                revision,
                created_at,
                updated_at,
                terminal_error_code
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            self._request_values(request),
        )

    def get_request(
        self,
        command_id: CommandID | str,
    ) -> RequestSnapshot | None:
        """Return a request snapshot by server command ID."""

        row = self._db().execute(
            """
            SELECT *
            FROM dispatch_requests
            WHERE command_id = ?
            """,
            (str(command_id),),
        ).fetchone()
        return None if row is None else self._request_from_row(row)

    def cas_update_request(
        self,
        current: RequestSnapshot,
        updated: RequestSnapshot,
    ) -> bool:
        """CAS-update a request by command ID and revision."""

        if current.command_id != updated.command_id:
            raise ValueError("request command IDs do not match")
        cursor = self._db().execute(
            """
            UPDATE dispatch_requests
            SET
                lease_id = ?,
                state = ?,
                revision = ?,
                updated_at = ?,
                terminal_error_code = ?
            WHERE command_id = ? AND revision = ?
            """,
            (
                updated.lease_id,
                updated.state.value,
                updated.revision,
                updated.updated_at,
                (
                    updated.terminal_error_code.value
                    if updated.terminal_error_code is not None
                    else None
                ),
                str(current.command_id),
                current.revision,
            ),
        )
        return cursor.rowcount == 1

    def next_attempt_number(
        self,
        command_id: CommandID | str,
    ) -> int:
        """Return the next monotonic attempt number in this transaction."""

        row = self._db().execute(
            """
            SELECT COALESCE(MAX(attempt_number), 0) + 1 AS next_number
            FROM dispatch_attempts
            WHERE command_id = ?
            """,
            (str(command_id),),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                "failed to allocate attempt number"
            )
        return int(row["next_number"])

    def add_attempt(self, attempt: AttemptSnapshot) -> None:
        """Insert a revision-one dispatch attempt."""

        self._db().execute(
            """
            INSERT INTO dispatch_attempts (
                attempt_id,
                command_id,
                attempt_number,
                lease_id,
                state,
                execution_certainty,
                revision,
                reconciliation_complete,
                result_json,
                terminal_error_code,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._attempt_values(attempt),
        )

    def get_attempt(
        self,
        attempt_id: str,
    ) -> AttemptSnapshot | None:
        """Return an attempt by server attempt ID."""

        row = self._db().execute(
            """
            SELECT *
            FROM dispatch_attempts
            WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()
        return None if row is None else self._attempt_from_row(row)

    def list_attempts(
        self,
        command_id: CommandID | str,
    ) -> tuple[AttemptSnapshot, ...]:
        """Return command attempts in monotonic attempt order."""

        rows = self._db().execute(
            """
            SELECT *
            FROM dispatch_attempts
            WHERE command_id = ?
            ORDER BY attempt_number
            """,
            (str(command_id),),
        ).fetchall()
        return tuple(self._attempt_from_row(row) for row in rows)

    def cas_update_attempt(
        self,
        current: AttemptSnapshot,
        updated: AttemptSnapshot,
    ) -> bool:
        """CAS-update an attempt by ID and revision."""

        if current.attempt_id != updated.attempt_id:
            raise ValueError("attempt IDs do not match")
        cursor = self._db().execute(
            """
            UPDATE dispatch_attempts
            SET
                state = ?,
                execution_certainty = ?,
                revision = ?,
                reconciliation_complete = ?,
                result_json = ?,
                terminal_error_code = ?,
                updated_at = ?
            WHERE
                attempt_id = ?
                AND command_id = ?
                AND revision = ?
            """,
            (
                updated.state.value,
                updated.execution_certainty.value,
                updated.revision,
                int(updated.reconciliation_complete),
                (
                    _json_text(_ask_result_data(updated.result))
                    if updated.result is not None
                    else None
                ),
                (
                    updated.terminal_error_code.value
                    if updated.terminal_error_code is not None
                    else None
                ),
                updated.updated_at,
                current.attempt_id,
                str(current.command_id),
                current.revision,
            ),
        )
        return cursor.rowcount == 1

    def allocate_fencing_token(self) -> int:
        """Allocate one database-monotonic lease fencing token."""

        cursor = self._db().execute(
            "INSERT INTO lease_fencing_sequence DEFAULT VALUES"
        )
        token = cursor.lastrowid
        if token is None:
            raise RuntimeError(
                "failed to allocate lease fencing token"
            )
        return int(token)

    def get_lease(self, lease_id: str) -> LeaseSnapshot | None:
        """Return a lease snapshot by ID."""

        row = self._db().execute(
            """
            SELECT *
            FROM leases
            WHERE lease_id = ?
            """,
            (lease_id,),
        ).fetchone()
        if row is None:
            return None

        process_identity = None
        if row["owner_process_pid"] is not None:
            process_identity = ProcessBirthIdentity(
                pid=row["owner_process_pid"],
                process_creation_time=(
                    row["owner_process_creation_time"]
                ),
            )

        fence = LeaseFenceTuple(
            session_id=row["session_id"],
            lease_id=row["lease_id"],
            fencing_token=row["fencing_token"],
            revision=row["revision"],
            owner_principal_id=row["owner_principal_id"],
            owner_instance_id=row["owner_instance_id"],
            owner_process_birth_identity=process_identity,
            command_id=CommandID(row["command_id"]),
            authority_epoch=row["authority_epoch"],
            attempt_id=row["attempt_id"],
            owner_peer_id=row["owner_peer_id"],
        )
        return LeaseSnapshot(
            lease_id=row["lease_id"],
            session_id=row["session_id"],
            fence=fence,
            state=LeaseState(row["state"]),
            heartbeat_expires_at=row["heartbeat_expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def add_lease(self, lease: LeaseSnapshot) -> None:
        """Insert a new lease snapshot."""

        process = lease.fence.owner_process_birth_identity
        self._db().execute(
            """
            INSERT INTO leases (
                lease_id,
                session_id,
                command_id,
                attempt_id,
                fencing_token,
                authority_epoch,
                revision,
                owner_principal_id,
                owner_instance_id,
                owner_process_pid,
                owner_process_creation_time,
                owner_peer_id,
                state,
                heartbeat_expires_at,
                created_at,
                updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                lease.lease_id,
                lease.session_id,
                str(lease.fence.command_id),
                lease.fence.attempt_id,
                lease.fence.fencing_token,
                lease.fence.authority_epoch,
                lease.fence.revision,
                lease.fence.owner_principal_id,
                lease.fence.owner_instance_id,
                process.pid if process is not None else None,
                (
                    process.process_creation_time
                    if process is not None
                    else None
                ),
                lease.fence.owner_peer_id,
                lease.state.value,
                lease.heartbeat_expires_at,
                lease.created_at,
                lease.updated_at,
            ),
        )

    def cas_update_lease(
        self,
        current: LeaseSnapshot,
        updated: LeaseSnapshot,
    ) -> bool:
        """CAS-update using the complete persisted lease fence."""

        if current.lease_id != updated.lease_id:
            raise ValueError("lease IDs do not match")

        current_process = (
            current.fence.owner_process_birth_identity
        )
        updated_process = (
            updated.fence.owner_process_birth_identity
        )

        cursor = self._db().execute(
            """
            UPDATE leases
            SET
                attempt_id = ?,
                fencing_token = ?,
                authority_epoch = ?,
                revision = ?,
                owner_process_pid = ?,
                owner_process_creation_time = ?,
                state = ?,
                heartbeat_expires_at = ?,
                updated_at = ?
            WHERE
                lease_id = ?
                AND command_id = ?
                AND attempt_id IS ?
                AND fencing_token = ?
                AND authority_epoch = ?
                AND revision = ?
                AND owner_instance_id = ?
                AND owner_process_pid IS ?
                AND owner_process_creation_time IS ?
            """,
            (
                updated.fence.attempt_id,
                updated.fence.fencing_token,
                updated.fence.authority_epoch,
                updated.fence.revision,
                (
                    updated_process.pid
                    if updated_process is not None
                    else None
                ),
                (
                    updated_process.process_creation_time
                    if updated_process is not None
                    else None
                ),
                updated.state.value,
                updated.heartbeat_expires_at,
                updated.updated_at,
                current.lease_id,
                str(current.fence.command_id),
                current.fence.attempt_id,
                current.fence.fencing_token,
                current.fence.authority_epoch,
                current.fence.revision,
                current.fence.owner_instance_id,
                (
                    current_process.pid
                    if current_process is not None
                    else None
                ),
                (
                    current_process.process_creation_time
                    if current_process is not None
                    else None
                ),
            ),
        )
        return cursor.rowcount == 1

    def cas_update_dispatch_bundle(
        self,
        current_request: RequestSnapshot,
        updated_request: RequestSnapshot,
        current_attempt: AttemptSnapshot,
        updated_attempt: AttemptSnapshot,
        current_lease: LeaseSnapshot,
        updated_lease: LeaseSnapshot,
    ) -> bool:
        """Atomically CAS a request, attempt, and complete lease fence."""

        connection = self._db()
        connection.execute("SAVEPOINT dispatch_bundle")
        try:
            if not self.cas_update_lease(
                current_lease,
                updated_lease,
            ):
                connection.execute(
                    "ROLLBACK TO dispatch_bundle"
                )
                connection.execute("RELEASE dispatch_bundle")
                return False
            if not self.cas_update_attempt(
                current_attempt,
                updated_attempt,
            ):
                connection.execute(
                    "ROLLBACK TO dispatch_bundle"
                )
                connection.execute("RELEASE dispatch_bundle")
                return False
            if not self.cas_update_request(
                current_request,
                updated_request,
            ):
                connection.execute(
                    "ROLLBACK TO dispatch_bundle"
                )
                connection.execute("RELEASE dispatch_bundle")
                return False
            connection.execute("RELEASE dispatch_bundle")
            return True
        except BaseException:
            connection.execute("ROLLBACK TO dispatch_bundle")
            connection.execute("RELEASE dispatch_bundle")
            raise

    def get_session_binding(
        self,
        key: SessionBindingKey,
    ) -> SessionBindingSnapshot | None:
        """Return a session binding snapshot by canonical key."""

        row = self._db().execute(
            """
            SELECT
                workspace_scope_id,
                instance_id,
                profile_id,
                conversation_scope,
                session_id,
                current_lease_id,
                adapter_fingerprint,
                readiness_binding,
                session_generation,
                revision,
                state,
                updated_at
            FROM session_bindings
            WHERE
                workspace_scope_id = ?
                AND instance_id = ?
                AND profile_id = ?
                AND conversation_scope = ?
            """,
            (
                key.workspace_scope_id,
                key.instance_id,
                key.profile_id,
                key.conversation_scope,
            ),
        ).fetchone()
        if row is None:
            return None
        return SessionBindingSnapshot(
            key=key,
            session_id=row["session_id"],
            current_lease_id=row["current_lease_id"],
            adapter_fingerprint=row["adapter_fingerprint"],
            readiness_binding=row["readiness_binding"],
            session_generation=row["session_generation"],
            revision=row["revision"],
            state=SessionBindingState(row["state"]),
            updated_at=row["updated_at"],
        )

    def add_session_binding(
        self,
        binding: SessionBindingSnapshot,
    ) -> None:
        """Insert a new session binding."""

        self._db().execute(
            """
            INSERT INTO session_bindings (
                workspace_scope_id,
                instance_id,
                profile_id,
                conversation_scope,
                session_id,
                current_lease_id,
                adapter_fingerprint,
                readiness_binding,
                session_generation,
                revision,
                state,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                binding.key.workspace_scope_id,
                binding.key.instance_id,
                binding.key.profile_id,
                binding.key.conversation_scope,
                binding.session_id,
                binding.current_lease_id,
                binding.adapter_fingerprint,
                binding.readiness_binding,
                binding.session_generation,
                binding.revision,
                binding.state.value,
                binding.updated_at,
            ),
        )

    def cas_update_session_binding(
        self,
        current: SessionBindingSnapshot,
        updated: SessionBindingSnapshot,
    ) -> bool:
        """CAS-update a session binding by key and current revision."""

        cursor = self._db().execute(
            """
            UPDATE session_bindings
            SET
                current_lease_id = ?,
                revision = ?,
                state = ?,
                updated_at = ?
            WHERE
                workspace_scope_id = ?
                AND instance_id = ?
                AND profile_id = ?
                AND conversation_scope = ?
                AND revision = ?
            """,
            (
                updated.current_lease_id,
                updated.revision,
                updated.state.value,
                updated.updated_at,
                current.key.workspace_scope_id,
                current.key.instance_id,
                current.key.profile_id,
                current.key.conversation_scope,
                current.revision,
            ),
        )
        return cursor.rowcount == 1

    def add_recovery_receipt(
        self,
        receipt: RecoveryReceipt,
    ) -> None:
        """Insert an immutable recovery receipt."""

        self._db().execute(
            """
            INSERT INTO recovery_receipts (
                recovery_receipt_id,
                session_id,
                lease_id,
                detected_at,
                recovery_actor_principal_id,
                trigger,
                mismatch_dimensions_json,
                evidence_digest,
                policy_id,
                policy_revision,
                decision,
                certainty_before_policy,
                certainty_after_policy,
                external_effect_certainty,
                pre_lifecycle_state,
                pre_revision,
                pre_fencing_token,
                post_lifecycle_state,
                post_revision,
                post_fencing_token
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?
            )
            """,
            (
                receipt.recovery_receipt_id,
                receipt.session_id,
                receipt.lease_id,
                receipt.detected_at,
                receipt.recovery_actor_principal_id,
                receipt.trigger.value,
                _json_text(receipt.mismatch_dimensions),
                receipt.evidence_digest,
                receipt.policy_id,
                receipt.policy_revision,
                receipt.decision.value,
                receipt.certainty_before_policy.value,
                receipt.certainty_after_policy.value,
                (
                    receipt.external_effect_certainty.value
                    if receipt.external_effect_certainty
                    else None
                ),
                receipt.pre_lifecycle_state.value,
                receipt.pre_revision,
                receipt.pre_fencing_token,
                receipt.post_lifecycle_state.value,
                receipt.post_revision,
                receipt.post_fencing_token,
            ),
        )

    def get_recovery_receipt(
        self,
        receipt_id: str,
    ) -> RecoveryReceipt | None:
        """Return a recovery receipt by ID."""

        row = self._db().execute(
            """
            SELECT *
            FROM recovery_receipts
            WHERE recovery_receipt_id = ?
            """,
            (receipt_id,),
        ).fetchone()
        if row is None:
            return None
        raw_effect_certainty = row[
            "external_effect_certainty"
        ]
        effect_certainty = (
            ExecutionCertainty(raw_effect_certainty)
            if raw_effect_certainty
            else None
        )
        return RecoveryReceipt(
            recovery_receipt_id=row["recovery_receipt_id"],
            session_id=row["session_id"],
            lease_id=row["lease_id"],
            detected_at=row["detected_at"],
            recovery_actor_principal_id=row[
                "recovery_actor_principal_id"
            ],
            trigger=RecoveryTrigger(row["trigger"]),
            mismatch_dimensions=_string_tuple(
                row["mismatch_dimensions_json"]
            ),
            evidence_digest=row["evidence_digest"],
            policy_id=row["policy_id"],
            policy_revision=row["policy_revision"],
            decision=RecoveryDecision(row["decision"]),
            certainty_before_policy=LeaseAuthorityCertainty(
                row["certainty_before_policy"]
            ),
            certainty_after_policy=LeaseAuthorityCertainty(
                row["certainty_after_policy"]
            ),
            external_effect_certainty=effect_certainty,
            pre_lifecycle_state=LeaseState(
                row["pre_lifecycle_state"]
            ),
            pre_revision=row["pre_revision"],
            pre_fencing_token=row["pre_fencing_token"],
            post_lifecycle_state=LeaseState(
                row["post_lifecycle_state"]
            ),
            post_revision=row["post_revision"],
            post_fencing_token=row["post_fencing_token"],
        )

    def _db(self) -> sqlite3.Connection:
        connection = self._connection
        if connection is None:
            raise RuntimeError(
                "SQLite unit of work has not been entered"
            )
        if self._finished:
            raise RuntimeError(
                "SQLite unit of work is already finished"
            )
        return connection

    @staticmethod
    def _request_values(
        request: RequestSnapshot,
    ) -> tuple[object, ...]:
        return (
            str(request.command_id),
            request.client_id,
            request.client_request_id,
            request.correlation_id,
            request.authenticated_principal,
            request.command_type,
            request.idempotency_key,
            request.payload_digest,
            _json_text(request.scope),
            _json_text(request.params),
            _json_text(request.expected_policy_revision),
            _json_text(
                request.expected_configuration_revision
            ),
            _json_text(request.policy_revision),
            _json_text(request.configuration_revision),
            _json_text(
                _completion_contract_data(
                    request.completion_contract
                )
            ),
            request.selected_peer_instance_id,
            request.selected_profile_id,
            request.route_decision_digest,
            request.lease_id,
            request.state.value,
            request.revision,
            request.created_at,
            request.updated_at,
            (
                request.terminal_error_code.value
                if request.terminal_error_code is not None
                else None
            ),
        )

    @staticmethod
    def _request_from_row(
        row: sqlite3.Row,
    ) -> RequestSnapshot:
        terminal_code = row["terminal_error_code"]
        return RequestSnapshot(
            command_id=CommandID(row["command_id"]),
            client_id=row["client_id"],
            client_request_id=row["client_request_id"],
            correlation_id=row["correlation_id"],
            authenticated_principal=row[
                "authenticated_principal"
            ],
            command_type=row["command_type"],
            idempotency_key=row["idempotency_key"],
            payload_digest=row["payload_digest"],
            scope=_json_object(row["scope_json"]),
            params=_json_object(row["params_json"]),
            expected_policy_revision=(
                _stored_optional_revision(
                    row["expected_policy_revision_json"]
                )
            ),
            expected_configuration_revision=(
                _stored_optional_revision(
                    row[
                        "expected_configuration_revision_json"
                    ]
                )
            ),
            policy_revision=_stored_revision(
                row["policy_revision_json"]
            ),
            configuration_revision=_stored_revision(
                row["configuration_revision_json"]
            ),
            completion_contract=(
                _completion_contract_from_raw(
                    row["completion_contract_json"]
                )
            ),
            selected_peer_instance_id=row[
                "selected_peer_instance_id"
            ],
            selected_profile_id=row["selected_profile_id"],
            route_decision_digest=row[
                "route_decision_digest"
            ],
            lease_id=row["lease_id"],
            state=RequestState(row["state"]),
            revision=row["revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            terminal_error_code=(
                ErrorCode(terminal_code)
                if terminal_code is not None
                else None
            ),
        )

    @staticmethod
    def _attempt_values(
        attempt: AttemptSnapshot,
    ) -> tuple[object, ...]:
        return (
            attempt.attempt_id,
            str(attempt.command_id),
            attempt.attempt_number,
            attempt.lease_id,
            attempt.state.value,
            attempt.execution_certainty.value,
            attempt.revision,
            int(attempt.reconciliation_complete),
            (
                _json_text(_ask_result_data(attempt.result))
                if attempt.result is not None
                else None
            ),
            (
                attempt.terminal_error_code.value
                if attempt.terminal_error_code is not None
                else None
            ),
            attempt.created_at,
            attempt.updated_at,
        )

    @staticmethod
    def _attempt_from_row(
        row: sqlite3.Row,
    ) -> AttemptSnapshot:
        result_raw = row["result_json"]
        terminal_code = row["terminal_error_code"]
        return AttemptSnapshot(
            attempt_id=row["attempt_id"],
            command_id=CommandID(row["command_id"]),
            attempt_number=row["attempt_number"],
            lease_id=row["lease_id"],
            state=RequestState(row["state"]),
            execution_certainty=ExecutionCertainty(
                row["execution_certainty"]
            ),
            revision=row["revision"],
            reconciliation_complete=bool(
                row["reconciliation_complete"]
            ),
            result=(
                _ask_result_from_raw(result_raw)
                if result_raw is not None
                else None
            ),
            terminal_error_code=(
                ErrorCode(terminal_code)
                if terminal_code is not None
                else None
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _outbox_from_row(row: sqlite3.Row) -> OutboxEvent:
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
