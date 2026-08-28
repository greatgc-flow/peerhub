"""Peer node registry and discovery service."""

from __future__ import annotations

from collections.abc import Sequence

from peerhub.adapters.registry import resolve_peer_target
from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.core.protocol import CommandID, JsonValue
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import EffectIntent, MutationRequest, MutationSubmission, TargetState

_BASE_NODE_IDS = ("cc", "ag", "cx")


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
        if target is None:
            raise RecordNotFoundError("peer-node", node_id)
        return target

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
