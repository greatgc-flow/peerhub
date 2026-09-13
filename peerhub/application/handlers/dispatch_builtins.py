"""Dispatch built-in command registration handlers."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any, Protocol, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from peerhub.application.commands import (
    AdmitDispatch,
    DispatchAdmissionView,
    DispatchLeaseView,
    DispatchRequestView,
    GetDispatchLease,
    GetDispatchRequest,
    SubmissionMetadata,
)
from peerhub.application.workflows import ApplicationWorkflows
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.ports import RequestContext
from peerhub.core.protocol import (
    CommandEnvelope,
    JsonValue,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    freeze_json_mapping,
)
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.contract import (
    CompletionContract,
    CompletionContractKind,
)
from peerhub.dispatch.service import DispatchService


def validate_wire_json_value(value: object) -> None:
    """Reject non-JSON values without coercing caller input."""

    if value is None or type(value) in {str, int, bool}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return
    if isinstance(value, list):
        for item in cast(list[object], value):
            validate_wire_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in cast(dict[object, object], value).items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            validate_wire_json_value(item)
        return
    raise ValueError(
        f"unsupported JSON value type: {type(value).__name__}"
    )


_validate_wire_json_value = validate_wire_json_value


class ResourceOwnershipError(Exception):
    """Signal a client/resource ownership mismatch to ``submit``."""


_ResourceOwnershipError = ResourceOwnershipError


class AdmissionInputs(Protocol):  # pyright: ignore[reportUntypedBaseClass]
    route_request_factory: Any
    dispatch_policy_revision: int | str | None
    session_id: str
    owner_principal_id: str
    owner_instance_id: str
    authority_epoch: int
    heartbeat_timeout_ms: int
    owner_peer_id: str


class AdmissionInputsProvider(Protocol):  # pyright: ignore[reportUntypedBaseClass]
    def resolve(
        self,
        command: AdmitDispatch,
        caller: RequestContext,
    ) -> AdmissionInputs:
        ...


def reconstruct_envelope(cmd: AdmitDispatch | Any) -> CommandEnvelope:  # pyright: ignore[reportUnknownParameterType]
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


class CompletionContractPayload(BaseModel):
    """Strict wire representation of a caller completion contract."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    kind: str = CompletionContractKind.DELIVERY_ONLY.value
    requirements: list[dict[str, object]] = Field(  # pyright: ignore[reportUnknownVariableType]
        default_factory=list
    )
    replay_safe: bool = True

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        try:
            CompletionContractKind(value)
        except ValueError as exc:
            raise ValueError(
                "kind must be a valid CompletionContractKind"
            ) from exc
        return value

    @field_validator("requirements")
    @classmethod
    def validate_requirement_values(
        cls,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        for requirement in value:
            validate_wire_json_value(requirement)
        return value

    @model_validator(mode="after")
    def validate_requirements(self) -> "CompletionContractPayload":
        if (
            CompletionContractKind(self.kind)
            is not CompletionContractKind.DELIVERY_ONLY
            and not self.requirements
        ):
            raise ValueError(
                "non-delivery completion contracts need requirements"
            )
        return self


class AdmitDispatchPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    prompt: str = ""
    required_capability_tier: CapabilityTier
    requested_capabilities: list[str] = Field(default_factory=list)
    profile_constraints: dict[str, Any] = Field(default_factory=dict)
    completion_contract: CompletionContractPayload = Field(
        default_factory=CompletionContractPayload
    )
    session_policy: dict[str, Any] = Field(default_factory=dict)

    @field_validator("required_capability_tier", mode="before")
    @classmethod
    def validate_required_capability_tier(
        cls,
        value: object,
    ) -> CapabilityTier:
        if isinstance(value, CapabilityTier):
            return value
        if isinstance(value, str):
            try:
                return CapabilityTier[value]
            except KeyError as exc:
                raise ValueError(
                    "required_capability_tier must be a valid "
                    "CapabilityTier"
                ) from exc
        raise ValueError(
            "required_capability_tier must be a valid CapabilityTier"
        )


class GetDispatchRequestPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    target_command_id: str


class GetDispatchLeasePayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    lease_id: str


def register_dispatch_builtin_handlers(
    *,
    api: Any,
    workflows: ApplicationWorkflows,
    dispatch: DispatchService,
    admission_provider: AdmissionInputsProvider | None = None,
) -> None:
    """Register the built-in dispatch wire handlers unchanged."""

    from peerhub.application.api import (
        CommandAvailability,
        CommandDescriptor,
        IdempotencyPolicy,
        Mutability,
        ScopeKind,
    )

    register = api.register
    descriptor = CommandDescriptor
    mutating = Mutability.MUTATING
    read_only = Mutability.READ_ONLY
    any_scope = ScopeKind.ANY
    domain_atomic_required = IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED
    idempotency_read_only = IdempotencyPolicy.READ_ONLY

    # 1. AdmitDispatch
    def decode_admit(env: CommandEnvelope) -> AdmitDispatch:
        from collections.abc import Mapping

        def _normalize(v: Any) -> Any:
            if isinstance(v, Mapping):
                return {k: _normalize(val) for k, val in v.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            if isinstance(v, (list, tuple)):
                return [_normalize(val) for val in v]  # pyright: ignore[reportUnknownVariableType]
            return v

        try:
            payload = AdmitDispatchPayload.model_validate(_normalize(env.params))
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

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
            prompt=payload.prompt,
            required_capability_tier=payload.required_capability_tier,
            requested_capabilities=tuple(payload.requested_capabilities),
            profile_constraints=freeze_json_mapping(payload.profile_constraints),
            completion_contract=freeze_json_mapping(
                payload.completion_contract.model_dump(mode="json")
            ),
            session_policy=freeze_json_mapping(payload.session_policy),
        )

    def handle_admit(
        cmd: AdmitDispatch,
        caller: RequestContext,
    ) -> DispatchAdmissionView:
        active_admission_provider = getattr(
            api, "_admission_provider", admission_provider
        )
        if not active_admission_provider:
            raise RuntimeError("admit_request requires AdmissionInputsProvider")

        inputs = active_admission_provider.resolve(cmd, caller)
        env = reconstruct_envelope(cmd)

        cc_in = cmd.completion_contract
        cc = CompletionContract(
            contract_id=f"{cmd.submission.client_request_id}-cc",
            kind=CompletionContractKind(cast(str, cc_in["kind"])),
            requirements=cast(
                tuple[Mapping[str, JsonValue], ...],
                cc_in["requirements"],
            ),
            replay_safe=cast(bool, cc_in["replay_safe"]),
        )

        active_workflows = getattr(api, "_workflows", workflows)
        res = active_workflows.admit_request(
            env,
            route_request_factory=inputs.route_request_factory,  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
            required_capability_tier=cmd.required_capability_tier,
            authenticated_subject=AuthenticatedSubject(
                principal_id=caller.principal,
                evidence_source="api-request-context",
            ),
            completion_contract=cc,
            dispatch_policy_revision=inputs.dispatch_policy_revision,  # pyright: ignore[reportArgumentType]
            session_id=inputs.session_id,
            owner_principal_id=inputs.owner_principal_id,
            owner_instance_id=inputs.owner_instance_id,
            authority_epoch=inputs.authority_epoch,
            heartbeat_timeout_ms=inputs.heartbeat_timeout_ms,
            owner_peer_id=inputs.owner_peer_id,
        )

        adm = res.dispatch_admission
        if not adm:
            raise RuntimeError(
                f"Admission rejected: {res.route.error_code if res.route else 'unknown'}"
            )
        req = adm[0]
        receipt = adm[1]
        lease = adm[2]
        capability_lease = adm[3]

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
            capability_lease_id=capability_lease.capability_lease_id,
        )

    avail_admit = (
        CommandAvailability.AVAILABLE
        if getattr(api, "_admission_provider", admission_provider)
        else CommandAvailability.NOT_BACKED
    )
    reason_admit = (
        None
        if getattr(api, "_admission_provider", admission_provider)
        else "admission_inputs_provider_missing"
    )

    register(
        descriptor(
            method="dispatch.admit",
            mutability=mutating,
            accepted_scope=any_scope,
            idempotency=domain_atomic_required,
            decode=decode_admit,
            handle=handle_admit,
            encode_result=lambda r: {  # pyright: ignore[reportUnknownLambdaType]
                "command_id": r.command_id,  # pyright: ignore[reportUnknownMemberType]
                "request_state": r.request_state.value,  # pyright: ignore[reportUnknownMemberType]
                "request_revision": r.request_revision,  # pyright: ignore[reportUnknownMemberType]
                "admission_receipt_id": r.admission_receipt_id,  # pyright: ignore[reportUnknownMemberType]
                "lease_id": r.lease_id,  # pyright: ignore[reportUnknownMemberType]
                "lease_state": r.lease_state.value,  # pyright: ignore[reportUnknownMemberType]
                "selected_instance_id": r.selected_instance_id,  # pyright: ignore[reportUnknownMemberType]
                "selected_profile_id": r.selected_profile_id,  # pyright: ignore[reportUnknownMemberType]
                "route_decision_digest": r.route_decision_digest,  # pyright: ignore[reportUnknownMemberType]
                "capability_lease_id": r.capability_lease_id,  # pyright: ignore[reportUnknownMemberType]
            },
            availability=avail_admit,
            unavailable_reason=reason_admit,
        )
    )

    # 2. GetDispatchRequest
    def decode_req_get(env: CommandEnvelope) -> GetDispatchRequest:
        from collections.abc import Mapping

        def _normalize(v: Any) -> Any:
            if isinstance(v, Mapping):
                return {k: _normalize(val) for k, val in v.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            if isinstance(v, (list, tuple)):
                return [_normalize(val) for val in v]  # pyright: ignore[reportUnknownVariableType]
            return v

        try:
            payload = GetDispatchRequestPayload.model_validate(_normalize(env.params))
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

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
            target_command_id=payload.target_command_id,
        )

    def handle_req_get(
        cmd: GetDispatchRequest,
        caller: RequestContext,
    ) -> DispatchRequestView:
        active_dispatch = getattr(api, "_dispatch", dispatch)
        req = active_dispatch.get_request(cmd.target_command_id)
        if not req:
            raise KeyError(cmd.target_command_id)

        if req.client_id != caller.client_id:
            raise ResourceOwnershipError()

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
            terminal_error_code=req.terminal_error_code.value
            if req.terminal_error_code
            else None,
        )

    register(
        descriptor(
            method="dispatch.request.get",
            mutability=read_only,
            accepted_scope=any_scope,
            idempotency=idempotency_read_only,
            decode=decode_req_get,
            handle=handle_req_get,
            encode_result=lambda r: {  # pyright: ignore[reportUnknownLambdaType]
                "command_id": r.command_id,  # pyright: ignore[reportUnknownMemberType]
                "client_id": r.client_id,  # pyright: ignore[reportUnknownMemberType]
                "client_request_id": r.client_request_id,  # pyright: ignore[reportUnknownMemberType]
                "correlation_id": r.correlation_id,  # pyright: ignore[reportUnknownMemberType]
                "authenticated_principal": r.authenticated_principal,  # pyright: ignore[reportUnknownMemberType]
                "command_type": r.command_type,  # pyright: ignore[reportUnknownMemberType]
                "idempotency_key": r.idempotency_key,  # pyright: ignore[reportUnknownMemberType]
                "payload_digest": r.payload_digest,  # pyright: ignore[reportUnknownMemberType]
                "scope": r.scope,  # pyright: ignore[reportUnknownMemberType]
                "expected_policy_revision": r.expected_policy_revision,  # pyright: ignore[reportUnknownMemberType]
                "expected_configuration_revision": r.expected_configuration_revision,  # pyright: ignore[reportUnknownMemberType]
                "policy_revision": r.policy_revision,  # pyright: ignore[reportUnknownMemberType]
                "configuration_revision": r.configuration_revision,  # pyright: ignore[reportUnknownMemberType]
                "selected_peer_instance_id": r.selected_peer_instance_id,  # pyright: ignore[reportUnknownMemberType]
                "selected_profile_id": r.selected_profile_id,  # pyright: ignore[reportUnknownMemberType]
                "route_decision_digest": r.route_decision_digest,  # pyright: ignore[reportUnknownMemberType]
                "lease_id": r.lease_id,  # pyright: ignore[reportUnknownMemberType]
                "state": r.state.value,  # pyright: ignore[reportUnknownMemberType]
                "revision": r.revision,  # pyright: ignore[reportUnknownMemberType]
                "created_at": r.created_at,  # pyright: ignore[reportUnknownMemberType]
                "updated_at": r.updated_at,  # pyright: ignore[reportUnknownMemberType]
                "terminal_error_code": r.terminal_error_code,  # pyright: ignore[reportUnknownMemberType]
            },
            availability=CommandAvailability.AVAILABLE,
        )
    )

    # 3. GetDispatchLease
    def decode_lease_get(env: CommandEnvelope) -> GetDispatchLease:
        from collections.abc import Mapping

        def _normalize(v: Any) -> Any:
            if isinstance(v, Mapping):
                return {k: _normalize(val) for k, val in v.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            if isinstance(v, (list, tuple)):
                return [_normalize(val) for val in v]  # pyright: ignore[reportUnknownVariableType]
            return v

        try:
            payload = GetDispatchLeasePayload.model_validate(_normalize(env.params))
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

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
            lease_id=payload.lease_id,
        )

    def handle_lease_get(
        cmd: GetDispatchLease,
        caller: RequestContext,
    ) -> DispatchLeaseView:
        active_dispatch = getattr(api, "_dispatch", dispatch)
        lease = active_dispatch.get_lease(cmd.lease_id)
        if not lease:
            raise KeyError(cmd.lease_id)

        request = active_dispatch.get_request(lease.fence.command_id)
        if request is None:
            raise KeyError(str(lease.fence.command_id))
        if request.client_id != caller.client_id:
            raise ResourceOwnershipError()

        return DispatchLeaseView(
            lease_id=lease.lease_id,
            state=lease.state,
            revision=lease.fence.revision,
            created_at=lease.created_at,
            updated_at=lease.updated_at,
            fence_command_id=str(lease.fence.command_id)
            if lease.fence.command_id
            else None,
            fence_attempt_id=lease.fence.attempt_id,
            fence_revision=lease.fence.revision,
        )

    register(
        descriptor(
            method="dispatch.lease.get",
            mutability=read_only,
            accepted_scope=any_scope,
            idempotency=idempotency_read_only,
            decode=decode_lease_get,
            handle=handle_lease_get,
            encode_result=lambda r: {  # pyright: ignore[reportUnknownLambdaType]
                "lease_id": r.lease_id,  # pyright: ignore[reportUnknownMemberType]
                "state": r.state.value,  # pyright: ignore[reportUnknownMemberType]
                "revision": r.revision,  # pyright: ignore[reportUnknownMemberType]
                "created_at": r.created_at,  # pyright: ignore[reportUnknownMemberType]
                "updated_at": r.updated_at,  # pyright: ignore[reportUnknownMemberType]
                "fence_command_id": r.fence_command_id,  # pyright: ignore[reportUnknownMemberType]
                "fence_attempt_id": r.fence_attempt_id,  # pyright: ignore[reportUnknownMemberType]
                "fence_revision": r.fence_revision,  # pyright: ignore[reportUnknownMemberType]
            },
            availability=CommandAvailability.AVAILABLE,
        )
    )
