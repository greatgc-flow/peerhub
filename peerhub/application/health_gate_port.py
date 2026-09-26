"""Health-gate port: is a voter node's peer profile currently open for admission?"""

from __future__ import annotations

from peerhub.application.peer_registry import PeerRegistryService
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.health.service import HealthService
from peerhub.health.contract import AdmissionState, AvailabilityState


class PeerHealthGatePort:
    """Implements ``HealthIdentityPort`` and the proposal voter gate with one rule."""

    def __init__(self, peer_registry: PeerRegistryService, health: HealthService) -> None:
        self._peer_registry = peer_registry
        self._health = health

    def check_health_gate(self, actor_id: str, evaluated_at: int) -> bool:
        try:
            node = self._peer_registry.get_node(actor_id)
        except RecordNotFoundError:
            return False
        peer_kind = node.state.get("peer_kind")
        profile_id = node.state.get("profile_id")
        if not isinstance(peer_kind, str) or not peer_kind:
            raise InvalidMutationError("peer node has malformed peer_kind")
        if not isinstance(profile_id, str) or not profile_id:
            raise InvalidMutationError("peer node has malformed profile_id")
        projection = self._health.read_health_projection(
            peer_kind, profile_id, evaluated_at=evaluated_at
        )
        return (
            projection is not None
            and projection.effective_availability_state
            not in {AvailabilityState.UNAVAILABLE, AvailabilityState.STALE}
            and projection.effective_admission_state is AdmissionState.OPEN
            and not projection.profile_gate_backed_off
        )
