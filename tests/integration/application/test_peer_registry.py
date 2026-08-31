from __future__ import annotations

from pathlib import Path

import pytest

from peerhub.application.peer_registry import (
    PeerRegistryService,
    collect_model_status,
)
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


def test_get_node_falls_back_to_a_base_node_not_yet_registered(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path)

    node = service.get_node("cc")

    assert node.state["node_id"] == "cc"
    assert node.state["source"] == "adapter-registry"


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


def test_bind_profile_creates_separate_target_and_register_does_not(
    tmp_path: Path,
) -> None:
    service, broker, clock = _service(tmp_path)
    service.register_node(
        node_id="worker-1",
        peer_kind="cc",
        actor_id="peer-1",
    )

    assert service.list_profile_bindings("worker-1") == ()

    submission = service.bind_profile(
        node_id="worker-1",
        profile_id="cc.standard",
        model_id="claude-opus-test",
        reasoning_effort="high",
        actor_id="peer-2",
    )

    binding = broker.get_target(submission.receipt.target_id)
    assert binding is not None
    assert binding.target_id == (
        "peer-profile-binding:worker-1:cc.standard"
    )
    assert binding.state == {
        "kind": "peer-profile-binding",
        "scope": "worker-1",
        "schema_version": 1,
        "binding_id": "peer-profile-binding:worker-1:cc.standard",
        "node_id": "worker-1",
        "profile_id": "cc.standard",
        "model_id": "claude-opus-test",
        "reasoning_effort": "high",
        "updated_at": clock.now(),
        "updated_by": "peer-2",
    }


def test_get_and_list_profile_bindings_support_filtered_and_global_reads(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path)
    service.bind_profile(
        node_id="worker-1",
        profile_id="cc.standard",
        model_id="model-a",
        actor_id="peer-1",
    )
    service.bind_profile(
        node_id="worker-2",
        profile_id="cx.standard",
        model_id="model-b",
        actor_id="peer-1",
    )

    binding = service.get_profile_binding("worker-1", "cc.standard")
    assert binding is not None
    assert binding.state["model_id"] == "model-a"
    assert service.get_profile_binding("worker-1", "missing") is None
    assert [
        item.state["node_id"]
        for item in service.list_profile_bindings("worker-1")
    ] == ["worker-1"]
    assert [
        item.state["node_id"] for item in service.list_profile_bindings()
    ] == ["worker-1", "worker-2"]


@pytest.mark.parametrize(
    ("field", "overrides"),
    (
        ("node_id", {"node_id": ""}),
        ("profile_id", {"profile_id": ""}),
        ("model_id", {"model_id": ""}),
    ),
)
def test_bind_profile_rejects_empty_required_text(
    tmp_path: Path,
    field: str,
    overrides: dict[str, str],
) -> None:
    service, _, _ = _service(tmp_path)
    arguments = {
        "node_id": "worker-1",
        "profile_id": "cc.standard",
        "model_id": "model-a",
        "actor_id": "peer-1",
        **overrides,
    }

    with pytest.raises(ValueError, match=field):
        service.bind_profile(**arguments)


def test_collect_model_status_reports_bindings_and_blank_unbound_defaults(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path)
    service.register_node(
        node_id="bound-worker",
        peer_kind="cc",
        actor_id="peer-1",
    )
    service.register_node(
        node_id="unbound-worker",
        peer_kind="cx",
        actor_id="peer-1",
    )
    service.bind_profile(
        node_id="bound-worker",
        profile_id="cc.standard",
        model_id="claude-opus-test",
        reasoning_effort="high",
        actor_id="peer-1",
    )

    rows = collect_model_status(service, health=None)
    by_peer = {str(row["peer"]): row for row in rows}

    assert by_peer["bound-worker"]["profile"] == "cc.standard"
    assert by_peer["bound-worker"]["model"] == "claude-opus-test"
    assert by_peer["bound-worker"]["effort"] == "high"
    assert by_peer["bound-worker"]["status"] == "UNKNOWN"
    assert by_peer["unbound-worker"]["profile"] == "cx.standard"
    assert by_peer["unbound-worker"]["model"] == ""
    assert by_peer["unbound-worker"]["effort"] == ""
