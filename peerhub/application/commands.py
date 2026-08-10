"""Public typed commands and result views for the PeerHub application boundary."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Generic, Protocol, TypeVar

from peerhub.core.protocol import JsonValue, RevisionValue
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.contract import LeaseState, RequestState

R = TypeVar("R")


@dataclass(frozen=True)
class SubmissionMetadata:
    client_request_id: str
    correlation_id: str
    client_id: str
    actor_id: str | None
    scope: Mapping[str, JsonValue]
    idempotency_key: str | None
    expected_policy_revision: RevisionValue | None
    expected_configuration_revision: RevisionValue | None
    client_timestamp: int


class Command(Protocol, Generic[R]):  # pyright: ignore[reportInvalidTypeVarUse]
    method: ClassVar[str]
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        ...

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> R:
        ...


CompletionContractInput = Mapping[str, JsonValue]
SessionPolicy = Mapping[str, JsonValue]


@dataclass(frozen=True)
class DispatchAdmissionView:
    command_id: str
    request_state: RequestState
    request_revision: int
    admission_receipt_id: str
    lease_id: str
    lease_state: LeaseState
    selected_instance_id: str
    selected_profile_id: str
    route_decision_digest: str


@dataclass(frozen=True, slots=True)
class AdmitDispatch(Command["DispatchAdmissionView"]):
    method: ClassVar[str] = "dispatch.admit"

    submission: SubmissionMetadata
    prompt: str
    required_capability_tier: CapabilityTier
    requested_capabilities: tuple[str, ...]
    profile_constraints: Mapping[str, JsonValue]
    completion_contract: CompletionContractInput
    session_policy: SessionPolicy

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "prompt": self.prompt,
            "required_capability_tier": self.required_capability_tier.name,
            "requested_capabilities": list(self.requested_capabilities),  # pyright: ignore[reportReturnType]
            "profile_constraints": self.profile_constraints,
            "completion_contract": self.completion_contract,
            "session_policy": self.session_policy,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> "DispatchAdmissionView":
        return DispatchAdmissionView(
            command_id=value["command_id"],  # type: ignore
            request_state=RequestState(value["request_state"]),  # type: ignore
            request_revision=value["request_revision"],  # type: ignore
            admission_receipt_id=value["admission_receipt_id"],  # type: ignore
            lease_id=value["lease_id"],  # type: ignore
            lease_state=LeaseState(value["lease_state"]),  # type: ignore
            selected_instance_id=value["selected_instance_id"],  # type: ignore
            selected_profile_id=value["selected_profile_id"],  # type: ignore
            route_decision_digest=value["route_decision_digest"],  # type: ignore
        )


@dataclass(frozen=True)
class DispatchRequestView:
    command_id: str
    client_id: str
    client_request_id: str
    correlation_id: str
    authenticated_principal: str
    command_type: str
    idempotency_key: str
    payload_digest: str
    scope: Mapping[str, JsonValue]
    expected_policy_revision: RevisionValue | None
    expected_configuration_revision: RevisionValue | None
    policy_revision: RevisionValue
    configuration_revision: RevisionValue
    selected_peer_instance_id: str
    selected_profile_id: str
    route_decision_digest: str
    lease_id: str
    state: RequestState
    revision: int
    created_at: int
    updated_at: int
    terminal_error_code: str | None


@dataclass(frozen=True, slots=True)
class GetDispatchRequest(Command["DispatchRequestView"]):
    method: ClassVar[str] = "dispatch.request.get"

    submission: SubmissionMetadata
    target_command_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"target_command_id": self.target_command_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> "DispatchRequestView":
        return DispatchRequestView(
            command_id=value["command_id"],  # type: ignore
            client_id=value["client_id"],  # type: ignore
            client_request_id=value["client_request_id"],  # type: ignore
            correlation_id=value["correlation_id"],  # type: ignore
            authenticated_principal=value["authenticated_principal"],  # type: ignore
            command_type=value["command_type"],  # type: ignore
            idempotency_key=value["idempotency_key"],  # type: ignore
            payload_digest=value["payload_digest"],  # type: ignore
            scope=value["scope"],  # type: ignore
            expected_policy_revision=value.get("expected_policy_revision"),  # type: ignore
            expected_configuration_revision=value.get("expected_configuration_revision"),  # type: ignore
            policy_revision=value["policy_revision"],  # type: ignore
            configuration_revision=value["configuration_revision"],  # type: ignore
            selected_peer_instance_id=value["selected_peer_instance_id"],  # type: ignore
            selected_profile_id=value["selected_profile_id"],  # type: ignore
            route_decision_digest=value["route_decision_digest"],  # type: ignore
            lease_id=value["lease_id"],  # type: ignore
            state=RequestState(value["state"]),  # type: ignore
            revision=value["revision"],  # type: ignore
            created_at=value["created_at"],  # type: ignore
            updated_at=value["updated_at"],  # type: ignore
            terminal_error_code=value.get("terminal_error_code"),  # type: ignore
        )


@dataclass(frozen=True)
class DispatchLeaseView:
    lease_id: str
    state: LeaseState
    revision: int
    created_at: int
    updated_at: int
    fence_command_id: str | None
    fence_attempt_id: str | None
    fence_revision: int


@dataclass(frozen=True, slots=True)
class GetDispatchLease(Command["DispatchLeaseView"]):
    method: ClassVar[str] = "dispatch.lease.get"

    submission: SubmissionMetadata
    lease_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"lease_id": self.lease_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> "DispatchLeaseView":
        return DispatchLeaseView(
            lease_id=value["lease_id"],  # type: ignore
            state=LeaseState(value["state"]),  # type: ignore
            revision=value["revision"],  # type: ignore
            created_at=value["created_at"],  # type: ignore
            updated_at=value["updated_at"],  # type: ignore
            fence_command_id=value.get("fence_command_id"),  # type: ignore
            fence_attempt_id=value.get("fence_attempt_id"),  # type: ignore
            fence_revision=value["fence_revision"],  # type: ignore
        )
