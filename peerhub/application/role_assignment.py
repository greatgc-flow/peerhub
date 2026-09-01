"""Durable, workspace-scoped role assignments."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from peerhub.application.peer_registry import PeerRegistryService
from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError
from peerhub.core.protocol import CommandID, ErrorCode, JsonValue, require_text
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import (
    EffectIntent,
    MutationRequest,
    MutationSubmission,
    TargetState,
)
from peerhub.health.contract import AdmissionState, AvailabilityState
from peerhub.health.service import HealthService


class RoleReleaseDisposition(StrEnum):
    RELEASED = "RELEASED"
    NOT_ASSIGNED = "NOT_ASSIGNED"


@dataclass(frozen=True, slots=True)
class RoleReleaseResult:
    disposition: RoleReleaseDisposition
    submission: MutationSubmission | None
    target: TargetState | None


class RoleAssigneeUnavailableError(InvalidMutationError):
    """A role target has explicit health evidence that denies assignment."""

    error_code = ErrorCode.PEER_UNAVAILABLE

    def __init__(
        self,
        peer_node_id: str,
        availability_state: AvailabilityState,
        admission_state: AdmissionState,
        *,
        stale: bool = False,
        profile_gate_backed_off: bool = False,
    ) -> None:
        self.peer_node_id = peer_node_id
        self.availability_state = availability_state
        self.admission_state = admission_state
        self.stale = stale
        if stale:
            status = AvailabilityState.STALE.value
        elif availability_state in {
            AvailabilityState.UNAVAILABLE,
            AvailabilityState.STALE,
        }:
            status = availability_state.value
        elif admission_state != AdmissionState.OPEN:
            status = admission_state.value
        else:
            status = "BACKED_OFF"
        self.status = status
        super().__init__(
            (
                "cannot assign role to unhealthy peer "
                f"{peer_node_id} status={status}"
            ),
            details={
                "peer_node_id": peer_node_id,
                "availability_state": availability_state.value,
                "admission_state": admission_state.value,
                "stale": stale,
            },
        )


class RoleAssignmentService:
    """Assign and release non-expiring workspace roles."""

    _DENIED_AVAILABILITY = {
        AvailabilityState.UNAVAILABLE,
        AvailabilityState.STALE,
    }
    _DENIED_ADMISSION = {
        AdmissionState.QUARANTINED,
        AdmissionState.COOLDOWN,
        AdmissionState.RECOVERY_REQUIRED,
    }

    def __init__(
        self,
        broker: GovernanceBroker,
        *,
        peer_registry: PeerRegistryService,
        health: HealthService,
        clock: Clock,
        ids: IdSource,
    ) -> None:
        self._broker = broker
        self._peer_registry = peer_registry
        self._health = health
        self._clock = clock
        self._ids = ids

    def _submit(
        self,
        *,
        target_id: str,
        expected_revision: int,
        actor_id: str,
        operation: str,
        desired_state: dict[str, JsonValue],
    ) -> MutationSubmission:
        request_id = self._ids.new_id("role-assignment-request")
        return self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(
                    self._ids.new_id("role-assignment-command")
                ),
                correlation_id=self._ids.new_id(
                    "role-assignment-correlation"
                ),
                client_id="peerhub.role-assignment",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=desired_state,
                effect_intent=EffectIntent(
                    kind="role-assignment.noop",
                    payload={},
                ),
            )
        )

    @staticmethod
    def _target_id(role: str) -> str:
        return f"role-assignment:{role}"

    @staticmethod
    def _required_state_text(target: TargetState, field: str) -> str:
        value = target.state.get(field)
        if not isinstance(value, str) or not value:
            raise InvalidMutationError(
                f"peer node has malformed {field}"
            )
        return value

    def assign_role(
        self,
        *,
        role: str,
        peer_node_id: str,
        actor_id: str,
    ) -> MutationSubmission:
        normalized_role = require_text(role, "role")
        normalized_peer_node_id = require_text(
            peer_node_id, "peer_node_id"
        )
        normalized_actor_id = require_text(actor_id, "actor_id")

        peer_node = self._peer_registry.get_node(
            normalized_peer_node_id
        )
        peer_kind = self._required_state_text(peer_node, "peer_kind")
        profile_id = self._required_state_text(peer_node, "profile_id")
        adapter_version = self._required_state_text(
            peer_node, "adapter_version"
        )

        now = self._clock.now()
        health_read = self._health.read_health_projection(
            peer_kind,
            profile_id,
            evaluated_at=now,
        )
        if health_read is not None:
            is_stale = health_read.stale_at_read
            if (
                is_stale
                or health_read.effective_availability_state
                in self._DENIED_AVAILABILITY
                or health_read.effective_admission_state in self._DENIED_ADMISSION
                or health_read.profile_gate_backed_off
            ):
                raise RoleAssigneeUnavailableError(
                    normalized_peer_node_id,
                    health_read.effective_availability_state,
                    health_read.effective_admission_state,
                    stale=is_stale,
                    profile_gate_backed_off=health_read.profile_gate_backed_off,
                )
            projection = health_read.projection
            health_basis: dict[str, JsonValue] = {
                "projection_id": projection.projection_id,
                "projection_revision": projection.revision,
                "availability_state": (
                    health_read.effective_availability_state.value
                ),
                "admission_state": health_read.effective_admission_state.value,
            }
        else:
            # Legacy UNKNOWN health is fail-open. Keep absence explicit rather
            # than inventing a healthy projection that was never observed.
            health_basis = {
                "projection_id": None,
                "projection_revision": None,
                "availability_state": None,
                "admission_state": None,
            }

        target_id = self._target_id(normalized_role)
        current = self._broker.get_target(target_id)
        desired_state: dict[str, JsonValue] = {
            "kind": "role-assignment",
            "scope": None,
            "schema_version": 1,
            "role": normalized_role,
            "status": "ACTIVE",
            "assignment_id": self._ids.new_id("role-assignment-instance"),
            "peer_node_id": normalized_peer_node_id,
            "peer_node_target_id": peer_node.target_id,
            "peer_node_revision": peer_node.revision,
            "peer_kind": peer_kind,
            "profile_id": profile_id,
            "adapter_version": adapter_version,
            "health_basis": health_basis,
            "assigned_at": now,
            "assigned_by": normalized_actor_id,
            "released_at": None,
            "released_by": None,
            "updated_at": now,
        }
        return self._submit(
            target_id=target_id,
            expected_revision=0 if current is None else current.revision,
            actor_id=normalized_actor_id,
            operation="role-assignment.assign",
            desired_state=desired_state,
        )

    def release_role(
        self,
        *,
        role: str,
        actor_id: str,
        peer_node_id: str | None = None,
    ) -> RoleReleaseResult:
        normalized_role = require_text(role, "role")
        normalized_actor_id = require_text(actor_id, "actor_id")
        normalized_peer_node_id = (
            None
            if peer_node_id is None
            else require_text(peer_node_id, "peer_node_id")
        )
        target_id = self._target_id(normalized_role)
        current = self._broker.get_target(target_id)
        if current is None or current.state.get("status") != "ACTIVE":
            return RoleReleaseResult(
                disposition=RoleReleaseDisposition.NOT_ASSIGNED,
                submission=None,
                target=current,
            )

        current_peer_node_id = current.state.get("peer_node_id")
        if not isinstance(current_peer_node_id, str):
            raise InvalidMutationError(
                "role assignment has malformed peer_node_id"
            )
        if (
            normalized_peer_node_id is not None
            and normalized_peer_node_id != current_peer_node_id
        ):
            raise InvalidMutationError(
                f"role {normalized_role} belongs to "
                f"{current_peer_node_id}, not {normalized_peer_node_id}"
            )

        now = self._clock.now()
        desired_state: dict[str, JsonValue] = {
            **dict(current.state),
            "status": "RELEASED",
            "released_at": now,
            "released_by": normalized_actor_id,
            "updated_at": now,
        }
        submission = self._submit(
            target_id=target_id,
            expected_revision=current.revision,
            actor_id=normalized_actor_id,
            operation="role-assignment.release",
            desired_state=desired_state,
        )
        target = self._broker.get_target(target_id)
        if target is None:
            raise RuntimeError(
                "committed role release target could not be re-read"
            )
        return RoleReleaseResult(
            disposition=RoleReleaseDisposition.RELEASED,
            submission=submission,
            target=target,
        )

    def get_role(self, role: str) -> TargetState | None:
        normalized_role = require_text(role, "role")
        target = self._broker.get_target(self._target_id(normalized_role))
        if target is None or target.state.get("status") != "ACTIVE":
            return None
        return target

    def list_roles(self) -> Sequence[TargetState]:
        return tuple(
            target
            for target in self._broker.list_targets(
                "role-assignment", None
            )
            if target.state.get("status") == "ACTIVE"
        )
