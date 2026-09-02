"""SQLite-backed capability configuration and one-time legacy import."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError
from peerhub.core.protocol import (
    CommandID,
    JsonValue,
    canonical_json_bytes,
    require_text,
)
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import (
    EffectIntent,
    MutationRequest,
    TargetState,
)
from peerhub.health.contract import AvailabilityState
from peerhub.routing.capability_matching import (
    CapabilityMatchingPolicy,
    ConfiguredCapability,
    DefaultProposerPolicy,
)


CAPABILITY_POLICY_TARGET_ID = "routing-policy:capability-native-v1:1"
_BASE_NODE_IDS = ("cc", "ag", "cx")
_DEFAULT_HEALTH_SUBDIRS = {
    "cc": "claude",
    "ag": "antigravity",
    "cx": "codex",
}


@dataclass(frozen=True, slots=True)
class PeerCapabilityConfig:
    node_id: str
    enabled: bool
    aliases: tuple[str, ...]
    capabilities: tuple[ConfiguredCapability, ...]
    target_id: str
    revision: int
    updated_at: int
    updated_by: str


@dataclass(frozen=True, slots=True)
class CapabilityConfigImportResult:
    configs: tuple[PeerCapabilityConfig, ...]
    policy: CapabilityMatchingPolicy


def _canonical_digest(value: Mapping[str, JsonValue]) -> str:
    import hashlib

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidMutationError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _string_sequence(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise InvalidMutationError(f"{name} must be a sequence of strings")
    items = cast("Sequence[object]", value)
    if not all(isinstance(item, str) and item for item in items):
        raise InvalidMutationError(f"{name} must be a sequence of strings")
    return tuple(cast(str, item) for item in items)


class CapabilityConfigService:
    """Own authoritative per-node skill claims and the native-v1 policy."""

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

    @staticmethod
    def target_id(node_id: str) -> str:
        return f"peer-capability-config:{require_text(node_id, 'node_id')}"

    def _submit(
        self,
        *,
        target_id: str,
        expected_revision: int,
        actor_id: str,
        operation: str,
        state: Mapping[str, JsonValue],
    ) -> TargetState:
        request_id = self._ids.new_id("capability-config-request")
        self._broker.submit(
            MutationRequest(
                request_id=request_id,
                command_id=CommandID(
                    self._ids.new_id("capability-config-command")
                ),
                correlation_id=self._ids.new_id(
                    "capability-config-correlation"
                ),
                client_id="peerhub.capability-config",
                command_type=operation,
                idempotency_key=request_id,
                actor_id=actor_id,
                policy_revision="capability-native-v1:1",
                target_id=target_id,
                expected_revision=expected_revision,
                operation=operation,
                desired_state=state,
                effect_intent=EffectIntent(
                    kind="capability-config.noop", payload={}
                ),
            )
        )
        target = self._broker.get_target(target_id)
        if target is None:  # pragma: no cover - committed CAS guarantees it
            raise RuntimeError("committed capability target was not readable")
        return target

    @staticmethod
    def _encode_capabilities(
        capabilities: Sequence[ConfiguredCapability],
    ) -> tuple[JsonValue, ...]:
        return tuple(
            {"name": capability.name, "sources": capability.sources}
            for capability in capabilities
        )

    def put_config(
        self,
        *,
        node_id: str,
        enabled: bool,
        aliases: tuple[str, ...],
        capabilities: tuple[ConfiguredCapability, ...],
        actor_id: str,
    ) -> PeerCapabilityConfig:
        normalized_node_id = require_text(node_id, "node_id")
        normalized_actor = require_text(actor_id, "actor_id")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        normalized_aliases = tuple(
            require_text(alias, "alias") for alias in aliases
        )
        if len({alias.casefold() for alias in normalized_aliases}) != len(
            normalized_aliases
        ):
            raise ValueError("aliases must be unique after case-folding")
        seen: set[str] = set()
        for capability in capabilities:
            folded = capability.name.casefold()
            if folded in seen:
                raise ValueError(
                    "capabilities must be unique after case-folding"
                )
            seen.add(folded)
        target_id = self.target_id(normalized_node_id)
        current = self._broker.get_target(target_id)
        now = self._clock.now()
        state: dict[str, JsonValue] = {
            "kind": "peer-capability-config",
            "scope": normalized_node_id,
            "schema_version": 1,
            "node_id": normalized_node_id,
            "enabled": enabled,
            "aliases": normalized_aliases,
            "capabilities": self._encode_capabilities(capabilities),
            "updated_at": now,
            "updated_by": normalized_actor,
        }
        target = self._submit(
            target_id=target_id,
            expected_revision=0 if current is None else current.revision,
            actor_id=normalized_actor,
            operation="capability-config.put",
            state=state,
        )
        return self._decode_config(target)

    @staticmethod
    def _decode_config(target: TargetState) -> PeerCapabilityConfig:
        state = target.state
        node_id = state.get("node_id")
        enabled = state.get("enabled")
        aliases_value = state.get("aliases")
        capabilities_value = state.get("capabilities")
        updated_by = state.get("updated_by")
        updated_at = state.get("updated_at")
        if not isinstance(node_id, str) or not node_id:
            raise InvalidMutationError("capability config has malformed node_id")
        if type(enabled) is not bool:
            raise InvalidMutationError("capability config has malformed enabled")
        aliases = _string_sequence(aliases_value, "aliases")
        if not isinstance(capabilities_value, (list, tuple)):
            raise InvalidMutationError("capabilities must be a sequence")
        capabilities: list[ConfiguredCapability] = []
        for value in capabilities_value:
            item = _mapping(value, "capability")
            name = item.get("name")
            if not isinstance(name, str):
                raise InvalidMutationError("capability name must be a string")
            capabilities.append(
                ConfiguredCapability(
                    name=name,
                    sources=_string_sequence(item.get("sources"), "sources"),
                )
            )
        if not isinstance(updated_by, str) or not updated_by:
            raise InvalidMutationError("capability config has malformed updated_by")
        if type(updated_at) is not int or updated_at < 0:
            raise InvalidMutationError("capability config has malformed updated_at")
        return PeerCapabilityConfig(
            node_id=node_id,
            enabled=enabled,
            aliases=aliases,
            capabilities=tuple(capabilities),
            target_id=target.target_id,
            revision=target.revision,
            updated_at=updated_at,
            updated_by=updated_by,
        )

    def get_config(self, node_id: str) -> PeerCapabilityConfig | None:
        target = self._broker.get_target(self.target_id(node_id))
        return None if target is None else self._decode_config(target)

    def list_configs(self) -> tuple[PeerCapabilityConfig, ...]:
        return tuple(
            self._decode_config(target)
            for target in self._broker.list_targets(
                "peer-capability-config", None
            )
        )

    @staticmethod
    def _policy_state(
        default_proposer: DefaultProposerPolicy,
    ) -> dict[str, JsonValue]:
        return {
            "kind": "routing-policy",
            "scope": "capability-matching",
            "schema_version": 1,
            "policy_id": "capability-native-v1",
            "policy_revision": 1,
            "formula_id": "native-v1",
            "capability_points": {
                "empty": 1,
                "exact": 10,
                "substring": 7,
            },
            "health_points": {
                "HEALTHY": 3,
                "DEGRADED": 1,
                "UNKNOWN": 0,
                "PROBING": 0,
                "STALE": -5,
            },
            "continuity_bonus": 2,
            "quota_bands": (
                (0.90, 3),
                (0.75, 2),
                (0.50, 1),
                (0.10, -1),
                (0.0, -3),
            ),
            "recent_history_window": 2,
            "recent_use_penalty": 2,
            "tie_break": (
                "ranking_score DESC",
                "HEALTHY first",
                "node_id ASC",
            ),
            "default_proposer": {
                "mode": default_proposer.mode,
                "fixed_node_id": default_proposer.fixed_node_id,
                "rotation_order": default_proposer.rotation_order,
            },
        }

    def ensure_native_policy(
        self,
        default_proposer: DefaultProposerPolicy,
        *,
        actor_id: str = "configuration-import",
    ) -> CapabilityMatchingPolicy:
        expected = self._policy_state(default_proposer)
        current = self._broker.get_target(CAPABILITY_POLICY_TARGET_ID)
        if current is None:
            current = self._submit(
                target_id=CAPABILITY_POLICY_TARGET_ID,
                expected_revision=0,
                actor_id=actor_id,
                operation="capability-policy.create",
                state=expected,
            )
        elif dict(current.state) != expected:
            raise InvalidMutationError(
                "immutable native-v1 capability policy conflicts with requested import"
            )
        return self._decode_policy(current)

    def get_policy(self) -> CapabilityMatchingPolicy:
        target = self._broker.get_target(CAPABILITY_POLICY_TARGET_ID)
        if target is None:
            raise InvalidMutationError(
                "capability matching policy has not been imported"
            )
        return self._decode_policy(target)

    @staticmethod
    def _decode_policy(target: TargetState) -> CapabilityMatchingPolicy:
        state = target.state
        capability_points = _mapping(
            state.get("capability_points"), "capability_points"
        )
        health_points = _mapping(state.get("health_points"), "health_points")
        proposer_value = _mapping(
            state.get("default_proposer"), "default_proposer"
        )
        mode = proposer_value.get("mode")
        fixed = proposer_value.get("fixed_node_id")
        if mode not in {"FIXED", "ROTATING"}:
            raise InvalidMutationError("default proposer mode is malformed")
        if fixed is not None and not isinstance(fixed, str):
            raise InvalidMutationError("fixed_node_id is malformed")
        rotation_order = _string_sequence(
            proposer_value.get("rotation_order"), "rotation_order"
        )
        quota_value = state.get("quota_bands")
        if not isinstance(quota_value, (list, tuple)):
            raise InvalidMutationError("quota_bands is malformed")
        quota_bands: list[tuple[float, int]] = []
        for band in quota_value:
            if (
                not isinstance(band, (list, tuple))
                or len(band) != 2
                or type(band[0]) not in {int, float}
                or type(band[1]) is not int
            ):
                raise InvalidMutationError("quota band is malformed")
            threshold = cast("int | float", band[0])
            quota_bands.append((float(threshold), band[1]))
        try:
            return CapabilityMatchingPolicy(
                formula_id=cast(str, state["formula_id"]),
                policy_id=cast(str, state["policy_id"]),
                policy_revision=cast(int, state["policy_revision"]),
                target_id=target.target_id,
                target_revision=target.revision,
                target_digest=_canonical_digest(state),
                empty_capability_points=cast(int, capability_points["empty"]),
                exact_capability_points=cast(int, capability_points["exact"]),
                substring_capability_points=cast(
                    int, capability_points["substring"]
                ),
                health_points=tuple(
                    (AvailabilityState(name), cast(int, points))
                    for name, points in health_points.items()
                ),
                continuity_bonus=cast(int, state["continuity_bonus"]),
                quota_bands=tuple(quota_bands),
                recent_history_window=cast(
                    int, state["recent_history_window"]
                ),
                recent_use_penalty=cast(int, state["recent_use_penalty"]),
                default_proposer=DefaultProposerPolicy(
                    mode=cast(str, mode),  # type: ignore[arg-type]
                    fixed_node_id=fixed,
                    rotation_order=rotation_order,
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidMutationError(
                "capability matching policy is malformed"
            ) from exc


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidMutationError(
            f"cannot read legacy capability source {path}: {exc}"
        ) from exc
    return _mapping(value, str(path))


def _health_capabilities(path: Path | None) -> tuple[str, ...]:
    if path is None or not path.exists():
        return ()
    health = _read_json(path)
    profile_value = health.get("profile")
    if not isinstance(profile_value, Mapping):
        return ()
    profile = _mapping(cast(object, profile_value), f"{path}.profile")
    capabilities = profile.get("capabilities")
    if capabilities is None:
        return ()
    return _string_sequence(capabilities, f"{path}.profile.capabilities")


def _merge_capability(
    ordered: list[tuple[str, list[str]]],
    indexes: dict[str, int],
    name: str,
    source: str,
) -> None:
    normalized = require_text(name, "capability name")
    folded = normalized.casefold()
    index = indexes.get(folded)
    if index is None:
        indexes[folded] = len(ordered)
        ordered.append((normalized, [source]))
        return
    sources = ordered[index][1]
    if source not in sources:
        sources.append(source)


def import_legacy_capability_configs(
    service: CapabilityConfigService,
    *,
    protocol_path: Path,
    orchestration_path: Path,
    health_paths: Mapping[str, Path] | None = None,
) -> CapabilityConfigImportResult:
    """Snapshot health, protocol, then role capabilities into SQLite once."""

    protocol = _read_json(protocol_path)
    orchestration = _read_json(orchestration_path)
    workload = _mapping(protocol.get("workload"), "protocol.workload")
    registry = _mapping(
        workload.get("capability_registry"),
        "protocol.workload.capability_registry",
    )
    roles = _mapping(
        orchestration.get("roles_registry"),
        "orchestration.roles_registry",
    )
    raw_nodes = orchestration.get("hub_nodes")
    if not isinstance(raw_nodes, (list, tuple)):
        raise InvalidMutationError("orchestration.hub_nodes must be a sequence")
    nodes: dict[str, Mapping[str, object]] = {}
    for value in cast("Sequence[object]", raw_nodes):
        node = _mapping(value, "orchestration.hub_nodes item")
        node_id = node.get("node_id")
        if isinstance(node_id, str):
            nodes[node_id] = node

    if health_paths is None:
        sys_root = orchestration_path.parent.parent
        health_paths = {
            node_id: sys_root / subdir / "health.json"
            for node_id, subdir in _DEFAULT_HEALTH_SUBDIRS.items()
        }

    configs: list[PeerCapabilityConfig] = []
    for node_id in _BASE_NODE_IDS:
        node = nodes.get(node_id, {})
        aliases_value = node.get("aliases", ())
        aliases = (
            _string_sequence(aliases_value, f"hub_nodes.{node_id}.aliases")
            if aliases_value
            else ()
        )
        enabled_value = node.get("enabled", True)
        if type(enabled_value) is not bool:
            raise InvalidMutationError(
                f"hub_nodes.{node_id}.enabled must be a boolean"
            )
        ordered: list[tuple[str, list[str]]] = []
        indexes: dict[str, int] = {}
        for capability in _health_capabilities(health_paths.get(node_id)):
            _merge_capability(
                ordered,
                indexes,
                capability,
                f"legacy-import:health.{node_id}.profile.capabilities",
            )
        registry_value = registry.get(node_id, ())
        registry_capabilities = (
            _string_sequence(
                registry_value,
                f"protocol.workload.capability_registry.{node_id}",
            )
            if registry_value
            else ()
        )
        for capability in registry_capabilities:
            _merge_capability(
                ordered,
                indexes,
                capability,
                "legacy-import:protocol.workload.capability_registry."
                f"{node_id}",
            )
        for role, peers_value in roles.items():
            peers = _string_sequence(
                peers_value, f"orchestration.roles_registry.{role}"
            )
            if node_id in peers:
                _merge_capability(
                    ordered,
                    indexes,
                    role,
                    f"legacy-import:orchestration.roles_registry.{role}",
                )
        existing = service.get_config(node_id)
        configs.append(
            existing
            if existing is not None
            else service.put_config(
                node_id=node_id,
                enabled=enabled_value,
                aliases=aliases,
                capabilities=tuple(
                    ConfiguredCapability(name=name, sources=tuple(sources))
                    for name, sources in ordered
                ),
                actor_id="configuration-import",
            )
        )

    consensus = _mapping(
        orchestration.get("consensus"), "orchestration.consensus"
    )
    token = consensus.get("default_proposer")
    voters = _string_sequence(
        consensus.get("default_voters"), "consensus.default_voters"
    )
    if token == "rotating":
        proposer = DefaultProposerPolicy(
            mode="ROTATING", fixed_node_id=None, rotation_order=voters
        )
    elif isinstance(token, str) and token:
        proposer = DefaultProposerPolicy(
            mode="FIXED", fixed_node_id=token, rotation_order=()
        )
    else:
        raise InvalidMutationError("consensus.default_proposer is malformed")
    policy = service.ensure_native_policy(proposer)
    return CapabilityConfigImportResult(configs=tuple(configs), policy=policy)
