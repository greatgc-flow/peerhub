"""Public API registry, validation boundary, and command submission."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar, Generic

from peerhub.core.errors import PeerHubError
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.ports import RequestContext
from peerhub.core.protocol import (
    CommandEnvelope,
    CommandFailure,
    CommandOutcome,
    CommandSuccess,
    ErrorCode,
    ErrorDetail,
    ErrorPhase,
    IdempotencyDisposition,
    JsonValue,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    RetryDisposition,
)
from peerhub.dispatch.service import DispatchService
from peerhub.application.workflows import ApplicationWorkflows
from peerhub.application.alert_raise import AlertRaiseCoordinator
from peerhub.application.arbiter_review import ArbiterReviewCoordinator
from peerhub.application.proposals import (
    ProposalCoordinator,
)
from peerhub.application.commands import (
    Command,
    SubmissionMetadata,
)
from peerhub.application.handlers.dispatch_builtins import (
    AdmissionInputs as AdmissionInputs,
    AdmissionInputsProvider as AdmissionInputsProvider,
    AdmitDispatchPayload as AdmitDispatchPayload,
    CompletionContractPayload as CompletionContractPayload,
    GetDispatchLeasePayload as GetDispatchLeasePayload,
    GetDispatchRequestPayload as GetDispatchRequestPayload,
    ResourceOwnershipError,
    reconstruct_envelope as reconstruct_envelope,
    validate_wire_json_value,
)
from peerhub.governance.consensus import ConsensusService
from peerhub.governance.tasks import TaskService
from peerhub.governance.lessons import LessonService
from peerhub.governance.rooms import RoomsService
from peerhub.governance.broker import GovernanceBroker
from peerhub.dispatch.duty_lease import DutyLeaseCoordinator
from peerhub.dispatch.room_session import RoomParticipationCoordinator
from peerhub.dispatch.terminal_duty import TerminalDutyService
from peerhub.application.process_lease_sweep import ProcessLeaseSweepCoordinator
from peerhub.application.peer_registry import PeerRegistryService
from peerhub.application.health_revalidation import HealthRevalidationCoordinator
from peerhub.application.role_assignment import RoleAssignmentService
from peerhub.application.leadership import LeadershipService
from peerhub.application.capability_matching import CapabilityMatchingCoordinator
from peerhub.application.quarantine_review import QuarantineReviewCoordinator
from peerhub.governance.feedback import FeedbackService
from peerhub.governance.operational_errors import OperationalErrorService
from peerhub.governance.file_locks import FileLockService
from peerhub.governance.artifact_records import ArtifactRecordService
from peerhub.health.service import HealthService
from peerhub.application.governance_authorizer import GovernanceAuthorizer


C = TypeVar("C", bound=Command[Any])  # pyright: ignore[reportUnknownVariableType]
R = TypeVar("R")  # pyright: ignore[reportUnknownVariableType]

_validate_wire_json_value = validate_wire_json_value


class CommandAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    NOT_BACKED = "NOT_BACKED"


class IdempotencyPolicy(str, Enum):
    READ_ONLY = "READ_ONLY"
    DOMAIN_ATOMIC_REQUIRED = "DOMAIN_ATOMIC_REQUIRED"


class Mutability(str, Enum):
    MUTATING = "MUTATING"
    READ_ONLY = "READ_ONLY"


class ScopeKind(str, Enum):
    WORKSPACE = "WORKSPACE"
    SYSTEM = "SYSTEM"
    ANY = "ANY"


_ResourceOwnershipError = ResourceOwnershipError


@dataclass(frozen=True)
class CommandDescriptor(Generic[C, R]):  # pyright: ignore[reportUntypedBaseClass]
    method: str
    mutability: Mutability | Callable[[CommandEnvelope], Mutability]
    accepted_scope: ScopeKind
    idempotency: (
        IdempotencyPolicy
        | Callable[[CommandEnvelope], IdempotencyPolicy]
    )
    decode: Callable[[CommandEnvelope], C]  # pyright: ignore[reportInvalidTypeForm]
    handle: Callable[[C, RequestContext], R]  # pyright: ignore[reportInvalidTypeForm]
    encode_result: Callable[[R], Mapping[str, JsonValue]]  # pyright: ignore[reportInvalidTypeForm]
    availability: CommandAvailability
    unavailable_reason: str | None = None


class ApplicationAPI:
    def __init__(
        self,
        *,
        workflows: ApplicationWorkflows,
        dispatch: DispatchService,
        admission_provider: AdmissionInputsProvider | None = None,
        consensus: ConsensusService | None = None,
        proposals: ProposalCoordinator | None = None,
        task: TaskService | None = None, lesson: LessonService | None = None,
        lesson_broker: GovernanceBroker | None = None,
        room: RoomsService | None = None,
        duty: DutyLeaseCoordinator | None = None,
        terminal_duty: TerminalDutyService | None = None,
        room_session: RoomParticipationCoordinator | None = None,
        arbiter: ArbiterReviewCoordinator | None = None,
        peer_registry: PeerRegistryService | None = None,
        health: HealthService | None = None,
        role_assignment: RoleAssignmentService | None = None,
        leadership: LeadershipService | None = None,
        capability_matching: CapabilityMatchingCoordinator | None = None,
        feedback: FeedbackService | None = None,
        file_locks: FileLockService | None = None,
        artifact_records: ArtifactRecordService | None = None,
        operational_errors: OperationalErrorService | None = None,
        quarantine_reviews: QuarantineReviewCoordinator | None = None,
        alert_raise: AlertRaiseCoordinator | None = None,
        health_revalidation: HealthRevalidationCoordinator | None = None,
        process_lease_sweep: ProcessLeaseSweepCoordinator | None = None,
        governance_broker: GovernanceBroker | None = None,
        authorizer: GovernanceAuthorizer | None = None,
    ) -> None:
        self._workflows = workflows
        self._dispatch = dispatch
        self._admission_provider = admission_provider
        self._consensus = consensus
        self._authorizer = authorizer if authorizer is not None else GovernanceAuthorizer(verifier=None)
        self._registry: dict[str, CommandDescriptor[Any, Any]] = {}  # pyright: ignore[reportInvalidTypeArguments]

        self._register_builtins()
        if governance_broker is not None:
            self._register_effect_status(governance_broker)
        if consensus is not None:
            self._register_consensus(
                consensus, lesson_broker, arbiter, proposals
            )
        if task is not None: self._register_task(task)
        if lesson is not None and lesson_broker is not None:
            self._register_lesson(lesson, lesson_broker, room)
        if room is not None: self._register_room(
            room, governance_broker, room_session
        )
        if duty is not None and terminal_duty is not None:
            self._register_duty(duty, terminal_duty, room_session)
        if room_session is not None: self._register_room_session(room_session)
        if peer_registry is not None:
            self._register_peer_registry(peer_registry, health, health_revalidation)
        if role_assignment is not None:
            self._register_role_assignment(role_assignment)
        if leadership is not None:
            self._register_leadership(leadership)
        if capability_matching is not None:
            self._register_capability_matching(capability_matching)
        if feedback is not None:
            self._register_feedback(feedback)
        if file_locks is not None:
            self._register_file_locks(file_locks)
        if artifact_records is not None:
            self._register_artifact_records(artifact_records)

        if operational_errors is not None:
            self._register_operational_errors(
                operational_errors, quarantine_reviews
            )
        if alert_raise is not None:
            self._register_alert_raise(alert_raise)
        if process_lease_sweep is not None:
            self._register_process_lease_sweep(process_lease_sweep)

    @staticmethod
    def _submission(env: CommandEnvelope) -> SubmissionMetadata:
        return SubmissionMetadata(env.client_request_id, env.correlation_id,
            env.client_id, env.actor_id, env.scope, env.idempotency_key,
            env.expected_policy_revision, env.expected_configuration_revision,
            env.client_timestamp)

    @staticmethod
    def _receipt(result: Any) -> Mapping[str, JsonValue]:
        receipt = result.receipt
        return {"receipt_id": receipt.receipt_id, "target_id": receipt.target_id,
                "previous_revision": receipt.previous_revision,
                "next_revision": receipt.next_revision,
                "status": receipt.status.value}

    def _register_effect_status(
        self,
        broker: GovernanceBroker,
    ) -> None:
        from peerhub.application.handlers.effects import (
            register_effect_status_handlers,
        )

        register_effect_status_handlers(api=self, broker=broker)

    def _register_consensus(
        self,
        service: ConsensusService,
        broker: GovernanceBroker | None,
        arbiter: ArbiterReviewCoordinator | None,
        proposals: ProposalCoordinator | None,
    ) -> None:
        from peerhub.application.handlers.consensus import (
            register_consensus_handlers,
        )

        register_consensus_handlers(
            api=self,
            service=service,
            broker=broker,
            arbiter=arbiter,
            proposals=proposals,
        )

    def _register_task(self, s: TaskService) -> None:
        from peerhub.application.handlers.tasks import register_task_handlers

        register_task_handlers(api=self, service=s)

    def _register_lesson(
        self,
        s: LessonService,
        broker: GovernanceBroker,
        room: RoomsService | None,
    ) -> None:
        from peerhub.application.handlers.lessons import register_lesson_handlers

        register_lesson_handlers(
            api=self,
            service=s,
            broker=broker,
            room=room,
        )

    def _register_room(
        self,
        s: RoomsService,
        broker: GovernanceBroker | None,
        room_session: RoomParticipationCoordinator | None = None,
    ) -> None:
        from peerhub.application.handlers.rooms import register_room_handlers

        register_room_handlers(
            api=self,
            service=s,
            broker=broker,
            room_session=room_session,
        )

    def _register_duty(
        self,
        d: DutyLeaseCoordinator,
        t: TerminalDutyService,
        room_session: RoomParticipationCoordinator | None,
    ) -> None:
        from peerhub.application.handlers.duty import register_duty_handlers

        register_duty_handlers(
            api=self,
            duty=d,
            terminal_duty=t,
            room_session=room_session,
        )

    def _register_room_session(
        self, coordinator: RoomParticipationCoordinator
    ) -> None:
        from peerhub.application.handlers.duty import (
            register_room_session_handlers,
        )

        register_room_session_handlers(api=self, coordinator=coordinator)

    def _register_alert_raise(
        self,
        coordinator: AlertRaiseCoordinator,
    ) -> None:
        from peerhub.application.handlers.alerts import register_alert_handlers

        register_alert_handlers(
            api=self,
            coordinator=coordinator,
        )

    def _register_peer_registry(
        self,
        service: PeerRegistryService,
        health: HealthService | None,
        health_revalidation: HealthRevalidationCoordinator | None = None,
    ) -> None:
        from peerhub.application.handlers.peers import (
            register_peer_registry_handlers,
        )

        register_peer_registry_handlers(
            api=self,
            service=service,
            health=health,
            health_revalidation=health_revalidation,
            dispatch=self._dispatch,
        )


    def _register_role_assignment(self, service: RoleAssignmentService) -> None:
        from peerhub.application.handlers.roles import register_role_handlers

        register_role_handlers(
            api=self,
            service=service,
        )

    def _register_leadership(self, service: LeadershipService) -> None:
        from peerhub.application.handlers.leadership import (
            register_leadership_handlers,
        )

        register_leadership_handlers(api=self, service=service)

    def _register_capability_matching(
        self,
        coordinator: CapabilityMatchingCoordinator,
    ) -> None:
        from peerhub.application.handlers.leadership import (
            register_capability_matching_handlers,
        )

        register_capability_matching_handlers(api=self, coordinator=coordinator)

    def _register_feedback(self, service: FeedbackService) -> None:
        from peerhub.application.handlers.feedback import register_feedback_handlers

        register_feedback_handlers(
            api=self,
            service=service,
        )

    def _register_file_locks(self, service: FileLockService) -> None:
        from peerhub.application.handlers.locks import register_lock_handlers

        register_lock_handlers(
            api=self,
            service=service,
        )

    def _register_artifact_records(
        self,
        service: ArtifactRecordService,
    ) -> None:
        from peerhub.application.handlers.artifacts import (
            register_artifact_handlers,
        )

        register_artifact_handlers(
            api=self,
            service=service,
        )

    def _register_operational_errors(
        self,
        service: OperationalErrorService,
        quarantine_reviews: QuarantineReviewCoordinator | None,
    ) -> None:
        from peerhub.application.handlers.operational_errors import (
            register_operational_error_handlers,
        )

        register_operational_error_handlers(
            api=self,
            service=service,
            quarantine_reviews=quarantine_reviews,
        )

    def _register_process_lease_sweep(
        self,
        coordinator: ProcessLeaseSweepCoordinator,
    ) -> None:
        from peerhub.application.handlers.process_lease_sweep import (
            register_process_lease_sweep_handlers,
        )

        register_process_lease_sweep_handlers(
            api=self,
            coordinator=coordinator,
        )

    def register(self, descriptor: CommandDescriptor[Any, Any]) -> None:  # pyright: ignore[reportInvalidTypeArguments]
        if descriptor.method in self._registry:
            raise ValueError(f"Duplicate command method: {descriptor.method}")
        self._registry[descriptor.method] = descriptor

    def _register_builtins(self) -> None:
        from peerhub.application.handlers.dispatch_builtins import (
            register_dispatch_builtin_handlers,
        )

        register_dispatch_builtin_handlers(
            api=self,
            workflows=self._workflows,
            dispatch=self._dispatch,
            admission_provider=self._admission_provider,
        )

    def submit(
        self,
        envelope: CommandEnvelope,
        /,
        *,
        caller: RequestContext,
    ) -> CommandOutcome[Mapping[str, JsonValue]]:
        
        # 1. Version validation
        if envelope.protocol_major != PROTOCOL_MAJOR:
            return CommandFailure(
                ok=False,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=PROTOCOL_MINOR,
                schema_version=SCHEMA_VERSION,
                diagnostic_id="diag-1",
                correlation_id=envelope.correlation_id,
                command_id=None,
                error=ErrorDetail(
                    code=ErrorCode.PROTOCOL_VERSION_MISMATCH,
                    phase=ErrorPhase.VALIDATION,
                    execution_certainty=ExecutionCertainty.NOT_STARTED,
                    retry_disposition=RetryDisposition.NEVER,
                    message="Protocol version mismatch",
                    details={},
                ),
            )

        # 2. Method validation
        desc = self._registry.get(envelope.method)
        if not desc:
            return CommandFailure(
                ok=False,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=PROTOCOL_MINOR,
                schema_version=SCHEMA_VERSION,
                diagnostic_id="diag-2",
                correlation_id=envelope.correlation_id,
                command_id=None,
                error=ErrorDetail(
                    code=ErrorCode.UNKNOWN_COMMAND,
                    phase=ErrorPhase.VALIDATION,
                    execution_certainty=ExecutionCertainty.NOT_STARTED,
                    retry_disposition=RetryDisposition.NEVER,
                    message=f"Unknown command: {envelope.method}",
                    details={},
                ),
            )

        if desc.availability == CommandAvailability.NOT_BACKED:
            return CommandFailure(
                ok=False,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=PROTOCOL_MINOR,
                schema_version=SCHEMA_VERSION,
                diagnostic_id="diag-3",
                correlation_id=envelope.correlation_id,
                command_id=None,
                error=ErrorDetail(
                    code=ErrorCode.COMMAND_NOT_BACKED,
                    phase=ErrorPhase.VALIDATION,
                    execution_certainty=ExecutionCertainty.NOT_STARTED,
                    retry_disposition=RetryDisposition.NEVER,
                    message=desc.unavailable_reason or "Command not backed",
                    details={},
                ),
            )

        # 3. Scope/Idempotency/Params validation. A command whose wire shape
        # supports both reads and writes may classify the concrete envelope;
        # artifact status is read-only unless all draft-registration fields
        # are present.
        effective_mutability = (
            desc.mutability(envelope)
            if callable(desc.mutability)
            else desc.mutability
        )
        effective_idempotency = (
            desc.idempotency(envelope)
            if callable(desc.idempotency)
            else desc.idempotency
        )
        if effective_idempotency == IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED and not envelope.idempotency_key:
            return CommandFailure(
                ok=False,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=PROTOCOL_MINOR,
                schema_version=SCHEMA_VERSION,
                diagnostic_id="diag-4",
                correlation_id=envelope.correlation_id,
                command_id=None,
                error=ErrorDetail(
                    code=ErrorCode.MISSING_IDEMPOTENCY_KEY,
                    phase=ErrorPhase.VALIDATION,
                    execution_certainty=ExecutionCertainty.NOT_STARTED,
                    retry_disposition=RetryDisposition.NEVER,
                    message="Missing idempotency key for mutating command",
                    details={},
                ),
            )

        try:
            cmd = desc.decode(envelope)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        except Exception as exc:
            return CommandFailure(
                ok=False,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=PROTOCOL_MINOR,
                schema_version=SCHEMA_VERSION,
                diagnostic_id="diag-5",
                correlation_id=envelope.correlation_id,
                command_id=None,
                error=ErrorDetail(
                    code=ErrorCode.INVALID_PARAMS,
                    phase=ErrorPhase.VALIDATION,
                    execution_certainty=ExecutionCertainty.NOT_STARTED,
                    retry_disposition=RetryDisposition.NEVER,
                    message=f"Invalid parameters: {exc}",
                    details={},
                ),
            )

        # 4. Auth: gateway-level GovernanceAuthorizer (R4/P4b, ratified
        # 2026-09-19). Verified when a credential is presented on the
        # envelope; otherwise the pre-existing asserted client-identity check.
        if not self._authorizer.authorize(
            caller=caller,
            envelope=envelope,
            submission_client_id=cmd.submission.client_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        ):
            if envelope.credential_id is not None:
                # Verified path failed: a credential was presented but did
                # not verify for the claimed actor -- distinct from a plain
                # asserted client_id mismatch, both in code and message, so
                # a caller (or a human reading CLI stderr) can tell the two
                # failure modes apart.
                auth_code = ErrorCode.ACTOR_UNAUTHORIZED
                auth_message = "credential does not verify for this actor"
            else:
                auth_code = ErrorCode.CLIENT_UNKNOWN
                auth_message = "Client ID mismatch"
            return CommandFailure(
                ok=False,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=PROTOCOL_MINOR,
                schema_version=SCHEMA_VERSION,
                diagnostic_id="diag-6",
                correlation_id=envelope.correlation_id,
                command_id=None,
                error=ErrorDetail(
                    code=auth_code,
                    phase=ErrorPhase.VALIDATION,
                    execution_certainty=ExecutionCertainty.NOT_STARTED,
                    retry_disposition=RetryDisposition.NEVER,
                    message=auth_message,
                    details={},
                ),
            )

        # Execute handler
        try:
            res = desc.handle(cmd, caller)  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType, reportUnknownVariableType]
            encoded_res = desc.encode_result(res)  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
            
            command_id = encoded_res.get("command_id")
            if isinstance(command_id, str):
                cid: str | None = command_id
            else:
                cid = None

            return CommandSuccess(
                ok=True,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=PROTOCOL_MINOR,
                schema_version=SCHEMA_VERSION,
                diagnostic_id="diag-ok",
                correlation_id=envelope.correlation_id,
                command_id=cid,
                state="ADMITTED" if effective_mutability == Mutability.MUTATING else "COMPLETED",
                receipt_ref=encoded_res.get("admission_receipt_id") if isinstance(encoded_res.get("admission_receipt_id"), str) else None,  # pyright: ignore[reportArgumentType]
                policy_revision=None,
                configuration_revision=None,
                idempotency=IdempotencyDisposition.CREATED,
                result=encoded_res,
            )
            
        except _ResourceOwnershipError:
            return CommandFailure(
                ok=False,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=PROTOCOL_MINOR,
                schema_version=SCHEMA_VERSION,
                diagnostic_id="diag-9",
                correlation_id=envelope.correlation_id,
                command_id=None,
                error=ErrorDetail(
                    code=ErrorCode.CLIENT_UNKNOWN,
                    phase=ErrorPhase.VALIDATION,
                    execution_certainty=ExecutionCertainty.NOT_STARTED,
                    retry_disposition=RetryDisposition.NEVER,
                    message="Client ID mismatch",
                    details={},
                ),
            )
        except KeyError as exc:
            return CommandFailure(
                ok=False,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=PROTOCOL_MINOR,
                schema_version=SCHEMA_VERSION,
                diagnostic_id="diag-7",
                correlation_id=envelope.correlation_id,
                command_id=None,
                error=ErrorDetail(
                    code=ErrorCode.RECORD_NOT_FOUND,
                    phase=ErrorPhase.VALIDATION,
                    execution_certainty=ExecutionCertainty.NOT_STARTED,
                    retry_disposition=RetryDisposition.NEVER,
                    message=f"Record not found: {exc}",
                    details={},
                ),
            )
        except PeerHubError as exc:
            # R4/P4b (found migrating consensus, 2026-09-19): PeerHubError
            # subclasses already carry a precise error_code (see
            # peerhub/core/errors.py's protocol-code mapping), but nothing
            # here ever read it before this fix -- every domain validation
            # error (InvalidMutationError, ActorUnauthorizedError, etc.)
            # fell through to the generic Exception branch below as an
            # opaque "Internal server error", discarding the real message.
            # This was a pre-existing gap in ApplicationAPI.submit(),
            # invisible until a gateway-routed call actually raised one.
            return CommandFailure(
                ok=False,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=PROTOCOL_MINOR,
                schema_version=SCHEMA_VERSION,
                diagnostic_id="diag-10",
                correlation_id=envelope.correlation_id,
                command_id=None,
                error=ErrorDetail(
                    code=exc.error_code,
                    phase=ErrorPhase.VALIDATION,
                    execution_certainty=ExecutionCertainty.NOT_STARTED,
                    retry_disposition=RetryDisposition.NEVER,
                    message=str(exc),
                    details={},
                ),
            )
        except Exception as exc:
            return CommandFailure(
                ok=False,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=PROTOCOL_MINOR,
                schema_version=SCHEMA_VERSION,
                diagnostic_id="diag-8",
                correlation_id=envelope.correlation_id,
                command_id=None,
                error=ErrorDetail(
                    code=ErrorCode.INTERNAL_ERROR,
                    phase=ErrorPhase.EFFECT,
                    execution_certainty=ExecutionCertainty.MAY_HAVE_STARTED,
                    retry_disposition=RetryDisposition.UNSAFE,
                    message="Internal server error",
                    details={"exception": type(exc).__name__},
                ),
            )
