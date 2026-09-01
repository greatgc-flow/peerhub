"""Peer node registry and discovery service."""

from __future__ import annotations

from collections.abc import Sequence

from peerhub.adapters.registry import resolve_peer_adapter, resolve_peer_target
from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.protocol import CommandID, JsonValue, require_text
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import EffectIntent, MutationRequest, MutationSubmission, TargetState
from peerhub.health.contract import AdmissionState, AvailabilityState
from peerhub.health.service import HealthService

_BASE_NODE_IDS = ("cc", "ag", "cx")

_LEGACY_STATUS_BY_AVAILABILITY = {
    AvailabilityState.UNKNOWN: "UNKNOWN",
    AvailabilityState.PROBING: "UNKNOWN",
    AvailabilityState.HEALTHY: "GREEN",
    AvailabilityState.DEGRADED: "YELLOW",
    AvailabilityState.UNAVAILABLE: "RED",
    AvailabilityState.STALE: "STALE",
}

_CLOSED_ADMISSION_STATES = {
    AdmissionState.COOLDOWN,
    AdmissionState.RECOVERY_REQUIRED,
    AdmissionState.QUARANTINED,
}


def _is_known_cli_name(name: str) -> bool:
    """True if ``name`` already resolves through the real adapter registry.

    Covers both peer kinds (``cc``/``ag``/``cx``) and their CLI aliases
    (``claude``/``agy``/``codex``) without importing registry.py's private
    ``_CLI_ALIASES`` mapping across a module boundary.
    """

    try:
        resolve_peer_target(name)
    except ValueError:
        return False
    return True


class PeerRegistryService:
    def __init__(
        self,
        broker: GovernanceBroker,
        *,
        clock: Clock,
        ids: IdSource,
    ) -> None:
        self._broker = broker
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
        request_id = self._ids.new_id("peer-registry-request")
        return self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(self._ids.new_id("peer-registry-command")),
                correlation_id=self._ids.new_id("peer-registry-correlation"),
                client_id="peerhub.peer-registry",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="protocol-v2",
                target_id=target_id,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=desired_state,
                effect_intent=EffectIntent(kind="peer-registry.noop", payload={}),
            )
        )

    def register_node(
        self,
        *,
        node_id: str,
        peer_kind: str,
        profile_id: str | None = None,
        tier: int = 4,
        node_type: str = "agent",
        actor_id: str,
    ) -> MutationSubmission:
        node_id = require_text(node_id, "node_id")
        actor_id = require_text(actor_id, "actor_id")
        if node_type == "virtual":
            raise InvalidMutationError("node_type 'virtual' is not supported")
        if _is_known_cli_name(node_id):
            raise InvalidMutationError(
                f"node_id {node_id!r} collides with a base adapter or CLI alias"
            )

        # Validates peer_kind/profile_id against the real adapter registry;
        # raises ValueError/ProfileNotFoundError for anything unrecognized.
        target = resolve_peer_target(peer_kind, profile_id=profile_id)

        target_id = f"peer-node:{node_id}"
        current = self._broker.get_target(target_id)
        expected_revision = 0 if current is None else current.revision
        now = self._clock.now()

        desired_state: dict[str, JsonValue] = {
            "kind": "peer-node",
            "scope": None,
            "schema_version": 1,
            "node_id": node_id,
            "peer_kind": target.peer_kind,
            "profile_id": target.profile.profile_id,
            "adapter_version": target.adapter.descriptor.adapter_version,
            "tier": tier,
            "node_type": node_type,
            "registered_at": now if current is None else current.state.get("registered_at", now),
            "registered_by": actor_id if current is None else current.state.get("registered_by", actor_id),
            "updated_at": now,
        }

        return self._submit(
            target_id=target_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
            operation="peer-registry.node.register",
            desired_state=desired_state,
        )

    def get_node(self, node_id: str) -> TargetState:
        target = self._broker.get_target(f"peer-node:{node_id}")
        if target is not None:
            return target
        base = self._base_node(node_id)
        if base is not None:
            return base
        raise RecordNotFoundError("peer-node", node_id)

    def _base_node(self, node_id: str) -> TargetState | None:
        try:
            target = resolve_peer_target(node_id)
        except ValueError:
            return None
        return TargetState(
            target_id=f"peer-node:{node_id}",
            revision=1,
            updated_at=0,
            state={
                "kind": "peer-node",
                "scope": None,
                "schema_version": 1,
                "node_id": node_id,
                "peer_kind": target.peer_kind,
                "profile_id": target.profile.profile_id,
                "adapter_version": target.adapter.descriptor.adapter_version,
                "tier": 4,
                "node_type": "agent",
                "registered_at": 0,
                "registered_by": "system",
                "updated_at": 0,
                "source": "adapter-registry",
            },
        )

    def list_nodes(self) -> Sequence[TargetState]:
        base_nodes = {
            node_id: base
            for node_id in _BASE_NODE_IDS
            if (base := self._base_node(node_id)) is not None
        }
        registered = self._broker.list_targets("peer-node", None)

        result: list[TargetState] = list(base_nodes.values())
        seen = set(base_nodes)
        for reg_node in registered:
            node_id = str(reg_node.state["node_id"])
            if node_id not in seen:
                result.append(reg_node)
                seen.add(node_id)

        result.sort(key=lambda item: item.target_id)
        return tuple(result)

    @staticmethod
    def _profile_binding_target_id(node_id: str, profile_id: str) -> str:
        return f"peer-profile-binding:{node_id}:{profile_id}"

    def bind_profile(
        self,
        *,
        node_id: str,
        profile_id: str,
        model_id: str,
        reasoning_effort: str | None = None,
        actor_id: str,
    ) -> MutationSubmission:
        """Create or replace one instance/profile model-pin binding."""

        normalized_node_id = require_text(node_id, "node_id")
        normalized_profile_id = require_text(profile_id, "profile_id")
        normalized_model_id = require_text(model_id, "model_id")
        normalized_effort = (
            None
            if reasoning_effort is None
            else require_text(reasoning_effort, "reasoning_effort")
        )
        normalized_actor_id = require_text(actor_id, "actor_id")
        target_id = self._profile_binding_target_id(
            normalized_node_id,
            normalized_profile_id,
        )
        current = self._broker.get_target(target_id)
        now = self._clock.now()
        desired_state: dict[str, JsonValue] = {
            "kind": "peer-profile-binding",
            "scope": normalized_node_id,
            "schema_version": 1,
            "binding_id": target_id,
            "node_id": normalized_node_id,
            "profile_id": normalized_profile_id,
            "model_id": normalized_model_id,
            "reasoning_effort": normalized_effort,
            "updated_at": now,
            "updated_by": normalized_actor_id,
        }
        return self._submit(
            target_id=target_id,
            expected_revision=0 if current is None else current.revision,
            actor_id=normalized_actor_id,
            operation="peer-registry.profile.bind",
            desired_state=desired_state,
        )

    def get_profile_binding(
        self,
        node_id: str,
        profile_id: str,
    ) -> TargetState | None:
        normalized_node_id = require_text(node_id, "node_id")
        normalized_profile_id = require_text(profile_id, "profile_id")
        return self._broker.get_target(
            self._profile_binding_target_id(
                normalized_node_id,
                normalized_profile_id,
            )
        )

    def list_profile_bindings(
        self,
        node_id: str | None = None,
    ) -> Sequence[TargetState]:
        normalized_node_id = (
            None if node_id is None else require_text(node_id, "node_id")
        )
        return self._broker.list_targets(
            "peer-profile-binding",
            normalized_node_id,
        )


def collect_model_status(
    registry: PeerRegistryService,
    health: HealthService | None,
) -> Sequence[dict[str, JsonValue]]:
    """Join registered nodes, profile bindings, and live health evidence.

    The legacy health profile included vendor-specific context-window and
    capability fields. Native ``HealthProjectionSnapshot`` deliberately does
    not persist those fields. Context therefore remains blank until a typed
    native source exists; adapter-declared capabilities are exposed only when
    health evidence for the pair exists, preserving legacy's health fallback
    boundary without making the adapter descriptor a liveness claim.
    """

    rows: list[dict[str, JsonValue]] = []
    for node in registry.list_nodes():
        node_id_value = node.state.get("node_id")
        peer_kind_value = node.state.get("peer_kind")
        default_profile_value = node.state.get("profile_id")
        if not isinstance(node_id_value, str) or not node_id_value:
            raise InvalidMutationError("peer node has malformed node_id")
        if not isinstance(peer_kind_value, str) or not peer_kind_value:
            raise InvalidMutationError("peer node has malformed peer_kind")
        if not isinstance(default_profile_value, str) or not default_profile_value:
            raise InvalidMutationError("peer node has malformed profile_id")

        bindings = registry.list_profile_bindings(node_id_value)
        binding_rows: Sequence[TargetState | None] = (
            tuple(bindings) if bindings else (None,)
        )
        for binding in binding_rows:
            if binding is None:
                profile_id = default_profile_value
                model_id = ""
                reasoning_effort = ""
            else:
                profile_value = binding.state.get("profile_id")
                model_value = binding.state.get("model_id")
                effort_value = binding.state.get("reasoning_effort")
                if not isinstance(profile_value, str) or not profile_value:
                    raise InvalidMutationError(
                        "peer profile binding has malformed profile_id"
                    )
                if not isinstance(model_value, str) or not model_value:
                    raise InvalidMutationError(
                        "peer profile binding has malformed model_id"
                    )
                if effort_value is not None and not isinstance(effort_value, str):
                    raise InvalidMutationError(
                        "peer profile binding has malformed reasoning_effort"
                    )
                profile_id = profile_value
                model_id = model_value
                reasoning_effort = effort_value or ""

            health_read = (
                None
                if health is None
                else health.read_health_projection(
                    peer_kind_value,
                    profile_id,
                )
            )
            if health_read is None:
                status = "UNKNOWN"
                capabilities = ""
            else:
                status = (
                    "RED"
                    if health_read.effective_admission_state
                    in _CLOSED_ADMISSION_STATES
                    else _LEGACY_STATUS_BY_AVAILABILITY[
                        health_read.effective_availability_state
                    ]
                )
                descriptor = resolve_peer_adapter(peer_kind_value).descriptor
                capabilities = ",".join(
                    sorted(capability.value for capability in descriptor.capabilities)
                )

            rows.append(
                {
                    "peer": node_id_value,
                    "status": status,
                    "profile": profile_id,
                    "model": model_id,
                    "effort": reasoning_effort,
                    "cost": "",
                    "context": "",
                    "capabilities": capabilities,
                }
            )
    return tuple(rows)


def collect_peer_status(
    registry: PeerRegistryService,
    health: HealthService | None,
    *,
    node_id: str | None = None,
    include_all: bool = False,
    now: int | None = None,
) -> Sequence[dict[str, JsonValue]]:
    """Join registered nodes, health projections, and adapter version information."""

    if node_id is not None:
        try:
            target_node = registry.get_node(node_id)
            nodes = [target_node]
        except RecordNotFoundError:
            nodes = []
    else:
        nodes = list(registry.list_nodes())

    rows: list[dict[str, JsonValue]] = []
    for node in nodes:
        node_id_val = str(node.state["node_id"])
        peer_kind_val = str(node.state["peer_kind"])
        profile_id_val = str(node.state["profile_id"])
        lifecycle_val = str(node.state.get("lifecycle", "active"))
        node_type_val = str(node.state.get("node_type", "agent"))

        health_read = (
            None
            if health is None
            else health.read_health_projection(
                peer_kind_val,
                profile_id_val,
                evaluated_at=now,
            )
        )
        is_backed_off = (
            False
            if health is None
            else health.is_profile_gate_backed_off(
                profile_id_val,
                evaluated_at=now if now is not None else health.current_time(),
            )
        )

        if health_read is None:
            health_str = "UNKNOWN"
            gate_str = "closed"
        else:
            if (
                health_read.effective_admission_state in _CLOSED_ADMISSION_STATES
                or is_backed_off
            ):
                gate_str = "closed"
            else:
                gate_str = "enabled"

            if health_read.effective_admission_state in _CLOSED_ADMISSION_STATES:
                health_str = "RED"
            else:
                health_str = _LEGACY_STATUS_BY_AVAILABILITY.get(
                    health_read.effective_availability_state,
                    "UNKNOWN",
                )

        try:
            target = resolve_peer_target(peer_kind_val, profile_id=profile_id_val)
            version_val = str(target.adapter.descriptor.adapter_version)
        except Exception:
            version_val = "unknown"

        rows.append(
            {
                "peer": node_id_val,
                "lifecycle": lifecycle_val,
                "gate": gate_str,
                "health": health_str,
                "version": version_val,
                "details": node_type_val,
            }
        )
    return tuple(rows)

