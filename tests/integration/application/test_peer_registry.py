from __future__ import annotations

from pathlib import Path

import pytest

from peerhub.application.peer_registry import PeerRegistryService
from peerhub.core.context import Clock
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.governance.broker import GovernanceBroker
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import SequentialIdSource


class FixedClock(Clock):
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def now(self) -> int:
        return self.value


def _service(tmp_path: Path) -> tuple[PeerRegistryService, GovernanceBroker, FixedClock]:
    store = SqliteStateStore(
        tmp_path / "peer-registry.sqlite3",
        workspace_home_id="peer-registry-test",
    )
    store.initialize()
    clock = FixedClock()
    ids = SequentialIdSource()
    broker = GovernanceBroker(store, clock=clock, ids=ids)
    return PeerRegistryService(broker, clock=clock, ids=ids), broker, clock


def test_list_nodes_on_a_fresh_workspace_returns_only_the_base_adapters(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path)

    nodes = service.list_nodes()

    assert [n.state["node_id"] for n in nodes] == ["ag", "cc", "cx"]
    assert all(n.state["source"] == "adapter-registry" for n in nodes)
    assert {n.state["profile_id"] for n in nodes} == {
        "ag.standard",
        "cc.standard",
        "cx.standard",
    }


def test_register_node_appears_in_list_alongside_base_nodes(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)

    service.register_node(
        node_id="worker-1", peer_kind="cc", actor_id="peer-1"
    )

    nodes = service.list_nodes()
    assert [n.state["node_id"] for n in nodes] == ["ag", "cc", "cx", "worker-1"]
    registered = next(n for n in nodes if n.state["node_id"] == "worker-1")
    assert registered.state["peer_kind"] == "cc"
    assert registered.state["profile_id"] == "cc.standard"
    assert registered.state["registered_by"] == "peer-1"


def test_identical_reregistration_is_state_stable_but_still_bumps_revision(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path)

    service.register_node(node_id="worker-1", peer_kind="cc", actor_id="peer-1")
    before = service.get_node("worker-1")

    service.register_node(node_id="worker-1", peer_kind="cc", actor_id="peer-1")
    after = service.get_node("worker-1")

    assert after.revision == before.revision + 1
    assert after.state["registered_at"] == before.state["registered_at"]
    assert after.state["registered_by"] == before.state["registered_by"]
    assert after.state["peer_kind"] == before.state["peer_kind"]


def test_differing_reregistration_overwrites_the_mutable_fields(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path)

    service.register_node(
        node_id="worker-1", peer_kind="cc", actor_id="peer-1", tier=4
    )
    service.register_node(
        node_id="worker-1", peer_kind="cc", actor_id="peer-1", tier=2, node_type="tool"
    )

    node = service.get_node("worker-1")
    assert node.state["tier"] == 2
    assert node.state["node_type"] == "tool"


def test_register_node_rejects_a_base_adapter_or_alias_collision(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path)

    for colliding_id in ("cc", "claude", "agy"):
        with pytest.raises(InvalidMutationError, match="collides"):
            service.register_node(
                node_id=colliding_id, peer_kind="cc", actor_id="peer-1"
            )


def test_register_node_rejects_an_unknown_peer_kind(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)

    with pytest.raises(ValueError, match="unsupported cli_name"):
        service.register_node(
            node_id="worker-1", peer_kind="nonexistent", actor_id="peer-1"
        )


def test_register_node_rejects_an_unknown_profile_id(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)

    with pytest.raises(ValueError, match="not supported"):
        service.register_node(
            node_id="worker-1",
            peer_kind="cc",
            profile_id="cc.nonexistent",
            actor_id="peer-1",
        )


def test_register_node_rejects_node_type_virtual(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)

    with pytest.raises(InvalidMutationError, match="virtual"):
        service.register_node(
            node_id="worker-1",
            peer_kind="cc",
            node_type="virtual",
            actor_id="peer-1",
        )


def test_get_node_raises_for_an_unregistered_node(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)

    with pytest.raises(RecordNotFoundError):
        service.get_node("does-not-exist")
