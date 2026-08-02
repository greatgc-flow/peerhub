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

from peerhub.core.errors import (
    InvalidMutationError,
    RecoveryProbeGrantConflictError,
    WorkspaceIdentityMismatchError,
)
from peerhub.core.evidence import (
    EvidenceRef,
    EvidenceState,
    EvidenceValue,
)
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import (
    AttemptTerminalObserved,
    CommandID,
    ErrorCode,
    OperationalFailureCategory,
    canonical_json_bytes,
)
from peerhub.dispatch.contract import (
    AdmissionReceipt,
    ArtifactManifestRecord,
    ArtifactMetadata,
    ArtifactRecoveryDigest,
    ArtifactState,
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
from peerhub.health.contract import (
    AdmissionDecision,
    AdmissionSnapshot,
    AdmissionSnapshotEntry,
    AdmissionState,
    AvailabilityState,
    CircuitState,
    HealthCircuitSnapshot,
    HealthPolicy,
    HealthProjectionSnapshot,
    PolicyReceipt,
    PolicyScope,
    ProbeResult,
    QuarantineAuthorityClass,
    ReadinessEvaluation,
    ReadinessGateState,
    ReadinessState,
    RecoveryProbeGrant,
    RecoveryProbeReceipt,
    RevalidationAction,
)
from peerhub.routing.contract import (
    ConfigurationSnapshot,
    RouteCandidateDecision,
    RouteDecision,
    RouteEligibility,
)
from peerhub.telemetry.contract import (
    OperationalObservation,
    OperationalProjectionSnapshot,
    ReadinessMeasurement,
    ReadinessObserved,
    UsageMeasurement,
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


def _readiness_evaluation_data(
    evaluation: ReadinessEvaluation,
) -> dict[str, Any]:
    return {
        "readiness_state": evaluation.readiness_state.value,
        "availability_state": evaluation.availability_state.value,
        "gate_state": evaluation.gate_state.value,
        "admission_decision": (
            evaluation.admission_decision.value
        ),
        "provider_effect_permitted": (
            evaluation.provider_effect_permitted
        ),
        "reason_code": evaluation.reason_code,
        "revalidation_action": (
            evaluation.revalidation_action.value
            if evaluation.revalidation_action is not None
            else None
        ),
        "zero_dispatch_calls": evaluation.zero_dispatch_calls,
    }


def _readiness_evaluation_from_raw(
    raw: str | None,
) -> ReadinessEvaluation | None:
    if raw is None:
        return None
    data = _json_object(raw)
    raw_revalidation = data.get("revalidation_action")
    return ReadinessEvaluation(
        readiness_state=ReadinessState(
            data["readiness_state"]
        ),
        availability_state=AvailabilityState(
            data["availability_state"]
        ),
        gate_state=ReadinessGateState(
            data["gate_state"]
        ),
        admission_decision=AdmissionDecision(
            data["admission_decision"]
        ),
        provider_effect_permitted=data[
            "provider_effect_permitted"
        ],
        reason_code=data.get("reason_code"),
        revalidation_action=(
            RevalidationAction(raw_revalidation)
            if raw_revalidation is not None
            else None
        ),
        zero_dispatch_calls=data["zero_dispatch_calls"],
    )


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
            "contract_kind": (
                result.completion.contract_kind.value
            ),
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
            contract_kind=CompletionContractKind(
                str(completion["contract_kind"])
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

            versions = self._migration_versions(connection)
            if 4 not in versions:
                connection.executescript(
                    self._migration_text(
                        "0004_idempotency_aliases.sql"
                    )
                )

            versions = self._migration_versions(connection)
            if 5 not in versions:
                connection.executescript(
                    self._migration_text(
                        "0005_health_routing.sql"
                    )
                )

            versions = self._migration_versions(connection)
            if 6 not in versions:
                connection.executescript(
                    self._migration_text(
                        "0006_recovery_probe_single_flight.sql"
                    )
                )

            versions = self._migration_versions(connection)
            if 7 not in versions:
                connection.executescript(
                    self._migration_text(
                        "0007_health_projection_readiness_context.sql"
                    )
                )

            versions = self._migration_versions(connection)
            if 8 not in versions:
                connection.executescript(
                    self._migration_text(
                        "0008_dispatch_artifact_metadata.sql"
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

        attempt_bound_states = {
            RequestState.DISPATCH_INTENT,
            RequestState.START_UNCERTAIN,
            RequestState.RUNNING,
            RequestState.CANCELLING,
            RequestState.ASSESSING,
            RequestState.SUCCEEDED_VERIFIED,
            RequestState.DELIVERED_UNVERIFIED,
            RequestState.INCOMPLETE,
            RequestState.FAILED,
            RequestState.INTERRUPTED,
            RequestState.CANCELLED,
        }
        if updated_request.state in attempt_bound_states:
            if updated_lease.fence.attempt_id is None:
                raise InvalidMutationError(
                    "dispatch-or-later lease requires attempt_id"
                )
            if (
                updated_lease.fence.attempt_id
                != updated_attempt.attempt_id
            ):
                raise InvalidMutationError(
                    "lease attempt_id does not match dispatch attempt"
                )

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

    # ── Slice 4: health policy revisions ──

    def _health_policy_from_row(self, row: sqlite3.Row) -> HealthPolicy:
        backoff = tuple(_json_value(row["recovery_backoff_seconds_json"]))
        return HealthPolicy(
            policy_id=row["policy_id"],
            revision=row["revision"],
            readiness_freshness_seconds=row["readiness_freshness_seconds"],
            recovery_backoff_seconds=backoff,
            recovery_jitter_fraction=row["recovery_jitter_fraction"],
            readiness_observation_threshold=row["readiness_observation_threshold"],
            administrative_recovery_probe_limit=row["administrative_recovery_probe_limit"],
        )

    def add_health_policy_revision(
        self,
        policy: HealthPolicy,
        created_at: int = 0,
    ) -> None:
        """Insert a frozen health policy revision."""
        self._db().execute(
            """
            INSERT INTO health_policy_revisions (
                policy_id,
                revision,
                readiness_freshness_seconds,
                recovery_backoff_seconds_json,
                recovery_jitter_fraction,
                readiness_observation_threshold,
                administrative_recovery_probe_limit,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy.policy_id,
                policy.revision,
                policy.readiness_freshness_seconds,
                _json_text(list(policy.recovery_backoff_seconds)),
                policy.recovery_jitter_fraction,
                policy.readiness_observation_threshold,
                policy.administrative_recovery_probe_limit,
                created_at,
            ),
        )

    def get_health_policy_revision(
        self,
        policy_id: str,
        revision: int,
    ) -> HealthPolicy | None:
        """Return a health policy revision by ID and revision number."""
        row = self._db().execute(
            """
            SELECT *
            FROM health_policy_revisions
            WHERE policy_id = ? AND revision = ?
            """,
            (policy_id, revision),
        ).fetchone()
        return None if row is None else self._health_policy_from_row(row)

    # ── Slice 4: readiness observations ──

    def _readiness_observation_from_row(
        self,
        row: sqlite3.Row,
    ) -> ReadinessObserved:
        measurement = None
        if row["runtime_revision"] is not None:
            measurement = ReadinessMeasurement(
                runtime_revision=row["runtime_revision"],
                issued_at=row["issued_at"],
                valid_until=row["valid_until"],
                integrity_verified=bool(row["integrity_verified"]),
            )
        evidence = EvidenceValue(
            state=EvidenceState(row["evidence_state"]),
            source_tag=row["source_tag"],
            provider_id=row["provider_id"],
            provider_version=row["provider_version"],
            observed_at=row["observed_at"],
            captured_at=row["captured_at"],
            freshness_ttl=row["freshness_ttl"],
            evidence_ref=EvidenceRef(row["evidence_ref"]),
            value=measurement,
        )
        return ReadinessObserved(
            observation_id=row["observation_id"],
            instance_id=row["instance_id"],
            profile_id=row["profile_id"],
            evidence=evidence,
        )

    def add_readiness_observation(
        self,
        observed: ReadinessObserved,
    ) -> None:
        """Insert an immutable readiness observation."""
        ev = observed.evidence
        val = ev.value
        self._db().execute(
            """
            INSERT INTO readiness_observations (
                observation_id,
                instance_id,
                profile_id,
                evidence_state,
                source_tag,
                provider_id,
                provider_version,
                observed_at,
                captured_at,
                freshness_ttl,
                evidence_ref,
                runtime_revision,
                issued_at,
                valid_until,
                integrity_verified
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observed.observation_id,
                observed.instance_id,
                observed.profile_id,
                ev.state.value,
                ev.source_tag,
                ev.provider_id,
                ev.provider_version,
                ev.observed_at,
                ev.captured_at,
                ev.freshness_ttl,
                str(ev.evidence_ref),
                val.runtime_revision if val is not None else None,
                val.issued_at if val is not None else None,
                val.valid_until if val is not None else None,
                int(val.integrity_verified) if val is not None else None,
            ),
        )

    def get_readiness_observation(
        self,
        observation_id: str,
    ) -> ReadinessObserved | None:
        """Return a readiness observation by observation ID."""
        row = self._db().execute(
            """
            SELECT *
            FROM readiness_observations
            WHERE observation_id = ?
            """,
            (observation_id,),
        ).fetchone()
        return None if row is None else self._readiness_observation_from_row(row)

    # ── Slice 4: operational observations ──

    def _operational_observation_from_row(
        self,
        row: sqlite3.Row,
    ) -> OperationalObservation:
        cat = (
            OperationalFailureCategory(row["operational_failure_category"])
            if row["operational_failure_category"] is not None
            else None
        )
        terminal_event = AttemptTerminalObserved(
            instance_id=row["instance_id"],
            profile_id=row["profile_id"],
            transport=row["transport"],
            operational_failure_category=cat,
            execution_certainty=ExecutionCertainty(row["execution_certainty"]),
            process_integrity=bool(row["process_integrity"]),
            started_at=row["started_at"],
            terminal_at=row["terminal_at"],
            latency=row["latency"],
            evidence_refs=_string_tuple(row["evidence_refs_json"]),
        )
        return OperationalObservation(
            observation_id=row["observation_id"],
            source_event_id=row["source_event_id"],
            outbox_position=row["outbox_position"],
            terminal_event=terminal_event,
        )

    def add_operational_observation(
        self,
        observation: OperationalObservation,
    ) -> None:
        """Insert an immutable operational observation."""
        te = observation.terminal_event
        self._db().execute(
            """
            INSERT INTO operational_observations (
                observation_id,
                source_event_id,
                outbox_position,
                instance_id,
                profile_id,
                transport,
                operational_failure_category,
                execution_certainty,
                process_integrity,
                started_at,
                terminal_at,
                latency,
                evidence_refs_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.observation_id,
                observation.source_event_id,
                observation.outbox_position,
                te.instance_id,
                te.profile_id,
                te.transport,
                (
                    te.operational_failure_category.value
                    if te.operational_failure_category is not None
                    else None
                ),
                te.execution_certainty.value,
                int(te.process_integrity),
                te.started_at,
                te.terminal_at,
                te.latency,
                _json_text(list(te.evidence_refs)),
            ),
        )

    def get_operational_observation(
        self,
        observation_id: str,
    ) -> OperationalObservation | None:
        """Return an operational observation by observation ID."""
        row = self._db().execute(
            """
            SELECT *
            FROM operational_observations
            WHERE observation_id = ?
            """,
            (observation_id,),
        ).fetchone()
        return None if row is None else self._operational_observation_from_row(row)

    # ── Slice 4: operational projections ──
    #
    # Each EvidenceValue-typed field (failure_category, process_integrity,
    # latency) and the usage field are stored as one complete serialized
    # EvidenceValue JSON blob (state, source_tag, provider_id,
    # provider_version, observed_at, captured_at, freshness_ttl,
    # evidence_ref, value) -- not just state+value -- so the real per-field
    # evidence provenance round-trips instead of being fabricated on read.

    @staticmethod
    def _evidence_value_to_dict(
        ev: EvidenceValue[Any],
        value_encoder,
    ) -> dict[str, Any]:
        return {
            "state": ev.state.value,
            "source_tag": ev.source_tag,
            "provider_id": ev.provider_id,
            "provider_version": ev.provider_version,
            "observed_at": ev.observed_at,
            "captured_at": ev.captured_at,
            "freshness_ttl": ev.freshness_ttl,
            "evidence_ref": str(ev.evidence_ref),
            "value": (
                None if ev.value is None else value_encoder(ev.value)
            ),
        }

    @staticmethod
    def _evidence_value_from_dict(
        data: dict[str, Any],
        value_decoder,
    ) -> EvidenceValue[Any]:
        raw_value = data["value"]
        return EvidenceValue(
            state=EvidenceState(data["state"]),
            source_tag=data["source_tag"],
            provider_id=data["provider_id"],
            provider_version=data["provider_version"],
            observed_at=data["observed_at"],
            captured_at=data["captured_at"],
            freshness_ttl=data["freshness_ttl"],
            evidence_ref=EvidenceRef(data["evidence_ref"]),
            value=(
                None if raw_value is None else value_decoder(raw_value)
            ),
        )

    def _operational_projection_from_row(
        self,
        row: sqlite3.Row,
    ) -> OperationalProjectionSnapshot:
        refs = tuple(
            EvidenceRef(r) for r in _string_tuple(row["evidence_refs_json"])
        )
        failure_category = self._evidence_value_from_dict(
            _json_value(row["failure_category_json"]),
            lambda raw: OperationalFailureCategory(raw),
        )
        process_integrity = self._evidence_value_from_dict(
            _json_value(row["process_integrity_json"]),
            lambda raw: bool(raw),
        )
        latency = self._evidence_value_from_dict(
            _json_value(row["latency_json"]),
            lambda raw: int(raw),
        )
        usage = self._evidence_value_from_dict(
            _json_value(row["usage_json"]),
            lambda raw: UsageMeasurement(
                quota_pool_scope=raw["quota_pool_scope"],
                used_fraction=float(raw["used_fraction"]),
                remaining_fraction=float(raw["remaining_fraction"]),
                window_started_at=int(raw["window_started_at"]),
                resets_at=int(raw["resets_at"]),
            ),
        )
        return OperationalProjectionSnapshot(
            projection_id=row["projection_id"],
            instance_id=row["instance_id"],
            profile_id=row["profile_id"],
            failure_category=failure_category,
            process_integrity=process_integrity,
            latency=latency,
            usage=usage,
            failure_streak=row["failure_streak"],
            last_terminal_at=row["last_terminal_at"],
            evidence_refs=refs,
            revision=row["revision"],
            updated_at=row["updated_at"],
        )

    def add_operational_projection(
        self,
        projection: OperationalProjectionSnapshot,
    ) -> None:
        """Insert a revision-one operational projection snapshot."""
        self._db().execute(
            """
            INSERT INTO operational_projections (
                projection_id,
                instance_id,
                profile_id,
                failure_category_json,
                process_integrity_json,
                latency_json,
                usage_json,
                failure_streak,
                last_terminal_at,
                evidence_refs_json,
                revision,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                projection.projection_id,
                projection.instance_id,
                projection.profile_id,
                _json_text(
                    self._evidence_value_to_dict(
                        projection.failure_category,
                        lambda v: v.value,
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(
                        projection.process_integrity,
                        lambda v: v,
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(
                        projection.latency,
                        lambda v: v,
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(
                        projection.usage,
                        lambda v: {
                            "quota_pool_scope": v.quota_pool_scope,
                            "used_fraction": v.used_fraction,
                            "remaining_fraction": v.remaining_fraction,
                            "window_started_at": v.window_started_at,
                            "resets_at": v.resets_at,
                        },
                    )
                ),
                projection.failure_streak,
                projection.last_terminal_at,
                _json_text(list(str(r) for r in projection.evidence_refs)),
                projection.revision,
                projection.updated_at,
            ),
        )

    def get_operational_projection(
        self,
        instance_id: str,
        profile_id: str,
    ) -> OperationalProjectionSnapshot | None:
        """Return an operational projection by instance ID and profile ID."""
        row = self._db().execute(
            """
            SELECT *
            FROM operational_projections
            WHERE instance_id = ? AND profile_id = ?
            """,
            (instance_id, profile_id),
        ).fetchone()
        return None if row is None else self._operational_projection_from_row(row)

    def cas_update_operational_projection(
        self,
        current: OperationalProjectionSnapshot,
        updated: OperationalProjectionSnapshot,
    ) -> bool:
        """CAS-update an operational projection snapshot."""
        if current.projection_id != updated.projection_id:
            raise ValueError("projection IDs do not match")
        cursor = self._db().execute(
            """
            UPDATE operational_projections
            SET
                failure_category_json = ?,
                process_integrity_json = ?,
                latency_json = ?,
                usage_json = ?,
                failure_streak = ?,
                last_terminal_at = ?,
                evidence_refs_json = ?,
                revision = ?,
                updated_at = ?
            WHERE
                projection_id = ?
                AND revision = ?
            """,
            (
                _json_text(
                    self._evidence_value_to_dict(
                        updated.failure_category,
                        lambda v: v.value,
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(
                        updated.process_integrity,
                        lambda v: v,
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(
                        updated.latency,
                        lambda v: v,
                    )
                ),
                _json_text(
                    self._evidence_value_to_dict(
                        updated.usage,
                        lambda v: {
                            "quota_pool_scope": v.quota_pool_scope,
                            "used_fraction": v.used_fraction,
                            "remaining_fraction": v.remaining_fraction,
                            "window_started_at": v.window_started_at,
                            "resets_at": v.resets_at,
                        },
                    )
                ),
                updated.failure_streak,
                updated.last_terminal_at,
                _json_text(list(str(r) for r in updated.evidence_refs)),
                updated.revision,
                updated.updated_at,
                current.projection_id,
                current.revision,
            ),
        )
        return cursor.rowcount == 1

    # ── Slice 4: health projections ──

    def _health_projection_from_row(
        self,
        row: sqlite3.Row,
    ) -> HealthProjectionSnapshot:
        refs = tuple(EvidenceRef(r) for r in _string_tuple(row["evidence_refs_json"]))
        return HealthProjectionSnapshot(
            projection_id=row["projection_id"],
            instance_id=row["instance_id"],
            profile_id=row["profile_id"],
            availability_state=AvailabilityState(row["availability_state"]),
            admission_state=AdmissionState(row["admission_state"]),
            readiness_observation_id=row["readiness_observation_id"],
            operational_projection_id=row["operational_projection_id"],
            operational_projection_revision=row["operational_projection_revision"],
            policy_id=row["policy_id"],
            policy_revision=row["policy_revision"],
            cooldown_until=row["cooldown_until"],
            evidence_refs=refs,
            revision=row["revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            readiness_evaluation=(
                _readiness_evaluation_from_raw(
                    row["readiness_evaluation_json"]
                )
            ),
            sealed_runtime_revision=(
                row["sealed_runtime_revision"]
            ),
            adapter_declares_probe_safe=(
                None
                if row["adapter_declares_probe_safe"] is None
                else bool(row["adapter_declares_probe_safe"])
            ),
        )

    def add_health_projection(
        self,
        projection: HealthProjectionSnapshot,
    ) -> None:
        """Insert a revision-one health projection snapshot."""
        self._db().execute(
            """
            INSERT INTO health_projections (
                projection_id,
                instance_id,
                profile_id,
                availability_state,
                admission_state,
                readiness_observation_id,
                operational_projection_id,
                operational_projection_revision,
                policy_id,
                policy_revision,
                cooldown_until,
                evidence_refs_json,
                revision,
                created_at,
                updated_at,
                readiness_evaluation_json,
                sealed_runtime_revision,
                adapter_declares_probe_safe
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                projection.projection_id,
                projection.instance_id,
                projection.profile_id,
                projection.availability_state.value,
                projection.admission_state.value,
                projection.readiness_observation_id,
                projection.operational_projection_id,
                projection.operational_projection_revision,
                projection.policy_id,
                projection.policy_revision,
                projection.cooldown_until,
                _json_text(list(str(r) for r in projection.evidence_refs)),
                projection.revision,
                projection.created_at,
                projection.updated_at,
                (
                    _json_text(
                        _readiness_evaluation_data(
                            projection.readiness_evaluation
                        )
                    )
                    if projection.readiness_evaluation is not None
                    else None
                ),
                projection.sealed_runtime_revision,
                (
                    int(projection.adapter_declares_probe_safe)
                    if projection.adapter_declares_probe_safe
                    is not None
                    else None
                ),
            ),
        )

    def get_health_projection(
        self,
        instance_id: str,
        profile_id: str,
    ) -> HealthProjectionSnapshot | None:
        """Return a health projection snapshot by instance ID and profile ID."""
        row = self._db().execute(
            """
            SELECT *
            FROM health_projections
            WHERE instance_id = ? AND profile_id = ?
            """,
            (instance_id, profile_id),
        ).fetchone()
        return None if row is None else self._health_projection_from_row(row)

    def cas_update_health_projection(
        self,
        current: HealthProjectionSnapshot,
        updated: HealthProjectionSnapshot,
    ) -> bool:
        """CAS-update a health projection snapshot."""
        if current.projection_id != updated.projection_id:
            raise ValueError("projection IDs do not match")
        cursor = self._db().execute(
            """
            UPDATE health_projections
            SET
                availability_state = ?,
                admission_state = ?,
                readiness_observation_id = ?,
                operational_projection_id = ?,
                operational_projection_revision = ?,
                policy_id = ?,
                policy_revision = ?,
                cooldown_until = ?,
                evidence_refs_json = ?,
                readiness_evaluation_json = ?,
                sealed_runtime_revision = ?,
                adapter_declares_probe_safe = ?,
                revision = ?,
                updated_at = ?
            WHERE
                projection_id = ?
                AND revision = ?
            """,
            (
                updated.availability_state.value,
                updated.admission_state.value,
                updated.readiness_observation_id,
                updated.operational_projection_id,
                updated.operational_projection_revision,
                updated.policy_id,
                updated.policy_revision,
                updated.cooldown_until,
                _json_text(list(str(r) for r in updated.evidence_refs)),
                (
                    _json_text(
                        _readiness_evaluation_data(
                            updated.readiness_evaluation
                        )
                    )
                    if updated.readiness_evaluation is not None
                    else None
                ),
                updated.sealed_runtime_revision,
                (
                    int(updated.adapter_declares_probe_safe)
                    if updated.adapter_declares_probe_safe
                    is not None
                    else None
                ),
                updated.revision,
                updated.updated_at,
                current.projection_id,
                current.revision,
            ),
        )
        return cursor.rowcount == 1

    # ── Slice 4: health circuits ──

    def _health_circuit_from_row(
        self,
        row: sqlite3.Row,
    ) -> HealthCircuitSnapshot:
        receipt = None
        if row["receipt_incident"] is not None:
            receipt = PolicyReceipt(
                incident=row["receipt_incident"],
                gate_generation=row["receipt_gate_generation"],
                timestamp=row["receipt_timestamp"],
                fingerprint=row["receipt_fingerprint"],
            )
        return HealthCircuitSnapshot(
            circuit_id=row["circuit_id"],
            scope=PolicyScope(row["scope"]),
            subject=row["subject"],
            state=CircuitState(row["state"]),
            quarantine_authority_class=QuarantineAuthorityClass(
                row["quarantine_authority_class"]
            ),
            receipt=receipt,
            backoff_count=row["backoff_count"],
            cooldown_until=row["cooldown_until"],
            revision=row["revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def add_health_circuit(
        self,
        circuit: HealthCircuitSnapshot,
    ) -> None:
        """Insert a revision-one health circuit snapshot."""
        rcpt = circuit.receipt
        self._db().execute(
            """
            INSERT INTO health_circuits (
                circuit_id,
                scope,
                subject,
                state,
                quarantine_authority_class,
                receipt_incident,
                receipt_gate_generation,
                receipt_timestamp,
                receipt_fingerprint,
                backoff_count,
                cooldown_until,
                revision,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                circuit.circuit_id,
                circuit.scope.value,
                circuit.subject,
                circuit.state.value,
                circuit.quarantine_authority_class.value,
                rcpt.incident if rcpt is not None else None,
                rcpt.gate_generation if rcpt is not None else None,
                rcpt.timestamp if rcpt is not None else None,
                rcpt.fingerprint if rcpt is not None else None,
                circuit.backoff_count,
                circuit.cooldown_until,
                circuit.revision,
                circuit.created_at,
                circuit.updated_at,
            ),
        )

    def get_health_circuit(
        self,
        scope: PolicyScope | str,
        subject: str,
    ) -> HealthCircuitSnapshot | None:
        """Return a health circuit snapshot by scope and subject."""
        scope_str = scope.value if isinstance(scope, PolicyScope) else str(scope)
        row = self._db().execute(
            """
            SELECT *
            FROM health_circuits
            WHERE scope = ? AND subject = ?
            """,
            (scope_str, subject),
        ).fetchone()
        return None if row is None else self._health_circuit_from_row(row)

    def cas_update_health_circuit(
        self,
        current: HealthCircuitSnapshot,
        updated: HealthCircuitSnapshot,
    ) -> bool:
        """CAS-update a health circuit snapshot."""
        if current.circuit_id != updated.circuit_id:
            raise ValueError("circuit IDs do not match")
        rcpt = updated.receipt
        cursor = self._db().execute(
            """
            UPDATE health_circuits
            SET
                state = ?,
                quarantine_authority_class = ?,
                receipt_incident = ?,
                receipt_gate_generation = ?,
                receipt_timestamp = ?,
                receipt_fingerprint = ?,
                backoff_count = ?,
                cooldown_until = ?,
                revision = ?,
                updated_at = ?
            WHERE
                circuit_id = ?
                AND revision = ?
            """,
            (
                updated.state.value,
                updated.quarantine_authority_class.value,
                rcpt.incident if rcpt is not None else None,
                rcpt.gate_generation if rcpt is not None else None,
                rcpt.timestamp if rcpt is not None else None,
                rcpt.fingerprint if rcpt is not None else None,
                updated.backoff_count,
                updated.cooldown_until,
                updated.revision,
                updated.updated_at,
                current.circuit_id,
                current.revision,
            ),
        )
        return cursor.rowcount == 1

    # ── Slice 4: recovery probe grants ──

    def _recovery_probe_grant_from_row(
        self,
        row: sqlite3.Row,
    ) -> RecoveryProbeGrant:
        receipt = PolicyReceipt(
            incident=row["receipt_incident"],
            gate_generation=row["receipt_gate_generation"],
            timestamp=row["receipt_timestamp"],
            fingerprint=row["receipt_fingerprint"],
        )
        return RecoveryProbeGrant(
            grant_id=row["grant_id"],
            circuit_id=row["circuit_id"],
            receipt=receipt,
            authorized_by=row["authorized_by"],
            authorized_at=row["authorized_at"],
            remaining_probes=row["remaining_probes"],
            consumed_at=row["consumed_at"],
            consumed_by_attempt_id=row["consumed_by_attempt_id"],
            revision=row["revision"],
        )

    def add_recovery_probe_grant(
        self,
        grant: RecoveryProbeGrant,
    ) -> None:
        """Insert an unconsumed recovery probe grant."""
        existing = self.get_live_recovery_probe_grant(
            grant.circuit_id
        )
        if existing is not None:
            raise RecoveryProbeGrantConflictError(
                grant.circuit_id,
                existing.grant_id,
            )

        rcpt = grant.receipt
        try:
            self._db().execute(
                """
                INSERT INTO recovery_probe_grants (
                    grant_id,
                    circuit_id,
                    receipt_incident,
                    receipt_gate_generation,
                    receipt_timestamp,
                    receipt_fingerprint,
                    authorized_by,
                    authorized_at,
                    remaining_probes,
                    consumed_at,
                    consumed_by_attempt_id,
                    revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant.grant_id,
                    grant.circuit_id,
                    rcpt.incident,
                    rcpt.gate_generation,
                    rcpt.timestamp,
                    rcpt.fingerprint,
                    grant.authorized_by,
                    grant.authorized_at,
                    grant.remaining_probes,
                    grant.consumed_at,
                    grant.consumed_by_attempt_id,
                    grant.revision,
                ),
            )
        except sqlite3.IntegrityError as error:
            existing = self.get_live_recovery_probe_grant(
                grant.circuit_id
            )
            if existing is not None:
                raise RecoveryProbeGrantConflictError(
                    grant.circuit_id,
                    existing.grant_id,
                ) from error
            raise

    def get_recovery_probe_grant(
        self,
        grant_id: str,
    ) -> RecoveryProbeGrant | None:
        """Return a recovery probe grant by grant ID."""
        row = self._db().execute(
            """
            SELECT *
            FROM recovery_probe_grants
            WHERE grant_id = ?
            """,
            (grant_id,),
        ).fetchone()
        return None if row is None else self._recovery_probe_grant_from_row(row)

    def get_live_recovery_probe_grant(
        self,
        circuit_id: str,
    ) -> RecoveryProbeGrant | None:
        """Return the sole unconsumed grant for a circuit."""
        row = self._db().execute(
            """
            SELECT *
            FROM recovery_probe_grants
            WHERE circuit_id = ?
              AND consumed_at IS NULL
            """,
            (circuit_id,),
        ).fetchone()
        return (
            None
            if row is None
            else self._recovery_probe_grant_from_row(row)
        )

    def cas_claim_recovery_probe_grant(
        self,
        current: RecoveryProbeGrant,
        updated: RecoveryProbeGrant,
    ) -> bool:
        """Attempt a contention-safe single-use claim on an unconsumed probe grant."""
        if current.grant_id != updated.grant_id:
            raise ValueError("grant IDs do not match")
        cursor = self._db().execute(
            """
            UPDATE recovery_probe_grants
            SET
                remaining_probes = ?,
                consumed_at = ?,
                consumed_by_attempt_id = ?,
                revision = ?
            WHERE
                grant_id = ?
                AND revision = ?
                AND consumed_at IS NULL
            """,
            (
                updated.remaining_probes,
                updated.consumed_at,
                updated.consumed_by_attempt_id,
                updated.revision,
                current.grant_id,
                current.revision,
            ),
        )
        return cursor.rowcount == 1

    # ── Slice 4: recovery probe receipts (immutable) ──

    def _recovery_probe_receipt_from_row(
        self,
        row: sqlite3.Row,
    ) -> RecoveryProbeReceipt:
        reported_receipt = PolicyReceipt(
            incident=row["reported_receipt_incident"],
            gate_generation=row["reported_receipt_gate_generation"],
            timestamp=row["reported_receipt_timestamp"],
            fingerprint=row["reported_receipt_fingerprint"],
        )
        refs = tuple(EvidenceRef(r) for r in _string_tuple(row["evidence_refs_json"]))
        return RecoveryProbeReceipt(
            probe_receipt_id=row["probe_receipt_id"],
            grant_id=row["grant_id"],
            attempt_id=row["attempt_id"],
            reported_revision=row["reported_revision"],
            reported_receipt=reported_receipt,
            result=ProbeResult(row["result"]),
            observed_at=row["observed_at"],
            evidence_refs=refs,
        )

    def add_recovery_probe_receipt(
        self,
        receipt: RecoveryProbeReceipt,
    ) -> None:
        """Insert an immutable recovery probe receipt."""
        rr = receipt.reported_receipt
        self._db().execute(
            """
            INSERT INTO recovery_probe_receipts (
                probe_receipt_id,
                grant_id,
                attempt_id,
                reported_revision,
                reported_receipt_incident,
                reported_receipt_gate_generation,
                reported_receipt_timestamp,
                reported_receipt_fingerprint,
                result,
                observed_at,
                evidence_refs_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.probe_receipt_id,
                receipt.grant_id,
                receipt.attempt_id,
                receipt.reported_revision,
                rr.incident,
                rr.gate_generation,
                rr.timestamp,
                rr.fingerprint,
                receipt.result.value,
                receipt.observed_at,
                _json_text(list(str(r) for r in receipt.evidence_refs)),
            ),
        )

    def get_recovery_probe_receipt(
        self,
        probe_receipt_id: str,
    ) -> RecoveryProbeReceipt | None:
        """Return a recovery probe receipt by probe receipt ID."""
        row = self._db().execute(
            """
            SELECT *
            FROM recovery_probe_receipts
            WHERE probe_receipt_id = ?
            """,
            (probe_receipt_id,),
        ).fetchone()
        return None if row is None else self._recovery_probe_receipt_from_row(row)

    def list_recovery_probe_receipts(
        self,
        grant_id: str,
    ) -> tuple[RecoveryProbeReceipt, ...]:
        """Return all recovery probe receipts for a grant ID ordered by observation time."""
        rows = self._db().execute(
            """
            SELECT *
            FROM recovery_probe_receipts
            WHERE grant_id = ?
            ORDER BY observed_at, probe_receipt_id
            """,
            (grant_id,),
        ).fetchall()
        return tuple(self._recovery_probe_receipt_from_row(row) for row in rows)

    # ── Slice 4: admission snapshots (immutable audit) ──

    def add_admission_snapshot(
        self,
        snapshot: AdmissionSnapshot,
    ) -> None:
        """Insert an immutable admission snapshot and all its entries."""
        db = self._db()
        db.execute(
            """
            INSERT INTO admission_snapshots (
                snapshot_id,
                revision,
                digest,
                configuration_revision,
                policy_id,
                policy_revision,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.revision,
                snapshot.digest,
                snapshot.configuration_revision,
                snapshot.policy_id,
                snapshot.policy_revision,
                snapshot.created_at,
            ),
        )
        for entry in snapshot.entries:
            db.execute(
                """
                INSERT INTO admission_snapshot_entries (
                    snapshot_id,
                    instance_id,
                    profile_id,
                    health_projection_id,
                    health_projection_revision,
                    availability_state,
                    admission_state,
                    evidence_refs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    entry.instance_id,
                    entry.profile_id,
                    entry.health_projection_id,
                    entry.health_projection_revision,
                    entry.availability_state.value,
                    entry.admission_state.value,
                    _json_text(list(str(r) for r in entry.evidence_refs)),
                ),
            )

    def get_admission_snapshot(
        self,
        snapshot_id: str,
    ) -> AdmissionSnapshot | None:
        """Return a full admission snapshot including all its entries."""
        db = self._db()
        row = db.execute(
            """
            SELECT *
            FROM admission_snapshots
            WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None

        entry_rows = db.execute(
            """
            SELECT *
            FROM admission_snapshot_entries
            WHERE snapshot_id = ?
            ORDER BY instance_id, profile_id
            """,
            (snapshot_id,),
        ).fetchall()

        entries = tuple(
            AdmissionSnapshotEntry(
                instance_id=erow["instance_id"],
                profile_id=erow["profile_id"],
                health_projection_id=erow["health_projection_id"],
                health_projection_revision=erow["health_projection_revision"],
                availability_state=AvailabilityState(erow["availability_state"]),
                admission_state=AdmissionState(erow["admission_state"]),
                evidence_refs=tuple(
                    EvidenceRef(r) for r in _string_tuple(erow["evidence_refs_json"])
                ),
            )
            for erow in entry_rows
        )

        return AdmissionSnapshot(
            snapshot_id=row["snapshot_id"],
            revision=row["revision"],
            digest=row["digest"],
            configuration_revision=row["configuration_revision"],
            policy_id=row["policy_id"],
            policy_revision=row["policy_revision"],
            entries=entries,
            created_at=row["created_at"],
        )

    # ── Slice 4: route decisions (immutable audit) ──

    def add_route_decision(
        self,
        decision: RouteDecision,
    ) -> None:
        """Insert an immutable route decision audit and all candidate decisions."""
        db = self._db()
        db.execute(
            """
            INSERT INTO route_decisions (
                decision_id,
                client_request_id,
                configuration_revision,
                configuration_digest,
                admission_snapshot_id,
                admission_snapshot_revision,
                admission_snapshot_digest,
                routing_policy_id,
                routing_policy_revision,
                audit_seed,
                selection_index,
                selected_candidate_id,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.decision_id,
                decision.client_request_id,
                decision.configuration.revision,
                decision.configuration.digest,
                decision.admission_snapshot_id,
                decision.admission_snapshot_revision,
                decision.admission_snapshot_digest,
                decision.routing_policy_id,
                decision.routing_policy_revision,
                decision.audit_seed,
                decision.selection_index,
                decision.selected_candidate_id,
                decision.created_at,
            ),
        )
        for candidate in decision.candidates:
            db.execute(
                """
                INSERT INTO route_candidate_decisions (
                    decision_id,
                    candidate_id,
                    instance_id,
                    representative_profile_id,
                    eligibility,
                    effective_weight,
                    exclusion_reason,
                    evidence_refs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    candidate.candidate_id,
                    candidate.instance_id,
                    candidate.representative_profile_id,
                    candidate.eligibility.value,
                    candidate.effective_weight,
                    candidate.exclusion_reason,
                    _json_text(list(str(r) for r in candidate.evidence_refs)),
                ),
            )

    def get_route_decision(
        self,
        decision_id: str,
    ) -> RouteDecision | None:
        """Return a full route decision audit including all candidate decisions."""
        db = self._db()
        row = db.execute(
            """
            SELECT *
            FROM route_decisions
            WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()
        if row is None:
            return None

        candidate_rows = db.execute(
            """
            SELECT *
            FROM route_candidate_decisions
            WHERE decision_id = ?
            ORDER BY candidate_id
            """,
            (decision_id,),
        ).fetchall()

        candidates = tuple(
            RouteCandidateDecision(
                candidate_id=crow["candidate_id"],
                instance_id=crow["instance_id"],
                representative_profile_id=crow["representative_profile_id"],
                eligibility=RouteEligibility(crow["eligibility"]),
                effective_weight=crow["effective_weight"],
                exclusion_reason=crow["exclusion_reason"],
                evidence_refs=tuple(
                    EvidenceRef(r) for r in _string_tuple(crow["evidence_refs_json"])
                ),
            )
            for crow in candidate_rows
        )

        config = ConfigurationSnapshot(
            revision=row["configuration_revision"],
            digest=row["configuration_digest"],
        )

        return RouteDecision(
            decision_id=row["decision_id"],
            client_request_id=row["client_request_id"],
            configuration=config,
            admission_snapshot_id=row["admission_snapshot_id"],
            admission_snapshot_revision=row["admission_snapshot_revision"],
            admission_snapshot_digest=row["admission_snapshot_digest"],
            routing_policy_id=row["routing_policy_id"],
            routing_policy_revision=row["routing_policy_revision"],
            candidates=candidates,
            audit_seed=row["audit_seed"],
            selection_index=row["selection_index"],
            selected_candidate_id=row["selected_candidate_id"],
            created_at=row["created_at"],
        )

    # ── Slice 5 Step 4: dispatch artifact metadata ──

    @staticmethod
    def _artifact_manifest_from_row(
        row: sqlite3.Row,
    ) -> ArtifactManifestRecord:
        return ArtifactManifestRecord(
            attempt_id=row["attempt_id"],
            workspace_scope_id=row["workspace_scope_id"],
            staging_root_ref=row["staging_root_ref"],
            manifest_digest=row["manifest_digest"],
            item_count=row["item_count"],
            intent_event_id=row["intent_event_id"],
            created_at=row["created_at"],
            consumed_at=row["consumed_at"],
            revision=row["revision"],
        )

    @staticmethod
    def _artifact_metadata_from_row(
        row: sqlite3.Row,
    ) -> ArtifactMetadata:
        return ArtifactMetadata(
            attempt_id=row["attempt_id"],
            artifact_id=row["artifact_id"],
            placeholder=row["placeholder"],
            workspace_scope_id=row["workspace_scope_id"],
            staging_ref=row["staging_ref"],
            access_mode=row["access_mode"],
            declared_lifecycle=row["declared_lifecycle"],
            expected_sha256_hex=row["expected_sha256_hex"],
            expected_length=row["expected_length"],
            verified_sha256_hex=row["verified_sha256_hex"],
            verified_length=row["verified_length"],
            verified_object_identity_json=row["verified_object_identity_json"],
            state=ArtifactState(row["state"]),
            failure_code=row["failure_code"],
            declared_at=row["declared_at"],
            staged_at=row["staged_at"],
            verified_at=row["verified_at"],
            reserved_at=row["reserved_at"],
            consumed_at=row["consumed_at"],
            cleaned_at=row["cleaned_at"],
            orphaned_at=row["orphaned_at"],
            revision=row["revision"],
        )

    def add_artifact_manifest(
        self,
        manifest: ArtifactManifestRecord,
        artifacts: tuple[ArtifactMetadata, ...],
    ) -> None:
        """Insert durable artifact manifest and artifact metadata rows."""
        self._db().execute(
            """
            INSERT INTO dispatch_artifact_manifests (
                attempt_id,
                workspace_scope_id,
                staging_root_ref,
                manifest_digest,
                item_count,
                intent_event_id,
                created_at,
                consumed_at,
                revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest.attempt_id,
                manifest.workspace_scope_id,
                manifest.staging_root_ref,
                manifest.manifest_digest,
                manifest.item_count,
                manifest.intent_event_id,
                manifest.created_at,
                manifest.consumed_at,
                manifest.revision,
            ),
        )
        for art in artifacts:
            self._db().execute(
                """
                INSERT INTO dispatch_artifacts (
                    attempt_id,
                    artifact_id,
                    placeholder,
                    workspace_scope_id,
                    staging_ref,
                    access_mode,
                    declared_lifecycle,
                    expected_sha256_hex,
                    expected_length,
                    verified_sha256_hex,
                    verified_length,
                    verified_object_identity_json,
                    state,
                    failure_code,
                    declared_at,
                    staged_at,
                    verified_at,
                    reserved_at,
                    consumed_at,
                    cleaned_at,
                    orphaned_at,
                    revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    art.attempt_id,
                    art.artifact_id,
                    art.placeholder,
                    art.workspace_scope_id,
                    art.staging_ref,
                    art.access_mode,
                    art.declared_lifecycle,
                    art.expected_sha256_hex,
                    art.expected_length,
                    art.verified_sha256_hex,
                    art.verified_length,
                    art.verified_object_identity_json,
                    art.state.value,
                    art.failure_code,
                    art.declared_at,
                    art.staged_at,
                    art.verified_at,
                    art.reserved_at,
                    art.consumed_at,
                    art.cleaned_at,
                    art.orphaned_at,
                    art.revision,
                ),
            )

    def get_artifact_manifest(
        self, attempt_id: str
    ) -> ArtifactManifestRecord | None:
        """Return artifact manifest by attempt ID."""
        row = self._db().execute(
            """
            SELECT * FROM dispatch_artifact_manifests WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()
        return None if row is None else self._artifact_manifest_from_row(row)

    def get_artifact_metadata(
        self, attempt_id: str, artifact_id: str
    ) -> ArtifactMetadata | None:
        """Return artifact metadata by attempt ID and artifact ID."""
        row = self._db().execute(
            """
            SELECT * FROM dispatch_artifacts
            WHERE attempt_id = ? AND artifact_id = ?
            """,
            (attempt_id, artifact_id),
        ).fetchone()
        return None if row is None else self._artifact_metadata_from_row(row)

    def list_artifact_metadata(
        self, attempt_id: str
    ) -> tuple[ArtifactMetadata, ...]:
        """List all artifact metadata rows for an attempt."""
        rows = self._db().execute(
            """
            SELECT * FROM dispatch_artifacts
            WHERE attempt_id = ?
            ORDER BY artifact_id
            """,
            (attempt_id,),
        ).fetchall()
        return tuple(self._artifact_metadata_from_row(row) for row in rows)

    def cas_update_artifact_metadata(
        self, current: ArtifactMetadata, updated: ArtifactMetadata
    ) -> bool:
        """CAS update artifact metadata row by revision."""
        if (
            current.attempt_id != updated.attempt_id
            or current.artifact_id != updated.artifact_id
        ):
            raise ValueError(
                "attempt_id and artifact_id must match for CAS update"
            )
        cursor = self._db().execute(
            """
            UPDATE dispatch_artifacts
            SET
                placeholder = ?,
                workspace_scope_id = ?,
                staging_ref = ?,
                access_mode = ?,
                declared_lifecycle = ?,
                expected_sha256_hex = ?,
                expected_length = ?,
                verified_sha256_hex = ?,
                verified_length = ?,
                verified_object_identity_json = ?,
                state = ?,
                failure_code = ?,
                declared_at = ?,
                staged_at = ?,
                verified_at = ?,
                reserved_at = ?,
                consumed_at = ?,
                cleaned_at = ?,
                orphaned_at = ?,
                revision = ?
            WHERE attempt_id = ? AND artifact_id = ? AND revision = ?
            """,
            (
                updated.placeholder,
                updated.workspace_scope_id,
                updated.staging_ref,
                updated.access_mode,
                updated.declared_lifecycle,
                updated.expected_sha256_hex,
                updated.expected_length,
                updated.verified_sha256_hex,
                updated.verified_length,
                updated.verified_object_identity_json,
                updated.state.value,
                updated.failure_code,
                updated.declared_at,
                updated.staged_at,
                updated.verified_at,
                updated.reserved_at,
                updated.consumed_at,
                updated.cleaned_at,
                updated.orphaned_at,
                updated.revision,
                current.attempt_id,
                current.artifact_id,
                current.revision,
            ),
        )
        return cursor.rowcount == 1

    def reserve_verified_artifacts_for_dispatch(
        self,
        *,
        attempt_id: str,
        expected_manifest_digest: str,
        intent_event_id: str,
        reserved_at: int,
    ) -> bool:
        """Transition artifacts from VERIFIED to RESERVED for an attempt, all-or-nothing.

        If any item in the manifest is not VERIFIED, zero items change state.
        Links intent_event_id on the manifest.
        """
        manifest_row = self._db().execute(
            """
            SELECT manifest_digest, item_count
            FROM dispatch_artifact_manifests
            WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()

        if manifest_row is None:
            return False
        if manifest_row["manifest_digest"] != expected_manifest_digest:
            return False

        item_count = manifest_row["item_count"]
        art_rows = self._db().execute(
            """
            SELECT state FROM dispatch_artifacts WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchall()

        if len(art_rows) != item_count or any(
            row["state"] != ArtifactState.VERIFIED.value for row in art_rows
        ):
            return False

        # All items are VERIFIED and match count -- perform reservation
        cursor = self._db().execute(
            """
            UPDATE dispatch_artifacts
            SET state = ?, reserved_at = ?, revision = revision + 1
            WHERE attempt_id = ? AND state = ?
            """,
            (
                ArtifactState.RESERVED.value,
                reserved_at,
                attempt_id,
                ArtifactState.VERIFIED.value,
            ),
        )
        if cursor.rowcount != item_count:
            return False

        self._db().execute(
            """
            UPDATE dispatch_artifact_manifests
            SET intent_event_id = ?, revision = revision + 1
            WHERE attempt_id = ?
            """,
            (intent_event_id, attempt_id),
        )
        return True

    def consume_reserved_artifacts(
        self,
        *,
        attempt_id: str,
        terminal_outcome_event_id: str,
        consumed_at: int,
    ) -> bool:
        """Transition artifacts from RESERVED to CONSUMED for an attempt.

        Atomic with setting consumed_at on manifest.
        """
        manifest_row = self._db().execute(
            """
            SELECT item_count FROM dispatch_artifact_manifests WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()

        if manifest_row is None:
            return False

        art_rows = self._db().execute(
            """
            SELECT state FROM dispatch_artifacts WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchall()

        if not art_rows or any(
            row["state"] != ArtifactState.RESERVED.value for row in art_rows
        ):
            return False

        cursor = self._db().execute(
            """
            UPDATE dispatch_artifacts
            SET state = ?, consumed_at = ?, revision = revision + 1
            WHERE attempt_id = ? AND state = ?
            """,
            (
                ArtifactState.CONSUMED.value,
                consumed_at,
                attempt_id,
                ArtifactState.RESERVED.value,
            ),
        )
        if cursor.rowcount != len(art_rows):
            return False

        self._db().execute(
            """
            UPDATE dispatch_artifact_manifests
            SET consumed_at = ?, revision = revision + 1
            WHERE attempt_id = ?
            """,
            (consumed_at, attempt_id),
        )
        return True

    def get_artifact_recovery_digest(
        self, attempt_id: str
    ) -> ArtifactRecoveryDigest | None:
        """Return recovery digest for an attempt."""
        manifest = self.get_artifact_manifest(attempt_id)
        if manifest is None:
            return None

        artifacts = self.list_artifact_metadata(attempt_id)
        intent_event_verified = False

        if manifest.intent_event_id is not None:
            outbox_row = self._db().execute(
                """
                SELECT event_kind, payload_json FROM outbox_events WHERE event_id = ?
                """,
                (manifest.intent_event_id,),
            ).fetchone()
            if outbox_row is not None:
                kind = outbox_row["event_kind"]
                payload = _json_object(outbox_row["payload_json"])
                manifest_digest_in_payload = payload.get("manifest_digest")
                if kind == "DISPATCH_INTENT" and (
                    manifest_digest_in_payload is None
                    or manifest_digest_in_payload == manifest.manifest_digest
                ):
                    intent_event_verified = True

        return ArtifactRecoveryDigest(
            attempt_id=attempt_id,
            workspace_scope_id=manifest.workspace_scope_id,
            manifest_digest=manifest.manifest_digest,
            item_count=manifest.item_count,
            intent_event_id=manifest.intent_event_id,
            intent_event_verified=intent_event_verified,
            artifacts=artifacts,
        )

    def mark_artifacts_orphaned(
        self,
        *,
        attempt_id: str,
        expected_manifest_revision: int,
        orphaned_at: int,
        failure_code: str,
    ) -> bool:
        """Mark non-terminal artifacts as ORPHANED."""
        manifest_row = self._db().execute(
            """
            SELECT revision FROM dispatch_artifact_manifests WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()

        if (
            manifest_row is None
            or manifest_row["revision"] != expected_manifest_revision
        ):
            return False

        self._db().execute(
            """
            UPDATE dispatch_artifacts
            SET state = ?, orphaned_at = ?, failure_code = ?, revision = revision + 1
            WHERE attempt_id = ? AND state NOT IN (?, ?)
            """,
            (
                ArtifactState.ORPHANED.value,
                orphaned_at,
                failure_code,
                attempt_id,
                ArtifactState.CONSUMED.value,
                ArtifactState.CLEANED.value,
            ),
        )

        self._db().execute(
            """
            UPDATE dispatch_artifact_manifests
            SET revision = revision + 1
            WHERE attempt_id = ? AND revision = ?
            """,
            (attempt_id, expected_manifest_revision),
        )
        return True

    def mark_artifact_cleaned(
        self, current: ArtifactMetadata, *, cleaned_at: int
    ) -> bool:
        """Mark a CONSUMED artifact as CLEANED. Rejects non-CONSUMED artifacts."""
        if current.state != ArtifactState.CONSUMED:
            return False

        cursor = self._db().execute(
            """
            UPDATE dispatch_artifacts
            SET state = ?, cleaned_at = ?, revision = revision + 1
            WHERE attempt_id = ? AND artifact_id = ? AND revision = ? AND state = ?
            """,
            (
                ArtifactState.CLEANED.value,
                cleaned_at,
                current.attempt_id,
                current.artifact_id,
                current.revision,
                ArtifactState.CONSUMED.value,
            ),
        )
        return cursor.rowcount == 1

