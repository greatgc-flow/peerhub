"""Public API registry, validation boundary, and command submission."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, TypeVar, Generic, Protocol, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from peerhub.core.execution import ExecutionCertainty
from peerhub.core.identity import AuthenticatedSubject
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
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.service import DispatchService
from peerhub.application.workflows import ApplicationWorkflows
from peerhub.application.lesson_broadcast import (
    LessonBroadcastCoordinator,
    LessonBroadcastResult,
)
from peerhub.application.arbiter_review import ArbiterReviewCoordinator
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
from peerhub.governance.consensus import ConsensusService
from peerhub.governance.tasks import TaskService
from peerhub.governance.lessons import LessonService
from peerhub.governance.rooms import RoomsService
from peerhub.governance.activity import list_active_lessons
from peerhub.governance.broker import GovernanceBroker
from peerhub.dispatch.duty_lease import (
    DutyLeaseCoordinator,
    DutyLeaseCloseRequest,
    DutyLeaseCreateRequest,
    DutyLeaseSnapshot,
    DutyOwnerIdentity,
)
from peerhub.dispatch.room_session import (
    RoomParticipationCoordinator,
    RoomSessionEndRequest,
    RoomSessionHeartbeatRequest,
    RoomSessionOpenRequest,
    RoomSessionSnapshot,
)
from peerhub.dispatch.terminal_duty import TerminalDutyService
from peerhub.application.legacy import (
    ConsensusProposeCommand, ConsensusVoteCommand, ConsensusCheckCommand,
    NewTopicCommand, ThreadAppendCommand, ThreadReactCommand, ClearRoomCommand,
    MessageSendCommand, MessageCheckCommand, MessageMarkReadCommand,
    ThreadPromoteCommand,
    AppendHandoffCommand, ContinuityCheckpointCommand, ContextFillCommand,
    LeaderClaimCommand, LeaderYieldCommand,
    TerminalHandoffCommand, TerminalHeartbeatCommand, TerminalCloseCommand,
    TerminalDutySweepCommand, TaskCheckpointCommand,
    TaskStatusCommand, TaskFailoverCommand, LessonProposeCommand,
    LessonActivateCommand, LessonRetireCommand, LessonBroadcastCommand,
    ApprovalRequestCommand,
    ConsensusSweepCommand, LessonsListCommand, ProposalListCommand, ArbiterReviewCommand,
    SessionOpenCommand, SessionCloseCommand, SessionHeartbeatCommand,
    RegisterNodeCommand, ListNodesCommand,
    AssignRoleCommand, ReleaseRoleCommand, RoleStatusCommand,
    FeedbackAddCommand, FeedbackListCommand, FeedbackResolveCommand,
)
from peerhub.application.peer_registry import PeerRegistryService
from peerhub.application.role_assignment import (
    RoleAssignmentService,
    RoleReleaseResult,
)
from peerhub.governance.feedback import FeedbackService

C = TypeVar("C", bound=Command[Any])  # pyright: ignore[reportUnknownVariableType]
R = TypeVar("R")  # pyright: ignore[reportUnknownVariableType]


def _validate_wire_json_value(value: object) -> None:
    """Reject non-JSON values without coercing caller input."""

    if value is None or type(value) in {str, int, bool}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return
    if isinstance(value, list):
        for item in cast(list[object], value):
            _validate_wire_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in cast(dict[object, object], value).items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _validate_wire_json_value(item)
        return
    raise ValueError(
        f"unsupported JSON value type: {type(value).__name__}"
    )


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


class _ResourceOwnershipError(Exception):
    """Signal a client/resource ownership mismatch to ``submit``."""


@dataclass(frozen=True)
class CommandDescriptor(Generic[C, R]):  # pyright: ignore[reportUntypedBaseClass]
    method: str
    mutability: Mutability
    accepted_scope: ScopeKind
    idempotency: IdempotencyPolicy
    decode: Callable[[CommandEnvelope], C]  # pyright: ignore[reportInvalidTypeForm]
    handle: Callable[[C, RequestContext], R]  # pyright: ignore[reportInvalidTypeForm]
    encode_result: Callable[[R], Mapping[str, JsonValue]]  # pyright: ignore[reportInvalidTypeForm]
    availability: CommandAvailability
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class _TerminalCloseResult:
    duty_lease: DutyLeaseSnapshot
    session_close_status: str
    session: RoomSessionSnapshot | None = None
    session_close_reason: str | None = None


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


def reconstruct_envelope(cmd: Command[Any]) -> CommandEnvelope:  # pyright: ignore[reportUnknownParameterType]
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
            _validate_wire_json_value(requirement)
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


class ApplicationAPI:
    def __init__(
        self,
        *,
        workflows: ApplicationWorkflows,
        dispatch: DispatchService,
        admission_provider: AdmissionInputsProvider | None = None,
        consensus: ConsensusService | None = None,
        task: TaskService | None = None, lesson: LessonService | None = None,
        lesson_broker: GovernanceBroker | None = None,
        room: RoomsService | None = None,
        duty: DutyLeaseCoordinator | None = None,
        terminal_duty: TerminalDutyService | None = None,
        room_session: RoomParticipationCoordinator | None = None,
        arbiter: ArbiterReviewCoordinator | None = None,
        peer_registry: PeerRegistryService | None = None,
        role_assignment: RoleAssignmentService | None = None,
        feedback: FeedbackService | None = None,
    ) -> None:
        self._workflows = workflows
        self._dispatch = dispatch
        self._admission_provider = admission_provider
        self._consensus = consensus
        self._registry: dict[str, CommandDescriptor[Any, Any]] = {}  # pyright: ignore[reportInvalidTypeArguments]
        
        self._register_builtins()
        if consensus is not None:
            self._register_consensus(consensus, lesson_broker, arbiter)
        if task is not None: self._register_task(task)
        if lesson is not None and lesson_broker is not None:
            self._register_lesson(lesson, lesson_broker, room)
        if room is not None: self._register_room(room)
        if duty is not None and terminal_duty is not None:
            self._register_duty(duty, terminal_duty, room_session)
        if room_session is not None: self._register_room_session(room_session)
        if peer_registry is not None: self._register_peer_registry(peer_registry)
        if role_assignment is not None:
            self._register_role_assignment(role_assignment)
        if feedback is not None:
            self._register_feedback(feedback)

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

    def _register_consensus(
        self,
        service: ConsensusService,
        broker: GovernanceBroker | None,
        arbiter: ArbiterReviewCoordinator | None,
    ) -> None:
        def string_tuple(params: Mapping[str, JsonValue], name: str) -> tuple[str, ...]:
            value = params[name]
            if not isinstance(value, (list, tuple)) or not all(
                isinstance(item, str) for item in value
            ):
                raise ValueError(f"{name} must be a sequence of strings")
            return tuple(cast(str, item) for item in value)

        def decode_propose(e: CommandEnvelope) -> ConsensusProposeCommand:
            p = e.params
            return ConsensusProposeCommand(
                self._submission(e), str(p["round_id"]), str(p["title"]),
                str(p["question"]), str(p["body"]), str(p["proposer_id"]),
                string_tuple(p, "required_participants"),
                string_tuple(p, "eligible_participants"), str(p["risk"]),
                str(p["source_hash"]),
            )
        def decode_vote(e: CommandEnvelope) -> ConsensusVoteCommand:
            p = e.params
            return ConsensusVoteCommand(self._submission(e), str(p["round_id"]), str(p["actor_id"]), str(p["choice"]))
        def decode_check(e: CommandEnvelope) -> ConsensusCheckCommand:
            return ConsensusCheckCommand(self._submission(e), str(e.params["round_id"]))
        self.register(CommandDescriptor("consensus.round.propose", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, decode_propose, lambda c, _: service.propose(round_id=c.round_id, title=c.title, question=c.question, body=c.body, proposer_id=c.proposer_id, required_participants=c.required_participants, eligible_participants=c.eligible_participants, risk=c.risk, source_hash=c.source_hash), self._receipt, CommandAvailability.AVAILABLE))
        self.register(CommandDescriptor("consensus.vote.cast", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, decode_vote, lambda c, _: service.cast_vote(c.round_id, actor_id=c.actor_id, choice=c.choice), self._receipt, CommandAvailability.AVAILABLE))
        self.register(CommandDescriptor("consensus.round.read", Mutability.READ_ONLY, ScopeKind.ANY, IdempotencyPolicy.READ_ONLY, decode_check, lambda c, _: service.get_target(c.round_id), lambda r: {"target_id": r.target_id, "revision": r.revision, "state": r.state}, CommandAvailability.AVAILABLE))
        def decode_sweep(e: CommandEnvelope) -> ConsensusSweepCommand:
            p=e.params; reason=p["reason"]; revision=p["expected_revision"]
            if not isinstance(p["round_id"],str) or not isinstance(reason,str): raise ValueError("round_id and reason must be strings")
            if revision is not None and (not isinstance(revision,int) or isinstance(revision,bool)): raise ValueError("expected_revision must be an integer or null")
            return ConsensusSweepCommand(self._submission(e),p["round_id"],reason,revision)
        self.register(CommandDescriptor("consensus.round.sweep", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, decode_sweep, lambda c,_:service.mark_timeout(c.round_id,c.reason,c.expected_revision), self._receipt, CommandAvailability.AVAILABLE))
        if broker is not None:
            def decode_proposal_list(e: CommandEnvelope) -> ProposalListCommand:
                return ProposalListCommand(self._submission(e))

            def encode_proposals(results: Sequence[Any]) -> Mapping[str, JsonValue]:
                proposals = [
                    {
                        "target_id": result.target_id,
                        "revision": result.revision,
                        "state": result.state,
                    }
                    for result in results
                ]
                return {"proposals": cast(JsonValue, proposals)}

            self.register(CommandDescriptor(
                "governance.proposal.list",
                Mutability.READ_ONLY,
                ScopeKind.ANY,
                IdempotencyPolicy.READ_ONLY,
                decode_proposal_list,
                lambda _command, _context: broker.list_targets(
                    "consensus-round", None
                ),
                encode_proposals,
                CommandAvailability.AVAILABLE,
            ))
            
        if arbiter is not None:
            def decode_arbiter_review(e: CommandEnvelope) -> ArbiterReviewCommand:
                return ArbiterReviewCommand(self._submission(e), str(e.params["round_id"]))
            self.register(CommandDescriptor(
                "consensus.arbiter.review",
                Mutability.MUTATING,
                ScopeKind.ANY,
                IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
                decode_arbiter_review,
                lambda c, _: arbiter.review(c.round_id),
                lambda r: dict(r),
                CommandAvailability.AVAILABLE,
            ))

    def _register_task(self, s: TaskService) -> None:
        def text(p: Mapping[str, JsonValue], n: str) -> str:
            if not isinstance(p[n], str): raise ValueError(f"{n} must be a string")
            return cast(str, p[n])
        def integer(p: Mapping[str, JsonValue], n: str) -> int | None:
            if p[n] is not None and (not isinstance(p[n], int) or isinstance(p[n], bool)): raise ValueError(f"{n} must be an integer or null")
            return cast(int | None, p[n])
        def strings(p: Mapping[str, JsonValue], n: str) -> tuple[str, ...]:
            v=p[n]
            if not isinstance(v,(list,tuple)) or not all(isinstance(x,str) for x in v): raise ValueError(f"{n} must be a sequence of strings")
            return tuple(cast(str,x) for x in v)
        def checkpoint(e: CommandEnvelope) -> TaskCheckpointCommand:
            p=e.params; return TaskCheckpointCommand(self._submission(e),text(p,"task_id"),text(p,"actor_id"),text(p,"checkpoint_id"),text(p,"stage"),text(p,"request_id"),text(p,"attempt_id"),None if p["resume_token_ref"] is None else text(p,"resume_token_ref"),strings(p,"completed_units"),strings(p,"remaining_units"),integer(p,"expected_revision"))
        def status(e: CommandEnvelope) -> TaskStatusCommand:
            return TaskStatusCommand(self._submission(e), text(e.params,"task_id"))
        def failover(e: CommandEnvelope) -> TaskFailoverCommand:
            p=e.params; return TaskFailoverCommand(self._submission(e),text(p,"task_id"),text(p,"to_actor_id"),text(p,"reason"),integer(p,"expected_revision"))
        self.register(CommandDescriptor("coordination.task.checkpoint", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, checkpoint, lambda c,_: s.checkpoint(task_id=c.task_id,actor_id=c.actor_id,checkpoint_id=c.checkpoint_id,stage=c.stage,request_id=c.request_id,attempt_id=c.attempt_id,resume_token_ref=c.resume_token_ref,completed_units=c.completed_units,remaining_units=c.remaining_units,expected_revision=c.expected_revision), self._receipt, CommandAvailability.AVAILABLE))
        self.register(CommandDescriptor("coordination.task.status", Mutability.READ_ONLY, ScopeKind.ANY, IdempotencyPolicy.READ_ONLY, status, lambda c,_: s.get_target(c.task_id), lambda r: {"target_id":r.target_id,"revision":r.revision,"state":r.state}, CommandAvailability.AVAILABLE))
        self.register(CommandDescriptor("coordination.task.failover", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, failover, lambda c,_: s.request_failover(c.task_id,to_actor_id=c.to_actor_id,reason=c.reason,expected_revision=c.expected_revision), self._receipt, CommandAvailability.AVAILABLE))
        def approval(e: CommandEnvelope) -> ApprovalRequestCommand:
            p=e.params
            vals: list[str] = []
            for n in ("task_id","requester_id","approval_id","approver_id"):
                value = p[n]
                if not isinstance(value,str): raise ValueError(f"{n} must be a string")
                vals.append(value)
            return ApprovalRequestCommand(self._submission(e),*vals)
        self.register(CommandDescriptor("governance.approval.request", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, approval, lambda c,_: s.request_approval(c.task_id,requester_id=c.requester_id,approval_id=c.approval_id,approver_id=c.approver_id), self._receipt, CommandAvailability.AVAILABLE))

    def _register_lesson(
        self,
        s: LessonService,
        broker: GovernanceBroker,
        room: RoomsService | None,
    ) -> None:
        def text(p: Mapping[str, JsonValue], n: str) -> str:
            if not isinstance(p[n], str): raise ValueError(f"{n} must be a string")
            return cast(str,p[n])
        def integer(p: Mapping[str, JsonValue], n: str) -> int | None:
            if p[n] is not None and (not isinstance(p[n],int) or isinstance(p[n],bool)): raise ValueError(f"{n} must be an integer or null")
            return cast(int|None,p[n])
        def strings(p: Mapping[str, JsonValue], n: str) -> tuple[str,...]:
            v=p[n]
            if not isinstance(v,(list,tuple)) or not all(isinstance(x,str) for x in v): raise ValueError(f"{n} must be a sequence of strings")
            return tuple(cast(str,x) for x in v)
        def propose(e: CommandEnvelope) -> LessonProposeCommand:
            p=e.params; return LessonProposeCommand(self._submission(e),text(p,"lesson_id"),text(p,"title"),text(p,"rule"),text(p,"category"),text(p,"severity"),text(p,"proposer_id"),strings(p,"affected_peers"),text(p,"scope_kind"),None if p["workspace_id"] is None else text(p,"workspace_id"))
        def activate(e: CommandEnvelope) -> LessonActivateCommand:
            p=e.params; return LessonActivateCommand(self._submission(e),text(p,"lesson_id"),text(p,"actor_id"),integer(p,"expected_revision"))
        def retire(e: CommandEnvelope) -> LessonRetireCommand:
            p=e.params; return LessonRetireCommand(self._submission(e),text(p,"lesson_id"),text(p,"actor_id"),text(p,"reason"),integer(p,"expected_revision"))
        self.register(CommandDescriptor("governance.lesson.propose", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, propose, lambda c,_:s.propose(lesson_id=c.lesson_id,title=c.title,rule=c.rule,category=c.category,severity=c.severity,proposer_id=c.proposer_id,affected_peers=c.affected_peers,scope_kind=c.scope_kind,workspace_id=c.workspace_id), self._receipt, CommandAvailability.AVAILABLE))
        self.register(CommandDescriptor("governance.lesson.activate", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, activate, lambda c,_:s.activate(c.lesson_id,actor_id=c.actor_id,expected_revision=c.expected_revision), self._receipt, CommandAvailability.AVAILABLE))
        self.register(CommandDescriptor("governance.lesson.retire", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, retire, lambda c,_:s.retire(c.lesson_id,actor_id=c.actor_id,reason=c.reason,expected_revision=c.expected_revision), self._receipt, CommandAvailability.AVAILABLE))
        def lessons_list(e: CommandEnvelope) -> LessonsListCommand:
            value=e.params["scope"]
            if value is not None and not isinstance(value,str): raise ValueError("scope must be a string or null")
            return LessonsListCommand(self._submission(e),value)
        def encode_lessons(results: Sequence[Any]) -> Mapping[str, JsonValue]:
            lessons = [{"target_id": r.target_id, "revision": r.revision, "state": r.state} for r in results]
            return {"lessons": cast(JsonValue, lessons)}
        self.register(CommandDescriptor("governance.lesson.list", Mutability.READ_ONLY, ScopeKind.ANY, IdempotencyPolicy.READ_ONLY, lessons_list, lambda c,_:list_active_lessons(broker,c.scope), encode_lessons, CommandAvailability.AVAILABLE))
        if room is not None:
            coordinator = LessonBroadcastCoordinator(
                broker=broker,
                lessons=s,
                rooms=room,
            )
            def broadcast(e: CommandEnvelope) -> LessonBroadcastCommand:
                return LessonBroadcastCommand(
                    self._submission(e),
                    text(e.params, "lesson_id"),
                    text(e.params, "room_id"),
                    text(e.params, "sender_instance_id"),
                    text(e.params, "sender_profile_id"),
                )
            def encode_broadcast(
                result: LessonBroadcastResult,
            ) -> Mapping[str, JsonValue]:
                return {
                    "campaign_id": result.campaign_id,
                    "campaign_target_id": result.campaign_target_id,
                    "lesson_id": result.lesson_id,
                    "room_id": result.room_id,
                    "recipient_profile_ids": result.recipient_profile_ids,
                    "inbox_message_target_ids": result.inbox_message_target_ids,
                    "delivery_target_ids": result.delivery_target_ids,
                }
            self.register(CommandDescriptor(
                "coordination.lesson.broadcast",
                Mutability.MUTATING,
                ScopeKind.ANY,
                IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
                broadcast,
                lambda c, _: coordinator.broadcast(
                    lesson_id=c.lesson_id,
                    room_id=c.room_id,
                    sender_instance_id=c.sender_instance_id,
                    sender_profile_id=c.sender_profile_id,
                    created_at=c.submission.client_timestamp,
                ),
                encode_broadcast,
                CommandAvailability.AVAILABLE,
            ))

    def _register_room(self, s: RoomsService) -> None:
        def text(e: CommandEnvelope, n: str) -> str:
            v=e.params[n]
            if not isinstance(v,str): raise ValueError(f"{n} must be a string")
            return v
        def topic(e: CommandEnvelope) -> NewTopicCommand:
            return NewTopicCommand(self._submission(e),text(e,"thread_id"),text(e,"room_id"),text(e,"subject"),text(e,"creator_id"))
        def append(e: CommandEnvelope) -> ThreadAppendCommand:
            return ThreadAppendCommand(
                self._submission(e),
                text(e, "message_id"),
                text(e, "room_id"),
                text(e, "thread_id"),
                text(e, "author_id"),
                text(e, "body"),
            )
        def optional_text(e: CommandEnvelope, n: str) -> str | None:
            value = e.params[n]
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{n} must be a string or null")
            return value
        def boolean(e: CommandEnvelope, n: str) -> bool:
            value = e.params[n]
            if not isinstance(value, bool):
                raise ValueError(f"{n} must be a boolean")
            return value
        def integer(e: CommandEnvelope, n: str) -> int:
            value = e.params[n]
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{n} must be an integer")
            return value
        def send(e: CommandEnvelope) -> MessageSendCommand:
            return MessageSendCommand(
                self._submission(e),
                text(e, "room_id"),
                text(e, "sender_instance_id"),
                text(e, "sender_profile_id"),
                text(e, "recipient_instance_id"),
                text(e, "recipient_profile_id"),
                text(e, "body"),
                text(e, "message_type"),
                optional_text(e, "thread_ref"),
                optional_text(e, "resource_ref"),
                optional_text(e, "correlation_id"),
            )
        def check_inbox(e: CommandEnvelope) -> MessageCheckCommand:
            return MessageCheckCommand(
                self._submission(e),
                text(e, "room_id"),
                text(e, "caller_instance_id"),
                text(e, "caller_profile_id"),
                boolean(e, "include_read"),
            )
        def mark_read(e: CommandEnvelope) -> MessageMarkReadCommand:
            return MessageMarkReadCommand(
                self._submission(e),
                text(e, "room_id"),
                text(e, "recipient_instance_id"),
                text(e, "recipient_profile_id"),
                integer(e, "up_through_sequence"),
            )
        def promote(e: CommandEnvelope) -> ThreadPromoteCommand:
            return ThreadPromoteCommand(
                self._submission(e),
                text(e, "message_id"),
                text(e, "room_id"),
                text(e, "thread_id"),
                text(e, "actor_id"),
            )
        def encode_inbox_messages(
            results: Sequence[Any],
        ) -> Mapping[str, JsonValue]:
            messages = [
                {
                    "target_id": result.target_id,
                    "revision": result.revision,
                    "state": result.state,
                }
                for result in results
            ]
            return {"messages": cast(JsonValue, messages)}
        def append_handoff(e: CommandEnvelope) -> AppendHandoffCommand:
            section = text(e, "section")
            if section not in {
                "RECENT_COMPLETED",
                "PENDING_ISSUES",
                "KEY_DECISIONS",
                "CONSENSUS_HISTORY",
                "ACTIVE_THREADS",
            }:
                raise ValueError(
                    "section must be a supported append-only handoff section"
                )
            return AppendHandoffCommand(
                self._submission(e),
                text(e, "room_id"),
                section,
                text(e, "text"),
                text(e, "actor_id"),
            )
        def checkpoint(e: CommandEnvelope) -> ContinuityCheckpointCommand:
            return ContinuityCheckpointCommand(
                self._submission(e),
                text(e, "room_id"),
                text(e, "actor_id"),
            )
        def context_fill(e: CommandEnvelope) -> ContextFillCommand:
            session_id = text(e, "session_id")
            if not session_id:
                raise ValueError("session_id must be a nonempty string")
            raw_sections = e.params["sections"]
            if raw_sections is None:
                sections: tuple[str, ...] | None = None
            elif isinstance(raw_sections, (list, tuple)) and all(
                isinstance(section, str) for section in raw_sections
            ):
                sections = tuple(cast(str, section) for section in raw_sections)
            else:
                raise ValueError("sections must be a sequence of strings or null")
            if sections is not None:
                if not sections:
                    raise ValueError("sections must not be empty")
                valid_sections = {
                    "GOAL",
                    "RECENT_COMPLETED",
                    "PENDING_ISSUES",
                    "KEY_DECISIONS",
                    "CONSENSUS_HISTORY",
                    "ACTIVE_THREADS",
                }
                if any(section not in valid_sections for section in sections):
                    raise ValueError("sections contains an unknown section name")
                if len(set(sections)) != len(sections):
                    raise ValueError("sections must not contain duplicates")
            return ContextFillCommand(
                self._submission(e),
                text(e, "room_id"),
                session_id,
                sections,
            )
        def encode_checkpoint(
            result: Mapping[str, JsonValue],
        ) -> Mapping[str, JsonValue]:
            return result
        def react(e: CommandEnvelope) -> ThreadReactCommand:
            action = text(e, "action")
            if action not in {"ADD", "REMOVE"}:
                raise ValueError("action must be ADD or REMOVE")
            return ThreadReactCommand(
                self._submission(e),
                text(e, "message_id"),
                text(e, "room_id"),
                text(e, "actor_instance_id"),
                text(e, "actor_profile_id"),
                text(e, "reaction_type"),
                action,
            )
        def record_reaction(command: ThreadReactCommand):
            operation = (
                s.react if command.action == "ADD" else s.unreact
            )
            return operation(
                message_id=command.message_id,
                room_id=command.room_id,
                actor_instance_id=command.actor_instance_id,
                actor_profile_id=command.actor_profile_id,
                reaction_type=command.reaction_type,
            )
        def clear(e: CommandEnvelope) -> ClearRoomCommand:
            return ClearRoomCommand(self._submission(e),text(e,"old_room_id"),text(e,"new_room_id"),text(e,"subject"),text(e,"actor_id"))
        self.register(CommandDescriptor("coordination.topic.create", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, topic, lambda c,_:s.create_thread(thread_id=c.thread_id,room_id=c.room_id,subject=c.subject,creator_id=c.creator_id), self._receipt, CommandAvailability.AVAILABLE))
        self.register(CommandDescriptor(
            "coordination.thread.append",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            append,
            lambda c, _: s.append_message(
                message_id=c.message_id,
                room_id=c.room_id,
                thread_id=c.thread_id,
                author_id=c.author_id,
                body=c.body,
            ),
            self._receipt,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "coordination.message.send",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            send,
            lambda c, _: s.send_message(
                room_id=c.room_id,
                sender_instance_id=c.sender_instance_id,
                sender_profile_id=c.sender_profile_id,
                recipient_instance_id=c.recipient_instance_id,
                recipient_profile_id=c.recipient_profile_id,
                body=c.body,
                message_type=c.message_type,
                thread_ref=c.thread_ref,
                resource_ref=c.resource_ref,
                correlation_id=c.correlation_id,
            ),
            self._receipt,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "coordination.message.check",
            Mutability.READ_ONLY,
            ScopeKind.ANY,
            IdempotencyPolicy.READ_ONLY,
            check_inbox,
            lambda c, _: s.check_inbox(
                room_id=c.room_id,
                caller_instance_id=c.caller_instance_id,
                caller_profile_id=c.caller_profile_id,
                include_read=c.include_read,
            ),
            encode_inbox_messages,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "coordination.message.mark_read",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            mark_read,
            lambda c, _: s.mark_read(
                room_id=c.room_id,
                recipient_instance_id=c.recipient_instance_id,
                recipient_profile_id=c.recipient_profile_id,
                up_through_sequence=c.up_through_sequence,
            ),
            self._receipt,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "coordination.thread.promote",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            promote,
            lambda c, _: s.promote_message(
                message_id=c.message_id,
                room_id=c.room_id,
                thread_id=c.thread_id,
                actor_id=c.actor_id,
            ),
            self._receipt,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "coordination.thread.react",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            react,
            lambda c, _: record_reaction(c),
            self._receipt,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "coordination.handoff.append",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            append_handoff,
            lambda c, _: s.append_handoff_note(
                room_id=c.room_id,
                section=c.section,
                text=c.text,
                actor_id=c.actor_id,
            ),
            self._receipt,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "coordination.checkpoint.create",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            checkpoint,
            lambda c, _: s.checkpoint(
                c.room_id,
                actor_id=c.actor_id,
                idempotency_key=c.submission.idempotency_key,
                idempotency_scope=c.submission.client_id,
            ),
            encode_checkpoint,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "coordination.context.fill",
            Mutability.READ_ONLY,
            ScopeKind.ANY,
            IdempotencyPolicy.READ_ONLY,
            context_fill,
            lambda c, _: s.context_fill(
                c.room_id,
                session_id=c.session_id,
                sections=c.sections,
            ),
            encode_checkpoint,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor("coordination.room.clear", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, clear, lambda c,_:s.clear_room(c.old_room_id,new_room_id=c.new_room_id,subject=c.subject,actor_id=c.actor_id), self._receipt, CommandAvailability.AVAILABLE))

    def _register_duty(
        self,
        d: DutyLeaseCoordinator,
        t: TerminalDutyService,
        room_session: RoomParticipationCoordinator | None,
    ) -> None:
        def text(e: CommandEnvelope,n: str) -> str:
            v=e.params[n]
            if not isinstance(v,str): raise ValueError(f"{n} must be a string")
            return v
        def integer(e: CommandEnvelope,n: str) -> int:
            v=e.params[n]
            if not isinstance(v,int) or isinstance(v,bool): raise ValueError(f"{n} must be an integer")
            return v
        def boolean(e: CommandEnvelope, n: str) -> bool:
            v = e.params.get(n, False)
            if not isinstance(v, bool):
                raise ValueError(f"{n} must be a boolean")
            return v
        def optional_text(e: CommandEnvelope, n: str) -> str:
            v = e.params.get(n, "")
            if not isinstance(v, str):
                raise ValueError(f"{n} must be a string")
            return v
        def optional_integer(e: CommandEnvelope, n: str) -> int:
            v = e.params.get(n, 0)
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError(f"{n} must be an integer")
            return v
        def owner(c: LeaderClaimCommand | LeaderYieldCommand | TerminalHeartbeatCommand) -> DutyOwnerIdentity:
            return DutyOwnerIdentity(c.instance_id,c.profile_id)
        def claim(e: CommandEnvelope) -> LeaderClaimCommand:
            return LeaderClaimCommand(self._submission(e),text(e,"room_id"),text(e,"instance_id"),text(e,"profile_id"),text(e,"owner_principal_id"),integer(e,"authority_epoch"))
        def yielding(e: CommandEnvelope) -> LeaderYieldCommand:
            return LeaderYieldCommand(self._submission(e),text(e,"lease_id"),text(e,"room_id"),text(e,"instance_id"),text(e,"profile_id"),integer(e,"term"),integer(e,"authority_epoch"))
        def handoff(e: CommandEnvelope) -> TerminalHandoffCommand:
            return TerminalHandoffCommand(self._submission(e),text(e,"current_lease_id"),text(e,"room_id"),text(e,"current_instance_id"),text(e,"current_profile_id"),integer(e,"term"),integer(e,"authority_epoch"),text(e,"new_instance_id"),text(e,"new_profile_id"),text(e,"new_owner_principal_id"),integer(e,"new_authority_epoch"))
        def heartbeat(e: CommandEnvelope) -> TerminalHeartbeatCommand:
            return TerminalHeartbeatCommand(self._submission(e),text(e,"lease_id"),text(e,"room_id"),text(e,"instance_id"),text(e,"profile_id"),integer(e,"term"),integer(e,"authority_epoch"))
        def close(e: CommandEnvelope) -> TerminalCloseCommand:
            command = TerminalCloseCommand(
                self._submission(e),
                text(e,"lease_id"),
                text(e,"room_id"),
                text(e,"instance_id"),
                text(e,"profile_id"),
                integer(e,"term"),
                integer(e,"authority_epoch"),
                boolean(e, "close_session"),
                optional_text(e, "session_id"),
                optional_integer(e, "session_generation"),
                optional_text(e, "workspace_scope_id"),
                optional_text(e, "actor_principal_id"),
            )
            if command.close_session and (
                not command.session_id
                or command.session_generation < 1
                or not command.workspace_scope_id
                or not command.actor_principal_id
            ):
                raise ValueError(
                    "close_session requires session_id, a positive "
                    "session_generation, workspace_scope_id, and "
                    "actor_principal_id"
                )
            return command
        def sweep(e: CommandEnvelope) -> TerminalDutySweepCommand:
            return TerminalDutySweepCommand(
                self._submission(e),
                text(e, "role"),
                text(e, "recovery_actor_principal_id"),
                text(e, "trigger"),
                text(e, "evidence_digest"),
                text(e, "policy_id"),
                text(e, "policy_revision"),
            )
        def enc(r: Any) -> Mapping[str, JsonValue]: return {"lease_id":r.lease_id,"room_id":r.room_id,"role":r.role,"state":r.state.value,"term":r.term,"authority_epoch":r.authority_epoch}
        def close_terminal(
            command: TerminalCloseCommand,
        ) -> _TerminalCloseResult:
            duty_lease = t.close_terminal_duty(
                command.lease_id,
                command.room_id,
                DutyOwnerIdentity(command.instance_id, command.profile_id),
                command.term,
                command.authority_epoch,
            )
            if not command.close_session:
                return _TerminalCloseResult(duty_lease, "not_requested")
            if room_session is None:
                return _TerminalCloseResult(
                    duty_lease,
                    "failed",
                    session_close_reason=(
                        "room participation coordinator is unavailable"
                    ),
                )
            try:
                session = room_session.end_session(
                    RoomSessionEndRequest(
                        session_id=command.session_id,
                        session_generation=command.session_generation,
                        workspace_scope_id=command.workspace_scope_id,
                        room_id=command.room_id,
                        actor_principal_id=command.actor_principal_id,
                        owner=DutyOwnerIdentity(
                            command.instance_id, command.profile_id
                        ),
                    )
                )
            except Exception as exc:
                return _TerminalCloseResult(
                    duty_lease,
                    "failed",
                    session_close_reason=f"{type(exc).__name__}: {exc}",
                )
            return _TerminalCloseResult(duty_lease, "ok", session)
        def enc_close(
            result: _TerminalCloseResult,
        ) -> Mapping[str, JsonValue]:
            duty_close: dict[str, JsonValue] = {
                "status": "ok",
                "lease": dict(enc(result.duty_lease)),
            }
            session_close: dict[str, JsonValue] = {
                "status": result.session_close_status,
            }
            if result.session is not None:
                session_close["session_id"] = result.session.session_id
                session_close["session_generation"] = (
                    result.session.session_generation
                )
                session_close["state"] = result.session.state.value
            if result.session_close_reason is not None:
                session_close["reason"] = result.session_close_reason
            return {
                "duty_close": duty_close,
                "session_close": session_close,
            }
        def enc_sweep(
            leases: tuple[DutyLeaseSnapshot, ...],
        ) -> Mapping[str, JsonValue]:
            encoded = [dict(enc(lease)) for lease in leases]
            return {
                "expired_count": len(encoded),
                "leases": cast(JsonValue, encoded),
            }
        self.register(CommandDescriptor("routing.leadership.claim", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, claim, lambda c,_:d.create_lease(DutyLeaseCreateRequest(c.room_id,"leader",DutyOwnerIdentity(c.instance_id,c.profile_id),c.owner_principal_id,60000,c.authority_epoch)), enc, CommandAvailability.AVAILABLE))
        self.register(CommandDescriptor("routing.leadership.yield", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, yielding, lambda c,_:d.close_lease(DutyLeaseCloseRequest(c.lease_id,c.room_id,"leader",owner(c),c.term,c.authority_epoch)), enc, CommandAvailability.AVAILABLE))
        self.register(CommandDescriptor("coordination.terminal.handoff", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, handoff, lambda c,_:t.handoff_terminal_duty(c.current_lease_id,c.room_id,DutyOwnerIdentity(c.current_instance_id,c.current_profile_id),c.term,c.authority_epoch,DutyOwnerIdentity(c.new_instance_id,c.new_profile_id),c.new_owner_principal_id,c.new_authority_epoch), enc, CommandAvailability.AVAILABLE))
        self.register(CommandDescriptor("coordination.terminal.heartbeat", Mutability.MUTATING, ScopeKind.ANY, IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED, heartbeat, lambda c,_:t.send_heartbeat(c.lease_id,c.room_id,owner(c),c.term,c.authority_epoch), enc, CommandAvailability.AVAILABLE))
        self.register(CommandDescriptor(
            "coordination.terminal.close",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            close,
            lambda c, _: close_terminal(c),
            enc_close,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "coordination.terminal.duty_sweep",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            sweep,
            lambda c, _: d.sweep_expired_leases(
                c.role,
                recovery_actor_principal_id=(
                    c.recovery_actor_principal_id
                ),
                trigger=c.trigger,
                evidence_digest=c.evidence_digest,
                policy_id=c.policy_id,
                policy_revision=c.policy_revision,
            ),
            enc_sweep,
            CommandAvailability.AVAILABLE,
        ))

    def _register_room_session(
        self, coordinator: RoomParticipationCoordinator
    ) -> None:
        def text(envelope: CommandEnvelope, name: str) -> str:
            value = envelope.params[name]
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
            return value

        def integer(envelope: CommandEnvelope, name: str) -> int:
            value = envelope.params[name]
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be an integer")
            return value

        def owner(
            command: SessionOpenCommand | SessionCloseCommand,
        ) -> DutyOwnerIdentity:
            return DutyOwnerIdentity(command.instance_id, command.profile_id)

        def decode_open(envelope: CommandEnvelope) -> SessionOpenCommand:
            return SessionOpenCommand(
                self._submission(envelope),
                text(envelope, "workspace_scope_id"),
                text(envelope, "room_id"),
                text(envelope, "actor_principal_id"),
                text(envelope, "instance_id"),
                text(envelope, "profile_id"),
                text(envelope, "session_fingerprint"),
                integer(envelope, "heartbeat_timeout_ms"),
            )

        def decode_close(envelope: CommandEnvelope) -> SessionCloseCommand:
            return SessionCloseCommand(
                self._submission(envelope),
                text(envelope, "session_id"),
                integer(envelope, "session_generation"),
                text(envelope, "workspace_scope_id"),
                text(envelope, "room_id"),
                text(envelope, "actor_principal_id"),
                text(envelope, "instance_id"),
                text(envelope, "profile_id"),
            )

        def decode_heartbeat(
            envelope: CommandEnvelope,
        ) -> SessionHeartbeatCommand:
            return SessionHeartbeatCommand(
                self._submission(envelope),
                text(envelope, "session_id"),
                integer(envelope, "session_generation"),
                text(envelope, "workspace_scope_id"),
                text(envelope, "room_id"),
                text(envelope, "actor_principal_id"),
                text(envelope, "instance_id"),
                text(envelope, "profile_id"),
                integer(envelope, "heartbeat_timeout_ms"),
            )

        def encode_snapshot(
            snapshot: RoomSessionSnapshot,
        ) -> Mapping[str, JsonValue]:
            return {
                "session_id": snapshot.session_id,
                "workspace_scope_id": snapshot.workspace_scope_id,
                "room_id": snapshot.room_id,
                "actor_principal_id": snapshot.actor_principal_id,
                "owner": {
                    "instance_id": snapshot.owner.instance_id,
                    "profile_id": snapshot.owner.profile_id,
                },
                "session_fingerprint": snapshot.session_fingerprint,
                "session_generation": snapshot.session_generation,
                "resume_parent_session_id": snapshot.resume_parent_session_id,
                "state": snapshot.state.value,
                "heartbeat_expires_at": snapshot.heartbeat_expires_at,
                "created_at": snapshot.created_at,
                "updated_at": snapshot.updated_at,
            }

        self.register(CommandDescriptor(
            "coordination.session.open",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            decode_open,
            lambda command, _: coordinator.open_session(
                RoomSessionOpenRequest(
                    workspace_scope_id=command.workspace_scope_id,
                    room_id=command.room_id,
                    actor_principal_id=command.actor_principal_id,
                    owner=owner(command),
                    session_fingerprint=command.session_fingerprint,
                    heartbeat_timeout_ms=command.heartbeat_timeout_ms,
                )
            ),
            encode_snapshot,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "coordination.session.close",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            decode_close,
            lambda command, _: coordinator.end_session(
                RoomSessionEndRequest(
                    session_id=command.session_id,
                    session_generation=command.session_generation,
                    workspace_scope_id=command.workspace_scope_id,
                    room_id=command.room_id,
                    actor_principal_id=command.actor_principal_id,
                    owner=owner(command),
                )
            ),
            encode_snapshot,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "coordination.session.heartbeat",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            decode_heartbeat,
            lambda command, _: coordinator.heartbeat(
                RoomSessionHeartbeatRequest(
                    session_id=command.session_id,
                    session_generation=command.session_generation,
                    workspace_scope_id=command.workspace_scope_id,
                    room_id=command.room_id,
                    actor_principal_id=command.actor_principal_id,
                    owner=owner(command),
                ),
                heartbeat_timeout_ms=command.heartbeat_timeout_ms,
            ),
            encode_snapshot,
            CommandAvailability.AVAILABLE,
        ))

    def _register_peer_registry(self, service: PeerRegistryService) -> None:
        def optional_text(envelope: CommandEnvelope, name: str) -> str | None:
            value = envelope.params.get(name)
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string or null")
            return value

        def decode_register(envelope: CommandEnvelope) -> RegisterNodeCommand:
            p = envelope.params
            node_id = p["node_id"]
            peer_kind = p["peer_kind"]
            node_type = p.get("node_type", "agent")
            tier = p.get("tier", 4)
            actor_id = p["actor_id"]
            if not isinstance(node_id, str) or not isinstance(peer_kind, str):
                raise ValueError("node_id and peer_kind must be strings")
            if not isinstance(node_type, str):
                raise ValueError("node_type must be a string")
            if not isinstance(tier, int) or isinstance(tier, bool):
                raise ValueError("tier must be an integer")
            if not isinstance(actor_id, str):
                raise ValueError("actor_id must be a string")
            return RegisterNodeCommand(
                self._submission(envelope),
                node_id,
                peer_kind,
                optional_text(envelope, "profile_id"),
                tier,
                node_type,
                actor_id,
            )

        def decode_list(envelope: CommandEnvelope) -> ListNodesCommand:
            return ListNodesCommand(self._submission(envelope))

        def encode_nodes(results: Sequence[Any]) -> Mapping[str, JsonValue]:
            nodes = [
                {
                    "target_id": result.target_id,
                    "revision": result.revision,
                    "state": result.state,
                }
                for result in results
            ]
            return {"nodes": cast(JsonValue, nodes)}

        self.register(CommandDescriptor(
            "configuration.instance.register",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            decode_register,
            lambda c, ctx: service.register_node(
                node_id=c.node_id,
                peer_kind=c.peer_kind,
                profile_id=c.profile_id,
                tier=c.tier,
                node_type=c.node_type,
                actor_id=c.actor_id,
            ),
            self._receipt,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "configuration.instance.list",
            Mutability.READ_ONLY,
            ScopeKind.ANY,
            IdempotencyPolicy.READ_ONLY,
            decode_list,
            lambda c, ctx: service.list_nodes(),
            encode_nodes,
            CommandAvailability.AVAILABLE,
        ))

    def _register_role_assignment(self, service: RoleAssignmentService) -> None:
        def required_text(envelope: CommandEnvelope, name: str) -> str:
            value = envelope.params[name]
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
            return value

        def optional_text(envelope: CommandEnvelope, name: str) -> str | None:
            value = envelope.params.get(name)
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string or null")
            return value

        def decode_assign(envelope: CommandEnvelope) -> AssignRoleCommand:
            return AssignRoleCommand(
                submission=self._submission(envelope),
                role=required_text(envelope, "role"),
                peer_node_id=required_text(envelope, "peer_node_id"),
                actor_id=required_text(envelope, "actor_id"),
            )

        def decode_release(envelope: CommandEnvelope) -> ReleaseRoleCommand:
            return ReleaseRoleCommand(
                submission=self._submission(envelope),
                role=required_text(envelope, "role"),
                actor_id=required_text(envelope, "actor_id"),
                peer_node_id=optional_text(envelope, "peer_node_id"),
            )

        def decode_status(envelope: CommandEnvelope) -> RoleStatusCommand:
            return RoleStatusCommand(self._submission(envelope))

        def encode_roles(results: Sequence[Any]) -> Mapping[str, JsonValue]:
            roles = [
                {
                    "target_id": result.target_id,
                    "revision": result.revision,
                    "state": result.state,
                }
                for result in results
            ]
            return {"roles": cast(JsonValue, roles)}

        def encode_release(result: RoleReleaseResult) -> Mapping[str, JsonValue]:
            receipt = (
                None
                if result.submission is None
                else dict(self._receipt(result.submission))
            )
            target = (
                None
                if result.target is None
                else {
                    "target_id": result.target.target_id,
                    "revision": result.target.revision,
                    "state": result.target.state,
                }
            )
            return {
                "disposition": result.disposition.value,
                "receipt": cast(JsonValue, receipt),
                "target": cast(JsonValue, target),
            }

        self.register(CommandDescriptor(
            "coordination.role.assign",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            decode_assign,
            lambda c, _: service.assign_role(
                role=c.role,
                peer_node_id=c.peer_node_id,
                actor_id=c.actor_id,
            ),
            self._receipt,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "coordination.role.release",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            decode_release,
            lambda c, _: service.release_role(
                role=c.role,
                actor_id=c.actor_id,
                peer_node_id=c.peer_node_id,
            ),
            encode_release,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "coordination.role.status",
            Mutability.READ_ONLY,
            ScopeKind.ANY,
            IdempotencyPolicy.READ_ONLY,
            decode_status,
            lambda c, _: service.list_roles(),
            encode_roles,
            CommandAvailability.AVAILABLE,
        ))

    def _register_feedback(self, service: FeedbackService) -> None:
        def required_text(envelope: CommandEnvelope, name: str) -> str:
            value = envelope.params[name]
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
            return value

        def optional_text(envelope: CommandEnvelope, name: str) -> str | None:
            value = envelope.params.get(name)
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string or null")
            return value

        def decode_add(envelope: CommandEnvelope) -> FeedbackAddCommand:
            detail = envelope.params.get("detail", "")
            if not isinstance(detail, str):
                raise ValueError("detail must be a string")
            return FeedbackAddCommand(
                submission=self._submission(envelope),
                source_peer=required_text(envelope, "source_peer"),
                category=required_text(envelope, "category"),
                severity=required_text(envelope, "severity"),
                title=required_text(envelope, "title"),
                detail=detail,
                actor_id=required_text(envelope, "actor_id"),
            )

        def decode_list(envelope: CommandEnvelope) -> FeedbackListCommand:
            return FeedbackListCommand(self._submission(envelope))

        def decode_resolve(
            envelope: CommandEnvelope,
        ) -> FeedbackResolveCommand:
            return FeedbackResolveCommand(
                submission=self._submission(envelope),
                feedback_id=required_text(envelope, "feedback_id"),
                status=required_text(envelope, "status"),
                actor_id=required_text(envelope, "actor_id"),
                owner=optional_text(envelope, "owner"),
            )

        def encode_feedback(
            results: Sequence[Any],
        ) -> Mapping[str, JsonValue]:
            items = [
                {
                    "target_id": result.target_id,
                    "revision": result.revision,
                    "state": result.state,
                }
                for result in results
            ]
            return {"feedback": cast(JsonValue, items)}

        self.register(CommandDescriptor(
            "governance.feedback.create",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            decode_add,
            lambda c, _: service.add_feedback(
                source_peer=c.source_peer,
                category=c.category,
                severity=c.severity,
                title=c.title,
                detail=c.detail,
                actor_id=c.actor_id,
            ),
            self._receipt,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "governance.feedback.list",
            Mutability.READ_ONLY,
            ScopeKind.ANY,
            IdempotencyPolicy.READ_ONLY,
            decode_list,
            lambda c, _: service.list_feedback(),
            encode_feedback,
            CommandAvailability.AVAILABLE,
        ))
        self.register(CommandDescriptor(
            "governance.feedback.resolve",
            Mutability.MUTATING,
            ScopeKind.ANY,
            IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
            decode_resolve,
            lambda c, _: service.resolve_feedback(
                c.feedback_id,
                status=c.status,
                owner=c.owner,
                actor_id=c.actor_id,
            ),
            self._receipt,
            CommandAvailability.AVAILABLE,
        ))

    def register(self, descriptor: CommandDescriptor[Any, Any]) -> None:  # pyright: ignore[reportInvalidTypeArguments]
        if descriptor.method in self._registry:
            raise ValueError(f"Duplicate command method: {descriptor.method}")
        self._registry[descriptor.method] = descriptor

    def _register_builtins(self) -> None:
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
                required_capability_tier=(
                    payload.required_capability_tier
                ),
                requested_capabilities=tuple(payload.requested_capabilities),
                profile_constraints=freeze_json_mapping(payload.profile_constraints),
                completion_contract=freeze_json_mapping(
                    payload.completion_contract.model_dump(mode="json")
                ),
                session_policy=freeze_json_mapping(payload.session_policy),
            )

        def handle_admit(cmd: AdmitDispatch, caller: RequestContext) -> DispatchAdmissionView:
            if not self._admission_provider:
                raise RuntimeError("admit_request requires AdmissionInputsProvider")

            inputs = self._admission_provider.resolve(cmd, caller)
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

            res = self._workflows.admit_request(
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
            
            # The workflow returns an AdmissionWorkflowResult.
            # dispatch_admission is a tuple: (request, client_binding, idempotency_binding, receipt)
            adm = res.dispatch_admission
            if not adm:
                raise RuntimeError(f"Admission rejected: {res.route.error_code if res.route else 'unknown'}")
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

        avail_admit = CommandAvailability.AVAILABLE if self._admission_provider else CommandAvailability.NOT_BACKED
        reason_admit = None if self._admission_provider else "admission_inputs_provider_missing"

        self.register(CommandDescriptor(
            method="dispatch.admit",
            mutability=Mutability.MUTATING,
            accepted_scope=ScopeKind.ANY,
            idempotency=IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED,
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
        ))

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

        def handle_req_get(cmd: GetDispatchRequest, caller: RequestContext) -> DispatchRequestView:
            req = self._dispatch.get_request(cmd.target_command_id)
            if not req:
                raise KeyError(cmd.target_command_id)

            if req.client_id != caller.client_id:
                raise _ResourceOwnershipError()

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
        ))

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

        def handle_lease_get(cmd: GetDispatchLease, caller: RequestContext) -> DispatchLeaseView:
            lease = self._dispatch.get_lease(cmd.lease_id)
            if not lease:
                raise KeyError(cmd.lease_id)

            request = self._dispatch.get_request(lease.fence.command_id)
            if request is None:
                raise KeyError(str(lease.fence.command_id))
            if request.client_id != caller.client_id:
                raise _ResourceOwnershipError()

            return DispatchLeaseView(
                lease_id=lease.lease_id,
                state=lease.state,
                revision=lease.fence.revision,
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

        # 4. Auth (assume caller context checks out for this skeleton, normally we'd check `caller.client_id == cmd.submission.client_id`)
        if caller.client_id != cmd.submission.client_id:  # pyright: ignore[reportUnknownMemberType]
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
                state="ADMITTED" if desc.mutability == Mutability.MUTATING else "COMPLETED",
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
