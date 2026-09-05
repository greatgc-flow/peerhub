"""Unit tests for the built-in peer-adapter registry."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from peerhub.adapters import registry
from peerhub.adapters.agy_adapter import RealAgyAdapter
from peerhub.adapters.claude_adapter import RealClaudeAdapter
from peerhub.adapters.codex_adapter import RealCodexAdapter
from peerhub.adapters.registry import (
    register_adapter_factory,
    resolve_peer_adapter,
    resolve_peer_target,
)
from peerhub.builtins.fake_adapter import FakePeerAdapter

_BUILTIN_FACTORIES = {
    "fake": FakePeerAdapter,
    "ag": RealAgyAdapter,
    "cc": RealClaudeAdapter,
    "cx": RealCodexAdapter,
}

_BUILTIN_ALIASES = {
    "agy": "ag",
    "ag": "ag",
    "claude": "cc",
    "cc": "cc",
    "codex": "cx",
    "cx": "cx",
}


@pytest.fixture
def restored_registry() -> Iterator[None]:
    """Snapshot and restore registry state around a registration test."""

    factories = dict(registry._adapter_factories)  # pyright: ignore[reportPrivateUsage]
    aliases = dict(registry._cli_aliases)  # pyright: ignore[reportPrivateUsage]
    try:
        yield
    finally:
        registry._adapter_factories.clear()  # pyright: ignore[reportPrivateUsage]
        registry._adapter_factories.update(factories)  # pyright: ignore[reportPrivateUsage]
        registry._cli_aliases.clear()  # pyright: ignore[reportPrivateUsage]
        registry._cli_aliases.update(aliases)  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def stub_executables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve every executable name without touching the real PATH."""

    monkeypatch.setattr(
        registry,
        "resolve_executable_path",
        lambda name: Path(f"/dummy/{name}"),
    )


@pytest.mark.parametrize(
    ("peer_kind", "adapter_type"),
    (
        ("fake", FakePeerAdapter),
        ("ag", RealAgyAdapter),
        ("cc", RealClaudeAdapter),
        ("cx", RealCodexAdapter),
    ),
)
def test_resolve_peer_adapter_returns_registered_type(
    peer_kind: str,
    adapter_type: type[object],
) -> None:
    assert isinstance(resolve_peer_adapter(peer_kind), adapter_type)


def test_resolve_peer_adapter_rejects_unknown_peer_kind() -> None:
    with pytest.raises(ValueError, match="unsupported peer_kind"):
        resolve_peer_adapter("unknown")

def test_resolve_executable_path_resolves_all_real_names() -> None:
    from peerhub.adapters.registry import resolve_executable_path
    for name in ["agy.exe", "claude.cmd", "codex.cmd"]:
        resolved = resolve_executable_path(name)
        assert resolved.is_absolute()
        assert resolved.name.lower() == name


# (d) Regression safety: the four built-ins must behave exactly as before.


def test_builtin_registry_contents_are_unchanged() -> None:
    assert dict(registry._ADAPTER_FACTORIES) == _BUILTIN_FACTORIES  # pyright: ignore[reportPrivateUsage]
    assert dict(registry._CLI_ALIASES) == _BUILTIN_ALIASES  # pyright: ignore[reportPrivateUsage]


def test_builtin_registry_views_remain_read_only() -> None:
    with pytest.raises(TypeError):
        registry._ADAPTER_FACTORIES["injected"] = FakePeerAdapter  # type: ignore[index]  # pyright: ignore[reportPrivateUsage,reportIndexIssue]
    with pytest.raises(TypeError):
        registry._CLI_ALIASES["injected"] = "injected"  # type: ignore[index]  # pyright: ignore[reportPrivateUsage,reportIndexIssue]
    assert "injected" not in registry._ADAPTER_FACTORIES  # pyright: ignore[reportPrivateUsage]
    assert "injected" not in registry._CLI_ALIASES  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(("alias", "peer_kind"), tuple(_BUILTIN_ALIASES.items()))
def test_resolve_peer_target_maps_builtin_aliases_unchanged(
    stub_executables: None,
    alias: str,
    peer_kind: str,
) -> None:
    target = resolve_peer_target(alias)

    assert target.cli_name == alias
    assert target.peer_kind == peer_kind
    assert isinstance(target.adapter, _BUILTIN_FACTORIES[peer_kind])
    assert target.profile.profile_id == f"{peer_kind}.standard"


# (a) A genuinely new peer_kind registers and resolves through both entry points.


def test_register_adapter_factory_resolves_new_kind_and_aliases(
    restored_registry: None,
    stub_executables: None,
) -> None:
    register_adapter_factory("local", ("ollama", "local"), FakePeerAdapter)

    assert isinstance(resolve_peer_adapter("local"), FakePeerAdapter)
    for alias in ("ollama", "local"):
        target = resolve_peer_target(alias)
        assert target.cli_name == alias
        assert target.peer_kind == "local"
        assert isinstance(target.adapter, FakePeerAdapter)
        assert target.profile.profile_id == "fake-standard"


def test_register_adapter_factory_allows_a_kind_without_cli_aliases(
    restored_registry: None,
) -> None:
    register_adapter_factory("headless", (), FakePeerAdapter)

    assert isinstance(resolve_peer_adapter("headless"), FakePeerAdapter)
    with pytest.raises(ValueError, match="unsupported cli_name"):
        resolve_peer_target("headless")


@pytest.mark.parametrize("bad_value", ("", "   "))
def test_register_adapter_factory_validates_text_inputs(
    restored_registry: None,
    bad_value: str,
) -> None:
    with pytest.raises(ValueError, match="peer_kind"):
        register_adapter_factory(bad_value, ("fresh-alias",), FakePeerAdapter)
    with pytest.raises(ValueError, match="cli_aliases"):
        register_adapter_factory("local", (bad_value,), FakePeerAdapter)
    assert "local" not in registry._adapter_factories  # pyright: ignore[reportPrivateUsage]


# (b) A duplicate peer_kind must raise rather than replace a real adapter.


@pytest.mark.parametrize("peer_kind", tuple(_BUILTIN_FACTORIES))
def test_register_adapter_factory_rejects_duplicate_peer_kind(
    restored_registry: None,
    peer_kind: str,
) -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_adapter_factory(peer_kind, ("fresh-alias",), FakePeerAdapter)

    assert isinstance(
        resolve_peer_adapter(peer_kind),
        _BUILTIN_FACTORIES[peer_kind],
    )
    assert "fresh-alias" not in registry._cli_aliases  # pyright: ignore[reportPrivateUsage]


# (c) An alias already owned by a different peer_kind must raise.


@pytest.mark.parametrize("alias", tuple(_BUILTIN_ALIASES))
def test_register_adapter_factory_rejects_alias_owned_by_another_kind(
    restored_registry: None,
    alias: str,
) -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_adapter_factory("local", (alias,), FakePeerAdapter)

    assert registry._cli_aliases[alias] == _BUILTIN_ALIASES[alias]  # pyright: ignore[reportPrivateUsage]
    assert "local" not in registry._adapter_factories  # pyright: ignore[reportPrivateUsage]


def test_register_adapter_factory_is_all_or_nothing_on_a_late_collision(
    restored_registry: None,
) -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_adapter_factory(
            "local",
            ("fresh-alias", "claude"),
            FakePeerAdapter,
        )

    assert "local" not in registry._adapter_factories  # pyright: ignore[reportPrivateUsage]
    assert "fresh-alias" not in registry._cli_aliases  # pyright: ignore[reportPrivateUsage]
    assert registry._cli_aliases["claude"] == "cc"  # pyright: ignore[reportPrivateUsage]
