"""A2A Remote Adapter (M3.2).

Implements Core Invariants 5 & 6:
- A2A remote identities are strictly quarantined and never become PeerHub identities implicitly.
- Agent Card declarations are treated as untrusted claims distinct from measured operational reality.
- Remote execution protocol state captured as opaque ExternalExecutionRef.
- Pure Python standard library implementation (REL-009).
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import Any


class A2AError(Exception):
    """Base exception for A2A adapter."""


class A2AAgentNotFoundError(A2AError):
    """Raised when target remote agent card is not found."""


class A2AIdentityLeakForbiddenError(A2AError):
    """Raised when unmapped remote identity is used in native context (Invariant 5)."""


class A2ACardValidationError(A2AError):
    """Raised when an AgentCard fails schema validation."""


class A2AStateTransitionError(A2AError):
    """Raised on invalid A2A task lifecycle transition."""


class A2AExecutionTimeoutError(A2AError):
    """Raised when remote task times out."""


@dataclasses.dataclass(frozen=True)
class AgentCard:
    agent_id: str
    name: str
    description: str
    declared_capabilities: list[str]
    endpoint_url: str
    version: str = "1.0.0"
    metadata: dict[str, Any] = dataclasses.field(default_factory=lambda: dict[str, Any]())

    def __post_init__(self) -> None:
        if not self.agent_id or not self.agent_id.strip():
            raise A2ACardValidationError("AgentCard 'agent_id' cannot be empty.")
        if not self.endpoint_url or not self.endpoint_url.strip():
            raise A2ACardValidationError("AgentCard 'endpoint_url' cannot be empty.")

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentCard:
        raw_caps = data.get("declared_capabilities", [])
        caps = [str(c) for c in raw_caps]
        return cls(
            agent_id=str(data["agent_id"]),
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            declared_capabilities=caps,
            endpoint_url=str(data["endpoint_url"]),
            version=str(data.get("version", "1.0.0")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclasses.dataclass(frozen=True)
class ExternalExecutionRef:
    remote_system: str
    remote_task_id: str
    remote_run_id: str
    opaque_state: dict[str, Any] = dataclasses.field(default_factory=lambda: dict[str, Any]())

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExternalExecutionRef:
        return cls(
            remote_system=str(data["remote_system"]),
            remote_task_id=str(data["remote_task_id"]),
            remote_run_id=str(data["remote_run_id"]),
            opaque_state=dict(data.get("opaque_state", {})),
        )


@dataclasses.dataclass(frozen=True)
class A2ATaskRequest:
    task_id: str
    target_agent_id: str
    action: str
    input_parameters: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class A2ATaskResponse:
    task_id: str
    status: str  # SUBMITTED | RUNNING | COMPLETED | FAILED | CANCELLED
    external_ref: ExternalExecutionRef
    output: dict[str, Any] | None = None
    error: str | None = None
    updated_at: str = dataclasses.field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    def to_record_evidence(self) -> dict[str, Any]:
        """Convert A2A task response to clean PeerHub record evidence payload."""
        return {
            "task_id": self.task_id,
            "status": self.status,
            "external_ref": self.external_ref.to_dict(),
            "output": self.output,
            "error": self.error,
            "updated_at": self.updated_at,
        }


class A2AAdapter:
    """Manages remote agent cards, identity isolation, and task bridging."""

    def __init__(self) -> None:
        self._cards: dict[str, AgentCard] = {}
        self._identity_mappings: dict[str, str] = {}  # remote_id -> local_peer_id
        self._tasks: dict[str, A2ATaskResponse] = {}
        self._observations: dict[str, list[tuple[bool, float]]] = {}  # agent_id -> [(success, latency_ms)]

    def register_agent_card(self, card: AgentCard) -> None:
        self._cards[card.agent_id] = card

    def get_agent_card(self, agent_id: str) -> AgentCard | None:
        return self._cards.get(agent_id)

    def list_agent_cards(self) -> list[AgentCard]:
        return list(self._cards.values())

    def configure_identity_mapping(self, remote_agent_id: str, local_peer_id: str) -> None:
        """Explicitly authorize mapping of a remote agent to a local peer ID."""
        self._identity_mappings[remote_agent_id] = local_peer_id

    def resolve_local_peer_identity(self, remote_agent_id: str) -> str | None:
        """Resolve mapped local peer ID or None if unmapped (quarantined)."""
        return self._identity_mappings.get(remote_agent_id)

    def assert_valid_local_peer_identity(self, remote_agent_id: str) -> str:
        """Assert explicit mapping exists, preventing implicit identity leakage (Invariant 5)."""
        local_id = self.resolve_local_peer_identity(remote_agent_id)
        if local_id is None:
            raise A2AIdentityLeakForbiddenError(
                f"Remote agent '{remote_agent_id}' has no explicit local peer mapping. "
                f"Implicit identity promotion is forbidden (Invariant 5)."
            )
        return local_id

    def record_observation(self, agent_id: str, success: bool, latency_ms: float) -> None:
        """Record empirical measurement for an agent."""
        if agent_id not in self._observations:
            self._observations[agent_id] = []
        self._observations[agent_id].append((success, latency_ms))

    def get_agent_status(self, agent_id: str) -> dict[str, Any]:
        """Return dual-view: declared card vs measured reality (Invariant 6)."""
        card = self._cards.get(agent_id)
        obs = self._observations.get(agent_id, [])

        declared_info: dict[str, Any] = {}
        if card:
            declared_info = {
                "name": card.name,
                "capabilities": card.declared_capabilities,
                "metadata": card.metadata,
                "endpoint": card.endpoint_url,
            }

        if not obs:
            measured_info = {
                "is_verified": False,
                "sample_count": 0,
                "observed_success_rate": None,
                "is_healthy": None,
            }
        else:
            total = len(obs)
            successes = sum(1 for s, _ in obs if s)
            rate = successes / total
            is_healthy = rate >= 0.8
            measured_info = {
                "is_verified": True,
                "sample_count": total,
                "observed_success_rate": rate,
                "is_healthy": is_healthy,
            }

        return {
            "agent_id": agent_id,
            "declared": declared_info,
            "measured": measured_info,
        }

    def dispatch_task(self, request: A2ATaskRequest) -> A2ATaskResponse:
        card = self._cards.get(request.target_agent_id)
        if card is None:
            raise A2AAgentNotFoundError(
                f"Cannot dispatch task '{request.task_id}': target agent '{request.target_agent_id}' not found."
            )

        ref = ExternalExecutionRef(
            remote_system=request.target_agent_id,
            remote_task_id=f"rmt-{request.task_id}",
            remote_run_id=f"run-{datetime.datetime.now(datetime.timezone.utc).timestamp()}",
            opaque_state={"action": request.action},
        )

        resp = A2ATaskResponse(
            task_id=request.task_id,
            status="SUBMITTED",
            external_ref=ref,
        )
        self._tasks[request.task_id] = resp
        return resp

    def poll_task(self, task_id: str) -> A2ATaskResponse:
        if task_id not in self._tasks:
            raise A2AAgentNotFoundError(f"Task '{task_id}' not found.")
        return self._tasks[task_id]

    def update_task_status(
        self,
        task_id: str,
        new_status: str,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> A2ATaskResponse:
        current = self.poll_task(task_id)

        # State transition validation
        if current.status in ("COMPLETED", "FAILED", "CANCELLED"):
            raise A2AStateTransitionError(
                f"Cannot transition terminal task '{task_id}' from '{current.status}' to '{new_status}'."
            )

        updated = A2ATaskResponse(
            task_id=current.task_id,
            status=new_status,
            external_ref=current.external_ref,
            output=output if output is not None else current.output,
            error=error if error is not None else current.error,
        )
        self._tasks[task_id] = updated
        return updated

    def cancel_task(self, task_id: str) -> A2ATaskResponse:
        return self.update_task_status(task_id, "CANCELLED")
