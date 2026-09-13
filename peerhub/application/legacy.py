"""Deprecated compatibility exports for former ``application.legacy`` users.

Native command definitions live in ``peerhub.application.commands`` by domain.
This module remains for known external imports during the migration.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Any

from peerhub.core.protocol import JsonValue
from peerhub.application.commands import (
    Command,
    SubmissionMetadata,
)
from peerhub.application.commands import consensus as _consensus_commands
from peerhub.application.commands import dispatch as _dispatch_commands
from peerhub.application import compatibility as _compatibility

# Preserve known imports without retaining native definitions in this module.
ConsensusCheckCommand = _consensus_commands.ConsensusCheckCommand
ConsensusProposeCommand = _consensus_commands.ConsensusProposeCommand
ConsensusSweepCommand = _consensus_commands.ConsensusSweepCommand
ConsensusVoteCommand = _consensus_commands.ConsensusVoteCommand
SubmitCoordinatorDispatch = _dispatch_commands.SubmitCoordinatorDispatch
SubmitDispatch = _dispatch_commands.SubmitDispatch
SubmitManyDispatch = _dispatch_commands.SubmitManyDispatch
legacy_room_id = _compatibility.legacy_room_id
legacy_thread_slug = _compatibility.legacy_thread_slug


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
class DiscoverCandidatesCommand(Command[Any]):
    method: ClassVar[str] = "routing.candidate.discover"
    submission: SubmissionMetadata
    needs: str
    effort: str = "mid"

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"needs": self.needs, "effort": self.effort}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ElectLeaderCommand(Command[Any]):
    method: ClassVar[str] = "routing.leadership.elect"
    submission: SubmissionMetadata
    actor_id: str
    needs: str = "general"
    effort: str = "mid"
    reason: str = ""

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "actor_id": self.actor_id,
            "needs": self.needs,
            "effort": self.effort,
            "reason": self.reason,
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
class EffectStatusCommand(Command[Any]):
    method: ClassVar[str] = "governance.effect.status"
    submission: SubmissionMetadata
    limit: int = 20

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"limit": self.limit}

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
class ArtifactClaimCommand(Command[Any]):
    method: ClassVar[str] = "governance.artifact.claim"
    submission: SubmissionMetadata
    name: str
    owner: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"name": self.name, "owner": self.owner}

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ArtifactStatusCommand(Command[Any]):
    method: ClassVar[str] = "governance.artifact.status"
    submission: SubmissionMetadata
    name: str | None = None
    peer: str | None = None
    draft_path: str | None = None

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "name": self.name,
            "peer": self.peer,
            "draft_path": self.draft_path,
        }

    @classmethod
    def decode_result(cls, value: Mapping[str, JsonValue]) -> Any:
        return value


@dataclass(frozen=True, slots=True)
class ArtifactFinalizeCommand(Command[Any]):
    method: ClassVar[str] = "governance.artifact.finalize"
    submission: SubmissionMetadata
    name: str
    file_path: str

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {"name": self.name, "file_path": self.file_path}

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
