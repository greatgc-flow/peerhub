"""Pure legacy action translation mapping."""

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import ClassVar, Generic, TypeVar, Any, cast

from peerhub.core.protocol import JsonValue
from peerhub.application.commands import (
    Command,
    SubmissionMetadata,
)

R = TypeVar("R")


def _string_tuple(value: JsonValue | None) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _optional_int(value: JsonValue | None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _int_or_zero(value: JsonValue | None) -> int:
    parsed = _optional_int(value)
    return parsed if parsed is not None else 0


def _bool_or_false(value: JsonValue | None) -> bool:
    return value if isinstance(value, bool) else False


def _optional_first_text(
    arguments: Mapping[str, JsonValue],
    names: tuple[str, ...],
) -> str | None:
    """Return the first supplied non-empty alias value, or None.

    Legacy actions accept several spellings for one field (``--peer``/
    ``--from``, ``--subject``/``--msg``, ``--feedback-id``/``--round-id``)
    and pick the first that is actually set.
    """

    for name in names:
        value = arguments.get(name)
        if value is None:
            continue
        text = str(value)
        if text:
            return text
    return None


def _optional_legacy_text(
    arguments: Mapping[str, JsonValue],
    name: str,
) -> str | None:
    """Return an optional legacy text argument without inventing a value."""

    value = arguments.get(name)
    return None if value is None else str(value)


def _first_text(
    arguments: Mapping[str, JsonValue],
    names: tuple[str, ...],
    default: str,
) -> str:
    resolved = _optional_first_text(arguments, names)
    return default if resolved is None else resolved


def _legacy_room_id(
    arguments: Mapping[str, JsonValue],
    scope: Mapping[str, JsonValue],
) -> str:
    """Resolve legacy's implicit current room from call context or scope."""

    names = ("room_id", "room", "current_room", "current-room")
    direct = _optional_first_text(arguments, names)
    if direct is not None:
        return direct
    context = arguments.get("context")
    if isinstance(context, Mapping):
        contextual = _optional_first_text(context, names)
        if contextual is not None:
            return contextual
    scoped = _optional_first_text(scope, names)
    if scoped is not None:
        return scoped
    scope_context = scope.get("context")
    if isinstance(scope_context, Mapping):
        scoped_contextual = _optional_first_text(scope_context, names)
        if scoped_contextual is not None:
            return scoped_contextual
    return ""


def legacy_thread_slug(topic: str) -> str:
    """Match legacy ``thread-new``'s deterministic topic-to-ID conversion."""

    return re.sub(r"[^\w-]", "-", topic.lower())[:40]


@dataclass(frozen=True)
class LegacyActionCall:
    action: str
    arguments: Mapping[str, JsonValue]


@dataclass(frozen=True)
class TranslatedCommand(Generic[R]):
    command: Command[R]


@dataclass(frozen=True)
class KnownLegacyActionNotBacked:
    legacy_action: str
    target_method: str
    ledger_status: str
    reason: str


@dataclass(frozen=True)
class InvalidLegacyArguments:
    action: str
    reason: str


@dataclass(frozen=True)
class UnknownLegacyAction:
    action: str


LegacyTranslationOutcome = (
    TranslatedCommand[object]
    | KnownLegacyActionNotBacked
    | InvalidLegacyArguments
    | UnknownLegacyAction
)


LEGACY_CATALOG = {
    'init-session': 'coordination.session.open',
    'end-session': 'coordination.session.close',
    'send': 'coordination.message.send',
    'broadcast': 'coordination.message.broadcast',
    'mark-read': 'coordination.message.mark_read',
    'append-log': 'governance.audit.append',
    'archive-file': 'governance.artifact.archive',
    'update-status': 'coordination.mission.update',
    'check': 'coordination.message.check',
    'status': 'peerhub.status.read',
    'check-gate': 'health.gate.check',
    'ask': 'dispatch.submit',
    'ask-all': 'dispatch.submit_many',
    'ask-coordinator': 'dispatch.submit_coordinator',
    'consensus-propose': 'consensus.round.propose',
    'consensus-vote': 'consensus.vote.cast',
    'consensus-check': 'consensus.round.read',
    'consensus-sweep': 'consensus.round.sweep',
    'register-node': 'configuration.instance.register',
    'list-nodes': 'configuration.instance.list',
    'health-update': 'health.evidence.record',
    'health-check': 'health.check',
    'peer-status': 'configuration.peer.status',
    'context-fill': 'coordination.context.fill',
    'checkpoint': 'coordination.checkpoint.create',
    'peer-quarantine': 'health.admission.quarantine',
    'peer-recover': 'health.peer.recover',
    'new-topic': 'coordination.topic.create',
    'clear-room': 'coordination.room.clear',
    'preflight': 'peerhub.preflight',
    'context-hash': 'peerhub.context.hash',
    'report-error': 'telemetry.error.record',
    'feedback-add': 'governance.feedback.create',
    'feedback-list': 'governance.feedback.list',
    'feedback-resolve': 'governance.feedback.resolve',
    'artifact-claim': 'governance.artifact.claim',
    'artifact-status': 'governance.artifact.status',
    'artifact-finalize': 'governance.artifact.finalize',
    'leader-yield': 'routing.leadership.yield',
    'leader-claim': 'routing.leadership.claim',
    'elect-leader': 'routing.leadership.elect',
    'discover': 'routing.candidate.discover',
    'assign-role': 'coordination.role.assign',
    'release-role': 'coordination.role.release',
    'role-status': 'coordination.role.status',
    'health-precheck': 'health.precheck',
    'health-sweep': 'health.sweep',
    'freshness-sweep': 'telemetry.freshness.sweep',
    'terminal-handoff': 'coordination.terminal.handoff',
    'terminal-duty-sweep': 'coordination.terminal.duty_sweep',
    'terminal-heartbeat': 'coordination.terminal.heartbeat',
    'terminal-close': 'coordination.terminal.close',
    'append-handoff': 'coordination.handoff.append',
    'task-checkpoint': 'coordination.task.checkpoint',
    'task-status': 'coordination.task.status',
    'task-failover': 'coordination.task.failover',
    'approval-request': 'governance.approval.request',
    'file-lock': 'governance.lock.acquire',
    'file-unlock': 'governance.lock.release',
    'lock-status': 'governance.lock.status',
    'profile-validate': 'configuration.profile.validate',
    'lease-status': 'dispatch.lease.status',
    'lease-sweep': 'dispatch.lease.sweep',
    'model-status': 'configuration.model.status',
    'transient-scan': 'telemetry.transient.scan',
    'directive-add': 'host.directive.add',
    'directive-list': 'host.directive.list',
    'directive-clear': 'host.directive.clear',
    'lessons-list': 'governance.lesson.list',
    'lessons-propose': 'governance.lesson.propose',
    'lessons-activate': 'governance.lesson.activate',
    'lessons-retire': 'governance.lesson.retire',
    'lesson-broadcast': 'coordination.lesson.broadcast',
    'lesson-sweep': 'governance.lesson.sweep',
    'lesson-inject': 'host.lesson.inject',
    'thread-new': 'coordination.thread.create',
    'thread-append': 'coordination.thread.append',
    'thread-react': 'coordination.thread.react',
    'thread-promote': 'coordination.thread.promote',
    'alert-raise': 'coordination.alert.raise',
    'proposal-add': 'governance.proposal.create',
    'proposal-vote': 'governance.proposal.vote',
    'proposal-list': 'governance.proposal.list',
    'broker-submit': 'governance.mutation.submit',
    'broker-drain': 'governance.effect.drain',
    'broker-status': 'governance.mutation.status',
    'update-signatures': 'peerhub.signature.update',
    'arbiter-review': 'consensus.arbiter.review',
    'credit-status': 'host.credit.status',
    'credit-consume': 'host.credit.consume'
}


@dataclass(frozen=True, slots=True)
class SubmitDispatch(Command[Any]):
    method: ClassVar[str] = "dispatch.submit"
    submission: SubmissionMetadata
    prompt: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"prompt": self.prompt}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class SubmitManyDispatch(Command[Any]):
    method: ClassVar[str] = "dispatch.submit_many"
    submission: SubmissionMetadata
    prompt: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"prompt": self.prompt}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class SubmitCoordinatorDispatch(Command[Any]):
    method: ClassVar[str] = "dispatch.submit_coordinator"
    submission: SubmissionMetadata
    prompt: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"prompt": self.prompt}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ConsensusProposeCommand(Command[Any]):
    """Wire command only; Client sends it to the application boundary."""
    method: ClassVar[str] = "consensus.round.propose"
    submission: SubmissionMetadata
    round_id: str
    title: str
    question: str
    body: str
    proposer_id: str
    required_participants: tuple[str, ...]
    eligible_participants: tuple[str, ...]
    risk: str
    source_hash: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"round_id": self.round_id, "title": self.title, "question": self.question, "body": self.body, "proposer_id": self.proposer_id, "required_participants": self.required_participants, "eligible_participants": self.eligible_participants, "risk": self.risk, "source_hash": self.source_hash}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ConsensusVoteCommand(Command[Any]):
    method: ClassVar[str] = "consensus.vote.cast"
    submission: SubmissionMetadata
    round_id: str
    actor_id: str
    choice: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"round_id": self.round_id, "actor_id": self.actor_id, "choice": self.choice}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ConsensusCheckCommand(Command[Any]):
    method: ClassVar[str] = "consensus.round.read"
    submission: SubmissionMetadata
    round_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"round_id": self.round_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value

@dataclass(frozen=True, slots=True)
class ConsensusSweepCommand(Command[Any]):
    method: ClassVar[str] = "consensus.round.sweep"
    submission: SubmissionMetadata
    round_id: str
    reason: str
    expected_revision: int | None
    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"round_id": self.round_id, "reason": self.reason, "expected_revision": self.expected_revision}
    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any: return value


@dataclass(frozen=True, slots=True)
class SessionOpenCommand(Command[Any]):
    method: ClassVar[str] = "coordination.session.open"
    submission: SubmissionMetadata
    workspace_scope_id: str
    room_id: str
    actor_principal_id: str
    instance_id: str
    profile_id: str
    session_fingerprint: str
    heartbeat_timeout_ms: int

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "workspace_scope_id": self.workspace_scope_id,
            "room_id": self.room_id,
            "actor_principal_id": self.actor_principal_id,
            "instance_id": self.instance_id,
            "profile_id": self.profile_id,
            "session_fingerprint": self.session_fingerprint,
            "heartbeat_timeout_ms": self.heartbeat_timeout_ms,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class SessionCloseCommand(Command[Any]):
    method: ClassVar[str] = "coordination.session.close"
    submission: SubmissionMetadata
    session_id: str
    session_generation: int
    workspace_scope_id: str
    room_id: str
    actor_principal_id: str
    instance_id: str
    profile_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "session_id": self.session_id,
            "session_generation": self.session_generation,
            "workspace_scope_id": self.workspace_scope_id,
            "room_id": self.room_id,
            "actor_principal_id": self.actor_principal_id,
            "instance_id": self.instance_id,
            "profile_id": self.profile_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class SessionHeartbeatCommand(SessionCloseCommand):
    method: ClassVar[str] = "coordination.session.heartbeat"
    heartbeat_timeout_ms: int

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            **SessionCloseCommand.encode_params(self),
            "heartbeat_timeout_ms": self.heartbeat_timeout_ms,
        }


@dataclass(frozen=True, slots=True)
class StatusReadCommand(Command[Any]):
    method: ClassVar[str] = "peerhub.status.read"
    submission: SubmissionMetadata
    room_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"room_id": self.room_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class UpdateStatusCommand(Command[Any]):
    """Apply the legacy room-summary fields without clobbering omissions."""

    method: ClassVar[str] = "coordination.mission.update"
    submission: SubmissionMetadata
    room_id: str
    mission: str | None = None
    blocked: str | None = None
    phase: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        params: dict[str, JsonValue] = {"room_id": self.room_id}
        if self.mission is not None:
            params["mission"] = self.mission
        if self.blocked is not None:
            params["blocked"] = self.blocked
        if self.phase is not None:
            params["phase"] = self.phase
        return params

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class NewTopicCommand(Command[Any]):
    method: ClassVar[str] = "coordination.topic.create"
    submission: SubmissionMetadata
    thread_id: str
    room_id: str
    subject: str
    creator_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"thread_id": self.thread_id, "room_id": self.room_id, "subject": self.subject, "creator_id": self.creator_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ThreadNewCommand(Command[Any]):
    method: ClassVar[str] = "coordination.thread.create"
    submission: SubmissionMetadata
    thread_id: str
    room_id: str
    subject: str
    creator_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "thread_id": self.thread_id,
            "room_id": self.room_id,
            "subject": self.subject,
            "creator_id": self.creator_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ThreadAppendCommand(Command[Any]):
    method: ClassVar[str] = "coordination.thread.append"
    submission: SubmissionMetadata
    message_id: str
    room_id: str
    thread_id: str
    author_id: str
    body: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "message_id": self.message_id,
            "room_id": self.room_id,
            "thread_id": self.thread_id,
            "author_id": self.author_id,
            "body": self.body,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class MessageSendCommand(Command[Any]):
    method: ClassVar[str] = "coordination.message.send"
    submission: SubmissionMetadata
    room_id: str
    sender_instance_id: str
    sender_profile_id: str
    recipient_instance_id: str
    recipient_profile_id: str
    body: str
    message_type: str = "MSG"
    thread_ref: str | None = None
    resource_ref: str | None = None
    correlation_id: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "sender_instance_id": self.sender_instance_id,
            "sender_profile_id": self.sender_profile_id,
            "recipient_instance_id": self.recipient_instance_id,
            "recipient_profile_id": self.recipient_profile_id,
            "body": self.body,
            "message_type": self.message_type,
            "thread_ref": self.thread_ref,
            "resource_ref": self.resource_ref,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class RoomBroadcastCommand(Command[Any]):
    method: ClassVar[str] = "coordination.message.broadcast"
    submission: SubmissionMetadata
    room_id: str
    from_: str
    msg: str
    targets: tuple[str, ...] | None
    msg_type: str = "MSG"
    priority: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "from_": self.from_,
            "msg": self.msg,
            "targets": self.targets,
            "msg_type": self.msg_type,
            "priority": self.priority,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class MessageCheckCommand(Command[Any]):
    method: ClassVar[str] = "coordination.message.check"
    submission: SubmissionMetadata
    room_id: str
    caller_instance_id: str
    caller_profile_id: str
    include_read: bool = False

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "caller_instance_id": self.caller_instance_id,
            "caller_profile_id": self.caller_profile_id,
            "include_read": self.include_read,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class MessageMarkReadCommand(Command[Any]):
    method: ClassVar[str] = "coordination.message.mark_read"
    submission: SubmissionMetadata
    room_id: str
    recipient_instance_id: str
    recipient_profile_id: str
    up_through_sequence: int

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "recipient_instance_id": self.recipient_instance_id,
            "recipient_profile_id": self.recipient_profile_id,
            "up_through_sequence": self.up_through_sequence,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ThreadPromoteCommand(Command[Any]):
    method: ClassVar[str] = "coordination.thread.promote"
    submission: SubmissionMetadata
    message_id: str
    room_id: str
    thread_id: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "message_id": self.message_id,
            "room_id": self.room_id,
            "thread_id": self.thread_id,
            "actor_id": self.actor_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class AppendHandoffCommand(Command[Any]):
    method: ClassVar[str] = "coordination.handoff.append"
    submission: SubmissionMetadata
    room_id: str
    section: str
    text: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "section": self.section,
            "text": self.text,
            "actor_id": self.actor_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ContinuityCheckpointCommand(Command[Any]):
    method: ClassVar[str] = "coordination.checkpoint.create"
    submission: SubmissionMetadata
    room_id: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"room_id": self.room_id, "actor_id": self.actor_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ContextFillCommand(Command[Any]):
    method: ClassVar[str] = "coordination.context.fill"
    submission: SubmissionMetadata
    room_id: str
    session_id: str
    sections: tuple[str, ...] | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "session_id": self.session_id,
            "sections": self.sections,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ThreadReactCommand(Command[Any]):
    method: ClassVar[str] = "coordination.thread.react"
    submission: SubmissionMetadata
    message_id: str
    room_id: str
    actor_instance_id: str
    actor_profile_id: str
    reaction_type: str
    action: str = "ADD"

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "message_id": self.message_id,
            "room_id": self.room_id,
            "actor_instance_id": self.actor_instance_id,
            "actor_profile_id": self.actor_profile_id,
            "reaction_type": self.reaction_type,
            "action": self.action,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ClearRoomCommand(Command[Any]):
    method: ClassVar[str] = "coordination.room.clear"
    submission: SubmissionMetadata
    old_room_id: str
    new_room_id: str
    subject: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"old_room_id": self.old_room_id, "new_room_id": self.new_room_id, "subject": self.subject, "actor_id": self.actor_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LeaderClaimCommand(Command[Any]):
    """Workspace-global leadership claim.

    Replaces an earlier room-scoped duty-lease shape (room_id/instance_id/
    profile_id/owner_principal_id/authority_epoch) that implemented the
    wrong semantic entirely -- see the LeadershipService ratification.
    """

    method: ClassVar[str] = "routing.leadership.claim"
    submission: SubmissionMetadata
    peer_node_id: str
    actor_id: str
    reason: str = ""
    domain: str = ""

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "peer_node_id": self.peer_node_id,
            "actor_id": self.actor_id,
            "reason": self.reason,
            "domain": self.domain,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LeaderYieldCommand(Command[Any]):
    """Workspace-global leadership yield (vacates unconditionally)."""

    method: ClassVar[str] = "routing.leadership.yield"
    submission: SubmissionMetadata
    yielding_peer_id: str
    actor_id: str
    reason: str = ""

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "yielding_peer_id": self.yielding_peer_id,
            "actor_id": self.actor_id,
            "reason": self.reason,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TerminalHandoffCommand(Command[Any]):
    method: ClassVar[str] = "coordination.terminal.handoff"
    submission: SubmissionMetadata
    current_lease_id: str
    room_id: str
    current_instance_id: str
    current_profile_id: str
    term: int
    authority_epoch: int
    new_instance_id: str
    new_profile_id: str
    new_owner_principal_id: str
    new_authority_epoch: int

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"current_lease_id": self.current_lease_id, "room_id": self.room_id, "current_instance_id": self.current_instance_id, "current_profile_id": self.current_profile_id, "term": self.term, "authority_epoch": self.authority_epoch, "new_instance_id": self.new_instance_id, "new_profile_id": self.new_profile_id, "new_owner_principal_id": self.new_owner_principal_id, "new_authority_epoch": self.new_authority_epoch}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TerminalHeartbeatCommand(Command[Any]):
    """Room-scoped terminal-duty lease heartbeat.

    Previously inherited these six fields from the old room-scoped
    LeaderYieldCommand. That name now carries workspace-global leadership
    fields instead, so the duty-lease shape lives here (and in its
    TerminalCloseCommand subclass) unchanged.
    """

    method: ClassVar[str] = "coordination.terminal.heartbeat"
    submission: SubmissionMetadata
    lease_id: str
    room_id: str
    instance_id: str
    profile_id: str
    term: int
    authority_epoch: int

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"lease_id": self.lease_id, "room_id": self.room_id, "instance_id": self.instance_id, "profile_id": self.profile_id, "term": self.term, "authority_epoch": self.authority_epoch}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TerminalCloseCommand(TerminalHeartbeatCommand):
    method: ClassVar[str] = "coordination.terminal.close"
    close_session: bool = False
    session_id: str = ""
    session_generation: int = 0
    workspace_scope_id: str = ""
    actor_principal_id: str = ""

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            **TerminalHeartbeatCommand.encode_params(self),
            "close_session": self.close_session,
            "session_id": self.session_id,
            "session_generation": self.session_generation,
            "workspace_scope_id": self.workspace_scope_id,
            "actor_principal_id": self.actor_principal_id,
        }


@dataclass(frozen=True, slots=True)
class TerminalDutySweepCommand(Command[Any]):
    method: ClassVar[str] = "coordination.terminal.duty_sweep"
    submission: SubmissionMetadata
    role: str
    recovery_actor_principal_id: str
    trigger: str
    evidence_digest: str
    policy_id: str
    policy_revision: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "role": self.role,
            "recovery_actor_principal_id": (
                self.recovery_actor_principal_id
            ),
            "trigger": self.trigger,
            "evidence_digest": self.evidence_digest,
            "policy_id": self.policy_id,
            "policy_revision": self.policy_revision,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TaskCheckpointCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.checkpoint"
    submission: SubmissionMetadata
    task_id: str
    actor_id: str
    checkpoint_id: str
    stage: str
    request_id: str
    attempt_id: str
    resume_token_ref: str | None
    completed_units: tuple[str, ...]
    remaining_units: tuple[str, ...]
    expected_revision: int | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"task_id": self.task_id, "actor_id": self.actor_id, "checkpoint_id": self.checkpoint_id, "stage": self.stage, "request_id": self.request_id, "attempt_id": self.attempt_id, "resume_token_ref": self.resume_token_ref, "completed_units": self.completed_units, "remaining_units": self.remaining_units, "expected_revision": self.expected_revision}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TaskStatusCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.status"
    submission: SubmissionMetadata
    task_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"task_id": self.task_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class TaskFailoverCommand(Command[Any]):
    method: ClassVar[str] = "coordination.task.failover"
    submission: SubmissionMetadata
    task_id: str
    to_actor_id: str
    reason: str
    expected_revision: int | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"task_id": self.task_id, "to_actor_id": self.to_actor_id, "reason": self.reason, "expected_revision": self.expected_revision}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value

@dataclass(frozen=True, slots=True)
class ApprovalRequestCommand(Command[Any]):
    method: ClassVar[str] = "governance.approval.request"
    submission: SubmissionMetadata
    task_id: str
    requester_id: str
    approval_id: str
    approver_id: str
    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"task_id": self.task_id, "requester_id": self.requester_id, "approval_id": self.approval_id, "approver_id": self.approver_id}
    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any: return value


@dataclass(frozen=True, slots=True)
class LessonInjectCommand(Command[Any]):
    method: ClassVar[str] = "governance.lesson.inject"
    submission: SubmissionMetadata
    target_peer_id: str
    workspace_id: str
    os: str | None
    shell: str | None
    task_types: frozenset[str]

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "target_peer_id": self.target_peer_id,
            "workspace_id": self.workspace_id,
            "os": self.os,
            "shell": self.shell,
            "task_types": tuple(self.task_types),
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LessonProposeCommand(Command[Any]):
    method: ClassVar[str] = "governance.lesson.propose"
    submission: SubmissionMetadata
    lesson_id: str
    title: str
    rule: str
    category: str
    severity: str
    proposer_id: str
    affected_peers: tuple[str, ...]
    scope_kind: str
    workspace_id: str | None
    sticky: bool
    os: tuple[str, ...] | None
    shell: tuple[str, ...] | None
    task_types: tuple[str, ...] | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"lesson_id": self.lesson_id, "title": self.title, "rule": self.rule, "category": self.category, "severity": self.severity, "proposer_id": self.proposer_id, "affected_peers": self.affected_peers, "scope_kind": self.scope_kind, "workspace_id": self.workspace_id, "sticky": self.sticky, "os": self.os, "shell": self.shell, "task_types": self.task_types}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LessonActivateCommand(Command[Any]):
    method: ClassVar[str] = "governance.lesson.activate"
    submission: SubmissionMetadata
    lesson_id: str
    actor_id: str
    expected_revision: int | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"lesson_id": self.lesson_id, "actor_id": self.actor_id, "expected_revision": self.expected_revision}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LessonRetireCommand(Command[Any]):
    method: ClassVar[str] = "governance.lesson.retire"
    submission: SubmissionMetadata
    lesson_id: str
    actor_id: str
    reason: str
    expected_revision: int | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"lesson_id": self.lesson_id, "actor_id": self.actor_id, "reason": self.reason, "expected_revision": self.expected_revision}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LessonBroadcastCommand(Command[Any]):
    method: ClassVar[str] = "coordination.lesson.broadcast"
    submission: SubmissionMetadata
    lesson_id: str
    room_id: str
    sender_instance_id: str
    sender_profile_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "lesson_id": self.lesson_id,
            "room_id": self.room_id,
            "sender_instance_id": self.sender_instance_id,
            "sender_profile_id": self.sender_profile_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class AlertRaiseCommand(Command[Any]):
    method: ClassVar[str] = "coordination.alert.raise"
    submission: SubmissionMetadata
    room_id: str
    raiser_instance_id: str
    raiser_profile_id: str
    severity: str
    message: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "raiser_instance_id": self.raiser_instance_id,
            "raiser_profile_id": self.raiser_profile_id,
            "severity": self.severity,
            "message": self.message,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LessonsListCommand(Command[Any]):
    method: ClassVar[str] = "governance.lesson.list"
    submission: SubmissionMetadata
    scope: str | None
    def encode_params(self) -> Mapping[str, JsonValue]: return {"scope": self.scope}
    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any: return value


@dataclass(frozen=True, slots=True)
class ProposalAddCommand(Command[Any]):
    method: ClassVar[str] = "governance.proposal.create"
    submission: SubmissionMetadata
    subject: str
    from_peer: str
    impact: str
    rationale: str
    text: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "subject": self.subject,
            "from_peer": self.from_peer,
            "impact": self.impact,
            "rationale": self.rationale,
            "text": self.text,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ProposalVoteCommand(Command[Any]):
    method: ClassVar[str] = "governance.proposal.vote"
    submission: SubmissionMetadata
    proposal_id: str
    voter: str
    vote: str
    reason: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "proposal_id": self.proposal_id,
            "voter": self.voter,
            "vote": self.vote,
            "reason": self.reason,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ProposalListCommand(Command[Any]):
    method: ClassVar[str] = "governance.proposal.list"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ArbiterReviewCommand(Command[Any]):
    method: ClassVar[str] = "consensus.arbiter.review"
    submission: SubmissionMetadata
    round_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"round_id": self.round_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class RegisterNodeCommand(Command[Any]):
    method: ClassVar[str] = "configuration.instance.register"
    submission: SubmissionMetadata
    node_id: str
    peer_kind: str
    profile_id: str | None
    tier: int
    node_type: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "node_id": self.node_id,
            "peer_kind": self.peer_kind,
            "profile_id": self.profile_id,
            "tier": self.tier,
            "node_type": self.node_type,
            "actor_id": self.actor_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ListNodesCommand(Command[Any]):
    method: ClassVar[str] = "configuration.instance.list"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class BindProfileCommand(Command[Any]):
    method: ClassVar[str] = "configuration.profile.bind"
    submission: SubmissionMetadata
    node_id: str
    profile_id: str
    model_id: str
    reasoning_effort: str | None
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "node_id": self.node_id,
            "profile_id": self.profile_id,
            "model_id": self.model_id,
            "reasoning_effort": self.reasoning_effort,
            "actor_id": self.actor_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ModelStatusCommand(Command[Any]):
    method: ClassVar[str] = "configuration.model.status"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class PeerStatusCommand(Command[Any]):
    method: ClassVar[str] = "configuration.peer.status"
    submission: SubmissionMetadata
    node_id: str | None = None
    include_all: bool = False

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "node_id": self.node_id,
            "include_all": self.include_all,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class HealthCheckCommand(Command[Any]):
    method: ClassVar[str] = "health.check"
    submission: SubmissionMetadata
    peer: str | None = None
    recover: bool = False

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "peer": self.peer,
            "recover": self.recover,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class PeerQuarantineCommand(Command[Any]):
    method: ClassVar[str] = "health.admission.quarantine"
    submission: SubmissionMetadata
    peer_id: str
    reason: str = "manual"
    actor_id: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        params: dict[str, JsonValue] = {
            "peer_id": self.peer_id,
            "reason": self.reason,
        }
        if self.actor_id is not None:
            params["actor_id"] = self.actor_id
        return params

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class PeerRecoverCommand(Command[Any]):
    method: ClassVar[str] = "health.peer.recover"
    submission: SubmissionMetadata
    peer_id: str
    reason: str = "manual"

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "peer_id": self.peer_id,
            "reason": self.reason,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class HealthPrecheckCommand(Command[Any]):
    method: ClassVar[str] = "health.precheck"
    submission: SubmissionMetadata
    peers: str | None = None
    needs: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "peers": self.peers,
            "needs": self.needs,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class CheckGateCommand(Command[Any]):
    method: ClassVar[str] = "health.gate.check"
    submission: SubmissionMetadata
    agent: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "agent": self.agent,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class HealthSweepCommand(Command[Any]):
    method: ClassVar[str] = "health.sweep"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LeaseStatusCommand(Command[Any]):
    method: ClassVar[str] = "dispatch.lease.status"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class LeaseSweepCommand(Command[Any]):
    method: ClassVar[str] = "dispatch.lease.sweep"
    submission: SubmissionMetadata
    limit: int = 100
    reap: bool = True

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "limit": self.limit,
            "reap": self.reap,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value



@dataclass(frozen=True, slots=True)
class AssignRoleCommand(Command[Any]):
    method: ClassVar[str] = "coordination.role.assign"
    submission: SubmissionMetadata
    role: str
    peer_node_id: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "role": self.role,
            "peer_node_id": self.peer_node_id,
            "actor_id": self.actor_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ReleaseRoleCommand(Command[Any]):
    method: ClassVar[str] = "coordination.role.release"
    submission: SubmissionMetadata
    role: str
    actor_id: str
    peer_node_id: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "role": self.role,
            "actor_id": self.actor_id,
            "peer_node_id": self.peer_node_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class RoomStatusCommand(Command[Any]):
    method: ClassVar[str] = "peerhub.status.read"
    submission: SubmissionMetadata
    room_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"room_id": self.room_id}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class RoleStatusCommand(Command[Any]):
    method: ClassVar[str] = "coordination.role.status"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value

@dataclass(frozen=True, slots=True)
class LockAcquireCommand(Command[Any]):
    method: ClassVar[str] = "governance.lock.acquire"
    submission: SubmissionMetadata
    name: str
    owner: str
    lock_scope: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "name": self.name,
            "owner": self.owner,
            "lock_scope": self.lock_scope,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value

@dataclass(frozen=True, slots=True)
class LockReleaseCommand(Command[Any]):
    method: ClassVar[str] = "governance.lock.release"
    submission: SubmissionMetadata
    name: str
    owner: str | None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "name": self.name,
            "owner": self.owner,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value

@dataclass(frozen=True, slots=True)
class LockStatusCommand(Command[Any]):
    method: ClassVar[str] = "governance.lock.status"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class FeedbackAddCommand(Command[Any]):
    method: ClassVar[str] = "governance.feedback.create"
    submission: SubmissionMetadata
    source_peer: str
    category: str
    severity: str
    title: str
    detail: str
    actor_id: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "source_peer": self.source_peer,
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "actor_id": self.actor_id,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class FeedbackListCommand(Command[Any]):
    method: ClassVar[str] = "governance.feedback.list"
    submission: SubmissionMetadata

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class FeedbackResolveCommand(Command[Any]):
    method: ClassVar[str] = "governance.feedback.resolve"
    submission: SubmissionMetadata
    feedback_id: str
    status: str
    actor_id: str
    owner: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "feedback_id": self.feedback_id,
            "status": self.status,
            "actor_id": self.actor_id,
            "owner": self.owner,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ReportErrorCommand(Command[Any]):
    method: ClassVar[str] = "telemetry.error.record"
    submission: SubmissionMetadata
    peer_key: str
    pattern: str
    severity: str
    detail: str
    actor_id: str
    threshold: int = 3

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "peer_key": self.peer_key,
            "pattern": self.pattern,
            "severity": self.severity,
            "detail": self.detail,
            "actor_id": self.actor_id,
            "threshold": self.threshold,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


class LegacyTranslator:
    def translate(
        self,
        call: LegacyActionCall,
        submission: SubmissionMetadata,
    ) -> LegacyTranslationOutcome:
        if call.action not in LEGACY_CATALOG:
            return UnknownLegacyAction(action=call.action)
            
        target = LEGACY_CATALOG[call.action]

        if call.action == "status":
            room_id = _legacy_room_id(call.arguments, submission.scope)
            if not room_id:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason="room_id is required in arguments, context, or scope",
                )
            return TranslatedCommand(
                command=StatusReadCommand(
                    submission=submission,
                    room_id=room_id,
                )
            )

        if call.action == "update-status":
            room_id = _legacy_room_id(call.arguments, submission.scope)
            if not room_id:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason="room_id is required in arguments, context, or scope",
                )
            return TranslatedCommand(
                command=UpdateStatusCommand(
                    submission=submission,
                    room_id=room_id,
                    mission=_optional_legacy_text(call.arguments, "mission"),
                    blocked=_optional_legacy_text(call.arguments, "blocked"),
                    phase=_optional_legacy_text(call.arguments, "phase"),
                )
            )

        if call.action == "ask":
            prompt = str(call.arguments.get("prompt", ""))
            return TranslatedCommand(command=SubmitDispatch(submission=submission, prompt=prompt))
        if call.action == "ask-all":
            prompt = str(call.arguments.get("prompt", ""))
            return TranslatedCommand(command=SubmitManyDispatch(submission=submission, prompt=prompt))
        if call.action == "ask-coordinator":
            prompt = str(call.arguments.get("prompt", ""))
            return TranslatedCommand(command=SubmitCoordinatorDispatch(submission=submission, prompt=prompt))
        if call.action == "consensus-propose":
            return TranslatedCommand(command=ConsensusProposeCommand(
                submission=submission,
                round_id=str(call.arguments.get("round_id", "")),
                title=str(call.arguments.get("title", "")),
                question=str(call.arguments.get("question", "")),
                body=str(call.arguments.get("body", "")),
                proposer_id=str(call.arguments.get("proposer_id", "")),
                required_participants=_string_tuple(call.arguments.get("required_participants")),
                eligible_participants=_string_tuple(call.arguments.get("eligible_participants")),
                risk=str(call.arguments.get("risk", "normal")),
                source_hash=str(call.arguments.get("source_hash", "")),
            ))
        if call.action == "consensus-vote":
            return TranslatedCommand(command=ConsensusVoteCommand(
                submission=submission,
                round_id=str(call.arguments.get("round_id", "")),
                actor_id=str(call.arguments.get("actor_id", "")),
                choice=str(call.arguments.get("choice", "")),
            ))
        if call.action == "consensus-check":
            return TranslatedCommand(command=ConsensusCheckCommand(
                submission=submission,
                round_id=str(call.arguments.get("round_id", "")),
            ))
        if call.action == "consensus-sweep":
            return TranslatedCommand(command=ConsensusSweepCommand(submission, str(call.arguments.get("round_id", "")), str(call.arguments.get("reason", "")), _optional_int(call.arguments.get("expected_revision"))))
        if call.action == "init-session":
            return TranslatedCommand(command=SessionOpenCommand(
                submission=submission,
                workspace_scope_id=str(call.arguments.get("workspace_scope_id", "")),
                room_id=str(call.arguments.get("room_id", "")),
                actor_principal_id=str(call.arguments.get("actor_principal_id", "")),
                instance_id=str(call.arguments.get("instance_id", "")),
                profile_id=str(call.arguments.get("profile_id", "")),
                session_fingerprint=str(call.arguments.get("session_fingerprint", "")),
                heartbeat_timeout_ms=_int_or_zero(call.arguments.get("heartbeat_timeout_ms")),
            ))
        if call.action == "end-session":
            return TranslatedCommand(command=SessionCloseCommand(
                submission=submission,
                session_id=str(call.arguments.get("session_id", "")),
                session_generation=_int_or_zero(call.arguments.get("session_generation")),
                workspace_scope_id=str(call.arguments.get("workspace_scope_id", "")),
                room_id=str(call.arguments.get("room_id", "")),
                actor_principal_id=str(call.arguments.get("actor_principal_id", "")),
                instance_id=str(call.arguments.get("instance_id", "")),
                profile_id=str(call.arguments.get("profile_id", "")),
            ))
        if call.action == "new-topic":
            return TranslatedCommand(command=NewTopicCommand(
                submission=submission,
                thread_id=str(call.arguments.get("thread_id", "")),
                room_id=str(call.arguments.get("room_id", "")),
                subject=str(call.arguments.get("subject", "")),
                creator_id=str(call.arguments.get("creator_id", "")),
            ))
        if call.action == "thread-new":
            topic = _optional_legacy_text(call.arguments, "topic")
            if not topic:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason="thread-new requires --topic",
                )
            thread_new_room_id = _legacy_room_id(call.arguments, submission.scope)
            if not thread_new_room_id:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason="room_id is required in arguments, context, or scope",
                )
            return TranslatedCommand(command=ThreadNewCommand(
                submission=submission,
                thread_id=legacy_thread_slug(topic),
                room_id=thread_new_room_id,
                subject=topic,
                creator_id=_first_text(
                    call.arguments,
                    ("from", "peer"),
                    "cc",
                ),
            ))
        if call.action == "thread-append":
            return TranslatedCommand(command=ThreadAppendCommand(
                submission=submission,
                message_id=str(call.arguments.get("message_id", "")),
                room_id=str(call.arguments.get("room_id", "")),
                thread_id=str(call.arguments.get("thread_id", "")),
                author_id=str(call.arguments.get("author_id", "")),
                body=str(call.arguments.get("body", "")),
            ))
        if call.action == "send":
            return TranslatedCommand(command=MessageSendCommand(
                submission=submission,
                room_id=str(call.arguments.get("room_id", "")),
                sender_instance_id=str(call.arguments.get("sender_instance_id", "")),
                sender_profile_id=str(call.arguments.get("sender_profile_id", "")),
                recipient_instance_id=str(call.arguments.get("recipient_instance_id", "")),
                recipient_profile_id=str(call.arguments.get("recipient_profile_id", "")),
                body=str(call.arguments.get("body", "")),
                message_type=str(call.arguments.get("message_type", "MSG")),
                thread_ref=(
                    None
                    if call.arguments.get("thread_ref") is None
                    else str(call.arguments.get("thread_ref"))
                ),
                resource_ref=(
                    None
                    if call.arguments.get("resource_ref") is None
                    else str(call.arguments.get("resource_ref"))
                ),
                correlation_id=(
                    None
                    if call.arguments.get("correlation_id") is None
                    else str(call.arguments.get("correlation_id"))
                ),
            ))
        if call.action == "broadcast":
            room_id = _legacy_room_id(call.arguments, submission.scope)
            if not room_id:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason="room_id is required in arguments, context, or scope",
                )
            msg = _optional_legacy_text(call.arguments, "msg")
            if not msg:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason="broadcast requires --msg",
                )
            raw_targets = call.arguments.get("targets")
            if raw_targets is None:
                targets = None
            elif isinstance(raw_targets, (list, tuple)) and all(
                isinstance(target, str) and target for target in raw_targets
            ):
                targets = tuple(cast(str, target) for target in raw_targets)
            else:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason="targets must be a sequence of nonempty strings",
                )
            return TranslatedCommand(command=RoomBroadcastCommand(
                submission=submission,
                room_id=room_id,
                from_=_first_text(call.arguments, ("from", "peer"), "cc"),
                msg=msg,
                targets=targets,
                msg_type=_first_text(call.arguments, ("type", "msg_type"), "MSG"),
                priority=_optional_legacy_text(call.arguments, "priority"),
            ))
        if call.action == "check":
            return TranslatedCommand(command=MessageCheckCommand(
                submission=submission,
                room_id=str(call.arguments.get("room_id", "")),
                caller_instance_id=str(call.arguments.get("caller_instance_id", "")),
                caller_profile_id=str(call.arguments.get("caller_profile_id", "")),
                include_read=_bool_or_false(call.arguments.get("include_read")),
            ))
        if call.action == "mark-read":
            return TranslatedCommand(command=MessageMarkReadCommand(
                submission=submission,
                room_id=str(call.arguments.get("room_id", "")),
                recipient_instance_id=str(call.arguments.get("recipient_instance_id", "")),
                recipient_profile_id=str(call.arguments.get("recipient_profile_id", "")),
                up_through_sequence=_int_or_zero(
                    call.arguments.get("up_through_sequence")
                ),
            ))
        if call.action == "thread-promote":
            return TranslatedCommand(command=ThreadPromoteCommand(
                submission=submission,
                message_id=str(call.arguments.get("message_id", "")),
                room_id=str(call.arguments.get("room_id", "")),
                thread_id=str(call.arguments.get("thread_id", "")),
                actor_id=str(call.arguments.get("actor_id", "")),
            ))
        if call.action == "append-handoff":
            section = str(call.arguments.get("section", ""))
            if section not in {
                "RECENT_COMPLETED",
                "PENDING_ISSUES",
                "KEY_DECISIONS",
                "CONSENSUS_HISTORY",
                "ACTIVE_THREADS",
            }:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason=(
                        "section must be RECENT_COMPLETED, PENDING_ISSUES, "
                        "KEY_DECISIONS, CONSENSUS_HISTORY, or ACTIVE_THREADS"
                    ),
                )
            return TranslatedCommand(command=AppendHandoffCommand(
                submission=submission,
                room_id=str(call.arguments.get("room_id", "")),
                section=section,
                text=str(call.arguments.get("text", "")),
                actor_id=str(
                    call.arguments.get(
                        "actor_id", submission.actor_id or "peerhub"
                    )
                ),
            ))
        if call.action == "checkpoint":
            return TranslatedCommand(command=ContinuityCheckpointCommand(
                submission=submission,
                room_id=str(call.arguments.get("room_id", "")),
                actor_id=str(
                    call.arguments.get(
                        "actor_id", submission.actor_id or "peerhub"
                    )
                ),
            ))
        if call.action == "context-fill":
            session_id = call.arguments.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason="session_id must be a nonempty string",
                )
            raw_sections = call.arguments.get("sections")
            if raw_sections is None:
                sections: tuple[str, ...] | None = None
            elif isinstance(raw_sections, str):
                sections = tuple(
                    section.strip()
                    for section in raw_sections.split(",")
                    if section.strip()
                )
            elif isinstance(raw_sections, (list, tuple)) and all(
                isinstance(section, str) for section in raw_sections
            ):
                sections = tuple(str(section) for section in raw_sections)
            else:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason="sections must be a sequence of strings",
                )
            valid_sections = {
                "GOAL",
                "RECENT_COMPLETED",
                "PENDING_ISSUES",
                "KEY_DECISIONS",
                "CONSENSUS_HISTORY",
                "ACTIVE_THREADS",
            }
            if sections is not None and (
                not sections
                or any(section not in valid_sections for section in sections)
                or len(set(sections)) != len(sections)
            ):
                return InvalidLegacyArguments(
                    action=call.action,
                    reason=(
                        "sections must contain unique, exact handoff section names"
                    ),
                )
            return TranslatedCommand(command=ContextFillCommand(
                submission=submission,
                room_id=str(call.arguments.get("room_id", "")),
                session_id=session_id,
                sections=sections,
            ))
        if call.action == "thread-react":
            action = str(call.arguments.get("action", "ADD"))
            if action not in {"ADD", "REMOVE"}:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason="action must be ADD or REMOVE",
                )
            return TranslatedCommand(command=ThreadReactCommand(
                submission=submission,
                message_id=str(call.arguments.get("message_id", "")),
                room_id=str(call.arguments.get("room_id", "")),
                actor_instance_id=str(
                    call.arguments.get("actor_instance_id", "")
                ),
                actor_profile_id=str(
                    call.arguments.get("actor_profile_id", "")
                ),
                reaction_type=str(call.arguments.get("reaction_type", "")),
                action=action,
            ))
        if call.action == "clear-room":
            return TranslatedCommand(command=ClearRoomCommand(
                submission=submission,
                old_room_id=str(call.arguments.get("old_room_id", "")),
                new_room_id=str(call.arguments.get("new_room_id", "")),
                subject=str(call.arguments.get("subject", "")),
                actor_id=str(call.arguments.get("actor_id", "")),
            ))
        if call.action == "leader-claim":
            # Legacy CLI shape: --agent (default "unknown"),
            # --reason/--detail, --needs for the domain.
            return TranslatedCommand(command=LeaderClaimCommand(
                submission=submission,
                peer_node_id=_first_text(
                    call.arguments,
                    ("peer_node_id", "agent", "peer"),
                    "unknown",
                ),
                actor_id=_first_text(
                    call.arguments, ("actor_id",), submission.actor_id or ""
                ),
                reason=_first_text(call.arguments, ("reason", "detail"), ""),
                domain=_first_text(call.arguments, ("domain", "needs"), ""),
            ))
        if call.action == "leader-yield":
            return TranslatedCommand(command=LeaderYieldCommand(
                submission=submission,
                yielding_peer_id=_first_text(
                    call.arguments,
                    ("yielding_peer_id", "agent", "peer"),
                    "unknown",
                ),
                actor_id=_first_text(
                    call.arguments, ("actor_id",), submission.actor_id or ""
                ),
                reason=_first_text(call.arguments, ("reason", "detail"), ""),
            ))
        if call.action == "terminal-heartbeat":
            return TranslatedCommand(command=TerminalHeartbeatCommand(submission=submission, lease_id=str(call.arguments.get("lease_id", "")), room_id=str(call.arguments.get("room_id", "")), instance_id=str(call.arguments.get("instance_id", "")), profile_id=str(call.arguments.get("profile_id", "")), term=_int_or_zero(call.arguments.get("term")), authority_epoch=_int_or_zero(call.arguments.get("authority_epoch"))))
        if call.action == "terminal-handoff":
            return TranslatedCommand(command=TerminalHandoffCommand(submission=submission, current_lease_id=str(call.arguments.get("current_lease_id", "")), room_id=str(call.arguments.get("room_id", "")), current_instance_id=str(call.arguments.get("current_instance_id", "")), current_profile_id=str(call.arguments.get("current_profile_id", "")), term=_int_or_zero(call.arguments.get("term")), authority_epoch=_int_or_zero(call.arguments.get("authority_epoch")), new_instance_id=str(call.arguments.get("new_instance_id", "")), new_profile_id=str(call.arguments.get("new_profile_id", "")), new_owner_principal_id=str(call.arguments.get("new_owner_principal_id", "")), new_authority_epoch=_int_or_zero(call.arguments.get("new_authority_epoch"))))
        if call.action == "terminal-close":
            return TranslatedCommand(command=TerminalCloseCommand(
                submission=submission,
                lease_id=str(call.arguments.get("lease_id", "")),
                room_id=str(call.arguments.get("room_id", "")),
                instance_id=str(call.arguments.get("instance_id", "")),
                profile_id=str(call.arguments.get("profile_id", "")),
                term=_int_or_zero(call.arguments.get("term")),
                authority_epoch=_int_or_zero(
                    call.arguments.get("authority_epoch")
                ),
                close_session=_bool_or_false(
                    call.arguments.get("close_session")
                ),
                session_id=str(call.arguments.get("session_id", "")),
                session_generation=_int_or_zero(
                    call.arguments.get("session_generation")
                ),
                workspace_scope_id=str(
                    call.arguments.get("workspace_scope_id", "")
                ),
                actor_principal_id=str(
                    call.arguments.get("actor_principal_id", "")
                ),
            ))
        if call.action == "terminal-duty-sweep":
            return TranslatedCommand(command=TerminalDutySweepCommand(
                submission=submission,
                role=str(call.arguments.get("role", "terminal-duty")),
                recovery_actor_principal_id=str(call.arguments.get(
                    "recovery_actor_principal_id",
                    submission.actor_id or "peerhub",
                )),
                trigger=str(call.arguments.get(
                    "trigger", "HEARTBEAT_TIMEOUT"
                )),
                evidence_digest=str(call.arguments.get(
                    "evidence_digest", "legacy:terminal-duty-sweep"
                )),
                policy_id=str(call.arguments.get(
                    "policy_id", "terminal-duty-sweep"
                )),
                policy_revision=str(call.arguments.get(
                    "policy_revision", "1"
                )),
            ))
        if call.action == "task-checkpoint":
            return TranslatedCommand(command=TaskCheckpointCommand(
                submission=submission, task_id=str(call.arguments.get("task_id", "")), actor_id=str(call.arguments.get("actor_id", "")), checkpoint_id=str(call.arguments.get("checkpoint_id", "")), stage=str(call.arguments.get("stage", "")), request_id=str(call.arguments.get("request_id", "")), attempt_id=str(call.arguments.get("attempt_id", "")), resume_token_ref=str(call.arguments["resume_token_ref"]) if call.arguments.get("resume_token_ref") is not None else None, completed_units=_string_tuple(call.arguments.get("completed_units")), remaining_units=_string_tuple(call.arguments.get("remaining_units")), expected_revision=_optional_int(call.arguments.get("expected_revision")),
            ))
        if call.action == "task-status":
            return TranslatedCommand(command=TaskStatusCommand(submission=submission, task_id=str(call.arguments.get("task_id", ""))))
        if call.action == "task-failover":
            return TranslatedCommand(command=TaskFailoverCommand(submission=submission, task_id=str(call.arguments.get("task_id", "")), to_actor_id=str(call.arguments.get("to_actor_id", "")), reason=str(call.arguments.get("reason", "")), expected_revision=_optional_int(call.arguments.get("expected_revision"))))
        if call.action == "approval-request":
            return TranslatedCommand(command=ApprovalRequestCommand(submission, str(call.arguments.get("task_id", "")), str(call.arguments.get("requester_id", "")), str(call.arguments.get("approval_id", "")), str(call.arguments.get("approver_id", ""))))
        if call.action == "lessons-propose":
            return TranslatedCommand(command=LessonProposeCommand(submission=submission, lesson_id=str(call.arguments.get("lesson_id", "")), title=str(call.arguments.get("title", "")), rule=str(call.arguments.get("rule", "")), category=str(call.arguments.get("category", "")), severity=str(call.arguments.get("severity", "")), proposer_id=str(call.arguments.get("proposer_id", "")), affected_peers=_string_tuple(call.arguments.get("affected_peers")), scope_kind=str(call.arguments.get("scope_kind", "global")), workspace_id=str(call.arguments["workspace_id"]) if call.arguments.get("workspace_id") is not None else None, sticky=False, os=None, shell=None, task_types=None))
        if call.action == "lessons-activate":
            return TranslatedCommand(command=LessonActivateCommand(submission=submission, lesson_id=str(call.arguments.get("lesson_id", "")), actor_id=str(call.arguments.get("actor_id", "")), expected_revision=_optional_int(call.arguments.get("expected_revision"))))
        if call.action == "lessons-retire":
            return TranslatedCommand(command=LessonRetireCommand(submission=submission, lesson_id=str(call.arguments.get("lesson_id", "")), actor_id=str(call.arguments.get("actor_id", "")), reason=str(call.arguments.get("reason", "MANUAL")), expected_revision=_optional_int(call.arguments.get("expected_revision"))))

        if call.action == "lesson-inject":
            # Real legacy injects for a specific peer.
            target_peer = str(call.arguments.get("peer") or call.arguments.get("to") or "cc")
            # Pull workspace profile contexts if they are passed in. (Usually handled by runtime context)
            os_val = str(call.arguments.get("os")) if call.arguments.get("os") else None
            shell_val = str(call.arguments.get("shell")) if call.arguments.get("shell") else None
            # We don't have task types in env natively, legacy gets it from workspace-profile. 
            # In peerhub translation, just use what's available or empty.
            return TranslatedCommand(command=LessonInjectCommand(submission=submission, target_peer_id=target_peer, workspace_id=str(call.arguments.get("workspace_id", "default")), os=os_val, shell=shell_val, task_types=frozenset()))

        if call.action == "lesson-broadcast":
            return TranslatedCommand(command=LessonBroadcastCommand(
                submission=submission,
                lesson_id=str(call.arguments.get("lesson_id", "")),
                room_id=str(call.arguments.get("room_id", "")),
                sender_instance_id=str(
                    call.arguments.get("sender_instance_id", "")
                ),
                sender_profile_id=str(
                    call.arguments.get("sender_profile_id", "")
                ),
            ))
        if call.action == "alert-raise":
            legacy_raiser = _first_text(
                call.arguments,
                ("agent", "from", "peer"),
                "unknown",
            )
            return TranslatedCommand(command=AlertRaiseCommand(
                submission=submission,
                room_id=_legacy_room_id(call.arguments, submission.scope),
                raiser_instance_id=_first_text(
                    call.arguments,
                    ("raiser_instance_id", "instance_id"),
                    legacy_raiser,
                ),
                raiser_profile_id=_first_text(
                    call.arguments,
                    ("raiser_profile_id", "profile_id"),
                    legacy_raiser,
                ),
                severity=_first_text(
                    call.arguments, ("severity",), "P1"
                ),
                message=_first_text(
                    call.arguments, ("msg", "message"), ""
                ),
            ))
        if call.action == "lessons-list":
            value = call.arguments.get("scope")
            return TranslatedCommand(command=LessonsListCommand(submission, None if value is None else str(value)))
        if call.action == "proposal-list":
            return TranslatedCommand(command=ProposalListCommand(submission))
        if call.action == "proposal-add":
            return TranslatedCommand(command=ProposalAddCommand(
                submission=submission,
                subject=_first_text(call.arguments, ("subject",), ""),
                from_peer=_first_text(
                    call.arguments, ("from_peer", "from", "peer"), "cc"
                ),
                impact=_first_text(call.arguments, ("impact",), "med"),
                rationale=_first_text(
                    call.arguments, ("rationale", "detail"), ""
                ),
                text=_first_text(call.arguments, ("text",), ""),
            ))
        if call.action == "proposal-vote":
            return TranslatedCommand(command=ProposalVoteCommand(
                submission=submission,
                proposal_id=_first_text(
                    call.arguments, ("proposal_id", "round_id"), ""
                ),
                voter=_first_text(
                    call.arguments, ("voter", "peer", "agent"), "cc"
                ),
                vote=_first_text(call.arguments, ("vote", "choice"), ""),
                reason=_first_text(call.arguments, ("reason",), ""),
            ))
        if call.action == "arbiter-review":
            return TranslatedCommand(command=ArbiterReviewCommand(
                submission=submission,
                round_id=str(call.arguments.get("round_id", "")),
            ))
        if call.action == "register-node":
            return TranslatedCommand(command=RegisterNodeCommand(
                submission=submission,
                node_id=str(call.arguments.get("node_id", "")),
                peer_kind=str(call.arguments.get("peer_kind", "")),
                profile_id=str(call.arguments["profile_id"]) if call.arguments.get("profile_id") is not None else None,
                tier=_int_or_zero(call.arguments.get("tier")) if "tier" in call.arguments and call.arguments["tier"] is not None else 4,
                node_type=str(call.arguments.get("node_type", "agent")),
                actor_id=str(call.arguments.get("actor_id", "")),
            ))
        if call.action == "list-nodes":
            return TranslatedCommand(command=ListNodesCommand(submission))
        if call.action == "model-status":
            return TranslatedCommand(command=ModelStatusCommand(submission))
        if call.action == "feedback-add":
            # Legacy resolves --peer/--from and --subject/--msg at its CLI
            # layer and supplies the defaults there, so they belong in this
            # translation rather than in the domain service.
            source_peer = _first_text(
                call.arguments, ("source_peer", "peer", "from"), "unknown"
            )
            title = _first_text(
                call.arguments, ("title", "subject", "msg"), "unknown gap"
            )
            return TranslatedCommand(command=FeedbackAddCommand(
                submission=submission,
                source_peer=source_peer,
                category=_first_text(
                    call.arguments, ("category",), "other"
                ),
                severity=_first_text(
                    call.arguments, ("severity",), "medium"
                ),
                title=title,
                detail=_first_text(call.arguments, ("detail",), ""),
                actor_id=_first_text(
                    call.arguments, ("actor_id",), submission.actor_id or ""
                ),
            ))
        if call.action == "feedback-list":
            return TranslatedCommand(command=FeedbackListCommand(submission))
        if call.action == "feedback-resolve":
            owner = _optional_first_text(
                call.arguments, ("owner", "agent", "peer")
            )
            return TranslatedCommand(command=FeedbackResolveCommand(
                submission=submission,
                feedback_id=_first_text(
                    call.arguments, ("feedback_id", "round_id"), ""
                ),
                status=_first_text(call.arguments, ("status",), "done"),
                actor_id=_first_text(
                    call.arguments, ("actor_id",), submission.actor_id or ""
                ),
                owner=owner,
            ))
        if call.action == "report-error":
            threshold_value = call.arguments.get("threshold")
            return TranslatedCommand(command=ReportErrorCommand(
                submission=submission,
                peer_key=_first_text(
                    call.arguments, ("peer_key", "peer", "agent"), "unknown"
                ),
                pattern=_first_text(
                    call.arguments, ("pattern", "reason"), "unknown"
                ),
                severity=_first_text(
                    call.arguments, ("severity",), "warn"
                ),
                detail=_first_text(call.arguments, ("detail",), ""),
                actor_id=_first_text(
                    call.arguments, ("actor_id",), submission.actor_id or ""
                ),
                threshold=(
                    3
                    if threshold_value is None
                    else _int_or_zero(threshold_value)
                ),
            ))
        if call.action == "assign-role":
            peer_node_id = call.arguments.get(
                "peer_node_id", call.arguments.get("peer", "")
            )
            return TranslatedCommand(command=AssignRoleCommand(
                submission=submission,
                role=str(call.arguments.get("role", "")),
                peer_node_id=str(peer_node_id),
                actor_id=str(
                    call.arguments.get("actor_id", submission.actor_id)
                ),
            ))
        if call.action == "release-role":
            peer_node_id = call.arguments.get("peer_node_id")
            if peer_node_id is None:
                peer_node_id = (
                    call.arguments.get("peer")
                    or call.arguments.get("agent")
                )
            return TranslatedCommand(command=ReleaseRoleCommand(
                submission=submission,
                role=str(call.arguments.get("role", "")),
                actor_id=str(
                    call.arguments.get("actor_id", submission.actor_id)
                ),
                peer_node_id=(
                    None
                    if peer_node_id is None or peer_node_id == ""
                    else str(peer_node_id)
                ),
            ))
        if call.action == "role-status":
            return TranslatedCommand(command=RoleStatusCommand(submission))
            
        if call.action == "file-lock":
            name = str(call.arguments.get("name", ""))
            owner = str(call.arguments.get("owner", ""))
            lock_scope = str(call.arguments.get("scope", "file"))
            return TranslatedCommand(command=LockAcquireCommand(
                submission=submission,
                name=name,
                owner=owner,
                lock_scope=lock_scope,
            ))
        if call.action == "file-unlock":
            name = str(call.arguments.get("name", ""))
            owner = call.arguments.get("owner")
            return TranslatedCommand(command=LockReleaseCommand(
                submission=submission,
                name=name,
                owner=str(owner) if owner is not None else None,
            ))
        if call.action == "lock-status":
            return TranslatedCommand(command=LockStatusCommand(submission))

        if call.action == "health-check":
            peer = _optional_first_text(
                call.arguments, ("peer", "target", "agent")
            )
            recover = _bool_or_false(call.arguments.get("recover"))
            return TranslatedCommand(command=HealthCheckCommand(
                submission=submission,
                peer=peer,
                recover=recover,
            ))
        if call.action == "peer-status":
            node_id = _optional_first_text(
                call.arguments, ("peer", "target", "agent", "node_id")
            )
            include_all = _bool_or_false(call.arguments.get("all"))
            return TranslatedCommand(command=PeerStatusCommand(
                submission=submission,
                node_id=node_id,
                include_all=include_all,
            ))
        if call.action == "peer-quarantine":
            peer_id = _first_text(
                call.arguments,
                ("peer", "target", "agent", "peer_id"),
                "",
            )
            if not peer_id:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason="peer is required",
                )
            reason = _first_text(
                call.arguments,
                ("reason", "detail"),
                "manual",
            )
            actor_id = _optional_first_text(
                call.arguments,
                ("actor", "actor_id", "caller", "from"),
            )
            return TranslatedCommand(command=PeerQuarantineCommand(
                submission=submission,
                peer_id=peer_id,
                reason=reason,
                actor_id=actor_id,
            ))
        if call.action == "peer-recover":
            peer_id = _first_text(
                call.arguments,
                ("peer", "target", "agent", "peer_id"),
                "all",
            )
            reason = _first_text(
                call.arguments,
                ("reason", "detail"),
                "manual",
            )
            return TranslatedCommand(command=PeerRecoverCommand(
                submission=submission,
                peer_id=peer_id,
                reason=reason,
            ))
        if call.action == "health-precheck":
            peers = _optional_first_text(call.arguments, ("peer", "peers"))
            needs = _optional_first_text(call.arguments, ("needs", "capabilities"))
            return TranslatedCommand(command=HealthPrecheckCommand(
                submission=submission,
                peers=peers,
                needs=needs,
            ))
        if call.action == "check-gate":
            agent = _first_text(
                call.arguments,
                ("agent", "peer", "target"),
                "",
            )
            if not agent:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason="agent is required",
                )
            return TranslatedCommand(command=CheckGateCommand(
                submission=submission,
                agent=agent,
            ))
        if call.action == "health-sweep":
            return TranslatedCommand(command=HealthSweepCommand(
                submission=submission,
            ))
        if call.action == "lease-status":
            return TranslatedCommand(command=LeaseStatusCommand(
                submission=submission,
            ))
        if call.action == "lease-sweep":
            raw_limit = call.arguments.get("limit")
            limit = 100 if raw_limit is None else _optional_int(raw_limit)
            if limit is None or limit < 1:
                return InvalidLegacyArguments(
                    action=call.action,
                    reason="limit must be a positive integer",
                )
            raw_reap = call.arguments.get("reap")
            reap = (
                raw_reap
                if isinstance(raw_reap, bool)
                else not _bool_or_false(call.arguments.get("no_reap"))
            )
            return TranslatedCommand(command=LeaseSweepCommand(
                submission=submission,
                limit=limit,
                reap=reap,
            ))

        return KnownLegacyActionNotBacked(
            legacy_action=call.action,
            target_method=target,
            ledger_status="INVENTORIED",
            reason="no PeerHub handler with semantic backing",
        )
