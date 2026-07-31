"""SQLite WAL implementation of the PeerHub state-store port."""

from __future__ import annotations

import json
import sqlite3
from importlib import resources
from pathlib import Path
from types import TracebackType
from typing import Any

from peerhub.core.errors import WorkspaceIdentityMismatchError
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import CommandID, canonical_json_bytes
from peerhub.dispatch.contract import (
    LeaseAuthorityCertainty,
    LeaseFenceTuple,
    LeaseSnapshot,
    LeaseState,
    ProcessBirthIdentity,
    RecoveryDecision,
    RecoveryReceipt,
    RecoveryTrigger,
    SessionBindingKey,
    SessionBindingSnapshot,
    SessionBindingState,
)
from peerhub.governance.contract import (
    CommandBinding,
    EffectIntent,
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


def _json_object(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("stored JSON value is not an object")
    return value


def _string_tuple(raw: str) -> tuple[str, ...]:
    value = json.loads(raw)
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) for item in value)
    ):
        raise RuntimeError(
            "stored evidence_refs is not a string array"
        )
    return tuple(value)


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
        """Apply Slice 1 and Slice 2 schemas and bind the workspace identity."""

        self._database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        migration_1 = (
            resources.files("peerhub.persistence.migrations")
            .joinpath("0001_phase1_kernel.sql")
            .read_text(encoding="utf-8")
        )
        migration_2 = (
            resources.files("peerhub.persistence.migrations")
            .joinpath("0002_dispatch_session_lease.sql")
            .read_text(encoding="utf-8")
        )

        connection = self._connect()
        try:
            connection.executescript(migration_1)
            connection.executescript(migration_2)
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
        """Return an idempotency binding."""

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
        """Insert an immutable command-ledger row."""

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
        """Insert one pending outbox event."""

        self._db().execute(
            """
            INSERT INTO outbox_events (
                event_id,
                request_id,
                transition_receipt_id,
                topic,
                payload_json,
                state,
                created_at,
                claimed_by,
                claim_attempt_id,
                claimed_at,
                consumed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.request_id,
                event.transition_receipt_id,
                event.topic,
                _json_text(event.payload),
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
        """Return one outbox event."""

        row = self._db().execute(
            """
            SELECT
                event_id,
                request_id,
                transition_receipt_id,
                topic,
                payload_json,
                state,
                created_at,
                claimed_by,
                claim_attempt_id,
                claimed_at,
                consumed_at
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
    ) -> tuple[OutboxEvent, ...]:
        """Return matching outbox events in deterministic order."""

        if not states:
            return ()
        placeholders = ", ".join("?" for _ in states)
        parameters = tuple(state.value for state in states) + (
            limit,
        )
        rows = self._db().execute(
            f"""
            SELECT
                event_id,
                request_id,
                transition_receipt_id,
                topic,
                payload_json,
                state,
                created_at,
                claimed_by,
                claim_attempt_id,
                claimed_at,
                consumed_at
            FROM outbox_events
            WHERE state IN ({placeholders})
            ORDER BY created_at, event_id
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

    def get_lease(self, lease_id: str) -> LeaseSnapshot | None:
        """Return a lease snapshot by ID."""

        row = self._db().execute(
            """
            SELECT
                lease_id,
                session_id,
                fencing_token,
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
            FROM leases
            WHERE lease_id = ?
            """,
            (lease_id,),
        ).fetchone()
        if row is None:
            return None
        process_identity = ProcessBirthIdentity(
            pid=row["owner_process_pid"],
            process_creation_time=row["owner_process_creation_time"],
        )
        fence = LeaseFenceTuple(
            session_id=row["session_id"],
            lease_id=row["lease_id"],
            fencing_token=row["fencing_token"],
            revision=row["revision"],
            owner_principal_id=row["owner_principal_id"],
            owner_instance_id=row["owner_instance_id"],
            owner_process_birth_identity=process_identity,
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

        self._db().execute(
            """
            INSERT INTO leases (
                lease_id,
                session_id,
                fencing_token,
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lease.lease_id,
                lease.session_id,
                lease.fence.fencing_token,
                lease.fence.revision,
                lease.fence.owner_principal_id,
                lease.fence.owner_instance_id,
                lease.fence.owner_process_birth_identity.pid,
                lease.fence.owner_process_birth_identity.process_creation_time,
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
        """CAS-update a lease by matching on current revision."""

        cursor = self._db().execute(
            """
            UPDATE leases
            SET
                fencing_token = ?,
                revision = ?,
                state = ?,
                heartbeat_expires_at = ?,
                updated_at = ?
            WHERE lease_id = ? AND revision = ?
            """,
            (
                updated.fence.fencing_token,
                updated.fence.revision,
                updated.state.value,
                updated.heartbeat_expires_at,
                updated.updated_at,
                current.lease_id,
                current.fence.revision,
            ),
        )
        return cursor.rowcount == 1

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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.recovery_receipt_id,
                receipt.session_id,
                receipt.lease_id,
                receipt.detected_at,
                receipt.recovery_actor_principal_id,
                receipt.trigger.value,
                _json_text(list(receipt.mismatch_dimensions)),
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
            SELECT
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
            FROM recovery_receipts
            WHERE recovery_receipt_id = ?
            """,
            (receipt_id,),
        ).fetchone()
        if row is None:
            return None
        raw_effect_certainty = row["external_effect_certainty"]
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
            recovery_actor_principal_id=row["recovery_actor_principal_id"],
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
            pre_lifecycle_state=LeaseState(row["pre_lifecycle_state"]),
            pre_revision=row["pre_revision"],
            pre_fencing_token=row["pre_fencing_token"],
            post_lifecycle_state=LeaseState(row["post_lifecycle_state"]),
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
    def _outbox_from_row(row: sqlite3.Row) -> OutboxEvent:
        return OutboxEvent(
            event_id=row["event_id"],
            request_id=row["request_id"],
            transition_receipt_id=(
                row["transition_receipt_id"]
            ),
            topic=row["topic"],
            payload=_json_object(row["payload_json"]),
            state=OutboxState(row["state"]),
            created_at=row["created_at"],
            claimed_by=row["claimed_by"],
            claim_attempt_id=row["claim_attempt_id"],
            claimed_at=row["claimed_at"],
            consumed_at=row["consumed_at"],
        )
