"""SQLite WAL implementation of the PeerHub state-store port.

Migrations are discovered from the packaged ``migrations`` directory and
applied exactly once, in ascending order. The sequence a database needs is
derived from the scripts that exist rather than from a hand-maintained
ladder in code, so a script can never be shipped and silently never run.
Migration 0003 fails closed when pre-Slice-3 lease rows exist because no
ratified source can supply their missing command, attempt, or
authority-epoch identities.
"""

from __future__ import annotations

import re
import sqlite3
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from types import TracebackType

from typing import Self, Sequence

from .sqlite_governance import SqliteGovernanceRepository
from .sqlite_dispatch import SqliteDispatchRepository
from .sqlite_health import SqliteHealthRepository
from .sqlite_telemetry import SqliteTelemetryRepository
from .sqlite_routing import SqliteRoutingRepository
from .sqlite_events import SqliteEventRepository


from peerhub.core.errors import (
    WorkspaceIdentityMismatchError,
)

from peerhub.core.protocol import (
    CommandID,
    EventEnvelope,
)
from peerhub.dispatch.capability import CapabilityLease
from peerhub.dispatch.contract import (
    AdmissionReceipt,
    ArtifactManifestRecord,
    ArtifactMetadata,
    ArtifactRecoveryDigest,
    AttemptSnapshot,
    ClientRequestBinding,
    CommandIdempotencyBinding,
    LeaseSnapshot,
    OutboxCheckpoint,
    RecoveryReceipt,
    RequestSnapshot,
    SessionBindingKey,
    SessionBindingSnapshot,
)
from peerhub.governance.contract import (
    CommandBinding,
    EffectReceipt,
    MutationPlan,
    MutationRequest,
    OutboxEvent,
    OutboxState,
    TargetState,
    TransitionReceipt,
)
from peerhub.health.contract import (
    AdmissionSnapshot,
    HealthCircuitSnapshot,
    HealthPolicy,
    HealthProjectionSnapshot,
    PolicyScope,
    RecoveryProbeGrant,
    RecoveryProbeReceipt,
)
from peerhub.routing.contract import (
    RouteDecision,
)
from peerhub.telemetry.contract import (
    OperationalObservation,
    OperationalProjectionSnapshot,
    ReadinessObserved,
    SessionBindingKey,
    SessionContextObserved,
    SessionContextProjectionSnapshot,
)





_MIGRATION_FILENAME = re.compile(r"\A(\d{4})_[A-Za-z0-9_]+\.sql\Z")

# Migrations 0002, 0009, and 0010 predate the self-contained migration
# template and open no transaction of their own, so the runner supplies
# one. This set is closed: every migration from 0011 onward follows the
# template documented in docs/migrations.md.
_UNWRAPPED_MIGRATIONS = frozenset({2, 9, 10})

# Migration 0002 also predates self-recording and writes neither its
# schema_migrations row nor user_version.
_UNRECORDED_MIGRATIONS = frozenset({2})


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
        """Apply every packaged migration once and bind workspace identity.

        The sequence is derived from the migration scripts that exist, not
        from a ladder maintained by hand, and the run is verified complete
        before returning. A database can therefore never be left looking
        fully migrated while a shipped migration has not run.
        """

        self._database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        connection = self._connect()
        try:
            available = self._available_migrations()
            applied = self._applied_versions(connection)
            self._reject_unknown_migrations(applied, available)

            for version, filename in available:
                if version in applied:
                    continue
                self._apply_migration(connection, version, filename)

            self._verify_sequence_complete(connection, available)

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

    def read_unit_of_work(self) -> SqliteReadUnitOfWork:
        """Return a new SQLite read-only unit of work."""

        return SqliteReadUnitOfWork(self)

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

    def _connect_read(self) -> sqlite3.Connection:
        connection = self._connect()
        connection.execute("PRAGMA query_only = ON")
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

    @classmethod
    def _applied_versions(
        cls,
        connection: sqlite3.Connection,
    ) -> frozenset[int]:
        """Return recorded migrations, treating a bare database as empty."""

        if not cls._table_exists(connection, "schema_migrations"):
            return frozenset()
        return cls._migration_versions(connection)

    @staticmethod
    def _migration_directory() -> Traversable:
        return resources.files("peerhub.persistence.migrations")

    @classmethod
    def _available_migrations(cls) -> tuple[tuple[int, str], ...]:
        """Return every packaged migration as ``(version, filename)``.

        Versions must be unique and contiguous from 1: a gap means a script
        is missing, and applying the sequence across it would produce a
        schema no migration ever described.
        """

        discovered: dict[int, str] = {}
        for entry in cls._migration_directory().iterdir():
            if not entry.is_file():
                continue
            match = _MIGRATION_FILENAME.match(entry.name)
            if match is None:
                continue
            version = int(match.group(1))
            duplicate = discovered.get(version)
            if duplicate is not None:
                raise RuntimeError(
                    "duplicate migration version "
                    f"{version}: {duplicate!r} and {entry.name!r}"
                )
            discovered[version] = entry.name

        if not discovered:
            raise RuntimeError(
                "no packaged migrations were found"
            )

        expected = tuple(range(1, max(discovered) + 1))
        if tuple(sorted(discovered)) != expected:
            missing = sorted(set(expected) - set(discovered))
            raise RuntimeError(
                "packaged migrations are not contiguous from 1; "
                f"missing {missing}"
            )

        return tuple(
            (version, discovered[version]) for version in expected
        )

    @classmethod
    def _apply_migration(
        cls,
        connection: sqlite3.Connection,
        version: int,
        filename: str,
    ) -> None:
        """Run one migration script, supplying anything it predates."""

        script = cls._migration_text(filename)
        if version in _UNRECORDED_MIGRATIONS:
            name = filename.removesuffix(".sql")
            script = "\n".join(
                (
                    script,
                    (
                        "INSERT INTO schema_migrations(version, name) "
                        f"VALUES ({version}, '{name}');"
                    ),
                    f"PRAGMA user_version = {version};",
                )
            )
        if version in _UNWRAPPED_MIGRATIONS:
            script = "\n".join(
                ("BEGIN IMMEDIATE;", script, "COMMIT;")
            )
        connection.executescript(script)

    @classmethod
    def _reject_unknown_migrations(
        cls,
        applied: frozenset[int],
        available: tuple[tuple[int, str], ...],
    ) -> None:
        """Refuse a database written by a build with more migrations."""

        unknown = sorted(
            applied - {version for version, _ in available}
        )
        if unknown:
            raise RuntimeError(
                "database records migrations this build does not "
                f"provide: {unknown}; it was created by a newer "
                "PeerHub and must not be downgraded in place"
            )

    @classmethod
    def _verify_sequence_complete(
        cls,
        connection: sqlite3.Connection,
        available: tuple[tuple[int, str], ...],
    ) -> None:
        """Fail unless every available migration recorded itself.

        A script that runs without inserting its ``schema_migrations`` row
        would otherwise re-run on every startup, forever, in silence.
        """

        applied = cls._migration_versions(connection)
        missing = sorted(
            version
            for version, _ in available
            if version not in applied
        )
        if missing:
            raise RuntimeError(
                "migration sequence did not complete: migrations "
                f"{missing} ran without recording themselves in "
                "schema_migrations"
            )

    @classmethod
    def _migration_text(cls, name: str) -> str:
        return (
            cls._migration_directory()
            .joinpath(name)
            .read_text(encoding="utf-8")
        )


class SqliteReadUnitOfWork:
    """One read-only view over SQLite state storage using PRAGMA query_only=ON and deferred transactions."""

    def __init__(self, store: SqliteStateStore) -> None:
        self._store = store
        self._connection: sqlite3.Connection | None = None
        self._finished = False
        self._governance = SqliteGovernanceRepository(self._db)
        self._dispatch = SqliteDispatchRepository(self._db)
        self._health = SqliteHealthRepository(self._db)
        self._telemetry = SqliteTelemetryRepository(self._db)
        self._routing = SqliteRoutingRepository(self._db)
        self._events = SqliteEventRepository(self._db)

    @property
    def governance(self) -> SqliteGovernanceRepository:
        """The governance domain repository facet."""
        return self._governance

    @property
    def dispatch(self) -> SqliteDispatchRepository:
        """The dispatch domain repository facet."""
        return self._dispatch

    @property
    def health(self) -> SqliteHealthRepository:
        """The health domain repository facet."""
        return self._health

    @property
    def telemetry(self) -> SqliteTelemetryRepository:
        """The telemetry domain repository facet."""
        return self._telemetry

    @property
    def routing(self) -> SqliteRoutingRepository:
        """The routing domain repository facet."""
        return self._routing

    @property
    def events(self) -> SqliteEventRepository:
        """The events domain repository facet."""
        return self._events

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError(
                "SQLite read unit of work has not been entered"
            )
        return self._connection

    def __enter__(self) -> Self:
        """Open a query_only connection and begin a deferred transaction."""
        if self._connection is not None:
            raise RuntimeError(
                "SQLite read unit of work cannot be re-entered"
            )
        self._connection = self._store._connect_read()  # pyright: ignore[reportPrivateUsage]
        self._connection.execute("BEGIN")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the read-only connection and roll back any deferred read state."""
        del exception_type, exception, traceback
        connection = self._connection
        if connection is None:
            return
        try:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
        finally:
            connection.close()
            self._connection = None
            self._finished = True

    def close(self) -> None:
        """Explicitly close the read-only unit of work."""
        connection = self._connection
        if connection is not None:
            try:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
            finally:
                connection.close()
                self._connection = None
                self._finished = True

    def count_active_leases(self) -> int:
        """Return the count of active leases."""
        return self.dispatch.count_active_leases()

    def get_client_request_binding(
        self,
        client_id: str,
        client_request_id: str,
    ) -> ClientRequestBinding | None:
        """Return a caller-request identity binding."""
        return self.dispatch.get_client_request_binding(client_id, client_request_id)

    def get_command_idempotency_binding(
        self,
        client_id: str,
        command_type: str,
        idempotency_key: str,
    ) -> CommandIdempotencyBinding | None:
        """Return a command-idempotency binding."""
        return self.dispatch.get_command_idempotency_binding(client_id, command_type, idempotency_key)

    def get_lease(self, lease_id: str) -> LeaseSnapshot | None:
        """Return one lease by ID."""
        return self.dispatch.get_lease(lease_id)

    def get_request(self, command_id: CommandID | str) -> RequestSnapshot | None:
        """Return one request by ID."""
        return self.dispatch.get_request(command_id)

    def get_attempt(self, attempt_id: str) -> AttemptSnapshot | None:
        """Return one attempt by ID."""
        return self.dispatch.get_attempt(attempt_id)

    def get_session_binding(self, key: SessionBindingKey) -> SessionBindingSnapshot | None:
        """Return one session binding by key."""
        return self.dispatch.get_session_binding(key)

    def get_admission_receipt(
        self,
        admission_receipt_id: str,
    ) -> AdmissionReceipt | None:
        """Return an admission receipt by ID."""
        return self.dispatch.get_admission_receipt(admission_receipt_id)

    def get_capability_lease(
        self,
        capability_lease_id: str,
    ) -> CapabilityLease | None:
        """Return one capability lease by ID."""
        return self.dispatch.get_capability_lease(capability_lease_id)

    def get_capability_lease_by_command_id(
        self,
        command_id: CommandID | str,
    ) -> CapabilityLease | None:
        """Return the capability lease uniquely bound to a command."""
        return self.dispatch.get_capability_lease_by_command_id(command_id)

    def get_capability_lease_by_admission_receipt_id(
        self,
        admission_receipt_id: str,
    ) -> CapabilityLease | None:
        """Return the lease uniquely bound to an admission receipt."""
        return self.dispatch.get_capability_lease_by_admission_receipt_id(
            admission_receipt_id
        )

    def get_target(self, target_id: str) -> TargetState | None:
        """Return the current target, if present."""
        return self.governance.get_target(target_id)

    def get_transition_receipt(
        self,
        receipt_id: str,
    ) -> TransitionReceipt | None:
        """Return a transition receipt by ID."""
        return self.governance.get_transition_receipt(receipt_id)

    def get_effect_receipt(
        self,
        outbox_event_id: str,
    ) -> EffectReceipt | None:
        """Return an effect receipt by outbox event ID."""
        return self.governance.get_effect_receipt(outbox_event_id)

    def get_outbox_event(self, event_id: str) -> OutboxEvent | None:
        """Return one canonical outbox event."""
        return self.governance.get_outbox_event(event_id)

    def list_outbox_events(
        self,
        states: tuple[OutboxState, ...] = (OutboxState.PENDING,),
        *,
        limit: int = 100,
        governance_only: bool = False,
    ) -> Sequence[OutboxEvent]:
        """Return a page of canonical outbox events matching states."""
        return self.governance.list_outbox_events(states, limit=limit, governance_only=governance_only)

    def list_unfinished_effect_deliveries(
        self,
        *,
        limit: int = 100,
        after_position: int = 0,
    ) -> Sequence[OutboxEvent]:
        """Return a page of unfinished effect deliveries ordered by position."""
        return self.governance.list_unfinished_effect_deliveries(limit=limit, after_position=after_position)

    def get_route_decision(self, decision_id: str) -> RouteDecision | None:
        """Return one persisted route decision."""
        return self.routing.get_route_decision(decision_id)

    def get_operational_projection(self, instance_id: str, profile_id: str) -> OperationalProjectionSnapshot | None:
        """Return one operational projection."""
        return self.telemetry.get_operational_projection(instance_id, profile_id)

    def get_health_circuit(self, scope: PolicyScope, subject: str) -> HealthCircuitSnapshot | None:
        """Return one health circuit snapshot."""
        return self.health.get_health_circuit(scope, subject)

    def get_health_policy_revision(self, policy_id: str, revision: int) -> HealthPolicy | None:
        """Return one health policy revision."""
        return self.health.get_health_policy_revision(policy_id, revision)

    def get_health_projection(self, instance_id: str, profile_id: str) -> HealthProjectionSnapshot | None:
        """Return one health projection snapshot."""
        return self.health.get_health_projection(instance_id, profile_id)

    def get_artifact_recovery_digest(self, attempt_id: str) -> ArtifactRecoveryDigest | None:
        """Return recovery digest for an attempt."""
        return self.dispatch.get_artifact_recovery_digest(attempt_id)


class SqliteUnitOfWork:
    """One `BEGIN IMMEDIATE` SQLite transaction."""

    def __init__(self, store: SqliteStateStore) -> None:
        self._store = store
        self._connection: sqlite3.Connection | None = None
        self._finished = False
        self._governance = SqliteGovernanceRepository(self._db)
        self._dispatch = SqliteDispatchRepository(self._db)
        self._health = SqliteHealthRepository(self._db)
        self._telemetry = SqliteTelemetryRepository(self._db)
        self._routing = SqliteRoutingRepository(self._db)
        self._events = SqliteEventRepository(self._db)

    @property
    def governance(self) -> SqliteGovernanceRepository:
        """The governance domain repository facet."""
        return self._governance

    @property
    def dispatch(self) -> SqliteDispatchRepository:
        """The dispatch domain repository facet."""
        return self._dispatch

    @property
    def health(self) -> SqliteHealthRepository:
        """The health domain repository facet."""
        return self._health

    @property
    def telemetry(self) -> SqliteTelemetryRepository:
        """The telemetry domain repository facet."""
        return self._telemetry

    @property
    def routing(self) -> SqliteRoutingRepository:
        """The routing domain repository facet."""
        return self._routing

    @property
    def events(self) -> SqliteEventRepository:
        """The events domain repository facet."""
        return self._events

    def __enter__(self) -> SqliteUnitOfWork:
        """Open a connection and begin an immediate transaction."""

        if self._connection is not None:
            raise RuntimeError(
                "SQLite unit of work cannot be re-entered"
            )
        self._connection = self._store._connect()  # pyright: ignore[reportPrivateUsage]
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

    def close(self) -> None:
        """Explicitly close the unit of work."""
        connection = self._connection
        if connection is not None:
            try:
                if not self._finished and connection.in_transaction:
                    connection.execute("ROLLBACK")
            finally:
                connection.close()
                self._connection = None
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
        """Insert one canonical pending outbox event and dual-write to new event log."""
        
        # Dual-write mapping:
        envelope = EventEnvelope(
            protocol_major=event.protocol_major,
            protocol_minor=event.protocol_minor,
            schema_version=event.schema_version,
            event_id=event.event_id,
            correlation_id=event.correlation_id,
            occurred_at=event.occurred_at,
            kind=event.event_kind,
            payload=event.payload,
            request_id=event.request_id,
            round_id=event.round_id,
            evidence_refs=event.evidence_refs,
            predecessor_digest=event.predecessor_digest,
            recovery_context=event.recovery_context,
        )
        outbox_position = self.events.append(envelope, appended_at=event.created_at)

        if event.transition_receipt_id is not None:
            self._db().execute(
                """
                INSERT INTO effect_deliveries (
                    event_id,
                    outbox_position,
                    request_id,
                    transition_receipt_id,
                    topic
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    outbox_position,
                    event.request_id,
                    event.transition_receipt_id,
                    event.topic,
                ),
            )

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

    def list_outbox_events_by_command(
        self,
        command_id: str,
    ) -> tuple[OutboxEvent, ...]:
        """Return all outbox events for a given command_id, ordered by position."""
        return self.governance.list_outbox_events_by_command(command_id)

    def get_effect_delivery(
        self,
        event_id: str,
    ) -> OutboxEvent | None:
        """Return one effect delivery hydrated as an OutboxEvent."""
        return self.governance.get_effect_delivery(event_id)

    def list_unfinished_effect_deliveries(
        self,
        *,
        limit: int,
        after_position: int = 0,
    ) -> tuple[OutboxEvent, ...]:
        """Return pending AND claimed (but unreceipted) deliveries in outbox order."""
        return self.governance.list_unfinished_effect_deliveries(limit=limit, after_position=after_position)

    def list_claimable_effect_deliveries(
        self,
        *,
        limit: int,
        after_position: int = 0,
    ) -> tuple[OutboxEvent, ...]:
        """Return ONLY pending (unclaimed) deliveries in outbox order."""
        return self.governance.list_claimable_effect_deliveries(limit=limit, after_position=after_position)

    def claim_effect_delivery(
        self,
        event_id: str,
        owner_id: str,
        attempt_id: str,
        claimed_at: int,
    ) -> OutboxEvent | None:
        """CAS-claim an effect delivery and mirror its legacy outbox state."""
        return self.governance.claim_effect_delivery(
            event_id,
            owner_id,
            attempt_id,
            claimed_at,
        )

    def complete_effect_delivery(
        self,
        receipt: EffectReceipt,
    ) -> bool:
        """Guardedly complete an effect delivery and mirror legacy state."""
        return self.governance.complete_effect_delivery(receipt)

    def claim_outbox_event(
        self,
        event_id: str,
        owner_id: str,
        attempt_id: str,
        claimed_at: int,
    ) -> OutboxEvent | None:
        """CAS-claim one pending outbox event."""
        claimed = self.governance.claim_outbox_event(event_id, owner_id, attempt_id, claimed_at)
        if claimed is not None:
            self._db().execute(
                """
                UPDATE effect_deliveries
                SET
                    claimed_by = ?,
                    claim_attempt_id = ?,
                    claimed_at = ?
                WHERE event_id = ?
                """,
                (owner_id, attempt_id, claimed_at, event_id),
            )
        return claimed

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

    def count_active_leases(self) -> int:
        """Return the number of active leases."""
        return self.dispatch.count_active_leases()

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

    def add_capability_lease(
        self,
        lease: CapabilityLease,
    ) -> None:
        """Insert an immutable capability lease."""
        return self.dispatch.add_capability_lease(lease)

    def get_capability_lease(
        self,
        capability_lease_id: str,
    ) -> CapabilityLease | None:
        """Return one capability lease by ID."""
        return self.dispatch.get_capability_lease(capability_lease_id)

    def get_capability_lease_by_command_id(
        self,
        command_id: CommandID | str,
    ) -> CapabilityLease | None:
        """Return the capability lease uniquely bound to a command."""
        return self.dispatch.get_capability_lease_by_command_id(command_id)

    def get_capability_lease_by_admission_receipt_id(
        self,
        admission_receipt_id: str,
    ) -> CapabilityLease | None:
        """Return the lease uniquely bound to an admission receipt."""
        return self.dispatch.get_capability_lease_by_admission_receipt_id(
            admission_receipt_id
        )

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
        return self.telemetry.add_readiness_observation(observed)

    def get_readiness_observation(
        self,
        observation_id: str,
    ) -> ReadinessObserved | None:
        """Return a readiness observation by observation ID."""
        return self.telemetry.get_readiness_observation(observation_id)

    def add_operational_observation(
        self,
        observation: OperationalObservation,
    ) -> None:
        """Insert an immutable operational observation."""
        return self.telemetry.add_operational_observation(observation)

    def get_operational_observation(
        self,
        observation_id: str,
    ) -> OperationalObservation | None:
        """Return an operational observation by observation ID."""
        return self.telemetry.get_operational_observation(observation_id)

    def add_operational_projection(
        self,
        projection: OperationalProjectionSnapshot,
    ) -> None:
        """Insert a revision-one operational projection snapshot."""
        return self.telemetry.add_operational_projection(projection)

    def get_operational_projection(
        self,
        instance_id: str,
        profile_id: str,
    ) -> OperationalProjectionSnapshot | None:
        """Return an operational projection by instance ID and profile ID."""
        return self.telemetry.get_operational_projection(instance_id, profile_id)

    def cas_update_operational_projection(
        self,
        current: OperationalProjectionSnapshot,
        updated: OperationalProjectionSnapshot,
    ) -> bool:
        """CAS-update an operational projection snapshot."""
        return self.telemetry.cas_update_operational_projection(current, updated)

    def add_session_context_observation(
        self,
        observation: SessionContextObserved,
    ) -> None:
        """Insert an immutable session context observation."""
        return self.telemetry.add_session_context_observation(observation)  # pyright: ignore[reportUnknownMemberType]

    def get_session_context_projection(
        self,
        workspace_scope_id: str,
        instance_id: str,
        profile_id: str,
        conversation_scope: str,
        generation_id: int,
    ) -> SessionContextProjectionSnapshot | None:
        """Return the current context occupancy by binding+generation."""
        return self.telemetry.get_session_context_projection(workspace_scope_id, instance_id, profile_id, conversation_scope, generation_id)

    def add_session_context_projection(
        self,
        projection: SessionContextProjectionSnapshot,
    ) -> None:
        """Insert a revision-one session context projection."""
        return self.telemetry.add_session_context_projection(projection)

    def cas_update_session_context_projection(
        self,
        current: SessionContextProjectionSnapshot,
        updated: SessionContextProjectionSnapshot,
    ) -> bool:
        """CAS-update a session context projection snapshot."""
        return self.telemetry.cas_update_session_context_projection(current, updated)

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
        return self.routing.add_route_decision(decision)

    def get_route_decision(
        self,
        decision_id: str,
    ) -> RouteDecision | None:
        """Return a full route decision audit including all candidate decisions."""
        return self.routing.get_route_decision(decision_id)

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
