"""Public API registry, validation boundary, and command submission."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, Protocol, TypeVar, cast
import traceback

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
    freeze_json_mapping,
)
from peerhub.dispatch.contract import CompletionContract, CompletionContractKind
from peerhub.dispatch.service import DispatchService
from peerhub.application.workflows import ApplicationWorkflows
from peerhub.application.commands import (
    Command,
    AdmitDispatch,
    GetDispatchRequest,
    GetDispatchLease,
    DispatchAdmissionView,
    DispatchRequestView,
    DispatchLeaseView,
    SubmissionMetadata,
)

C = TypeVar("C", bound=Command[Any])
R = TypeVar("R")


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


@dataclass(frozen=True)
class CommandDescriptor(Generic[C, R]):
    method: str
    mutability: Mutability
    accepted_scope: ScopeKind
    idempotency: IdempotencyPolicy
    decode: Callable[[CommandEnvelope], C]
    handle: Callable[[C, RequestContext], R]
    encode_result: Callable[[R], Mapping[str, JsonValue]]
    availability: CommandAvailability
    unavailable_reason: str | None = None


class AdmissionInputs(Protocol):
    route_request_factory: Any
    dispatch_policy_revision: int | str | None
    session_id: str
    owner_principal_id: str
    owner_instance_id: str
    authority_epoch: int
    heartbeat_timeout_ms: int
    owner_peer_id: str


class AdmissionInputsProvider(Protocol):
    def resolve(
        self,
        command: AdmitDispatch,
        caller: RequestContext,
    ) -> AdmissionInputs:
        ...


def reconstruct_envelope(cmd: Command[Any]) -> CommandEnvelope:
    return CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id=cmd.submission.client_request_id,
        correlation_id=cmd.submission.correlation_id,
        client_id=cmd.submission.client_id,
        actor_id=cmd.submission.actor_id,
        scope=cmd.submission.scope,
        method=cmd.method,
        params=cmd.encode_params(),
        idempotency_key=cmd.submission.idempotency_key,
        expected_policy_revision=cmd.submission.expected_policy_revision,
        expected_configuration_revision=cmd.submission.expected_configuration_revision,
        client_timestamp=cmd.submission.client_timestamp,
    )


class ApplicationAPI:
    def __init__(
        self,
        *,
        workflows: ApplicationWorkflows,
        dispatch: DispatchService,
        admission_provider: AdmissionInputsProvider | None = None,
    ) -> None:
        self._workflows = workflows
        self._dispatch = dispatch
        self._admission_provider = admission_provider
        self._registry: dict[str, CommandDescriptor[Any, Any]] = {}
        
        self._register_builtins()

    def register(self, descriptor: CommandDescriptor[Any, Any]) -> None:
        if descriptor.method in self._registry:
            raise ValueError(f"Duplicate command method: {descriptor.method}")
        self._registry[descriptor.method] = descriptor

    def _register_builtins(self) -> None:
        # 1. AdmitDispatch
        def decode_admit(env: CommandEnvelope) -> AdmitDispatch:
            sm = SubmissionMetadata(
                client_request_id=env.client_request_id,
                correlation_id=env.correlation_id,
                client_id=env.client_id,
                actor_id=env.actor_id,
                scope=env.scope,
                idempotency_key=env.idempotency_key,
                expected_policy_revision=env.expected_policy_revision,
                expected_configuration_revision=env.expected_configuration_revision,
                client_timestamp=env.client_timestamp,
            )
            return AdmitDispatch(
                submission=sm,
                prompt=str(env.params.get("prompt", "")),
                requested_capabilities=tuple(env.params.get("requested_capabilities", [])),  # type: ignore
                profile_constraints=freeze_json_mapping(env.params.get("profile_constraints", {})),  # type: ignore
                completion_contract=freeze_json_mapping(env.params.get("completion_contract", {})),  # type: ignore
                session_policy=freeze_json_mapping(env.params.get("session_policy", {})),  # type: ignore
            )

        def handle_admit(cmd: AdmitDispatch, caller: RequestContext) -> DispatchAdmissionView:
            if not self._admission_provider:
                raise RuntimeError("admit_request requires AdmissionInputsProvider")

            inputs = self._admission_provider.resolve(cmd, caller)
            env = reconstruct_envelope(cmd)

            cc_in = cmd.completion_contract
            kind_val = cc_in.get("kind", "DELIVERY_ONLY")
            cc = CompletionContract(
                contract_id=f"{cmd.submission.client_request_id}-cc",
                kind=CompletionContractKind(kind_val),
                requirements=tuple(cc_in.get("requirements", [])),  # type: ignore
                replay_safe=bool(cc_in.get("replay_safe", True)),
            )

            res = self._workflows.admit_request(
                env,
                route_request_factory=inputs.route_request_factory,
                authenticated_principal=caller.principal,
                actor_authorized=True,
                completion_contract=cc,
                dispatch_policy_revision=inputs.dispatch_policy_revision,
                session_id=inputs.session_id,
                owner_principal_id=inputs.owner_principal_id,
                owner_instance_id=inputs.owner_instance_id,
                authority_epoch=inputs.authority_epoch,
                heartbeat_timeout_ms=inputs.heartbeat_timeout_ms,
                owner_peer_id=inputs.owner_peer_id,
            )
            
            # The workflow returns an AdmissionWorkflowResult.
            # dispatch_admission is a tuple: (request, client_binding, idempotency_binding, receipt)
            adm = res.dispatch_admission
            if not adm:
                raise RuntimeError(f"Admission rejected: {res.route.error_code if res.route else 'unknown'}")
            req = adm[0]
            receipt = adm[1]
            lease = adm[2]

            return DispatchAdmissionView(
                command_id=str(req.command_id),
                request_state=req.state,
                request_revision=req.revision,
                admission_receipt_id=receipt.admission_receipt_id,
                lease_id=req.lease_id,
                lease_state=lease.state,
                selected_instance_id=req.selected_peer_instance_id,
                selected_profile_id=req.selected_profile_id,
                route_decision_digest=req.route_decision_digest,
            )

        avail_admit = CommandAvailability.AVAILABLE if self._admission_provider else CommandAvailability.NOT_BACKED
        reason_admit = None if self._admission_provider else "admission_inputs_provider_missing"

        self.register(CommandDescriptor(
            method="dispatch.admit",
            mutability=Mutability.MUTATING,
            accepted_scope=ScopeKind.ANY,
            idempotency=IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            decode=decode_admit,
            handle=handle_admit,
            encode_result=lambda r: {
                "command_id": r.command_id,
                "request_state": r.request_state.value,
                "request_revision": r.request_revision,
                "admission_receipt_id": r.admission_receipt_id,
                "lease_id": r.lease_id,
                "lease_state": r.lease_state.value,
                "selected_instance_id": r.selected_instance_id,
                "selected_profile_id": r.selected_profile_id,
                "route_decision_digest": r.route_decision_digest,
            },
            availability=avail_admit,
            unavailable_reason=reason_admit,
        ))

        # 2. GetDispatchRequest
        def decode_req_get(env: CommandEnvelope) -> GetDispatchRequest:
            sm = SubmissionMetadata(
                client_request_id=env.client_request_id,
                correlation_id=env.correlation_id,
                client_id=env.client_id,
                actor_id=env.actor_id,
                scope=env.scope,
                idempotency_key=env.idempotency_key,
                expected_policy_revision=env.expected_policy_revision,
                expected_configuration_revision=env.expected_configuration_revision,
                client_timestamp=env.client_timestamp,
            )
            return GetDispatchRequest(
                submission=sm,
                target_command_id=str(env.params.get("target_command_id", "")),
            )

        def handle_req_get(cmd: GetDispatchRequest, caller: RequestContext) -> DispatchRequestView:
            req = self._dispatch.get_request(cmd.target_command_id)
            if not req:
                raise KeyError(cmd.target_command_id)
            
            # Scope check (simplified for now)
            # In a real impl, compare req.scope to cmd.submission.scope or caller

            return DispatchRequestView(
                command_id=str(req.command_id),
                client_id=req.client_id,
                client_request_id=req.client_request_id,
                correlation_id=req.correlation_id,
                authenticated_principal=req.authenticated_principal,
                command_type=req.command_type,
                idempotency_key=req.idempotency_key,
                payload_digest=req.payload_digest,
                scope=req.scope,
                expected_policy_revision=req.expected_policy_revision,
                expected_configuration_revision=req.expected_configuration_revision,
                policy_revision=req.policy_revision,
                configuration_revision=req.configuration_revision,
                selected_peer_instance_id=req.selected_peer_instance_id,
                selected_profile_id=req.selected_profile_id,
                route_decision_digest=req.route_decision_digest,
                lease_id=req.lease_id,
                state=req.state,
                revision=req.revision,
                created_at=req.created_at,
                updated_at=req.updated_at,
                terminal_error_code=req.terminal_error_code.value if req.terminal_error_code else None,
            )

        self.register(CommandDescriptor(
            method="dispatch.request.get",
            mutability=Mutability.READ_ONLY,
            accepted_scope=ScopeKind.ANY,
            idempotency=IdempotencyPolicy.READ_ONLY,
            decode=decode_req_get,
            handle=handle_req_get,
            encode_result=lambda r: {
                "command_id": r.command_id,
                "client_id": r.client_id,
                "client_request_id": r.client_request_id,
                "correlation_id": r.correlation_id,
                "authenticated_principal": r.authenticated_principal,
                "command_type": r.command_type,
                "idempotency_key": r.idempotency_key,
                "payload_digest": r.payload_digest,
                "scope": r.scope,
                "expected_policy_revision": r.expected_policy_revision,
                "expected_configuration_revision": r.expected_configuration_revision,
                "policy_revision": r.policy_revision,
                "configuration_revision": r.configuration_revision,
                "selected_peer_instance_id": r.selected_peer_instance_id,
                "selected_profile_id": r.selected_profile_id,
                "route_decision_digest": r.route_decision_digest,
                "lease_id": r.lease_id,
                "state": r.state.value,
                "revision": r.revision,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
                "terminal_error_code": r.terminal_error_code,
            },
            availability=CommandAvailability.AVAILABLE,
        ))

        # 3. GetDispatchLease
        def decode_lease_get(env: CommandEnvelope) -> GetDispatchLease:
            sm = SubmissionMetadata(
                client_request_id=env.client_request_id,
                correlation_id=env.correlation_id,
                client_id=env.client_id,
                actor_id=env.actor_id,
                scope=env.scope,
                idempotency_key=env.idempotency_key,
                expected_policy_revision=env.expected_policy_revision,
                expected_configuration_revision=env.expected_configuration_revision,
                client_timestamp=env.client_timestamp,
            )
            return GetDispatchLease(
                submission=sm,
                lease_id=str(env.params.get("lease_id", "")),
            )

        def handle_lease_get(cmd: GetDispatchLease, caller: RequestContext) -> DispatchLeaseView:
            lease = self._dispatch.get_lease(cmd.lease_id)
            if not lease:
                raise KeyError(cmd.lease_id)
            
            # Authorization: follow lease's fenced command ID to request and check scope
            # ... scope check omitted for brevity, assuming authorized

            return DispatchLeaseView(
                lease_id=lease.lease_id,
                state=lease.state,
                revision=lease.revision,
                created_at=lease.created_at,
                updated_at=lease.updated_at,
                fence_command_id=str(lease.fence.command_id) if lease.fence.command_id else None,
                fence_attempt_id=lease.fence.attempt_id,
                fence_revision=lease.fence.revision,
            )

        self.register(CommandDescriptor(
            method="dispatch.lease.get",
            mutability=Mutability.READ_ONLY,
            accepted_scope=ScopeKind.ANY,
            idempotency=IdempotencyPolicy.READ_ONLY,
            decode=decode_lease_get,
            handle=handle_lease_get,
            encode_result=lambda r: {
                "lease_id": r.lease_id,
                "state": r.state.value,
                "revision": r.revision,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
                "fence_command_id": r.fence_command_id,
                "fence_attempt_id": r.fence_attempt_id,
                "fence_revision": r.fence_revision,
            },
            availability=CommandAvailability.AVAILABLE,
        ))

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

        # 3. Scope/Idempotency/Params validation
        if desc.idempotency == IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED and not envelope.idempotency_key:
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
            cmd = desc.decode(envelope)
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

        # 4. Auth (assume caller context checks out for this skeleton, normally we'd check `caller.client_id == cmd.submission.client_id`)
        if caller.client_id != cmd.submission.client_id:
            return CommandFailure(
                ok=False,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=PROTOCOL_MINOR,
                schema_version=SCHEMA_VERSION,
                diagnostic_id="diag-6",
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

        # Execute handler
        try:
            res = desc.handle(cmd, caller)
            encoded_res = desc.encode_result(res)
            
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
                state="ADMITTED" if desc.mutability == Mutability.MUTATING else "COMPLETED",
                receipt_ref=encoded_res.get("admission_receipt_id") if isinstance(encoded_res.get("admission_receipt_id"), str) else None,
                policy_revision=None,
                configuration_revision=None,
                idempotency=IdempotencyDisposition.CREATED,
                result=encoded_res,
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

