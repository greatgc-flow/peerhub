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

from .sqlite_governance import SqliteGovernanceRepository
from .sqlite_dispatch import SqliteDispatchRepository
from .sqlite_health import SqliteHealthRepository
from .sqlite_helpers import (
    _json_text,
    _json_value,
    _json_object,
    _optional_json_object,
    _string_tuple,
    _stored_revision,
    _stored_optional_revision,
)

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
        self.governance = SqliteGovernanceRepository(self._db)
        self.dispatch = SqliteDispatchRepository(self._db)
        self.health = SqliteHealthRepository(self._db)

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
        return self.governance.get_target(target_id)

    def compare_and_set_target(
        self,
        current: TargetState | None,
        updated: TargetState,
    ) -> bool:
        """Insert or CAS-update a versioned target."""
        return self.governance.compare_and_set_target(current, updated)

    def get_command_binding(
        self,
        client_id: str,
        command_type: str,
        idempotency_key: str,
    ) -> CommandBinding | None:
        """Return a governance idempotency binding."""
        return self.governance.get_command_binding(client_id, command_type, idempotency_key)

    def add_command_binding(
        self,
        binding: CommandBinding,
    ) -> None:
        """Insert an immutable governance command-ledger row."""
        return self.governance.add_command_binding(binding)

    def add_mutation_request(
        self,
        request: MutationRequest,
        payload_digest: str,
        created_at: int,
    ) -> None:
        """Insert an immutable mutation request."""
        return self.governance.add_mutation_request(request, payload_digest, created_at)

    def add_mutation_plan(self, plan: MutationPlan) -> None:
        """Insert an immutable mutation plan."""
        return self.governance.add_mutation_plan(plan)

    def add_transition_receipt(
        self,
        receipt: TransitionReceipt,
    ) -> None:
        """Insert an immutable transition receipt."""
        return self.governance.add_transition_receipt(receipt)

    def get_transition_receipt(
        self,
        receipt_id: str,
    ) -> TransitionReceipt | None:
        """Return a transition receipt by ID."""
        return self.governance.get_transition_receipt(receipt_id)

    def add_outbox_event(self, event: OutboxEvent) -> None:
        """Insert one canonical pending outbox event."""
        return self.governance.add_outbox_event(event)

    def get_outbox_event(
        self,
        event_id: str,
    ) -> OutboxEvent | None:
        """Return one canonical outbox event."""
        return self.governance.get_outbox_event(event_id)

    def list_outbox_events(
        self,
        states: tuple[OutboxState, ...],
        *,
        limit: int,
        governance_only: bool = False,
        after_position: int = 0,
    ) -> tuple[OutboxEvent, ...]:
        """Return matching events in workspace outbox order."""
        return self.governance.list_outbox_events(states, limit=limit, governance_only=governance_only, after_position=after_position)

    def claim_outbox_event(
        self,
        event_id: str,
        owner_id: str,
        attempt_id: str,
        claimed_at: int,
    ) -> OutboxEvent | None:
        """CAS-claim one pending outbox event."""
        return self.governance.claim_outbox_event(event_id, owner_id, attempt_id, claimed_at)

    def mark_outbox_consumed(
        self,
        event_id: str,
        owner_id: str,
        attempt_id: str,
        consumed_at: int,
    ) -> bool:
        """CAS-mark a claimed event consumed."""
        return self.governance.mark_outbox_consumed(event_id, owner_id, attempt_id, consumed_at)

    def get_outbox_checkpoint(
        self,
        consumer_id: str,
    ) -> OutboxCheckpoint | None:
        """Return a consumer's revisioned outbox checkpoint."""
        return self.governance.get_outbox_checkpoint(consumer_id)

    def add_outbox_checkpoint(
        self,
        checkpoint: OutboxCheckpoint,
    ) -> None:
        """Insert a consumer's initial checkpoint."""
        return self.governance.add_outbox_checkpoint(checkpoint)

    def cas_update_outbox_checkpoint(
        self,
        current: OutboxCheckpoint,
        updated: OutboxCheckpoint,
    ) -> bool:
        """CAS-advance a checkpoint using its stored revision."""
        return self.governance.cas_update_outbox_checkpoint(current, updated)

    def add_effect_receipt(
        self,
        receipt: EffectReceipt,
    ) -> None:
        """Insert one immutable terminal effect receipt."""
        return self.governance.add_effect_receipt(receipt)

    def get_effect_receipt(
        self,
        outbox_event_id: str,
    ) -> EffectReceipt | None:
        """Return an outbox event's immutable terminal receipt."""
        return self.governance.get_effect_receipt(outbox_event_id)

    def get_client_request_binding(
        self,
        client_id: str,
        client_request_id: str,
    ) -> ClientRequestBinding | None:
        """Return a caller-request identity binding."""
        return self.dispatch.get_client_request_binding(client_id, client_request_id)

    def add_client_request_binding(
        self,
        binding: ClientRequestBinding,
    ) -> None:
        """Insert an immutable caller-request identity."""
        return self.dispatch.add_client_request_binding(binding)

    def get_command_idempotency_binding(
        self,
        client_id: str,
        command_type: str,
        idempotency_key: str,
    ) -> CommandIdempotencyBinding | None:
        """Return a Slice 3 idempotency-key binding."""
        return self.dispatch.get_command_idempotency_binding(client_id, command_type, idempotency_key)

    def add_command_idempotency_binding(
        self,
        binding: CommandIdempotencyBinding,
    ) -> None:
        """Insert an immutable Slice 3 idempotency binding."""
        return self.dispatch.add_command_idempotency_binding(binding)

    def add_admission_receipt(
        self,
        receipt: AdmissionReceipt,
    ) -> None:
        """Insert an immutable admission receipt."""
        return self.dispatch.add_admission_receipt(receipt)

    def get_admission_receipt(
        self,
        admission_receipt_id: str,
    ) -> AdmissionReceipt | None:
        """Return an admission receipt by ID."""
        return self.dispatch.get_admission_receipt(admission_receipt_id)

    def add_request(self, request: RequestSnapshot) -> None:
        """Insert an admitted request snapshot."""
        return self.dispatch.add_request(request)

    def get_request(
        self,
        command_id: CommandID | str,
    ) -> RequestSnapshot | None:
        """Return a request snapshot by server command ID."""
        return self.dispatch.get_request(command_id)

    def cas_update_request(
        self,
        current: RequestSnapshot,
        updated: RequestSnapshot,
    ) -> bool:
        """CAS-update a request by command ID and revision."""
        return self.dispatch.cas_update_request(current, updated)

    def next_attempt_number(
        self,
        command_id: CommandID | str,
    ) -> int:
        """Return the next monotonic attempt number in this transaction."""
        return self.dispatch.next_attempt_number(command_id)

    def add_attempt(self, attempt: AttemptSnapshot) -> None:
        """Insert a revision-one dispatch attempt."""
        return self.dispatch.add_attempt(attempt)

    def get_attempt(
        self,
        attempt_id: str,
    ) -> AttemptSnapshot | None:
        """Return an attempt by server attempt ID."""
        return self.dispatch.get_attempt(attempt_id)

    def list_attempts(
        self,
        command_id: CommandID | str,
    ) -> tuple[AttemptSnapshot, ...]:
        """Return command attempts in monotonic attempt order."""
        return self.dispatch.list_attempts(command_id)

    def cas_update_attempt(
        self,
        current: AttemptSnapshot,
        updated: AttemptSnapshot,
    ) -> bool:
        """CAS-update an attempt by ID and revision."""
        return self.dispatch.cas_update_attempt(current, updated)

    def allocate_fencing_token(self) -> int:
        """Allocate one database-monotonic lease fencing token."""
        return self.dispatch.allocate_fencing_token()

    def get_lease(self, lease_id: str) -> LeaseSnapshot | None:
        """Return a lease snapshot by ID."""
        return self.dispatch.get_lease(lease_id)

    def add_lease(self, lease: LeaseSnapshot) -> None:
        """Insert a new lease snapshot."""
        return self.dispatch.add_lease(lease)

    def cas_update_lease(
        self,
        current: LeaseSnapshot,
        updated: LeaseSnapshot,
    ) -> bool:
        """CAS-update using the complete persisted lease fence."""
        return self.dispatch.cas_update_lease(current, updated)

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
        return self.dispatch.cas_update_dispatch_bundle(current_request, updated_request, current_attempt, updated_attempt, current_lease, updated_lease)

    def get_session_binding(
        self,
        key: SessionBindingKey,
    ) -> SessionBindingSnapshot | None:
        """Return a session binding snapshot by canonical key."""
        return self.dispatch.get_session_binding(key)

    def add_session_binding(
        self,
        binding: SessionBindingSnapshot,
    ) -> None:
        """Insert a new session binding."""
        return self.dispatch.add_session_binding(binding)

    def cas_update_session_binding(
        self,
        current: SessionBindingSnapshot,
        updated: SessionBindingSnapshot,
    ) -> bool:
        """CAS-update a session binding by key and current revision."""
        return self.dispatch.cas_update_session_binding(current, updated)

    def add_recovery_receipt(
        self,
        receipt: RecoveryReceipt,
    ) -> None:
        """Insert an immutable recovery receipt."""
        return self.dispatch.add_recovery_receipt(receipt)

    def get_recovery_receipt(
        self,
        receipt_id: str,
    ) -> RecoveryReceipt | None:
        """Return a recovery receipt by ID."""
        return self.dispatch.get_recovery_receipt(receipt_id)

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

    def add_health_policy_revision(
        self,
        policy: HealthPolicy,
        created_at: int = 0,
    ) -> None:
        """Insert a frozen health policy revision."""
        return self.health.add_health_policy_revision(policy, created_at)

    def get_health_policy_revision(
        self,
        policy_id: str,
        revision: int,
    ) -> HealthPolicy | None:
        """Return a health policy revision by ID and revision number."""
        return self.health.get_health_policy_revision(policy_id, revision)

    def add_readiness_observation(
        self,
        observed: ReadinessObserved,
    ) -> None:
        """Insert an immutable readiness observation."""
        return self.health.add_readiness_observation(observed)

    def get_readiness_observation(
        self,
        observation_id: str,
    ) -> ReadinessObserved | None:
        """Return a readiness observation by observation ID."""
        return self.health.get_readiness_observation(observation_id)

    def add_operational_observation(
        self,
        observation: OperationalObservation,
    ) -> None:
        """Insert an immutable operational observation."""
        return self.health.add_operational_observation(observation)

    def get_operational_observation(
        self,
        observation_id: str,
    ) -> OperationalObservation | None:
        """Return an operational observation by observation ID."""
        return self.health.get_operational_observation(observation_id)

    def add_operational_projection(
        self,
        projection: OperationalProjectionSnapshot,
    ) -> None:
        """Insert a revision-one operational projection snapshot."""
        return self.health.add_operational_projection(projection)

    def get_operational_projection(
        self,
        instance_id: str,
        profile_id: str,
    ) -> OperationalProjectionSnapshot | None:
        """Return an operational projection by instance ID and profile ID."""
        return self.health.get_operational_projection(instance_id, profile_id)

    def cas_update_operational_projection(
        self,
        current: OperationalProjectionSnapshot,
        updated: OperationalProjectionSnapshot,
    ) -> bool:
        """CAS-update an operational projection snapshot."""
        return self.health.cas_update_operational_projection(current, updated)

    def add_health_projection(
        self,
        projection: HealthProjectionSnapshot,
    ) -> None:
        """Insert a revision-one health projection snapshot."""
        return self.health.add_health_projection(projection)

    def get_health_projection(
        self,
        instance_id: str,
        profile_id: str,
    ) -> HealthProjectionSnapshot | None:
        """Return a health projection snapshot by instance ID and profile ID."""
        return self.health.get_health_projection(instance_id, profile_id)

    def cas_update_health_projection(
        self,
        current: HealthProjectionSnapshot,
        updated: HealthProjectionSnapshot,
    ) -> bool:
        """CAS-update a health projection snapshot."""
        return self.health.cas_update_health_projection(current, updated)

    def add_health_circuit(
        self,
        circuit: HealthCircuitSnapshot,
    ) -> None:
        """Insert a revision-one health circuit snapshot."""
        return self.health.add_health_circuit(circuit)

    def get_health_circuit(
        self,
        scope: PolicyScope | str,
        subject: str,
    ) -> HealthCircuitSnapshot | None:
        """Return a health circuit snapshot by scope and subject."""
        return self.health.get_health_circuit(scope, subject)

    def cas_update_health_circuit(
        self,
        current: HealthCircuitSnapshot,
        updated: HealthCircuitSnapshot,
    ) -> bool:
        """CAS-update a health circuit snapshot."""
        return self.health.cas_update_health_circuit(current, updated)

    def add_recovery_probe_grant(
        self,
        grant: RecoveryProbeGrant,
    ) -> None:
        """Insert an unconsumed recovery probe grant."""
        return self.health.add_recovery_probe_grant(grant)

    def get_recovery_probe_grant(
        self,
        grant_id: str,
    ) -> RecoveryProbeGrant | None:
        """Return a recovery probe grant by grant ID."""
        return self.health.get_recovery_probe_grant(grant_id)

    def get_live_recovery_probe_grant(
        self,
        circuit_id: str,
    ) -> RecoveryProbeGrant | None:
        """Return the sole unconsumed grant for a circuit."""
        return self.health.get_live_recovery_probe_grant(circuit_id)

    def cas_claim_recovery_probe_grant(
        self,
        current: RecoveryProbeGrant,
        updated: RecoveryProbeGrant,
    ) -> bool:
        """Attempt a contention-safe single-use claim on an unconsumed probe grant."""
        return self.health.cas_claim_recovery_probe_grant(current, updated)

    def add_recovery_probe_receipt(
        self,
        receipt: RecoveryProbeReceipt,
    ) -> None:
        """Insert an immutable recovery probe receipt."""
        return self.health.add_recovery_probe_receipt(receipt)

    def get_recovery_probe_receipt(
        self,
        probe_receipt_id: str,
    ) -> RecoveryProbeReceipt | None:
        """Return a recovery probe receipt by probe receipt ID."""
        return self.health.get_recovery_probe_receipt(probe_receipt_id)

    def list_recovery_probe_receipts(
        self,
        grant_id: str,
    ) -> tuple[RecoveryProbeReceipt, ...]:
        """Return all recovery probe receipts for a grant ID ordered by observation time."""
        return self.health.list_recovery_probe_receipts(grant_id)

    def add_admission_snapshot(
        self,
        snapshot: AdmissionSnapshot,
    ) -> None:
        """Insert an immutable admission snapshot and all its entries."""
        return self.health.add_admission_snapshot(snapshot)

    def get_admission_snapshot(
        self,
        snapshot_id: str,
    ) -> AdmissionSnapshot | None:
        """Return a full admission snapshot including all its entries."""
        return self.health.get_admission_snapshot(snapshot_id)

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



    def add_artifact_manifest(
        self,
        manifest: ArtifactManifestRecord,
        artifacts: tuple[ArtifactMetadata, ...],
    ) -> None:
        """Insert durable artifact manifest and artifact metadata rows."""
        return self.dispatch.add_artifact_manifest(manifest, artifacts)

    def get_artifact_manifest(
        self, attempt_id: str
    ) -> ArtifactManifestRecord | None:
        """Return artifact manifest by attempt ID."""
        return self.dispatch.get_artifact_manifest(attempt_id)

    def get_artifact_metadata(
        self, attempt_id: str, artifact_id: str
    ) -> ArtifactMetadata | None:
        """Return artifact metadata by attempt ID and artifact ID."""
        return self.dispatch.get_artifact_metadata(attempt_id, artifact_id)

    def list_artifact_metadata(
        self, attempt_id: str
    ) -> tuple[ArtifactMetadata, ...]:
        """List all artifact metadata rows for an attempt."""
        return self.dispatch.list_artifact_metadata(attempt_id)

    def cas_update_artifact_metadata(
        self, current: ArtifactMetadata, updated: ArtifactMetadata
    ) -> bool:
        """CAS update artifact metadata row by revision."""
        return self.dispatch.cas_update_artifact_metadata(current, updated)

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
        return self.dispatch.reserve_verified_artifacts_for_dispatch(attempt_id=attempt_id, expected_manifest_digest=expected_manifest_digest, intent_event_id=intent_event_id, reserved_at=reserved_at)

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
        return self.dispatch.consume_reserved_artifacts(attempt_id=attempt_id, terminal_outcome_event_id=terminal_outcome_event_id, consumed_at=consumed_at)

    def get_artifact_recovery_digest(
        self, attempt_id: str
    ) -> ArtifactRecoveryDigest | None:
        """Return recovery digest for an attempt."""
        return self.dispatch.get_artifact_recovery_digest(attempt_id)

    def mark_artifacts_orphaned(
        self,
        *,
        attempt_id: str,
        expected_manifest_revision: int,
        orphaned_at: int,
        failure_code: str,
    ) -> bool:
        """Mark non-terminal artifacts as ORPHANED."""
        return self.dispatch.mark_artifacts_orphaned(attempt_id=attempt_id, expected_manifest_revision=expected_manifest_revision, orphaned_at=orphaned_at, failure_code=failure_code)

    def mark_artifact_cleaned(
        self, current: ArtifactMetadata, *, cleaned_at: int
    ) -> bool:
        """Mark a CONSUMED artifact as CLEANED. Rejects non-CONSUMED artifacts."""
        return self.dispatch.mark_artifact_cleaned(current, cleaned_at=cleaned_at)

    def mark_artifact_staged(
        self,
        *,
        attempt_id: str,
        artifact_id: str,
        staging_path_relative: str,
        expected_revision: int,
        staged_at: int,
    ) -> bool:
        """DECLARED → STAGED. Rejects if current state ≠ DECLARED or revision mismatch.

        Narrow typed repository method per the ratified ArtifactMaterializer
        contract (docs/design/SLICE5-KICKOFF-R1.md §1.4). Does NOT use the
        generic ``cas_update_artifact_metadata`` for this transition.
        """
        return self.dispatch.mark_artifact_staged(attempt_id=attempt_id, artifact_id=artifact_id, staging_path_relative=staging_path_relative, expected_revision=expected_revision, staged_at=staged_at)

    def mark_artifact_verified(
        self,
        *,
        attempt_id: str,
        artifact_id: str,
        verified_digest: str,
        verified_length: int,
        target_path_relative: str,
        expected_revision: int,
        verified_at: int,
    ) -> bool:
        """STAGED → VERIFIED. Rejects if current state ≠ STAGED or revision mismatch.

        Narrow typed repository method per the ratified ArtifactMaterializer
        contract (docs/design/SLICE5-KICKOFF-R1.md §1.4). Does NOT use the
        generic ``cas_update_artifact_metadata`` for this transition.
        """
        return self.dispatch.mark_artifact_verified(attempt_id=attempt_id, artifact_id=artifact_id, verified_digest=verified_digest, verified_length=verified_length, target_path_relative=target_path_relative, expected_revision=expected_revision, verified_at=verified_at)

    def reclaim_orphaned_artifact(
        self,
        current: ArtifactMetadata,
        *,
        cleaned_at: int,
    ) -> bool:
        """ORPHANED → CLEANED. The gap-closing method for the async GC pass.

        Mirrors ``mark_artifact_cleaned``'s CONSUMED-only guard pattern but
        for the ORPHANED→CLEANED transition. Rejects if current state ≠
        ORPHANED.

        Per docs/design/SLICE5-KICKOFF-R1.md §1.10: deliberately separate from
        ``mark_artifact_cleaned`` (CONSUMED→CLEANED) to keep the happy-path
        cleanup guard exactly as strict as Step 4 ratified it.
        """
        return self.dispatch.reclaim_orphaned_artifact(current, cleaned_at=cleaned_at)

