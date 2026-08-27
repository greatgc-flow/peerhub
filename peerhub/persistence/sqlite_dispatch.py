import sqlite3
from collections.abc import Callable, Mapping

from .sqlite_helpers import (
    _json_text,  # pyright: ignore[reportPrivateUsage]
    _json_object,  # pyright: ignore[reportPrivateUsage]
    _string_tuple,  # pyright: ignore[reportPrivateUsage]
    _stored_revision,  # pyright: ignore[reportPrivateUsage]
    _stored_optional_revision,  # pyright: ignore[reportPrivateUsage]
)

from peerhub.core.errors import InvalidMutationError
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import (
    CommandID,
    ErrorCode,
    ErrorPhase,
    OperationalFailureCategory,
)
from peerhub.dispatch.capability import (
    CapabilityLease,
    CapabilityTier,
    EnforcementLevel,
)
from peerhub.dispatch.contract import (
    AdmissionReceipt,
    ArtifactManifestRecord,
    ArtifactMetadata,
    ArtifactRecoveryDigest,
    ArtifactState,
    AskResult,
    AttemptFailureClassification,
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
    SessionRotationState,
    SessionRotationKey,
    SessionRotationGenerationSnapshot,
    TerminalClassification,
)
from peerhub.dispatch.duty_lease import DutyLeaseSnapshot, DutyLeaseState, DutyOwnerIdentity, DutyRecoveryReceipt
from peerhub.dispatch.room_session import (
    RoomSessionEvent,
    RoomSessionSnapshot,
    RoomSessionState,
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
        for item in requirements  # pyright: ignore[reportUnknownVariableType]
    ):
        raise RuntimeError(
            "stored completion requirements are invalid"
        )
    return CompletionContract(
        contract_id=str(value["contract_id"]),
        kind=CompletionContractKind(str(value["kind"])),
        requirements=tuple(requirements),  # pyright: ignore[reportUnknownArgumentType]
        replay_safe=bool(value["replay_safe"]),
    )


def _required_capability_tier_from_stored(
    raw: object,
) -> CapabilityTier:
    if not isinstance(raw, str):
        raise RuntimeError(
            "stored request is missing required_capability_tier"
        )
    try:
        return CapabilityTier[raw]
    except KeyError as exc:
        raise RuntimeError(
            "stored request required_capability_tier is invalid"
        ) from exc


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
        "terminal_classification": (
            result.terminal_classification.value
            if result.terminal_classification is not None
            else None
        ),
        "failure_classification": (
            {
                "code": result.failure_classification.code.value,
                "phase": result.failure_classification.phase.value,
                "operational_failure_category": (
                    result.failure_classification.operational_failure_category.value
                    if result.failure_classification.operational_failure_category is not None
                    else None
                ),
            }
            if result.failure_classification is not None
            else None
        ),
    }


def _ask_result_from_raw(raw: str) -> AskResult:
    value = _json_object(raw)

    # Distinguishing explicit unknown vs explicit none is deferred to increment 5 (outer retry/resume loop)
    execution = value.get("execution")
    protocol = value.get("protocol")
    completion = value.get("completion")
    if not isinstance(execution, dict):
        raise RuntimeError("stored execution outcome is invalid")
    if not isinstance(protocol, dict):
        raise RuntimeError("stored protocol assessment is invalid")
    if not isinstance(completion, dict):
        raise RuntimeError("stored completion assessment is invalid")

    raw_failure = protocol.get("protocol_failure")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    failure = (
        ErrorCode(str(raw_failure))  # pyright: ignore[reportUnknownArgumentType]
        if raw_failure is not None
        else None
    )
    failed_requirements = completion.get(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        "failed_requirements"
    )
    evidence_refs = completion.get("evidence_refs")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
            started=bool(execution["started"]),  # pyright: ignore[reportUnknownArgumentType]
            exit_code=execution.get("exit_code"),  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
            timed_out=bool(execution["timed_out"]),  # pyright: ignore[reportUnknownArgumentType]
            cancelled=bool(execution["cancelled"]),  # pyright: ignore[reportUnknownArgumentType]
            execution_certainty=ExecutionCertainty(
                str(execution["execution_certainty"])  # pyright: ignore[reportUnknownArgumentType]
            ),
        ),
        protocol=ProtocolAssessment(
            parsed=bool(protocol["parsed"]),  # pyright: ignore[reportUnknownArgumentType]
            response_present=bool(
                protocol["response_present"]  # pyright: ignore[reportUnknownArgumentType]
            ),
            vendor_completion_marker=protocol.get(  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
                "vendor_completion_marker"
            ),
            suspected_truncation=bool(
                protocol["suspected_truncation"]  # pyright: ignore[reportUnknownArgumentType]
            ),
            protocol_failure=failure,
        ),
        completion=CompletionAssessment(
            state=CompletionAssessmentState(
                str(completion["state"])  # pyright: ignore[reportUnknownArgumentType]
            ),
            contract_kind=CompletionContractKind(
                str(completion["contract_kind"])  # pyright: ignore[reportUnknownArgumentType]
            ),
            failed_requirements=tuple(
                str(item) for item in failed_requirements  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
            ),
            evidence_refs=tuple(
                str(item) for item in evidence_refs  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
            ),
        ),
        policy_revision=policy_revision,
        terminal_classification=(
            TerminalClassification(str(value["terminal_classification"]))
            if value.get("terminal_classification") is not None
            else None
        ),
        failure_classification=(
            AttemptFailureClassification(
                code=ErrorCode(str(value["failure_classification"]["code"])),  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
                phase=ErrorPhase(str(value["failure_classification"]["phase"])),  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
                operational_failure_category=(
                    OperationalFailureCategory(str(value["failure_classification"]["operational_failure_category"]))  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
                    if value["failure_classification"].get("operational_failure_category") is not None  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
                    else None
                ),
            )
            if value.get("failure_classification") is not None
            else None
        ),
    )


class SqliteDispatchRepository:
    def __init__(self, db_factory: Callable[[], sqlite3.Connection]) -> None:  # pyright: ignore[reportUnknownParameterType]
        self._db = db_factory  # pyright: ignore[reportUnknownMemberType]

    @staticmethod
    def _duty_snapshot(row: sqlite3.Row) -> DutyLeaseSnapshot:
        return DutyLeaseSnapshot(
            row["lease_id"], row["room_id"], row["role"],
            DutyOwnerIdentity(row["owner_instance_id"], row["owner_profile_id"]),
            row["owner_principal_id"], row["authority_epoch"], row["term"],
            row["challenge_until"], DutyLeaseState(row["state"]),
            row["heartbeat_expires_at"], row["created_at"], row["updated_at"],
            row["consecutive_terms_held"],
        )

    def get_duty_lease(self, lease_id: str) -> DutyLeaseSnapshot | None:
        row = self._db().execute("SELECT * FROM duty_leases WHERE lease_id = :lease_id", {"lease_id": lease_id}).fetchone()
        return None if row is None else self._duty_snapshot(row)

    def get_active_duty_lease(self, room_id: str, role: str) -> DutyLeaseSnapshot | None:
        row = self._db().execute("SELECT * FROM duty_leases WHERE room_id = :room_id AND role = :role AND state = 'ACTIVE'", {"room_id": room_id, "role": role}).fetchone()
        return None if row is None else self._duty_snapshot(row)

    def get_latest_duty_lease(self, room_id: str, role: str) -> DutyLeaseSnapshot | None:
        row = self._db().execute("SELECT * FROM duty_leases WHERE room_id = :room_id AND role = :role ORDER BY authority_epoch DESC LIMIT 1", {"room_id": room_id, "role": role}).fetchone()
        return None if row is None else self._duty_snapshot(row)

    def list_expired_duty_leases(
        self, role: str, as_of: int
    ) -> tuple[DutyLeaseSnapshot, ...]:
        rows = self._db().execute(
            """
            SELECT *
            FROM duty_leases
            WHERE role = :role
              AND state = 'ACTIVE'
              AND heartbeat_expires_at < :as_of
            ORDER BY heartbeat_expires_at, room_id, lease_id
            """,
            {"role": role, "as_of": as_of},
        ).fetchall()
        return tuple(self._duty_snapshot(row) for row in rows)

    def mark_duty_lease_expired(self, lease_id: str, updated_at: int) -> None:
        self._db().execute("UPDATE duty_leases SET state = 'EXPIRED', updated_at = :updated_at WHERE lease_id = :lease_id", {"updated_at": updated_at, "lease_id": lease_id})

    def insert_duty_lease(self, snapshot: DutyLeaseSnapshot) -> None:
        self._db().execute("""INSERT INTO duty_leases (lease_id, room_id, role, owner_instance_id, owner_profile_id, owner_principal_id, authority_epoch, term, challenge_until, state, heartbeat_expires_at, created_at, updated_at, consecutive_terms_held) VALUES (:lease_id, :room_id, :role, :owner_instance_id, :owner_profile_id, :owner_principal_id, :authority_epoch, :term, :challenge_until, :state, :heartbeat_expires_at, :created_at, :updated_at, :consecutive_terms_held)""", {"lease_id": snapshot.lease_id, "room_id": snapshot.room_id, "role": snapshot.role, "owner_instance_id": snapshot.owner.instance_id, "owner_profile_id": snapshot.owner.profile_id, "owner_principal_id": snapshot.owner_principal_id, "authority_epoch": snapshot.authority_epoch, "term": snapshot.term, "challenge_until": snapshot.challenge_until, "state": snapshot.state.value, "heartbeat_expires_at": snapshot.heartbeat_expires_at, "created_at": snapshot.created_at, "updated_at": snapshot.updated_at, "consecutive_terms_held": snapshot.consecutive_terms_held})

    def update_duty_lease_heartbeat(self, lease_id: str, heartbeat_expires_at: int, updated_at: int) -> None:
        self._db().execute("UPDATE duty_leases SET heartbeat_expires_at = :heartbeat_expires_at, updated_at = :updated_at WHERE lease_id = :lease_id", {"heartbeat_expires_at": heartbeat_expires_at, "updated_at": updated_at, "lease_id": lease_id})

    def release_duty_lease(self, lease_id: str, updated_at: int) -> None:
        self._db().execute("UPDATE duty_leases SET state = 'RELEASED', updated_at = :updated_at WHERE lease_id = :lease_id", {"updated_at": updated_at, "lease_id": lease_id})

    def insert_duty_recovery_receipt(self, receipt_id: str, receipt: DutyRecoveryReceipt) -> None:
        self._db().execute("INSERT INTO duty_lease_recovery_receipts VALUES (:id, :lease, :at, :actor, :trigger, :digest, :policy, :revision)", {"id": receipt_id, "lease": receipt.lease_id, "at": receipt.recovered_at, "actor": receipt.recovery_actor_principal_id, "trigger": receipt.trigger, "digest": receipt.evidence_digest, "policy": receipt.policy_id, "revision": receipt.policy_revision})

    @staticmethod
    def _room_session_snapshot(row: sqlite3.Row) -> RoomSessionSnapshot:
        return RoomSessionSnapshot(
            session_id=row["session_id"],
            workspace_scope_id=row["workspace_scope_id"],
            room_id=row["room_id"],
            actor_principal_id=row["actor_principal_id"],
            owner=DutyOwnerIdentity(
                row["owner_instance_id"], row["owner_profile_id"]
            ),
            session_fingerprint=row["session_fingerprint"],
            session_generation=row["session_generation"],
            resume_parent_session_id=row["resume_parent_session_id"],
            state=RoomSessionState(row["state"]),
            heartbeat_expires_at=row["heartbeat_expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_room_session(
        self, session_id: str
    ) -> RoomSessionSnapshot | None:
        row = self._db().execute(
            """
            SELECT *
            FROM room_participation_sessions
            WHERE session_id = :session_id
            """,
            {"session_id": session_id},
        ).fetchone()
        return None if row is None else self._room_session_snapshot(row)

    def get_active_room_session(
        self,
        workspace_scope_id: str,
        room_id: str,
        actor_principal_id: str,
        instance_id: str,
        profile_id: str,
    ) -> RoomSessionSnapshot | None:
        row = self._db().execute(
            """
            SELECT *
            FROM room_participation_sessions
            WHERE workspace_scope_id = :workspace_scope_id
              AND room_id = :room_id
              AND actor_principal_id = :actor_principal_id
              AND owner_instance_id = :instance_id
              AND owner_profile_id = :profile_id
              AND state = 'ACTIVE'
            """,
            {
                "workspace_scope_id": workspace_scope_id,
                "room_id": room_id,
                "actor_principal_id": actor_principal_id,
                "instance_id": instance_id,
                "profile_id": profile_id,
            },
        ).fetchone()
        return None if row is None else self._room_session_snapshot(row)

    def list_active_room_sessions(
        self, room_id: str
    ) -> tuple[RoomSessionSnapshot, ...]:
        rows = self._db().execute(
            """
            SELECT *
            FROM room_participation_sessions
            WHERE room_id = :room_id
              AND state = 'ACTIVE'
            ORDER BY created_at, session_id
            """,
            {"room_id": room_id},
        ).fetchall()
        return tuple(self._room_session_snapshot(row) for row in rows)

    def get_latest_room_session(
        self,
        workspace_scope_id: str,
        room_id: str,
        actor_principal_id: str,
        instance_id: str,
        profile_id: str,
    ) -> RoomSessionSnapshot | None:
        row = self._db().execute(
            """
            SELECT *
            FROM room_participation_sessions
            WHERE workspace_scope_id = :workspace_scope_id
              AND room_id = :room_id
              AND actor_principal_id = :actor_principal_id
              AND owner_instance_id = :instance_id
              AND owner_profile_id = :profile_id
            ORDER BY session_generation DESC
            LIMIT 1
            """,
            {
                "workspace_scope_id": workspace_scope_id,
                "room_id": room_id,
                "actor_principal_id": actor_principal_id,
                "instance_id": instance_id,
                "profile_id": profile_id,
            },
        ).fetchone()
        return None if row is None else self._room_session_snapshot(row)

    def insert_room_session(self, snapshot: RoomSessionSnapshot) -> None:
        self._db().execute(
            """
            INSERT INTO room_participation_sessions (
                session_id,
                workspace_scope_id,
                room_id,
                actor_principal_id,
                owner_instance_id,
                owner_profile_id,
                session_fingerprint,
                session_generation,
                resume_parent_session_id,
                state,
                heartbeat_expires_at,
                created_at,
                updated_at
            ) VALUES (
                :session_id,
                :workspace_scope_id,
                :room_id,
                :actor_principal_id,
                :owner_instance_id,
                :owner_profile_id,
                :session_fingerprint,
                :session_generation,
                :resume_parent_session_id,
                :state,
                :heartbeat_expires_at,
                :created_at,
                :updated_at
            )
            """,
            {
                "session_id": snapshot.session_id,
                "workspace_scope_id": snapshot.workspace_scope_id,
                "room_id": snapshot.room_id,
                "actor_principal_id": snapshot.actor_principal_id,
                "owner_instance_id": snapshot.owner.instance_id,
                "owner_profile_id": snapshot.owner.profile_id,
                "session_fingerprint": snapshot.session_fingerprint,
                "session_generation": snapshot.session_generation,
                "resume_parent_session_id": (
                    snapshot.resume_parent_session_id
                ),
                "state": snapshot.state.value,
                "heartbeat_expires_at": snapshot.heartbeat_expires_at,
                "created_at": snapshot.created_at,
                "updated_at": snapshot.updated_at,
            },
        )

    def update_room_session_heartbeat(
        self,
        current: RoomSessionSnapshot,
        heartbeat_expires_at: int,
        updated_at: int,
    ) -> bool:
        cursor = self._db().execute(
            """
            UPDATE room_participation_sessions
            SET heartbeat_expires_at = :heartbeat_expires_at,
                updated_at = :updated_at
            WHERE session_id = :session_id
              AND workspace_scope_id = :workspace_scope_id
              AND room_id = :room_id
              AND actor_principal_id = :actor_principal_id
              AND owner_instance_id = :owner_instance_id
              AND owner_profile_id = :owner_profile_id
              AND session_generation = :session_generation
              AND state = 'ACTIVE'
              AND heartbeat_expires_at = :expected_heartbeat_expires_at
              AND heartbeat_expires_at >= :updated_at
            """,
            {
                "session_id": current.session_id,
                "workspace_scope_id": current.workspace_scope_id,
                "room_id": current.room_id,
                "actor_principal_id": current.actor_principal_id,
                "owner_instance_id": current.owner.instance_id,
                "owner_profile_id": current.owner.profile_id,
                "session_generation": current.session_generation,
                "expected_heartbeat_expires_at": (
                    current.heartbeat_expires_at
                ),
                "heartbeat_expires_at": heartbeat_expires_at,
                "updated_at": updated_at,
            },
        )
        return cursor.rowcount == 1

    def transition_room_session(
        self,
        current: RoomSessionSnapshot,
        state: RoomSessionState,
        updated_at: int,
        *,
        allow_expired: bool = False,
    ) -> bool:
        cursor = self._db().execute(
            """
            UPDATE room_participation_sessions
            SET state = :state,
                updated_at = :updated_at
            WHERE session_id = :session_id
              AND workspace_scope_id = :workspace_scope_id
              AND room_id = :room_id
              AND actor_principal_id = :actor_principal_id
              AND owner_instance_id = :owner_instance_id
              AND owner_profile_id = :owner_profile_id
              AND session_generation = :session_generation
              AND state = 'ACTIVE'
              AND heartbeat_expires_at = :expected_heartbeat_expires_at
              AND (
                  :allow_expired = 1
                  OR heartbeat_expires_at >= :updated_at
              )
            """,
            {
                "session_id": current.session_id,
                "workspace_scope_id": current.workspace_scope_id,
                "room_id": current.room_id,
                "actor_principal_id": current.actor_principal_id,
                "owner_instance_id": current.owner.instance_id,
                "owner_profile_id": current.owner.profile_id,
                "session_generation": current.session_generation,
                "expected_heartbeat_expires_at": (
                    current.heartbeat_expires_at
                ),
                "state": state.value,
                "updated_at": updated_at,
                "allow_expired": int(allow_expired),
            },
        )
        return cursor.rowcount == 1

    def insert_room_session_event(self, event: RoomSessionEvent) -> None:
        self._db().execute(
            """
            INSERT INTO room_session_events (
                event_id,
                session_id,
                event_type,
                at,
                actor_principal_id
            ) VALUES (
                :event_id,
                :session_id,
                :event_type,
                :at,
                :actor_principal_id
            )
            """,
            {
                "event_id": event.event_id,
                "session_id": event.session_id,
                "event_type": event.event_type.value,
                "at": event.at,
                "actor_principal_id": event.actor_principal_id,
            },
        )

    def get_client_request_binding(
        self,
        client_id: str,
        client_request_id: str,
    ) -> ClientRequestBinding | None:
        """Return a caller-request identity binding."""

        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
            client_id=row["client_id"],  # pyright: ignore[reportUnknownArgumentType]
            client_request_id=row["client_request_id"],  # pyright: ignore[reportUnknownArgumentType]
            payload_digest=row["payload_digest"],  # pyright: ignore[reportUnknownArgumentType]
            command_id=CommandID(row["command_id"]),  # pyright: ignore[reportUnknownArgumentType]
            admission_receipt_id=row[  # pyright: ignore[reportUnknownArgumentType]
                "admission_receipt_id"
            ],
            created_at=row["created_at"],  # pyright: ignore[reportUnknownArgumentType]
        )

    def add_client_request_binding(
        self,
        binding: ClientRequestBinding,
    ) -> None:
        """Insert an immutable caller-request identity."""

        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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

        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
            client_id=row["client_id"],  # pyright: ignore[reportUnknownArgumentType]
            command_type=row["command_type"],  # pyright: ignore[reportUnknownArgumentType]
            idempotency_key=row["idempotency_key"],  # pyright: ignore[reportUnknownArgumentType]
            payload_digest=row["payload_digest"],  # pyright: ignore[reportUnknownArgumentType]
            command_id=CommandID(row["command_id"]),  # pyright: ignore[reportUnknownArgumentType]
            admission_receipt_id=row[  # pyright: ignore[reportUnknownArgumentType]
                "admission_receipt_id"
            ],
            created_at=row["created_at"],  # pyright: ignore[reportUnknownArgumentType]
        )

    def add_command_idempotency_binding(
        self,
        binding: CommandIdempotencyBinding,
    ) -> None:
        """Insert an immutable Slice 3 idempotency binding."""

        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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

        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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

        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
            admission_receipt_id=row["admission_receipt_id"],  # pyright: ignore[reportUnknownArgumentType]
            command_id=CommandID(row["command_id"]),  # pyright: ignore[reportUnknownArgumentType]
            client_id=row["client_id"],  # pyright: ignore[reportUnknownArgumentType]
            client_request_id=row["client_request_id"],  # pyright: ignore[reportUnknownArgumentType]
            command_type=row["command_type"],  # pyright: ignore[reportUnknownArgumentType]
            idempotency_key=row["idempotency_key"],  # pyright: ignore[reportUnknownArgumentType]
            payload_digest=row["payload_digest"],  # pyright: ignore[reportUnknownArgumentType]
            completion_contract_id=row[  # pyright: ignore[reportUnknownArgumentType]
                "completion_contract_id"
            ],
            lease_id=row["lease_id"],  # pyright: ignore[reportUnknownArgumentType]
            policy_revision=_stored_revision(
                row["policy_revision_json"]  # pyright: ignore[reportUnknownArgumentType]
            ),
            configuration_revision=_stored_revision(
                row["configuration_revision_json"]  # pyright: ignore[reportUnknownArgumentType]
            ),
            admitted_at=row["admitted_at"],  # pyright: ignore[reportUnknownArgumentType]
        )

    def add_capability_lease(
        self,
        lease: CapabilityLease,
    ) -> None:
        """Insert an immutable capability lease."""

        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
            """
            INSERT INTO capability_leases (
                capability_lease_id,
                command_id,
                admission_receipt_id,
                session_lease_id,
                subject_principal_id,
                selected_peer_kind,
                required_tier,
                authorized_tier,
                minimum_enforcement,
                selected_peer_instance_id,
                selected_profile_id,
                route_decision_digest,
                policy_revision_json,
                issuer_id,
                issued_at,
                expires_at,
                authorized_attempt_number,
                previous_attempt_id
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                lease.capability_lease_id,
                str(lease.command_id),
                lease.admission_receipt_id,
                lease.session_lease_id,
                lease.subject_principal_id,
                lease.selected_peer_kind,
                lease.required_tier.name,
                lease.authorized_tier.name,
                lease.minimum_enforcement.name,
                lease.selected_peer_instance_id,
                lease.selected_profile_id,
                lease.route_decision_digest,
                _json_text(lease.policy_revision),
                lease.issuer_id,
                lease.issued_at,
                lease.expires_at,
                lease.authorized_attempt_number,
                lease.previous_attempt_id,
            ),
        )

    def get_capability_lease(
        self,
        capability_lease_id: str,
    ) -> CapabilityLease | None:
        """Return one capability lease by ID."""

        return self._get_capability_lease(
            "capability_lease_id",
            capability_lease_id,
        )

    def get_capability_lease_by_admission_receipt_id(
        self,
        admission_receipt_id: str,
    ) -> CapabilityLease | None:
        """Return the lease uniquely bound to an admission receipt."""

        return self._get_capability_lease(
            "admission_receipt_id",
            admission_receipt_id,
        )

    def _get_capability_lease(
        self,
        column: str,
        value: str,
    ) -> CapabilityLease | None:
        if column not in {
            "capability_lease_id",
            "admission_receipt_id",
            "session_lease_id",
        }:
            raise ValueError("unsupported capability lease lookup")
        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            f"""
            SELECT *
            FROM capability_leases
            WHERE {column} = ?
            """,
            (value,),
        ).fetchone()
        if row is None:
            return None
        return CapabilityLease(
            capability_lease_id=row["capability_lease_id"],  # pyright: ignore[reportUnknownArgumentType]
            command_id=CommandID(row["command_id"]),  # pyright: ignore[reportUnknownArgumentType]
            admission_receipt_id=row["admission_receipt_id"],  # pyright: ignore[reportUnknownArgumentType]
            session_lease_id=row["session_lease_id"],  # pyright: ignore[reportUnknownArgumentType]
            subject_principal_id=row["subject_principal_id"],  # pyright: ignore[reportUnknownArgumentType]
            selected_peer_kind=row["selected_peer_kind"],  # pyright: ignore[reportUnknownArgumentType]
            required_tier=CapabilityTier[row["required_tier"]],  # pyright: ignore[reportUnknownArgumentType]
            authorized_tier=CapabilityTier[row["authorized_tier"]],  # pyright: ignore[reportUnknownArgumentType]
            minimum_enforcement=EnforcementLevel[  # pyright: ignore[reportUnknownArgumentType]
                row["minimum_enforcement"]
            ],
            selected_peer_instance_id=row["selected_peer_instance_id"],  # pyright: ignore[reportUnknownArgumentType]
            selected_profile_id=row["selected_profile_id"],  # pyright: ignore[reportUnknownArgumentType]
            route_decision_digest=row["route_decision_digest"],  # pyright: ignore[reportUnknownArgumentType]
            policy_revision=_stored_revision(
                row["policy_revision_json"]  # pyright: ignore[reportUnknownArgumentType]
            ),
            issuer_id=row["issuer_id"],  # pyright: ignore[reportUnknownArgumentType]
            issued_at=row["issued_at"],  # pyright: ignore[reportUnknownArgumentType]
            expires_at=row["expires_at"],  # pyright: ignore[reportUnknownArgumentType]
            authorized_attempt_number=row["authorized_attempt_number"],  # pyright: ignore[reportUnknownArgumentType]
            previous_attempt_id=row["previous_attempt_id"],  # pyright: ignore[reportUnknownArgumentType]
        )

    def get_capability_lease_by_session_lease_id(
        self,
        session_lease_id: str,
    ) -> CapabilityLease | None:
        """Return capability lease by session lease id."""

        return self._get_capability_lease(
            "session_lease_id",
            session_lease_id,
        )

    def get_capability_lease_for_attempt(
        self,
        command_id: CommandID | str,
        authorized_attempt_number: int,
    ) -> CapabilityLease | None:
        """Return capability lease by attempt number."""

        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            SELECT *
            FROM capability_leases
            WHERE command_id = ? AND authorized_attempt_number = ?
            """,
            (str(command_id), authorized_attempt_number),
        ).fetchone()
        if row is None:
            return None
        return CapabilityLease(
            capability_lease_id=row["capability_lease_id"],  # pyright: ignore[reportUnknownArgumentType]
            command_id=CommandID(row["command_id"]),  # pyright: ignore[reportUnknownArgumentType]
            admission_receipt_id=row["admission_receipt_id"],  # pyright: ignore[reportUnknownArgumentType]
            session_lease_id=row["session_lease_id"],  # pyright: ignore[reportUnknownArgumentType]
            subject_principal_id=row["subject_principal_id"],  # pyright: ignore[reportUnknownArgumentType]
            selected_peer_kind=row["selected_peer_kind"],  # pyright: ignore[reportUnknownArgumentType]
            required_tier=CapabilityTier[row["required_tier"]],  # pyright: ignore[reportUnknownArgumentType]
            authorized_tier=CapabilityTier[row["authorized_tier"]],  # pyright: ignore[reportUnknownArgumentType]
            minimum_enforcement=EnforcementLevel[  # pyright: ignore[reportUnknownArgumentType]
                row["minimum_enforcement"]
            ],
            selected_peer_instance_id=row["selected_peer_instance_id"],  # pyright: ignore[reportUnknownArgumentType]
            selected_profile_id=row["selected_profile_id"],  # pyright: ignore[reportUnknownArgumentType]
            route_decision_digest=row["route_decision_digest"],  # pyright: ignore[reportUnknownArgumentType]
            policy_revision=_stored_revision(
                row["policy_revision_json"]  # pyright: ignore[reportUnknownArgumentType]
            ),
            issuer_id=row["issuer_id"],  # pyright: ignore[reportUnknownArgumentType]
            issued_at=row["issued_at"],  # pyright: ignore[reportUnknownArgumentType]
            expires_at=row["expires_at"],  # pyright: ignore[reportUnknownArgumentType]
            authorized_attempt_number=row["authorized_attempt_number"],  # pyright: ignore[reportUnknownArgumentType]
            previous_attempt_id=row["previous_attempt_id"],  # pyright: ignore[reportUnknownArgumentType]
        )

    def get_retry_policy_max_attempts(
        self,
        command_id: CommandID | str,
    ) -> int | None:
        """Return the maximum attempts for a command."""

        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            SELECT max_attempts
            FROM retry_policies
            WHERE command_id = ?
            """,
            (str(command_id),),
        ).fetchone()
        if row is None:
            return None
        return int(row["max_attempts"])  # pyright: ignore[reportUnknownArgumentType]

    def add_retry_policy(
        self,
        command_id: CommandID | str,
        max_attempts: int,
    ) -> None:
        """Insert a retry policy."""

        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
            """
            INSERT INTO retry_policies (command_id, max_attempts)
            VALUES (?, ?)
            """,
            (str(command_id), max_attempts),
        )

    def add_request(self, request: RequestSnapshot) -> None:
        """Insert an admitted request snapshot."""

        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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
                required_capability_tier,
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
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            self._request_values(request),
        )

    def get_request(
        self,
        command_id: CommandID | str,
    ) -> RequestSnapshot | None:
        """Return a request snapshot by server command ID."""

        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            SELECT *
            FROM dispatch_requests
            WHERE command_id = ?
            """,
            (str(command_id),),
        ).fetchone()
        return None if row is None else self._request_from_row(row)  # pyright: ignore[reportUnknownArgumentType]

    def cas_update_request(
        self,
        current: RequestSnapshot,
        updated: RequestSnapshot,
    ) -> bool:
        """CAS-update a request by command ID and revision."""

        if current.command_id != updated.command_id:
            raise ValueError("request command IDs do not match")
        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            UPDATE dispatch_requests
            SET
                lease_id = ?,
                configuration_revision_json = ?,
                selected_peer_instance_id = ?,
                selected_profile_id = ?,
                route_decision_digest = ?,
                state = ?,
                revision = ?,
                updated_at = ?,
                terminal_error_code = ?
            WHERE command_id = ? AND revision = ?
            """,
            (
                updated.lease_id,
                _json_text(updated.configuration_revision),
                updated.selected_peer_instance_id,
                updated.selected_profile_id,
                updated.route_decision_digest,
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
        return cursor.rowcount == 1  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    def next_attempt_number(
        self,
        command_id: CommandID | str,
    ) -> int:
        """Return the next monotonic attempt number in this transaction."""

        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
        return int(row["next_number"])  # pyright: ignore[reportUnknownArgumentType]

    def add_attempt(self, attempt: AttemptSnapshot) -> None:
        """Insert a revision-one dispatch attempt."""

        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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

        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            SELECT *
            FROM dispatch_attempts
            WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()
        return None if row is None else self._attempt_from_row(row)  # pyright: ignore[reportUnknownArgumentType]

    def list_attempts(
        self,
        command_id: CommandID | str,
    ) -> tuple[AttemptSnapshot, ...]:
        """Return command attempts in monotonic attempt order."""

        rows = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            SELECT *
            FROM dispatch_attempts
            WHERE command_id = ?
            ORDER BY attempt_number
            """,
            (str(command_id),),
        ).fetchall()
        return tuple(self._attempt_from_row(row) for row in rows)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]

    def cas_update_attempt(
        self,
        current: AttemptSnapshot,
        updated: AttemptSnapshot,
    ) -> bool:
        """CAS-update an attempt by ID and revision."""

        if current.attempt_id != updated.attempt_id:
            raise ValueError("attempt IDs do not match")
        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
        return cursor.rowcount == 1  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    def allocate_fencing_token(self) -> int:
        """Allocate one database-monotonic lease fencing token."""

        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            "INSERT INTO lease_fencing_sequence DEFAULT VALUES"
        )
        token = cursor.lastrowid  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if token is None:
            raise RuntimeError(
                "failed to allocate lease fencing token"
            )
        return int(token)  # pyright: ignore[reportUnknownArgumentType]

    def get_lease(self, lease_id: str) -> LeaseSnapshot | None:
        """Return a lease snapshot by ID."""

        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
                pid=row["owner_process_pid"],  # pyright: ignore[reportUnknownArgumentType]
                process_creation_time=(  # pyright: ignore[reportUnknownArgumentType]
                    row["owner_process_creation_time"]
                ),
            )

        fence = LeaseFenceTuple(
            session_id=row["session_id"],  # pyright: ignore[reportUnknownArgumentType]
            lease_id=row["lease_id"],  # pyright: ignore[reportUnknownArgumentType]
            fencing_token=row["fencing_token"],  # pyright: ignore[reportUnknownArgumentType]
            revision=row["revision"],  # pyright: ignore[reportUnknownArgumentType]
            owner_principal_id=row["owner_principal_id"],  # pyright: ignore[reportUnknownArgumentType]
            owner_instance_id=row["owner_instance_id"],  # pyright: ignore[reportUnknownArgumentType]
            owner_process_birth_identity=process_identity,
            command_id=CommandID(row["command_id"]),  # pyright: ignore[reportUnknownArgumentType]
            authority_epoch=row["authority_epoch"],  # pyright: ignore[reportUnknownArgumentType]
            attempt_id=row["attempt_id"],  # pyright: ignore[reportUnknownArgumentType]
            owner_peer_id=row["owner_peer_id"],  # pyright: ignore[reportUnknownArgumentType]
        )
        return LeaseSnapshot(
            lease_id=row["lease_id"],  # pyright: ignore[reportUnknownArgumentType]
            session_id=row["session_id"],  # pyright: ignore[reportUnknownArgumentType]
            fence=fence,
            state=LeaseState(row["state"]),
            heartbeat_expires_at=row["heartbeat_expires_at"],  # pyright: ignore[reportUnknownArgumentType]
            created_at=row["created_at"],  # pyright: ignore[reportUnknownArgumentType]
            updated_at=row["updated_at"],  # pyright: ignore[reportUnknownArgumentType]
        )

    def add_lease(self, lease: LeaseSnapshot) -> None:
        """Insert a new lease snapshot."""

        process = lease.fence.owner_process_birth_identity
        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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

    def count_active_leases(self, now_ms: int | None = None) -> int:
        """Return the number of active leases."""

        query = """
            SELECT COUNT(*) AS active_count
            FROM leases
            WHERE state IN ('RESERVED', 'ACTIVE', 'RENEWED')
        """
        params: tuple[object, ...] = ()

        if now_ms is not None:
            query += " AND heartbeat_expires_at >= ?"
            params = (now_ms,)

        row = self._db().execute(query, params).fetchone()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return int(row["active_count"]) if row else 0  # pyright: ignore[reportUnknownArgumentType]

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

        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
        return cursor.rowcount == 1  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

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

        connection = self._db()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        connection.execute("SAVEPOINT dispatch_bundle")  # pyright: ignore[reportUnknownMemberType]
        try:
            if not self.cas_update_lease(
                current_lease,
                updated_lease,
            ):
                connection.execute(  # pyright: ignore[reportUnknownMemberType]
                    "ROLLBACK TO dispatch_bundle"
                )
                connection.execute("RELEASE dispatch_bundle")  # pyright: ignore[reportUnknownMemberType]
                return False
            if not self.cas_update_attempt(
                current_attempt,
                updated_attempt,
            ):
                connection.execute(  # pyright: ignore[reportUnknownMemberType]
                    "ROLLBACK TO dispatch_bundle"
                )
                connection.execute("RELEASE dispatch_bundle")  # pyright: ignore[reportUnknownMemberType]
                return False
            if not self.cas_update_request(
                current_request,
                updated_request,
            ):
                connection.execute(  # pyright: ignore[reportUnknownMemberType]
                    "ROLLBACK TO dispatch_bundle"
                )
                connection.execute("RELEASE dispatch_bundle")  # pyright: ignore[reportUnknownMemberType]
                return False
            connection.execute("RELEASE dispatch_bundle")  # pyright: ignore[reportUnknownMemberType]
            return True
        except BaseException:
            connection.execute("ROLLBACK TO dispatch_bundle")  # pyright: ignore[reportUnknownMemberType]
            connection.execute("RELEASE dispatch_bundle")  # pyright: ignore[reportUnknownMemberType]
            raise

    def get_session_binding(
        self,
        key: SessionBindingKey,
    ) -> SessionBindingSnapshot | None:
        """Return a session binding snapshot by canonical key."""

        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
            session_id=row["session_id"],  # pyright: ignore[reportUnknownArgumentType]
            current_lease_id=row["current_lease_id"],  # pyright: ignore[reportUnknownArgumentType]
            adapter_fingerprint=row["adapter_fingerprint"],  # pyright: ignore[reportUnknownArgumentType]
            readiness_binding=row["readiness_binding"],  # pyright: ignore[reportUnknownArgumentType]
            session_generation=row["session_generation"],  # pyright: ignore[reportUnknownArgumentType]
            revision=row["revision"],  # pyright: ignore[reportUnknownArgumentType]
            state=SessionBindingState(row["state"]),
            updated_at=row["updated_at"],  # pyright: ignore[reportUnknownArgumentType]
        )

    def add_session_binding(
        self,
        binding: SessionBindingSnapshot,
    ) -> None:
        """Insert a new session binding."""

        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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

        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
        return cursor.rowcount == 1  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    def add_recovery_receipt(
        self,
        receipt: RecoveryReceipt,
    ) -> None:
        """Insert an immutable recovery receipt."""

        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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

        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            SELECT *
            FROM recovery_receipts
            WHERE recovery_receipt_id = ?
            """,
            (receipt_id,),
        ).fetchone()
        if row is None:
            return None
        raw_effect_certainty = row[  # pyright: ignore[reportUnknownVariableType]
            "external_effect_certainty"
        ]
        effect_certainty = (
            ExecutionCertainty(raw_effect_certainty)
            if raw_effect_certainty
            else None
        )
        return RecoveryReceipt(
            recovery_receipt_id=row["recovery_receipt_id"],  # pyright: ignore[reportUnknownArgumentType]
            session_id=row["session_id"],  # pyright: ignore[reportUnknownArgumentType]
            lease_id=row["lease_id"],  # pyright: ignore[reportUnknownArgumentType]
            detected_at=row["detected_at"],  # pyright: ignore[reportUnknownArgumentType]
            recovery_actor_principal_id=row[  # pyright: ignore[reportUnknownArgumentType]
                "recovery_actor_principal_id"
            ],
            trigger=RecoveryTrigger(row["trigger"]),
            mismatch_dimensions=_string_tuple(
                row["mismatch_dimensions_json"]  # pyright: ignore[reportUnknownArgumentType]
            ),
            evidence_digest=row["evidence_digest"],  # pyright: ignore[reportUnknownArgumentType]
            policy_id=row["policy_id"],  # pyright: ignore[reportUnknownArgumentType]
            policy_revision=row["policy_revision"],  # pyright: ignore[reportUnknownArgumentType]
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
            pre_revision=row["pre_revision"],  # pyright: ignore[reportUnknownArgumentType]
            pre_fencing_token=row["pre_fencing_token"],  # pyright: ignore[reportUnknownArgumentType]
            post_lifecycle_state=LeaseState(
                row["post_lifecycle_state"]
            ),
            post_revision=row["post_revision"],  # pyright: ignore[reportUnknownArgumentType]
            post_fencing_token=row["post_fencing_token"],  # pyright: ignore[reportUnknownArgumentType]
        )

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
            request.required_capability_tier.name,
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
            required_capability_tier=(
                _required_capability_tier_from_stored(
                    row["required_capability_tier"]
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
        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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
            self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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
        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            SELECT * FROM dispatch_artifact_manifests WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()
        return None if row is None else self._artifact_manifest_from_row(row)  # pyright: ignore[reportUnknownArgumentType]

    def get_artifact_metadata(
        self, attempt_id: str, artifact_id: str
    ) -> ArtifactMetadata | None:
        """Return artifact metadata by attempt ID and artifact ID."""
        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            SELECT * FROM dispatch_artifacts
            WHERE attempt_id = ? AND artifact_id = ?
            """,
            (attempt_id, artifact_id),
        ).fetchone()
        return None if row is None else self._artifact_metadata_from_row(row)  # pyright: ignore[reportUnknownArgumentType]

    def list_artifact_metadata(
        self, attempt_id: str
    ) -> tuple[ArtifactMetadata, ...]:
        """List all artifact metadata rows for an attempt."""
        rows = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            SELECT * FROM dispatch_artifacts
            WHERE attempt_id = ?
            ORDER BY artifact_id
            """,
            (attempt_id,),
        ).fetchall()
        return tuple(self._artifact_metadata_from_row(row) for row in rows)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]

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
        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
        return cursor.rowcount == 1  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

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
        manifest_row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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

        item_count = manifest_row["item_count"]  # pyright: ignore[reportUnknownVariableType]
        art_rows = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            SELECT state FROM dispatch_artifacts WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchall()

        if len(art_rows) != item_count or any(  # pyright: ignore[reportUnknownArgumentType]
            row["state"] != ArtifactState.VERIFIED.value for row in art_rows  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
        ):
            return False

        # All items are VERIFIED and match count -- perform reservation
        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
        if cursor.rowcount != item_count:  # pyright: ignore[reportUnknownMemberType]
            return False

        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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
        manifest_row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            SELECT item_count FROM dispatch_artifact_manifests WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()

        if manifest_row is None:
            return False

        art_rows = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            SELECT state FROM dispatch_artifacts WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchall()

        if not art_rows or any(
            row["state"] != ArtifactState.RESERVED.value for row in art_rows  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
        ):
            return False

        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
        if cursor.rowcount != len(art_rows):  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
            return False

        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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
            outbox_row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                """
                SELECT event_kind, payload_json FROM event_log WHERE event_id = ?
                """,
                (manifest.intent_event_id,),
            ).fetchone()
            if outbox_row is not None:
                kind = outbox_row["event_kind"]  # pyright: ignore[reportUnknownVariableType]
                payload = _json_object(outbox_row["payload_json"])  # pyright: ignore[reportUnknownArgumentType]
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
        manifest_row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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

        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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

        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
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

        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
        return cursor.rowcount == 1  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

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
        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            UPDATE dispatch_artifacts
            SET state = ?,
                staging_ref = ?,
                staged_at = ?,
                revision = revision + 1
            WHERE attempt_id = ?
              AND artifact_id = ?
              AND revision = ?
              AND state = ?
            """,
            (
                ArtifactState.STAGED.value,
                staging_path_relative,
                staged_at,
                attempt_id,
                artifact_id,
                expected_revision,
                ArtifactState.DECLARED.value,
            ),
        )
        return cursor.rowcount == 1  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

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
        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            UPDATE dispatch_artifacts
            SET state = ?,
                verified_sha256_hex = ?,
                verified_length = ?,
                staging_ref = ?,
                verified_at = ?,
                revision = revision + 1
            WHERE attempt_id = ?
              AND artifact_id = ?
              AND revision = ?
              AND state = ?
            """,
            (
                ArtifactState.VERIFIED.value,
                verified_digest,
                verified_length,
                target_path_relative,
                verified_at,
                attempt_id,
                artifact_id,
                expected_revision,
                ArtifactState.STAGED.value,
            ),
        )
        return cursor.rowcount == 1  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

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
        if current.state != ArtifactState.ORPHANED:
            return False

        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
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
                ArtifactState.ORPHANED.value,
            ),
        )
        return cursor.rowcount == 1  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    def get_max_rotation_generation(
        self,
        workspace_scope_id: str,
        instance_id: str,
        profile_id: str,
        conversation_scope: str,
    ) -> SessionRotationGenerationSnapshot | None:
        """Return the current max generation for a session rotation key."""
        row = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            SELECT * FROM session_binding_generations
            WHERE workspace_scope_id = ? AND instance_id = ? AND profile_id = ? AND conversation_scope = ?
            ORDER BY generation_id DESC LIMIT 1
            """,
            (workspace_scope_id, instance_id, profile_id, conversation_scope),
        ).fetchone()
        if row is None:
            return None
        return self._session_rotation_from_row(row)  # pyright: ignore[reportUnknownArgumentType]

    @staticmethod
    def _session_rotation_from_row(row: sqlite3.Row) -> SessionRotationGenerationSnapshot:
        return SessionRotationGenerationSnapshot(
            key=SessionRotationKey(
                workspace_scope_id=row["workspace_scope_id"],
                instance_id=row["instance_id"],
                profile_id=row["profile_id"],
                conversation_scope=row["conversation_scope"],
                generation_id=row["generation_id"],
            ),
            conversation_id=row["conversation_id"],
            state=SessionRotationState(row["state"]),
            claim_token=row["claim_token"],
            claim_expiry=row["claim_expiry"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def insert_rotation_generation(
        self,
        snapshot: SessionRotationGenerationSnapshot,
    ) -> None:
        """Insert a new session rotation generation."""
        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
            """
            INSERT INTO session_binding_generations (
                workspace_scope_id,
                instance_id,
                profile_id,
                conversation_scope,
                generation_id,
                conversation_id,
                state,
                claim_token,
                claim_expiry,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.key.workspace_scope_id,
                snapshot.key.instance_id,
                snapshot.key.profile_id,
                snapshot.key.conversation_scope,
                snapshot.key.generation_id,
                snapshot.conversation_id,
                snapshot.state.value,
                snapshot.claim_token,
                snapshot.claim_expiry,
                snapshot.created_at,
                snapshot.updated_at,
            ),
        )

    def claim_rotation(
        self,
        *,
        workspace_scope_id: str,
        instance_id: str,
        profile_id: str,
        conversation_scope: str,
        expected_generation_id: int,
        claim_token: str,
        claim_expiry: int,
        updated_at: int,
    ) -> bool:
        """CAS the current ACTIVE generation to DRAINING with a claim_token+claim_expiry."""
        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            UPDATE session_binding_generations
            SET state = ?, claim_token = ?, claim_expiry = ?, updated_at = ?
            WHERE workspace_scope_id = ? AND instance_id = ? AND profile_id = ? AND conversation_scope = ?
              AND generation_id = ? AND state = ?
            """,
            (
                SessionRotationState.DRAINING.value,
                claim_token,
                claim_expiry,
                updated_at,
                workspace_scope_id,
                instance_id,
                profile_id,
                conversation_scope,
                expected_generation_id,
                SessionRotationState.ACTIVE.value,
            )
        )
        return cursor.rowcount == 1  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    def commit_rotation(
        self,
        *,
        workspace_scope_id: str,
        instance_id: str,
        profile_id: str,
        conversation_scope: str,
        expected_generation_id: int,
        claim_token: str,
        new_conversation_id: str,
        updated_at: int,
    ) -> bool:
        """Commit rotation: insert generation+1 as ACTIVE and update DRAINING to RETIRED."""
        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            UPDATE session_binding_generations
            SET state = ?, updated_at = ?
            WHERE workspace_scope_id = ? AND instance_id = ? AND profile_id = ? AND conversation_scope = ?
              AND generation_id = ? AND state = ? AND claim_token = ?
            """,
            (
                SessionRotationState.RETIRED.value,
                updated_at,
                workspace_scope_id,
                instance_id,
                profile_id,
                conversation_scope,
                expected_generation_id,
                SessionRotationState.DRAINING.value,
                claim_token,
            )
        )
        if cursor.rowcount != 1:  # pyright: ignore[reportUnknownMemberType]
            return False
            
        self._db().execute(  # pyright: ignore[reportUnknownMemberType]
            """
            INSERT INTO session_binding_generations (
                workspace_scope_id,
                instance_id,
                profile_id,
                conversation_scope,
                generation_id,
                conversation_id,
                state,
                claim_token,
                claim_expiry,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_scope_id,
                instance_id,
                profile_id,
                conversation_scope,
                expected_generation_id + 1,
                new_conversation_id,
                SessionRotationState.ACTIVE.value,
                None,
                None,
                updated_at,
                updated_at,
            ),
        )
        return True

    def sweep_expired_rotation_claims(
        self,
        *,
        current_time: int,
    ) -> int:
        """Revert DRAINING rows with expired claim_expiry to ACTIVE."""
        cursor = self._db().execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            """
            UPDATE session_binding_generations
            SET state = ?, claim_token = NULL, claim_expiry = NULL, updated_at = ?
            WHERE state = ? AND claim_expiry <= ?
            """,
            (
                SessionRotationState.ACTIVE.value,
                current_time,
                SessionRotationState.DRAINING.value,
                current_time,
            )
        )
        return cursor.rowcount  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
