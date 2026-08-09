"""Unit tests for the built-in peer-adapter registry."""

from __future__ import annotations

import pytest

from peerhub.adapters.agy_adapter import RealAgyAdapter
from peerhub.adapters.claude_adapter import RealClaudeAdapter
from peerhub.adapters.codex_adapter import RealCodexAdapter
from peerhub.adapters.registry import resolve_peer_adapter
from peerhub.builtins.fake_adapter import FakePeerAdapter


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
    from peerhub.adapters.registry import _resolve_executable_path
    for name in ["agy.exe", "claude.cmd", "codex.cmd"]:
        resolved = _resolve_executable_path(name)
        assert resolved.is_absolute()
        assert resolved.name.lower() == name
