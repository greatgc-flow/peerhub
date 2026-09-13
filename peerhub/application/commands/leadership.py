"""Leadership and candidate-routing commands."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.core.protocol import JsonValue


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
