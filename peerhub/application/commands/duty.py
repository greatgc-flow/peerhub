"""Terminal-duty lease commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


@dataclass(frozen=True, slots=True)
class TerminalClaimCommand(Command[Any]):
    method: ClassVar[str] = "coordination.terminal.claim"
    submission: SubmissionMetadata
    room_id: str
    instance_id: str
    profile_id: str
    owner_principal_id: str
    authority_epoch: int
    heartbeat_timeout_ms: int

    def encode_params(self) -> Mapping[str, JsonValue]:
        return {
            "room_id": self.room_id,
            "instance_id": self.instance_id,
            "profile_id": self.profile_id,
            "owner_principal_id": self.owner_principal_id,
            "authority_epoch": self.authority_epoch,
            "heartbeat_timeout_ms": self.heartbeat_timeout_ms,
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
